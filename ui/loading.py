"""
ui/loading.py — Enhanced loading and progress UI components.

Replaces the basic pipeline-step HTML divs with a polished, animated,
informative loading experience showing:
  - Animated step cards with progress percentages
  - Real-time status updates with timing
  - Interesting contextual facts while waiting
  - Overall progress bar with ETA

Public API:
    render_progress_header(mode, entity_name)  → HTML string
    render_loading_step(step, total, title, desc, status, elapsed)  → HTML string
    render_progress_bar(current, total)   → HTML string
    render_loading_css()                  → CSS string
    render_loading_fact()                 → HTML string (contextual tip)
"""

from __future__ import annotations

import random
import time


# ═══════════════════════════════════════════════════════════════════════════════
# LOADING CSS — inject once at pipeline start
# ═══════════════════════════════════════════════════════════════════════════════

_LOADING_CSS = """
<style>
@keyframes v3-pulse {
    0%, 100% { opacity: 1; }
    50% { opacity: 0.6; }
}
@keyframes v3-progress-stripe {
    0% { background-position: 1rem 0; }
    100% { background-position: 0 0; }
}
@keyframes v3-spin {
    to { transform: rotate(360deg); }
}
@keyframes v3-slide-in {
    from { opacity: 0; transform: translateY(8px); }
    to { opacity: 1; transform: translateY(0); }
}
@keyframes v3-shimmer {
    0% { background-position: -200% 0; }
    100% { background-position: 200% 0; }
}
.v3-progress-container {
    margin: 12px 0;
    padding: 16px 20px;
    border-radius: 12px;
    background: var(--surface-alt, #f8f9fa);
    border: 1px solid var(--border, #dee2e6);
    animation: v3-slide-in 0.4s ease-out;
}
.v3-progress-header {
    display: flex;
    align-items: center;
    gap: 12px;
    margin-bottom: 12px;
    padding-bottom: 10px;
    border-bottom: 1px solid var(--border, #dee2e6);
}
.v3-progress-header .v3-entity-badge {
    font-size: 15px;
    font-weight: 700;
    color: var(--text-primary, #1a1a2e);
    display: flex;
    align-items: center;
    gap: 8px;
}
.v3-progress-header .v3-mode-label {
    font-size: 11px;
    color: var(--text-secondary, #6c757d);
    padding: 2px 8px;
    background: var(--accent-light, rgba(108,117,125,0.1));
    border-radius: 4px;
}
.v3-overall-bar {
    height: 6px;
    background: var(--border-strong, #e9ecef);
    border-radius: 3px;
    overflow: hidden;
    margin: 8px 0 4px 0;
}
.v3-overall-fill {
    height: 100%;
    border-radius: 3px;
    background: linear-gradient(90deg, var(--accent, #4361ee), var(--accent-dark, #3a0ca3));
    background-size: 1rem 1rem;
    background-image: linear-gradient(
        45deg, rgba(255,255,255,.15) 25%, transparent 25%,
        transparent 50%, rgba(255,255,255,.15) 50%,
        rgba(255,255,255,.15) 75%, transparent 75%, transparent
    );
    animation: v3-progress-stripe 1s linear infinite;
    transition: width 0.6s ease;
}
.v3-step-card {
    display: flex;
    align-items: center;
    gap: 12px;
    padding: 10px 14px;
    margin: 6px 0;
    border-radius: 10px;
    border: 1px solid var(--border, #e9ecef);
    background: var(--surface, #fff);
    animation: v3-slide-in 0.3s ease-out;
    transition: border-color 0.3s, box-shadow 0.3s;
}
.v3-step-card.active {
    border-color: var(--accent, #4361ee);
    box-shadow: 0 2px 8px rgba(67,97,238,0.15);
}
.v3-step-card.done {
    border-color: var(--success, #28a745);
    background: var(--success-bg, #f5fff7);
}
.v3-step-card.waiting {
    opacity: 0.5;
}
.v3-step-icon {
    width: 36px;
    height: 36px;
    display: flex;
    align-items: center;
    justify-content: center;
    border-radius: 50%;
    font-size: 18px;
    flex-shrink: 0;
}
.v3-step-icon.active {
    background: var(--accent-light, rgba(67,97,238,0.08));
    animation: v3-pulse 1.5s ease-in-out infinite;
}
.v3-step-icon.done {
    background: var(--success-bg, rgba(40,167,69,0.08));
}
.v3-step-icon.waiting {
    background: var(--surface-alt, #f0f0f0);
}
.v3-step-body {
    flex: 1;
    min-width: 0;
}
.v3-step-title {
    font-size: 13px;
    font-weight: 600;
    color: var(--text-primary, #1a1a2e);
    display: flex;
    align-items: center;
    gap: 6px;
}
.v3-step-desc {
    font-size: 11px;
    color: var(--text-secondary, #6c757d);
    margin-top: 2px;
    line-height: 1.4;
}
.v3-step-meta {
    font-size: 11px;
    color: var(--text-muted, #adb5bd);
    text-align: right;
    min-width: 50px;
    flex-shrink: 0;
}
.v3-spinner {
    width: 16px;
    height: 16px;
    border: 2px solid var(--border, #e9ecef);
    border-top-color: var(--accent, #4361ee);
    border-radius: 50%;
    animation: v3-spin 0.8s linear infinite;
    display: inline-block;
}
.v3-fact-bar {
    margin-top: 10px;
    padding: 8px 12px;
    border-radius: 8px;
    background: var(--accent-light, rgba(67,97,238,0.04));
    background-size: 200% 100%;
    animation: v3-shimmer 3s linear infinite;
    font-size: 11px;
    color: var(--accent, #4361ee);
    display: flex;
    align-items: center;
    gap: 8px;
}
</style>
"""


