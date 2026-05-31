"""Shared render body for the Network Overview, imported by both app.py
(home route) and pages/1_Network_Overview.py so there's a single source.
"""
import streamlit as st
import pandas as pd

from core import data_loader as dl
from core import state, metrics
from core.theme import RELIABILITY_BAND_COLORS, band_pill, SCENARIO_LABELS
from components import charts


def render():
    state.sidebar_selectors()

    st.title("Vancouver Transit Reliability Intelligence")
    st.caption("Bus bunching & corridor reliability across the 20 highest-"
               "activity TransLink corridors, with a FIFA 2026 stress lens.")

    priority = dl.load_priority()
    fifa_rank = dl.load_fifa_ranking()
    hotspots = dl.load_hotspots()

    # ---- verdict ----
    verdict = metrics.verdict_sentence(priority, fifa_rank,
                                       scenario_label="a PM-peak match")
    charts.verdict_banner(verdict)

    # ---- KPI strip ----
    k = metrics.network_kpis(priority)
    charts.kpi_strip([
        {"label": "Routes monitored", "value": k["routes_monitored"]},
        {"label": "Network avg reliability", "value": k["avg_reliability"]},
        {"label": "Total bunching events", "value": f'{k["total_bunching"]:,}'},
        {"label": "Worst corridor",
         "value": k["worst_corridor"], "sub": f'score {k["worst_corridor_score"]}'},
        {"label": "Biggest win if fixed",
         "value": k["biggest_win"],
         "sub": f'priority {k["biggest_win_score"]}'},
    ])

    # ---- executive callouts (operational intelligence cards) ----
    st.markdown("##### Operational intelligence")
    cards = metrics.executive_callouts(priority, fifa_rank, hotspots)
    ccols = st.columns(len(cards))
    accents = ["#A32D2D", "#D85A30", "#EF9F27", "#534AB7"]
    for col, card, acc in zip(ccols, cards, accents):
        with col:
            charts.callout(card["title"],
                           f'<b style="font-size:1.15rem;">{card["value"]}</b><br>'
                           f'<span style="font-size:0.84rem;">{card["sub"]}</span>',
                           kind="info")

    st.markdown("")
    left, right = st.columns([1.05, 1])

    # ---- left: reliability ranking (the 'map' substitute, honest) ----
    with left:
        st.subheader("Corridor Reliability Ranking")
        charts.honesty_tag("Ranked view — geographic map is a v2 upgrade "
                           "(stops need geocoding)")
        d = priority.sort_values("reliability_score").copy()
        colors = [RELIABILITY_BAND_COLORS.get(b, "#888") for b in d["reliability_band"]]
        import plotly.graph_objects as go
        fig = go.Figure(go.Bar(
            x=d["reliability_score"], y=d["route_short_name"], orientation="h",
            marker_color=colors, text=d["reliability_band"],
            textposition="inside", insidetextanchor="end",
            hovertemplate="%{y}: %{x} reliability<extra></extra>"))
        fig.update_xaxes(title="Reliability score", range=[60, 100])
        fig.update_yaxes(title="Route / Corridor")
        fig = charts._layout(fig, height=560)
        st.plotly_chart(fig, use_container_width=True)
        charts.chart_note("Each corridor's reliability score, colored by band. "
                          "Routes at the bottom are the network's weak points "
                          "before any FIFA load.")

    # ---- right: explorable corridor table ----
    with right:
        st.subheader("Explore corridors")
        types = sorted(priority["route_type"].unique())
        sel_types = st.multiselect("Route type", types, default=types)
        only_fragile = st.checkbox("Show only fragile (below-median reliability)")

        view = priority[priority["route_type"].isin(sel_types)].copy()
        if only_fragile:
            view = metrics.fragile_routes(view)

        table = view.sort_values("intervention_priority_score", ascending=False)[
            ["route_short_name", "route_long_name", "route_type",
             "reliability_score", "reliability_band", "bunching_rate_pct",
             "intervention_priority_score"]].rename(columns={
                "route_short_name": "Route", "route_long_name": "Name",
                "route_type": "Type", "reliability_score": "Reliability",
                "reliability_band": "Band", "bunching_rate_pct": "Bunch %",
                "intervention_priority_score": "Priority"})
        st.dataframe(table, use_container_width=True, hide_index=True,
                     height=360)

        st.markdown("**Jump to a route**")
        opts = view.sort_values("intervention_priority_score",
                                ascending=False)["route_short_name"].tolist()
        pick = st.selectbox("Open in Route Deep-Dive", opts,
                            label_visibility="collapsed")
        if st.button("Open Route Deep-Dive →", type="primary"):
            state.set_route(pick)
            st.switch_page("pages/2_Route_Deep_Dive.py")

    # ---- extra-service gap finding ----
    gap = metrics.extra_service_gap(priority)
    if gap["gap"]:
        st.info(
            f"**Coverage finding:** TransLink's announced FIFA extra-service "
            f"routes **{', '.join(gap['gap'])}** are *not* in the monitored "
            f"Top 20 — they receive added service but currently have no "
            f"reliability coverage. Recommend extending telemetry before the "
            f"tournament. (Monitored & served: {', '.join(gap['monitored']) or 'none'}.)")
