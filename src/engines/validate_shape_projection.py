from pathlib import Path
import duckdb

DB_PATH = Path("data/warehouse/transit.duckdb")
OUTPUT_DIR = Path("reports/sql_outputs")

OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


def main():
    con = duckdb.connect(str(DB_PATH))

    try:
        print("Connected to DuckDB")

        print("\nProjection Error Distribution")
        print(con.execute("""
        SELECT
            COUNT(*) AS total_rows,
            ROUND(AVG(projection_error_m), 2) AS avg_error_m,
            ROUND(MEDIAN(projection_error_m), 2) AS median_error_m,
            ROUND(MAX(projection_error_m), 2) AS max_error_m,

            SUM(CASE WHEN projection_error_m <= 50 THEN 1 ELSE 0 END) AS rows_under_50m,
            SUM(CASE WHEN projection_error_m <= 100 THEN 1 ELSE 0 END) AS rows_under_100m,
            SUM(CASE WHEN projection_error_m <= 250 THEN 1 ELSE 0 END) AS rows_under_250m,
            SUM(CASE WHEN projection_error_m > 500 THEN 1 ELSE 0 END) AS rows_over_500m,

            ROUND(100.0 * SUM(CASE WHEN projection_error_m <= 100 THEN 1 ELSE 0 END) / COUNT(*), 2) AS pct_under_100m,
            ROUND(100.0 * SUM(CASE WHEN projection_error_m > 500 THEN 1 ELSE 0 END) / COUNT(*), 2) AS pct_over_500m
        FROM vehicle_positions_projected_099;
        """).fetchdf())

        print("\nProjection Error By Shape")
        print(con.execute("""
        SELECT
            direction_id,
            shape_id,
            COUNT(*) AS rows,
            COUNT(DISTINCT vehicle_id) AS vehicles,
            ROUND(AVG(projection_error_m), 2) AS avg_error_m,
            ROUND(MEDIAN(projection_error_m), 2) AS median_error_m,
            ROUND(MAX(projection_error_m), 2) AS max_error_m,
            ROUND(100.0 * SUM(CASE WHEN projection_error_m <= 100 THEN 1 ELSE 0 END) / COUNT(*), 2) AS pct_under_100m
        FROM vehicle_positions_projected_099
        GROUP BY direction_id, shape_id
        ORDER BY avg_error_m DESC;
        """).fetchdf())

        print("\nWorst Projected Points")
        print(con.execute("""
        SELECT
            collection_timestamp,
            vehicle_id,
            route_id,
            trip_id,
            direction_id,
            shape_id,
            latitude,
            longitude,
            approx_shape_dist_traveled,
            projection_error_m
        FROM vehicle_positions_projected_099
        ORDER BY projection_error_m DESC
        LIMIT 25;
        """).fetchdf())

        print("\nProjection Quality Decision")
        result = con.execute("""
        SELECT
            ROUND(100.0 * SUM(CASE WHEN projection_error_m <= 100 THEN 1 ELSE 0 END) / COUNT(*), 2) AS pct_under_100m,
            ROUND(100.0 * SUM(CASE WHEN projection_error_m > 500 THEN 1 ELSE 0 END) / COUNT(*), 2) AS pct_over_500m
        FROM vehicle_positions_projected_099;
        """).fetchone()

        pct_under_100m = result[0]
        pct_over_500m = result[1]

        print(f"Percent under 100m: {pct_under_100m}%")
        print(f"Percent over 500m: {pct_over_500m}%")

        if pct_under_100m >= 90 and pct_over_500m <= 2:
            print("Decision: GOOD ENOUGH for first headway prototype")
        elif pct_under_100m >= 80 and pct_over_500m <= 5:
            print("Decision: USABLE, but filter high-error points before headway")
        else:
            print("Decision: NEEDS IMPROVEMENT before headway")

        con.execute("""
        COPY (
            SELECT *
            FROM vehicle_positions_projected_099
            WHERE projection_error_m > 250
            ORDER BY projection_error_m DESC
        )
        TO 'reports/sql_outputs/projection_high_error_points_099.csv'
        WITH (HEADER, DELIMITER ',');
        """)

        print("\nExported reports/sql_outputs/projection_high_error_points_099.csv")

    finally:
        con.close()


if __name__ == "__main__":
    main()