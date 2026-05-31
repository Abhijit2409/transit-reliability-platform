from pathlib import Path
import duckdb

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
        # 1. Shape distance summary
        # --------------------------------------------------
        con.execute("""
        CREATE OR REPLACE TABLE shape_distance_summary AS
        SELECT
            shape_id,
            COUNT(*) AS shape_points,
            MIN(shape_pt_sequence) AS first_sequence,
            MAX(shape_pt_sequence) AS last_sequence,
            MIN(shape_dist_traveled) AS min_shape_distance,
            MAX(shape_dist_traveled) AS max_shape_distance,
            ROUND(MAX(shape_dist_traveled), 2) AS shape_length_km
        FROM dim_shapes
        WHERE shape_dist_traveled IS NOT NULL
        GROUP BY shape_id
        ORDER BY shape_length_km DESC;
        """)

        print("Created shape_distance_summary")

        # --------------------------------------------------
        # 2. Route-shape mapping
        # --------------------------------------------------
        con.execute("""
        CREATE OR REPLACE TABLE route_shape_summary_clean AS
        SELECT
            t.route_id,
            r.route_short_name,
            r.route_long_name,
            t.direction_id,
            t.shape_id,
            COUNT(DISTINCT t.trip_id) AS scheduled_trip_count,
            s.shape_points,
            s.shape_length_km
        FROM dim_trips t
        LEFT JOIN dim_routes r
            ON t.route_id = r.route_id
        LEFT JOIN shape_distance_summary s
            ON t.shape_id = s.shape_id
        WHERE t.shape_id IS NOT NULL
        GROUP BY
            t.route_id,
            r.route_short_name,
            r.route_long_name,
            t.direction_id,
            t.shape_id,
            s.shape_points,
            s.shape_length_km
        ORDER BY
            t.route_id,
            t.direction_id,
            scheduled_trip_count DESC;
        """)

        print("Created route_shape_summary_clean")

        # --------------------------------------------------
        # 3. Eligible route-shapes
        # Only route-days already marked Eligible are included.
        # --------------------------------------------------
        con.execute("""
        CREATE OR REPLACE TABLE eligible_route_shapes AS
        WITH eligible_routes AS (
            SELECT
                route_id,
                COUNT(*) AS eligible_days
            FROM route_eligibility_summary
            WHERE eligibility_status = 'Eligible'
            GROUP BY route_id
        )

        SELECT
            rs.route_id,
            rs.route_short_name,
            rs.route_long_name,
            rs.direction_id,
            rs.shape_id,
            rs.scheduled_trip_count,
            rs.shape_points,
            rs.shape_length_km,
            er.eligible_days
        FROM route_shape_summary_clean rs
        INNER JOIN eligible_routes er
            ON rs.route_id = er.route_id
        WHERE rs.shape_id IS NOT NULL
          AND rs.shape_length_km IS NOT NULL
          AND rs.shape_points >= 2
        ORDER BY
            er.eligible_days DESC,
            rs.route_id,
            rs.direction_id,
            rs.scheduled_trip_count DESC;
        """)

        print("Created eligible_route_shapes")

        # --------------------------------------------------
        # 4. Export CSVs
        # --------------------------------------------------
        exports = {
            "shape_distance_summary": OUTPUT_DIR / "shape_distance_summary.csv",
            "route_shape_summary_clean": OUTPUT_DIR / "route_shape_summary_clean.csv",
            "eligible_route_shapes": OUTPUT_DIR / "eligible_route_shapes.csv",
        }

        for table_name, output_path in exports.items():
            con.execute(f"""
            COPY {table_name}
            TO '{output_path.as_posix()}'
            WITH (HEADER, DELIMITER ',');
            """)
            print(f"Exported {output_path}")

        # --------------------------------------------------
        # 5. Sanity checks
        # --------------------------------------------------
        print("\nWarehouse Shape Reference Summary:")

        print(con.execute("""
        SELECT
            COUNT(DISTINCT shape_id) AS total_shapes,
            ROUND(AVG(shape_length_km), 2) AS avg_shape_length_km,
            ROUND(MAX(shape_length_km), 2) AS max_shape_length_km
        FROM shape_distance_summary;
        """).fetchdf())

        print("\nEligible route-shape count:")

        print(con.execute("""
        SELECT
            COUNT(*) AS eligible_route_shape_rows,
            COUNT(DISTINCT route_id) AS eligible_routes,
            COUNT(DISTINCT shape_id) AS eligible_shapes
        FROM eligible_route_shapes;
        """).fetchdf())

        print("\nTop eligible route-shapes:")

        print(con.execute("""
        SELECT
            route_id,
            route_short_name,
            direction_id,
            shape_id,
            scheduled_trip_count,
            shape_length_km,
            eligible_days
        FROM eligible_route_shapes
        ORDER BY eligible_days DESC, scheduled_trip_count DESC
        LIMIT 20;
        """).fetchdf())

        print("\nShape reference warehouse build complete")

    finally:
        con.close()


if __name__ == "__main__":
    main()