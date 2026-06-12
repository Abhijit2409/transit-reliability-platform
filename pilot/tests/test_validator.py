"""
tests/test_validator.py
Proves the trust layer blocks unsafe SQL and admits only safe SELECTs.

Run from project root:  python -m pytest tests/test_validator.py -v
(or: python tests/test_validator.py  for the no-pytest fallback runner)

The malicious cases are the point. Each asserts a SPECIFIC rejection reason so
a future refactor cannot silently turn a hard block into a soft pass.
"""

from __future__ import annotations

import pytest

from config.loaders import load_guardrail_config
from copilot.contracts import (
    CandidateSQL,
    RejectionReason,
    ValidatedQuery,
    _VALIDATION_SENTINEL,
)
from copilot.validator import validate

CFG = load_guardrail_config()


# --------------------------------------------------------------------------- #
# SAFE queries — must PASS
# --------------------------------------------------------------------------- #

SAFE_QUERIES = [
    "SELECT route_short_name, reliability_score FROM top20_route_reliability_scores WHERE route_short_name = 'R4'",
    "SELECT route_short_name, reliability_score FROM top20_route_reliability_scores ORDER BY reliability_score ASC LIMIT 5",
    "SELECT SUM(total_bunching_events) FROM top20_route_reliability_scores",
    "SELECT route_type, avg_reliability_score FROM top20_route_type_summary WHERE route_type IN ('RapidBus','Regular Bus')",
    # subquery, both tables allowlisted
    "SELECT * FROM (SELECT route_short_name FROM top20_corridor_priority_ranking ORDER BY intervention_priority_score DESC LIMIT 3)",
]


@pytest.mark.parametrize("sql", SAFE_QUERIES)
def test_safe_queries_pass(sql):
    result = validate(CandidateSQL(sql), CFG)
    assert result.ok, f"expected pass, got {result.reason}: {result.detail}"
    assert isinstance(result.query, ValidatedQuery)
    assert result.query._marker is _VALIDATION_SENTINEL
    # LIMIT must be present and within bounds after validation
    assert result.query.enforced_limit <= CFG.max_limit


def test_missing_limit_is_injected():
    result = validate(CandidateSQL("SELECT route_short_name FROM top20_route_reliability_scores"), CFG)
    assert result.ok
    assert "LIMIT" in result.query.sql.upper()
    assert result.query.enforced_limit == CFG.max_limit


# --------------------------------------------------------------------------- #
# UNSAFE queries — must be REJECTED, with the right reason
# --------------------------------------------------------------------------- #

UNSAFE_CASES = [
    # (sql, expected_reason)
    ("", RejectionReason.EMPTY),
    ("   ", RejectionReason.EMPTY),
    # write / DDL / DML
    ("DELETE FROM top20_route_reliability_scores", RejectionReason.NOT_A_SELECT),
    ("UPDATE top20_route_reliability_scores SET reliability_score = 100", RejectionReason.NOT_A_SELECT),
    ("INSERT INTO top20_route_reliability_scores VALUES (1)", RejectionReason.NOT_A_SELECT),
    ("DROP TABLE top20_route_reliability_scores", RejectionReason.NOT_A_SELECT),
    ("CREATE TABLE evil AS SELECT * FROM top20_route_reliability_scores", RejectionReason.NOT_A_SELECT),
    ("ALTER TABLE top20_route_reliability_scores ADD COLUMN x INT", RejectionReason.NOT_A_SELECT),
    # state / OS reach
    ("PRAGMA database_list", RejectionReason.NOT_A_SELECT),
    ("ATTACH 'evil.db' AS evil", RejectionReason.NOT_A_SELECT),
    ("COPY top20_route_reliability_scores TO 'out.csv'", RejectionReason.NOT_A_SELECT),
    # stacked statements (SQL-injection style)
    ("SELECT 1 FROM top20_route_reliability_scores; DROP TABLE top20_route_reliability_scores", RejectionReason.MULTIPLE_STATEMENTS),
    # blocked-pattern tables
    ("SELECT * FROM bunching_events_099", RejectionReason.TABLE_BLOCKED_PATTERN),
    ("SELECT * FROM route_reliability_score_099", RejectionReason.TABLE_BLOCKED_PATTERN),
    ("SELECT * FROM bronze_vehicle_positions", RejectionReason.TABLE_BLOCKED_PATTERN),
    ("SELECT * FROM silver_vehicle_positions", RejectionReason.TABLE_BLOCKED_PATTERN),
    ("SELECT * FROM gtfs_routes", RejectionReason.TABLE_BLOCKED_PATTERN),
    ("SELECT * FROM dim_routes", RejectionReason.TABLE_BLOCKED_PATTERN),
    # not-allowlisted table (real table, just not exposed)
    ("SELECT * FROM route_eligibility_summary", RejectionReason.TABLE_NOT_ALLOWLISTED),
    ("SELECT * FROM some_random_table", RejectionReason.TABLE_NOT_ALLOWLISTED),
    # LIMIT over the cap
    ("SELECT route_short_name FROM top20_route_reliability_scores LIMIT 100000", RejectionReason.LIMIT_EXCEEDS_MAX),
    # garbage / unparseable
    ("SELECT FROM WHERE", RejectionReason.PARSE_ERROR),
    ("this is not sql at all !!!", RejectionReason.PARSE_ERROR),
    # subquery reaching a blocked table even though outer is allowlisted
    ("SELECT * FROM top20_route_reliability_scores WHERE route_id IN (SELECT route_id FROM bronze_vehicle_positions)", RejectionReason.TABLE_BLOCKED_PATTERN),
    # CTE that reads a blocked table
    ("WITH x AS (SELECT * FROM bunching_events_099) SELECT * FROM x", RejectionReason.TABLE_BLOCKED_PATTERN),
]


@pytest.mark.parametrize("sql,expected", UNSAFE_CASES)
def test_unsafe_queries_blocked(sql, expected):
    result = validate(CandidateSQL(sql), CFG)
    assert result.rejected, f"expected rejection for: {sql!r}"
    assert result.reason == expected, (
        f"for {sql!r} expected {expected}, got {result.reason} ({result.detail})"
    )
    assert result.query is None


def test_rejected_query_cannot_be_executed():
    """A rejection yields no ValidatedQuery, so there is nothing to execute."""
    result = validate(CandidateSQL("DROP TABLE top20_route_reliability_scores"), CFG)
    assert result.query is None


def test_handbuilt_validated_query_is_refused_by_assert():
    """A ValidatedQuery fabricated without the sentinel must fail assert_validated()."""
    forged = ValidatedQuery(
        sql="SELECT 1 FROM top20_route_reliability_scores",
        referenced_tables=("top20_route_reliability_scores",),
        enforced_limit=1,
        _marker=None,  # not the sentinel
    )
    with pytest.raises(PermissionError):
        forged.assert_validated()


# --------------------------------------------------------------------------- #
# no-pytest fallback
# --------------------------------------------------------------------------- #

if __name__ == "__main__":
    passed = failed = 0
    for sql in SAFE_QUERIES:
        r = validate(CandidateSQL(sql), CFG)
        ok = r.ok
        passed += ok; failed += (not ok)
        print(("PASS" if ok else "FAIL"), "safe:", sql[:55])
    for sql, expected in UNSAFE_CASES:
        r = validate(CandidateSQL(sql), CFG)
        ok = r.rejected and r.reason == expected
        passed += ok; failed += (not ok)
        print(("PASS" if ok else "FAIL"), f"unsafe[{expected.value}]:", sql[:45])
    print(f"\n{passed} passed, {failed} failed")
    raise SystemExit(1 if failed else 0)
