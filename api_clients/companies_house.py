"""
api_clients/companies_house.py — Companies House API: company profile,
officers, PSCs, filing history, accounts, and trustee appointment lookups.
"""

import re
import requests
from datetime import datetime, date
from concurrent.futures import ThreadPoolExecutor

from config import CH_API_KEY, get_ssl_verify

# ── Known virtual-office / mailbox addresses ─────────────────────────────────
_VIRTUAL_OFFICE_MARKERS: list[str] = [
    "27 old gloucester street",
    "20-22 Wenlock Road".lower(),
    "71-75 Shelton Street".lower(),
    "128 City Road".lower(),
    "167-169 Great Portland Street".lower(),
    "85 Great Portland Street".lower(),
    "Suite 4, 2 Old Brompton Road".lower(),
    "7 Bell Yard".lower(),
    "Kemp House, 160 City Road".lower(),
    "International House, 24 Holborn Viaduct".lower(),
    "86-90 Paul Street".lower(),
    "3rd Floor, 86-90 Paul Street".lower(),
    "1 Fore Street Avenue".lower(),
    "Linen Hall, 162-168 Regent Street".lower(),
    "3 More London Riverside".lower(),
]

# ── INDUSTRY RISK FOR DIRECT DEBIT (DD / Bacs) ──────────────────────────────
# Maps SIC code prefixes → (dd_risk_level, industry_category, reason)
# Based on UK payment processor and Bacs risk classifications.
# HIGH = high chargeback rates, fraud susceptibility, regulatory scrutiny
# MEDIUM = moderate chargeback or regulatory complexity
# LOW = standard risk, well-understood sector
#
# Prefix matching: 5-digit first, then 4, 3, 2 — most specific wins.

