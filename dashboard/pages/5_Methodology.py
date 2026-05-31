"""Methodology & Limitations — the credibility page."""
import streamlit as st

from core.theme import APP_CSS
st.markdown(APP_CSS, unsafe_allow_html=True)
from core import state
state.sidebar_selectors()

st.title("Methodology & limitations")
st.caption("How I produce the numbers, and exactly where to distrust them. "
           "This project analyzes real TransLink GTFS-Realtime telemetry that "
           "I collect, process, and score end to end.")

st.subheader("My pipeline")
st.markdown("""
1. **Collect** GTFS-Realtime vehicle positions every 30 seconds → raw parquet.
2. **Load** telemetry into DuckDB; **join** GTFS-Realtime trips to GTFS Static.
3. **Project** each vehicle GPS point onto its route shape (nearest-vertex
   approximation) to get distance-along-route in km.
4. **Spacing** between consecutive buses on the same direction/shape, after
   removing terminal/layover zones and high projection-error points.
5. **Bunching** events where spacing falls below thresholds; classified
   moderate / high / severe.
6. **Reliability score** per route from bunching rate, severe rate, and peak
   concentration.
7. **Corridor priority** ranking and **stop-level hotspots** with names.
8. **FIFA stress simulation** + **ML-assisted risk classification** layered on
   the baseline.
""")

st.subheader("How each score is computed")
st.markdown("""
- **Reliability score** — higher is better; driven down by bunching, severe
  events, and peak concentration.
- **Intervention priority** — combines event volume, severity, and the worst
  single segment; higher = more to gain by fixing.
- **FIFA stress score** — `base_fragility × exposure_multiplier ×
  (1 + scenario_pressure)`, then min-max scaled to 0–100. Base fragility is
  `(100 − reliability) + bunching_rate + 2 × severe_rate`. The exposure
  multiplier sums transparent uplifts (BC Place, downtown, Fan Festival,
  SkyTrain connector, peak vulnerability, existing weakness).
- **FIFA-adjusted reliability** — baseline reliability minus a penalty
  proportional to the stress score (max 25 points).
""")

st.subheader("Limitations — read before trusting any number")
st.markdown("""
- **Single service date.** All baselines reflect one day of telemetry;
  multi-day baselines would stabilize the estimates.
- **Self-labeled ML.** The classifier is trained on the FIFA stress score we
  defined. High accuracy is *expected* and is **not** evidence of real-world
  predictive skill. The model is used for explainability and consistency
  checks only.
- **Hand-assigned exposure tags.** BC Place / downtown / Fan Festival flags
  rely on route knowledge, not geometry. They should be reviewed by local
  planners; geometry-based tagging is a future improvement.
- **No geographic coordinates in hotspot outputs.** Hotspots carry stop names
  and distances but not lat/lon, so the app plots them on a distance-along-
  route axis rather than a map. Geocoding stops → a true marker map is the
  planned v2 upgrade (the Hotspot Map page).
- **Nearest-vertex projection.** Distance-along-route is approximate; full
  line-segment projection would be more precise.
- **Not a live feed.** Everything here is analysis of collected telemetry.
""")

st.subheader("What real validation would require")
st.markdown("""
Actual FIFA-matchday AVL telemetry, automated passenger counts, fare-tap
origin–destination data, planned service-change schedules, and event-day
crowd-flow estimates — then retraining against *observed* matchday degradation
rather than a self-defined label.
""")

st.info("**On the roadmap (v2):** an interactive Hotspot Map with geocoded "
        "stops and clickable stop-pair drilldown; weight-sensitivity sliders "
        "on the FIFA exposure config; and a one-click route-briefing export.")

# ---- Project Impact (recruiter / interview demonstration) ----
st.markdown("---")
st.subheader("Project impact")
st.caption("How I turn raw telemetry into an operational decision — the "
           "end-to-end value chain I built for this project.")
from components import charts
st.plotly_chart(charts.project_impact_flow(), use_container_width=True)
charts.chart_note("Each layer feeds the next: a 30-second data feed becomes a "
                  "reliability engine, becomes a FIFA stress simulation, becomes "
                  "a concrete answer to 'which corridor needs support, and when.'")
