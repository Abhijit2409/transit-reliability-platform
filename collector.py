"""
collector.py
============
TransLink GTFS-Realtime Vehicle Position Collector.

This collector:
- polls TransLink GTFS-RT VehiclePositions every 30 seconds
- parses the protobuf feed
- writes one validated parquet file per polling cycle
- uploads the validated parquet file to Supabase Storage
- logs pyarrow version for debugging parquet compatibility

Important:
- Local parquet writing is handled by src/parquet_writer.py
- That writer performs atomic writes and read-back validation
- Supabase upload happens only after local validation succeeds
"""

# =============================================================================
# IMPORTS
# =============================================================================

import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import requests
import pyarrow
from google.transit import gtfs_realtime_pb2

from src.parquet_writer import write_collection_cycle


# =============================================================================
# OPTIONAL LOCAL ENV LOADING
# =============================================================================

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass


# =============================================================================
# CONFIG
# =============================================================================

API_KEY = os.getenv("TRANSLINK_API_KEY", "").strip()

SUPABASE_URL = os.getenv("SUPABASE_URL", "").strip()
SUPABASE_SERVICE_ROLE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "").strip()
SUPABASE_BUCKET_NAME = os.getenv("SUPABASE_BUCKET_NAME", "").strip()

API_URL = "https://gtfsapi.translink.ca/v3/gtfsposition"

POLLING_INTERVAL_SECONDS = 30
REQUEST_TIMEOUT_SECONDS = 15
UPLOAD_TIMEOUT_SECONDS = 30

OUTPUT_FOLDER = "data/raw"
SUPABASE_REMOTE_PREFIX = "raw"
LOG_FOLDER = "logs"

COLLECT_ALL_ROUTES = True
SELECTED_ROUTE_IDS = ["099", "095", "R5"]


# =============================================================================
# LOGGING
# =============================================================================

def setup_logging() -> None:
    """Configure logging to console and a daily local log file."""
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
# =============================================================================

def fetch_vehicle_positions() -> bytes:
    """Fetch raw GTFS-RT VehiclePositions protobuf bytes from TransLink."""
    response = requests.get(
        API_URL,
        params={"apikey": API_KEY},
        timeout=REQUEST_TIMEOUT_SECONDS,
    )
    response.raise_for_status()
    return response.content


# =============================================================================
# PARSE
# =============================================================================

def parse_feed(raw_bytes: bytes, collection_timestamp: datetime) -> list[dict]:
    """Parse GTFS-RT protobuf response into vehicle position rows."""
    feed = gtfs_realtime_pb2.FeedMessage()
    feed.ParseFromString(raw_bytes)

    rows: list[dict] = []

    for entity in feed.entity:
        if not entity.HasField("vehicle"):
            continue

        vehicle = entity.vehicle

        route_id = vehicle.trip.route_id if vehicle.HasField("trip") else None
        trip_id = vehicle.trip.trip_id if vehicle.HasField("trip") else None
        vehicle_id = vehicle.vehicle.id if vehicle.HasField("vehicle") else None

        if vehicle.HasField("position"):
            latitude = vehicle.position.latitude
            longitude = vehicle.position.longitude

            # Important:
            # These GTFS-RT fields may be absent.
            # Missing should stay None, not fake zero.
            bearing = (
                vehicle.position.bearing
                if vehicle.position.HasField("bearing")
                else None
            )

            speed = (
                vehicle.position.speed
                if vehicle.position.HasField("speed")
                else None
            )
        else:
            latitude = None
            longitude = None
            bearing = None
            speed = None

        api_vehicle_timestamp = (
            datetime.fromtimestamp(vehicle.timestamp, tz=timezone.utc)
            if vehicle.timestamp
            else None
        )

        if not COLLECT_ALL_ROUTES and str(route_id) not in SELECTED_ROUTE_IDS:
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
# SUPABASE HELPERS
# =============================================================================

def supabase_is_configured() -> bool:
    """Return True if all required Supabase environment variables are present."""
    return bool(SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY and SUPABASE_BUCKET_NAME)