_INDUSTRY_DD_RISK: dict[str, tuple[str, str, str]] = {
    # ── 1. Financial & Regulated Services ─────────────────────────────
    "64110": ("high",   "Financial Services",       "Central banking — heavily regulated"),
    "64191": ("high",   "Financial Services",       "Banks — strict regulatory framework"),
    "64192": ("high",   "Financial Services",       "Building societies — regulated financial institution"),
    "64205": ("high",   "Financial Services",       "Financial services holding company"),
    "64301": ("medium", "Financial Services",       "Investment trust — regulated fund"),
    "64302": ("medium", "Financial Services",       "Unit trust — regulated fund"),
    "64303": ("medium", "Financial Services",       "Venture / development capital"),
    "64999": ("high",   "Financial Services",       "Financial intermediation NEC — crypto, FX platforms"),
    "6419":  ("high",   "Financial Services",       "Monetary intermediation — payday/unsecured lending risk"),
    "649":   ("high",   "Financial Services",       "Other financial services — potential crypto / FX / lending"),
    "651":   ("medium", "Insurance",                "Insurance — complex products, cancellation risk"),
    "652":   ("medium", "Insurance",                "Reinsurance"),
    "653":   ("medium", "Insurance",                "Pension funding"),
    "661":   ("medium", "Financial Services",       "Financial markets administration"),
    "662":   ("low",    "Insurance Services",       "Insurance auxiliary — low DD risk"),
    "663":   ("medium", "Financial Services",       "Fund management"),
    "641":   ("medium", "Financial Services",       "Financial intermediation"),
    "642":   ("low",    "Holding Companies",        "Activities of holding companies"),
    "643":   ("medium", "Financial Services",       "Trusts & funds"),

    # ── 2. Gambling & Adult (high chargeback / dispute) ───────────────
    "92710": ("high",   "Gambling & Betting",       "Gambling — UK GC regulated, high chargeback"),
    "927":   ("high",   "Gambling & Betting",       "Gambling & betting activities"),
    "920":   ("high",   "Adult / Entertainment",    "Entertainment — potential adult content risk"),
    "9234":  ("high",   "Adult / Entertainment",    "Other entertainment activities"),

    # ── 3. Travel & Future Delivery ───────────────────────────────────
    "79110": ("high",   "Travel & Tourism",         "Travel agency — future delivery, high ticket value"),
    "79120": ("high",   "Travel & Tourism",         "Tour operator — future delivery risk"),
    "79901": ("high",   "Travel & Tourism",         "Reservation services — future delivery"),
    "791":   ("high",   "Travel & Tourism",         "Travel agency activities"),
    "792":   ("high",   "Travel & Tourism",         "Tour operator activities"),
    "799":   ("high",   "Travel & Tourism",         "Reservation / booking services"),
    "511":   ("medium", "Travel & Transport",       "Passenger air transport"),

    # ── 4. Health, Wellness & Regulated Products ──────────────────────
    "47730": ("medium", "Pharmaceuticals",          "Dispensing chemist — regulated product sales"),
    "47260": ("medium", "Tobacco & Vaping",         "Tobacco retail — regulatory restrictions"),
    "2120":  ("medium", "Pharmaceuticals",          "Pharmaceutical manufacturing"),
    "4773":  ("medium", "Pharmaceuticals",          "Pharmacy / dispensing"),
    "8690":  ("low",    "Healthcare",               "Other human health activities"),

    # ── 5. Telemarketing & Direct Sales ───────────────────────────────
    "82990": ("medium", "Business Support",         "Other business support — telemarketing / direct sales risk"),
    "829":   ("medium", "Business Support",         "Business support services NEC"),

    # ── 6. Subscription / Recurring Billing ───────────────────────────
    "620":   ("low",    "IT & Software",            "Software / SaaS — standard DD risk"),
    "631":   ("low",    "IT & Software",            "Data processing & hosting"),
    "639":   ("low",    "IT & Software",            "Other information services"),

    # ── 7. Retail & E-Commerce ────────────────────────────────────────
    "479":   ("medium", "E-Commerce / Mail Order",  "Internet / mail order retail — chargeback exposure"),
    "4791":  ("medium", "E-Commerce / Mail Order",  "Online retail — non-delivery risk"),
    "471":   ("low",    "Retail",                   "Non-specialised retail"),
    "472":   ("low",    "Retail",                   "Food & beverage retail"),
    "474":   ("low",    "Retail",                   "ICT equipment retail"),
    "475":   ("low",    "Retail",                   "Other household goods retail"),
    "4777":  ("medium", "Retail (High Value)",      "Jewellery, watches, precious metals — high value goods"),

    # ── 8. Charity & Non-Profit ───────────────────────────────────────
    "889":   ("low",    "Charity / Non-Profit",     "Social work — can have high DD cancellation"),
    "949":   ("low",    "Charity / Non-Profit",     "Other membership organisations"),
    "9499":  ("low",    "Charity / Non-Profit",     "Other membership organisations NEC"),
    "9411":  ("low",    "Trade / Professional Body", "Business & employer organisations"),

    # ── 9. Professional Services (generally low risk) ─────────────────
    "691":   ("low",    "Professional Services",    "Legal activities"),
    "692":   ("low",    "Professional Services",    "Accounting / bookkeeping"),
    "701":   ("low",    "Corporate",                "Head office activities"),
    "702":   ("low",    "Professional Services",    "Management consultancy"),
    "711":   ("low",    "Professional Services",    "Architecture / engineering"),
    "712":   ("low",    "Professional Services",    "Technical testing"),
    "731":   ("low",    "Marketing & Advertising",  "Advertising"),
    "741":   ("low",    "Professional Services",    "Design activities"),
    "782":   ("low",    "Recruitment",              "Temporary employment agency"),
    "781":   ("low",    "Recruitment",              "Employment placement"),

    # ── 10. Construction & Property ───────────────────────────────────
    "411":   ("low",    "Construction",             "Property development"),
    "412":   ("low",    "Construction",             "Construction of buildings"),
    "421":   ("low",    "Construction",             "Road & railway construction"),
    "433":   ("low",    "Construction",             "Building completion"),
    "681":   ("low",    "Real Estate",              "Buying & selling property"),
    "682":   ("low",    "Real Estate",              "Real estate management"),
    "683":   ("low",    "Real Estate",              "Real estate on fee / contract"),

    # ── 11. Food & Hospitality ────────────────────────────────────────
    "561":   ("low",    "Hospitality",              "Restaurants & food service"),
    "562":   ("low",    "Hospitality",              "Catering"),
    "563":   ("low",    "Hospitality",              "Bars / drinking establishments"),
    "551":   ("low",    "Hospitality",              "Hotels & accommodation"),

    # ── 12. Wholesale & Distribution ──────────────────────────────────
    "461":   ("low",    "Wholesale",                "Wholesale agents"),
    "462":   ("low",    "Wholesale",                "Agricultural raw materials wholesale"),
    "467":   ("low",    "Wholesale",                "Other specialised wholesale"),
    "469":   ("low",    "Wholesale",                "Non-specialised wholesale"),

    # ── 13. Education & Training ──────────────────────────────────────
    "855":   ("low",    "Education",                "Other education"),
    "854":   ("low",    "Education",                "Higher education"),
    "856":   ("low",    "Education",                "Educational support"),

    # ── 14. Manufacturing ─────────────────────────────────────────────
    "10":    ("low",    "Manufacturing",            "Food manufacturing"),
    "22":    ("low",    "Manufacturing",            "Rubber & plastics"),
    "25":    ("low",    "Manufacturing",            "Fabricated metal products"),
    "28":    ("low",    "Manufacturing",            "Machinery manufacturing"),

    # ── 15. Other Services NEC (broad catch-all — NOT inherently risky) ──
    "96090": ("low",    "Other Services",           "Other services NEC — broad catch-all"),
    # ── 16. Sports & Recreation ───────────────────────────────────────
    "931":   ("low",    "Sports & Fitness",         "Sports facilities (gyms etc.)"),
    "932":   ("low",    "Leisure & Recreation",     "Amusement & recreation"),

    # ── 17. HARD-CODED HIGH-RISK INDUSTRIES (Regulatory / AML) ───────
    # Nuclear fuel processing
    "24460": ("high",   "Nuclear Materials",        "Processing of nuclear fuel — proliferation risk, export controls"),
    "244":   ("high",   "Nuclear Materials",        "Nuclear fuel processing — proliferation financing risk"),
    # Explosives & weapons
    "20510": ("high",   "Explosives",               "Manufacture of explosives — dual-use / weapons risk"),
    "205":   ("high",   "Explosives",               "Explosives manufacturing — regulatory scrutiny, export controls"),
    "25400": ("high",   "Weapons Manufacturing",    "Manufacture of weapons & ammunition — arms export controls"),
    "254":   ("high",   "Weapons Manufacturing",    "Weapons & ammunition manufacturing — regulated sector"),
    # Money Service Businesses (MSB)
    "66190": ("high",   "Money Services",           "Money service business — AML-regulated, high fraud risk"),
    # Pawnbroking
    "64920": ("high",   "Pawnbroking / Lending",    "Pawnbroking / other credit granting — predatory lending risk"),
    # Crypto / Digital Assets (explicit)
    "62090": ("high",   "Crypto / Digital Assets",  "Other IT activities — includes crypto exchanges, DeFi platforms"),
}


