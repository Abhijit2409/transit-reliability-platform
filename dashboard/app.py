"""Vancouver Transit Reliability Intelligence Platform — entry point.

Run with:  streamlit run app.py

I use st.navigation so the sidebar shows exactly the product pages
(no duplicate "app" entry). This file owns the single set_page_config call,
injects global CSS once, and registers the pages in order.

The AI copilot is the default landing page: it is the inviting front door to
the same governed warehouse the analytical pages explore. The dashboard pages
are unchanged; the copilot is an additional way to interact with the platform.
"""
import os

import streamlit as st

st.set_page_config(
    page_title="Vancouver Transit Reliability Intelligence",
    page_icon="🚌", layout="wide", initial_sidebar_state="expanded")

# --- Streamlit Cloud secret bridge -----------------------------------------
# On Streamlit Cloud the OpenAI key is set in the app's Secrets, exposed via
# st.secrets — but the OpenAI SDK reads os.environ. Bridge it so the copilot
# works on Cloud. Only set it when a local env var isn't already present (local
# dev wins), and guard against a missing secrets.toml so startup never crashes.
if not os.environ.get("OPENAI_API_KEY"):
    try:
        if "OPENAI_API_KEY" in st.secrets:
            os.environ["OPENAI_API_KEY"] = st.secrets["OPENAI_API_KEY"]
    except Exception:
        # No secrets.toml configured (common in local dev) — leave env as-is.
        pass

from core.theme import APP_CSS
from core import state

st.markdown(APP_CSS, unsafe_allow_html=True)
state.init_state()

pages = [
    st.Page("pages/0_Ask_Copilot.py", title="Ask the Copilot",
            icon="🤖", default=True),
    st.Page("pages/1_Network_Overview.py", title="Network Overview", icon="🗺️"),
    st.Page("pages/2_Route_Deep_Dive.py", title="Route Deep Dive", icon="🔍"),
    st.Page("pages/3_Hotspot_Explorer.py", title="Hotspot Explorer", icon="📍"),
    st.Page("pages/4_FIFA_Stress_Lab.py", title="FIFA Stress Lab", icon="⚽"),
    st.Page("pages/5_Methodology.py", title="Methodology", icon="📐"),
    st.Page("pages/6_Route_Comparison.py", title="Route Comparison", icon="⚖️"),
    st.Page("pages/7_Copilot_Evaluation.py", title="Copilot Evaluation", icon="✅"),
    st.Page("pages/8_About_Copilot.py", title="About the Copilot", icon="🛡️"),
]
nav = st.navigation(pages, position="sidebar")
nav.run()
