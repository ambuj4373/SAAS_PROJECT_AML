"""
api_clients/tavily_search.py — V4 Intelligence-Grade Web Search & Adverse Media.

Powered by Tavily + Serper with multi-layer intelligence:
  • Source credibility scoring (government > major media > trade > blog)
  • Temporal decay weighting (recent articles matter more)
  • Negation-aware keyword detection (avoids false positives)
  • Severity classification per adverse hit
  • Entity disambiguation with distinctive-name filtering
"""

import re
import os
import math
from datetime import datetime, timezone
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

from config import tavily, get_ssl_verify

# ─── TAVILY SEARCH HELPERS ──────────────────────────────────────────────────

# Adverse crime terms — separated from sanctioned jurisdictions to avoid
# false positives for charities that legitimately operate in those areas.
ADVERSE_CRIME_TERMS = (
    '"Money Launder" OR "sanction" OR "corrupt" OR "bribe" OR "criminal" '
    'OR "crime" OR "illicit" OR "terror" OR "fraud" OR "scam" OR "found guilty"'
)
# Sanctioned jurisdiction terms — used only in a separate query to avoid
# conflating humanitarian mentions with adverse media.
SANCTIONED_JURISDICTIONS = (
    '"Iran" OR "Syria" OR "Crimea" OR "North Korea" OR "DPRK" '
    'OR "Cuba" OR "Belarus" OR "Donetsk" OR "Luhansk"'
)
ADVERSE_TERMS = ADVERSE_CRIME_TERMS  # backward compat — primary queries use crime terms only


def tavily_search(query, depth="advanced", max_results=5):
    if tavily is None:
        return []
    try:
        res = tavily.search(query=query, search_depth=depth, max_results=max_results)
        return res.get("results", [])
    except Exception as e:
        return [{"title": "Search unavailable", "url": "", "content": str(e),
                 "_error": True, "_relevant": False}]


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

# ═════════════════════════════════════════════════════════════════════════════
# V4: SOURCE CREDIBILITY ENGINE
# ═════════════════════════════════════════════════════════════════════════════
# Tiered domain credibility — higher-tier sources carry more weight.

_SOURCE_TIERS: dict[str, float] = {}

# Tier 1: Government / Regulatory (credibility 1.0)
for _d in [
    "gov.uk", "fca.org.uk", "charitycommission.gov.uk", "ico.org.uk",
    "parliament.uk", "judiciary.uk", "sfo.gov.uk", "nca.gov.uk",
    "ofac.treasury.gov", "justice.gov", "sec.gov", "interpol.int",
    "sanctions.un.org", "europa.eu", "fatf-gafi.org",
    "companieshouse.gov.uk", "hmrc.gov.uk",
]:
    _SOURCE_TIERS[_d] = 1.0

# Tier 2: Major international media (credibility 0.90)
for _d in [
    "bbc.co.uk", "bbc.com", "reuters.com", "theguardian.com",
    "ft.com", "thetimes.co.uk", "telegraph.co.uk", "independent.co.uk",
    "nytimes.com", "washingtonpost.com", "bloomberg.com", "wsj.com",
    "apnews.com", "aljazeera.com", "sky.com", "channel4.com",
    "economist.com", "forbes.com",
]:
    _SOURCE_TIERS[_d] = 0.90

# Tier 3: Trade / industry (credibility 0.75)
for _d in [
    "civilsociety.co.uk", "thirdsector.co.uk", "charitytoday.co.uk",
    "accountancyage.com", "lawgazette.co.uk", "complianceweek.com",
    "riskscreen.com", "lexology.com", "moneylaunderingnews.com",
    "internationalinvestment.net", "finextra.com",
]:
    _SOURCE_TIERS[_d] = 0.75

# Tier 4: Regional / local media (credibility 0.60)
for _d in [
    "manchestereveningnews.co.uk", "birminghammail.co.uk",
    "liverpoolecho.co.uk", "walesonline.co.uk", "scotsman.com",
    "yorkshirepost.co.uk", "eveningstandard.co.uk", "mirror.co.uk",
    "dailymail.co.uk", "express.co.uk", "metro.co.uk", "sun.co.uk",
    "standard.co.uk", "itv.com",
]:
    _SOURCE_TIERS[_d] = 0.60

_DEFAULT_SOURCE_CREDIBILITY = 0.40  # Unknown / blog / forum


def _get_source_credibility(url: str) -> tuple[float, str]:
    """Return (credibility_score, tier_label) for a URL."""
    if not url:
        return _DEFAULT_SOURCE_CREDIBILITY, "unknown"
    try:
        domain = urlparse(url).netloc.lower().lstrip("www.")
    except Exception:
        return _DEFAULT_SOURCE_CREDIBILITY, "unknown"

    for known_domain, score in _SOURCE_TIERS.items():
        if domain == known_domain or domain.endswith("." + known_domain):
            if score >= 0.95:
                return score, "government"
            if score >= 0.85:
                return score, "major_media"
            if score >= 0.70:
                return score, "trade_media"
            return score, "regional_media"
    return _DEFAULT_SOURCE_CREDIBILITY, "unknown"


# ═════════════════════════════════════════════════════════════════════════════
# V4: TEMPORAL DECAY & SEVERITY CLASSIFICATION
# ═════════════════════════════════════════════════════════════════════════════

_SEVERITY_KEYWORDS = {
    "critical": [
        "convicted", "found guilty", "sentenced", "jailed", "imprisoned",
        "terrorism", "terror financing", "money laundering conviction",
        "sanctioned", "designated", "asset freeze",
    ],
    "high": [
        "charged", "arrested", "indicted", "prosecuted", "seized",
        "fraud", "bribery", "corruption", "money launder",
        "criminal investigation", "serious fraud office",
    ],
    "medium": [
        "investigation", "inquiry", "alleged", "accused", "suspected",
        "fine", "penalty", "regulatory action", "warning", "banned",
        "disqualified", "enforcement",
    ],
    "low": [
        "complaint", "concern", "review", "audit", "dispute",
        "criticism", "controversy",
    ],
}

# Negation phrases that invalidate an adverse keyword hit
_NEGATION_PHRASES = re.compile(
    r"\b(not\s+(?:guilty|convicted|charged|involved|associated|linked)|"
    r"no\s+(?:evidence|link|connection|involvement)|"
    r"cleared\s+of|acquitted|exonerated|dismissed|withdrawn|dropped|overturned|innocent)",
    re.IGNORECASE,
)


def _extract_date_from_result(result: dict) -> datetime | None:
    """Try to parse a date from a search result."""
    for field in ("published_date", "date", "publishedDate"):
        raw = result.get(field, "")
        if not raw:
            continue
        for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d", "%d/%m/%Y", "%B %d, %Y"):
            try:
                return datetime.strptime(raw[:19], fmt).replace(tzinfo=timezone.utc)
            except (ValueError, TypeError):
                continue
    return None


def _temporal_decay(result: dict, half_life_years: float = 3.0) -> float:
    """Exponential decay weight: recent articles count more.

    Half-life of 3 years: a 3-year-old article has 50% weight,
    6-year-old has 25%, etc.  Returns 0.10..1.0.
    """
    pub_date = _extract_date_from_result(result)
    if not pub_date:
        return 0.70  # unknown date → moderate weight
    now = datetime.now(timezone.utc)
    age_years = (now - pub_date).days / 365.25
    if age_years <= 0:
        return 1.0
    decay = math.exp(-0.693 * age_years / half_life_years)  # ln(2) ≈ 0.693
    return max(0.10, round(decay, 2))


def _classify_severity(content: str) -> tuple[str, list[str]]:
    """Classify adverse media severity from content text.

    Returns (severity_level, matched_terms).
    """
    content_lower = content.lower()

    # Check for negation — if the content is primarily about being cleared,
    # downgrade severity
    negations = _NEGATION_PHRASES.findall(content_lower)

    for level in ("critical", "high", "medium", "low"):
        matched = [kw for kw in _SEVERITY_KEYWORDS[level] if kw in content_lower]
        if matched:
            # If negation found alongside keywords, downgrade by one level
            if negations:
                downgrade = {"critical": "high", "high": "medium",
                             "medium": "low", "low": "low"}
                return downgrade[level], matched
            return level, matched
    return "low", []


def _compute_adverse_score(result: dict, name: str, is_fca_regulated: bool = False) -> dict:
    """Compute a composite intelligence score for an adverse media result.

    Enriches the result dict with:
      _relevance_score : 0-1 overall relevance
      _source_credibility : 0-1 source tier score
      _source_tier : label (government|major_media|...)
      _temporal_weight : 0-1 recency decay
      _severity : critical|high|medium|low
      _severity_terms : list of matched severity keywords
      _relevant : bool (backward-compatible flag)
    
    For FCA-regulated entities, uses FCA-specific keywords for higher sensitivity.
    """
    content = (
        (result.get("content") or "") + " " + (result.get("title") or "")
    )
    url = result.get("url", "")

    # Source credibility
    cred, tier = _get_source_credibility(url)
    result["_source_credibility"] = cred
    result["_source_tier"] = tier

    # Temporal decay
    decay = _temporal_decay(result)
    result["_temporal_weight"] = decay

    # Severity classification
    severity, severity_terms = _classify_severity(content)
    result["_severity"] = severity
    result["_severity_terms"] = severity_terms

    # Entity relevance (existing gate logic)
    is_relevant = _is_relevant_adverse(result, name, is_fca_regulated)
    result["_relevant"] = is_relevant

    # Composite score: relevance × credibility × temporal_weight × severity_multiplier
    # For FCA-regulated entities, increase severity multipliers (higher sensitivity)
    if is_fca_regulated:
        sev_mult = {"critical": 1.0, "high": 0.95, "medium": 0.80, "low": 0.55}
    else:
        sev_mult = {"critical": 1.0, "high": 0.85, "medium": 0.65, "low": 0.40}
    
    if is_relevant:
        score = cred * decay * sev_mult.get(severity, 0.5)
    else:
        score = 0.0
    result["_relevance_score"] = round(score, 3)

    return result

# Words to ignore when building distinctive-name tokens
_COMPANY_NOISE = {
    "the", "and", "of", "for", "in", "a", "an", "at", "on", "by",
    "ltd", "limited", "plc", "inc", "corp", "corporation", "co",
    "llp", "lp", "uk", "group", "holdings", "services", "solutions",
    "consulting", "international", "global", "company",
}


