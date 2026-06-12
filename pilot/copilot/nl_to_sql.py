"""
copilot/nl_to_sql.py
Natural-language question -> CandidateSQL (UNVALIDATED, UNEXECUTED).

Responsibilities (and hard boundaries):
  - Run the deterministic resolvers (refusal, entity, metric, time).
  - Fail closed when: refusal triggers, entity confidence is too low, metric
    confidence is too low, or a time reference is present but ambiguous.
  - Build a deterministic SQL-generation prompt restricted to the 5 allowlisted
    top20_* tables, grounded in schema_descriptions.yaml + the resolved context.
  - Call the OpenAI Responses API and return a CandidateSQL.

This module NEVER validates and NEVER executes SQL. The caller must pass the
returned CandidateSQL through validator.validate() before execution. Output is
SQL only — never narrative.
"""

from __future__ import annotations

import os
import re
import time as _time

import yaml
from openai import OpenAI, OpenAIError

from config.loaders import (
    load_entities,
    load_guardrail_config,
    load_metrics,
    load_time_windows,
)
from copilot.contracts import (
    CandidateSQL,
    GenerationStatus,
    MatchConfidence,
    MetricResolution,
    RouteResolution,
    SQLGenerationResult,
    TimeResolution,
)
from copilot._usage import _extract_usage
from copilot.entity_resolver import resolve_routes
from copilot.metric_resolver import resolve_metric
from copilot.refusal import check_refusal
from copilot.time_resolver import resolve_time

# --- model settings (pinned snapshot via config; env-overridable) --- #
from config.model_config import SQL_MODEL as _MODEL, SQL_TEMPERATURE as _TEMPERATURE

# Confidence floors. EXACT/HIGH pass; LOW/NONE fail closed.
_ACCEPTABLE = (MatchConfidence.EXACT, MatchConfidence.HIGH)

_SCHEMA_DIR_FILE = "schema_descriptions.yaml"

# Strip accidental markdown fences / leading "sql" the model may emit.
_FENCE_RE = re.compile(r"^\s*```(?:sql)?\s*|\s*```\s*$", re.I | re.M)


def _schema_path() -> str:
    from pathlib import Path
    return str(Path(__file__).resolve().parent.parent / "semantic" / _SCHEMA_DIR_FILE)


def _load_schema_descriptions() -> dict:
    with open(_schema_path()) as f:
        data = yaml.safe_load(f)
    if not isinstance(data, dict) or "tables" not in data:
        raise ValueError("schema_descriptions.yaml malformed: missing 'tables'")
    return data


def _exposed_schema_text(schema: dict, allowed: frozenset[str]) -> str:
    """Render ONLY allowlisted tables and ONLY exposed columns for the prompt."""
    lines: list[str] = []
    global_notes = schema.get("global_notes", {})
    if global_notes:
        lines.append("CRITICAL DATA NOTES:")
        for k, v in global_notes.items():
            lines.append(f"- {k}: {' '.join(str(v).split())}")
        lines.append("")
    for tname, tinfo in schema["tables"].items():
        if tname not in allowed:
            continue
        lines.append(f"TABLE {tname}  (grain: {tinfo.get('grain','?')})")
        purpose = tinfo.get("purpose")
        if purpose:
            lines.append(f"  purpose: {' '.join(str(purpose).split())}")
        for col, meta in tinfo["columns"].items():
            if meta.get("expose") is False:
                continue
            desc = " ".join(str(meta.get("description", "")).split())
            role = meta.get("role", "")
            direction = meta.get("direction")
            risk = meta.get("nl_risk")
            extra = f" [{role}"
            if direction:
                extra += f", {direction}"
            extra += "]"
            line = f"    {col}{extra}: {desc}"
            if risk:
                line += f"  RISK: {' '.join(str(risk).split())}"
            lines.append(line)
        lines.append("")
    return "\n".join(lines)


def _resolved_context_text(
    routes: RouteResolution,
    metric: MetricResolution,
    time: TimeResolution,
) -> str:
    """Hand the model the deterministically-resolved facts so it does not guess."""
    parts: list[str] = ["RESOLVED CONTEXT (use these canonical values exactly):"]
    if routes.routes:
        rs = ", ".join(
            f"{r.canonical} (route_short_name='{r.canonical}', long='{r.route_long_name}')"
            for r in routes.routes
        )
        parts.append(f"- routes: {rs}")
    if metric.ok and metric.metric:
        m = metric.metric
        parts.append(
            f"- metric: {m.name} -> column '{m.column}' in table '{m.owning_table}' "
            f"(direction: {m.direction})"
        )
        if m.sort_for_superlative:
            parts.append(
                f"- ordering: ORDER BY {m.column} {m.sort_for_superlative} "
                f"(this is the correct direction for the phrasing)"
            )
    if time.ok and time.window:
        w = time.window
        parts.append(
            f"- time window: filter {w.column} = '{w.canonical}'"
            + (f"  ({w.assumption_note})" if w.assumption_note else "")
        )
    if len(parts) == 1:
        parts.append("- (no specific route/metric/time resolved; answer from the schema)")
    return "\n".join(parts)


