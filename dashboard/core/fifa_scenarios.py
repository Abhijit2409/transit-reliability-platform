"""FIFA scenario recompute — reuses the EXACT config from the notebook.

WHY THIS MODULE WAS REVISED
---------------------------
The notebook's fifa_scenario_comparison.csv min-max scales each scenario
column INDEPENDENTLY. Because scaling is rank-preserving and every route's
raw stress moves by the same scenario factor, all four columns collapse to
identical numbers — the scenario effect becomes invisible (the "flat bars"
bug). That is an artifact of per-column rescaling, not a real finding.

Fix: recompute stress from the raw building blocks that already exist in
fifa_route_stress_scores.csv (base_fragility, stress_multiplier,
has_extra_service) and scale every scenario against ONE FIXED REFERENCE
(the worst raw stress under the highest-pressure scenario). Now the columns
genuinely differ, the scenario pressure (0.00 -> 0.80) is visible, and
nothing is fabricated — we only reuse fields and constants already present.
"""
import pandas as pd

# ---- mirror of the notebook's FIFA config ----
SCENARIO_PRESSURE = {
    "normal_day":          0.00,
    "fifa_offpeak_match":  0.25,
    "fifa_pmpeak_match":   0.55,
    "fifa_knockout_match": 0.80,
}
EXTRA_SERVICE_RELIEF = 0.15
RISK_BANDS = [(0, 25, "Low"), (25, 45, "Medium"),
              (45, 65, "High"), (65, 999, "Critical")]
MAX_PENALTY_POINTS = 25.0
MAX_PRESSURE = max(SCENARIO_PRESSURE.values())


def band_for(score: float) -> str:
    for lo, hi, label in RISK_BANDS:
        if lo <= score < hi:
            return label
    return "Unknown"


def _raw_stress(row, pressure: float) -> float:
    relief = (1 - EXTRA_SERVICE_RELIEF) if row.get("has_extra_service", 0) else 1.0
    return row["base_fragility"] * row["stress_multiplier"] * (1 + pressure) * relief


def _reference_max(stress_base: pd.DataFrame) -> float:
    """Single fixed denominator: worst raw stress under the worst scenario.

    Using one reference for all scenarios is what lets the columns differ.
    """
    worst = stress_base.apply(lambda r: _raw_stress(r, MAX_PRESSURE), axis=1)
    return float(worst.max())


def all_scenarios_matrix(stress_base: pd.DataFrame) -> pd.DataFrame:
    """Recompute a route x scenario matrix of stress scores (0-100),
    correctly differentiated across scenarios.

    stress_base must contain: route_short_name, route_type, base_fragility,
    stress_multiplier, has_extra_service.
    Returns columns: route_short_name, route_type, <each scenario key>.
    """
    ref = _reference_max(stress_base) or 1.0
    out = stress_base[["route_short_name", "route_type"]].copy()
    for scen, pressure in SCENARIO_PRESSURE.items():
        raw = stress_base.apply(lambda r: _raw_stress(r, pressure), axis=1)
        out[scen] = (raw / ref * 100).round(2)
    return out


def scenario_scores(stress_base: pd.DataFrame, scenario_key: str) -> pd.DataFrame:
    """Per-route stress score + band for one scenario (correctly scaled)."""
    matrix = all_scenarios_matrix(stress_base)
    out = matrix[["route_short_name", "route_type", scenario_key]].copy()
    out = out.rename(columns={scenario_key: "fifa_stress_score"})
    out["fifa_risk_band"] = out["fifa_stress_score"].apply(band_for)
    return out.sort_values("fifa_stress_score", ascending=False).reset_index(drop=True)


def adjusted_reliability(stress_df: pd.DataFrame,
                         reliability: pd.DataFrame) -> pd.DataFrame:
    """Apply the stress penalty to baseline reliability for a scenario."""
    base = reliability[["route_short_name", "reliability_score"]]
    m = stress_df.merge(base, on="route_short_name", how="left")
    m["fifa_penalty"] = (m["fifa_stress_score"] / 100) * MAX_PENALTY_POINTS
    m["fifa_adjusted_reliability"] = (
        (m["reliability_score"] - m["fifa_penalty"]).clip(0, 100).round(2))
    m["reliability_drop"] = (
        m["reliability_score"] - m["fifa_adjusted_reliability"]).round(2)
    return m


def scenario_delta(stress_base: pd.DataFrame, scenario_key: str) -> pd.DataFrame:
    """Change in stress score from Normal day to the selected scenario.

    This is the headline 'which route changes most' signal.

    Built column-by-column (not via rename) so it works even when the
    selected scenario IS normal_day — in that case stress_normal and
    stress_scenario are equal and every delta is 0, which is correct.
    """
    matrix = all_scenarios_matrix(stress_base)
    d = matrix[["route_short_name", "route_type"]].copy()
    d["stress_normal"] = matrix["normal_day"].values
    d["stress_scenario"] = matrix[scenario_key].values
    d["stress_delta"] = (d["stress_scenario"] - d["stress_normal"]).round(2)
    d["band_normal"] = d["stress_normal"].apply(band_for)
    d["band_scenario"] = d["stress_scenario"].apply(band_for)
    return d.sort_values("stress_delta", ascending=False).reset_index(drop=True)


def scenario_band_counts(stress_df: pd.DataFrame) -> dict:
    vc = stress_df["fifa_risk_band"].value_counts().to_dict()
    return {b: int(vc.get(b, 0)) for b in ["Low", "Medium", "High", "Critical"]}