def _is_relevant_adverse(result, name, is_fca_regulated=False):
    """Check if a Tavily result is a TRUE adverse hit for this specific entity.

    Strict two-gate filter:
    1. The entity's **distinctive name** must appear (full exact match OR
       ALL non-noise words present together).
    2. At least one adverse keyword must also appear (FCA-specific keywords
       for regulated entities).

    This prevents common-word names like "The Advice Centre Ltd" from
    matching random articles that merely contain "advice" or "centre".
    
    For FCA-regulated entities, uses FCA-specific keywords for higher sensitivity.
    """
    if result.get("_error"):
        return False

    content = (
        (result.get("content") or "")
        + " " + (result.get("title") or "")
    ).lower()

    # ── Gate 1 — entity name must genuinely appear ────────────────────
    clean_name = name.strip().lower()

    # Fast path: exact full name in content
    if clean_name in content:
        keywords = FCA_RISK_KEYWORDS if is_fca_regulated else ADVERSE_KEYWORDS
        return any(kw in content for kw in keywords)

    # Also try without suffix (e.g. "THE ADVICE CENTRE")
    for suffix in (" ltd", " limited", " plc", " inc", " llp", " lp"):
        if clean_name.endswith(suffix):
            short_name = clean_name[: -len(suffix)].strip()
            if short_name and short_name in content:
                keywords = FCA_RISK_KEYWORDS if is_fca_regulated else ADVERSE_KEYWORDS
                return any(kw in content for kw in keywords)

    # Fallback: ALL distinctive tokens must be present (word-boundary)
    tokens = [p.lower() for p in name.split() if len(p) > 1]
    distinctive = [t for t in tokens if t not in _COMPANY_NOISE]

    if not distinctive:
        # Name consists entirely of noise words — exact match only
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


def search_adverse_media(name, context_terms=None, is_fca_regulated=False):
    """Search for adverse media using multiple open query strategies.
    
    Args:
        name: Entity name to search for
        context_terms: optional list of contextual identifiers (e.g. city, role)
        is_fca_regulated: if True, include FCA-specific compliance risk searches
    
    For FCA-regulated entities, includes additional searches for AML compliance,
    market abuse, client funds mismanagement, and regulatory sanctions.
    """
    all_results = []

    # Query 1: Name + adverse terms (original, strong)
    q1 = f'"{name}" AND ({ADVERSE_TERMS})'
    r1 = tavily_search(q1, max_results=5)
    all_results += r1

    # Query 2: Name + open adverse keywords (catches more)
    q2 = (f'"{name}" (fraud OR bribery OR corruption OR "money laundering" '
          f'OR terrorism OR sanctions OR prosecution OR investigation OR scandal)')
    r2 = tavily_search(q2, depth="basic", max_results=5)
    # De-dupe by URL
    seen_urls = {r.get("url") for r in all_results}
    all_results += [r for r in r2 if r.get("url") not in seen_urls]

    # ─── ENHANCED FCA SEARCH FOR REGULATED ENTITIES ─────────────────────
    if is_fca_regulated:
        seen_urls = {r.get("url") for r in all_results}
        
        # AML Compliance
        q_aml = (f'"{name}" (AML OR "anti-money laundering" OR "aml breach" OR '
                 f'"suspicious activity report" OR "aml investigation")')
        r_aml = tavily_search(q_aml, depth="basic", max_results=5)
        all_results += [r for r in r_aml if r.get("url") not in seen_urls]
        seen_urls = {r.get("url") for r in all_results}
        
        # Market Abuse & Trading Conduct
        q_market = (f'"{name}" ("market abuse" OR "insider trading" OR '
                   f'"price manipulation" OR "wash trading")')
        r_market = tavily_search(q_market, depth="basic", max_results=5)
        all_results += [r for r in r_market if r.get("url") not in seen_urls]
        seen_urls = {r.get("url") for r in all_results}
        
        # Client Funds & Fiduciary Issues
        q_funds = (f'"{name}" ("client funds" OR "segregated account" OR '
                  f'"fund mismanagement" OR "fiduciary duty")')
        r_funds = tavily_search(q_funds, depth="basic", max_results=5)
        all_results += [r for r in r_funds if r.get("url") not in seen_urls]
        seen_urls = {r.get("url") for r in all_results}
        
        # FCA Specific Enforcement
        q_fca = (f'"{name}" (FCA OR "regulatory action" OR "license revoked" OR '
                f'"fca sanction" OR "fca investigation")')
        r_fca = tavily_search(q_fca, depth="basic", max_results=5)
        all_results += [r for r in r_fca if r.get("url") not in seen_urls]
        seen_urls = {r.get("url") for r in all_results}
        
        # Financial Crime & Fraud
        q_crime = (f'"{name}" ("financial crime" OR "embezzlement" OR '
                  f'"pyramid scheme" OR "investment fraud")')
        r_crime = tavily_search(q_crime, depth="basic", max_results=5)
        all_results += [r for r in r_crime if r.get("url") not in seen_urls]

    # Query 3: If context terms provided, try name + context for broader coverage
    if context_terms:
        ctx = " ".join(context_terms[:3])
        q3 = f'"{name}" {ctx}'
        r3 = tavily_search(q3, depth="basic", max_results=3)
        seen_urls = {r.get("url") for r in all_results}
        all_results += [r for r in r3 if r.get("url") not in seen_urls]

    # Tag each result with full intelligence scoring
    for r in all_results:
        _compute_adverse_score(r, name, is_fca_regulated)
    return all_results


def count_true_adverse(results):
    """Count only genuinely relevant adverse media hits."""
    return sum(1 for r in (results or []) if r.get("_relevant", False))


def compute_adverse_media_intelligence(results: list[dict]) -> dict:
    """V4: Compute aggregate adverse media intelligence metrics.

    Returns:
        dict with:
          - true_adverse_count: int
          - weighted_severity_score: float (0-100)
          - avg_source_credibility: float (0-1)
          - avg_recency_weight: float (0-1)
          - severity_distribution: dict (critical/high/medium/low counts)
          - highest_severity: str
          - top_sources: list[dict] (most credible adverse hits)
    """
    relevant = [r for r in (results or []) if r.get("_relevant", False)]
    if not relevant:
        return {
            "true_adverse_count": 0,
            "weighted_severity_score": 0.0,
            "avg_source_credibility": 0.0,
            "avg_recency_weight": 0.0,
            "severity_distribution": {"critical": 0, "high": 0, "medium": 0, "low": 0},
            "highest_severity": "none",
            "top_sources": [],
        }

    # Severity distribution
    sev_dist = {"critical": 0, "high": 0, "medium": 0, "low": 0}
    for r in relevant:
        sev = r.get("_severity", "low")
        sev_dist[sev] = sev_dist.get(sev, 0) + 1

    # Weighted severity score (0-100):  sum(relevance_score) normalised
    score_sum = sum(r.get("_relevance_score", 0.5) for r in relevant)
    # Scale: 1 critical gov source = ~1.0, so ~3 would be very high
    weighted_score = min(100.0, score_sum * 25.0)

    avg_cred = sum(r.get("_source_credibility", 0.4) for r in relevant) / len(relevant)
    avg_recency = sum(r.get("_temporal_weight", 0.7) for r in relevant) / len(relevant)

    # Highest severity
    for level in ("critical", "high", "medium", "low"):
        if sev_dist.get(level, 0) > 0:
            highest = level
            break
    else:
        highest = "none"

    # Top sources (sorted by relevance_score)
    top = sorted(relevant, key=lambda r: r.get("_relevance_score", 0), reverse=True)[:5]
    top_sources = [
        {
            "title": r.get("title", "")[:100],
            "url": r.get("url", ""),
            "severity": r.get("_severity", "unknown"),
            "source_tier": r.get("_source_tier", "unknown"),
            "relevance_score": r.get("_relevance_score", 0),
        }
        for r in top
    ]

    return {
        "true_adverse_count": len(relevant),
        "weighted_severity_score": round(weighted_score, 1),
        "avg_source_credibility": round(avg_cred, 2),
        "avg_recency_weight": round(avg_recency, 2),
        "severity_distribution": sev_dist,
        "highest_severity": highest,
        "top_sources": top_sources,
    }


def search_generic(name):
    return tavily_search(name, depth="basic")


def search_website_projects(website_url, charity_name):
    return tavily_search(f"site:{website_url} projects activities programs {charity_name}")


def search_positive_media(charity_name, location=""):
    """Search for positive media using multiple open query strategies."""
    all_results = []

    # Query 1: Awards, grants, partnerships with recognised bodies
    q1 = (f'"{charity_name}" (award OR grant OR partnership OR '
          f'"United Nations" OR "DFID" OR "FCDO" OR "USAID" OR "GIZ" OR '
          f'"charity of the year" OR "charity award" OR "WHO" OR '
          f'"European Commission" OR "World Bank")')
    r1 = tavily_search(q1, depth="advanced", max_results=5)
    all_results += r1

    # Query 2: General positive coverage
    q2 = f'"{charity_name}" (recognition OR "annual report" OR impact OR achievement)'
    if location:
        q2 += f' OR "{charity_name}" "{location}"'
    r2 = tavily_search(q2, depth="basic", max_results=5)
    seen_urls = {r.get("url") for r in all_results}
    all_results += [r for r in r2 if r.get("url") not in seen_urls]

    return all_results


def extract_social_media_from_website(website_url):
    """Extract verified social media profile links from a website.

    Multi-pass approach (Resilient Social Hunter):
      Pass A: Scrape homepage HTML for social <a> tags.
      Pass B: If any major platform is missing, crawl /about, /contact,
              /about-us, /contact-us, /legal, /connect, /follow-us, /links.
    Returns dict of platform -> URL (deterministic, no guessing).
    """
    if not website_url:
        return {}

    _DEEP_SCAN_PATHS = [
        "/about", "/about-us", "/contact", "/contact-us",
        "/legal", "/connect", "/follow-us", "/links",
        "/footer", "/company", "/who-we-are",
    ]
    _TARGET_PLATFORMS = {"facebook", "twitter", "instagram", "linkedin", "youtube"}

    crawler = _SiteCrawler(website_url)
    try:
        # ── Pass A: Homepage ──────────────────────────────────────────
        _, _, html = crawler.fetch(crawler.base_url)
        if html:
            crawler.extract_social_media(html, crawler.base_url)

        # ── Pass B: Deep scan sub-pages if platforms missing ──────────
        found = set(crawler.social_links.keys())
        if found < _TARGET_PLATFORMS:  # Any missing?
            for path in _DEEP_SCAN_PATHS:
                if set(crawler.social_links.keys()) >= _TARGET_PLATFORMS:
                    break  # All found
                url = crawler.base_url + path
                try:
                    _, status, sub_html = crawler.fetch(url)
                    if sub_html:
                        crawler.extract_social_media(sub_html, url)
                except Exception:
                    pass
    except Exception:
        pass
    finally:
        crawler.close()

    # Build clean output with nulls for missing platforms
    result = {}
    for platform in ["facebook", "twitter", "instagram", "linkedin", "youtube"]:
        result[platform] = crawler.social_links.get(platform)
    return result


