"""
Foreign Entity OSINT & Reputation Research
============================================

When UBO tracing hits a foreign entity, this module:
1. Searches news/media for the company
2. Performs OSINT on domain/website
3. Scores reputation based on findings
4. Identifies beneficial owners via web intelligence
"""

import os
import re
from typing import Optional
from api_clients.tavily_search import search_news, search_web
from api_clients.serper_api import search_serper

def research_foreign_entity(
    entity_name: str,
    country: str,
    registration_number: Optional[str] = None
) -> dict:
    """
    Research a foreign entity using OSINT.
    
    Returns dict with:
    - news_findings: Adverse media hits
    - web_intelligence: Website/domain analysis
    - reputation_score: 0-100
    - beneficial_owners: List of identified owners
    - risk_flags: List of red flags
    """
    
    findings = {
        "entity_name": entity_name,
        "country": country,
        "registration_number": registration_number,
        "news_findings": [],
        "web_intelligence": {},
        "reputation_score": 50,  # Default middle ground
        "beneficial_owners": [],
        "risk_flags": [],
        "osint_sources": []
    }
    
    # 1. News & Adverse Media Search
    findings["news_findings"] = _search_adverse_media(entity_name, country)
    
    # 2. Web OSINT
    findings["web_intelligence"] = _perform_web_osint(entity_name, country)
    
    # 3. Search for Beneficial Owners
    findings["beneficial_owners"] = _identify_beneficial_owners(entity_name, country, registration_number)
    
    # 4. Calculate Reputation Score
    findings["reputation_score"] = _calculate_reputation_score(findings)
    
    # 5. Flag Risk Indicators
    findings["risk_flags"] = _identify_risk_flags(findings)
    
    return findings


def _search_adverse_media(entity_name: str, country: str) -> list:
    """Search for adverse media, court records, sanctions."""
    
    news_findings = []
    
    try:
        # Search 1: Direct company name + country
        search_query = f'"{entity_name}" {country} -news +scandal OR fraud OR court OR lawsuit OR sanction OR investigation'
        results = search_news(search_query, max_results=5)
        
        if results:
            for result in results:
                news_findings.append({
                    "headline": result.get("title", ""),
                    "source": result.get("source", ""),
                    "date": result.get("date", ""),
                    "url": result.get("url", ""),
                    "snippet": result.get("snippet", ""),
                    "sentiment": _analyze_sentiment(result.get("snippet", "")),
                    "severity": _assess_severity(result.get("title", ""))
                })
        
        # Search 2: Registration number if available
        if entity_name:
            reg_query = f'{entity_name} company OR corporation OR enterprise registration'
            reg_results = search_web(reg_query, max_results=3)
            news_findings.extend(reg_results)
    
    except Exception as e:
        print(f"⚠️ Adverse media search failed: {e}")
    
    return news_findings


def _perform_web_osint(entity_name: str, country: str) -> dict:
    """OSINT on website, domain, social media presence."""
    
    web_intel = {
        "domain_found": False,
        "website_credibility": 0,
        "social_media_presence": [],
        "email_domain": None,
        "years_in_business": None,
        "domain_registration_age": None,
        "https_valid": False,
        "trust_signals": [],
        "red_flags": []
    }
    
    try:
        # Search for official website
        domain_search = f'{entity_name} official website {country}'
        domain_results = search_serper(domain_search, max_results=3)
        
        if domain_results:
            for result in domain_results:
                url = result.get("link", "")
                if url and not "wikipedia" in url:
                    web_intel["domain_found"] = True
                    web_intel["domain_url"] = url
                    
                    # Extract domain
                    domain_match = re.search(r'https?://(?:www\.)?([a-zA-Z0-9.-]+)', url)
                    if domain_match:
                        web_intel["domain"] = domain_match.group(1)
                    
                    # Check for HTTPS
                    web_intel["https_valid"] = url.startswith("https://")
                    
                    # Credibility scoring
                    web_intel["website_credibility"] = _score_domain_credibility(url, result.get("snippet", ""))
        
        # Search for social media presence
        social_query = f'{entity_name} linkedin OR twitter OR facebook site:linkedin.com OR site:twitter.com'
        social_results = search_serper(social_query, max_results=5)
        
        if social_results:
            web_intel["social_media_presence"] = [
                {
                    "platform": _extract_platform(r.get("link", "")),
                    "url": r.get("link", ""),
                    "description": r.get("snippet", "")
                }
                for r in social_results
            ]
    
    except Exception as e:
        print(f"⚠️ Web OSINT failed: {e}")
    
    return web_intel


