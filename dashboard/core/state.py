"""Cross-page state. The selected route + scenario live in st.session_state
so a choice made anywhere (table click, sidebar dropdown) follows the user
to every page. This is what makes the app feel like one product, not five
disconnected scripts.
"""
import streamlit as st
from core import data_loader as dl
from core.theme import SCENARIO_LABELS, SCENARIO_ORDER


def init_state():
    if "selected_route" not in st.session_state:
        opts = dl.route_options()
        st.session_state.selected_route = opts[0] if opts else None
    if "selected_scenario" not in st.session_state:
        st.session_state.selected_scenario = "fifa_pmpeak_match"


def set_route(route: str):
    st.session_state.selected_route = route


def get_route() -> str:
    init_state()
    return st.session_state.selected_route


def get_scenario() -> str:
    init_state()
    return st.session_state.selected_scenario


def sidebar_selectors():
    """Render the two global selectors in the sidebar; return (route, scenario)."""
    init_state()
    st.sidebar.markdown("### Global filters")
    opts = dl.route_options()
    cur = st.session_state.selected_route
    idx = opts.index(cur) if cur in opts else 0
    route = st.sidebar.selectbox(
        "Route", opts, index=idx,
        help="Drives the Route Deep-Dive and the FIFA lab highlight.")
    st.session_state.selected_route = route

    scur = st.session_state.selected_scenario
    sidx = SCENARIO_ORDER.index(scur) if scur in SCENARIO_ORDER else 2
    scenario = st.sidebar.selectbox(
        "FIFA scenario", SCENARIO_ORDER, index=sidx,
        format_func=lambda k: SCENARIO_LABELS[k],
        help="Drives the FIFA Stress Lab and the deep-dive FIFA strip.")
    st.session_state.selected_scenario = scenario

    st.sidebar.markdown("---")
    st.sidebar.caption(
        "Analysis of collected GTFS-Realtime telemetry for one service date. "
        "Not a live feed.")
    return route, scenario
