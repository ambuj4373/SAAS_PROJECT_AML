"""
core/risk_scorer.py — Structured numerical risk scoring layer for V3.

Produces a **0–100 risk score** and risk category from signals already generated
by the analysis pipeline. The scoring is deterministic and explainable: every
point added to the score can be traced to a specific signal.

Risk Categories (score → level):
    0–19   → Low
    20–39  → Medium
    40–64  → High
    65–100 → Critical

The scorer operates independently of the LLM: it consumes structured analysis
outputs and produces a ``RiskScore`` object that is injected into the LLM prompt
as a pre-computed verdict (the LLM must not override it).

Two public entry points:
    score_charity(state_dict)   → RiskScore
    score_company(check_dict)   → RiskScore
"""

from __future__ import annotations

import math
from typing import Any

from core.models import RiskLevel, RiskScore, RiskSignal
from core.scoring_config import (
    CRITICAL_SCORE_THRESHOLD,
    HIGH_SCORE_THRESHOLD,
    MEDIUM_SCORE_THRESHOLD,
    SEVERITY_POINTS,
)
from core.validators import safe_get, safe_int, safe_float, safe_list


# ═══════════════════════════════════════════════════════════════════════════════
# SCORE THRESHOLDS — sourced from core.scoring_config (documented rationale).
# ═══════════════════════════════════════════════════════════════════════════════

def _level_from_score(score: float) -> RiskLevel:
    if score >= CRITICAL_SCORE_THRESHOLD:
        return RiskLevel.CRITICAL
    if score >= HIGH_SCORE_THRESHOLD:
        return RiskLevel.HIGH
    if score >= MEDIUM_SCORE_THRESHOLD:
        return RiskLevel.MEDIUM
    return RiskLevel.LOW


def _clamp(val: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, val))


# ═══════════════════════════════════════════════════════════════════════════════
# CHARITY RISK SCORER
# ═══════════════════════════════════════════════════════════════════════════════

def score_charity(
    charity_data: dict,
    financial_history: list[dict],
    financial_anomalies: dict,
    governance_indicators: dict,
    structural_governance: dict,
    country_risk_classified: list[dict],
    adverse_org: list[dict],
    adverse_trustees: dict[str, list[dict]],
    fatf_org_screen: dict | None,
    fatf_trustee_screens: dict[str, dict],
    hrcob_core_controls: dict,
    policy_classification: list[dict],
    social_links: dict,
    online_presence: list[dict],
    cc_governance: dict,
    ch_data: dict | None = None,
    fca_details: dict | None = None,
) -> RiskScore:
    """Compute a numerical risk score for a charity based on all analysis signals.

    Returns a ``RiskScore`` with overall score, per-category scores, and detailed
    signal list.
    """
    signals: list[RiskSignal] = []
    category_scores: dict[str, float] = {
        "Geography": 0.0,
        "Financial": 0.0,
        "Governance": 0.0,
        "Media & Screening": 0.0,
        "Transparency": 0.0,
        "Operational": 0.0,
    }
    hard_stops: list[str] = []
    methodology: list[str] = []

    # ── 1. GEOGRAPHY ─────────────────────────────────────────────────
    _score_geography(country_risk_classified, signals, category_scores)

    # ── 2. FINANCIAL ─────────────────────────────────────────────────
    _score_financial_charity(
        charity_data, financial_history, financial_anomalies,
        signals, category_scores,
    )

    # ── 3. GOVERNANCE ────────────────────────────────────────────────
    _score_governance(
        charity_data, governance_indicators, structural_governance,
        cc_governance, ch_data, signals, category_scores,
    )

    # ── 4. MEDIA & SCREENING ────────────────────────────────────────
    _score_media_charity(
        adverse_org, adverse_trustees, fatf_org_screen,
        fatf_trustee_screens, signals, category_scores, hard_stops,
    )

    # ── 5. TRANSPARENCY ─────────────────────────────────────────────
    _score_transparency(
        hrcob_core_controls, policy_classification, social_links,
        online_presence, signals, category_scores,
    )

    # ── 6. OPERATIONAL ───────────────────────────────────────────────
    _score_operational_charity(
        charity_data, cc_governance, signals, category_scores,
    )

    # ── 7. FCA REGULATION ────────────────────────────────────────────
    fca = fca_details or {}
    if fca.get("found"):
        _add(signals, category_scores, "Governance",
             "✅ FCA regulated entity (25% risk reduction applied)",
             RiskLevel.LOW, "fca_details", -5)

    # ── Aggregate ────────────────────────────────────────────────────
    risk_score = _build_risk_score(
        category_scores, signals, hard_stops, methodology,
        entity_type="charity",
    )
    
    # Apply FCA multiplier to overall score if found
    if fca.get("found"):
        fca_multiplier = fca.get("risk_reduction", 0.75)
        risk_score.overall_score = risk_score.overall_score * fca_multiplier
    
    return risk_score


