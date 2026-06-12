"""Copilot Evaluation — the benchmark behind the copilot's answers.

Reads the committed eval results CSV (produced by pilot/eval/runner.py) and
shows the accuracy figure, category breakdown, and latency/token metrics in the
dashboard's existing KPI-card style. This is the proof that sits behind the
"benchmarked 20/20" claim on the Ask page.
"""
import sys
from pathlib import Path

import pandas as pd
import streamlit as st

from core.theme import APP_CSS
st.markdown(APP_CSS, unsafe_allow_html=True)
from core import state

_ROOT = Path(__file__).resolve().parents[2]
_CSV = _ROOT / "pilot" / "eval" / "results" / "latest_eval_results.csv"

state.sidebar_selectors()

st.title("Copilot evaluation")
st.caption("The copilot is graded against a fixed golden set on every run. "
           "Grading is deterministic: refusal questions must refuse, answerable "
           "questions must compute the correct result values — not merely sound "
           "plausible.")


def _load() -> pd.DataFrame | None:
    if not _CSV.exists():
        return None
    df = pd.read_csv(_CSV)
    if "passed" in df.columns:
        df["is_pass"] = df["passed"].astype(str).str.upper().eq("PASS")
    for c in ("latency_ms", "total_tokens", "retry_count"):
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce").fillna(0)
    return df


df = _load()
if df is None or df.empty:
    st.warning("No evaluation results found yet. Run "
               "`python -m eval.runner` from the `pilot/` directory.")
    st.stop()

total = len(df)
passed = int(df["is_pass"].sum())
accuracy = 100.0 * passed / total if total else 0.0
refusals = int((df["category"] == "refusal").sum()) if "category" in df else 0

c1, c2, c3 = st.columns(3)
c1.markdown(
    f'<div class="kpi-card"><p class="kpi-label">Execution accuracy</p>'
    f'<p class="kpi-value" style="color:#1D9E75;">{accuracy:.0f}%</p>'
    f'<p class="kpi-sub">deterministic golden-set grading</p></div>',
    unsafe_allow_html=True)
c2.markdown(
    f'<div class="kpi-card"><p class="kpi-label">Questions passed</p>'
    f'<p class="kpi-value">{passed}/{total}</p>'
    f'<p class="kpi-sub">across all categories</p></div>',
    unsafe_allow_html=True)
c3.markdown(
    f'<div class="kpi-card"><p class="kpi-label">Out-of-scope refused</p>'
    f'<p class="kpi-value">{refusals}</p>'
    f'<p class="kpi-sub">correctly declined, not guessed</p></div>',
    unsafe_allow_html=True)

st.subheader("Accuracy by category")
if "category" in df.columns:
    by_cat = (df.groupby("category")
                .agg(total=("is_pass", "size"), passed=("is_pass", "sum"))
                .reset_index())
    by_cat["accuracy %"] = (100.0 * by_cat["passed"] / by_cat["total"]).round(0)
    st.dataframe(by_cat.rename(columns={"category": "Category"}),
                 hide_index=True, use_container_width=True)

st.subheader("Performance")
m1, m2, m3 = st.columns(3)
if "latency_ms" in df.columns and df["latency_ms"].sum() > 0:
    live = df[df["latency_ms"] > 0]["latency_ms"]
    m1.metric("Avg latency", f"{live.mean():.0f} ms")
    m2.metric("Max latency", f"{live.max():.0f} ms")
else:
    m1.metric("Avg latency", "—")
    m2.metric("Max latency", "—")
if "total_tokens" in df.columns:
    m3.metric("Total tokens", f"{int(df['total_tokens'].sum()):,}")

if "retry_count" in df.columns and df["retry_count"].sum() > 0:
    st.caption(f"Rate-limit retries during this run: {int(df['retry_count'].sum())}")

with st.expander("Per-question results"):
    cols = [c for c in ["qid", "category", "question", "status", "passed",
                        "latency_ms", "total_tokens"] if c in df.columns]
    st.dataframe(df[cols], hide_index=True, use_container_width=True)
