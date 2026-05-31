"""Hotspot Explorer — an operations investigation tool.

Where is bunching risk concentrated? Filter by route and severity, read the
stop-pair table, and see intensity on a distance-along-route strip chart.

No geographic coordinates exist in the hotspot outputs, so we do NOT fake a
map. We use a route-distance strip chart (how planners read corridor
strip-charts): x = segment km, y = route, bubble size = events, color =
severity. This updates live with the filters.
"""
import streamlit as st
import pandas as pd

from core.theme import APP_CSS, FIFA_BAND_COLORS, band_pill
st.markdown(APP_CSS, unsafe_allow_html=True)

from core import data_loader as dl
from core import state
from components import charts

route, scenario = state.sidebar_selectors()

hotspots = dl.load_hotspots()
try:
    fifa_hot = dl.load_fifa_hotspots()
    has_fifa = True
except Exception:
    has_fifa = False

st.title("Hotspot explorer")
st.caption("Investigate where bunching concentrates, between named stops. "
           "Built for dispatch and field-supervision planning. "
           "So what? — it pinpoints the named segments where supervisors should stand.")

# ----------------- filters (page-local, update everything live) -----------
f1, f2, f3 = st.columns([1, 1, 1.2])
with f1:
    route_opts = ["All routes"] + sorted(hotspots["route_short_name"].unique(),
                                         key=lambda x: (len(x), x))
    # default to the sidebar-selected route if it has hotspots
    default_idx = route_opts.index(route) if route in route_opts else 0
    sel_route = st.selectbox("Route", route_opts, index=default_idx)
with f2:
    sev_mode = st.selectbox(
        "Severity focus",
        ["All events", "Has severe events", "Severe-heavy (≥5 severe)"])
with f3:
    min_events = st.slider("Min bunching events in segment", 0,
                           int(hotspots["bunching_events"].max()), 0)

# scenario filter only if FIFA hotspot risk is available
use_fifa_overlay = False
if has_fifa:
    use_fifa_overlay = st.checkbox(
        "Overlay FIFA hotspot risk score (from fifa_hotspot_risk_summary.csv)",
        value=False,
        help="Adds the FIFA-weighted risk score column where available.")

# ----------------- apply filters -----------------
view = hotspots.copy()
if sel_route != "All routes":
    view = view[view["route_short_name"] == sel_route]
if sev_mode == "Has severe events":
    view = view[view["severe_events"] > 0]
elif sev_mode == "Severe-heavy (≥5 severe)":
    view = view[view["severe_events"] >= 5]
view = view[view["bunching_events"] >= min_events]

if use_fifa_overlay and has_fifa:
    cols = ["route_short_name", "direction_id", "shape_id", "route_segment_km",
            "hotspot_risk_score", "fifa_risk_band"]
    avail = [c for c in cols if c in fifa_hot.columns]
    view = view.merge(fifa_hot[avail],
                      on=[c for c in ["route_short_name", "direction_id",
                                      "shape_id", "route_segment_km"]
                          if c in avail and c in view.columns],
                      how="left")

# ----------------- KPI cards -----------------
if view.empty:
    st.warning("No hotspot segments match these filters. Loosen the severity "
               "or minimum-events filter.")
    st.stop()

worst = view.sort_values("bunching_events", ascending=False).iloc[0]
charts.kpi_strip([
    {"label": "Segments shown", "value": len(view)},
    {"label": "Routes covered", "value": view["route_short_name"].nunique()},
    {"label": "Total bunching events",
     "value": f'{int(view["bunching_events"].sum()):,}'},
    {"label": "Severe events",
     "value": f'{int(view["severe_events"].sum()):,}'},
    {"label": "Worst segment",
     "value": worst["route_short_name"],
     "sub": f'{int(worst["bunching_events"])} events @ {worst["route_segment_km"]} km'},
])

# ----------------- intensity strip chart -----------------
st.markdown("")
st.subheader("Top Bunching Hotspots")
charts.honesty_tag("Distance-along-route view — geographic map needs stop "
                   "geocoding (v2). No coordinates are fabricated.")
color_field = "severe_events"
st.plotly_chart(charts.chart_hotspot_strip(view, color_by=color_field),
                use_container_width=True)
charts.explain("Each bubble is a hotspot segment positioned by its distance "
               "along the route. Bigger = more bunching events; darker = more "
               "severe. Clusters at the same km on a route are recurring "
               "trouble spots worth a supervisor.")

# ----------------- stop-pair investigation table -----------------
st.markdown("---")
st.subheader("Segment detail")
st.caption("Sortable. Each row is a stretch between two named stops.")
table_cols = {
    "route_short_name": "Route", "route_long_name": "Name",
    "direction_id": "Dir", "route_segment_km": "Seg km",
    "stop_before_name": "From stop", "stop_after_name": "To stop",
    "bunching_events": "Events", "severe_events": "Severe",
    "avg_gap_km": "Avg gap km", "median_gap_km": "Median gap km",
    "closest_gap_km": "Closest km", "vehicles_involved": "Vehicles",
}
present = [c for c in table_cols if c in view.columns]
if use_fifa_overlay and "hotspot_risk_score" in view.columns:
    present += ["hotspot_risk_score"]
    table_cols["hotspot_risk_score"] = "FIFA risk"
show = view.sort_values("bunching_events", ascending=False)[present].rename(
    columns=table_cols)
st.dataframe(show, use_container_width=True, hide_index=True, height=420)

# ----------------- single-segment drilldown -----------------
st.markdown("---")
st.subheader("Inspect a single segment")
view = view.reset_index(drop=True)
labels = [f'{r.route_short_name} · dir {int(r.direction_id)} · '
          f'{r.stop_before_name} → {r.stop_after_name} '
          f'({int(r.bunching_events)} events)'
          for r in view.itertuples()]
pick = st.selectbox("Segment", range(len(labels)),
                    format_func=lambda i: labels[i])
seg = view.iloc[pick]
from core import explain as ex
charts.callout("Hotspot story", ex.hotspot_narrative(seg),
               kind="danger" if seg["severe_events"] >= 5 else "info")
charts.callout(
    f"Route {seg['route_short_name']} — {seg.get('route_long_name','')}",
    f"Direction {int(seg['direction_id'])} · segment at "
    f"{seg['route_segment_km']} km, between <b>{seg['stop_before_name']}</b> "
    f"and <b>{seg['stop_after_name']}</b>.<br>"
    f"{int(seg['bunching_events'])} bunching events "
    f"({int(seg['severe_events'])} severe). "
    f"Median gap {seg['median_gap_km']} km, closest {seg['closest_gap_km']} km, "
    f"{int(seg['vehicles_involved'])} vehicles involved.",
    kind="danger" if seg["severe_events"] >= 5 else "warning")

st.markdown("---")
cta1, cta2 = st.columns(2)
with cta1:
    if sel_route != "All routes" and st.button(f"Open route {sel_route} deep-dive →"):
        state.set_route(sel_route)
        st.switch_page("pages/2_Route_Deep_Dive.py")
with cta2:
    if st.button("← Back to Network Overview"):
        st.switch_page("pages/1_Network_Overview.py")
