"""Stress decomposition + plain-English explanation engine.

ONE place that turns a route's FIFA stress into:
  - labeled contribution shares (for waterfall / contribution charts)
  - a generated plain-English narrative
Both the Route Deep-Dive and FIFA Stress Lab consume this so the two pages
can never disagree.

Nothing is fabricated. The decomposition reuses fields already in
fifa_route_stress_scores.csv (base_fragility, the six exposure flags,
stress_multiplier) and the SAME weights the notebook used to build them.
"""
import pandas as pd
from core.fifa_scenarios import SCENARIO_PRESSURE, band_for, _raw_stress, _reference_max

# ---- the notebook's exposure weights (uplift each flag adds to multiplier) --
EXPOSURE_WEIGHTS = {
    "existing_reliability_weakness": ("Reliability weakness", 0.20),
    "pm_peak_vulnerability":         ("Peak-period exposure", 0.15),
    "bc_place_exposure":             ("Downtown / BC Place", 0.30),
    "downtown_exposure":             ("Downtown core", 0.20),
    "fan_festival_exposure":         ("Fan Festival / Hastings", 0.25),
    "skytrain_connector_exposure":   ("SkyTrain connector", 0.10),
}


def decompose(route_row: pd.Series, scenario_key: str) -> pd.DataFrame:
    """Break a route's stress into readable, normalized contribution shares.

    Returns a DataFrame: component, raw_weight, share_pct  (shares sum ~100).

    Logic: stress = base_fragility * (1 + sum exposure uplifts) * (1 + pressure).
    We attribute the score to three groups of drivers:
      - baseline fragility (always present)
      - each active exposure uplift
      - the scenario pressure multiplier
    Weights are taken in the same additive space the multiplier is built from,
    so shares reflect how much each driver pushes the score up.
    """
    pressure = SCENARIO_PRESSURE.get(scenario_key, 0.0)

    # base contribution: normalize fragility to a 0-1ish scale against the cohort
    base = float(route_row["base_fragility"])

    parts = []
    # 1) baseline fragility -- the route's pre-FIFA condition
    parts.append(("Baseline reliability weakness", base * 0.5))

    # 2) each active exposure flag contributes weight * base (uplift scales fragility)
    for col, (label, w) in EXPOSURE_WEIGHTS.items():
        if col in route_row and route_row[col]:
            parts.append((label, base * w))

    # 3) scenario pressure contributes its share of the lift
    parts.append(("FIFA scenario pressure", base * pressure))

    df = pd.DataFrame(parts, columns=["component", "raw_weight"])
    total = df["raw_weight"].sum() or 1.0
    df["share_pct"] = (df["raw_weight"] / total * 100).round(1)
    return df.sort_values("share_pct", ascending=False).reset_index(drop=True)


def stress_for_scenario(route_row: pd.Series, scenario_key: str,
                        reference_max: float) -> float:
    raw = _raw_stress(route_row, SCENARIO_PRESSURE.get(scenario_key, 0.0))
    return round(raw / (reference_max or 1.0) * 100, 1)


def route_narrative(route_row: pd.Series, scenario_key: str,
                    stress_score: float, reliability_drop: float,
                    scenario_label: str) -> str:
    """Generate a plain-English risk explanation for one route + scenario."""
    band = band_for(stress_score)
    name = route_row.get("route_long_name", "")
    short = route_row["route_short_name"]
    rel = route_row.get("reliability_score")
    bunch = route_row.get("bunching_rate_pct")

    # collect the active exposure reasons
    reasons = []
    if route_row.get("existing_reliability_weakness"):
        reasons.append("already-elevated bunching")
    if route_row.get("bc_place_exposure") or route_row.get("downtown_exposure"):
        reasons.append("a downtown / BC Place corridor")
    if route_row.get("fan_festival_exposure"):
        reasons.append("Fan Festival access to Hastings Park")
    if route_row.get("pm_peak_vulnerability"):
        reasons.append("strong peak-period concentration")
    if route_row.get("skytrain_connector_exposure"):
        reasons.append("a SkyTrain transfer point")

    relief = (" Announced extra service partly offsets this."
              if route_row.get("has_extra_service") else "")

    if band in ("Critical", "High"):
        lead = (f"Route {short} ({name}) is {band.lower()}-risk under "
                f"{scenario_label}")
        because = (" because it combines " + ", ".join(reasons[:3]) + "."
                   if reasons else ".")
        tail = (f" Projected reliability falls about {reliability_drop:.0f} "
                f"points, from {rel:.0f}.")
        return lead + because + tail + relief
    elif band == "Medium":
        return (f"Route {short} ({name}) sees moderate degradation under "
                f"{scenario_label}"
                + (" — driven by " + ", ".join(reasons[:2]) + "." if reasons
                   else ".")
                + f" Reliability eases ~{reliability_drop:.0f} points but stays "
                f"workable." + relief)
    else:
        return (f"Route {short} ({name}) stays low-risk under {scenario_label}, "
                f"benefiting from a strong baseline ({rel:.0f}) and limited "
                f"venue exposure." + relief)


def hotspot_narrative(seg: pd.Series) -> str:
    """Generate a narrative summary for a single hotspot segment."""
    gap_m = None
    if pd.notna(seg.get("median_gap_km")):
        gap_m = int(round(float(seg["median_gap_km"]) * 1000))
    events = int(seg["bunching_events"])
    severe = int(seg["severe_events"])
    sev_clause = (f" including {severe} severe events" if severe else "")
    gap_clause = (f" with a median spacing gap of about {gap_m} m" if gap_m
                  is not None else "")
    intensity = ("one of the most severe reliability issues in the monitored "
                 "network" if severe >= 5 or events >= 80
                 else "a recurring reliability pressure point"
                 if events >= 30 else "a localized issue")
    return (f"Between {seg['stop_before_name']} and {seg['stop_after_name']}, "
            f"route {seg['route_short_name']} recorded {events} bunching events"
            f"{sev_clause}{gap_clause}. This represents {intensity}.")
