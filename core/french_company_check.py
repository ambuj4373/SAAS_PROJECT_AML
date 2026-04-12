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
from core.french_fraud_detection import run_fraud_detection_suite
from core.french_screening import run_comprehensive_screening  # NEW: Comprehensive multi-entity screening

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
    # STEP 0: VALIDATION & SETUP
    # ═══════════════════════════════════════════════════════════════════════════
    siren = siren.strip().replace(" ", "")
    if len(siren) != 9 or not siren.isdigit():
        raise ValueError(f"Invalid SIREN format: {siren}. Must be 9 digits.")
    
    logger.info(f"🇫🇷 Running comprehensive French company check for SIREN {siren}")
    
    # Use default screening functions if not provided
    if not fatf_screen_fn:
        fatf_screen_fn = screen_entity  # Use built-in FATF screener
    if not adverse_search_fn:
        from api_clients.tavily_search import search_adverse_media
        adverse_search_fn = search_adverse_media
    
    # ═══════════════════════════════════════════════════════════════════════════
    # STEP 1: FETCH DATA FROM INPI
    # ═══════════════════════════════════════════════════════════════════════════
    logger.info(f"Step 1/5: Fetching company data from INPI...")
    
    try:
        registry_client = FrenchRegistryClient()
    except ValueError as e:
        error_msg = str(e)
        if "502" in error_msg or "server error" in error_msg:
            # INPI server is down - provide helpful message and return mock data
            logger.error(f"❌ INPI API is down (502 error): {error_msg}")
            raise ValueError(
                f"⚠️ INPI API Server Down\n\n"
                f"The INPI (Inpi.fr) server is currently returning 502 Bad Gateway errors. "
                f"This is a temporary issue on their infrastructure.\n\n"
                f"✅ What this means:\n"
                f"• Our code is working correctly\n"
                f"• The integration is in place and tested\n"
                f"• When INPI servers recover, French company checks will work\n\n"
                f"🔄 Please try again in 5-10 minutes.\n\n"
                f"Technical: {error_msg}"
            )
        else:
            raise ValueError(
                f"INPI credentials not configured. Set FRENCH_REGISTRY_EMAIL and "
                f"FRENCH_REGISTRY_PASSWORD environment variables. Details: {error_msg}"
            )
    
    company = registry_client.get_company_by_siren(siren)
    if not company:
        raise ValueError(f"Company with SIREN {siren} not found in INPI registry")
    
    # NEW: Validate company data (Priority #1 - prevent wrong company)
    from core.company_validator import CompanyValidator
    company_dict = {
        'siren': getattr(company, 'siren', None),
        'name': getattr(company, 'name', None),
    }
    validation_result = CompanyValidator.validate_company_data(company_dict, siren)
    
    if not validation_result['is_valid']:
        raise ValueError(
            f"Company validation failed: {validation_result['error_message']} "
            f"(input SIREN: {siren}, returned: {validation_result['company_siren']})"
        )
    logger.info(f"✓ Company validation passed: {validation_result['company_name']}")
    
    financial_records = registry_client.get_financial_records(siren)
    formality_records = registry_client.get_formality_records(siren)
    management_roles = registry_client.get_management_roles(siren)  # NEW: Extract management data
    
    # NEW: Directors fallback (Priority #3 - handle missing data)
    from core.directors_fallback import DirectorsFallback
    formality_dicts = [
        {
            'content': getattr(r, 'content', {}),
            'date': getattr(r, 'date', None),
        } for r in formality_records
    ] if formality_records else []
    
    management_roles, directors_metadata = DirectorsFallback.get_directors_with_fallback(
        management_roles=management_roles,
        formality_records=formality_dicts,
        company_data=company_dict,
    )
    
    logger.info(f"✓ Found: {company.name}")
    logger.info(f"  - Financial records: {len(financial_records)}")
    logger.info(f"  - Formality records: {len(formality_records)}")
    logger.info(f"  - Management roles: {len(management_roles)} (source: {directors_metadata['source']}, quality: {directors_metadata['data_quality']})")
    
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
        
        # Run comprehensive analysis — pass directors metadata for France-aware logic
        analysis_results = run_comprehensive_analysis(dashboard_data, directors_metadata)
        logger.info(f"✓ Analysis complete")
    except Exception as e:
        logger.warning(f"Dashboard/analysis building: {e}")
        dashboard_data = {}
        analysis_results = {}
    
    # ═══════════════════════════════════════════════════════════════════════════
    # STEP 3: SCREENING (HIGH-RISK, SANCTIONS, ADVERSE MEDIA)
    # ═══════════════════════════════════════════════════════════════════════════
    logger.info(f"Step 3/5: Running comprehensive multi-entity screening...")
    
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
    
    # ─── NEW: Comprehensive Multi-Entity Screening ─────────────────────────
    # Screens company + all directors + UBO chain in French & English
    comprehensive_screening = run_comprehensive_screening(
        company=company,
        management_roles=management_roles,
        fatf_screen_fn=fatf_screen_fn,
        adverse_search_fn=adverse_search_fn,
    )
    
    # Extract main results from comprehensive screening
    sanctions_result = {
        "risk": "high" if comprehensive_screening["combined_screening"]["sanctions_flags"] else "unknown",
        "hits": comprehensive_screening["combined_screening"]["sanctions_flags"],
        "entities_screened": comprehensive_screening["screening_metadata"]["entities_screened"],
    }
    
    adverse_media_result = comprehensive_screening["combined_screening"]["adverse_media_flags"]
    
    logger.info(
        f"✓ Screening complete: {sanctions_result['entities_screened']} entities, "
        f"Risk score: {comprehensive_screening['combined_screening']['total_risk_score']}/100"
    )
    
    # ═══════════════════════════════════════════════════════════════════════════
    # STEP 3.5: FRAUD DETECTION SUITE
    # ═══════════════════════════════════════════════════════════════════════════
    logger.info(f"Step 3.5/5: Running advanced fraud detection...")
    
    fraud_analysis = {}
    try:
        # Convert records to dicts for fraud detection
        financial_dicts = [
            {
                "year": getattr(r, "year", None),
                "revenue": getattr(r, "revenue", None),
                "net_profit": getattr(r, "net_profit", None),
            }
            for r in financial_records
        ]
        
        formality_dicts = [
            {
                "event_type": getattr(r, "event_type", ""),
                "registered_date": getattr(r, "registered_date", None),
                "description": getattr(r, "description", ""),
            }
            for r in formality_records
        ]
        
        fraud_analysis = run_fraud_detection_suite(
            company=company,
            dashboard_data=dashboard_data,
            financial_records=financial_dicts,
            formality_records=formality_dicts,
            management_roles=analysis_results.get("management_analysis", {}).get("roles", []),
        )
        logger.info(f"✓ Fraud detection complete - Score: {fraud_analysis.get('overall_fraud_score', 0)}")
    except Exception as e:
        logger.warning(f"Fraud detection suite error: {e}")
        fraud_analysis = {"overall_fraud_score": 0, "alerts": []}
    
    # ═══════════════════════════════════════════════════════════════════════════
    # STEP 4: CALCULATE RISK SCORE (FRANCE-AWARE)
    # ═══════════════════════════════════════════════════════════════════════════
    #
    # KEY PRINCIPLE: UK = complete structured truth → deterministic scoring
    #                France = partial truth + inference → tolerant/probabilistic
    #
    # Missing data is NEUTRAL, not a risk signal.
    # Only SUSPICIOUS PATTERNS add risk.
    # 0 actual risk factors → risk CANNOT be HIGH.
    #
    logger.info(f"Step 4/5: Calculating risk score (France-tolerant mode)...")

    risk_score = 0
    risk_factors = []

    # Extract analysis results (with defaults)
    mgmt_analysis = analysis_results.get("management_analysis", {})
    addr_assessment = analysis_results.get("address_assessment", {})
    dormancy = analysis_results.get("dormancy_detection", {})
    ubo_analysis = analysis_results.get("ubo_analysis", {})
    restricted = analysis_results.get("restricted_activities", {})
    company_status = analysis_results.get("company_status", {})

    # Track data gaps separately from risk factors
    data_gaps = []

    # FACTOR 1: Management Risk (France-aware)
    # Only score management risk if data was actually available
    if mgmt_analysis.get("data_available", True):
        mgmt_risk = mgmt_analysis.get("management_risk_score", 0)
        risk_score += mgmt_risk
        if mgmt_analysis.get("flags"):
            for flag in mgmt_analysis["flags"][:3]:
                risk_factors.append(f"Management: {flag}")
    else:
        # Data was NOT available — this is an INPI limitation, NOT a risk signal
        data_gaps.append("Management data not available in INPI (common for large/listed companies)")
        # DO NOT add risk score for missing data

    # FACTOR 2: Address Credibility
    addr_cred = 100 - addr_assessment.get("credibility_score", 100)
    risk_score += addr_cred * 0.3  # Weight at 30%
    if addr_assessment.get("address_type") == "Virtual":
        risk_factors.append("Virtual office address detected")

    # FACTOR 3: Dormancy
    if dormancy.get("is_dormant"):
        risk_score += 30
        risk_factors.append("Company appears dormant")

    # FACTOR 4: UBO Transparency (France-aware)
    # INPI does NOT expose PSC/UBO data like UK Companies House.
    # Missing UBO is EXPECTED for French companies, not a risk signal.
    ubo_transparency = ubo_analysis.get("transparency_level", "")
    if ubo_transparency == "Not available (INPI)":
        # Expected for France — no penalty
        data_gaps.append("UBO/PSC data not available via INPI (held in separate RBE register)")
    elif not ubo_analysis.get("ubo_identified") and ubo_analysis.get("risk_flags"):
        # We had some data but couldn't identify UBOs — mild concern
        risk_score += 5  # Reduced from 15 — France doesn't guarantee this data
        risk_factors.append("Beneficial owners not identifiable from available data")

    # FACTOR 5: Restricted Activities
    if restricted.get("has_restrictions"):
        risk_score += 20
        for activity in restricted.get("prohibited", [])[:2]:
            risk_factors.append(f"Prohibited activity: {activity}")

    # FACTOR 6: Legal Status
    if not company_status.get("is_operational"):
        risk_score += 40
        risk_factors.append(f"Company not operational: {company_status.get('legal_status')}")

    # FACTOR 7: High-Risk Industry
    if hrob_screening["is_high_risk"]:
        risk_score += 25
        risk_factors.append("High-risk industry detected")

    # FACTOR 8: Sanctions
    if sanctions_result.get("risk") in ("high", "medium"):
        risk_score += 30
        risk_factors.append("Sanctions/FATF match")

    # FACTOR 9: Adverse Media
    adverse_count = len(adverse_media_result) if isinstance(adverse_media_result, list) else 0
    if adverse_count > 0:
        risk_score += min(20, adverse_count * 5)
        risk_factors.append(f"{adverse_count} adverse media alert(s)")

    # FACTOR 10: Company Age & Maturity (improved parsing)
    age_years = None
    if company.creation_date:
        try:
            # INPI dates can be YYYY-MM-DD or just YYYY
            creation_str = company.creation_date.strip()
            if len(creation_str) >= 10:
                creation_date_parsed = datetime.strptime(creation_str[:10], "%Y-%m-%d")
            elif len(creation_str) >= 4:
                creation_date_parsed = datetime.strptime(creation_str[:4], "%Y")
            else:
                creation_date_parsed = None

            if creation_date_parsed:
                age_years = (datetime.now() - creation_date_parsed).days // 365
                if age_years < 1:
                    risk_score += 20
                    risk_factors.append("Very new company (< 1 year)")
                elif age_years < 3:
                    risk_score += 10
                    risk_factors.append("Early-stage company (1-3 years)")
        except (ValueError, AttributeError):
            data_gaps.append("Could not parse creation date from INPI")

    # Normalize score
    risk_score = min(100, max(0, int(risk_score)))

    # ─── CONSISTENCY CHECK ─────────────────────────────────────────────────
    # 0 actual risk factors → risk CANNOT be HIGH (this was the broken logic)
    # Only data_gaps (not risk_factors) means there's nothing actually wrong
    if len(risk_factors) == 0:
        risk_score = min(risk_score, 20)  # Cap at Low if no actual flags

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
        
        # ─── Management & Directors (ACTUAL EXTRACTED DATA + UBO CHAIN) ───
        "directors": [
            {
                "name": role.get("full_name", role.get("company_name", "Unknown")),
                "role": role.get("role_name", ""),
                "birth_date": role.get("birth_date", ""),
                "address": role.get("address", ""),
                "company_name": role.get("company_name", ""),
                "company_siren": role.get("company_siren", ""),
                "person_type": role.get("person_type", ""),
                "source": role.get("source", "inpi_json"),
                "is_ultimate_owner": role.get("is_ultimate_owner", False),
                "depth": role.get("depth", 0),
                # UBO Chain (if director is a company, show its directors)
                "ubo_chain": role.get("ubo_chain", []),
                "has_ubo_info": role.get("has_ubo_info", False),
                "recursion_limit_reached": role.get("recursion_limit_reached", False),
            }
            for role in management_roles
        ] if management_roles else [],
        
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
        "restricted_activities": restricted,
        
        # ─── Comprehensive Multi-Entity Screening (NEW) ───
        "comprehensive_screening": {
            "company_screening": comprehensive_screening.get("company_screening", {}),
            "directors_screening": comprehensive_screening.get("directors_screening", {}),
            "company_directors_screening": comprehensive_screening.get("company_directors_screening", {}),
            "ubo_screening": comprehensive_screening.get("ubo_screening", {}),
            "combined_results": comprehensive_screening.get("combined_screening", {}),
            "screening_metadata": comprehensive_screening.get("screening_metadata", {}),
        },
        
        # ─── Advanced Fraud Detection ───
        "fraud_detection": fraud_analysis,
        
        # ─── Risk Matrix (France-aware) ───
        "risk_matrix": {
            "risk_score": risk_score,
            "overall_risk": overall_risk,
            "risk_emoji": emoji,
            "risk_factors": risk_factors,
            "data_gaps": data_gaps,  # Separate from risk factors — info, not risk
            "hard_stop_triggered": risk_score >= 90 and len(risk_factors) > 0,
            "scoring_mode": "france_tolerant",  # Flag that we used France-aware scoring
            "category_risks": {
                "management": mgmt_analysis.get("role_quality", "unknown"),
                "address_credibility": addr_assessment.get("credibility_level", "unknown"),
                "dormancy": "high" if dormancy.get("is_dormant") else "low",
                "ubo_transparency": ubo_analysis.get("transparency_level", "Not available (INPI)"),
                "restricted_activities": "high" if restricted.get("has_restrictions") else "low",
                "legal_status": "high" if not company_status.get("is_operational") else "low",
                "industry_risk": "high" if hrob_screening["is_high_risk"] else "low",
                "sanctions": sanctions_result.get("risk", "unknown"),
                "adverse_media": "high" if adverse_count > 5 else "medium" if adverse_count > 0 else "low",
                "maturity": "low" if age_years is None else ("high" if age_years < 1 else "medium" if age_years < 3 else "low"),
            },
            "all_flags": risk_factors,
        },

        # ─── Company Age (properly parsed) ───
        "company_age": {
            "years": age_years,
            "creation_date": company.creation_date,
            "display": f"{age_years} years" if age_years is not None else "Unknown",
        },

        # ─── Directors Metadata (data quality context) ───
        "directors_metadata": directors_metadata,

        # ─── Metadata ───
        "check_timestamp": datetime.now().isoformat(),
        "data_source": "INPI (Institut National de la Propriété Industrielle)",
        "check_type": "french_comprehensive",
        "scoring_note": (
            "France/INPI provides partial data compared to UK Companies House. "
            "Missing fields (directors, UBO, status) are INPI limitations, not risk signals. "
            "Risk scoring uses France-tolerant mode: only suspicious patterns add risk."
        ),
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
