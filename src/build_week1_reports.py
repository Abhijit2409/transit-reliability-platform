from pathlib import Path
import pandas as pd


# =========================================================
# CONFIG
# =========================================================

RAW_DIR = Path("data/raw/2026-05-18")
REPORTS_DIR = Path("reports")

REPORTS_DIR.mkdir(exist_ok=True)

print(f"Reading parquet files from: {RAW_DIR.resolve()}")


# =========================================================
# LOAD ALL PARQUET FILES
# =========================================================

files = sorted(RAW_DIR.glob("vehicle_positions_*.parquet"))

print(f"Found {len(files)} parquet files")

dfs = []

for file in files:
    try:
        df = pd.read_parquet(file)

        # Keep source filename for debugging
        df["source_file"] = file.name

        dfs.append(df)

    except Exception as e:
        print(f"Failed to read {file.name}: {e}")


# =========================================================
# COMBINE DATA
# =========================================================

data = pd.concat(dfs, ignore_index=True)

print(f"\nTotal rows loaded: {len(data):,}")

print("\nColumns:")
print(data.columns.tolist())


# =========================================================
# TIMESTAMP PROCESSING
# =========================================================

data["collection_timestamp"] = pd.to_datetime(
    data["collection_timestamp"],
    errors="coerce"
)

data["hour"] = data["collection_timestamp"].dt.hour


# =========================================================
# ROUTE VEHICLE COUNTS
# =========================================================

route_vehicle_counts = (
    data.groupby("route_id")
    .agg(
        total_records=("vehicle_id", "size"),
        unique_vehicles=("vehicle_id", "nunique"),
    )
    .reset_index()
)

route_vehicle_counts["records_per_vehicle"] = (
    route_vehicle_counts["total_records"]
    / route_vehicle_counts["unique_vehicles"]
).round(2)

route_vehicle_counts = route_vehicle_counts.sort_values(
    "total_records",
    ascending=False
)

route_vehicle_counts.to_csv(
    REPORTS_DIR / "route_vehicle_counts.csv",
    index=False
)

print("\nSaved: route_vehicle_counts.csv")


# =========================================================
# HOURLY DISTRIBUTION
# =========================================================

hourly_distribution = (
    data.groupby("hour")
    .size()
    .reset_index(name="record_count")
    .sort_values("hour")
)

hourly_distribution.to_csv(
    REPORTS_DIR / "hourly_distribution.csv",
    index=False
)

print("Saved: hourly_distribution.csv")


# =========================================================
# ROUTE COVERAGE SUMMARY
# =========================================================

route_coverage_summary = (
    data.groupby("route_id")
    .agg(
        first_seen=("collection_timestamp", "min"),
        last_seen=("collection_timestamp", "max"),
        active_hours=("hour", "nunique"),
        total_records=("vehicle_id", "size"),
        unique_vehicles=("vehicle_id", "nunique"),
    )
    .reset_index()
)

route_coverage_summary = route_coverage_summary.sort_values(
    "total_records",
    ascending=False
)

route_coverage_summary.to_csv(
    REPORTS_DIR / "route_coverage_summary.csv",
    index=False
)

print("Saved: route_coverage_summary.csv")


# =========================================================
# STATIONARY VEHICLE SUMMARY
# =========================================================
# NOTE:
# GTFS-RT speed data is unreliable (mostly zeros),
# so this is retained only as a data-quality artifact.

if "speed" in data.columns:

    stationary_vehicle_summary = (
        data.groupby("vehicle_id")
        .agg(
            total_records=("vehicle_id", "size"),
            zero_speed_records=("speed", lambda x: (x == 0).sum()),
            mean_speed=("speed", "mean"),
        )
        .reset_index()
    )

    stationary_vehicle_summary["zero_speed_share"] = (
        stationary_vehicle_summary["zero_speed_records"]
        / stationary_vehicle_summary["total_records"]
    ).round(4)

    stationary_vehicle_summary.to_csv(
        REPORTS_DIR / "stationary_vehicle_summary.csv",
        index=False
    )

    print("Saved: stationary_vehicle_summary.csv")


# =========================================================
# ROUTE SPEED SUMMARY
# =========================================================
# NOTE:
# Retained only to document feed limitations.

if "speed" in data.columns:

    route_avg_speed = (
        data.groupby("route_id")
        .agg(
            sample_count=("speed", "size"),
            mean_speed_mps=("speed", "mean"),
            median_speed_mps=("speed", "median"),
            max_speed_mps=("speed", "max"),
        )
        .reset_index()
    )

    route_avg_speed["mean_speed_kmh"] = (
        route_avg_speed["mean_speed_mps"] * 3.6
    )

    route_avg_speed["median_speed_kmh"] = (
        route_avg_speed["median_speed_mps"] * 3.6
    )

    route_avg_speed["max_speed_kmh"] = (
        route_avg_speed["max_speed_mps"] * 3.6
    )

    route_avg_speed.to_csv(
        REPORTS_DIR / "route_avg_speed.csv",
        index=False
    )

    print("Saved: route_avg_speed.csv")


# =========================================================
# FINAL SUMMARY
# =========================================================

print("\n===================================")
print("REPORT REBUILD COMPLETE")
print("===================================")

print(f"\nReports saved to:\n{REPORTS_DIR.resolve()}")

print("\nHourly distribution preview:")
print(hourly_distribution.head(24))
