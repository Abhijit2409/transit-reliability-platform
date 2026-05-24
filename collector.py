"""
collector.py
============
TransLink GTFS-Realtime Vehicle Position Collector.

Polls the TransLink GTFS-Realtime "vehicle positions" feed on a fixed
interval, parses the protobuf response, and appends the results to
hourly Parquet files for downstream analysis (Python, SQL, Power BI,
Streamlit).

The collector is designed to run continuously in a terminal. It is
fault-tolerant: a single failed API call, parse error, or write error
will be logged but will not stop the loop.

Stop the collector with Ctrl+C.
"""

# =============================================================================
# IMPORTS
# =============================================================================
import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from src.parquet_writer import write_collection_cycle

import pandas as pd
import requests
from google.transit import gtfs_realtime_pb2
import pyarrow
print("PYARROW VERSION:", pyarrow.__version__)

# Local convenience: load a `.env` file if one exists, so you don't have to
# `export` variables every time. On Railway, env vars are injected by the
# platform and this call is a harmless no-op.
try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass


# =============================================================================
# CONFIG
# -----------------------------------------------------------------------------
# Secrets come from environment variables (set locally in a .env file or
# in the Railway dashboard). Non-secret defaults can stay in code.
# =============================================================================

# ---- Secrets (required) -----------------------------------------------------
# Your TransLink developer API key. Set this in your environment, never in
# source code.
API_KEY = os.getenv("TRANSLINK_API_KEY", "").strip()

# Supabase project credentials. The service role key has full read/write
# access to Storage, so treat it like a password: never commit it.
SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_SERVICE_ROLE_KEY = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")
SUPABASE_BUCKET_NAME = os.environ.get("SUPABASE_BUCKET_NAME", "")

# ---- Non-secrets ------------------------------------------------------------
# TransLink GTFS-Realtime vehicle positions endpoint.
API_URL = "https://gtfsapi.translink.ca/v3/gtfsposition"

# How often to poll the API, in seconds.
POLLING_INTERVAL_SECONDS = 30

# How long to wait for the API to respond before giving up on a single call.
REQUEST_TIMEOUT_SECONDS = 15

# How long to wait for a Supabase upload before giving up.
UPLOAD_TIMEOUT_SECONDS = 30

# Where Parquet files are written locally. The collector creates this
# folder (and dated sub-folders) automatically. On Railway this is the
# container's ephemeral disk — durable storage lives in Supabase.
OUTPUT_FOLDER = "data/raw"

# Prefix used inside the Supabase bucket. Resulting object keys look like:
#   raw/2026-05-08/vehicle_positions_19.parquet
SUPABASE_REMOTE_PREFIX = "raw"

# Where log files are written.
LOG_FOLDER = "logs"

# Route filtering.
#   COLLECT_ALL_ROUTES = True   -> keep every vehicle in the feed
#   COLLECT_ALL_ROUTES = False  -> keep only vehicles whose route_id is in
#                                  SELECTED_ROUTE_IDS (compared as strings)
COLLECT_ALL_ROUTES = True
SELECTED_ROUTE_IDS = ["099", "095", "R5"]  # example: 99 B-Line, 95 B-Line, R5


# =============================================================================
# LOGGING
# -----------------------------------------------------------------------------
# We log to both the terminal AND a daily log file in LOG_FOLDER, so you can
# review what happened overnight without re-running anything.
# =============================================================================
def setup_logging() -> None:
    """Configure root logger to write to console and a daily file."""
    Path(LOG_FOLDER).mkdir(parents=True, exist_ok=True)
    log_file = Path(LOG_FOLDER) / f"collector_{datetime.now().strftime('%Y-%m-%d')}.log"

    logging.basicConfig(
        level=logging.INFO,
        format="[%(asctime)s] [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        handlers=[
            logging.FileHandler(log_file, encoding="utf-8"),
            logging.StreamHandler(),
        ],
    )


# =============================================================================
# FETCH
# -----------------------------------------------------------------------------
# One function, one job: ask TransLink for the latest feed and return the
# raw protobuf bytes. Network errors bubble up so the main loop can log and
# retry.
# =============================================================================
def fetch_vehicle_positions() -> bytes:
    """Call the TransLink API and return the raw protobuf response bytes."""
    response = requests.get(
        API_URL,
        params={"apikey": API_KEY},
        timeout=REQUEST_TIMEOUT_SECONDS,
    )
    response.raise_for_status()  # turn HTTP errors (4xx/5xx) into exceptions
    return response.content


