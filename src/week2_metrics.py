"""
week2_metrics.py
================
Week 2 operational analytics layer for the GTFS-RT transit telemetry platform.

Where Week 1 answered "is the pipeline working?" and "what does a single day
of operations look like?", Week 2 takes the next step: multi-day analysis.

With 5 days of clean telemetry (2026-05-19 → 2026-05-23) we can finally ask
questions that need *time* to answer:
    - Which routes run consistently every day vs. only some days?
    - How does network activity vary by day-of-week and hour-of-day together?
    - Which vehicles are workhorses (active every day) vs. spares?
    - Which routes have suspicious variance day-over-day (outliers)?
    - Is telemetry completeness stable, or are there days where coverage drops?

Design philosophy (consistent with reliability_metrics.py):
    - Load once, compute many. All parquet files are concatenated into one
      dataframe; every metric reads from that single source.
    - Each metric is a self-contained function returning a tidy dataframe.
    - Outputs are analytics-ready CSVs in reports/week2/.
    - Schema is taken from the REAL parquet schema, not assumptions:
        collection_timestamp   datetime64[us, UTC]
        api_vehicle_timestamp  datetime64[us, UTC]
        entity_id              str
        vehicle_id             str
        route_id               str
        trip_id                str
        latitude               float64
        longitude              float64
        bearing                float64  (currently all zeros — unused)
        speed                  float64  (currently all zeros — unused)

    Because `speed` and `bearing` are not populated by the upstream GTFS-RT
    feed, all Week 2 metrics are derived from row-level activity and
    timestamp patterns — NOT from kinematic fields. This keeps the script
    robust to the known data-quality issue.

Usage (from project root):
    python src/week2_metrics.py
    python src/week2_metrics.py --start-date 2026-05-19 --end-date 2026-05-23
    python src/week2_metrics.py --data-dir data/raw --output-dir reports/week2
"""

import argparse
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional, Tuple

import pandas as pd


# ---------------------------------------------------------------------------
# CONFIGURATION
# ---------------------------------------------------------------------------
# Default I/O locations relative to project root.
DEFAULT_DATA_DIR = Path("data/raw")
DEFAULT_OUTPUT_DIR = Path("reports/week2")

# Default analysis window — the five clean days available as of Week 2.
# These act as defaults; CLI flags override.
DEFAULT_START_DATE = "2026-05-19"
DEFAULT_END_DATE = "2026-05-23"

# Real schema from the parquet files. Verified against a sample file.
# Keep this in sync with the collector's output schema.
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

# Operational thresholds — tuned to TransLink's network shape.
# A route seen on fewer than this many days is "intermittent" rather than
# part of the persistent network. With a 5-day window, 5 = every day,
# 4 = missed at most one day.
PERSISTENT_DAYS_THRESHOLD = 4

# When detecting outlier routes, we flag routes whose day-over-day record
# count varies by more than this coefficient of variation (stddev / mean).
# 0.5 is conservative — routes with normal weekday/weekend differences
# should pass; only routes with truly erratic counts get flagged.
OUTLIER_CV_THRESHOLD = 0.5

# Top-N counts for console summary.
TOP_N_ROUTES = 15


# ---------------------------------------------------------------------------
# LOGGING
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-7s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("week2_metrics")


