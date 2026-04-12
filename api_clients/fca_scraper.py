"""
api_clients/fca_scraper.py — Scrape FCA Register details by CH number.

Given a Companies House number, scrape the FCA Register page and extract:
- Firm Reference Number (FRN)
- Authorization status & date
- Regulated activities
- Address, phone, website
- Client money restrictions
- Requirements & suspensions

This is the PRACTICAL solution: lookup by CH# → scrape FCA page → extract data
"""

from __future__ import annotations

import re
from typing import Any, Optional
from datetime import datetime

import requests
from bs4 import BeautifulSoup
from pydantic import BaseModel, Field

from core.logging_config import get_logger
from config import get_ssl_verify

log = get_logger("api_clients.fca_scraper")


class FCAFirmDetails(BaseModel):
    """Scraped FCA Register data for a firm."""
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
    raw_html: str = ""

    @property
    def risk_reduction_factor(self) -> float:
        """Calculate risk reduction based on FCA status."""
        if not self.found:
            return 1.0
        if self.is_currently_authorised:
            return 0.75  # 25% reduction
        else:
            return 0.95  # 5% reduction (formerly regulated)


def scrape_fca_firm_by_ch_number(ch_number: str, timeout: int = 15) -> FCAFirmDetails:
    """Scrape FCA Register using Companies House number.
    
    This is the BEST approach because:
    1. FCA Register search supports CH number lookup (ftc=a)
    2. Each firm has a unique CH number → unique FCA page
    3. We extract all details from the actual FCA page
    
    Args:
        ch_number: Companies House registration number (e.g., "01925556")
        timeout: Request timeout in seconds
    
    Returns:
        FCAFirmDetails with all extracted data
    """
    
    ch_number = str(ch_number).strip()
    if not ch_number:
        return FCAFirmDetails()
    
    result = FCAFirmDetails(ch_number=ch_number)
    
    try:
        # Create session for cookie/state persistence
        session = requests.Session()
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36",
        }
        session.headers.update(headers)
        
        # Step 1: Get session first (initialize cookies)
        log.debug(f"[FCA] Initializing session...")
        base_url = "https://register.fca.org.uk/ShPo_PublicRegister/Search"
        try:
            session.get(base_url, timeout=5, verify=get_ssl_verify())
        except Exception as e:
            log.debug(f"[FCA] Session init warning: {e}")
        
        # Step 2: Search FCA Register by CH number
        search_url = "https://register.fca.org.uk/ShPo_PublicRegister/Search"
        params = {
            "ftc": "a",  # Search by Companies House number
            "sb": ch_number,
            "pg": 1,
        }
        
        log.info(f"[FCA] Searching for CH#{ch_number}")
        response = session.get(
            search_url,
            params=params,
            timeout=timeout,
            verify=get_ssl_verify(),
        )
        
        if response.status_code != 200:
            log.warning(f"[FCA] Search failed: {response.status_code}")
            return result
        
        # Step 3: Extract firm link from search results
        soup = BeautifulSoup(response.text, "html.parser")
        
        # Look for firm link in results
        firm_link = None
        for link in soup.find_all("a", href=True):
            href = link.get("href")
            if href and isinstance(href, str):
                if "/ShPo_PublicRegister/ViewFirm/" in href or "/register/" in href.lower():
                    firm_link = href
                    break
        
        if not firm_link:
            log.debug(f"[FCA] No firm found for CH#{ch_number}")
            return result
        
        # Make absolute URL if relative
        if isinstance(firm_link, str) and firm_link.startswith("/"):
            firm_link = "https://register.fca.org.uk" + firm_link
        
        if not isinstance(firm_link, str):
            log.debug(f"[FCA] Invalid firm link format")
            return result
        
        # Step 4: Scrape the firm's FCA Register page
        log.info(f"[FCA] Scraping firm details from: {firm_link}")
        firm_response = session.get(
            firm_link,
            timeout=timeout,
            verify=get_ssl_verify(),
        )
        
        if firm_response.status_code == 200:
            result.page_url = firm_response.url
            result.raw_html = firm_response.text
            _parse_fca_firm_page(result, firm_response.text)
            result.found = True
            result.scraped_timestamp = datetime.now().isoformat()
            
            if result.frn:
                log.info(f"[FCA] ✅ Found: {result.firm_name} (FRN:{result.frn}, CH:{ch_number})")
            else:
                log.warning(f"[FCA] Parsed but missing FRN for CH#{ch_number}")
        else:
            log.warning(f"[FCA] Firm page failed: {firm_response.status_code}")
        
        return result
    
    except requests.Timeout:
        log.error(f"[FCA] Timeout scraping CH#{ch_number}")
        return result
    except Exception as e:
        log.error(f"[FCA] Error scraping CH#{ch_number}: {e}")
        return result