def get_remote_object_key(local_path: Path) -> str:
    """
    Convert local parquet path to Supabase object key.

    Local:
        data/raw/2026-05-24/vehicle_positions_22_221224.parquet

    Remote:
        raw/2026-05-24/vehicle_positions_22_221224.parquet
    """
    date_folder = local_path.parent.name
    filename = local_path.name
    return f"{SUPABASE_REMOTE_PREFIX}/{date_folder}/{filename}"


def upload_to_supabase(local_path: Path) -> None:
    """
    Upload a validated parquet file to Supabase Storage.

    The local file has already passed read-back validation before this function
    is called.
    """
    if not local_path.exists():
        raise FileNotFoundError(f"Cannot upload missing file: {local_path}")

    object_key = get_remote_object_key(local_path)

    url = (
        f"{SUPABASE_URL}/storage/v1/object/"
        f"{SUPABASE_BUCKET_NAME}/{object_key}"
    )

    headers = {
        "Authorization": f"Bearer {SUPABASE_SERVICE_ROLE_KEY}",
        "apikey": SUPABASE_SERVICE_ROLE_KEY,
        "Content-Type": "application/octet-stream",

        # Unique filenames should not need upsert, but keeping true prevents
        # failures if the same file is retried after a network issue.
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
# ONE COLLECTION CYCLE
# =============================================================================

def run_one_cycle() -> None:
    """Run one full fetch → parse → write → upload cycle."""
    collection_timestamp = datetime.now(timezone.utc)

    try:
        raw_bytes = fetch_vehicle_positions()
        logging.info("API request successful")
    except requests.RequestException as e:
        logging.error(f"API request failed: {e}")
        return

    try:
        rows = parse_feed(raw_bytes, collection_timestamp)
        logging.info(f"Vehicles collected: {len(rows)}")
    except Exception as e:
        logging.error(f"Parsing failed: {e}")
        return

    if not rows:
        logging.info("No vehicles to save this cycle")
        return

    try:
        df = pd.DataFrame(rows)

        written_path = write_collection_cycle(
            df=df,
            base_dir=Path(OUTPUT_FOLDER),
            collected_at=collection_timestamp,
        )

        if written_path is None:
            logging.error("Parquet write failed")
            return

        logging.info(f"Saved validated parquet to {written_path.as_posix()}")

    except Exception as e:
        logging.error(f"Save failed: {e}")
        return

    if not supabase_is_configured():
        logging.info("Supabase upload skipped: configuration missing")
        return

    try:
        upload_to_supabase(written_path)
        logging.info(f"Uploaded to Supabase: {get_remote_object_key(written_path)}")
    except requests.RequestException as e:
        logging.error(f"Supabase upload failed: {e}")
    except Exception as e:
        logging.error(f"Unexpected upload error: {e}")


# =============================================================================
# MAIN LOOP
# =============================================================================

def main() -> None:
    """Run the collector continuously."""
    setup_logging()

    logging.info("Starting TransLink GTFS collector")
    logging.info(f"PYARROW VERSION: {pyarrow.__version__}")
    logging.info(f"Polling interval: {POLLING_INTERVAL_SECONDS}s")
    logging.info(f"Output folder: {OUTPUT_FOLDER}")

    if not API_KEY:
        logging.error("TRANSLINK_API_KEY environment variable is not set. Exiting.")
        return

    if COLLECT_ALL_ROUTES:
        logging.info("Mode: ALL routes")
    else:
        logging.info(f"Mode: filtered routes {SELECTED_ROUTE_IDS}")

    if supabase_is_configured():
        logging.info(
            f"Supabase upload: ENABLED -> bucket '{SUPABASE_BUCKET_NAME}', "
            f"prefix '{SUPABASE_REMOTE_PREFIX}/'"
        )
    else:
        logging.info(
            "Supabase upload: DISABLED "
            "(missing SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY, or SUPABASE_BUCKET_NAME)"
        )

    while True:
        run_one_cycle()
        time.sleep(POLLING_INTERVAL_SECONDS)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        logging.info("Collector stopped by user")