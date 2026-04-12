"""
ui/charts.py — Plotly interactive charts + matplotlib fallback.

Every public function returns either a Plotly ``Figure`` or matplotlib
``Figure`` so the caller can display via ``st.plotly_chart`` or
``st.pyplot`` respectively.
"""

from __future__ import annotations

import math
from typing import Any

# Matplotlib (always available)
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.ticker as mticker

# Plotly (optional — graceful degradation)
try:
    import plotly.graph_objects as go  # type: ignore
    import plotly.express as px  # type: ignore
    HAS_PLOTLY = True
except ImportError:
    HAS_PLOTLY = False


# ═══════════════════════════════════════════════════════════════════════════════
# THEME-AWARE COLOURS
# ═══════════════════════════════════════════════════════════════════════════════

PALETTES = {
    "Light": ["#3b82f6", "#10b981", "#f59e0b", "#ef4444", "#8b5cf6", "#ec4899", "#06b6d4"],
    "Dark":  ["#60a5fa", "#4ade80", "#fbbf24", "#f87171", "#a78bfa", "#f472b6", "#22d3ee"],
}
BG       = {"Light": "#ffffff", "Dark": "#1e293b"}
FG       = {"Light": "#1e293b", "Dark": "#e2e8f0"}
GRID     = {"Light": "#e2e8f0", "Dark": "#334155"}
INC_CLR  = {"Light": "#059669", "Dark": "#4ade80"}
EXP_CLR  = {"Light": "#dc2626", "Dark": "#f87171"}
NET_CLR  = {"Light": "#6366f1", "Dark": "#818cf8"}

RISK_COLORS = {
    "Very High Risk": {"Light": "#dc2626", "Dark": "#f87171"},
    "High Risk":      {"Light": "#ea580c", "Dark": "#fb923c"},
    "Medium Risk":    {"Light": "#d97706", "Dark": "#fbbf24"},
    "Low Risk":       {"Light": "#16a34a", "Dark": "#4ade80"},
    "Unknown":        {"Light": "#94a3b8", "Dark": "#64748b"},
}

RISK_SCORE_COLORS = {
    "Critical": "#dc2626",
    "High":     "#ea580c",
    "Medium":   "#d97706",
    "Low":      "#16a34a",
}


def _t(theme: str) -> str:
    return theme if theme in ("Light", "Dark") else "Light"


def _plotly_layout(theme: str, title: str = "", height: int = 350) -> dict:
    """Base Plotly layout dict for consistent theme styling."""
    th = _t(theme)
    return dict(
        title=dict(text=title, font=dict(size=14, color=FG[th])),
        paper_bgcolor=BG[th],
        plot_bgcolor=BG[th],
        font=dict(color=FG[th], size=11),
        height=height,
        margin=dict(l=50, r=30, t=50, b=40),
        xaxis=dict(gridcolor=GRID[th], zerolinecolor=GRID[th]),
        yaxis=dict(gridcolor=GRID[th], zerolinecolor=GRID[th]),
        legend=dict(bgcolor="rgba(0,0,0,0)"),
    )


# ═══════════════════════════════════════════════════════════════════════════════
# MATPLOTLIB HELPERS (kept for compatibility)
# ═══════════════════════════════════════════════════════════════════════════════

def _style_ax(ax, fig, theme: str = "Light"):
    th = _t(theme)
    fig.patch.set_facecolor(BG[th])
    ax.set_facecolor(BG[th])
    ax.tick_params(colors=FG[th], labelsize=8)
    ax.xaxis.label.set_color(FG[th])
    ax.yaxis.label.set_color(FG[th])
    ax.title.set_color(FG[th])
    for spine in ax.spines.values():
        spine.set_color(GRID[th])
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.grid(axis="y", color=GRID[th], linewidth=0.5, alpha=0.5)


