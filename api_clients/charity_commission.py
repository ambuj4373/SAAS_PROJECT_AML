"""
api_clients/charity_commission.py — Charity Commission API interactions,
PDF downloads, governance intelligence, and financial history.
"""

import re
import os
import requests
from datetime import datetime
from bs4 import BeautifulSoup

from config import (
    CHARITY_COMMISSION_API_KEY, CH_API_KEY,
    get_ssl_verify, get_country_risk, is_elevated_risk,
)

def download_cc_accounts_pdf(charity_num, company_number=None, organisation_number=None):
    """Try to download the most recent accounts PDF."""
    result = download_cc_latest_tar(charity_num, company_number=company_number,
                                     organisation_number=organisation_number)
    if result:
        return result["bytes"], result["url"]
    return None, None


def download_cc_latest_tar(charity_num, allow_previous_year=True,
                           company_number: str | None = None,
                           organisation_number: str | None = None):
    """Download the most recent Accounts & TAR PDF.

    Strategy
    --------
    1. CC submissions API → find latest *Accounts* submission.
    2. Scrape the CC accounts-and-annual-returns web page.
    3. Companies House document API (if *company_number* supplied).
    4. If the latest year's PDF is unavailable and *allow_previous_year*
       is True, try the previous year.

    Returns dict or None::

        {"url": str, "year": str, "title": str, "bytes": bytes,
         "date_received": str, "on_time": bool|None,
         "source": str}
    """
    v = get_ssl_verify()
    base = "https://register-of-charities.charitycommission.gov.uk"
    api_h = {"Ocp-Apim-Subscription-Key": CHARITY_COMMISSION_API_KEY}
    api_base = "https://api.charitycommission.gov.uk/register/api"

    # ── Candidates: (url, year, title, date_received, on_time, source) ─
    pdf_candidates: list[tuple] = []
    seen_urls: set[str] = set()

    # Keywords that indicate annual-return-only (NOT accounts/TAR)
    _annual_return_kw = ["annual return", "annual_return"]

    def _is_accounts(title: str) -> bool:
        """Return True if the title looks like Accounts/TAR, not Annual Return."""
        t = title.lower()
        if any(kw in t for kw in _annual_return_kw):
            return False
        return True

    # ── Method 1: CC submissions API ─────────────────────────────────
    try:
        r = requests.get(f"{api_base}/charitysubmissions/{charity_num}/0",
                         headers=api_h, timeout=20, verify=v)
        if r.status_code == 200:
            subs = r.json()
            if isinstance(subs, list):
                for sub in subs:
                    doc_url = (sub.get("doc_url")
                               or sub.get("document_url") or "")
                    if not doc_url or ".pdf" not in doc_url.lower():
                        continue
                    title = (sub.get("submission_type")
                             or sub.get("title") or "Accounts")
                    if not _is_accounts(title):
                        continue
                    if doc_url in seen_urls:
                        continue
                    fy = (sub.get("fin_period_end_date")
                          or sub.get("reporting_year") or "")
                    year = str(fy)[:4] if fy else ""
                    date_recv = str(sub.get("date_received") or "")[:10]
                    on_time = sub.get("on_time")  # may be None
                    pdf_candidates.append((doc_url, year, title, date_recv,
                                           on_time,
                                           "Charity Commission Official Filing"))
                    seen_urls.add(doc_url)
    except Exception:
        pass

    # ── Method 2: Scrape the CC accounts web page ────────────────────
    _cc_id = organisation_number or charity_num
    _page_urls = [
        f"{base}/charity-search/-/charity-details/{_cc_id}"
        f"/accounts-and-annual-returns",
        f"{base}/en/charity-search/-/charity-details/{_cc_id}"
        f"/accounts-and-annual-returns",
    ]
    for page_url in _page_urls:
        try:
            page_r = requests.get(
                page_url, timeout=20, verify=v,
                headers={"User-Agent": "Mozilla/5.0"},
                allow_redirects=True,
            )
            if page_r.status_code != 200:
                continue

            soup = BeautifulSoup(page_r.text, "html.parser")

            # --- Strategy A: parse table rows for "Accounts" lines ---
            for tr in soup.find_all("tr"):
                cells = tr.find_all(["td", "th"])
                if len(cells) < 3:
                    continue
                row_text = " ".join(
                    c.get_text(strip=True) for c in cells).lower()
                # Must look like an accounts / TAR row
                if not any(kw in row_text
                           for kw in ("accounts", "tar", "financial")):
                    continue
                if not _is_accounts(row_text):
                    continue

                # Extract year & date received from cells
                year = ""
                date_recv = ""
                on_time_flag = None
                title_text = cells[0].get_text(strip=True) or "Accounts"
                for cell in cells:
                    ct = cell.get_text(strip=True)
                    yr_match = re.search(r"(\d{4})", ct)
                    if yr_match and not year:
                        year = yr_match.group(1)
                    dt_match = re.search(
                        r"(\d{1,2}\s+\w+\s+\d{4})", ct)
                    if dt_match and not date_recv:
                        date_recv = dt_match.group(1)
                    if "late" in ct.lower():
                        on_time_flag = False
                    elif "on time" in ct.lower():
                        on_time_flag = True

                # Find any download link in this row (PDF href OR
                # Liferay portlet resource URL with "Download" text)
                for a_tag in tr.find_all("a", href=True):
                    href = a_tag["href"]
                    link_text = a_tag.get_text(strip=True).lower()
                    is_download = (
                        ".pdf" in href.lower()
                        or "download" in link_text
                        or "accounts-resource" in href.lower()
                        or "p_p_resource_id" in href.lower()
                    )
                    if not is_download:
                        continue
                    if not href.startswith("http"):
                        href = base + href
                    if href in seen_urls:
                        continue
                    pdf_candidates.append(
                        (href, year, title_text, date_recv,
                         on_time_flag,
                         "Charity Commission Official Filing"))
                    seen_urls.add(href)

            # --- Strategy B (legacy): bare PDF links outside tables ---
            for a_tag in soup.find_all("a", href=True):
                href = a_tag["href"]
                if ".pdf" not in href.lower():
                    continue
                if not href.startswith("http"):
                    href = base + href
                if href in seen_urls:
                    continue
                link_text = a_tag.get_text(strip=True) or ""
                parent_tr = a_tag.find_parent("tr")
                title_text = link_text or "Accounts"
                year = ""
                date_recv = ""
                if parent_tr:
                    cells = parent_tr.find_all("td")
                    for cell in cells:
                        ct = cell.get_text(strip=True)
                        yr_match = re.search(r"(\d{4})", ct)
                        if yr_match and not year:
                            year = yr_match.group(1)
                        dt_match = re.search(
                            r"(\d{1,2}[/-]\d{1,2}[/-]\d{2,4})", ct)
                        if dt_match and not date_recv:
                            date_recv = dt_match.group(1)
                    if cells:
                        title_text = (
                            cells[0].get_text(strip=True) or title_text)
                if not _is_accounts(title_text):
                    continue
                pdf_candidates.append(
                    (href, year, title_text, date_recv, None,
                     "Charity Commission Official Filing"))
                seen_urls.add(href)

            if pdf_candidates:
                break
        except Exception:
            continue

    # ── Method 3: Companies House document API ───────────────────────
    _co = (company_number or "").strip()
    if _co and CH_API_KEY:
        try:
            _ch_base = "https://api.company-information.service.gov.uk"
            _ch_auth = requests.auth.HTTPBasicAuth(CH_API_KEY, "")
            _fh = requests.get(
                f"{_ch_base}/company/{_co}/filing-history",
                auth=_ch_auth, timeout=20, verify=v,
                params={"category": "accounts", "items_per_page": 6},
            )
            if _fh.status_code == 200:
                _items = _fh.json().get("items", [])
                for _fi in _items:
                    _desc = _fi.get("description", "")
                    # Skip anything that's clearly not accounts
                    if "annual-return" in _desc or "confirmation" in _desc:
                        continue
                    _doc_meta_url = (
                        _fi.get("links", {}).get("document_metadata", ""))
                    if not _doc_meta_url:
                        continue
                    _filing_date = _fi.get("date", "")
                    _year = _filing_date[:4] if _filing_date else ""
                    _title = _desc.replace("-", " ").replace("_", " ").title()
                    # Build direct PDF download URL from metadata
                    _dm = requests.get(
                        _doc_meta_url, auth=_ch_auth, timeout=15, verify=v)
                    if _dm.status_code != 200:
                        continue
                    _dm_data = _dm.json()
                    _doc_content = (
                        _dm_data.get("links", {}).get("document", ""))
                    if not _doc_content:
                        continue
                    # Full download URL
                    _pdf_dl = _doc_content
                    if not _pdf_dl.startswith("http"):
                        _pdf_dl = (
                            "https://document-api.company-information"
                            ".service.gov.uk" + _pdf_dl)
                    if _pdf_dl in seen_urls:
                        continue
                    pdf_candidates.append(
                        (_pdf_dl, _year,
                         f"Accounts ({_title[:50]})",
                         _filing_date, None,
                         "Companies House Filing"))
                    seen_urls.add(_pdf_dl)
        except Exception:
            pass

    if not pdf_candidates:
        return None

    # Sort newest first
    pdf_candidates.sort(key=lambda x: x[1], reverse=True)

    # Determine how many to try: latest only, or latest + previous
    max_attempts = 2 if allow_previous_year else 1

    for url, year, title, date_recv, on_time, source in (
            pdf_candidates[:max_attempts]):
        try:
            _dl_headers = {"User-Agent": "Mozilla/5.0"}
            # CH document API requires Accept: application/pdf + auth
            _dl_auth = None
            if "company-information.service.gov.uk" in url:
                _dl_headers["Accept"] = "application/pdf"
                _dl_auth = requests.auth.HTTPBasicAuth(CH_API_KEY, "")
            pdf_r = requests.get(url, timeout=30, verify=v,
                                 headers=_dl_headers, auth=_dl_auth)
            if (pdf_r.status_code == 200
                    and len(pdf_r.content) > 500
                    and pdf_r.content[:4] == b'%PDF'):
                return {
                    "url": url,
                    "year": year,
                    "title": title,
                    "bytes": pdf_r.content,
                    "date_received": date_recv,
                    "on_time": on_time,
                    "source": source,
                }
        except Exception:
            continue

    return None


