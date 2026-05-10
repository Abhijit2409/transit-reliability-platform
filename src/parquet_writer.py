"""
parquet_writer.py
=================
Safe, atomic parquet writer for GTFS-RT collector.

Design principles:
- Write-once, never modify: each collection cycle produces ONE immutable file.
- Atomic writes: write to .tmp, validate, then rename. Crashes never leave
  corrupt files at the destination filename.
- Read-back validation: every file is read back with pandas before being
  considered "successful". If pandas can't read it, the file is rejected.
- Schema enforcement: required columns are verified before write.

Why this fixes "Repetition level histogram size mismatch":
- We never append to existing parquet files (the most common cause).
- We never have two writers touching the same filename (unique timestamps).
- We never leave half-written files at the final path (atomic rename).
- We catch malformed writes immediately via read-back validation.
"""

import logging
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq


logger = logging.getLogger(__name__)


# Required columns every vehicle position record must have BEFORE writing.
# Adjust this list to match your collector's actual schema.
REQUIRED_COLUMNS = [
    "collection_timestamp",
    "api_vehicle_timestamp",
    "entity_id",
    "vehicle_id",
    "route_id",
    "trip_id",
    "latitude",
    "longitude",
    "bearing",
    "speed",
]


class ParquetWriteError(Exception):
    """Raised when a parquet write fails validation. Caller should retry next cycle."""
    pass


def build_output_path(base_dir: Path, collected_at: datetime) -> Path:
    """
    Build a unique, partitioned output path for this collection cycle.

    Layout: data/raw/YYYY-MM-DD/vehicle_positions_HH_HHMMSS.parquet

    Why include HHMMSS in the filename?
    - Guarantees no two cycles ever write to the same filename.
    - Eliminates race conditions and append corruption entirely.
    - Easy to sort chronologically for downstream consolidation.

    Args:
        base_dir: Root data directory (e.g., Path("data/raw"))
        collected_at: UTC timestamp of this collection cycle.

    Returns:
        Full path where the parquet file should land.
    """
    # Partition by date for efficient downstream filtering.
    date_str = collected_at.strftime("%Y-%m-%d")
    hour_str = collected_at.strftime("%H")
    # Full timestamp in filename keeps every file unique within the hour.
    time_str = collected_at.strftime("%H%M%S")

    output_dir = base_dir / date_str
    output_dir.mkdir(parents=True, exist_ok=True)

    filename = f"vehicle_positions_{hour_str}_{time_str}.parquet"
    return output_dir / filename


def validate_dataframe(df: pd.DataFrame) -> None:
    """
    Pre-write validation: catch problems BEFORE we touch the disk.

    This is "shift-left" validation — we'd rather skip a bad batch than
    write a corrupt file that confuses downstream consumers.

    Raises:
        ParquetWriteError: If the dataframe is unsuitable for writing.
    """
    if df is None or len(df) == 0:
        raise ParquetWriteError("DataFrame is empty — nothing to write")

    missing = [col for col in REQUIRED_COLUMNS if col not in df.columns]
    if missing:
        raise ParquetWriteError(f"Missing required columns: {missing}")