# ═══════════════════════════════════════════════════════════════════════════════
# RISK SCORE GAUGE
# ═══════════════════════════════════════════════════════════════════════════════

def risk_score_gauge(score: int, level: str, theme: str = "Light"):
    """
    Semi-circular gauge for the 0-100 risk score.
    Returns a Plotly figure, or matplotlib figure as fallback.
    """
    color = RISK_SCORE_COLORS.get(level, "#94a3b8")

    if HAS_PLOTLY:
        th = _t(theme)
        fig = go.Figure(go.Indicator(
            mode="gauge+number",
            value=score,
            number=dict(suffix="/100", font=dict(size=28, color=FG[th])),
            gauge=dict(
                axis=dict(range=[0, 100], tickwidth=1, tickcolor=FG[th]),
                bar=dict(color=color, thickness=0.6),
                bgcolor=GRID[th],
                borderwidth=0,
                steps=[
                    dict(range=[0, 19], color="#dcfce7" if th == "Light" else "#14532d"),
                    dict(range=[20, 39], color="#fef9c3" if th == "Light" else "#422006"),
                    dict(range=[40, 64], color="#fed7aa" if th == "Light" else "#431407"),
                    dict(range=[65, 100], color="#fecaca" if th == "Light" else "#450a0a"),
                ],
                threshold=dict(line=dict(color=color, width=4), thickness=0.75, value=score),
            ),
            title=dict(text=f"Risk Level: {level}", font=dict(size=14, color=color)),
        ))
        fig.update_layout(
            paper_bgcolor=BG[th],
            plot_bgcolor=BG[th],
            font=dict(color=FG[th]),
            height=250,
            margin=dict(l=30, r=30, t=50, b=10),
        )
        return fig

    # Matplotlib fallback — simple bar
    fig, ax = plt.subplots(figsize=(5, 1.5))
    _style_ax(ax, fig, theme)
    ax.barh([0], [score], color=color, height=0.5, zorder=3)
    ax.set_xlim(0, 100)
    ax.set_yticks([])
    ax.set_title(f"Risk Score: {score}/100 — {level}", fontsize=10, fontweight="600")
    plt.tight_layout()
    return fig


# ═══════════════════════════════════════════════════════════════════════════════
# RISK CATEGORY BREAKDOWN
# ═══════════════════════════════════════════════════════════════════════════════

def risk_category_bars(category_scores: dict, theme: str = "Light"):
    """Horizontal bar chart of per-category risk scores (0-100)."""
    if not category_scores:
        return None

    categories = list(category_scores.keys())
    scores = [category_scores[c] for c in categories]

    def _color(s):
        if s >= 65: return RISK_SCORE_COLORS["Critical"]
        if s >= 40: return RISK_SCORE_COLORS["High"]
        if s >= 20: return RISK_SCORE_COLORS["Medium"]
        return RISK_SCORE_COLORS["Low"]

    colors = [_color(s) for s in scores]

    if HAS_PLOTLY:
        th = _t(theme)
        fig = go.Figure(go.Bar(
            y=categories, x=scores, orientation="h",
            marker=dict(color=colors),
            text=[f"{s}" for s in scores],
            textposition="outside",
            textfont=dict(size=11, color=FG[th]),
        ))
        _lay = _plotly_layout(th, "Risk Breakdown by Category", height=max(200, len(categories) * 50))
        _lay["xaxis"] = {**_lay.get("xaxis", {}), "range": [0, 105], "gridcolor": GRID[th], "title": "Score"}
        _lay["yaxis"] = {**_lay.get("yaxis", {}), "autorange": "reversed"}
        fig.update_layout(**_lay, showlegend=False)
        return fig

    # Matplotlib fallback
    fig, ax = plt.subplots(figsize=(6, max(2, len(categories) * 0.5)))
    _style_ax(ax, fig, theme)
    ax.barh(categories, scores, color=colors, height=0.5, zorder=3)
    ax.set_xlim(0, 105)
    ax.invert_yaxis()
    for i, s in enumerate(scores):
        ax.text(s + 1, i, str(s), va="center", fontsize=8)
    ax.set_title("Risk Breakdown by Category", fontsize=10, fontweight="600")
    plt.tight_layout()
    return fig


