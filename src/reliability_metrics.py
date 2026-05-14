"""
reliability_metrics.py
======================
First operational analytics layer for the GTFS-RT transit telemetry platform.

This is where raw telemetry becomes transit intelligence.

The previous layers answer "is the pipeline working?" — this layer answers
"what is the transit system actually doing?". It transforms millions of
vehicle position records into the operational metrics that a transit agency
like TransLink would put on a planning dashboard: which routes are running,
how fast vehicles are moving, where coverage is thin, where buses are stuck.

Design philosophy:
    - Read once, compute many. Loading parquet files is the expensive step,
      so we load the full dataset into memory once and run every metric
      against that single dataframe.
    - Each metric is its own function. This makes the code testable, lets
      you run individual metrics during debugging, and maps cleanly to
      individual CSV outputs.
    - Outputs are analytics-ready CSVs. Downstream consumers (BI tools,
      notebooks, dashboards) shouldn't need to do any cleaning to use them.
    - Console summary is a sanity check, not the product. The CSVs are the
      real deliverable — the console output exists so you can spot obvious
      problems before sharing the reports.

Usage (from project root):
    python src/reliability_metrics.py
    python src/reliability_metrics.py --date 2026-05-09
    python src/reliability_metrics.py --data-dir data/raw --output-dir reports
"""

import argparse
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

import pandas as pd


# ---------------------------------------------------------------------------
# CONFIGURATION
# ---------------------------------------------------------------------------
# Default I/O locations relative to project root.
DEFAULT_DATA_DIR = Path("data/raw")
DEFAULT_OUTPUT_DIR = Path("reports")

