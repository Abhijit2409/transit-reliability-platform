from pathlib import Path
from datetime import datetime, timedelta

import pandas as pd
import pyarrow.parquet as pq


RAW_ROOT = Path("data/raw")

START_DATE = "2026-05-10"
END_DATE   = "2026-05-24"


def daterange(start, end):
    current = start
    while current <= end:
        yield current
        current += timedelta(days=1)


start_dt = datetime.strptime(START_DATE, "%Y-%m-%d")
end_dt   = datetime.strptime(END_DATE, "%Y-%m-%d")

print("\n" + "=" * 90)
print("GTFS-RT DATASET STATUS CHECK")
print("=" * 90)

summary_rows = []

for dt in daterange(start_dt, end_dt):

    date_str = dt.strftime("%Y-%m-%d")
    folder = RAW_ROOT / date_str

    if not folder.exists():
        print(f"{date_str} | MISSING FOLDER")
        continue

    parquet_files = sorted(folder.glob("*.parquet"))

    total_files = len(parquet_files)

    valid_files = 0
    corrupted_files = 0
    total_rows = 0

    earliest_ts = None
    latest_ts = None

    for file in parquet_files:

        try:
            # Quick metadata validation
            pq_file = pq.ParquetFile(file)

            rows = pq_file.metadata.num_rows

            # Full pandas validation
            df = pd.read_parquet(file)

            valid_files += 1
            total_rows += rows

            if "collection_timestamp" in df.columns:

                ts = pd.to_datetime(
                    df["collection_timestamp"],
                    utc=True,
                    errors="coerce",
                ).dropna()

                if not ts.empty:

                    file_min = ts.min()
                    file_max = ts.max()

                    if earliest_ts is None or file_min < earliest_ts:
                        earliest_ts = file_min

                    if latest_ts is None or file_max > latest_ts:
                        latest_ts = file_max

        except Exception:
            corrupted_files += 1

    print(
        f"{date_str} | "
        f"files={total_files:>5} | "
        f"valid={valid_files:>5} | "
        f"corrupt={corrupted_files:>5} | "
        f"rows={total_rows:>10,}"
    )

    summary_rows.append({
        "date": date_str,
        "total_files": total_files,
        "valid_files": valid_files,
        "corrupted_files": corrupted_files,
        "total_rows": total_rows,
        "earliest_record": earliest_ts,
        "latest_record": latest_ts,
    })

summary_df = pd.DataFrame(summary_rows)

print("\n" + "=" * 90)
print("SUMMARY TABLE")
print("=" * 90)

print(summary_df)

summary_df.to_csv(
    "reports/all_dates_health_summary.csv",
    index=False,
)

print("\nSaved:")
print("reports/all_dates_health_summary.csv")