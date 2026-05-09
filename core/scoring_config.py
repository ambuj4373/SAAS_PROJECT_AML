"""
core/scoring_config.py — Centralised, documented scoring thresholds.

Single source of truth for the most material risk-scoring thresholds and
weights. Every value here drives a visible, regulator-readable claim in
the report — so each one carries a short rationale comment.

Why this module exists
----------------------
Scoring thresholds were previously scattered as magic numbers across
``core/risk_scorer.py``, ``core/risk_engine.py``, and
``core/financial_patterns.py``. That made it impossible to answer the
question "where does the 30% YoY threshold come from?" without grepping
the codebase. Centralising them here is partly a defensibility play
(an auditor can be shown one file) and partly a tunability play
(threshold A/B experiments now touch one location).

Not every magic number is in here yet — only the ones that drive
material report claims. As we run more reports and find threshold
choices that surface, migrate them here with rationale.

Conventions
-----------
- All constants are UPPER_CASE module-level names.
- Each carries a comment with: source / rationale / what changing it does.
- Float values are documented in their natural units (e.g., 0.30 for
  "30%", not 30 for "thirty percentage points").
"""

from __future__ import annotations

# ─── Risk score → level boundaries (charities + companies) ─────────────────
#
# An overall score of 0–100 maps to a four-band RiskLevel. The bands are
# the same as the ones used in app.py's UI rendering and the LLM prompt's
# "Overall Risk Rating" section, so they MUST stay consistent across
# scorer, prompt and UI.
#
# Source: original heuristic from V3 design (April 2026). The 65/40/20
# split roughly aligns with the FATF "high-risk / standard / low-risk"
# tiers when overlaid against typical UK SME charity risk distributions.
# Tightening the 65 boundary downward → more reports flagged Critical
# (more analyst escalations); loosening it → fewer escalations.

CRITICAL_SCORE_THRESHOLD = 65.0   # ≥65 → Critical
HIGH_SCORE_THRESHOLD = 40.0       # ≥40 → High
MEDIUM_SCORE_THRESHOLD = 20.0     # ≥20 → Medium
# below MEDIUM_SCORE_THRESHOLD → Low


# ─── Risk score categories ────────────────────────────────────────────────
#
# The six top-level categories that the per-signal scoring engine
# aggregates into. Used both as the risk_score.category_scores keys and
# as the top row of the "Risk Assessment & Mitigants" table in the
# generated report. Keep names in sync with prompts/charity_prompt.py
# Section 9 risk table.

RISK_CATEGORIES = (
    "Geography",          # Country-level operational footprint
    "Financial",          # Income/expenditure stability + anomalies
    "Governance",         # Board structure, registration, FCA status
    "Media & Screening",  # Adverse media + sanctions/PEP screening
    "Transparency",       # Policy availability + online presence
    "Operational",        # Charity-specific scale-vs-controls signals
)


# ─── Financial anomaly detection (used in core/risk_engine.py) ────────────
#
# Triggers when a charity's reported income or expenditure shifts beyond
# these thresholds across consecutive years.
#
# 30% YoY: This is the empirical threshold UK charity finance teams flag
# in their own MI dashboards as a "review-worthy" change. Below 30%,
# normal year-on-year fluctuation; above 30%, something specific
# happened (windfall, restructuring, lost contract).
# Lowering this → more anomaly flags, more analyst noise on
# fast-growing or contracting charities.
#
# 25% volatility (CV): Coefficient of variation across multi-year income.
# A 25% CV means the income series varies by ~25% of its mean year over
# year. Above this, the charity's revenue is structurally unstable
# rather than cyclically variable. Below this, normal grant/donation
# rhythm.
#
# 15pp ratio shift: Expenditure-to-income ratio shifting >15 percentage
# points across consecutive periods. Catches shifts like 90% → 110%
# (charity moving from break-even to deficit) without flagging stable
# operating patterns.

ANOMALY_YOY_JUMP = 0.30        # 30% year-on-year change in income or expenditure
ANOMALY_VOLATILITY_CV = 0.25   # Coefficient of variation across history
ANOMALY_RATIO_SHIFT = 0.15     # 15 percentage-point shift in expenditure/income ratio


# ─── Financial pattern severity → score points ────────────────────────────
#
# Each detected financial pattern has a severity ("critical", "high",
# etc.) and a confidence (0-1). The score points awarded to a pattern =
# SEVERITY_POINTS[severity] × confidence. So a "high" pattern with
# confidence 0.8 contributes 10 × 0.8 = 8 points to the Financial
# category score.
#
# Why these specific values: chosen so that two confirmed "high" patterns
# (≈16 points) push Financial into Medium (≥20), three high or one
# critical pushes into High (≥40). Tuned by hand against the V3 golden
# set; revisit when the golden set grows.

SEVERITY_POINTS = {
    "critical": 15,
    "high":     10,
    "medium":   5,
    "low":      2,
    "info":     0,
}


# ─── Spend-to-income flags (used in core/financial_patterns.py) ───────────
#
# These thresholds drive specific FINANCIAL patterns the engine flags:
#
# Spend-to-income > 120%: charity spending materially more than it
#   raises. Three years of >120% is structural, not cyclical.
# Surplus > 50%: unusually large surplus year. Common in fundraising
#   campaign years; flagged for analyst awareness, not necessarily a
#   risk signal on its own.
# Deficit > 30% of income: significant operating shortfall (after
#   reserves account for it).
#
# All three values come from comparison with the Charity Commission's
# own "review-worthy variance" guidance for the SOFA financial-format
# template. Adjusting these affects the financial-anomaly flag count;
# they are NOT risk score weights themselves (those are SEVERITY_POINTS).

SPEND_TO_INCOME_PCT_HIGH = 120.0   # %
SURPLUS_PCT_HIGH = 50.0            # % of income
DEFICIT_PCT_HIGH = -30.0           # % of income (negative = deficit)


# ─── Hard-stop signals — escalate to CRITICAL regardless of total ─────────
#
# Some signals override the numerical scoring engine. A confirmed
# high-confidence sanctions match, for example, makes a charity
# CRITICAL even if the total score is otherwise low. These are the
# situations where "summing signals" would be mathematically reasonable
# but operationally wrong: any high-confidence sanctions exposure is a
# regulator-level concern that must be treated as such.
#
# This list is more a contract than a tunable: removing items here
# changes the regulatory defensibility of the report.

HARD_STOPS = (
    "Confirmed sanctions match (high-confidence) on entity",
    "Confirmed sanctions match (high-confidence) on a trustee/director",
    "Charity is in administration",
    "Charity has been removed from the register",
)


# ─── Helpers exposed to scorer modules ────────────────────────────────────


def level_from_score(score: float) -> str:
    """Return one of 'Critical', 'High', 'Medium', 'Low' for a 0–100 score.

    Returns a plain string label. Callers that need the typed
    ``RiskLevel`` enum should map it themselves; this avoids a circular
    import between scoring_config and core.models.
    """
    if score >= CRITICAL_SCORE_THRESHOLD:
        return "Critical"
    if score >= HIGH_SCORE_THRESHOLD:
        return "High"
    if score >= MEDIUM_SCORE_THRESHOLD:
        return "Medium"
    return "Low"


def severity_points(severity: str, confidence: float = 1.0) -> float:
    """Return score points for a finding given its severity + confidence."""
    base = SEVERITY_POINTS.get(severity, 0)
    return round(base * max(0.0, min(1.0, confidence)), 2)