def fetch_ch_data(company_num):
    """Companies House: company profile + officers."""
    url = f"https://api.company-information.service.gov.uk/company/{company_num}"
    auth = requests.auth.HTTPBasicAuth(CH_API_KEY, "")
    v = get_ssl_verify()

    p = requests.get(url, auth=auth, timeout=20, verify=v)
    p.raise_for_status()
    profile = p.json()
    try:
        o = requests.get(f"{url}/officers", auth=auth, timeout=20, verify=v)
        o.raise_for_status()
        officers = o.json()
    except Exception:
        officers = {}
    active = [off for off in officers.get("items", []) if off.get("resigned_on") is None]
    return {
        "name": profile.get("company_name"),
        "status": profile.get("company_status"),
        "type": profile.get("type"),
        "date_of_creation": profile.get("date_of_creation"),
        "registered_office": profile.get("registered_office_address"),
        "sic_codes": profile.get("sic_codes"),
        "officers": active,
        "officer_names": [off.get("name") for off in active],
    }


# ═══════════════════════════════════════════════════════════════════════════════
# EXTENDED COMPANY DATA — for Company Check mode
# ═══════════════════════════════════════════════════════════════════════════════

def fetch_company_full_profile(company_num: str) -> dict:
    """Fetch the full company profile from CH API.
    Returns the raw JSON dict from /company/{num}."""
    url = f"https://api.company-information.service.gov.uk/company/{company_num}"
    auth = requests.auth.HTTPBasicAuth(CH_API_KEY, "")
    v = get_ssl_verify()
    r = requests.get(url, auth=auth, timeout=20, verify=v)
    r.raise_for_status()
    return r.json()


def fetch_company_officers(company_num: str, *, include_resigned: bool = False) -> list[dict]:
    """Fetch all officers for a company.
    Returns list of officer dicts with links for appointment lookups."""
    url = f"https://api.company-information.service.gov.uk/company/{company_num}/officers"
    auth = requests.auth.HTTPBasicAuth(CH_API_KEY, "")
    v = get_ssl_verify()
    all_items = []
    start_index = 0
    while True:
        r = requests.get(url, auth=auth, timeout=20, verify=v,
                         params={"start_index": start_index, "items_per_page": 100})
        if r.status_code != 200:
            break
        data = r.json()
        items = data.get("items", [])
        if not items:
            break
        all_items.extend(items)
        if len(all_items) >= data.get("total_results", 0):
            break
        start_index += len(items)
    if not include_resigned:
        all_items = [o for o in all_items if not o.get("resigned_on")]
    return all_items


def fetch_company_pscs(company_num: str) -> list[dict]:
    """Fetch Persons of Significant Control for a company."""
    url = f"https://api.company-information.service.gov.uk/company/{company_num}/persons-with-significant-control"
    auth = requests.auth.HTTPBasicAuth(CH_API_KEY, "")
    v = get_ssl_verify()
    try:
        r = requests.get(url, auth=auth, timeout=20, verify=v)
        if r.status_code != 200:
            return []
        return r.json().get("items", [])
    except Exception:
        return []


def fetch_company_filing_history(company_num: str, max_items: int = 25) -> list[dict]:
    """Fetch recent filing history for a company."""
    url = f"https://api.company-information.service.gov.uk/company/{company_num}/filing-history"
    auth = requests.auth.HTTPBasicAuth(CH_API_KEY, "")
    v = get_ssl_verify()
    try:
        r = requests.get(url, auth=auth, timeout=20, verify=v,
                         params={"items_per_page": max_items})
        if r.status_code != 200:
            return []
        return r.json().get("items", [])
    except Exception:
        return []


def fetch_company_charges(company_num: str) -> list[dict]:
    """Fetch charges (mortgages/debentures) for a company."""
    url = f"https://api.company-information.service.gov.uk/company/{company_num}/charges"
    auth = requests.auth.HTTPBasicAuth(CH_API_KEY, "")
    v = get_ssl_verify()
    try:
        r = requests.get(url, auth=auth, timeout=20, verify=v)
        if r.status_code != 200:
            return []
        return r.json().get("items", [])
    except Exception:
        return []


def fetch_officer_other_appointments(officer: dict) -> tuple[str, list[dict]]:
    """Fetch all active appointments for one officer.
    Returns (officer_name, list_of_appointments)."""
    name = officer.get("name", "Unknown")
    links = officer.get("links", {})
    appt_path = (links.get("officer", {}) or {}).get("appointments", "")
    if not appt_path:
        return name, [], 0
    auth = requests.auth.HTTPBasicAuth(CH_API_KEY, "")
    v = get_ssl_verify()
    url = f"https://api.company-information.service.gov.uk{appt_path}"
    try:
        r = requests.get(url, auth=auth, timeout=15, verify=v)
        if r.status_code != 200:
            return name, []
        items = r.json().get("items", [])
        active = []
        dissolved_count = 0
        for item in items:
            co = item.get("appointed_to", {})
            resigned = item.get("resigned_on")
            status = co.get("company_status", "")
            if status in ("dissolved", "liquidation", "administration",
                          "converted-closed", "insolvency-proceedings"):
                dissolved_count += 1
            if resigned:
                continue
            active.append({
                "company_name": co.get("company_name", ""),
                "company_number": co.get("company_number", ""),
                "company_status": status,
                "officer_role": item.get("officer_role", ""),
                "appointed_on": item.get("appointed_on", ""),
            })
        return name, active, dissolved_count
    except Exception:
        return name, [], 0