# ═══════════════════════════════════════════════════════════════════════════════
# COMPANY RISK SCORER
# ═══════════════════════════════════════════════════════════════════════════════

def score_company(check: dict) -> RiskScore:
    """Compute a numerical risk score for a company from its analysis bundle.

    ``check`` is the full dict returned by ``run_company_check()``.
    """
    signals: list[RiskSignal] = []
    category_scores: dict[str, float] = {
        "Geography": 0.0,
        "Financial": 0.0,
        "Governance": 0.0,
        "Media & Screening": 0.0,
        "Transparency": 0.0,
        "Operational": 0.0,
    }
    hard_stops: list[str] = []
    methodology: list[str] = []

    # ── Company Age ──────────────────────────────────────────────────
    age_risk = safe_get(check, "company_age", "risk_level") or ""
    age_months = safe_int(safe_get(check, "company_age", "age_months"))
    if age_risk == "high":
        _add(signals, category_scores, "Operational", "Company less than 6 months old",
             RiskLevel.HIGH, "company_age", 12)
    elif age_risk == "medium":
        _add(signals, category_scores, "Operational", "Company less than 12 months old",
             RiskLevel.MEDIUM, "company_age", 6)
    elif age_risk == "low-medium":
        _add(signals, category_scores, "Operational", "Company less than 24 months old",
             RiskLevel.LOW, "company_age", 3)

    # ── Company Status ───────────────────────────────────────────────
    status = safe_get(check, "status_analysis") or {}
    status_risk = (status.get("risk_level") or "").lower()
    if status_risk == "high":
        _add(signals, category_scores, "Governance", "Company status is concerning",
             RiskLevel.HIGH, "status_analysis", 15)
        for f in status.get("flags", []):
            if "dissolved" in f.lower() or "liquidation" in f.lower():
                hard_stops.append(f)
    for flag in status.get("flags", []):
        _add(signals, category_scores, "Governance", flag,
             RiskLevel.MEDIUM, "status_analysis", 3)

    # ── Virtual Office ───────────────────────────────────────────────
    vo = safe_get(check, "virtual_office") or {}
    if vo.get("is_virtual"):
        _add(signals, category_scores, "Operational",
             f"Registered at known virtual office: {vo.get('matched_marker', '')}",
             RiskLevel.MEDIUM, "virtual_office", 5)

    # ── SIC / Industry Risk ──────────────────────────────────────────
    sic = safe_get(check, "sic_risk") or {}
    sic_risk = (sic.get("risk_level") or "").lower()
    if sic_risk == "high":
        _add(signals, category_scores, "Operational",
             f"High-risk industry: {sic.get('industry_category', '')}",
             RiskLevel.HIGH, "sic_risk", 10)
    elif sic_risk == "medium":
        _add(signals, category_scores, "Operational",
             f"Medium-risk industry: {sic.get('industry_category', '')}",
             RiskLevel.MEDIUM, "sic_risk", 5)

    # ── Director Analysis ────────────────────────────────────────────
    directors = safe_get(check, "director_analysis") or {}
    for flag in directors.get("risk_flags", []):
        sev = RiskLevel.HIGH if "high" in flag.lower() else RiskLevel.MEDIUM
        pts = 8 if sev == RiskLevel.HIGH else 4
        _add(signals, category_scores, "Governance", flag, sev,
             "director_analysis", pts)

    # ── PSC Analysis ─────────────────────────────────────────────────
    pscs = safe_get(check, "psc_analysis") or {}
    for flag in pscs.get("flags", []):
        sev = RiskLevel.HIGH if "corporate" in flag.lower() else RiskLevel.MEDIUM
        pts = 6 if sev == RiskLevel.HIGH else 3
        _add(signals, category_scores, "Governance", flag, sev,
             "psc_analysis", pts)

    # ── UBO Chain ────────────────────────────────────────────────────
    ubo = safe_get(check, "ubo_chain") or {}
    if ubo.get("max_depth_reached"):
        _add(signals, category_scores, "Governance",
             "UBO chain reached max trace depth — incomplete ownership picture",
             RiskLevel.MEDIUM, "ubo_chain", 8)
    for owner in ubo.get("ultimate_owners", []):
        if owner.get("terminal_type") == "End of Trace: Foreign/Unresolvable Entity":
            _add(signals, category_scores, "Geography",
                 f"Foreign/unresolvable UBO entity: {owner.get('name', 'Unknown')}",
                 RiskLevel.MEDIUM, "ubo_chain", 5)

    # ── Dormancy ─────────────────────────────────────────────────────
    dorm = safe_get(check, "dormancy") or {}
    if dorm.get("was_dormant"):
        _add(signals, category_scores, "Operational",
             "Company shows dormant-to-active transition (potential shelf company)",
             RiskLevel.MEDIUM, "dormancy", 6)

    # ── Accounts / Filing ────────────────────────────────────────────
    accts = safe_get(check, "accounts_data") or {}
    filing_risk = (accts.get("filing_overdue_risk") or "").lower()
    if filing_risk == "high":
        _add(signals, category_scores, "Governance",
             f"Accounts significantly overdue ({accts.get('filing_gap_months', '?')} months)",
             RiskLevel.HIGH, "accounts", 10)
    elif filing_risk == "medium":
        _add(signals, category_scores, "Governance",
             "Accounts overdue",
             RiskLevel.MEDIUM, "accounts", 5)

    # ── Restricted Activities ────────────────────────────────────────
    restricted = safe_get(check, "restricted_activities") or {}
    for item in restricted.get("prohibited", []):
        hard_stops.append(f"Prohibited activity: {item.get('category', 'Unknown')}")
        _add(signals, category_scores, "Operational",
             f"Prohibited activity detected: {item.get('category', '')}",
             RiskLevel.CRITICAL, "restricted_activities", 25)
    for item in restricted.get("restricted", []):
        _add(signals, category_scores, "Operational",
             f"Restricted activity detected: {item.get('category', '')}",
             RiskLevel.HIGH, "restricted_activities", 10)

    # ── FATF Screening ───────────────────────────────────────────────
    fatf = safe_get(check, "fatf_screening") or {}
    fatf_risk = (fatf.get("risk_level") or "").lower()
    if fatf_risk in ("high", "critical"):
        hard_stops.append("FATF predicate-offence screening: High risk match")
        _add(signals, category_scores, "Media & Screening",
             "FATF screening returned high-risk match",
             RiskLevel.CRITICAL, "fatf_screening", 25)
    elif fatf_risk == "medium":
        _add(signals, category_scores, "Media & Screening",
             "FATF screening returned medium-risk match",
             RiskLevel.MEDIUM, "fatf_screening", 8)

    # ── Adverse Media ────────────────────────────────────────────────
    # adverse_media in company_check can be either a dict (summary) or a
    # list of hits (current shape). Handle both.
    adverse = safe_get(check, "adverse_media")
    if isinstance(adverse, list):
        adv_count = sum(1 for h in adverse if isinstance(h, dict)
                        and h.get("verified_adverse"))
    elif isinstance(adverse, dict):
        adv_count = safe_int(adverse.get("true_adverse_count",
                                         adverse.get("adverse_count")))
    else:
        adv_count = 0
    if adv_count >= 3:
        _add(signals, category_scores, "Media & Screening",
             f"{adv_count} adverse media hits found",
             RiskLevel.HIGH, "adverse_media", 15)
    elif adv_count >= 1:
        _add(signals, category_scores, "Media & Screening",
             f"{adv_count} adverse media hit(s) found",
             RiskLevel.MEDIUM, "adverse_media", 7)

    # ── Website Cross-Reference ──────────────────────────────────────
    xref = safe_get(check, "cross_reference") or {}
    cred = (xref.get("credibility_level") or "").lower()
    if cred == "none" or cred == "minimal":
        _add(signals, category_scores, "Transparency",
             "No credible website presence detected",
             RiskLevel.MEDIUM, "cross_reference", 8)
    for flag in xref.get("red_flags", []):
        _add(signals, category_scores, "Transparency", flag,
             RiskLevel.MEDIUM, "cross_reference", 3)

    # ── Merchant Suitability ─────────────────────────────────────────
    merchant = safe_get(check, "merchant_suitability") or {}
    dd_suit = (merchant.get("dd_suitability") or "").lower()
    if dd_suit == "not suitable":
        _add(signals, category_scores, "Operational",
             "Company assessed as not suitable for Direct Debit",
             RiskLevel.HIGH, "merchant_suitability", 10)

    # ── FCA Regulation ───────────────────────────────────────────────
    fca = safe_get(check, "fca_details") or {}
    if fca.get("found"):
        _add(signals, category_scores, "Governance",
             "✅ FCA regulated entity (25% risk reduction applied)",
             RiskLevel.LOW, "fca_details", -5)

    # ── Aggregate ────────────────────────────────────────────────────
    risk_score = _build_risk_score(
        category_scores, signals, hard_stops, methodology,
        entity_type="company",
    )
    
    # Apply FCA multiplier to overall score if found
    if fca.get("found"):
        fca_multiplier = fca.get("risk_reduction", 0.75)
        risk_score.overall_score = risk_score.overall_score * fca_multiplier
    
    return risk_score


