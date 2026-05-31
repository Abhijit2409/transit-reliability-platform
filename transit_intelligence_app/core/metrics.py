"""Derived metrics and insight generators.

These turn raw baseline tables into the decision-oriented framings the
product is built around: the verdict sentence, the "biggest win if fixed"
route, the reliability anatomy breakdown, and the extra-service gap finding.
"""
import pandas as pd

# TransLink-announced extra-service routes (from the FIFA scenario config)
EXTRA_SERVICE_ROUTES = ["R5", "014", "028", "130", "222"]


def network_kpis(priority: pd.DataFrame) -> dict:
    """Headline numbers for the overview KPI strip."""
    worst = priority.sort_values("reliability_score").iloc[0]
    biggest_win = priority.sort_values(
        "intervention_priority_score", ascending=False).iloc[0]
    return {
        "routes_monitored": len(priority),
        "avg_reliability": round(priority["reliability_score"].mean(), 1),
        "total_bunching": int(priority["total_bunching_events"].sum()),
        "worst_corridor": worst["route_short_name"],
        "worst_corridor_score": round(worst["reliability_score"], 1),
        "biggest_win": biggest_win["route_short_name"],
        "biggest_win_score": round(biggest_win["intervention_priority_score"], 1),
    }


def fragile_routes(priority: pd.DataFrame) -> pd.DataFrame:
    """Routes below median reliability = 'already fragile before FIFA'."""
    thresh = priority["reliability_score"].median()
    return priority[priority["reliability_score"] < thresh].copy()


def verdict_sentence(priority: pd.DataFrame, fifa_rank: pd.DataFrame,
                     scenario_label: str = "a PM-peak match") -> str:
    """Auto-generated headline verdict for the landing page."""
    frag = fragile_routes(priority)
    n_frag = len(frag)
    crit = fifa_rank[fifa_rank["fifa_risk_band"].isin(["Critical", "High"])]
    n_crit = len(crit)
    top = fifa_rank.sort_values("fifa_stress_score", ascending=False).iloc[0]
    return (
        f"{n_frag} of {len(priority)} corridors are fragile "
        f"(below-median reliability). {n_crit} go FIFA-critical under "
        f"{scenario_label}. Top concern: route {top['route_short_name']} "
        f"({top['route_long_name']})."
    )


def reliability_anatomy(route_row: pd.Series) -> pd.DataFrame:
    """Break a route's fragility into readable contributions.

    Mirrors the scenario model's base_fragility construction:
        (100 - reliability) + bunching_rate + 2*severe_rate
    so the user can SEE what drags the score down.
    """
    base_gap = 100 - route_row["reliability_score"]
    bunch = route_row["bunching_rate_pct"]
    severe = 2 * route_row["severe_bunching_rate_pct"]
    return pd.DataFrame({
        "component": [
            "Reliability gap (100 - score)",
            "Bunching rate",
            "Severe bunching (weighted ×2)",
        ],
        "contribution": [round(base_gap, 2), round(bunch, 2), round(severe, 2)],
    })


def extra_service_gap(priority: pd.DataFrame) -> dict:
    """Which TransLink extra-service routes are NOT in the monitored Top 20."""
    monitored = set(priority["route_short_name"].astype(str))
    in_set = [r for r in EXTRA_SERVICE_ROUTES if r in monitored]
    not_in_set = [r for r in EXTRA_SERVICE_ROUTES if r not in monitored]
    return {"monitored": in_set, "gap": not_in_set}


def route_record(route: str, priority: pd.DataFrame,
                 reliability: pd.DataFrame, fifa: pd.DataFrame) -> dict:
    """Assemble everything known about one route from the three layers."""
    p = priority[priority["route_short_name"] == route]
    r = reliability[reliability["route_short_name"] == route]
    f = fifa[fifa["route_short_name"] == route]
    return {
        "priority": p.iloc[0] if len(p) else None,
        "reliability": r.iloc[0] if len(r) else None,
        "fifa": f.iloc[0] if len(f) else None,
    }


def executive_callouts(priority, fifa_rank, hotspots) -> list:
    """Auto-generated operational-intelligence cards for the overview.

    Returns list of {title, value, sub} dicts. Uses only existing fields.
    """
    cards = []
    # most vulnerable (highest FIFA stress)
    mv = fifa_rank.sort_values("fifa_stress_score", ascending=False).iloc[0]
    cards.append({"title": "Most vulnerable route",
                  "value": mv["route_short_name"],
                  "sub": f'{mv["route_long_name"]} · FIFA stress {mv["fifa_stress_score"]:.0f}'})
    # largest FIFA reliability drop
    ld = fifa_rank.sort_values("reliability_drop", ascending=False).iloc[0]
    cards.append({"title": "Largest FIFA reliability drop",
                  "value": ld["route_short_name"],
                  "sub": f'−{ld["reliability_drop"]:.0f} pts under PM-peak match'})
    # most severe hotspot corridor
    sev = (hotspots.groupby("route_short_name", as_index=False)["severe_events"]
           .sum().sort_values("severe_events", ascending=False).iloc[0])
    cards.append({"title": "Most severe hotspot corridor",
                  "value": sev["route_short_name"],
                  "sub": f'{int(sev["severe_events"])} severe bunching events'})
    # highest intervention priority
    ip = priority.sort_values("intervention_priority_score", ascending=False).iloc[0]
    cards.append({"title": "Highest intervention priority",
                  "value": ip["route_short_name"],
                  "sub": f'priority score {ip["intervention_priority_score"]:.0f}'})
    return cards