def analyse_company_age(date_of_creation: str) -> dict:
    """Analyse company age and return risk flags."""
    if not date_of_creation:
        return {"age_months": None, "risk": "unknown", "note": "No creation date available"}
    try:
        created = datetime.strptime(date_of_creation, "%Y-%m-%d").date()
    except ValueError:
        return {"age_months": None, "risk": "unknown", "note": f"Unparseable date: {date_of_creation}"}
    today = date.today()
    months = (today.year - created.year) * 12 + (today.month - created.month)
    if months < 6:
        risk = "high"
        note = f"Company is only {months} month(s) old — very newly incorporated. Elevated fraud risk."
    elif months < 12:
        risk = "medium"
        note = f"Company is {months} month(s) old — less than 1 year. Typical new-entity risk."
    elif months < 24:
        risk = "low-medium"
        note = f"Company is {months} months old ({months // 12} year(s)). Still relatively recent."
    else:
        years = months // 12
        risk = "low"
        note = f"Company is {years} year(s) old. Established entity."
    return {"age_months": months, "years": months // 12, "risk": risk,
            "date_of_creation": date_of_creation, "note": note}


def detect_virtual_office(registered_office: dict | None) -> dict:
    """Check if the registered office matches known virtual office addresses."""
    if not registered_office:
        return {"is_virtual": False, "note": "No address available"}
    parts = [
        registered_office.get("address_line_1", ""),
        registered_office.get("address_line_2", ""),
        registered_office.get("locality", ""),
        registered_office.get("postal_code", ""),
    ]
    full_addr = ", ".join(p for p in parts if p).lower()
    for marker in _VIRTUAL_OFFICE_MARKERS:
        if marker in full_addr:
            return {
                "is_virtual": True,
                "matched_marker": marker,
                "full_address": full_addr,
                "note": "Registered office matches a known virtual/mailbox address.",
            }
    return {"is_virtual": False, "full_address": full_addr, "note": ""}


def classify_sic_risk(sic_codes: list[str] | None) -> dict:
    """Classify company industry for Direct Debit risk based on SIC codes.

    Instead of simply flagging SIC codes as 'high risk', this maps each code
    to its DD industry risk category with context. Returns the highest-risk
    classification found and the industry description.
    """
    if not sic_codes:
        return {
            "codes": [],
            "industry_classifications": [],
            "risk_level": "unknown",
            "industry_category": "Unknown",
            "note": "No SIC codes registered — cannot classify industry risk",
        }

    classifications: list[dict] = []
    highest_risk = "low"
    _risk_rank = {"high": 3, "medium": 2, "low": 1, "unknown": 0}

    for code in sic_codes:
        matched = False
        # Try most specific first (5 digits), down to 2
        for prefix_len in (5, 4, 3, 2):
            prefix = code[:prefix_len]
            if prefix in _INDUSTRY_DD_RISK:
                risk, category, reason = _INDUSTRY_DD_RISK[prefix]
                classifications.append({
                    "code": code,
                    "dd_risk": risk,
                    "industry": category,
                    "reason": reason,
                })
                if _risk_rank.get(risk, 0) > _risk_rank.get(highest_risk, 0):
                    highest_risk = risk
                matched = True
                break
        if not matched:
            classifications.append({
                "code": code,
                "dd_risk": "low",
                "industry": "General",
                "reason": "Standard industry — no elevated DD risk identified",
            })

    # Determine primary industry from the first classification
    primary_industry = (
        classifications[0]["industry"] if classifications else "Unknown"
    )

    # Build summary note
    high_risk_items = [c for c in classifications if c["dd_risk"] == "high"]
    medium_risk_items = [c for c in classifications if c["dd_risk"] == "medium"]

    if high_risk_items:
        industries = ", ".join(set(c["industry"] for c in high_risk_items))
        note = (
            f"Industry classified as HIGH risk for Direct Debit: {industries}. "
            f"Expect higher fees, stricter terms, and potential rolling reserves."
        )
    elif medium_risk_items:
        industries = ", ".join(set(c["industry"] for c in medium_risk_items))
        note = (
            f"Industry has MODERATE DD risk factors: {industries}. "
            f"Standard processing likely with some enhanced monitoring."
        )
    else:
        note = "Industry is standard risk for Direct Debit — no elevated concerns."

    return {
        "codes": sic_codes,
        "industry_classifications": classifications,
        "risk_level": highest_risk,
        "industry_category": primary_industry,
        "note": note,
    }


