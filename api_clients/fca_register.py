"""
api_clients/fca_register.py — FCA Financial Services Register API integration.

Queries the FCA Register API to determine if firms/companies are:
  1. Registered with the FCA
  2. Their regulatory status (Authorised, Appointed Rep, etc.)
  3. Their regulated activities (insurance, investment, lending, etc.)
  4. Their current supervision status

This significantly reduces risk for regulated entities as they face:
  - Regular compliance audits
  - Strict capital requirements
  - Ongoing monitoring & enforcement
  - Consumer protection schemes

Rate limit: 50 requests per 10 seconds (built-in backoff)
API Endpoint: https://register.fca.org.uk/[firm_search_endpoint]

Public API:
    lookup_firm_by_name(firm_name) → FCAFirmResult
    lookup_firm_by_frn(frn) → FCAFirmResult
    compute_fca_risk_reduction(firm_result) → float
    classify_regulated_activities(activities) → dict[str, str]
"""

from __future__ import annotations

import json
import time
import re
from typing import Any, Optional
from datetime import datetime
from urllib.parse import quote

import requests
from pydantic import BaseModel, Field

from config import get_ssl_verify
from core.logging_config import get_logger

log = get_logger("api_clients.fca_register")

# ═══════════════════════════════════════════════════════════════════════════════
# RATE LIMITING
# ═══════════════════════════════════════════════════════════════════════════════

_RATE_LIMIT_CALLS = 50  # requests per window
_RATE_LIMIT_WINDOW = 10  # seconds
_last_requests: list[float] = []


def _enforce_rate_limit():
    """Enforce FCA rate limit: 50 requests per 10 seconds."""
    global _last_requests
    now = time.time()
    _last_requests = [t for t in _last_requests if now - t < _RATE_LIMIT_WINDOW]

    if len(_last_requests) >= _RATE_LIMIT_CALLS:
        sleep_time = _RATE_LIMIT_WINDOW - (now - _last_requests[0])
        if sleep_time > 0:
            log.warning(f"FCA rate limit approaching, sleeping {sleep_time:.2f}s")
            time.sleep(sleep_time)
            _last_requests = []

    _last_requests.append(now)


# ═══════════════════════════════════════════════════════════════════════════════
# DATA MODELS
# ═══════════════════════════════════════════════════════════════════════════════

class FCAActivity(BaseModel):
    """A single regulated activity."""
    activity_name: str
    activity_category: str
    investment_types: list[str] = Field(default_factory=list)
    customer_types: list[str] = Field(default_factory=list)
    status: str = "Authorised"
    effective_date: str = ""


class FCARegulator(BaseModel):
    """Regulatory body overseeing this firm."""
    regulatory_body: str  # "FCA", "PRA", "both"
    effective_date: str = ""


class FCAFirmResult(BaseModel):
    """Complete FCA firm lookup result."""
    found: bool = False
    firm_name: str = ""
    frn: str = ""  # FCA Firm Reference Number
    firm_type: str = ""  # "Limited", "Partnership", "Sole Trader", etc.
    authorisation_status: str = ""  # "Authorised", "No longer authorised", etc.
    authorised_date: str = ""
    current_status_date: str = ""
    regulated_activities: list[FCAActivity] = Field(default_factory=list)
    regulators: list[FCARegulator] = Field(default_factory=list)
    principal_address: str = ""
    telephone: str = ""
    companies_house_number: str = ""
    appointed_representatives: list[dict[str, Any]] = Field(default_factory=list)
    last_updated: str = ""
    raw_response: dict[str, Any] = Field(default_factory=dict)

    @property
    def is_regulated(self) -> bool:
        """Is this firm currently FCA-regulated?"""
        return (
            self.authorisation_status.lower() in [
                "authorised",
                "appointed representative",
                "arranged",
            ]
            and self.found
        )

    @property
    def risk_reduction_factor(self) -> float:
        """Risk reduction multiplier (0.0-1.0).

        Regulated firms reduce overall risk significantly:
          - Authorised: 0.75 (25% reduction)
          - Appointed Rep: 0.80 (20% reduction)
          - No longer authorised: 0.95 (5% reduction, still some trust)
          - Not found: 1.0 (no reduction)
        """
        if not self.found:
            return 1.0

        status = self.authorisation_status.lower()
        if "authorised" in status and "no longer" not in status:
            return 0.75
        elif "appointed representative" in status or "arranged" in status:
            return 0.80
        elif "no longer" in status:
            return 0.95
        return 1.0


# ═══════════════════════════════════════════════════════════════════════════════
# FCA API LOOKUP
# ═══════════════════════════════════════════════════════════════════════════════

