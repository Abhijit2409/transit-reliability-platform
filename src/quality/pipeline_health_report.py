"""
pipeline_health_report.py
=========================
Production-style observability layer for the GTFS-RT transit telemetry pipeline.

This script answers one fundamental question: "Is my pipeline healthy right now?"

It scans all collected parquet files under data/raw/, runs a battery of health
checks, prints a console dashboard, and exports a timestamped CSV report to
reports/ for historical tracking.

Why observability matters:
- A pipeline that fails silently is worse than one that crashes loudly.
- Health metrics over time let you spot trends (e.g., row counts dropping,
  collection gaps widening) before they become outages.
- CSV reports are an audit trail — useful for debugging, demos, and proving
  reliability claims to a hiring manager or stakeholder.

Usage (from project root):
    python src/pipeline_health_report.py
    python src/pipeline_health_report.py --data-dir data/raw
    python src/pipeline_health_report.py --date 2026-05-23
"""

import argparse
import csv
import logging
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

import pandas as pd
import pyarrow.parquet as pq


# ---------------------------------------------------------------------------
# CONFIGURATION
# ---------------------------------------------------------------------------
# These are the columns your collector writes. Health checks reference this
# list to verify schema consistency across files.
EXPECTED_COLUMNS = [
    "collection_timestamp",
    "api_vehicle_timestamp",
    "entity_id",
    "vehicle_id",
    "route_id",
    "trip_id",
    "latitude",
    "longitude",
    "bearing",
    "speed",
]

# Default locations relative to project root.
DEFAULT_DATA_DIR = Path("data/raw")
REPORT_DIR = Path("reports")

# A pipeline is "fresh" if the latest record is within this many minutes.
# Adjust for your polling interval — 30-second polling should easily stay
# under 5 minutes unless something's wrong.
FRESHNESS_THRESHOLD_MINUTES = 5


# ---------------------------------------------------------------------------
# LOGGING
# ---------------------------------------------------------------------------
# Simple logger that prints to console. Health reports are typically run
# interactively or on a schedule, so console output is the right channel.
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-7s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("health_report")


# ---------------------------------------------------------------------------
# FILE DISCOVERY
# ---------------------------------------------------------------------------
def find_parquet_files(data_dir: Path, date_filter: Optional[str] = None) -> List[Path]:
    """
    Find all parquet files under the data directory.

    What it does:
        Recursively walks data_dir looking for *.parquet files. If a date
        filter is given (e.g. "2026-05-09"), only files under that date's
        subfolder are returned.

    Why it matters:
        The whole report is based on what files exist. Catching "zero files
        found" early gives a clear error instead of an empty, confusing report.

    How it helps monitoring:
        File counts and locations are the foundation of every other metric.
        If this function returns nothing, that itself is a critical alert —
        the collector isn't producing output.
    """
    if not data_dir.exists():
        logger.error(f"Data directory does not exist: {data_dir}")
        return []

    # If a specific date was requested, scope the search to that subfolder.
    search_root = data_dir / date_filter if date_filter else data_dir

    if not search_root.exists():
        logger.error(f"Search root does not exist: {search_root}")
        return []

    # rglob walks ALL subdirectories — perfect for date-partitioned layouts
    # like data/raw/2026-05-09/vehicle_positions_22_143015.parquet
    files = sorted(search_root.rglob("*.parquet"))
    logger.info(f"Found {len(files)} parquet files under {search_root}")
    return files


