# TransLink Bus Operational Observability & Corridor Infrastructure Intelligence

Analytical layer 4 of the GTFS-RT transit telemetry platform.

This is the static-infrastructure phase that closes the gap between the
RT-only intelligence framework (layer 3) and a true infrastructure-aware
operational view. Layer 3 told you *which routes are running and how
reliably*; this layer tells you *what the physical bus network those
vehicles flow through actually looks like* — corridor geometry, stop
membership, route hierarchy, and the boundary between observed telemetry
and published infrastructure.

> **Scope reminder.** The TransLink GTFS-RT vehicle feed in this dataset
> contains BUS TELEMETRY ONLY. Every artifact in this layer is bus-only by
> design. SkyTrain, SeaBus, and West Coast Express remain out of scope
> here — see `reports/executive_findings.md` for the modal coverage
> finding from layer 3.

---

## What changed in this upgrade

The previous layer produced corridor *rankings* (a CSV of route_ids ordered
by activity) and *vehicle position maps* (point clouds of where buses were
seen). What it could not do was draw the actual *line* of a bus corridor on
a map — that requires `shapes.txt` from GTFS Static, which is now
integrated.

| Before | After |
|---|---|
| Maps showed RT point clouds only | Maps draw real corridor polylines from `shapes.txt` |
| Critical corridors = a ranked CSV | Critical corridors = a ranked CSV AND a highlighted map |
| Stops were not visualized | Stops along each corridor are now rendered (8,627 bus stops resolved) |
| RT data was the only source | Static GTFS + RT are layered and clearly separated |
| 99 B-Line was a row in a CSV | 99 B-Line has its own dedicated infrastructure map |
| RapidBus was a subtype tag | RapidBus is a network — six corridors plotted together with stops |
| NightBus was a row in a CSV | NightBus has its own dark-themed overnight network map |

The previous five analytical layers (multimodal_transit_intelligence.py)
and the per-mode RT activity maps (geospatial_maps.py) are unchanged. This
layer is additive.

---

## What this layer produces

### Static bus corridor maps — `assets/*.html`

| Filename | What it shows |
|---|---|
| `critical_bus_corridors_map.html` | All 240 bus corridors faintly, with the top 20 critical corridors highlighted by subtype |
| `top10_bus_routes_with_stops_map.html` | The 10 highest-activity bus corridors with every stop they serve |
| `rapidbus_corridors_map.html` | The R1–R6 RapidBus network in TransLink green, with stops |
| `route_99_bline_corridor_map.html` | The 99 B-Line corridor with every stop (highest RT activity in the system) |
| `nightbus_corridors_map.html` | All N-prefixed overnight corridors on a dark-themed map |
| `major_bus_corridors_map.html` | Top regular + express corridors (excludes RapidBus and B-Line) |
| `bus_telemetry_vs_static_corridors_map.html` | Static corridors + RT vehicle positions overlaid — the headline split |

### Static companion charts — `assets/*.png`

| Filename | What it answers |
|---|---|
| `bus_corridor_hierarchy.png` | Top-20 corridors compared side-by-side on RT intensity vs static stop count |
| `bus_stop_count_distribution.png` | Where critical corridors sit in the overall stops-per-route distribution |
| `bus_subtype_route_counts.png` | Routes per subtype (infrastructure-side; complements `bus_subtype_composition.png`) |

### Tables — `outputs/*.csv`

| Filename | Contents |
|---|---|
| `bus_route_catalog.csv` | Every bus route with subtype, color, stop count, RT activity, and a critical-corridor flag |
| `bus_shape_index.csv` | Chosen representative `shape_id` per bus route (the longest-extent shape) |
| `bus_corridor_geometry.csv` | Long-format `(route_id, sequence, lat, lon)` polyline data, 55,318 points across 240 routes |
| `bus_stops_catalog.csv` | The 8,627 stops served by at least one bus trip |
| `bus_route_stop_membership.csv` | The (route_id, stop_id) edge table — 14,752 edges |
| `critical_bus_corridors.csv` | The top 20 by combined RT activity + static stop count |

All existing RT-derived outputs (`route_dimension.csv`, `top_corridors.csv`,
`operational_stability_score.csv`, etc.) from layer 3 are preserved
unchanged.

### Reports — `reports/*.md`

