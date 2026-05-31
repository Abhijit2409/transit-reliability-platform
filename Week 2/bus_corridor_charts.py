"""
bus_corridor_charts.py
======================
Static (PNG) chart counterparts to the bus corridor maps.

Maps are great for spatial intelligence, but they're heavy (~1.5 MB per
HTML) and don't read well in a README or a portfolio site. This module
produces small, scannable PNG companions for the same data.

What it produces (in assets/)
-----------------------------
    bus_corridor_hierarchy.png
        Bar chart of the top 20 critical bus corridors, showing both RT
        activity (records) and static stop count. The "spinal cord" view.

    bus_stop_count_distribution.png
        Histogram of stops-per-route across the bus network, with the
        critical corridors highlighted. Shows where the top corridors sit
        in the overall distribution.

    bus_subtype_route_counts.png
        Horizontal bar chart of how many corridors belong to each subtype.
        Static-side companion to the existing bus_subtype_composition.png
        (which is built from RT records). Counting routes, not records,
        emphasizes infrastructure scale instead of telemetry volume.
"""

from __future__ import annotations

import argparse
import logging
from pathlib import Path

import matplotlib.pyplot as plt
import pandas as pd
import numpy as np


SUBTYPE_COLORS = {
    "B-Line":      "#d04110",
    "RapidBus":    "#008522",
    "NightBus":    "#3b1f5e",
    "Express":     "#2a6fb0",
    "Regular Bus": "#737373",
}


logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)-7s | %(message)s")
logger = logging.getLogger("bus_corridor_charts")


def chart_corridor_hierarchy(catalog: pd.DataFrame, assets_dir: Path) -> None:
    """
    Top-20 critical corridors as a paired bar chart:
        - left bar (height = RT total_records)  → "how busy is it actually?"
        - right bar (height = static_stop_count) → "how much infrastructure?"

    Why pair these: a route can be high on one signal and low on the other.
    Express 503 has high RT (long route, many cycles) but fewer stops than
    a stop-heavy regular like the 49. Showing both side by side makes that
    asymmetry visible.
    """
    if "total_records" not in catalog.columns:
        logger.warning("no RT total_records — skipping hierarchy chart")
        return
    top = catalog.sort_values("total_records", ascending=False).head(20).copy()
    top = top.sort_values("total_records", ascending=True)  # bottom-up for horiz bars

    fig, axes = plt.subplots(1, 2, figsize=(13, 8), sharey=True)
    ylabels = [
        f"{r['route_short_name'].lstrip('0') or r['route_short_name']} — {r['route_long_name'][:28]}"
        for _, r in top.iterrows()
    ]
    colors = [SUBTYPE_COLORS.get(s, "#888") for s in top["bus_subtype"]]

    # Left: RT activity
    axes[0].barh(ylabels, top["total_records"], color=colors)
    axes[0].set_title("RT records (operational intensity)", fontsize=11, fontweight="bold")
    axes[0].set_xlabel("Total RT vehicle records")
    axes[0].invert_xaxis()
    axes[0].grid(axis="x", linestyle=":", alpha=0.5)

    # Right: static stops
    axes[1].barh(ylabels, top["static_stop_count"], color=colors)
    axes[1].set_title("Static stops (infrastructure footprint)", fontsize=11, fontweight="bold")
    axes[1].set_xlabel("Number of stops on corridor")
    axes[1].grid(axis="x", linestyle=":", alpha=0.5)

    fig.suptitle("Critical Bus Corridor Hierarchy — Top 20 Routes", fontsize=13, fontweight="bold", y=0.995)

    # Legend
    legend_handles = [plt.Rectangle((0,0),1,1, color=c) for c in SUBTYPE_COLORS.values()]
    fig.legend(legend_handles, list(SUBTYPE_COLORS.keys()),
               loc="lower center", ncol=5, frameon=False, fontsize=9, bbox_to_anchor=(0.5, -0.01))

    plt.tight_layout(rect=[0, 0.03, 1, 0.97])
    out = assets_dir / "bus_corridor_hierarchy.png"
    fig.savefig(out, dpi=140, bbox_inches="tight")
    plt.close(fig)
    logger.info(f"saved {out.name}")


