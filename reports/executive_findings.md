# TransLink Multimodal Transit Operations Intelligence — Executive Findings
_Generated: 2026-05-25T05:52:37.204536+00:00_

---

## Scope & Caveats
- Analysis window: 2026-05-19 → 2026-05-23 (5 days, Tuesday–Saturday).
- All hourly outputs are in **Vancouver local time (PDT, UTC-7)**.
- The GTFS-RT vehicle position feed in this dataset contains **bus telemetry only**. SkyTrain, SeaBus, West Coast Express, and HandyDART are absent from the feed despite being defined in GTFS Static. This is treated as a finding, not a defect.
- The `speed` and `bearing` fields are 0% populated. No kinematic analysis is performed.

## Headline Numbers
- **analysis window**: 2026-05-19 → 2026-05-23
- **total records 5d**: 9307516
- **unique vehicles 5d**: 1639
- **unique routes 5d**: 225
- **modes with telemetry**: 1
- **modes in static**: 5
- **routes carrying 80pct**: 100
- **gini coefficient**: 0.4915
- **top 10pct routes share**: 32.77
- **blind spot routes**: 17

## Layer 1 — Network Foundations
- Operational activity is 100.0% bus. SkyTrain (3 lines), SeaBus, West Coast Express, and HandyDART exist in GTFS Static but contribute zero vehicle position records — TransLink's GTFS-RT vehicle feed is a bus-only feed.
- Bus sub-type composition (% of bus records): Regular Bus 84.8%, RapidBus 6.9%, Express 5.3%, B-Line 2.3%, NightBus 0.8%
- Activity is highly concentrated: 44.9% of routes (100 routes) generate 80% of records. The operational backbone is much narrower than the route catalog suggests.
- Top 3 infrastructure-critical corridors: 099 Broadway B-Line (214,370 records); 049 49th Avenue (195,742 records); R4 41st Avenue (188,891 records)

## Layer 2 — Temporal Mobility Intelligence
- Peak network hour in Vancouver local time: 16:00 with 142,650 avg records/hour. Quietest hour: 03:00 (3,943 records/hour). Activity range: 36.2× swing.
- Weekday average: 1,915,858 records/day. Weekend average: 1,644,085 records/day. Weekend operates at 85.8% of weekday volume — a service contraction, not a data gap.
- NightBus concentration check: 86.7% of NightBus activity falls between 22:00–04:59 Vancouver time. Validates that NightBus is correctly serving the overnight window — operational signature matches design.
- B-Line ↔ Regular Bus temporal correlation: +0.94. High positive correlation means the high-frequency B-Line corridor pulses with the broader bus network — they share the same commuter rhythm.
- NightBus ↔ Regular Bus temporal correlation: -0.76. Negative correlation confirms NightBus is operationally COMPLEMENTARY to the day network — it runs when nothing else does.

## Layer 3 — Reliability & Stability Intelligence
- Most operationally stable persistent routes: N22 (Macdonald, score 97.6); 209 (Mountain Hwy, score 95.2); N10 (Granville-YVR, score 95.0)
- Highest-variance persistent routes (operations worth inspecting): 080 (Marine Dr Express, CV 0.48); 602 (Bridgeport Station/Tsawwassen Heights, CV 0.47); 143 (SFU Exchange/Burquitlam Station, CV 0.45)
- Sub-type variability ranking: RapidBus is most clock-driven (median CV = 0.073), while B-Line is most demand-driven (median CV = 0.171). Higher CV at the demand-driven end is consistent with routes that adjust to ridership rather than running a fixed clock.
- Persistent routes (active ≥4 of 5 days): 222 of 225 (98.7%). The non-persistent remainder includes peak-only expresses, weekday-only specials, and routes with collection gaps.

## Layer 4 — Multimodal Coordination Intelligence
- Identified 99 bus routes serving SkyTrain corridors (by long-name match). These routes carry 40.2% of all bus telemetry — the bus network is heavily oriented toward feeding rail infrastructure.
- Feeder peak hour: 16:00 Vancouver time. Non-feeder peak hour: 16:00. The shapes overlap, suggesting feeders are temporally synchronized with the broader bus network — they don't peak earlier or later as dedicated first-mile/last-mile shuttles would.
- High-flexibility connector vehicles (serve 6+ routes in 5 days): 954. These vehicles act as fleet shock absorbers — when a route needs a bus, these are the buses that move. Worth identifying for operational resilience analysis.

## Layer 5 — Resilience & Observability Intelligence
- Network concentration: top 10% of routes carry 32.8% of activity; top 20% carry 52.5%. Gini coefficient = 0.491 (moderate inequality) — losing a few top corridors would have disproportionate operational impact.
- Telemetry blind spots by mode: {'Bus': 11, 'HandyDART': 1, 'SeaBus': 1, 'SkyTrain': 3, 'West Coast Express': 1}. The most consequential blind spots are SkyTrain (3 lines invisible) and SeaBus (1 line invisible) — high-ridership infrastructure with zero real-time observability through this feed.
- No daily volume anomalies (|z| > 1.5) detected. Weekday→Saturday variation is within expected service-design range.
- Speed and bearing fields are populated at 0% across all 5 days. This is not a pipeline issue — TransLink's GTFS-RT producer is not sending kinematic data. Any derived-speed analytics would need to be computed from consecutive position deltas instead of trusting the speed column.
- Fragile-critical routes (top-quartile load AND top-quartile CV): 6. These are the routes most worth instrumenting for deeper monitoring — they matter most AND vary most.

## Operational Recommendations
- Engage TransLink to determine why SkyTrain, SeaBus, West Coast Express, and HandyDART are absent from the GTFS-RT vehicle feed. These are mandatory multimodal coverage gaps for any real operations-intelligence platform.
- Implement derived-speed analytics from consecutive position deltas, since the `speed` field is 0% populated. Combine with route geometry (shapes.txt when available) for actual corridor speed estimation.
- Build a daily anomaly-detection alert on records-per-day and routes-active counts. Five days is enough to establish a baseline; six weeks would enable proper seasonality and weekday-pattern detection.
- Tag the fragile-critical routes (high load + high CV) for deeper instrumentation. These are the routes most worth monitoring and most likely to drive rider complaints.
- Add a `transit_mode` column at the pipeline level using `routes.txt` joins so every downstream analytical layer can stratify by mode without re-deriving classification.

## Limitations
- Five-day window is too short for weekday-pattern statistical inference. Repeat the analysis on a 4+ week window for seasonality, weekday vs weekend, and trend.
- GTFS-RT feed lacks SkyTrain / SeaBus / WCE / HandyDART vehicles. Multimodal claims in this analysis are limited to bus sub-types.
- `speed` and `bearing` are 0% populated. No claims about vehicle speed, congestion, or kinematic behavior are made from the feed alone.
- `shapes.txt` is currently unavailable. Geospatial maps use point positions only; no route-corridor polylines are drawn.
- Passenger ridership, demand, and rider behavior are NOT inferred. All findings describe operational VEHICLE PRESENCE — supply-side telemetry only.