def analyse_directors(
    officers: list[dict],
    company_num: str,
) -> dict:
    """Analyse directors for risk indicators: nationalities, appointment counts,
    failed companies. Fetches other appointments in parallel."""
    if not officers:
        return {"directors": [], "risk_flags": [], "summary": "No officers found"}

    auth = requests.auth.HTTPBasicAuth(CH_API_KEY, "")
    v = get_ssl_verify()
    from config import get_country_risk, is_elevated_risk

    director_profiles = []
    risk_flags = []

    def _process_officer(officer):
        name = officer.get("name", "Unknown")
        role = officer.get("officer_role", "")
        nationality = officer.get("nationality", "")
        appointed = officer.get("appointed_on", "")
        country_of_residence = officer.get("country_of_residence", "")
        dob = officer.get("date_of_birth", {})
        dob_month = dob.get("month") if dob else None
        dob_year = dob.get("year") if dob else None

        # Fetch other appointments
        result = fetch_officer_other_appointments(officer)
        if len(result) == 3:
            _, other_appts, dissolved_count = result
        else:
            _, other_appts = result
            dissolved_count = 0

        # Exclude this company
        other_appts = [a for a in other_appts if a.get("company_number") != company_num]

        # Approximate age from partial DOB
        approx_age = None
        if dob_year:
            approx_age = date.today().year - dob_year

        flags = []
        if approx_age and approx_age < 22:
            flags.append(f"Director is approximately {approx_age} years old — unusually young for a company director")
        if len(other_appts) >= 20:
            flags.append(f"Holds {len(other_appts)} other active directorships — possible professional director or nominee")
        elif len(other_appts) >= 10:
            flags.append(f"Holds {len(other_appts)} other active directorships — high directorship count")
        if dissolved_count >= 2:
            flags.append(f"🟡 Associated with {dissolved_count} dissolved companies — observation only, common for serial entrepreneurs")
        # 0-1 dissolved = normal business lifecycle, no flag

        # Nationality risk check
        nat_risk = ""
        if nationality:
            nat_risk = get_country_risk(nationality)
            if is_elevated_risk(nat_risk):
                flags.append(f"Nationality '{nationality}' is classified as {nat_risk}")
        if country_of_residence:
            res_risk = get_country_risk(country_of_residence)
            if is_elevated_risk(res_risk):
                flags.append(f"Country of residence '{country_of_residence}' is {res_risk}")

        # Extract officer ID for Companies House link
        _officer_links = officer.get("links", {})
        _appt_path = (_officer_links.get("officer", {}) or {}).get("appointments", "")
        # Path looks like /officers/<ID>/appointments
        _officer_id = ""
        if _appt_path:
            _parts = _appt_path.strip("/").split("/")
            if len(_parts) >= 2:
                _officer_id = _parts[1]

        return {
            "name": name,
            "role": role,
            "nationality": nationality,
            "nationality_risk": nat_risk,
            "country_of_residence": country_of_residence,
            "appointed_on": appointed,
            "approx_age": approx_age,
            "dob_year": dob_year,
            "dob_month": dob_month,
            "other_active_appointments": len(other_appts),
            "other_appointments_detail": other_appts[:15],
            "dissolved_companies": dissolved_count,
            "officer_id": _officer_id,
            "flags": flags,
        }

    with ThreadPoolExecutor(max_workers=5) as executor:
        futures = [executor.submit(_process_officer, off) for off in officers[:10]]
        for f in futures:
            try:
                profile = f.result(timeout=30)
                director_profiles.append(profile)
                risk_flags.extend(profile["flags"])
            except Exception:
                pass

    return {
        "directors": director_profiles,
        "risk_flags": risk_flags,
        "director_count": len(director_profiles),
        "summary": f"{len(risk_flags)} director risk flag(s) detected" if risk_flags else "No director risk flags",
    }


def detect_dormancy_risk(filing_history: list[dict], date_of_creation: str) -> dict:
    """Detect if the company was dormant and recently became active (shelf company risk)."""
    if not filing_history:
        return {"is_dormant_risk": False, "note": "No filing history available"}

    dormant_filings = []
    active_filings = []
    latest_accounts_date = None

    for f in filing_history:
        desc = (f.get("description", "") or "").lower()
        cat = (f.get("category", "") or "").lower()
        filing_date = f.get("date", "")
        if "dormant" in desc:
            dormant_filings.append(filing_date)
        if cat == "accounts" and "full" in desc:
            active_filings.append(filing_date)
        if cat == "accounts" and not latest_accounts_date:
            latest_accounts_date = filing_date

    if dormant_filings and active_filings:
        try:
            _valid_dormant = [datetime.strptime(d, "%Y-%m-%d") for d in dormant_filings if d]
            _valid_active = [datetime.strptime(d, "%Y-%m-%d") for d in active_filings if d]
            if not _valid_dormant or not _valid_active:
                raise ValueError("No valid dates")
            last_dormant = max(_valid_dormant)
            first_active = min(_valid_active)
            if first_active > last_dormant:
                gap = (first_active - last_dormant).days
                if gap > 365:
                    return {
                        "is_dormant_risk": True,
                        "dormant_until": last_dormant.strftime("%Y-%m-%d"),
                        "active_from": first_active.strftime("%Y-%m-%d"),
                        "gap_days": gap,
                        "note": f"Company was dormant until {last_dormant.strftime('%Y-%m-%d')} then filed "
                                f"active accounts {gap} days later. Possible shelf company.",
                    }
        except (ValueError, TypeError):
            pass

    if dormant_filings and not active_filings:
        return {
            "is_dormant_risk": True,
            "note": "Company has filed dormant accounts but no full/active accounts found in recent history.",
            "dormant_filings": len(dormant_filings),
        }

    return {"is_dormant_risk": False, "note": "No dormancy risk indicators detected"}