def lookup_firm_by_ch_number(ch_number: str, firm_name: str = "", timeout: int = 10) -> FCAFirmResult:
    """BEST METHOD: Look up firm by Companies House number in FCA Register.
    
    This is the most reliable way to find FCA-regulated firms because:
    - Every FCA-regulated company must have a Companies House registration
    - FCA Register stores the CH number as a field
    - Lookup by CH number has minimal false negatives
    
    Args:
        ch_number: Companies House registration number (8-10 digits)
        firm_name: Optional firm name for fallback (if CH lookup fails)
        timeout: Request timeout in seconds
    
    Returns:
        FCAFirmResult with details if found
    """
    if not ch_number or not ch_number.strip():
        if firm_name:
            return lookup_firm_by_name(firm_name, timeout)
        return FCAFirmResult()

    _enforce_rate_limit()
    ch_number = ch_number.strip()
    
    try:
        # Try direct lookup by Companies House number
        search_url = "https://register.fca.org.uk/ShPo_PublicRegister/Search"
        
        params = {
            "ftc": "a",  # search by Companies House number
            "sb": ch_number,
            "pg": 1,
        }
        
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        }
        
        response = requests.get(
            search_url,
            params=params,
            headers=headers,
            timeout=timeout,
            verify=get_ssl_verify(),
        )
        
        if response.status_code == 200:
            result = _parse_fca_search_result(response.text, ch_number)
            if result.found:
                log.info(f"FCA: ✅ Found by CH#{ch_number}: {result.firm_name}")
                return result
        
        # Fallback to firm name if provided
        if firm_name:
            log.debug(f"FCA: CH#{ch_number} not found, trying firm name")
            return lookup_firm_by_name(firm_name, timeout)
        
        log.debug(f"FCA: CH#{ch_number} not found in register")
        return FCAFirmResult()
        
    except requests.RequestException as e:
        log.error(f"FCA lookup failed for CH#{ch_number}: {e}")
        return FCAFirmResult()


def lookup_firm_by_name(firm_name: str, timeout: int = 10) -> FCAFirmResult:
    """Look up a firm by name in the FCA Register.

    Args:
        firm_name: The name of the firm to search for
        timeout: Request timeout in seconds

    Returns:
        FCAFirmResult with details if found, or empty result if not found
    """
    if not firm_name or not firm_name.strip():
        return FCAFirmResult()

    _enforce_rate_limit()

    try:
        search_url = "https://register.fca.org.uk/ShPo_PublicRegister/Search"

        params = {
            "ftc": "c",  # search by firm name (company)
            "sb": firm_name,
            "pg": 1,
        }

        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        }

        response = requests.get(
            search_url,
            params=params,
            headers=headers,
            timeout=timeout,
            verify=get_ssl_verify(),
        )

        if response.status_code == 200:
            return _parse_fca_search_result(response.text, firm_name)
        else:
            log.debug(f"FCA lookup failed for '{firm_name}': {response.status_code}")
            return FCAFirmResult()

    except requests.RequestException as e:
        log.error(f"FCA API request failed for '{firm_name}': {e}")
        return FCAFirmResult()


def lookup_firm_by_frn(frn: str, timeout: int = 10) -> FCAFirmResult:
    """Look up a firm by FCA Firm Reference Number (FRN).

    Args:
        frn: The FCA Firm Reference Number (6-7 digits)
        timeout: Request timeout in seconds

    Returns:
        FCAFirmResult with details if found
    """
    if not frn or not frn.strip():
        return FCAFirmResult()

    _enforce_rate_limit()

    try:
        # FRN direct lookup
        search_url = f"https://register.fca.org.uk/ShPo_PublicRegister/Search"

        params = {
            "ftc": "b",  # search by FRN
            "sb": frn,
            "pg": 1,
        }

        headers = {
            "User-Agent": "HRCOB-V4.0-Intelligence-Platform/1.0",
            "Accept": "application/json,text/html",
        }

        response = requests.get(
            search_url,
            params=params,
            headers=headers,
            timeout=timeout,
            verify=get_ssl_verify(),
        )

        if response.status_code == 200:
            return _parse_fca_search_result(response.text, frn)
        else:
            log.warning(f"FCA lookup failed for FRN '{frn}': {response.status_code}")
            return FCAFirmResult()

    except requests.RequestException as e:
        log.error(f"FCA API request failed for FRN '{frn}': {e}")
        return FCAFirmResult()


