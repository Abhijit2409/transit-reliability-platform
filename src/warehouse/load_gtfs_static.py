from pathlib import Path
import duckdb

DB_PATH = Path("data/warehouse/transit.duckdb")
GTFS_DIR = Path("data/gtfs")

GTFS_FILES = {
    "gtfs_routes": "routes.txt",
    "gtfs_trips": "trips.txt",
    "gtfs_stops": "stops.txt",
    "gtfs_shapes": "shapes.txt",
    "gtfs_stop_times": "stop_times.txt",
}


def main():
    if not DB_PATH.exists():
        raise FileNotFoundError(
            f"DuckDB database not found at {DB_PATH}. "
            "Run src/warehouse/setup_duckdb.py first."
        )

    if not GTFS_DIR.exists():
        raise FileNotFoundError(
            f"GTFS folder not found at {GTFS_DIR}. "
            "Expected files inside data/gtfs/"
        )

    con = duckdb.connect(str(DB_PATH))

    try:
        print("Connected to DuckDB")
        print(f"Loading GTFS files from: {GTFS_DIR}")

        for table_name, file_name in GTFS_FILES.items():
            file_path = GTFS_DIR / file_name

            if not file_path.exists():
                raise FileNotFoundError(f"Missing GTFS file: {file_path}")

            print(f"Loading {file_name} into {table_name}")

            con.execute(f"""
                CREATE OR REPLACE TABLE {table_name} AS
                SELECT *
                FROM read_csv_auto(
                    '{file_path.as_posix()}',
                    HEADER=TRUE,
                    ALL_VARCHAR=TRUE,
                    SAMPLE_SIZE=-1
                );
                """)

            row_count = con.execute(
                f"SELECT COUNT(*) FROM {table_name}"
            ).fetchone()[0]

            print(f"Loaded {table_name}: {row_count:,} rows")

        print("\nGTFS static load complete")

        print("\nGTFS Tables:")
        print(con.execute("""
        SELECT table_name
        FROM information_schema.tables
        WHERE table_name LIKE 'gtfs_%'
        ORDER BY table_name;
        """).fetchdf())

        print("\nJoin Coverage Check:")
        print(con.execute("""
        SELECT
            COUNT(*) AS silver_rows,
            COUNT(t.trip_id) AS matched_trip_rows,
            ROUND(100.0 * COUNT(t.trip_id) / COUNT(*), 2) AS trip_match_percentage
        FROM silver_vehicle_positions s
        LEFT JOIN gtfs_trips t
            ON CAST(s.trip_id AS VARCHAR) = CAST(t.trip_id AS VARCHAR);
        """).fetchdf())

    finally:
        con.close()


if __name__ == "__main__":
    main()