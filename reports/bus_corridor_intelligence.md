# Bus Corridor Infrastructure Intelligence — Layer 4 Findings

This report sits next to `executive_findings.md` (layer 3 RT findings) and
`geospatial_map_interpretations.md` (layer 3 map interpretations). It
covers what the layer 4 corridor maps and tables reveal about the *physical
infrastructure* of the TransLink bus network — the static-side counterpart
to the operational-side findings from layer 3.

> **Scope.** Bus-only. The GTFS-RT feed in this dataset is bus telemetry
> only; layer 4 keeps the same boundary by filtering the GTFS Static
> extract to `route_type == 3` at every join.

---

## 1. Network inventory — what we now know about the bus network

| Quantity | Value |
|---|---|
| Catalogued bus routes | 240 |
| Routes with a representative shape on the map | 240 (100% coverage) |
| Shape geometry points loaded | 55,318 |
| Unique bus stops in the network | 8,627 |
| (Route, Stop) infrastructure edges | 14,752 |

**Reading.** Every bus route in the TransLink published GTFS has a
representative shape that this layer can draw. 100% map coverage on the
static side is unusual and worth flagging — it confirms that the gap in
the previous (RT-only) maps was a tooling gap, not a data gap.

| Subtype | Route count | What it is |
|---|---:|---|
| Regular Bus | 203 | Standard local/community bus service |
| Express | 20 | 5xx-series and "Express" long-haul commuter routes |
| NightBus | 10 | N-prefixed overnight network (typically 12am–5am) |
| RapidBus | 6 | TransLink's branded frequent-service network (R1–R6) |
| B-Line | 1 | The 99 — the system's flagship branded corridor |

Compare this to the RT-records share in `bus_subtype_composition.png` from
layer 3. The asymmetry is the point: the 1 B-Line route carries
disproportionately more telemetry than 1/240th of the network, RapidBus
punches above its 6/240 weight, and NightBus underperforms its 10/240
share because it only operates a few hours per day. This contrast between
"infrastructure share" and "telemetry share" is what
`bus_subtype_route_counts.png` makes explicit.

---

## 2. Critical bus corridors — where the spinal column actually runs

The `critical_bus_corridors_map.html` highlights the top 20 corridors
against a faint background of the full network. Three readings stand out:

**(a) The east–west spine.** The 99 B-Line, R4 (41st Avenue), 49 (49th
Avenue), 9 (Broadway), 14 (Hastings/UBC) and 25 (King Edward) form a
visible east–west grid across the city of Vancouver. These are the
streets that have to keep moving for the network to function. A single
bus-lane failure on Broadway or Hastings affects multiple top-20
corridors simultaneously.

**(b) The Surrey trunk.** The 321 (King George Blvd), 335 (Newton
Exchange / Surrey Central), 502 / 503 (Fraser Hwy) and 410 (Brighouse /
22nd St Station) form the south-of-Fraser trunk. This is a less
visually-dense network than the Vancouver east-west spine but carries
significant RT activity per route, indicating long route cycles rather
than tightly stacked frequent service.

**(c) Hub–and–spoke patterns at the SkyTrain rapid-transit interchanges.**
Many top-20 routes terminate at a SkyTrain station: Brighouse, 22nd St,
Lougheed, Surrey Central, Newton. These are the feeder corridors the
layer-3 framework called out — their static geometry now makes the
feeder relationship visually concrete.

---

## 3. The 99 B-Line — single-route observability

`route_99_bline_corridor_map.html` plots the entire B-Line corridor with
all 40 of its stops. Three operational facts to note:

- **40 stops on the longest-extent shape.** This is far fewer than a
  typical Vancouver east-west route (the 49 has 126 stops, the 25 has
  127). Wide stop spacing is the defining feature of B-Line / RapidBus
  service — speed traded against coverage.

- **Single route, 9% of all bus telemetry.** Layer 3's
  `top_corridors.csv` shows the 99 produced 214,370 RT records in the
  analysis window. That single corridor alone is approximately 9% of all
  observed bus telemetry. Operationally, this corridor IS the bus
  network's most visible single asset.

- **Single-point-of-failure profile.** Because so much telemetry runs on
  one corridor, any service disruption here propagates faster through
  the framework's downstream metrics than a disruption elsewhere would.
  The route 99 map exists in part to make that concentration impossible
  to miss.

---

## 4. RapidBus — the frequent-service network as a network

`rapidbus_corridors_map.html` plots all six R-routes together. The map
makes three things visible that no single route map can:

- **R-routes don't significantly overlap.** Each RapidBus corridor covers
  a distinct part of Metro Vancouver — R1 (King George Blvd), R2 (Marine
  Drive North Shore), R3 (Lougheed Hwy), R4 (41st Avenue), R5 (Hastings
  St), R6 (Scott Road). This is the geographic diversification of the
  frequent-service product.

- **Wide stop spacing is consistent across all six.** Stop counts: R1=39,
  R2=35, R3=44, R4=34, R5=38, R6=39. None of them resembles a local-route
  stop-density profile. RapidBus is operating as a single product class,
  not as six variations of regular service.

- **Coverage gaps the map exposes.** RapidBus does not penetrate downtown
  Vancouver directly — the closest service is the R5 along Hastings. No
  RapidBus corridor serves the corridor between False Creek and the
  airport. These are gaps the static map makes obvious that an RT
  scatter could not.