def chart_stop_count_distribution(catalog: pd.DataFrame, assets_dir: Path) -> None:
    """
    Histogram of stops-per-route. Highlights where the critical corridors
    sit in the overall distribution.

    Why this matters: it shows that "critical" corridors aren't always
    high-stop corridors (RapidBus has fewer stops by design — it's a
    frequent service, not a coverage service).
    """
    counts = catalog["static_stop_count"]
    fig, ax = plt.subplots(figsize=(11, 5.5))
    ax.hist(counts, bins=30, color="#bbbbbb", edgecolor="#444", linewidth=0.5, label="All bus routes")

    if "is_critical" in catalog.columns:
        critical_counts = catalog[catalog["is_critical"]]["static_stop_count"]
        ax.hist(
            critical_counts, bins=30, color="#d04110", edgecolor="#222", linewidth=0.5,
            label="Top 20 critical corridors", alpha=0.85,
        )

    ax.set_xlabel("Number of stops on route")
    ax.set_ylabel("Count of routes")
    ax.set_title("Bus Route Stop-Count Distribution\nWhere the critical corridors sit in the network", fontsize=12, fontweight="bold")
    ax.legend(frameon=False)
    ax.grid(axis="y", linestyle=":", alpha=0.5)

    median = counts.median()
    ax.axvline(median, color="#444", linestyle="--", linewidth=1.0)
    ax.text(median, ax.get_ylim()[1]*0.95, f" median = {int(median)} stops", color="#444", fontsize=9, va="top")

    plt.tight_layout()
    out = assets_dir / "bus_stop_count_distribution.png"
    fig.savefig(out, dpi=140, bbox_inches="tight")
    plt.close(fig)
    logger.info(f"saved {out.name}")


def chart_subtype_route_counts(catalog: pd.DataFrame, assets_dir: Path) -> None:
    """
    Horizontal bar of routes-per-subtype.

    Why: the existing bus_subtype_composition.png is share of RT *records*.
    This is share of *route count* — an infrastructure-side view, not a
    telemetry-side view. They look different (10 NightBus routes carry far
    less telemetry than their count would suggest, because they only run
    a few hours per day) and that contrast is itself useful.
    """
    counts = catalog["bus_subtype"].value_counts()
    fig, ax = plt.subplots(figsize=(9, 4.5))
    colors = [SUBTYPE_COLORS.get(s, "#888") for s in counts.index]
    ax.barh(counts.index, counts.values, color=colors)
    for i, v in enumerate(counts.values):
        ax.text(v + 2, i, str(int(v)), va="center", fontsize=10)

    ax.set_xlabel("Number of bus routes (GTFS Static)")
    ax.set_title("Bus Subtype Composition — Route Count\nInfrastructure-side view (vs. RT-records view in bus_subtype_composition.png)",
                 fontsize=12, fontweight="bold")
    ax.invert_yaxis()
    ax.grid(axis="x", linestyle=":", alpha=0.5)
    plt.tight_layout()
    out = assets_dir / "bus_subtype_route_counts.png"
    fig.savefig(out, dpi=140, bbox_inches="tight")
    plt.close(fig)
    logger.info(f"saved {out.name}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, default=Path("outputs"))
    parser.add_argument("--assets-dir", type=Path, default=Path("assets"))
    args = parser.parse_args()

    catalog_path = args.output_dir / "bus_route_catalog.csv"
    if not catalog_path.exists():
        logger.error(f"missing {catalog_path} — run bus_corridor_geometry.py first")
        return
    catalog = pd.read_csv(catalog_path, dtype={"route_id": str})
    args.assets_dir.mkdir(parents=True, exist_ok=True)

    chart_corridor_hierarchy(catalog, args.assets_dir)
    chart_stop_count_distribution(catalog, args.assets_dir)
    chart_subtype_route_counts(catalog, args.assets_dir)


if __name__ == "__main__":
    main()
