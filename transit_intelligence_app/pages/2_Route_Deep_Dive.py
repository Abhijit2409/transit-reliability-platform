"""Route Deep-Dive — why is this route fragile, and when?"""
import streamlit as st
import pandas as pd

from core.theme import (APP_CSS, RELIABILITY_BAND_COLORS, FIFA_BAND_COLORS,
                        band_pill, SCENARIO_LABELS)
st.markdown(APP_CSS, unsafe_allow_html=True)

from core import data_loader as dl
from core import state, metrics
from core.fifa_scenarios import scenario_scores, adjusted_reliability
from components import charts

route, scenario = state.sidebar_selectors()

priority = dl.load_priority()
reliability = dl.load_reliability()
fifa_stress = dl.load_fifa_stress()
hourly = dl.load_hourly()
hotspots = dl.load_hotspots()

rec = metrics.route_record(route, priority, reliability, fifa_stress)
p = rec["priority"]

if p is None:
    st.error(f"No data for route {route}.")
    st.stop()

# ---- header ----
st.title(f"Route {route} — {p['route_long_name']}")
band = p["reliability_band"]
st.markdown(
    f"{p['route_type']} &nbsp;·&nbsp; reliability **{p['reliability_score']}** "
    f"{band_pill(band, RELIABILITY_BAND_COLORS)} &nbsp;·&nbsp; "
    f"priority rank by intervention score", unsafe_allow_html=True)

# ---- KPI strip ----
charts.kpi_strip([
    {"label": "Reliability score", "value": p["reliability_score"]},
    {"label": "Bunching rate", "value": f'{p["bunching_rate_pct"]}%'},
    {"label": "Severe rate", "value": f'{p["severe_bunching_rate_pct"]}%'},
    {"label": "Peak bunching share", "value": f'{p["peak_bunching_share_pct"]}%'},
    {"label": "Hotspot segments", "value": int(p["hotspot_segments"])},
])

st.markdown("")
c1, c2 = st.columns([1, 1.1])

# ---- reliability anatomy ----
with c1:
    st.subheader("Why this score?")
    st.caption("What drags the route's reliability down, decomposed.")
    anatomy = metrics.reliability_anatomy(p)
    st.plotly_chart(charts.chart_reliability_anatomy(anatomy),
                    use_container_width=True)

# ---- hourly curve ----
with c2:
    st.subheader("When does it bunch?")
    st.caption("Diurnal bunching pattern — peaks should match the schedule.")
    hr = hourly[hourly["route_short_name"] == route]
    if len(hr):
        st.plotly_chart(charts.chart_hourly(hr), use_container_width=True)
    else:
        st.info("No hourly pattern recorded for this route.")

# ---- FIFA strip ----
st.markdown("---")
st.subheader(f"FIFA impact — {SCENARIO_LABELS[scenario]}")
ss = scenario_scores(fifa_stress, scenario)
adj = adjusted_reliability(ss, reliability)
row = adj[adj["route_short_name"] == route]
if len(row):
    r = row.iloc[0]
    fb = r["fifa_risk_band"]
    charts.kpi_strip([
        {"label": "Baseline reliability", "value": r["reliability_score"]},
        {"label": "FIFA-adjusted", "value": r["fifa_adjusted_reliability"],
         "sub": f'−{r["reliability_drop"]} pts'},
        {"label": "FIFA stress score", "value": f'{r["fifa_stress_score"]:.0f}'},
        {"label": "Risk band", "value": fb},
    ])
else:
    st.info("No FIFA scenario data for this route.")

