"""Cached loaders for the 12 baseline CSVs.

Every loader is wrapped in @st.cache_data so the files are read from disk
once per session, not on every widget interaction. The DATA_DIR resolves
relative to this file so the app runs from any working directory.
"""
from pathlib import Path
import pandas as pd
import streamlit as st

DATA_DIR = Path(__file__).resolve().parent.parent / "data"


def _read(name: str) -> pd.DataFrame:
    path = DATA_DIR / name
    if not path.exists():
        raise FileNotFoundError(
            f"Expected data file not found: {path}\n"
            f"Place the 12 baseline CSVs in {DATA_DIR}."
        )
    return pd.read_csv(path)


# ---------------- Reliability intelligence layer (SQL/DuckDB outputs) -------
@st.cache_data(show_spinner=False)
def load_reliability() -> pd.DataFrame:
    df = _read("top20_route_reliability_scores.csv")
    df["route_short_name"] = df["route_short_name"].astype(str)
    return df


@st.cache_data(show_spinner=False)
def load_priority() -> pd.DataFrame:
    df = _read("top20_corridor_priority_ranking.csv")
    df["route_short_name"] = df["route_short_name"].astype(str)
    return df


@st.cache_data(show_spinner=False)
def load_route_type_summary() -> pd.DataFrame:
    return _read("top20_route_type_summary.csv")


@st.cache_data(show_spinner=False)
def load_hotspots() -> pd.DataFrame:
    df = _read("top20_bunching_hotspots_with_stops.csv")
    df["route_short_name"] = df["route_short_name"].astype(str)
    return df


@st.cache_data(show_spinner=False)
def load_hourly() -> pd.DataFrame:
    df = _read("top20_hourly_bunching_pattern.csv")
    df["route_short_name"] = df["route_short_name"].astype(str)
    return df


# ---------------- FIFA 2026 stress + ML layer (Python outputs) --------------
@st.cache_data(show_spinner=False)
def load_fifa_stress() -> pd.DataFrame:
    df = _read("fifa_route_stress_scores.csv")
    df["route_short_name"] = df["route_short_name"].astype(str)
    return df


@st.cache_data(show_spinner=False)
def load_fifa_ranking() -> pd.DataFrame:
    df = _read("fifa_corridor_risk_ranking.csv")
    df["route_short_name"] = df["route_short_name"].astype(str)
    return df


@st.cache_data(show_spinner=False)
def load_fifa_scenarios() -> pd.DataFrame:
    df = _read("fifa_scenario_comparison.csv")
    df["route_short_name"] = df["route_short_name"].astype(str)
    return df


@st.cache_data(show_spinner=False)
def load_fifa_ml_predictions() -> pd.DataFrame:
    df = _read("fifa_ml_route_predictions.csv")
    df["route_short_name"] = df["route_short_name"].astype(str)
    return df


@st.cache_data(show_spinner=False)
def load_fifa_feature_importance() -> pd.DataFrame:
    return _read("fifa_feature_importance.csv")


@st.cache_data(show_spinner=False)
def load_fifa_model_summary() -> pd.DataFrame:
    return _read("fifa_model_summary.csv")


@st.cache_data(show_spinner=False)
def load_fifa_hotspots() -> pd.DataFrame:
    df = _read("fifa_hotspot_risk_summary.csv")
    df["route_short_name"] = df["route_short_name"].astype(str)
    return df


@st.cache_data(show_spinner=False)
def route_options() -> list:
    """Sorted route short names for the global selector."""
    p = load_priority()
    # order by intervention priority so the most-interesting routes are first
    return p.sort_values("intervention_priority_score", ascending=False)[
        "route_short_name"].tolist()
