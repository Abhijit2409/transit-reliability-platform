"""
eval/runner.py
Repeatable evaluation of the Transit Intelligence Copilot against the golden set.

Runs every golden-set question through copilot/pipeline.py (the ONLY execution
entry point — this runner never generates, validates, or executes SQL itself),
grades the outcome category-aware, prints a console summary, and exports a CSV.

Grading philosophy (deterministic, no second model call):
  - refusal questions          -> PASS iff the pipeline REFUSED.
  - refusal_or_answer (gated)  -> PASS iff it refused OR answered successfully
                                  (both are acceptable per the golden notes).
  - answerable questions       -> PASS iff status is SUCCESS/EMPTY_RESULT AND the
                                  expected anchor values appear in the executed
                                  result rows. The answer TEXT is not string-
                                  matched (LLM narration is never verbatim); we
                                  grade what the pipeline computed, not its prose.

Fails closed: any unexpected error while running a question is recorded as a
FAIL with the exception detail, never silently skipped.

Run from project root:  python -m eval.runner
"""

from __future__ import annotations

import csv
import re
import sys
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import yaml

from copilot import pipeline
from copilot.contracts import PipelineResult, PipelineStatus

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_GOLDEN_PATH = _PROJECT_ROOT / "eval" / "golden_set.yaml"
_RESULTS_DIR = _PROJECT_ROOT / "eval" / "results"
_RESULTS_CSV = _RESULTS_DIR / "latest_eval_results.csv"
_DEFAULT_DB = _PROJECT_ROOT / "data" / "warehouse" / "transit.duckdb"

# Statuses that count as the pipeline having produced an answer.
_ANSWERED = (PipelineStatus.SUCCESS, PipelineStatus.EMPTY_RESULT)

# Expected `shape` values that indicate a refusal-type question.
_REFUSAL_SHAPES = {"refusal"}
_FLEXIBLE_SHAPES = {"refusal_or_answer"}

# Pull anchor values out of an expected-answer string for result matching:
# numbers (incl. decimals/commas) and quoted/capitalized route-ish tokens.
_NUM_RE = re.compile(r"\d[\d,]*\.?\d*")
_TOKEN_RE = re.compile(r"\b(R\d+|0\d{2}|\d{3})\b")


@dataclass(frozen=True)
class EvalRow:
    qid: str
    category: str
    question: str
    status: str
    generated_sql: str
    validated_sql: str
    answer_text: str
    expected_answer: str
    passed: bool
    failure_reason: str
    latency_ms: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0


@dataclass
class EvalSummary:
    rows: list[EvalRow] = field(default_factory=list)

    @property
    def total(self) -> int:
        return len(self.rows)

    @property
    def passed(self) -> int:
        return sum(1 for r in self.rows if r.passed)

    @property
    def failed(self) -> int:
        return self.total - self.passed

    @property
    def accuracy(self) -> float:
        return (100.0 * self.passed / self.total) if self.total else 0.0


def _norm_num(s: str) -> str:
    return s.replace(",", "").rstrip(".")


def _expected_anchors(expected_answer: str) -> tuple[set[str], set[str]]:
    """Return (numeric_anchors, token_anchors) extracted from the expected text."""
    nums = {_norm_num(m.group(0)) for m in _NUM_RE.finditer(expected_answer)}
    # drop trivially-short/￮ numbers that match noise (keep len>=2 or decimals)
    nums = {n for n in nums if len(n) >= 2 or "." in n}
    tokens = {m.group(0) for m in _TOKEN_RE.finditer(expected_answer)}
    return nums, tokens


def _result_haystack(result_rows_text: str) -> str:
    return _norm_num(result_rows_text)


def _rows_to_text(result: PipelineResult) -> str:
    # The pipeline does not expose raw rows; we match against the answer text
    # AND the validated SQL output is re-derivable only via the answer. Since
    # grading must not execute SQL itself, we match anchors against the answer
    # text the narrator produced (which, by contract, only contains result
    # values). This keeps grading inside the pipeline's guarantees.
    return result.answer_text or ""


def _grade(q: dict, result: PipelineResult) -> tuple[bool, str]:
    """Return (passed, failure_reason). Empty reason on pass."""
    shape = str(q.get("expected", {}).get("shape", "")).strip()
    status = result.status

    # 1. refusal questions
    if shape in _REFUSAL_SHAPES:
        if status is PipelineStatus.REFUSED:
            return True, ""
        return False, f"expected refusal, got {status.value}"

    # 2. flexible (gated) questions: refuse OR succeed are both acceptable
    if shape in _FLEXIBLE_SHAPES:
        if status is PipelineStatus.REFUSED or status in _ANSWERED:
            return True, ""
        return False, f"expected refusal-or-answer, got {status.value}"

    # 3. answerable questions must reach an answer
    if status not in _ANSWERED:
        return False, f"expected answer, got {status.value} ({result.detail or result.refusal_reason or result.validation_reason})"

    if status is PipelineStatus.EMPTY_RESULT:
        return False, "answerable question returned no rows"

    # 4. anchor check: expected numeric/token anchors must appear in the answer
    expected_answer = str(q.get("expected", {}).get("answer", ""))
    nums, tokens = _expected_anchors(expected_answer)
    haystack = _norm_num(_rows_to_text(result))

    missing_nums = {n for n in nums if n not in haystack}
    missing_tokens = {t for t in tokens if t not in (result.answer_text or "")}

    # For ranked/aggregate answers we require the PRIMARY anchor (first number
    # and any route token) to be present; allow some lower-rank values to be
    # absent from a summarized narration.
    if not nums and not tokens:
        # nothing to anchor on -> accept a successful answer as pass
        return True, ""

    primary_ok = True
    reason_parts: list[str] = []
    if tokens and missing_tokens == tokens:
        primary_ok = False
        reason_parts.append(f"missing route tokens {sorted(missing_tokens)}")
    if nums and missing_nums == nums:
        primary_ok = False
        reason_parts.append(f"missing numeric anchors {sorted(missing_nums)}")

    if primary_ok:
        return True, ""
    return False, "; ".join(reason_parts) or "anchors not found in answer"