def extract_accounts_data(filing_history: list[dict], company_num: str) -> dict:
    """Extract key financial indicators from the most recent accounts filing description."""
    # CH API doesn't provide actual financial figures in the filing-history
    # endpoint — only descriptions.  We note what we can from the filings.
    accounts_filings = [f for f in (filing_history or [])
                        if (f.get("category", "") or "").lower() == "accounts"]
    if not accounts_filings:
        return {"has_accounts": False, "note": "No accounts filings found"}
    latest = accounts_filings[0]
    desc = latest.get("description", "")
    filing_date = latest.get("date", "")
    made_up_to = ""
    # Try to find made-up date from description_values
    for key in ("made_up_date",):
        val = (latest.get("description_values", {}) or {}).get(key, "")
        if val:
            made_up_to = val

    return {
        "has_accounts": True,
        "latest_accounts_date": filing_date,
        "made_up_to": made_up_to,
        "description": desc,
        "accounts_type": latest.get("type", ""),
        "accounts_count": len(accounts_filings),
        **_calculate_filing_overdue(filing_date),
    }


def _calculate_filing_overdue(latest_accounts_date: str) -> dict:
    """Calculate precise filing gap and overdue risk.

    Returns dict with:
      - filing_gap_days: int or None
      - filing_gap_months: int or None
      - filing_overdue_risk: 'high' | 'medium' | 'low' | 'unknown'
      - filing_overdue_note: str
    """
    if not latest_accounts_date:
        return {
            "filing_gap_days": None,
            "filing_gap_months": None,
            "filing_overdue_risk": "unknown",
            "filing_overdue_note": "No accounts filing date available",
        }
    try:
        accounts_dt = datetime.strptime(latest_accounts_date[:10], "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return {
            "filing_gap_days": None,
            "filing_gap_months": None,
            "filing_overdue_risk": "unknown",
            "filing_overdue_note": f"Unparseable accounts date: {latest_accounts_date}",
        }
    gap = date.today() - accounts_dt
    gap_days = gap.days
    gap_months = gap_days // 30

    if gap_days > 730:  # > 24 months
        risk = "high"
        note = (
            f"🔴 Latest accounts filed {gap_months} months ago ({latest_accounts_date}). "
            f"Filing gap exceeds 24 months — major regulatory red flag. "
            f"Company may be non-compliant with Companies Act s441."
        )
    elif gap_days > 548:  # > 18 months
        risk = "medium"
        note = (
            f"🟡 Latest accounts filed {gap_months} months ago ({latest_accounts_date}). "
            f"Filing gap exceeds 18 months — accounts may be overdue."
        )
    elif gap_days > 365:  # > 12 months
        risk = "low"
        note = (
            f"Latest accounts filed {gap_months} months ago ({latest_accounts_date}). "
            f"Within normal annual filing cycle."
        )
    else:
        risk = "low"
        note = (
            f"Accounts filed recently ({gap_months} months ago, {latest_accounts_date}). "
            f"Filing is current."
        )

    return {
        "filing_gap_days": gap_days,
        "filing_gap_months": gap_months,
        "filing_overdue_risk": risk,
        "filing_overdue_note": note,
    }


# ─── UBO (ULTIMATE BENEFICIAL OWNER) TRACER ──────────────────────────────

def _is_uk_registered(place: str, country: str) -> bool:
    """Check whether a PSC's registration location suggests UK."""
    _uk = ("england", "wales", "scotland", "northern ireland",
           "united kingdom", "uk", "companies house")
    combined = f"{place} {country}".lower()
    return any(m in combined for m in _uk) or not combined.strip()