# ═══════════════════════════════════════════════════════════════════════════════
# DEEP WEBSITE SCRAPER
# ═══════════════════════════════════════════════════════════════════════════════
# Crawls a website URL and its key sub-pages to produce rich text content,
# suitable for restricted-activity detection, industry classification, etc.

_DEEP_SCRAPE_PATHS = [
    "/", "/about", "/about-us", "/who-we-are", "/our-story",
    "/services", "/products", "/what-we-do", "/our-services",
    "/industries", "/solutions", "/how-it-works",
    "/contact", "/contact-us",
    "/team", "/our-team", "/leadership",
    "/faq", "/faqs", "/help",
    "/terms", "/terms-and-conditions", "/terms-of-service",
    "/privacy", "/privacy-policy",
    "/careers", "/jobs",
]


def scrape_website_deep(
    website_url: str, *, max_pages: int = 15, max_chars_per_page: int = 6000,
) -> list[dict]:
    """Deeply scrape a website for rich text content.

    Crawls the homepage plus key business-relevant sub-pages (about, services,
    products, terms, etc.) and follows internal links discovered on the homepage.

    Returns a list of dicts compatible with the web_results format:
        [{"title": ..., "url": ..., "content": ..., "_source": "direct_scrape"}, ...]
    """
    if not website_url:
        return []

    crawler = _SiteCrawler(website_url)
    pages: list[dict] = []

    try:
        # ── Phase 1: Homepage ────────────────────────────────────────
        _, status, homepage_html = crawler.fetch(crawler.base_url)
        if homepage_html:
            text = _SiteCrawler.html_to_text(homepage_html, max_chars=max_chars_per_page)
            title = ""
            try:
                soup = BeautifulSoup(homepage_html, "html.parser")
                t = soup.find("title")
                if t:
                    title = t.get_text(strip=True)[:200]
            except Exception:
                pass
            pages.append({
                "title": title or website_url,
                "url": crawler.base_url,
                "content": text,
                "_source": "direct_scrape",
            })

            # Discover internal links from homepage navigation
            nav_links = crawler.discover_relevant_internal_links(
                homepage_html, crawler.base_url
            )
        else:
            nav_links = []

        # ── Phase 2: Common business sub-pages ───────────────────────
        for path in _DEEP_SCRAPE_PATHS:
            if len(pages) >= max_pages:
                break
            url = crawler.base_url.rstrip("/") + path
            if url in crawler.visited:
                continue
            _, st, html = crawler.fetch(url)
            if not html:
                continue
            text = _SiteCrawler.html_to_text(html, max_chars=max_chars_per_page)
            if len(text.strip()) < 50:
                continue  # skip near-empty pages
            title = ""
            try:
                soup = BeautifulSoup(html, "html.parser")
                t = soup.find("title")
                if t:
                    title = t.get_text(strip=True)[:200]
            except Exception:
                pass
            pages.append({
                "title": title or path,
                "url": url,
                "content": text,
                "_source": "direct_scrape",
            })

        # ── Phase 3: Follow discovered nav links ────────────────────
        for link_url, link_text in nav_links:
            if len(pages) >= max_pages:
                break
            if link_url in crawler.visited:
                continue
            _, st, html = crawler.fetch(link_url)
            if not html:
                continue
            text = _SiteCrawler.html_to_text(html, max_chars=max_chars_per_page)
            if len(text.strip()) < 50:
                continue
            title = ""
            try:
                soup = BeautifulSoup(html, "html.parser")
                t = soup.find("title")
                if t:
                    title = t.get_text(strip=True)[:200]
            except Exception:
                pass
            pages.append({
                "title": title or link_text or link_url,
                "url": link_url,
                "content": text,
                "_source": "direct_scrape",
            })

    except Exception:
        pass
    finally:
        crawler.close()

    return pages


def search_online_presence(charity_name, website_url=""):
    """Search for digital footprint: reviews, transparency, Fundraising Regulator."""
    q = (f'"{charity_name}" ("Fundraising Regulator" OR "GuideStar" OR '
         f'review OR transparency OR "annual report" OR "charity register")')
    if website_url:
        q += f' OR site:{website_url} transparency annual report'
    return tavily_search(q, depth="basic", max_results=5)


# Non-profile path segments on Twitter/X that should be rejected.
# These appear when search returns tweets, hashtags, lists, etc. instead
# of actual profile pages.
_TWITTER_NON_PROFILE_PATHS = {
    "/status/", "/statuses/", "/i/", "/hashtag/", "/search",
    "/explore", "/lists/", "/moments/", "/events/", "/topics/",
    "/communities/", "/spaces/", "/intent/", "/compose/",
    "/notifications", "/messages", "/settings", "/home",
    "/tos", "/privacy", "/help", "/login", "/signup",
}

# Non-profile path segments on Facebook
_FACEBOOK_NON_PROFILE_PATHS = {
    "/posts/", "/photos/", "/videos/", "/events/", "/notes/",
    "/stories/", "/reels/", "/watch/", "/marketplace/",
    "/groups/", "/gaming/", "/login", "/help", "/policies",
}

# Non-profile path segments on Instagram
_INSTAGRAM_NON_PROFILE_PATHS = {
    "/p/", "/reel/", "/reels/", "/stories/", "/explore/",
    "/tv/", "/live/", "/accounts/", "/directory/",
}


def _is_twitter_profile_url(url: str) -> bool:
    """Return True only if *url* looks like a Twitter/X profile page.

    Valid patterns:
      https://twitter.com/CompanyName
      https://x.com/CompanyName
    Invalid patterns:
      https://twitter.com/someone/status/1234567890   (a tweet)
      https://twitter.com/hashtag/keyword              (a hashtag page)
      https://x.com/i/events/123                       (event page)
    """
    parsed = urlparse(url)
    path = parsed.path.lower().rstrip("/")

    # Must have exactly one path segment (the username)
    segments = [s for s in path.split("/") if s]
    if len(segments) != 1:
        # Allow /CompanyName but reject /CompanyName/status/123, /hashtag/x, etc.
        return False

    # Reject known non-profile paths
    path_with_slash = f"/{segments[0]}/" if segments else ""
    for blocked in _TWITTER_NON_PROFILE_PATHS:
        if blocked.strip("/") == segments[0]:
            return False

    return True