def _identify_beneficial_owners(
    entity_name: str,
    country: str,
    registration_number: Optional[str] = None
) -> list:
    """Try to identify beneficial owners of foreign entity via web intelligence."""
    
    owners = []
    
    try:
        # Search for management/leadership information
        mgmt_query = f'{entity_name} {country} CEO OR founder OR managing director OR director OR owner'
        mgmt_results = search_serper(mgmt_query, max_results=5)
        
        for result in mgmt_results:
            snippet = result.get("snippet", "")
            
            # Extract names (simple pattern matching)
            name_patterns = re.findall(r'(?:CEO|Founder|Director|Managing Director|Owner)[:\s]+([A-Z][a-z]+ [A-Z][a-z]+)', snippet)
            
            for name in name_patterns:
                if name not in [o.get("name") for o in owners]:
                    owners.append({
                        "name": name,
                        "role": _extract_role(snippet),
                        "source": "Web Intelligence",
                        "confidence": "Low"
                    })
    
    except Exception as e:
        print(f"⚠️ Beneficial owner identification failed: {e}")
    
    return owners


def _calculate_reputation_score(findings: dict) -> int:
    """
    Calculate 0-100 reputation score based on findings.
    Higher = better reputation.
    """
    
    score = 50  # Start neutral
    
    # Negative factors
    news = findings.get("news_findings", [])
    if news:
        adverse_count = len([n for n in news if _analyze_sentiment(n.get("headline", "")) == "negative"])
        score -= min(adverse_count * 10, 40)  # Max -40
    
    # Positive factors
    web_intel = findings.get("web_intelligence", {})
    if web_intel.get("domain_found"):
        score += 15
    if web_intel.get("https_valid"):
        score += 5
    if len(web_intel.get("social_media_presence", [])) > 0:
        score += 10
    
    # Owner information
    if findings.get("beneficial_owners"):
        score += 10
    
    return max(0, min(100, score))


def _identify_risk_flags(findings: dict) -> list:
    """Identify specific risk indicators."""
    
    flags = []
    
    # Adverse media risk
    news = findings.get("news_findings", [])
    for article in news:
        if article.get("sentiment") == "negative":
            flags.append({
                "type": "Adverse Media",
                "severity": article.get("severity", "Medium"),
                "detail": article.get("headline", "")
            })
    
    # Opacity flags
    if not findings.get("web_intelligence", {}).get("domain_found"):
        flags.append({
            "type": "Opacity",
            "severity": "Medium",
            "detail": "No official website found"
        })
    
    if not findings.get("beneficial_owners"):
        flags.append({
            "type": "Ownership Opacity",
            "severity": "High",
            "detail": "Unable to identify beneficial owners"
        })
    
    # Domain age/credibility
    web_intel = findings.get("web_intelligence", {})
    if web_intel.get("website_credibility", 0) < 40:
        flags.append({
            "type": "Low Website Credibility",
            "severity": "Medium",
            "detail": "Website shows low credibility signals"
        })
    
    return flags


# Helper functions
def _analyze_sentiment(text: str) -> str:
    """Simple sentiment analysis."""
    negative_words = ['fraud', 'scandal', 'lawsuit', 'investigation', 'sanction', 'ban', 'default']
    positive_words = ['registered', 'certified', 'reputable', 'established']
    
    text_lower = text.lower()
    
    neg_count = sum(1 for word in negative_words if word in text_lower)
    pos_count = sum(1 for word in positive_words if word in text_lower)
    
    if neg_count > pos_count:
        return "negative"
    elif pos_count > neg_count:
        return "positive"
    else:
        return "neutral"


def _assess_severity(headline: str) -> str:
    """Assess severity of adverse finding."""
    high_severity = ['fraud', 'sanction', 'criminal', 'collapse', 'bankruptcy']
    medium_severity = ['investigation', 'lawsuit', 'fine', 'warning']
    
    headline_lower = headline.lower()
    
    if any(word in headline_lower for word in high_severity):
        return "High"
    elif any(word in headline_lower for word in medium_severity):
        return "Medium"
    else:
        return "Low"


def _score_domain_credibility(url: str, snippet: str) -> int:
    """Score domain credibility 0-100."""
    score = 50
    
    if url.startswith("https://"):
        score += 10
    
    if "official" in snippet.lower() or "main website" in snippet.lower():
        score += 20
    
    if any(tld in url for tld in ['.com', '.org', '.co.uk']):
        score += 5
    
    return min(100, score)


def _extract_platform(url: str) -> str:
    """Extract social media platform name from URL."""
    if "linkedin" in url:
        return "LinkedIn"
    elif "twitter" in url or "x.com" in url:
        return "Twitter/X"
    elif "facebook" in url:
        return "Facebook"
    elif "instagram" in url:
        return "Instagram"
    else:
        return "Social Media"


def _extract_role(snippet: str) -> str:
    """Extract role from snippet."""
    roles = ['CEO', 'Founder', 'Director', 'Managing Director', 'Owner', 'Chairman']
    for role in roles:
        if role.lower() in snippet.lower():
            return role
    return "Executive"
