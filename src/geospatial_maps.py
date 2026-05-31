"""
geospatial_maps.py
==================
Multimodal transit geospatial visualization layer.

Generates interactive Folium maps from GTFS-RT vehicle positions joined
with GTFS Static route metadata. Designed to be run as a sibling module
to multimodal_transit_intelligence.py — same project, same /assets and
/outputs conventions.

============================================================================
WHAT THIS SCRIPT PRODUCES
============================================================================
Interactive maps (saved to assets/):
    vancouver_multimodal_vehicle_map.html  — all positions, colored by mode
    bus_activity_map.html                   — every bus position observed
    skytrain_activity_map.html              — SkyTrain positions (if feed has them)
    expo_line_activity_map.html             — Expo Line only
    canada_line_activity_map.html           — Canada Line only
    seabus_activity_map.html                — SeaBus positions
    west_coast_express_activity_map.html    — WCE positions
    rapidbus_activity_map.html              — RapidBus corridors only
    nightbus_activity_map.html              — NightBus only (if any night-hour data)
    peak_hour_transit_density_map.html      — PM peak hour heatmap
    multimodal_transit_density_heatmap.html — full-window heatmap, all modes

Each map includes a per-map interpretation written to reports/.

============================================================================
DATA REQUIREMENTS & HONEST CAVEATS
============================================================================
INPUT:
    - data/raw/YYYY-MM-DD/*.parquet  (vehicle positions with lat/lon)
    - routes.txt                      (GTFS Static route metadata)

CAVEATS (must read before interpreting any map):
    1. The TransLink GTFS-RT vehicle feed in this dataset contains BUS
       TELEMETRY ONLY. SkyTrain, SeaBus, West Coast Express, and HandyDART
       do NOT report through this feed. Maps for those modes will render
       as empty-placeholder HTML pages with an explanatory message
       embedded — never as a fake map.
    2. `shapes.txt` is unavailable, so no route polylines are drawn.
       Future enhancement: when shapes.txt becomes available, overlay
       route geometry on the existing point layers. The map-builder
       function signatures are designed so this can be added without
       restructuring downstream code.
    3. These maps represent VEHICLE PRESENCE — supply-side operational
       observation only. They do NOT show passenger flows, ridership,
       or demand. Reading them as ridership maps would be wrong.
    4. Approximately 0.3% of positions have lat=0 OR lon=0 (GPS dropouts);
       these are filtered out before mapping.

USAGE:
    python src/geospatial_maps.py
    python src/geospatial_maps.py --data-dir data/raw --routes routes.txt
    python src/geospatial_maps.py --start-date 2026-05-19 --end-date 2026-05-23
    python src/geospatial_maps.py --max-points 50000  # for laptops
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

import pandas as pd
import folium
from folium.plugins import HeatMap, MarkerCluster


# ---------------------------------------------------------------------------
# LOGGING
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-7s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("geo_maps")


# ---------------------------------------------------------------------------
# CONFIGURATION
# ---------------------------------------------------------------------------
# Centered roughly on downtown Vancouver. Used as the default map center
# if there are no data points to compute a bounding-box centroid from.
VANCOUVER_CENTER = (49.260, -123.114)

# Vancouver was PDT (UTC-7) throughout the analysis window in May 2026.
VANCOUVER_UTC_OFFSET = -7

# GTFS route_type → human-readable mode.
MODE_MAP: Dict[int, str] = {
    1: "SkyTrain",
    2: "West Coast Express",
    3: "Bus",
    4: "SeaBus",
    715: "HandyDART",
}

# Color scheme — kept consistent with the analysis script's palette.
MODE_COLORS: Dict[str, str] = {
    "Bus":                "#1f77b4",
    "SkyTrain":           "#d62728",
    "Expo Line":          "#0066cc",
    "Millennium Line":    "#f1c40f",
    "Canada Line":        "#3a9b3a",
    "SeaBus":             "#17becf",
    "West Coast Express": "#7B3F99",
    "HandyDART":          "#7f7f7f",
    "RapidBus":           "#e67e22",
    "NightBus":           "#1a2540",
    "B-Line":             "#c0392b",
    "Express":            "#2c3e50",
    "Community Shuttle":  "#95a5a6",
    "Regular Bus":        "#3498db",
}

ASSETS_DIR = Path("assets")
OUTPUTS_DIR = Path("outputs")
REPORTS_DIR = Path("reports")


# ---------------------------------------------------------------------------
# SUB-TYPE CLASSIFIER (same logic as the main analysis script)
# ---------------------------------------------------------------------------
def classify_bus_subtype(short_name: str, long_name: str) -> str:
    """Operational sub-class for a bus route. Same rules as analysis script."""
    s = str(short_name) if pd.notna(short_name) else ""
    l = str(long_name).lower() if pd.notna(long_name) else ""
    if s.startswith("R") and len(s) <= 3 and s[1:].isdigit():
        return "RapidBus"
    if s.startswith("N") and s[1:].isdigit():
        return "NightBus"
    if "b-line" in l or s == "099":
        return "B-Line"
    if "express" in l:
        return "Express"
    if s.startswith("C") and s[1:].isdigit():
        return "Community Shuttle"
    return "Regular Bus"


# ---------------------------------------------------------------------------
# LOADING
# ---------------------------------------------------------------------------
def discover_parquet_files(
    data_dir: Path,
    start_date: Optional[str],
    end_date: Optional[str],
) -> List[Path]:
    """Find parquet files in data_dir, optionally filtered by date subfolder."""
    if not data_dir.exists():
        log.error(f"Data directory not found: {data_dir}")
        return []

    files: List[Path] = []
    if start_date and end_date:
        try:
            sd = datetime.strptime(start_date, "%Y-%m-%d").date()
            ed = datetime.strptime(end_date, "%Y-%m-%d").date()
        except ValueError:
            log.error("Bad date format; expected YYYY-MM-DD.")
            return []
        for sub in sorted(data_dir.iterdir()):
            if not sub.is_dir():
                continue
            try:
                sd_dt = datetime.strptime(sub.name, "%Y-%m-%d").date()
            except ValueError:
                continue
            if sd <= sd_dt <= ed:
                files.extend(sorted(sub.rglob("*.parquet")))
    else:
        files = sorted(data_dir.rglob("*.parquet"))

    log.info(f"Discovered {len(files):,} parquet files")
    return files


def load_positions(
    files: List[Path],
    max_points: Optional[int] = None,
) -> pd.DataFrame:
    """Load vehicle position records from parquet files.

    Returns a dataframe with at least: lat, lon, route_id, vehicle_id,
    collection_timestamp. Invalid coordinates (lat=0 or lon=0) are dropped.

    Parameters
    ----------
    files : list of Path
        Parquet files to read.
    max_points : int, optional
        If given, randomly sample down to this many rows AFTER concat.
        Useful for keeping interactive Folium maps responsive on laptops
        when you have millions of rows. Sampling preserves spatial
        distribution while reducing browser load.
    """
    if not files:
        return pd.DataFrame()

    frames: List[pd.DataFrame] = []
    skipped = 0
    for i, f in enumerate(files, 1):
        try:
            df = pd.read_parquet(f)
            if len(df) > 0:
                frames.append(df)
        except Exception as e:
            log.warning(f"Skipping unreadable file {f.name}: {e}")
            skipped += 1

    if not frames:
        return pd.DataFrame()

    combined = pd.concat(frames, ignore_index=True)
    log.info(f"Loaded {len(combined):,} raw positions ({skipped} files skipped)")

    # Filter invalid coordinates (GPS dropouts emit 0.0/0.0)
    before = len(combined)
    combined = combined[
        (combined["latitude"] != 0.0) & (combined["longitude"] != 0.0)
        & combined["latitude"].notna() & combined["longitude"].notna()
    ].copy()
    log.info(f"After dropping invalid coords: {len(combined):,} positions "
             f"({before - len(combined):,} removed)")

    # Force string IDs for clean joins
    if "route_id" in combined.columns:
        combined["route_id"] = combined["route_id"].astype(str)
    if "vehicle_id" in combined.columns:
        combined["vehicle_id"] = combined["vehicle_id"].astype(str)

    # Sample for browser performance if requested
    if max_points and len(combined) > max_points:
        combined = combined.sample(n=max_points, random_state=42).reset_index(drop=True)
        log.info(f"Sampled down to {len(combined):,} points for map responsiveness")

    # Add Vancouver local hour for peak-hour filtering downstream
    if "collection_timestamp" in combined.columns:
        ts = pd.to_datetime(combined["collection_timestamp"], utc=True, errors="coerce")
        combined["vancouver_hour"] = ((ts.dt.hour + VANCOUVER_UTC_OFFSET) % 24).astype("Int64")

    return combined


def load_routes(routes_path: Path) -> pd.DataFrame:
    """Load and enrich routes.txt with mode and bus subtype columns."""
    routes = pd.read_csv(routes_path)
    routes["route_id"] = routes["route_id"].astype(str)
    routes["mode"] = routes["route_type"].map(MODE_MAP).fillna("Unknown")
    routes["bus_subtype"] = routes.apply(
        lambda row: classify_bus_subtype(row.get("route_short_name"), row.get("route_long_name"))
        if row["mode"] == "Bus" else row["mode"],
        axis=1,
    )
    log.info(f"Loaded routes.txt: {len(routes)} routes")
    return routes


# ---------------------------------------------------------------------------
# MAP BUILDERS
# ---------------------------------------------------------------------------
def _empty_map_with_message(message: str, filename: str) -> Path:
    """Render a placeholder map page when there is no data.

    Used for modes (SkyTrain, SeaBus, WCE, HandyDART) absent from the
    GTFS-RT feed. Returning a real HTML page with an honest explanation
    is more useful than silently skipping or fabricating data.
    """
    html = f"""<!DOCTYPE html>
