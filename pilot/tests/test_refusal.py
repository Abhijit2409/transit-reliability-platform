"""
tests/test_refusal.py
Locks out-of-scope detection against the real semantic config.

Refusal is the first line of the trust story: an honest decline beats a
confidently-wrong answer. Each test asserts both that the question is refused
AND the specific category, so a regression cannot silently re-route a refusal.
"""

from __future__ import annotations

import pytest

from copilot.contracts import RefusalCategory
from copilot.refusal import check_refusal


# --------------------------------------------------------------------------- #
# Ridership / passenger data (not in warehouse)
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("q", [
    "How many passengers ride R4 each day?",
    "What's the ridership on the 99 B-Line?",
    "How many boardings did route 49 have?",
    "How busy is the R4?",
])
def test_ridership_refused(q):
    r = check_refusal(q)
    assert r.refuse
    assert r.category == RefusalCategory.RIDERSHIP


# --------------------------------------------------------------------------- #
# Non-bus modes
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("q", [
    "Which SkyTrain line is most delayed?",
    "How reliable is the SeaBus?",
    "What about the West Coast Express?",
    "Is the Canada Line bunching?",
])
def test_nonbus_refused(q):
    r = check_refusal(q)
    assert r.refuse
    assert r.category == RefusalCategory.SKYTRAIN_OR_NONBUS


# --------------------------------------------------------------------------- #
# On-time / schedule adherence (not measured)
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("q", [
    "What's the 099's on-time performance against schedule?",
    "How punctual is R4 versus the timetable?",
    "What is route 49's schedule adherence?",
])
def test_on_time_performance_refused(q):
    r = check_refusal(q)
    assert r.refuse
    assert r.category == RefusalCategory.ON_TIME_PERFORMANCE


# --------------------------------------------------------------------------- #
# Multi-day / trend (single service_date scope)
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("q", [
    "How has R4's reliability changed over the past 6 months?",
    "Show me the bunching trend over time",
    "What's the weekly reliability for route 49?",
    "How does this month compare to last month?",
])
def test_multi_day_trend_refused(q):
    r = check_refusal(q)
    assert r.refuse
    assert r.category == RefusalCategory.MULTI_DAY_TREND


# --------------------------------------------------------------------------- #
# Non-Top-20 route
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("q", [
    "How is route 999 doing?",
    "What's the reliability of route 888?",
])
def test_non_top20_route_refused(q):
    r = check_refusal(q)
    assert r.refuse
    assert r.category == RefusalCategory.NON_TOP20_ROUTE


# --------------------------------------------------------------------------- #
# Gated data-quality / eligibility metric (in_scope: false)
# --------------------------------------------------------------------------- #

def test_explicit_data_quality_refused():
    r = check_refusal("What is the data quality of route R4?")
    assert r.refuse
    assert r.category == RefusalCategory.UNSUPPORTED_METRIC


# --------------------------------------------------------------------------- #
# In-scope questions must NOT be refused
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("q", [
    "Which routes have the worst reliability?",
    "What was the reliability score of R4?",
    "Which corridors should operations prioritize?",
    "Which routes have the most bunching?",
    "Compare Route 099 and R4.",
    "Which routes perform worst during PM Peak?",
])
def test_in_scope_questions_allowed(q):
    r = check_refusal(q)
    assert not r.refuse
    assert r.category is None