# =============================================================================
# PARSE
# -----------------------------------------------------------------------------
# Convert the protobuf payload into a list of plain-Python dicts. We keep
# only the fields we asked for in the spec; everything else is ignored.
# =============================================================================
def parse_feed(raw_bytes: bytes, collection_timestamp: datetime) -> list[dict]:
    """Parse a GTFS-Realtime FeedMessage into a list of vehicle position rows.

    Each returned dict has exactly the columns we want to store in Parquet.
    """
    feed = gtfs_realtime_pb2.FeedMessage()
    feed.ParseFromString(raw_bytes)

    rows: list[dict] = []

    for entity in feed.entity:
        # The feed can carry multiple entity types; we only want vehicles.
        if not entity.HasField("vehicle"):
            continue

        vehicle = entity.vehicle

        # --- Pull nested fields safely -------------------------------------
        # GTFS-Realtime fields are all optional, so we check HasField()
        # before reading sub-messages to avoid silent default values.
        route_id = vehicle.trip.route_id if vehicle.HasField("trip") else None
        trip_id = vehicle.trip.trip_id if vehicle.HasField("trip") else None
        vehicle_id = vehicle.vehicle.id if vehicle.HasField("vehicle") else None

        latitude = vehicle.position.latitude if vehicle.HasField("position") else None
        longitude = vehicle.position.longitude if vehicle.HasField("position") else None
        bearing = vehicle.position.bearing if vehicle.HasField("position") else None
        speed = vehicle.position.speed if vehicle.HasField("position") else None

        # The vehicle.timestamp field is a Unix epoch seconds value.
        api_vehicle_timestamp = (
            datetime.fromtimestamp(vehicle.timestamp, tz=timezone.utc)
            if vehicle.timestamp
            else None
        )

        # --- Optional route filter -----------------------------------------
        if not COLLECT_ALL_ROUTES and route_id not in SELECTED_ROUTE_IDS:
            continue

        rows.append(
            {
                "collection_timestamp": collection_timestamp,
                "api_vehicle_timestamp": api_vehicle_timestamp,
                "entity_id": entity.id,
                "vehicle_id": vehicle_id,
                "route_id": route_id,
                "trip_id": trip_id,
                "latitude": latitude,
                "longitude": longitude,
                "bearing": bearing,
                "speed": speed,
            }
        )

    return rows


# =============================================================================
# SAVE
# -----------------------------------------------------------------------------
# Storage layout:
#   data/raw/YYYY-MM-DD/vehicle_positions_HH.parquet
#
# Within a single hour we APPEND new 30-second snapshots into the same file.
# Parquet doesn't support true append, so we read-then-rewrite. At ~700
# vehicles per poll * 120 polls per hour = ~84k rows per file, this is fast
# (well under a second on a typical laptop) and keeps the on-disk layout
# clean and easy to query later.
# =============================================================================
def get_output_path(collection_timestamp: datetime) -> Path:
    """Return the hourly Parquet path for a given collection timestamp.

    Creates the date sub-folder if it doesn't already exist. Uses LOCAL
    time for the folder/file names so they line up with how a Vancouver
    analyst would think about "the 7pm hour".
    """
    local_ts = collection_timestamp.astimezone()  # convert UTC -> local
    date_str = local_ts.strftime("%Y-%m-%d")
    hour_str = local_ts.strftime("%H")

    folder = Path(OUTPUT_FOLDER) / date_str
    folder.mkdir(parents=True, exist_ok=True)

    return folder / f"vehicle_positions_{hour_str}.parquet"


# =============================================================================
# UPLOAD (Supabase Storage)
# -----------------------------------------------------------------------------
# We talk to Supabase Storage's REST API directly with `requests`. This keeps
# our dependencies small and avoids version drift in third-party SDKs.
#
# The endpoint is:
#   POST {SUPABASE_URL}/storage/v1/object/{BUCKET}/{KEY}
# with header `x-upsert: true` so we can re-upload (overwrite) the same
# hourly file every 30 seconds without errors.
# =============================================================================
def supabase_is_configured() -> bool:
    """Return True only if all Supabase env vars are present."""
    return bool(SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY and SUPABASE_BUCKET_NAME)


def get_remote_object_key(local_path: Path) -> str:
    """Translate a local Parquet path to its Supabase object key.

    Local:  data/raw/2026-05-08/vehicle_positions_19.parquet
    Remote: raw/2026-05-08/vehicle_positions_19.parquet
    """
    # The local layout already has the date folder + filename we want; we
    # just swap the top-level folder for SUPABASE_REMOTE_PREFIX.
    date_folder = local_path.parent.name
    filename = local_path.name
    return f"{SUPABASE_REMOTE_PREFIX}/{date_folder}/{filename}"


