"""
scripts/build_deploy_warehouse.py
Build a small, GitHub-friendly DuckDB containing ONLY the allowlisted gold
tables the copilot needs — leaving the full 800+ MB warehouse (raw bronze/
silver telemetry tiers) out of the deploy artifact.

Source : data/warehouse/transit.duckdb        (large; never modified)
Output : data/warehouse/transit_deploy.duckdb  (small; committed for deploy)

Run from the project root:
    python scripts/build_deploy_warehouse.py

The output contains exactly the five tables in the copilot's guardrail allowlist
(config/guardrail_config.yaml). Keeping them in sync matters: if the allowlist
changes, re-run this script so the deploy DB still serves every allowlisted
table.
"""

from __future__ import annotations

import sys
from pathlib import Path

import duckdb

# Resolve paths relative to the project root (scripts/ -> root).
_ROOT = Path(__file__).resolve().parent.parent
_SOURCE_DB = _ROOT / "data" / "warehouse" / "transit.duckdb"
_DEPLOY_DB = _ROOT / "data" / "warehouse" / "transit_deploy.duckdb"

# The copilot's allowlist — the ONLY tables it may query. Keep in sync with
# config/guardrail_config.yaml (allowed_tables).
GOLD_TABLES = [
    "top20_route_reliability_scores",
    "top20_corridor_priority_ranking",
    "top20_hourly_bunching_pattern",
    "top20_bunching_hotspots_with_stops",
    "top20_route_type_summary",
]


def _fail(message: str) -> None:
    print(f"ERROR: {message}", file=sys.stderr)
    sys.exit(1)


def main() -> None:
    # 1. source must exist
    if not _SOURCE_DB.exists():
        _fail(f"source warehouse not found: {_SOURCE_DB}")

    # 2. open the source READ-ONLY so the original is never modified
    try:
        src = duckdb.connect(str(_SOURCE_DB), read_only=True)
    except Exception as e:
        _fail(f"could not open source warehouse read-only: {e}")

    # 3. verify every required table exists in the source before writing anything
    existing = {row[0] for row in src.execute("SHOW TABLES").fetchall()}
    missing = [t for t in GOLD_TABLES if t not in existing]
    if missing:
        src.close()
        _fail(
            "source warehouse is missing required gold table(s): "
            + ", ".join(missing)
        )

    # 4. fresh deploy DB — remove any prior build so tables are recreated cleanly
    if _DEPLOY_DB.exists():
        _DEPLOY_DB.unlink()
    _DEPLOY_DB.parent.mkdir(parents=True, exist_ok=True)

    dst = duckdb.connect(str(_DEPLOY_DB))  # writable

    # 5. copy each table by reading from the source DB via ATTACH, so we never
    #    materialize the whole warehouse in memory.
    dst.execute(f"ATTACH '{_SOURCE_DB}' AS src (READ_ONLY)")
    print(f"Building deploy warehouse: {_DEPLOY_DB.name}")
    print("-" * 56)
    total_rows = 0
    for table in GOLD_TABLES:
        dst.execute(f"DROP TABLE IF EXISTS {table}")
        dst.execute(f"CREATE TABLE {table} AS SELECT * FROM src.{table}")
        n = dst.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        total_rows += n
        print(f"  {table:<38} {n:>8,} rows")
    dst.execute("DETACH src")

    # 6. compact the file so it is as small as possible
    dst.execute("VACUUM")
    dst.close()
    src.close()

    size_mb = _DEPLOY_DB.stat().st_size / (1024 * 1024)
    print("-" * 56)
    print(f"  {'TOTAL':<38} {total_rows:>8,} rows")
    print(f"\nWrote {_DEPLOY_DB} ({size_mb:.2f} MB)")
    if size_mb > 100:
        print("WARNING: deploy DB exceeds GitHub's 100 MB limit — investigate "
              "which table is unexpectedly large.", file=sys.stderr)


if __name__ == "__main__":
    main()
