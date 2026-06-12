"""About the Copilot — trust & safety, in plain language.

Explains how the copilot stays trustworthy over operational data: the model
never produces numbers, a validator stands between it and the database, only
curated gold tables are queryable, and it refuses what it cannot answer
honestly. The allowlist is read live from the copilot's own guardrail config,
so this page can never drift from what the system actually enforces.
"""
import sys
from pathlib import Path

import streamlit as st
import yaml

from core.theme import APP_CSS
st.markdown(APP_CSS, unsafe_allow_html=True)
from core import state

_ROOT = Path(__file__).resolve().parent.parent
_GUARDRAIL = _ROOT / "pilot" / "config" / "guardrail_config.yaml"

state.sidebar_selectors()

st.title("About the Copilot")
st.caption("This copilot is built for an audience that distrusts black-box AI "
           "over operational data. Here is exactly how it stays trustworthy — "
           "and where it deliberately refuses to answer.")


def _config() -> dict:
    if not _GUARDRAIL.exists():
        return {}
    return yaml.safe_load(_GUARDRAIL.read_text()) or {}


cfg = _config()
allowed = cfg.get("allowed_tables", [])
max_limit = (cfg.get("query_rules", {}) or {}).get("max_limit", "—")

st.subheader("The model never produces a number")
st.markdown(
    "The language model only writes **SQL**. Every value in an answer is "
    "computed by DuckDB from the warehouse and passed back as data — the model "
    "narrates results, it does not invent them. Any required result value the "
    "narration omits is appended deterministically by code.")

st.subheader("A validator stands between the model and the database")
st.markdown(
    f"Generated SQL is parsed and checked before it can run. It must be a single "
    f"read-only `SELECT`, reference only allowlisted tables, contain no DDL/DML, "
    f"and carry an enforced `LIMIT` (max {max_limit}). Anything else is rejected "
    f"and never reaches the database. The connection itself is opened read-only, "
    f"so even a query that somehow slipped through could not write.")

st.subheader("Only curated gold tables are queryable")
if allowed:
    st.markdown("The model can see and query **only** these tables:")
    for t in allowed:
        st.markdown(f"- `{t}`")
    st.markdown('<p class="small-note">Raw telemetry, staging, and single-route '
                'tables are invisible to the model — preventing wrong-grain or '
                'mislabeled answers.</p>', unsafe_allow_html=True)
else:
    st.caption("Guardrail config not found.")

st.subheader("It refuses what it cannot answer honestly")
st.markdown(
    "Questions outside the data's scope are declined rather than guessed: "
    "ridership, SkyTrain or other non-bus modes, on-time-vs-schedule performance, "
    "multi-day trends (the warehouse is a single service date), and routes "
    "outside the Top-20 monitored set. An honest refusal beats a confidently "
    "wrong number.")

st.subheader("Benchmarked, not asserted")
st.markdown(
    "The copilot is graded against a fixed golden set on every run, with "
    "deterministic pass/fail criteria. See **Copilot Evaluation** for the current "
    "accuracy figure and category breakdown. The same warehouse powers the "
    "dashboard's analytical pages — the copilot is simply another, conversational "
    "way to interact with it.")

st.markdown('<span class="honesty-tag">One warehouse · two interfaces · '
            'fully governed.</span>', unsafe_allow_html=True)