# ═══════════════════════════════════════════════════════════════════════════════
# PRIVATE SCORING HELPERS
# ═══════════════════════════════════════════════════════════════════════════════

# ── Signal severity weight multiplier (for diminishing-return scaling) ────────
_SEVERITY_MULTIPLIER = {
    RiskLevel.CRITICAL: 1.0,
    RiskLevel.HIGH: 0.85,
    RiskLevel.MEDIUM: 0.65,
    RiskLevel.LOW: 0.45,
    RiskLevel.NONE: 0.0,
    RiskLevel.UNKNOWN: 0.5,
}


def _add(signals: list, scores: dict, category: str, description: str,
         severity: RiskLevel, source: str, points: float):
    """Add a risk signal and update category score."""
    signals.append(RiskSignal(
        category=category,
        description=description,
        severity=severity,
        source=source,
        score_impact=points,
    ))
    scores[category] = scores.get(category, 0.0) + points


# ── Diminishing returns ──────────────────────────────────────────────────────
def _apply_diminishing_returns(category_scores: dict[str, float],
                               signals: list[RiskSignal]) -> dict[str, float]:
    """Apply log-scaled diminishing returns per category.

    Prevents 10 minor flags from scoring higher than 1 genuinely critical flag.
    The first ~25 raw points map almost 1:1; beyond that the marginal value of
    each additional point decreases logarithmically.
    """
    adjusted: dict[str, float] = {}
    for cat, raw in category_scores.items():
        if raw <= 0:
            adjusted[cat] = 0.0
            continue
        # Count signal severities in this category
        cat_signals = [s for s in signals if s.category == cat]
        has_critical = any(s.severity == RiskLevel.CRITICAL for s in cat_signals)
        # If a CRITICAL signal is present, preserve more of the raw score
        if has_critical:
            adjusted[cat] = min(raw, 100.0)
        else:
            # Logarithmic compression: 25 * ln(1 + raw/10)
            # Maps:  10 raw → ~17,  25 raw → ~32,  50 raw → ~46,  100 raw → ~60
            adjusted[cat] = min(25.0 * math.log1p(raw / 10.0), 100.0)
    return adjusted