# Columns the collector writes — referenced for schema validation.
REQUIRED_COLUMNS = [
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

# Operational threshold: a vehicle reporting speed below this (m/s) is
# considered "stationary". 0.5 m/s ≈ 1.8 km/h, which is slower than a
# walking pace — well below any real bus movement.
# Why this threshold matters: stationary buses are the leading indicator
# of operational problems. A bus stopped at a red light is fine; a bus
# stopped for 10 minutes is a service disruption.
STATIONARY_SPEED_THRESHOLD_MPS = 0.5

# How many "top N" routes to include in busiest-routes outputs.
TOP_N_ROUTES = 15


# ---------------------------------------------------------------------------
# LOGGING
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-7s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("reliability_metrics")


# ---------------------------------------------------------------------------
# DATA LOADING
# ---------------------------------------------------------------------------
def find_parquet_files(data_dir: Path, date_filter: Optional[str] = None) -> List[Path]:
    """
    Discover parquet files to analyze.

    What it does:
        Recursively walks data_dir looking for *.parquet files. If a date
        filter is provided, only files under that date's subfolder are
        returned.

    Why it matters:
        Operational metrics make most sense over a coherent time window —
        usually a single day. The date filter lets you produce "what happened
        on Tuesday?" reports without re-processing the entire archive.

    How it helps:
        Most real transit analytics is daily: planners look at yesterday's
        operations to plan tomorrow's. Date-scoped analysis matches that
        workflow.
    """
    if not data_dir.exists():
        logger.error(f"Data directory does not exist: {data_dir}")
        return []

    search_root = data_dir / date_filter if date_filter else data_dir
    if not search_root.exists():
        logger.error(f"Search root does not exist: {search_root}")
        return []

    files = sorted(search_root.rglob("*.parquet"))
    logger.info(f"Discovered {len(files)} parquet files under {search_root}")
    return files


def load_dataset(files: List[Path]) -> pd.DataFrame:
    """
    Load every parquet file into a single combined dataframe.

    What it does:
        Reads each file, concatenates them into one big dataframe, drops any
        files that fail to load (with a warning), and validates the resulting
        schema.

    Why it matters:
        Every downstream metric needs the same input shape. Consolidating
        once at the start means every metric function just receives a clean
        dataframe — no per-metric file iteration, no repeated I/O.

    Engineering decision — why one big dataframe:
        For a single day of 30-second polling, you'll have ~2,880 parquet
        files with maybe a few hundred rows each. That's tens of MB of data,
        easily fits in memory, and pandas can crunch it in seconds.
        If we scaled this to a month or more, we'd switch to a chunked
        approach or move to DuckDB / Polars. But for "yesterday's operations",
        the all-in-memory approach is the right tradeoff: simple code, fast
        analytics, easy debugging.

    Returns:
        DataFrame with all rows from all files, or an empty DataFrame if
        nothing loaded.
    """
    if not files:
        return pd.DataFrame()

    frames = []
    skipped = 0
    for i, f in enumerate(files, 1):
        try:
            df = pd.read_parquet(f)
            if len(df) > 0:
                frames.append(df)
        except Exception as e:
            # Defensive: corrupt files should already be caught by the health
            # report, but we don't want one bad file to crash analytics.
            logger.warning(f"Skipping unreadable file {f.name}: {e}")
            skipped += 1

    if not frames:
        logger.error("No readable parquet files found")
        return pd.DataFrame()

    combined = pd.concat(frames, ignore_index=True)
    logger.info(
        f"Loaded {len(combined):,} rows from {len(frames)} files "
        f"({skipped} skipped)"
    )

    # Schema sanity check: warn if expected columns are missing.
    # We don't fail hard — some metrics can still run with partial schemas.
    missing = [c for c in REQUIRED_COLUMNS if c not in combined.columns]
    if missing:
        logger.warning(f"Expected columns missing from dataset: {missing}")

    return combined


def normalize_timestamps(df: pd.DataFrame) -> pd.DataFrame:
    """
    Convert timestamp columns to proper datetime types.

    What it does:
        Parses collection_timestamp and api_vehicle_timestamp as UTC datetimes.
        Adds a derived 'hour' column for hourly bucketing.

    Why it matters:
        Parquet stores timestamps natively, but after concat the dtype can
        drift to object in edge cases. Normalizing here means every downstream
        function can assume datetime semantics work.

    Engineering decision — why a derived hour column:
        Multiple metrics need "group by hour of day". Computing the hour once
        and storing it is cheaper than recomputing it inside every groupby.
        This is a common pattern: derive shared features once, reuse everywhere.
    """
    if "collection_timestamp" in df.columns:
        df["collection_timestamp"] = pd.to_datetime(
            df["collection_timestamp"], errors="coerce", utc=True
        )
        # Add hour bucket for hourly aggregations.
        df["hour"] = df["collection_timestamp"].dt.hour

    if "api_vehicle_timestamp" in df.columns:
        df["api_vehicle_timestamp"] = pd.to_datetime(
            df["api_vehicle_timestamp"], errors="coerce", utc=True
        )

    return df


# ---------------------------------------------------------------------------
# METRIC: VEHICLE COUNTS BY ROUTE
# ---------------------------------------------------------------------------
def compute_route_vehicle_counts(df: pd.DataFrame) -> pd.DataFrame:
    """
    Count records and unique vehicles per route.

    What it means:
        For each route_id, how many position records did we see, and how
        many distinct vehicles were on that route during the analysis window?

    Why transit agencies care:
        - Total records = how heavily that route is being observed. Routes
          with thousands of records had vehicles running all day; routes
          with a handful are infrequent or possibly suspended.
        - Unique vehicles = the fleet allocation. A route running every
          10 minutes during peak needs more vehicles than a route running
          every 30 minutes.
        - Records / unique_vehicles ratio = how long each vehicle stayed
          on that route. High ratio = long shifts, common on trunk routes.
          Low ratio = vehicles rotating off, common on short feeder routes.

    Operational insight:
        Planners use this to validate that the schedule matches reality.
        If route 99 is supposed to have 12 vehicles and we only see 8,
        either the schedule is wrong or buses didn't show up for service.
    """
    if "route_id" not in df.columns:
        logger.warning("No route_id column — skipping route vehicle counts")
        return pd.DataFrame()

    # groupby + agg: standard pandas pattern for per-group statistics.
    # The 'nunique' aggregation counts distinct values (in our case, distinct
    # vehicle_ids) within each route_id group.
    result = (
        df.groupby("route_id", dropna=True)
        .agg(
            total_records=("vehicle_id", "count"),
            unique_vehicles=("vehicle_id", "nunique"),
        )
        .reset_index()
    )

    # Derived ratio: average records per vehicle on this route.
    # round(2) keeps the CSV readable — analysts don't need 15 decimal places.
    result["records_per_vehicle"] = (
        result["total_records"] / result["unique_vehicles"]
    ).round(2)

    # Sort descending so the busiest routes are at the top of the CSV.
    # Sorting at output time makes spreadsheet inspection much easier.
    result = result.sort_values("total_records", ascending=False).reset_index(drop=True)

    return result


# ---------------------------------------------------------------------------
# METRIC: AVERAGE SPEED BY ROUTE
# ---------------------------------------------------------------------------
def compute_route_avg_speed(df: pd.DataFrame) -> pd.DataFrame:
    """
    Compute speed statistics per route.

    What it means:
        For each route, what's the mean speed, the median speed, and the
        speed at the 95th percentile? Speed is reported by the GTFS-RT feed
        in meters per second.

    Why transit agencies care:
        - Mean speed answers "how fast does service flow on this route?".
          A bus route averaging 3 m/s (~11 km/h) is dramatically slower
          than one averaging 8 m/s (~29 km/h), which tells you about
          congestion, stop density, and route geometry.
        - Median is more robust to outliers (one stopped vehicle pulls the
          mean down).
        - p95 captures the "uncongested" experience — what's possible when
          conditions are good.

    Engineering decision — converting units:
        Internal computations stay in m/s (the feed's native unit) but we
        also output km/h because that's what planners and the public think
        in. Always offer both — engineers will sanity-check with m/s,
        analysts will report in km/h.

    Operational insight:
        Year-over-year drops in average speed on a route are a leading
        indicator of growing congestion. This is the metric that gets cited
        in transit planning documents and bus-lane proposals.
    """
    if "route_id" not in df.columns or "speed" not in df.columns:
        logger.warning("Missing route_id or speed — skipping speed analysis")
        return pd.DataFrame()

    # Filter out rows with no speed data BEFORE aggregating. If we don't,
    # nulls get included as zeros and skew everything downward.
    speed_df = df[df["speed"].notna()].copy()

    if speed_df.empty:
        logger.warning("No rows with valid speed data")
        return pd.DataFrame()

    # quantile() gives us percentiles — 0.50 is median, 0.95 is the 95th.
    # Using a single groupby with multiple aggregations is much faster than
    # running three separate groupbys.
    result = (
        speed_df.groupby("route_id", dropna=True)
        .agg(
            sample_count=("speed", "count"),
            mean_speed_mps=("speed", "mean"),
            median_speed_mps=("speed", "median"),
            p95_speed_mps=("speed", lambda s: s.quantile(0.95)),
            max_speed_mps=("speed", "max"),
        )
        .reset_index()
    )

    # Convert to km/h for human-friendly reading. 1 m/s = 3.6 km/h.
    for col in ["mean_speed_mps", "median_speed_mps", "p95_speed_mps", "max_speed_mps"]:
        kmh_col = col.replace("_mps", "_kmh")
        result[kmh_col] = (result[col] * 3.6).round(2)
        result[col] = result[col].round(3)

    result = result.sort_values("mean_speed_mps", ascending=False).reset_index(drop=True)
    return result


# ---------------------------------------------------------------------------
# METRIC: ROUTE COVERAGE OVER TIME
# ---------------------------------------------------------------------------
def compute_route_coverage(df: pd.DataFrame) -> pd.DataFrame:
    """
    For each route, when did we first and last see it, and across how many
    hours did it operate?

    What it means:
        - first_seen / last_seen: timestamps of the earliest and latest
          observation on this route.
        - active_hours: distinct hours of the day in which this route had
          any vehicle reporting.
        - active_span_hours: wall-clock duration from first to last sighting.

    Why transit agencies care:
        - Routes are supposed to operate within published service hours.
          If a route's first_seen is 6:30 AM but the schedule says service
          starts at 5:00 AM, the early-morning runs are missing.
        - active_hours tells you how spread-out service is. A route with
          18 active_hours runs nearly all day; a route with 4 is rush-hour
          only.

    Operational insight:
        Coverage gaps are operational failures that are easy to miss without
        this metric. A driver no-show that knocks out the first run of the
        day shows up here as a delayed first_seen.
    """
    if "route_id" not in df.columns or "collection_timestamp" not in df.columns:
        logger.warning("Missing route_id or collection_timestamp — skipping coverage")
        return pd.DataFrame()

    # Drop nulls in critical columns before aggregating.
    cov_df = df.dropna(subset=["route_id", "collection_timestamp"]).copy()
    if cov_df.empty:
        return pd.DataFrame()

    result = (
        cov_df.groupby("route_id", dropna=True)
        .agg(
            total_records=("collection_timestamp", "count"),
            first_seen=("collection_timestamp", "min"),
            last_seen=("collection_timestamp", "max"),
            active_hours=("hour", "nunique"),
        )
        .reset_index()
    )

    # Wall-clock span between first and last sighting.
    # total_seconds() / 3600 gives a clean float in hours.
    result["active_span_hours"] = (
        (result["last_seen"] - result["first_seen"]).dt.total_seconds() / 3600
    ).round(2)

    # ISO format timestamps render cleanly in CSV and Excel.
    result["first_seen"] = result["first_seen"].dt.strftime("%Y-%m-%d %H:%M:%S")
    result["last_seen"] = result["last_seen"].dt.strftime("%Y-%m-%d %H:%M:%S")

    result = result.sort_values("total_records", ascending=False).reset_index(drop=True)
    return result


# ---------------------------------------------------------------------------
# METRIC: GPS UPDATE FREQUENCY
# ---------------------------------------------------------------------------
def compute_gps_update_frequency(df: pd.DataFrame) -> pd.DataFrame:
    """
    Calculate how often each vehicle reports its position.

    What it means:
        For each vehicle, what's the median time between consecutive position
        updates? GTFS-RT feeds vary — some agencies push every 10 seconds,
        others every 60. The median per-vehicle update interval characterizes
        the actual data quality the agency is providing.

    Why transit agencies care:
        - Real-time arrival predictions are only as accurate as the underlying
          GPS data. A 30-second median update is good; a 5-minute median is
          essentially useless for arrival predictions.
        - Vehicles with much longer update intervals than peers may have
          failing on-board hardware.

    Engineering decision — using api_vehicle_timestamp, not collection_timestamp:
        collection_timestamp = when WE polled. api_vehicle_timestamp = when
        the vehicle reported. We care about the vehicle's reporting cadence,
        not our polling cadence, so api_vehicle_timestamp is the right input.

    Engineering decision — using median, not mean:
        A single long gap (vehicle out of service for hours, then back)
        would skew the mean wildly. Median is robust to that and reflects
        the typical experience.

    Operational insight:
        If the median update interval is way longer than your polling interval,
        the bottleneck is the agency's data, not your collector. That's an
        important distinction when explaining pipeline performance.
    """
    if "vehicle_id" not in df.columns or "api_vehicle_timestamp" not in df.columns:
        logger.warning("Missing vehicle_id or api_vehicle_timestamp — skipping GPS frequency")
        return pd.DataFrame()

    freq_df = df.dropna(subset=["vehicle_id", "api_vehicle_timestamp"]).copy()
    if freq_df.empty:
        return pd.DataFrame()

    # Sort by vehicle, then by report time. After sorting, consecutive rows
    # within the same vehicle are consecutive reports — exactly what we need
    # for diff().
    freq_df = freq_df.sort_values(["vehicle_id", "api_vehicle_timestamp"])

    # diff() within each vehicle group: time elapsed between consecutive
    # reports. The first row of each group is NaT (no prior report exists),
    # which we drop below.
    freq_df["interval_seconds"] = (
        freq_df.groupby("vehicle_id")["api_vehicle_timestamp"]
        .diff()
        .dt.total_seconds()
    )

    # Drop the NaT first-rows and any negative diffs (shouldn't happen after
    # sort, but defensive coding catches data quality surprises).
    freq_df = freq_df[freq_df["interval_seconds"] > 0]
    if freq_df.empty:
        return pd.DataFrame()

    result = (
        freq_df.groupby("vehicle_id", dropna=True)
        .agg(
            report_count=("interval_seconds", "count"),
            median_interval_sec=("interval_seconds", "median"),
            mean_interval_sec=("interval_seconds", "mean"),
            max_interval_sec=("interval_seconds", "max"),
        )
        .reset_index()
    )

    for col in ["median_interval_sec", "mean_interval_sec", "max_interval_sec"]:
        result[col] = result[col].round(1)

    # Sort by median interval ascending — best-reporting vehicles at the top.
    result = result.sort_values("median_interval_sec").reset_index(drop=True)
    return result


# ---------------------------------------------------------------------------
# METRIC: STATIONARY VEHICLE DETECTION
# ---------------------------------------------------------------------------
def compute_stationary_vehicles(df: pd.DataFrame) -> pd.DataFrame:
    """
    Identify vehicles that report low or zero speed.

    What it means:
        For each vehicle, what fraction of its reports were below the
        stationary speed threshold? A high fraction means the vehicle spent
        a lot of time not moving.

    Why transit agencies care:
        - Brief stops are normal: traffic lights, passenger boarding, layovers.
        - Extended stops indicate problems: mechanical breakdowns, traffic
          congestion, or operator issues.
        - The fraction-stationary metric, aggregated across a fleet, reveals
          congestion hotspots and reliability issues.

    Engineering decision — threshold-based, not zero-based:
        GPS noise means a stationary vehicle rarely reports exactly 0.0.
        Using STATIONARY_SPEED_THRESHOLD_MPS (0.5 m/s) catches "effectively
        stopped" without missing edge cases.

    Engineering decision — fraction, not absolute count:
        A vehicle with 1000 reports and 200 stationary is very different
        from one with 50 reports and 200 stationary (impossible, but
        illustrates the point). Always normalize counts to fractions when
        comparing across entities with different sample sizes.

    Operational insight:
        Vehicles spending more than ~30% of their time stationary deserve
        investigation. That could be normal for a route with many timed
        layovers, or it could be a breakdown that wasn't reported.
    """
    if "vehicle_id" not in df.columns or "speed" not in df.columns:
        logger.warning("Missing vehicle_id or speed — skipping stationary analysis")
        return pd.DataFrame()

    stat_df = df.dropna(subset=["vehicle_id", "speed"]).copy()
    if stat_df.empty:
        return pd.DataFrame()

    # Boolean column: True where speed is below the stationary threshold.
    # Casting to int lets us sum/mean it directly in the groupby.
    stat_df["is_stationary"] = (
        stat_df["speed"] < STATIONARY_SPEED_THRESHOLD_MPS
    ).astype(int)

    result = (
        stat_df.groupby("vehicle_id", dropna=True)
        .agg(
            total_reports=("speed", "count"),
            stationary_reports=("is_stationary", "sum"),
            # Including route_id helps cross-reference: which routes have
            # the most-stuck vehicles?
            primary_route=("route_id", lambda s: s.mode().iloc[0] if not s.mode().empty else None),
        )
        .reset_index()
    )

    # Fraction of time spent stationary. Multiply by 100 for percentage.
    result["pct_stationary"] = (
        result["stationary_reports"] / result["total_reports"] * 100
    ).round(2)

    # Sort descending so the most-stuck vehicles surface first.
    result = result.sort_values("pct_stationary", ascending=False).reset_index(drop=True)
    return result


# ---------------------------------------------------------------------------
# METRIC: HOURLY TIMESTAMP DISTRIBUTION
# ---------------------------------------------------------------------------
def compute_hourly_distribution(df: pd.DataFrame) -> pd.DataFrame:
    """
    How many records, unique vehicles, and unique routes per hour of day?

    What it means:
        Buckets all observations by hour-of-day (0–23). Shows the
        diurnal pattern of transit activity.

    Why transit agencies care:
        - Peak vs. off-peak service levels are visible at a glance.
        - Sudden dips at unexpected hours flag service disruptions.
        - The shape of the curve should match the published service schedule;
          mismatches are operational issues.

    Operational insight:
        A "normal" weekday transit system shows two peaks (morning and
        evening rush) with a midday plateau. Weekends are flatter and lower.
        Anything that doesn't match that pattern deserves a look.
    """
    if "hour" not in df.columns:
        logger.warning("No hour column — skipping hourly distribution")
        return pd.DataFrame()

    result = (
        df.dropna(subset=["hour"])
        .groupby("hour")
        .agg(
            total_records=("vehicle_id", "count"),
            unique_vehicles=("vehicle_id", "nunique"),
            unique_routes=("route_id", "nunique"),
        )
        .reset_index()
        .sort_values("hour")
        .reset_index(drop=True)
    )
    result["hour"] = result["hour"].astype(int)
    return result


# ---------------------------------------------------------------------------
# OUTPUT
# ---------------------------------------------------------------------------
def save_csv(df: pd.DataFrame, output_dir: Path, name: str) -> Optional[Path]:
    """
    Save a metric dataframe to CSV.

    What it does:
        Writes the dataframe to output_dir/name.csv. Skips empty dataframes
        with a warning — empty CSVs are worse than no CSVs because they
        look like the metric ran and just returned nothing important.

    Why it matters:
        CSVs are the unit of delivery for this layer. Every metric produces
        a CSV, and those CSVs are what downstream notebooks, dashboards,
        and analysts consume.
    """
    if df.empty:
        logger.warning(f"Skipping {name}.csv — no data")
        return None

    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / f"{name}.csv"
    df.to_csv(path, index=False, encoding="utf-8")
    logger.info(f"Saved {name}.csv ({len(df):,} rows)")
    return path


def print_console_summary(
    df: pd.DataFrame,
    route_counts: pd.DataFrame,
    route_speeds: pd.DataFrame,
    coverage: pd.DataFrame,
    gps_freq: pd.DataFrame,
    stationary: pd.DataFrame,
    hourly: pd.DataFrame,
) -> None:
    """
    Print a compact dashboard summarizing the metrics.

    What it does:
        Shows headline numbers and the top-N entries from each metric.

    Why it matters:
        The CSVs are the real product, but a console summary catches obvious
        problems before you bother opening the files. If the console shows
        "0 unique routes" you know something's wrong before reading any CSV.
    """
    bar = "=" * 70
    print()
    print(bar)
    print("  TRANSIT RELIABILITY METRICS SUMMARY")
    print(bar)
    print(f"  Generated:              {datetime.now(timezone.utc).isoformat()}")
    print(f"  Total records analyzed: {len(df):,}")

    if "vehicle_id" in df.columns:
        print(f"  Unique vehicles:        {df['vehicle_id'].nunique():,}")
    if "route_id" in df.columns:
        print(f"  Unique routes:          {df['route_id'].nunique():,}")
    if "collection_timestamp" in df.columns:
        valid_ts = df["collection_timestamp"].dropna()
        if not valid_ts.empty:
            print(f"  Earliest record:        {valid_ts.min()}")
            print(f"  Latest record:          {valid_ts.max()}")
    print()

    # --- Top routes by activity ---
    if not route_counts.empty:
        print(f"  TOP {TOP_N_ROUTES} ROUTES BY RECORD COUNT")
        print("  " + "-" * 60)
        for _, row in route_counts.head(TOP_N_ROUTES).iterrows():
            print(
                f"  {str(row['route_id']):<12} "
                f"{int(row['total_records']):>8,} records, "
                f"{int(row['unique_vehicles']):>4} vehicles"
            )
        print()

    # --- Fastest & slowest routes ---
    if not route_speeds.empty:
        print("  ROUTES — FASTEST 5 BY MEAN SPEED")
        print("  " + "-" * 60)
        for _, row in route_speeds.head(5).iterrows():
            print(
                f"  {str(row['route_id']):<12} "
                f"{row['mean_speed_kmh']:>6.2f} km/h "
                f"(median {row['median_speed_kmh']:.2f}, "
                f"p95 {row['p95_speed_kmh']:.2f})"
            )
        print()
        print("  ROUTES — SLOWEST 5 BY MEAN SPEED")
        print("  " + "-" * 60)
        for _, row in route_speeds.tail(5).iloc[::-1].iterrows():
            print(
                f"  {str(row['route_id']):<12} "
                f"{row['mean_speed_kmh']:>6.2f} km/h "
                f"(median {row['median_speed_kmh']:.2f}, "
                f"p95 {row['p95_speed_kmh']:.2f})"
            )
        print()

    # --- GPS update frequency overview ---
    if not gps_freq.empty:
        print("  GPS UPDATE FREQUENCY")
        print("  " + "-" * 60)
        median_of_medians = gps_freq["median_interval_sec"].median()
        print(f"  Fleet median update interval: {median_of_medians:.1f} sec")
        print(f"  Vehicles analyzed:            {len(gps_freq):,}")
        print()

    # --- Stationary vehicles overview ---
    if not stationary.empty:
        print("  TOP 5 MOST-STATIONARY VEHICLES")
        print("  " + "-" * 60)
        for _, row in stationary.head(5).iterrows():
            print(
                f"  {str(row['vehicle_id']):<15} "
                f"{row['pct_stationary']:>6.2f}% stationary "
                f"(route {row['primary_route']})"
            )
        print()

    # --- Hourly distribution ---
    if not hourly.empty:
        print("  HOURLY ACTIVITY")
        print("  " + "-" * 60)
        max_records = hourly["total_records"].max()
        for _, row in hourly.iterrows():
            # ASCII bar chart, scaled to the busiest hour
            bar_len = int((row["total_records"] / max_records) * 40) if max_records else 0
            bar_viz = "█" * bar_len
            print(
                f"  {int(row['hour']):02d}:00  "
                f"{int(row['total_records']):>7,} records  {bar_viz}"
            )
        print()

    print(bar)


# ---------------------------------------------------------------------------
# MAIN
# ---------------------------------------------------------------------------
def main():
    """
    Orchestrate the full reliability metrics run.

    Workflow:
        1. Parse arguments.
        2. Discover and load parquet files.
        3. Normalize timestamps.
        4. Compute each metric.
        5. Print console summary.
        6. Save all CSVs.
    """
    parser = argparse.ArgumentParser(
        description="Generate operational reliability metrics for GTFS-RT pipeline."
    )
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=DEFAULT_DATA_DIR,
        help=f"Root data directory (default: {DEFAULT_DATA_DIR})",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        help=f"Output directory for CSVs (default: {DEFAULT_OUTPUT_DIR})",
    )
    parser.add_argument(
        "--date",
        type=str,
        default=None,
        help="Limit analysis to a specific date subfolder, e.g. 2026-05-09",
    )
    args = parser.parse_args()

    # --- Load ---
    files = find_parquet_files(args.data_dir, date_filter=args.date)
    if not files:
        logger.error("No parquet files found — nothing to analyze")
        sys.exit(2)

    df = load_dataset(files)
    if df.empty:
        logger.error("Loaded dataset is empty — nothing to analyze")
        sys.exit(2)

    df = normalize_timestamps(df)

    # --- Compute ---
    # Each metric is independent — if one fails, others still produce output.
    logger.info("Computing route vehicle counts...")
    route_counts = compute_route_vehicle_counts(df)

    logger.info("Computing route average speeds...")
    route_speeds = compute_route_avg_speed(df)

    logger.info("Computing route coverage...")
    coverage = compute_route_coverage(df)

    logger.info("Computing GPS update frequency...")
    gps_freq = compute_gps_update_frequency(df)

    logger.info("Computing stationary vehicle summary...")
    stationary = compute_stationary_vehicles(df)

    logger.info("Computing hourly distribution...")
    hourly = compute_hourly_distribution(df)

    # --- Report ---
    print_console_summary(
        df, route_counts, route_speeds, coverage, gps_freq, stationary, hourly
    )

    # --- Export ---
    save_csv(route_counts, args.output_dir, "route_vehicle_counts")
    save_csv(route_speeds, args.output_dir, "route_avg_speed")
    save_csv(coverage, args.output_dir, "route_coverage_summary")
    save_csv(gps_freq, args.output_dir, "gps_update_frequency")
    save_csv(stationary, args.output_dir, "stationary_vehicle_summary")
    save_csv(hourly, args.output_dir, "hourly_distribution")

    logger.info("Reliability metrics generation complete.")
    sys.exit(0)


if __name__ == "__main__":
    main()