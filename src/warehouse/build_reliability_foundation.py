from pathlib import Path
import duckdb

# --------------------------------------------------
# Paths
# --------------------------------------------------
DB_PATH = Path("data/warehouse/transit.duckdb")
OUTPUT_DIR = Path("reports/sql_outputs")

OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


def main():
    if not DB_PATH.exists():
        raise FileNotFoundError(
            f"DuckDB database not found at {DB_PATH}. "
            "Run src/warehouse/setup_duckdb.py first."
        )

    con = duckdb.connect(str(DB_PATH))

    try:
        print("Connected to DuckDB warehouse")

        # --------------------------------------------------
        # 1. Route Daily Summary
        # --------------------------------------------------
        con.execute("""
        CREATE OR REPLACE TABLE route_daily_summary AS
        SELECT
            service_date,
            route_id,
            COUNT(*) AS total_observations,
            COUNT(DISTINCT vehicle_id) AS unique_vehicles,
            COUNT(DISTINCT trip_id) AS unique_trips,
            COUNT(DISTINCT hour_of_day) AS observed_service_hours,
            MIN(collection_timestamp) AS first_seen,
            MAX(collection_timestamp) AS last_seen
        FROM silver_vehicle_positions
        WHERE route_id IS NOT NULL
        GROUP BY service_date, route_id
        ORDER BY service_date, route_id;
        """)

        print("Created route_daily_summary")

        # --------------------------------------------------
        # 2. Telemetry Quality Summary
        # --------------------------------------------------
        con.execute("""
        CREATE OR REPLACE TABLE telemetry_quality_summary AS
        SELECT
            service_date,
            route_id,
            COUNT(*) AS total_observations,
            COUNT(DISTINCT vehicle_id) AS unique_vehicles,
            COUNT(DISTINCT trip_id) AS unique_trips,

            SUM(CASE WHEN latitude IS NULL OR longitude IS NULL THEN 1 ELSE 0 END) AS missing_location_rows,
            SUM(CASE WHEN vehicle_id IS NULL THEN 1 ELSE 0 END) AS missing_vehicle_rows,
            SUM(CASE WHEN route_id IS NULL THEN 1 ELSE 0 END) AS missing_route_rows,

            ROUND(
                100.0 * SUM(
                    CASE
                        WHEN latitude IS NOT NULL
                         AND longitude IS NOT NULL
                         AND vehicle_id IS NOT NULL
                         AND route_id IS NOT NULL
                        THEN 1 ELSE 0
                    END
                ) / COUNT(*),
                2
            ) AS valid_row_percentage
        FROM silver_vehicle_positions
        GROUP BY service_date, route_id
        ORDER BY service_date, route_id;
        """)

        print("Created telemetry_quality_summary")

        # --------------------------------------------------
        # 3. Route Stability Summary
        # Important:
        # Missing hours are NOT treated as zero activity.
        # Stability is calculated only across observed service hours.
        # This is correct because not every route runs 24 hours.
        # --------------------------------------------------
        con.execute("""
        CREATE OR REPLACE TABLE route_stability_summary AS
        WITH hourly AS (
            SELECT
                service_date,
                route_id,
                hour_of_day,
                COUNT(*) AS hourly_observations,
                COUNT(DISTINCT vehicle_id) AS hourly_unique_vehicles,
                COUNT(DISTINCT trip_id) AS hourly_unique_trips
            FROM silver_vehicle_positions
            WHERE route_id IS NOT NULL
            GROUP BY service_date, route_id, hour_of_day
        ),

        route_stats AS (
            SELECT
                service_date,
                route_id,
                COUNT(*) AS observed_service_hours,
                SUM(hourly_observations) AS total_observations,
                AVG(hourly_observations) AS avg_hourly_observations,
                STDDEV_SAMP(hourly_observations) AS stddev_hourly_observations,
                AVG(hourly_unique_vehicles) AS avg_hourly_vehicles,
                AVG(hourly_unique_trips) AS avg_hourly_trips
            FROM hourly
            GROUP BY service_date, route_id
        )

        SELECT
            service_date,
            route_id,
            observed_service_hours,
            total_observations,
            ROUND(avg_hourly_observations, 2) AS avg_hourly_observations,
            ROUND(COALESCE(stddev_hourly_observations, 0), 2) AS stddev_hourly_observations,
            ROUND(avg_hourly_vehicles, 2) AS avg_hourly_vehicles,
            ROUND(avg_hourly_trips, 2) AS avg_hourly_trips,

            ROUND(
                COALESCE(stddev_hourly_observations, 0)
                / NULLIF(avg_hourly_observations, 0),
                4
            ) AS observation_cv,

            CASE
                WHEN observed_service_hours >= 18 THEN 'High Coverage'
                WHEN observed_service_hours >= 10 THEN 'Medium Coverage'
                ELSE 'Low Coverage'
            END AS coverage_band,

            CASE
                WHEN COALESCE(stddev_hourly_observations, 0)
                     / NULLIF(avg_hourly_observations, 0) <= 0.50
                    THEN 'Stable'

                WHEN COALESCE(stddev_hourly_observations, 0)
                     / NULLIF(avg_hourly_observations, 0) <= 1.00
                    THEN 'Moderate'

                ELSE 'Volatile'
            END AS stability_band

        FROM route_stats
        ORDER BY service_date, route_id;
        """)

        print("Created route_stability_summary")

        # --------------------------------------------------
        # Export CSV outputs
        # --------------------------------------------------
        exports = {
            "route_daily_summary": OUTPUT_DIR / "route_daily_summary.csv",
            "telemetry_quality_summary": OUTPUT_DIR / "telemetry_quality_summary.csv",
            "route_stability_summary": OUTPUT_DIR / "route_stability_summary.csv",
        }

        for table_name, output_path in exports.items():
            con.execute(f"""
            COPY {table_name}
            TO '{output_path.as_posix()}'
            WITH (HEADER, DELIMITER ',');
            """)
            print(f"Exported {output_path}")

        # --------------------------------------------------
        # Sanity checks
        # --------------------------------------------------
        print("\nSummary:")

        for table_name in exports:
            row_count = con.execute(
                f"SELECT COUNT(*) FROM {table_name}"
            ).fetchone()[0]

            print(f"{table_name}: {row_count:,} rows")

        print("\nReliability foundation build complete")

    finally:
        con.close()


if __name__ == "__main__":
    main()