# ═══════════════════════════════════════════════════════════════════════════════
# DATA ENGINES
# ═══════════════════════════════════════════════════════════════════════════════

def fetch_charity_data(charity_num):
    """Charity Commission: core details + financial overview."""
    v = get_ssl_verify()
    h = {"Ocp-Apim-Subscription-Key": CHARITY_COMMISSION_API_KEY}
    base = "https://api.charitycommission.gov.uk/register/api"

    r1 = requests.get(f"{base}/allcharitydetails/{charity_num}/0", headers=h, timeout=20, verify=v)
    r1.raise_for_status()
    d = r1.json()

    overview = {}
    try:
        r2 = requests.get(f"{base}/charityoverview/{charity_num}/0", headers=h, timeout=20, verify=v)
        r2.raise_for_status()
        overview = r2.json()
    except Exception:
        pass

    trustees = [t["trustee_name"] for t in (d.get("trustee_names") or []) if t.get("trustee_name")]

    classifications = d.get("who_what_where") or []
    what_list = [c["classification_desc"] for c in classifications if c.get("classification_type") == "What"]
    who_list = [c["classification_desc"] for c in classifications if c.get("classification_type") == "Who"]
    how_list = [c["classification_desc"] for c in classifications if c.get("classification_type") == "How"]

    countries_raw = d.get("CharityAoOCountryContinent") or []
    countries = [{"country": a.get("country"), "continent": a.get("continent")} for a in countries_raw]

    # ── Other names (trading names, former names) ──
    other_names = [n.get("charity_name", "") for n in (d.get("other_names") or [])
                   if n.get("charity_name")]

    return {
        "charity_name": d.get("charity_name"),
        "charity_number": d.get("reg_charity_number"),
        "organisation_number": d.get("organisation_number"),
        "company_number": d.get("charity_co_reg_number"),
        "charity_type": d.get("charity_type"),
        "reg_status": d.get("reg_status"),
        "date_of_registration": d.get("date_of_registration"),
        "date_of_removal": d.get("date_of_removal"),
        "removal_reason": d.get("removal_reason"),
        "address": ", ".join(filter(None, [
            d.get("address_line_one"), d.get("address_line_two"),
            d.get("address_line_three"), d.get("address_post_code"),
        ])),
        "phone": d.get("phone"),
        "email": d.get("email"),
        "website": d.get("web"),
        "trustees": trustees,
        "what_it_does": what_list,
        "who_it_helps": who_list,
        "how_it_operates": how_list,
        "countries": countries,
        "activities": overview.get("activities"),
        "latest_income": overview.get("latest_income"),
        "latest_expenditure": overview.get("latest_expenditure"),
        "employees": overview.get("employees"),
        "num_trustees": overview.get("trustees"),
        "fin_year_end": overview.get("latest_acc_fin_year_end_date"),
        "inc_donations": overview.get("inc_donations_legacies"),
        "inc_charitable": overview.get("inc_charitable_activities"),
        "inc_trading": overview.get("inc_other_trading_activities"),
        "inc_investments": overview.get("inc_investments"),
        "inc_other": overview.get("inc_other"),
        "exp_raising": overview.get("exp_raising_funds"),
        "exp_charitable": overview.get("exp_charitable_activities"),
        "exp_other": overview.get("exp_other"),
        "investment_gains": overview.get("investment_gains_losses"),
        "trading_subsidiary": overview.get("trading_subsidiary"),
        "trustee_benefits": overview.get("any_trustee_benefit"),
        "grant_making": overview.get("grant_making_main_activity"),
        "employees_over_60k": overview.get("employees_over_60k"),
        "volunteers": overview.get("volunteers"),
        # ── Governance intelligence fields (from allcharitydetails) ──
        "other_names": other_names,
        "insolvent": d.get("insolvent"),
        "in_administration": d.get("in_administration"),
        "cio_ind": d.get("cio_ind"),
        "cio_dissolution_ind": d.get("cio_dissolution_ind"),
        "interim_manager_ind": d.get("interim_manager_ind"),
        "date_of_interim_manager_appt": d.get("date_of_interim_manager_appt"),
        "prev_excepted_ind": d.get("prev_excepted_ind"),
        "reporting_status": d.get("reporting_status"),
        "constituency": (d.get("constituency_name") or [{}])[0].get("constituency_name")
                        if d.get("constituency_name") else None,
        # ── Governance-relevant overview fields ──
        "raises_funds_from_public": overview.get("raises_funds_from_public"),
        "professional_fundraiser": overview.get("professional_fundraiser"),
        "receive_govt_funding_contracts": overview.get("receive_govt_funding_contracts"),
        "receive_govt_funding_grants": overview.get("receive_govt_funding_grants"),
        "number_govt_contracts": overview.get("number_govt_contracts"),
        "number_govt_grants": overview.get("number_govt_grants"),
        "income_from_govt_contracts": overview.get("income_from_govt_contracts"),
        "income_from_govt_grants": overview.get("income_from_govt_grants"),
        "trustee_payments_acting_as_trustee": overview.get("trustee_payments_acting_as_trustee"),
        "trustee_payments_services": overview.get("trustee_payments_services"),
        "trustee_also_director": overview.get("trustee_also_director"),
    }