# ---------------------------------------------------------------------------
# FILE DISCOVERY
# ---------------------------------------------------------------------------
def find_parquet_files_in_range(
    data_dir: Path,
    start_date: str,
    end_date: str,
) -> List[Path]:
    """
    Find all parquet files whose date-partitioned subfolder falls within
    [start_date, end_date] inclusive.

    What it does:
        Iterates date subfolders under data_dir, keeps only those that parse
        as a date inside the requested range, and recursively collects every
        *.parquet file beneath them.

    Why it matters:
        Multi-day analysis only makes sense if you can scope it to the days
        you actually want. The collector writes into data/raw/YYYY-MM-DD/,
        so this function is the entry point that maps a date range to a
        concrete file list.

    Returns:
        Sorted list of parquet file paths. Sorting is stable so reruns
        always process files in the same order — important for reproducibility.
    """
    if not data_dir.exists():
        logger.error(f"Data directory does not exist: {data_dir}")
        return []

    try:
        start_dt = datetime.strptime(start_date, "%Y-%m-%d").date()
        end_dt = datetime.strptime(end_date, "%Y-%m-%d").date()
    except ValueError as e:
        logger.error(f"Invalid date format (expected YYYY-MM-DD): {e}")
        return []

    if start_dt > end_dt:
        logger.error(f"start_date {start_date} is after end_date {end_date}")
        return []

    all_files: List[Path] = []
    matched_dates: List[str] = []

    # Iterate every immediate subfolder of data_dir. Only those that parse
    # as a date within our window contribute files.
    for subdir in sorted(data_dir.iterdir()):
        if not subdir.is_dir():
            continue
        try:
            sub_dt = datetime.strptime(subdir.name, "%Y-%m-%d").date()
        except ValueError:
            # Not a date-partitioned folder — skip silently.
            continue

        if start_dt <= sub_dt <= end_dt:
            files_in_day = sorted(subdir.rglob("*.parquet"))
            if files_in_day:
                matched_dates.append(subdir.name)
                all_files.extend(files_in_day)
                logger.info(f"  {subdir.name}: {len(files_in_day):,} files")

    logger.info(
        f"Discovered {len(all_files):,} parquet files across "
        f"{len(matched_dates)} day(s): {matched_dates}"
    )
    return all_files