def write_parquet_atomic(
    df: pd.DataFrame,
    final_path: Path,
    compression: str = "snappy",
) -> int:
    """
    Atomically write a DataFrame to parquet with full read-back validation.

    The atomic write pattern, step by step:

    1. Write to a .tmp file in the SAME directory as the final destination.
       (Same dir is required for atomic os.replace on Windows.)
    2. Read the .tmp file back with pandas to confirm it's actually readable.
    3. Verify row count and required columns survived the round trip.
    4. Only after all checks pass, atomically rename .tmp → final_path.
       os.replace() is atomic on both Windows and Linux: either the new file
       fully exists at the destination, or it doesn't. Never half-written.

    If any step fails, the .tmp file is deleted and an exception is raised.
    The final_path is never touched, so no corrupt file is ever created.

    Args:
        df: DataFrame to write. Must pass validate_dataframe().
        final_path: Where the parquet file should ultimately land.
        compression: Parquet compression codec (snappy is the standard).

    Returns:
        File size in bytes after successful write.

    Raises:
        ParquetWriteError: If validation or read-back fails at any stage.
    """
    validate_dataframe(df)

    expected_rows = len(df)
    logger.info(f"Preparing to write {expected_rows} rows to {final_path.name}")

    # Build a unique temp filename in the SAME directory as the destination.
    # Same-directory is critical: os.replace() is only atomic within a single
    # filesystem volume. Cross-volume renames fall back to copy+delete and
    # lose atomicity.
    final_path.parent.mkdir(parents=True, exist_ok=True)

    # tempfile.mkstemp creates the file with a unique name and returns an
    # OS-level file descriptor. We close it immediately because pyarrow will
    # open the file by path. mkstemp guarantees no name collisions even with
    # multiple processes.
    tmp_fd, tmp_path_str = tempfile.mkstemp(
        suffix=".parquet.tmp",
        prefix=final_path.stem + "_",
        dir=str(final_path.parent),
    )
    os.close(tmp_fd)
    tmp_path = Path(tmp_path_str)

    try:
        # --- Step 1: Convert DataFrame → Arrow Table → Parquet ---
        # Going through Arrow explicitly (instead of df.to_parquet) gives us
        # tighter control over schema and avoids some pandas/pyarrow version
        # quirks that can cause the "histogram mismatch" error.
        table = pa.Table.from_pandas(df, preserve_index=False)

        pq.write_table(
            table,
            tmp_path,
            compression=compression,
            # use_dictionary=True compresses repeated string values (route_id,
            # trip_id) very efficiently — typical for transit data.
            use_dictionary=True,
            # write_statistics=True lets downstream readers skip row groups,
            # but more importantly ensures the metadata-data consistency
            # checks pass on read-back.
            write_statistics=True,
            # Single row group per file: simpler, fewer chances for the
            # multi-row-group metadata bugs that cause your error.
            row_group_size=expected_rows,
        )

        size_bytes = tmp_path.stat().st_size
        logger.info(f"Wrote temp file: {tmp_path.name} ({size_bytes:,} bytes)")

        # --- Step 2: Read-back validation with pandas ---
        # This is the critical step. If pandas can't read what we just wrote,
        # the file IS corrupt and we must reject it. This is exactly the
        # check that would have caught your current bug.
        try:
            df_check = pd.read_parquet(tmp_path)
        except Exception as e:
            raise ParquetWriteError(
                f"Read-back validation failed: pandas cannot read written file: {e}"
            )

        # --- Step 3: Verify the round-trip preserved our data ---
        if len(df_check) != expected_rows:
            raise ParquetWriteError(
                f"Row count mismatch: wrote {expected_rows}, read back {len(df_check)}"
            )

        missing_after = [c for c in REQUIRED_COLUMNS if c not in df_check.columns]
        if missing_after:
            raise ParquetWriteError(
                f"Columns missing after round trip: {missing_after}"
            )

        logger.info(
            f"Read-back validation OK: {len(df_check)} rows, "
            f"{len(df_check.columns)} columns"
        )

        # --- Step 4: Atomic rename ---
        # os.replace overwrites the destination atomically on both Windows
        # and Unix. This is the ONLY moment the final filename appears on
        # disk, and at that moment the file is already validated.
        os.replace(tmp_path, final_path)
        logger.info(f"Atomic rename complete: {final_path.name}")

        return size_bytes

    except Exception:
        # Cleanup: if anything failed, remove the temp file so we don't
        # accumulate orphaned .tmp files on disk.
        if tmp_path.exists():
            try:
                tmp_path.unlink()
                logger.debug(f"Cleaned up temp file: {tmp_path.name}")
            except OSError as cleanup_err:
                logger.warning(
                    f"Failed to clean up temp file {tmp_path}: {cleanup_err}"
                )
        raise  # Re-raise the original exception for the caller to handle


def write_collection_cycle(
    df: pd.DataFrame,
    base_dir: Path,
    collected_at: Optional[datetime] = None,
) -> Optional[Path]:
    """
    High-level entry point: write one collection cycle's data safely.

    This is what your collector loop should call once per cycle. It handles
    path generation, atomic writing, and validation. If anything fails, it
    logs the error and returns None — the collector should continue running
    and try again on the next cycle.

    Args:
        df: DataFrame from this polling cycle.
        base_dir: Root data directory (e.g., Path("data/raw")).
        collected_at: UTC timestamp; defaults to now.

    Returns:
        Path to the written file on success, None on failure.
    """
    if collected_at is None:
        collected_at = datetime.now(timezone.utc)

    final_path = build_output_path(base_dir, collected_at)

    try:
        size_bytes = write_parquet_atomic(df, final_path)
        logger.info(
            f"SUCCESS: wrote {len(df)} rows to {final_path} "
            f"({size_bytes:,} bytes, {size_bytes / 1024:.2f} KB)"
        )
        return final_path
    except ParquetWriteError as e:
        logger.error(f"FAILED to write cycle: {e}")
        return None
    except Exception as e:
        # Catch-all for unexpected errors (disk full, permissions, etc.).
        # We log and return None instead of crashing the collector loop.
        logger.exception(f"UNEXPECTED ERROR writing cycle: {e}")
        return None