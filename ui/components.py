"""
ui/components.py — Shared Streamlit UI components.

Reusable rendering functions used by all dashboard modes.
Every function takes a ``st`` module (or container) and
renders directly — no return value unless stated otherwise.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Any


# ═══════════════════════════════════════════════════════════════════════════════
# TEXT FORMATTERS
# ═══════════════════════════════════════════════════════════════════════════════

def fmt_money(val) -> str:
    if val is None:
        return "N/A"
    return f"£{val:,.0f}"


def fmt_date(iso_str: str) -> str:
    if not iso_str:
        return "N/A"
    try:
        dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
        return dt.strftime("%d %B %Y")
    except Exception:
        return iso_str


def fmt_cost(cost_usd: float) -> str:
    return f"${cost_usd:.4f}" if cost_usd > 0 else "Free"


# ═══════════════════════════════════════════════════════════════════════════════
# REPORT BANNERS
# ═══════════════════════════════════════════════════════════════════════════════

TRAFFIC_LIGHT_HTML = {
    "High":   '<span class="tl-badge tl-high"><span class="tl-dot"></span><b>HIGH</b></span>',
    "Medium": '<span class="tl-badge tl-med"><span class="tl-dot"></span><b>MEDIUM</b></span>',
    "Low":    '<span class="tl-badge tl-low"><span class="tl-dot"></span><b>LOW</b></span>',
}


def render_report_banner(
    st_mod,
    title: str,
    entity_name: str,
    meta_items: list[tuple[str, str]],
    css_class: str = "report-banner",
    subtitle_class: str = "subtitle",
):
    """
    Render a full-width report header banner.

    Parameters
    ----------
    meta_items : list of (label, value) tuples
        E.g. [("Charity No", "123456"), ("Report Date", "01 Jan 2025")]
    css_class : str
        CSS class for the outer div (report-banner, donor-banner, etc.)
    """
    meta_html = " &nbsp;·&nbsp; ".join(
        f"{label}: <strong>{value}</strong>" for label, value in meta_items
    )
    st_mod.markdown(
        f"""<div class="{css_class}">