# ── CC Governance Page Scraper ──────────────────────────────────────────
# Organisation-type explanations & risk context
_ORG_TYPE_INFO: dict[str, dict] = {
    "CIO": {
        "full_name": "Charitable Incorporated Organisation",
        "description": "Legal entity with limited liability for trustees. Can own property and enter contracts in its own name.",
        "ch_required": False,
        "risk_note": "CIOs are not registered with Companies House — no separate CH filing history available.",
    },
    "Charitable company": {
        "full_name": "Charitable Company (Limited by Guarantee)",
        "description": "Registered with both Charity Commission and Companies House. Dual regulatory oversight.",
        "ch_required": True,
        "risk_note": "Charitable companies must file accounts with both CC and CH, providing dual transparency.",
    },
    "Trust": {
        "full_name": "Charitable Trust",
        "description": "Unincorporated charity governed by a trust deed. Trustees may have personal liability.",
        "ch_required": False,
        "risk_note": "Trusts lack separate legal personality — trustees may bear personal liability. No CH registration.",
    },
    "Unincorporated association": {
        "full_name": "Unincorporated Association",
        "description": "Membership-based charity without separate legal personality. Members may share liability.",
        "ch_required": False,
        "risk_note": "No separate legal entity — limited formal governance structures may be in place.",
    },
    "Royal Charter body": {
        "full_name": "Royal Charter Body",
        "description": "Created by Royal Charter. Typically established institutions (universities, professional bodies).",
        "ch_required": False,
        "risk_note": "Royal Charter bodies are well-established institutions with strong regulatory oversight.",
    },
    "Community Benefit Society": {
        "full_name": "Community Benefit Society (BenCom)",
        "description": "Registered with the FCA, not Companies House. Run for the benefit of the community.",
        "ch_required": False,
        "risk_note": "Regulated by FCA rather than Companies House. Different filing requirements.",
    },
    "Excepted charity": {
        "full_name": "Excepted Charity",
        "description": "Not required to register with the Charity Commission (e.g. some churches, scout groups).",
        "ch_required": False,
        "risk_note": "Excepted charities have reduced CC oversight — may lack some transparency features.",
    },
    "Exempt charity": {
        "full_name": "Exempt Charity",
        "description": "Regulated by a principal regulator other than the Charity Commission (e.g. some universities).",
        "ch_required": False,
        "risk_note": "Regulated by an alternative principal regulator. CC data may be limited.",
    },
}