# ---------------------------------------------------------------------------
# PER-FILE INSPECTION
# ---------------------------------------------------------------------------
def inspect_file(file_path: Path) -> Dict:
    """
    Extract health metrics from a single parquet file.

    What it does:
        Opens one parquet file and pulls out everything we need for the
        aggregate report: row count, size, timestamp range, route IDs,
        null counts, duplicate counts, and corruption status.

    Why it matters:
        Each file is a unit of "did the collector succeed at this moment?".
        Aggregating per-file results into a summary tells you whether
        problems are widespread or isolated to specific times.

    How it helps monitoring:
        If 1 file out of 1000 is corrupt, you have a transient bug.
        If 200 are corrupt, you have a systemic issue. Per-file granularity
        is what lets you tell the difference.

    Returns:
        Dict with all metrics for this file. Status field summarizes overall
        health: "OK", "WARN", or "FAIL".
    """
    # Start with a default result. We fill in fields as checks complete.
    result = {
        "file": str(file_path),
        "filename": file_path.name,
        "size_bytes": 0,
        "row_count": 0,
        "is_empty": False,
        "is_corrupted": False,
        "missing_columns": [],
        "duplicate_count": 0,
        "earliest_timestamp": None,
        "latest_timestamp": None,
        "unique_routes": 0,
        "null_percentages": {},
        "status": "OK",
    }

    # --- Check 1: file size ---
    # A 0-byte file means the writer failed before flushing any data.
    size = file_path.stat().st_size
    result["size_bytes"] = size

    if size == 0:
        result["is_empty"] = True
        result["status"] = "FAIL"
        return result

    # --- Check 2: corruption (metadata-only read) ---
    # pyarrow's ParquetFile reads only the footer, which is cheap and tells
    # us if the file is structurally valid before we commit to loading rows.
    try:
        pq_file = pq.ParquetFile(file_path)
        result["row_count"] = pq_file.metadata.num_rows
    except Exception as e:
        # If pyarrow can't even read the metadata, the file is severely broken.
        result["is_corrupted"] = True
        result["status"] = "FAIL"
        logger.warning(f"Corrupted (metadata unreadable): {file_path.name} — {e}")
        return result

    # A file with 0 rows is technically valid but useless.
    if result["row_count"] == 0:
        result["is_empty"] = True
        result["status"] = "FAIL"
        return result

    # --- Now load with pandas for deeper inspection ---
    # We wrap in try/except because the metadata-vs-data mismatch bug from
    # the previous devlog manifests HERE, not at metadata read.
    try:
        df = pd.read_parquet(file_path)
    except Exception as e:
        result["is_corrupted"] = True
        result["status"] = "FAIL"
        logger.warning(f"Corrupted (pandas read failed): {file_path.name} — {e}")
        return result

    # --- Check 3: schema completeness ---
    # Every expected column should be present. Missing columns mean either
    # the GTFS feed changed or the collector regressed.
    missing = [c for c in EXPECTED_COLUMNS if c not in df.columns]
    result["missing_columns"] = missing
    if missing:
        result["status"] = "FAIL"

    # --- Check 4: duplicate records ---
    # Same vehicle + same api_vehicle_timestamp = duplicate observation.
    # Some duplicates are normal (vehicle didn't move between polls) but
    # large counts suggest the API is returning stale data or the collector
    # is double-writing.
    if "vehicle_id" in df.columns and "api_vehicle_timestamp" in df.columns:
        dup_mask = df.duplicated(subset=["vehicle_id", "api_vehicle_timestamp"])
        result["duplicate_count"] = int(dup_mask.sum())
        if result["duplicate_count"] > 0 and result["status"] == "OK":
            result["status"] = "WARN"

    # --- Check 5: timestamp range ---
    # Earliest and latest collection_timestamp tell us when this file's data
    # was actually captured. Useful for spotting collection gaps.
    if "collection_timestamp" in df.columns:
        ts = pd.to_datetime(df["collection_timestamp"], errors="coerce", utc=True)
        valid_ts = ts.dropna()
        if not valid_ts.empty:
            result["earliest_timestamp"] = valid_ts.min().isoformat()
            result["latest_timestamp"] = valid_ts.max().isoformat()

    # --- Check 6: route coverage ---
    # How many distinct routes appeared in this file? Low coverage during
    # peak hours suggests vehicles aren't reporting or routes aren't running.
    if "route_id" in df.columns:
        result["unique_routes"] = int(df["route_id"].nunique(dropna=True))

    # --- Check 7: null percentages per column ---
    # High null rates flag upstream data quality issues (e.g., GPS dropouts,
    # vehicles without bearing data, etc.).
    null_pcts = {}
    for col in df.columns:
        # isna() → boolean Series; mean() of booleans = fraction of True values
        pct = float(df[col].isna().mean() * 100)
        null_pcts[col] = round(pct, 2)
    result["null_percentages"] = null_pcts

    # Any column over 50% null is a warning — that data is barely usable.
    if any(p > 50 for p in null_pcts.values()) and result["status"] == "OK":
        result["status"] = "WARN"

    return result


