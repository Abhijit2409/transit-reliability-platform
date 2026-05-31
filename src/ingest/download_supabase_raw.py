import os
import time
from pathlib import Path

from dotenv import load_dotenv
from supabase import create_client


# =========================================================
# CONFIG
# =========================================================

load_dotenv()

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY")
BUCKET_NAME = os.getenv("SUPABASE_BUCKET_NAME", "translink-gtfs-raw")

DATE = "2026-05-22"

SUPABASE_PREFIX = f"raw/{DATE}"
LOCAL_DIR = Path(f"data/raw/{DATE}")
LOCAL_DIR.mkdir(parents=True, exist_ok=True)

MAX_RETRIES = 5
RETRY_SLEEP_SECONDS = 3


# =========================================================
# VALIDATE ENV
# =========================================================

if not SUPABASE_URL:
    raise ValueError("Missing SUPABASE_URL in .env")

if not SUPABASE_KEY:
    raise ValueError("Missing SUPABASE_SERVICE_ROLE_KEY in .env")

if not BUCKET_NAME:
    raise ValueError("Missing SUPABASE_BUCKET_NAME in .env")


# =========================================================
# CONNECT
# =========================================================

supabase = create_client(SUPABASE_URL, SUPABASE_KEY)


# =========================================================
# LIST ALL FILES WITH PAGINATION
# =========================================================

print(f"Listing Supabase files from: {SUPABASE_PREFIX}")

all_files = []
limit = 100
offset = 0

while True:
    batch = supabase.storage.from_(BUCKET_NAME).list(
        SUPABASE_PREFIX,
        {
            "limit": limit,
            "offset": offset,
            "sortBy": {
                "column": "name",
                "order": "asc",
            },
        },
    )

    if not batch:
        break

    all_files.extend(batch)
    print(f"Files listed so far: {len(all_files)}")

    if len(batch) < limit:
        break

    offset += limit

parquet_files = [
    file for file in all_files
    if file.get("name", "").endswith(".parquet")
]

print(f"Total parquet files found: {len(parquet_files)}")


# =========================================================
# DOWNLOAD + LIGHT PARQUET VALIDATION
# =========================================================

downloaded = 0
skipped = 0
failed = []

for file in parquet_files:
    file_name = file["name"]

    remote_path = f"{SUPABASE_PREFIX}/{file_name}"
    final_path = LOCAL_DIR / file_name
    temp_path = LOCAL_DIR / f"{file_name}.tmp"

    if final_path.exists():
        with open(final_path, "rb") as f:
            header = f.read(4)

        if header == b"PAR1":
            print(f"Skipping existing parquet: {file_name}")
            skipped += 1
            continue
        else:
            print(f"Deleting invalid existing file: {file_name}")
            final_path.unlink()

    success = False

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            print(f"Downloading: {file_name} | attempt {attempt}/{MAX_RETRIES}")

            file_bytes = supabase.storage.from_(BUCKET_NAME).download(remote_path)

            # IMPORTANT:
            # A real parquet file starts with magic bytes b"PAR1".
            # If this check fails, Supabase likely returned an error/HTML/JSON response.
            if not file_bytes or file_bytes[:4] != b"PAR1":
                preview = file_bytes[:80] if file_bytes else b"EMPTY"
                raise ValueError(f"Downloaded content is not parquet. Preview: {preview}")

            with open(temp_path, "wb") as f:
                f.write(file_bytes)

            temp_path.replace(final_path)

            downloaded += 1
            success = True
            break

        except Exception as e:
            print(f"Failed attempt {attempt}: {file_name} — {e}")

            if temp_path.exists():
                temp_path.unlink()

            time.sleep(RETRY_SLEEP_SECONDS)

    if not success:
        failed.append(file_name)


# =========================================================
# SAVE FAILED LIST
# =========================================================

failed_path = LOCAL_DIR / "failed_downloads.txt"

with open(failed_path, "w", encoding="utf-8") as f:
    for file_name in failed:
        f.write(file_name + "\n")


# =========================================================
# FINAL SUMMARY
# =========================================================

print("\n===================================")
print("DOWNLOAD COMPLETE")
print("===================================")
print(f"Date: {DATE}")
print(f"Supabase folder: {SUPABASE_PREFIX}")
print(f"Local folder: {LOCAL_DIR.resolve()}")
print(f"Downloaded: {downloaded}")
print(f"Skipped existing parquet: {skipped}")
print(f"Failed after retries: {len(failed)}")
print(f"Failed list saved to: {failed_path}")