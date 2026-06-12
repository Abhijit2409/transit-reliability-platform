"""
copilot/refusal.py
Deterministic out-of-scope detection. First line of the trust story.

No model calls. Detects questions the warehouse cannot honestly answer and
returns a typed RefusalResult with the category and reason. Categories:
  - ridership            (no passenger/boarding data)
  - skytrain_or_nonbus   (bus-only warehouse)
  - on_time_performance  (no schedule-adherence metric)
  - multi_day_trend      (single service_date snapshot)
  - non_top20_route      (route mentioned but not in the Top-20 catalog)
  - unsupported_metric   (data-quality/eligibility family gated off)

Ordering: the most specific / highest-signal categories are checked first.
Fails closed in the sense that detection is conservative on the SAFE side — it
only refuses on clear out-of-scope signals, leaving genuine ambiguity to the
downstream resolvers (which themselves fail closed on low confidence).
"""

from __future__ import annotations

import re
from functools import lru_cache

from config.loaders import load_metrics
from copilot.contracts import RefusalCategory, RefusalResult
from copilot.entity_resolver import resolve_routes

# --- keyword signals per category ----------------------------------------- #

_RIDERSHIP = re.compile(
    r"\b(ridership|riders?|passengers?|boardings?|how many people|"
    r"how busy|crowding|load factor|occupancy)\b",
    re.I,
)

_NONBUS = re.compile(
    r"\b(skytrain|sky train|seabus|sea bus|west coast express|wce|"
    r"handydart|ferry|expo line|millennium line|canada line|train)\b",
    re.I,
)

_ON_TIME = re.compile(
    r"\b(on[\s-]?time|schedule adherence|against schedule|vs schedule|"
    r"versus schedule|punctual(ity)?|delay(ed|s)?\b(?!.*bunch))\b",
    re.I,
)

# Multi-day / trend language. The warehouse holds a single service_date.
_TREND = re.compile(
    r"\b(trend|over time|over the (past|last)|month(ly|s)?|week(ly|s)?|"
    r"year(ly|s)?|day[\s-]?over[\s-]?day|historically|history|"
    r"changed? over|since last|compared to (last|previous)|"
    r"past \d+ (day|week|month|year))\b",
    re.I,
)

# 'on-time'/'delay' alone could appear in legitimate bunching talk; require a
# schedule cue to avoid false positives on "delayed bus bunching".
_SCHEDULE_CUE = re.compile(r"\b(schedule|timetable|on[\s-]?time|punctual)\b", re.I)


@lru_cache(maxsize=1)
def _gated_metric_terms() -> tuple[tuple[str, str], ...]:
    """Return (synonym, metric_name) pairs for metrics flagged in_scope: false."""
    out: list[tuple[str, str]] = []
    for name, m in load_metrics()["metrics"].items():
        if not bool(m.get("in_scope", True)):
            for s in m.get("synonyms", []) or []:
                out.append((s.lower(), name))
            out.append((name.replace("_", " "), name))
    return tuple(out)


def check_refusal(question: str) -> RefusalResult:
    """Return a RefusalResult; refuse=False means the question may proceed."""
    q = question.strip()
    ql = q.lower()

    # 1. ridership / passenger data
    if (m := _RIDERSHIP.search(ql)):
        return RefusalResult(
            refuse=True,
            category=RefusalCategory.RIDERSHIP,
            reason=(
                "This warehouse is built from vehicle-position telemetry and "
                "covers bunching and reliability, not passenger counts or ridership."
            ),
            matched_terms=(m.group(0),),
        )

    # 2. non-bus modes (SkyTrain, SeaBus, ferry, ...)
    if (m := _NONBUS.search(ql)):
        return RefusalResult(
            refuse=True,
            category=RefusalCategory.SKYTRAIN_OR_NONBUS,
            reason="This warehouse covers bus routes only; the named service is out of scope.",
            matched_terms=(m.group(0),),
        )

    # 3. on-time / schedule-adherence performance
    if _SCHEDULE_CUE.search(ql) and _ON_TIME.search(ql):
        return RefusalResult(
            refuse=True,
            category=RefusalCategory.ON_TIME_PERFORMANCE,
            reason=(
                "The warehouse measures headway/bunching reliability from vehicle "
                "telemetry, not on-time performance against the published schedule."
            ),
            matched_terms=(_ON_TIME.search(ql).group(0),),
        )

    # 4. multi-day / trend (single-date scope)
    if (m := _TREND.search(ql)):
        return RefusalResult(
            refuse=True,
            category=RefusalCategory.MULTI_DAY_TREND,
            reason=(
                "The current warehouse contains a single service date, so trends "
                "or changes over time cannot be computed."
            ),
            matched_terms=(m.group(0),),
        )

    # 5. gated/unsupported metric family (e.g. data-quality / eligibility)
    for term, metric_name in _gated_metric_terms():
        if re.search(r"(?<![a-z])" + re.escape(term) + r"(?![a-z])", ql):
            return RefusalResult(
                refuse=True,
                category=RefusalCategory.UNSUPPORTED_METRIC,
                reason=(
                    f"The '{metric_name}' family is not exposed by this copilot. "
                    "Performance reliability is available; data-quality/eligibility is not."
                ),
                matched_terms=(term,),
            )

    # 6. route mentioned but not in the Top-20 catalog
    route_res = resolve_routes(q)
    if route_res.has_unresolved:
        return RefusalResult(
            refuse=True,
            category=RefusalCategory.NON_TOP20_ROUTE,
            reason=(
                "A route was referenced that is not among the Top-20 monitored "
                "routes, so there is no data for it."
            ),
            matched_terms=route_res.unresolved_terms,
        )

    return RefusalResult(refuse=False)
