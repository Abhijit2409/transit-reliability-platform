"""Reusable presentation components: KPI cards, verdict banner, callout cards,
and the chart factory.

Charts use Plotly with a light template and the meaning-encoding palette
from core.theme. No pie charts (cut by design); markers double-encode
(size + color) so they survive colorblindness and dark backgrounds.
"""
import plotly.graph_objects as go
import pandas as pd
import streamlit as st

from core.theme import (RELIABILITY_BAND_COLORS, FIFA_BAND_COLORS,
                        FIFA_BAND_ORDER, PLOTLY_TEMPLATE,
                        SCENARIO_LABELS, SCENARIO_ORDER)


# --------------------------------------------------------------------------
def kpi_strip(items: list):
    """items: list of dicts {label, value, sub(optional), accent(optional hex)}."""
    cols = st.columns(len(items))
    for col, it in zip(cols, items):
        sub = f'<p class="kpi-sub">{it.get("sub","")}</p>' if it.get("sub") else ""
        accent = it.get("accent")
        bar = (f'<div style="height:3px;background:{accent};border-radius:3px;'
               f'margin:-4px 0 8px 0;"></div>') if accent else ""
        col.markdown(
            f'<div class="kpi-card">{bar}<p class="kpi-label">{it["label"]}</p>'
            f'<p class="kpi-value">{it["value"]}</p>{sub}</div>',
            unsafe_allow_html=True)


def verdict_banner(text: str):
    st.markdown(
        f'<div class="verdict"><p class="verdict-title">VERDICT</p>'
        f'<p class="verdict-body">{text}</p></div>',
        unsafe_allow_html=True)


def callout(title: str, body: str, kind: str = "info"):
    """Colored callout card. kind: info | warning | success | danger."""
    palette = {
        "info":    ("#eef2fb", "#2b5fae", "#c9d6ef"),
        "warning": ("#fdf4e3", "#92590a", "#f0d39a"),
        "success": ("#e9f6ef", "#0f6e56", "#b9e3d2"),
        "danger":  ("#fbecec", "#a32d2d", "#eec4c4"),
    }
    bg, fg, br = palette.get(kind, palette["info"])
    st.markdown(
        f'<div style="background:{bg};border:1px solid {br};border-radius:12px;'
        f'padding:14px 18px;margin:6px 0;">'
        f'<p style="margin:0 0 4px 0;font-weight:500;color:{fg};font-size:0.92rem;">'
        f'{title}</p>'
        f'<p style="margin:0;color:#3a3a36;font-size:0.9rem;line-height:1.5;">'
        f'{body}</p></div>', unsafe_allow_html=True)


def honesty_tag(text: str):
    st.markdown(f'<span class="honesty-tag">{text}</span>',
                unsafe_allow_html=True)


def chart_note(text: str):
    """A 'what this chart shows' line placed directly under a chart."""
    st.markdown(f'<p class="small-note"><b>What this shows:</b> {text}</p>',
                unsafe_allow_html=True)


def explain(text: str):
    """Short 'what to look at' note under a chart."""
    st.markdown(f'<p class="small-note">↳ {text}</p>', unsafe_allow_html=True)


# --------------------------------------------------------------------------
def _layout(fig, height=360, title=None):
    fig.update_layout(
        template=PLOTLY_TEMPLATE, height=height,
        margin=dict(l=10, r=10, t=44 if title else 16, b=10),
        title=title, font=dict(size=13),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, x=0,
                    title_text=""))  # <- kills the 'undefined' legend-group title
    return fig


# ---- reliability / route-level ------------------------------------------
def chart_reliability_anatomy(anatomy: pd.DataFrame):
    fig = go.Figure(go.Bar(
        x=anatomy["contribution"], y=anatomy["component"], orientation="h",
        marker_color=["#888780", "#EF9F27", "#E24B4A"],
        text=anatomy["contribution"], textposition="outside"))
    fig.update_xaxes(title="Contribution to fragility")
    fig.update_yaxes(title="Fragility component")
    return _layout(fig, height=240)


def chart_hourly(hourly_route: pd.DataFrame):
    g = (hourly_route.groupby("hour_of_day", as_index=False)
         .agg(bunching_events=("bunching_events", "sum"),
              severe_events=("severe_events", "sum")))
    fig = go.Figure()
    fig.add_bar(x=g["hour_of_day"], y=g["bunching_events"],
                name="All bunching", marker_color="#85B7EB")
    fig.add_bar(x=g["hour_of_day"], y=g["severe_events"],
                name="Severe", marker_color="#A32D2D")
    fig.update_layout(barmode="overlay")
    fig.update_xaxes(title="Hour of day", dtick=2)
    fig.update_yaxes(title="Events")
    return _layout(fig, height=320)


