"""
french_company_check.py — Comprehensive French Company Analysis

Complete UK-equivalent company sense-check for French companies.
Performs full analysis matching company_check.py but tailored for INPI data:

  ✅ Company status & regulatory analysis
  ✅ Management team evaluation with risk scoring
  ✅ UBO/beneficial ownership structure analysis
  ✅ Address credibility assessment
  ✅ Dormancy detection
  ✅ Restricted activities screening
  ✅ High-risk industry detection (30 APE codes)
  ✅ Sanctions & adverse media screening
  ✅ Risk matrix compilation with hard stops
  ✅ Financial health indicators
  ✅ Compliance timeline analysis
"""

import logging
import os
from typing import Dict, Any, Callable, Optional
from datetime import datetime

from api_clients.french_registry import (
    FrenchRegistryClient,
    FrenchCompanyBasic,
)
from core.fatf_screener import screen_entity
from core.high_risk_industries import flag_high_risk_industry
from core.french_dashboard import FrenchDashboardBuilder
from core.french_company_analysis import (
    run_comprehensive_analysis,
    FrenchManagementAnalysis,
    FrenchAddressAssessment,
    FrenchDormancyDetection,
    FrenchUBOAnalysis,
    FrenchRestrictedActivities,
    FrenchCompanyStatus,
)

logger = logging.getLogger(__name__)


