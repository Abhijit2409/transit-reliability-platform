"""
tests/test_pipeline.py
End-to-end integration tests for the orchestration layer.

What is and isn't mocked:
  - MOCKED: the OpenAI boundary only. A FakeClient returns scripted SQL for the
    generation call and scripted prose for the narration call. This lets us
    drive any pipeline path deterministically without a live API.
  - NOT MOCKED: validator logic, executor, narrator shape-checks, contracts,
    and the pipeline orchestration itself all run for real. The executor runs
    against the real read-only DuckDB warehouse.

The two structural-invariant tests are the point of this file:
  - the executor NEVER receives unvalidated SQL (it only ever gets the
    ValidatedQuery the validator minted), and
  - the narrator is NEVER called before a successful execution.
These are proven by spying on the real call boundaries, not by trusting outputs.

Run:  python -m pytest tests/test_pipeline.py -v
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from copilot import pipeline
from copilot import executor as executor_mod
from copilot import narrator as narrator_mod
from copilot.contracts import (
    PipelineResult,
    PipelineStatus,
    QueryResult,
    ValidatedQuery,
    _VALIDATION_SENTINEL,
)

DB = "data/warehouse/transit.duckdb"


# --------------------------------------------------------------------------- #
# Fake OpenAI client (mocks ONLY the model boundary)
# --------------------------------------------------------------------------- #

class FakeClient:
    """Scripts the two model calls the pipeline makes.

    The generation call is identified by the SQL-generation instructions; the
    narration call by the 'RESULT ROWS' marker in its input. `sql` is returned
    for generation, `narration` for narration.
    """

    def __init__(self, sql: str = "SELECT 1 WHERE 1=0",
                 narration: str = "Narrated answer based on the result."):
        self._sql = sql
        self._narration = narration
        self.responses = self
        self.generation_calls = 0
        self.narration_calls = 0

    def create(self, **kwargs):
        if "RESULT ROWS" in kwargs.get("input", ""):
            self.narration_calls += 1
            return SimpleNamespace(output_text=self._narration)
        self.generation_calls += 1
        return SimpleNamespace(output_text=self._sql)


# --------------------------------------------------------------------------- #
# 1. Successful answer flow
# --------------------------------------------------------------------------- #

def test_success_flow_full_sequence():
    client = FakeClient(
        sql="SELECT route_short_name, reliability_score, reliability_band "
            "FROM top20_route_reliability_scores WHERE route_short_name='R4' LIMIT 100",
        narration="R4 has a reliability score of 77.6 (Watch band).",
    )
    res = pipeline.run("What was the reliability score of R4?", client=client, db_path=DB)

    assert isinstance(res, PipelineResult)
    assert res.status is PipelineStatus.SUCCESS
    assert res.question == "What was the reliability score of R4?"
    assert res.generated_sql and "top20_route_reliability_scores" in res.generated_sql
    assert res.validated_sql is not None             # only set after validation
    assert res.answer_text == "R4 has a reliability score of 77.6 (Watch band)."
    assert res.row_count == 1
    # both model calls happened, in the right amounts
    assert client.generation_calls == 1
    assert client.narration_calls == 1


# --------------------------------------------------------------------------- #
# 2. Refusal flow (short-circuits before any model call)
# --------------------------------------------------------------------------- #

def test_refusal_flow():
    client = FakeClient()
    res = pipeline.run("How many passengers ride R4 each day?", client=client, db_path=DB)

    assert res.status is PipelineStatus.REFUSED
    assert res.refusal_reason  # populated
    assert res.generated_sql is None
    assert res.validated_sql is None
    assert res.answer_text is None
    # NO model call should have occurred
    assert client.generation_calls == 0
    assert client.narration_calls == 0


# --------------------------------------------------------------------------- #
# 3. Low-confidence flow
# --------------------------------------------------------------------------- #

def test_low_confidence_flow():
    client = FakeClient()
    res = pipeline.run("What is the weather like?", client=client, db_path=DB)

    assert res.status is PipelineStatus.LOW_CONFIDENCE
    assert res.generated_sql is None
    assert res.validated_sql is None
    assert client.generation_calls == 0


# --------------------------------------------------------------------------- #
# 4. Ambiguous-time flow
# --------------------------------------------------------------------------- #

def test_ambiguous_time_flow():
    client = FakeClient()
    res = pipeline.run("bunching at rush hour", client=client, db_path=DB)

    assert res.status is PipelineStatus.AMBIGUOUS_TIME
    assert res.validated_sql is None
    assert client.generation_calls == 0


# --------------------------------------------------------------------------- #
# 5. Validation-failure flow (a real SELECT that is unsafe)
# --------------------------------------------------------------------------- #

def test_validation_failure_blocked_table():
    # The model emits a well-formed SELECT against a BLOCKED _099 table.
    client = FakeClient(sql="SELECT route_id FROM bunching_events_099 LIMIT 10")
    res = pipeline.run("Which routes have the most bunching?", client=client, db_path=DB)

    assert res.status is PipelineStatus.VALIDATION_FAILED
    assert res.generated_sql is not None             # it WAS generated
    assert res.validated_sql is None                 # but never validated
    assert res.answer_text is None                   # and never narrated
    assert "099" in res.validation_reason or "blocked" in res.validation_reason
    # narration must not have been attempted
    assert client.narration_calls == 0


def test_validation_failure_limit_over_cap():
    client = FakeClient(
        sql="SELECT route_short_name FROM top20_route_reliability_scores LIMIT 99999"
    )
    res = pipeline.run("Which routes are least reliable?", client=client, db_path=DB)
    assert res.status is PipelineStatus.VALIDATION_FAILED
    assert res.validated_sql is None
    assert client.narration_calls == 0


# --------------------------------------------------------------------------- #
# 6. Execution-failure flow
# --------------------------------------------------------------------------- #

def test_execution_failure_flow(monkeypatch):
    # Force the (real) executor to raise, to prove the pipeline catches it and
    # fails closed without proceeding to narration.
    client = FakeClient(
        sql="SELECT route_short_name FROM top20_route_reliability_scores LIMIT 5"
    )

    def boom(query, db_path=None):
        raise RuntimeError("simulated duckdb failure")

    monkeypatch.setattr(executor_mod, "execute", boom)
    # pipeline imported executor as executor_mod; patch the same object it calls
    monkeypatch.setattr(pipeline.executor_mod, "execute", boom)

    res = pipeline.run("Which routes are least reliable?", client=client, db_path=DB)

    assert res.status is PipelineStatus.EXECUTION_ERROR
    assert res.generated_sql is not None
    assert res.validated_sql is not None             # validation succeeded first
    assert res.answer_text is None
    assert "simulated duckdb failure" in res.execution_error
    assert client.narration_calls == 0               # never narrated


# --------------------------------------------------------------------------- #
# 7. Narration-failure flow
# --------------------------------------------------------------------------- #

def test_narration_failure_flow(monkeypatch):
    from copilot.contracts import NarratedAnswer, NarrationStatus

    client = FakeClient(
        sql="SELECT route_short_name, reliability_score "
            "FROM top20_route_reliability_scores WHERE route_short_name='R4' LIMIT 100"
    )

    def malformed(question, sql, result, client=None):
        return NarratedAnswer(
            status=NarrationStatus.MALFORMED_RESULT,
            answer_text="malformed",
            sql=sql,
            row_count=result.row_count,
            detail="simulated malformed result",
        )

    monkeypatch.setattr(pipeline.narrator_mod, "narrate", malformed)

    res = pipeline.run("What was the reliability score of R4?", client=client, db_path=DB)

    assert res.status is PipelineStatus.NARRATION_ERROR
    assert res.validated_sql is not None             # execution succeeded
    assert res.row_count is not None and res.row_count >= 1
    assert "malformed" in res.narration_error


# --------------------------------------------------------------------------- #
# 8. STRUCTURAL INVARIANT: executor never receives unvalidated SQL
# --------------------------------------------------------------------------- #

def test_executor_only_ever_receives_validated_query(monkeypatch):
    """Spy on the real executor: whatever it is handed must be a ValidatedQuery
    minted by the validator (carries the sentinel), never a raw string or a
    CandidateSQL."""
    seen = {}
    real_execute = executor_mod.execute

    def spy(query, db_path=None):
        seen["type"] = type(query).__name__
        seen["is_validated_query"] = isinstance(query, ValidatedQuery)
        seen["has_sentinel"] = getattr(query, "_marker", None) is _VALIDATION_SENTINEL
        return real_execute(query, db_path=db_path)

    monkeypatch.setattr(pipeline.executor_mod, "execute", spy)

    client = FakeClient(
        sql="SELECT route_short_name FROM top20_route_reliability_scores LIMIT 5"
    )
    res = pipeline.run("Which routes are least reliable?", client=client, db_path=DB)

    assert res.status in (PipelineStatus.SUCCESS, PipelineStatus.EMPTY_RESULT)
    assert seen["is_validated_query"] is True
    assert seen["has_sentinel"] is True
    assert seen["type"] == "ValidatedQuery"


def test_executor_not_called_when_validation_fails(monkeypatch):
    """If validation fails, the executor must not be invoked at all."""
    called = {"n": 0}

    def spy(query, db_path=None):
        called["n"] += 1
        raise AssertionError("executor must not run on validation failure")

    monkeypatch.setattr(pipeline.executor_mod, "execute", spy)

    client = FakeClient(sql="SELECT * FROM bunching_events_099 LIMIT 10")  # blocked
    res = pipeline.run("Which routes have the most bunching?", client=client, db_path=DB)

    assert res.status is PipelineStatus.VALIDATION_FAILED
    assert called["n"] == 0


# --------------------------------------------------------------------------- #
# 9. STRUCTURAL INVARIANT: narration never called before successful execution
# --------------------------------------------------------------------------- #

def test_narrator_called_only_after_execution(monkeypatch):
    order: list[str] = []

    real_execute = executor_mod.execute
    real_narrate = narrator_mod.narrate

    def exec_spy(query, db_path=None):
        order.append("execute")
        return real_execute(query, db_path=db_path)

    def narrate_spy(question, sql, result, client=None):
        order.append("narrate")
        assert isinstance(result, QueryResult)       # got a real result
        return real_narrate(question, sql, result, client=client)

    monkeypatch.setattr(pipeline.executor_mod, "execute", exec_spy)
    monkeypatch.setattr(pipeline.narrator_mod, "narrate", narrate_spy)

    client = FakeClient(
        sql="SELECT route_short_name, reliability_score "
            "FROM top20_route_reliability_scores WHERE route_short_name='R4' LIMIT 100",
        narration="R4 scored 77.6.",
    )
    res = pipeline.run("What was the reliability score of R4?", client=client, db_path=DB)

    assert res.status is PipelineStatus.SUCCESS
    assert order == ["execute", "narrate"]           # strict ordering


def test_narrator_not_called_on_refusal(monkeypatch):
    called = {"n": 0}

    def narrate_spy(question, sql, result, client=None):
        called["n"] += 1
        raise AssertionError("narrator must not run on refusal")

    monkeypatch.setattr(pipeline.narrator_mod, "narrate", narrate_spy)

    res = pipeline.run("Which SkyTrain line is most delayed?", client=FakeClient(), db_path=DB)
    assert res.status is PipelineStatus.REFUSED
    assert called["n"] == 0


# --------------------------------------------------------------------------- #
# 10. PipelineResult field contract
# --------------------------------------------------------------------------- #

def test_pipeline_result_fields_present_on_success():
    client = FakeClient(
        sql="SELECT route_short_name FROM top20_route_reliability_scores LIMIT 5",
        narration="Some routes.",
    )
    res = pipeline.run("Which routes are least reliable?", client=client, db_path=DB)
    # all declared fields exist and types are sane
    for field_name in (
        "status", "question", "generated_sql", "validated_sql",
        "answer_text", "row_count", "refusal_reason", "validation_reason",
        "execution_error", "narration_error", "detail",
    ):
        assert hasattr(res, field_name)
    assert isinstance(res.status, PipelineStatus)
    assert res.ok is True


def test_empty_result_status():
    client = FakeClient(
        sql="SELECT route_short_name FROM top20_route_reliability_scores "
            "WHERE reliability_score < 0 LIMIT 100",
        narration="should not be used",
    )
    res = pipeline.run("Which routes are least reliable?", client=client, db_path=DB)
    assert res.status is PipelineStatus.EMPTY_RESULT
    assert res.row_count == 0
    assert res.ok is True                            # empty is still a clean run