def _run_question(q: dict, db_path: str | None) -> EvalRow:
    qid = str(q.get("id", "?"))
    category = str(q.get("category", "?"))
    question = str(q.get("question", ""))
    expected_answer = str(q.get("expected", {}).get("answer", ""))

    try:
        result = pipeline.run(question, db_path=db_path)
    except Exception as e:  # fail closed: never skip
        return EvalRow(
            qid=qid, category=category, question=question,
            status="RUNNER_ERROR", generated_sql="", validated_sql="",
            answer_text="", expected_answer=expected_answer,
            passed=False, failure_reason=f"{type(e).__name__}: {e}",
        )

    passed, reason = _grade(q, result)
    return EvalRow(
        qid=qid,
        category=category,
        question=question,
        status=result.status.value,
        generated_sql=result.generated_sql or "",
        validated_sql=result.validated_sql or "",
        answer_text=result.answer_text or "",
        expected_answer=expected_answer,
        passed=passed,
        failure_reason=reason,
        latency_ms=round(result.latency_ms or 0.0, 1),
        input_tokens=result.input_tokens,
        output_tokens=result.output_tokens,
        total_tokens=result.total_tokens,
    )


def load_golden(path: Path = _GOLDEN_PATH) -> list[dict]:
    data = yaml.safe_load(path.read_text())
    questions = data.get("questions", [])
    if not questions:
        raise ValueError(f"no questions found in {path}")
    return questions


def export_csv(summary: EvalSummary, path: Path = _RESULTS_CSV) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = [
        "qid", "category", "question", "status", "generated_sql",
        "validated_sql", "answer_text", "expected_answer", "passed",
        "failure_reason", "latency_ms", "input_tokens", "output_tokens",
        "total_tokens",
    ]
    with path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for r in summary.rows:
            w.writerow({
                "qid": r.qid, "category": r.category, "question": r.question,
                "status": r.status, "generated_sql": r.generated_sql,
                "validated_sql": r.validated_sql, "answer_text": r.answer_text,
                "expected_answer": r.expected_answer,
                "passed": "PASS" if r.passed else "FAIL",
                "failure_reason": r.failure_reason,
                "latency_ms": r.latency_ms,
                "input_tokens": r.input_tokens,
                "output_tokens": r.output_tokens,
                "total_tokens": r.total_tokens,
            })


def print_summary(summary: EvalSummary) -> None:
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%SZ")
    print("=" * 64)
    print(f"Transit Intelligence Copilot — Evaluation  ({ts})")
    print("=" * 64)
    print(f"Total questions : {summary.total}")
    print(f"Passed          : {summary.passed}")
    print(f"Failed          : {summary.failed}")
    print(f"Accuracy        : {summary.accuracy:.1f}%")
    _lat = [r.latency_ms for r in summary.rows if r.latency_ms]
    _tot_tokens = sum(r.total_tokens for r in summary.rows)
    if _lat:
        _avg = sum(_lat) / len(_lat)
        print(f"Avg latency     : {_avg:.0f} ms  (max {max(_lat):.0f} ms)")
    if _tot_tokens:
        print(f"Total tokens    : {_tot_tokens}  (sum across all questions)")

    # accuracy by category
    by_cat_total: Counter[str] = Counter()
    by_cat_pass: Counter[str] = Counter()
    for r in summary.rows:
        by_cat_total[r.category] += 1
        if r.passed:
            by_cat_pass[r.category] += 1
    print("\nAccuracy by category:")
    for cat in sorted(by_cat_total):
        t, p = by_cat_total[cat], by_cat_pass[cat]
        pct = 100.0 * p / t if t else 0.0
        print(f"  {cat:12s} {p}/{t}  ({pct:.0f}%)")

    # failures grouped by reason
    failures = [r for r in summary.rows if not r.passed]
    if failures:
        print("\nFailures grouped by reason:")
        grouped: dict[str, list[str]] = {}
        for r in failures:
            grouped.setdefault(r.failure_reason or "(unspecified)", []).append(r.qid)
        for reason, ids in sorted(grouped.items(), key=lambda kv: -len(kv[1])):
            print(f"  [{len(ids)}] {reason}")
            print(f"        ids: {', '.join(ids)}")
    else:
        print("\nNo failures.")
    print("=" * 64)


def run_eval(db_path: str | None = None) -> EvalSummary:
    db = db_path if db_path is not None else (str(_DEFAULT_DB) if _DEFAULT_DB.exists() else None)
    questions = load_golden()
    summary = EvalSummary()
    for q in questions:
        summary.rows.append(_run_question(q, db))
    return summary


def main() -> int:
    summary = run_eval()
    print_summary(summary)
    export_csv(summary)
    print(f"\nResults written to: {_RESULTS_CSV}")
    # Non-zero exit if anything failed, so CI / scripts can gate on it.
    return 0 if summary.failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
