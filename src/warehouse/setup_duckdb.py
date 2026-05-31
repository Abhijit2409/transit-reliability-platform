import duckdb
from pathlib import Path

DB_PATH = "data/warehouse/transit.duckdb"
RAW_PATH = "data/raw/**/*.parquet"
OUTPUT_DIR = Path("reports/sql_outputs")

OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

con = duckdb.connect(DB_PATH)

print("Connected to DuckDB")

# Bronze layer: read all raw parquet files as one SQL view
con.execute(f"""
CREATE OR REPLACE VIEW bronze_vehicle_positions AS
SELECT *
FROM read_parquet('{RAW_PATH}', union_by_name=True);
""")

print("Created bronze_vehicle_positions view")

# Silver layer: cleaned vehicle positions
con.execute("""
CREATE OR REPLACE TABLE silver_vehicle_positions AS
SELECT
    collection_timestamp,
    api_vehicle_timestamp,
    entity_id,
    vehicle_id,
    route_id,
    trip_id,
    latitude,
    longitude,
    bearing,
    speed,
    CAST(collection_timestamp AS DATE) AS service_date,
    EXTRACT(hour FROM collection_timestamp) AS hour_of_day
FROM bronze_vehicle_positions
WHERE vehicle_id IS NOT NULL
  AND route_id IS NOT NULL
  AND latitude IS NOT NULL
  AND longitude IS NOT NULL
  AND latitude BETWEEN 48 AND 50
  AND longitude BETWEEN -124 AND -121;
""")

print("Created silver_vehicle_positions table")

# Route-hour summary
con.execute("""
CREATE OR REPLACE TABLE route_hourly_summary AS
SELECT
    service_date,
    route_id,
    hour_of_day,
    COUNT(*) AS total_observations,
    COUNT(DISTINCT vehicle_id) AS unique_vehicles,
    COUNT(DISTINCT trip_id) AS unique_trips,
    MIN(collection_timestamp) AS first_seen,
    MAX(collection_timestamp) AS last_seen
FROM silver_vehicle_positions
GROUP BY service_date, route_id, hour_of_day
ORDER BY service_date, route_id, hour_of_day;
""")

print("Created route_hourly_summary table")

# Export summary CSV
con.execute("""
COPY route_hourly_summary
TO 'reports/sql_outputs/route_hourly_summary.csv'
WITH (HEADER, DELIMITER ',');
""")

print("Exported reports/sql_outputs/route_hourly_summary.csv")

# Quick sanity check
row_count = con.execute("SELECT COUNT(*) FROM silver_vehicle_positions").fetchone()[0]
route_count = con.execute("SELECT COUNT(DISTINCT route_id) FROM silver_vehicle_positions").fetchone()[0]

print(f"Silver rows: {row_count:,}")
print(f"Routes found: {route_count:,}")

con.close()
print("DuckDB setup complete")