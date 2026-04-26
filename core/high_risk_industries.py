"""
high_risk_industries.py

High-risk industry classifier for UK companies.
Used by the company check to flag companies requiring enhanced due diligence
based on their SIC code classification.

Categories:
  - Property Management
  - Financial Services & Lending
  - Insurance
  - Gambling & Betting
  - Cryptocurrency & Fintech
  - Trust & Offshore Services
  - Money Transmission & Remittance
  - Travel & Hospitality (certain segments)
  - High-Value Goods (Precious Metals, Jewellery, Diamonds)
  - Adult Entertainment & Escort Services
  - Weapons & Narcotics
  - Telemarketing & High-Risk Customer Acquisition
"""

# ═══════════════════════════════════════════════════════════════════════════════
# UK SIC CODES — High Risk Industries
# ═══════════════════════════════════════════════════════════════════════════════

_UK_HIGH_RISK_SIC_CODES: dict[str, dict] = {
    # ── Property Management ──────────────────────────────────────────
    "6811": {
        "industry": "Property Management (residential)",
        "risk_level": "high",
        "reason": "Rent collection, tenant management, significant fund handling",
        "hrob_required": True,
        "keywords": ["property management", "letting agent"],
    },
    "6812": {
        "industry": "Property Management (non-residential)",
        "risk_level": "high",
        "reason": "Commercial property management, service charges",
        "hrob_required": True,
        "keywords": ["property management", "commercial"],
    },
    "6820": {
        "industry": "Property Rental & Leasing",
        "risk_level": "high",
        "reason": "Landlord operations",
        "hrob_required": True,
        "keywords": ["property rental", "leasing"],
    },

    # ── Financial Services ───────────────────────────────────────────
    "6411": {
        "industry": "Central Banking",
        "risk_level": "high",
        "reason": "Banking operations - FCA regulated",
        "hrob_required": True,
        "keywords": ["banking"],
    },
    "6419": {
        "industry": "Other Credit Granting",
        "risk_level": "high",
        "reason": "Lending, loans - potential predatory lending",
        "hrob_required": True,
        "keywords": ["lending", "loans", "credit"],
    },
    "6421": {
        "industry": "Portfolio Investment Activities",
        "risk_level": "high",
        "reason": "Investment management - FCA regulated",
        "hrob_required": True,
        "keywords": ["investments", "portfolio"],
    },
    "6422": {
        "industry": "Funds & Collective Investments",
        "risk_level": "high",
        "reason": "Investment fund management",
        "hrob_required": True,
        "keywords": ["funds", "investments"],
    },
    "6423": {
        "industry": "Stock Exchange Activities",
        "risk_level": "high",
        "reason": "Securities trading - high-risk derivatives",
        "hrob_required": True,
        "keywords": ["trading", "securities", "stocks"],
    },
    "6491": {
        "industry": "Financial Leasing",
        "risk_level": "high",
        "reason": "Hire purchase, asset finance",
        "hrob_required": True,
        "keywords": ["leasing", "finance"],
    },
    "6492": {
        "industry": "Other Financial Activities",
        "risk_level": "high",
        "reason": "Factoring, securitization",
        "hrob_required": True,
        "keywords": ["factoring", "finance"],
    },

    # ── Insurance ────────────────────────────────────────────────────
    "6511": {
        "industry": "Life Insurance",
        "risk_level": "high",
        "reason": "Premium collection - PRA regulated",
        "hrob_required": True,
        "keywords": ["insurance", "life"],
    },
    "6512": {
        "industry": "Non-Life Insurance",
        "risk_level": "high",
        "reason": "Motor, property insurance - PRA regulated",
        "hrob_required": True,
        "keywords": ["insurance"],
    },
    "6521": {
        "industry": "Life Insurance & Pension Funding",
        "risk_level": "high",
        "reason": "Combined products",
        "hrob_required": True,
        "keywords": ["insurance", "pension"],
    },
    "6522": {
        "industry": "Health Insurance",
        "risk_level": "high",
        "reason": "Health coverage - PRA regulated",
        "hrob_required": True,
        "keywords": ["health", "insurance"],
    },
    "6611": {
        "industry": "Insurance Agents & Brokers",
        "risk_level": "high",
        "reason": "Premium intermediation",
        "hrob_required": True,
        "keywords": ["insurance broker", "agent"],
    },

    # ── Pensions ─────────────────────────────────────────────────────
    "6531": {
        "industry": "Pension Fund Management",
        "risk_level": "high",
        "reason": "Retirement fund management - PRA regulated",
        "hrob_required": True,
        "keywords": ["pension", "retirement"],
    },

    # ── Gambling ─────────────────────────────────────────────────────
    "9200": {
        "industry": "Gambling & Betting",
        "risk_level": "high",
        "reason": "Gambling operations - Gambling Commission regulated",
        "hrob_required": True,
        "keywords": ["gambling", "betting", "casino"],
    },
    "9211": {
        "industry": "Casino Operations",
        "risk_level": "high",
        "reason": "Casino operations - high money handling",
        "hrob_required": True,
        "keywords": ["casino", "gambling"],
    },
    "9212": {
        "industry": "Lottery & Betting",
        "risk_level": "high",
        "reason": "Sports betting, lotteries",
        "hrob_required": True,
        "keywords": ["betting", "lottery"],
    },

    # ── Precious Metals & Jewellery ──────────────────────────────────
    "4753": {
        "industry": "Precious Metals & Jewellery Retail",
        "risk_level": "high",
        "reason": "High-value goods - cash-intensive",
        "hrob_required": True,
        "keywords": ["jewellery", "precious", "diamonds"],
    },
    "4754": {
        "industry": "Pawnbroking",
        "risk_level": "high",
        "reason": "Pawnbroker operations - high cash",
        "hrob_required": True,
        "keywords": ["pawnbroker", "pawn"],
    },

    # ── Weapons ──────────────────────────────────────────────────────
    "2511": {
        "industry": "Manufacture of Weapons & Ammunition",
        "risk_level": "high",
        "reason": "Weapons manufacturing - export controls",
        "hrob_required": True,
        "keywords": ["weapons", "ammunition"],
    },

    # ── Travel ───────────────────────────────────────────────────────
    "7911": {
        "industry": "Travel Agency",
        "risk_level": "high",
        "reason": "Travel services - ATOL/ABTA compliance",
        "hrob_required": True,
        "keywords": ["travel", "agency"],
    },
    "7912": {
        "industry": "Tour Operator",
        "risk_level": "high",
        "reason": "Tour operations - advance payments",
        "hrob_required": True,
        "keywords": ["tour", "operator"],
    },
}

