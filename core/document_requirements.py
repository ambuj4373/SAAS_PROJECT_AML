"""
core/document_requirements.py — SIC-code aware compliance document checklist.

The unique product value vs "another Companies House mirror" is that
**we tell the buyer what to ask the entity for**. A retail customer doing
KYB on a payment processor needs FCA Part 4A permission and an MLRO
appointment letter. A retail customer doing KYB on a corner-shop wholesaler
needs none of that.

This module classifies a company by its SIC codes and returns:
    - the regulated regime that applies (or "general business")
    - the documents the buyer should request
    - the registers / authorities to cross-check
    - the compliance flags to investigate

Public API
----------
- classify_industry(sic_codes: list[str]) -> IndustryProfile
- generate_document_checklist(profile: IndustryProfile) -> list[Requirement]
- requirements_for(sic_codes, status, country, ...) -> ComplianceGuidance
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable


# ─── Data shapes ────────────────────────────────────────────────────────────


@dataclass
class Requirement:
    """One document or check the buyer should perform."""

    title: str  # short label
    detail: str  # one-sentence why
    severity: str  # "mandatory" | "recommended" | "informational"
    source_authority: str = ""  # the regulator / register that issues this
    verification_method: str = ""  # how to verify (e.g. "Cross-check via FCA register")

    def to_dict(self) -> dict:
        return {
            "title": self.title,
            "detail": self.detail,
            "severity": self.severity,
            "source_authority": self.source_authority,
            "verification_method": self.verification_method,
        }


@dataclass
class IndustryProfile:
    """How we classified the entity."""

    primary_regime: str  # "financial_services" | "legal_accounting" | "general_business" | etc.
    regime_label: str  # human-readable
    matched_sic: list[str] = field(default_factory=list)
    confidence: str = "medium"  # "high" if a strong SIC, "low" if defaulted


@dataclass
class ComplianceGuidance:
    """Final output passed into the prompt."""

    industry: IndustryProfile
    requirements: list[Requirement] = field(default_factory=list)
    cross_checks: list[Requirement] = field(default_factory=list)
    red_flags_to_test: list[str] = field(default_factory=list)
    summary_line: str = ""

    def to_dict(self) -> dict:
        return {
            "industry": {
                "regime": self.industry.primary_regime,
                "regime_label": self.industry.regime_label,
                "matched_sic": self.industry.matched_sic,
                "confidence": self.industry.confidence,
            },
            "requirements": [r.to_dict() for r in self.requirements],
            "cross_checks": [r.to_dict() for r in self.cross_checks],
            "red_flags_to_test": self.red_flags_to_test,
            "summary_line": self.summary_line,
        }


# ─── SIC → regime mapping ────────────────────────────────────────────────────
#
# Format: (sic_prefix, regime_key)
# Order matters — most specific prefixes first.

_SIC_PREFIX_TABLE: list[tuple[str, str]] = [
    # ── Financial services (heavily regulated) ───────────────────────────
    ("64191", "banking"),
    ("64110", "central_bank"),  # extremely rare
    ("641", "banking"),
    ("642", "financial_services"),
    ("649", "financial_services"),  # other financial activities incl crypto
    ("651", "insurance"),
    ("652", "insurance"),  # reinsurance
    ("653", "insurance"),
    ("66110", "financial_market_admin"),
    ("661", "financial_services"),
    ("662", "financial_services"),  # incl insurance auxiliaries
    ("663", "fund_management"),

    # ── Legal & accounting (AML supervised) ──────────────────────────────
    ("69101", "legal_services"),
    ("69102", "legal_services"),
    ("69109", "legal_services"),
    ("691", "legal_services"),
    ("69201", "accounting"),
    ("69202", "accounting"),
    ("69203", "accounting"),
    ("692", "accounting"),

    # ── Estate agents (HMRC AML supervised) ──────────────────────────────
    ("68310", "estate_agency"),
    ("68320", "property_management"),

    # ── Healthcare (CQC) ─────────────────────────────────────────────────
    ("86", "healthcare"),
    ("87", "healthcare"),  # residential care
    ("88", "healthcare"),  # social work without accommodation

    # ── Education ────────────────────────────────────────────────────────
    ("85", "education"),

    # ── Construction (Modern Slavery if large) ───────────────────────────
    ("41", "construction"),
    ("42", "construction"),
    ("43", "construction"),

    # ── Real estate dev ──────────────────────────────────────────────────
    ("681", "real_estate"),

    # ── Wholesale & retail ───────────────────────────────────────────────
    ("46", "wholesale"),
    ("47", "retail"),

    # ── Hospitality ──────────────────────────────────────────────────────
    ("55", "hospitality"),
    ("56", "hospitality"),

    # ── Transport / logistics ────────────────────────────────────────────
    ("49", "transport"),
    ("50", "transport"),
    ("51", "transport_air"),
    ("52", "transport"),

    # ── IT / professional services ───────────────────────────────────────
    ("62", "technology"),
    ("63", "technology"),  # incl data processing
    ("70", "professional_services"),
    ("71", "professional_services"),
    ("72", "professional_services"),  # research
    ("73", "professional_services"),  # advertising
    ("74", "professional_services"),

    # ── Manufacturing (broad) ────────────────────────────────────────────
    ("10", "manufacturing"),  # food
    ("11", "manufacturing_alcohol"),  # alcohol — HMRC AWRS
    ("12", "manufacturing_tobacco"),  # tobacco
    ("13", "manufacturing"),
    ("14", "manufacturing"),
    ("15", "manufacturing"),
    ("16", "manufacturing"),
    ("17", "manufacturing"),
    ("18", "manufacturing"),
    ("19", "manufacturing_petroleum"),
    ("20", "manufacturing_chemicals"),  # chemicals — REACH
    ("21", "manufacturing_pharma"),  # MHRA
    ("22", "manufacturing"),
    ("23", "manufacturing"),
    ("24", "manufacturing"),
    ("25", "manufacturing"),
    ("26", "manufacturing"),
    ("27", "manufacturing"),
    ("28", "manufacturing"),
    ("29", "manufacturing"),
    ("30", "manufacturing"),
    ("31", "manufacturing"),
    ("32", "manufacturing"),
    ("33", "manufacturing"),

    # ── Energy ───────────────────────────────────────────────────────────
    ("35", "energy"),  # Ofgem
    ("36", "water"),
    ("37", "water"),
    ("38", "waste"),  # Environment Agency
    ("39", "waste"),

    # ── Mining ───────────────────────────────────────────────────────────
    ("05", "mining"),
    ("06", "mining"),
    ("07", "mining"),
    ("08", "mining"),
    ("09", "mining"),

    # ── Telecoms / media (Ofcom) ─────────────────────────────────────────
    ("58", "media"),
    ("59", "media"),
    ("60", "media_broadcast"),
    ("61", "telecoms"),

    # ── Gambling (Gambling Commission) ───────────────────────────────────
    ("92", "gambling"),

    # ── Charity-adjacent ─────────────────────────────────────────────────
    ("94", "membership_org"),
]


_REGIME_LABELS = {
    "banking": "Banking",
    "central_bank": "Central banking",
    "financial_services": "Financial services",
    "insurance": "Insurance",
    "fund_management": "Fund management",
    "financial_market_admin": "Financial market administration",
    "legal_services": "Legal services",
    "accounting": "Accounting & bookkeeping",
    "estate_agency": "Estate agency / lettings",
    "property_management": "Property management",
    "healthcare": "Healthcare & social care",
    "education": "Education",
    "construction": "Construction",
    "real_estate": "Real estate development",
    "wholesale": "Wholesale trade",
    "retail": "Retail",
    "hospitality": "Hospitality",
    "transport": "Transport & logistics",
    "transport_air": "Air transport",
    "technology": "Technology & IT services",
    "professional_services": "Professional services",
    "manufacturing": "Manufacturing",
    "manufacturing_alcohol": "Alcohol manufacturing",
    "manufacturing_tobacco": "Tobacco manufacturing",
    "manufacturing_petroleum": "Petroleum",
    "manufacturing_chemicals": "Chemicals",
    "manufacturing_pharma": "Pharmaceuticals",
    "energy": "Energy generation / supply",
    "water": "Water utilities",
    "waste": "Waste management",
    "mining": "Mining & extraction",
    "media": "Media & publishing",
    "media_broadcast": "Broadcasting",
    "telecoms": "Telecommunications",
    "gambling": "Gambling & betting",
    "membership_org": "Membership organisation",
    "general_business": "General business",
}


# ─── Requirement library ─────────────────────────────────────────────────────
#
# Each regime returns a list of Requirement objects. We also have a
# baseline set every UK Ltd should pass.

_BASELINE_REQUIREMENTS: list[Requirement] = [
    Requirement(
        title="Certificate of incorporation",
        detail="Issued by Companies House at registration; confirms legal existence and form.",
        severity="mandatory",
        source_authority="Companies House",
        verification_method="Cross-check the company number on Companies House.",
    ),
    Requirement(
        title="Memorandum & articles of association",
        detail="The company's constitutional documents — defines purpose and share structure.",
        severity="mandatory",
        source_authority="Companies House (filed)",
        verification_method="Check filed copy on Companies House.",
    ),
    Requirement(
        title="Confirmation statement (latest)",
        detail="Annual return confirming directors, shareholders, PSCs and registered office.",
        severity="mandatory",
        source_authority="Companies House",
        verification_method="Should be filed annually; check filing history for currency.",
    ),
    Requirement(
        title="Most recent statutory accounts",
        detail="Reveals trading status, balance sheet, and financial health.",
        severity="mandatory",
        source_authority="Companies House",
        verification_method="Pull latest filed accounts; check for late filing.",
    ),
    Requirement(
        title="UBO declaration (>25% beneficial ownership)",
        detail="Required under MLR 2017. Self-declared by the entity, cross-checked against PSC register.",
        severity="mandatory",
        source_authority="Entity (self-declared)",
        verification_method="Cross-check named UBOs against the PSC register.",
    ),
    Requirement(
        title="Proof of registered office address",
        detail="Recent utility bill or lease at registered office. Helps detect mass-registration / virtual addresses.",
        severity="recommended",
        source_authority="Entity",
        verification_method="Check address against the company's website and against UK virtual-office registers.",
    ),
    Requirement(
        title="Director ID verification (each named director)",
        detail="Photo ID + proof of address per director under MLR 2017 enhanced due diligence.",
        severity="mandatory",
        source_authority="Each director",
        verification_method="Cross-check name + DOB against Companies House records.",
    ),
]


_REGIME_REQUIREMENTS: dict[str, list[Requirement]] = {

    "banking": [
        Requirement(
            title="FCA Part 4A permission certificate",
            detail="Mandatory for any deposit-taking activity. Without this, the entity cannot lawfully take deposits.",
            severity="mandatory",
            source_authority="Financial Conduct Authority",
            verification_method="Search the FCA Register at register.fca.org.uk by FRN or name.",
        ),
        Requirement(
            title="PRA authorisation",
            detail="Banks are dual-regulated: PRA for prudential, FCA for conduct.",
            severity="mandatory",
            source_authority="Prudential Regulation Authority",
            verification_method="Cross-check the PRA register.",
        ),
        Requirement(
            title="MLRO appointment letter",
            detail="A board-appointed Money Laundering Reporting Officer is mandatory under MLR 2017.",
            severity="mandatory",
            source_authority="Entity (board minutes)",
            verification_method="Confirm name; verify they are not also disqualified under another regime.",
        ),
        Requirement(
            title="AML / CTF policy + risk assessment",
            detail="Board-approved AML/CTF policy and a written firm-wide risk assessment.",
            severity="mandatory",
            source_authority="Entity",
            verification_method="Request copy; check date of last review (should be <12 months).",
        ),
        Requirement(
            title="Sanctions screening programme",
            detail="Documented sanctions screening procedure covering OFSI, OFAC, UN, EU lists.",
            severity="mandatory",
            source_authority="Entity",
            verification_method="Request policy and a sample of recent screening logs.",
        ),
    ],

    "financial_services": [
        Requirement(
            title="FCA registration / authorisation",
            detail="Most financial activity requires FCA registration or authorisation under FSMA 2000.",
            severity="mandatory",
            source_authority="Financial Conduct Authority",
            verification_method="Search the FCA Register at register.fca.org.uk; confirm permissions cover the stated activity.",
        ),
        Requirement(
            title="MLRO appointment + AML policy",
            detail="Mandatory under MLR 2017 for all financial firms in scope.",
            severity="mandatory",
            source_authority="Entity",
            verification_method="Request appointment letter and current policy; review approval date.",
        ),
        Requirement(
            title="Sanctions screening procedure",
            detail="OFSI, OFAC, UN list screening; documented and applied to customers and transactions.",
            severity="mandatory",
            source_authority="Entity",
            verification_method="Request policy + sample screening logs.",
        ),
        Requirement(
            title="UBO declaration + PSC reconciliation",
            detail="Beneficial owners over 25% must be declared under MLR 2017 and match PSC register.",
            severity="mandatory",
            source_authority="Entity + Companies House",
            verification_method="Cross-check named UBOs against PSC register; flag any divergence.",
        ),
        Requirement(
            title="Cryptoasset registration (if applicable)",
            detail="If the entity offers crypto services, additional FCA registration under MLRs is required since 2020.",
            severity="recommended",
            source_authority="Financial Conduct Authority",
            verification_method="Check FCA Cryptoasset Register; request copy of registration if claimed.",
        ),
    ],

    "insurance": [
        Requirement(
            title="FCA / PRA authorisation",
            detail="Insurance is dual-regulated. Both authorisations needed.",
            severity="mandatory",
            source_authority="FCA + PRA",
            verification_method="Cross-check FCA Register and PRA register.",
        ),
        Requirement(
            title="Solvency II compliance evidence",
            detail="Annual SFCR (Solvency and Financial Condition Report).",
            severity="mandatory",
            source_authority="Entity",
            verification_method="Request latest SFCR; check publication date.",
        ),
        Requirement(
            title="MLRO + AML policy",
            detail="Required for life insurance and capital redemption.",
            severity="recommended",
            source_authority="Entity",
            verification_method="Request copy if engaging in life or investment products.",
        ),
    ],

    "legal_services": [
        Requirement(
            title="SRA / Bar / CILEx supervision",
            detail="Legal practices must be supervised by a recognised AML supervisory body.",
            severity="mandatory",
            source_authority="SRA / Bar Standards Board / CILEx",
            verification_method="Search the SRA register at sra.org.uk for solicitors; the BSB for barristers.",
        ),
        Requirement(
            title="MLRO appointment + AML policy",
            detail="Required under MLR 2017 for any firm conducting in-scope legal work (property, trust, company services).",
            severity="mandatory",
            source_authority="Entity",
            verification_method="Request appointment letter + current policy.",
        ),
        Requirement(
            title="Professional indemnity insurance certificate",
            detail="Mandatory for SRA-regulated firms.",
            severity="mandatory",
            source_authority="Insurer",
            verification_method="Request policy schedule and check expiry date + minimum cover.",
        ),
    ],

    "accounting": [
        Requirement(
            title="AML supervisory body registration",
            detail="ICAEW, ACCA, AAT, CIMA or HMRC. Operating without supervision is a criminal offence under MLR 2017.",
            severity="mandatory",
            source_authority="ICAEW / ACCA / AAT / HMRC",
            verification_method="Search the relevant supervisor's register; if HMRC-supervised, check the AML registration via HMRC.",
        ),
        Requirement(
            title="MLRO appointment letter",
            detail="Practices must appoint an MLRO unless sole practitioner.",
            severity="mandatory",
            source_authority="Entity",
            verification_method="Request appointment letter.",
        ),
        Requirement(
            title="Firm-wide risk assessment + AML policy",
            detail="Board / partner approved; reviewed annually.",
            severity="mandatory",
            source_authority="Entity",
            verification_method="Request copy; check approval and review dates.",
        ),
        Requirement(
            title="Professional indemnity insurance",
            detail="Required by ICAEW, ACCA and other supervisors.",
            severity="mandatory",
            source_authority="Insurer",
            verification_method="Request policy schedule; check minimum cover ≥ £100k or supervisor's threshold.",
        ),
    ],

    "estate_agency": [
        Requirement(
            title="HMRC AML supervision registration",
            detail="Estate agents are HMRC-supervised under MLR 2017.",
            severity="mandatory",
            source_authority="HMRC",
            verification_method="Verify HMRC AML registration number.",
        ),
        Requirement(
            title="Property redress scheme membership",
            detail="Mandatory under the Estate Agents Act and Consumer Rights Act.",
            severity="mandatory",
            source_authority="The Property Ombudsman / Property Redress Scheme",
            verification_method="Search the relevant redress scheme directory.",
        ),
        Requirement(
            title="MLRO + AML policy",
            detail="Mandatory under MLR 2017.",
            severity="mandatory",
            source_authority="Entity",
            verification_method="Request appointment letter + policy.",
        ),
        Requirement(
            title="Client money protection certificate",
            detail="Letting agents holding client money must hold CMP scheme membership.",
            severity="mandatory",
            source_authority="CMP scheme provider",
            verification_method="Request certificate; verify with scheme.",
        ),
    ],

    "healthcare": [
        Requirement(
            title="Care Quality Commission registration",
            detail="Mandatory for any regulated activity (care home, clinic, hospital, dental).",
            severity="mandatory",
            source_authority="Care Quality Commission",
            verification_method="Search the CQC register at cqc.org.uk.",
        ),
        Requirement(
            title="Professional body registration (clinical staff)",
            detail="GMC for doctors, NMC for nurses, GDC for dentists, HCPC for AHPs.",
            severity="mandatory",
            source_authority="GMC / NMC / GDC / HCPC",
            verification_method="Cross-check named practitioners against the relevant register.",
        ),
        Requirement(
            title="DBS / safeguarding policies",
            detail="Enhanced DBS for staff; safeguarding policy for vulnerable groups.",
            severity="mandatory",
            source_authority="Entity",
            verification_method="Request safeguarding policy + DBS register evidence.",
        ),
    ],

    "construction": [
        Requirement(
            title="CIS registration",
            detail="HMRC Construction Industry Scheme registration if subcontracting.",
            severity="recommended",
            source_authority="HMRC",
            verification_method="Confirm UTR + CIS registration with the entity.",
        ),
        Requirement(
            title="Modern Slavery Act statement",
            detail="Required if global turnover ≥ £36m. Must be on the entity's website.",
            severity="recommended",
            source_authority="Entity website",
            verification_method="Check the website for a Modern Slavery Act statement.",
        ),
        Requirement(
            title="Health & safety policy + accreditations",
            detail="CHAS / SafeContractor / Constructionline are common pre-qualification accreditations.",
            severity="recommended",
            source_authority="Entity",
            verification_method="Request accreditation certificates; cross-check with the issuing body.",
        ),
    ],

    "gambling": [
        Requirement(
            title="Gambling Commission operating licence",
            detail="Mandatory for any gambling activity in Britain.",
            severity="mandatory",
            source_authority="Gambling Commission",
            verification_method="Search the Public Register at gamblingcommission.gov.uk.",
        ),
        Requirement(
            title="MLRO + AML policy",
            detail="Casinos are HMRC AML-supervised; remote gambling is subject to LCCP AML provisions.",
            severity="mandatory",
            source_authority="Entity",
            verification_method="Request policy + appointment letter.",
        ),
        Requirement(
            title="Sanctions screening procedure",
            detail="Required under LCCP and MLR 2017.",
            severity="mandatory",
            source_authority="Entity",
            verification_method="Request policy + sample logs.",
        ),
    ],

    "energy": [
        Requirement(
            title="Ofgem licence",
            detail="Generation, supply, distribution, and transmission all require Ofgem licensing.",
            severity="mandatory",
            source_authority="Ofgem",
            verification_method="Search the Ofgem licence database.",
        ),
    ],

    "telecoms": [
        Requirement(
            title="Ofcom General Conditions compliance",
            detail="Communications providers must register with Ofcom and comply with General Conditions.",
            severity="mandatory",
            source_authority="Ofcom",
            verification_method="Confirm General Conditions registration; review last entry on Ofcom site.",
        ),
    ],

    "manufacturing_alcohol": [
        Requirement(
            title="HMRC Alcohol Wholesaler Registration Scheme (AWRS)",
            detail="Mandatory for any business wholesaling alcohol.",
            severity="mandatory",
            source_authority="HMRC",
            verification_method="Verify AWRS URN with HMRC.",
        ),
        Requirement(
            title="Premises licence",
            detail="Premises licence under the Licensing Act 2003 from the local authority.",
            severity="mandatory",
            source_authority="Local authority",
            verification_method="Cross-check the premises with the council's licensing register.",
        ),
    ],

    "manufacturing_pharma": [
        Requirement(
            title="MHRA manufacturing authorisation",
            detail="Mandatory for any manufacture of medicinal products.",
            severity="mandatory",
            source_authority="MHRA",
            verification_method="Cross-check MIA on the MHRA Manufacturer's Licence database.",
        ),
        Requirement(
            title="GMP certificate",
            detail="Good Manufacturing Practice certification.",
            severity="mandatory",
            source_authority="MHRA",
            verification_method="Verify GMP status with MHRA.",
        ),
    ],

    "transport": [
        Requirement(
            title="Operator's licence",
            detail="Goods/PSV operators need an O-licence from the Traffic Commissioner.",
            severity="mandatory",
            source_authority="Office of the Traffic Commissioner",
            verification_method="Search the OTC public register.",
        ),
    ],

    "retail": [
        Requirement(
            title="VAT registration (if turnover ≥ £85k)",
            detail="Mandatory above VAT threshold.",
            severity="recommended",
            source_authority="HMRC",
            verification_method="Check VAT number via HMRC's VIES service.",
        ),
        Requirement(
            title="GDPR / ICO registration",
            detail="If processing personal data, ICO data protection fee likely required.",
            severity="recommended",
            source_authority="Information Commissioner's Office",
            verification_method="Search the ICO public register.",
        ),
    ],

    "wholesale": [
        Requirement(
            title="VAT registration",
            detail="Almost certain at this scale.",
            severity="recommended",
            source_authority="HMRC",
            verification_method="Check VAT number.",
        ),
    ],

    "technology": [
        Requirement(
            title="ICO data protection registration",
            detail="Mandatory for any UK business processing personal data unless exempt.",
            severity="mandatory",
            source_authority="Information Commissioner's Office",
            verification_method="Search the ICO public register at ico.org.uk.",
        ),
        Requirement(
            title="GDPR / privacy policy on website",
            detail="Required for any website that collects personal data.",
            severity="mandatory",
            source_authority="Entity website",
            verification_method="Check the entity's website for a current privacy policy.",
        ),
    ],

    "real_estate": [
        Requirement(
            title="HMRC AML supervision (high-value dealers)",
            detail="Dealers in high-value property may fall under HMRC AML supervision.",
            severity="recommended",
            source_authority="HMRC",
            verification_method="Confirm HMRC AML registration if turnover qualifies.",
        ),
    ],
}


_REGIME_RED_FLAGS: dict[str, list[str]] = {

    "banking": [
        "Operating banking activity without an FCA Part 4A permission.",
        "MLRO not appointed or vacant for >30 days.",
        "Use of nominee directors with no independent verification.",
        "UBO chain that resolves to a high-risk jurisdiction with no commercial rationale.",
    ],
    "financial_services": [
        "FCA registration claimed but not verified on the public register.",
        "MLRO appointment letter not produced on request.",
        "Claimed crypto activity without an FCA Cryptoasset registration.",
        "Same registered address as multiple unrelated financial entities.",
    ],
    "insurance": [
        "Claims FCA / PRA authorisation but not visible on the register.",
        "No SFCR available despite trading status.",
    ],
    "legal_services": [
        "Solicitor / barrister claimed but not on SRA / BSB register.",
        "No PII certificate produced.",
        "Operating client money without CMP membership.",
    ],
    "accounting": [
        "No supervisory body — operating an unsupervised AML-relevant business.",
        "No MLRO + no firm-wide risk assessment.",
        "Practitioner struck off but trading under a similar name.",
    ],
    "estate_agency": [
        "No HMRC AML registration despite trading.",
        "No redress scheme membership.",
        "Letting agent holding client money without CMP scheme.",
    ],
    "healthcare": [
        "Operating regulated activity without CQC registration.",
        "Clinical staff named but not on the relevant professional register.",
        "Repeated CQC requires-improvement / inadequate ratings.",
    ],
    "construction": [
        "Modern Slavery Act statement missing despite turnover ≥ £36m.",
        "Pattern of insolvent companies under same controlling director (phoenixing).",
    ],
    "gambling": [
        "Gambling activity claimed but no operating licence in the public register.",
        "Sole director with no relevant background.",
    ],
    "manufacturing_alcohol": [
        "Wholesaling claimed but no AWRS URN visible.",
    ],
    "manufacturing_pharma": [
        "Manufacturing claimed without an MHRA manufacturer's licence.",
    ],
    "transport": [
        "Operating without a current O-licence.",
    ],
    "technology": [
        "Processing personal data without ICO registration.",
        "No privacy policy on the entity website.",
    ],
}


# ─── Public API ──────────────────────────────────────────────────────────────


def classify_industry(sic_codes: Iterable[str] | None) -> IndustryProfile:
    """Map a list of SIC codes to the most-specific regime in the table."""
    codes = [str(c) for c in (sic_codes or []) if c]
    if not codes:
        return IndustryProfile(
            primary_regime="general_business",
            regime_label=_REGIME_LABELS["general_business"],
            confidence="low",
        )

    best_match = ("general_business", "", 0)
    for code in codes:
        # SIC codes are 5-digit; we walk the prefix table from longest to shortest
        for prefix, regime in _SIC_PREFIX_TABLE:
            if code.startswith(prefix) and len(prefix) > best_match[2]:
                best_match = (regime, code, len(prefix))
                break

    regime, matched_code, plen = best_match
    matched_sic = [c for c in codes if any(
        c.startswith(p) for p, r in _SIC_PREFIX_TABLE if r == regime
    )]
    confidence = "high" if plen >= 3 else "medium" if plen >= 2 else "low"

    return IndustryProfile(
        primary_regime=regime,
        regime_label=_REGIME_LABELS.get(regime, regime),
        matched_sic=matched_sic or codes[:1],
        confidence=confidence,
    )


def generate_document_checklist(
    profile: IndustryProfile,
    *,
    include_baseline: bool = True,
) -> list[Requirement]:
    """Return the document checklist for an industry profile."""
    out: list[Requirement] = []
    if include_baseline:
        out.extend(_BASELINE_REQUIREMENTS)
    out.extend(_REGIME_REQUIREMENTS.get(profile.primary_regime, []))
    return out


def red_flags_for(profile: IndustryProfile) -> list[str]:
    return list(_REGIME_RED_FLAGS.get(profile.primary_regime, []))


def requirements_for(
    sic_codes: Iterable[str] | None,
    *,
    company_status: str = "",
    company_age_years: float | None = None,
) -> ComplianceGuidance:
    """Top-level entry point: return everything needed for the prompt.

    Parameters
    ----------
    sic_codes : list of SIC codes from Companies House profile
    company_status : "active" / "dissolved" / etc.; modulates the summary line
    company_age_years : if known; affects whether some requirements apply
    """
    profile = classify_industry(sic_codes)
    requirements = generate_document_checklist(profile)
    red_flags = red_flags_for(profile)

    # Contextual cross-checks (apply across regimes)
    cross_checks = [
        Requirement(
            title="Companies House live status check",
            detail="Confirm the company is currently 'active' and not in administration / liquidation.",
            severity="mandatory",
            source_authority="Companies House",
            verification_method="Pull live data from companies-house.gov.uk.",
        ),
        Requirement(
            title="Sanctions screening",
            detail="Check entity + UBOs + directors against OFSI, OFAC, UN consolidated lists.",
            severity="mandatory",
            source_authority="OFSI / OFAC / UN",
            verification_method="Probitas runs this automatically (Section 6).",
        ),
        Requirement(
            title="Adverse-media screening",
            detail="A 90-day press scan of the entity name + each named UBO.",
            severity="recommended",
            source_authority="Open web",
            verification_method="Probitas runs this automatically (Section 7).",
        ),
    ]

    # Summary line for the prompt to riff off
    if profile.primary_regime == "general_business":
        summary_line = (
            "This entity is not in a sector that triggers specific UK regulator "
            "registration requirements beyond standard Companies House obligations. "
            "A baseline KYB document set applies."
        )
    else:
        summary_line = (
            f"This entity operates in **{profile.regime_label}** "
            f"(SIC {', '.join(profile.matched_sic)}). The regime-specific document "
            f"checklist below should be requested in addition to the baseline KYB set."
        )

    return ComplianceGuidance(
        industry=profile,
        requirements=requirements,
        cross_checks=cross_checks,
        red_flags_to_test=red_flags,
        summary_line=summary_line,
    )
