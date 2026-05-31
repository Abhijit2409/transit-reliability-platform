"""FIFA 2026 Stress Lab — corrected scenario recompute + honest ML support."""
import streamlit as st
import pandas as pd

from core.theme import (APP_CSS, FIFA_BAND_COLORS, SCENARIO_LABELS, band_pill)
st.markdown(APP_CSS, unsafe_allow_html=True)

from core import data_loader as dl
from core import state, metrics
from core.fifa_scenarios import (all_scenarios_matrix, scenario_scores,
                                 adjusted_reliability, scenario_band_counts,
                                 scenario_delta)
from components import charts

route, scenario = state.sidebar_selectors()

reliability = dl.load_reliability()
priority = dl.load_priority()
stress_base = dl.load_fifa_stress()          # has base_fragility, stress_multiplier...
ml = dl.load_fifa_ml_predictions()
imp = dl.load_fifa_feature_importance()
model_summary = dl.load_fifa_model_summary()

scen_label = SCENARIO_LABELS[scenario].split("(")[0].strip()

st.title("FIFA 2026 transit stress lab")
st.caption("A scenario-based stress lens on the baseline network. "
           "Change the FIFA scenario in the sidebar — every panel updates live. "
           "So what? — it shows which corridors to staff up, and on which match days.")

# ---- recompute (correctly differentiated across scenarios) ----
matrix = all_scenarios_matrix(stress_base)
ss = scenario_scores(stress_base, scenario)
adj = adjusted_reliability(ss, reliability)
counts = scenario_band_counts(ss)
delta = scenario_delta(stress_base, scenario)
gap = metrics.extra_service_gap(priority)
top_mover = delta.iloc[0]

# ---- KPI cards ----
charts.kpi_strip([
    {"label": "Scenario", "value": scen_label,
     "accent": FIFA_BAND_COLORS["High"]},
    {"label": "Critical + High routes",
     "value": counts["Critical"] + counts["High"],
     "sub": f'{counts["Critical"]} critical · {counts["High"]} high'},
    {"label": "Avg reliability drop",
     "value": f'{adj["reliability_drop"].mean():.1f} pts'},
    {"label": "Biggest mover vs normal",
     "value": top_mover["route_short_name"],
     "sub": f'+{top_mover["stress_delta"]:.0f} stress'},
    {"label": "Extra-service gap",
     "value": len(gap["gap"]), "sub": "routes outside Top 20"},
])

# ---- headline: heatmap + delta side by side ----
st.markdown("")
st.subheader("FIFA Stress by Route and Scenario")
c1, c2 = st.columns([1.15, 1])
with c1:
    st.plotly_chart(
        charts.chart_scenario_heatmap(matrix, highlight_scenario=scenario),
        use_container_width=True)
    charts.explain(
        f"Each cell is a route's FIFA stress score under one scenario; color "
        f"maps to the risk band. The boxed column is your selected scenario "
        f"({scen_label}). Read left→right along any row to see how much that "
        f"route worsens as demand pressure rises.")
with c2:
    st.plotly_chart(
        charts.chart_scenario_delta(delta, scen_label, n=12),
        use_container_width=True)
    charts.explain(
        f"How much each route's stress score climbs from a normal day to "
        f"{scen_label}. The routes at the top change the most — they are where "
        f"FIFA demand does the real damage, not just where stress is already high.")

# ---- before vs after dumbbell ----
st.markdown("---")
st.subheader(f"Baseline vs {scen_label} reliability")
st.plotly_chart(
    charts.chart_dumbbell(adj, "reliability_score", "fifa_adjusted_reliability",
                          "route_short_name", base_name="Baseline",
                          adj_name=f"{scen_label}"),
    use_container_width=True)
charts.explain("Red dot = the route's reliability under this scenario; blue = "
               "today. The longer the connecting line, the harder FIFA hits "
               "that corridor.")

