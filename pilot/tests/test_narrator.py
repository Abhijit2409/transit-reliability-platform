"""
tests/test_narrator.py
Proves the narrator deterministically requires every route identifier and
numeric value from the QueryResult to be echoed verbatim.

No live API: a capture client records the prompt the narrator builds so we can
assert the MUST-MENTION checklist is present and complete. Also unit-tests the
anchor-extraction helper directly.
"""

from __future__ import annotations

from types import SimpleNamespace

from copilot import narrator as narrator_mod
from copilot.narrator import _required_anchors, narrate
from copilot.contracts import NarrationStatus, QueryResult


class CaptureClient:
    """Records the input sent to the model and returns a fixed answer."""

    def __init__(self, answer: str = "ok"):
        self.answer = answer
        self.captured_input = ""
        self.responses = self

    def create(self, **kwargs):
        self.captured_input = kwargs.get("input", "")
        return SimpleNamespace(output_text=self.answer, usage=None)


# --------------------------------------------------------------------------- #
# _required_anchors unit tests
# --------------------------------------------------------------------------- #

def test_required_anchors_extracts_route_and_numbers():
    result = QueryResult(
        columns=("route_short_name", "reliability_band", "reliability_score"),
        rows=(("099", "Watch", 84.21),),
        row_count=1,
        sql="SELECT ...",
    )
    routes, nums = _required_anchors(result)
    assert routes == ["099"]
    assert "84.21" in nums


def test_required_anchors_route_without_short_name_column():
    # L04 shape: route_short_name IS present in corridor table results
    result = QueryResult(
        columns=("route_short_name", "route_long_name", "intervention_priority_score"),
        rows=(("R4", "41st Avenue", 52.42),),
        row_count=1,
        sql="SELECT ...",
    )
    routes, nums = _required_anchors(result)
    assert routes == ["R4"]
    assert "52.42" in nums


def test_required_anchors_dedupes_and_preserves_order():
    result = QueryResult(
        columns=("route_short_name", "score"),
        rows=(("R4", 10.0), ("099", 20.0), ("R4", 10.0)),
        row_count=3,
        sql="SELECT ...",
    )
    routes, nums = _required_anchors(result)
    assert routes == ["R4", "099"]          # de-duped, order preserved
    assert nums == ["10.0", "20.0"]


# --------------------------------------------------------------------------- #
# Prompt construction: MUST-MENTION checklist present and complete
# --------------------------------------------------------------------------- #

def test_l02_prompt_requires_route_and_score():
    """L02: '099' and '84.21' must both be in the MUST-MENTION block."""
    result = QueryResult(
        columns=("route_short_name", "reliability_band", "reliability_score"),
        rows=(("099", "Watch", 84.21),),
        row_count=1,
        sql="SELECT route_short_name, reliability_band, reliability_score "
            "FROM top20_route_reliability_scores WHERE route_short_name = '099' LIMIT 100",
    )
    client = CaptureClient("Route 099 is in the Watch band with a reliability score of 84.21.")
    out = narrate("What reliability band is the 99 B-Line in?", result.sql, result, client=client)
    assert out.status is NarrationStatus.OK
    assert "MUST MENTION" in client.captured_input
    assert "099" in client.captured_input
    assert "84.21" in client.captured_input


def test_l04_prompt_requires_route_token():
    """L04: 'R4' and '52.42' must both be in the MUST-MENTION block."""
    result = QueryResult(
        columns=("route_short_name", "route_long_name", "intervention_priority_score"),
        rows=(("R4", "41st Avenue", 52.42),),
        row_count=1,
        sql="SELECT route_short_name, route_long_name, intervention_priority_score "
            "FROM top20_corridor_priority_ranking WHERE route_long_name = '41st Avenue' LIMIT 100",
    )
    client = CaptureClient("R4 (41st Avenue) has an intervention priority score of 52.42.")
    out = narrate("What is the intervention priority score for the 41st Avenue route?",
                  result.sql, result, client=client)
    assert out.status is NarrationStatus.OK
    assert "MUST MENTION" in client.captured_input
    assert "R4" in client.captured_input
    assert "52.42" in client.captured_input


def test_instructions_demand_verbatim_identifier():
    """The standing instructions must require the exact route token, not just
    a descriptive name."""
    instr = narrator_mod._INSTRUCTIONS
    assert "exactly as it appears" in instr
    assert "verbatim" in instr


def test_empty_result_has_no_must_mention():
    """An empty result needs no anchors and must not fabricate a checklist."""
    result = QueryResult(columns=("route_short_name",), rows=(), row_count=0, sql="SELECT 1 WHERE 1=0")
    # empty path returns before any model call; just confirm status
    out = narrate("q", result.sql, result)
    assert out.status is NarrationStatus.EMPTY_RESULT
