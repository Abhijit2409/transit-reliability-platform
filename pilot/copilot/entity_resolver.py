"""
copilot/entity_resolver.py
Deterministic resolution of route mentions to canonical route_short_name.

No model calls. Matches user phrases ("R4", "99 B-Line", "Broadway B-Line",
"route 49") against the catalog in entities.yaml. Fails closed: a phrase that
looks like a route reference but matches nothing is reported as unresolved
(the pipeline then routes it to refusal), never silently dropped.
"""

from __future__ import annotations

import re
from functools import lru_cache

from config.loaders import load_entities
from copilot.contracts import (
    MatchConfidence,
    ResolvedRoute,
    RouteResolution,
)

# Tokens that, when adjacent to a number, signal a route reference even if the
# bare number isn't otherwise matched (used only to detect *unresolved* routes).
_ROUTE_CUE = re.compile(r"\b(route|bus|line|the)\s+([a-z]?\d{1,3}[a-z]?)\b", re.I)
# Standalone route-code-shaped tokens, e.g. R4, 099, 49, R5.
_ROUTE_CODE = re.compile(r"\b([a-z]?\d{1,3})\b", re.I)


class _RouteIndex:
    """Precomputed lookup tables built once from entities.yaml."""

    def __init__(self, entities: dict):
        self.by_alias: dict[str, dict] = {}
        self.by_long_name: dict[str, dict] = {}
        self.by_canonical: dict[str, dict] = {}
        self.numeric_aliases: set[str] = set()

        for row in entities["routes"]["catalog"]:
            canonical = str(row["canonical"])
            self.by_canonical[canonical.lower()] = row
            self.by_long_name[_norm(row["route_long_name"])] = row
            for alias in row.get("aliases", []):
                a = _norm(alias)
                self.by_alias[a] = row
                # track which aliases are purely numeric/short codes
                if re.fullmatch(r"[a-z]?\d{1,3}", a):
                    self.numeric_aliases.add(a)
            # canonical itself is a valid match key
            self.by_alias[_norm(canonical)] = row

    def lookup(self, phrase: str) -> dict | None:
        p = _norm(phrase)
        if p in self.by_alias:
            return self.by_alias[p]
        if p in self.by_long_name:
            return self.by_long_name[p]
        # zero-pad numeric: '49' -> '049'
        if re.fullmatch(r"\d{1,3}", p):
            for cand in (p, p.zfill(3)):
                if cand in self.by_alias:
                    return self.by_alias[cand]
        return None


def _norm(s: str) -> str:
    """Lowercase, collapse whitespace, strip surrounding punctuation."""
    s = s.lower().strip()
    s = re.sub(r"\s+", " ", s)
    s = s.strip(".,;:!?\"'()")
    return s


@lru_cache(maxsize=1)
def _index() -> _RouteIndex:
    return _RouteIndex(load_entities())


def _to_resolved(row: dict, matched_on: str, conf: MatchConfidence) -> ResolvedRoute:
    return ResolvedRoute(
        canonical=str(row["canonical"]),
        route_id=int(row["route_id"]),
        route_long_name=str(row["route_long_name"]),
        route_type=str(row["route_type"]),
        matched_on=matched_on,
        confidence=conf,
    )


def resolve_routes(question: str) -> RouteResolution:
    """Find and resolve every route reference in `question`.

    Strategy:
      1. Try multi-word long-name phrases (e.g. 'Broadway B-Line') — longest first.
      2. Try cued references ('route 49', 'the R4').
      3. Try standalone codes ('R4', '099', '49').
    Anything route-shaped that fails all three is reported unresolved.
    """
    idx = _index()
    q = " " + question.strip() + " "
    resolved: dict[str, ResolvedRoute] = {}   # keyed by canonical, dedupes
    consumed_spans: list[tuple[int, int]] = []

    # 1. long-name / multi-word alias phrases (longest aliases first to avoid
    #    'broadway' shadowing 'broadway b-line')
    phrase_keys = sorted(
        set(list(idx.by_long_name.keys()) + [k for k in idx.by_alias if " " in k]),
        key=len,
        reverse=True,
    )
    lowered = q.lower()

    def _overlaps(span: tuple[int, int]) -> bool:
        return any(span[0] < e and s < span[1] for s, e in consumed_spans)

    for key in phrase_keys:
        if not key:
            continue
        pat = re.compile(r"(?<![a-z0-9])" + re.escape(key) + r"(?![a-z0-9])", re.I)
        for m in pat.finditer(lowered):
            # A shorter phrase fully inside an already-consumed longer phrase
            # (e.g. 'broadway' inside 'broadway b-line') is a false positive.
            if _overlaps(m.span()):
                continue
            row = idx.by_long_name.get(key) or idx.by_alias.get(key)
            if row:
                rr = _to_resolved(row, key, MatchConfidence.EXACT)
                resolved.setdefault(rr.canonical, rr)
                consumed_spans.append(m.span())

    # 2. cued references: 'route 49', 'bus R4', 'the 99'
    for m in _ROUTE_CUE.finditer(q):
        if _overlaps(m.span()):
            continue
        code = m.group(2)
        row = idx.lookup(code)
        if row:
            rr = _to_resolved(row, m.group(0).strip(), MatchConfidence.EXACT)
            resolved.setdefault(rr.canonical, rr)
            consumed_spans.append(m.span())

    # 3. standalone codes: 'R4', '099', '49'
    unresolved: set[str] = set()
    for m in _ROUTE_CODE.finditer(q):
        if _overlaps(m.span()):
            continue
        token = m.group(1)
        # ignore pure 4-digit+ numbers and obvious non-routes handled above
        row = idx.lookup(token)
        if row:
            rr = _to_resolved(row, token, MatchConfidence.HIGH)
            resolved.setdefault(rr.canonical, rr)
        else:
            # Only flag as unresolved-route if it was explicitly cued as a route
            # somewhere, OR it is a letter+digits code shape (e.g. 'R9', 'B12').
            if re.fullmatch(r"[a-z]\d{1,3}", token, re.I):
                unresolved.add(token)

    # cued-but-unmatched numbers are unresolved routes (e.g. 'route 250' style)
    for m in _ROUTE_CUE.finditer(q):
        code = m.group(2)
        if not idx.lookup(code):
            unresolved.add(code)

    return RouteResolution(
        routes=tuple(resolved.values()),
        unresolved_terms=tuple(sorted(unresolved)),
    )