# ---- route-level explanation (driven by sidebar route) ----
st.markdown("---")
st.subheader(f"Route focus: {route}")
rrow = adj[adj["route_short_name"] == route]
drow = delta[delta["route_short_name"] == route]
if len(rrow) and len(drow):
    r, dd = rrow.iloc[0], drow.iloc[0]
    charts.kpi_strip([
        {"label": "Baseline reliability", "value": r["reliability_score"]},
        {"label": f"{scen_label} reliability",
         "value": r["fifa_adjusted_reliability"],
         "sub": f'−{r["reliability_drop"]} pts'},
        {"label": "Stress (normal)", "value": f'{dd["stress_normal"]:.0f}',
         "sub": dd["band_normal"]},
        {"label": f"Stress ({scen_label})", "value": f'{dd["stress_scenario"]:.0f}',
         "sub": dd["band_scenario"], "accent": FIFA_BAND_COLORS.get(dd["band_scenario"])},
    ])
    moved = dd["band_normal"] != dd["band_scenario"]
    charts.callout(
        f"What happens to route {route} under {scen_label}",
        f"Stress rises from {dd['stress_normal']:.0f} to {dd['stress_scenario']:.0f} "
        f"(+{dd['stress_delta']:.0f}), "
        + (f"pushing it from <b>{dd['band_normal']}</b> to "
           f"<b>{dd['band_scenario']}</b> risk. " if moved else
           f"staying within the <b>{dd['band_scenario']}</b> band. ")
        + f"Projected reliability falls {r['reliability_drop']:.1f} points.",
        kind="danger" if dd["band_scenario"] == "Critical" else
             ("warning" if dd["band_scenario"] in ("High", "Medium") else "info"))

    # Why is this route risky? — decomposition shared with the deep-dive
    from core import explain as ex
    st.markdown(f"###### Why is route {route} risky?")
    frow = stress_base[stress_base["route_short_name"] == route]
    if len(frow):
        wc1, wc2 = st.columns([1.1, 1])
        decomp = ex.decompose(frow.iloc[0], scenario)
        with wc1:
            st.plotly_chart(charts.chart_waterfall(decomp),
                            use_container_width=True)
            charts.chart_note("How each driver stacks up to build the stress "
                              "score under this scenario.")
        with wc2:
            narrative = ex.route_narrative(
                frow.iloc[0], scenario, dd["stress_scenario"],
                r["reliability_drop"], scen_label)
            charts.callout(f"Risk explanation — {route}", narrative,
                           kind="danger" if dd["band_scenario"] == "Critical"
                           else ("warning" if dd["band_scenario"] in
                                 ("High", "Medium") else "info"))
else:
    st.info(f"No FIFA scenario data for route {route}.")

# ---- ML section (honest) ----
st.markdown("---")
st.subheader("ML decision support")
charts.honesty_tag("ML is decision support, not a forecast — trained on my "
                   "own scenario labels; it validates internal consistency only.")
st.markdown(
    "Because the model is trained on the FIFA stress score I defined, strong "
    "accuracy is *expected and not evidence of real-world skill*. Its value is "
    "the **feature story** and flagging routes where a tree model **disagrees** "
    "with the linear scenario scoring.")
m1, m2 = st.columns([1, 1.1])
with m1:
    st.markdown("**What drives the risk**")
    st.plotly_chart(charts.chart_feature_importance(imp, n=10),
                    use_container_width=True)
    charts.explain("Which baseline features the model leans on. Existing "
                   "fragility and venue/peak exposure dominate — consistent "
                   "with how the scenario formula is built.")
with m2:
    st.markdown("**Scenario score vs ML probability**")
    st.plotly_chart(charts.chart_scenario_ml_scatter(ml),
                    use_container_width=True)
    disagree = ml[ml["agreement"] == "DISAGREE"]
    if len(disagree):
        charts.callout("Worth a human look",
                       "ML and scenario scoring disagree on: "
                       f"{', '.join(disagree['route_short_name'])}.",
                       kind="warning")
    else:
        charts.callout("Models agree",
                       "The ML classifier and scenario scoring agree on every "
                       "route.", kind="success")

st.markdown("**Model summary**")
st.dataframe(model_summary, use_container_width=True, hide_index=True)

if gap["gap"]:
    charts.callout(
        "Coverage finding",
        f"TransLink extra-service routes <b>{', '.join(gap['gap'])}</b> are "
        f"outside the monitored Top 20 — add telemetry before the tournament.",
        kind="info")

st.markdown("---")
if st.button("← Back to Network Overview"):
    st.switch_page("pages/1_Network_Overview.py")
