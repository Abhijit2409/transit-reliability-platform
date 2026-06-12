"""
tests/test_time_resolver.py
Locks time-of-day resolution against the real time_windows.yaml.

Verifies canonical peak periods, the AM/PM disambiguation of 'rush', and that
bare 'rush hour' carries the documented LOW-confidence + assumption note so the
pipeline can surface the assumption instead of silently defaulting.
"""

from __future__ import annotations

import pytest

from copilot.contracts import MatchConfidence
from copilot.time_resolver import resolve_time


def test_pm_peak_resolves_exact():
    r = resolve_time("Which routes perform worst during PM Peak?")
    assert r.ok
    assert r.window.canonical == "PM Peak"
    assert r.window.column == "peak_period"
    assert r.window.hours_of_day == (15, 16, 17, 18)
    assert r.window.confidence == MatchConfidence.EXACT


def test_am_peak_resolves_exact():
    r = resolve_time("bunching during AM Peak")
    assert r.ok
    assert r.window.canonical == "AM Peak"
    assert r.window.hours_of_day == (7, 8, 9)


def test_morning_rush_resolves_to_am_peak():
    r = resolve_time("how bad is bunching in the morning rush")
    assert r.ok
    assert r.window.canonical == "AM Peak"
    assert r.window.confidence == MatchConfidence.HIGH


def test_evening_rush_resolves_to_pm_peak():
    r = resolve_time("evening rush hour bunching")
    assert r.ok
    assert r.window.canonical == "PM Peak"
    assert r.window.confidence == MatchConfidence.HIGH


def test_off_peak_resolves_exact():
    r = resolve_time("what about off peak bunching")
    assert r.ok
    assert r.window.canonical == "Off Peak"
    # Off Peak spans the non-peak hours; just assert it is a non-empty range
    assert len(r.window.hours_of_day) > 0


# --------------------------------------------------------------------------- #
# Bare 'rush hour' — ambiguous: LOW confidence + assumption note, defaults PM
# --------------------------------------------------------------------------- #

def test_bare_rush_hour_is_low_confidence_with_note():
    r = resolve_time("what happens at rush hour")
    assert r.window is not None
    assert r.window.canonical == "PM Peak"            # documented default
    assert r.window.confidence == MatchConfidence.LOW
    assert r.window.assumption_note != ""             # assumption surfaced
    assert not r.ok                                   # LOW -> pipeline fails closed


def test_morning_qualifier_overrides_rush_ambiguity():
    r = resolve_time("morning rush hour delays")
    assert r.window.canonical == "AM Peak"
    assert r.window.confidence == MatchConfidence.HIGH


# --------------------------------------------------------------------------- #
# No time reference
# --------------------------------------------------------------------------- #

def test_no_time_reference_yields_none():
    r = resolve_time("Which routes are least reliable?")
    assert r.window is None
    assert r.confidence == MatchConfidence.NONE
    assert not r.ok
