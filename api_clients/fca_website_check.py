"""
api_clients/fca_website_check.py — Check if company mentions FCA on their website.

Simple approach: Given a company's website, check if they mention FCA regulation.
This is PRACTICAL and DIRECT.
"""

import re
import requests
from typing import Optional
from pydantic import BaseModel

from core.logging_config import get_logger
from config import get_ssl_verify

log = get_logger("api_clients.fca_website_check")


class FCAWebsiteCheck(BaseModel):
    """Result of checking company website for FCA mentions."""
    found_fca_mention: bool = False
    frn: str = ""  # Extract FCA FRN if mentioned
    firm_name: str = ""
    mentions: list[str] = []  # FCA mentions found on page
    website_url: str = ""
    risk_reduction_factor: float = 0.75  # If FCA found, 25% reduction


def check_company_website_for_fca(website_url: str, company_name: str = "") -> FCAWebsiteCheck:
    """
    Check if company website mentions FCA regulation.
    
    Companies often display FCA registration like:
    - "FCA Regulated - FRN 123456"
    - "Regulated by FCA"
    - "Financial Conduct Authority"
    - "FSCS Protection"
    
    Args:
        website_url: Company website URL
        company_name: Optional company name for context
    
    Returns:
        FCAWebsiteCheck with findings
    """
    
    result = FCAWebsiteCheck(website_url=website_url, firm_name=company_name)
    
    if not website_url:
        return result
    
    # Ensure URL has protocol
    if not website_url.startswith(("http://", "https://")):
        website_url = f"https://{website_url}"
    
    try:
        log.info(f"[FCA Check] Checking website: {website_url}")
        
        response = requests.get(
            website_url,
            timeout=10,
            verify=get_ssl_verify(),
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"},
            allow_redirects=True,
        )
        
        if response.status_code != 200:
            log.debug(f"[FCA Check] Website returned {response.status_code}")
            return result
        
        # Get text content
        html = response.text.lower()
        
        # Look for FCA mentions
        fca_patterns = [
            r"fca\s+regulated",
            r"regulated\s+by\s+fca",
            r"financial\s+conduct\s+authority",
            r"fca\s+firm\s+reference",
            r"frn\s*:\s*(\d{6,7})",
            r"firm\s+reference\s+number\s*:\s*(\d{6,7})",
            r"fca\s+registration",
            r"fscs\s+protection",
            r"fca\s+authorised",
            r"fca\s+authorised\s+firm",
        ]
        
        mentions_found = []
        frn_found = ""
        
        for pattern in fca_patterns:
            matches = re.finditer(pattern, html)
            for match in matches:
                # Extract full context (50 chars before and after)
                start = max(0, match.start() - 50)
                end = min(len(html), match.end() + 50)
                context = html[start:end].strip()
                mentions_found.append(context)
                
                # Try to extract FRN if pattern has group
                if match.groups():
                    frn = match.group(1)
                    if frn and not frn_found:
                        frn_found = frn
        
        if mentions_found:
            result.found_fca_mention = True
            result.mentions = mentions_found
            if frn_found:
                result.frn = frn_found
            log.info(f"[FCA Check] ✅ Found FCA mention on website")
            return result
        
        log.debug(f"[FCA Check] No FCA mention found on website")
        return result
    
    except requests.Timeout:
        log.debug(f"[FCA Check] Timeout checking website")
        return result
    except Exception as e:
        log.debug(f"[FCA Check] Error: {e}")
        return result


def is_fca_regulated_industry(sic_codes: list[str] | None = None, industry_category: str = "") -> bool:
    """
    Check if company's industry requires FCA regulation.
    
    FCA regulates these SIC divisions and codes:
    
    1. Banking & Lending (64110, 64191, 64192, 64921, 64922)
       - Central banking, retail banks, building societies, credit grantors, mortgage finance
    
    2. Insurance (65110, 65120, 65201, 65202, 65300)
       - Life insurance, non-life insurance, reinsurance, pension funding
    
    3. Investments & Pensions (64301-64304, 66300)
       - Investment trusts, unit trusts, venture capital, OEICs, fund management
    
    4. Auxiliary Activities (66110, 66120, 66190, 66210, 66220, 66290)
       - Financial market administration, brokerage, insurance agents/brokers
    
    5. Other Financial Services (64910, 64991, 64992)
       - Financial leasing, security dealing, factoring
    
    Args:
        sic_codes: List of SIC codes from Companies House
        industry_category: Industry category string (from CH data)
    
    Returns:
        True if company's industry is FCA-regulated
    """
    
    # Exact FCA-regulated SIC codes
    fca_regulated_sic_codes = {
        # Banking & Lending
        "64110",  # Central banking
        "64191",  # Banks
        "64192",  # Building societies
        "64921",  # Credit granting by non-deposit taking finance houses
        "64922",  # Activities of mortgage finance companies
        
        # Insurance
        "65110",  # Life insurance
        "65120",  # Non-life insurance
        "65201",  # Life reinsurance
        "65202",  # Non-life reinsurance
        "65300",  # Pension funding
        
        # Investments & Pensions
        "64301",  # Activities of investment trusts
        "64302",  # Activities of unit trusts
        "64303",  # Venture and development capital companies
        "64304",  # Open-ended investment companies (OEICs)
        "66300",  # Fund management activities
        
        # Auxiliary Activities
        "66110",  # Administration of financial markets
        "66120",  # Security and commodity contracts brokerage
        "66190",  # Other activities auxiliary to financial services
        "66210",  # Risk and damage evaluation
        "66220",  # Activities of insurance agents and brokers
        "66290",  # Other activities auxiliary to insurance and pension funding
        
        # Other Financial Services
        "64910",  # Financial leasing
        "64991",  # Security dealing on own account
        "64992",  # Factoring
    }
    
    # Check SIC codes
    if sic_codes:
        for code in sic_codes:
            code_str = str(code).strip()
            # Check exact match
            if code_str in fca_regulated_sic_codes:
                return True
            # Check 4-digit prefix match for flexibility
            if code_str[:4] in fca_regulated_sic_codes:
                return True
    
    # Fallback to keyword matching if SIC not available
    if industry_category:
        industry_lower = industry_category.lower()
        fca_keywords = [
            "bank",
            "building society",
            "credit union",
            "mortgage",
            "lending",
            "investment",
            "pension",
            "insurance",
            "broker",
            "fund management",
            "financial services",
            "financial leasing",
            "factoring",
        ]
        for keyword in fca_keywords:
            if keyword in industry_lower:
                return True
    
    return False


