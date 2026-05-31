"""
inspect_parquet.py
==================
Quick-look inspection tool for individual parquet files.

While validate_pipeline.py answers "is everything healthy?", this script
answers "what does this specific file actually contain?". Useful for ad-hoc
debugging, exploring new data, or sanity-checking a single collection.

Usage:
    python src/inspect_parquet.py path/to/file.parquet
    python src/inspect_parquet.py path/to/file.parquet --sample 20
"""

import argparse
import logging
import sys
from pathlib import Path

import pandas as pd
import pyarrow.parquet as pq


# Reuse a simple logging config — concise output for an interactive tool.
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("inspect_parquet")


def print_section(title: str) -> None:
    """Helper to print a nicely formatted section header."""
    print()
    print("=" * 70)
    print(f"  {title}")
    print("=" * 70)


def inspect_schema(file_path: Path) -> None:
    """
    Show the parquet file's schema (column names + types) without loading data.

    pq.ParquetFile reads only metadata, which is fast even for large files —
    you don't pay the cost of loading rows just to see column types.
    """
    print_section("SCHEMA")
    parquet_file = pq.ParquetFile(file_path)
    schema = parquet_file.schema_arrow

    # Print each column with its data type, nicely aligned.
    for field in schema:
        # f-string with width specifier: '<25' = left-align in 25-char field
        print(f"  {field.name:<25} {field.type}")

    print(f"\n  Total columns: {len(schema)}")
    print(f"  Total rows:    {parquet_file.metadata.num_rows:,}")
    print(f"  File size:     {file_path.stat().st_size / 1024:.2f} KB")


def inspect_sample(df: pd.DataFrame, n: int) -> None:
    """Print the first N rows so you can eyeball the actual data."""
    print_section(f"SAMPLE ROWS (first {n})")

    # pandas display options: show all columns (don't truncate width)
    with pd.option_context(
        "display.max_columns", None,
        "display.width", 200,
        "display.max_colwidth", 30,
    ):
        print(df.head(n))


def inspect_routes(df: pd.DataFrame) -> None:
    """Show how many distinct routes are in this file."""
    print_section("ROUTE COVERAGE")
    if "route_id" not in df.columns:
        print("  No route_id column present.")
        return

    unique_routes = df["route_id"].nunique(dropna=True)
    print(f"  Unique routes: {unique_routes}")

    # Show top 10 routes by record count — tells us which lines are busiest.
    top_routes = df["route_id"].value_counts().head(10)
    print(f"\n  Top 10 routes by record count:")
    for route, count in top_routes.items():
        print(f"    {route:<15} {count:,} records")


def inspect_time_range(df: pd.DataFrame) -> None:
    """Show the earliest and latest api_timestamp in the file."""
    print_section("TIME RANGE")
    if "api_timestamp" not in df.columns:
        print("  No api_timestamp column present.")
        return

    # Coerce to datetime — gracefully handle any malformed values.
    ts = pd.to_datetime(df["api_timestamp"], errors="coerce", utc=True)
    valid = ts.dropna()

    if valid.empty:
        print("  No valid timestamps found.")
        return

    duration = valid.max() - valid.min()
    print(f"  Earliest:  {valid.min()}")
    print(f"  Latest:    {valid.max()}")
    print(f"  Duration:  {duration}")
    print(f"  Records:   {len(valid):,}")


def inspect_missing(df: pd.DataFrame) -> None:
    """Report null counts and percentages per column."""
    print_section("MISSING VALUES")

    total_rows = len(df)
    if total_rows == 0:
        print("  Empty dataframe.")
        return

    # Build a small summary table: column | null count | null %
    print(f"  {'Column':<25} {'Nulls':>10} {'Pct':>8}")
    print(f"  {'-' * 25} {'-' * 10} {'-' * 8}")

    for col in df.columns:
        null_count = df[col].isna().sum()
        null_pct = (null_count / total_rows) * 100
        # Highlight columns with any nulls so they catch the eye.
        marker = " *" if null_count > 0 else ""
        print(f"  {col:<25} {null_count:>10,} {null_pct:>7.2f}%{marker}")


def main():
    """Parse args, load the file, and run all inspections."""
    parser = argparse.ArgumentParser(description="Inspect a single parquet file.")
    parser.add_argument("file", type=Path, help="Path to .parquet file")
    parser.add_argument(
        "--sample", type=int, default=10, help="Number of sample rows (default: 10)"
    )
    args = parser.parse_args()

    if not args.file.exists():
        logger.error(f"File not found: {args.file}")
        sys.exit(1)

    if args.file.suffix != ".parquet":
        logger.error(f"Not a parquet file: {args.file}")
        sys.exit(1)

    logger.info(f"Inspecting {args.file}")

    # Step 1: schema (fast, metadata-only)
    inspect_schema(args.file)

    # Step 2: load the dataframe for the data-level inspections
    try:
        df = pd.read_parquet(args.file)
    except Exception as e:
        logger.error(f"Failed to read parquet: {e}")
        sys.exit(1)

    # Step 3: run each inspection in sequence
    inspect_sample(df, args.sample)
    inspect_routes(df)
    inspect_time_range(df)
    inspect_missing(df)

    print()
    logger.info("Inspection complete.")


if __name__ == "__main__":
    main()