def search_social_osint(company_name: str, website_url: str = "") -> dict:
    """OSINT Pivot — search for missing social profiles via Tavily.

    For each major platform, runs a targeted search like:
      site:linkedin.com/company "{company_name}" "{domain}"
    Returns dict of platform -> URL for profiles found externally.

    When *website_url* is empty the matching threshold is raised so that
    only high-confidence profile URLs are returned.  This avoids the common
    false-positive pattern where Tavily returns a tweet or post that merely
    *mentions* words from the company name.
    """
    if not company_name:
        return {}

    _has_website = bool(website_url and website_url.strip())

    domain = ""
    if _has_website:
        parsed = urlparse(website_url if "://" in website_url else f"https://{website_url}")
        domain = (parsed.netloc or parsed.path).lower().lstrip("www.")

    # Platform search configs: (platform_key, site_filter, path_hint)
    _PLATFORM_SEARCHES = [
        ("linkedin", "site:linkedin.com/company", "/company/"),
        ("twitter", "site:twitter.com OR site:x.com", None),
        ("facebook", "site:facebook.com", None),
        ("instagram", "site:instagram.com", None),
    ]

    # Build name slug variants for URL validation
    _name_lower = company_name.lower().strip()
    _name_words = set(re.sub(r'[^\w\s]', '', _name_lower).split()) - {
        'ltd', 'limited', 'plc', 'llp', 'the', 'and', 'of', 'uk', 'inc',
        'group', 'holdings',
    }
    _name_slug = re.sub(r'[^a-z0-9]+', '-', _name_lower).strip('-')
    _name_slug_under = re.sub(r'[^a-z0-9]+', '_', _name_lower).strip('_')
    _name_slug_none = re.sub(r'[^a-z0-9]', '', _name_lower)

    # Strip noise words from slug variants too so "the-advice-centre-ltd"
    # becomes "advice-centre" for cleaner matching.
    _noise = {'ltd', 'limited', 'plc', 'llp', 'the', 'and', 'of', 'uk',
              'inc', 'group', 'holdings'}
    _name_slug_clean = '-'.join(
        w for w in _name_slug.split('-') if w and w not in _noise
    ).strip('-')
    _name_slug_none_clean = re.sub(r'[^a-z0-9]', '', _name_slug_clean)

    def _url_matches_company(url: str, *, platform: str = "") -> bool:
        """Check if a social profile URL plausibly belongs to this company.

        When *_has_website* is False the threshold is raised:
        - slug matches must use the *clean* (noise-free) slug and be ≥ 5 chars
        - word matching requires ≥ 75% of distinctive words (not 50%)
        - single-word company names require an exact slug match (no word %)
        """
        path = urlparse(url).path.lower()

        # ── Slug-based matching (strong signal) ──────────────────────
        if _name_slug_clean and len(_name_slug_clean) >= 5 and _name_slug_clean in path:
            return True
        if _name_slug_none_clean and len(_name_slug_none_clean) >= 5 and _name_slug_none_clean in path:
            return True

        # When we have a website, also accept the original (noisy) slug
        if _has_website:
            if _name_slug and _name_slug in path:
                return True
            if _name_slug_under and _name_slug_under in path:
                return True
            if _name_slug_none and len(_name_slug_none) >= 5 and _name_slug_none in path:
                return True

        # ── Word-based matching ──────────────────────────────────────
        if _name_words:
            path_clean = path.replace('-', ' ').replace('_', ' ').replace('/', ' ')
            hits = sum(1 for w in _name_words if len(w) >= 3 and w in path_clean)
            n_words = len([w for w in _name_words if len(w) >= 3])

            if n_words == 0:
                pass  # No significant words — skip word matching
            elif n_words == 1:
                # Single-word names are very ambiguous.
                # Only accept if the slug also broadly matches.
                if hits == 1:
                    word = list(w for w in _name_words if len(w) >= 3)[0]
                    # The word must appear as a standalone path segment
                    path_segments = [s for s in path.split('/') if s]
                    segment_words = set()
                    for seg in path_segments:
                        segment_words.update(seg.replace('-', ' ').replace('_', ' ').split())
                    if word in segment_words and _has_website:
                        return True
                    # Without website, single-word match is too weak — skip
            elif _has_website:
                # With website: 50% threshold (original)
                if hits >= max(1, n_words * 0.5):
                    return True
            else:
                # Without website: raise to 75% threshold
                if n_words >= 2 and hits >= max(2, int(n_words * 0.75)):
                    return True

        # ── Domain-based matching ────────────────────────────────────
        if domain:
            domain_stem = domain.split('.')[0]
            if domain_stem and len(domain_stem) >= 3 and domain_stem in path:
                return True

        return False

    found: dict[str, str] = {}
    for plat_key, site_filter, path_hint in _PLATFORM_SEARCHES:
        query = f'{site_filter} "{company_name}"'
        if domain:
            query += f' "{domain}"'
        try:
            results = tavily_search(query, depth="basic", max_results=5)
            for r in (results or []):
                url = r.get("url", "")
                if not url:
                    continue
                url_lower = url.lower()

                # Platform domain check
                if plat_key == "linkedin" and "linkedin.com" in url_lower:
                    if ("/company/" in url_lower or "/in/" in url_lower) and _url_matches_company(url, platform="linkedin"):
                        found[plat_key] = url
                        break

                elif plat_key == "twitter" and ("twitter.com" in url_lower or "x.com" in url_lower):
                    # ── Strict Twitter/X profile validation ──────────
                    # Reject any URL that is NOT a profile page
                    path_lower = urlparse(url).path.lower()
                    if any(seg in path_lower for seg in _TWITTER_NON_PROFILE_PATHS):
                        continue
                    # Must be a clean profile URL (single path segment)
                    if not _is_twitter_profile_url(url):
                        continue
                    if _url_matches_company(url, platform="twitter"):
                        found[plat_key] = url
                        break

                elif plat_key == "facebook" and "facebook.com" in url_lower:
                    path_lower = urlparse(url).path.lower()
                    if any(seg in path_lower for seg in _FACEBOOK_NON_PROFILE_PATHS):
                        continue
                    if _url_matches_company(url, platform="facebook"):
                        found[plat_key] = url
                        break

                elif plat_key == "instagram" and "instagram.com" in url_lower:
                    path_lower = urlparse(url).path.lower()
                    if any(seg in path_lower for seg in _INSTAGRAM_NON_PROFILE_PATHS):
                        continue
                    if _url_matches_company(url, platform="instagram"):
                        found[plat_key] = url
                        break
        except Exception:
            pass

    # Tag results with confidence level so callers know the reliability
    if found:
        found["_osint_confidence"] = "high" if _has_website else "low"

    return found


# Common policy page paths charities use (charity-agnostic slugs)
_POLICY_PATHS = [
    "/policies", "/policy", "/governance", "/about-us/policies",
    "/about/policies", "/our-policies", "/key-policies",
    "/resources/policies", "/about-us/governance", "/about/governance",
    "/safeguarding", "/transparency", "/privacy-policy",
    "/data-protection", "/complaints", "/about/safeguarding",
    "/downloads", "/resources", "/about", "/documents",
    "/publications", "/key-documents", "/corporate-governance",
    "/media", "/assets/documents", "/wp-content/uploads",
]

# Slugs that identify a page as a likely policy hub
_HUB_SLUGS = re.compile(
    r'/(policies|policy|governance|documents|downloads|resources|'
    r'publications|key-documents|key-policies|our-policies|corporate-governance)'
    r'(/|$)', re.IGNORECASE)

# Link text patterns that indicate a policy hub or individual policy page
_POLICY_LINK_PATTERNS = re.compile(
    r'\b(polic|safeguard|whistleblow|anti.?brib|anti.?fraud|data.?protect|'
    r'privacy|gdpr|complain|grievance|modern.?slavery|health.?safety|'
    r'equal.?opportun|diversity|inclusion|risk.?manage|aml|anti.?money|'
    r'counter.?terror|governance|code.?of.?conduct|bullying|harassment|'
    r'disciplin|volunteer.?handbook|download|document|resource|publication)\b',
    re.IGNORECASE)

# Document file extensions we want to capture from hubs
_DOC_EXTENSIONS = {'.pdf', '.docx', '.doc', '.xlsx', '.xls', '.odt', '.pptx'}

# The policy types we check for — with keyword variants for matching
POLICY_CHECKLIST = [
    "Safeguarding", "Anti-Bribery & Corruption", "Anti-Money Laundering (AML/CTF)",
    "Risk Management", "Whistleblowing", "GDPR / Data Protection",
    "Modern Slavery", "Health & Safety", "Equal Opportunities",
    "Social Media Policy", "Grievance", "Disciplinary",
    "Anti-Harassment & Bullying",
]

_POLICY_KEYWORDS = {
    "Safeguarding":                 ["safeguard", "child protect", "adult protect", "dbs", "vulnerable",
                                     "protection policy", "welfare policy", "vulnerable adults",
                                     "safeguarding lead", "safe recruitment"],
    "Anti-Bribery & Corruption":    ["anti-brib", "anti brib", "bribery", "corruption", "anti-fraud", "fraud",
                                     "anti-corruption", "anti corruption", "financial crime",
                                     "gifts and hospitality", "conflicts of interest",
                                     "fraud prevention", "corrupt practice"],
    "Anti-Money Laundering (AML/CTF)": ["money laundering", "money-laundering", "money‑laundering",
                                        "aml", "ctf", "counter-terror", "counter terror",
                                        "terrorist financ", "proceeds of crime", "financial crime",
                                        "financial integrity", "sanctions compliance",
                                        "financial crime prevention", "suspicious activity"],
    "Risk Management":              ["risk manage", "risk register", "risk assess", "risk framework",
                                     "enterprise risk", "risk governance", "risk appetite",
                                     "risk tolerance", "principal risk", "strategic risk",
                                     "risk oversight", "risk committee"],
    "Whistleblowing":               ["whistleblow", "whistle-blow", "whistle blow",
                                     "speak up", "raising concern", "public interest disclosure",
                                     "confidential reporting", "protected disclosure",
                                     "speak-up", "freedom to speak"],
    "GDPR / Data Protection":       ["data protect", "gdpr", "privacy", "information commissioner",
                                     "ico", "data process", "dpia",
                                     "data retention", "information governance",
                                     "data security", "privacy impact", "subject access"],
    "Modern Slavery":               ["modern slavery", "human trafficking", "forced labour",
                                     "supply chain transparency", "slavery statement",
                                     "modern slavery act", "exploitation"],
    "Health & Safety":              ["health & safety", "health and safety", "h&s policy", "fire safety", "first aid",
                                     "workplace safety", "occupational health", "risk of harm",
                                     "safety management", "lone working"],
    "Equal Opportunities":          ["equal opportun", "equality", "diversity", "inclusion", "edi policy", "edi",
                                     "protected characteristic", "equalities",
                                     "diversity and inclusion", "d&i policy", "equity"],
    "Social Media Policy":          ["social media", "online safety", "digital", "acceptable use",
                                     "digital communications", "online conduct",
                                     "social networking", "internet use"],
    "Grievance":                    ["grievance", "complaint", "complaint procedure", "complaints", "dispute resolution",
                                     "formal complaint", "complaints handling", "complaint process"],
    "Disciplinary":                 ["disciplin", "discipline",
                                     "disciplinary procedure", "code of conduct",
                                     "staff conduct", "misconduct"],
    "Anti-Harassment & Bullying":   ["harass", "bullying", "dignity at work", "anti-harassment",
                                     "workplace harassment", "anti-bullying",
                                     "respectful workplace", "zero tolerance"],
}

# ─── PROXIMITY / CONFIDENCE HELPERS ──────────────────────────────────────────
# Words that, when found near a policy keyword, indicate a formal policy exists
_POLICY_ANCHOR_TERMS = re.compile(
    r'\b(policy|policies|procedure|procedures|framework|statement|code of|'
    r'guidance|protocol|standard|strategy|charter|manual|handbook|guidelines|'
    r'commitment|pledge|declaration|terms of reference)\b',
    re.IGNORECASE)

# Maximum word distance between keyword and anchor term for proximity match
_PROXIMITY_WINDOW = 40  # words


def _check_proximity(text: str, keyword: str, window: int = _PROXIMITY_WINDOW) -> str:
    """Check if *keyword* appears within *window* words of a policy anchor term.

    Returns:
        'high'   — keyword is within *window* words of an anchor term
        'medium' — keyword is present but NOT near an anchor term
        ''       — keyword not found at all
    """
    kw_lower = keyword.lower()
    text_lower = text.lower()
    if kw_lower not in text_lower:
        return ""
    # Tokenise into words with positions
    words = text_lower.split()
    # Find all positions of the keyword (substring match within word tokens)
    kw_positions = []
    for i, w in enumerate(words):
        if kw_lower in w:
            kw_positions.append(i)
    if not kw_positions:
        return "medium"  # present as substring in larger token
    # Find all anchor term positions
    anchor_positions = [i for i, w in enumerate(words) if _POLICY_ANCHOR_TERMS.search(w)]
    if not anchor_positions:
        return "medium"
    # Check if any keyword position is within window of any anchor
    for kp in kw_positions:
        for ap in anchor_positions:
            if abs(kp - ap) <= window:
                return "high"
    return "medium"


