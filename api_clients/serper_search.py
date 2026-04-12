"""
api_clients/serper_search.py — Google News adverse media search via Serper.dev.

Serper.dev provides a Google Search API that returns structured Google News
results.  This module uses it as an **alternative / supplement** to Tavily
for adverse media screening.  Google News is especially good at surfacing
recent regulatory enforcement, prosecution, and scandal articles.

Public API
----------
serper_search_news(query, max_results=10) → list[dict]
    Raw Google News search via Serper.

search_adverse_media_serper(name, context_terms=None) → list[dict]
    Multi-query adverse media screening through Google News.
    Returns results in the same shape as Tavily so they can be merged.
"""

from __future__ import annotations

import os
import re

import requests

from config import SERPER_API_KEY, get_ssl_verify

_SERPER_ENDPOINT = "https://google.serper.dev/news"

# ─── Adverse keywords (shared with tavily_search.py) ─────────────────────────
ADVERSE_KEYWORDS = {
    "money launder", "sanction", "corrupt", "bribe", "criminal",
    "crime", "illicit", "terror", "fraud", "scam", "found guilty",
    "prosecut", "convict", "investigat", "alleged", "charged",
    "arrest", "seized", "frozen", "penalty", "fine", "banned",
}

# ─── FCA-Specific Risk Keywords ───────────────────────────────────────────────
# Enhanced keywords for FCA-regulated financial services entities
FCA_RISK_KEYWORDS = {
    # AML & Compliance
    "aml", "anti-money laundering", "aml breach", "aml failure",
    "suspicious activity report", "sar filed", "aml investigation",
    "aml violation", "compliance failure", "compliance breach",
    
    # Market Abuse & Trading
    "market abuse", "insider trading", "price manipulation",
    "pump and dump", "wash trading", "spoofing", "layering",
    "manipulative practice", "market conduct",
    
    # Regulatory Actions
    "fca fine", "fca sanction", "fca warning", "fca enforcement",
    "fca investigation", "regulatory action", "suspended", "revoked",
    "license revoked", "license suspended", "enforcement action",
    
    # Client Funds & Mismanagement
    "client funds", "client money", "segregated account", "fund mismanagement",
    "misuse of client", "improper handling", "client assets",
    "trustee breach", "fiduciary duty", "client protection",
    
    # Financial Crime
    "financial crime", "embezzlement", "misappropriation",
    "ponzi", "pyramid scheme", "affinity fraud", "investment fraud",
    "securities fraud", "stolen funds",
}

_COMPANY_NOISE = {
    "the", "and", "of", "for", "in", "a", "an", "at", "on", "by",
    "ltd", "limited", "plc", "inc", "corp", "corporation", "co",
    "llp", "lp", "uk", "group", "holdings", "services", "solutions",
    "consulting", "international", "global", "company",
}


def serper_search_news(
    query: str,
    *,
    max_results: int = 10,
    country: str = "gb",
) -> list[dict]:
    """Execute a Google News search via Serper.dev.

    Returns a list of dicts with keys: title, url, content (snippet), date.
    Gracefully returns [] if the API key is missing or the request fails.
    """
    if not SERPER_API_KEY:
        return []

    headers = {
        "X-API-KEY": SERPER_API_KEY,
        "Content-Type": "application/json",
    }
    payload = {
        "q": query,
        "gl": country,
        "num": max_results,
    }

    try:
        resp = requests.post(
            _SERPER_ENDPOINT,
            json=payload,
            headers=headers,
            timeout=20,
            verify=get_ssl_verify(),
        )
        resp.raise_for_status()
        data = resp.json()

        results = []
        for item in data.get("news", []):
            results.append({
                "title": item.get("title", ""),
                "url": item.get("link", ""),
                "content": item.get("snippet", ""),
                "date": item.get("date", ""),
                "_source": "serper_news",
            })
        return results

    except Exception as e:
        return [{
            "title": "Serper News search unavailable",
            "url": "",
            "content": str(e),
            "_error": True,
            "_relevant": False,
            "_source": "serper_news",
        }]


def serper_search_web(
    query: str,
    *,
    max_results: int = 10,
    country: str = "gb",
) -> list[dict]:
    """Execute a Google Web search via Serper.dev.

    Returns results in the same shape as Tavily for compatibility.
    """
    if not SERPER_API_KEY:
        return []

    headers = {
        "X-API-KEY": SERPER_API_KEY,
        "Content-Type": "application/json",
    }
    payload = {
        "q": query,
        "gl": country,
        "num": max_results,
    }

    try:
        resp = requests.post(
            "https://google.serper.dev/search",
            json=payload,
            headers=headers,
            timeout=20,
            verify=get_ssl_verify(),
        )
        resp.raise_for_status()
        data = resp.json()

        results = []
        for item in data.get("organic", []):
            results.append({
                "title": item.get("title", ""),
                "url": item.get("link", ""),
                "content": item.get("snippet", ""),
                "_source": "serper_web",
            })
        return results

    except Exception as e:
        return [{
            "title": "Serper Web search unavailable",
            "url": "",
            "content": str(e),
            "_error": True,
            "_relevant": False,
            "_source": "serper_web",
        }]


