"""
copilot/time_resolver.py
Deterministic resolution of time-of-day language to a peak_period window.

No model calls. Uses time_windows.yaml. Resolves 'PM Peak', 'morning rush',
'off peak', etc. to a canonical window with its hour range. Ambiguous 'rush
hour' (no AM/PM) defaults to PM Peak but records the assumption. Multi-day /
trend language is NOT handled here (that is a refusal — see refusal.py); this
resolver only returns a window when one is clearly present.
"""

from __future__ import annotations

import re
from functools import lru_cache

from config.loaders import load_time_windows
from copilot.contracts import (
    MatchConfidence,
    ResolvedTimeWindow,
    TimeResolution,
)


class _TimeIndex:
    def __init__(self, tw: dict):
        self.alias_to_window: dict[str, dict] = {}
        self.windows: dict[str, dict] = {}
        for w in tw["peak_periods"]:
            canonical = str(w["canonical"])
            self.windows[canonical] = w
            self.alias_to_window[_norm(canonical)] = w
            for alias in w.get("aliases", []):
                self.alias_to_window[_norm(alias)] = w
        # default target for ambiguous 'rush hour'
        self.rush_default = self.windows.get("PM Peak")


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s.lower().strip())


@lru_cache(maxsize=1)
def _index() -> _TimeIndex:
    return _TimeIndex(load_time_windows())


def _build(window: dict, matched_on: str, conf: MatchConfidence, note: str = "") -> ResolvedTimeWindow:
    return ResolvedTimeWindow(
        canonical=str(window["canonical"]),
        column=str(window.get("column", "peak_period")),
        hours_of_day=tuple(int(h) for h in window.get("hours_of_day", [])),
        matched_on=matched_on,
        confidence=conf,
        assumption_note=note,
    )


def resolve_time(question: str) -> TimeResolution:
    """Resolve a single peak-period reference in the question, if present."""
    idx = _index()
    q = _norm(question)

    # Longest aliases first so 'pm peak' wins over 'peak'.
    aliases = sorted(idx.alias_to_window.keys(), key=len, reverse=True)
    for alias in aliases:
        if re.search(r"(?<![a-z])" + re.escape(alias) + r"(?![a-z])", q):
            window = idx.alias_to_window[alias]
            # 'rush hour' / bare 'rush' is AM/PM ambiguous unless qualified
            if alias in ("rush hour", "rush"):
                if re.search(r"\b(morning|am)\b", q):
                    return TimeResolution(_build(idx.windows["AM Peak"], alias, MatchConfidence.HIGH), MatchConfidence.HIGH)
                if re.search(r"\b(evening|afternoon|pm)\b", q):
                    return TimeResolution(_build(idx.windows["PM Peak"], alias, MatchConfidence.HIGH), MatchConfidence.HIGH)
                return TimeResolution(
                    _build(idx.rush_default, alias, MatchConfidence.LOW,
                           note="ambiguous 'rush hour' defaulted to PM Peak"),
                    MatchConfidence.LOW,
                )
            conf = MatchConfidence.EXACT if alias == _norm(window["canonical"]) else MatchConfidence.HIGH
            return TimeResolution(_build(window, alias, conf), conf)

    return TimeResolution(window=None, confidence=MatchConfidence.NONE)
