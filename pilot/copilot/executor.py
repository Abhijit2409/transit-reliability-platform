"""
copilot/executor.py
Executes a ValidatedQuery against the read-only warehouse.

The executor accepts ONLY a ValidatedQuery, and calls assert_validated() before
touching the database. A ValidatedQuery can only be minted by the validator
(it carries a sentinel the validator stamps), so it is impossible to run SQL
here that did not pass validation. Read-only connection is the second layer.
"""

from __future__ import annotations

from pathlib import Path

import duckdb

from copilot.contracts import QueryResult, ValidatedQuery
from warehouse.connection import read_only_connection


def execute(
    query: ValidatedQuery,
    db_path: str | Path | None = None,
) -> QueryResult:
    """Run a validated query and return a QueryResult.

    Raises:
        TypeError: if `query` is not a ValidatedQuery.
        PermissionError: if the ValidatedQuery was not minted by the validator.
    """
    if not isinstance(query, ValidatedQuery):
        raise TypeError(
            f"executor accepts only ValidatedQuery, got {type(query).__name__}"
        )

    # The core guarantee: prove this object came from the validator.
    query.assert_validated()

    with read_only_connection(db_path) as con:
        cursor = con.execute(query.sql)
        rows = cursor.fetchall()
        columns = tuple(d[0] for d in cursor.description) if cursor.description else ()

    tuple_rows = tuple(tuple(r) for r in rows)
    return QueryResult(
        columns=columns,
        rows=tuple_rows,
        row_count=len(tuple_rows),
        sql=query.sql,
    )
