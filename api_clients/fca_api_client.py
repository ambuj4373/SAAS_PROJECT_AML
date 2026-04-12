"""
api_clients/fca_api_client.py — Query FCA API using Companies House number.

Use the FCA API key to pull firm details directly.
FCA holds Companies House number in their database - we can query by CH# directly.
"""

from __future__ import annotations

import os
from typing import Optional
from datetime import datetime

import requests
from pydantic import BaseModel, Field

from core.logging_config import get_logger
from config import get_ssl_verify

log = get_logger("api_clients.fca_api_client")


class FCAFirmDetails(BaseModel):
    """FCA firm data from API."""
    found: bool = False
    firm_name: str = ""
    frn: str = ""  # FCA Firm Reference Number
    ch_number: str = ""  # Companies House number
    address: str = ""
    phone: str = ""
    email: str = ""
    website: str = ""
    status: str = ""  # "Authorised", "No longer authorised"
    authorised_since: str = ""
    firm_type: str = ""  # "Regulated", "Exempt"
    trading_names: list[str] = Field(default_factory=list)
    regulated_activities: list[str] = Field(default_factory=list)
    client_money_restrictions: list[str] = Field(default_factory=list)
    requirements: list[str] = Field(default_factory=list)
    can_hold_client_money: bool = False
    can_control_client_money: bool = False
    is_currently_authorised: bool = False
    scraped_timestamp: str = ""
    page_url: str = ""

    @property
    def risk_reduction_factor(self) -> float:
        """Calculate risk reduction based on FCA status."""
        if not self.found:
            return 1.0
        if self.is_currently_authorised:
            return 0.75  # 25% reduction for active FCA authorised
        else:
            return 0.95  # 5% reduction for formerly authorised


def get_fca_api_key() -> str:
    """Get FCA API key from environment or config."""
    # Try environment variable first
    api_key = os.getenv("FCA_API_KEY")
    if api_key:
        return api_key
    
    # Fallback to the key user provided
    return "7e943729b1ba933c72deb9b18f199da8"


def lookup_fca_firm_by_ch_number(ch_number: str, timeout: int = 15) -> FCAFirmDetails:
    """
    Query FCA API using Companies House number.
    
    FCA database includes Companies House numbers, so we can search directly.
    
    Args:
        ch_number: Companies House registration number (e.g., "01925556")
        timeout: Request timeout in seconds
    
    Returns:
        FCAFirmDetails with all data from FCA API
    """
    
    ch_number = str(ch_number).strip()
    if not ch_number:
        return FCAFirmDetails()
    
    result = FCAFirmDetails(ch_number=ch_number)
    api_key = get_fca_api_key()
    
    if not api_key:
        log.error("[FCA] No API key configured")
        return result
    
    try:
        # FCA API endpoints to try
        # Option 1: Search by CH number
        api_urls = [
            f"https://api.fca.org.uk/v1/firms/search?ch_number={ch_number}",
            f"https://api.fca.org.uk/v1/firms?ch_number={ch_number}",
            f"https://api.fca.org.uk/v2/firms?search={ch_number}",
        ]
        
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "User-Agent": "HRCOB/4.0",
        }
        
        log.info(f"[FCA API] Looking up CH#{ch_number}")
        
        response = None
        last_error = None
        
        for url in api_urls:
            try:
                log.debug(f"[FCA API] Trying: {url}")
                response = requests.get(
                    url,
                    headers=headers,
                    timeout=timeout,
                    verify=get_ssl_verify(),
                )
                
                if response.status_code == 200:
                    log.debug(f"[FCA API] Success with: {url}")
                    break
                else:
                    last_error = response.status_code
                    log.debug(f"[FCA API] {url} → {response.status_code}")
            except Exception as e:
                last_error = str(e)
                log.debug(f"[FCA API] {url} → {e}")
        
        if not response or response.status_code != 200:
            log.warning(f"[FCA API] Failed: {last_error}")
            return result
        
        # Parse response
        data = response.json()
        
        # Extract firm details from response
        if isinstance(data, dict):
            firm = data.get("firm") or data.get("data") or data
        elif isinstance(data, list) and len(data) > 0:
            firm = data[0]
        else:
            log.warning(f"[FCA API] Unexpected response format: {type(data)}")
            return result
        
        # Map API response to our model
        result.found = True
        result.firm_name = firm.get("name") or firm.get("firm_name") or ""
        result.frn = str(firm.get("frn") or firm.get("reference_number") or "")
        result.ch_number = firm.get("companies_house_number") or firm.get("ch_number") or ch_number
        result.address = firm.get("address") or ""
        result.phone = firm.get("phone") or firm.get("telephone") or ""
        result.email = firm.get("email") or ""
        result.website = firm.get("website") or ""
        
        # Status
        status = firm.get("status") or ""
        result.status = status
        result.is_currently_authorised = "authorised" in status.lower() and "no longer" not in status.lower()
        
        result.authorised_since = firm.get("authorised_since") or firm.get("authorisation_date") or ""
        result.firm_type = firm.get("firm_type") or ""
        
        # Activities
        result.regulated_activities = firm.get("regulated_activities") or []
        result.trading_names = firm.get("trading_names") or []
        
        # Client money
        result.can_hold_client_money = firm.get("can_hold_client_money", False)
        result.can_control_client_money = firm.get("can_control_client_money", False)
        result.client_money_restrictions = firm.get("client_money_restrictions") or []
        result.requirements = firm.get("requirements") or []
        
        result.page_url = f"https://register.fca.org.uk/FirmDetailsPage?FRN={result.frn}"
        result.scraped_timestamp = datetime.now().isoformat()
        
        if result.found:
            log.info(f"[FCA API] ✅ Found: {result.firm_name} (FRN:{result.frn}, CH:{ch_number})")
        
        return result
    
    except requests.Timeout:
        log.error(f"[FCA API] Timeout for CH#{ch_number}")
        return result
    except Exception as e:
        log.error(f"[FCA API] Error for CH#{ch_number}: {e}")
        return result