def _parse_fca_search_result(html_response: str, search_term: str) -> FCAFirmResult:
    """Parse HTML response from FCA public register search.

    Since FCA doesn't expose a pure JSON API (requires authentication/subscription),
    we parse the HTML response to extract key firm details.

    Returns a FCAFirmResult with best-effort extracted data.
    """
    result = FCAFirmResult()

    try:
        # Check if response indicates no results
        if "no results" in html_response.lower() or "0 results" in html_response.lower():
            return result

        # Extract key patterns using regex
        # FCA firm reference number: 6-7 digits
        frn_match = re.search(r"FRN:\s*(\d{6,7})", html_response, re.IGNORECASE)
        if frn_match:
            result.frn = frn_match.group(1)
            result.found = True

        # Firm name
        name_match = re.search(r"Firm Name:\s*([^<\n]+)", html_response, re.IGNORECASE)
        if name_match:
            result.firm_name = name_match.group(1).strip()

        # Authorisation status
        status_match = re.search(
            r"Status:\s*([^<\n]+)", html_response, re.IGNORECASE
        )
        if status_match:
            result.authorisation_status = status_match.group(1).strip()

        # Try to extract firm type
        type_match = re.search(
            r"Firm Type:\s*([^<\n]+)", html_response, re.IGNORECASE
        )
        if type_match:
            result.firm_type = type_match.group(1).strip()

        # Address extraction
        address_match = re.search(r"Address:\s*([^<]+)<", html_response, re.IGNORECASE)
        if address_match:
            result.principal_address = address_match.group(1).strip()

        # Mark as found if we got a FRN
        if result.frn:
            result.found = True
            log.info(f"FCA: Found firm {result.firm_name} (FRN: {result.frn})")
        else:
            log.debug(f"FCA: No firm found for search term '{search_term}'")

        return result

    except Exception as e:
        log.error(f"Error parsing FCA response: {e}")
        return result


# ═══════════════════════════════════════════════════════════════════════════════
# RISK SCORING & CLASSIFICATION
# ═══════════════════════════════════════════════════════════════════════════════

def compute_fca_risk_reduction(firm_result: FCAFirmResult) -> float:
    """Compute risk score reduction based on FCA registration status.

    Returns a multiplier to apply to the overall risk score:
      - 0.75 for active FCA-regulated firms (25% reduction)
      - 0.80 for appointed representatives (20% reduction)
      - 0.95 for formerly regulated (5% reduction, minimal trust)
      - 1.0 for non-regulated or unknown (no reduction)

    Example:
        If overall risk score would be 45 (High):
          - If FCA regulated: 45 * 0.75 = 33.75 (Medium)
          - If not regulated: 45 * 1.0 = 45 (High)
    """
    return firm_result.risk_reduction_factor


def classify_regulated_activities(
    firm_result: FCAFirmResult,
) -> dict[str, list[str]]:
    """Classify firm's regulated activities into risk categories.

    Returns a dict mapping activity categories to risk levels:
      - "low_risk_activities": Insurance, pensions, general advice
      - "medium_risk_activities": Investment, lending
      - "high_risk_activities": Custody, derivatives trading
    """
    low_risk = []
    medium_risk = []
    high_risk = []

    for activity in firm_result.regulated_activities:
        category = activity.activity_category.lower()
        name = activity.activity_name.lower()

        # Low-risk: insurance, pensions, general advice
        if any(
            x in category or x in name
            for x in [
                "insurance",
                "pension",
                "annuity",
                "general insurance",
                "long-term insurance",
            ]
        ):
            low_risk.append(activity.activity_name)

        # High-risk: derivatives, custody, complex products
        elif any(
            x in category or x in name
            for x in ["derivatives", "custody", "safekeeping", "investment trust"]
        ):
            high_risk.append(activity.activity_name)

        # Medium-risk: investment, lending, advisory
        else:
            medium_risk.append(activity.activity_name)

    return {
        "low_risk_activities": low_risk,
        "medium_risk_activities": medium_risk,
        "high_risk_activities": high_risk,
    }


# ═══════════════════════════════════════════════════════════════════════════════
# SUMMARY & REPORTING
# ═══════════════════════════════════════════════════════════════════════════════

def summarise_fca_regulation(firm_result: FCAFirmResult) -> dict[str, Any]:
    """Generate a summary of the firm's FCA regulatory status.

    Returns a dict with:
      - is_regulated: bool
      - risk_reduction: float (0.0-1.0)
      - status_label: str
      - activities_summary: str
      - compliance_benefits: list[str]
    """
    if not firm_result.found:
        return {
            "is_regulated": False,
            "risk_reduction": 1.0,
            "status_label": "Not FCA regulated",
            "activities_summary": "N/A",
            "compliance_benefits": [],
            "note": "Entity is not registered with FCA. Higher due diligence required.",
        }

    activity_count = len(firm_result.regulated_activities)
    status = firm_result.authorisation_status

    benefits = [
        "Regulated by UK Financial Conduct Authority",
        "Subject to FCA Handbook rules and guidance",
        "Covered by Financial Services Compensation Scheme (FSCS)",
        "Regular compliance audits and monitoring",
    ]

    if firm_result.is_regulated:
        benefits.extend(
            [
                "Currently authorised to conduct regulated activities",
                f"Authorised since {firm_result.authorised_date or 'N/A'}",
            ]
        )
    else:
        benefits = [
            "Formerly regulated by FCA (heightened scrutiny needed)",
            "No longer authorised to conduct regulated activities",
        ]

    return {
        "is_regulated": firm_result.is_regulated,
        "risk_reduction": compute_fca_risk_reduction(firm_result),
        "status_label": status,
        "activities_summary": (
            f"{activity_count} regulated activities" if activity_count > 0 else "None"
        ),
        "compliance_benefits": benefits,
        "frn": firm_result.frn,
        "firm_type": firm_result.firm_type,
    }