# ---- FIFA scenario visuals ----------------------------------------------
def chart_scenario_heatmap(matrix: pd.DataFrame, value_label="FIFA stress score",
                           highlight_scenario: str = None):
    """Heatmap: rows = routes, columns = scenarios, color = stress score.

    Colorscale steps align to the risk-band cut points (25/45/65) so color
    reads directly as Low / Medium / High / Critical.
    """
    scen_cols = [c for c in SCENARIO_ORDER if c in matrix.columns]
    m = matrix.set_index("route_short_name")[scen_cols]
    # order routes by the highlighted (or worst) scenario, worst at top
    order_col = highlight_scenario if highlight_scenario in scen_cols else scen_cols[-1]
    m = m.loc[matrix.set_index("route_short_name")[order_col]
              .sort_values(ascending=True).index]

    # banded colorscale (0-100): Low<25 blue, Med<45 amber, High<65 coral, Crit red
    colorscale = [
        [0.00, "#cfe2f7"], [0.249, "#cfe2f7"],
        [0.25, "#f8d99a"], [0.449, "#f8d99a"],
        [0.45, "#e9a47e"], [0.649, "#e9a47e"],
        [0.65, "#c66"],    [1.00, "#7d1f1f"],
    ]
    fig = go.Figure(go.Heatmap(
        z=m.values, x=[SCENARIO_LABELS[c] for c in scen_cols], y=m.index,
        zmin=0, zmax=100, colorscale=colorscale,
        colorbar=dict(title=value_label, tickvals=[12, 35, 55, 82],
                      ticktext=["Low", "Med", "High", "Crit"]),
        hovertemplate="Route %{y}<br>%{x}<br>Score: %{z:.0f}<extra></extra>",
        text=m.values, texttemplate="%{text:.0f}", textfont=dict(size=10)))
    fig.update_xaxes(title="Scenario")
    fig.update_yaxes(title="Bus route")
    # highlight the selected scenario column with a border band
    if highlight_scenario in scen_cols:
        ci = scen_cols.index(highlight_scenario)
        fig.add_vline(x=ci, line_width=0)  # anchor; box drawn via shape
        fig.add_shape(type="rect", x0=ci - 0.5, x1=ci + 0.5,
                      y0=-0.5, y1=len(m) - 0.5,
                      line=dict(color="#26251f", width=2.5))
    return _layout(fig, height=max(360, 24 * len(m)),
                   title="FIFA Stress by Route and Scenario")


def chart_scenario_delta(delta: pd.DataFrame, scenario_label: str, n=12):
    """Horizontal delta bars: stress increase Normal -> selected scenario."""
    d = delta.sort_values("stress_delta", ascending=False).head(n).iloc[::-1]
    colors = [FIFA_BAND_COLORS.get(b, "#888") for b in d["band_scenario"]]
    fig = go.Figure(go.Bar(
        x=d["stress_delta"], y=d["route_short_name"], orientation="h",
        marker_color=colors,
        text=[f"+{v:.0f}  →{b}" for v, b in zip(d["stress_delta"], d["band_scenario"])],
        textposition="outside",
        hovertemplate="Route %{y}<br>+%{x:.1f} stress vs normal<extra></extra>"))
    fig.update_xaxes(title="Stress increase vs normal day")
    fig.update_yaxes(title="Bus route")
    return _layout(fig, height=max(320, 28 * len(d)),
                   title=f"Routes Most Impacted by {scenario_label}")


def chart_dumbbell(df, base_col, adj_col, label_col, n=12,
                   base_name="Baseline", adj_name="Adjusted",
                   ascending_pick="adj"):
    """Generic dumbbell. Picks the n rows with the largest gap/lowest adj."""
    pick = adj_col if ascending_pick == "adj" else base_col
    d = df.sort_values(pick).head(n).sort_values(pick, ascending=False)
    fig = go.Figure()
    for _, r in d.iterrows():
        fig.add_trace(go.Scatter(
            x=[r[adj_col], r[base_col]], y=[r[label_col], r[label_col]],
            mode="lines", line=dict(color="#cccccc", width=2),
            showlegend=False, hoverinfo="skip"))
    fig.add_trace(go.Scatter(
        x=d[base_col], y=d[label_col], mode="markers", name=base_name,
        marker=dict(color="#185FA5", size=11)))
    fig.add_trace(go.Scatter(
        x=d[adj_col], y=d[label_col], mode="markers", name=adj_name,
        marker=dict(color="#A32D2D", size=11)))
    fig.update_xaxes(title="Reliability score")
    return _layout(fig, height=max(320, 28 * len(d)))


