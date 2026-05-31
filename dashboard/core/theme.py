"""Central theme: colors, band mappings, and shared CSS.

Color encodes meaning, never decoration:
  - reliability bands  -> teal (good) ... amber (watch) ... red (critical)
  - FIFA risk bands    -> single cool->warm ramp Low->Critical
Two ramps max per view; markers double-encode (color + size) so they
survive colorblindness and dark backgrounds.
"""

# --- reliability bands (from the SQL layer) ---
RELIABILITY_BAND_COLORS = {
    "Reliable": "#1D9E75",   # teal
    "Watch":    "#EF9F27",   # amber
    "Critical": "#E24B4A",   # red (appears only if a band ever drops this low)
}

# --- FIFA risk bands (cool -> warm) ---
FIFA_BAND_COLORS = {
    "Low":      "#378ADD",   # blue
    "Medium":   "#EF9F27",   # amber
    "High":     "#D85A30",   # coral
    "Critical": "#A32D2D",   # deep red
}
FIFA_BAND_ORDER = ["Low", "Medium", "High", "Critical"]

# route-type accent (categorical, used sparingly)
ROUTE_TYPE_COLORS = {
    "RapidBus":           "#534AB7",
    "B-Line":             "#1D9E75",
    "Regular Bus":        "#888780",
    "Express / Regional": "#185FA5",
}

# human-readable scenario labels for the FIFA lab
SCENARIO_LABELS = {
    "normal_day":          "Normal day (no event)",
    "fifa_offpeak_match":  "FIFA off-peak match",
    "fifa_pmpeak_match":   "FIFA PM-peak match",
    "fifa_knockout_match": "FIFA knockout / high-demand",
}
SCENARIO_ORDER = list(SCENARIO_LABELS.keys())

PLOTLY_TEMPLATE = "plotly_white"

# global CSS injected once per page
APP_CSS = """
<style>
    .block-container {padding-top: 2.2rem; padding-bottom: 3rem; max-width: 1300px;}
    /* KPI cards */
    .kpi-card {
        background: #ffffff; border: 1px solid #e6e4dd; border-radius: 12px;
        padding: 16px 18px; height: 100%;
    }
    .kpi-label {font-size: 0.78rem; color: #6b6a64; text-transform: none; margin: 0;}
    .kpi-value {font-size: 1.7rem; font-weight: 500; color: #26251f; margin: 2px 0 0 0;
                font-variant-numeric: tabular-nums;}
    .kpi-sub   {font-size: 0.74rem; color: #8a897f; margin: 2px 0 0 0;}
    /* verdict banner */
    .verdict {
        border-radius: 12px; padding: 18px 22px; margin-bottom: 6px;
        border: 1px solid #f0c98a; background: #fdf6e9;
    }
    .verdict-title {font-size: 0.8rem; font-weight: 500; letter-spacing: .04em;
                    color: #92590a; margin: 0 0 4px 0;}
    .verdict-body  {font-size: 1.12rem; color: #3a3a36; margin: 0; line-height: 1.5;}
    .pill {display:inline-block; padding:2px 10px; border-radius: 999px;
           font-size:0.74rem; font-weight:500;}
    .honesty-tag {
        display:inline-block; background:#eef0f4; color:#445; border:1px solid #d4d8e0;
        border-radius:6px; padding:3px 9px; font-size:0.72rem; margin-bottom:8px;
    }
    .small-note {font-size:0.78rem; color:#8a897f;}
    /* tighten dataframe */
    [data-testid="stDataFrame"] {border-radius: 10px;}
</style>
"""


def band_pill(label: str, colors: dict) -> str:
    """Return an HTML pill for a band label."""
    c = colors.get(label, "#888780")
    return (f'<span class="pill" style="background:{c}22;color:{c};'
            f'border:1px solid {c}55;">{label}</span>')