def render_loading_css() -> str:
    """Return CSS for the V3 loading system. Inject once."""
    return _LOADING_CSS


# ═══════════════════════════════════════════════════════════════════════════════
# PROGRESS HEADER & BAR
# ═══════════════════════════════════════════════════════════════════════════════

def render_progress_header(
    mode: str,
    entity_name: str = "",
    entity_id: str = "",
) -> str:
    """Render the top-level progress header with entity info."""
    mode_labels = {
        "charity": "🔍 Full Due-Diligence Report",
        "company": "🏢 Company Sense-Check",
        "donor": "💰 Donor Overview",
    }
    mode_label = mode_labels.get(mode, "Analysis")

    id_display = f' <span style="font-size:11px;color:#888;">({entity_id})</span>' if entity_id else ""

    return f"""
    <div class="v3-progress-header">
        <div class="v3-entity-badge">
            {mode_label}
            {f': {entity_name[:50]}{id_display}' if entity_name else ''}
        </div>
        <div class="v3-mode-label">Intelligence Pipeline V3</div>
    </div>
    """


def render_progress_bar(current: int, total: int) -> str:
    """Render an animated overall progress bar."""
    pct = min(100, int(current / total * 100)) if total > 0 else 0
    return f"""
    <div style="display:flex;align-items:center;gap:8px;">
        <div class="v3-overall-bar" style="flex:1;">
            <div class="v3-overall-fill" style="width:{pct}%;"></div>
        </div>
        <span style="font-size:12px;font-weight:600;color:var(--accent,#4361ee);min-width:40px;text-align:right;">{pct}%</span>
    </div>
    """


# ═══════════════════════════════════════════════════════════════════════════════
# STEP CARDS
# ═══════════════════════════════════════════════════════════════════════════════

def render_loading_step(
    step: int,
    total: int,
    title: str,
    description: str = "",
    status: str = "active",
    elapsed: float = 0.0,
    icon: str = "",
    country: str = "uk",
) -> str:
    """Render a single pipeline step card.

    Args:
        status: 'active' (running), 'done' (complete), 'waiting' (pending), 'error'
        country: 'uk' or 'france' - adapts description text accordingly
    """
    adapted_desc = description

    status_icons = {
        "active": f'<div class="v3-spinner"></div>',
        "done": "✅",
        "waiting": "⏳",
        "error": "❌",
    }
    step_icons = {
        "active": "active",
        "done": "done",
        "waiting": "waiting",
        "error": "active",
    }

    icon_content = icon or status_icons.get(status, "")
    icon_class = step_icons.get(status, "waiting")
    card_class = status

    meta = ""
    if status == "done" and elapsed > 0:
        meta = f"{elapsed:.1f}s"
    elif status == "active":
        meta = f'<span style="color:var(--accent,#4361ee);">Running...</span>'

    return f"""
    <div class="v3-step-card {card_class}">
        <div class="v3-step-icon {icon_class}">{icon_content}</div>
        <div class="v3-step-body">
            <div class="v3-step-title">
                Step {step}/{total} — {title}
            </div>
            <div class="v3-step-desc">{adapted_desc}</div>
        </div>
        <div class="v3-step-meta">{meta}</div>
    </div>
    """


# ═══════════════════════════════════════════════════════════════════════════════
# CONTEXTUAL FACTS / TIPS
# ═══════════════════════════════════════════════════════════════════════════════

_LOADING_FACTS = [
    "💡 The UK has over 170,000 registered charities regulated by the Charity Commission.",
    "💡 Companies House processes over 7 million company documents every year.",
    "💡 KYC (Know Your Customer) checks help prevent money laundering and terrorist financing.",
    "💡 FATF identifies 21 categories of predicate offences for money laundering.",
    "💡 The UK's National Risk Assessment identifies charities as vulnerable to terrorist abuse.",
    "💡 Enhanced due diligence is required for entities operating in high-risk jurisdictions.",
    "💡 Adverse media screening checks news sources for fraud, sanctions, and compliance issues.",
    "💡 PSC (Persons with Significant Control) registers were introduced in the UK in 2016.",
    "💡 Beneficial ownership transparency is a key pillar of anti-money laundering strategy.",
    "💡 The Charity Commission can issue regulatory orders including freezing assets.",
    "💡 SIC codes classify companies into industry sectors for regulatory risk assessment.",
    "💡 Gift Aid registration confirms a charity's tax status with HMRC.",
    "💡 Cross-referencing CC and CH records can reveal governance inconsistencies.",
    "💡 AI analysis adds context but should always be reviewed by a human analyst.",
    "💡 Source credibility varies — official registers are weighted higher than social media.",
    "💡 Financial anomaly detection looks for unusual growth, spending patterns, and structural changes.",
    "💡 Multiple data sources increase confidence in intelligence assessments.",
    "🔍 Scanning official registers for verified data...",
    "🔍 Cross-referencing across multiple intelligence databases...",
    "🔍 Applying AI-powered risk analysis to collected data...",
]