# ── Cross-category correlation ───────────────────────────────────────────────
def _correlation_bonus(signals: list[RiskSignal],
                       methodology: list[str]) -> float:
    """Award a bonus when risk signals from independent sources reinforce.

    If ≥3 different categories each have a HIGH+ signal, the compound risk is
    greater than the simple weighted sum.  Returns 0..8 bonus points.
    """
    high_cats = {s.category for s in signals
                 if s.severity in (RiskLevel.CRITICAL, RiskLevel.HIGH)}
    n = len(high_cats)
    if n >= 4:
        methodology.append(f"Cross-category correlation: {n} pillars with HIGH+ signals (+8 pts)")
        return 8.0
    if n >= 3:
        methodology.append(f"Cross-category correlation: {n} pillars with HIGH+ signals (+5 pts)")
        return 5.0
    if n >= 2:
        methodology.append(f"Cross-category correlation: {n} pillars with HIGH+ signals (+2 pts)")
        return 2.0
    return 0.0


# ── Bayesian-inspired confidence ─────────────────────────────────────────────
_ALL_DATA_SOURCES = {
    "charity": [
        "country_risk", "financial_data", "financial_ratio", "financial_anomalies",
        "financial_history", "structural_governance", "ch_consistency",
        "adverse_org", "adverse_trustees", "fatf_org", "fatf_trustee",
        "hrcob_core", "policies", "social_media", "reporting_status",
    ],
    "company": [
        "company_age", "status_analysis", "virtual_office", "sic_risk",
        "director_analysis", "psc_analysis", "ubo_chain", "dormancy",
        "accounts", "restricted_activities", "fatf_screening",
        "adverse_media", "cross_reference", "merchant_suitability",
    ],
}