# ═══════════════════════════════════════════════════════════════════════════════
# FINANCIAL CHARTS
# ═══════════════════════════════════════════════════════════════════════════════

def financial_trend(history: list[dict], theme: str = "Light"):
    """
    Income & expenditure trend over multiple years.
    Returns Plotly or matplotlib figure.
    """
    if not history or len(history) < 2:
        return None

    years = [h["year"] for h in history]
    incomes = [h["income"] for h in history]
    expenditures = [h["expenditure"] for h in history]
    surpluses = [i - e for i, e in zip(incomes, expenditures)]

    if HAS_PLOTLY:
        th = _t(theme)
        fig = go.Figure()
        fig.add_trace(go.Scatter(
            x=years, y=incomes, name="Income",
            mode="lines+markers", line=dict(color=INC_CLR[th], width=2.5),
            marker=dict(size=6),
        ))
        fig.add_trace(go.Scatter(
            x=years, y=expenditures, name="Expenditure",
            mode="lines+markers", line=dict(color=EXP_CLR[th], width=2.5),
            marker=dict(size=6, symbol="square"),
        ))
        fig.add_trace(go.Scatter(
            x=years, y=surpluses, name="Net Surplus/Deficit",
            mode="lines+markers",
            line=dict(color=NET_CLR[th], width=1.5, dash="dash"),
            marker=dict(size=4, symbol="triangle-up"),
        ))
        _lay = _plotly_layout(th, "Income & Expenditure Trend", height=380)
        _lay["yaxis"] = {**_lay.get("yaxis", {}), "title": "£", "gridcolor": GRID[th], "tickformat": ",.0f"}
        _lay["xaxis"] = {**_lay.get("xaxis", {}), "title": "Financial Year", "gridcolor": GRID[th]}
        fig.update_layout(**_lay, hovermode="x unified")
        return fig

    # Matplotlib fallback
    th = _t(theme)
    fig, ax = plt.subplots(figsize=(7, 3.5))
    _style_ax(ax, fig, theme)
    ax.plot(years, incomes, marker="o", markersize=5, linewidth=2,
            color=INC_CLR[th], label="Income", zorder=3)
    ax.plot(years, expenditures, marker="s", markersize=5, linewidth=2,
            color=EXP_CLR[th], label="Expenditure", zorder=3)
    ax.fill_between(years, incomes, expenditures, alpha=0.06, color=NET_CLR[th], zorder=1)
    ax.plot(years, surpluses, marker="^", markersize=3, linewidth=1,
            color=NET_CLR[th], linestyle="--", alpha=0.6, label="Net Surplus / Deficit", zorder=2)
    ax.axhline(y=0, color=GRID[th], linewidth=0.5, linestyle=":")
    ax.yaxis.set_major_formatter(mticker.FuncFormatter(lambda x, _: f"£{x:,.0f}"))
    ax.set_xlabel("Financial Year", fontsize=8.5)
    ax.set_ylabel("£", fontsize=8.5)
    ax.set_title("Income & Expenditure Trend", fontweight="600", fontsize=10)
    ax.legend(fontsize=7.5, loc="best", frameon=False, labelcolor=FG[th])
    plt.tight_layout()
    return fig