# ─── SOCIAL MEDIA DOMAIN MAP ─────────────────────────────────────────────────
_SOCIAL_DOMAINS = {
    "facebook.com": "facebook", "m.facebook.com": "facebook",
    "www.facebook.com": "facebook",
    "twitter.com": "twitter", "www.twitter.com": "twitter",
    "x.com": "twitter", "www.x.com": "twitter",
    "instagram.com": "instagram", "www.instagram.com": "instagram",
    "linkedin.com": "linkedin", "www.linkedin.com": "linkedin",
    "youtube.com": "youtube", "www.youtube.com": "youtube",
    "youtu.be": "youtube",
}
_SOCIAL_SHARE_PATTERNS = re.compile(
    r'sharer\.php|share\?|intent/tweet|addtoany|sharebutton|share_url|pinterest\.com/pin',
    re.IGNORECASE)
_TRACKING_PARAMS = re.compile(r'[?&](utm_\w+|fbclid|gclid|ref|source|mc_[a-z]+)=[^&]*')

# File extensions to skip when crawling
_SKIP_EXTENSIONS = {
    '.jpg', '.jpeg', '.png', '.gif', '.svg', '.webp', '.ico', '.bmp',
    '.mp3', '.mp4', '.avi', '.mov', '.wmv', '.zip', '.rar', '.gz',
    '.exe', '.msi', '.dmg', '.css', '.js', '.woff', '.woff2', '.ttf',
}


class _SiteCrawler:
    """Lightweight session-based site crawler with hub detection,
    document link extraction, social media extraction, and a hard cap on pages visited."""

    MAX_PAGES = 25  # Hard cap per domain

    def __init__(self, base_url):
        clean = base_url.rstrip('/')
        if not clean.startswith('http'):
            clean = 'https://' + clean
        self.base_url = clean
        self.base_domain = urlparse(clean).netloc
        self.verify = get_ssl_verify()
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            )
        })
        self.session.verify = self.verify if self.verify is not True else True
        self.visited = set()
        self.social_links = {}  # platform -> url
        self.hubs = []          # list of hub URLs detected
        self.doc_links = []     # list of {url, text, source, is_document}
        self.audit = []

    # ── Low-level helpers ────────────────────────────────────────────
    def fetch(self, url):
        """Fetch a URL via session. Returns (url, status, html) or (url, 'error', '')."""
        if url in self.visited or len(self.visited) >= self.MAX_PAGES:
            return url, "skipped", ""
        self.visited.add(url)
        try:
            r = self.session.get(url, timeout=25, allow_redirects=True)
            if r.status_code == 200 and len(r.text) > 100:
                return url, r.status_code, r.text
            return url, r.status_code, ""
        except Exception:
            return url, "error", ""

    @staticmethod
    def extract_links(html, base_url):
        """Parse HTML and return all <a> links as (abs_url, link_text) tuples."""
        links = []
        try:
            soup = BeautifulSoup(html, "html.parser")
            for a in soup.find_all("a", href=True):
                href = a["href"].strip()
                if not href or href.startswith(("#", "mailto:", "tel:", "javascript:")):
                    continue
                text = a.get_text(" ", strip=True)[:200]
                abs_url = urljoin(base_url, href)
                links.append((abs_url, text))
        except Exception:
            pass
        return links

    @staticmethod
    def html_to_text(html, max_chars=4000):
        """Strip HTML to plain text."""
        try:
            soup = BeautifulSoup(html, "html.parser")
            for tag in soup(["script", "style", "nav"]):
                tag.decompose()
            text = soup.get_text(" ", strip=True)
            return re.sub(r'\s+', ' ', text)[:max_chars]
        except Exception:
            text = re.sub(r'<[^>]+>', ' ', html)
            return re.sub(r'\s+', ' ', text)[:max_chars]

    def _is_internal(self, url):
        """Check if URL belongs to same domain."""
        parsed = urlparse(url)
        return parsed.netloc == self.base_domain

    def _is_skippable(self, url):
        """Skip images, media, and other non-HTML resources."""
        path = urlparse(url).path.lower()
        return any(path.endswith(ext) for ext in _SKIP_EXTENSIONS)

    # ── Social media extraction ──────────────────────────────────────
    def extract_social_media(self, html, page_url):
        """Extract verified social media profile links from page HTML.
        Only keeps real profile links, not share buttons."""
        for abs_url, link_text in self.extract_links(html, page_url):
            parsed = urlparse(abs_url)
            domain = parsed.netloc.lower()
            # Match against known social domains
            platform = _SOCIAL_DOMAINS.get(domain)
            if not platform:
                continue
            # Skip share buttons
            if _SOCIAL_SHARE_PATTERNS.search(abs_url):
                continue
            # Skip if path is just / (homepage of social site)
            if parsed.path.rstrip('/') == '':
                continue
            # Clean tracking params
            clean_url = _TRACKING_PARAMS.sub('', abs_url).rstrip('?&')
            # Keep first found per platform (most likely header/footer)
            if platform not in self.social_links:
                self.social_links[platform] = clean_url

    # ── Internal link discovery ──────────────────────────────────────
    def discover_relevant_internal_links(self, html, page_url, pattern=None):
        """Find internal links matching a relevance pattern.
        Returns list of (url, link_text) for internal, non-skippable links."""
        relevant = []
        for abs_url, link_text in self.extract_links(html, page_url):
            if not self._is_internal(abs_url):
                continue
            if self._is_skippable(abs_url):
                continue
            if abs_url in self.visited:
                continue
            # Apply relevance filter
            if pattern:
                if pattern.search(link_text) or pattern.search(abs_url):
                    relevant.append((abs_url, link_text))
            else:
                relevant.append((abs_url, link_text))
        return relevant

    # ── Hub detection ────────────────────────────────────────────────
    def is_hub_page(self, url, html):
        """Determine if a fetched page is a policy hub.
        A hub is any page whose URL matches hub slugs OR whose title
        suggests policies, AND that contains ≥2 policy-relevant links."""
        url_match = bool(_HUB_SLUGS.search(urlparse(url).path))
        title_match = False
        try:
            soup = BeautifulSoup(html, "html.parser")
            title_tag = soup.find("title")
            if title_tag:
                title_match = bool(_POLICY_LINK_PATTERNS.search(title_tag.get_text()))
            h1 = soup.find("h1")
            if h1:
                title_match = title_match or bool(_POLICY_LINK_PATTERNS.search(h1.get_text()))
        except Exception:
            pass
        if not url_match and not title_match:
            return False
        # Must have at least 2 policy-relevant links
        relevant = self.discover_relevant_internal_links(html, url, _POLICY_LINK_PATTERNS)
        doc_count = sum(1 for u, _ in self.extract_links(html, url)
                        if any(u.lower().endswith(ext) for ext in _DOC_EXTENSIONS))
        return (len(relevant) + doc_count) >= 2

    def extract_document_links(self, html, page_url, source_label):
        """Extract all links to documents (PDFs, DOCX, etc.) and policy-related
        internal pages from *html*. Records them in self.doc_links."""
        for abs_url, link_text in self.extract_links(html, page_url):
            path_lower = urlparse(abs_url).path.lower()
            is_doc = any(path_lower.endswith(ext) for ext in _DOC_EXTENSIONS)
            is_policy_link = bool(_POLICY_LINK_PATTERNS.search(link_text) or
                                  _POLICY_LINK_PATTERNS.search(abs_url))
            if is_doc or (is_policy_link and (self._is_internal(abs_url) or is_doc)):
                # Avoid duplicates
                if not any(d["url"] == abs_url for d in self.doc_links):
                    self.doc_links.append({
                        "url": abs_url,
                        "text": link_text or os.path.basename(urlparse(abs_url).path),
                        "source": source_label,
                        "is_document": is_doc,
                    })

    def close(self):
        self.session.close()


def _scrape_policy_pages(website_url):
    """Crawl charity website for policy content using session-based crawler.

    Strategy (charity-agnostic — no hard-coded domain assumptions):
    1. Homepage — extract nav / header / footer links for policy pages.
    2. Common policy-slug paths — fetch and check each.
    3. Hub detection — any page whose URL or title matches hub slugs and
       contains ≥2 policy-relevant links is treated as a hub.
    4. Hub processing — extract ALL document links (PDF/DOCX/HTML) from
       each hub page; record link text + filename.
    5. Follow discovered internal policy links from hubs/footer.
    6. Social media extraction on every page visited.

    Returns (found_pages, doc_links, audit, social_links).
    """
    crawler = _SiteCrawler(website_url)
    audit = []
    found_pages = []          # {url, snippet, is_hub}

    # ── Phase 1: Fetch homepage ──────────────────────────────────────
    _, hstatus, homepage_html = crawler.fetch(crawler.base_url)
    if homepage_html:
        crawler.extract_social_media(homepage_html, crawler.base_url)
        # Check if homepage itself is a hub (rare but possible)
        if crawler.is_hub_page(crawler.base_url, homepage_html):
            found_pages.append({"url": crawler.base_url, "snippet": crawler.html_to_text(homepage_html), "is_hub": True})
            crawler.hubs.append(crawler.base_url)
            crawler.extract_document_links(homepage_html, crawler.base_url, "Homepage hub")
        # Discover policy-relevant links from nav/header/footer
        policy_links_hp = crawler.discover_relevant_internal_links(
            homepage_html, crawler.base_url, _POLICY_LINK_PATTERNS)
        for link_url, link_text in policy_links_hp:
            if not any(d["url"] == link_url for d in crawler.doc_links):
                crawler.doc_links.append({
                    "url": link_url, "text": link_text,
                    "source": "Homepage nav/header/footer", "is_document": False,
                })
        audit.append({"url": crawler.base_url, "status": hstatus,
                      "found": True, "note": "Homepage scanned for nav links + social"})
    else:
        audit.append({"url": crawler.base_url, "status": hstatus,
                      "found": False, "note": "Homepage fetch failed"})

    # ── Phase 2: Fetch common policy paths ───────────────────────────
    for path in _POLICY_PATHS:
        url = crawler.base_url + path
        url, status, html = crawler.fetch(url)
        hit = bool(html)
        if hit:
            text = crawler.html_to_text(html)
            crawler.extract_social_media(html, url)
            is_hub = crawler.is_hub_page(url, html)
            found_pages.append({"url": url, "snippet": text, "is_hub": is_hub})
            if is_hub:
                crawler.hubs.append(url)
                crawler.extract_document_links(html, url, f"Hub: {url}")
            else:
                # Still extract document links from non-hub policy pages
                crawler.extract_document_links(html, url, f"Policy page: {url}")
            audit.append({"url": url, "status": status, "found": True,
                          "note": "Hub detected" if is_hub else "Policy page"})
        else:
            audit.append({"url": url, "status": status, "found": False})

    # ── Phase 3: Follow discovered links from hubs + nav ─────────────
    follow_urls = []
    seen = set()
    for dl in crawler.doc_links:
        u = dl["url"]
        if u not in crawler.visited and u not in seen:
            path_lower = urlparse(u).path.lower()
            is_doc = any(path_lower.endswith(ext) for ext in _DOC_EXTENSIONS)
            if not is_doc:  # only follow HTML pages (docs are recorded already)
                follow_urls.append(u)
                seen.add(u)

    for url in follow_urls[:15]:
        url, status, html = crawler.fetch(url)
        hit = bool(html)
        if hit:
            text = crawler.html_to_text(html)
            crawler.extract_social_media(html, url)
            # Check if this followed page is ALSO a hub
            is_hub = crawler.is_hub_page(url, html)
            if is_hub:
                crawler.hubs.append(url)
                crawler.extract_document_links(html, url, f"Hub: {url}")
            # Keyword filter: keep page only if content has policy-related terms
            text_lower = text.lower()
            has_policy_kw = any(
                kw in text_lower for kwlist in _POLICY_KEYWORDS.values() for kw in kwlist
            )
            if has_policy_kw or is_hub:
                found_pages.append({"url": url, "snippet": text, "is_hub": is_hub})
                audit.append({"url": url, "status": status, "found": True,
                              "note": "Hub from follow" if is_hub else "Followed link (policy keywords matched)"})
            else:
                audit.append({"url": url, "status": status, "found": True,
                              "note": "Followed — content filtered out (no policy keywords)"})
        else:
            audit.append({"url": url, "status": status, "found": False,
                          "note": "Followed link — fetch failed"})

    social = dict(crawler.social_links)
    doc_links = list(crawler.doc_links)
    hub_count = len(crawler.hubs)
    crawler.close()
    return found_pages, doc_links, audit, social