def _is_relevant_adverse_serper(result: dict, name: str, is_fca_regulated: bool = False) -> bool:
    """Check if a Serper result is a TRUE adverse hit for this entity.

    Same two-gate filter as Tavily:
    1. Entity's distinctive name must appear in content/title.
    2. At least one adverse keyword must also appear (FCA-specific keywords
       for regulated entities).
    """
    if result.get("_error"):
        return False

    content = (
        (result.get("content") or "")
        + " " + (result.get("title") or "")
    ).lower()

    # ── Gate 1 — entity name must genuinely appear ────────────────────
    clean_name = name.strip().lower()

    # Fast path: exact full name
    if clean_name in content:
        keywords = FCA_RISK_KEYWORDS if is_fca_regulated else ADVERSE_KEYWORDS
        return any(kw in content for kw in keywords)

    # Try without suffix
    for suffix in (" ltd", " limited", " plc", " inc", " llp", " lp"):
        if clean_name.endswith(suffix):
            short_name = clean_name[: -len(suffix)].strip()
            if short_name and short_name in content:
                keywords = FCA_RISK_KEYWORDS if is_fca_regulated else ADVERSE_KEYWORDS
                return any(kw in content for kw in keywords)

    # Fallback: ALL distinctive tokens must be present
    tokens = [p.lower() for p in name.split() if len(p) > 1]
    distinctive = [t for t in tokens if t not in _COMPANY_NOISE]

    if not distinctive:
        return False

    all_match = all(
        re.search(r"\b" + re.escape(tok) + r"\b", content)
        for tok in distinctive
    )
    if not all_match:
        return False

    # ── Gate 2 — adverse keyword ──────────────────────────────────────
    keywords = FCA_RISK_KEYWORDS if is_fca_regulated else ADVERSE_KEYWORDS
    return any(kw in content for kw in keywords)


def search_adverse_media_serper(
    name: str,
    context_terms: list[str] | None = None,
    is_fca_regulated: bool = False,
) -> list[dict]:
    """Search for adverse media via Google News (Serper.dev).

    Runs multiple queries to maximise coverage:
    1. Name + adverse crime terms (Google News)
    2. Name + regulatory/enforcement terms (Google News)
    3. Name + FCA-specific terms if is_fca_regulated=True (Google News)
    4. Name + context terms if provided (Google News)

    For FCA-regulated entities, includes additional searches for AML,
    market abuse, client funds mismanagement, and regulatory sanctions.

    Returns results in the same shape as Tavily's search_adverse_media()
    so they can be directly merged.
    """
    all_results: list[dict] = []
    seen_urls: set[str] = set()

    def _add(items: list[dict]) -> None:
        for r in items:
            url = r.get("url", "")
            if url and url not in seen_urls:
                seen_urls.add(url)
                all_results.append(r)

    # Query 1: Name + adverse crime keywords (Google News)
    q1 = f'"{name}" fraud OR corruption OR sanctions OR "money laundering" OR prosecution'
    _add(serper_search_news(q1, max_results=10))

    # Query 2: Name + regulatory / enforcement
    q2 = (
        f'"{name}" FCA OR "Serious Fraud Office" OR "National Crime Agency" '
        f'OR HMRC OR "Companies House" OR disqualified OR struck off OR fine'
    )
    _add(serper_search_news(q2, max_results=5))

    # ─── ENHANCED FCA SEARCH FOR REGULATED ENTITIES ─────────────────────
    if is_fca_regulated:
        # AML Compliance concerns
        q_aml = f'"{name}" AML OR "anti-money laundering" OR "suspicious activity report" OR "aml breach"'
        _add(serper_search_news(q_aml, max_results=5))
        
        # Market Abuse / Trading Conduct
        q_market = f'"{name}" "market abuse" OR "insider trading" OR "price manipulation" OR "wash trading"'
        _add(serper_search_news(q_market, max_results=5))
        
        # Client Funds & Fiduciary Issues
        q_funds = f'"{name}" "client funds" OR "segregated account" OR "fund mismanagement" OR "fiduciary"'
        _add(serper_search_news(q_funds, max_results=5))
        
        # FCA Specific Enforcement
        q_fca = f'"{name}" FCA fine OR "fca sanction" OR "fca investigation" OR "license revoked"'
        _add(serper_search_news(q_fca, max_results=5))
        
        # Regulatory Compliance Failures
        q_compliance = f'"{name}" "compliance failure" OR "governance failure" OR "compliance officer" OR "risk management"'
        _add(serper_search_news(q_compliance, max_results=5))

    # Query 3: Name + context (e.g. company name for director searches)
    if context_terms:
        ctx = " ".join(context_terms[:3])
        q3 = f'"{name}" {ctx} fraud OR scandal OR investigation'
        _add(serper_search_news(q3, max_results=5))

    # Tag each result with relevance
    for r in all_results:
        r["_relevant"] = _is_relevant_adverse_serper(r, name, is_fca_regulated)

    return all_results
