"""
Observed headway engine — Route 099 clean prototype.

Creates:
- DuckDB table: observed_headways_099_clean
- CSV: reports/sql_outputs/observed_headways_099_clean.csv

Purpose:
Convert projected vehicle positions into clean vehicle spacing/headway rows,
while removing terminal/layover false positives.
"""

from pathlib import Path
import duckdb

DB_PATH = Path("data/warehouse/transit.duckdb")
OUTPUT_DIR = Path("reports/sql_outputs")
OUTPUT_CSV = OUTPUT_DIR / "observed_headways_099_clean.csv"

SOURCE_TABLE = "vehicle_positions_projected_099"
SHAPE_LENGTH_TABLE = "shape_distance_summary"
OUTPUT_TABLE = "observed_headways_099_clean"

MAX_PROJECTION_ERROR_M = 100
TERMINAL_BUFFER_KM = 0.75
KEEP_ZERO_GAP_PAIRS = False

OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


def main():
    con = duckdb.connect(str(DB_PATH))

    try:
        print("Connected to DuckDB")

        raw_rows = con.execute(
            f"SELECT COUNT(*) FROM {SOURCE_TABLE}"
        ).fetchone()[0]

        rows_after_proj = con.execute(f"""
            SELECT COUNT(*)
            FROM {SOURCE_TABLE}
            WHERE projection_error_m <= {MAX_PROJECTION_ERROR_M}
              AND approx_shape_dist_traveled IS NOT NULL
        """).fetchone()[0]

        removed_by_projection = raw_rows - rows_after_proj

        con.execute(f"""
        CREATE OR REPLACE TABLE {OUTPUT_TABLE} AS
        WITH proj_clean AS (
            SELECT
                service_date,
                collection_timestamp,
                api_vehicle_timestamp,
                vehicle_id,
                route_id,
                trip_id,
                direction_id,
                shape_id,
                latitude,
                longitude,
                approx_shape_dist_traveled,
                projection_error_m
            FROM {SOURCE_TABLE}
            WHERE projection_error_m <= {MAX_PROJECTION_ERROR_M}
              AND approx_shape_dist_traveled IS NOT NULL
        ),

        with_length AS (
            SELECT
                p.*,
                s.shape_length_km AS shape_length_km
            FROM proj_clean p
            LEFT JOIN {SHAPE_LENGTH_TABLE} s
              ON p.shape_id = s.shape_id
        ),

        in_service AS (
            SELECT *
            FROM with_length
            WHERE approx_shape_dist_traveled > {TERMINAL_BUFFER_KM}
              AND (
                    shape_length_km IS NULL
                    OR approx_shape_dist_traveled
                       < (shape_length_km - {TERMINAL_BUFFER_KM})
                  )
        ),

        ranked AS (
            SELECT
                *,
                ROW_NUMBER() OVER (
                    PARTITION BY collection_timestamp, direction_id, shape_id
                    ORDER BY approx_shape_dist_traveled
                ) AS bus_rank,

                LEAD(vehicle_id) OVER (
                    PARTITION BY collection_timestamp, direction_id, shape_id
                    ORDER BY approx_shape_dist_traveled
                ) AS next_vehicle_id,

                LEAD(approx_shape_dist_traveled) OVER (
                    PARTITION BY collection_timestamp, direction_id, shape_id
                    ORDER BY approx_shape_dist_traveled
                ) AS next_shape_dist,

                LEAD(latitude) OVER (
                    PARTITION BY collection_timestamp, direction_id, shape_id
                    ORDER BY approx_shape_dist_traveled
                ) AS next_latitude,

                LEAD(longitude) OVER (
                    PARTITION BY collection_timestamp, direction_id, shape_id
                    ORDER BY approx_shape_dist_traveled
                ) AS next_longitude
            FROM in_service
        )

        SELECT
            service_date,
            collection_timestamp,
            api_vehicle_timestamp,
            route_id,
            direction_id,
            shape_id,
            shape_length_km,
            vehicle_id,
            trip_id,
            bus_rank,
            approx_shape_dist_traveled,
            next_vehicle_id,
            next_shape_dist,
            ROUND(next_shape_dist - approx_shape_dist_traveled, 4)
                AS distance_to_next_bus_km,
            latitude,
            longitude,
            next_latitude,
            next_longitude,
            projection_error_m
        FROM ranked
        WHERE next_vehicle_id IS NOT NULL
          AND next_shape_dist IS NOT NULL
          AND next_shape_dist >= approx_shape_dist_traveled
          AND next_vehicle_id <> vehicle_id
          AND (
                {str(KEEP_ZERO_GAP_PAIRS).lower()}
                OR (next_shape_dist - approx_shape_dist_traveled) > 0
              )
        ORDER BY collection_timestamp, direction_id, shape_id, bus_rank;
        """)

        clean_rows = con.execute(
            f"SELECT COUNT(*) FROM {OUTPUT_TABLE}"
        ).fetchone()[0]

        in_service_points = con.execute(f"""
            WITH proj_clean AS (
                SELECT shape_id, approx_shape_dist_traveled
                FROM {SOURCE_TABLE}
                WHERE projection_error_m <= {MAX_PROJECTION_ERROR_M}
                  AND approx_shape_dist_traveled IS NOT NULL
            )
            SELECT COUNT(*)
            FROM proj_clean p
            LEFT JOIN {SHAPE_LENGTH_TABLE} s
              ON p.shape_id = s.shape_id
            WHERE p.approx_shape_dist_traveled > {TERMINAL_BUFFER_KM}
              AND (
                    s.shape_length_km IS NULL
                    OR p.approx_shape_dist_traveled
                       < (s.shape_length_km - {TERMINAL_BUFFER_KM})
                  )
        """).fetchone()[0]

        removed_by_terminal = rows_after_proj - in_service_points

        con.execute(f"""
            COPY {OUTPUT_TABLE}
            TO '{OUTPUT_CSV.as_posix()}'
            WITH (HEADER, DELIMITER ',');
        """)

        print("\n=== ROW COUNT FUNNEL ===")
        print(f"raw source rows                 : {raw_rows:,}")
        print(f"removed by projection filter    : {removed_by_projection:,}")
        print(f"after projection filter         : {rows_after_proj:,}")
        print(f"removed by terminal buffer      : {removed_by_terminal:,}")
        print(f"final clean spacing rows        : {clean_rows:,}")
        print(f"exported                        : {OUTPUT_CSV}")

        print("\n=== Distance Summary ===")
        print(con.execute(f"""
            SELECT
                COUNT(*) AS spacing_rows,
                COUNT(DISTINCT collection_timestamp) AS timestamps,
                COUNT(DISTINCT vehicle_id) AS vehicles,
                ROUND(AVG(distance_to_next_bus_km), 3) AS avg_km,
                ROUND(MEDIAN(distance_to_next_bus_km), 3) AS median_km,
                ROUND(MIN(distance_to_next_bus_km), 3) AS min_km,
                ROUND(MAX(distance_to_next_bus_km), 3) AS max_km
            FROM {OUTPUT_TABLE};
        """).fetchdf())

        print("\n=== Close-spacing counts after cleaning ===")
        print(con.execute(f"""
            SELECT
                COUNT(*) FILTER (WHERE distance_to_next_bus_km <= 0.50)
                    AS events_under_500m,
                COUNT(*) FILTER (WHERE distance_to_next_bus_km <= 0.25)
                    AS events_under_250m,
                COUNT(*) FILTER (WHERE distance_to_next_bus_km <= 0.10)
                    AS events_under_100m
            FROM {OUTPUT_TABLE};
        """).fetchdf())

        print("\n=== Closest clean vehicle pairs ===")
        print(con.execute(f"""
            SELECT
                collection_timestamp,
                direction_id,
                shape_id,
                vehicle_id,
                next_vehicle_id,
                approx_shape_dist_traveled,
                next_shape_dist,
                distance_to_next_bus_km
            FROM {OUTPUT_TABLE}
            ORDER BY distance_to_next_bus_km ASC
            LIMIT 25;
        """).fetchdf())

        print("\n=== Direction / Shape Summary ===")
        print(con.execute(f"""
            SELECT
                direction_id,
                shape_id,
                ANY_VALUE(shape_length_km) AS shape_length_km,
                COUNT(*) AS spacing_rows,
                COUNT(DISTINCT vehicle_id) AS vehicles,
                ROUND(MEDIAN(distance_to_next_bus_km), 3) AS median_gap_km,
                COUNT(*) FILTER (WHERE distance_to_next_bus_km <= 0.25)
                    AS events_under_250m
            FROM {OUTPUT_TABLE}
            GROUP BY direction_id, shape_id
            ORDER BY direction_id, spacing_rows DESC;
        """).fetchdf())

        print("\nObserved headway clean layer complete")

    finally:
        con.close()


if __name__ == "__main__":
    main()