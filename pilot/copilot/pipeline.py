"""
copilot/pipeline.py
The orchestration layer: one clean entry point for the full safe sequence.

    question
       -> generate_sql        (resolvers + model; UNVALIDATED CandidateSQL)
       -> validator.validate  (HARD trust boundary; nothing skips this)
       -> executor.execute     (ONLY a ValidatedQuery; read-only DuckDB)
       -> narrator.narrate     (RESULT-only narration)
       -> PipelineResult

Trust boundary (non-negotiable): generated SQL ALWAYS passes through
validator.validate() before the executor is touched. There is no code path in
this module that hands CandidateSQL — or any raw string — to the executor. The
executor only ever receives the ValidatedQuery object minted by the validator.
narrator.narrate() is only reachable after a genuine QueryResult exists.

Every stage fails closed into a typed PipelineResult. Logging is via the stdlib
logger (hooks only; no observability framework yet).
"""

from __future__ import annotations

import logging

from openai import OpenAI

from copilot import executor as executor_mod
from copilot import nl_to_sql as nl_to_sql_mod
from copilot import validator as validator_mod
from copilot import narrator as narrator_mod
from copilot.contracts import (
    GenerationStatus,
    NarrationStatus,
    PipelineResult,
    PipelineStatus,
)

logger = logging.getLogger("tic.pipeline")

# Map a generation failure status to the corresponding pipeline status.
_GEN_FAILURE_MAP = {
    GenerationStatus.REFUSED: PipelineStatus.REFUSED,
    GenerationStatus.LOW_CONFIDENCE: PipelineStatus.LOW_CONFIDENCE,
    GenerationStatus.AMBIGUOUS_TIME: PipelineStatus.AMBIGUOUS_TIME,
    GenerationStatus.MODEL_ERROR: PipelineStatus.GENERATION_ERROR,
    GenerationStatus.EMPTY_OUTPUT: PipelineStatus.GENERATION_ERROR,
}


def run(
    question: str,
    client: OpenAI | None = None,
    db_path: str | None = None,
) -> PipelineResult:
    """Run the full safe sequence for one question.

    `client` and `db_path` are injectable for testing; production passes neither.
    """
    q = (question or "").strip()
    logger.info("pipeline.start question=%r", q)

    # ----------------------------------------------------------------- #
    # 1. GENERATION (resolvers + model). Returns UNVALIDATED CandidateSQL.
    # ----------------------------------------------------------------- #
    gen = nl_to_sql_mod.generate_sql(q, client=client)

    if not gen.ok:
        status = _GEN_FAILURE_MAP.get(gen.status, PipelineStatus.GENERATION_ERROR)
        logger.info("pipeline.generation_failed status=%s detail=%s", status.value, gen.detail)
        refusal_reason = gen.refusal.reason if (gen.refusal is not None) else (
            gen.detail if status is PipelineStatus.REFUSED else ""
        )
        return PipelineResult(
            status=status,
            question=q,
            refusal_reason=refusal_reason,
            detail=gen.detail,
        )

    candidate = gen.candidate
    assert candidate is not None  # guaranteed by gen.ok
    generated_sql = candidate.sql
    _gen_usage = gen.usage
    _gen_latency = gen.latency_ms or 0.0
    logger.info("pipeline.generated_sql=%r", generated_sql)

    # ----------------------------------------------------------------- #
    # 2. VALIDATION — the trust boundary. CandidateSQL in, ValidatedQuery out.
    #    Nothing below this point runs if validation fails.
    # ----------------------------------------------------------------- #
    validation = validator_mod.validate(candidate)
    if validation.rejected:
        reason = validation.reason.value if validation.reason else "unknown"
        logger.warning(
            "pipeline.validation_failed reason=%s detail=%s sql=%r",
            reason, validation.detail, generated_sql,
        )
        return PipelineResult(
            status=PipelineStatus.VALIDATION_FAILED,
            question=q,
            generated_sql=generated_sql,
            validation_reason=f"{reason}: {validation.detail}".strip(": "),
            detail=validation.detail,
        )

    validated = validation.query
    assert validated is not None  # guaranteed by not rejected
    validated_sql = validated.sql
    logger.info("pipeline.validated_sql=%r tables=%s", validated_sql, validated.referenced_tables)

    # ----------------------------------------------------------------- #
    # 3. EXECUTION — ONLY the ValidatedQuery object reaches the executor.
    # ----------------------------------------------------------------- #
    try:
        result = executor_mod.execute(validated, db_path=db_path)
    except Exception as e:  # executor raises TypeError/PermissionError/duckdb errors
        logger.error("pipeline.execution_error type=%s detail=%s", type(e).__name__, e)
        return PipelineResult(
            status=PipelineStatus.EXECUTION_ERROR,
            question=q,
            generated_sql=generated_sql,
            validated_sql=validated_sql,
            execution_error=f"{type(e).__name__}: {e}",
            detail=str(e),
        )

    logger.info("pipeline.executed row_count=%d", result.row_count)

    # ----------------------------------------------------------------- #
    # 4. NARRATION — result-only. Never reached without a real QueryResult.
    # ----------------------------------------------------------------- #
    narrated = narrator_mod.narrate(q, validated_sql, result, client=client)

    if narrated.status is NarrationStatus.MALFORMED_RESULT:
        logger.error("pipeline.narration_malformed detail=%s", narrated.detail)
        return PipelineResult(
            status=PipelineStatus.NARRATION_ERROR,
            question=q,
            generated_sql=generated_sql,
            validated_sql=validated_sql,
            row_count=result.row_count,
            narration_error=narrated.detail,
            detail=narrated.detail,
        )
    if narrated.status is NarrationStatus.MODEL_ERROR:
        logger.error("pipeline.narration_model_error detail=%s", narrated.detail)
        return PipelineResult(
            status=PipelineStatus.NARRATION_ERROR,
            question=q,
            generated_sql=generated_sql,
            validated_sql=validated_sql,
            row_count=result.row_count,
            narration_error=narrated.detail,
            detail=narrated.detail,
        )

    final_status = (
        PipelineStatus.EMPTY_RESULT
        if narrated.status is NarrationStatus.EMPTY_RESULT
        else PipelineStatus.SUCCESS
    )
    logger.info("pipeline.success status=%s row_count=%d", final_status.value, result.row_count)

    _nar_usage = narrated.usage
    _in = (_gen_usage.input_tokens if _gen_usage else 0) + (_nar_usage.input_tokens if _nar_usage else 0)
    _out = (_gen_usage.output_tokens if _gen_usage else 0) + (_nar_usage.output_tokens if _nar_usage else 0)
    _tot = (_gen_usage.total_tokens if _gen_usage else 0) + (_nar_usage.total_tokens if _nar_usage else 0)
    _lat = _gen_latency + (narrated.latency_ms or 0.0)
    return PipelineResult(
        status=final_status,
        question=q,
        generated_sql=generated_sql,
        validated_sql=validated_sql,
        answer_text=narrated.answer_text,
        row_count=result.row_count,
        input_tokens=_in,
        output_tokens=_out,
        total_tokens=_tot,
        latency_ms=_lat,
    )