def income_vs_expense_bar(income: float, expenditure: float, theme: str = "Light"):
    """Bar chart comparing income vs expenditure for a single year."""
    inc = income or 0
    exp = expenditure or 0
    surplus = inc - exp

    if HAS_PLOTLY:
        th = _t(theme)
        colors = [INC_CLR[th], EXP_CLR[th]]
        s_color = INC_CLR[th] if surplus >= 0 else EXP_CLR[th]
        fig = go.Figure(go.Bar(
            x=["Income", "Expenditure"],
            y=[inc, exp],
            marker_color=colors,
            text=[f"£{inc:,.0f}", f"£{exp:,.0f}"],
            textposition="outside",
            textfont=dict(size=12, color=FG[th]),
        ))
        _lay = _plotly_layout(th, f"Surplus: £{surplus:,.0f}", height=300)
        _lay["yaxis"] = {**_lay.get("yaxis", {}), "tickformat": ",.0f", "gridcolor": GRID[th]}
        fig.update_layout(**_lay, showlegend=False)
        fig.update_layout(title=dict(font=dict(color=s_color)))
        return fig

    # Matplotlib fallback
    th = _t(theme)
    fig, ax = plt.subplots(figsize=(5, 3))
    _style_ax(ax, fig, theme)
    bars = ax.bar(["Income", "Expenditure"], [inc, exp],
                  color=[INC_CLR[th], EXP_CLR[th]], width=0.45, zorder=3)
    ax.yaxis.set_major_formatter(mticker.FuncFormatter(lambda x, _: f"£{x:,.0f}"))
    for bar, val in zip(bars, [inc, exp]):
        ax.text(bar.get_x() + bar.get_width() / 2,
                bar.get_height() + max(inc, exp) * 0.02,
                f"£{val:,.0f}", ha="center", va="bottom", fontsize=8,
                fontweight="600", color=FG[th])
    s_color = INC_CLR[th] if surplus >= 0 else EXP_CLR[th]
    ax.set_title(f"Surplus: £{surplus:,.0f}", fontweight="600", fontsize=9.5, color=s_color)
    plt.tight_layout()
    return fig


def pie_chart(data_dict: dict, title: str, theme: str = "Light"):
    """Donut chart for categorical financial data (e.g., income sources)."""
    if not data_dict:
        return None

    labels = list(data_dict.keys())
    values = list(data_dict.values())

    if HAS_PLOTLY:
        th = _t(theme)
        palette = PALETTES[th]
        colors = [palette[i % len(palette)] for i in range(len(labels))]
        fig = go.Figure(go.Pie(
            labels=labels, values=values, hole=0.45,
            marker=dict(colors=colors, line=dict(color=BG[th], width=2)),
            textinfo="percent",
            textfont=dict(size=10),
            hovertemplate="%{label}<br>£%{value:,.0f}<br>%{percent}<extra></extra>",
        ))
        _lay = _plotly_layout(th, title, height=350)
        _lay["legend"] = {**_lay.get("legend", {}), "font": dict(size=10)}
        fig.update_layout(**_lay, showlegend=True)
        return fig

    # Matplotlib fallback
    th = _t(theme)
    palette = PALETTES[th]
    fig, ax = plt.subplots(figsize=(5, 3.5))
    fig.patch.set_facecolor(BG[th])
    ax.set_facecolor(BG[th])
    colors = [palette[i % len(palette)] for i in range(len(labels))]
    wedges, texts, autotexts = ax.pie(
        values, labels=None, autopct="%1.1f%%", colors=colors,
        pctdistance=0.78, startangle=90,
        wedgeprops=dict(width=0.45, edgecolor=BG[th], linewidth=1.5),
    )
    for at in autotexts:
        at.set_fontsize(7.5)
        at.set_color(FG[th])
    ax.legend(wedges, [f"{l}: £{v:,.0f}" for l, v in zip(labels, values)],
              loc="center left", bbox_to_anchor=(1, 0.5), fontsize=7.5,
              frameon=False, labelcolor=FG[th])
    ax.set_title(title, fontweight="600", fontsize=10, color=FG[th])
    plt.tight_layout()
    return fig


