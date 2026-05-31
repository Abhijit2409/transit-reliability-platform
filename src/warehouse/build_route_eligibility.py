from pathlib import Path
import duckdb

DB_PATH = Path("data/warehouse/transit.duckdb")
OUTPUT_DIR = Path("reports/sql_outputs")

OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


def main():

    con = duckdb.connect(str(DB_PATH))

    try:
        print("Connected to DuckDB")

        con.execute("""
        CREATE OR REPLACE TABLE route_eligibility_summary AS
        SELECT
            s.service_date,
            s.route_id,

            s.observed_service_hours,
            s.total_observations,
            s.observation_cv,

            s.coverage_band,
            s.stability_band,

            q.valid_row_percentage,

            CASE
                WHEN s.observed_service_hours >= 12
                     AND s.total_observations >= 1000
                     AND q.valid_row_percentage >= 95

                THEN 'Eligible'

                ELSE 'Not Eligible'
            END AS eligibility_status

        FROM route_stability_summary s
        LEFT JOIN telemetry_quality_summary q
            ON s.service_date = q.service_date
           AND s.route_id = q.route_id
        """)

        print("Created route_eligibility_summary")

        con.execute("""
        COPY route_eligibility_summary
        TO 'reports/sql_outputs/route_eligibility_summary.csv'
        WITH (HEADER, DELIMITER ',');
        """)

        print("Exported route_eligibility_summary.csv")

        print("\nEligibility Summary")

        print(
            con.execute("""
            SELECT
                eligibility_status,
                COUNT(*) AS route_days
            FROM route_eligibility_summary
            GROUP BY eligibility_status
            ORDER BY route_days DESC
            """).fetchdf()
        )

        print("\nCoverage Breakdown")

        print(
            con.execute("""
            SELECT
                coverage_band,
                COUNT(*) AS route_days
            FROM route_eligibility_summary
            GROUP BY coverage_band
            ORDER BY route_days DESC
            """).fetchdf()
        )

        print("\nTop Eligible Routes")

        print(
            con.execute("""
            SELECT
                route_id,
                COUNT(*) AS eligible_days
            FROM route_eligibility_summary
            WHERE eligibility_status = 'Eligible'
            GROUP BY route_id
            ORDER BY eligible_days DESC
            LIMIT 20
            """).fetchdf()
        )

    finally:
        con.close()


if __name__ == "__main__":
    main()
    