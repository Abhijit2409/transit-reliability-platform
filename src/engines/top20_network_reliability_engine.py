from pathlib import Path
import duckdb
import pandas as pd
import numpy as np

DB_PATH = Path("data/warehouse/transit.duckdb")
OUTPUT_DIR = Path("reports/sql_outputs")
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

TOP20_ROUTES = [
    "099", "049", "R4", "016", "321",
    "503", "025", "019", "014", "335",
    "R5", "007", "410", "009", "250",
    "100", "502", "160", "020", "004",
]

MAX_PROJECTION_ERROR_M = 100
TERMINAL_BUFFER_KM = 0.75

BUNCHING_MODERATE_KM = 0.50
BUNCHING_HIGH_KM = 0.25
BUNCHING_SEVERE_KM = 0.10


def classify_route_type(route_short_name: str) -> str:
    if route_short_name.startswith("R"):
        return "RapidBus"
    if route_short_name == "099":
        return "B-Line"
    if route_short_name in ["503", "502", "250", "160"]:
        return "Express / Regional"
    return "Regular Bus"


def haversine_distance_m(lat1, lon1, lat2, lon2):
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
    con = duckdb.connect(str(DB_PATH))
    all_projected = []

    try:
        print("Connected to DuckDB")
        print("Starting Top 20 network reliability engine")

        for route_short_name in TOP20_ROUTES:
            print("\n" + "-" * 70)
            print(f"Processing route {route_short_name}")

            route_lookup = con.execute(f"""
                SELECT route_id, route_short_name, route_long_name
                FROM dim_routes
                WHERE route_short_name = '{route_short_name}'
                LIMIT 1;
            """).fetchdf()

            if route_lookup.empty:
                print(f"SKIPPED: route_short_name {route_short_name} not found")
                continue

            route_id = route_lookup.loc[0, "route_id"]
            route_long_name = route_lookup.loc[0, "route_long_name"]
            route_type = classify_route_type(route_short_name)

            date_lookup = con.execute(f"""
                SELECT service_date
                FROM route_eligibility_summary
                WHERE route_id = '{route_id}'
                  AND eligibility_status = 'Eligible'
                ORDER BY service_date DESC
                LIMIT 1;
            """).fetchdf()

            if date_lookup.empty:
                print(f"SKIPPED: route {route_short_name} has no eligible service date")
                continue

            service_date = date_lookup.loc[0, "service_date"]

            print(f"route_id: {route_id}")
            print(f"route_name: {route_long_name}")
            print(f"route_type: {route_type}")
            print(f"service_date: {service_date}")

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

            if vehicle_df.empty:
                print("SKIPPED: no vehicle observations")
                continue

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

            print(f"vehicle rows: {len(vehicle_df):,}")
            print(f"shape points: {len(shape_df):,}")
            print(f"shape count: {len(shape_ids):,}")

            projected_df = project_points_to_shape(vehicle_df, shape_df)

            if projected_df.empty:
                print("SKIPPED: projection returned no rows")
                continue

            projected_df["route_short_name"] = route_short_name
            projected_df["route_long_name"] = route_long_name
            projected_df["route_type"] = route_type

            all_projected.append(projected_df)

            print(f"projected rows: {len(projected_df):,}")

        if not all_projected:
            raise ValueError("No routes were projected. Check route list and eligibility.")

        final_projected_df = pd.concat(all_projected, ignore_index=True)
        con.register("final_projected_df", final_projected_df)

        con.execute("""
            CREATE OR REPLACE TABLE top20_vehicle_positions_projected AS
            SELECT *
            FROM final_projected_df;
        """)

        print("\nCreated table: top20_vehicle_positions_projected")

        con.execute(f"""
            CREATE OR REPLACE TABLE top20_observed_headways_clean AS
            WITH proj_clean AS (
                SELECT
                    p.*,
                    s.shape_length_km
                FROM top20_vehicle_positions_projected p
                LEFT JOIN shape_distance_summary s
                    ON p.shape_id = s.shape_id
                WHERE p.projection_error_m <= {MAX_PROJECTION_ERROR_M}
                  AND p.approx_shape_dist_traveled IS NOT NULL
            ),

            in_service AS (
                SELECT *
                FROM proj_clean
                WHERE approx_shape_dist_traveled > {TERMINAL_BUFFER_KM}
                  AND (
                        shape_length_km IS NULL
                        OR approx_shape_dist_traveled < (shape_length_km - {TERMINAL_BUFFER_KM})
                      )
            ),

            ranked AS (
                SELECT
                    *,
                    ROW_NUMBER() OVER (
                        PARTITION BY route_id, collection_timestamp, direction_id, shape_id
                        ORDER BY approx_shape_dist_traveled
                    ) AS bus_rank,

                    LEAD(vehicle_id) OVER (
                        PARTITION BY route_id, collection_timestamp, direction_id, shape_id
                        ORDER BY approx_shape_dist_traveled
                    ) AS next_vehicle_id,

                    LEAD(approx_shape_dist_traveled) OVER (
                        PARTITION BY route_id, collection_timestamp, direction_id, shape_id
                        ORDER BY approx_shape_dist_traveled
                    ) AS next_shape_dist
                FROM in_service
            )

            SELECT
                service_date,
                collection_timestamp,
                api_vehicle_timestamp,
                route_id,
                route_short_name,
                route_long_name,
                route_type,
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
                projection_error_m
            FROM ranked
            WHERE next_vehicle_id IS NOT NULL
              AND next_shape_dist IS NOT NULL
              AND next_shape_dist >= approx_shape_dist_traveled
              AND next_vehicle_id <> vehicle_id
              AND (next_shape_dist - approx_shape_dist_traveled) > 0
            ORDER BY route_short_name, collection_timestamp, direction_id, shape_id, bus_rank;
        """)

        print("Created table: top20_observed_headways_clean")

        con.execute(f"""
            CREATE OR REPLACE TABLE top20_bunching_events AS
            SELECT
                service_date,
                collection_timestamp,
                route_id,
                route_short_name,
                route_long_name,
                route_type,
                direction_id,
                shape_id,
                vehicle_id,
                next_vehicle_id,
                approx_shape_dist_traveled,
                next_shape_dist,
                distance_to_next_bus_km,

                CASE
                    WHEN distance_to_next_bus_km <= {BUNCHING_SEVERE_KM} THEN 'Severe'
                    WHEN distance_to_next_bus_km <= {BUNCHING_HIGH_KM} THEN 'High'
                    WHEN distance_to_next_bus_km <= {BUNCHING_MODERATE_KM} THEN 'Moderate'
                    ELSE 'Normal'
                END AS bunching_severity,

                CASE
                    WHEN EXTRACT(hour FROM collection_timestamp) BETWEEN 7 AND 9 THEN 'AM Peak'
                    WHEN EXTRACT(hour FROM collection_timestamp) BETWEEN 15 AND 18 THEN 'PM Peak'
                    ELSE 'Off Peak'
                END AS peak_period,

                FLOOR(approx_shape_dist_traveled / 0.5) * 0.5 AS route_segment_km

            FROM top20_observed_headways_clean
            WHERE distance_to_next_bus_km <= {BUNCHING_MODERATE_KM};
        """)

        print("Created table: top20_bunching_events")

        con.execute("""
            CREATE OR REPLACE TABLE top20_bunching_hotspots AS
            SELECT
                route_id,
                route_short_name,
                route_long_name,
                route_type,
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

            FROM top20_bunching_events
            GROUP BY
                route_id,
                route_short_name,
                route_long_name,
                route_type,
                direction_id,
                shape_id,
                route_segment_km
            ORDER BY bunching_events DESC;
        """)

        print("Created table: top20_bunching_hotspots")

        # --------------------------------------------------
        # NEW: Human-readable stop context for hotspot segments
        # --------------------------------------------------
        con.execute("""
            CREATE OR REPLACE TABLE top20_bunching_hotspots_with_stops AS
            WITH stop_reference AS (
                SELECT
                    st.trip_id,
                    t.route_id,
                    t.direction_id,
                    t.shape_id,
                    st.stop_id,
                    s.stop_name,
                    CAST(st.stop_sequence AS INTEGER) AS stop_sequence,
                    CAST(st.shape_dist_traveled AS DOUBLE) AS stop_dist_km
                FROM dim_stop_times st
                INNER JOIN dim_trips t
                    ON st.trip_id = t.trip_id
                LEFT JOIN dim_stops s
                    ON st.stop_id = s.stop_id
                WHERE st.shape_dist_traveled IS NOT NULL
            ),

            unique_stops AS (
                SELECT
                    route_id,
                    direction_id,
                    shape_id,
                    stop_id,
                    stop_name,
                    MIN(stop_sequence) AS stop_sequence,
                    MIN(stop_dist_km) AS stop_dist_km
                FROM stop_reference
                GROUP BY
                    route_id,
                    direction_id,
                    shape_id,
                    stop_id,
                    stop_name
            ),

            hotspots AS (
                SELECT
                    *,
                    route_segment_km AS segment_start_km,
                    route_segment_km + 0.5 AS segment_end_km
                FROM top20_bunching_hotspots
            ),

            stop_before AS (
                SELECT
                    h.route_id,
                    h.direction_id,
                    h.shape_id,
                    h.route_segment_km,
                    u.stop_id AS stop_before_id,
                    u.stop_name AS stop_before_name,
                    u.stop_dist_km AS stop_before_dist_km,
                    ROW_NUMBER() OVER (
                        PARTITION BY h.route_id, h.direction_id, h.shape_id, h.route_segment_km
                        ORDER BY u.stop_dist_km DESC
                    ) AS rn
                FROM hotspots h
                LEFT JOIN unique_stops u
                    ON h.route_id = u.route_id
                   AND h.direction_id = u.direction_id
                   AND h.shape_id = u.shape_id
                   AND u.stop_dist_km <= h.segment_start_km
            ),

            stop_after AS (
                SELECT
                    h.route_id,
                    h.direction_id,
                    h.shape_id,
                    h.route_segment_km,
                    u.stop_id AS stop_after_id,
                    u.stop_name AS stop_after_name,
                    u.stop_dist_km AS stop_after_dist_km,
                    ROW_NUMBER() OVER (
                        PARTITION BY h.route_id, h.direction_id, h.shape_id, h.route_segment_km
                        ORDER BY u.stop_dist_km ASC
                    ) AS rn
                FROM hotspots h
                LEFT JOIN unique_stops u
                    ON h.route_id = u.route_id
                   AND h.direction_id = u.direction_id
                   AND h.shape_id = u.shape_id
                   AND u.stop_dist_km >= h.segment_end_km
            )

            SELECT
                h.route_id,
                h.route_short_name,
                h.route_long_name,
                h.route_type,
                h.direction_id,
                h.shape_id,
                h.route_segment_km,
                h.segment_start_km,
                h.segment_end_km,

                sb.stop_before_id,
                sb.stop_before_name,
                ROUND(sb.stop_before_dist_km, 2) AS stop_before_dist_km,

                sa.stop_after_id,
                sa.stop_after_name,
                ROUND(sa.stop_after_dist_km, 2) AS stop_after_dist_km,

                h.bunching_events,
                h.severe_events,
                h.high_events,
                h.moderate_events,
                h.avg_gap_km,
                h.median_gap_km,
                h.closest_gap_km,
                h.vehicles_involved,
                h.active_timestamps

            FROM hotspots h
            LEFT JOIN stop_before sb
                ON h.route_id = sb.route_id
               AND h.direction_id = sb.direction_id
               AND h.shape_id = sb.shape_id
               AND h.route_segment_km = sb.route_segment_km
               AND sb.rn = 1
            LEFT JOIN stop_after sa
                ON h.route_id = sa.route_id
               AND h.direction_id = sa.direction_id
               AND h.shape_id = sa.shape_id
               AND h.route_segment_km = sa.route_segment_km
               AND sa.rn = 1
            ORDER BY h.bunching_events DESC;
        """)

        print("Created table: top20_bunching_hotspots_with_stops")

        con.execute("""
            CREATE OR REPLACE TABLE top20_hourly_bunching_pattern AS
            SELECT
                service_date,
                route_id,
                route_short_name,
                route_long_name,
                route_type,
                EXTRACT(hour FROM collection_timestamp) AS hour_of_day,
                peak_period,

                COUNT(*) AS bunching_events,
                COUNT(*) FILTER (WHERE bunching_severity = 'Severe') AS severe_events,
                COUNT(*) FILTER (WHERE bunching_severity = 'High') AS high_events,
                COUNT(*) FILTER (WHERE bunching_severity = 'Moderate') AS moderate_events,

                ROUND(AVG(distance_to_next_bus_km), 3) AS avg_gap_km,
                ROUND(MEDIAN(distance_to_next_bus_km), 3) AS median_gap_km

            FROM top20_bunching_events
            GROUP BY
                service_date,
                route_id,
                route_short_name,
                route_long_name,
                route_type,
                hour_of_day,
                peak_period
            ORDER BY route_short_name, hour_of_day;
        """)

        print("Created table: top20_hourly_bunching_pattern")

        con.execute("""
            CREATE OR REPLACE TABLE top20_route_reliability_scores AS
            WITH spacing AS (
                SELECT
                    service_date,
                    route_id,
                    route_short_name,
                    route_long_name,
                    route_type,
                    COUNT(*) AS total_spacing_rows
                FROM top20_observed_headways_clean
                GROUP BY
                    service_date,
                    route_id,
                    route_short_name,
                    route_long_name,
                    route_type
            ),

            bunching AS (
                SELECT
                    service_date,
                    route_id,
                    COUNT(*) AS total_bunching_events,
                    COUNT(*) FILTER (WHERE bunching_severity = 'Severe') AS severe_events,
                    COUNT(*) FILTER (WHERE peak_period IN ('AM Peak', 'PM Peak')) AS peak_events
                FROM top20_bunching_events
                GROUP BY service_date, route_id
            ),

            scored AS (
                SELECT
                    s.service_date,
                    s.route_id,
                    s.route_short_name,
                    s.route_long_name,
                    s.route_type,
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

            FROM scored
            ORDER BY reliability_score ASC;
        """)

        print("Created table: top20_route_reliability_scores")

        con.execute("""
            CREATE OR REPLACE TABLE top20_route_type_summary AS
            SELECT
                route_type,
                COUNT(*) AS route_count,
                ROUND(AVG(reliability_score), 2) AS avg_reliability_score,
                ROUND(MIN(reliability_score), 2) AS worst_reliability_score,
                ROUND(MAX(reliability_score), 2) AS best_reliability_score,
                ROUND(AVG(bunching_rate_pct), 2) AS avg_bunching_rate_pct,
                ROUND(AVG(severe_bunching_rate_pct), 2) AS avg_severe_bunching_rate_pct,
                ROUND(AVG(peak_bunching_share_pct), 2) AS avg_peak_bunching_share_pct,
                SUM(total_bunching_events) AS total_bunching_events,
                SUM(severe_events) AS total_severe_events
            FROM top20_route_reliability_scores
            GROUP BY route_type
            ORDER BY avg_reliability_score ASC;
        """)

        print("Created table: top20_route_type_summary")

        con.execute("""
            CREATE OR REPLACE TABLE top20_corridor_priority_ranking AS
            SELECT
                r.route_id,
                r.route_short_name,
                r.route_long_name,
                r.route_type,
                r.total_spacing_rows,
                r.total_bunching_events,
                r.severe_events,
                r.bunching_rate_pct,
                r.severe_bunching_rate_pct,
                r.peak_bunching_share_pct,
                r.reliability_score,
                r.reliability_band,

                COUNT(h.route_segment_km) AS hotspot_segments,
                COALESCE(MAX(h.bunching_events), 0) AS worst_segment_events,
                COALESCE(MAX(h.severe_events), 0) AS worst_segment_severe_events,

                ROUND(
                    (100 - r.reliability_score)
                    + (r.bunching_rate_pct * 1.5)
                    + (r.severe_bunching_rate_pct * 3.0)
                    + (COALESCE(MAX(h.bunching_events), 0) * 0.05),
                    2
                ) AS intervention_priority_score

            FROM top20_route_reliability_scores r
            LEFT JOIN top20_bunching_hotspots h
                ON r.route_id = h.route_id
            GROUP BY
                r.route_id,
                r.route_short_name,
                r.route_long_name,
                r.route_type,
                r.total_spacing_rows,
                r.total_bunching_events,
                r.severe_events,
                r.bunching_rate_pct,
                r.severe_bunching_rate_pct,
                r.peak_bunching_share_pct,
                r.reliability_score,
                r.reliability_band
            ORDER BY intervention_priority_score DESC;
        """)

        print("Created table: top20_corridor_priority_ranking")

        exports = {
            "top20_vehicle_positions_projected": "top20_vehicle_positions_projected.csv",
            "top20_observed_headways_clean": "top20_observed_headways_clean.csv",
            "top20_bunching_events": "top20_bunching_events.csv",
            "top20_bunching_hotspots": "top20_bunching_hotspots.csv",
            "top20_bunching_hotspots_with_stops": "top20_bunching_hotspots_with_stops.csv",
            "top20_hourly_bunching_pattern": "top20_hourly_bunching_pattern.csv",
            "top20_route_reliability_scores": "top20_route_reliability_scores.csv",
            "top20_route_type_summary": "top20_route_type_summary.csv",
            "top20_corridor_priority_ranking": "top20_corridor_priority_ranking.csv",
        }

        for table_name, file_name in exports.items():
            output_path = OUTPUT_DIR / file_name
            con.execute(f"""
                COPY {table_name}
                TO '{output_path.as_posix()}'
                WITH (HEADER, DELIMITER ',');
            """)
            print(f"Exported {output_path}")

        print("\nTop 20 Reliability Scores")
        print(con.execute("""
            SELECT
                route_short_name,
                route_long_name,
                route_type,
                total_spacing_rows,
                total_bunching_events,
                severe_events,
                bunching_rate_pct,
                severe_bunching_rate_pct,
                peak_bunching_share_pct,
                reliability_score,
                reliability_band
            FROM top20_route_reliability_scores
            ORDER BY reliability_score ASC;
        """).fetchdf())

        print("\nTop 20 Hotspots With Stops")
        print(con.execute("""
            SELECT
                route_short_name,
                route_long_name,
                route_type,
                direction_id,
                route_segment_km,
                stop_before_name,
                stop_after_name,
                bunching_events,
                severe_events,
                avg_gap_km,
                closest_gap_km
            FROM top20_bunching_hotspots_with_stops
            ORDER BY bunching_events DESC
            LIMIT 20;
        """).fetchdf())

        print("\nRoute Type Summary")
        print(con.execute("""
            SELECT *
            FROM top20_route_type_summary
            ORDER BY avg_reliability_score ASC;
        """).fetchdf())

        print("\nCorridor Priority Ranking")
        print(con.execute("""
            SELECT
                route_short_name,
                route_long_name,
                route_type,
                reliability_score,
                bunching_rate_pct,
                severe_bunching_rate_pct,
                worst_segment_events,
                intervention_priority_score
            FROM top20_corridor_priority_ranking
            ORDER BY intervention_priority_score DESC;
        """).fetchdf())

        print("\nTop 20 network reliability engine complete")

    finally:
        con.close()


if __name__ == "__main__":
    main()