def lookup_fca_firm_by_frn(frn: str, timeout: int = 15) -> FCAFirmDetails:
    """Query FCA API using FRN (Firm Reference Number)."""
    
    frn = str(frn).strip()
    if not frn:
        return FCAFirmDetails()
    
    result = FCAFirmDetails(frn=frn)
    api_key = get_fca_api_key()
    
    if not api_key:
        log.error("[FCA] No API key configured")
        return result
    
    try:
        # Try FRN-based endpoint
        api_urls = [
            f"https://api.fca.org.uk/v1/firms/{frn}",
            f"https://api.fca.org.uk/v1/firms/search?frn={frn}",
        ]
        
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "User-Agent": "HRCOB/4.0",
        }
        
        log.info(f"[FCA API] Looking up FRN{frn}")
        
        for url in api_urls:
            try:
                response = requests.get(
                    url,
                    headers=headers,
                    timeout=timeout,
                    verify=get_ssl_verify(),
                )
                
                if response.status_code == 200:
                    data = response.json()
                    firm = data.get("firm") or data.get("data") or data
                    
                    result.found = True
                    result.firm_name = firm.get("name") or ""
                    result.frn = frn
                    result.ch_number = firm.get("companies_house_number") or ""
                    result.status = firm.get("status") or ""
                    result.is_currently_authorised = "authorised" in result.status.lower()
                    result.page_url = f"https://register.fca.org.uk/FirmDetailsPage?FRN={frn}"
                    result.scraped_timestamp = datetime.now().isoformat()
                    
                    log.info(f"[FCA API] ✅ Found by FRN: {result.firm_name}")
                    return result
            except Exception as e:
                log.debug(f"[FCA API] {url} failed: {e}")
        
        return result
    
    except Exception as e:
        log.error(f"[FCA API] Error for FRN{frn}: {e}")
        return result


def get_fca_details_for_company(ch_data: dict, company_website: str = "") -> dict:
    """
    Production-ready integration: Get FCA details for a company.
    
    Input: Companies House data dict with company_number field
    Output: Dict with FCA regulation status and risk adjustment
    
    Args:
        ch_data: Companies House API response dict
        company_website: Optional company website URL
    
    Returns:
        Dict with FCA details ready for pipeline integration
    """
    
    ch_number = ch_data.get("company_number", "").strip()
    
    if not ch_number:
        return {
            "fca_found": False,
            "reason": "No Companies House number",
            "risk_reduction": 1.0,
        }
    
    # Query FCA API with CH number
    fca_firm = lookup_fca_firm_by_ch_number(ch_number)
    
    if not fca_firm.found:
        return {
            "fca_found": False,
            "reason": "Not found in FCA Register",
            "risk_reduction": 1.0,
            "ch_number": ch_number,
        }
    
    # Return comprehensive FCA details
    return {
        "fca_found": True,
        "frn": fca_firm.frn,
        "firm_name": fca_firm.firm_name,
        "status": fca_firm.status,
        "is_authorised": fca_firm.is_currently_authorised,
        "risk_reduction": fca_firm.risk_reduction_factor,
        "address": fca_firm.address,
        "phone": fca_firm.phone,
        "email": fca_firm.email,
        "website": fca_firm.website,
        "authorised_since": fca_firm.authorised_since,
        "regulated_activities": fca_firm.regulated_activities,
        "client_money_restrictions": fca_firm.client_money_restrictions,
        "can_hold_client_money": fca_firm.can_hold_client_money,
        "can_control_client_money": fca_firm.can_control_client_money,
        "requirements": fca_firm.requirements,
        "page_url": fca_firm.page_url,
        "ch_number": ch_number,
        "scraped_at": fca_firm.scraped_timestamp,
    }
