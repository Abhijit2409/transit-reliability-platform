# Geospatial Map Interpretations
_Generated: 2026-05-25T05:52:41.268552+00:00_

Each section below describes one map artifact in `assets/`. Maps represent VEHICLE PRESENCE only — not passenger movement or ridership demand.

All hour references are Vancouver local time (PDT, UTC-7) unless noted.

---

## `vancouver_multimodal_vehicle_map.html`

**What this map shows:** Every observed vehicle position in the analysis window, colored by transit mode (Bus, SkyTrain, SeaBus, WCE, HandyDART). In this dataset only the Bus layer is populated.

**Why it matters operationally:** It establishes the geographic footprint of the GTFS-RT feed. Operationally, you can see at a glance which neighborhoods are densely instrumented and where coverage thins.

**Limitations:** Bus-only coverage; other modes do not report through this feed. Points may overplot in dense corridors — use the heatmap version to see density gradients.

**How additional GTFS Static would improve it:** With `shapes.txt` we could overlay official route polylines, making it visually clear which points belong to which corridor. With `stops.txt` we could pin SkyTrain stations as reference markers.

---

## `bus_activity_map.html`

**What this map shows:** Every bus position, colored by operational sub-type (B-Line, RapidBus, Regular, Express, Community Shuttle, NightBus).

**Why it matters operationally:** Sub-type coloring reveals the operational hierarchy spatially. RapidBus and B-Line points trace TransLink's frequent transit corridors — the spine the rest of the bus network feeds into.

**Limitations:** Point overplotting in central Vancouver makes it hard to read density gradients; use the heatmap for that purpose.

**How additional GTFS Static would improve it:** `shapes.txt` would let us draw actual route corridors and see how closely vehicles track their nominal paths.

---

## `skytrain_activity_map.html`

**What this map shows:** Intended to show SkyTrain vehicle positions across all three lines.

**Why it matters operationally:** SkyTrain is the spine of regional transit. Real-time positions would let us assess headway adherence, station dwell times, and service contraction during off-peak hours.

**Limitations:** ZERO SkyTrain vehicles report through this GTFS-RT feed. The map is a placeholder.

**How additional GTFS Static would improve it:** A SkyTrain vehicle position source (e.g., a separate feed or Compass operational data) would populate this. `shapes.txt` would add line geometry as reference.

---

## `expo_line_activity_map.html`

**What this map shows:** Intended to show Expo Line train positions from Waterfront to King George / Production Way.

**Why it matters operationally:** Expo Line is the highest-ridership SkyTrain corridor. Operational monitoring here would matter most for service reliability metrics.

**Limitations:** No Expo Line data in feed.

**How additional GTFS Static would improve it:** A SkyTrain real-time feed plus `shapes.txt` polylines would enable headway analytics by station segment.

---

## `canada_line_activity_map.html`

**What this map shows:** Intended to show Canada Line train positions from Waterfront to YVR / Richmond-Brighouse.

**Why it matters operationally:** Canada Line is the airport-region spine. Real-time positions would enable airport-connection coordination analytics.

**Limitations:** No Canada Line data in feed.

**How additional GTFS Static would improve it:** SkyTrain real-time feed + `shapes.txt`.

---

## `seabus_activity_map.html`

**What this map shows:** Intended to show SeaBus vessel positions in Burrard Inlet (Waterfront ↔ Lonsdale Quay).

**Why it matters operationally:** SeaBus is the primary North Shore↔Downtown rapid link. Real-time positions would enable crossing-time analysis.

**Limitations:** No SeaBus data in feed.

**How additional GTFS Static would improve it:** A SeaBus vessel position feed would populate this map. The crossing is short and well-defined, so even sparse data would be analytically valuable.

---

## `west_coast_express_activity_map.html`

**What this map shows:** Intended to show West Coast Express commuter rail positions between Waterfront and Mission.

**Why it matters operationally:** WCE serves the eastern commuter belt. Real-time positions would let us assess schedule adherence on a service that runs only during peak hours.

**Limitations:** No WCE data in feed.

**How additional GTFS Static would improve it:** WCE-specific real-time feed plus rail `shapes.txt`.

---

## `rapidbus_activity_map.html`

**What this map shows:** Positions for R1–R6 RapidBus corridors only.

**Why it matters operationally:** RapidBus is TransLink's signature limited-stop bus tier and one of the most reliable proxies for ridership demand. Spatial patterns here highlight the city's high-frequency arterial network.

**Limitations:** Single color per RapidBus route would be ideal; current palette uses the orange RapidBus brand color across all six.

**How additional GTFS Static would improve it:** `shapes.txt` would draw the actual RapidBus corridor lines, making it visually obvious where each R-route runs.

---

## `nightbus_activity_map.html`

**What this map shows:** Overnight bus network (N8 through N35) positions during night hours.

**Why it matters operationally:** NightBus is the only mode running during the 02:00–05:00 window (when SkyTrain is closed). Its geographic footprint is the city's overnight mobility backbone — critical for shift workers and hospitality staff.

**Limitations:** Requires the data slice to include overnight hours.

**How additional GTFS Static would improve it:** `shapes.txt` would draw NightBus corridors so the city's overnight network shape is visible at a glance.

---

## `peak_hour_transit_density_map.html`

**What this map shows:** Heatmap of vehicle positions during the PM peak window (15:00–17:59 Vancouver local time). Bright areas = high concentration of bus vehicle-reports.

**Why it matters operationally:** Identifies the city's evening operational hotspots — where the bus network is most concentrated when commute volumes are highest. Useful for planning bus-lane priority and signal pre-emption.

**Limitations:** High intensity correlates with BOTH high service frequency AND slow vehicle movement (more position reports per unit distance). The map cannot distinguish these on its own.

**How additional GTFS Static would improve it:** Combining with derived speed (from position deltas) would separate 'busy because frequent' from 'busy because stuck'. `stops.txt` would identify whether hotspots cluster at major interchanges (expected) or mid-corridor (potential congestion).

---

## `multimodal_transit_density_heatmap.html`

**What this map shows:** Aggregate heatmap of all observed vehicle positions across the entire analysis window, all modes.

**Why it matters operationally:** The city's transit operational footprint, integrated over time. The brightest pixels are where TransLink's surface fleet spends the most vehicle-hours — these are the corridors any disruption (weather, construction, special event) would affect most.

**Limitations:** Still bus-only because that's what the feed provides. The density signal blends service-frequency with vehicle dwell time.

**How additional GTFS Static would improve it:** Adding SkyTrain real-time data would re-balance the heatmap to reflect true multimodal operational footprint. `shapes.txt` polylines would let us identify which exact corridors anchor the brightest hot zones.

---