# Registration event type interpretations
_REG_EVENT_INFO: dict[str, str] = {
    "CIO registration": "Registered as a Charitable Incorporated Organisation",
    "Standard registration": "Standard charity registration",
    "Removed": "Removed from the register (closed, merged, or dissolved)",
    "Merged": "Merged into another charity",
    "Converted to CIO": "Changed legal structure to a CIO",
    "Re-registered": "Previously removed, now registered again",
    "Linked charity registration": "Part of a larger group structure",
    "Charitable company registration": "Registered as both a charity and a company",
    "Restoration": "Restored to the register after previous removal",
}


def build_cc_governance_intel(charity_data: dict) -> dict:
    """Build governance intelligence from CC API data already fetched.

    The CC governance web page is a JavaScript SPA (Liferay portal) that cannot
    be scraped with requests. Instead, we derive the same governance intelligence
    from the CC API responses (allcharitydetails + charityoverview) which contain
    all the underlying data.

    Returns dict with keys: registration_history, organisation_type,
    other_names, gift_aid, other_regulators, cc_declared_policies,
    land_property, governance_url, + risk/status flags.
    """
    base_url = "https://register-of-charities.charitycommission.gov.uk"
    charity_num = charity_data.get("charity_number", "")
    _cc_org_num = charity_data.get("organisation_number") or charity_num
    gov_url = f"{base_url}/charity-search/-/charity-details/{_cc_org_num}/governance"

    result = {
        "scrape_ok": True,  # Data sourced from API — always available
        "governance_url": gov_url,
        "data_source": "CC API (allcharitydetails + charityoverview)",
    }

    # ── Organisation type ──
    result["organisation_type"] = charity_data.get("charity_type")

    # ── Registration history (derived from API fields) ──
    reg_history = []
    reg_date = charity_data.get("date_of_registration")
    charity_type = charity_data.get("charity_type", "")
    if reg_date:
        rd_str = str(reg_date)[:10]
        if charity_data.get("cio_ind"):
            event_type = "CIO registration"
        elif charity_data.get("company_number"):
            event_type = "Charitable company registration"
        elif charity_data.get("prev_excepted_ind"):
            event_type = "Standard registration (previously excepted)"
        else:
            event_type = "Standard registration"
        reg_history.append({
            "date": rd_str,
            "event": event_type,
            "interpretation": _REG_EVENT_INFO.get(event_type, f"Registered as {charity_type}"),
        })

    removal_date = charity_data.get("date_of_removal")
    if removal_date:
        reason = charity_data.get("removal_reason") or "Unknown reason"
        reg_history.append({
            "date": str(removal_date)[:10],
            "event": "Removed",
            "interpretation": f"Removed from register: {reason}",
        })

    if charity_data.get("cio_dissolution_ind"):
        reg_history.append({
            "date": "",
            "event": "CIO dissolution indicator set",
            "interpretation": "CIO dissolution process initiated or completed",
        })

    result["registration_history"] = reg_history

    # ── Other names ──
    result["other_names"] = charity_data.get("other_names", [])

    # ── Gift Aid (derived from reg_status + reporting_status) ──
    # The CC API doesn't directly expose gift_aid status, but active registered
    # charities with up-to-date reporting are typically HMRC-recognised.
    reporting = charity_data.get("reporting_status", "")
    reg_status = charity_data.get("reg_status", "")
    if reg_status == "R" and reporting and "received" in reporting.lower():
        result["gift_aid"] = "Likely recognised by HMRC (active, reporting up to date)"
    elif reg_status == "R":
        result["gift_aid"] = "Status assumed active (registered charity)"
    elif reg_status == "RM":
        result["gift_aid"] = "Removed — gift aid status likely revoked"
    else:
        result["gift_aid"] = None

    # ── CC Declared Policies (not available via API — note for user) ──
    # The CC governance page shows policies declared by the charity in their
    # annual return, but this data is not exposed via the CC API.
    # We note this as a data limitation.
    result["cc_declared_policies"] = []
    result["cc_declared_policies_note"] = (
        "CC-declared policies (from annual return) are displayed on the CC governance page "
        "but not exposed via the CC API. Visit the governance page to view declared policies."
    )

    # ── Land & Property (not directly in API) ──
    result["land_property"] = None

    # ── Other regulators (not directly in API) ──
    result["other_regulators"] = None

    # ── Critical status flags (from API) ──
    result["status_flags"] = {}
    if charity_data.get("insolvent"):
        result["status_flags"]["insolvent"] = True
    if charity_data.get("in_administration"):
        result["status_flags"]["in_administration"] = True
    if charity_data.get("interim_manager_ind"):
        result["status_flags"]["interim_manager"] = True
        result["status_flags"]["interim_manager_date"] = charity_data.get(
            "date_of_interim_manager_appt")
    if charity_data.get("cio_dissolution_ind"):
        result["status_flags"]["cio_dissolution"] = True
    if charity_data.get("date_of_removal"):
        result["status_flags"]["removed"] = True
        result["status_flags"]["removal_reason"] = charity_data.get("removal_reason")

    # ── Funding model intelligence ──
    result["funding_model"] = {}
    if charity_data.get("raises_funds_from_public") is not None:
        result["funding_model"]["raises_from_public"] = charity_data["raises_funds_from_public"]
    if charity_data.get("professional_fundraiser") is not None:
        result["funding_model"]["professional_fundraiser"] = charity_data["professional_fundraiser"]
    if charity_data.get("receive_govt_funding_contracts"):
        result["funding_model"]["govt_contracts"] = {
            "count": charity_data.get("number_govt_contracts", 0),
            "income": charity_data.get("income_from_govt_contracts"),
        }
    if charity_data.get("receive_govt_funding_grants"):
        result["funding_model"]["govt_grants"] = {
            "count": charity_data.get("number_govt_grants", 0),
            "income": charity_data.get("income_from_govt_grants"),
        }
    result["funding_model"]["grant_making"] = charity_data.get("grant_making")

    # ── Trustee governance detail ──
    result["trustee_governance"] = {
        "trustee_benefits": charity_data.get("trustee_benefits"),
        "trustee_payments_acting_as_trustee": charity_data.get("trustee_payments_acting_as_trustee"),
        "trustee_payments_services": charity_data.get("trustee_payments_services"),
        "trustee_also_director": charity_data.get("trustee_also_director"),
        "trading_subsidiary": charity_data.get("trading_subsidiary"),
    }

    # ── Reporting status ──
    result["reporting_status"] = charity_data.get("reporting_status")

    return result


