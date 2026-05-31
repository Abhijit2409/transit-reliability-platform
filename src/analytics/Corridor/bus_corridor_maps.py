"""
bus_corridor_maps.py
====================
Interactive bus-corridor visualization layer.

Reads the static geometry CSVs produced by bus_corridor_geometry.py and
renders the seven canonical bus infrastructure maps the framework now
publishes. This is the visual upgrade that turns RT-point-only maps into
real corridor maps with stops, hierarchy, and infrastructure context.

What this module produces (all in assets/)
------------------------------------------
    critical_bus_corridors_map.html
        Every bus corridor drawn at low opacity, with the top-N critical
        corridors highlighted on top. The "spinal column" of the network.

    top10_bus_routes_with_stops_map.html
        The 10 highest-activity bus corridors with every stop they serve
        rendered as a marker. The most operationally important infrastructure
        in the bus network, at full resolution.

    rapidbus_corridors_map.html
        The six RapidBus corridors (R1–R6), TransLink's frequent-service
        backbone, in their official green livery.

    route_99_bline_corridor_map.html
        The 99 B-Line corridor, with every stop and a special "headline"
        styling. The 99 alone carries more bus telemetry than any other
        route in the system.

    nightbus_corridors_map.html
        All N-prefixed overnight corridors, plotted as a single network so
        you can see how the night service knits the city together.

    major_bus_corridors_map.html
        Top critical regular and express bus corridors (excluding RapidBus
        / B-Line which get their own dedicated maps), colored by subtype.

    bus_telemetry_vs_static_corridors_map.html
        Overlay: static corridor lines underneath, RT vehicle positions on
        top. This is the headline image of the upgrade — it makes the
        separation between "infrastructure" and "live telemetry" visually
        explicit.

Design conventions
------------------
- TransLink RapidBus color (#008522, green) is preserved for RapidBus.
- 99 B-Line uses its official livery color (#d04110, terracotta).
- Default basemap is CartoDB Positron — neutral, doesn't compete with
  colored corridor lines.
- Every map has a Folium LayerControl when more than one layer is present,
  so a reader can toggle corridor vs stop layers independently.
- Maps that show stops use small CircleMarkers, not default pin icons,
  because the stop counts (~8,600 system-wide) make pin clustering
  necessary anyway.

Scope honesty
-------------
This module renders BUS-only static infrastructure. SkyTrain / SeaBus /
WCE shapes are excluded by the geometry layer above. The RT overlay map
also draws only bus telemetry; no fake rail movement is fabricated.

Usage (from project root):
    python src/bus_corridor_maps.py
    python src/bus_corridor_maps.py --rt-positions data/raw/2026-05-09
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path
from typing import Dict, Iterable, List, Optional

import folium
import pandas as pd
from folium import plugins


# ---------------------------------------------------------------------------
# CONFIGURATION
# ---------------------------------------------------------------------------
DEFAULT_OUTPUT_DIR = Path("outputs")
DEFAULT_ASSETS_DIR = Path("assets")

# Map center — covers Metro Vancouver's bus service area comfortably.
VANCOUVER_CENTER = (49.246, -123.041)
DEFAULT_ZOOM = 11

# Subtype color palette — chosen to match the existing framework's
# multimodal palette as closely as possible while remaining colorblind-
# distinguishable on a CartoDB Positron base.
SUBTYPE_COLORS: Dict[str, str] = {
    "B-Line":      "#d04110",   # official 99 B-Line terracotta
    "RapidBus":    "#008522",   # official TransLink RapidBus green
    "NightBus":    "#3b1f5e",   # deep indigo — night theme
    "Express":     "#2a6fb0",   # cool blue
    "Regular Bus": "#737373",   # neutral grey — defers visually to the others
}
DEFAULT_LINE_WEIGHT = 3
HIGHLIGHT_LINE_WEIGHT = 5
BACKGROUND_LINE_WEIGHT = 1.5
BACKGROUND_LINE_OPACITY = 0.18

# How many corridors to highlight on the "critical" and "top 10" maps.
TOP_N_CRITICAL = 20
TOP_N_HEADLINE = 10


# ---------------------------------------------------------------------------
# LOGGING
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-7s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("bus_corridor_maps")


# ---------------------------------------------------------------------------
# DATA LOADING
# ---------------------------------------------------------------------------
def load_geometry_outputs(output_dir: Path) -> Dict[str, pd.DataFrame]:
    """
    Load all six geometry CSVs produced by bus_corridor_geometry.py.

    Why one loader: every map below needs some subset of these tables.
    Loading them once and passing dicts of dataframes around keeps each
    map function clean and removes repeated I/O.
    """
    required = [
        "bus_route_catalog",
        "bus_corridor_geometry",
        "bus_stops_catalog",
        "bus_route_stop_membership",
        "critical_bus_corridors",
    ]
    out: Dict[str, pd.DataFrame] = {}
    for name in required:
        path = output_dir / f"{name}.csv"
        if not path.exists():
            logger.error(f"missing input: {path} — run bus_corridor_geometry.py first")
            sys.exit(2)
        df = pd.read_csv(path, dtype={"route_id": str, "stop_id": str})
        out[name] = df
        logger.info(f"loaded {name}.csv ({len(df):,} rows)")
    return out


# ---------------------------------------------------------------------------
# MAP HELPERS
# ---------------------------------------------------------------------------
def new_map(
    title: str,
    center: tuple = VANCOUVER_CENTER,
    zoom: int = DEFAULT_ZOOM,
) -> folium.Map:
    """
    Create a styled base map with a title banner.

    Why a helper: every map shares the same base configuration (CartoDB
    Positron, same Vancouver center, same zoom). Centralizing that means a
    single styling change (e.g. switching basemaps) updates every map.
    """
    m = folium.Map(
        location=center,
        zoom_start=zoom,
        tiles="CartoDB positron",
        control_scale=True,
    )

    # Floating title — Folium has no built-in title element, so we inject
    # a small HTML overlay. Keeping the title inside the map makes the
    # exported HTML file self-describing when shared standalone.
    title_html = f"""
    <div style="
        position: fixed; top: 10px; left: 50px; z-index: 9999;
        background: rgba(255,255,255,0.92); padding: 8px 14px;
        border: 1px solid #888; border-radius: 4px;
        font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
        font-size: 14px; font-weight: 600; color: #222;
        box-shadow: 0 2px 6px rgba(0,0,0,0.15);">
        {title}
    </div>
    """
    m.get_root().html.add_child(folium.Element(title_html))
    return m


def add_legend(
    m: folium.Map,
    title: str,
    entries: List[tuple],
) -> None:
    """
    Inject a colored legend into the map.

    entries: list of (color, label) tuples.

    Why this exists: Folium doesn't ship a real legend widget. The HTML
    overlay below is a battle-tested workaround that survives map zoom and
    pan, and looks reasonable when the HTML is opened standalone.
    """
    rows = "".join(
        f'<div style="display:flex; align-items:center; margin:2px 0;">'
        f'<span style="display:inline-block; width:18px; height:8px; '
        f'background:{color}; margin-right:8px; border-radius:1px;"></span>'
        f'<span style="font-size:12px; color:#222;">{label}</span></div>'
        for color, label in entries
    )
    legend_html = f"""
    <div style="
        position: fixed; bottom: 20px; right: 20px; z-index: 9999;
        background: rgba(255,255,255,0.94); padding: 10px 12px;
        border: 1px solid #888; border-radius: 4px;
        font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
        box-shadow: 0 2px 6px rgba(0,0,0,0.15);">
        <div style="font-size:12px; font-weight:700; color:#222; margin-bottom:4px;">{title}</div>
        {rows}
    </div>
    """
    m.get_root().html.add_child(folium.Element(legend_html))


def shape_points_for_route(
    geometry: pd.DataFrame,
    route_id: str,
) -> List[List[float]]:
    """
    Extract the lat/lon polyline for a single route from the geometry table.
    """
    pts = geometry[geometry["route_id"] == route_id].sort_values("sequence")
    return pts[["lat", "lon"]].values.tolist()


def draw_corridor(
    m: folium.Map,
    coords: List[List[float]],
    color: str,
    weight: float,
    opacity: float,
    tooltip: Optional[str] = None,
    popup: Optional[str] = None,
    layer: Optional[folium.FeatureGroup] = None,
) -> None:
    """
    Add a single corridor polyline to a map or layer.

    Why we ignore empty coords: a route may have been catalogued but never
    selected a usable shape — skipping silently keeps the map clean rather
    than throwing an exception that interrupts the whole build.
    """
    if not coords or len(coords) < 2:
        return
    pl = folium.PolyLine(
        locations=coords,
        color=color,
        weight=weight,
        opacity=opacity,
        tooltip=tooltip,
        popup=popup,
    )
    if layer is not None:
        pl.add_to(layer)
    else:
        pl.add_to(m)


def route_label(row: pd.Series) -> str:
    """
    Build a consistent human-readable label for a route — used in tooltips
    and popups across every map.
    """
    short = str(row.get("route_short_name", "")).lstrip("0") or str(row.get("route_short_name", ""))
    long_ = row.get("route_long_name", "")
    subtype = row.get("bus_subtype", "")
    return f"<b>Route {short}</b> — {long_}<br/><i>{subtype}</i>"


def save(m: folium.Map, assets_dir: Path, name: str) -> Path:
    """Persist a Folium map to assets/<name>.html."""
    assets_dir.mkdir(parents=True, exist_ok=True)
    out = assets_dir / f"{name}.html"
    m.save(str(out))
    logger.info(f"saved {name}.html")
    return out


# ---------------------------------------------------------------------------
# MAP 1: CRITICAL BUS CORRIDORS
# ---------------------------------------------------------------------------
def map_critical_corridors(
    catalog: pd.DataFrame,
    geometry: pd.DataFrame,
    assets_dir: Path,
) -> None:
    """
    Draw every bus corridor as faint background, then highlight the top-N
    critical corridors on top. The visual answer to "where does the
    network's spinal column actually go?".
    """
    m = new_map("Critical Bus Corridors — TransLink Bus Network")

    # Background layer: every catalogued corridor at very low opacity.
    # Why include all of them: without the background, the highlight has
    # no spatial context. With it, the eye can see exactly which corridors
    # are doing the heavy lifting against the full network.
    bg = folium.FeatureGroup(name="All bus corridors (background)", show=True)
    for _, row in catalog.iterrows():
        coords = shape_points_for_route(geometry, row["route_id"])
        draw_corridor(
            m=m, coords=coords, color="#999999",
            weight=BACKGROUND_LINE_WEIGHT, opacity=BACKGROUND_LINE_OPACITY,
            layer=bg,
        )
    bg.add_to(m)

    # Highlight layer: critical corridors colored by subtype.
    fg = folium.FeatureGroup(name="Critical corridors (top 20)", show=True)
    critical = catalog[catalog["is_critical"]].copy()
    for _, row in critical.iterrows():
        coords = shape_points_for_route(geometry, row["route_id"])
        color = SUBTYPE_COLORS.get(row["bus_subtype"], "#444444")
        popup = route_label(row)
        if "total_records" in critical.columns:
            popup += f"<br/>RT records: {int(row['total_records']):,}"
        if "static_stop_count" in critical.columns:
            popup += f"<br/>Stops: {int(row['static_stop_count'])}"
        draw_corridor(
            m=m, coords=coords, color=color,
            weight=HIGHLIGHT_LINE_WEIGHT, opacity=0.85,
            tooltip=f"Route {row['route_short_name'].lstrip('0') or row['route_short_name']}",
            popup=popup, layer=fg,
        )
    fg.add_to(m)

    folium.LayerControl(collapsed=False).add_to(m)
    add_legend(
        m, "Corridor subtype",
        [(c, st) for st, c in SUBTYPE_COLORS.items()],
    )
    save(m, assets_dir, "critical_bus_corridors_map")


# ---------------------------------------------------------------------------
# MAP 2: TOP 10 BUS ROUTES WITH STOPS
# ---------------------------------------------------------------------------
def map_top10_with_stops(
    catalog: pd.DataFrame,
    geometry: pd.DataFrame,
    stops: pd.DataFrame,
    membership: pd.DataFrame,
    assets_dir: Path,
) -> None:
    """
    The ten highest-activity bus corridors with EVERY stop they touch.

    Why this map matters: the previous "top corridor ranking" was a bar
    chart. A bar chart can't show you that route 49 and route 99 run
    parallel along the same east-west commuter spine. A map can.

    Stops are rendered as small CircleMarkers, not pins, because at this
    resolution there are several hundred stops and pin icons would
    obscure the corridor lines they're meant to annotate.
    """
    m = new_map("Top 10 Bus Routes — Corridor + Stop Infrastructure")

    top10 = catalog.sort_values(
        "total_records" if "total_records" in catalog.columns else "static_stop_count",
        ascending=False,
    ).head(TOP_N_HEADLINE).copy()

    # Color each of the 10 distinctly so they're separable on the map.
    # We use a 10-color qualitative palette that contrasts with the white
    # CartoDB basemap.
    distinct_colors = [
        "#e6194b", "#3cb44b", "#4363d8", "#f58231", "#911eb4",
        "#46f0f0", "#f032e6", "#bcf60c", "#fabebe", "#008080",
    ]

    legend_entries: List[tuple] = []
    stops_layer = folium.FeatureGroup(name="Stops on top 10 routes", show=True)

    for i, (_, row) in enumerate(top10.iterrows()):
        rid = row["route_id"]
        coords = shape_points_for_route(geometry, rid)
        color = distinct_colors[i % len(distinct_colors)]
        short = row["route_short_name"].lstrip("0") or row["route_short_name"]

        # Per-route layer so users can toggle individual top routes on/off.
        route_layer = folium.FeatureGroup(name=f"Route {short} — {row['route_long_name']}", show=True)
        draw_corridor(
            m=m, coords=coords, color=color,
            weight=HIGHLIGHT_LINE_WEIGHT, opacity=0.85,
            tooltip=f"Route {short}",
            popup=route_label(row),
            layer=route_layer,
        )

        # Render the stops served by this route. We pull from membership
        # rather than re-deriving here.
        rstops_ids = set(membership[membership["route_id"] == rid]["stop_id"].tolist())
        rstops = stops[stops["stop_id"].isin(rstops_ids)]
        for _, s in rstops.iterrows():
            folium.CircleMarker(
                location=[s["stop_lat"], s["stop_lon"]],
                radius=2.5, color=color, fill=True, fill_opacity=0.7,
                weight=0.5,
                tooltip=f"{s.get('stop_name', s['stop_id'])} (Route {short})",
            ).add_to(stops_layer)

        route_layer.add_to(m)
        legend_entries.append((color, f"Route {short} — {row['route_long_name'][:30]}"))

    stops_layer.add_to(m)
    folium.LayerControl(collapsed=True).add_to(m)
    add_legend(m, "Top 10 routes", legend_entries)
    save(m, assets_dir, "top10_bus_routes_with_stops_map")


# ---------------------------------------------------------------------------
# MAP 3: RAPIDBUS CORRIDORS
# ---------------------------------------------------------------------------
def map_rapidbus(
    catalog: pd.DataFrame,
    geometry: pd.DataFrame,
    stops: pd.DataFrame,
    membership: pd.DataFrame,
    assets_dir: Path,
) -> None:
    """
    The six RapidBus corridors (R1–R6) drawn in their official green livery,
    with every stop they serve.

    Why a dedicated map: RapidBus is TransLink's branded frequent-service
    product. It has higher stop spacing, dedicated branding, and very
    different operational characteristics from regular bus. Plotting all six
    together shows the actual coverage of the "RapidBus network" as a
    discrete product, which is hard to see in a system-wide map.
    """
    m = new_map("RapidBus Network — R1 through R6")

    rb = catalog[catalog["bus_subtype"] == "RapidBus"].copy()
    if rb.empty:
        logger.warning("no RapidBus routes found — skipping rapidbus map")
        return

    # Use the official RapidBus green for the corridor itself, with the
    # short_name labeled on a midpoint marker for legibility.
    color = SUBTYPE_COLORS["RapidBus"]
    stops_layer = folium.FeatureGroup(name="RapidBus stops", show=True)

    for _, row in rb.iterrows():
        rid = row["route_id"]
        coords = shape_points_for_route(geometry, rid)
        if not coords:
            continue
        draw_corridor(
            m=m, coords=coords, color=color,
            weight=HIGHLIGHT_LINE_WEIGHT + 1, opacity=0.85,
            tooltip=f"{row['route_short_name']} — {row['route_long_name']}",
            popup=route_label(row),
        )
        # Drop a label at the midpoint so the corridor IDs itself.
        mid = coords[len(coords) // 2]
        folium.map.Marker(
            location=mid,
            icon=folium.DivIcon(
                html=(
                    f'<div style="background:{color}; color:white; '
                    f'padding:2px 6px; border-radius:3px; font-weight:700; '
                    f'font-size:11px; font-family:sans-serif; '
                    f'box-shadow:0 1px 2px rgba(0,0,0,0.3); white-space:nowrap;">'
                    f'{row["route_short_name"]}</div>'
                )
            ),
        ).add_to(m)

        rstops_ids = set(membership[membership["route_id"] == rid]["stop_id"].tolist())
        rstops = stops[stops["stop_id"].isin(rstops_ids)]
        for _, s in rstops.iterrows():
            folium.CircleMarker(
                location=[s["stop_lat"], s["stop_lon"]],
                radius=3, color=color, fill=True, fill_opacity=0.85, weight=0.5,
                tooltip=f"{s.get('stop_name', s['stop_id'])} (RapidBus {row['route_short_name']})",
            ).add_to(stops_layer)

    stops_layer.add_to(m)
    folium.LayerControl(collapsed=True).add_to(m)
    add_legend(m, "Service", [(color, "RapidBus corridor"), (color, "RapidBus stop")])
    save(m, assets_dir, "rapidbus_corridors_map")


# ---------------------------------------------------------------------------
# MAP 4: 99 B-LINE
# ---------------------------------------------------------------------------
def map_route_99(
    catalog: pd.DataFrame,
    geometry: pd.DataFrame,
    stops: pd.DataFrame,
    membership: pd.DataFrame,
    assets_dir: Path,
) -> None:
    """
    The 99 B-Line corridor with every stop, headlined as the busiest single
    bus corridor in the TransLink network.

    Why a dedicated map: the 99 by itself accounts for the largest share of
    RT vehicle records (~214k records over the analysis window in the existing
    framework — about 9% of all bus telemetry). A corridor that important
    deserves its own visual that doesn't have to share screen space with
    anything else.
    """
    m = new_map("Route 99 B-Line — Broadway Corridor")

    bline = catalog[catalog["bus_subtype"] == "B-Line"].copy()
    if bline.empty:
        logger.warning("no B-Line route found — skipping route 99 map")
        return
    row = bline.iloc[0]
    rid = row["route_id"]
    coords = shape_points_for_route(geometry, rid)
    if not coords:
        logger.warning("99 B-Line has no shape geometry — skipping map")
        return

    color = SUBTYPE_COLORS["B-Line"]

    # Draw the corridor — thicker than usual, because this map is dedicated.
    folium.PolyLine(
        locations=coords, color=color, weight=6, opacity=0.9,
        tooltip="Route 99 — Broadway B-Line",
        popup=route_label(row),
    ).add_to(m)

    # Plot every stop on the corridor.
    rstops_ids = set(membership[membership["route_id"] == rid]["stop_id"].tolist())
    rstops = stops[stops["stop_id"].isin(rstops_ids)]
    for _, s in rstops.iterrows():
        folium.CircleMarker(
            location=[s["stop_lat"], s["stop_lon"]],
            radius=5, color=color, fill=True, fill_opacity=0.9, weight=1,
            tooltip=s.get("stop_name", s["stop_id"]),
            popup=f"<b>{s.get('stop_name', '')}</b><br/>Stop ID: {s['stop_id']}",
        ).add_to(m)

    # Auto-zoom to the corridor extent — better than a generic Vancouver view
    # for a route-specific map.
    m.fit_bounds([
        [min(c[0] for c in coords), min(c[1] for c in coords)],
        [max(c[0] for c in coords), max(c[1] for c in coords)],
    ])

    add_legend(m, "Route 99 B-Line", [
        (color, "Broadway B-Line corridor"),
        (color, f"{len(rstops)} stops along corridor"),
    ])
    save(m, assets_dir, "route_99_bline_corridor_map")


# ---------------------------------------------------------------------------
# MAP 5: NIGHTBUS CORRIDORS
# ---------------------------------------------------------------------------
def map_nightbus(
    catalog: pd.DataFrame,
    geometry: pd.DataFrame,
    assets_dir: Path,
) -> None:
    """
    All N-prefixed overnight bus corridors plotted together.

    Why a dedicated map: NightBus is a topologically different network from
    daytime service — fewer routes, longer trunks, hub-and-spoke around
    downtown. Seeing them all on one map is the cleanest way to convey
    "this is the city's late-night spine".

    The map uses a CartoDB Dark Matter tile to evoke the time of day this
    network runs — purely a presentation choice, but it makes the page
    instantly readable as "the night network".
    """
    nightbuses = catalog[catalog["bus_subtype"] == "NightBus"].copy()
    if nightbuses.empty:
        logger.warning("no NightBus routes found — skipping nightbus map")
        return

    m = folium.Map(
        location=VANCOUVER_CENTER, zoom_start=11,
        tiles="CartoDB dark_matter", control_scale=True,
    )
    m.get_root().html.add_child(folium.Element(
        '<div style="position:fixed; top:10px; left:50px; z-index:9999; '
        'background:rgba(20,20,30,0.92); padding:8px 14px; border:1px solid #555; '
        'border-radius:4px; font-family:-apple-system,sans-serif; font-size:14px; '
        'font-weight:600; color:#eee; box-shadow:0 2px 6px rgba(0,0,0,0.4);">'
        'NightBus Network — Overnight Bus Corridors</div>'
    ))

    color = "#f6c344"   # warm amber against the dark map — high contrast
    for _, row in nightbuses.iterrows():
        coords = shape_points_for_route(geometry, row["route_id"])
        if not coords:
            continue
        folium.PolyLine(
            locations=coords, color=color, weight=3.5, opacity=0.85,
            tooltip=f"{row['route_short_name']} — {row['route_long_name']}",
            popup=route_label(row),
        ).add_to(m)
        mid = coords[len(coords) // 2]
        folium.map.Marker(
            location=mid,
            icon=folium.DivIcon(
                html=(
                    f'<div style="background:#222; color:{color}; '
                    f'padding:2px 6px; border-radius:3px; font-weight:700; '
                    f'font-size:10px; font-family:sans-serif; '
                    f'border:1px solid {color}; white-space:nowrap;">'
                    f'{row["route_short_name"]}</div>'
                )
            ),
        ).add_to(m)

    # Inline legend tuned to the dark theme.
    m.get_root().html.add_child(folium.Element(
        '<div style="position:fixed; bottom:20px; right:20px; z-index:9999; '
        'background:rgba(20,20,30,0.94); padding:10px 12px; border:1px solid #555; '
        'border-radius:4px; font-family:sans-serif; color:#eee;">'
        '<div style="font-size:12px; font-weight:700; margin-bottom:4px;">NightBus</div>'
        f'<div style="font-size:12px;"><span style="display:inline-block; width:18px; '
        f'height:6px; background:{color}; margin-right:8px;"></span>'
        f'Overnight corridor ({len(nightbuses)} routes)</div></div>'
    ))
    save(m, assets_dir, "nightbus_corridors_map")


# ---------------------------------------------------------------------------
# MAP 6: MAJOR REGULAR + EXPRESS BUS CORRIDORS
# ---------------------------------------------------------------------------
def map_major_corridors(
    catalog: pd.DataFrame,
    geometry: pd.DataFrame,
    assets_dir: Path,
) -> None:
    """
    The major non-RapidBus / non-B-Line corridors.

    Why: RapidBus and B-Line have their own dedicated maps. What's left is
    the "everyday workhorse" layer of the bus network — the 49, the 14,
    the 25, the 503. Showing these together (without the RapidBus/B-Line
    overhead) puts the regular-service backbone of the system on a single
    canvas.
    """
    m = new_map("Major Bus Corridors — Regular + Express Backbone")

    # Top regular + express routes by activity (or stop count if no RT).
    sort_col = "total_records" if "total_records" in catalog.columns else "static_stop_count"
    keep_subtypes = {"Regular Bus", "Express"}
    major = catalog[catalog["bus_subtype"].isin(keep_subtypes)].copy()
    major = major.sort_values(sort_col, ascending=False).head(25)

    for _, row in major.iterrows():
        coords = shape_points_for_route(geometry, row["route_id"])
        if not coords:
            continue
        color = SUBTYPE_COLORS.get(row["bus_subtype"], "#666666")
        draw_corridor(
            m=m, coords=coords, color=color,
            weight=DEFAULT_LINE_WEIGHT + 1, opacity=0.75,
            tooltip=f"Route {row['route_short_name'].lstrip('0') or row['route_short_name']}",
            popup=route_label(row),
        )

    add_legend(m, "Backbone service", [
        (SUBTYPE_COLORS["Regular Bus"], "Regular Bus (top 25 by activity)"),
        (SUBTYPE_COLORS["Express"], "Express"),
    ])
    save(m, assets_dir, "major_bus_corridors_map")


# ---------------------------------------------------------------------------
# MAP 7: BUS TELEMETRY OVERLAY ON STATIC CORRIDORS
# ---------------------------------------------------------------------------
def load_rt_positions(rt_dir: Optional[Path]) -> pd.DataFrame:
    """
    Load RT vehicle position points if the user supplied a data directory.

    Why optional: the geometry maps are useful standalone. The overlay map
    only makes sense if RT data exists. Most readers running this from a
    portfolio repo won't have the full 6 GB raw parquet — the function
    returns empty in that case and the overlay map falls back to corridor-
    only.

    To keep this lightweight we sample at most ~5000 points — a denser
    plot would just smear into a single blob at city zoom.
    """
    if rt_dir is None or not rt_dir.exists():
        return pd.DataFrame()
    files = sorted(rt_dir.rglob("*.parquet"))
    if not files:
        return pd.DataFrame()
    frames = []
    target_per_file = max(1, 5000 // max(len(files), 1))
    for f in files:
        try:
            df = pd.read_parquet(f, columns=["latitude", "longitude", "route_id", "vehicle_id"])
            if len(df) > target_per_file:
                df = df.sample(n=target_per_file, random_state=42)
            frames.append(df)
        except Exception as e:
            logger.warning(f"skipping {f.name}: {e}")
    if not frames:
        return pd.DataFrame()
    out = pd.concat(frames, ignore_index=True).dropna(subset=["latitude", "longitude"])
    logger.info(f"RT overlay: sampled {len(out):,} vehicle positions")
    return out


def map_telemetry_vs_static(
    catalog: pd.DataFrame,
    geometry: pd.DataFrame,
    rt_positions: pd.DataFrame,
    assets_dir: Path,
) -> None:
    """
    Side-by-side in one map: static bus corridor lines (subtype-colored)
    underneath, RT vehicle positions on top as small points.

    Why this is the headline map: it makes the static/RT separation visible.
    A reader can SEE that the corridor geometry comes from GTFS Static,
    while the points are observed live telemetry, and that they coincide
    (because real buses follow their published shapes). If they diverge,
    that itself is a story — diversions, detours, GPS drift.

    If no RT data is on hand, the function still produces a corridor-only
    map labelled as such, so the portfolio output is always complete.
    """
    m = new_map("Static Bus Corridors vs. RT Telemetry — Overlay")

    # Layer 1: corridors, subtype-colored.
    corridor_layer = folium.FeatureGroup(name="Static corridors (GTFS shapes.txt)", show=True)
    for _, row in catalog.iterrows():
        coords = shape_points_for_route(geometry, row["route_id"])
        if not coords:
            continue
        color = SUBTYPE_COLORS.get(row["bus_subtype"], "#999999")
        weight = HIGHLIGHT_LINE_WEIGHT if row.get("is_critical", False) else DEFAULT_LINE_WEIGHT - 1
        opacity = 0.7 if row.get("is_critical", False) else 0.25
        draw_corridor(
            m=m, coords=coords, color=color, weight=weight, opacity=opacity,
            tooltip=f"Route {row['route_short_name']}",
            layer=corridor_layer,
        )
    corridor_layer.add_to(m)

    # Layer 2: RT vehicle positions, if available.
    if not rt_positions.empty:
        rt_layer = folium.FeatureGroup(name="RT vehicle positions (sampled)", show=True)
        for _, p in rt_positions.iterrows():
            folium.CircleMarker(
                location=[p["latitude"], p["longitude"]],
                radius=2, color="#000000", fill=True, fill_opacity=0.6,
                weight=0,
                tooltip=f"Vehicle {p.get('vehicle_id','?')} • Route {p.get('route_id','?')}",
            ).add_to(rt_layer)
        rt_layer.add_to(m)
        note = f"{len(rt_positions):,} sampled RT positions"
    else:
        note = "No RT data on hand — corridors only"

    folium.LayerControl(collapsed=False).add_to(m)
    add_legend(m, "Layers", [
        ("#000000", note),
        (SUBTYPE_COLORS["B-Line"], "B-Line corridor"),
        (SUBTYPE_COLORS["RapidBus"], "RapidBus corridor"),
        (SUBTYPE_COLORS["Express"], "Express corridor"),
        (SUBTYPE_COLORS["Regular Bus"], "Regular Bus corridor"),
    ])
    save(m, assets_dir, "bus_telemetry_vs_static_corridors_map")


# ---------------------------------------------------------------------------
# MAIN
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(
        description="Render bus-only static corridor maps from geometry outputs."
    )
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--assets-dir", type=Path, default=DEFAULT_ASSETS_DIR)
    parser.add_argument(
        "--rt-positions", type=Path, default=None,
        help="Optional directory of *.parquet RT vehicle positions for the overlay map.",
    )
    args = parser.parse_args()

    data = load_geometry_outputs(args.output_dir)
    catalog = data["bus_route_catalog"]
    geometry = data["bus_corridor_geometry"]
    stops = data["bus_stops_catalog"]
    membership = data["bus_route_stop_membership"]

    logger.info("rendering critical corridors map...")
    map_critical_corridors(catalog, geometry, args.assets_dir)

    logger.info("rendering top 10 routes + stops map...")
    map_top10_with_stops(catalog, geometry, stops, membership, args.assets_dir)

    logger.info("rendering RapidBus corridors map...")
    map_rapidbus(catalog, geometry, stops, membership, args.assets_dir)

    logger.info("rendering 99 B-Line corridor map...")
    map_route_99(catalog, geometry, stops, membership, args.assets_dir)

    logger.info("rendering NightBus corridors map...")
    map_nightbus(catalog, geometry, args.assets_dir)

    logger.info("rendering major bus corridors map...")
    map_major_corridors(catalog, geometry, args.assets_dir)

    logger.info("rendering telemetry vs. static overlay map...")
    rt = load_rt_positions(args.rt_positions)
    map_telemetry_vs_static(catalog, geometry, rt, args.assets_dir)

    print()
    print("=" * 70)
    print("  BUS CORRIDOR MAP RENDERING COMPLETE")
    print("=" * 70)
    for name in [
        "critical_bus_corridors_map",
        "top10_bus_routes_with_stops_map",
        "rapidbus_corridors_map",
        "route_99_bline_corridor_map",
        "nightbus_corridors_map",
        "major_bus_corridors_map",
        "bus_telemetry_vs_static_corridors_map",
    ]:
        p = args.assets_dir / f"{name}.html"
        marker = "✓" if p.exists() else "MISSING"
        print(f"  [{marker}] assets/{name}.html")
    print("=" * 70)


if __name__ == "__main__":
    main()