<h1>{title}</h1>
<div class="{subtitle_class}">{entity_name}</div>
<div class="meta">{meta_html}</div>
</div>""",
        unsafe_allow_html=True,
    )


def render_donor_banner(
    st_mod,
    entity_name: str,
    charity_num: str,
    snapshot_date: str = "",
):
    """Render the Free Overview mode banner."""
    if not snapshot_date:
        snapshot_date = datetime.now().strftime("%d %B %Y")
    render_report_banner(
        st_mod,
        title="🔍 Know Your Charity UK",
        entity_name=entity_name,
        meta_items=[
            ("Charity No", charity_num),
            ("Snapshot Date", snapshot_date),
            ("Mode", "Free Overview"),
        ],
        css_class="donor-banner",
        subtitle_class="donor-name",
    )


def render_charity_banner(
    st_mod,
    entity_name: str,
    charity_num: str,
    reg_status: str,
    cost_display: str,
    report_date: str = "",
):
    """Render the In-Depth charity report banner."""
    if not report_date:
        report_date = datetime.now().strftime("%d %B %Y")
    render_report_banner(
        st_mod,
        title="🛡️ Know Your Charity UK — In-Depth Report",
        entity_name=entity_name,
        meta_items=[
            ("Charity No", charity_num),
            ("Report Date", report_date),
            ("Status", reg_status),
            ("Cost", cost_display),
        ],
        css_class="report-banner",
    )


# ═══════════════════════════════════════════════════════════════════════════════
# RAG TILES & RISK BADGES
# ═══════════════════════════════════════════════════════════════════════════════

def render_rag_tiles(st_mod, categories: list[tuple[str, str, str]]):
    """
    Render a grid of RAG (Red-Amber-Green) tiles.

    Parameters
    ----------
    categories : list of (label, rag_color, display_value)
        rag_color: "green", "amber", or "red"
        display_value: e.g. "LOW", "MEDIUM", "HIGH"
    """
    tiles = "".join(
        f'<div class="rag-tile rag-{rag}">'
        f'<div class="rag-label">{label}</div>'
        f'<div class="rag-value">{value}</div>'
        f'</div>'
        for label, rag, value in categories
    )
    st_mod.markdown(f'<div class="rag-grid">{tiles}</div>', unsafe_allow_html=True)


def render_risk_badge(st_mod, overall_risk: str):
    """Render an overall risk badge (e.g. LOW, MEDIUM, HIGH)."""
    risk_lower = overall_risk.lower().replace(" ", "-")
    css_class = f"risk-badge-{risk_lower}"
    st_mod.markdown(
        f'<span class="risk-badge {css_class}">OVERALL RISK: {overall_risk}</span>',
        unsafe_allow_html=True,
    )


def render_risk_score_summary(st_mod, risk_score: dict):
    """
    Render the V3 numerical risk score summary block:
    gauge + category breakdown + hard stops.
    """
    if not risk_score:
        return

    from ui.charts import risk_score_gauge, risk_category_bars, show_chart, HAS_PLOTLY
    import streamlit as _st

    score = risk_score.get("overall_score", 0)
    level = risk_score.get("overall_level", "Unknown")
    cats = risk_score.get("category_scores", {})
    hard_stops = risk_score.get("hard_stops", [])

    # Gauge
    theme = _st.session_state.get("app_theme", "Light")
    gauge_fig = risk_score_gauge(score, level, theme)
    show_chart(st_mod, gauge_fig)

    # Hard stops
    if hard_stops:
        st_mod.error(
            "⛔ **Hard Stop(s) Triggered:**\n" +
            "\n".join(f"- {hs}" for hs in hard_stops)
        )

    # Category breakdown
    if cats:
        bar_fig = risk_category_bars(cats, theme)
        show_chart(st_mod, bar_fig)


# ═══════════════════════════════════════════════════════════════════════════════
# TRANSPARENCY BOX (Donor Mode)
# ═══════════════════════════════════════════════════════════════════════════════

def render_transparency_box(
    st_mod,
    level: str,
    icon: str,
    css_class: str,
    disclaimer: str = "",
):
    """
    Render a transparency assessment box (Donor / Free mode).

    Parameters
    ----------
    level : str
        e.g. "Good", "Fair", "Limited", "Very Limited"
    css_class : str
        One of: transparency-green, transparency-yellow, transparency-amber,
        transparency-red
    """
    if not disclaimer:
        disclaimer = (
            "Based on publicly available information from the Charity "
            "Commission register and web sources. For informational "
            "purposes only."
        )
    st_mod.markdown(
        f"""<div class="transparency-box {css_class}">
