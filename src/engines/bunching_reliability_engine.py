from pathlib import Path
import duckdb

DB_PATH = Path("data/warehouse/transit.duckdb")
OUTPUT_DIR = Path("reports/sql_outputs")

OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

SOURCE_TABLE = "observed_headways_099_clean"


def main():
    con = duckdb.connect(str(DB_PATH))

    try:
        print("Connected to DuckDB")

        # --------------------------------------------------
        # 1. Create bunching events for Route 099
        # --------------------------------------------------
        con.execute(f"""
        CREATE OR REPLACE TABLE bunching_events_099 AS
        SELECT
            service_date,
            collection_timestamp,
            route_id,
            direction_id,
            shape_id,
            vehicle_id,
            next_vehicle_id,
            approx_shape_dist_traveled,
            next_shape_dist,
            distance_to_next_bus_km,

            CASE
                WHEN distance_to_next_bus_km <= 0.10 THEN 'Severe'
                WHEN distance_to_next_bus_km <= 0.25 THEN 'High'
                WHEN distance_to_next_bus_km <= 0.50 THEN 'Moderate'
                ELSE 'Normal'
            END AS bunching_severity,

            CASE
                WHEN EXTRACT(hour FROM collection_timestamp) BETWEEN 7 AND 9 THEN 'AM Peak'
                WHEN EXTRACT(hour FROM collection_timestamp) BETWEEN 15 AND 18 THEN 'PM Peak'
                ELSE 'Off Peak'
            END AS peak_period,

            FLOOR(approx_shape_dist_traveled / 0.5) * 0.5 AS route_segment_km

        FROM {SOURCE_TABLE}
        WHERE distance_to_next_bus_km <= 0.50;
        """)

        print("Created bunching_events_099")

        # --------------------------------------------------
        # 2. Route bunching summary
        # --------------------------------------------------
        con.execute("""
        CREATE OR REPLACE TABLE route_bunching_summary_099 AS
        SELECT
            service_date,
            route_id,

            COUNT(*) AS total_bunching_events,

            COUNT(*) FILTER (WHERE bunching_severity = 'Severe') AS severe_events,
            COUNT(*) FILTER (WHERE bunching_severity = 'High') AS high_events,
            COUNT(*) FILTER (WHERE bunching_severity = 'Moderate') AS moderate_events,

            COUNT(*) FILTER (WHERE peak_period = 'AM Peak') AS am_peak_events,
            COUNT(*) FILTER (WHERE peak_period = 'PM Peak') AS pm_peak_events,
            COUNT(*) FILTER (WHERE peak_period = 'Off Peak') AS off_peak_events,

            ROUND(AVG(distance_to_next_bus_km), 3) AS avg_bunching_gap_km,
            ROUND(MEDIAN(distance_to_next_bus_km), 3) AS median_bunching_gap_km,
            ROUND(MIN(distance_to_next_bus_km), 3) AS closest_gap_km,

            COUNT(DISTINCT vehicle_id) AS vehicles_involved,
            COUNT(DISTINCT route_segment_km) AS affected_segments

        FROM bunching_events_099
        GROUP BY service_date, route_id;
        """)

        print("Created route_bunching_summary_099")

        # --------------------------------------------------
        # 3. Segment hotspot summary
        # --------------------------------------------------
        con.execute("""
        CREATE OR REPLACE TABLE bunching_hotspots_099 AS
        SELECT
            route_id,
            direction_id,
            shape_id,
            route_segment_km,

            COUNT(*) AS bunching_events,
            COUNT(*) FILTER (WHERE bunching_severity = 'Severe') AS severe_events,
            COUNT(*) FILTER (WHERE bunching_severity = 'High') AS high_events,
            COUNT(*) FILTER (WHERE bunching_severity = 'Moderate') AS moderate_events,

            ROUND(AVG(distance_to_next_bus_km), 3) AS avg_gap_km,
            ROUND(MEDIAN(distance_to_next_bus_km), 3) AS median_gap_km,
            ROUND(MIN(distance_to_next_bus_km), 3) AS closest_gap_km,

            COUNT(DISTINCT vehicle_id) AS vehicles_involved,
            COUNT(DISTINCT collection_timestamp) AS active_timestamps

        FROM bunching_events_099
        GROUP BY
            route_id,
            direction_id,
            shape_id,
            route_segment_km
        ORDER BY bunching_events DESC;
        """)

        print("Created bunching_hotspots_099")

        # --------------------------------------------------
        # 4. Hourly bunching pattern
        # --------------------------------------------------
        con.execute("""
        CREATE OR REPLACE TABLE hourly_bunching_pattern_099 AS
        SELECT
            service_date,
            route_id,
            EXTRACT(hour FROM collection_timestamp) AS hour_of_day,
            peak_period,

            COUNT(*) AS bunching_events,
            COUNT(*) FILTER (WHERE bunching_severity = 'Severe') AS severe_events,
            COUNT(*) FILTER (WHERE bunching_severity = 'High') AS high_events,
            COUNT(*) FILTER (WHERE bunching_severity = 'Moderate') AS moderate_events,

            ROUND(AVG(distance_to_next_bus_km), 3) AS avg_gap_km,
            ROUND(MEDIAN(distance_to_next_bus_km), 3) AS median_gap_km

        FROM bunching_events_099
        GROUP BY
            service_date,
            route_id,
            hour_of_day,
            peak_period
        ORDER BY hour_of_day;
        """)

        print("Created hourly_bunching_pattern_099")

        # --------------------------------------------------
        # 5. Simple reliability score prototype
        # --------------------------------------------------
        con.execute("""
        CREATE OR REPLACE TABLE route_reliability_score_099 AS
        WITH spacing AS (
            SELECT
                service_date,
                route_id,
                COUNT(*) AS total_spacing_rows
            FROM observed_headways_099_clean
            GROUP BY service_date, route_id
        ),

        bunching AS (
            SELECT
                service_date,
                route_id,
                COUNT(*) AS total_bunching_events,
                COUNT(*) FILTER (WHERE bunching_severity = 'Severe') AS severe_events,
                COUNT(*) FILTER (WHERE peak_period IN ('AM Peak', 'PM Peak')) AS peak_events
            FROM bunching_events_099
            GROUP BY service_date, route_id
        ),

        scored AS (
            SELECT
                s.service_date,
                s.route_id,
                s.total_spacing_rows,

                COALESCE(b.total_bunching_events, 0) AS total_bunching_events,
                COALESCE(b.severe_events, 0) AS severe_events,
                COALESCE(b.peak_events, 0) AS peak_events,

                ROUND(
                    100.0 * COALESCE(b.total_bunching_events, 0)
                    / NULLIF(s.total_spacing_rows, 0),
                    2
                ) AS bunching_rate_pct,

                ROUND(
                    100.0 * COALESCE(b.severe_events, 0)
                    / NULLIF(s.total_spacing_rows, 0),
                    2
                ) AS severe_bunching_rate_pct,

                ROUND(
                    100.0 * COALESCE(b.peak_events, 0)
                    / NULLIF(COALESCE(b.total_bunching_events, 0), 0),
                    2
                ) AS peak_bunching_share_pct

            FROM spacing s
            LEFT JOIN bunching b
                ON s.service_date = b.service_date
               AND s.route_id = b.route_id
        )

        SELECT
            *,
            GREATEST(
                0,
                ROUND(
                    100
                    - (bunching_rate_pct * 1.2)
                    - (severe_bunching_rate_pct * 2.0)
                    - (COALESCE(peak_bunching_share_pct, 0) * 0.10),
                    2
                )
            ) AS reliability_score,

            CASE
                WHEN GREATEST(
                    0,
                    100
                    - (bunching_rate_pct * 1.2)
                    - (severe_bunching_rate_pct * 2.0)
                    - (COALESCE(peak_bunching_share_pct, 0) * 0.10)
                ) >= 85 THEN 'Reliable'

                WHEN GREATEST(
                    0,
                    100
                    - (bunching_rate_pct * 1.2)
                    - (severe_bunching_rate_pct * 2.0)
                    - (COALESCE(peak_bunching_share_pct, 0) * 0.10)
                ) >= 70 THEN 'Watch'

                WHEN GREATEST(
                    0,
                    100
                    - (bunching_rate_pct * 1.2)
                    - (severe_bunching_rate_pct * 2.0)
                    - (COALESCE(peak_bunching_share_pct, 0) * 0.10)
                ) >= 55 THEN 'Degraded'

                ELSE 'Critical'
            END AS reliability_band

        FROM scored;
        """)

        print("Created route_reliability_score_099")

        # --------------------------------------------------
        # 6. Export outputs
        # --------------------------------------------------
        exports = {
            "bunching_events_099": "bunching_events_099.csv",
            "route_bunching_summary_099": "route_bunching_summary_099.csv",
            "bunching_hotspots_099": "bunching_hotspots_099.csv",
            "hourly_bunching_pattern_099": "hourly_bunching_pattern_099.csv",
            "route_reliability_score_099": "route_reliability_score_099.csv",
        }

        for table_name, file_name in exports.items():
            output_path = OUTPUT_DIR / file_name
            con.execute(f"""
            COPY {table_name}
            TO '{output_path.as_posix()}'
            WITH (HEADER, DELIMITER ',');
            """)
            print(f"Exported {output_path}")

        # --------------------------------------------------
        # 7. Print summaries
        # --------------------------------------------------
        print("\nBunching Event Summary")
        print(con.execute("""
        SELECT
            bunching_severity,
            COUNT(*) AS events
        FROM bunching_events_099
        GROUP BY bunching_severity
        ORDER BY events DESC;
        """).fetchdf())

        print("\nPeak Period Summary")
        print(con.execute("""
        SELECT
            peak_period,
            COUNT(*) AS events
        FROM bunching_events_099
        GROUP BY peak_period
        ORDER BY events DESC;
        """).fetchdf())

        print("\nTop Hotspots")
        print(con.execute("""
        SELECT
            direction_id,
            shape_id,
            route_segment_km,
            bunching_events,
            severe_events,
            avg_gap_km,
            closest_gap_km
        FROM bunching_hotspots_099
        ORDER BY bunching_events DESC
        LIMIT 20;
        """).fetchdf())

        print("\nReliability Score")
        print(con.execute("""
        SELECT *
        FROM route_reliability_score_099;
        """).fetchdf())

        print("\nBunching + reliability prototype complete")

    finally:
        con.close()


if __name__ == "__main__":
    main()
    