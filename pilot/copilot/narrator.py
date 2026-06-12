"""
copilot/narrator.py
QueryResult -> concise analyst-style answer.

Responsibilities and boundaries:
  - Accept the user question, the validated SQL string, and a QueryResult.
  - Produce a short answer that may reference ONLY values present in the result.
  - Fail closed if the QueryResult is malformed (rows wider/narrower than the
    column header, or non-tuple rows).
  - State plainly when the result set is empty.
  - Carry the SQL on the returned object for transparency.

This module NEVER validates SQL and NEVER executes anything. The model is given
the result set as DATA and is instructed it may not introduce any number, route,
metric, date, or comparison not present in that data. As a structural backstop,
the result is serialized deterministically and handed to the model as the sole
source of facts.
"""

from __future__ import annotations

import os
import re
import time

from openai import OpenAI, OpenAIError

from config.loaders import load_guardrail_config
from copilot._usage import _extract_usage
from copilot.contracts import (
    NarratedAnswer,
    NarrationStatus,
    QueryResult,
)

from config.model_config import NARRATOR_MODEL as _MODEL, NARRATOR_TEMPERATURE as _TEMPERATURE
_MAX_ROWS_IN_PROMPT = 100  # matches the validator's LIMIT cap

_INSTRUCTIONS = (
    "You are a transit data analyst. You are given a user question and the EXACT "
    "result rows of a SQL query that has already been run. Write a concise, plain "
    "answer (1-3 sentences).\n\n"
    "ABSOLUTE RULES:\n"
    "1. Use ONLY numbers and labels that appear verbatim in the RESULT ROWS. "
    "Never invent, estimate, round beyond what is shown, or infer a value.\n"
    "2. Do NOT introduce any route, metric, date, or comparison that is not present "
    "in the result rows.\n"
    "3. ALWAYS state the route identifier exactly as it appears in the result "
    "(e.g. '099', 'R4') — use that exact token, not just a descriptive name like "
    "'the Broadway B-Line'. If a row has a route identifier column, it MUST appear "
    "in your answer.\n"
    "4. ALWAYS include the key numeric value(s) for each row verbatim (scores, "
    "rates, counts, priority values). Do not describe a value qualitatively in "
    "place of stating the number.\n"
    "5. Do NOT explain the SQL or mention tables/columns by their internal names.\n"
    "6. If the result has exactly one row/value, state the identifier and its "
    "value(s) directly. If it is a ranking, list the rows in the given order, each "
    "with its identifier and value.\n"
    "7. Keep it factual and brief. No speculation about causes or recommendations "
    "unless the data explicitly contains them.\n"
    "If the result rows are empty, say that the query returned no matching data."
)


def _validate_shape(result: QueryResult) -> str | None:
    """Return an error string if the QueryResult is malformed, else None."""
    if not isinstance(result.columns, tuple):
        return "columns is not a tuple"
    if not isinstance(result.rows, tuple):
        return "rows is not a tuple"
    if result.row_count != len(result.rows):
        return f"row_count {result.row_count} != len(rows) {len(result.rows)}"
    ncols = len(result.columns)
    # Only enforce width when there are columns to enforce against.
    if ncols > 0:
        for i, row in enumerate(result.rows):
            if not isinstance(row, tuple):
                return f"row {i} is not a tuple"
            if len(row) != ncols:
                return f"row {i} width {len(row)} != column count {ncols}"
    return None


def _required_anchors(result: QueryResult) -> tuple[list[str], list[str]]:
    """Deterministically extract the route identifiers and numeric values that
    MUST appear in the answer, drawn directly from the result rows.

    Returns (route_tokens, numeric_values), each de-duplicated and order-preserved.
    Route identifiers are taken from a route_short_name column when present;
    numeric values are every int/float cell rendered as it will appear.
    """
    route_tokens: list[str] = []
    numeric_values: list[str] = []
    seen_routes: set[str] = set()
    seen_nums: set[str] = set()

    cols = result.columns
    # locate a route-identifier column if one exists
    route_idx = None
    for i, c in enumerate(cols):
        if c.lower() == "route_short_name":
            route_idx = i
            break

    for row in result.rows:
        for i, val in enumerate(row):
            if val is None:
                continue
            if i == route_idx:
                tok = str(val)
                if tok not in seen_routes:
                    seen_routes.add(tok)
                    route_tokens.append(tok)
            elif isinstance(val, (int, float)) and not isinstance(val, bool):
                rendered = str(val)
                if rendered not in seen_nums:
                    seen_nums.add(rendered)
                    numeric_values.append(rendered)
    return route_tokens, numeric_values


