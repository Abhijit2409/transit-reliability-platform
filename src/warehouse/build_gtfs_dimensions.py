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
        # 1. Clean route dimension
        # --------------------------------------------------
        con.execute("""
        CREATE OR REPLACE TABLE dim_routes AS
        SELECT
            CAST(route_id AS VARCHAR) AS route_id,
            route_short_name,
            route_long_name,
            route_type
        FROM gtfs_routes;
        """)

        print("Created dim_routes")

        # --------------------------------------------------
        # 2. Clean trip dimension
        # --------------------------------------------------
        con.execute("""
        CREATE OR REPLACE TABLE dim_trips AS
        SELECT
            CAST(trip_id AS VARCHAR) AS trip_id,
            CAST(route_id AS VARCHAR) AS route_id,
            CAST(service_id AS VARCHAR) AS service_id,
            CAST(shape_id AS VARCHAR) AS shape_id,
            CAST(direction_id AS INTEGER) AS direction_id
        FROM gtfs_trips
        WHERE trip_id IS NOT NULL
          AND route_id IS NOT NULL;
        """)

        print("Created dim_trips")

        # --------------------------------------------------
        # 3. Clean stops dimension
        # --------------------------------------------------
        con.execute("""
        CREATE OR REPLACE TABLE dim_stops AS
        SELECT
            CAST(stop_id AS VARCHAR) AS stop_id,
            stop_name,
            CAST(stop_lat AS DOUBLE) AS stop_lat,
            CAST(stop_lon AS DOUBLE) AS stop_lon
        FROM gtfs_stops
        WHERE stop_id IS NOT NULL
          AND stop_lat IS NOT NULL
          AND stop_lon IS NOT NULL;
        """)

        print("Created dim_stops")

        # --------------------------------------------------
        # 4. Clean shapes dimension
        # --------------------------------------------------
        con.execute("""
        CREATE OR REPLACE TABLE dim_shapes AS
        SELECT
            CAST(shape_id AS VARCHAR) AS shape_id,
            CAST(shape_pt_lat AS DOUBLE) AS shape_pt_lat,
            CAST(shape_pt_lon AS DOUBLE) AS shape_pt_lon,
            CAST(shape_pt_sequence AS INTEGER) AS shape_pt_sequence,
            CAST(shape_dist_traveled AS DOUBLE) AS shape_dist_traveled
        FROM gtfs_shapes
        WHERE shape_id IS NOT NULL
          AND shape_pt_lat IS NOT NULL
          AND shape_pt_lon IS NOT NULL
          AND shape_pt_sequence IS NOT NULL;
        """)

        print("Created dim_shapes")

        # --------------------------------------------------
        # 5. Clean stop times dimension
        # --------------------------------------------------
        con.execute("""
        CREATE OR REPLACE TABLE dim_stop_times AS
        SELECT
            CAST(trip_id AS VARCHAR) AS trip_id,
            CAST(arrival_time AS VARCHAR) AS arrival_time,
            CAST(departure_time AS VARCHAR) AS departure_time,
            CAST(stop_id AS VARCHAR) AS stop_id,
            CAST(stop_sequence AS INTEGER) AS stop_sequence,
            CAST(shape_dist_traveled AS DOUBLE) AS shape_dist_traveled
        FROM gtfs_stop_times
        WHERE trip_id IS NOT NULL
          AND stop_id IS NOT NULL
          AND stop_sequence IS NOT NULL;
        """)

        print("Created dim_stop_times")

        # --------------------------------------------------
        # 6. Enrich Silver vehicle positions with trip metadata
        # --------------------------------------------------
        con.execute("""
        CREATE OR REPLACE TABLE silver_vehicle_positions_enriched AS
        SELECT
            s.*,
            t.direction_id,
            t.shape_id,
            t.service_id
        FROM silver_vehicle_positions s
        LEFT JOIN dim_trips t
            ON CAST(s.trip_id AS VARCHAR) = t.trip_id;
        """)

        print("Created silver_vehicle_positions_enriched")

        # --------------------------------------------------
        # 7. Join coverage checks
        # --------------------------------------------------
        print("\nJoin Coverage Checks")

        print(con.execute("""
        SELECT
            COUNT(*) AS silver_rows,
            COUNT(shape_id) AS matched_shape_rows,
            ROUND(100.0 * COUNT(shape_id) / COUNT(*), 2) AS shape_match_percentage,
            COUNT(direction_id) AS matched_direction_rows,
            ROUND(100.0 * COUNT(direction_id) / COUNT(*), 2) AS direction_match_percentage
        FROM silver_vehicle_positions_enriched;
        """).fetchdf())

        print("\nDimension Row Counts")

        print(con.execute("""
        SELECT 'dim_routes' AS table_name, COUNT(*) AS rows FROM dim_routes
        UNION ALL
        SELECT 'dim_trips', COUNT(*) FROM dim_trips
        UNION ALL
        SELECT 'dim_stops', COUNT(*) FROM dim_stops
        UNION ALL
        SELECT 'dim_shapes', COUNT(*) FROM dim_shapes
        UNION ALL
        SELECT 'dim_stop_times', COUNT(*) FROM dim_stop_times
        UNION ALL
        SELECT 'silver_vehicle_positions_enriched', COUNT(*) FROM silver_vehicle_positions_enriched;
        """).fetchdf())

        # --------------------------------------------------
        # 8. Export small summary CSVs
        # --------------------------------------------------
        con.execute("""
        COPY (
            SELECT
                route_id,
                route_short_name,
                route_long_name,
                route_type
            FROM dim_routes
            ORDER BY route_id
        )
        TO 'reports/sql_outputs/dim_routes_preview.csv'
        WITH (HEADER, DELIMITER ',');
        """)

        con.execute("""
        COPY (
            SELECT
                route_id,
                direction_id,
                shape_id,
                COUNT(*) AS trip_count
            FROM dim_trips
            GROUP BY route_id, direction_id, shape_id
            ORDER BY route_id, direction_id, trip_count DESC
        )
        TO 'reports/sql_outputs/route_shape_summary.csv'
        WITH (HEADER, DELIMITER ',');
        """)

        print("\nExported:")
        print("reports/sql_outputs/dim_routes_preview.csv")
        print("reports/sql_outputs/route_shape_summary.csv")

        print("\nGTFS dimension build complete")

    finally:
        con.close()


if __name__ == "__main__":
    main()