def _parse_fca_firm_page(result: FCAFirmDetails, html: str) -> None:
    """Parse FCA firm page HTML and extract all details."""
    
    try:
        soup = BeautifulSoup(html, "html.parser")
        
        # Extract firm name (usually in h1 or title)
        h1 = soup.find("h1")
        if h1:
            result.firm_name = h1.get_text(strip=True)
        
        # Extract FRN: "Reference number: 718612"
        text = soup.get_text()
        frn_match = re.search(r"Reference number:\s*(\d+)", text, re.IGNORECASE)
        if frn_match:
            result.frn = frn_match.group(1)
        
        # Extract CH number from page: "Companies House number: 01925556"
        ch_match = re.search(r"Companies House number[^\d]*(\d+)", text, re.IGNORECASE)
        if ch_match:
            result.ch_number = ch_match.group(1)
        
        # Extract status: "Status: Authorised" or "Status: No longer authorised"
        status_match = re.search(r"Status\s*:\s*([^\n<]+)", text, re.IGNORECASE)
        if status_match:
            status_text = status_match.group(1).strip()
            result.status = status_text
            result.is_currently_authorised = "authorised" in status_text.lower() and "no longer" not in status_text.lower()
        
        # Extract authorised date: "Since 26/05/2016"
        auth_match = re.search(r"Since\s+(\d{2}/\d{2}/\d{4})", text, re.IGNORECASE)
        if auth_match:
            result.authorised_since = auth_match.group(1)
        
        # Extract address (look for pattern with postcode)
        addr_match = re.search(
            r"Address[^A-Z]*([A-Za-z\s\d,.\-]{20,200}?)(?=Phone|Email|Website|$)",
            text,
            re.IGNORECASE | re.DOTALL
        )
        if addr_match:
            addr = addr_match.group(1).strip()
            # Clean up (remove extra spaces/newlines)
            addr = " ".join(addr.split())
            result.address = addr[:200]  # Truncate
        
        # Extract phone
        phone_match = re.search(r"\+44\s*\d+[\d\s\-]{5,}", text)
        if phone_match:
            result.phone = phone_match.group(0).strip()
        
        # Extract email
        email_match = re.search(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}", text)
        if email_match:
            result.email = email_match.group(0)
        
        # Extract website
        website_match = re.search(r"www\.[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}", text)
        if website_match:
            result.website = website_match.group(0)
        
        # Extract trading names (look for "trading under X trading names")
        trading_match = re.search(r"trades under\s+(\d+)\s+trading names", text, re.IGNORECASE)
        if trading_match:
            # Would need to parse table, for now just note count
            count = trading_match.group(1)
            result.trading_names = [f"(Total: {count} trading names)"]
        
        # Extract regulated activities
        activities_section = re.search(
            r"Activities and services(.{0,2000}?)(?:Who is|$)",
            text,
            re.IGNORECASE | re.DOTALL
        )
        if activities_section:
            activity_text = activities_section.group(1)
            # Look for activity categories
            for activity in ["Consumer credit", "Investments", "Insurance", "Payments", "Pensions"]:
                if activity.lower() in activity_text.lower():
                    result.regulated_activities.append(activity)
        
        # Extract client money restrictions
        if "cannot hold" in text.lower() and "cannot control" in text.lower():
            result.can_hold_client_money = False
            result.can_control_client_money = False
            result.client_money_restrictions.append("Cannot hold or control client money")
        elif "control but not hold" in text.lower():
            result.can_hold_client_money = False
            result.can_control_client_money = True
            result.client_money_restrictions.append("Can control but cannot hold client money")
        
        # Extract firm type (Regulated, Exempt, etc.)
        type_match = re.search(r"Type\s*:\s*([^\n<]+)", text, re.IGNORECASE)
        if type_match:
            result.firm_type = type_match.group(1).strip()
        
        # Extract requirements/restrictions (look for requirement section)
        req_section = re.search(
            r"(?:Requirements|Restrictions)(.{0,1000}?)(?:What this|$)",
            text,
            re.IGNORECASE | re.DOTALL
        )
        if req_section:
            req_text = req_section.group(1)
            # Extract bullet points or individual requirements
            for req in re.findall(r"[-•]\s*(.{20,150}?)(?:\n|$)", req_text):
                req_clean = " ".join(req.split())[:100]
                if req_clean and len(req_clean) > 10:
                    result.requirements.append(req_clean)
    
    except Exception as e:
        log.error(f"Error parsing FCA page: {e}")