def geographic_risk_pie(risk_counts: dict, theme: str = "Light"):
    """
    Donut chart showing the distribution of countries by risk level.
    """
    if not risk_counts:
        return None

    labels = list(risk_counts.keys())
    values = list(risk_counts.values())

    if HAS_PLOTLY:
        th = _t(theme)
        colors = [RISK_COLORS.get(l, {}).get(th, "#94a3b8") for l in labels]
        fig = go.Figure(go.Pie(
            labels=labels, values=values, hole=0.45,
            marker=dict(colors=colors, line=dict(color=BG[th], width=2)),
            textinfo="percent+label",
            textfont=dict(size=10),
            hovertemplate="%{label}: %{value} countries (%{percent})<extra></extra>",
        ))
        fig.update_layout(
            **_plotly_layout(th, "Geographic Risk Distribution", height=350),
            showlegend=True,
        )
        return fig

    # Matplotlib fallback
    th = _t(theme)
    colors = [RISK_COLORS.get(l, {}).get(th, "#94a3b8") for l in labels]
    fig, ax = plt.subplots(figsize=(5, 3.5))
    fig.patch.set_facecolor(BG[th])
    ax.set_facecolor(BG[th])
    wedges, texts, autotexts = ax.pie(
        values, labels=None, colors=colors, autopct="%1.0f%%",
        startangle=90, pctdistance=0.78,
        wedgeprops=dict(width=0.45, edgecolor=BG[th], linewidth=1.5),
    )
    for at in autotexts:
        at.set_fontsize(7.5)
        at.set_color(FG[th])
    ax.legend(wedges, [f"{l} ({v})" for l, v in zip(labels, values)],
              loc="center left", bbox_to_anchor=(1, 0.5), fontsize=7.5,
              frameon=False, labelcolor=FG[th])
    ax.set_title("Geographic Risk Distribution", fontweight="600", fontsize=10, color=FG[th])
    plt.tight_layout()
    return fig


# ═══════════════════════════════════════════════════════════════════════════════
# COMPANY-SPECIFIC CHARTS
# ═══════════════════════════════════════════════════════════════════════════════

def ownership_bar(psc_details: list[dict], theme: str = "Light"):
    """
    Horizontal bar chart of PSC ownership bands.
    ``psc_details``: list of dicts with keys: name, ownership_band, kind.
    """
    if not psc_details:
        return None

    BAND_MID = {
        "75%-to-100%": 87.5,
        "50%-to-75%": 62.5,
        "25%-to-50%": 37.5,
        "25%-to-50%-and-voting-rights": 37.5,
        "significant-influence-or-control": 25,
    }
    COLOR_PERSON = {"Light": "#16a34a", "Dark": "#4ade80"}
    COLOR_CORP   = {"Light": "#7c3aed", "Dark": "#a78bfa"}
    COLOR_OTHER  = {"Light": "#3b82f6", "Dark": "#60a5fa"}

    th = _t(theme)
    labels, vals, colors = [], [], []

    for p in psc_details:
        band = p.get("ownership_band") or ""
        mid = BAND_MID.get(band, 0)
        nm = p.get("name", "?")
        kind = p.get("kind", "")
        label = f"{nm}" + (f" ({band})" if band else "")
        labels.append(label)
        vals.append(mid if mid > 0 else 25)
        if "corporate" in kind.lower():
            colors.append(COLOR_CORP[th])
        elif "individual" in kind.lower():
            colors.append(COLOR_PERSON[th])
        else:
            colors.append(COLOR_OTHER[th])

    if HAS_PLOTLY:
        fig = go.Figure(go.Bar(
            y=labels, x=vals, orientation="h",
            marker=dict(color=colors),
            text=[f"{v:.0f}%" for v in vals],
            textposition="outside",
            textfont=dict(size=10, color=FG[th]),
        ))
        _lay = _plotly_layout(th, "PSC Ownership Structure", height=max(200, len(labels) * 50))
        _lay["xaxis"] = {**_lay.get("xaxis", {}), "range": [0, 105], "title": "Ownership %", "gridcolor": GRID[th]}
        _lay["yaxis"] = {**_lay.get("yaxis", {}), "autorange": "reversed"}
        fig.update_layout(**_lay, showlegend=False)
        return fig

    # Matplotlib fallback
    fig, ax = plt.subplots(figsize=(8, max(2, len(labels) * 0.6)))
    fig.patch.set_facecolor(BG[th])
    ax.set_facecolor(BG[th])
    bars = ax.barh(range(len(labels)), vals, color=colors, edgecolor="none", height=0.6)
    ax.set_yticks(range(len(labels)))
    ax.set_yticklabels(labels, fontsize=9, color=FG[th])
    ax.set_xlabel("Ownership %", fontsize=9, color=FG[th])
    ax.set_xlim(0, 105)
    ax.tick_params(axis="x", colors=FG[th], labelsize=8)
    ax.invert_yaxis()
    for i, (v, bar) in enumerate(zip(vals, bars)):
        band = psc_details[i].get("ownership_band", "")
        ax.text(v + 1, i, band if band else "PSC", va="center", fontsize=8, color=FG[th])
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.spines["bottom"].set_color(FG[th])
    ax.spines["left"].set_color(FG[th])
    plt.tight_layout()
    return fig