def _classify_policies(found_pages, doc_links, search_results):
    """Classify each policy in POLICY_CHECKLIST using a three-tier keyword
    classifier enhanced with **proximity logic** and **confidence scoring**.

    Tier 1 — **Found**: A document link (PDF/DOCX) or page URL/title whose
             filename or link text matches the policy type's keywords.
             Confidence: **high**.
    Tier 2a — **Partial (high confidence)**: Keyword appears in body text
              within proximity of a policy anchor term (e.g. "policy",
              "procedure", "framework").
    Tier 2b — **Partial (medium confidence)**: Keyword appears in body text
              but NOT near a policy anchor term — may be incidental mention.
    Tier 3 — **Not Located**: No evidence in any crawled page, document link,
             or web search result.  Confidence: n/a.

    Returns list of dicts:
        {policy, status, status_icon, source_url, evidence, comment,
         detection_confidence}
    """

    # ── Build corpus ─────────────────────────────────────────────────
    title_items = []   # (url, combined_title, source, is_document)
    for dl in (doc_links or []):
        title_text = (dl.get("text", "") or "").lower()
        path = urlparse(dl.get("url", "")).path
        filename = os.path.basename(path).lower().replace("-", " ").replace("_", " ")
        combined = title_text + " " + filename
        title_items.append((dl["url"], combined, dl.get("source", "Hub"), dl.get("is_document", False)))

    body_items = []    # (url, body_text, source_label)
    for page in (found_pages or []):
        body_items.append((page["url"], page.get("snippet", "").lower(), "Website page"))
    for sr in (search_results or []):
        text = ((sr.get("title") or "") + " " + (sr.get("content") or "")).lower()
        body_items.append((sr.get("url", ""), text, "Web search"))

    STATUS_ICONS = {
        "found": "✅ Found",
        "partial": "🔍 Partial",
        "not_located": "ℹ️ Not Located in Public Materials",
    }

    results = []
    for policy_name in POLICY_CHECKLIST:
        keywords = _POLICY_KEYWORDS.get(policy_name, [policy_name.lower()])
        best = {"status": "not_located", "url": "", "evidence": "", "comment": "",
                "detection_confidence": "none"}

        # ── Tier 1: Title / filename / URL match → Found (high) ──
        for url, title_text, source, is_doc in title_items:
            url_lower = url.lower()
            for kw in keywords:
                kw_l = kw.lower()
                kw_slug = kw_l.replace(" ", "-")
                kw_under = kw_l.replace(" ", "_")
                if kw_l in title_text or kw_slug in url_lower or kw_under in url_lower:
                    doc_type = "PDF/document" if is_doc else "page"
                    evidence = f"{title_text.strip()[:120]} ({doc_type} on {source})"
                    best = {
                        "status": "found", "url": url,
                        "evidence": evidence.strip(),
                        "comment": f"Policy document identified via {source.lower()}",
                        "detection_confidence": "high",
                    }
                    break
            if best["status"] == "found":
                break

        # ── Tier 2: Body text keyword + proximity check ──────────
        if best["status"] != "found":
            best_proximity = ""       # track best proximity across all body items
            best_body = {"url": "", "kw": "", "source": ""}
            for url, body, source in body_items:
                for kw in keywords:
                    prox = _check_proximity(body, kw)
                    if prox and (not best_proximity or
                                 (prox == "high" and best_proximity != "high")):
                        best_proximity = prox
                        best_body = {"url": url, "kw": kw, "source": source}
                    if best_proximity == "high":
                        break
                if best_proximity == "high":
                    break

            if best_proximity == "high":
                # Keyword near policy anchor → Partial with high confidence
                best = {
                    "status": "partial", "url": best_body["url"],
                    "evidence": (
                        f"Keyword '{best_body['kw']}' found near policy/framework language "
                        f"on {best_body['source'].lower()}; no standalone document link found"
                    ),
                    "comment": (
                        f"Content referencing '{best_body['kw']}' in a policy context identified; "
                        "standalone policy document not confirmed"
                    ),
                    "detection_confidence": "high",
                }
            elif best_proximity == "medium":
                # Keyword present but not near anchor → Partial with medium confidence
                best = {
                    "status": "partial", "url": best_body["url"],
                    "evidence": (
                        f"Keyword '{best_body['kw']}' mentioned on {best_body['source'].lower()}; "
                        "not in proximity to policy/procedure language"
                    ),
                    "comment": (
                        f"Related content mentioning '{best_body['kw']}' found but may be "
                        "incidental rather than a formal policy; standalone document not confirmed"
                    ),
                    "detection_confidence": "medium",
                }

        # ── Tier 3: Not located ──────────────────────────────────
        if best["status"] == "not_located":
            best["comment"] = (
                f"No {policy_name.lower()} document could be located in public materials scanned "
                "(website, policy hub, uploaded documents). The policy may exist internally or under a different title."
            )
            best["detection_confidence"] = "none"

        results.append({
            "policy": policy_name,
            "status": best["status"],
            "status_icon": STATUS_ICONS[best["status"]],
            "source_url": best["url"],
            "evidence": best["evidence"],
            "comment": best["comment"],
            "detection_confidence": best["detection_confidence"],
        })

    return results


# ─── HRCOB CORE CONTROLS ─────────────────────────────────────────────────────
# Three mandatory assessment areas for HRCOB due-diligence.
# All other policies are secondary / contextual.
_HRCOB_CORE_CONTROLS = ["safeguarding", "financial_crime", "risk_management"]

# Keywords for the two halves of "financial crime" (both sides must appear for Found)
_FC_BRIBERY_KW = [
    "bribery", "brib", "anti-brib", "anti brib", "corruption",
    "anti-corruption", "anti corruption",
    "gifts and hospitality", "corrupt practice", "conflicts of interest",
]
_FC_AML_KW = [
    "money laundering", "money-laundering", "money‑laundering",
    "aml", "ctf", "proceeds of crime", "terrorist financ",
    "counter-terror", "counter terror", "terrorism legislation",
    "financial crime", "sanctions compliance", "financial integrity",
    "suspicious activity", "financial crime prevention",
]

# Safeguarding — procedural detail keywords (beyond high-level mention)
_SG_PROCEDURAL_KW = [
    "abuse report", "reporting concern", "designated safeguarding lead",
    "safeguarding officer", "safeguarding procedure", "dbs check",
    "barring service", "disclosure and barring", "vulnerable person",
    "child protection procedure", "safeguarding training",
    "safeguarding referral", "lado", "local authority designated officer",
    "safeguarding lead", "reporting abuse", "safeguarding policy statement",
    "welfare officer", "protection officer", "safe recruitment",
    "safer recruitment", "prevent duty",
]

# Risk management — structured detail keywords
_RM_STRUCTURED_KW = [
    "risk register", "risk assessment", "risk review process",
    "risk management framework", "principal risks", "risk appetite",
    "risk matrix", "risk mitigation", "strategic risk", "operational risk",
    "trustees reviewed risk", "trustees have considered",
    "trustees consider the major risk", "risk report",
    "enterprise risk management", "risk governance", "risk tolerance",
    "risk oversight", "risk committee", "board risk",
]