---

## 5. NightBus — a smaller, topologically different network

`nightbus_corridors_map.html` shows all ten N-routes against a dark
basemap. The takeaway is structural, not operational:

- **Hub at downtown Vancouver.** Almost every NightBus corridor either
  starts or passes through downtown. This is the classic late-night
  hub-and-spoke pattern — overnight networks consolidate around a single
  hub because off-hour ridership cannot support a meshed grid.

- **NightBus shapes mirror daytime trunks, with simplifications.** The
  N9 follows the daytime Broadway / Lougheed corridor; the N17 follows
  the daytime West Broadway corridor; the N19 follows daytime Kingsway.
  This is by design — operators map the night network to the daytime
  network's most-used trunks rather than running a different topology.

- **Why this matters for observability.** The layer-3 stability metrics
  showed NightBus had distinctly different temporal signatures from
  regular bus. The static map shows *why*: it's not running in different
  *places*; it's running in fewer places at fewer times. The
  infrastructure shape isn't different — only the schedule density is.

---

## 6. Top 10 with stops — the dense readout

`top10_bus_routes_with_stops_map.html` is the highest-density map in this
layer. It plots:

- 10 distinct corridors, each in its own color
- Every stop on every one of those routes
- Per-route toggle so a reader can isolate any single route

This map exists for the question "which streets are doing the most
work?". The visible parallel running of the 99, R4 and the 49 along the
east-west axis answers that immediately. Same for the King George Blvd /
Fraser Hwy concentration in Surrey.

A specific reading: **the 16 (29th Ave Stn / Arbutus) has more stops
than any other top-20 route at 168**. That tells you the 16 is a
coverage-oriented corridor — it stops more often, serves more
neighborhoods, and is not optimized for cross-city speed. The 99 is its
opposite: 40 stops, but the highest RT activity by far. Both can be
"critical corridors" while being completely different operational
products. The corridor map is what makes that comparison legible at a
glance.

---

## 7. Major corridors — the regular-service backbone

`major_bus_corridors_map.html` strips out RapidBus and the B-Line so the
question "what does the regular-service backbone look like on its own?"
can be answered without the headline corridors stealing the visual
weight. This is the map that probably matches a transit planner's mental
model of the system most closely — the workhorse routes that move the
city.

---

## 8. Static corridor + RT overlay — the headline split

`bus_telemetry_vs_static_corridors_map.html` is the map this entire
layer was built to make possible. It draws:

- **Underneath:** every bus corridor's static shape, low-opacity by
  default, subtype-colored, with critical corridors highlighted.
- **On top:** sampled RT vehicle positions as small black points.

The intended reading is: *the points should lie on the lines*. They
overwhelmingly do. Where they diverge — a point off the corridor, or a
corridor with no points on it — is exactly the kind of finding the
overlay map exists to surface:

- A point off a corridor indicates a detour, a GPS error, a deadhead
  move, or a route variant not represented by the chosen "longest
  shape". All of these are operationally interesting.
- A corridor with no points indicates either a route that wasn't running
  in the analysis window, or one that's missing telemetry. The layer-3
  observability findings already flagged the latter case at the *mode*
  level; this map lets the same question be asked at the *individual
  route* level.

---

## 9. Observability limitations (carried forward and clarified)

The layer-3 caveats still apply. Two are worth restating in this layer's
language:

- **Single representative shape per route.** A single TransLink route
  has multiple `shape_id`s (direction × time-of-day × short-turn). This
  layer picks the longest unique shape for visual completeness. That
  works for an *infrastructure* map but is the wrong choice for a
  routing-grade analysis. Anyone reusing `bus_corridor_geometry.csv` for
  scheduling or travel-time work should join via `trips.txt` and `stop_times.txt` instead.

- **No fabrication of non-bus telemetry.** The overlay map shows only
  bus RT positions. No SkyTrain, SeaBus, or WCE vehicle points are
  plotted, because none exist in the GTFS-RT feed in this dataset.
  Layer-3's modal coverage finding (and the placeholder maps in layer
  3's `assets/`) remain the source of truth on that gap.

---

## 10. What changes downstream

After this layer:

- The `route_dimension.csv` dimension table is now joinable to
  `bus_route_catalog.csv` and `bus_stops_catalog.csv` via `route_id` and
  `stop_id` respectively. A Power BI / Streamlit semantic model now has
  three first-class entities (route, stop, vehicle) where before it had
  one.

- The "is_critical" flag in `bus_route_catalog.csv` is the single
  authoritative tag for "this route is infrastructure-critical". Every
  downstream chart or dashboard that wants to show critical-only views
  should read it from here rather than re-deriving it.

- The `bus_corridor_geometry.csv` long-format table is the canonical
  source for any future map. Folium, Leaflet, MapLibre, deck.gl — all
  can consume it directly. No re-parsing of `shapes.txt` is needed.

- Stop-level analysis is now possible. Headway adherence (when does each
  bus *actually* arrive at each stop, versus when was it scheduled?) is
  the obvious next layer — it's the metric a transit agency would care
  about most, and this layer puts the static stop network in reach of
  the RT vehicle observations.
