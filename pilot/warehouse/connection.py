"""
warehouse/connection.py
Read-only DuckDB connection factory.

The connection is opened read_only=True so the database file cannot be mutated
at the handle level — even a query that somehow slipped past the validator
physically cannot write. This is the hard floor beneath the validator's
logical guarantees (defence in depth).
"""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

import duckdb

from config.loaders import load_guardrail_config

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
# Default to the small deploy DB (five allowlisted gold tables). The full
# warehouse is 800+ MB and is not committed; callers may still pass an explicit
# db_path to point elsewhere (e.g. tests against the full local warehouse).
_DEFAULT_DB_PATH = _PROJECT_ROOT / "data" / "warehouse" / "transit_deploy.duckdb"


def _resolve_db_path(db_path: str | Path | None) -> Path:
    return Path(db_path) if db_path is not None else _DEFAULT_DB_PATH


@contextmanager
def read_only_connection(
    db_path: str | Path | None = None,
) -> Iterator[duckdb.DuckDBPyConnection]:
    """Yield a read-only DuckDB connection with a statement timeout applied.

    Usage:
        with read_only_connection() as con:
            con.execute(validated_query.sql).fetchall()
    """
    cfg = load_guardrail_config()
    path = _resolve_db_path(db_path)
    if not path.exists():
        raise FileNotFoundError(f"warehouse database not found: {path}")

    if cfg.connection_mode != "read_only":
        # Fail closed: this project never opens the warehouse writable.
        raise ValueError(
            f"refusing to open warehouse in mode '{cfg.connection_mode}'; "
            "only 'read_only' is permitted"
        )

    con = duckdb.connect(str(path), read_only=True)
    try:
        # Best-effort statement timeout. DuckDB exposes this via a PRAGMA-like
        # SET; wrapped so an older build that lacks it doesn't break the floor.
        timeout_s = max(cfg.statement_timeout_ms, 0) / 1000.0
        try:
            con.execute(f"SET statement_timeout = '{int(cfg.statement_timeout_ms)}ms'")
        except duckdb.Error:
            pass  # timeout is a nicety; read-only is the guarantee
        yield con
    finally:
        con.close()
