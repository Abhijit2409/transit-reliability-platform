"""
bus_corridor_geometry.py
========================
Static bus corridor geometry builder for the TransLink GTFS-RT platform.

This module is the missing static-infrastructure layer of the framework. The
previous (RT-only) layers answered "where are buses *right now* and how
reliably is the network running?". This layer answers "what is the physical
shape of the bus network the RT vehicles are flowing through?".

Why this layer exists
---------------------
RT vehicle positions are points. A point cloud, no matter how dense, does
not show you a *corridor*. A corridor is a line — the spatial backbone of a
route — and that line lives in GTFS Static (`shapes.txt`). Until we join
shapes.txt to the routes we observe in telemetry, every "corridor map" we
have produced has actually been a vehicle-position map. Real corridor
infrastructure mapping starts here.

What this module produces
-------------------------
CSV outputs (written to outputs/):
    bus_route_catalog.csv          — every bus route with subtype, color,
                                     and whether it appeared in RT telemetry
    bus_shape_index.csv            — one representative shape per bus route
                                     (the longest unique shape for that route)
    bus_corridor_geometry.csv      — long-format (route_id, shape_pt_lat,
                                     shape_pt_lon, sequence) for plotting
                                     every chosen corridor as a polyline
    bus_stops_catalog.csv          — every stop that is served by at least
                                     one bus trip (filters out HandyDART /
                                     SkyTrain-only / SeaBus stops)
    bus_route_stop_membership.csv  — (route_id, stop_id) edges — which
                                     stops belong to which routes
    critical_bus_corridors.csv     — the top-N operationally critical
                                     corridors, with both RT activity and
                                     static stop counts

How it's used downstream
------------------------
bus_corridor_maps.py reads these CSVs to draw the actual interactive maps.
Splitting "build the data" from "render the map" keeps the geometry logic
testable and makes re-rendering maps fast (no need to re-parse 180 MB of
stop_times every time you tweak a map style).

Scope honesty
-------------
The GTFS-RT vehicle feed in this dataset is BUS-ONLY. This module honors
that boundary: it filters routes.txt to `route_type == 3` (bus) before any
join, so SkyTrain / SeaBus / WCE / HandyDART shapes and stops never enter
the pipeline. The framework's RT layers already know they're bus-only;
this static layer now matches that scope.

Usage (from project root):
    python src/bus_corridor_geometry.py
    python src/bus_corridor_geometry.py --gtfs-dir data/gtfs_static
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path
from typing import Optional

import pandas as pd


# ---------------------------------------------------------------------------
# CONFIGURATION
# ---------------------------------------------------------------------------
# GTFS route_type 3 is "Bus". Everything else (SkyTrain=1, WCE=2, SeaBus=4,
# HandyDART=715) is out of scope for this layer.
BUS_ROUTE_TYPE = 3

# Where the project keeps the static GTFS extract.
DEFAULT_GTFS_DIR = Path("data/gtfs_static")
DEFAULT_OUTPUT_DIR = Path("outputs")

# Bus subtype classification — same logic used by multimodal_transit_intelligence.
# RapidBus = TransLink's frequent-service brand (R1–R6, green livery).
# B-Line   = the legacy bullet-stop branded service, currently just the 99.
# NightBus = N-prefixed overnight routes.
# Express  = 5xx / "Express" in long name (longer-distance commuter).
# Regular  = everything else.
RAPIDBUS_PREFIX = "R"
BLINE_ROUTE_IDS = {"6641"}              # GTFS route_id for 99 B-Line
NIGHTBUS_PREFIX = "N"

# Pull this many routes into the "critical corridor" shortlist for the
# top-corridor map. Twenty matches what the existing top_corridors.csv
# already publishes, which keeps the downstream story consistent.
CRITICAL_CORRIDOR_TOP_N = 20

# stop_times.txt is ~180 MB. We never load it whole — we read it in chunks
# and project to the columns we actually need.
STOP_TIMES_CHUNKSIZE = 500_000
STOP_TIMES_COLS = ["trip_id", "stop_id", "stop_sequence"]


# ---------------------------------------------------------------------------
# LOGGING
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-7s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("bus_corridor_geometry")


# ---------------------------------------------------------------------------
# CLASSIFICATION
# ---------------------------------------------------------------------------
def classify_bus_subtype(row: pd.Series) -> str:
    """
    Tag every bus route with an operational subtype.

    Why subtype matters: the RT layers already show that RapidBus, B-Line,
    NightBus and regular buses behave very differently in time and space.
    Carrying the same subtype tag into the static-geometry layer lets the
    maps below match the existing color and grouping conventions exactly,
    so a reader doesn't see "RapidBus" mean one thing in a chart and a
    different thing on a map.
    """
    rid = str(row["route_id"])
    short = str(row.get("route_short_name", "") or "")

    if rid in BLINE_ROUTE_IDS:
        return "B-Line"
    if short.startswith(NIGHTBUS_PREFIX) and short[1:].isdigit():
        return "NightBus"
    if short.startswith(RAPIDBUS_PREFIX) and short[1:].isdigit():
        return "RapidBus"
    # "Express" routes — TransLink's longer-haul commuter buses. We detect
    # by route_long_name containing "Express" OR a 5xx-series short name.
    long_name = str(row.get("route_long_name", "") or "").lower()
    if "express" in long_name or short.startswith("5"):
        return "Express"
    return "Regular Bus"


# ---------------------------------------------------------------------------
# LOAD HELPERS
# ---------------------------------------------------------------------------
def load_bus_routes(gtfs_dir: Path) -> pd.DataFrame:
    """
    Load routes.txt and filter to buses only.

    Why filter here, at the gateway: every join that follows uses the bus
    route_id set. Filtering once at the source means downstream code never
    accidentally pulls a SkyTrain trip or a SeaBus shape, and the rest of
    the module can be written as if buses are the only mode that exists.
    """
    routes_path = gtfs_dir / "routes.txt"
    df = pd.read_csv(routes_path, dtype={"route_id": str})
    logger.info(f"routes.txt — {len(df)} rows total")

    buses = df[df["route_type"] == BUS_ROUTE_TYPE].copy()
    logger.info(f"   filtered to {len(buses)} bus routes")

    buses["bus_subtype"] = buses.apply(classify_bus_subtype, axis=1)

    # Normalize route_short_name strip — GTFS files sometimes carry trailing
    # whitespace that breaks string comparisons downstream.
    buses["route_short_name"] = buses["route_short_name"].astype(str).str.strip()
    return buses


def load_bus_trips(gtfs_dir: Path, bus_route_ids: set) -> pd.DataFrame:
    """
    Load trips.txt and filter to trips that belong to a bus route.

    Why this matters: shapes.txt has thousands of shape_ids — many are
    SkyTrain/SeaBus/WCE shapes. trips.txt is the bridge that tells us
    "this shape_id belongs to bus route X". Without this join we would have
    to load every shape and re-filter later, which is wasteful.
    """
    trips_path = gtfs_dir / "trips.txt"
    # Read only the columns we need to keep memory low.
    df = pd.read_csv(
        trips_path,
        dtype={"route_id": str, "shape_id": str, "trip_id": str},
        usecols=["route_id", "trip_id", "shape_id", "direction_id", "trip_headsign"],
    )
    logger.info(f"trips.txt — {len(df):,} rows total")

    bus_trips = df[df["route_id"].isin(bus_route_ids)].copy()
    logger.info(f"   filtered to {len(bus_trips):,} bus trips")
    return bus_trips


def select_representative_shapes(
    bus_routes: pd.DataFrame,
    bus_trips: pd.DataFrame,
    gtfs_dir: Path,
) -> pd.DataFrame:
    """
    For each bus route, pick ONE shape_id to represent its corridor on a map.

    Why a single shape per route: a single TransLink bus route can have a
    dozen shape variants (different directions, short-turn patterns, late-night
    routings). Plotting all of them produces a fuzzy spaghetti that nobody
    can read. Picking the longest unique shape gives the most complete
    representation of the route's geographic footprint, which is what a
    "corridor map" needs to communicate.

    The choice of *longest* shape (rather than most-used) is deliberate:
        - Most-used favors the dominant direction, which is fine but can
          truncate one-way short-turn variants.
        - Longest shape captures the maximum extent of the route — exactly
          the visual we want for an infrastructure map.

    Returns:
        DataFrame with one row per route_id: route_id, shape_id, n_points
    """
    shapes_path = gtfs_dir / "shapes.txt"

    # Stream shapes.txt in chunks to count points per shape_id without
    # loading the whole file. ~18 MB easily fits in memory, but using
    # chunked reads keeps the pattern consistent with stop_times.txt below.
    shape_lengths = (
        pd.read_csv(shapes_path, dtype={"shape_id": str}, usecols=["shape_id"])
        .groupby("shape_id")
        .size()
        .rename("n_points")
        .reset_index()
    )
    logger.info(f"shapes.txt — {len(shape_lengths):,} distinct shape_ids")

    # Restrict to shapes that belong to at least one bus trip.
    bus_shape_ids = set(bus_trips["shape_id"].dropna().unique())
    bus_shapes = shape_lengths[shape_lengths["shape_id"].isin(bus_shape_ids)].copy()
    logger.info(f"   {len(bus_shapes):,} shapes belong to bus trips")

    # Join shape_id → route_id via trips (a shape can be shared by multiple
    # trips of the same route). Drop duplicate shape_id+route_id pairs.
    shape_to_route = bus_trips[["shape_id", "route_id"]].drop_duplicates()
    bus_shapes = bus_shapes.merge(shape_to_route, on="shape_id", how="left")

    # Pick the longest shape per route_id. Sort + drop_duplicates is a clean
    # pandas idiom for "argmax per group".
    representative = (
        bus_shapes.sort_values("n_points", ascending=False)
        .drop_duplicates(subset=["route_id"], keep="first")
        .reset_index(drop=True)
    )
    logger.info(
        f"   selected one representative shape per route — "
        f"{len(representative)} routes covered"
    )
    return representative


def load_shape_geometry(
    gtfs_dir: Path,
    representative_shapes: pd.DataFrame,
) -> pd.DataFrame:
    """
    Load the actual lat/lon points for every chosen representative shape.

    Why this is a separate function: representative shape selection only
    needs counts. Plotting needs coordinates. Splitting the two lets us
    avoid loading 600k+ lat/lon pairs into the selection step.

    Returns:
        Long-format DataFrame: (route_id, shape_id, sequence, lat, lon)
        sorted so consecutive rows trace the corridor in order.
    """
    shapes_path = gtfs_dir / "shapes.txt"
    chosen = set(representative_shapes["shape_id"].tolist())

    df = pd.read_csv(shapes_path, dtype={"shape_id": str})
    geom = df[df["shape_id"].isin(chosen)].copy()
    logger.info(f"shape geometry — {len(geom):,} points across {geom['shape_id'].nunique()} shapes")

    # Attach route_id via the representative table.
    shape_to_route = dict(
        zip(representative_shapes["shape_id"], representative_shapes["route_id"])
    )
    geom["route_id"] = geom["shape_id"].map(shape_to_route)

    # Sort so each shape traces in order — folium polylines require this.
    geom = geom.sort_values(["shape_id", "shape_pt_sequence"]).reset_index(drop=True)
    geom = geom.rename(
        columns={
            "shape_pt_lat": "lat",
            "shape_pt_lon": "lon",
            "shape_pt_sequence": "sequence",
        }
    )
    return geom[["route_id", "shape_id", "sequence", "lat", "lon"]]


def load_bus_stop_membership(
    gtfs_dir: Path,
    bus_trips: pd.DataFrame,
) -> pd.DataFrame:
    """
    Build the (route_id, stop_id) membership table from stop_times.txt.

    Why this is the hard part: stop_times.txt is the largest GTFS file by
    a wide margin (~180 MB / ~6M rows for TransLink). We must NEVER load it
    whole. The streaming pattern below reads it in chunks, projects to just
    the columns we need, filters each chunk to bus trips, and accumulates
    only the (trip_id, stop_id) pairs.

    Two-step join logic:
        1. stop_times tells us which stops belong to which TRIPS.
        2. trips.txt tells us which trips belong to which ROUTE.
        3. Therefore (stop_id, trip_id) joined with (trip_id, route_id)
           yields (stop_id, route_id).

    Returns:
        DataFrame (route_id, stop_id) — one row per route+stop edge,
        deduplicated.
    """
    stop_times_path = gtfs_dir / "stop_times.txt"
    bus_trip_ids = set(bus_trips["trip_id"].astype(str).tolist())
    logger.info(
        f"stop_times.txt — streaming in chunks of {STOP_TIMES_CHUNKSIZE:,} rows "
        f"(filtering to {len(bus_trip_ids):,} bus trips)"
    )

    chunk_iter = pd.read_csv(
        stop_times_path,
        dtype={"trip_id": str, "stop_id": str, "stop_sequence": "Int64"},
        usecols=STOP_TIMES_COLS,
        chunksize=STOP_TIMES_CHUNKSIZE,
    )

    pieces = []
    total_rows = 0
    bus_rows = 0
    for i, chunk in enumerate(chunk_iter, 1):
        total_rows += len(chunk)
        bus_chunk = chunk[chunk["trip_id"].isin(bus_trip_ids)]
        bus_rows += len(bus_chunk)
        # Keep only unique (trip_id, stop_id) within the chunk to start
        # shrinking memory immediately.
        pieces.append(bus_chunk[["trip_id", "stop_id"]].drop_duplicates())
        if i % 5 == 0:
            logger.info(f"   chunk {i}: scanned {total_rows:,} rows, kept {bus_rows:,} bus rows")

    trip_stop = pd.concat(pieces, ignore_index=True).drop_duplicates()
    logger.info(f"   stop_times scan complete: {len(trip_stop):,} unique (trip_id, stop_id) pairs")

    # Now turn (trip_id, stop_id) into (route_id, stop_id) by joining trips.
    trip_route = bus_trips[["trip_id", "route_id"]].drop_duplicates()
    route_stop = (
        trip_stop.merge(trip_route, on="trip_id", how="inner")
        .loc[:, ["route_id", "stop_id"]]
        .drop_duplicates()
        .reset_index(drop=True)
    )
    logger.info(f"   resolved to {len(route_stop):,} unique (route_id, stop_id) edges")
    return route_stop


def load_bus_stops(
    gtfs_dir: Path,
    route_stop: pd.DataFrame,
) -> pd.DataFrame:
    """
    Load stops.txt and filter to stops that are actually served by buses.

    Why filter here: stops.txt contains SkyTrain stations and other non-bus
    stops we don't care about for a bus corridor map. The route_stop edge
    table tells us exactly which stop_ids the bus network touches.
    """
    stops_path = gtfs_dir / "stops.txt"
    df = pd.read_csv(stops_path, dtype={"stop_id": str})
    logger.info(f"stops.txt — {len(df):,} rows total")

    bus_stop_ids = set(route_stop["stop_id"].unique())
    bus_stops = df[df["stop_id"].isin(bus_stop_ids)].copy()
    logger.info(f"   filtered to {len(bus_stops):,} bus stops")

    keep_cols = [c for c in ["stop_id", "stop_code", "stop_name", "stop_lat", "stop_lon"] if c in bus_stops.columns]
    return bus_stops[keep_cols].reset_index(drop=True)


# ---------------------------------------------------------------------------
# CRITICAL CORRIDOR JOIN
# ---------------------------------------------------------------------------
def build_critical_corridors(
    bus_routes: pd.DataFrame,
    route_stop: pd.DataFrame,
    representative_shapes: pd.DataFrame,
    rt_top_corridors_path: Optional[Path],
) -> pd.DataFrame:
    """
    Combine static geometry signals with the existing RT-derived activity
    ranking to produce the canonical "critical bus corridors" table.

    Why combine signals: ranking by RT activity alone tells you which routes
    are running hard *right now*. Ranking by stop count alone tells you
    which routes touch the most infrastructure. The critical-corridor
    concept needs both — a route that ranks high on operational activity
    AND touches many stops is genuinely a backbone corridor; a route high
    on only one signal might be a niche express or a long but quiet
    suburban loop.

    Returns:
        DataFrame ordered by RT activity, with stop counts attached and a
        is_critical flag for the top-N.
    """
    # Stop count per route — pure static signal.
    stop_counts = (
        route_stop.groupby("route_id").size().rename("static_stop_count").reset_index()
    )

    # Whether we have a usable shape for plotting.
    has_shape = representative_shapes.assign(has_shape=True)[["route_id", "has_shape"]]

    base = bus_routes.merge(stop_counts, on="route_id", how="left")
    base = base.merge(has_shape, on="route_id", how="left")
    base["static_stop_count"] = base["static_stop_count"].fillna(0).astype(int)
    base["has_shape"] = base["has_shape"].fillna(False)

    # If we have the RT top corridors CSV from the multimodal framework,
    # merge its activity counts in. This is the bridge between "what the
    # bus network statically looks like" and "what we actually observed".
    if rt_top_corridors_path and rt_top_corridors_path.exists():
        rt = pd.read_csv(rt_top_corridors_path, dtype={"route_id": str})
        rt_cols = [c for c in ["route_id", "total_records", "unique_vehicles", "days_active"] if c in rt.columns]
        base = base.merge(rt[rt_cols], on="route_id", how="left")
        # NaN means the route had no observed telemetry in the analysis window.
        for c in ["total_records", "unique_vehicles", "days_active"]:
            if c in base.columns:
                base[c] = base[c].fillna(0).astype(int)
        sort_col = "total_records"
        logger.info(f"   joined RT activity from {rt_top_corridors_path.name}")
    else:
        # Fall back to ranking by stop count if no RT data is on hand.
        sort_col = "static_stop_count"
        logger.warning("   no RT top_corridors.csv found — ranking by static stop count only")

    base = base.sort_values(sort_col, ascending=False).reset_index(drop=True)
    base["activity_rank"] = base.index + 1
    base["is_critical"] = base["activity_rank"] <= CRITICAL_CORRIDOR_TOP_N

    return base


# ---------------------------------------------------------------------------
# OUTPUT
# ---------------------------------------------------------------------------
def save(df: pd.DataFrame, output_dir: Path, name: str) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / f"{name}.csv"
    df.to_csv(path, index=False, encoding="utf-8")
    logger.info(f"saved {name}.csv  ({len(df):,} rows)")
    return path


# ---------------------------------------------------------------------------
# MAIN
# ---------------------------------------------------------------------------
def main():
    """
    Workflow:
        1. Load and classify bus routes.
        2. Load bus trips (route → trip → shape bridge).
        3. Select one representative shape per route and load its geometry.
        4. Build the (route_id, stop_id) edge table from stop_times.txt.
        5. Filter stops.txt to bus stops only.
        6. Combine static + RT signals into a critical-corridor ranking.
        7. Write everything to outputs/.
    """
    parser = argparse.ArgumentParser(
        description="Build bus-only static corridor geometry from GTFS Static."
    )
    parser.add_argument("--gtfs-dir", type=Path, default=DEFAULT_GTFS_DIR)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument(
        "--rt-top-corridors",
        type=Path,
        default=DEFAULT_OUTPUT_DIR / "top_corridors.csv",
        help="Path to the RT-derived top_corridors.csv from multimodal_transit_intelligence.",
    )
    args = parser.parse_args()

    if not args.gtfs_dir.exists():
        logger.error(f"GTFS directory not found: {args.gtfs_dir}")
        sys.exit(2)

    # Step 1: bus routes
    bus_routes = load_bus_routes(args.gtfs_dir)
    bus_route_ids = set(bus_routes["route_id"].astype(str).tolist())

    # Step 2: bus trips
    bus_trips = load_bus_trips(args.gtfs_dir, bus_route_ids)

    # Step 3: representative shapes + geometry
    representative_shapes = select_representative_shapes(bus_routes, bus_trips, args.gtfs_dir)
    geometry = load_shape_geometry(args.gtfs_dir, representative_shapes)

    # Step 4: route-stop membership (the heavy stop_times.txt scan)
    route_stop = load_bus_stop_membership(args.gtfs_dir, bus_trips)

    # Step 5: bus stops
    bus_stops = load_bus_stops(args.gtfs_dir, route_stop)

    # Step 6: critical corridor ranking
    critical = build_critical_corridors(
        bus_routes, route_stop, representative_shapes, args.rt_top_corridors
    )

    # Step 7: write everything
    save(critical, args.output_dir, "bus_route_catalog")
    save(representative_shapes, args.output_dir, "bus_shape_index")
    save(geometry, args.output_dir, "bus_corridor_geometry")
    save(bus_stops, args.output_dir, "bus_stops_catalog")
    save(route_stop, args.output_dir, "bus_route_stop_membership")
    critical_top = critical[critical["is_critical"]].copy()
    save(critical_top, args.output_dir, "critical_bus_corridors")

    # Console summary — quick sanity glance.
    print()
    print("=" * 70)
    print("  BUS CORRIDOR GEOMETRY — BUILD SUMMARY")
    print("=" * 70)
    print(f"  Bus routes catalogued:        {len(bus_routes)}")
    print(f"  Routes with a chosen shape:   {len(representative_shapes)}")
    print(f"  Shape geometry points loaded: {len(geometry):,}")
    print(f"  Bus stops resolved:           {len(bus_stops):,}")
    print(f"  Route×Stop edges:             {len(route_stop):,}")
    print()
    print("  Bus subtype breakdown:")
    for st, n in bus_routes["bus_subtype"].value_counts().items():
        print(f"    {st:<14} {n:>4}")
    print()
    print(f"  Top {CRITICAL_CORRIDOR_TOP_N} critical corridors:")
    cols = ["route_short_name", "route_long_name", "bus_subtype", "static_stop_count"]
    if "total_records" in critical_top.columns:
        cols.append("total_records")
    print(critical_top[cols].head(CRITICAL_CORRIDOR_TOP_N).to_string(index=False))
    print("=" * 70)


if __name__ == "__main__":
    main()