<div class="level">{icon} Transparency Assessment: {level}</div>
<div class="disclaimer">{disclaimer}</div>
</div>""",
        unsafe_allow_html=True,
    )


# ═══════════════════════════════════════════════════════════════════════════════
# TOKEN / COST METRICS
# ═══════════════════════════════════════════════════════════════════════════════

def render_token_cost_metrics(st_mod, cost_info: dict):
    """Render the 4-column token/cost row at the bottom of reports."""
    c1, c2, c3, c4 = st_mod.columns(4)
    c1.metric("Prompt Tokens", f"{cost_info.get('prompt_tokens', 0):,}")
    c2.metric("Completion Tokens", f"{cost_info.get('completion_tokens', 0):,}")
    c3.metric("Total Tokens", f"{cost_info.get('total_tokens', 0):,}")
    c4.metric("Cost (USD)", fmt_cost(cost_info.get("cost_usd", 0)))


# ═══════════════════════════════════════════════════════════════════════════════
# FEEDBACK WIDGET
# ═══════════════════════════════════════════════════════════════════════════════

def render_feedback_widget(
    st_mod,
    entity_id: str,
    row_id: int | None,
    prefix: str = "fb",
    update_fn=None,
):
    """
    Render Like/Dislike + comment + submit for a report.

    Parameters
    ----------
    entity_id : str
        Used in widget keys for uniqueness.
    row_id : int or None
        Database row ID for updating feedback.
    update_fn : callable or None
        ``(row_id, liked, comment) → None`` — persists feedback.
    """
    import streamlit as _st

    key_prefix = f"{prefix}_{entity_id}"
    fb = _st.radio(
        "Was this report useful?",
        ["👍 Yes", "👎 No"],
        horizontal=True,
        key=f"{key_prefix}_rating",
    )
    liked = fb == "👍 Yes"

    comment = ""
    if not liked:
        comment = _st.text_area(
            "What could be improved?",
            key=f"{key_prefix}_comment",
            max_chars=500,
        )

    if _st.button("Submit Feedback", key=f"{key_prefix}_submit"):
        if update_fn and row_id:
            try:
                update_fn(row_id, liked, comment)
                _st.success("✅ Thank you for your feedback!")
            except Exception as e:
                _st.error(f"Could not save feedback: {e}")
        else:
            _st.info("Feedback noted. Thank you!")


# ═══════════════════════════════════════════════════════════════════════════════
# PIPELINE PROGRESS INDICATOR
# ═══════════════════════════════════════════════════════════════════════════════

def render_pipeline_step(
    st_mod,
    icon: str,
    step: str,
    title: str,
    desc: str,
    est_time: str = "",
    active: bool = True,
):
    """Render a styled pipeline progress step."""
    active_cls = "active" if active else ""
    time_html = f'<span class="step-time">{est_time}</span>' if est_time else ""
    st_mod.markdown(
        f'<div class="pipeline-step {active_cls}">'
        f'<span class="step-icon">{icon}</span>'
        f'<div class="step-body">'
        f'<div class="step-title">Step {step} — {title}</div>'
        f'<div class="step-desc">{desc}</div>'
        f'</div>{time_html}</div>',
        unsafe_allow_html=True,
    )


def render_pipeline_step_from_meta(st_mod, meta: dict, active: bool = True):
    """Render a pipeline step from a stage metadata dict."""
    render_pipeline_step(
        st_mod,
        icon=meta.get("icon", "⚙️"),
        step=meta.get("step", ""),
        title=meta.get("title", "Processing"),
        desc=meta.get("desc", ""),
        est_time=meta.get("est_time", ""),
        active=active,
    )


# ═══════════════════════════════════════════════════════════════════════════════
# DATA HASH / CONSISTENCY
# ═══════════════════════════════════════════════════════════════════════════════

def compute_data_hash(
    entity_id: str,
    report_date: str,
    key_fields: dict,
) -> str:
    """
    Compute a deterministic hash from input data fields.
    Returns a formatted string like ``A1B2-CC-123456-2025-01-01``.
    """
    payload = json.dumps(
        {"entity_id": entity_id, "date": report_date, **key_fields},
        sort_keys=True,
    )
    h = hashlib.sha256(payload.encode()).hexdigest()[:8].upper()
    return f"{h[:4]}-CC-{entity_id}-{report_date}"


# ═══════════════════════════════════════════════════════════════════════════════
# FOOTER
# ═══════════════════════════════════════════════════════════════════════════════

def render_app_footer(
    st_mod,
    mode: str = "",
    cost_display: str = "",
    model: str = "",
    sources: str = "",
):
    """
    Render the standard app footer.
    Includes meta caption + built-by line.
    """
    if mode or cost_display or model or sources:
        parts = []
        if mode:
            parts.append(f"Mode: {mode}")
        if model:
            parts.append(f"Model: {model}")
        if cost_display:
            parts.append(f"Cost: {cost_display}")
        parts.append(f"Generated: {datetime.now().strftime('%d %B %Y %H:%M')}")
        if sources:
            parts.append(f"Sources: {sources}")
        st_mod.caption(" · ".join(parts))

    st_mod.markdown(
        '<div class="app-footer">'
        "Built by Ambuj Shukla with the help of Co-Pilot · "
        '<a href="mailto:knowyourcharity@ambujshukla.com">'
        "knowyourcharity@ambujshukla.com</a>"
        "</div>",
        unsafe_allow_html=True,
    )


# ═══════════════════════════════════════════════════════════════════════════════
# VALIDATION / REFERENCE LINKS
# ═══════════════════════════════════════════════════════════════════════════════

def render_validation_links(st_mod, entity_type: str, entity_id: str):
    """Render quick-access validation links for an entity."""
    links = []
    if entity_type == "charity":
        links.append(
            f"[Charity Commission Register]"
            f"(https://register-of-charities.charitycommission.gov.uk"
            f"/charity-search/-/charity-details/{entity_id}/charity-overview)"
        )
    if entity_type in ("charity", "company"):
        # For charities with linked company, entity_id might be the company number
        links.append(
            f"[Companies House Profile]"
            f"(https://find-and-update.company-information.service.gov.uk"
            f"/company/{entity_id})"
        )
    if links:
        st_mod.markdown(" · ".join(links))