# ---------------------------------------------------------------------------
# AGGREGATION
# ---------------------------------------------------------------------------
def aggregate_metrics(results: List[Dict]) -> Dict:
    """
    Roll up per-file results into pipeline-wide metrics.

    What it does:
        Takes the list of per-file inspection results and computes summary
        statistics: totals, averages, extremes, and aggregate route coverage.

    Why it matters:
        Per-file detail is useful for debugging, but pipeline operators need
        the big picture: is everything roughly working? How much data am I
        collecting? When did collection start and stop?

    How it helps monitoring:
        These are the numbers you'd put on a status dashboard. Trends in
        these metrics over time (run the report daily) reveal slow
        degradations that single-file checks miss.
    """
    # Filter out failed files for "successful" stats — we don't want a
    # corrupted file dragging down our row count averages.
    valid_results = [r for r in results if r["status"] != "FAIL"]

    total_files = len(results)
    valid_files = len(valid_results)
    failed_files = sum(1 for r in results if r["status"] == "FAIL")
    warned_files = sum(1 for r in results if r["status"] == "WARN")
    empty_files = sum(1 for r in results if r["is_empty"])
    corrupted_files = sum(1 for r in results if r["is_corrupted"])

    total_rows = sum(r["row_count"] for r in valid_results)
    total_duplicates = sum(r["duplicate_count"] for r in results)

    # Average rows per file — useful for spotting "thin" collections where
    # the API returned partial data.
    avg_rows = (total_rows / valid_files) if valid_files > 0 else 0

    # Find largest and smallest files by size — outliers in either direction
    # warrant investigation.
    largest = max(valid_results, key=lambda r: r["size_bytes"], default=None)
    smallest = min(valid_results, key=lambda r: r["size_bytes"], default=None)

    # Pipeline time range: earliest and latest data across ALL files.
    all_earliest = [r["earliest_timestamp"] for r in valid_results if r["earliest_timestamp"]]
    all_latest = [r["latest_timestamp"] for r in valid_results if r["latest_timestamp"]]
    earliest = min(all_earliest) if all_earliest else None
    latest = max(all_latest) if all_latest else None

    # Files per hour — counts how many collection cycles ran in each hour.
    # The filename convention is vehicle_positions_HH_HHMMSS.parquet, but
    # we'll just bucket by the file's mtime hour for simplicity and resilience
    # to filename format changes.
    hour_counts = Counter()
    for r in valid_results:
        # Extract hour from latest_timestamp if available, else from filename.
        if r["latest_timestamp"]:
            dt = datetime.fromisoformat(r["latest_timestamp"])
            hour_key = dt.strftime("%Y-%m-%d %H:00")
            hour_counts[hour_key] += 1

    # Aggregate route coverage across the whole pipeline.
    # We can't truly dedupe routes without re-reading every file, so we report
    # the max single-file unique route count as a proxy for breadth.
    max_routes_in_file = max((r["unique_routes"] for r in valid_results), default=0)

    # Aggregate null percentages: average each column's null % across files.
    # Tells us which fields are consistently unreliable.
    aggregate_nulls = {}
    for col in EXPECTED_COLUMNS:
        pcts = [r["null_percentages"].get(col, 0) for r in valid_results if r["null_percentages"]]
        if pcts:
            aggregate_nulls[col] = round(sum(pcts) / len(pcts), 2)

    # Freshness: how long ago was the most recent record?
    freshness_status = "UNKNOWN"
    freshness_minutes = None
    if latest:
        latest_dt = datetime.fromisoformat(latest)
        now = datetime.now(timezone.utc)
        freshness_minutes = (now - latest_dt).total_seconds() / 60
        if freshness_minutes <= FRESHNESS_THRESHOLD_MINUTES:
            freshness_status = "FRESH"
        else:
            freshness_status = "STALE"

    return {
        "total_files": total_files,
        "valid_files": valid_files,
        "failed_files": failed_files,
        "warned_files": warned_files,
        "empty_files": empty_files,
        "corrupted_files": corrupted_files,
        "total_rows": total_rows,
        "total_duplicates": total_duplicates,
        "avg_rows_per_file": round(avg_rows, 1),
        "largest_file": largest["filename"] if largest else None,
        "largest_file_bytes": largest["size_bytes"] if largest else 0,
        "smallest_file": smallest["filename"] if smallest else None,
        "smallest_file_bytes": smallest["size_bytes"] if smallest else 0,
        "earliest_timestamp": earliest,
        "latest_timestamp": latest,
        "freshness_status": freshness_status,
        "freshness_minutes": round(freshness_minutes, 1) if freshness_minutes else None,
        "files_per_hour": dict(hour_counts),
        "max_routes_in_file": max_routes_in_file,
        "aggregate_null_percentages": aggregate_nulls,
    }