# ═══════════════════════════════════════════════════════════════════════════════
# EXPORTED FUNCTIONS
# ═══════════════════════════════════════════════════════════════════════════════

def is_high_risk_industry(
    code: str,
    country: str = "uk"
) -> tuple[bool, str, bool]:
    """
    Check if a company's industry code indicates high-risk sector.

    Returns:
        (is_high_risk: bool, industry_name: str, requires_hrob: bool)
    """
    codes_map = _UK_HIGH_RISK_SIC_CODES

    # Try exact match first
    if code in codes_map:
        data = codes_map[code]
        return (
            data["risk_level"] == "high",
            data["industry"],
            data["hrob_required"]
        )

    # Try prefix match (first 4 chars for SIC)
    code_prefix = code[:4] if len(code) >= 4 else code[:2]
    for key, data in codes_map.items():
        if key.startswith(code_prefix):
            return (
                data["risk_level"] == "high",
                data["industry"],
                data["hrob_required"]
            )

    return (False, "Unknown", False)


def get_industry_details(
    code: str,
    country: str = "uk"
) -> dict | None:
    """
    Get full details for a high-risk industry code.

    Returns dict with keys: industry, risk_level, reason, hrob_required, keywords
    Or None if code not found in high-risk list.
    """
    codes_map = _UK_HIGH_RISK_SIC_CODES

    if code in codes_map:
        return codes_map[code]

    # Try prefix match
    code_prefix = code[:4] if len(code) >= 4 else code[:2]
    for key, data in codes_map.items():
        if key.startswith(code_prefix):
            return data

    return None


def flag_high_risk_industry(
    sic_codes: list[str] | None,
    ape_codes: list[str] | None = None,
    company_name: str = "",
    country: str = "uk"
) -> dict:
    """
    Screen a company's industry codes for high-risk sectors.

    Returns dict with:
      - is_high_risk: bool
      - matched_industries: list of {code, industry, risk_level, reason}
      - requires_hrob: bool (at least one industry requires HROB)
      - summary: str
    """
    codes_to_check = sic_codes or []

    if not codes_to_check:
        return {
            "is_high_risk": False,
            "matched_industries": [],
            "requires_hrob": False,
            "summary": "No industry codes provided",
        }

    matched = []
    requires_hrob = False

    for code in codes_to_check:
        is_high, industry_name, needs_hrob = is_high_risk_industry(code, country)
        if is_high:
            details = get_industry_details(code, country)
            matched.append({
                "code": code,
                "industry": industry_name,
                "risk_level": "high",
                "reason": details.get("reason", "") if details else "",
            })
            if needs_hrob:
                requires_hrob = True

    if not matched:
        summary = f"Company operates in standard/low-risk industries (no high-risk codes detected)"
    elif len(matched) == 1:
        summary = f"⚠️ HROB Review Required — high-risk industry detected: {matched[0]['industry']}"
    else:
        industries = ", ".join(m["industry"] for m in matched)
        summary = f"🔴 Multiple high-risk industries detected: {industries}"

    return {
        "is_high_risk": len(matched) > 0,
        "matched_industries": matched,
        "requires_hrob": requires_hrob,
        "summary": summary,
    }