def upload_to_supabase(local_path: Path) -> None:
    """Upload (or overwrite) a single Parquet file to Supabase Storage.

    Raises requests.RequestException on network/HTTP errors so the caller
    can log and continue. The local file is left in place either way, so
    a failed upload can be retried on the next polling cycle.
    """
    object_key = get_remote_object_key(local_path)
    url = f"{SUPABASE_URL}/storage/v1/object/{SUPABASE_BUCKET_NAME}/{object_key}"

    headers = {
        "Authorization": f"Bearer {SUPABASE_SERVICE_ROLE_KEY}",
        # apikey is also accepted; sending both keeps us compatible with
        # both Supabase Storage gateway versions.
        "apikey": SUPABASE_SERVICE_ROLE_KEY,
        "Content-Type": "application/octet-stream",
        # x-upsert: true means "create OR replace", which is exactly what
        # we want when we re-upload the growing hourly file.
        "x-upsert": "true",
    }

    with open(local_path, "rb") as f:
        response = requests.post(
            url,
            headers=headers,
            data=f,
            timeout=UPLOAD_TIMEOUT_SECONDS,
        )

    response.raise_for_status()


# =============================================================================
# ONE POLLING CYCLE
# -----------------------------------------------------------------------------
# A single iteration of: fetch -> parse -> save -> upload. Wrapped in
# granular try/except blocks so each failure mode logs a useful message
# but never kills the outer loop.
# =============================================================================
def run_one_cycle() -> None:
    """Perform one fetch/parse/save cycle. Never raises."""
    collection_timestamp = datetime.now(timezone.utc)

    # ---- FETCH --------------------------------------------------------------
    try:
        raw_bytes = fetch_vehicle_positions()
        logging.info("API request successful")
    except requests.RequestException as e:
        logging.error(f"API request failed: {e}")
        return

    # ---- PARSE --------------------------------------------------------------
    try:
        rows = parse_feed(raw_bytes, collection_timestamp)
        logging.info(f"Vehicles collected: {len(rows)}")
    except Exception as e:
        logging.error(f"Parsing failed: {e}")
        return

    if not rows:
        # Either the feed was empty or the route filter excluded everything.
        logging.info("No vehicles to save this cycle")
        return

    # ---- SAVE LOCAL ---------------------------------------------------------
    try:
        df = pd.DataFrame(rows)

        written_path = write_collection_cycle(
            df=df,
            base_dir=Path("data/raw"),
        )

        if written_path is None:
            logging.error("Parquet write failed")
            return

        logging.info(f"Saved validated parquet to {written_path.as_posix()}")
        
        
    
    except Exception as e:
        logging.error(f"Save failed: {e}")
        return  # nothing to upload if local save failed

    # ---- UPLOAD TO SUPABASE -------------------------------------------------
    # The local file is the source of truth; the upload is best-effort.
    # If it fails we log and move on. Because we re-upload the SAME hourly
    # file on every cycle, the next successful upload will include any
    # rows that the failed upload missed.
    if not supabase_is_configured():
        return

    try:
        upload_to_supabase(written_path)
        logging.info(f"Uploaded to Supabase: {get_remote_object_key(written_path)}")
    except requests.RequestException as e:
        logging.error(f"Supabase upload failed (will retry next cycle): {e}")
    except Exception as e:
        logging.error(f"Unexpected upload error: {e}")


# =============================================================================
# MAIN LOOP
# =============================================================================
def main() -> None:
    """Run the collector forever, polling every POLLING_INTERVAL_SECONDS."""
    setup_logging()

    logging.info("Starting TransLink GTFS collector")

    # Fail fast on missing API key — without it, every fetch will 401.
    if not API_KEY:
        logging.error("TRANSLINK_API_KEY environment variable is not set. Exiting.")
        return

    logging.info(f"Polling interval: {POLLING_INTERVAL_SECONDS}s")
    logging.info(f"Output folder:    {OUTPUT_FOLDER}")
    logging.info(
        "Mode: ALL routes"
        if COLLECT_ALL_ROUTES
        else f"Mode: filtered routes {SELECTED_ROUTE_IDS}"
    )

    if supabase_is_configured():
        logging.info(
            f"Supabase upload: ENABLED -> bucket '{SUPABASE_BUCKET_NAME}', "
            f"prefix '{SUPABASE_REMOTE_PREFIX}/'"
        )
    else:
        logging.info(
            "Supabase upload: DISABLED (one or more of SUPABASE_URL, "
            "SUPABASE_SERVICE_ROLE_KEY, SUPABASE_BUCKET_NAME is missing)"
        )

    while True:
        run_one_cycle()
        time.sleep(POLLING_INTERVAL_SECONDS)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        # Friendly shutdown when the user presses Ctrl+C.
        logging.info("Collector stopped by user (Ctrl+C)")
