from pathlib import Path
import duckdb

DB_PATH = Path("data/warehouse/transit.duckdb")

con = duckdb.connect(str(DB_PATH))

print("\n=== Tables ===")
print(con.execute("SHOW TABLES").fetchdf())

print("\n=== Route stability bands ===")
print(con.execute("""
SELECT
    stability_band,
    COUNT(*) AS route_days
FROM route_stability_summary
GROUP BY stability_band
ORDER BY route_days DESC;
""").fetchdf())

print("\n=== Coverage bands ===")
print(con.execute("""
SELECT
    coverage_band,
    COUNT(*) AS route_days
FROM route_stability_summary
GROUP BY coverage_band
ORDER BY route_days DESC;
""").fetchdf())

print("\n=== Most volatile route-days ===")
print(con.execute("""
SELECT
    service_date,
    route_id,
    observed_service_hours,
    total_observations,
    observation_cv,
    coverage_band,
    stability_band
FROM route_stability_summary
ORDER BY observation_cv DESC
LIMIT 20;
""").fetchdf())

print("\n=== Best high-coverage stable route-days ===")
print(con.execute("""
SELECT
    service_date,
    route_id,
    observed_service_hours,
    total_observations,
    observation_cv,
    coverage_band,
    stability_band
FROM route_stability_summary
WHERE coverage_band = 'High Coverage'
ORDER BY observation_cv ASC
LIMIT 20;
""").fetchdf())

print("\n=== Telemetry quality check ===")
print(con.execute("""
SELECT
    MIN(valid_row_percentage) AS min_valid_pct,
    AVG(valid_row_percentage) AS avg_valid_pct,
    MAX(valid_row_percentage) AS max_valid_pct
FROM telemetry_quality_summary;
""").fetchdf())

con.close()