_ALL_CATEGORIES = [
    "Geography", "Financial", "Governance",
    "Media & Screening", "Transparency", "Operational",
]


def _compute_confidence(
    signals: list[RiskSignal],
    hard_stops: list[str],
    entity_type: str,
    search_errors: list[str] | None = None,
) -> tuple[float, list[str]]:
    """Multi-dimensional confidence score (0–1).

    Dimensions:
      1. Source coverage  — what fraction of expected data sources produced signals
      2. Category breadth — how many of the 6 risk categories have ≥1 signal
      3. Signal agreement — do signals consistently point same direction?
      4. Search errors    — any API failures degrade confidence
      5. Signal volume    — more evidence ≥ higher confidence (with ceiling)

    Returns (confidence, methodology_notes).
    """
    notes: list[str] = []
    search_errors = search_errors or []

    # 1. Source coverage  (weight 0.25)
    expected_sources = set(_ALL_DATA_SOURCES.get(entity_type, []))
    observed_sources = {s.source for s in signals}
    if expected_sources:
        source_cov = len(observed_sources & expected_sources) / len(expected_sources)
    else:
        source_cov = 0.5
    notes.append(f"Source coverage: {source_cov:.0%} ({len(observed_sources)}/{len(expected_sources)} sources)")

    # 2. Category breadth (weight 0.20)
    active_cats = {s.category for s in signals}
    cat_breadth = len(active_cats) / max(len(_ALL_CATEGORIES), 1)
    notes.append(f"Category breadth: {cat_breadth:.0%} ({len(active_cats)}/{len(_ALL_CATEGORIES)} categories)")

    # 3. Signal agreement (weight 0.20)
    if signals:
        severity_nums = [_SEVERITY_MULTIPLIER.get(s.severity, 0.5) for s in signals]
        mean_sev = sum(severity_nums) / len(severity_nums)
        variance = sum((x - mean_sev) ** 2 for x in severity_nums) / len(severity_nums)
        # Low variance → signals agree → higher confidence
        agreement = max(0.0, 1.0 - math.sqrt(variance))
    else:
        agreement = 0.3  # no signals = low agreement info
    notes.append(f"Signal agreement: {agreement:.0%}")

    # 4. Search errors penalty (weight 0.15)
    error_penalty = min(len(search_errors) * 0.12, 0.6)  # max 60% penalty
    error_factor = 1.0 - error_penalty
    if search_errors:
        notes.append(f"Search error penalty: -{error_penalty:.0%} ({len(search_errors)} error(s))")

    # 5. Signal volume (weight 0.20)
    vol = min(len(signals) / 15.0, 1.0)  # saturates at 15 signals
    notes.append(f"Signal volume: {vol:.0%} ({len(signals)} signals)")

    # Weighted blend
    raw = (source_cov * 0.25
           + cat_breadth * 0.20
           + agreement * 0.20
           + error_factor * 0.15
           + vol * 0.20)

    # Hard stops always boost confidence (we *know* there's a problem)
    if hard_stops:
        raw = max(raw, 0.80)
        notes.append("Hard stop present — confidence floor at 80%")

    confidence = _clamp(raw, 0.15, 0.95)
    return round(confidence, 2), notes


def _build_risk_score(
    category_scores: dict[str, float],
    signals: list[RiskSignal],
    hard_stops: list[str],
    methodology: list[str],
    entity_type: str = "charity",
    search_errors: list[str] | None = None,
) -> RiskScore:
    """Aggregate category scores into final RiskScore (V4 intelligence engine).

    Pipeline:
      1. Diminishing returns per category (log-scaled)
      2. Weighted category aggregation
      3. Cross-category correlation bonus
      4. Hard-stop floor enforcement
      5. Multi-dimensional confidence computation
    """
    # Category weights (sum to 1.0)
    if entity_type == "charity":
        weights = {
            "Geography": 0.15,
            "Financial": 0.20,
            "Governance": 0.20,
            "Media & Screening": 0.20,
            "Transparency": 0.15,
            "Operational": 0.10,
        }
    else:
        weights = {
            "Geography": 0.10,
            "Financial": 0.15,
            "Governance": 0.20,
            "Media & Screening": 0.20,
            "Transparency": 0.15,
            "Operational": 0.20,
        }

    # Step 1: Diminishing returns (prevents minor-flag stacking)
    adjusted = _apply_diminishing_returns(category_scores, signals)
    methodology.append("Applied log-scaled diminishing returns per category")

    # Also keep raw scores for display
    capped_raw = {k: min(v, 100.0) for k, v in category_scores.items()}

    # Step 2: Weighted sum
    overall = sum(adjusted.get(k, 0.0) * w for k, w in weights.items())

    # Step 3: Correlation bonus
    overall += _correlation_bonus(signals, methodology)

    # Step 4: Hard stops override — floor at 65 (Critical)
    if hard_stops:
        overall = max(overall, 65.0)
        methodology.append("Hard stop detected — score floor at 65 (Critical)")

    overall = _clamp(overall)

    # Category levels (from raw scores for transparent display)
    cat_levels = {k: _level_from_score(v) for k, v in capped_raw.items()}

    # Step 5: Bayesian confidence
    confidence, conf_notes = _compute_confidence(
        signals, hard_stops, entity_type, search_errors,
    )
    methodology.extend(conf_notes)

    return RiskScore(
        overall_score=round(overall, 1),
        overall_level=_level_from_score(overall),
        category_scores={k: round(v, 1) for k, v in capped_raw.items()},
        category_levels=cat_levels,
        signals=signals,
        hard_stops=hard_stops,
        confidence=confidence,
        methodology_notes=methodology,
    )