# ---- Why is this route risky? (decomposition + plain-English) ----
st.markdown("---")
st.subheader("Why is this route risky?")
from core import explain as ex
from core.fifa_scenarios import _reference_max, _raw_stress
frow = fifa_stress[fifa_stress["route_short_name"] == route]
if len(frow):
    fr = frow.iloc[0]
    ref = _reference_max(fifa_stress)
    sc_score = round(_raw_stress(fr, ex.SCENARIO_PRESSURE.get(scenario, 0)) / (ref or 1) * 100, 1)
    decomp = ex.decompose(fr, scenario)
    drop = row.iloc[0]["reliability_drop"] if len(row) else 0
    narrative = ex.route_narrative(fr, scenario, sc_score, drop,
                                   SCENARIO_LABELS[scenario].split("(")[0].strip())
    dcol, ncol = st.columns([1.1, 1])
    with dcol:
        st.plotly_chart(charts.chart_contribution(decomp, sc_score),
                        use_container_width=True)
        charts.chart_note("The slices add up to this route's FIFA stress score. "
                          "A long bar means that driver is doing most of the work.")
    with ncol:
        charts.callout(f"Risk explanation — {route}", narrative,
                       kind="danger" if sc_score >= 65 else
                            ("warning" if sc_score >= 25 else "info"))
        st.caption("Updates automatically with the selected route and scenario.")
else:
    st.info("No decomposition available for this route.")

# ---- compare against another route (mini-panel) ----
st.markdown("---")
st.subheader("Compare against another route")
others = [r for r in dl.route_options() if r != route]
other = st.selectbox("Compare with", others, key="dd_compare",
                     help="Quick A/B. Full side-by-side lives in Route Comparison.")
comp_rows = priority[priority["route_short_name"].isin([route, other])].copy()
comp_rows["route_short_name"] = pd.Categorical(
    comp_rows["route_short_name"], [route, other], ordered=True)
comp_rows = comp_rows.sort_values("route_short_name")
cc1, cc2, cc3 = st.columns(3)
with cc1:
    st.plotly_chart(charts.chart_compare_bars(
        comp_rows, "reliability_score", "Reliability"), use_container_width=True)
with cc2:
    st.plotly_chart(charts.chart_compare_bars(
        comp_rows, "bunching_rate_pct", "Bunching %"), use_container_width=True)
with cc3:
    st.plotly_chart(charts.chart_compare_bars(
        comp_rows, "intervention_priority_score", "Priority"),
        use_container_width=True)
if st.button(f"Full comparison: {route} vs {other} →"):
    st.session_state["cmp_a"] = route
    st.session_state["cmp_b"] = other
    st.switch_page("pages/6_Route_Comparison.py")


# ---- this route's hotspots ----
st.markdown("---")
st.subheader("Hotspots on this route")
st.caption("Bunching concentrations between named stops, by direction.")
hs = hotspots[hotspots["route_short_name"] == route].copy()
if len(hs):
    show = hs.sort_values("bunching_events", ascending=False)[
        ["direction_id", "stop_before_name", "stop_after_name",
         "bunching_events", "severe_events", "median_gap_km",
         "closest_gap_km", "vehicles_involved"]].rename(columns={
            "direction_id": "Dir", "stop_before_name": "From stop",
            "stop_after_name": "To stop", "bunching_events": "Events",
            "severe_events": "Severe", "median_gap_km": "Median gap km",
            "closest_gap_km": "Closest km", "vehicles_involved": "Vehicles"})
    st.dataframe(show, use_container_width=True, hide_index=True, height=300)
    # per-direction strip for this one route (y = direction)
    hs_dir = hs.copy()
    hs_dir["route_short_name"] = "dir " + hs_dir["direction_id"].astype(str)
    st.plotly_chart(charts.chart_hotspot_strip(hs_dir), use_container_width=True)
    charts.explain("Bubbles positioned by distance along the route; bigger = "
                   "more events, darker = more severe. Recurring clusters mark "
                   "where to focus supervision.")
    st.caption("Want the full network view? Open the Hotspot Explorer from the "
               "sidebar.")
else:
    st.info("No hotspot segments recorded for this route.")

st.markdown("---")
cta1, cta2 = st.columns(2)
with cta1:
    if st.button("Explore tournament impact in FIFA Lab →"):
        st.switch_page("pages/4_FIFA_Stress_Lab.py")
with cta2:
    if st.button("← Back to Network Overview"):
        st.switch_page("pages/1_Network_Overview.py")