# ═══════════════════════════════════════════════════════════════════════════════
# PIPELINE METRICS CHART
# ═══════════════════════════════════════════════════════════════════════════════

def pipeline_timing_bar(stage_timings: dict, theme: str = "Light"):
    """Horizontal bar chart of pipeline stage execution times."""
    if not stage_timings:
        return None

    stages = list(stage_timings.keys())
    times = [stage_timings[s] for s in stages]

    if HAS_PLOTLY:
        th = _t(theme)
        palette = PALETTES[th]
        colors = [palette[i % len(palette)] for i in range(len(stages))]
        fig = go.Figure(go.Bar(
            y=stages, x=times, orientation="h",
            marker=dict(color=colors),
            text=[f"{t:.1f}s" for t in times],
            textposition="outside",
            textfont=dict(size=10, color=FG[th]),
        ))
        _lay = _plotly_layout(th, "Pipeline Stage Timings", height=max(180, len(stages) * 40))
        _lay["xaxis"] = {**_lay.get("xaxis", {}), "title": "Seconds", "gridcolor": GRID[th]}
        _lay["yaxis"] = {**_lay.get("yaxis", {}), "autorange": "reversed"}
        fig.update_layout(**_lay, showlegend=False)
        return fig

    # Matplotlib fallback
    th = _t(theme)
    palette = PALETTES[th]
    colors = [palette[i % len(palette)] for i in range(len(stages))]
    fig, ax = plt.subplots(figsize=(6, max(2, len(stages) * 0.5)))
    _style_ax(ax, fig, theme)
    ax.barh(stages, times, color=colors, height=0.5, zorder=3)
    ax.invert_yaxis()
    for i, t in enumerate(times):
        ax.text(t + 0.2, i, f"{t:.1f}s", va="center", fontsize=8)
    ax.set_xlabel("Seconds")
    ax.set_title("Pipeline Stage Timings", fontsize=10, fontweight="600")
    plt.tight_layout()
    return fig


# ═══════════════════════════════════════════════════════════════════════════════
# HELPER: display chart in streamlit (plotly or matplotlib)
# ═══════════════════════════════════════════════════════════════════════════════

def show_chart(st_module, fig, use_container_width: bool = True):
    """
    Display a chart figure in Streamlit.
    Automatically detects Plotly vs matplotlib.
    """
    if fig is None:
        return
    if HAS_PLOTLY and isinstance(fig, go.Figure):
        st_module.plotly_chart(fig, use_container_width=use_container_width)
    else:
        st_module.pyplot(fig)
        plt.close(fig)
