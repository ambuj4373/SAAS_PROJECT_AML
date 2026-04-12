"""
high_risk_industries.py

Comprehensive high-risk industry classifier for both UK and French companies.
Used by the company check to flag companies requiring enhanced due diligence
based on their industry classification (APE codes for France, SIC codes for UK).

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
# FRENCH APE CODES — High Risk Industries
# ═══════════════════════════════════════════════════════════════════════════════
# APE (Activité Principale Exercée) are 5-digit codes used by INSEE
# Format: NNNNS where N=digit, S=letter

_FRENCH_HIGH_RISK_APE_CODES: dict[str, dict] = {
    # ── Property Management ──────────────────────────────────────────
    "6811Z": {
        "industry": "Property Management (residential)",
        "risk_level": "high",
        "reason": "Rent collection, tenant management, significant fund handling",
        "hrob_required": True,
        "keywords": ["property management", "letting agent", "property rental"],
    },
    "6812Z": {
        "industry": "Property Management (non-residential)",
        "risk_level": "high",
        "reason": "Commercial property management, service charges, fund handling",
        "hrob_required": True,
        "keywords": ["commercial property", "property management"],
    },
    "6820Z": {
        "industry": "Property Rental & Leasing",
        "risk_level": "high",
        "reason": "Landlord operations, lease management",
        "hrob_required": True,
        "keywords": ["property rental", "leasing"],
    },
    
    # ── Financial Services & Lending ──────────────────────────────────
    "6411Z": {
        "industry": "Central Banking",
        "risk_level": "high",
        "reason": "Central bank operations - regulated by Banque de France",
        "hrob_required": True,
        "keywords": ["banking", "central bank"],
    },
    "6419Z": {
        "industry": "Other Credit Granting",
        "risk_level": "high",
        "reason": "Lending, loans, credit facilities - potential predatory lending",
        "hrob_required": True,
        "keywords": ["lending", "loans", "credit", "financing"],
    },
    "6421Z": {
        "industry": "Portfolio Investment Activities",
        "risk_level": "high",
        "reason": "Investment management, asset management - FCA-equivalent regulation",
        "hrob_required": True,
        "keywords": ["investments", "portfolio", "assets"],
    },
    "6422Z": {
        "industry": "Funds & Collective Investments",
        "risk_level": "high",
        "reason": "Investment fund management - structured products",
        "hrob_required": True,
        "keywords": ["funds", "investments", "collective"],
    },
    "6423Z": {
        "industry": "Stock Exchange & Money Market Activities",
        "risk_level": "high",
        "reason": "Securities trading, derivatives, high-frequency trading",
        "hrob_required": True,
        "keywords": ["trading", "securities", "stocks"],
    },
    "6491Z": {
        "industry": "Financial Leasing",
        "risk_level": "high",
        "reason": "Hire purchase, asset finance - deferred payment risk",
        "hrob_required": True,
        "keywords": ["leasing", "finance", "hire purchase"],
    },
    "6492Z": {
        "industry": "Other Financial Activities (excluding insurance/pension)",
        "risk_level": "high",
        "reason": "Factoring, securitization, alternative finance",
        "hrob_required": True,
        "keywords": ["factoring", "finance"],
    },
    
    # ── Insurance ────────────────────────────────────────────────────
    "6511Z": {
        "industry": "Life Insurance",
        "risk_level": "high",
        "reason": "Premium collection, investment management - regulated",
        "hrob_required": True,
        "keywords": ["insurance", "premiums", "life insurance"],
    },
    "6512Z": {
        "industry": "Non-Life Insurance",
        "risk_level": "high",
        "reason": "Motor, property, liability insurance - premium collection",
        "hrob_required": True,
        "keywords": ["insurance", "premiums"],
    },
    "6521Z": {
        "industry": "Life Insurance & Pension Funding",
        "risk_level": "high",
        "reason": "Combined life and pension products",
        "hrob_required": True,
        "keywords": ["insurance", "pension", "annuity"],
    },
    "6522Z": {
        "industry": "Health Insurance",
        "risk_level": "high",
        "reason": "Health insurance, medical cost management",
        "hrob_required": True,
        "keywords": ["health", "insurance", "medical"],
    },
    "6611Z": {
        "industry": "Insurance Agents & Brokers",
        "risk_level": "high",
        "reason": "Premium intermediation, client fund handling",
        "hrob_required": True,
        "keywords": ["insurance broker", "agent"],
    },
    
    # ── Pensions ─────────────────────────────────────────────────────
    "6531Z": {
        "industry": "Pension Fund Management",
        "risk_level": "high",
        "reason": "Retirement fund management - regulated by ACPR",
        "hrob_required": True,
        "keywords": ["pension", "retirement", "fund"],
    },
    
    # ── Payment & Money Services ─────────────────────────────────────
    "6612Z": {
        "industry": "Risk & Damage Assessment",
        "risk_level": "medium",
        "reason": "Insurance assessment - related to insurance services",
        "hrob_required": False,
        "keywords": ["insurance", "assessment"],
    },
    
    # ── Gambling & Betting ───────────────────────────────────────────
    "9200Z": {
        "industry": "Gambling & Betting (except lotteries)",
        "risk_level": "high",
        "reason": "Gambling operations - regulated under French gaming laws",
        "hrob_required": True,
        "keywords": ["gambling", "betting", "casino", "slots"],
    },
    "9211Z": {
        "industry": "Casino Operations",
        "risk_level": "high",
        "reason": "Casino operations - high money handling, AML risks",
        "hrob_required": True,
        "keywords": ["casino", "gambling"],
    },
    "9212Z": {
        "industry": "Sports Betting & Lottery Operations",
        "risk_level": "high",
        "reason": "Betting on sports, lottery administration",
        "hrob_required": True,
        "keywords": ["betting", "lottery", "sports"],
    },
    
    # ── Trust & Offshore Services ────────────────────────────────────
    "6930Z": {
        "industry": "Trust & Fund Management (Other)",
        "risk_level": "high",
        "reason": "Trust administration, fund management - transparency risk",
        "hrob_required": True,
        "keywords": ["trust", "fund", "management"],
    },
    
    # ── Money Transmission ───────────────────────────────────────────
    "6621Z": {
        "industry": "Risk Management Activities",
        "risk_level": "medium",
        "reason": "Financial risk management services",
        "hrob_required": False,
        "keywords": ["risk", "management"],
    },
    
    # ── Precious Metals & Jewellery ──────────────────────────────────
    "4753Z": {
        "industry": "Precious Metals & Jewellery Retail",
        "risk_level": "high",
        "reason": "High-value goods, cash-intensive, AML/CFT risks",
        "hrob_required": True,
        "keywords": ["jewellery", "precious", "metals", "diamonds"],
    },
    "4754Z": {
        "industry": "Pawnbroking",
        "risk_level": "high",
        "reason": "Pawnbroker operations - high cash, collateral valuation risk",
        "hrob_required": True,
        "keywords": ["pawnbroker", "pawn", "collateral"],
    },
    
    # ── Adult Entertainment ──────────────────────────────────────────
    "9002Z": {
        "industry": "Operation of Arts Facilities",
        "risk_level": "medium",
        "reason": "Arts venue operation - potential adult entertainment",
        "hrob_required": False,
        "keywords": ["arts", "entertainment", "venue"],
    },
    "9060Z": {
        "industry": "Other Entertainment Activities",
        "risk_level": "medium",
        "reason": "Entertainment operations - could include adult services",
        "hrob_required": False,
        "keywords": ["entertainment", "activities"],
    },
    
    # ── Weapons & Defence ────────────────────────────────────────────
    "2511Z": {
        "industry": "Manufacture of Weapons & Ammunition",
        "risk_level": "high",
        "reason": "Weapons manufacturing - export controls, OFSI compliance",
        "hrob_required": True,
        "keywords": ["weapons", "ammunition", "firearms"],
    },
    "4642Z": {
        "industry": "Wholesale of Metals & Metal Ores",
        "risk_level": "medium",
        "reason": "Metal trading - potential dual-use materials",
        "hrob_required": False,
        "keywords": ["metals", "ores", "wholesale"],
    },
    
    # ── Telemarketing ────────────────────────────────────────────────
    "8211Z": {
        "industry": "Combined Office Administration",
        "risk_level": "medium",
        "reason": "Office administration - potential telemarketing operations",
        "hrob_required": False,
        "keywords": ["administration", "office"],
    },
    
    # ── Travel & Hospitality ─────────────────────────────────────────
    "7911Z": {
        "industry": "Travel Agency",
        "risk_level": "high",
        "reason": "Travel services - potential ATOL/ABTA compliance, customer fund handling",
        "hrob_required": True,
        "keywords": ["travel", "agency", "booking"],
    },
    "7912Z": {
        "industry": "Tour Operator",
        "risk_level": "high",
        "reason": "Tour operations - package holidays, advance payments",
        "hrob_required": True,
        "keywords": ["tour", "operator", "holiday"],
    },
    "7990Z": {
        "industry": "Other Travel Services",
        "risk_level": "medium",
        "reason": "Travel-related services - customer fund risk",
        "hrob_required": False,
        "keywords": ["travel", "services"],
    },
    
    # ── Import/Export with High-Risk Goods ───────────────────────────
    "4671Z": {
        "industry": "Wholesale of Waste & Scrap Materials",
        "risk_level": "medium",
        "reason": "Waste trading - potential environmental/compliance issues",
        "hrob_required": False,
        "keywords": ["waste", "scrap", "materials"],
    },
}

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
    if country.lower() == "france":
        codes_map = _FRENCH_HIGH_RISK_APE_CODES
    else:
        codes_map = _UK_HIGH_RISK_SIC_CODES
    
    # Try exact match first
    if code in codes_map:
        data = codes_map[code]
        return (
            data["risk_level"] == "high",
            data["industry"],
            data["hrob_required"]
        )
    
    # Try prefix match (first 4 chars for SIC/APE)
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
    if country.lower() == "france":
        codes_map = _FRENCH_HIGH_RISK_APE_CODES
    else:
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
    ape_codes: list[str] | None,
    company_name: str,
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
    codes_to_check = []
    
    if country.lower() == "france" and ape_codes:
        codes_to_check = ape_codes
    elif country.lower() != "france" and sic_codes:
        codes_to_check = sic_codes
    else:
        # No codes provided for the given country
        return {
            "is_high_risk": False,
            "matched_industries": [],
            "requires_hrob": False,
            "summary": f"No industry codes provided for {country}",
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