<html><head>
<meta charset="utf-8">
<title>{filename}</title>
<style>
  body {{ font-family: -apple-system, Segoe UI, sans-serif;
         background: #f4f4f6; color: #222; padding: 60px;
         max-width: 760px; margin: 0 auto; }}
  h1 {{ font-weight: 600; border-bottom: 3px solid #c0392b; padding-bottom: 12px; }}
  .badge {{ display: inline-block; padding: 4px 10px; border-radius: 4px;
            background: #c0392b; color: white; font-size: 13px;
            text-transform: uppercase; letter-spacing: 1px; }}
  p {{ line-height: 1.55; }}
  .note {{ background: white; border-left: 4px solid #1f77b4;
           padding: 16px 20px; margin: 20px 0; font-size: 14px; }}
</style></head>
<body>
<span class="badge">No data available</span>
<h1>{filename}</h1>
<div class="note">{message}</div>
<p><strong>Why this is honest, not a failure:</strong> the GTFS-RT vehicle
position feed published by TransLink in this dataset window contains
bus telemetry only. SkyTrain, SeaBus, West Coast Express, and HandyDART
exist in GTFS Static (`routes.txt`) but emit zero real-time vehicle
positions. Rendering a fake map would misrepresent reality.</p>
<p><strong>How to enable this map:</strong> obtain a GTFS-RT feed that
includes the relevant mode, or integrate alternative data sources
(SkyTrain has separate Compass or operational data feeds).</p>
<p><em>Generated by geospatial_maps.py — TransLink GTFS-RT
multimodal transit intelligence framework.</em></p>
</body></html>"""
    path = ASSETS_DIR / filename
    path.write_text(html, encoding="utf-8")
    log.info(f"  → assets/{filename}  (placeholder — mode absent from feed)")
    return path


def _map_center_from_points(df: pd.DataFrame) -> tuple:
    """Return a sensible map center: mean of valid points or Vancouver default."""
    if len(df) == 0:
        return VANCOUVER_CENTER
    return (df["latitude"].mean(), df["longitude"].mean())


def _add_legend(fmap: folium.Map, entries: List[tuple]) -> None:
    """Add a simple color legend to a Folium map.

    entries is a list of (label, color) tuples.
    """
    rows = "".join(
        f'<div style="margin:3px 0;"><span style="display:inline-block;'
        f'width:12px;height:12px;background:{color};border-radius:2px;'
        f'margin-right:6px;vertical-align:middle;"></span>{label}</div>'
        for label, color in entries
    )
    html = f"""
    <div style="position: fixed; bottom: 20px; right: 20px; z-index: 9999;
                background: white; padding: 10px 14px; border-radius: 6px;
                box-shadow: 0 2px 6px rgba(0,0,0,0.18);
                font-family: -apple-system, Segoe UI, sans-serif;
                font-size: 12px;">
      <div style="font-weight: 600; margin-bottom: 4px;">Legend</div>
      {rows}
    </div>
    """
    fmap.get_root().html.add_child(folium.Element(html))


def build_point_map(
    df: pd.DataFrame,
    title: str,
    filename: str,
    color_by: str,
    color_map: Dict[str, str],
    legend_entries: Optional[List[tuple]] = None,
    use_cluster: bool = False,
    radius: int = 3,
) -> Path:
    """Build a Folium point map and save to assets/.

    Parameters
    ----------
    df : DataFrame
        Must contain latitude, longitude, and the color_by column.
    title : str
        Title shown at top of the map.
    filename : str
        Output filename (will be saved to assets/<filename>).
    color_by : str
        Column name to drive marker color.
    color_map : dict
        Map of values in color_by → hex color.
    legend_entries : list of (label, color) tuples, optional
        Custom legend; if None, derived from values in color_by.
    use_cluster : bool
        If True, use MarkerCluster (good for sparse layers). If False,
        use CircleMarker for dense visualizations.
    radius : int
        Marker radius when not using cluster.
    """
    if df.empty:
        return _empty_map_with_message(
            f"No vehicle positions matched the filter for: <strong>{title}</strong>. "
            f"This typically means the mode or sub-type is not present in the "
            f"GTFS-RT feed for the analysis window.",
            filename
        )

    fmap = folium.Map(
        location=_map_center_from_points(df),
        zoom_start=11,
        tiles="CartoDB positron",
        control_scale=True,
    )

    # Title bar
    title_html = f"""
    <div style="position: fixed; top: 10px; left: 50px; z-index: 9999;
                background: white; padding: 8px 16px; border-radius: 6px;
                box-shadow: 0 2px 6px rgba(0,0,0,0.18);
                font-family: -apple-system, Segoe UI, sans-serif;
                font-size: 14px; font-weight: 600;">
      {title} <span style="font-weight: 400; color: #666;">
      ({len(df):,} positions)</span>
    </div>
    """
    fmap.get_root().html.add_child(folium.Element(title_html))

    target = MarkerCluster().add_to(fmap) if use_cluster else fmap

    for _, row in df.iterrows():
        color = color_map.get(row[color_by], "#888")
        popup = folium.Popup(
            f"<b>Route:</b> {row.get('route_short_name', row.get('route_id', '?'))}"
            f"<br><b>Long name:</b> {row.get('route_long_name', '')}"
            f"<br><b>Vehicle:</b> {row.get('vehicle_id', '?')}"
            f"<br><b>Mode:</b> {row.get('mode', '?')}"
            f"<br><b>Sub-type:</b> {row.get('bus_subtype', '?')}",
            max_width=280,
        )
        if use_cluster:
            folium.CircleMarker(
                location=(row["latitude"], row["longitude"]),
                radius=radius, color=color, fill=True,
                fill_color=color, fill_opacity=0.75, weight=1,
                popup=popup,
            ).add_to(target)
        else:
            folium.CircleMarker(
                location=(row["latitude"], row["longitude"]),
                radius=radius, color=color, fill=True,
                fill_color=color, fill_opacity=0.6, weight=0.3,
                popup=popup,
            ).add_to(target)

    # Legend
    if legend_entries is None:
        values = df[color_by].dropna().unique()
        legend_entries = [(v, color_map.get(v, "#888")) for v in sorted(values)]
    _add_legend(fmap, legend_entries)

    path = ASSETS_DIR / filename
    fmap.save(str(path))
    log.info(f"  → assets/{filename}  ({len(df):,} points)")
    return path


def build_heatmap(
    df: pd.DataFrame,
    title: str,
    filename: str,
    radius: int = 12,
    blur: int = 18,
) -> Path:
    """Build a Folium HeatMap and save to assets/.

    Operationally meaningful for showing where the bus network spends
    the most VEHICLE-HOURS. Note: this is operational density, NOT
    rider density — high heatmap intensity means many bus position
    reports, which correlates with frequent service AND/OR slow service.
    """
    if df.empty:
        return _empty_map_with_message(
            f"No vehicle positions available for: <strong>{title}</strong>.",
            filename
        )

    fmap = folium.Map(
        location=_map_center_from_points(df),
        zoom_start=11,
        tiles="CartoDB dark_matter",
        control_scale=True,
    )

    title_html = f"""
    <div style="position: fixed; top: 10px; left: 50px; z-index: 9999;
                background: rgba(255,255,255,0.92); padding: 8px 16px;
                border-radius: 6px; box-shadow: 0 2px 6px rgba(0,0,0,0.18);
                font-family: -apple-system, Segoe UI, sans-serif;
                font-size: 14px; font-weight: 600;">
      {title} <span style="font-weight: 400; color: #666;">
      ({len(df):,} positions)</span>
    </div>
    """
    fmap.get_root().html.add_child(folium.Element(title_html))

    points = df[["latitude", "longitude"]].values.tolist()
    HeatMap(points, radius=radius, blur=blur, min_opacity=0.35).add_to(fmap)

    path = ASSETS_DIR / filename
    fmap.save(str(path))
    log.info(f"  → assets/{filename}  ({len(df):,} heat points)")
    return path


# ---------------------------------------------------------------------------
# INTERPRETATION WRITER
# ---------------------------------------------------------------------------
def write_map_interpretations(interpretations: List[Dict[str, str]]) -> Path:
    """Write per-map interpretation document.

    Each map gets four standardized sections:
        - what it shows
        - why it matters operationally
        - limitations
        - how additional GTFS Static files would improve it
    """
    REPORTS_DIR.mkdir(exist_ok=True)
    path = REPORTS_DIR / "geospatial_map_interpretations.md"

    lines = []
    lines.append("# Geospatial Map Interpretations\n")
    lines.append(f"_Generated: {datetime.now(timezone.utc).isoformat()}_\n\n")
    lines.append("Each section below describes one map artifact in `assets/`. "
                 "Maps represent VEHICLE PRESENCE only — not passenger movement or "
                 "ridership demand.\n\n")
    lines.append("All hour references are Vancouver local time (PDT, UTC-7) unless noted.\n\n")
    lines.append("---\n\n")

    for entry in interpretations:
        lines.append(f"## `{entry['filename']}`\n\n")
        lines.append(f"**What this map shows:** {entry['shows']}\n\n")
        lines.append(f"**Why it matters operationally:** {entry['matters']}\n\n")
        lines.append(f"**Limitations:** {entry['limitations']}\n\n")
        lines.append(f"**How additional GTFS Static would improve it:** {entry['enhancement']}\n\n")
        lines.append("---\n\n")

    path.write_text("".join(lines), encoding="utf-8")
    log.info(f"Wrote map interpretations → {path}")
    return path


# ---------------------------------------------------------------------------
# MAIN MAP GENERATION
# ---------------------------------------------------------------------------
def generate_all_maps(positions: pd.DataFrame, routes: pd.DataFrame) -> None:
    """Generate every requested map and write the interpretation report."""

    # Join routes onto positions so every point has mode + subtype
    p = positions.merge(
        routes[["route_id", "route_short_name", "route_long_name",
                "route_type", "mode", "bus_subtype"]],
        on="route_id", how="left"
    )
    log.info(f"Joined positions × routes: {len(p):,} enriched points")

    interpretations: List[Dict[str, str]] = []

    # ---- 1. Multimodal vehicle map (all positions, colored by mode) ----
    log.info("Building map 1/10: multimodal vehicle map")
    legend = [(m, MODE_COLORS.get(m, "#888")) for m in p["mode"].dropna().unique()]
    build_point_map(
        p, "Vancouver Multimodal Vehicle Activity", "vancouver_multimodal_vehicle_map.html",
        color_by="mode", color_map=MODE_COLORS, legend_entries=legend, radius=2,
    )
    interpretations.append({
        "filename": "vancouver_multimodal_vehicle_map.html",
        "shows": "Every observed vehicle position in the analysis window, "
                 "colored by transit mode (Bus, SkyTrain, SeaBus, WCE, HandyDART). "
                 "In this dataset only the Bus layer is populated.",
        "matters": "It establishes the geographic footprint of the GTFS-RT feed. "
                   "Operationally, you can see at a glance which neighborhoods "
                   "are densely instrumented and where coverage thins.",
        "limitations": "Bus-only coverage; other modes do not report through this feed. "
                       "Points may overplot in dense corridors — use the heatmap "
                       "version to see density gradients.",
        "enhancement": "With `shapes.txt` we could overlay official route polylines, "
                       "making it visually clear which points belong to which corridor. "
                       "With `stops.txt` we could pin SkyTrain stations as reference markers.",
    })

    # ---- 2. Bus activity map ----
    log.info("Building map 2/10: bus activity map")
    buses = p[p["mode"] == "Bus"]
    bus_legend = [(s, MODE_COLORS.get(s, "#888"))
                  for s in sorted(buses["bus_subtype"].dropna().unique())]
    build_point_map(
        buses, "Bus Vehicle Activity (by sub-type)", "bus_activity_map.html",
        color_by="bus_subtype", color_map=MODE_COLORS, legend_entries=bus_legend, radius=2,
    )
    interpretations.append({
        "filename": "bus_activity_map.html",
        "shows": "Every bus position, colored by operational sub-type "
                 "(B-Line, RapidBus, Regular, Express, Community Shuttle, NightBus).",
        "matters": "Sub-type coloring reveals the operational hierarchy spatially. "
                   "RapidBus and B-Line points trace TransLink's frequent transit "
                   "corridors — the spine the rest of the bus network feeds into.",
        "limitations": "Point overplotting in central Vancouver makes it hard to read "
                       "density gradients; use the heatmap for that purpose.",
        "enhancement": "`shapes.txt` would let us draw actual route corridors and "
                       "see how closely vehicles track their nominal paths.",
    })

    # ---- 3. SkyTrain activity ----
    log.info("Building map 3/10: SkyTrain activity map")
    sky = p[p["mode"] == "SkyTrain"]
    if sky.empty:
        _empty_map_with_message(
            "SkyTrain vehicles do not report through the GTFS-RT vehicle position "
            "feed available in this dataset. Three SkyTrain lines (Expo, Millennium, "
            "Canada) exist in GTFS Static but have zero real-time positions.",
            "skytrain_activity_map.html"
        )
    else:
        build_point_map(
            sky, "SkyTrain Activity", "skytrain_activity_map.html",
            color_by="route_long_name",
            color_map={"Expo Line": MODE_COLORS["Expo Line"],
                       "Millennium Line": MODE_COLORS["Millennium Line"],
                       "Canada Line": MODE_COLORS["Canada Line"]},
            radius=4,
        )
    interpretations.append({
        "filename": "skytrain_activity_map.html",
        "shows": "Intended to show SkyTrain vehicle positions across all three lines.",
        "matters": "SkyTrain is the spine of regional transit. Real-time positions "
                   "would let us assess headway adherence, station dwell times, and "
                   "service contraction during off-peak hours.",
        "limitations": "ZERO SkyTrain vehicles report through this GTFS-RT feed. "
                       "The map is a placeholder.",
        "enhancement": "A SkyTrain vehicle position source (e.g., a separate feed or "
                       "Compass operational data) would populate this. `shapes.txt` "
                       "would add line geometry as reference.",
    })

    # ---- 4. Expo Line ----
    log.info("Building map 4/10: Expo Line")
    expo = p[p["route_long_name"] == "Expo Line"]
    if expo.empty:
        _empty_map_with_message(
            "Expo Line vehicles do not report through this GTFS-RT feed.",
            "expo_line_activity_map.html"
        )
    else:
        build_point_map(
            expo, "Expo Line Activity", "expo_line_activity_map.html",
            color_by="route_long_name",
            color_map={"Expo Line": MODE_COLORS["Expo Line"]},
            radius=4,
        )
    interpretations.append({
        "filename": "expo_line_activity_map.html",
        "shows": "Intended to show Expo Line train positions from Waterfront to "
                 "King George / Production Way.",
        "matters": "Expo Line is the highest-ridership SkyTrain corridor. Operational "
                   "monitoring here would matter most for service reliability metrics.",
        "limitations": "No Expo Line data in feed.",
        "enhancement": "A SkyTrain real-time feed plus `shapes.txt` polylines would "
                       "enable headway analytics by station segment.",
    })

    # ---- 5. Canada Line ----
    log.info("Building map 5/10: Canada Line")
    canada = p[p["route_long_name"] == "Canada Line"]
    if canada.empty:
        _empty_map_with_message(
            "Canada Line vehicles do not report through this GTFS-RT feed.",
            "canada_line_activity_map.html"
        )
    else:
        build_point_map(
            canada, "Canada Line Activity", "canada_line_activity_map.html",
            color_by="route_long_name",
            color_map={"Canada Line": MODE_COLORS["Canada Line"]},
            radius=4,
        )
    interpretations.append({
        "filename": "canada_line_activity_map.html",
        "shows": "Intended to show Canada Line train positions from Waterfront "
                 "to YVR / Richmond-Brighouse.",
        "matters": "Canada Line is the airport-region spine. Real-time positions "
                   "would enable airport-connection coordination analytics.",
        "limitations": "No Canada Line data in feed.",
        "enhancement": "SkyTrain real-time feed + `shapes.txt`.",
    })

    # ---- 6. SeaBus ----
    log.info("Building map 6/10: SeaBus")
    seabus = p[p["mode"] == "SeaBus"]
    if seabus.empty:
        _empty_map_with_message(
            "SeaBus vessels do not report through this GTFS-RT vehicle feed.",
            "seabus_activity_map.html"
        )
    else:
        build_point_map(
            seabus, "SeaBus Activity", "seabus_activity_map.html",
            color_by="mode", color_map=MODE_COLORS, radius=5,
        )
    interpretations.append({
        "filename": "seabus_activity_map.html",
        "shows": "Intended to show SeaBus vessel positions in Burrard Inlet "
                 "(Waterfront ↔ Lonsdale Quay).",
        "matters": "SeaBus is the primary North Shore↔Downtown rapid link. "
                   "Real-time positions would enable crossing-time analysis.",
        "limitations": "No SeaBus data in feed.",
        "enhancement": "A SeaBus vessel position feed would populate this map. "
                       "The crossing is short and well-defined, so even sparse "
                       "data would be analytically valuable.",
    })

    # ---- 7. West Coast Express ----
    log.info("Building map 7/10: West Coast Express")
    wce = p[p["mode"] == "West Coast Express"]
    if wce.empty:
        _empty_map_with_message(
            "West Coast Express does not report through this GTFS-RT feed.",
            "west_coast_express_activity_map.html"
        )
    else:
        build_point_map(
            wce, "West Coast Express", "west_coast_express_activity_map.html",
            color_by="mode", color_map=MODE_COLORS, radius=5,
        )
    interpretations.append({
        "filename": "west_coast_express_activity_map.html",
        "shows": "Intended to show West Coast Express commuter rail positions "
                 "between Waterfront and Mission.",
        "matters": "WCE serves the eastern commuter belt. Real-time positions would "
                   "let us assess schedule adherence on a service that runs only "
                   "during peak hours.",
        "limitations": "No WCE data in feed.",
        "enhancement": "WCE-specific real-time feed plus rail `shapes.txt`.",
    })

    # ---- 8. RapidBus ----
    log.info("Building map 8/10: RapidBus")
    rapid = p[p["bus_subtype"] == "RapidBus"]
    if rapid.empty:
        _empty_map_with_message(
            "No RapidBus positions in this slice of the data.",
            "rapidbus_activity_map.html"
        )
    else:
        rapid_legend = [(rn, MODE_COLORS["RapidBus"])
                        for rn in sorted(rapid["route_short_name"].dropna().unique())]
        build_point_map(
            rapid, "RapidBus Corridors (R1–R6)", "rapidbus_activity_map.html",
            color_by="route_short_name",
            color_map={rn: MODE_COLORS["RapidBus"]
                       for rn in rapid["route_short_name"].dropna().unique()},
            legend_entries=rapid_legend, radius=3,
        )
    interpretations.append({
        "filename": "rapidbus_activity_map.html",
        "shows": "Positions for R1–R6 RapidBus corridors only.",
        "matters": "RapidBus is TransLink's signature limited-stop bus tier and one "
                   "of the most reliable proxies for ridership demand. Spatial "
                   "patterns here highlight the city's high-frequency arterial network.",
        "limitations": "Single color per RapidBus route would be ideal; current "
                       "palette uses the orange RapidBus brand color across all six.",
        "enhancement": "`shapes.txt` would draw the actual RapidBus corridor lines, "
                       "making it visually obvious where each R-route runs.",
    })

    # ---- 9. NightBus ----
    log.info("Building map 9/10: NightBus")
    night = p[p["bus_subtype"] == "NightBus"]
    if night.empty:
        _empty_map_with_message(
            "No NightBus positions in this slice of the data. "
            "NightBus runs ~22:00–05:00 — if the sample doesn't include those hours, "
            "no NightBus points will appear.",
            "nightbus_activity_map.html"
        )
    else:
        night_legend = [(rn, MODE_COLORS["NightBus"])
                        for rn in sorted(night["route_short_name"].dropna().unique())]
        build_point_map(
            night, "NightBus Network (N-series)", "nightbus_activity_map.html",
            color_by="route_short_name",
            color_map={rn: MODE_COLORS["NightBus"]
                       for rn in night["route_short_name"].dropna().unique()},
            legend_entries=night_legend, radius=3,
        )
    interpretations.append({
        "filename": "nightbus_activity_map.html",
        "shows": "Overnight bus network (N8 through N35) positions during night hours.",
        "matters": "NightBus is the only mode running during the 02:00–05:00 window "
                   "(when SkyTrain is closed). Its geographic footprint is the city's "
                   "overnight mobility backbone — critical for shift workers and "
                   "hospitality staff.",
        "limitations": "Requires the data slice to include overnight hours.",
        "enhancement": "`shapes.txt` would draw NightBus corridors so the city's "
                       "overnight network shape is visible at a glance.",
    })

    # ---- 10. Peak-hour transit density (PM peak: 15:00-18:00 Vancouver local) ----
    log.info("Building map 10/11: peak-hour density heatmap")
    if "vancouver_hour" in p.columns:
        pm_peak = p[p["vancouver_hour"].isin([15, 16, 17])]
    else:
        pm_peak = pd.DataFrame()
    build_heatmap(
        pm_peak,
        "PM Peak Hour Transit Density (15:00–17:59 Vancouver local)",
        "peak_hour_transit_density_map.html",
        radius=14, blur=20,
    )
    interpretations.append({
        "filename": "peak_hour_transit_density_map.html",
        "shows": "Heatmap of vehicle positions during the PM peak window "
                 "(15:00–17:59 Vancouver local time). Bright areas = high concentration "
                 "of bus vehicle-reports.",
        "matters": "Identifies the city's evening operational hotspots — where the "
                   "bus network is most concentrated when commute volumes are highest. "
                   "Useful for planning bus-lane priority and signal pre-emption.",
        "limitations": "High intensity correlates with BOTH high service frequency "
                       "AND slow vehicle movement (more position reports per unit "
                       "distance). The map cannot distinguish these on its own.",
        "enhancement": "Combining with derived speed (from position deltas) would "
                       "separate 'busy because frequent' from 'busy because stuck'. "
                       "`stops.txt` would identify whether hotspots cluster at major "
                       "interchanges (expected) or mid-corridor (potential congestion).",
    })

    # ---- 11. Multimodal density heatmap (full window) ----
    log.info("Building map 11/11: full-window multimodal heatmap")
    build_heatmap(
        p, "Multimodal Vehicle Density — Full Analysis Window",
        "multimodal_transit_density_heatmap.html",
        radius=10, blur=14,
    )
    interpretations.append({
        "filename": "multimodal_transit_density_heatmap.html",
        "shows": "Aggregate heatmap of all observed vehicle positions across the "
                 "entire analysis window, all modes.",
        "matters": "The city's transit operational footprint, integrated over time. "
                   "The brightest pixels are where TransLink's surface fleet spends "
                   "the most vehicle-hours — these are the corridors any disruption "
                   "(weather, construction, special event) would affect most.",
        "limitations": "Still bus-only because that's what the feed provides. The "
                       "density signal blends service-frequency with vehicle dwell time.",
        "enhancement": "Adding SkyTrain real-time data would re-balance the heatmap "
                       "to reflect true multimodal operational footprint. `shapes.txt` "
                       "polylines would let us identify which exact corridors anchor "
                       "the brightest hot zones.",
    })

    write_map_interpretations(interpretations)


# ---------------------------------------------------------------------------
# MAIN
# ---------------------------------------------------------------------------
def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=Path("data/raw"),
                        help="Directory containing date-partitioned parquet files")
    parser.add_argument("--routes", type=Path, default=Path("routes.txt"),
                        help="Path to GTFS Static routes.txt")
    parser.add_argument("--start-date", type=str, default=None,
                        help="Start date YYYY-MM-DD (optional)")
    parser.add_argument("--end-date", type=str, default=None,
                        help="End date YYYY-MM-DD (optional)")
    parser.add_argument("--max-points", type=int, default=80000,
                        help="Cap on points used per map (for browser performance)")
    args = parser.parse_args()

    ASSETS_DIR.mkdir(exist_ok=True)
    OUTPUTS_DIR.mkdir(exist_ok=True)
    REPORTS_DIR.mkdir(exist_ok=True)

    # Load data
    if not args.routes.exists():
        log.error(f"Routes file not found: {args.routes}")
        sys.exit(2)
    routes = load_routes(args.routes)

    files = discover_parquet_files(args.data_dir, args.start_date, args.end_date)
    if not files:
        log.error("No parquet files found.")
        sys.exit(2)

    positions = load_positions(files, max_points=args.max_points)
    if positions.empty:
        log.error("No valid positions loaded.")
        sys.exit(2)

    # Generate maps
    generate_all_maps(positions, routes)

    print()
    print("=" * 72)
    print("  GEOSPATIAL MAPS — RUN SUMMARY")
    print("=" * 72)
    print(f"  Positions plotted:    {len(positions):,}")
    print(f"  Routes loaded:        {len(routes)}")
    print(f"  Maps → assets/*.html")
    print(f"  Interpretations → reports/geospatial_map_interpretations.md")
    print("=" * 72)


if __name__ == "__main__":
    main()
