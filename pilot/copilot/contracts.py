"""
copilot/contracts.py
Typed objects passed between pipeline stages — the internal API.

The single most important invariant in the project lives here: `ValidatedQuery`
has a private marker that only the validator can set. The executor refuses to
run anything that is not a genuinely-validated query. This makes "only validated
SQL reaches the database" a property of the type system, not of discipline.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

# A process-unique sentinel. The validator imports and stamps this onto every
# ValidatedQuery it produces. Code outside this module cannot fabricate a
# ValidatedQuery that the executor will accept, because it cannot supply the
# sentinel (it is checked by identity, not value).
_VALIDATION_SENTINEL = object()


class RejectionReason(str, Enum):
    """Why a candidate SQL was rejected. Stored for logging and tests."""

    EMPTY = "empty_sql"
    PARSE_ERROR = "parse_error"
    MULTIPLE_STATEMENTS = "multiple_statements"
    NOT_A_SELECT = "not_a_select"
    DISALLOWED_STATEMENT_TYPE = "disallowed_statement_type"
    TABLE_NOT_ALLOWLISTED = "table_not_allowlisted"
    TABLE_BLOCKED_PATTERN = "table_blocked_pattern"
    LIMIT_EXCEEDS_MAX = "limit_exceeds_max"
    FORBIDDEN_CONSTRUCT = "forbidden_construct"


@dataclass(frozen=True)
class CandidateSQL:
    """Raw SQL proposed for execution (later: emitted by the NL->SQL stage)."""

    sql: str


@dataclass(frozen=True)
class ValidatedQuery:
    """SQL proven safe by the validator.

    Do NOT construct directly. Use validator.validate(). The executor verifies
    `_marker is _VALIDATION_SENTINEL` and raises otherwise, so a hand-built
    instance cannot be executed.
    """

    sql: str                       # normalized, LIMIT-enforced SQL
    referenced_tables: tuple[str, ...]
    enforced_limit: int
    _marker: Any = field(default=None, repr=False, compare=False)

    def assert_validated(self) -> None:
        if self._marker is not _VALIDATION_SENTINEL:
            raise PermissionError(
                "ValidatedQuery was not produced by the validator; refusing to execute."
            )


@dataclass(frozen=True)
class ValidationResult:
    """Outcome of validation. Exactly one of `query` / `reason` is set."""

    ok: bool
    query: ValidatedQuery | None = None
    reason: RejectionReason | None = None
    detail: str = ""

    @property
    def rejected(self) -> bool:
        return not self.ok


@dataclass(frozen=True)
class QueryResult:
    """Result set returned by the executor."""

    columns: tuple[str, ...]
    rows: tuple[tuple[Any, ...], ...]
    row_count: int
    sql: str

    @property
    def is_empty(self) -> bool:
        return self.row_count == 0


# --------------------------------------------------------------------------- #
# Resolver contracts (deterministic semantic-resolution stage)
# Every resolver returns one of these typed objects, never a bare dict.
# `confidence` lets the pipeline fail closed: low confidence -> treat as a miss.
# --------------------------------------------------------------------------- #

from enum import Enum as _Enum  # local alias to avoid touching earlier imports


class MatchConfidence(str, _Enum):
    EXACT = "exact"      # canonical/alias/long-name direct hit
    HIGH = "high"        # normalized token match
    LOW = "low"          # weak/partial signal -> caller should fail closed
    NONE = "none"        # no match


@dataclass(frozen=True)
class ResolvedRoute:
    canonical: str               # route_short_name as stored ('004', 'R4')
    route_id: int
    route_long_name: str
    route_type: str
    matched_on: str              # the surface phrase that matched
    confidence: MatchConfidence


@dataclass(frozen=True)
class RouteResolution:
    """Result of resolving route mentions in a question."""
    routes: tuple[ResolvedRoute, ...]
    unresolved_terms: tuple[str, ...]    # route-like phrases that did NOT resolve

    @property
    def ok(self) -> bool:
        return len(self.routes) > 0 and len(self.unresolved_terms) == 0

    @property
    def has_unresolved(self) -> bool:
        return len(self.unresolved_terms) > 0


@dataclass(frozen=True)
class ResolvedMetric:
    name: str                    # canonical metric key from metrics.yaml
    label: str
    owning_table: str
    column: str
    direction: str               # higher_is_better | lower_is_better | neutral
    sort_for_superlative: str | None   # 'ASC' | 'DESC' | None, given the phrasing
    matched_on: str
    confidence: MatchConfidence
    in_scope: bool               # False -> resolver found it but it's gated off


@dataclass(frozen=True)
class MetricResolution:
    metric: ResolvedMetric | None
    confidence: MatchConfidence

    @property
    def ok(self) -> bool:
        return self.metric is not None and self.confidence in (
            MatchConfidence.EXACT, MatchConfidence.HIGH
        )


@dataclass(frozen=True)
class ResolvedTimeWindow:
    canonical: str               # 'AM Peak' | 'PM Peak' | 'Off Peak'
    column: str                  # 'peak_period'
    hours_of_day: tuple[int, ...]
    matched_on: str
    confidence: MatchConfidence
    assumption_note: str = ""    # e.g. 'rush hour' defaulted to PM Peak


@dataclass(frozen=True)
class TimeResolution:
    window: ResolvedTimeWindow | None
    confidence: MatchConfidence

    @property
    def ok(self) -> bool:
        return self.window is not None and self.confidence in (
            MatchConfidence.EXACT, MatchConfidence.HIGH
        )


class RefusalCategory(str, _Enum):
    RIDERSHIP = "ridership"
    SKYTRAIN_OR_NONBUS = "skytrain_or_nonbus"
    ON_TIME_PERFORMANCE = "on_time_performance"
    MULTI_DAY_TREND = "multi_day_trend"
    NON_TOP20_ROUTE = "non_top20_route"
    UNSUPPORTED_METRIC = "unsupported_metric"


@dataclass(frozen=True)
class RefusalResult:
    refuse: bool
    category: RefusalCategory | None = None
    reason: str = ""
    matched_terms: tuple[str, ...] = ()


# --------------------------------------------------------------------------- #
# Telemetry: token usage (optional; populated from Responses API when available)
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class TokenUsage:
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0

    def __add__(self, other: "TokenUsage") -> "TokenUsage":
        return TokenUsage(
            self.input_tokens + other.input_tokens,
            self.output_tokens + other.output_tokens,
            self.total_tokens + other.total_tokens,
        )


# --------------------------------------------------------------------------- #
# Generation + narration contracts (model-calling stages)
# --------------------------------------------------------------------------- #

class GenerationStatus(str, _Enum):
    OK = "ok"                        # CandidateSQL produced
    REFUSED = "refused"              # refusal layer triggered
    LOW_CONFIDENCE = "low_confidence"  # resolver confidence too low -> fail closed
    AMBIGUOUS_TIME = "ambiguous_time"  # time reference present but ambiguous
    MODEL_ERROR = "model_error"      # API/transport failure
    EMPTY_OUTPUT = "empty_output"    # model returned no usable SQL


@dataclass(frozen=True)
class SQLGenerationResult:
    """Outcome of the NL->SQL stage. Exactly one of `candidate` / failure set.

    The candidate is UNVALIDATED by design: nl_to_sql never runs the validator.
    The caller MUST pass `candidate` through validator.validate() before execution.
    """
    status: GenerationStatus
    candidate: CandidateSQL | None = None
    refusal: "RefusalResult | None" = None
    detail: str = ""
    usage: "TokenUsage | None" = None
    latency_ms: float | None = None

    @property
    def ok(self) -> bool:
        return self.status is GenerationStatus.OK and self.candidate is not None


class NarrationStatus(str, _Enum):
    OK = "ok"
    EMPTY_RESULT = "empty_result"        # result set had no rows -> stated plainly
    MALFORMED_RESULT = "malformed_result"  # missing columns/rows -> fail closed
    MODEL_ERROR = "model_error"


@dataclass(frozen=True)
class NarratedAnswer:
    """Final analyst-style answer. Carries the SQL for transparency."""
    status: NarrationStatus
    answer_text: str
    sql: str
    row_count: int
    detail: str = ""
    usage: "TokenUsage | None" = None
    latency_ms: float | None = None

    @property
    def ok(self) -> bool:
        return self.status in (NarrationStatus.OK, NarrationStatus.EMPTY_RESULT)


# --------------------------------------------------------------------------- #
# Pipeline orchestration contracts
# --------------------------------------------------------------------------- #

class PipelineStatus(str, _Enum):
    SUCCESS = "success"                  # full path completed, answer produced
    EMPTY_RESULT = "empty_result"        # validated+executed, but no rows
    REFUSED = "refused"                  # refusal layer (out-of-scope)
    LOW_CONFIDENCE = "low_confidence"    # resolver confidence too low
    AMBIGUOUS_TIME = "ambiguous_time"    # ambiguous time reference
    GENERATION_ERROR = "generation_error"  # model error / empty SQL output
    VALIDATION_FAILED = "validation_failed"  # SQL rejected by validator
    EXECUTION_ERROR = "execution_error"  # DuckDB/execution failure
    NARRATION_ERROR = "narration_error"  # malformed result / narrator failure


@dataclass(frozen=True)
class PipelineResult:
    """Single typed result for the whole NL -> answer sequence.

    `validated_sql` is only ever populated AFTER the validator approved it, so
    its presence is itself evidence the trust boundary was honored.
    """
    status: PipelineStatus
    question: str
    generated_sql: str | None = None     # raw model output (unvalidated)
    validated_sql: str | None = None     # only set if validator approved
    answer_text: str | None = None
    row_count: int | None = None
    # stage-specific error detail (exactly one is typically set)
    refusal_reason: str = ""
    validation_reason: str = ""
    execution_error: str = ""
    narration_error: str = ""
    detail: str = ""
    # telemetry (best-effort; populated on the live path)
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    latency_ms: float | None = None

    @property
    def ok(self) -> bool:
        return self.status in (PipelineStatus.SUCCESS, PipelineStatus.EMPTY_RESULT)
