# Vancouver Transit Reliability Intelligence Platform

Streamlit app over an existing GTFS-Realtime reliability engine (DuckDB/SQL)
plus a FIFA 2026 stress-simulation + ML layer (Python).

## Run
```bash
pip install -r requirements.txt
streamlit run app.py
```

## Pages (MVP)
1. **Network Overview** — verdict-first landing, KPI strip, corridor ranking, explorable table, extra-service-gap finding.
2. **Route Deep-Dive** — reliability anatomy, hourly bunching curve, FIFA strip, this route's hotspots.
4. **FIFA 2026 Stress Lab** — live scenario recompute, before→after dumbbell, four-scenario comparison, honest ML decision-support section.
5. **Methodology & Limitations** — pipeline, formulas, honest caveats, v2 roadmap.

Page 3 (interactive Hotspot Map with geocoded stops) is the planned v2 headline feature; hotspots currently render on a distance-along-route axis because the baseline outputs carry stop names but no coordinates.

## Data
Place the 12 baseline CSVs in `data/`. The two layers:
- Reliability: `top20_*` (5 files)
- FIFA/ML: `fifa_*` (7 files)

## Architecture
- `core/` — cached loaders, derived metrics, FIFA scenario recompute (reuses the notebook's config), session state.
- `components/` — KPI strip, verdict banner, chart factory, shared overview body.
- `pages/` — the four MVP pages.


## Pages (updated)
1. **Network Overview** — verdict, KPI strip, **executive callout cards** (most vulnerable / largest FIFA drop / most severe hotspot / highest priority), corridor ranking, explorable table.
2. **Route Deep-Dive** — reliability anatomy, hourly curve, FIFA strip, **"Why is this route risky?"** contribution chart + plain-English explanation, **compare-against mini-panel**, this route's hotspots.
3. **Hotspot Explorer** — filters, intensity strip chart, segment table, single-segment **hotspot story** narrative.
4. **FIFA Stress Lab** — corrected scenario heatmap, delta chart, dumbbell, route-focus **waterfall decomposition + narrative**, honest ML section.
5. **Methodology** — pipeline, formulas, limitations, and the **Project Impact** flow (recruiter view).
6. **Route Comparison** — full A vs B: head-to-head table, radar, comparison bars, planning verdict.

## New since last version
- `core/explain.py` — stress decomposition + narrative engine (one source for pages 2 & 4).
- `pages/6_Route_Comparison.py` — dedicated comparison page.
- `components/charts.py` — added contribution, waterfall, comparison bars, radar, project-impact flow, and `chart_note()`.
- `core/metrics.py` — `executive_callouts()`.


## Navigation
The sidebar is defined explicitly via `st.navigation` in `app.py`, so it shows
exactly six pages in order (Network Overview, Route Deep Dive, Hotspot Explorer,
FIFA Stress Lab, Methodology, Route Comparison) with no duplicate entry point.
