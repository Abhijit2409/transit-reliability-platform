"""
tests/test_entity_resolver.py
Locks deterministic route-alias resolution against the real entities.yaml.

These guard the "valid-but-wrong" failure mode at the entity layer: the model
can write perfect SQL against the wrong route if resolution is sloppy.
"""

from __future__ import annotations

import pytest

from copilot.contracts import MatchConfidence, RouteResolution
from copilot.entity_resolver import resolve_routes


def _canonicals(res: RouteResolution) -> set[str]:
    return {r.canonical for r in res.routes}


# --------------------------------------------------------------------------- #
# Core alias resolution
# --------------------------------------------------------------------------- #

def test_r4_resolves_to_R4():
    res = resolve_routes("What was the reliability score of R4?")
    assert _canonicals(res) == {"R4"}
    assert res.ok
    (route,) = res.routes
    assert route.route_id == 37810
    assert route.route_long_name == "41st Avenue"
    assert route.route_type == "RapidBus"


def test_99_bline_resolves_to_099():
    res = resolve_routes("What reliability band is the 99 B-Line in?")
    assert _canonicals(res) == {"099"}
    (route,) = res.routes
    assert route.route_id == 6641
    assert route.route_long_name == "Broadway B-Line"


def test_broadway_bline_phrase_resolves_to_099():
    res = resolve_routes("How does the Broadway B-Line perform?")
    assert _canonicals(res) == {"099"}


def test_route_49_resolves_to_049_zero_padded():
    res = resolve_routes("Tell me about route 49")
    assert _canonicals(res) == {"049"}
    (route,) = res.routes
    assert route.canonical == "049"          # stored zero-padded, not '49'
    assert route.route_id == 6636


# --------------------------------------------------------------------------- #
# The "Broadway" substring trap
# --------------------------------------------------------------------------- #

def test_broadway_bline_does_not_also_match_009():
    """'Broadway B-Line' must resolve ONLY to 099, not also to 009 (Broadway)."""
    res = resolve_routes("How does the Broadway B-Line do?")
    assert _canonicals(res) == {"099"}
    assert "009" not in _canonicals(res)


def test_plain_broadway_can_match_009_when_standalone():
    """Sanity: a standalone 'Broadway 9' still resolves 009 (the alias exists),
    without the longer B-Line phrase suppressing it."""
    res = resolve_routes("How is Broadway 9 doing?")
    assert "009" in _canonicals(res)


# --------------------------------------------------------------------------- #
# Multi-route comparisons
# --------------------------------------------------------------------------- #

def test_compare_099_and_r4_resolves_both():
    res = resolve_routes("Compare Route 099 and R4.")
    assert _canonicals(res) == {"099", "R4"}
    assert res.ok


# --------------------------------------------------------------------------- #
# Fail-closed: non-Top-20 routes must NOT silently resolve
# --------------------------------------------------------------------------- #

def test_route_999_does_not_resolve_and_is_flagged_unresolved():
    res = resolve_routes("How is route 999 doing?")
    assert _canonicals(res) == set()
    assert res.has_unresolved
    assert "999" in res.unresolved_terms
    assert not res.ok


def test_letter_code_not_in_catalog_is_unresolved():
    """A route-shaped letter+digit code that isn't catalogued is unresolved."""
    res = resolve_routes("What about the R9?")
    assert "R9" not in _canonicals(res)
    assert res.has_unresolved
    assert "r9" in {t.lower() for t in res.unresolved_terms}


# --------------------------------------------------------------------------- #
# In-scope route that looks out-of-scope (250 IS Top-20)
# --------------------------------------------------------------------------- #

def test_route_250_is_in_scope():
    res = resolve_routes("How is route 250 doing?")
    assert _canonicals(res) == {"250"}
    assert not res.has_unresolved


# --------------------------------------------------------------------------- #
# Confidence semantics
# --------------------------------------------------------------------------- #

def test_long_name_match_is_exact_confidence():
    res = resolve_routes("How is the Broadway B-Line?")
    (route,) = res.routes
    assert route.confidence == MatchConfidence.EXACT


def test_no_route_mention_yields_empty_resolution():
    res = resolve_routes("Which routes have the worst reliability?")
    assert _canonicals(res) == set()
    assert not res.has_unresolved   # 'routes' generic, not a specific code