def chart_top_risk(ranking: pd.DataFrame, n=10):
    d = ranking.sort_values("fifa_stress_score", ascending=False).head(n).iloc[::-1]
    colors = [FIFA_BAND_COLORS.get(b, "#888") for b in d["fifa_risk_band"]]
    fig = go.Figure(go.Bar(
        x=d["fifa_stress_score"], y=d["route_short_name"], orientation="h",
        marker_color=colors,
        text=[f"{s:.0f} · {b}" for s, b in
              zip(d["fifa_stress_score"], d["fifa_risk_band"])],
        textposition="outside"))
    fig.update_xaxes(title="FIFA stress score (0–100)", range=[0, 118])
    fig.update_yaxes(title="Bus route")
    return _layout(fig, height=max(320, 30 * len(d)))


def chart_feature_importance(imp: pd.DataFrame, n=10):
    d = imp.sort_values("rf_importance", ascending=False).head(n).iloc[::-1]
    fig = go.Figure(go.Bar(
        x=d["rf_importance"], y=d["feature"], orientation="h",
        marker_color="#534AB7"))
    fig.update_xaxes(title="Random Forest importance")
    fig.update_yaxes(title="Feature")
    return _layout(fig, height=max(320, 30 * len(d)))


def chart_scenario_ml_scatter(ml: pd.DataFrame, threshold: float = 45.0):
    fig = go.Figure()
    colmap = {"agree": "#185FA5", "DISAGREE": "#A32D2D"}
    for agr, sub in ml.groupby("agreement"):
        fig.add_trace(go.Scatter(
            x=sub["fifa_stress_score"], y=sub["rf_prob_high"],
            mode="markers+text", text=sub["route_short_name"],
            textposition="top center", textfont=dict(size=10),
            name=agr, marker=dict(size=12, color=colmap.get(agr, "#888"))))
    fig.add_hline(y=0.5, line_dash="dash", line_color="#bbb")
    fig.add_vline(x=threshold, line_dash="dash", line_color="#bbb")
    fig.update_xaxes(title="Scenario stress score")
    fig.update_yaxes(title="ML P(high risk)", range=[-0.05, 1.05])
    return _layout(fig, height=420)


# ---- hotspot strip (no coordinates -> distance-along-route view) --------
def chart_hotspot_strip(hs: pd.DataFrame, color_by="severe_events"):
    """Strip chart: x = segment km, y = route, bubble size = events,
    color = severity. Honest substitute for a geographic map (no lat/lon)."""
    if hs.empty:
        return _layout(go.Figure(), height=200)
    fig = go.Figure()
    sizeref = 2.0 * max(hs["bunching_events"].max(), 1) / (34 ** 2)
    fig.add_trace(go.Scatter(
        x=hs["route_segment_km"], y=hs["route_short_name"].astype(str),
        mode="markers",
        marker=dict(size=hs["bunching_events"], sizemode="area",
                    sizeref=sizeref, sizemin=5,
                    color=hs[color_by], colorscale="OrRd", showscale=True,
                    colorbar=dict(title=color_by.replace("_", " ")),
                    line=dict(width=0.5, color="#7d1f1f")),
        customdata=hs[["stop_before_name", "stop_after_name", "direction_id",
                       "severe_events", "median_gap_km", "closest_gap_km"]],
        hovertemplate=("Route %{y} · dir %{customdata[2]}<br>"
                       "%{customdata[0]} → %{customdata[1]}<br>"
                       "Events: %{marker.size} (severe %{customdata[3]})<br>"
                       "Median gap %{customdata[4]} km · "
                       "closest %{customdata[5]} km<extra></extra>")))
    fig.update_xaxes(title="Distance along route (km)")
    fig.update_yaxes(title="Bus route")
    return _layout(fig, height=max(300, 30 * hs["route_short_name"].nunique() + 120))


# ---- decomposition / explanation visuals (improvement 1) ----------------
def chart_contribution(decomp: pd.DataFrame, score: float = None):
    """Horizontal contribution bar: what makes up the stress score."""
    d = decomp.iloc[::-1]
    palette = ["#534AB7", "#185FA5", "#1D9E75", "#EF9F27", "#D85A30", "#A32D2D"]
    colors = (palette * 3)[:len(d)][::-1]
    fig = go.Figure(go.Bar(
        x=d["share_pct"], y=d["component"], orientation="h",
        marker_color=colors,
        text=[f"{p:.0f}%" for p in d["share_pct"]], textposition="outside",
        hovertemplate="%{y}<br>%{x:.1f}% of stress<extra></extra>"))
    title = f"Stress score breakdown" + (f" (score {score:.0f})" if score else "")
    fig.update_xaxes(title="Share of FIFA stress score", range=[0, max(d["share_pct"]) * 1.25])
    fig.update_yaxes(title="Stress driver")
    return _layout(fig, height=max(240, 42 * len(d)))


