from pathlib import Path
import duckdb
import pandas as pd
import numpy as np

DB_PATH = Path("data/warehouse/transit.duckdb")
OUTPUT_DIR = Path("reports/sql_outputs")

OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

TARGET_ROUTE_SHORT_NAME = "099"


def haversine_distance_m(lat1, lon1, lat2, lon2):
    """
    Vectorized haversine distance in meters.
    """
    earth_radius_m = 6371000

    lat1_rad = np.radians(lat1)
    lon1_rad = np.radians(lon1)
    lat2_rad = np.radians(lat2)
    lon2_rad = np.radians(lon2)

    dlat = lat2_rad - lat1_rad
    dlon = lon2_rad - lon1_rad

    a = (
        np.sin(dlat / 2) ** 2
        + np.cos(lat1_rad) * np.cos(lat2_rad) * np.sin(dlon / 2) ** 2
    )

    c = 2 * np.arcsin(np.sqrt(a))

    return earth_radius_m * c


def project_points_to_shape(vehicle_df, shape_df):
    """
    First-version projection:
    For each vehicle GPS point, find the nearest GTFS shape point
    for the same shape_id.

    This is not yet full line-segment projection.
    It is a safe validation version before building the full engine.
    """
    projected_rows = []

    for shape_id, group in vehicle_df.groupby("shape_id"):
        shape_points = shape_df[shape_df["shape_id"] == shape_id].copy()

        if shape_points.empty:
            continue

        shape_lats = shape_points["shape_pt_lat"].to_numpy()
        shape_lons = shape_points["shape_pt_lon"].to_numpy()
        shape_distances = shape_points["shape_dist_traveled"].to_numpy()
        shape_sequences = shape_points["shape_pt_sequence"].to_numpy()

        vehicle_group = group.copy()

        nearest_shape_distances = []
        nearest_shape_sequences = []
        projection_errors_m = []

        for _, row in vehicle_group.iterrows():
            distances = haversine_distance_m(
                row["latitude"],
                row["longitude"],
                shape_lats,
                shape_lons,
            )

            nearest_idx = int(np.argmin(distances))

            nearest_shape_distances.append(shape_distances[nearest_idx])
            nearest_shape_sequences.append(shape_sequences[nearest_idx])
            projection_errors_m.append(distances[nearest_idx])

        vehicle_group["approx_shape_dist_traveled"] = nearest_shape_distances
        vehicle_group["nearest_shape_sequence"] = nearest_shape_sequences
        vehicle_group["projection_error_m"] = projection_errors_m

        projected_rows.append(vehicle_group)

    if not projected_rows:
        return pd.DataFrame()

    return pd.concat(projected_rows, ignore_index=True)