def render_loading_fact() -> str:
    """Return a random contextual fact/tip for display during loading."""
    fact = random.choice(_LOADING_FACTS)
    return f"""
    <div class="v3-fact-bar">
        <span>{fact}</span>
    </div>
    """


# ═══════════════════════════════════════════════════════════════════════════════
# CHARITY & COMPANY STEP DEFINITIONS
# ═══════════════════════════════════════════════════════════════════════════════

CHARITY_STEPS = [
    {
        "title": "Charity Commission Records",
        "desc": "Retrieving official registration, trustees, governance history, and financial filings from the Charity Commission API.",
        "icon": "🏛️",
    },
    {
        "title": "Companies House Cross-Reference",
        "desc": "Looking up any linked company registration, director records, and filing history for governance cross-checks.",
        "icon": "🏢",
    },
    {
        "title": "Document Intelligence",
        "desc": "Extracting and analysing annual accounts, trustee annual reports, and governance documents using text extraction and AI.",
        "icon": "📄",
    },
    {
        "title": "OSINT & Media Screening",
        "desc": "Running adverse media checks, FATF predicate offence screening, and web intelligence across multiple search engines in parallel.",
        "icon": "🔍",
    },
    {
        "title": "Geographic Risk Assessment",
        "desc": "Classifying operating countries against the HRCOB risk matrix and generating KYC country profiles for high-risk jurisdictions.",
        "icon": "🌍",
    },
    {
        "title": "Data Analysis & Pattern Detection",
        "desc": "Computing evidence confidence, financial patterns, entity overlaps, and source credibility scores.",
        "icon": "📊",
    },
    {
        "title": "AI Analyst Report",
        "desc": "Generating comprehensive due-diligence report with risk assessment, structured findings, and evidence-backed conclusions.",
        "icon": "🤖",
    },
]

COMPANY_STEPS = [
    {
        "title": "Company Intelligence Gathering",
        "desc": "🔍 Pulling company profile from Companies House (status, officers, charges). Analyzing SIC codes for industry classification. Scraping company website for credibility signals. Searching for adverse media & social media presence. Running FCA regulatory checks if applicable.",
        "icon": "🏢",
    },
    {
        "title": "Risk Analysis & Verdicts",
        "desc": "⚖️ Computing compliance verdicts (hard stops, restricted activities). Checking for FCA violations, sanctions, and fraud indicators. Analyzing director backgrounds and geopolitical risk. Classifying business model and payment patterns. Building overall risk score across 10+ categories.",
        "icon": "⚖️",
    },
    {
        "title": "Adverse Media Deep-Dive",
        "desc": "🔍 Running enhanced web intelligence searches (standard + FCA-specific queries for regulated entities). Analyzing 50+ sources for negative news, litigation, regulatory action. Cross-referencing company with directors and beneficial owners. Assessing credibility and severity of findings.",
        "icon": "🤖",
    },
    {
        "title": "AI Report Generation",
        "desc": "✍️ Feeding all structured data to AI (GPT-4.1 or Gemini). Generating executive narrative with key risks, governance concerns, and recommendations. Creating management summary with confidence scores. Building audit trail of sources and evidence.",
        "icon": "📊",
    },
    {
        "title": "Dashboard Preparation",
        "desc": "📈 Building interactive visualizations (risk matrix, governance network, director connections). Creating drill-down investigation panels. Organizing data tabs for governance, ownership, screening results. Preparing PDF export with formatted report.",
        "icon": "📋",
    },
]


def render_full_progress(
    mode: str,
    current_step: int,
    entity_name: str = "",
    entity_id: str = "",
    step_times: dict[int, float] | None = None,
) -> str:
    """Render the complete progress UI for a given state.

    Args:
        mode: 'charity' or 'company'
        current_step: 0-based index of current step
        step_times: dict of step_index → elapsed_seconds for completed steps
    """
    steps = CHARITY_STEPS if mode == "charity" else COMPANY_STEPS
    step_times = step_times or {}

    html = render_loading_css()
    html += '<div class="v3-progress-container">'
    html += render_progress_header(mode, entity_name, entity_id)
    html += render_progress_bar(current_step, len(steps))

    for i, step_def in enumerate(steps):
        if i < current_step:
            status = "done"
        elif i == current_step:
            status = "active"
        else:
            status = "waiting"

        elapsed = step_times.get(i, 0.0)
        html += render_loading_step(
            step=i + 1,
            total=len(steps),
            title=step_def["title"],
            description=step_def["desc"],
            status=status,
            elapsed=elapsed,
            icon=step_def.get("icon", "") if status != "active" else "",
        )

    html += render_loading_fact()
    html += '</div>'
    return html
