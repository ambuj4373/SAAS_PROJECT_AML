"""
core/fca_context.py — FCA Regulatory Context for all analysis engines.

Provides FCA awareness across the entire system:
- Enhanced search queries for FCA-regulated industries
- Additional risk signals and red flags for regulated entities
- Customized scoring for financial compliance risks
- AI/LLM awareness context for narrative generation

This module ensures the entire system knows when analyzing an FCA-regulated
entity and adjusts sensitivity, search terms, and risk thresholds accordingly.
"""

from __future__ import annotations
from typing import Any

from core.logging_config import get_logger

log = get_logger("core.fca_context")


# ═══════════════════════════════════════════════════════════════════════════════
# FCA INDUSTRY CONTEXT
# ═══════════════════════════════════════════════════════════════════════════════

class FCAContext:
    """Regulatory context for FCA-regulated entities."""
    
    # Higher sensitivity for regulated entities
    REGULATED_SEARCH_MULTIPLIER = 2.0  # 2x more search terms
    REGULATED_RISK_MULTIPLIER = 1.5    # 1.5x higher risk signals
    REGULATED_SCORE_SENSITIVITY = 1.25 # 1.25x more sensitive to flags
    
    # FCA-specific risk categories
    FCA_RISK_CATEGORIES = {
        "AML_COMPLIANCE": {
            "weight": 15,  # 15 points if triggered
            "keywords": [
                "aml violation", "anti-money laundering breach", "aml failure",
                "suspicious activity report", "sar filed", "aml investigation",
                "money laundering scheme", "aml breach", "compliance failure",
                "regulatory action aml", "fca sanction aml",
            ],
            "description": "Anti-Money Laundering (AML) Compliance Issues"
        },
        "MARKET_ABUSE": {
            "weight": 12,
            "keywords": [
                "market abuse", "insider trading", "price manipulation",
                "pump and dump", "wash trading", "spoofing", "layering",
                "manipulative practice", "market conduct violation",
            ],
            "description": "Market Abuse / Trading Conduct Violations"
        },
        "FRAUD_FINANCIAL_CRIME": {
            "weight": 20,
            "keywords": [
                "fraud", "financial crime", "embezzlement", "misappropriation",
                "ponzi", "pyramid scheme", "affinity fraud", "investment fraud",
                "wire fraud", "securities fraud", "stolen funds",
            ],
            "description": "Financial Fraud / Crime Allegations"
        },
        "REGULATORY_SANCTION": {
            "weight": 18,
            "keywords": [
                "fca fine", "fca sanction", "fca warning", "fca enforcement",
                "fca investigation", "regulatory action", "suspended", "revoked",
                "license revoked", "license suspended", "enforcement action",
            ],
            "description": "FCA Regulatory Actions / Sanctions"
        },
        "CLIENT_FUND_MISMANAGEMENT": {
            "weight": 16,
            "keywords": [
                "client funds misused", "segregated account breach", "client money",
                "fund mismanagement", "improper handling", "client assets",
                "trustee breach", "fiduciary duty", "client protection",
            ],
            "description": "Client Funds Mismanagement / Breach"
        },
        "COMPLIANCE_OFFICER_ISSUES": {
            "weight": 8,
            "keywords": [
                "compliance officer resign", "compliance breach", "compliance failure",
                "compliance team", "lack of controls", "control failure",
                "internal audit", "governance failure", "risk management failure",
            ],
            "description": "Compliance / Governance Officer Issues"
        },
        "DIRECTOR_DISQUALIFICATION": {
            "weight": 14,
            "keywords": [
                "director disqualified", "disqualification order", "unfitness",
                "breach of duty", "director banned", "shadow director",
                "insolvency", "fraudulent conduct director",
            ],
            "description": "Director Disqualification / Unfitness"
        },
    }
    
    @staticmethod
    def get_fca_aware_search_context(
        company_name: str,
        industry_category: str,
        fca_details: dict | None = None,
    ) -> dict:
        """
        Generate FCA-aware context for search operations.
        
        Returns search query enhancements and context flags.
        """
        
        is_fca_regulated = fca_details and fca_details.get("industry_regulated", False)
        
        context = {
            "is_fca_regulated": is_fca_regulated,
            "industry_category": industry_category,
            "search_intensity": "HIGH" if is_fca_regulated else "NORMAL",
            "additional_search_terms": [],
            "risk_sensitivity": FCAContext.REGULATED_SCORE_SENSITIVITY if is_fca_regulated else 1.0,
        }
        
        if is_fca_regulated:
            # Add extra search terms for FCA-regulated entities
            context["additional_search_terms"] = [
                f"{company_name} AML compliance",
                f"{company_name} FCA compliance",
                f"{company_name} regulatory breach",
                f"{company_name} financial crime",
                f"{company_name} market abuse",
                f"{company_name} client funds",
                f"{company_name} compliance officer",
                f"{company_name} FCA sanction",
                f"{company_name} FCA investigation",
                f"{company_name} director disqualified",
            ]
        
        return context
    
    @staticmethod
    def get_fca_risk_signals(
        adverse_media: list[dict],
        company_name: str,
        fca_details: dict | None = None,
    ) -> dict:
        """
        Extract FCA-specific risk signals from adverse media.
        
        Checks for compliance, AML, fraud, and regulatory issues.
        Returns enhanced risk signals for FCA-regulated entities.
        """
        
        if not fca_details or not fca_details.get("industry_regulated"):
            return {}
        
        signals = {
            "detected_risks": [],
            "risk_score_adjustment": 0,
            "compliance_concerns": [],
            "regulatory_flags": [],
        }
        
        media_text = " ".join([
            item.get("content", "") or item.get("title", "")
            for item in adverse_media
        ]).lower()
        
        # Check each FCA risk category
        for category, details in FCAContext.FCA_RISK_CATEGORIES.items():
            for keyword in details["keywords"]:
                if keyword.lower() in media_text:
                    signals["detected_risks"].append({
                        "category": category,
                        "weight": details["weight"],
                        "description": details["description"],
                        "keyword": keyword,
                    })
                    signals["risk_score_adjustment"] += details["weight"]
                    signals["regulatory_flags"].append(f"⚠️ {details['description']}")
        
        return signals
    
    @staticmethod
    def get_llm_context_for_fca(
        company_name: str,
        industry_category: str,
        fca_details: dict | None = None,
        adverse_risks: dict | None = None,
    ) -> str:
        """
        Generate narrative context for LLM about FCA regulation.
        
        This helps the AI understand the regulatory context and adjust
        tone, emphasis, and recommendations accordingly.
        """
        
        if not fca_details or not fca_details.get("industry_regulated"):
            return ""
        
        context = f"""
[FCA REGULATORY CONTEXT FOR AI/NARRATIVE]

{company_name} is in a FINANCIAL SERVICES / FCA-REGULATED INDUSTRY ({industry_category}).

This entity is subject to:
• UK Financial Conduct Authority (FCA) regulation
• Anti-Money Laundering (AML) compliance requirements
• Market Abuse Regulation (MAR) / trading conduct rules
• Client funds protection requirements
• Capital adequacy and governance standards
• Regulatory reporting and disclosure obligations
• FCA investigations and enforcement authority

HEIGHTENED SCRUTINY AREAS:
• Any history of AML breaches or suspicious activity reporting (SAR) failures
• Market manipulation, insider trading, or trading conduct violations
• Misuse of client funds or segregation account breaches
• FCA sanctions, fines, warnings, or enforcement actions
• Director disqualifications or fitness/propriety concerns
• Compliance officer departures or governance failures
• Regulatory investigation involvement

NARRATIVE ADJUSTMENTS:
• Emphasize regulatory compliance track record
• Highlight any compliance certifications or clean audit records
• Flag any regulatory concerns with heightened severity
• Assess competence and fitness of management team
• Evaluate robustness of AML/compliance controls
• Consider impact of any regulatory actions on business viability

RISK CONSIDERATIONS:
• Regulated entities face reputational damage from compliance failures
• Regulatory actions can rapidly escalate (license suspension/revocation)
• Client fund mismanagement creates systemic risk
• AML failures expose entity to criminal liability
• Market abuse violations indicate intentional misconduct vs. negligence
"""
        
        if adverse_risks:
            context += f"\nDETECTED FCA-RELATED RISKS:\n"
            for risk in adverse_risks.get("regulatory_flags", []):
                context += f"  {risk}\n"
            
            if adverse_risks.get("risk_score_adjustment"):
                context += f"\n⚠️ RISK ADJUSTMENT: +{adverse_risks['risk_score_adjustment']} points from FCA-specific compliance risks\n"
        
        return context