def run_french_company_check(
    siren: str,
    website_url: str = "",
    tavily_search_fn: Optional[Callable] = None,
    adverse_search_fn: Optional[Callable] = None,
    fatf_screen_fn: Optional[Callable] = None,
) -> Dict[str, Any]:
    """
    Run comprehensive sense-check for a French company.
    
    Performs all UK-equivalent analysis using INPI data:
      ✅ Company status & regulatory analysis
      ✅ Management team evaluation
      ✅ UBO/beneficial ownership tracing
      ✅ Address credibility assessment
      ✅ Dormancy detection
      ✅ Restricted activities screening
      ✅ High-risk industry detection
      ✅ Sanctions & adverse media screening
      ✅ Risk matrix compilation
    
    Args:
        siren: 9-digit SIREN number
        website_url: Company website URL (optional)
        tavily_search_fn: Web search function
        adverse_search_fn: Adverse media search function
        fatf_screen_fn: FATF/sanctions screening function
    
    Returns:
        Dictionary with company data and comprehensive risk assessment
    """
    
    # ═══════════════════════════════════════════════════════════════════════════
    # STEP 0: VALIDATION
    # ═══════════════════════════════════════════════════════════════════════════
    siren = siren.strip().replace(" ", "")
    if len(siren) != 9 or not siren.isdigit():
        raise ValueError(f"Invalid SIREN format: {siren}. Must be 9 digits.")
    
    logger.info(f"🇫🇷 Running comprehensive French company check for SIREN {siren}")
    
    # ═══════════════════════════════════════════════════════════════════════════
    # STEP 1: FETCH DATA FROM INPI
    # ═══════════════════════════════════════════════════════════════════════════
    logger.info(f"Step 1/5: Fetching company data from INPI...")
    
    try:
        registry_client = FrenchRegistryClient()
    except ValueError as e:
        raise ValueError(
            "INPI credentials not configured. Set FRENCH_REGISTRY_EMAIL and "
            "FRENCH_REGISTRY_PASSWORD environment variables."
        )
    
    company = registry_client.get_company_by_siren(siren)
    if not company:
        raise ValueError(f"Company with SIREN {siren} not found in INPI registry")
    
    financial_records = registry_client.get_financial_records(siren)
    formality_records = registry_client.get_formality_records(siren)
    
    logger.info(f"✓ Found: {company.name}")
    logger.info(f"  - Financial records: {len(financial_records)}")
    logger.info(f"  - Formality records: {len(formality_records)}")
    
    # ═══════════════════════════════════════════════════════════════════════════
    # STEP 2: BUILD DASHBOARD & RUN ANALYSIS
    # ═══════════════════════════════════════════════════════════════════════════
    logger.info(f"Step 2/5: Building dashboard and running analysis...")
    
    dashboard_data = {}
    analysis_results = {}
    
    try:
        # Build rich dashboard from raw INPI data
        raw_inpi_data = getattr(registry_client, '_last_response', {})
        dashboard_builder = FrenchDashboardBuilder(raw_inpi_data, company.name, siren)
        dashboard_data = dashboard_builder.build_complete_dashboard()
        
        # Run comprehensive analysis
        analysis_results = run_comprehensive_analysis(dashboard_data)
        logger.info(f"✓ Analysis complete")
    except Exception as e:
        logger.warning(f"Dashboard/analysis building: {e}")
        dashboard_data = {}
        analysis_results = {}
    
    # ═══════════════════════════════════════════════════════════════════════════
    # STEP 3: SCREENING (HIGH-RISK, SANCTIONS, ADVERSE MEDIA)
    # ═══════════════════════════════════════════════════════════════════════════
    logger.info(f"Step 3/5: Running screening checks...")
    
    # Extract APE codes
    ape_codes = []
    if hasattr(company, 'ape_code') and company.ape_code:
        ape_codes = [company.ape_code]
    elif hasattr(company, 'ape_codes') and company.ape_codes:
        ape_codes = company.ape_codes
    
    # High-risk industry screening
    hrob_screening = flag_high_risk_industry(
        sic_codes=None,
        ape_codes=ape_codes,
        company_name=company.name,
        country="france"
    )
    
    if hrob_screening["is_high_risk"]:
        logger.warning(f"⚠️ High-risk industry: {hrob_screening['summary']}")
    
    # Sanctions screening
    sanctions_result = {"risk": "unknown"}
    if fatf_screen_fn:
        try:
            sanctions_result = fatf_screen_fn(
                entity_name=company.name,
                entity_type="company",
                country="FR"
            )
        except Exception as e:
            logger.warning(f"Sanctions screening error: {e}")
    
    # Adverse media screening
    adverse_media_result = {"alerts": []}
    if adverse_search_fn:
        try:
            search_query = f"{company.name} {company.city} France"
            adverse_media_result = adverse_search_fn(search_query)
        except Exception as e:
            logger.warning(f"Adverse media screening error: {e}")
    
    # ═══════════════════════════════════════════════════════════════════════════
    # STEP 4: CALCULATE RISK SCORE
    # ═══════════════════════════════════════════════════════════════════════════
    logger.info(f"Step 4/5: Calculating risk score...")
    
    risk_score = 0
    risk_factors = []
    
    # Extract analysis results (with defaults)
    mgmt_analysis = analysis_results.get("management_analysis", {})
    addr_assessment = analysis_results.get("address_assessment", {})
    dormancy = analysis_results.get("dormancy_detection", {})
    ubo_analysis = analysis_results.get("ubo_analysis", {})
    restricted = analysis_results.get("restricted_activities", {})
    company_status = analysis_results.get("company_status", {})
    
    # FACTOR 1: Management Risk
    mgmt_risk = mgmt_analysis.get("management_risk_score", 0)
    risk_score += mgmt_risk
    if mgmt_analysis.get("flags"):
        for flag in mgmt_analysis["flags"][:3]:  # Top 3 flags
            risk_factors.append(f"Management: {flag}")
    
    # FACTOR 2: Address Credibility
    addr_cred = 100 - addr_assessment.get("credibility_score", 100)
    risk_score += addr_cred * 0.3  # Weight at 30%
    if addr_assessment.get("address_type") == "Virtual":
        risk_factors.append("⚠️ Virtual office address")
    
    # FACTOR 3: Dormancy
    if dormancy.get("is_dormant"):
        risk_score += 30
        risk_factors.append("🔴 Company appears dormant")
    
    # FACTOR 4: UBO Transparency
    if not ubo_analysis.get("ubo_identified"):
        risk_score += 15
        risk_factors.append("⚠️ Beneficial owners not clearly identified")
    
    # FACTOR 5: Restricted Activities
    if restricted.get("has_restrictions"):
        risk_score += 20
        for activity in restricted.get("prohibited", [])[:2]:
            risk_factors.append(f"🔴 Prohibited activity: {activity}")
    
    # FACTOR 6: Legal Status
    if not company_status.get("is_operational"):
        risk_score += 40
        risk_factors.append(f"🔴 Company not operational: {company_status.get('legal_status')}")
    
    # FACTOR 7: High-Risk Industry
    if hrob_screening["is_high_risk"]:
        risk_score += 25
        risk_factors.append(f"🔴 High-risk industry detected")
    
    # FACTOR 8: Sanctions
    if sanctions_result.get("risk") in ("high", "medium"):
        risk_score += 30
        risk_factors.append("🔴 Sanctions/FATF match")
    
    # FACTOR 9: Adverse Media
    adverse_count = len(adverse_media_result.get("alerts", []))
    if adverse_count > 0:
        risk_score += min(20, adverse_count * 5)
        risk_factors.append(f"⚠️ {adverse_count} adverse media alert(s)")
    
    # FACTOR 10: Company Age & Maturity
    if company.creation_date:
        try:
            creation_year = int(company.creation_date[:4])
            age_years = datetime.now().year - creation_year
            if age_years < 1:
                risk_score += 20
                risk_factors.append("⚠️ Very new company (< 1 year)")
            elif age_years < 3:
                risk_score += 10
                risk_factors.append("⚠️ Early-stage company (1-3 years)")
        except:
            pass
    
    # Normalize score
    risk_score = min(100, max(0, int(risk_score)))
    
    # Determine overall risk
    if risk_score >= 75:
        overall_risk = "High"
        emoji = "🔴"
    elif risk_score >= 50:
        overall_risk = "Medium"
        emoji = "🟡"
    elif risk_score >= 25:
        overall_risk = "Low-Medium"
        emoji = "🟡"
    else:
        overall_risk = "Low"
        emoji = "🟢"
    
    # ═══════════════════════════════════════════════════════════════════════════
    # STEP 5: BUILD RETURN STRUCTURE (UK-equivalent format)
    # ═══════════════════════════════════════════════════════════════════════════
    logger.info(f"Step 5/5: Compiling results...")
    
    result = {
        # ─── Company Details ───
        "company_number": siren,
        "company_name": company.name,
        "company_type": "French Company (INPI Registry)",
        "country": "France",
        
        # ─── Company Profile ───
        "company_profile": {
            "name": company.name,
            "siren": siren,
            "legal_form": company.legal_form,
            "status": company.status,
            "incorporation_date": company.creation_date,
            "head_office": company.address,
            "postal_code": company.postal_code,
            "city": company.city,
            "website": website_url,
            "ape_code": getattr(company, 'ape_code', ''),
        },
        
        # ─── Dashboard Data ───
        "dashboard": dashboard_data,
        
        # ─── Detailed Analysis Results ───
        "analysis": {
            "management": mgmt_analysis,
            "address": addr_assessment,
            "dormancy": dormancy,
            "ubo": ubo_analysis,
            "restricted_activities": restricted,
            "legal_status": company_status,
        },
        
        # ─── Financial Data ───
        "financial_health": {
            "recent_filings": len(financial_records),
            "latest_filing": financial_records[0].year if financial_records else None,
            "records": [
                {
                    "year": getattr(r, 'year', None),
                    "revenue": getattr(r, 'revenue', None),
                    "net_profit": getattr(r, 'net_profit', None),
                } if hasattr(r, 'year') else r
                for r in financial_records
            ]
        },
        
        # ─── Compliance & Formality ───
        "formality_records": [
            {
                "date": getattr(r, 'date', None),
                "description": getattr(r, 'description', None),
                "type": getattr(r, 'type', None),
            } if hasattr(r, 'date') else r
            for r in formality_records
        ],
        
        # ─── HROB Assessment ───
        "hrob_verticals": {
            "requires_hrob": hrob_screening.get("requires_hrob", False),
            "matched_industries": hrob_screening.get("matched_industries", []),
            "summary": hrob_screening.get("summary", ""),
        },
        
        # ─── Screening Results ───
        "sanctions_screening": sanctions_result,
        "fatf_screening": sanctions_result,  # UK-equivalent alias
        "adverse_media": adverse_media_result,
        "restricted_activities": restricted.get("prohibited", []),
        
        # ─── Risk Matrix (UK-equivalent) ───
        "risk_matrix": {
            "risk_score": risk_score,
            "overall_risk": overall_risk,
            "risk_emoji": emoji,
            "risk_factors": risk_factors,
            "hard_stop_triggered": risk_score >= 90,
            "category_risks": {
                "management": mgmt_analysis.get("role_quality", "unknown"),
                "address_credibility": addr_assessment.get("credibility_level", "unknown"),
                "dormancy": "high" if dormancy.get("is_dormant") else "low",
                "ubo_transparency": ubo_analysis.get("transparency_level", "unknown"),
                "restricted_activities": "high" if restricted.get("has_restrictions") else "low",
                "legal_status": "high" if not company_status.get("is_operational") else "low",
                "industry_risk": "high" if hrob_screening["is_high_risk"] else "low",
                "sanctions": sanctions_result.get("risk", "unknown"),
                "adverse_media": "high" if adverse_count > 5 else "medium" if adverse_count > 0 else "low",
                "maturity": "medium" if (company.creation_date and datetime.now().year - int(company.creation_date[:4]) < 3) else "low",
            },
            "all_flags": risk_factors,
        },
        
        # ─── Metadata ───
        "check_timestamp": datetime.now().isoformat(),
        "data_source": "INPI (Institut National de la Propriété Industrielle)",
        "check_type": "french_comprehensive",
    }
    
    logger.info(f"✓ French company check complete")
    logger.info(f"  Risk Score: {risk_score}/100 ({overall_risk})")
    logger.info(f"  Hard stop: {'YES' if risk_score >= 90 else 'NO'}")
    
    return result


def detect_company_country(identifier: str) -> str:
    """
    Detect if identifier is UK company number or French SIREN.
    
    Args:
        identifier: Company number or SIREN
    
    Returns:
        "UK" or "France"
    """
    identifier = identifier.strip()
    
    # French SIREN: always 9 digits
    if len(identifier) == 9 and identifier.isdigit():
        return "France"
    
    # UK Companies House: 8 digits
    if len(identifier) == 8 and identifier.isdigit():
        return "UK"
    
    # Default
    return "UK"


if __name__ == "__main__":
    print("French Company Check Module (Comprehensive)")
    print("=" * 70)
    print("Provides UK-equivalent analysis for French companies via INPI")