def get_fca_status_for_company(company_data: dict, website_url: str = "", sic_codes: list[str] | None = None, industry_category: str = "") -> dict:
    """
    Get FCA status for a company by checking their website.
    
    ONLY applies FCA reduction if company is in a regulated industry.
    
    Input: Company data from Companies House + industry info
    Output: FCA regulation status dict ready for pipeline
    
    Args:
        company_data: Companies House company data
        website_url: Company website URL (from CH data or user input)
        sic_codes: SIC codes from Companies House
        industry_category: Industry category (from CH data)
    
    Returns:
        Dict with FCA status and risk adjustment (only if regulated industry)
    """
    
    company_name = company_data.get("company_name", "")
    ch_number = company_data.get("company_number", "")
    
    # Get SIC codes and industry from company_data if not provided
    if not sic_codes and "sic_codes" in company_data:
        sic_codes = company_data.get("sic_codes", [])
    if not industry_category and "industry_category" in company_data:
        industry_category = company_data.get("industry_category", "")
    
    # Check if company is in an FCA-regulated industry
    if not is_fca_regulated_industry(sic_codes, industry_category):
        log.debug(f"[FCA] {company_name or ch_number} is not in FCA-regulated industry - skipping FCA check")
        return {
            "fca_found": False,
            "reason": "Not in FCA-regulated industry",
            "risk_reduction": 1.0,
            "ch_number": ch_number,
            "industry_not_regulated": True,
        }
    
    # Use provided website or try to extract from company data
    if not website_url and "website" in company_data:
        website_url = company_data.get("website", "")
    
    if not website_url:
        log.debug(f"[FCA] No website URL for {company_name or ch_number}")
        return {
            "fca_found": False,
            "reason": "No website to check",
            "risk_reduction": 1.0,
            "ch_number": ch_number,
            "industry_regulated": True,
        }
    
    # Check website for FCA mention (only if regulated industry)
    fca_check = check_company_website_for_fca(website_url, company_name)
    
    if fca_check.found_fca_mention:
        log.info(f"[FCA] ✅ Found FCA mention for {company_name} (regulated industry)")
        return {
            "fca_found": True,
            "firm_name": company_name,
            "frn": fca_check.frn,
            "mentions": fca_check.mentions,
            "risk_reduction": fca_check.risk_reduction_factor,
            "source": "company_website",
            "ch_number": ch_number,
            "industry_regulated": True,
        }
    else:
        log.debug(f"[FCA] No FCA mention for {company_name} (but in regulated industry)")
        return {
            "fca_found": False,
            "reason": "No FCA mention on company website (but industry is regulated)",
            "risk_reduction": 1.0,
            "ch_number": ch_number,
            "industry_regulated": True,
        }


# ═════════════════════════════════════════════════════════════════════════════
# TEST
# ═════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    
    print("\n" + "="*80)
    print("TEST: Check company websites for FCA mentions")
    print("="*80)
    
    # Test with some real websites (if you know FCA-regulated companies)
    test_websites = [
        ("https://www.barclays.co.uk", "Barclays Bank"),
        ("https://www.hsbc.co.uk", "HSBC"),
        ("https://www.lloydsbanking.com", "Lloyds"),
    ]
    
    for website, company_name in test_websites:
        print(f"\nChecking: {company_name}")
        print(f"  URL: {website}")
        
        result = check_company_website_for_fca(website, company_name)
        
        print(f"  FCA Found: {result.found_fca_mention}")
        if result.found_fca_mention:
            print(f"  FRN: {result.frn}")
            print(f"  Risk Reduction: {result.risk_reduction_factor:.2f}x")
            if result.mentions:
                print(f"  Mentions: {result.mentions[0][:100]}...")
    
    print("\n" + "="*80)