def trace_ubo_chain(company_num, *, max_depth=3):
    """Recursive PSC tracer — follows corporate ownership chains until
    natural persons, state entities, or publicly-traded corps are found.

    Parameters
    ----------
    company_num : str
    max_depth   : int  (default 3 layers)

    Returns
    -------
    dict  — chain, ultimate_owners, layers_traced, graph_edges
    """
    visited = set()
    chain = []
    ultimate_owners = []
    graph_edges = []  # (source_co_name, target_entity_name, label)

    def _trace(co_num, co_name, depth):
        if depth > max_depth or co_num in visited:
            if depth > max_depth and co_num not in visited:
                ultimate_owners.append({
                    "name": co_name, "kind": "max-depth", "depth": depth,
                    "terminal_type": "Max Depth Reached",
                })
            return
        visited.add(co_num)

        pscs = fetch_company_pscs(co_num)
        layer = {"company_number": co_num, "company_name": co_name,
                 "depth": depth, "pscs": [], "ceased_pscs": []}

        for psc in pscs:
            kind = (psc.get("kind") or "").lower()
            _ne = psc.get("name_elements") or {}
            name = psc.get("name") or " ".join(
                p for p in (_ne.get("forename", ""), _ne.get("surname", ""))
                if p
            ) or "Unknown"
            natures = psc.get("natures_of_control", [])
            nationality = psc.get("nationality", "")
            ceased_on = psc.get("ceased_on", "")

            entry = {
                "name": name, "kind": kind, "depth": depth,
                "natures_of_control": natures, "nationality": nationality,
                "ceased": bool(ceased_on), "ceased_on": ceased_on,
            }

            # Skip ceased PSCs from active ownership tracing
            if ceased_on:
                entry["terminal_type"] = "Ceased"
                layer["ceased_pscs"].append(entry)
                continue

            if "individual" in kind:
                entry["terminal_type"] = "Natural Person"
                ultimate_owners.append(entry)
                graph_edges.append((co_name, name, "UBO (individual)"))

            elif "corporate" in kind:
                ident = psc.get("identification") or {}
                reg_num = (ident.get("registration_number") or "").strip()
                legal_form = (ident.get("legal_form") or "").lower()
                place = (ident.get("place_registered") or "").lower()
                country = (ident.get("country_registered") or "").lower()

                if any(kw in legal_form for kw in
                       ("plc", "public limited", "societas europaea")):
                    entry["terminal_type"] = "Publicly Traded (PLC)"
                    ultimate_owners.append(entry)
                    graph_edges.append((co_name, name, "PLC"))

                elif reg_num and _is_uk_registered(place, country):
                    padded = reg_num.zfill(8) if reg_num.isdigit() else reg_num
                    graph_edges.append((co_name, name, "corporate owner"))
                    try:
                        sub = fetch_company_full_profile(padded)
                        sub_name = sub.get("company_name", name)
                        if "plc" in (sub.get("type") or "").lower():
                            entry["terminal_type"] = "Publicly Traded (PLC)"
                            entry["traced_company"] = sub_name
                            ultimate_owners.append(entry)
                        else:
                            entry["traced_company_number"] = padded
                            entry["traced_company_name"] = sub_name
                            _trace(padded, sub_name, depth + 1)
                    except Exception:
                        entry["terminal_type"] = "End of Trace: Could Not Resolve"
                        ultimate_owners.append(entry)
                else:
                    entry["terminal_type"] = (
                        "End of Trace: Foreign/Unresolvable Entity"
                    )
                    entry["registered_country"] = country or place or "unknown"
                    ultimate_owners.append(entry)
                    graph_edges.append((co_name, name, "foreign corp"))

            elif "legal-person" in kind:
                entry["terminal_type"] = "Government / State Entity"
                ultimate_owners.append(entry)
                graph_edges.append((co_name, name, "state entity"))

            elif "super-secure" in kind:
                entry["terminal_type"] = "Protected (Super-Secure PSC)"
                ultimate_owners.append(entry)
                graph_edges.append((co_name, name, "protected"))

            else:
                entry["terminal_type"] = "Unknown PSC Type"
                ultimate_owners.append(entry)

            layer["pscs"].append(entry)

        if not pscs:
            layer["note"] = "No PSCs found — may be exempt or not yet filed"
        chain.append(layer)

    # Resolve root company name
    try:
        root = fetch_company_full_profile(company_num)
        root_name = root.get("company_name", company_num)
    except Exception:
        root_name = company_num

    _trace(company_num, root_name, 0)

    return {
        "chain": chain,
        "ultimate_owners": ultimate_owners,
        "layers_traced": len(chain),
        "max_depth_reached": any(
            u.get("terminal_type") == "Max Depth Reached"
            for u in ultimate_owners
        ),
        "graph_edges": graph_edges,
    }


# ─── STRUCTURAL GOVERNANCE ANOMALY DETECTION ─────────────────────────────

def fetch_trustee_appointments(ch_data):
    """For each active CH officer, fetch their other company appointments.
    Uses CH API /officers/{officer_id}/appointments endpoint.
    Returns dict: officer_name -> list of {company_name, company_number,
    company_status, officer_role, appointed_on, sic_codes}.
    """
    if not ch_data or not CH_API_KEY:
        return {}

    officers = ch_data.get("officers", [])
    if not officers:
        return {}

    auth = requests.auth.HTTPBasicAuth(CH_API_KEY, "")
    v = get_ssl_verify()
    results = {}

    def _fetch_one(officer):
        name = officer.get("name", "Unknown")
        links = officer.get("links", {})
        # CH officer links look like: {"officer": {"appointments": "/officers/abc123/appointments"}}
        appt_path = (links.get("officer", {}) or {}).get("appointments", "")
        if not appt_path:
            return name, []
        url = f"https://api.company-information.service.gov.uk{appt_path}"
        try:
            r = requests.get(url, auth=auth, timeout=15, verify=v)
            if r.status_code != 200:
                return name, []
            data = r.json()
            items = data.get("items", [])
            appts = []
            for item in items:
                co_name = (item.get("appointed_to") or {}).get("company_name", "")
                co_num = (item.get("appointed_to") or {}).get("company_number", "")
                co_status = (item.get("appointed_to") or {}).get("company_status", "")
                role = item.get("officer_role", "")
                appointed = item.get("appointed_on", "")
                resigned = item.get("resigned_on")
                # Only active appointments
                if resigned:
                    continue
                appts.append({
                    "company_name": co_name,
                    "company_number": co_num,
                    "company_status": co_status,
                    "officer_role": role,
                    "appointed_on": appointed,
                })
            return name, appts
        except Exception:
            return name, []

    # Limit to first 10 officers to avoid rate-limiting
    with ThreadPoolExecutor(max_workers=5) as executor:
        futures = [executor.submit(_fetch_one, off) for off in officers[:10]]
        for f in futures:
            try:
                name, appts = f.result(timeout=20)
                results[name] = appts
            except Exception:
                pass

    return results