# ── Geography scoring (shared between charity & company) ─────────────────────

def _score_geography(classified: list[dict], signals: list, scores: dict):
    """Score geography risk from classified country list."""
    vh_count = sum(1 for c in classified if c.get("risk_level") == "Very High Risk")
    h_count = sum(1 for c in classified if c.get("risk_level") == "High Risk")

    if vh_count:
        _add(signals, scores, "Geography",
             f"{vh_count} Very High Risk jurisdiction(s) in operational area",
             RiskLevel.HIGH, "country_risk", min(vh_count * 10, 30))
    if h_count:
        _add(signals, scores, "Geography",
             f"{h_count} High Risk jurisdiction(s) in operational area",
             RiskLevel.MEDIUM, "country_risk", min(h_count * 5, 15))


# ── Financial scoring (charity-specific) ─────────────────────────────────────

def _score_financial_charity(
    charity_data: dict, history: list[dict], anomalies: dict,
    signals: list, scores: dict,
):
    """Score financial risk for a charity (V4: pattern-severity-aware)."""
    income = safe_float(charity_data.get("latest_income"))
    expenditure = safe_float(charity_data.get("latest_expenditure"))

    # No financial data
    if income == 0 and expenditure == 0:
        _add(signals, scores, "Financial",
             "No income or expenditure recorded", RiskLevel.MEDIUM,
             "financial_data", 8)
        return

    # Spend ratio
    if income > 0:
        ratio = expenditure / income
        if ratio > 1.15:
            _add(signals, scores, "Financial",
                 f"Expenditure exceeds income by {(ratio-1)*100:.0f}%",
                 RiskLevel.HIGH, "financial_ratio", 12)
        elif ratio > 0.95:
            _add(signals, scores, "Financial",
                 "Expenditure very close to or exceeds income",
                 RiskLevel.MEDIUM, "financial_ratio", 5)

    # ── V4: Consume financial pattern severities ─────────────────────
    # Instead of just counting anomalies, score each pattern by its actual
    # severity and confidence from the detection engine.
    pattern_report = anomalies.get("_pattern_report")  # PatternReport dict
    if pattern_report and isinstance(pattern_report, dict):
        patterns = pattern_report.get("patterns", [])
        # Severity → score points mapping comes from core.scoring_config so the
        # rationale is documented in one place and tunable centrally.
        _sev_points = SEVERITY_POINTS
        _sev_levels = {
            "critical": RiskLevel.CRITICAL, "high": RiskLevel.HIGH,
            "medium": RiskLevel.MEDIUM, "low": RiskLevel.LOW,
        }
        scored_count = 0
        for pat in patterns[:8]:  # cap at 8 patterns to prevent flooding
            sev = pat.get("severity", "info")
            pts = _sev_points.get(sev, 0)
            conf = pat.get("confidence", 0.7)
            if pts == 0:
                continue
            # Scale points by pattern's own confidence
            adj_pts = round(pts * conf, 1)
            level = _sev_levels.get(sev, RiskLevel.MEDIUM)
            title = pat.get("title", "Financial anomaly detected")
            _add(signals, scores, "Financial", title, level,
                 f"financial_pattern_{pat.get('pattern_type', 'unknown')}", adj_pts)
            scored_count += 1

        # Health score integration — overall financial health as secondary signal
        health = pattern_report.get("overall_health", "unknown")
        if health == "critical":
            _add(signals, scores, "Financial",
                 "Overall financial health assessed as CRITICAL",
                 RiskLevel.HIGH, "financial_health", 8)
        elif health == "concern":
            _add(signals, scores, "Financial",
                 "Overall financial health: concern",
                 RiskLevel.MEDIUM, "financial_health", 4)

    else:
        # Fallback: old-style anomaly count scoring
        anomaly_count = safe_int(anomalies.get("anomaly_count"))
        if anomaly_count >= 5:
            _add(signals, scores, "Financial",
                 f"{anomaly_count} financial anomalies detected",
                 RiskLevel.HIGH, "financial_anomalies", 15)
        elif anomaly_count >= 3:
            _add(signals, scores, "Financial",
                 f"{anomaly_count} financial anomalies detected",
                 RiskLevel.MEDIUM, "financial_anomalies", 8)
        elif anomaly_count >= 1:
            _add(signals, scores, "Financial",
                 f"{anomaly_count} financial anomaly/anomalies detected",
                 RiskLevel.LOW, "financial_anomalies", 3)

    # Insufficient history
    if len(history) < 2:
        _add(signals, scores, "Financial",
             "Less than 2 years of financial history available",
             RiskLevel.MEDIUM, "financial_history", 5)