def _enforce_anchors(answer_text: str, result: QueryResult) -> str:
    """Deterministic post-generation guarantee.

    Compute the route identifiers and numeric values that MUST appear (from the
    result rows), find any missing verbatim from the model's answer, and append
    them as a plain sentence. This does NOT call the model and does NOT alter
    correct answers — it only adds values that are already in the result but
    that the model failed to echo. The result is that anchors can never be
    missing from the final answer_text regardless of model phrasing.
    """
    route_tokens, numeric_values = _required_anchors(result)
    required = route_tokens + numeric_values
    if not required:
        return answer_text

    missing = [tok for tok in required if tok not in answer_text]
    if not missing:
        return answer_text

    suffix = "Result values: " + ", ".join(missing) + "."
    if answer_text and not answer_text.rstrip().endswith((".", "!", "?")):
        answer_text = answer_text.rstrip() + "."
    return (answer_text + " " + suffix).strip() if answer_text else suffix


def _serialize_result(result: QueryResult) -> str:
    """Deterministic, compact rendering of the result as the model's only facts."""
    header = " | ".join(result.columns) if result.columns else "(no columns)"
    lines = [f"COLUMNS: {header}", "RESULT ROWS:"]
    if result.row_count == 0:
        lines.append("(no rows)")
        return "\n".join(lines)
    for row in result.rows[:_MAX_ROWS_IN_PROMPT]:
        lines.append(" | ".join("" if v is None else str(v) for v in row))
    if result.row_count > _MAX_ROWS_IN_PROMPT:
        lines.append(f"... ({result.row_count - _MAX_ROWS_IN_PROMPT} more rows)")
    return "\n".join(lines)


def _client() -> OpenAI:
    return OpenAI()


def narrate(
    question: str,
    sql: str,
    result: QueryResult,
    client: OpenAI | None = None,
) -> NarratedAnswer:
    """Turn a QueryResult into an analyst answer. Fail closed on malformed input."""
    # 1. fail closed on malformed result
    shape_error = _validate_shape(result)
    if shape_error is not None:
        return NarratedAnswer(
            status=NarrationStatus.MALFORMED_RESULT,
            answer_text="I couldn't produce an answer because the query result was malformed.",
            sql=sql,
            row_count=getattr(result, "row_count", 0),
            detail=shape_error,
        )

    # 2. empty result -> deterministic message, no model needed
    if result.row_count == 0:
        return NarratedAnswer(
            status=NarrationStatus.EMPTY_RESULT,
            answer_text="The query ran successfully but returned no matching data.",
            sql=sql,
            row_count=0,
        )

    # 3. narrate from the result rows only
    cfg = load_guardrail_config()
    route_tokens, numeric_values = _required_anchors(result)
    must_mention_parts: list[str] = []
    if route_tokens:
        must_mention_parts.append(
            "route identifiers (use each EXACTLY as written): "
            + ", ".join(route_tokens)
        )
    if numeric_values:
        must_mention_parts.append(
            "numeric values (state each EXACTLY as written): "
            + ", ".join(numeric_values)
        )
    must_mention = ""
    if must_mention_parts:
        must_mention = (
            "\n\nMUST MENTION — your answer must contain every one of these "
            "tokens verbatim:\n- " + "\n- ".join(must_mention_parts)
        )

    user_input = (
        f"USER QUESTION: {question.strip()}\n\n{_serialize_result(result)}{must_mention}"
    )
    try:
        _t0 = time.perf_counter()
        resp = (client or _client()).responses.create(
            model=_MODEL,
            temperature=_TEMPERATURE,
            max_output_tokens=cfg.max_tokens_per_call,
            instructions=_INSTRUCTIONS,
            input=user_input,
        )
        _latency_ms = (time.perf_counter() - _t0) * 1000.0
    except OpenAIError as e:
        return NarratedAnswer(
            status=NarrationStatus.MODEL_ERROR,
            answer_text="I couldn't generate an answer due to a model error.",
            sql=sql,
            row_count=result.row_count,
            detail=str(e),
        )

    _usage = _extract_usage(resp)
    text = (getattr(resp, "output_text", "") or "").strip()
    if not text:
        return NarratedAnswer(
            status=NarrationStatus.MODEL_ERROR,
            answer_text="I couldn't generate an answer (empty model output).",
            sql=sql,
            row_count=result.row_count,
            detail="empty output_text",
            usage=_usage, latency_ms=_latency_ms,
        )

    final_text = _enforce_anchors(text, result)
    return NarratedAnswer(
        status=NarrationStatus.OK,
        answer_text=final_text,
        sql=sql,
        row_count=result.row_count,
        usage=_usage, latency_ms=_latency_ms,
    )