# ---------------------------------------------------------------------------
# DATA LOADING
# ---------------------------------------------------------------------------
def load_dataset(files: List[Path]) -> Tuple[pd.DataFrame, int]:
    """
    Load every parquet file into a single combined dataframe.

    What it does:
        Reads each parquet file with pandas, skips any file that fails to
        load (logging a warning), and concatenates the survivors.

    Why it matters:
        Every Week 2 metric needs the same input shape. Doing the load once
        means each metric function receives a clean dataframe and never
        touches the filesystem.

    Engineering decision — defensive loading:
        Even though Week 1 health reports verify file integrity, we still
        wrap each read in try/except. A single unreadable file should never
        crash a multi-day analytics run.

    Returns:
        (combined_dataframe, skipped_file_count)
    """
    if not files:
        return pd.DataFrame(), 0

    frames: List[pd.DataFrame] = []
    skipped = 0
    log_every = max(1, len(files) // 20)  # ~20 progress messages total

    for i, f in enumerate(files, 1):
        try:
            df = pd.read_parquet(f)
            if len(df) > 0:
                frames.append(df)
        except Exception as e:
            logger.warning(f"Skipping unreadable file {f.name}: {e}")
            skipped += 1

        if i % log_every == 0 or i == len(files):
            logger.info(f"  Loaded {i:,}/{len(files):,} files")

    if not frames:
        logger.error("No readable parquet files — nothing to analyze")
        return pd.DataFrame(), skipped

    combined = pd.concat(frames, ignore_index=True)
    logger.info(
        f"Combined dataset: {len(combined):,} rows from "
        f"{len(frames):,} files ({skipped} skipped)"
    )

    # Schema sanity check — warn but don't fail. Some metrics can still
    # run with a partial schema.
    missing = [c for c in EXPECTED_COLUMNS if c not in combined.columns]
    if missing:
        logger.warning(f"Expected columns missing from dataset: {missing}")

    return combined, skipped


def enrich_with_time_columns(df: pd.DataFrame) -> pd.DataFrame:
    """
    Add derived time columns used by multiple metrics.

    What it does:
        - Ensures collection_timestamp is tz-aware UTC datetime.
        - Adds 'date' (YYYY-MM-DD string) for daily grouping.
        - Adds 'hour' (0–23 int) for hourly grouping.
        - Adds 'day_of_week' (0=Monday, 6=Sunday) for weekday/weekend logic.

    Why it matters:
        Computing these once at the top means every metric function can
        groupby them directly. Doing it lazily inside each metric would
        recompute the same dt accessors a half-dozen times.

    Note on the source column:
        We derive these from collection_timestamp (when WE polled) rather
        than api_vehicle_timestamp (when the vehicle reported). Collection
        time is the reliable clock — it's our server's timestamp and is
        never null or skewed. Vehicle timestamps can occasionally drift
        if a bus's on-board clock is off.
    """
    if "collection_timestamp" in df.columns:
        # Parquet already stores these as datetime64[us, UTC], but normalize
        # defensively in case a future writer change breaks that.
        df["collection_timestamp"] = pd.to_datetime(
            df["collection_timestamp"], errors="coerce", utc=True
        )
        df["date"] = df["collection_timestamp"].dt.strftime("%Y-%m-%d")
        df["hour"] = df["collection_timestamp"].dt.hour
        df["day_of_week"] = df["collection_timestamp"].dt.dayofweek

    if "api_vehicle_timestamp" in df.columns:
        df["api_vehicle_timestamp"] = pd.to_datetime(
            df["api_vehicle_timestamp"], errors="coerce", utc=True
        )

    return df


# ---------------------------------------------------------------------------
# METRIC 1: DAILY SUMMARY
# ---------------------------------------------------------------------------
def compute_daily_summary(df: pd.DataFrame) -> pd.DataFrame:
    """
    One row per day: headline operational numbers.

    What it means:
        For each calendar date in the analysis window:
        - total_records:    how much telemetry we collected
        - unique_vehicles:  how many distinct buses reported at least once
        - unique_routes:    how many distinct routes had any reporting
        - unique_trips:     how many distinct trip_ids appeared
        - active_hours:     how many distinct hours-of-day had any reporting
        - first_seen / last_seen: when the day's first and last record fell

    Why transit agencies care:
        This is the daily heartbeat of the network. Day-to-day swings in
        these numbers reveal everything from weekend service reductions to
        upstream feed outages. If 2026-05-23 shows 80% of the records of
        every other day, something happened that day worth investigating.

    Why it's row one of the Week 2 outputs:
        Every other report in this script slices the data differently;
        daily_summary is the "control panel" view that frames everything
        else. Open it first to know what kind of week you're looking at.
    """
    if "date" not in df.columns:
        logger.warning("No 'date' column — skipping daily summary")
        return pd.DataFrame()

    grouped = df.groupby("date", dropna=True)

    result = grouped.agg(
        total_records=("vehicle_id", "count"),
        unique_vehicles=("vehicle_id", "nunique"),
        unique_routes=("route_id", "nunique"),
        unique_trips=("trip_id", "nunique"),
        active_hours=("hour", "nunique"),
        first_seen=("collection_timestamp", "min"),
        last_seen=("collection_timestamp", "max"),
    ).reset_index()

    # Derived metric: average records per vehicle per day. Useful for
    # spotting days when vehicles reported less often (feed degradation
    # or polling gaps).
    result["records_per_vehicle"] = (
        result["total_records"] / result["unique_vehicles"]
    ).round(2)

    # Day-of-week label for human readers. Saturday/Sunday rows should
    # naturally show lower numbers; this makes that obvious in the CSV.
    result["day_of_week"] = pd.to_datetime(result["date"]).dt.day_name()

    # ISO format the timestamps for clean CSV rendering.
    result["first_seen"] = result["first_seen"].dt.strftime("%Y-%m-%d %H:%M:%S")
    result["last_seen"] = result["last_seen"].dt.strftime("%Y-%m-%d %H:%M:%S")

    return result.sort_values("date").reset_index(drop=True)


# ---------------------------------------------------------------------------
# METRIC 2: ROUTE ACTIVITY SUMMARY
# ---------------------------------------------------------------------------
def compute_route_activity_summary(df: pd.DataFrame) -> pd.DataFrame:
    """
    One row per route: aggregate activity over the entire window.

    What it means:
        For each route_id:
        - total_records:   total observations across all days
        - unique_vehicles: distinct vehicles that ran on this route
        - days_active:     how many of the analysis days this route appeared
        - active_hours:    distinct hours-of-day this route had reporting
        - first_seen / last_seen: window start and end for this route

    Why transit agencies care:
        This is the canonical "route load" report. A planner answering
        "which routes are heaviest?" looks here first. The combination of
        days_active and total_records also separates trunk routes (high on
        both) from peak-only express routes (high records, low days)
        from special services (low on both).

    How it relates to Week 1:
        Week 1 had a single-day version of this; Week 2's multi-day version
        adds the days_active dimension, which is what enables the
        persistence and outlier analyses later in the script.
    """
    if "route_id" not in df.columns:
        logger.warning("No route_id column — skipping route activity summary")
        return pd.DataFrame()

    grouped = df.groupby("route_id", dropna=True)

    result = grouped.agg(
        total_records=("vehicle_id", "count"),
        unique_vehicles=("vehicle_id", "nunique"),
        unique_trips=("trip_id", "nunique"),
        days_active=("date", "nunique"),
        active_hours=("hour", "nunique"),
        first_seen=("collection_timestamp", "min"),
        last_seen=("collection_timestamp", "max"),
    ).reset_index()

    # Derived metric: average records per vehicle on this route across the
    # window. Higher values = vehicles spent more time on this route.
    result["records_per_vehicle"] = (
        result["total_records"] / result["unique_vehicles"]
    ).round(2)

    # Format timestamps for CSV readability.
    result["first_seen"] = result["first_seen"].dt.strftime("%Y-%m-%d %H:%M:%S")
    result["last_seen"] = result["last_seen"].dt.strftime("%Y-%m-%d %H:%M:%S")

    return result.sort_values("total_records", ascending=False).reset_index(drop=True)


# ---------------------------------------------------------------------------
# METRIC 3: HOURLY NETWORK ACTIVITY
# ---------------------------------------------------------------------------
def compute_hourly_network_activity(df: pd.DataFrame) -> pd.DataFrame:
    """
    Network-wide activity bucketed by hour of day, averaged across days.

    What it means:
        For each hour-of-day (0–23), the AVERAGE per-day total records,
        unique vehicles, and unique routes. Aggregating across multiple
        days smooths out daily noise and shows the typical diurnal pattern.

    Why transit agencies care:
        Single-day hourly charts mix real patterns with one-day noise. A
        5-day average gives a much more reliable "what does a typical
        hour look like?" answer — the baseline you compare any single day
        against to detect anomalies.

    Engineering decision — averaging vs. summing:
        We compute totals per (date, hour) first, then average across
        dates. Summing would just give a bigger version of the single-day
        chart; averaging gives the "expected hour" that's actually useful
        for anomaly detection.
    """
    if "hour" not in df.columns or "date" not in df.columns:
        logger.warning("No 'hour' or 'date' column — skipping hourly network activity")
        return pd.DataFrame()

    # Step 1: per-(date, hour) totals.
    per_day_hour = (
        df.groupby(["date", "hour"], dropna=True)
        .agg(
            records=("vehicle_id", "count"),
            vehicles=("vehicle_id", "nunique"),
            routes=("route_id", "nunique"),
        )
        .reset_index()
    )

    # Step 2: average across dates for each hour bucket.
    result = (
        per_day_hour.groupby("hour")
        .agg(
            days_observed=("date", "nunique"),
            avg_records=("records", "mean"),
            avg_unique_vehicles=("vehicles", "mean"),
            avg_unique_routes=("routes", "mean"),
            min_records=("records", "min"),
            max_records=("records", "max"),
        )
        .reset_index()
    )

    # Round to readable precision. Float means like 2847.6666666 are visual
    # noise in a CSV; one decimal place is plenty.
    for col in ["avg_records", "avg_unique_vehicles", "avg_unique_routes"]:
        result[col] = result[col].round(1)

    result["hour"] = result["hour"].astype(int)
    return result.sort_values("hour").reset_index(drop=True)


# ---------------------------------------------------------------------------
# METRIC 4: ROUTE × HOUR HEATMAP
# ---------------------------------------------------------------------------
def compute_route_hour_heatmap(df: pd.DataFrame) -> pd.DataFrame:
    """
    Two-dimensional view: records per route per hour.

    What it means:
        A "long-form" table with one row per (route_id, hour) combination,
        showing how many records and unique vehicles appeared in each cell.
        This is the source data for a heatmap visualization in the notebook.

    Why transit agencies care:
        Route-level hourly behaviour reveals service patterns that
        per-route or per-hour aggregates hide. A route with most activity
        between 7–9 AM and 4–6 PM is a commuter peak service; a route
        evenly distributed across the day is full-time trunk service.

    Engineering decision — long format, not wide:
        We output (route_id, hour, value) rather than a pivot table.
        Long format is easier to filter, easier for tools like seaborn to
        consume directly, and avoids the awkwardness of having 24 columns
        named '0' through '23' in a CSV.
    """
    if "route_id" not in df.columns or "hour" not in df.columns:
        logger.warning("Missing route_id or hour — skipping route-hour heatmap")
        return pd.DataFrame()

    result = (
        df.groupby(["route_id", "hour"], dropna=True)
        .agg(
            records=("vehicle_id", "count"),
            unique_vehicles=("vehicle_id", "nunique"),
        )
        .reset_index()
    )

    result["hour"] = result["hour"].astype(int)
    return result.sort_values(["route_id", "hour"]).reset_index(drop=True)


# ---------------------------------------------------------------------------
# METRIC 5: VEHICLE ACTIVITY SUMMARY
# ---------------------------------------------------------------------------
def compute_vehicle_activity_summary(df: pd.DataFrame) -> pd.DataFrame:
    """
    One row per vehicle: how active was each bus across the window?

    What it means:
        For each vehicle_id:
        - total_records:   total observations across the window
        - days_active:     how many of the analysis days this vehicle ran
        - routes_served:   how many distinct routes this vehicle worked
        - primary_route:   the route this vehicle most frequently appeared on
        - first_seen / last_seen: window start and end for this vehicle

    Why transit agencies care:
        Fleet utilization. A bus active every day on a single route is a
        regular workhorse. A bus active occasionally on many routes is
        likely a spare or training vehicle. A bus that appeared only once
        could be a guest from another agency, a maintenance loaner, or a
        data glitch.

    Engineering decision — primary_route via mode():
        mode() returns the most common value. For vehicles that switch
        routes throughout a day or week, this captures their "home" route.
        We guard against ties and empty modes with .iloc[0] inside a
        defensive check.
    """
    if "vehicle_id" not in df.columns:
        logger.warning("No vehicle_id column — skipping vehicle activity summary")
        return pd.DataFrame()

    grouped = df.groupby("vehicle_id", dropna=True)

    result = grouped.agg(
        total_records=("vehicle_id", "count"),
        days_active=("date", "nunique"),
        routes_served=("route_id", "nunique"),
        first_seen=("collection_timestamp", "min"),
        last_seen=("collection_timestamp", "max"),
    ).reset_index()

    # Compute primary route separately. mode() can return multiple values
    # (ties), so we take the first via iloc[0] inside a try/except guard.
    def _primary_route(series: pd.Series) -> Optional[str]:
        s = series.dropna()
        if s.empty:
            return None
        m = s.mode()
        return m.iloc[0] if not m.empty else None

    primary = (
        df.dropna(subset=["vehicle_id"])
        .groupby("vehicle_id")["route_id"]
        .apply(_primary_route)
        .reset_index()
        .rename(columns={"route_id": "primary_route"})
    )
    result = result.merge(primary, on="vehicle_id", how="left")

    # Derived metric: average records per active day. Lets you spot vehicles
    # that ran few hours per day vs. all-day workhorses.
    result["records_per_active_day"] = (
        result["total_records"] / result["days_active"]
    ).round(2)

    result["first_seen"] = result["first_seen"].dt.strftime("%Y-%m-%d %H:%M:%S")
    result["last_seen"] = result["last_seen"].dt.strftime("%Y-%m-%d %H:%M:%S")

    return result.sort_values("total_records", ascending=False).reset_index(drop=True)


# ---------------------------------------------------------------------------
# METRIC 6: TELEMETRY QUALITY SUMMARY
# ---------------------------------------------------------------------------
def compute_telemetry_quality_summary(df: pd.DataFrame) -> pd.DataFrame:
    """
    Per-day data quality fingerprint.

    What it means:
        For each date:
        - row_count:                total rows ingested that day
        - null_pct_<column>:        percentage of null values per column
        - invalid_coord_pct:        rows with lat=0 OR lon=0 (GPS dropouts)
        - speed_populated_pct:      fraction of rows with non-zero speed
        - bearing_populated_pct:    fraction of rows with non-zero bearing

    Why transit agencies care:
        Data quality is rarely binary "good or bad" — it degrades gradually
        and inconsistently. This metric makes drift visible. The speed and
        bearing columns are known to be all-zero in the current feed; if
        they suddenly start populating (or stop), this report shows the day
        it changed.

    Engineering decision — coord validity check:
        GTFS-RT feeds sometimes emit (0.0, 0.0) when GPS is unavailable.
        Real Vancouver coordinates are roughly lat ≈ 49.x, lon ≈ -123.x,
        so a literal zero is almost always invalid. We flag those rows
        without dropping them — quality reporting should observe, not edit.
    """
    if "date" not in df.columns:
        logger.warning("No 'date' column — skipping telemetry quality summary")
        return pd.DataFrame()

    rows = []
    for date_str, day_df in df.groupby("date", dropna=True):
        n = len(day_df)
        if n == 0:
            continue

        record = {
            "date": date_str,
            "row_count": n,
        }

        # Null percentage per known column. Skip columns missing entirely
        # from this slice (shouldn't happen with consistent schema, but
        # defensive against schema drift).
        for col in EXPECTED_COLUMNS:
            if col in day_df.columns:
                record[f"null_pct_{col}"] = round(
                    float(day_df[col].isna().mean() * 100), 2
                )

        # Invalid coordinates: lat or lon equal to exactly 0.0.
        # (Vancouver is never at the equator or prime meridian.)
        if "latitude" in day_df.columns and "longitude" in day_df.columns:
            bad_coords = (day_df["latitude"] == 0.0) | (day_df["longitude"] == 0.0)
            record["invalid_coord_pct"] = round(float(bad_coords.mean() * 100), 2)

        # Speed / bearing population. These are 100% zeros in the current
        # feed; tracking the populated % flags any future change.
        if "speed" in day_df.columns:
            record["speed_populated_pct"] = round(
                float((day_df["speed"] != 0.0).mean() * 100), 2
            )
        if "bearing" in day_df.columns:
            record["bearing_populated_pct"] = round(
                float((day_df["bearing"] != 0.0).mean() * 100), 2
            )

        rows.append(record)

    if not rows:
        return pd.DataFrame()

    return pd.DataFrame(rows).sort_values("date").reset_index(drop=True)


# ---------------------------------------------------------------------------
# METRIC 7: ROUTE STABILITY SUMMARY
# ---------------------------------------------------------------------------
def compute_route_stability_summary(df: pd.DataFrame) -> pd.DataFrame:
    """
    How consistent is each route across days?

    What it means:
        For each route_id:
        - days_active:           distinct dates this route appeared
        - mean_daily_records:    average records per active day
        - std_daily_records:     standard deviation of daily records
        - cv_daily_records:      coefficient of variation (stddev / mean)
        - min_daily_records:     lowest single-day record count
        - max_daily_records:     highest single-day record count
        - is_persistent:         True if days_active >= PERSISTENT_DAYS_THRESHOLD

    Why transit agencies care:
        A core network is built on routes that run every day with stable
        load. This metric separates them from peak-only or weekend-only
        services. The coefficient of variation (CV) is the key field:
        - CV near 0:   extremely stable, same load every day
        - CV moderate: normal weekday/weekend variation
        - CV high:     erratic — possibly a route with service changes,
                       outages, or special events during the window

    Engineering decision — why CV instead of raw stddev:
        Raw stddev favours small-count routes. A high-traffic route with
        stddev of 200 might be very stable (CV ≈ 0.02) while a low-traffic
        route with stddev of 50 might be wildly unstable (CV ≈ 0.7).
        CV normalizes by mean and is comparable across routes of any size.
    """
    if "route_id" not in df.columns or "date" not in df.columns:
        logger.warning("Missing route_id or date — skipping route stability")
        return pd.DataFrame()

    # Daily record counts per route — the source of all stability stats.
    per_route_day = (
        df.groupby(["route_id", "date"], dropna=True)
        .size()
        .reset_index(name="daily_records")
    )

    result = (
        per_route_day.groupby("route_id")
        .agg(
            days_active=("date", "nunique"),
            mean_daily_records=("daily_records", "mean"),
            std_daily_records=("daily_records", "std"),
            min_daily_records=("daily_records", "min"),
            max_daily_records=("daily_records", "max"),
            total_records=("daily_records", "sum"),
        )
        .reset_index()
    )

    # CV calculation — guard against divide-by-zero on single-day routes.
    # std() returns NaN for groups of size 1, so we fillna(0) to keep
    # downstream filters from breaking.
    result["std_daily_records"] = result["std_daily_records"].fillna(0)
    result["cv_daily_records"] = (
        result["std_daily_records"] / result["mean_daily_records"]
    ).round(4)

    # Round means for readability without losing analytical precision.
    result["mean_daily_records"] = result["mean_daily_records"].round(1)
    result["std_daily_records"] = result["std_daily_records"].round(1)

    # Persistence flag — routes active most days form the operational backbone.
    result["is_persistent"] = result["days_active"] >= PERSISTENT_DAYS_THRESHOLD

    return result.sort_values(
        ["is_persistent", "total_records"], ascending=[False, False]
    ).reset_index(drop=True)


# ---------------------------------------------------------------------------
# METRIC 8: OUTLIER ROUTES
# ---------------------------------------------------------------------------
def compute_outlier_routes(stability: pd.DataFrame) -> pd.DataFrame:
    """
    Flag routes with unusually erratic day-over-day record counts.

    What it means:
        Routes whose coefficient of variation (CV) exceeds
        OUTLIER_CV_THRESHOLD. These are persistent routes (active most days)
        whose daily load swings far more than peer routes of similar size.

    Why transit agencies care:
        These are the routes worth investigating manually. Possible causes:
        - Mid-week service changes (added or cancelled runs).
        - Vehicle assignment changes (different fleet size each day).
        - Upstream feed glitches affecting one specific route.
        - Real-world disruptions: construction reroutes, special events,
          weather-related cancellations.

    Engineering decision — only flag PERSISTENT routes:
        A route active 1 of 5 days will have a meaningless CV. Filtering
        to persistent routes ensures the outlier list is actionable:
        every flagged route has enough data to make the comparison real.
    """
    if stability.empty:
        return pd.DataFrame()

    # Only persistent routes are eligible — non-persistent routes have too
    # few data points for CV to be meaningful.
    persistent = stability[stability["is_persistent"]].copy()
    if persistent.empty:
        return pd.DataFrame()

    outliers = persistent[persistent["cv_daily_records"] > OUTLIER_CV_THRESHOLD].copy()

    # Sort by CV descending — most erratic routes first.
    outliers = outliers.sort_values("cv_daily_records", ascending=False).reset_index(
        drop=True
    )

    # Add a human-readable reason column so the CSV is self-explanatory.
    outliers["flag_reason"] = (
        f"CV > {OUTLIER_CV_THRESHOLD}: daily record count varies "
        "more than expected for a persistent route"
    )

    return outliers


# ---------------------------------------------------------------------------
# OUTPUT
# ---------------------------------------------------------------------------
def save_csv(df: pd.DataFrame, output_dir: Path, name: str) -> Optional[Path]:
    """
    Save a metric dataframe to CSV under output_dir.

    Empty dataframes are skipped with a warning rather than written as
    headers-only files — a missing CSV is a clearer signal than an empty one.
    """
    if df.empty:
        logger.warning(f"  Skipping {name}.csv — empty result")
        return None

    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / f"{name}.csv"
    df.to_csv(path, index=False, encoding="utf-8")
    logger.info(f"  Saved {name}.csv ({len(df):,} rows)")
    return path


# ---------------------------------------------------------------------------
# CONSOLE SUMMARY
# ---------------------------------------------------------------------------
def print_run_summary(
    total_rows: int,
    files_processed: int,
    files_skipped: int,
    csvs_written: List[Path],
    output_dir: Path,
    start_date: str,
    end_date: str,
) -> None:
    """
    Compact end-of-run report.

    Prints the headline numbers you need to know your run worked:
    how much data was loaded, how many files contributed, which CSVs
    were written, and where to find them.
    """
    bar = "=" * 70
    print()
    print(bar)
    print("  WEEK 2 METRICS — RUN SUMMARY")
    print(bar)
    print(f"  Generated:           {datetime.now(timezone.utc).isoformat()}")
    print(f"  Date range:          {start_date} → {end_date}")
    print(f"  Total rows loaded:   {total_rows:,}")
    print(f"  Files processed:     {files_processed:,}")
    if files_skipped:
        print(f"  Files skipped:       {files_skipped:,} (unreadable)")
    print(f"  CSVs generated:      {len(csvs_written)}")
    for p in csvs_written:
        print(f"      - {p.name}")
    print(f"  Output directory:    {output_dir.resolve()}")
    print(bar)


# ---------------------------------------------------------------------------
# MAIN
# ---------------------------------------------------------------------------
def main() -> None:
    """
    Orchestrate the Week 2 metrics run end-to-end.

    Workflow:
        1. Parse CLI arguments.
        2. Discover parquet files in the requested date range.
        3. Load and combine into one dataframe.
        4. Enrich with date/hour/day-of-week columns.
        5. Compute every metric in sequence.
        6. Save all CSVs to reports/week2/.
        7. Print run summary.
    """
    parser = argparse.ArgumentParser(
        description="Generate Week 2 operational metrics for the GTFS-RT pipeline."
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
        "--start-date",
        type=str,
        default=DEFAULT_START_DATE,
        help=f"Start date (YYYY-MM-DD, default: {DEFAULT_START_DATE})",
    )
    parser.add_argument(
        "--end-date",
        type=str,
        default=DEFAULT_END_DATE,
        help=f"End date (YYYY-MM-DD, default: {DEFAULT_END_DATE})",
    )
    args = parser.parse_args()

    # --- Discover ---
    logger.info(f"Scanning {args.data_dir} for files between {args.start_date} and {args.end_date}")
    files = find_parquet_files_in_range(
        args.data_dir, args.start_date, args.end_date
    )
    if not files:
        logger.error("No parquet files found in the requested range")
        sys.exit(2)

    # --- Load ---
    logger.info("Loading parquet files into combined dataframe...")
    df, skipped = load_dataset(files)
    if df.empty:
        logger.error("Loaded dataset is empty — nothing to analyze")
        sys.exit(2)

    # --- Enrich ---
    logger.info("Enriching dataset with derived time columns...")
    df = enrich_with_time_columns(df)

    # --- Compute every metric ---
    logger.info("Computing daily summary...")
    daily = compute_daily_summary(df)

    logger.info("Computing route activity summary...")
    route_activity = compute_route_activity_summary(df)

    logger.info("Computing hourly network activity...")
    hourly_network = compute_hourly_network_activity(df)

    logger.info("Computing route × hour heatmap...")
    heatmap = compute_route_hour_heatmap(df)

    logger.info("Computing vehicle activity summary...")
    vehicle_activity = compute_vehicle_activity_summary(df)

    logger.info("Computing telemetry quality summary...")
    quality = compute_telemetry_quality_summary(df)

    logger.info("Computing route stability summary...")
    stability = compute_route_stability_summary(df)

    logger.info("Detecting outlier routes...")
    outliers = compute_outlier_routes(stability)

    # --- Save ---
    logger.info(f"Writing CSVs to {args.output_dir}/")
    csvs_written: List[Path] = []
    for df_out, name in [
        (daily,            "daily_summary"),
        (route_activity,   "route_activity_summary"),
        (hourly_network,   "hourly_network_activity"),
        (heatmap,          "route_hour_heatmap"),
        (vehicle_activity, "vehicle_activity_summary"),
        (quality,          "telemetry_quality_summary"),
        (stability,        "route_stability_summary"),
        (outliers,         "outlier_routes"),
    ]:
        path = save_csv(df_out, args.output_dir, name)
        if path is not None:
            csvs_written.append(path)

    # --- Final summary ---
    print_run_summary(
        total_rows=len(df),
        files_processed=len(files) - skipped,
        files_skipped=skipped,
        csvs_written=csvs_written,
        output_dir=args.output_dir,
        start_date=args.start_date,
        end_date=args.end_date,
    )

    logger.info("Week 2 metrics generation complete.")
    sys.exit(0)


if __name__ == "__main__":
    main()