# ── Governance scoring (charity-specific) ────────────────────────────────────

def _score_governance(
    charity_data: dict, indicators: dict, structural: dict,
    cc_governance: dict, ch_data: dict | None,
    signals: list, scores: dict,
):
    """Score governance risk for a charity."""
    # Structural flags
    capacity_flags = safe_list(structural.get("capacity_flags"))
    concentration_flags = safe_list(structural.get("concentration_flags"))
    total = len(capacity_flags) + len(concentration_flags)
    if total >= 4:
        _add(signals, scores, "Governance",
             f"{total} structural governance concerns",
             RiskLevel.HIGH, "structural_governance", 12)
    elif total >= 2:
        _add(signals, scores, "Governance",
             f"{total} structural governance observation(s)",
             RiskLevel.MEDIUM, "structural_governance", 5)

    # Gift aid
    ga = indicators.get("gift_aid_flag", "")
    if ga == "warning":
        _add(signals, scores, "Governance",
             "Gift Aid status: warning (not recognised or removed)",
             RiskLevel.MEDIUM, "gift_aid", 5)

    # CH consistency
    ch_con = indicators.get("ch_consistency") or ""
    if "should have" in ch_con.lower() and "none found" in ch_con.lower():
        _add(signals, scores, "Governance",
             "Missing expected Companies House registration",
             RiskLevel.MEDIUM, "ch_consistency", 6)

    # Registration history flags
    for flag in safe_list(indicators.get("reg_history_flags")):
        _add(signals, scores, "Governance",
             f"Registration event: {flag}",
             RiskLevel.LOW, "reg_history", 2)

    # Young charity
    years = indicators.get("years_registered")
    if years is not None and years < 2:
        _add(signals, scores, "Governance",
             f"Charity registered less than 2 years ago ({years} year(s))",
             RiskLevel.MEDIUM, "charity_age", 5)


# ── Media scoring (charity-specific) ────────────────────────────────────────