def _classify_core_controls(found_pages, doc_links, search_results):
    """Classify the three HRCOB core controls with specialised logic,
    **proximity awareness**, and **confidence scoring**:

    1. **safeguarding** — Found if standalone document OR procedural detail;
       Partial if only high-level mention.
    2. **financial_crime** — Found if document covers BOTH bribery AND AML;
       Partial if only one side present.
    3. **risk_management** — Found if standalone document OR structured
       risk-review description; Partial if generic mention only.

    Each control result now includes ``detection_confidence`` (high | medium |
    low | none) derived from match type and proximity analysis.

    Returns dict: {
        "safeguarding":    {status, evidence, source_url, comment, detection_confidence},
        "financial_crime": {status, evidence, source_url, comment, detection_confidence},
        "risk_management": {status, evidence, source_url, comment, detection_confidence},
        "hrcob_status": str,
        "hrcob_narrative": str,
    }
    """

    # ── Build corpora ────────────────────────────────────────────────
    title_items = []  # (url, combined_title, source, is_doc)
    for dl in (doc_links or []):
        title_text = (dl.get("text", "") or "").lower()
        path = urlparse(dl.get("url", "")).path
        filename = os.path.basename(path).lower().replace("-", " ").replace("_", " ")
        combined = title_text + " " + filename
        title_items.append((dl["url"], combined, dl.get("source", "Hub"), dl.get("is_document", False)))

    body_items = []  # (url, body_text, source)
    for page in (found_pages or []):
        body_items.append((page["url"], page.get("snippet", "").lower(), "Website page"))
    for sr in (search_results or []):
        text = ((sr.get("title") or "") + " " + (sr.get("content") or "")).lower()
        body_items.append((sr.get("url", ""), text, "Web search"))

    # All text pooled for combined searches
    all_title_text = " ".join(t for _, t, _, _ in title_items)
    all_body_text = " ".join(b for _, b, _ in body_items)
    all_text = all_title_text + " " + all_body_text

    STATUS_ICONS = {"found": "✅ Found", "partial": "🔍 Partial", "not_located": "ℹ️ Not Located in Public Materials"}

    # ── Helper: find best evidence URL/source for a keyword list ──────
    def _find_evidence(keywords, tier="title"):
        """Search title_items or body_items for first keyword hit."""
        items = title_items if tier == "title" else [(u, b, s, False) for u, b, s in body_items]
        for url, text, source, is_doc in items:
            for kw in keywords:
                if kw.lower() in text:
                    dtype = "PDF/document" if is_doc else ("page" if tier == "title" else "page body text")
                    return url, f"'{kw}' in {dtype} ({source})", source
        return "", "", ""

    # ── Helper: find evidence with proximity analysis ─────────────────
    def _find_evidence_proximity(keywords, tier="body"):
        """Search body_items for keyword with proximity to policy anchor.
        Returns (url, evidence_str, source, proximity_level).
        proximity_level: 'high' | 'medium' | '' (not found).
        """
        best_prox = ""
        best_result = ("", "", "")
        items = body_items if tier == "body" else [(u, t, s) for u, t, s, _ in title_items]
        for url, text, source in items:
            for kw in keywords:
                prox = _check_proximity(text, kw)
                if prox and (not best_prox or (prox == "high" and best_prox != "high")):
                    best_prox = prox
                    dtype = "page body text" if tier == "body" else "page title"
                    best_result = (url, f"'{kw}' in {dtype} ({source})", source)
                if best_prox == "high":
                    break
            if best_prox == "high":
                break
        return (*best_result, best_prox)

    # ══════════════════════════════════════════════════════════════════
    # 1. SAFEGUARDING
    # ══════════════════════════════════════════════════════════════════
    sg_kw = _POLICY_KEYWORDS["Safeguarding"]
    sg_proc_kw = _SG_PROCEDURAL_KW

    sg = {"status": "not_located", "evidence": "", "source_url": "", "comment": "",
          "detection_confidence": "none"}

    # Tier 1a: Document title/filename match → Found (high)
    url, ev, src = _find_evidence(sg_kw, "title")
    if url:
        sg = {"status": "found", "source_url": url, "evidence": ev,
              "comment": f"Safeguarding policy document identified via {src.lower()}",
              "detection_confidence": "high"}
    else:
        # Tier 1b: Procedural detail in body text → Found (check proximity)
        url2, ev2, src2, prox2 = _find_evidence_proximity(sg_proc_kw, "body")
        if url2:
            conf = "high" if prox2 == "high" else "medium"
            sg = {"status": "found", "source_url": url2, "evidence": ev2,
                  "comment": "Safeguarding procedural detail found in page content",
                  "detection_confidence": conf}
        else:
            # Tier 2: High-level mention → Partial (check proximity for confidence)
            url3, ev3, src3, prox3 = _find_evidence_proximity(sg_kw, "body")
            if url3:
                conf = "medium" if prox3 == "high" else "low"
                sg = {"status": "partial", "source_url": url3, "evidence": ev3,
                      "comment": "Safeguarding mentioned at high level; no procedural detail or standalone document confirmed",
                      "detection_confidence": conf}
            else:
                sg["comment"] = (
                    "No safeguarding-related language could be located in public materials scanned "
                    "(website, policy hub, uploaded documents). The policy may exist internally."
                )

    # ══════════════════════════════════════════════════════════════════
    # 2. FINANCIAL CRIME (combined bribery + AML)
    # ══════════════════════════════════════════════════════════════════
    fc = {"status": "not_located", "evidence": "", "source_url": "", "comment": "",
          "detection_confidence": "none"}

    # Check across all text first to determine which halves are present
    has_bribery_title = any(kw in all_title_text for kw in _FC_BRIBERY_KW)
    has_aml_title = any(kw in all_title_text for kw in _FC_AML_KW)
    has_bribery_body = any(kw in all_body_text for kw in _FC_BRIBERY_KW)
    has_aml_body = any(kw in all_body_text for kw in _FC_AML_KW)

    has_bribery = has_bribery_title or has_bribery_body
    has_aml = has_aml_title or has_aml_body

    # Proximity analysis for FC body matches
    _fc_bribery_prox = max(
        (_check_proximity(all_body_text, kw) for kw in _FC_BRIBERY_KW),
        key=lambda x: {"high": 2, "medium": 1, "": 0}.get(x, 0), default=""
    ) if has_bribery_body else ""
    _fc_aml_prox = max(
        (_check_proximity(all_body_text, kw) for kw in _FC_AML_KW),
        key=lambda x: {"high": 2, "medium": 1, "": 0}.get(x, 0), default=""
    ) if has_aml_body else ""

    def _fc_confidence(has_title_b, has_title_a, prox_b, prox_a):
        """Derive overall financial crime detection confidence."""
        # Title matches are always high confidence
        scores = []
        if has_title_b:
            scores.append("high")
        elif prox_b:
            scores.append(prox_b)
        if has_title_a:
            scores.append("high")
        elif prox_a:
            scores.append(prox_a)
        if not scores:
            return "low"
        # Overall confidence is the minimum of the two halves
        rank = {"high": 2, "medium": 1, "low": 0}
        return min(scores, key=lambda x: rank.get(x, 0))

    # Check for a single combined document covering both
    combined_doc_url, combined_doc_ev = "", ""
    for url, text, source, is_doc in title_items:
        b_hit = any(kw in text for kw in _FC_BRIBERY_KW)
        a_hit = any(kw in text for kw in _FC_AML_KW)
        if b_hit and a_hit:
            combined_doc_url = url
            combined_doc_ev = f"Combined financial crime document ({source})"
            break

    if combined_doc_url:
        fc = {"status": "found", "source_url": combined_doc_url,
              "evidence": combined_doc_ev,
              "comment": "Combined anti-corruption, bribery & money laundering policy document identified",
              "detection_confidence": "high"}
    elif has_bribery_title and has_aml_title:
        url_b, ev_b, _ = _find_evidence(_FC_BRIBERY_KW, "title")
        url_a, ev_a, _ = _find_evidence(_FC_AML_KW, "title")
        fc = {"status": "found", "source_url": url_b or url_a,
              "evidence": f"Separate documents: {ev_b}; {ev_a}",
              "comment": "Both bribery/corruption and AML/CTF policy documents identified",
              "detection_confidence": "high"}
    elif has_bribery and has_aml:
        url_b, ev_b, _ = _find_evidence(_FC_BRIBERY_KW, "title") if has_bribery_title else _find_evidence(_FC_BRIBERY_KW, "body")
        url_a, ev_a, _ = _find_evidence(_FC_AML_KW, "title") if has_aml_title else _find_evidence(_FC_AML_KW, "body")
        conf = _fc_confidence(has_bribery_title, has_aml_title, _fc_bribery_prox, _fc_aml_prox)
        fc = {"status": "found", "source_url": url_b or url_a,
              "evidence": f"{ev_b}; {ev_a}",
              "comment": "Both bribery/corruption and money laundering coverage identified",
              "detection_confidence": conf}
    elif has_bribery or has_aml:
        side = "bribery/corruption" if has_bribery else "AML/money laundering"
        other = "AML/money laundering" if has_bribery else "bribery/corruption"
        kws = _FC_BRIBERY_KW if has_bribery else _FC_AML_KW
        url_p, ev_p, _ = _find_evidence(kws, "title") if (has_bribery_title or has_aml_title) else _find_evidence(kws, "body")
        _broader_fc_kw = ["financial crime", "fraud", "anti-fraud", "fraud policy",
                          "fraud prevention", "financial controls"]
        _other_side_in_broader = any(kw in all_text for kw in _broader_fc_kw)
        if _other_side_in_broader:
            prox_side = "high" if (has_bribery_title or has_aml_title) else (_fc_bribery_prox or _fc_aml_prox or "medium")
            fc = {"status": "found", "source_url": url_p,
                  "evidence": ev_p + "; broader financial crime/fraud coverage also present",
                  "comment": f"{side.title()} coverage confirmed; broader financial crime/fraud "
                             f"framework also identified (AML coverage not independently confirmed "
                             f"if applicable)",
                  "detection_confidence": prox_side if prox_side in ("high", "medium") else "medium"}
        else:
            fc = {"status": "partial", "source_url": url_p,
                  "evidence": ev_p,
                  "comment": f"{side.title()} coverage found but {other} not located; "
                             f"standalone combined financial crime policy not confirmed",
                  "detection_confidence": "medium"}
    else:
        _broader_fc_kw = ["financial crime", "fraud", "anti-fraud", "fraud policy",
                          "fraud prevention", "financial controls"]
        _broader_in_text = any(kw in all_text for kw in _broader_fc_kw)
        if _broader_in_text:
            url_f, ev_f, _ = _find_evidence(_broader_fc_kw, "title")
            if not url_f:
                url_f, ev_f, _ = _find_evidence(_broader_fc_kw, "body")
            fc = {"status": "partial", "source_url": url_f,
                  "evidence": ev_f or "Broader financial crime/fraud coverage present",
                  "comment": "Fraud or financial crime framework identified but specific "
                             "bribery/corruption and AML/CTF policies not individually confirmed",
                  "detection_confidence": "low"}
        else:
            fc["comment"] = (
                "No bribery, corruption, or money laundering policy document could be located "
                "in public materials scanned. The policy may exist internally or under a different title."
            )

    # ══════════════════════════════════════════════════════════════════
    # 3. RISK MANAGEMENT
    # ══════════════════════════════════════════════════════════════════
    rm_kw = _POLICY_KEYWORDS["Risk Management"]
    rm = {"status": "not_located", "evidence": "", "source_url": "", "comment": "",
          "detection_confidence": "none"}

    # Tier 1a: Document title/filename match → Found (high)
    url, ev, src = _find_evidence(rm_kw, "title")
    if url:
        rm = {"status": "found", "source_url": url, "evidence": ev,
              "comment": f"Risk management document identified via {src.lower()}",
              "detection_confidence": "high"}
    else:
        # Tier 1b: Structured risk detail in body → Found (proximity-aware)
        url2, ev2, src2, prox2 = _find_evidence_proximity(_RM_STRUCTURED_KW, "body")
        if url2:
            conf = "high" if prox2 == "high" else "medium"
            rm = {"status": "found", "source_url": url2, "evidence": ev2,
                  "comment": "Structured risk management process described in page/report content",
                  "detection_confidence": conf}
        else:
            # Tier 2: Generic mention → Partial (proximity-aware)
            url3, ev3, src3, prox3 = _find_evidence_proximity(rm_kw, "body")
            if url3:
                conf = "medium" if prox3 == "high" else "low"
                rm = {"status": "partial", "source_url": url3, "evidence": ev3,
                      "comment": "Risk management mentioned generically; no structured risk review or standalone document confirmed",
                      "detection_confidence": conf}
            else:
                rm["comment"] = (
                    "No risk management document or structured risk assessment could be located "
                    "in public materials scanned. The policy may exist internally."
                )

    # ══════════════════════════════════════════════════════════════════
    # HRCOB CORE CONTROL ASSESSMENT (Proportional / Advisory)
    # ══════════════════════════════════════════════════════════════════
    core_statuses = {
        "safeguarding": sg["status"],
        "financial_crime": fc["status"],
        "risk_management": rm["status"],
    }
    statuses = list(core_statuses.values())
    n_found = statuses.count("found")
    n_partial = statuses.count("partial")
    n_missing = statuses.count("not_located")

    if all(s == "found" for s in statuses):
        hrcob_status = "Satisfactory"
        hrcob_narrative = (
            "All three HRCOB core control areas (Safeguarding, Financial Crime, and Risk Management) "
            "are documented in publicly available materials. The governance framework appears "
            "structured and proportionate to the charity's size and operations."
        )
    elif n_missing == 0:
        hrcob_status = "Acceptable with Clarification"
        partial_areas = [k.replace("_", " ").title() for k, v in core_statuses.items() if v == "partial"]
        hrcob_narrative = (
            f"Core controls are present in public documentation, though "
            f"{', '.join(partial_areas)} lack{'s' if len(partial_areas) == 1 else ''} "
            "standalone procedural detail. Clarification from the charity would strengthen "
            "assurance. This observation is advisory and should be weighed alongside the "
            "charity's size, operational geography, and overall risk profile."
        )
    elif n_missing == 1:
        missing = [k.replace("_", " ").title() for k, v in core_statuses.items() if v == "not_located"]
        hrcob_status = "Clarification Recommended"
        hrcob_narrative = (
            f"One core control area ({', '.join(missing)}) could not be located in public "
            "materials scanned. The policy may exist internally or under a different title. "
            "Requesting documentation directly from the charity is recommended. This finding "
            "should be considered alongside other risk factors rather than treated as a "
            "standalone determinant of overall risk."
        )
    else:
        missing = [k.replace("_", " ").title() for k, v in core_statuses.items() if v == "not_located"]
        hrcob_status = "Further Enquiry Recommended"
        hrcob_narrative = (
            f"Multiple core control areas ({', '.join(missing)}) could not be located in "
            "public documentation. While these policies may exist internally, the absence "
            "of publicly accessible documentation across several mandatory areas warrants "
            "direct engagement with the charity. The analyst should consider this finding "
            "in context with the charity's size, geography, and other due-diligence factors "
            "when forming an overall risk view. This status reflects a gap in publicly "
            "available evidence and should not be interpreted as a governance failure."
        )

    # Add status icons
    for ctrl in [sg, fc, rm]:
        ctrl["status_icon"] = STATUS_ICONS[ctrl["status"]]

    return {
        "safeguarding": sg,
        "financial_crime": fc,
        "risk_management": rm,
        "hrcob_status": hrcob_status,
        "hrcob_narrative": hrcob_narrative,
    }