def fetch_financial_history(charity_num, max_years=5):
    """Fetch year-by-year income & expenditure from CC API.
    Returns list of dicts sorted ascending by year:
    [{"year": "2021", "income": 123456, "expenditure": 100000}, ...]
    """
    v = get_ssl_verify()
    h = {"Ocp-Apim-Subscription-Key": CHARITY_COMMISSION_API_KEY}
    base = "https://api.charitycommission.gov.uk/register/api"
    try:
        r = requests.get(
            f"{base}/charityfinancialhistory/{charity_num}/0",
            headers=h, timeout=20, verify=v,
        )
        if r.status_code != 200:
            return []
        data = r.json()
        if not isinstance(data, list):
            return []
        rows = []
        for entry in data:
            income = entry.get("income")
            expenditure = entry.get("expenditure")
            fin_year = entry.get("fin_period_end_date") or entry.get("financial_year_end") or ""
            if fin_year and income is not None:
                # Extract year from date string
                year = fin_year[:4] if len(fin_year) >= 4 else fin_year
                rows.append({
                    "year": year,
                    "income": income or 0,
                    "expenditure": expenditure or 0,
                })
        # Sort ascending by year, take last N
        rows.sort(key=lambda x: x["year"])
        return rows[-max_years:]
    except Exception:
        return []