# ---------------------------------------------------------------------------
# CONSOLE REPORT
# ---------------------------------------------------------------------------
def print_report(summary: Dict) -> None:
    """
    Pretty-print the aggregated metrics to the console.

    What it does:
        Formats the summary dict as a human-readable dashboard with sections,
        aligned columns, and clear status indicators.

    Why it matters:
        A summary you can't read at a glance won't get read. Production teams
        live and die by their dashboards — a good console report is the
        zero-dependency version of that.

    How it helps monitoring:
        Quick visual scan tells you if today's collection is normal or weird.
        Run it after a deployment, after a Railway restart, or at the end of
        a shift to confirm everything's healthy.
    """
    bar = "=" * 70
    print()
    print(bar)
    print("  GTFS-RT PIPELINE HEALTH REPORT")
    print(bar)
    print(f"  Generated:           {datetime.now(timezone.utc).isoformat()}")
    print()

    # --- File statistics ---
    print("  FILE STATISTICS")
    print("  " + "-" * 50)
    print(f"  Total files scanned:       {summary['total_files']}")
    print(f"  Valid files:               {summary['valid_files']}")
    print(f"  Failed files:              {summary['failed_files']}")
    print(f"  Warning files:             {summary['warned_files']}")
    print(f"  Empty files:               {summary['empty_files']}")
    print(f"  Corrupted files:           {summary['corrupted_files']}")
    print()

    # --- Data volume ---
    print("  DATA VOLUME")
    print("  " + "-" * 50)
    print(f"  Total rows collected:      {summary['total_rows']:,}")
    print(f"  Duplicate rows:            {summary['total_duplicates']:,}")
    print(f"  Average rows per file:     {summary['avg_rows_per_file']:,}")
    print()

    # --- File extremes ---
    if summary["largest_file"]:
        print("  FILE SIZE EXTREMES")
        print("  " + "-" * 50)
        print(f"  Largest file:              {summary['largest_file']}")
        print(f"                             {summary['largest_file_bytes']:,} bytes "
              f"({summary['largest_file_bytes'] / 1024:.2f} KB)")
        print(f"  Smallest file:             {summary['smallest_file']}")
        print(f"                             {summary['smallest_file_bytes']:,} bytes "
              f"({summary['smallest_file_bytes'] / 1024:.2f} KB)")
        print()

    # --- Time range and freshness ---
    print("  TIME RANGE & FRESHNESS")
    print("  " + "-" * 50)
    print(f"  Earliest record:           {summary['earliest_timestamp'] or 'N/A'}")
    print(f"  Latest record:             {summary['latest_timestamp'] or 'N/A'}")
    if summary["freshness_minutes"] is not None:
        print(f"  Pipeline freshness:        {summary['freshness_status']} "
              f"({summary['freshness_minutes']} min ago)")
    else:
        print(f"  Pipeline freshness:        {summary['freshness_status']}")
    print()

    # --- Files per hour ---
    if summary["files_per_hour"]:
        print("  FILES PER HOUR")
        print("  " + "-" * 50)
        # Sort by hour for chronological display
        for hour in sorted(summary["files_per_hour"].keys()):
            count = summary["files_per_hour"][hour]
            # A simple ASCII bar chart — visual cue for collection rate
            bar_viz = "█" * min(count, 50)
            print(f"  {hour}    {count:>4}  {bar_viz}")
        print()

    # --- Route coverage ---
    print("  ROUTE COVERAGE")
    print("  " + "-" * 50)
    print(f"  Max routes in single file: {summary['max_routes_in_file']}")
    print()

    # --- Null percentages ---
    if summary["aggregate_null_percentages"]:
        print("  AVERAGE NULL % PER COLUMN")
        print("  " + "-" * 50)
        for col, pct in summary["aggregate_null_percentages"].items():
            # Flag columns with notable null rates
            marker = "  WARN" if pct > 10 else ""
            print(f"  {col:<28} {pct:>6.2f}%{marker}")
        print()

    print(bar)


