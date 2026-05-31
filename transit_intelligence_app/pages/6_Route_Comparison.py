"""Route Comparison — side-by-side A vs B for service planners."""
import streamlit as st
import pandas as pd

from core.theme import APP_CSS, RELIABILITY_BAND_COLORS, FIFA_BAND_COLORS, band_pill, SCENARIO_LABELS
st.markdown(APP_CSS, unsafe_allow_html=True)

from core import data_loader as dl
from core import state, explain as ex
from core.fifa_scenarios import scenario_scores, adjusted_reliability
from components import charts

route, scenario = state.sidebar_selectors()

priority = dl.load_priority()
reliability = dl.load_reliability()
fifa_stress = dl.load_fifa_stress()
hotspots = dl.load_hotspots()
opts = dl.route_options()

st.title("Route comparison")
st.caption("Side-by-side reliability and FIFA risk for any two corridors. "
           "So what? — decide which of two routes needs support first.")

# selectors (searchable; pre-filled from deep-dive hand-off if present)
a_default = st.session_state.get("cmp_a", opts[0])
b_default = st.session_state.get("cmp_b", opts[1] if len(opts) > 1 else opts[0])
c1, c2 = st.columns(2)
with c1:
    a = st.selectbox("Route A", opts, index=opts.index(a_default)
                     if a_default in opts else 0)
with c2:
    b_opts = [r for r in opts if r != a]
    b = st.selectbox("Route B", b_opts, index=b_opts.index(b_default)
                     if b_default in b_opts else 0)

# assemble comparison frame
ss = scenario_scores(fifa_stress, scenario)
adj = adjusted_reliability(ss, reliability)
hs_counts = (hotspots.groupby("route_short_name")
             .agg(hotspot_count=("bunching_events", "size"),
                  severe_total=("severe_events", "sum")).reset_index())

base = priority.merge(adj[["route_short_name", "fifa_stress_score",
                           "fifa_risk_band", "fifa_adjusted_reliability",
                           "reliability_drop"]], on="route_short_name", how="left")
base = base.merge(hs_counts, on="route_short_name", how="left")

pair = base[base["route_short_name"].isin([a, b])].copy()
pair["route_short_name"] = pd.Categorical(pair["route_short_name"], [a, b], ordered=True)
pair = pair.sort_values("route_short_name")

if len(pair) < 2:
    st.warning("Pick two distinct routes with data.")
    st.stop()

ra, rb = pair.iloc[0], pair.iloc[1]

# ---- headline metric table ----
st.markdown("##### Head to head")
metrics_tbl = pd.DataFrame({
    "Metric": ["Route type", "Reliability score", "Reliability band",
               "FIFA stress score", "FIFA risk band", "FIFA-adjusted reliability",
               "Reliability drop (pts)", "Bunching rate %", "Severe bunching %",
               "Peak bunching share %", "Hotspot segments", "Severe hotspot events",
               "Intervention priority"],
    a: [ra["route_type"], ra["reliability_score"], ra["reliability_band"],
        f'{ra["fifa_stress_score"]:.0f}', ra["fifa_risk_band"],
        ra["fifa_adjusted_reliability"], ra["reliability_drop"],
        ra["bunching_rate_pct"], ra["severe_bunching_rate_pct"],
        ra["peak_bunching_share_pct"], int(ra.get("hotspot_count", 0) or 0),
        int(ra.get("severe_total", 0) or 0), ra["intervention_priority_score"]],
    b: [rb["route_type"], rb["reliability_score"], rb["reliability_band"],
        f'{rb["fifa_stress_score"]:.0f}', rb["fifa_risk_band"],
        rb["fifa_adjusted_reliability"], rb["reliability_drop"],
        rb["bunching_rate_pct"], rb["severe_bunching_rate_pct"],
        rb["peak_bunching_share_pct"], int(rb.get("hotspot_count", 0) or 0),
        int(rb.get("severe_total", 0) or 0), rb["intervention_priority_score"]],
})
# Each route column mixes text (type, band) and numbers (scores). Arrow
# can't serialize a mixed-type object column, so render everything as text.
for col in (a, b):
    metrics_tbl[col] = metrics_tbl[col].astype(str)
st.dataframe(metrics_tbl, use_container_width=True, hide_index=True, height=500)

# ---- visual comparison ----
st.markdown("##### Visual comparison")
v1, v2 = st.columns([1, 1])
with v1:
    st.plotly_chart(charts.chart_compare_radar(pair, {
        "bunching_rate_pct": ("Bunching", True),
        "severe_bunching_rate_pct": ("Severe", True),
        "peak_bunching_share_pct": ("Peak conc.", True),
        "fifa_stress_score": ("FIFA stress", True),
        "reliability_score": ("Reliability", False),
    }), use_container_width=True)
    charts.chart_note("Larger shape = more risk on every axis (reliability is "
                      "inverted so outward always means worse).")
with v2:
    st.plotly_chart(charts.chart_compare_bars(pair, "fifa_stress_score",
                    "FIFA stress score", fmt="{:.0f}"), use_container_width=True)
    st.plotly_chart(charts.chart_compare_bars(pair, "reliability_drop",
                    "FIFA reliability drop (pts)", fmt="{:.1f}"),
                    use_container_width=True)

# ---- verdict ----
worse = a if ra["fifa_stress_score"] >= rb["fifa_stress_score"] else b
better = b if worse == a else a
charts.callout(
    f"Planning verdict — {SCENARIO_LABELS[scenario].split('(')[0].strip()}",
    f"<b>{worse}</b> carries the higher FIFA risk and should be prioritized for "
    f"monitoring or service support over <b>{better}</b>. Compare the radar: the "
    f"larger footprint is the more exposed corridor.",
    kind="warning")

st.markdown("---")
if st.button("← Back to Network Overview"):
    st.switch_page("pages/1_Network_Overview.py")