def check_fca_via_website_scrape(company_website: str) -> Optional[str]:
    """Try to find FCA FRN mentioned on company's own website.
    
    Some companies display their FCA registration on their website:
    - "FCA Regulated - FRN 123456"
    - "Regulated by FCA under FRN 123456"
    - etc.
    
    This is a bonus check that can confirm FCA status without scraping FCA site.
    """
    
    if not company_website:
        return None
    
    # Ensure URL has protocol
    if not company_website.startswith(("http://", "https://")):
        company_website = f"https://{company_website}"
    
    try:
        response = requests.get(
            company_website,
            timeout=10,
            verify=get_ssl_verify(),
            headers={"User-Agent": "Mozilla/5.0"}
        )
        
        if response.status_code == 200:
            text = response.text
            
            # Look for FCA references
            frn_match = re.search(r"FRN\s*:?\s*(\d{6,7})", text, re.IGNORECASE)
            if frn_match:
                frn = frn_match.group(1)
                log.info(f"[FCA] Found FRN {frn} mentioned on website")
                return frn
            
            # Look for "FCA Regulated" mentions
            if "fca" in text.lower() and ("regulated" in text.lower() or "authorised" in text.lower()):
                log.info(f"[FCA] Website mentions FCA regulation")
                return "website_confirms_fca"
    
    except Exception as e:
        log.debug(f"Could not scrape website: {e}")
    
    return None


# ═══════════════════════════════════════════════════════════════════════════════
# INTEGRATION: Add to Company Screening Pipeline
# ═══════════════════════════════════════════════════════════════════════════════

def get_fca_details_for_company(ch_data: dict, company_website: str = "") -> dict:
    """
    Get FCA details for a company - FULL integration.
    
    Combines:
    1. FCA Register scrape by CH number (most reliable)
    2. Website check (bonus confirmation)
    
    Args:
        ch_data: Companies House data dict with "company_number"
        company_website: Optional company website to check
    
    Returns:
        Dict with FCA details ready to add to pipeline state
    """
    
    ch_number = ch_data.get("company_number", "").strip()
    company_name = ch_data.get("company_name", "")
    
    if not ch_number:
        return {"fca_found": False, "reason": "No CH number"}
    
    # PRIMARY: Scrape FCA Register by CH number
    fca_details = scrape_fca_firm_by_ch_number(ch_number)
    
    if fca_details.found:
        result = {
            "fca_found": True,
            "frn": fca_details.frn,
            "firm_name": fca_details.firm_name,
            "status": fca_details.status,
            "is_authorised": fca_details.is_currently_authorised,
            "risk_reduction": fca_details.risk_reduction_factor,
            "address": fca_details.address,
            "phone": fca_details.phone,
            "email": fca_details.email,
            "website": fca_details.website,
            "authorised_since": fca_details.authorised_since,
            "regulated_activities": fca_details.regulated_activities,
            "client_money_restrictions": fca_details.client_money_restrictions,
            "can_hold_client_money": fca_details.can_hold_client_money,
            "can_control_client_money": fca_details.can_control_client_money,
            "requirements": fca_details.requirements,
            "page_url": fca_details.page_url,
            "source": "fca_register_scrape",
        }
        
        # BONUS: Check website for additional confirmation
        if company_website:
            website_frn = check_fca_via_website_scrape(company_website)
            if website_frn:
                result["website_confirmation"] = website_frn
        
        return result
    else:
        # Not found on FCA Register - try website as backup
        if company_website:
            website_frn = check_fca_via_website_scrape(company_website)
            if website_frn:
                return {
                    "fca_found": "website_mention_only",
                    "frn": website_frn if website_frn != "website_confirms_fca" else "",
                    "note": "Company mentions FCA regulation on website but not verified via FCA Register",
                    "source": "company_website",
                }
        
        return {"fca_found": False, "reason": f"Not found on FCA Register (CH#{ch_number})"}