- `executive_findings.md` — original layer-3 executive findings (unchanged)
- `geospatial_map_interpretations.md` — original per-map interpretations (unchanged)
- `bus_corridor_intelligence.md` — **NEW** — interpretation of every corridor map, dependency reading, and observability notes

---

## How to run

From the project root, in order:

```bash
# Layer 3 — original RT-derived intelligence (unchanged)
python src/multimodal_transit_intelligence.py
python src/geospatial_maps.py

# Layer 4 — NEW: bus corridor geometry from GTFS Static
python src/bus_corridor_geometry.py            # build the geometry CSVs
python src/bus_corridor_maps.py                # render the 7 HTML maps
python src/bus_corridor_charts.py              # render the 3 PNG companions

# Optional: overlay layer 4 maps with RT positions
python src/bus_corridor_maps.py --rt-positions data/raw/2026-05-09
```

Required Python packages: `pandas`, `numpy`, `matplotlib`, `seaborn`,
`folium`, `pyarrow`. New in this layer: nothing — `folium` and `pandas`
already cover it.

Required input: a TransLink GTFS Static extract in `data/gtfs_static/`
containing `routes.txt`, `trips.txt`, `shapes.txt`, `stops.txt`,
`stop_times.txt`.

---

## Critical caveats (read before interpreting any artifact)

The original layer-3 caveats still apply, with one resolution:

1. **Timezone.** Same as before — all hourly outputs are in Vancouver local time.

2. **Single-mode telemetry.** The GTFS-RT vehicle feed remains bus-only.
   This layer reinforces that boundary by filtering GTFS Static to
   `route_type == 3` (bus) at every join, so no SkyTrain / SeaBus / WCE
   geometry leaks into any corridor map.

3. **No kinematic data.** `speed` and `bearing` columns are still 0%
   populated. This layer doesn't claim corridor speeds.

4. **~~`shapes.txt` unavailable.~~ RESOLVED.** This is the upgrade that
   delivers corridor geometry. Every bus route now has a representative
   shape on every relevant map.

5. **Representative-shape simplification.** A single TransLink route can
   have a dozen shape variants (directional, short-turn, late-night). This
   layer picks the *longest unique* shape per route as the corridor's
   visual representation. That captures the maximum geographic extent of
   the route but does not show direction-specific or short-turn variants.
   For routing-grade analysis (not infrastructure maps), use the full
   trip-shape join instead.

6. **Operations, not ridership.** Unchanged from layer 3.

7. **Five-day window.** Unchanged from layer 3.

---

## Future analysis roadmap

Items resolved or partially resolved by this layer have been marked.

- ~~**Route geometry overlay.**~~ **Done in this layer** — every map draws polylines from `shapes.txt`.
- ~~**Stop integration.**~~ **Done in this layer** — `stop_times.txt` joined to produce the `(route_id, stop_id)` edge table.
- **Derived speed analytics.** Still deferred — needs vehicle-position deltas.
- **Multimodal expansion.** Still deferred — needs a separate rail RT feed.
- **Headway adherence.** Now achievable — `stop_times.txt` is integrated. Next phase: join RT arrival predictions to scheduled stop times.
- **Anomaly alerting.** Unchanged — requires baseline weeks.
- **Streamlit / Power BI dashboards.** The new corridor tables slot into the existing semantic model unchanged; the catalog acts as a sister dimension to `route_dimension.csv`.

---

## Repository layout

```
project/
├── src/
│   ├── multimodal_transit_intelligence.py   # layer 3 — 5-layer RT analytical framework
│   ├── geospatial_maps.py                   # layer 3 — RT vehicle-position maps
│   ├── bus_corridor_geometry.py             # layer 4 NEW — GTFS Static → corridor CSVs
│   ├── bus_corridor_maps.py                 # layer 4 NEW — corridor HTML maps
│   ├── bus_corridor_charts.py               # layer 4 NEW — corridor PNG companions
│   └── run_intelligence_framework.py        # convenience runner
├── data/
│   └── gtfs_static/                         # required: GTFS Static extract
├── assets/                                  # charts (.png) + maps (.html)
├── outputs/                                 # processed tables (.csv)
├── reports/                                 # findings + interpretations (.md)
└── notebooks/
    └── week2_multimodal_transit_intelligence.ipynb   # narrative with new corridor section
```