def _score_media_charity(
    adverse_org: list[dict], adverse_trustees: dict,
    fatf_org: dict | None, fatf_trustees: dict,
    signals: list, scores: dict, hard_stops: list,
):
    """Score media & screening risk for a charity (V4: severity-weighted)."""
    # ── Org adverse media (V4: use severity + source credibility) ────
    relevant_org = [r for r in adverse_org if r.get("_relevant")]
    if relevant_org:
        # Use weighted scoring if available (V4 enriched results)
        has_intelligence = any("_severity" in r for r in relevant_org)
        if has_intelligence:
            critical_hits = sum(1 for r in relevant_org if r.get("_severity") == "critical")
            high_hits = sum(1 for r in relevant_org if r.get("_severity") == "high")
            med_hits = sum(1 for r in relevant_org if r.get("_severity") in ("medium", "low"))
            avg_cred = sum(r.get("_source_credibility", 0.4) for r in relevant_org) / len(relevant_org)

            if critical_hits:
                _add(signals, scores, "Media & Screening",
                     f"{critical_hits} CRITICAL-severity adverse hit(s) for organisation "
                     f"(avg source credibility: {avg_cred:.0%})",
                     RiskLevel.CRITICAL, "adverse_org", min(critical_hits * 12 + 5, 25))
            if high_hits:
                _add(signals, scores, "Media & Screening",
                     f"{high_hits} HIGH-severity adverse hit(s) for organisation",
                     RiskLevel.HIGH, "adverse_org", min(high_hits * 8, 18))
            if med_hits:
                _add(signals, scores, "Media & Screening",
                     f"{med_hits} medium/low-severity adverse hit(s) for organisation",
                     RiskLevel.MEDIUM, "adverse_org", min(med_hits * 4, 10))
        else:
            # Fallback: old-style count-based scoring
            org_count = len(relevant_org)
            if org_count >= 3:
                _add(signals, scores, "Media & Screening",
                     f"{org_count} adverse media hits for organisation",
                     RiskLevel.HIGH, "adverse_org", 15)
            elif org_count >= 1:
                _add(signals, scores, "Media & Screening",
                     f"{org_count} adverse media hit(s) for organisation",
                     RiskLevel.MEDIUM, "adverse_org", 7)

    # Trustee adverse media
    trustee_hits = 0
    for name, results in (adverse_trustees or {}).items():
        hits = sum(1 for r in results if r.get("_relevant"))
        if hits:
            trustee_hits += hits
            _add(signals, scores, "Media & Screening",
                 f"Trustee '{name}': {hits} adverse media hit(s)",
                 RiskLevel.MEDIUM, "adverse_trustees", min(hits * 5, 10))

    # FATF screening
    if fatf_org:
        fatf_risk = (fatf_org.get("risk_level") or "").lower()
        if fatf_risk in ("high", "critical"):
            hard_stops.append("FATF screening: High risk match for organisation")
            _add(signals, scores, "Media & Screening",
                 "FATF predicate-offence match (organisation)",
                 RiskLevel.CRITICAL, "fatf_org", 25)
        elif fatf_risk == "medium":
            _add(signals, scores, "Media & Screening",
                 "FATF screening: medium risk for organisation",
                 RiskLevel.MEDIUM, "fatf_org", 8)

    for name, screen in (fatf_trustees or {}).items():
        t_risk = (screen.get("risk_level") or "").lower()
        if t_risk in ("high", "critical"):
            hard_stops.append(f"FATF screening: High risk match for trustee '{name}'")
            _add(signals, scores, "Media & Screening",
                 f"FATF match for trustee '{name}'",
                 RiskLevel.CRITICAL, "fatf_trustee", 20)


# ── Transparency scoring (shared) ───────────────────────────────────────────

def _score_transparency(
    hrcob: dict, policy_classification: list[dict],
    social_links: dict, online_presence: list[dict],
    signals: list, scores: dict,
):
    """Score transparency / disclosure risk."""
    # HRCOB core controls
    hrcob_status = (hrcob.get("hrcob_status") or "").lower()
    if hrcob_status == "fail":
        _add(signals, scores, "Transparency",
             "HRCOB core controls assessment: FAIL",
             RiskLevel.HIGH, "hrcob_core", 15)
    elif hrcob_status == "partial":
        _add(signals, scores, "Transparency",
             "HRCOB core controls: partially met",
             RiskLevel.MEDIUM, "hrcob_core", 6)

    # Policy discovery
    if policy_classification:
        not_found = sum(1 for p in policy_classification
                        if (p.get("status") or "").lower() == "not located")
        total = len(policy_classification)
        if total and not_found / total > 0.7:
            _add(signals, scores, "Transparency",
                 f"{not_found}/{total} policies not located on website",
                 RiskLevel.MEDIUM, "policies", 8)
        elif total and not_found / total > 0.4:
            _add(signals, scores, "Transparency",
                 f"{not_found}/{total} policies not located",
                 RiskLevel.LOW, "policies", 4)

    # Online presence
    if not social_links or all(not v for v in social_links.values()):
        _add(signals, scores, "Transparency",
             "No social media presence detected",
             RiskLevel.LOW, "social_media", 3)


# ── Operational scoring (charity-specific) ───────────────────────────────────

def _score_operational_charity(
    charity_data: dict, cc_governance: dict,
    signals: list, scores: dict,
):
    """Score operational risk for a charity."""
    # Reporting status
    status = charity_data.get("charity_reporting_status", "")
    if status and "not" in status.lower():
        _add(signals, scores, "Operational",
             f"Charity reporting status: {status}",
             RiskLevel.MEDIUM, "reporting_status", 6)

    # No declared policies (from CC data)
    policies_count = len(safe_list(cc_governance.get("cc_declared_policies")))
    if policies_count == 0:
        _add(signals, scores, "Operational",
             "No policies declared to Charity Commission",
             RiskLevel.MEDIUM, "declared_policies", 5)
