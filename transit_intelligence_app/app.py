"""Vancouver Transit Reliability Intelligence Platform — entry point.

Run with:  streamlit run app.py

I use st.navigation so the sidebar shows exactly the six product pages
(no duplicate "app" entry). This file owns the single set_page_config call,
injects global CSS once, and registers the pages in order.
"""
import streamlit as st

st.set_page_config(
    page_title="Vancouver Transit Reliability Intelligence",
    page_icon="🚌", layout="wide", initial_sidebar_state="expanded")

from core.theme import APP_CSS
from core import state

st.markdown(APP_CSS, unsafe_allow_html=True)
state.init_state()

# Register pages explicitly -> clean sidebar, no auto-discovered duplicate.
pages = [
    st.Page("pages/1_Network_Overview.py", title="Network Overview",
            icon="🗺️", default=True),
    st.Page("pages/2_Route_Deep_Dive.py", title="Route Deep Dive", icon="🔍"),
    st.Page("pages/3_Hotspot_Explorer.py", title="Hotspot Explorer", icon="📍"),
    st.Page("pages/4_FIFA_Stress_Lab.py", title="FIFA Stress Lab", icon="⚽"),
    st.Page("pages/5_Methodology.py", title="Methodology", icon="📐"),
    st.Page("pages/6_Route_Comparison.py", title="Route Comparison", icon="⚖️"),
]
nav = st.navigation(pages, position="sidebar")
nav.run()
