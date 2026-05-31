from pathlib import Path
import duckdb

DB_PATH = Path("data/warehouse/transit.duckdb")
OUTPUT_DIR = Path("reports/sql_outputs")

OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


def main():
    con = duckdb.connect(str(DB_PATH))

    try:
        print("Connected to DuckDB")

        # --------------------------------------------------
        # Observed vehicle spacing/headway prototype for Route 099
        # --------------------------------------------------
        con.execute("""
        CREATE OR REPLACE TABLE observed_headways_099 AS
        WITH cleaned AS (
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
            FROM vehicle_positions_projected_099
            WHERE projection_error_m <= 100
              AND approx_shape_dist_traveled IS NOT NULL
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
            FROM cleaned
        )

        SELECT
            service_date,
            collection_timestamp,
            api_vehicle_timestamp,
            route_id,
            direction_id,
            shape_id,
            vehicle_id,
            trip_id,
            bus_rank,
            approx_shape_dist_traveled,
            next_vehicle_id,
            next_shape_dist,
            ROUND(next_shape_dist - approx_shape_dist_traveled, 4) AS distance_to_next_bus_km,
            latitude,
            longitude,
            next_latitude,
            next_longitude,
            projection_error_m
        FROM ranked
        WHERE next_vehicle_id IS NOT NULL
          AND next_shape_dist IS NOT NULL
          AND next_shape_dist >= approx_shape_dist_traveled
        ORDER BY collection_timestamp, direction_id, shape_id, bus_rank;
        """)

        print("Created observed_headways_099")

        con.execute("""
        COPY observed_headways_099
        TO 'reports/sql_outputs/observed_headways_099.csv'
        WITH (HEADER, DELIMITER ',');
        """)

        print("Exported reports/sql_outputs/observed_headways_099.csv")

        print("\nObserved Headway Summary")
        print(con.execute("""
        SELECT
            COUNT(*) AS spacing_rows,
            COUNT(DISTINCT collection_timestamp) AS timestamps,
            COUNT(DISTINCT vehicle_id) AS vehicles,
            ROUND(AVG(distance_to_next_bus_km), 3) AS avg_distance_to_next_bus_km,
            ROUND(MEDIAN(distance_to_next_bus_km), 3) AS median_distance_to_next_bus_km,
            ROUND(MIN(distance_to_next_bus_km), 3) AS min_distance_to_next_bus_km,
            ROUND(MAX(distance_to_next_bus_km), 3) AS max_distance_to_next_bus_km
        FROM observed_headways_099;
        """).fetchdf())

        print("\nPotential close-spacing events")
        print(con.execute("""
        SELECT
            COUNT(*) AS events_under_500m,
            COUNT(*) FILTER (WHERE distance_to_next_bus_km <= 0.25) AS events_under_250m,
            COUNT(*) FILTER (WHERE distance_to_next_bus_km <= 0.10) AS events_under_100m
        FROM observed_headways_099
        WHERE distance_to_next_bus_km <= 0.50;
        """).fetchdf())

        print("\nClosest vehicle pairs")
        print(con.execute("""
        SELECT
            collection_timestamp,
            direction_id,
            shape_id,
            vehicle_id,
            next_vehicle_id,
            approx_shape_dist_traveled,
            next_shape_dist,
            distance_to_next_bus_km
        FROM observed_headways_099
        ORDER BY distance_to_next_bus_km ASC
        LIMIT 25;
        """).fetchdf())

        print("\nObserved headway prototype complete")

    finally:
        con.close()


if __name__ == "__main__":
    main()