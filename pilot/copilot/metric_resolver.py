"""
copilot/metric_resolver.py
Deterministic resolution of business phrases to canonical metrics + sort direction.

No model calls. Uses metrics.yaml. Maps phrases like "worst reliability",
"most bunching", "priority corridor", "severe bunching" to a metric and — when
the phrasing is superlative ("worst", "highest", "least") — the correct SQL
sort direction given the metric's polarity. This is where the reliability_score
direction trap is handled: "worst reliability" -> reliability_score ASC.

Fails closed: a phrase with no confident metric match returns ok=False.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache

from config.loaders import load_metrics
from copilot.contracts import (
    MatchConfidence,
    MetricResolution,
    ResolvedMetric,
)

# Quality words: meaning depends on the metric's polarity (best vs worst).
_QUALITY_GOOD = {"best", "strongest"}
_QUALITY_BAD = {"worst", "poorest", "weakest"}
# Magnitude words: literal high/low of the value, polarity-independent.
_MAG_HIGH = {"most", "highest", "top", "greatest", "biggest", "maximum"}
_MAG_LOW = {"least", "lowest", "bottom", "minimum"}

# Aggregates used by the matcher regex.
_WORST_WORDS = _QUALITY_BAD | _MAG_LOW
_BEST_WORDS = _QUALITY_GOOD | _MAG_HIGH

_SUPERLATIVE_RE = re.compile(
    r"\b(" + "|".join(sorted(_QUALITY_GOOD | _QUALITY_BAD | _MAG_HIGH | _MAG_LOW)) + r")\b",
    re.I,
)

# Implicit-metric cues: a "performance" question during a "peak" window means
# peak bunching even though no metric word appears.
_PERFORMANCE_CUE = re.compile(r"\b(perform|performs|performing|performance|do|does|doing)\b", re.I)
_PEAK_CUE = re.compile(r"\b(am peak|pm peak|off peak|peak|rush)\b", re.I)


@dataclass(frozen=True)
class _MetricRow:
    name: str
    label: str
    owning_table: str
    column: str
    direction: str
    in_scope: bool
    phrases: tuple[str, ...]   # all matchable synonyms + label, normalized


class _MetricIndex:
    def __init__(self, metrics: dict):
        self.rows: list[_MetricRow] = []
        # invert _EXTRA_PHRASES: metric_name -> [extra phrases]
        extra_by_metric: dict[str, list[str]] = {}
        for phrase, metric_name in _EXTRA_PHRASES.items():
            extra_by_metric.setdefault(metric_name, []).append(phrase)

        for name, m in metrics["metrics"].items():
            phrases = set()
            phrases.add(_norm(name.replace("_", " ")))
            if m.get("label"):
                phrases.add(_norm(m["label"]))
            for s in m.get("synonyms", []) or []:
                phrases.add(_norm(s))
            for s in extra_by_metric.get(name, []):
                phrases.add(_norm(s))
            # worst_means / best_means text is documentation, not matchable
            self.rows.append(
                _MetricRow(
                    name=name,
                    label=str(m.get("label", name)),
                    owning_table=str(m.get("owning_table", "")),
                    column=str(m.get("column", "")),
                    direction=str(m.get("direction", "neutral")),
                    in_scope=bool(m.get("in_scope", True)),
                    phrases=tuple(sorted(phrases, key=len, reverse=True)),
                )
            )


# Morphological / phrasing variants that natural questions use but that aren't
# literal synonyms in metrics.yaml. Maps an extra matchable phrase -> metric name.
# Keeps metrics.yaml clean (definitions) while handling adjective/verb forms.
_EXTRA_PHRASES: dict[str, str] = {
    "reliable": "reliability_score",
    "reliability": "reliability_score",
    "unreliable": "reliability_score",
    "prioritize": "intervention_priority_score",
    "prioritise": "intervention_priority_score",
    "priority corridor": "intervention_priority_score",
    "priority corridors": "intervention_priority_score",
    "intervene": "intervention_priority_score",
    "bunch": "bunching_rate_pct",
    "bunched": "bunching_rate_pct",
    "bunching rate": "bunching_rate_pct",
}


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s.lower().strip())


@lru_cache(maxsize=1)
def _index() -> _MetricIndex:
    return _MetricIndex(load_metrics())


def _superlative_sort(direction: str, question_norm: str) -> str | None:
    """Given metric polarity and the question's superlative word, return the SQL
    ORDER BY direction, or None if no superlative is present.

    Two word classes:
      MAGNITUDE ('most','highest' / 'least','lowest') -> literal value extreme,
        polarity-independent. "most bunching" = highest value = DESC, even
        though bunching is lower_is_better.
      QUALITY ('best' / 'worst') -> polarity-aware.
        higher_is_better: best->DESC, worst->ASC
        lower_is_better:  best->ASC,  worst->DESC
    """
    m = _SUPERLATIVE_RE.search(question_norm)
    if not m:
        return None
    word = m.group(1).lower()

    if word in _MAG_HIGH:
        return "DESC"
    if word in _MAG_LOW:
        return "ASC"

    wants_bad = word in _QUALITY_BAD
    if direction == "higher_is_better":
        return "ASC" if wants_bad else "DESC"
    if direction == "lower_is_better":
        return "DESC" if wants_bad else "ASC"
    # neutral metric with a quality word: best-effort literal
    return "DESC"


def resolve_metric(question: str) -> MetricResolution:
    """Resolve the primary metric referenced in the question.

    Picks the longest matching synonym across all metrics (longer phrase = more
    specific = higher confidence). Ties broken by match length.
    """
    idx = _index()
    q = _norm(question)

    best_row: _MetricRow | None = None
    best_phrase = ""
    for row in idx.rows:
        for phrase in row.phrases:
            if not phrase:
                continue
            if re.search(r"(?<![a-z])" + re.escape(phrase) + r"(?![a-z])", q):
                if len(phrase) > len(best_phrase):
                    best_phrase = phrase
                    best_row = row

    if best_row is None:
        # Implicit-metric fallback: a question about how routes "perform" during
        # a peak period names no metric explicitly, but the intended measure is
        # peak bunching. Only triggers when BOTH a performance cue and a peak
        # reference are present, so it does not broaden behavior elsewhere.
        if _PERFORMANCE_CUE.search(q) and _PEAK_CUE.search(q):
            for row in idx.rows:
                if row.name == "bunching_events_hourly":
                    sort_dir = _superlative_sort(row.direction, q) or "DESC"
                    resolved = ResolvedMetric(
                        name=row.name,
                        label=row.label,
                        owning_table=row.owning_table,
                        column=row.column,
                        direction=row.direction,
                        sort_for_superlative=sort_dir,
                        matched_on="implicit:performance+peak",
                        confidence=MatchConfidence.HIGH,
                        in_scope=row.in_scope,
                    )
                    return MetricResolution(metric=resolved, confidence=MatchConfidence.HIGH)
        return MetricResolution(metric=None, confidence=MatchConfidence.NONE)

    # Confidence: multi-word specific phrase -> EXACT; single short word -> HIGH.
    conf = MatchConfidence.EXACT if " " in best_phrase or len(best_phrase) >= 10 else MatchConfidence.HIGH
    sort_dir = _superlative_sort(best_row.direction, q)

    resolved = ResolvedMetric(
        name=best_row.name,
        label=best_row.label,
        owning_table=best_row.owning_table,
        column=best_row.column,
        direction=best_row.direction,
        sort_for_superlative=sort_dir,
        matched_on=best_phrase,
        confidence=conf,
        in_scope=best_row.in_scope,
    )
    return MetricResolution(metric=resolved, confidence=conf)