def assess_structural_governance(charity_data, ch_data, trustees,
                                  trustee_appointments=None):
    """Detect structural governance anomalies.

    Returns dict with:
      - capacity_flags: list of observation strings
      - trustee_directorships: dict of name -> {count, entities}
      - concentration_flags: list of observation strings
      - summary: overall assessment string
    """
    flags = []
    concentration = []
    trustee_directorships = {}

    inc = charity_data.get("latest_income") or 0
    exp = charity_data.get("latest_expenditure") or 0
    num_trustees = charity_data.get("num_trustees") or len(trustees or [])
    employees = charity_data.get("employees") or 0
    volunteers = charity_data.get("volunteers") or 0
    entity_name = charity_data.get("charity_name", "")

    # ── Income vs Trustee capacity ──
    if inc >= 1_000_000 and num_trustees <= 3:
        flags.append(
            f"Annual income is £{inc:,.0f} with only {num_trustees} trustee(s). "
            f"For charities of this income scale, a small trustee board may "
            f"limit the breadth of oversight, skills diversity, and succession "
            f"planning capacity. This is an observation for the analyst to "
            f"explore — it does not imply wrongdoing."
        )
    elif inc >= 500_000 and num_trustees <= 2:
        flags.append(
            f"Annual income is £{inc:,.0f} with only {num_trustees} trustee(s). "
            f"A board of 2 or fewer limits quorum capacity and independent "
            f"oversight. Consider whether the charity has adequate governance "
            f"structures for its financial scale."
        )

    # ── Income vs Employees ──
    if inc >= 1_000_000 and employees == 0:
        flags.append(
            f"Annual income is £{inc:,.0f} with no paid employees reported. "
            f"A purely volunteer-run charity at this income level may face "
            f"capacity constraints in financial management, compliance, and "
            f"operational delivery. Verify whether the charity uses external "
            f"service providers or seconded staff."
        )
    elif inc >= 500_000 and employees == 0:
        flags.append(
            f"Annual income is £{inc:,.0f} with no paid employees. "
            f"Consider whether volunteer-only governance and operations "
            f"are proportionate to income."
        )

    # ── Expenditure vs Income with tiny board ──
    if inc > 0 and exp > 0:
        spend_pct = exp / inc * 100
        if spend_pct > 95 and num_trustees <= 3 and inc >= 500_000:
            flags.append(
                f"High spend-to-income ratio ({spend_pct:.0f}%) combined with a "
                f"small board ({num_trustees} trustees). Financial headroom is "
                f"limited, increasing dependency on continuous income flow."
            )

    # ── Trustee directorships analysis ──
    if trustee_appointments:
        for name, appts in trustee_appointments.items():
            # Exclude the current charity's own company from count
            co_num = (charity_data.get("company_number") or "").strip()
            other = [a for a in appts if a.get("company_number") != co_num]
            if other:
                trustee_directorships[name] = {
                    "count": len(other),
                    "entities": [
                        {
                            "company_name": a["company_name"],
                            "company_number": a["company_number"],
                            "company_status": a["company_status"],
                            "officer_role": a["officer_role"],
                        }
                        for a in other[:15]  # cap output size
                    ],
                }
                if len(other) >= 3:
                    entity_names = [a["company_name"] for a in other[:5]]
                    concentration.append(
                        f"{name} holds {len(other)} other active directorship(s)/officership(s) "
                        f"(including: {', '.join(entity_names)}"
                        f"{'...' if len(other) > 5 else ''}). "
                        f"Multiple directorships may indicate governance concentration "
                        f"or time-capacity considerations — this is noted for the analyst "
                        f"to assess contextually, not as an indication of misconduct."
                    )

    # ── Cross-trustee overlap ──
    if trustee_appointments and len(trustee_appointments) >= 2:
        # Check if multiple trustees share directorships at the same entity
        entity_to_trustees = {}
        co_num = (charity_data.get("company_number") or "").strip()
        for name, appts in trustee_appointments.items():
            for a in appts:
                aco = a.get("company_number", "")
                if aco and aco != co_num:
                    entity_to_trustees.setdefault(aco, set()).add(name)
        for aco, tset in entity_to_trustees.items():
            if len(tset) >= 2:
                co_name = ""
                for name in tset:
                    for a in trustee_appointments.get(name, []):
                        if a.get("company_number") == aco:
                            co_name = a.get("company_name", aco)
                            break
                    if co_name:
                        break
                concentration.append(
                    f"Trustees {', '.join(sorted(tset))} share a directorship at "
                    f"{co_name} ({aco}). Shared external relationships between "
                    f"multiple trustees are noted for context."
                )

    # Summary
    total_flags = len(flags) + len(concentration)
    if total_flags == 0:
        summary = "No structural governance anomalies detected."
    elif total_flags <= 2:
        summary = f"{total_flags} governance observation(s) noted — low structural concern."
    else:
        summary = f"{total_flags} governance observation(s) noted — analyst review recommended."

    return {
        "capacity_flags": flags,
        "trustee_directorships": trustee_directorships,
        "concentration_flags": concentration,
        "summary": summary,
        "total_flags": total_flags,
    }


