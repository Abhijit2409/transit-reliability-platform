"""
tests/test_metric_resolver.py
Locks metric resolution and — critically — superlative SORT DIRECTION.

The reliability_score direction trap is the headline: 'worst reliability' must
order ASC (lowest score = worst) because the metric is higher_is_better. A
regression here produces valid SQL that answers the exact opposite question.
"""

from __future__ import annotations

import pytest

from copilot.contracts import MatchConfidence
from copilot.metric_resolver import resolve_metric


# --------------------------------------------------------------------------- #
# Reliability direction trap
# --------------------------------------------------------------------------- #

def test_worst_reliability_is_ascending():
    r = resolve_metric("Which routes have the worst reliability?")
    assert r.ok
    assert r.metric.name == "reliability_score"
    assert r.metric.direction == "higher_is_better"
    assert r.metric.sort_for_superlative == "ASC"   # lowest score = worst


def test_least_reliable_is_ascending():
    r = resolve_metric("Which routes are least reliable?")
    assert r.ok
    assert r.metric.name == "reliability_score"
    assert r.metric.sort_for_superlative == "ASC"


def test_most_reliable_is_descending():
    r = resolve_metric("Which routes are most reliable?")
    assert r.ok
    assert r.metric.name == "reliability_score"
    assert r.metric.sort_for_superlative == "DESC"


def test_best_reliability_is_descending():
    r = resolve_metric("Which route has the best reliability?")
    assert r.ok
    assert r.metric.name == "reliability_score"
    assert r.metric.sort_for_superlative == "DESC"


# --------------------------------------------------------------------------- #
# Bunching magnitude (lower_is_better, but 'most' = literal high)
# --------------------------------------------------------------------------- #

def test_most_bunching_is_descending():
    """'most bunching' = highest bunching value = DESC, even though the metric
    is lower_is_better. Magnitude words are literal, not polarity-flipped."""
    r = resolve_metric("Which routes have the most bunching?")
    assert r.ok
    assert r.metric.name in ("bunching_rate_pct", "total_bunching_events")
    assert r.metric.direction == "lower_is_better"
    assert r.metric.sort_for_superlative == "DESC"


def test_least_bunching_is_ascending():
    r = resolve_metric("Which routes have the least bunching?")
    assert r.ok
    assert r.metric.name in ("bunching_rate_pct", "total_bunching_events")
    assert r.metric.sort_for_superlative == "ASC"


def test_worst_bunching_quality_word_is_descending():
    """'worst bunching' uses a quality word on a lower_is_better metric -> DESC."""
    r = resolve_metric("Which routes have the worst bunching?")
    assert r.ok
    assert r.metric.direction == "lower_is_better"
    assert r.metric.sort_for_superlative == "DESC"


def test_highest_bunching_rate_is_descending():
    r = resolve_metric("Which routes have the highest bunching rate?")
    assert r.ok
    assert r.metric.name == "bunching_rate_pct"
    assert r.metric.sort_for_superlative == "DESC"


# --------------------------------------------------------------------------- #
# Priority / intervention
# --------------------------------------------------------------------------- #

def test_priority_corridor_resolves_to_intervention_priority():
    r = resolve_metric("Show me the priority corridor")
    assert r.ok
    assert r.metric.name == "intervention_priority_score"
    assert r.metric.direction == "higher_is_better"


def test_where_should_we_focus_descending_priority():
    r = resolve_metric("Which corridors have the highest priority to fix?")
    assert r.ok
    assert r.metric.name == "intervention_priority_score"
    assert r.metric.sort_for_superlative == "DESC"


def test_prioritize_resolves_to_intervention_priority():
    r = resolve_metric("Which corridors should operations prioritize?")
    assert r.ok
    assert r.metric.name == "intervention_priority_score"


# --------------------------------------------------------------------------- #
# Severe bunching (no superlative -> no forced ordering)
# --------------------------------------------------------------------------- #

def test_severe_bunching_resolves_without_superlative():
    r = resolve_metric("Show me severe bunching")
    assert r.ok
    assert r.metric.name in ("severe_bunching_rate_pct", "severe_events")
    assert r.metric.sort_for_superlative is None


# --------------------------------------------------------------------------- #
# No-metric / gated
# --------------------------------------------------------------------------- #

def test_unrelated_question_resolves_no_metric():
    r = resolve_metric("What's the weather like today?")
    assert not r.ok
    assert r.metric is None
    assert r.confidence == MatchConfidence.NONE


def test_gated_metric_flagged_out_of_scope():
    """Data-quality/eligibility is in_scope: false in metrics.yaml. If the
    resolver matches it at all, it must carry in_scope=False so the pipeline
    can refuse rather than query it."""
    r = resolve_metric("What is the data quality of the routes?")
    if r.metric is not None and r.metric.name == "eligibility_status":
        assert r.metric.in_scope is False


# --------------------------------------------------------------------------- #
# Implicit-metric fallback (A01 regression guard)
# --------------------------------------------------------------------------- #

def test_perform_worst_during_peak_resolves_to_bunching():
    """'perform worst during PM Peak' names no metric explicitly; the implicit
    measure is peak bunching. Must resolve, with DESC ordering for 'worst'."""
    r = resolve_metric("Which routes perform worst during PM Peak?")
    assert r.ok
    assert r.metric.name == "bunching_events_hourly"
    assert r.metric.sort_for_superlative == "DESC"


def test_performance_cue_without_peak_does_not_trigger_fallback():
    """The fallback requires BOTH a performance cue and a peak reference."""
    r = resolve_metric("how do the routes do")
    assert not r.ok