def enhance_adverse_media_search(
    company_name: str,
    industry_category: str,
    fca_details: dict | None = None,
) -> list[str]:
    """Generate FCA-aware adverse media search terms."""
    
    base_terms = [
        company_name,
        f"{company_name} compliance",
        f"{company_name} scandal",
        f"{company_name} fraud",
    ]
    
    if fca_details and fca_details.get("industry_regulated"):
        # Add FCA-specific search terms
        fca_terms = [
            f"{company_name} AML",
            f"{company_name} anti-money laundering",
            f"{company_name} market abuse",
            f"{company_name} FCA investigation",
            f"{company_name} FCA sanction",
            f"{company_name} regulatory breach",
            f"{company_name} client funds",
            f"{company_name} financial crime",
            f"{company_name} insider trading",
            f"{company_name} compliance officer",
        ]
        return base_terms + fca_terms
    
    return base_terms


def enhance_website_intelligence_search(
    company_name: str,
    company_website: str,
    fca_details: dict | None = None,
) -> dict:
    """Generate FCA-aware website intelligence checks."""
    
    checks = {
        "standard": [
            "about us",
            "team",
            "contact",
            "privacy policy",
        ],
        "fca_specific": [] if not (fca_details and fca_details.get("industry_regulated")) else [
            "fca register",
            "fca authorised",
            "fca firm reference",
            "frn",
            "compliance",
            "aml policy",
            "client funds",
            "regulatory",
            "cookie policy",
            "terms and conditions",
            "complaints procedure",
            "fscs protection",
            "regulatory disclosures",
        ],
    }
    
    return checks


def apply_fca_risk_amplification(
    base_risk_score: float,
    fca_details: dict | None = None,
    fca_compliance_risks: dict | None = None,
) -> float:
    """
    Apply FCA-specific risk amplification.
    
    For FCA-regulated entities, certain risk factors carry higher weight.
    """
    
    if not fca_details or not fca_details.get("industry_regulated"):
        return base_risk_score
    
    amplified_score = base_risk_score
    
    # If FCA found on website, reduce as planned
    if fca_details.get("found"):
        amplified_score *= 0.75  # 25% reduction
    
    # But if we found compliance risks, add them back
    if fca_compliance_risks:
        risk_adjustment = fca_compliance_risks.get("risk_score_adjustment", 0)
        amplified_score += risk_adjustment
    
    return amplified_score


# ═══════════════════════════════════════════════════════════════════════════════
# EXPORT
# ═══════════════════════════════════════════════════════════════════════════════

__all__ = [
    "FCAContext",
    "enhance_adverse_media_search",
    "enhance_website_intelligence_search",
    "apply_fca_risk_amplification",
]