def chart_waterfall(decomp: pd.DataFrame):
    """Waterfall: cumulative build-up of stress drivers."""
    d = decomp.copy()
    fig = go.Figure(go.Waterfall(
        orientation="v", x=d["component"], y=d["share_pct"],
        measure=["relative"] * len(d),
        connector={"line": {"color": "#cfcdc4"}},
        increasing={"marker": {"color": "#D85A30"}},
        text=[f"{p:.0f}%" for p in d["share_pct"]], textposition="outside"))
    fig.update_yaxes(title="Cumulative share of stress (%)")
    fig.update_xaxes(title="Stress driver", tickangle=-20)
    return _layout(fig, height=360)


# ---- route comparison (improvement 3) -----------------------------------
def chart_compare_bars(comp: pd.DataFrame, metric_col, label, fmt="{:.1f}"):
    """Two-bar comparison for one metric across Route A and Route B."""
    fig = go.Figure(go.Bar(
        x=comp["route_short_name"], y=comp[metric_col],
        marker_color=["#185FA5", "#D85A30"],
        text=[fmt.format(v) for v in comp[metric_col]], textposition="outside"))
    fig.update_xaxes(title="Bus route")
    fig.update_yaxes(title=label)
    return _layout(fig, height=260)


def chart_compare_radar(comp: pd.DataFrame, metrics: dict):
    """Radar comparing two routes across normalized metrics.

    metrics: {column: (label, higher_is_worse_bool)} -- values normalized
    0-1 against the pair so the shape is readable.
    """
    cats = [lbl for (_, (lbl, _)) in metrics.items()]
    fig = go.Figure()
    colors = ["#185FA5", "#D85A30"]
    for i, (_, row) in enumerate(comp.iterrows()):
        vals = []
        for col, (lbl, worse_high) in metrics.items():
            pair = comp[col].astype(float)
            lo, hi = pair.min(), pair.max()
            norm = 0.5 if hi == lo else (row[col] - lo) / (hi - lo)
            # orient so "more risk" always points outward
            vals.append(norm if worse_high else 1 - norm)
        fig.add_trace(go.Scatterpolar(
            r=vals + [vals[0]], theta=cats + [cats[0]], fill="toself",
            name=str(row["route_short_name"]), line_color=colors[i % 2]))
    fig.update_layout(polar=dict(radialaxis=dict(visible=True, range=[0, 1],
                                                 showticklabels=False)),
                      showlegend=True)
    return _layout(fig, height=380)


# ---- project impact flow (improvement 8) --------------------------------
def project_impact_flow():
    """Recruiter-facing pipeline-to-decision flow as an SVG figure."""
    def _rgba(hexc, a):
        hexc = hexc.lstrip("#")
        r, g, b = int(hexc[0:2], 16), int(hexc[2:4], 16), int(hexc[4:6], 16)
        return f"rgba({r},{g},{b},{a})"
    stages = [
        ("Data pipeline", "GTFS-RT every 30s → parquet → DuckDB", "#534AB7"),
        ("Reliability engine", "projection · spacing · bunching · scores", "#185FA5"),
        ("FIFA simulation", "scenario stress + ML decision support", "#1D9E75"),
        ("Operational decision support", "where to monitor, on which day", "#D85A30"),
    ]
    fig = go.Figure()
    n = len(stages)
    for i, (title, sub, c) in enumerate(stages):
        y = n - i
        fig.add_shape(type="rect", x0=0.05, x1=0.95, y0=y - 0.38, y1=y + 0.38,
                      line=dict(color=c, width=2), fillcolor=_rgba(c, 0.09),
                      layer="below")
        fig.add_annotation(x=0.5, y=y + 0.12, text=f"<b>{title}</b>",
                           showarrow=False, font=dict(size=15, color=c))
        fig.add_annotation(x=0.5, y=y - 0.16, text=sub, showarrow=False,
                           font=dict(size=12, color="#5f5e5a"))
        if i < n - 1:
            fig.add_annotation(x=0.5, y=y - 0.5, text="↓", showarrow=False,
                               font=dict(size=20, color="#9c9a92"))
    fig.update_xaxes(visible=False, range=[0, 1])
    fig.update_yaxes(visible=False, range=[0.4, n + 0.6])
    fig.update_layout(template=PLOTLY_TEMPLATE, height=420,
                      margin=dict(l=10, r=10, t=10, b=10))
    return fig