def search_policies(charity_name, website_url=""):
    """Search for policies using: 1) website crawl with hub detection + document
    link extraction, 2) domain-limited keyword searches, 3) broader web search.
    Returns (results, audit_trail, doc_links, policy_classification, social_links)."""
    results = []
    audit_trail = []
    doc_links = []
    social_links = {}
    found_pages = []

    # Method 1: Deep website crawl (common paths → hub detection → follow links)
    if website_url:
        found_pages, doc_links, scrape_audit, social_links = _scrape_policy_pages(website_url)
        hub_count = sum(1 for p in found_pages if p.get("is_hub"))
        doc_count = sum(1 for d in doc_links if d.get("is_document"))
        audit_trail.append({
            "method": "Website crawl: common paths + hub detection + nav scan",
            "urls_tried": len(scrape_audit),
            "pages_found": len(found_pages),
            "hubs_detected": hub_count,
            "document_links_discovered": doc_count,
            "total_links_discovered": len(doc_links),
            "details": scrape_audit,
        })
        for page in found_pages:
            results.append({
                "title": f"{'Policy Hub' if page['is_hub'] else 'Policy page'}: {page['url']}",
                "url": page["url"],
                "content": page["snippet"],
            })

    # Method 2: Domain-limited keyword search per policy type (batched)
    if website_url:
        clean_url = website_url.rstrip('/')
        keyword_groups = [
            "safeguarding anti-bribery money laundering risk management whistleblowing",
            "data protection GDPR privacy modern slavery health safety",
            "equal opportunities grievance disciplinary harassment complaints",
        ]
        for group in keyword_groups:
            tavily_site = tavily_search(
                f"site:{clean_url} {group}",
                depth="advanced", max_results=5,
            )
            results += tavily_site
            audit_trail.append({
                "method": f"Tavily site-specific search (site:{clean_url})",
                "keywords": group,
                "results_count": len(tavily_site),
            })

    # Method 3: Broader web search
    q = (f'"{charity_name}" AND ("safeguarding policy" OR "anti-bribery" OR '
         f'"money laundering policy" OR "risk management" OR "whistleblowing" OR '
         f'"data protection policy" OR "compliance framework" OR "policies")')
    tavily_broad = tavily_search(q, depth="basic", max_results=5)
    results += tavily_broad
    audit_trail.append({
        "method": "Tavily broad web search for policy mentions",
        "query": q[:200],
        "results_count": len(tavily_broad),
    })

    # ── Classify each policy — two-tier keyword classifier ───────────
    # Combine crawled pages with Tavily search result pages
    all_pages = list(found_pages)
    for r in results:
        if r.get("url"):
            all_pages.append({"url": r["url"], "snippet": r.get("content", "")})

    policy_classification = _classify_policies(all_pages, doc_links, results)
    hrcob_core_controls = _classify_core_controls(all_pages, doc_links, results)

    return results, audit_trail, doc_links, policy_classification, social_links, hrcob_core_controls


def search_partnerships(charity_name, website_url=""):
    """Search for partner organisations, 3rd-party relationships.
    Returns (results, audit_trail)."""
    audit_trail = []
    results = []

    # Method 1: site-specific partner search
    if website_url:
        clean = website_url.rstrip('/')
        site_results = tavily_search(
            f"site:{clean} partner project programme team country",
            depth="basic", max_results=3,
        )
        results += site_results
        audit_trail.append({
            "method": f"Tavily site search (site:{clean}) for partners",
            "results_count": len(site_results),
        })

    # Method 2: Broader web search
    q = (f'"{charity_name}" AND ("partner organisation" OR "delivery partner" OR '
         f'"local partner" OR "implementing partner" OR "MoU" OR '
         f'"due diligence on partners" OR "partner vetting")')
    broad = tavily_search(q, depth="advanced", max_results=5)
    results += broad
    audit_trail.append({
        "method": "Tavily broad web search for partnership mentions",
        "query": q[:200],
        "results_count": len(broad),
    })

    return results, audit_trail


def search_country_risk_batch(country_list):
    if not country_list:
        return []
    names = ", ".join(country_list[:20])
    return tavily_search(
        f"Know Your Country risk profile Basel AML Index for: {names}. "
        f"Include risk rating, key concerns.",
        depth="advanced", max_results=8,
    )


def search_country_kyc_profile(country_name):
    return tavily_search(
        f"site:knowyourcountry.com {country_name} country summary sanctions FATF "
        f"terrorism corruption criminal markets AML risk rating",
        depth="advanced", max_results=3,
    )


# ═══════════════════════════════════════════════════════════════════════════════
# HYBRID ADVERSE MEDIA — Tavily + Serper (Google News) combined
# ═══════════════════════════════════════════════════════════════════════════════

def search_adverse_media_hybrid(name, context_terms=None, is_fca_regulated=False):
    """Search for adverse media using BOTH Tavily AND Serper (Google News).

    Runs Tavily's standard adverse media search, then supplements with
    Serper's Google News results.  De-duplicates by URL.  This gives much
    broader coverage: Tavily is good at deep web content, Serper is superb
    at recent news articles and regulatory enforcement notices.

    For FCA-regulated entities, uses FCA-specific search keywords and higher
    sensitivity scoring across both providers.

    Falls back gracefully: if one provider fails, the other still works.
    """
    # ── Tavily results (existing logic) ───────────────────────────────
    tavily_results = []
    tavily_error = None
    try:
        tavily_results = search_adverse_media(name, context_terms, is_fca_regulated)
    except Exception as e:
        tavily_error = e

    # ── Serper results (Google News) ──────────────────────────────────
    serper_results = []
    serper_error = None
    try:
        from api_clients.serper_search import search_adverse_media_serper
        serper_results = search_adverse_media_serper(name, context_terms, is_fca_regulated)
    except Exception as e:
        serper_error = e

    # ── Merge & de-duplicate ──────────────────────────────────────────
    merged = list(tavily_results)
    seen_urls = {r.get("url") for r in merged if r.get("url")}

    for r in serper_results:
        url = r.get("url", "")
        if url and url not in seen_urls:
            seen_urls.add(url)
            merged.append(r)

    # If both failed completely, surface the error
    if tavily_error and serper_error and not merged:
        raise tavily_error  # re-raise so _search_errors catches it

    return merged