# ---------------------------------------------------------------------------
# CSV EXPORT
# ---------------------------------------------------------------------------
def export_csv(results: List[Dict], summary: Dict) -> Path:
    """
    Export per-file results and the aggregate summary to a CSV file.

    What it does:
        Writes two CSVs to reports/: one with per-file details, one with the
        aggregate summary. Both are timestamped so historical reports
        accumulate without overwriting.

    Why it matters:
        CSV is the universal interchange format. You can open it in Excel,
        load it into pandas for trend analysis, or import it into a BI tool
        like Looker. Plain text exports also serve as a portable audit trail
        that doesn't depend on any database.

    How it helps monitoring:
        Daily CSV exports become time-series data. After two weeks you can
        chart "rows collected per day" or "failed files per day" and SEE
        whether your pipeline is improving, stable, or degrading.

    Returns:
        Path to the per-file CSV (most useful for debugging).
    """
    REPORT_DIR.mkdir(exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")

    # --- Per-file CSV ---
    # One row per parquet file, with all health metrics flattened.
    per_file_path = REPORT_DIR / f"health_per_file_{timestamp}.csv"
    fieldnames = [
        "filename", "status", "size_bytes", "row_count",
        "is_empty", "is_corrupted", "missing_columns",
        "duplicate_count", "earliest_timestamp", "latest_timestamp",
        "unique_routes",
    ]
    with per_file_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for r in results:
            # Convert list/dict fields to strings so CSV handles them cleanly
            row = dict(r)
            row["missing_columns"] = ",".join(r["missing_columns"]) if r["missing_columns"] else ""
            writer.writerow(row)
    logger.info(f"Per-file report saved: {per_file_path}")

    # --- Aggregate summary CSV ---
    # One row, key metrics only — easy to append-load into a trend dataset.
    summary_path = REPORT_DIR / f"health_summary_{timestamp}.csv"
    summary_row = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "total_files": summary["total_files"],
        "valid_files": summary["valid_files"],
        "failed_files": summary["failed_files"],
        "warned_files": summary["warned_files"],
        "empty_files": summary["empty_files"],
        "corrupted_files": summary["corrupted_files"],
        "total_rows": summary["total_rows"],
        "total_duplicates": summary["total_duplicates"],
        "avg_rows_per_file": summary["avg_rows_per_file"],
        "earliest_timestamp": summary["earliest_timestamp"],
        "latest_timestamp": summary["latest_timestamp"],
        "freshness_status": summary["freshness_status"],
        "freshness_minutes": summary["freshness_minutes"],
        "max_routes_in_file": summary["max_routes_in_file"],
    }
    with summary_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(summary_row.keys()))
        writer.writeheader()
        writer.writerow(summary_row)
    logger.info(f"Summary report saved: {summary_path}")

    return per_file_path


# ---------------------------------------------------------------------------
# MAIN
# ---------------------------------------------------------------------------
def main():
    """
    Orchestrate the full health report run.

    Workflow:
        1. Parse command-line arguments.
        2. Find all parquet files in scope.
        3. Inspect each file individually.
        4. Aggregate per-file results into pipeline-wide metrics.
        5. Print the console dashboard.
        6. Export CSV reports.
        7. Exit with status code reflecting overall health.
    """
    parser = argparse.ArgumentParser(
        description="Generate a health report for the GTFS-RT pipeline."
    )
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=DEFAULT_DATA_DIR,
        help=f"Root data directory (default: {DEFAULT_DATA_DIR})",
    )
    parser.add_argument(
        "--date",
        type=str,
        default=None,
        help="Limit scan to a specific date subfolder, e.g. 2026-05-09",
    )
    args = parser.parse_args()

    # Step 1: discover files
    files = find_parquet_files(args.data_dir, date_filter=args.date)
    if not files:
        logger.error("No parquet files found. Pipeline may not be running.")
        sys.exit(2)

    # Step 2: inspect each file
    results = []
    for i, f in enumerate(files, 1):
        logger.info(f"[{i}/{len(files)}] Inspecting {f.name}")
        results.append(inspect_file(f))

    # Step 3: aggregate
    summary = aggregate_metrics(results)

    # Step 4: print console report
    print_report(summary)

    # Step 5: export CSV
    export_csv(results, summary)

    # Step 6: exit code reflects health
    # 0 = healthy, 1 = failures detected, 2 = no data at all
    # This makes the script useful in CI/CD and Railway cron jobs:
    # a non-zero exit triggers alerting automatically.
    if summary["failed_files"] > 0:
        logger.warning(f"{summary['failed_files']} failed files detected.")
        sys.exit(1)

    logger.info("Pipeline health check complete. No failures detected.")
    sys.exit(0)


if __name__ == "__main__":
    main()