def main():
    if not DB_PATH.exists():
        raise FileNotFoundError(
            f"DuckDB database not found at {DB_PATH}. "
            "Run warehouse setup scripts first."
        )

    con = duckdb.connect(str(DB_PATH))

    try:
        print("Connected to DuckDB warehouse")

        # --------------------------------------------------
        # 1. Pick the route_id for route short name 099
        # --------------------------------------------------
        route_lookup = con.execute(f"""
        SELECT route_id, route_short_name, route_long_name
        FROM dim_routes
        WHERE route_short_name = '{TARGET_ROUTE_SHORT_NAME}'
        LIMIT 1;
        """).fetchdf()

        if route_lookup.empty:
            raise ValueError(f"Could not find route_short_name {TARGET_ROUTE_SHORT_NAME}")

        route_id = route_lookup.loc[0, "route_id"]
        route_name = route_lookup.loc[0, "route_long_name"]

        print(f"Target route: {TARGET_ROUTE_SHORT_NAME}")
        print(f"route_id: {route_id}")
        print(f"route_name: {route_name}")

        # --------------------------------------------------
        # 2. Pick latest eligible service date for this route
        # --------------------------------------------------
        date_lookup = con.execute(f"""
        SELECT service_date
        FROM route_eligibility_summary
        WHERE route_id = '{route_id}'
          AND eligibility_status = 'Eligible'
        ORDER BY service_date DESC
        LIMIT 1;
        """).fetchdf()

        if date_lookup.empty:
            raise ValueError(f"No eligible service date found for route_id {route_id}")

        service_date = date_lookup.loc[0, "service_date"]

        print(f"Using service_date: {service_date}")

        # --------------------------------------------------
        # 3. Load vehicle observations for this route/date
        # --------------------------------------------------
        vehicle_df = con.execute(f"""
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
            longitude
        FROM silver_vehicle_positions_enriched
        WHERE route_id = '{route_id}'
          AND service_date = DATE '{service_date}'
          AND shape_id IS NOT NULL
          AND latitude IS NOT NULL
          AND longitude IS NOT NULL
        ORDER BY collection_timestamp, vehicle_id;
        """).fetchdf()

        print(f"Loaded vehicle observations: {len(vehicle_df):,}")

        if vehicle_df.empty:
            raise ValueError("No vehicle observations found for selected route/date")

        # --------------------------------------------------
        # 4. Load only the shapes used by this route/date
        # --------------------------------------------------
        shape_ids = vehicle_df["shape_id"].dropna().unique().tolist()
        shape_ids_sql = ", ".join([f"'{sid}'" for sid in shape_ids])

        shape_df = con.execute(f"""
        SELECT
            shape_id,
            shape_pt_lat,
            shape_pt_lon,
            shape_pt_sequence,
            shape_dist_traveled
        FROM dim_shapes
        WHERE shape_id IN ({shape_ids_sql})
        ORDER BY shape_id, shape_pt_sequence;
        """).fetchdf()

        print(f"Loaded shape points: {len(shape_df):,}")
        print(f"Shapes used: {len(shape_ids):,}")

        # --------------------------------------------------
        # 5. Project vehicle GPS points to nearest shape point
        # --------------------------------------------------
        print("Projecting GPS points to GTFS shape points...")

        projected_df = project_points_to_shape(vehicle_df, shape_df)

        if projected_df.empty:
            raise ValueError("Projection returned no rows")

        print(f"Projected rows: {len(projected_df):,}")

        # --------------------------------------------------
        # 6. Store projected output in DuckDB
        # --------------------------------------------------
        con.register("projected_df", projected_df)

        con.execute("""
        CREATE OR REPLACE TABLE vehicle_positions_projected_099 AS
        SELECT *
        FROM projected_df;
        """)

        print("Created DuckDB table: vehicle_positions_projected_099")

        # --------------------------------------------------
        # 7. Export CSV sample
        # --------------------------------------------------
        con.execute("""
        COPY (
            SELECT *
            FROM vehicle_positions_projected_099
            ORDER BY collection_timestamp, vehicle_id
            LIMIT 5000
        )
        TO 'reports/sql_outputs/vehicle_positions_projected_099_sample.csv'
        WITH (HEADER, DELIMITER ',');
        """)

        print("Exported reports/sql_outputs/vehicle_positions_projected_099_sample.csv")

        # --------------------------------------------------
        # 8. Projection quality check
        # --------------------------------------------------
        print("\nProjection Quality Summary")

        print(con.execute("""
        SELECT
            COUNT(*) AS projected_rows,
            ROUND(AVG(projection_error_m), 2) AS avg_projection_error_m,
            ROUND(MEDIAN(projection_error_m), 2) AS median_projection_error_m,
            ROUND(MAX(projection_error_m), 2) AS max_projection_error_m
        FROM vehicle_positions_projected_099;
        """).fetchdf())

        print("\nDirection / Shape Summary")

        print(con.execute("""
        SELECT
            direction_id,
            shape_id,
            COUNT(*) AS observations,
            COUNT(DISTINCT vehicle_id) AS vehicles,
            ROUND(MIN(approx_shape_dist_traveled), 2) AS min_shape_dist,
            ROUND(MAX(approx_shape_dist_traveled), 2) AS max_shape_dist
        FROM vehicle_positions_projected_099
        GROUP BY direction_id, shape_id
        ORDER BY direction_id, observations DESC;
        """).fetchdf())

        print("\nShape projection prototype complete")

    finally:
        con.close()


if __name__ == "__main__":
    main()