def _build_instructions(allowed: frozenset[str], max_limit: int) -> str:
    table_list = ", ".join(sorted(allowed))
    return (
        "You are a SQL generator for a read-only DuckDB transit analytics warehouse. "
        "You translate one analyst question into exactly one DuckDB SELECT query.\n\n"
        "HARD RULES:\n"
        f"1. Query ONLY these tables: {table_list}. Never reference any other table.\n"
        "2. Output a single SELECT statement. No DDL, DML, PRAGMA, ATTACH, COPY, or multiple statements.\n"
        f"3. Always include a LIMIT no greater than {max_limit}.\n"
        "4. Use the canonical values and ordering given in RESOLVED CONTEXT verbatim.\n"
        "5. Filter routes on route_short_name using the exact canonical string (e.g. '004', 'R4').\n"
        "6. Respect the RISK notes (e.g. 'least reliable' means lowest reliability_score; "
        "smaller gap_km means worse bunching).\n"
        "7. Output SQL ONLY — no explanation, no markdown, no comments, no prose.\n"
        "If the question cannot be answered from these tables, output exactly: SELECT 1 WHERE 1=0"
    )


def _clean_sql(text: str) -> str:
    return _FENCE_RE.sub("", text or "").strip().rstrip(";").strip()


def _client() -> OpenAI:
    # API key sourced from environment by the SDK (OPENAI_API_KEY).
    return OpenAI()


def generate_sql(question: str, client: OpenAI | None = None) -> SQLGenerationResult:
    """Resolve, gate, and (if clear) generate a CandidateSQL. Fail closed."""
    q = (question or "").strip()
    if not q:
        return SQLGenerationResult(GenerationStatus.LOW_CONFIDENCE, detail="empty question")

    # 1. refusal gate (out-of-scope) — fail closed before any model call
    refusal = check_refusal(q)
    if refusal.refuse:
        return SQLGenerationResult(
            GenerationStatus.REFUSED, refusal=refusal, detail=refusal.reason
        )

    # 2. deterministic resolution
    routes = resolve_routes(q)
    metric = resolve_metric(q)
    time = resolve_time(q)

    # 3. fail-closed confidence gates
    #    - any route-shaped term that did not resolve is fatal
    if routes.has_unresolved:
        return SQLGenerationResult(
            GenerationStatus.LOW_CONFIDENCE,
            detail=f"unresolved route terms: {routes.unresolved_terms}",
        )
    #    - if routes resolved, all must clear the confidence floor
    if routes.routes and any(r.confidence not in _ACCEPTABLE for r in routes.routes):
        return SQLGenerationResult(
            GenerationStatus.LOW_CONFIDENCE, detail="route confidence below floor"
        )
    #    - a metric is required unless the question is a pure route lookup/compare
    metric_required = not routes.routes  # no route at all -> must have a metric
    if metric_required and not metric.ok:
        return SQLGenerationResult(
            GenerationStatus.LOW_CONFIDENCE, detail="no confident metric resolved"
        )
    if metric.metric and metric.metric.confidence not in _ACCEPTABLE:
        return SQLGenerationResult(
            GenerationStatus.LOW_CONFIDENCE, detail="metric confidence below floor"
        )
    #    - gated metric (in_scope: false) must not proceed
    if metric.metric and not metric.metric.in_scope:
        return SQLGenerationResult(
            GenerationStatus.REFUSED,
            detail=f"metric '{metric.metric.name}' is not in scope",
        )
    #    - a time reference that resolved only at LOW confidence is ambiguous
    if time.window is not None and time.confidence not in _ACCEPTABLE:
        return SQLGenerationResult(
            GenerationStatus.AMBIGUOUS_TIME,
            detail=f"ambiguous time reference: {time.window.assumption_note or time.window.matched_on}",
        )

    # 4. build the deterministic prompt
    cfg = load_guardrail_config()
    schema = _load_schema_descriptions()
    # touch entities/metrics/time_windows load to fail loudly if files are absent
    load_entities(); load_metrics(); load_time_windows()

    instructions = _build_instructions(cfg.allowed_tables, cfg.max_limit)
    user_input = (
        f"QUESTION: {q}\n\n"
        f"{_resolved_context_text(routes, metric, time)}\n\n"
        f"SCHEMA (allowlisted tables only):\n{_exposed_schema_text(schema, cfg.allowed_tables)}"
    )

    # 5. model call (Responses API) — SQL only
    try:
        _t0 = _time.perf_counter()
        resp = (client or _client()).responses.create(
            model=_MODEL,
            temperature=_TEMPERATURE,
            max_output_tokens=cfg.max_tokens_per_call,
            instructions=instructions,
            input=user_input,
        )
        _latency_ms = (_time.perf_counter() - _t0) * 1000.0
    except OpenAIError as e:
        return SQLGenerationResult(GenerationStatus.MODEL_ERROR, detail=str(e))

    _usage = _extract_usage(resp)
    sql = _clean_sql(getattr(resp, "output_text", "") or "")
    if not sql or not sql.upper().lstrip().startswith(("SELECT", "WITH")):
        return SQLGenerationResult(
            GenerationStatus.EMPTY_OUTPUT, detail="model did not return a SELECT",
            usage=_usage, latency_ms=_latency_ms,
        )

    return SQLGenerationResult(
        GenerationStatus.OK, candidate=CandidateSQL(sql=sql),
        usage=_usage, latency_ms=_latency_ms,
    )
