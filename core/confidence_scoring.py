"""
core/confidence_scoring.py — Confidence scoring for AI conclusions.

Computes a data-driven confidence score indicating how strong the evidence
is behind the AI's key findings. Takes into account:
  - Number and quality of data sources
  - Completeness of data collected
  - Consistency between different data points
  - Age/freshness of information

Public API:
    compute_confidence(analysis_data)  → ConfidenceReport
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


# ═══════════════════════════════════════════════════════════════════════════════
# MODELS
# ═══════════════════════════════════════════════════════════════════════════════

class DataCompleteness(BaseModel):
    """Assessment of how complete the data collection was."""
    category: str
    available: bool = False
    quality: str = Field("unknown", description="high|medium|low|missing")
    detail: str = ""
    weight: float = Field(1.0, description="How important this data is")


class ConsistencyCheck(BaseModel):
    """Result of a cross-reference consistency check."""
    check_name: str
    consistent: bool = True
    detail: str = ""
    severity: str = Field("info", description="high|medium|low|info")


class ConfidenceReport(BaseModel):
    """Complete confidence assessment."""
    overall_confidence: float = Field(0.5, ge=0, le=1)
    confidence_label: str = Field("Moderate", description="Very High|High|Moderate|Low|Very Low")
    data_completeness: list[DataCompleteness] = Field(default_factory=list)
    completeness_score: float = Field(0.5, ge=0, le=1)
    consistency_checks: list[ConsistencyCheck] = Field(default_factory=list)
    consistency_score: float = Field(0.5, ge=0, le=1)
    source_count: int = 0
    data_age_note: str = ""
    limitations: list[str] = Field(default_factory=list)
    summary: str = ""


# ═══════════════════════════════════════════════════════════════════════════════
# CONFIDENCE CALCULATOR
# ═══════════════════════════════════════════════════════════════════════════════

def compute_confidence_charity(
    charity_data: dict[str, Any],
    financial_history: list[dict],
    ch_data: dict | None,
    adverse_org: list[dict],
    adverse_trustees: dict[str, list[dict]],
    fatf_org_screen: dict | None,
    cc_pdf_text: str,
    uploaded_text: str,
    country_risk_classified: list[dict],
    policy_classification: list[dict],
    social_links: dict,
    cc_governance: dict,
) -> ConfidenceReport:
    """Compute confidence score for charity analysis."""

    completeness: list[DataCompleteness] = []
    consistency: list[ConsistencyCheck] = []
    limitations: list[str] = []
    source_count = 0

    # ── 1. Data Completeness Assessment ──────────────────────────────

    # Charity Commission data
    has_cc = bool(charity_data and charity_data.get("charity_name"))
    completeness.append(DataCompleteness(
        category="Charity Commission Record",
        available=has_cc,
        quality="high" if has_cc else "missing",
        detail="Primary official data source" if has_cc else "No CC data retrieved",
        weight=2.0,
    ))
    if has_cc:
        source_count += 1

    # Financial history
    fin_years = len(financial_history or [])
    fin_quality = "high" if fin_years >= 4 else ("medium" if fin_years >= 2 else ("low" if fin_years == 1 else "missing"))
    completeness.append(DataCompleteness(
        category="Financial History",
        available=fin_years > 0,
        quality=fin_quality,
        detail=f"{fin_years} year(s) of data" if fin_years else "No financial history",
        weight=1.8,
    ))
    if fin_years:
        source_count += 1

    # Companies House cross-reference
    has_ch = bool(ch_data and ch_data.get("company_name"))
    completeness.append(DataCompleteness(
        category="Companies House Cross-Reference",
        available=has_ch,
        quality="high" if has_ch else ("low" if charity_data.get("company_number") else "medium"),
        detail="CH data retrieved" if has_ch else ("Entity has no linked company" if not charity_data.get("company_number") else "CH lookup failed"),
        weight=1.2,
    ))
    if has_ch:
        source_count += 1

    # Adverse media screening
    has_adverse = bool(adverse_org) or any(v for v in (adverse_trustees or {}).values())
    total_adverse = len(adverse_org or []) + sum(len(v) for v in (adverse_trustees or {}).values())
    completeness.append(DataCompleteness(
        category="Adverse Media Screening",
        available=True,  # Always attempted
        quality="high" if total_adverse > 0 or has_adverse else "medium",
        detail=f"{total_adverse} results found" if total_adverse else "Screening completed, no results",
        weight=1.5,
    ))
    source_count += 1

    # FATF screening
    has_fatf = bool(fatf_org_screen)
    completeness.append(DataCompleteness(
        category="FATF Predicate Offence Screen",
        available=has_fatf,
        quality="high" if has_fatf else "missing",
        detail="FATF screening completed" if has_fatf else "FATF screening not available",
        weight=1.3,
    ))
    if has_fatf:
        source_count += 1

    # Document analysis (TAR/Annual Report)
    has_docs = bool(cc_pdf_text and len(cc_pdf_text) > 100)
    has_uploads = bool(uploaded_text and len(uploaded_text) > 100)
    completeness.append(DataCompleteness(
        category="Annual Report / Documents",
        available=has_docs or has_uploads,
        quality="high" if has_docs else ("medium" if has_uploads else "missing"),
        detail=f"CC docs: {'yes' if has_docs else 'no'}, Uploaded: {'yes' if has_uploads else 'no'}",
        weight=1.4,
    ))
    if has_docs or has_uploads:
        source_count += 1

    # Country risk assessment
    has_geo = bool(country_risk_classified)
    completeness.append(DataCompleteness(
        category="Geographic Risk Assessment",
        available=has_geo,
        quality="high" if has_geo else "missing",
        detail=f"{len(country_risk_classified)} countries assessed" if has_geo else "No geographic data",
        weight=1.0,
    ))

    # Policy classification
    has_policies = bool(policy_classification)
    completeness.append(DataCompleteness(
        category="Governance Policy Check",
        available=has_policies,
        quality="high" if has_policies else "low",
        detail=f"{len(policy_classification)} policies checked" if has_policies else "No policy data",
        weight=1.0,
    ))

    # Web/Social presence
    has_social = bool(social_links and any(social_links.values()))
    completeness.append(DataCompleteness(
        category="Web & Social Presence",
        available=has_social,
        quality="medium" if has_social else "low",
        detail="Social profiles found" if has_social else "Limited web presence data",
        weight=0.6,
    ))

    # ── 2. Consistency Checks ────────────────────────────────────────

    # Income consistency: CC vs financial history
    if has_cc and financial_history:
        cc_income = charity_data.get("latest_income", 0) or 0
        latest_fin = financial_history[-1] if financial_history else {}
        fin_income = latest_fin.get("income", 0) or 0
        if cc_income > 0 and fin_income > 0:
            ratio = min(cc_income, fin_income) / max(cc_income, fin_income)
            consistent = ratio > 0.7
            consistency.append(ConsistencyCheck(
                check_name="CC income vs financial history",
                consistent=consistent,
                detail=f"CC: £{cc_income:,.0f} vs History: £{fin_income:,.0f} (ratio: {ratio:.2f})",
                severity="medium" if not consistent else "info",
            ))

    # Trustee count consistency
    if has_cc:
        cc_trustees = len(charity_data.get("trustees", []))
        cc_num = charity_data.get("num_trustees", 0) or cc_trustees
        if cc_num > 0 and cc_trustees > 0:
            consistent = abs(cc_num - cc_trustees) <= 1
            if not consistent:
                consistency.append(ConsistencyCheck(
                    check_name="Trustee count consistency",
                    consistent=False,
                    detail=f"Reported: {cc_num}, Listed: {cc_trustees}",
                    severity="low",
                ))

    # CH status vs CC registration
    if has_ch and has_cc:
        ch_status = (ch_data.get("company_status") or "").lower()
        if "dissolved" in ch_status or "liquidation" in ch_status:
            consistency.append(ConsistencyCheck(
                check_name="CH company status vs CC registration",
                consistent=False,
                detail=f"Company status is '{ch_status}' but charity is registered",
                severity="high",
            ))

    # CC governance data available
    if cc_governance:
        if cc_governance.get("regulatory_action"):
            consistency.append(ConsistencyCheck(
                check_name="Regulatory action check",
                consistent=False,
                detail=f"Regulatory action noted: {cc_governance.get('regulatory_action')}",
                severity="high",
            ))

    # ── 3. Calculate Scores ──────────────────────────────────────────

    # Completeness score (weighted)
    total_weight = sum(c.weight for c in completeness)
    quality_map = {"high": 1.0, "medium": 0.6, "low": 0.3, "missing": 0.0, "unknown": 0.2}
    weighted_quality = sum(
        quality_map.get(c.quality, 0.2) * c.weight
        for c in completeness
    )
    completeness_score = weighted_quality / total_weight if total_weight > 0 else 0.5

    # Consistency score
    if consistency:
        consistent_count = len([c for c in consistency if c.consistent])
        consistency_score = consistent_count / len(consistency)
    else:
        consistency_score = 0.7  # Neutral when no checks possible

    # Overall confidence (weighted blend)
    overall = 0.6 * completeness_score + 0.3 * consistency_score + 0.1 * min(source_count / 6, 1.0)

    # Data age penalty
    data_age_note = ""
    fin_year = charity_data.get("fin_year_end", "")
    if fin_year:
        try:
            for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
                try:
                    dt = datetime.strptime(str(fin_year)[:19], fmt)
                    months_old = (datetime.now() - dt).days / 30
                    if months_old > 24:
                        overall *= 0.9
                        data_age_note = f"Financial data is ~{int(months_old)} months old"
                        limitations.append(data_age_note)
                    elif months_old > 18:
                        overall *= 0.95
                        data_age_note = f"Financial data is ~{int(months_old)} months old"
                    break
                except ValueError:
                    continue
        except Exception:
            pass

    # Limitations
    if not has_docs and not has_uploads:
        limitations.append("No annual report or document text analysed")
    if not has_fatf:
        limitations.append("FATF screening unavailable")
    if not has_geo:
        limitations.append("No geographic risk data")
    if fin_years < 3:
        limitations.append(f"Only {fin_years} year(s) of financial data (3+ recommended)")

    # Label
    if overall >= 0.85:
        label = "Very High"
    elif overall >= 0.70:
        label = "High"
    elif overall >= 0.50:
        label = "Moderate"
    elif overall >= 0.30:
        label = "Low"
    else:
        label = "Very Low"

    summary_parts = [f"Confidence: {label} ({int(overall*100)}%)"]
    summary_parts.append(f"{source_count} data sources")
    if limitations:
        summary_parts.append(f"{len(limitations)} limitation(s)")
    summary = " · ".join(summary_parts)

    return ConfidenceReport(
        overall_confidence=round(overall, 2),
        confidence_label=label,
        data_completeness=completeness,
        completeness_score=round(completeness_score, 2),
        consistency_checks=consistency,
        consistency_score=round(consistency_score, 2),
        source_count=source_count,
        data_age_note=data_age_note,
        limitations=limitations,
        summary=summary,
    )


def compute_confidence_company(
    co_check_data: dict[str, Any],
) -> ConfidenceReport:
    """Compute confidence score for company analysis."""
    completeness: list[DataCompleteness] = []
    consistency: list[ConsistencyCheck] = []
    limitations: list[str] = []
    source_count = 0

    profile = co_check_data.get("basic_profile", {})
    directors = co_check_data.get("director_analysis", {})
    pscs = co_check_data.get("psc_analysis", {})
    filings = co_check_data.get("filing_analysis", {})
    web = co_check_data.get("online_presence", {})
    adverse = co_check_data.get("adverse_media", {})

    # Basic profile
    has_profile = bool(profile and profile.get("company_name"))
    completeness.append(DataCompleteness(
        category="Companies House Profile",
        available=has_profile,
        quality="high" if has_profile else "missing",
        detail="Full profile retrieved" if has_profile else "No profile data",
        weight=2.0,
    ))
    if has_profile:
        source_count += 1

    # Directors
    dir_list = directors.get("directors", [])
    completeness.append(DataCompleteness(
        category="Director Analysis",
        available=bool(dir_list),
        quality="high" if len(dir_list) >= 1 else "missing",
        detail=f"{len(dir_list)} directors found",
        weight=1.5,
    ))
    if dir_list:
        source_count += 1

    # PSCs
    psc_list = pscs.get("psc_details", [])
    completeness.append(DataCompleteness(
        category="PSC / Ownership Data",
        available=bool(psc_list),
        quality="high" if psc_list else "low",
        detail=f"{len(psc_list)} PSCs identified" if psc_list else "No PSC data",
        weight=1.5,
    ))

    # Filings
    completeness.append(DataCompleteness(
        category="Filing History",
        available=bool(filings),
        quality="high" if filings.get("latest_accounts") else "low",
        detail="Filing history available" if filings else "No filing data",
        weight=1.2,
    ))

    # Web presence
    has_web = bool(web and (web.get("website_found") or web.get("social_media")))
    completeness.append(DataCompleteness(
        category="Online Presence",
        available=has_web,
        quality="medium" if has_web else "low",
        detail="Web presence verified" if has_web else "Limited web data",
        weight=0.8,
    ))
    if has_web:
        source_count += 1

    # Adverse media
    completeness.append(DataCompleteness(
        category="Adverse Media Screen",
        available=True,
        quality="high",
        detail="Screening completed",
        weight=1.3,
    ))
    source_count += 1

    # Calculate
    total_weight = sum(c.weight for c in completeness)
    quality_map = {"high": 1.0, "medium": 0.6, "low": 0.3, "missing": 0.0, "unknown": 0.2}
    weighted_quality = sum(quality_map.get(c.quality, 0.2) * c.weight for c in completeness)
    completeness_score = weighted_quality / total_weight if total_weight > 0 else 0.5

    consistency_score = 0.75  # Default for company (fewer cross-checks available)

    overall = 0.6 * completeness_score + 0.3 * consistency_score + 0.1 * min(source_count / 4, 1.0)

    if not psc_list:
        limitations.append("No PSC/ownership data available")
    if not has_web:
        limitations.append("Limited online presence data")

    if overall >= 0.85:
        label = "Very High"
    elif overall >= 0.70:
        label = "High"
    elif overall >= 0.50:
        label = "Moderate"
    elif overall >= 0.30:
        label = "Low"
    else:
        label = "Very Low"

    return ConfidenceReport(
        overall_confidence=round(overall, 2),
        confidence_label=label,
        data_completeness=completeness,
        completeness_score=round(completeness_score, 2),
        consistency_checks=consistency,
        consistency_score=round(consistency_score, 2),
        source_count=source_count,
        limitations=limitations,
        summary=f"Confidence: {label} ({int(overall*100)}%) · {source_count} data sources",
    )


# ═══════════════════════════════════════════════════════════════════════════════
# UI HELPERS
# ═══════════════════════════════════════════════════════════════════════════════

def render_confidence_badge(report: ConfidenceReport) -> str:
    """Compact HTML badge showing confidence level."""
    color_map = {
        "Very High": "#28a745", "High": "#17a2b8",
        "Moderate": "#ffc107", "Low": "#fd7e14", "Very Low": "#dc3545",
    }
    color = color_map.get(report.confidence_label, "#6c757d")
    pct = int(report.overall_confidence * 100)

    return f"""
    <div style="display:inline-flex;align-items:center;gap:8px;padding:6px 12px;
                border-radius:6px;background:{color}12;border:1px solid {color}30;">
        <div style="font-size:18px;font-weight:700;color:{color};">{pct}%</div>
        <div>
            <div style="font-size:12px;font-weight:600;color:{color};">
                Evidence Confidence: {report.confidence_label}
            </div>
            <div style="font-size:10px;color:#666;">
                {report.source_count} sources · {int(report.completeness_score*100)}% complete
            </div>
        </div>
    </div>
    """


def render_confidence_detail(report: ConfidenceReport) -> str:
    """Detailed HTML breakdown of confidence components."""
    # Completeness table
    rows = ""
    for dc in report.data_completeness:
        qcolor = {"high": "#28a745", "medium": "#ffc107", "low": "#fd7e14", "missing": "#dc3545"}.get(dc.quality, "#6c757d")
        icon = {"high": "✅", "medium": "⚠️", "low": "⚡", "missing": "❌"}.get(dc.quality, "❓")
        rows += f"""
        <tr style="border-bottom:1px solid #f0f0f0;">
            <td style="padding:4px 6px;font-size:12px;">{icon}</td>
            <td style="padding:4px 6px;font-size:12px;">{dc.category}</td>
            <td style="padding:4px 6px;font-size:12px;color:{qcolor};font-weight:600;">{dc.quality.title()}</td>
            <td style="padding:4px 6px;font-size:11px;color:#666;">{dc.detail}</td>
        </tr>"""

    lim_html = ""
    if report.limitations:
        lim_items = "".join(f"<li style='font-size:11px;color:#856404;'>{l}</li>" for l in report.limitations)
        lim_html = f"<div style='margin-top:8px;'><strong style='font-size:12px;'>Limitations:</strong><ul style='margin:4px 0;padding-left:18px;'>{lim_items}</ul></div>"

    return f"""
    <table style="width:100%;border-collapse:collapse;">
        <thead><tr style="border-bottom:2px solid #ddd;">
            <th style="padding:4px 6px;font-size:11px;text-align:left;width:24px;"></th>
            <th style="padding:4px 6px;font-size:11px;text-align:left;">Data Source</th>
            <th style="padding:4px 6px;font-size:11px;text-align:left;">Quality</th>
            <th style="padding:4px 6px;font-size:11px;text-align:left;">Detail</th>
        </tr></thead>
        <tbody>{rows}</tbody>
    </table>
    {lim_html}
    """
