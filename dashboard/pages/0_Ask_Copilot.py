"""Ask the Copilot — natural-language analytics over the same governed warehouse.

This page is the AI front door to the platform. Every answer is computed from
the warehouse by validated SQL and shown with that SQL — the model narrates
results, it never invents numbers. It reuses the dashboard's APP_CSS so it reads
as a native part of the product, and calls the existing copilot backend in
pilot/ (no backend logic is duplicated here).
"""
import sys
from pathlib import Path

import streamlit as st

from core.theme import APP_CSS
st.markdown(APP_CSS, unsafe_allow_html=True)
from core import state

# --- make the tested copilot backend in pilot/ importable, without moving it ---
_ROOT = Path(__file__).resolve().parents[2]          # project root
_PILOT = _ROOT / "pilot"
sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(_PILOT))

from pilot.copilot import pipeline                              # noqa: E402
from copilot.contracts import PipelineStatus              # noqa: E402

# Warehouse path resolved from the project root (where the dashboard runs).
# Uses the small deploy DB (five allowlisted gold tables only) — the full
# warehouse is 800+ MB and is not committed.
_DB = str(_ROOT / "data" / "warehouse" / "transit_deploy.duckdb")

# Per-status presentation reusing the dashboard's band palette where it fits.
_STATUS = {
    "success":           ("Answered",             "#1D9E75"),
    "empty_result":      ("No matching data",     "#8a897f"),
    "refused":           ("Out of scope",         "#EF9F27"),
    "low_confidence":    ("Needs clarification",  "#EF9F27"),
    "ambiguous_time":    ("Ambiguous time",       "#EF9F27"),
    "validation_failed": ("Blocked by validator", "#E24B4A"),
    "generation_error":  ("Generation error",     "#E24B4A"),
    "execution_error":   ("Execution error",      "#E24B4A"),
    "narration_error":   ("Narration error",      "#E24B4A"),
}

EXAMPLES = [
    "What was the reliability score of R4?",
    "Which routes have the highest bunching rate?",
    "Which corridors should operations prioritize?",
    "Compare Route 099 and R4.",
    "Which routes perform worst during PM Peak?",
]


def _pill(status_value: str) -> str:
    label, color = _STATUS.get(status_value, (status_value, "#8a897f"))
    return (f'<span class="pill" style="background:{color}22;color:{color};'
            f'border:1px solid {color}55;">{label}</span>')


def _render(result) -> None:
    st.markdown(_pill(result.status.value), unsafe_allow_html=True)
    st.write("")

    status = result.status
    if status in (PipelineStatus.SUCCESS, PipelineStatus.EMPTY_RESULT):
        st.markdown(
            f'<div class="verdict"><div class="verdict-title">ANSWER</div>'
            f'<div class="verdict-body">{result.answer_text}</div></div>',
            unsafe_allow_html=True,
        )
    elif status == PipelineStatus.REFUSED:
        st.info(f"**Out of scope.** {result.refusal_reason}")
    elif status in (PipelineStatus.LOW_CONFIDENCE, PipelineStatus.AMBIGUOUS_TIME):
        st.warning(
            "I couldn't confidently map this to the data this copilot covers. "
            "Try naming a specific monitored route, a metric (reliability, "
            "bunching, priority), and—if relevant—a peak period."
        )
        if result.detail:
            st.caption(f"Detail: {result.detail}")
    elif status == PipelineStatus.VALIDATION_FAILED:
        st.error("The generated SQL was blocked by the safety validator before "
                 "it could touch the database.")
        st.caption(f"Reason: {result.validation_reason}")
    else:
        st.error("Something went wrong while answering. No unsafe query was run.")
        if result.detail:
            st.caption(f"Detail: {result.detail}")

    st.write("")

    # Always-visible SQL — the transparency guarantee.
    sql = result.validated_sql or result.generated_sql
    if sql:
        tag = "Validated SQL" if result.validated_sql else "Generated SQL (not executed)"
        st.markdown(f'<p class="kpi-label" style="margin-bottom:4px;">{tag}</p>',
                    unsafe_allow_html=True)
        st.code(sql, language="sql")
        if result.validated_sql:
            st.markdown(
                '<p class="small-note">Passed the validator (read-only, '
                'allowlisted gold tables, single SELECT, enforced LIMIT) before '
                'execution.</p>', unsafe_allow_html=True)
    elif status == PipelineStatus.REFUSED:
        st.markdown('<p class="small-note">No SQL was generated — the question '
                    'was declined before reaching the model.</p>',
                    unsafe_allow_html=True)

    # Execution metadata, in the dashboard's KPI-card style.
    st.write("")
    latency = f"{result.latency_ms:.0f} ms" if result.latency_ms else "—"
    rows = "—" if result.row_count is None else str(result.row_count)
    tokens = str(result.total_tokens) if result.total_tokens else "—"
    cards = st.columns(4)
    for col, label, value in (
        (cards[0], "Status", result.status.value.replace("_", " ")),
        (cards[1], "Latency", latency),
        (cards[2], "Rows", rows),
        (cards[3], "Tokens", tokens),
    ):
        col.markdown(
            f'<div class="kpi-card"><p class="kpi-label">{label}</p>'
            f'<p class="kpi-value">{value}</p></div>',
            unsafe_allow_html=True,
        )


# --- sidebar (shared global selectors keep the app feeling like one product) ---
route, _scenario = state.sidebar_selectors()

st.title("Ask Transit Intelligence")
st.caption("Ask a question in plain language. Every answer is computed from the "
           "same governed warehouse that powers the dashboard, and is shown with "
           "the exact SQL that produced it — benchmarked 20/20 on a fixed test set.")

st.markdown('<span class="honesty-tag">The model writes SQL; DuckDB computes '
            'every number. Nothing is invented.</span>', unsafe_allow_html=True)

if "copilot_q" not in st.session_state:
    st.session_state.copilot_q = ""

# Example chips, including one contextual to the globally-selected route.
st.markdown('<p class="kpi-label" style="margin-bottom:4px;">Try an example</p>',
            unsafe_allow_html=True)
examples = list(EXAMPLES)
if route:
    contextual = f"What was the reliability score of {route}?"
    if contextual not in examples:
        examples = [contextual] + examples[:-1]
chip_cols = st.columns(len(examples))
for i, ex in enumerate(examples):
    short = ex if len(ex) <= 24 else ex[:22] + "…"
    if chip_cols[i].button(short, key=f"chip_{i}", help=ex, use_container_width=True):
        st.session_state.copilot_q = ex

question = st.text_input(
    "Your question", value=st.session_state.copilot_q,
    placeholder="e.g. Which routes have the highest bunching rate?",
    label_visibility="collapsed")
ask = st.button("Ask", type="primary")

import os  # noqa: E402
if ask and question.strip():
    if not os.environ.get("OPENAI_API_KEY"):
        st.error("`OPENAI_API_KEY` is not set, so live questions can't run. "
                 "See **Copilot Evaluation** for the benchmarked results.")
    else:
        with st.spinner("Resolving → generating SQL → validating → executing…"):
            result = pipeline.run(question.strip(), db_path=_DB)
        st.write("")
        _render(result)
elif ask:
    st.info("Enter a question first.")
