"""
core/company_check.py — Company Sense-Check Analysis Engine.

Orchestrates deep-dive analysis of a UK company using Companies House data
and website intelligence.  Produces structured risk findings that feed into
an LLM prompt for a final narrative report.

Public API
----------
run_company_check(company_num, website_url, *, tavily_search_fn, fatf_screen_fn)
    → dict  — full analysis bundle ready for LLM + dashboard.
"""

from __future__ import annotations

import re
from datetime import datetime, date, timezone
from typing import Any, Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import urlparse

from api_clients.companies_house import (
    fetch_company_full_profile,
    fetch_company_officers,
    fetch_company_pscs,
    fetch_company_filing_history,
    fetch_company_charges,
    analyse_company_age,
    detect_virtual_office,
    classify_sic_risk,
    analyse_directors,
    detect_dormancy_risk,
    extract_accounts_data,
    trace_ubo_chain,
)
from core.fca_context import FCAContext
from core.uk_fraud_detection import run_uk_fraud_detection_suite


# ═══════════════════════════════════════════════════════════════════════════════
# WEBSITE CREDIBILITY ASSESSMENT
# ═══════════════════════════════════════════════════════════════════════════════

def _normalise(text: str) -> str:
    """Lowercase, strip punctuation, collapse whitespace."""
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s]", " ", text.lower())).strip()


# Patterns that indicate placeholder / template / low-quality content
_PLACEHOLDER_PATTERNS: list[tuple[str, str]] = [
    (r"lorem ipsum", "Lorem Ipsum placeholder text detected"),
    (r"coming soon", "\"Coming Soon\" placeholder detected"),
    (r"under construction", "Website listed as \"Under Construction\""),
    (r"sample page", "Sample / template page detected"),
    (r"your company name", "Generic template placeholder (\"Your Company Name\")"),
    (r"example\.com", "example.com placeholder link detected"),
    (r"this is a demo", "Demo site text detected"),
    (r"powered by starter", "Starter template indicator detected"),
    (r"hello world", "Default \"Hello World\" content detected"),
    (r"just another wordpress", "Default WordPress tagline detected"),
]

# Social media domains we look for in links / text
_SOCIAL_PLATFORMS = {
    "linkedin.com": "LinkedIn",
    "twitter.com": "Twitter / X",
    "x.com": "Twitter / X",
    "facebook.com": "Facebook",
    "instagram.com": "Instagram",
    "youtube.com": "YouTube",
    "tiktok.com": "TikTok",
    "trustpilot.com": "Trustpilot",
    "glassdoor.co.uk": "Glassdoor",
    "glassdoor.com": "Glassdoor",
    "github.com": "GitHub",
    "pinterest.com": "Pinterest",
}

# Professional content indicators
_PROFESSIONAL_KEYWORDS: list[tuple[str, str]] = [
    (r"\babout\s+us\b", "About Us page / section"),
    (r"\bcontact\s+us\b", "Contact Us page / section"),
    (r"\bour\s+team\b|\bmeet\s+the\s+team\b|\bour\s+people\b", "Team / Staff section"),
    (r"\bcase\s+stud(?:y|ies)\b", "Case Studies"),
    (r"\btestimonial", "Testimonials / Reviews"),
    (r"\bblog\b|\bnews\b|\barticle", "Blog / News section"),
    (r"\bfaq\b|\bfrequently\s+asked\b", "FAQ section"),
    (r"\bterms\s+(?:and|&)\s+conditions\b|\bterms\s+of\s+(?:service|use)\b", "Terms & Conditions"),
    (r"\bprivacy\s+policy\b", "Privacy Policy"),
    (r"\brefund\s+policy\b|\breturns?\s+policy\b", "Refund / Returns Policy"),
    (r"\bcookie\s+(?:policy|consent|notice)\b", "Cookie Policy / Consent"),
    (r"\baccreditation|accredited|iso\s*\d{4}", "Accreditation / Quality Standard"),
    (r"\bpartner(?:s|ship)\b", "Partnerships section"),
    (r"\bcareer|vacancies|job(?:s| opening)", "Careers / Jobs section"),
]


def cross_reference_website(
    profile: dict,
    website_search_results: list[dict],
    website_url: str,
) -> dict:
    """Assess website credibility, quality and depth of information.

    Instead of comparing website content against CH registry data
    (which are fundamentally different things), this evaluates:
    - Content depth and quality
    - Template / placeholder detection
    - Social media presence
    - Professional indicators (T&Cs, About, Team, etc.)
    - Domain characteristics
    - Contact information depth

    Returns credibility findings — no alignment/match scores.
    """
    positives: list[str] = []
    red_flags: list[str] = []
    findings: list[str] = []
    social_links: dict[str, str] = {}

    # Build a blob of all website text
    web_text_parts = []
    page_urls: list[str] = []
    for r in website_search_results:
        content = r.get("content") or ""
        title = r.get("title") or ""
        url = r.get("url") or ""
        web_text_parts.append(f"{content} {title}")
        if url:
            page_urls.append(url)

    web_text = " ".join(web_text_parts)
    web_text_lower = web_text.lower()

    if not web_text.strip():
        return {
            "credibility_level": "Unknown",
            "positives": [],
            "red_flags": ["No website content could be retrieved for analysis"],
            "findings": ["Website may be down, blocking crawlers, or not indexed"],
            "social_links": {},
            "content_depth": {
                "total_words": 0,
                "pages_found": 0,
                "has_substantial_content": False,
            },
            "professional_indicators": [],
            "domain_info": {},
        }

    # ── Content depth analysis ────────────────────────────────────────
    total_words = len(web_text.split())
    pages_found = len(website_search_results)
    has_substantial = total_words > 300

    content_depth = {
        "total_words": total_words,
        "pages_found": pages_found,
        "has_substantial_content": has_substantial,
    }

    if total_words > 1000:
        positives.append(
            f"Website has substantial content ({total_words:,} words "
            f"across {pages_found} indexed pages)"
        )
    elif total_words > 300:
        findings.append(
            f"Website has moderate content ({total_words:,} words "
            f"across {pages_found} indexed pages)"
        )
    else:
        findings.append(
            f"Website has minimal content ({total_words:,} words "
            f"across {pages_found} indexed pages) — consistent with a "
            f"B2B / Lead-Generation profile or Holding Company page"
        )

    # ── Template / placeholder detection ──────────────────────────────
    for pattern, label in _PLACEHOLDER_PATTERNS:
        if re.search(pattern, web_text_lower):
            red_flags.append(label)

    # ── Social media presence ─────────────────────────────────────────
    # Only extract social links from search results when we have a website URL.
    # When no website is provided, the search results are generic web pages
    # about the company, and any social URLs found are likely from articles,
    # tweets, or posts MENTIONING the company — not the company's own profiles.
    # The OSINT pivot (Phase 4.2) handles no-website social discovery with
    # proper profile-URL validation.
    if website_url:
        full_text_for_links = " ".join(
            (r.get("raw_content") or r.get("content") or "")
            + " " + (r.get("url") or "")
            for r in website_search_results
        )

        # Non-profile path patterns to reject (tweets, posts, hashtags, etc.)
        _social_non_profile_segments = {
            "twitter.com": {"/status/", "/statuses/", "/hashtag/", "/search", "/i/",
                            "/explore", "/lists/", "/moments/", "/events/", "/intent/"},
            "x.com":       {"/status/", "/statuses/", "/hashtag/", "/search", "/i/",
                            "/explore", "/lists/", "/moments/", "/events/", "/intent/"},
            "facebook.com": {"/posts/", "/photos/", "/videos/", "/events/", "/stories/",
                             "/watch/", "/groups/", "/marketplace/"},
            "instagram.com": {"/p/", "/reel/", "/reels/", "/stories/", "/explore/", "/tv/"},
        }

        for domain_str, platform in _SOCIAL_PLATFORMS.items():
            url_pattern = re.compile(
                r"https?://(?:www\.)?" + re.escape(domain_str) + r"[/\w\-_.%]*",
                re.IGNORECASE,
            )
            match = url_pattern.search(full_text_for_links)
            if match:
                found_url = match.group(0)
                # Reject non-profile URLs (tweets, posts, hashtags, etc.)
                _blocked = _social_non_profile_segments.get(domain_str, set())
                if any(seg in found_url.lower() for seg in _blocked):
                    continue  # Skip this match — it's a post/tweet, not a profile
                social_links[platform] = found_url
            # NOTE: Removed the bare-domain fallback (e.g. "https://twitter.com")
            # which was triggered just because the domain name appeared in text.
            # A bare domain with no path is useless as a social profile link.

        if len(social_links) >= 3:
            positives.append(
                f"Strong social media presence — {len(social_links)} platforms "
                f"linked: {', '.join(sorted(social_links.keys()))}"
            )
        elif len(social_links) >= 1:
            findings.append(
                f"Social media presence found on {len(social_links)} "
                f"platform(s): {', '.join(sorted(social_links.keys()))}"
            )
        else:
            findings.append(
                "No social media links detected on the website — "
                "many B2B and trade businesses do not maintain public profiles"
            )
    else:
        findings.append(
            "No website provided — social media discovery deferred "
            "to OSINT search with profile-URL validation"
        )

    # ── Professional content indicators ───────────────────────────────
    professional_found: list[str] = []
    for pattern, label in _PROFESSIONAL_KEYWORDS:
        if re.search(pattern, web_text_lower):
            professional_found.append(label)

    if len(professional_found) >= 5:
        positives.append(
            f"Website shows {len(professional_found)} professional "
            f"content indicators: {', '.join(professional_found[:6])}"
        )
    elif len(professional_found) >= 2:
        findings.append(
            f"{len(professional_found)} professional content sections "
            f"detected: {', '.join(professional_found)}"
        )
    elif professional_found:
        findings.append(
            f"Only 1 professional content indicator: {professional_found[0]}"
        )
    else:
        findings.append(
            "No standard professional content pages detected — "
            "site may serve as a simple digital business card or "
            "B2B lead-generation landing page"
        )

    # ── Contact information depth ─────────────────────────────────────
    contact_items: list[str] = []

    # Phone
    uk_phone = re.search(
        r"(?:0\d{2,4}\s?\d{3,4}\s?\d{3,4}|\+44\s?\d[\d\s]{8,})", web_text,
    )
    if uk_phone:
        contact_items.append("Phone number")

    # Email
    email_match = re.search(
        r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}", web_text,
    )
    if email_match:
        contact_items.append("Email address")

    # Physical address (UK postcode as proxy)
    if re.search(r"[A-Z]{1,2}\d[A-Z\d]?\s*\d[A-Z]{2}", web_text, re.IGNORECASE):
        contact_items.append("Physical address (UK postcode)")

    if len(contact_items) >= 3:
        positives.append(f"Full contact details provided: {', '.join(contact_items)}")
    elif contact_items:
        findings.append(f"Partial contact details: {', '.join(contact_items)}")
    else:
        findings.append(
            "No contact details (phone, email, or address) were found on the website — "
            "some companies list these only on internal portals or social media"
        )

    # ── VAT / Company number (optional trust signals) ─────────────────
    co_num = profile.get("company_number", "")
    vat_match = re.search(r"(?:vat|gb)\s*(?:no\.?|number|reg)?\s*\d{9}", web_text_lower)
    if vat_match:
        positives.append("VAT number displayed on website — adds credibility")
    if co_num and co_num in web_text:
        positives.append(
            f"Company registration number ({co_num}) displayed — "
            f"transparent business practice"
        )

    # ── Domain analysis ───────────────────────────────────────────────
    domain_info: dict = {}
    if website_url:
        parsed = urlparse(
            website_url if "://" in website_url else f"https://{website_url}",
        )
        domain = parsed.netloc or parsed.path
        domain = domain.lower().lstrip("www.")
        tld = domain.split(".")[-1] if "." in domain else ""
        full_tld = ".".join(domain.split(".")[-2:]) if domain.count(".") >= 2 else tld

        domain_info["domain"] = domain
        domain_info["tld"] = full_tld or tld

        # TLD reputation
        reputable_tlds = {
            "co.uk", "org.uk", "gov.uk", "ac.uk", "nhs.uk",
            "com", "org", "net", "io",
        }
        risky_tlds = {"xyz", "top", "click", "buzz", "gq", "ml", "tk", "cf", "ga"}

        if full_tld in reputable_tlds or tld in reputable_tlds:
            positives.append(f"Domain uses reputable TLD (.{full_tld})")
        elif tld in risky_tlds:
            red_flags.append(
                f"Domain uses high-risk free/cheap TLD (.{tld}) — "
                f"often associated with disposable or fraudulent sites"
            )
        else:
            findings.append(f"Domain TLD: .{full_tld or tld}")

        # Check if domain name roughly matches company name
        company_name = _normalise(profile.get("company_name") or "")
        domain_stem = domain.split(".")[0].replace("-", " ").replace("_", " ")
        if company_name and domain_stem:
            # Simple containment check
            name_words = set(company_name.split()) - {
                "ltd", "limited", "plc", "llp", "the", "and", "of", "uk",
            }
            domain_words = set(domain_stem.split())
            overlap = name_words & domain_words
            if overlap and len(overlap) >= min(2, len(name_words)):
                positives.append(
                    f"Domain name ({domain}) relates to the company name"
                )

    # ── Determine credibility level ───────────────────────────────────
    pos_count = len(positives)
    flag_count = len(red_flags)

    if flag_count == 0 and pos_count >= 4:
        credibility = "High"
    elif flag_count <= 1 and pos_count >= 2:
        credibility = "Good"
    elif flag_count >= 3 or (flag_count >= 2 and pos_count == 0):
        credibility = "Low"
    elif flag_count >= 1 and pos_count <= 1:
        credibility = "Weak"
    else:
        credibility = "Moderate"

    return {
        "credibility_level": credibility,
        "positives": positives,
        "red_flags": red_flags,
        "findings": findings,
        "social_links": social_links,
        "content_depth": content_depth,
        "professional_indicators": professional_found,
        "domain_info": domain_info,
    }


# ═══════════════════════════════════════════════════════════════════════════════
# SIC-vs-WEBSITE ACTIVITY MISMATCH
# ═══════════════════════════════════════════════════════════════════════════════

# Industry-category → keywords you'd expect on a website operating in that space
_INDUSTRY_WEBSITE_SIGNALS: dict[str, list[str]] = {
    "Financial Services": [
        "loan", "mortgage", "investment", "forex", "trading", "banking",
        "lending", "credit", "finance", "wealth management", "fintech",
        "payments", "crypto", "currency",
    ],
    "Insurance": [
        "insurance", "policy", "underwriting", "claims", "premium",
        "cover", "indemnity", "life insurance", "motor insurance",
    ],
    "Insurance Services": [
        "insurance", "broker", "claims", "underwriting",
    ],
    "Gambling & Betting": [
        "bet", "gambl", "casino", "odds", "wager", "slot", "poker",
        "lottery", "bingo", "sportsbetting",
    ],
    "Adult / Entertainment": [
        "adult", "entertainment", "streaming", "content creator",
        "subscription", "dating", "escort",
    ],
    "Travel & Tourism": [
        "travel", "flight", "hotel", "holiday", "booking", "tour",
        "vacation", "resort", "cruise", "airport",
    ],
    "Pharmaceuticals": [
        "pharmacy", "prescription", "medicine", "drug", "chemist",
        "pharmaceutical", "health", "dispensing",
    ],
    "Tobacco & Vaping": [
        "tobacco", "vape", "vaping", "e-cigarette", "nicotine", "smoking",
    ],
    "E-Commerce / Mail Order": [
        "shop", "buy", "cart", "basket", "checkout", "delivery",
        "product", "order", "shipping", "store",
    ],
    "IT & Software": [
        "software", "saas", "cloud", "api", "platform", "app",
        "development", "it ", "tech", "digital", "data",
    ],
    "Professional Services": [
        "consult", "advisory", "legal", "solicitor", "accountant",
        "audit", "tax", "advice", "management",
    ],
    "Construction": [
        "construction", "building", "builder", "renovation", "project",
        "architect", "property", "development", "planning",
    ],
    "Real Estate": [
        "property", "estate", "letting", "rental", "tenant", "landlord",
        "residential", "commercial", "conveyancing",
    ],
    "Hospitality": [
        "restaurant", "hotel", "food", "catering", "dining", "bar",
        "pub", "cafe", "cuisine", "menu",
    ],
    "Wholesale": [
        "wholesale", "distribution", "supply", "trade", "b2b",
        "bulk", "supplier",
    ],
    "Retail": [
        "retail", "shop", "store", "buy", "product",
    ],
    "Education": [
        "education", "training", "course", "learning", "tutor",
        "school", "university", "qualification", "accredit",
    ],
    "Manufacturing": [
        "manufactur", "production", "factory", "component",
        "assembly", "engineering",
    ],
    "Healthcare": [
        "health", "clinic", "patient", "medical", "care", "nhs",
        "therapy", "wellbeing", "treatment",
    ],
    "Charity / Non-Profit": [
        "charity", "donate", "non-profit", "nonprofit", "volunteer",
        "foundation", "community", "fundrais",
    ],
    "Marketing & Advertising": [
        "marketing", "advertising", "campaign", "media", "brand",
        "creative", "agency", "seo", "social media",
    ],
    "Recruitment": [
        "recruit", "staffing", "job", "career", "candidate",
        "employment", "hire", "placement", "vacancy",
    ],
    "Sports & Fitness": [
        "gym", "fitness", "sport", "training", "exercise",
        "membership", "coach", "health club",
    ],
    "Leisure & Recreation": [
        "leisure", "recreation", "entertainment", "fun", "activity",
        "adventure", "park", "attraction",
    ],
}


def check_sic_website_mismatch(
    sic_risk: dict,
    website_search_results: list[dict],
) -> dict:
    """Check if the company's SIC-declared industry aligns with its website.

    This is NOT about CH-registry-vs-website comparison. It checks whether
    the website content matches the type of business the SIC codes describe.
    For example: SIC says 'IT consultancy' but the website sells jewellery.

    Returns a mismatch analysis dict.
    """
    classifications = sic_risk.get("industry_classifications", [])
    if not classifications:
        return {
            "mismatch_detected": False,
            "note": "No SIC classifications to compare against website",
        }

    web_text = " ".join(
        (r.get("content") or "") + " " + (r.get("title") or "")
        for r in website_search_results
    ).lower()

    if not web_text.strip():
        return {
            "mismatch_detected": False,
            "note": "No website content available for SIC activity comparison",
        }

    results: list[dict] = []
    any_mismatch = False

    for cls in classifications:
        industry = cls.get("industry", "General")
        signals = _INDUSTRY_WEBSITE_SIGNALS.get(industry, [])
        if not signals:
            results.append({
                "sic_code": cls.get("code"),
                "industry": industry,
                "alignment": "not_checked",
                "note": f"No website signal keywords defined for '{industry}'",
            })
            continue

        hits = [s for s in signals if s in web_text]
        hit_ratio = len(hits) / len(signals) if signals else 0

        if hit_ratio >= 0.15:  # At least 15% of signals found
            results.append({
                "sic_code": cls.get("code"),
                "industry": industry,
                "alignment": "aligned",
                "hits": len(hits),
                "note": (
                    f"Website content aligns with {industry} "
                    f"({len(hits)}/{len(signals)} industry keywords found)"
                ),
            })
        else:
            any_mismatch = True
            results.append({
                "sic_code": cls.get("code"),
                "industry": industry,
                "alignment": "mismatch",
                "hits": len(hits),
                "note": (
                    f"SIC indicates '{industry}' but website content does not "
                    f"reflect this activity ({len(hits)}/{len(signals)} keywords). "
                    f"The company may trade in a different sector or use the "
                    f"website for a subsidiary activity."
                ),
            })

    return {
        "mismatch_detected": any_mismatch,
        "activity_checks": results,
        "note": (
            "SIC-declared industry does not match website activity — "
            "verify the company's actual line of business"
            if any_mismatch
            else "Website content broadly aligns with SIC-declared industry"
        ),
    }


# ═══════════════════════════════════════════════════════════════════════════════
# STATUS & GOVERNANCE ANALYSIS
# ═══════════════════════════════════════════════════════════════════════════════

_CONCERNING_STATUSES = {
    "active - proposal to strike off": "Company has an active proposal to be struck off the register — high risk",
    "liquidation": "Company is in liquidation — all dealings should be halted",
    "administration": "Company is in administration — subject to insolvency procedures",
    "dissolved": "Company has been dissolved — no longer exists as a legal entity",
    "converted-closed": "Company has been converted or closed",
    "insolvency-proceedings": "Company is subject to insolvency proceedings",
    "voluntary-arrangement": "Company has entered a voluntary arrangement with creditors",
    "receivership": "Company is in receivership",
}


def analyse_company_status(profile: dict) -> dict:
    """Analyse company status for risk flags."""
    status = (profile.get("company_status") or "").lower()
    has_been_liquidated = profile.get("has_been_liquidated", False)
    has_charges = profile.get("has_charges", False)
    has_insolvency_history = profile.get("has_insolvency_history", False)

    flags = []
    risk_level = "low"

    if status in _CONCERNING_STATUSES:
        flags.append(_CONCERNING_STATUSES[status])
        risk_level = "high"
    elif status != "active":
        flags.append(f"Company status is '{status}' — not standard active")
        risk_level = "medium"

    if has_been_liquidated:
        flags.append("Company has a history of liquidation")
        risk_level = "high"
    if has_insolvency_history:
        flags.append("Company has insolvency history on record")
        if risk_level != "high":
            risk_level = "medium"

    return {
        "status": profile.get("company_status", ""),
        "risk_level": risk_level,
        "flags": flags,
        "has_charges": has_charges,
        "has_insolvency_history": has_insolvency_history,
        "has_been_liquidated": has_been_liquidated,
    }


def _parse_ownership_band(natures: list[str]) -> str | None:
    """Extract the ownership-percentage band from natures_of_control strings.

    Companies House uses fixed descriptors like:
      'ownership-of-shares-25-to-50-percent'
      'ownership-of-shares-75-to-100-percent'
    Returns a human-readable band such as '25–50 %' or None.
    """
    import re
    for n in natures:
        m = re.search(r"(\d+)-to-(\d+)-percent", n)
        if m:
            return f"{m.group(1)}–{m.group(2)}%"
    return None


def analyse_pscs(pscs: list[dict]) -> dict:
    """Analyse Persons of Significant Control for risk indicators.

    Properly separates **active** and **ceased** PSCs so that ownership
    percentages are reported only for active persons and cannot exceed
    100 %.  Ceased persons are kept for historical reference but clearly
    labelled and excluded from risk calculations.
    """
    if not pscs:
        return {"psc_count": 0, "active_count": 0, "ceased_count": 0,
                "flags": [], "psc_details": [],
                "note": "No PSCs found — may be exempt or not yet filed"}

    from config import get_country_risk, is_elevated_risk

    flags = []
    psc_details = []
    active_count = 0
    ceased_count = 0

    for psc in pscs:
        _ne = psc.get("name_elements") or {}
        name = psc.get("name") or " ".join(
            p for p in (_ne.get("forename", ""), _ne.get("surname", "")) if p
        ) or "Unknown"
        nationality = psc.get("nationality", "")
        country = psc.get("country_of_residence", "")
        natures = psc.get("natures_of_control", [])
        kind = psc.get("kind", "")
        ceased_on = psc.get("ceased_on", "")  # e.g. "2023-01-15"
        notified_on = psc.get("notified_on", "")
        is_ceased = bool(ceased_on)

        if is_ceased:
            ceased_count += 1
        else:
            active_count += 1

        ownership_band = _parse_ownership_band(natures)

        nat_risk = get_country_risk(nationality) if nationality else ""
        country_risk = get_country_risk(country) if country else ""

        psc_flags = []
        # Only flag active PSCs for risk purposes
        if not is_ceased:
            if nationality and is_elevated_risk(nat_risk):
                psc_flags.append(f"PSC nationality '{nationality}' classified as {nat_risk}")
            if country and is_elevated_risk(country_risk):
                psc_flags.append(f"PSC country of residence '{country}' classified as {country_risk}")
            if "corporate" in kind.lower():
                psc_flags.append("PSC is a corporate entity — reduced transparency")

        psc_details.append({
            "name": name,
            "nationality": nationality,
            "country_of_residence": country,
            "natures_of_control": natures,
            "ownership_band": ownership_band,
            "kind": kind,
            "ceased": is_ceased,
            "ceased_on": ceased_on,
            "notified_on": notified_on,
            "flags": psc_flags,
        })
        flags.extend(psc_flags)

    note = f"{len(flags)} active-PSC risk flag(s)" if flags else "No active-PSC risk flags"
    if ceased_count:
        note += f" · {ceased_count} ceased PSC(s) excluded from risk calculation"

    return {
        "psc_count": len(pscs),
        "active_count": active_count,
        "ceased_count": ceased_count,
        "psc_details": psc_details,
        "flags": flags,
        "note": note,
    }


# ═══════════════════════════════════════════════════════════════════════════════
# RESTRICTED ACTIVITIES DETECTOR (payment processing policy)
# ═══════════════════════════════════════════════════════════════════════════════
# Each entry: (category_label, [keywords found in website/SIC], severity)
# severity: "prohibited" = hard block, "restricted" = needs prior agreement

_RESTRICTED_ACTIVITY_RULES: list[tuple[str, list[str], str]] = [
    # ── Absolute prohibitions ─────────────────────────────────────────
    ("Gambling & Betting",
     ["gambling", "betting", "casino", "lottery", "prize draw",
      "slots", "poker", "bingo", "wager", "odds", "sportsbetting",
      "bookmaker", "sweepstake"],
     "prohibited"),
    ("Cryptocurrency",
     ["crypto", "cryptocurrency", "bitcoin", "ethereum", "blockchain",
      "nft", "defi", "token sale", "ico ", "crypto wallet",
      "crypto exchange", "digital currency", "web3"],
     "prohibited"),
    ("Adult Entertainment",
     ["adult entertainment", "escort", "adult content", "pornograph",
      "webcam model", "onlyfans", "adult dating", "xxx",
      "erotic", "strip club"],
     "prohibited"),
    ("Weapons & Firearms",
     ["firearms", "guns", "ammunition", "weapons", "gun shop",
      "arms dealer", "tactical weapon", "rifle", "shotgun"],
     "prohibited"),
    ("Narcotics & Paraphernalia",
     ["cannabis", "marijuana", "weed", "cbd oil", "hemp",
      "narcotics", "drug paraphernalia", "psychedelic",
      "mushroom", "vape liquid", "e-liquid nicotine"],
     "prohibited"),
    ("Pyramid / Ponzi / MLM",
     ["pyramid scheme", "ponzi", "multi-level marketing", "mlm",
      "get rich quick", "network marketing", "referral income",
      "passive income opportunity", "downline"],
     "prohibited"),
    ("Shell / Front Companies",
     ["shell company", "front company", "nominee director",
      "company formation agent", "offshore company", "domiciliation"],
     "prohibited"),
    ("Counterfeit Goods",
     ["counterfeit", "replica", "knock-off", "fake designer",
      "imitation brand"],
     "prohibited"),
    ("Sanctions Evasion",
     ["sanctions evasion", "sanctioned country", "ofac",
      "asset freeze", "designated person"],
     "prohibited"),

    # ── Restricted (needs prior agreement) ────────────────────────────
    ("Payday Loans / Predatory Lending",
     ["payday loan", "payday lending", "short-term loan",
      "high-cost credit", "guarantor loan", "logbook loan"],
     "restricted"),
    ("Money Remittance / MSB",
     ["money transfer", "remittance", "money service",
      "foreign exchange", "fx broker", "currency exchange",
      "hawala", "money transmission"],
     "restricted"),
    ("Spread Betting / CFDs / FX Derivatives",
     ["spread betting", "cfd", "contract for difference",
      "forex trading", "fx derivative", "binary option",
      "leveraged trading"],
     "restricted"),
    ("Debt Recovery",
     ["debt recovery", "debt collection", "bailiff",
      "enforcement agent", "debt purchase"],
     "restricted"),
    ("Unregulated Financial Advice",
     ["unregulated financial", "unregulated investment",
      "unregulated advice", "unregulated fund"],
     "restricted"),
    ("Crowdfunding / Investment Opportunity",
     ["crowdfunding", "equity crowdfunding", "investment opportunity",
      "business opportunity", "angel invest", "seed funding platform"],
     "restricted"),
    ("Online Dating / Marriage Services",
     ["online dating", "dating app", "matchmaking",
      "marriage agency", "dating service"],
     "restricted"),
    ("Telemarketing / Fake Engagement",
     ["telemarketing", "outbound calling", "buy followers",
      "buy likes", "fake engagement", "social media bot"],
     "restricted"),
    ("Travel Industry",
     ["travel agent", "tour operator", "flight booking",
      "holiday package", "cruise", "travel company",
      "package holiday", "atol", "abta"],
     "restricted"),
    ("Tobacco & Alcohol",
     ["tobacco", "cigarette", "cigar", "alcohol", "spirits",
      "wine merchant", "brewery", "distillery", "off-licence",
      "vaping", "e-cigarette"],
     "restricted"),
    ("Chemicals & Hazardous Substances",
     ["chemicals", "hazardous substance", "chemical supplier",
      "industrial chemical", "acid", "solvent"],
     "restricted"),
    ("Prescription Medication",
     ["prescription", "online pharmacy", "medication",
      "controlled drug", "pharmaceutical dispensing"],
     "restricted"),
    ("Stored Value / E-Wallets",
     ["e-wallet", "prepaid card", "stored value", "gift card",
      "prepaid phone", "voucher", "digital wallet"],
     "restricted"),
    ("Timeshares / Delayed Delivery",
     ["timeshare", "holiday ownership", "fractional ownership",
      "pre-order", "made to order", "bespoke furniture",
      "significantly later date"],
     "restricted"),
    ("Trust & Offshore Services",
     ["trust service", "offshore company", "company formation",
      "registered agent", "domiciliation", "nominee service"],
     "restricted"),
    ("Dietary Supplements / Seeds / Plants",
     ["dietary supplement", "protein powder", "weight loss pill",
      "herbal supplement", "seed bank", "plant nursery"],
     "restricted"),
    ("Precious Metals / Jewellery",
     ["precious metal", "gold bullion", "silver bullion",
      "diamond", "gemstone", "jewellery dealer",
      "pawnbroker", "gold dealer"],
     "restricted"),
]


# ── Negation / disclaimer prefixes that reduce confidence ────────────────────
_NEGATION_PREFIXES = [
    "not a ", "not an ", "is not ", "are not ", "we don't ", "we do not ",
    "does not ", "do not ", "never ", "unlike ", "not involved in ",
    "not associated with ", "not related to ", "avoid ", "against ",
    "anti-", "prohibit", "complain about", "warn about", "warning against",
    "report on", "article about", "news about", "investigation into",
    "accused of", "alleged", "suspected", "we reject ", "we oppose ",
    "no involvement in", "not engaged in", "exempt from",
]

# Categories where a SINGLE keyword match is too weak for a hard block.
# These get downgraded to "restricted" unless ≥ MIN_MATCHES keywords hit.
_HIGH_FP_CATEGORIES: dict[str, int] = {
    "Pyramid / Ponzi / MLM": 2,
    "Shell / Front Companies": 2,
    "Counterfeit Goods": 2,
    "Sanctions Evasion": 2,
}


def _kw_word_boundary_match(keyword: str, text: str) -> bool:
    """Check if *keyword* appears in *text* at word boundaries."""
    return bool(re.search(r"\b" + re.escape(keyword) + r"\b", text))


def _extract_context_snippets(keyword: str, text: str, window: int = 120) -> list[str]:
    """Return short text windows around each occurrence of *keyword*."""
    snippets: list[str] = []
    for m in re.finditer(r"\b" + re.escape(keyword) + r"\b", text):
        start = max(0, m.start() - window)
        end = min(len(text), m.end() + window)
        snippets.append(text[start:end].strip())
    return snippets[:3]  # cap at 3 snippets


def _is_negated(keyword: str, text: str) -> bool:
    """Return True if every occurrence of *keyword* appears in a negation context."""
    snippets = _extract_context_snippets(keyword, text, window=80)
    if not snippets:
        return True  # not found at all ⇒ treat as negated
    negated_count = 0
    for snippet in snippets:
        snippet_lower = snippet.lower()
        kw_idx = snippet_lower.find(keyword.lower())
        prefix = snippet_lower[max(0, kw_idx - 80):kw_idx]
        if any(neg in prefix for neg in _NEGATION_PREFIXES):
            negated_count += 1
    # ALL occurrences negated ⇒ it's just a disclaimer, not a real signal
    return negated_count == len(snippets)


# Industry-context briefings — what an analyst would say when an entity's
# website/SIC signals match this category. Each entry: a one-line
# description, the regulatory frame, and the buyer-actionable controls
# to look for. This replaces the old "prohibited" verdict layer with
# something useful: "here is what this industry is, and here is what to
# verify before transacting with them."
_INDUSTRY_BRIEFINGS: dict[str, dict] = {
    "Gambling & Betting": {
        "description": "Operates in the gambling and betting sector.",
        "regulatory_frame": "Licensed by the Gambling Commission under the Gambling Act 2005. Subject to AML obligations under the MLR 2017 as a 'casino' or 'remote gambling' operator.",
        "typical_controls": [
            "Verify Gambling Commission operating licence (search via the Public Register at gamblingcommission.gov.uk).",
            "Confirm GamStop integration for online operators.",
            "Request the firm's most recent Source-of-Funds policy and AML risk assessment.",
        ],
    },
    "Cryptocurrency": {
        "description": "Operates in the cryptocurrency / digital-asset sector.",
        "regulatory_frame": "Cryptoasset firms in the UK must register with the FCA under the MLR 2017. From October 2023 they are subject to the Financial Promotions regime (PERG 8). Travel Rule (MLR 2022 amendment) applies to transfers over £1,000.",
        "typical_controls": [
            "Verify the firm appears on the FCA's cryptoasset register (register.fca.org.uk).",
            "Request the firm's Travel Rule compliance attestation and chosen on-chain analytics provider (Chainalysis, Elliptic, TRM Labs).",
            "Confirm a board-approved AML / Sanctions policy specific to digital assets.",
        ],
    },
    "Adult Entertainment": {
        "description": "Operates in the adult-entertainment / adult-content sector.",
        "regulatory_frame": "Subject to the Online Safety Act 2023 (age-verification duties). Card acquirers typically apply enhanced underwriting and high reserve requirements. Chargeback exposure is materially higher than mainstream commerce.",
        "typical_controls": [
            "Verify age-verification provider (e.g. AgeChecked, Yoti, OneID).",
            "Request the firm's content-moderation policy and 2257-equivalent record-keeping evidence.",
            "Confirm card-scheme MATCH-list status before merchant onboarding.",
        ],
    },
    "Weapons & Firearms": {
        "description": "Operates in the firearms / weapons trade.",
        "regulatory_frame": "Sale, transfer and possession regulated under the Firearms Acts 1968-2017. Export controls apply under the Export Control Order 2008. Dealers require Home Office authorisation.",
        "typical_controls": [
            "Verify Firearms Dealer registration with local police firearms licensing department.",
            "Confirm Export Control Joint Unit (ECJU) licensing for any cross-border sales.",
            "Request the firm's end-user verification policy.",
        ],
    },
    "Narcotics & Paraphernalia": {
        "description": "Operates in cannabis / CBD / vaping or related regulated-substance commerce.",
        "regulatory_frame": "Cannabis-derived products: regulated under MoDA 1971 (THC < 0.2%) and Novel Foods (FSA). Vaping products: TPD-compliant (TRPR 2016). Wholesalers may require MHRA licensing.",
        "typical_controls": [
            "Confirm Novel Foods authorisation status for any CBD ingestibles (FSA register).",
            "Verify THC testing certificates from accredited labs.",
            "Request the firm's age-verification gateway evidence (TPD requires 18+).",
        ],
    },
    "Pyramid / Ponzi / MLM": {
        "description": "Website/branding signals MLM, network-marketing or referral-income structures.",
        "regulatory_frame": "Trading Schemes Act 1996 and related Regulations govern multi-level marketing in the UK. Pyramid promotional schemes (no genuine product) are prohibited under the CPRs 2008.",
        "typical_controls": [
            "Request the firm's compensation plan and verify retail-vs-recruitment income split.",
            "Confirm membership in the Direct Selling Association (DSA) if claimed.",
            "Verify product-return / cooling-off policy meets CCRs 2013.",
        ],
    },
    "Shell / Front Companies": {
        "description": "Branding or services signal company-formation, nominee directors, or domiciliation activity.",
        "regulatory_frame": "TCSP (Trust or Company Service Provider) activity requires HMRC supervision under MLR 2017. Nominee director arrangements must be transparent under the PSC regime.",
        "typical_controls": [
            "Verify HMRC TCSP supervision (HMRC AML-supervised business register).",
            "Confirm the firm files PSC information for its nominee arrangements.",
            "Request the firm's CDD/KYB policy and source-of-wealth verification process.",
        ],
    },
    "Counterfeit Goods": {
        "description": "Website signals possible imitation, replica or counterfeit branding.",
        "regulatory_frame": "Sale of counterfeit goods is a criminal offence under the Trade Marks Act 1994 and Copyright, Designs and Patents Act 1988.",
        "typical_controls": [
            "Verify authorised-reseller agreements with original brand owners.",
            "Request authenticity certificates / serial-number records for high-value goods.",
            "Confirm IP rights coverage in product listings.",
        ],
    },
    "Sanctions Evasion": {
        "description": "Website or activity signals possible exposure to sanctioned jurisdictions or designated persons.",
        "regulatory_frame": "UK sanctions regime under SAMLA 2018, enforced by OFSI. Breach is a criminal offence; OFSI has strict-liability monetary penalties.",
        "typical_controls": [
            "Run live OFSI / OFAC / UN screening for the entity and every UBO before transacting.",
            "Confirm the firm has a documented Sanctions policy with screening cadence and escalation paths.",
            "Verify there is no exposure to designated jurisdictions in the customer base or supply chain.",
        ],
    },
    "Payday Loans / Predatory Lending": {
        "description": "Operates in the high-cost short-term credit market.",
        "regulatory_frame": "FCA-authorised consumer credit firm (CONC). HCSTC cap on cost (0.8% / day, total cost cap of 100%). Subject to FCA's vulnerability-customer rules.",
        "typical_controls": [
            "Verify FCA authorisation and full-permission status (register.fca.org.uk).",
            "Request the firm's affordability-assessment policy and arrears-handling procedures.",
            "Confirm Financial Ombudsman Service registration and FOS levy payment.",
        ],
    },
    "Money Remittance / MSB": {
        "description": "Operates as a Money Service Business (remittance, FX, or e-money agent).",
        "regulatory_frame": "FCA-authorised or registered Payment Institution / EMI / Small Payment Institution under the PSRs 2017 or EMRs 2011. Also HMRC-supervised under MLR 2017.",
        "typical_controls": [
            "Verify FCA authorisation status — Payment Institution / EMI / API / SPI.",
            "Confirm HMRC MSB supervision (HMRC AML-supervised business register).",
            "Request the firm's Safeguarding policy and segregated-account arrangements.",
        ],
    },
    "Spread Betting / CFDs / FX Derivatives": {
        "description": "Operates in retail leveraged-trading products (CFDs, spread bets, FX derivatives).",
        "regulatory_frame": "FCA-authorised (MiFID II / FSMA). Subject to FCA's CFD restrictions for retail clients (PERG 13, ESMA-derived margin caps).",
        "typical_controls": [
            "Verify FCA full-scope authorisation and CFD permission.",
            "Confirm negative-balance protection and ESMA-aligned leverage limits.",
            "Request the firm's appropriateness-test policy and warning disclosures.",
        ],
    },
    "Debt Recovery": {
        "description": "Operates in debt collection or enforcement services.",
        "regulatory_frame": "FCA-authorised consumer credit firm if recovering regulated debts (CONC 7). Enforcement agents (bailiffs) certified by County Court under the Tribunals, Courts and Enforcement Act 2007.",
        "typical_controls": [
            "Verify FCA authorisation for consumer-credit debt collection.",
            "Confirm Credit Services Association membership if claimed.",
            "Request the firm's vulnerability-customer policy and complaints procedure.",
        ],
    },
    "Unregulated Financial Advice": {
        "description": "Website or branding signals investment / advisory services that may not be FCA-authorised.",
        "regulatory_frame": "Carrying out a regulated activity (investment advice, arranging deals) without authorisation is a criminal offence (FSMA s.19).",
        "typical_controls": [
            "Verify FCA authorisation against the firm's actual activities (register.fca.org.uk).",
            "Request copies of risk warnings, appropriateness tests, and client agreements.",
            "Confirm professional indemnity insurance and FOS coverage.",
        ],
    },
    "Crowdfunding / Investment Opportunity": {
        "description": "Operates an investment / crowdfunding / equity platform.",
        "regulatory_frame": "FCA-authorised under PERG 8 (financial promotions) and PERG 18 (P2P / equity crowdfunding). Restricted-investor regime applies (COBS 4.7).",
        "typical_controls": [
            "Verify FCA full-permission status and operating-platform permission.",
            "Confirm restricted-investor / certified-sophisticated-investor categorisation procedures.",
            "Request the firm's appropriateness-test framework and risk warnings.",
        ],
    },
    "Online Dating / Marriage Services": {
        "description": "Operates an online dating / matchmaking platform.",
        "regulatory_frame": "Subject to GDPR + DPA 2018 (sensitive personal data, including sexual orientation). Subscription billing comes under the CCRs 2013 (cancellation rights). Online Safety Act 2023 imposes user-safety duties.",
        "typical_controls": [
            "Verify ICO registration and DPIA evidence.",
            "Confirm CMA-aligned subscription / auto-renewal disclosures.",
            "Request the firm's user-safety / fake-profile policy under the Online Safety Act.",
        ],
    },
    "Telemarketing / Fake Engagement": {
        "description": "Website signals telemarketing, cold-calling, or social-engagement-for-sale services.",
        "regulatory_frame": "Telephone marketing is regulated by PECR 2003 (consent + TPS suppression). Sale of fake engagement metrics breaches platform ToS and may breach CPRs 2008.",
        "typical_controls": [
            "Verify TPS / CTPS suppression process and consent records.",
            "Confirm ICO registration and DPIA for marketing-data processing.",
            "Request the firm's customer-acquisition source-verification policy.",
        ],
    },
}


def _briefing_for(category: str) -> dict | None:
    """Return the industry briefing for a detected category, or None if unmapped.
    Briefings are buyer-actionable context — what this industry is, the regulatory
    frame, and the typical controls — NOT a verdict."""
    return _INDUSTRY_BRIEFINGS.get(category)


def detect_restricted_activities(
    web_text: str,
    sic_risk: dict,
    company_name: str,
) -> dict:
    """Detect industries that warrant contextual due-diligence briefings.

    Replaces the prior "prohibited / restricted" verdict layer. The detection
    still uses word-boundary regex, negation handling, and signal-strength
    thresholds — but the output is now CONTEXTUAL, not prohibitive. Each
    detected industry carries a one-line description, the relevant regulatory
    frame, and a typical-controls checklist the buyer can act on.

    Returns dict with:
      - elevated_industries: list of industries where the briefing materially
                             changes the due-diligence approach
      - regulated_industries: list of industries with prior-agreement /
                              authorisation requirements but standard CDD
      - industries: combined list with full briefings
      - total_flags: int — count of detected industries (informational)

    Legacy keys preserved for back-compat:
      - prohibited / restricted / hard_block / details — these still exist
        for any downstream code that hasn't migrated, but `hard_block` is
        ALWAYS False (Probitas does not issue hard blocks).
    """
    web_lower = web_text.lower()
    name_lower = company_name.lower()
    combined = f"{web_lower} {name_lower}"

    # Also check SIC descriptions
    sic_descriptions = " ".join(
        (c.get("industry", "") + " " + c.get("reason", "")).lower()
        for c in sic_risk.get("industry_classifications", [])
    )
    combined += " " + sic_descriptions

    elevated: list[dict] = []     # high-context industries
    regulated: list[dict] = []    # regulated industries with prior-agreement needs

    for category, keywords, original_severity in _RESTRICTED_ACTIVITY_RULES:
        matched_kws = [kw for kw in keywords if _kw_word_boundary_match(kw, combined)]
        if not matched_kws:
            continue

        confirmed_kws = [kw for kw in matched_kws if not _is_negated(kw, combined)]
        if not confirmed_kws:
            continue

        all_snippets: list[str] = []
        for kw in confirmed_kws[:3]:
            all_snippets.extend(_extract_context_snippets(kw, combined, window=100))

        briefing = _briefing_for(category) or {}

        entry = {
            "category": category,
            "matched_keywords": confirmed_kws[:5],
            "signal_strength": len(confirmed_kws),
            "context_snippets": all_snippets[:5],
            # Contextual briefing — the heart of the new presentation
            "description": briefing.get("description", f"Detected signals related to {category}."),
            "regulatory_frame": briefing.get("regulatory_frame", ""),
            "typical_controls": briefing.get("typical_controls", []),
        }

        # Bucket by how strongly the industry profile changes the buyer's
        # due-diligence approach. "Elevated" = the briefing is substantive
        # enough that the analyst would surface it prominently; "regulated"
        # = standard regulated activity, surface as context.
        if original_severity == "prohibited":
            min_matches = _HIGH_FP_CATEGORIES.get(category, 1)
            if len(confirmed_kws) >= min_matches:
                entry["context_level"] = "elevated"
                elevated.append(entry)
            else:
                entry["context_level"] = "regulated"
                entry["note"] = (
                    f"Weak signal ({len(confirmed_kws)} keyword match — "
                    f"surface as context only)."
                )
                regulated.append(entry)
        else:
            entry["context_level"] = "regulated"
            regulated.append(entry)

    industries = elevated + regulated
    return {
        # New contextual shape
        "elevated_industries": elevated,
        "regulated_industries": regulated,
        "industries": industries,
        "total_flags": len(industries),
        # Legacy back-compat keys — kept so older readers don't break.
        # hard_block is ALWAYS False; Probitas presents context, not vetoes.
        "prohibited": elevated,
        "restricted": regulated,
        "details": industries,
        "hard_block": False,
    }


# ═══════════════════════════════════════════════════════════════════════════════
# HROB VERTICAL CLASSIFIER (High Risk Onboarding Review)
# ═══════════════════════════════════════════════════════════════════════════════
# Verticals requiring enhanced onboarding review — detected via holistic
# signals (website content + SIC + company name), NOT SIC alone.

_HROB_VERTICALS: list[tuple[str, list[str], list[str], str]] = [
    # (vertical_name, website_keywords, sic_prefixes, description)
    ("Investments & Savings",
     ["investment", "savings", "regulated investment", "investment advice",
      "investment platform", "credit union", "isa ", "stocks and shares",
      "wealth management", "portfolio", "fund manager", "asset management",
      "robo-advis", "discretionary management"],
     ["641", "643", "649", "661", "663", "64301", "64302", "64303"],
     "Regulated investment advice, investment platforms, credit unions"),

    ("Pensions",
     ["pension", "pension provider", "pension scheme", "sipp",
      "retirement fund", "annuity", "workplace pension",
      "auto-enrolment", "pension drawdown", "pension transfer"],
     ["653"],
     "Pension providers and pension administration"),

    ("Insurance",
     ["insurance premium", "insurance broker", "insurance underwriter",
      "insurance policy", "health insurance", "life insurance",
      "motor insurance", "pet insurance", "home insurance",
      "insurance claim", "insurance cover", "indemnity"],
     ["651", "652", "662"],
     "Companies processing insurance premiums, underwriters"),

    ("Lending",
     ["lending", "loan", "buy now pay later", "bnpl", "b2b lending",
      "b2c lending", "embedded finance", "market finance",
      "hire purchase", "credit facility", "bridging loan",
      "invoice finance", "asset finance", "leasing",
      "instalment", "credit line", "consumer credit"],
     ["6419", "649", "64920"],
     "BNPL, B2B/B2C lending, embedded finance, hire purchase"),

    ("Property Management",
     ["property management", "letting agent", "estate management",
      "residential management", "commercial property management",
      "rent collection", "landlord", "tenant", "tenancy",
      "property portfolio", "block management", "managing agent"],
     ["682", "683", "681"],
     "Residential/commercial property management, rent collection"),

    ("Co-Working Spaces",
     ["co-working", "coworking", "hot desk", "office rental",
      "serviced office", "shared workspace", "flexible office",
      "room rental", "meeting room hire", "desk space"],
     [],
     "Co-working office rentals, room rentals"),

    ("Property Service Charges",
     ["service charge", "block management", "freeholder",
      "leaseholder", "management company", "ground rent",
      "residential block", "apartment service charge",
      "commonhold", "maintenance charge"],
     [],
     "Residential block management, apartment service charge collection"),

    ("Home / Utility Cover",
     ["boiler cover", "home cover", "home maintenance",
      "white goods cover", "utility cover", "home emergency",
      "plumbing cover", "electrical cover", "appliance cover",
      "heating cover", "breakdown cover", "home care plan"],
     [],
     "Boiler cover, home maintenance, white goods, utility cover"),

    ("Funeral Plans",
     ["funeral plan", "pre-paid funeral", "funeral director",
      "burial plan", "cremation plan", "funeral service",
      "funeral insurance"],
     [],
     "Funeral plan providers"),

    ("Energy",
     ["energy provider", "energy supplier", "electricity supply",
      "gas supply", "energy tariff", "green energy", "solar panel",
      "energy broker", "utility provider", "energy switching",
      "smart meter", "energy bill"],
     ["351", "353"],
     "Energy providers and suppliers"),
]


def classify_hrob_verticals(
    web_text: str,
    sic_codes: list[str] | None,
    company_name: str,
    sic_risk: dict,
) -> dict:
    """Classify whether the company falls into an HROB (High Risk Onboarding
    Review) vertical using holistic signals from website, SIC, and name.

    Does NOT rely on SIC alone — treats SIC as one weak signal among many.

    Returns dict with:
      - matched_verticals: list of {vertical, confidence, signals, description}
      - requires_hrob: bool (True if any vertical matched with high confidence)
      - summary: str
    """
    web_lower = web_text.lower()
    name_lower = company_name.lower()
    sic_set = set(sic_codes or [])

    matched: list[dict] = []

    for vertical, web_kws, sic_prefixes, desc in _HROB_VERTICALS:
        signals: list[str] = []
        score = 0

        # Signal 1: Website keyword hits (strongest signal)
        web_hits = [kw for kw in web_kws if kw in web_lower]
        if web_hits:
            score += min(len(web_hits) * 2, 10)  # Cap at 10
            signals.append(f"Website: {len(web_hits)} keyword(s) found ({', '.join(web_hits[:4])})")

        # Signal 2: Company name contains vertical keywords
        name_hits = [kw for kw in web_kws if kw in name_lower]
        if name_hits:
            score += 5  # Name is a strong signal
            signals.append(f"Company name: contains '{name_hits[0]}'")

        # Signal 3: SIC code match (weak signal — informational only)
        sic_matches = []
        for prefix in sic_prefixes:
            for code in sic_set:
                if code.startswith(prefix):
                    sic_matches.append(code)
        if sic_matches:
            score += 2  # SIC is a weak signal
            signals.append(f"SIC: code(s) {', '.join(sic_matches)} align with {vertical}")

        # Determine confidence
        if score >= 7:
            confidence = "high"
        elif score >= 4:
            confidence = "medium"
        elif score >= 2:
            confidence = "low"
        else:
            continue  # No match

        matched.append({
            "vertical": vertical,
            "confidence": confidence,
            "score": score,
            "signals": signals,
            "description": desc,
        })

    # Sort by score descending
    matched.sort(key=lambda x: x["score"], reverse=True)

    requires_hrob = any(m["confidence"] in ("high", "medium") for m in matched)

    if not matched:
        summary = "No HROB verticals detected — standard onboarding applies."
    elif requires_hrob:
        verticals_str = ", ".join(m["vertical"] for m in matched if m["confidence"] in ("high", "medium"))
        summary = (
            f"⚠️ HROB Review Required — matched vertical(s): {verticals_str}. "
            f"Enhanced onboarding due diligence applies."
        )
    else:
        verticals_str = ", ".join(m["vertical"] for m in matched)
        summary = (
            f"Weak signal(s) for: {verticals_str}. "
            f"Standard onboarding likely sufficient but monitor."
        )

    return {
        "matched_verticals": matched,
        "requires_hrob": requires_hrob,
        "summary": summary,
    }


# ═══════════════════════════════════════════════════════════════════════════════
# HOLISTIC INDUSTRY CLASSIFIER (replaces SIC-only approach)
# ═══════════════════════════════════════════════════════════════════════════════

# Website keyword banks for industry detection (beyond SIC).
_INDUSTRY_WEBSITE_SIGNALS: dict[str, list[str]] = {
    "Financial Services": [
        "financial service", "regulated by the fca", "fca regulated",
        "authorised and regulated", "financial conduct authority",
        "investment", "wealth management", "asset management",
        "portfolio management", "fund manager", "discretionary",
        "fintech", "financial planning", "financial advis",
        "credit union", "banking", "capital markets",
    ],
    "Insurance": [
        "insurance", "underwriter", "insurance broker",
        "insurance premium", "policy cover", "claims",
        "indemnity", "actuarial", "reinsurance",
    ],
    "Lending & Credit": [
        "lending", "loan", "credit", "mortgage", "bridging finance",
        "invoice finance", "asset finance", "hire purchase",
        "buy now pay later", "bnpl", "consumer credit",
        "credit facility", "payday", "guarantor loan",
    ],
    "Payments & E-Money": [
        "payment service", "payment processor", "e-money",
        "electronic money", "prepaid card", "stored value",
        "payment gateway", "acquiring", "merchant service",
        "payment facilitator",
    ],
    "Gambling & Gaming": [
        "gambling", "betting", "casino", "lottery",
        "gaming", "slots", "poker", "bingo",
        "bookmaker", "wagering",
    ],
    "Cryptocurrency & DeFi": [
        "crypto", "cryptocurrency", "bitcoin", "ethereum",
        "blockchain", "defi", "nft", "token", "web3",
        "digital asset", "crypto exchange",
    ],
    "Charity & Non-Profit": [
        "charity", "charitable", "non-profit", "not-for-profit",
        "donations", "fundraising", "volunteer", "humanitarian",
        "relief", "aid organisation", "social enterprise",
    ],
    "Property & Real Estate": [
        "property management", "estate agent", "letting agent",
        "real estate", "landlord", "tenant", "rental",
        "block management", "freehold", "leasehold",
        "commercial property", "residential property",
    ],
    "Legal Services": [
        "solicitor", "law firm", "legal service", "barrister",
        "legal advice", "conveyancing", "litigation",
        "notary", "legal aid",
    ],
    "Accountancy & Tax": [
        "accountant", "accountancy", "bookkeeping", "tax",
        "audit", "payroll", "vat return", "self assessment",
        "chartered accountant", "cpa",
    ],
    "Healthcare & Medical": [
        "healthcare", "medical", "clinical", "pharmacy",
        "hospital", "gp practice", "dental", "optician",
        "mental health", "therapy", "physiotherapy",
    ],
    "Technology & Software": [
        "software", "saas", "platform", "app", "technology",
        "cloud", "data analytics", "artificial intelligence",
        "machine learning", "it service", "hosting",
        "cybersecurity", "devops",
    ],
    "Construction & Building": [
        "construction", "building", "contractor", "builder",
        "renovation", "refurbishment", "civil engineering",
        "scaffolding", "plumbing", "electrical contractor",
    ],
    "Education & Training": [
        "education", "training", "school", "university",
        "course", "tutorial", "e-learning", "academy",
        "coaching", "tutoring",
    ],
    "Retail & E-commerce": [
        "retail", "shop", "store", "e-commerce", "online shop",
        "marketplace", "buy", "sell", "products", "merchandise",
    ],
    "Energy & Utilities": [
        "energy", "electricity", "gas supply", "utility",
        "solar", "wind", "renewable", "smart meter",
        "energy tariff", "energy supplier",
    ],
    "Consulting & Professional Services": [
        "consulting", "consultancy", "advisory", "management consultant",
        "business advisory", "strategy", "professional service",
    ],
    "Travel & Tourism": [
        "travel", "tourism", "holiday", "tour operator",
        "flight", "hotel", "cruise", "booking", "travel agent",
    ],
    "Food & Hospitality": [
        "restaurant", "catering", "food delivery", "takeaway",
        "hotel", "hospitality", "pub", "bar", "café",
    ],
    "Defence & Security": [
        "defence", "defense", "security", "military",
        "arms", "weapons", "ammunition", "tactical",
    ],
}


def classify_actual_industry(
    web_text: str,
    sic_risk: dict,
    company_name: str,
    sic_codes: list[str] | None,
) -> dict:
    """Determine the company's ACTUAL industry from all available signals,
    not just SIC codes (which are self-declared and often inaccurate).

    Combines:
      1. Website content analysis (strongest signal)
      2. Company name semantics (strong signal)
      3. SIC code classification (weak/informational signal)

    Returns dict with:
      - determined_industry: str (best guess)
      - confidence: str (high/medium/low)
      - sic_declared_industry: str (what SIC says)
      - sic_alignment: str (aligned/mismatch/no_website)
      - evidence: list[str]
      - note: str
    """
    web_lower = web_text.lower()
    name_lower = company_name.lower()

    sic_industry = sic_risk.get("industry_category", "Unknown")

    # Score each industry by website keyword hits
    industry_scores: dict[str, int] = {}
    industry_hits: dict[str, list[str]] = {}
    for industry, keywords in _INDUSTRY_WEBSITE_SIGNALS.items():
        hits = [kw for kw in keywords if kw in web_lower]
        if hits:
            industry_scores[industry] = len(hits)
            industry_hits[industry] = hits[:5]

    # Also check company name
    for industry, keywords in _INDUSTRY_WEBSITE_SIGNALS.items():
        name_hits = [kw for kw in keywords if kw in name_lower]
        if name_hits:
            industry_scores[industry] = industry_scores.get(industry, 0) + len(name_hits) * 2

    evidence: list[str] = []
    if not industry_scores:
        # No website signal — fall back to SIC
        return {
            "determined_industry": sic_industry,
            "confidence": "low",
            "sic_declared_industry": sic_industry,
            "sic_alignment": "no_website",
            "evidence": ["No website content available — relying on SIC codes (low confidence)"],
            "note": (
                f"Industry classification based on SIC codes only ({sic_industry}). "
                f"SIC codes are self-declared and may not reflect actual business activity."
            ),
        }

    # Top industry by website evidence
    top_industry = max(industry_scores, key=lambda k: industry_scores[k])
    top_score = industry_scores[top_industry]

    if top_score >= 5:
        confidence = "high"
    elif top_score >= 3:
        confidence = "medium"
    else:
        confidence = "low"

    evidence.append(
        f"Website analysis: strongest match is '{top_industry}' "
        f"({top_score} keyword hits: {', '.join(industry_hits.get(top_industry, [])[:4])})"
    )

    # Check SIC alignment
    if sic_industry == top_industry or sic_industry == "Unknown":
        sic_alignment = "aligned"
        evidence.append(f"SIC-declared industry ({sic_industry}) aligns with website evidence")
    else:
        sic_alignment = "mismatch"
        evidence.append(
            f"⚠️ SIC declares '{sic_industry}' but website evidence suggests '{top_industry}'"
        )

    # If there's a second strong industry, note it
    sorted_industries = sorted(industry_scores.items(), key=lambda x: x[1], reverse=True)
    if len(sorted_industries) >= 2 and sorted_industries[1][1] >= 3:
        evidence.append(
            f"Secondary activity detected: '{sorted_industries[1][0]}' "
            f"({sorted_industries[1][1]} keyword hits)"
        )

    note = f"Industry determined as '{top_industry}' (confidence: {confidence}) based on website content analysis"
    if sic_alignment == "mismatch":
        note += f". Note: SIC codes declare '{sic_industry}' which does not match — SIC may be outdated or generic."

    return {
        "determined_industry": top_industry,
        "confidence": confidence,
        "sic_declared_industry": sic_industry,
        "sic_alignment": sic_alignment,
        "evidence": evidence,
        "note": note,
    }


# ═══════════════════════════════════════════════════════════════════════════════
# MERCHANT SUITABILITY ANALYSIS
# ═══════════════════════════════════════════════════════════════════════════════

# SIC-prefix → (chargeback_risk, typical_model, typical_payment)
_SIC_MERCHANT_PROFILE: dict[str, tuple[str, str, str]] = {
    "791": ("high",   "B2C", "one-off"),     # Travel agency
    "792": ("high",   "B2C", "one-off"),     # Tour operator
    "799": ("high",   "B2C", "one-off"),     # Reservation services
    "927": ("high",   "B2C", "one-off"),     # Gambling & betting
    "920": ("high",   "B2C", "one-off"),     # Entertainment activities
    "479": ("medium", "B2C", "one-off"),     # Mail order / internet retail
    "471": ("medium", "B2C", "one-off"),     # Non-specialised retail
    "472": ("medium", "B2C", "one-off"),     # Food/beverage retail
    "931": ("low",    "B2C", "recurring"),   # Sports facilities (gyms)
    "932": ("medium", "B2C", "mixed"),       # Amusement & recreation
    "561": ("low",    "B2C", "one-off"),     # Restaurants
    "620": ("low",    "B2B", "recurring"),   # IT / software
    "631": ("low",    "B2B", "recurring"),   # Data processing
    "691": ("low",    "B2B", "recurring"),   # Legal
    "692": ("low",    "B2B", "recurring"),   # Accounting
    "701": ("low",    "B2B", "N/A"),         # Head offices
    "702": ("low",    "B2B", "recurring"),   # Management consultancy
    "641": ("medium", "B2B", "mixed"),       # Financial intermediation
    "642": ("medium", "B2B", "mixed"),       # Holdings
    "643": ("medium", "B2B", "mixed"),       # Trusts & funds
    "649": ("medium", "B2B", "mixed"),       # Other financial
    "661": ("medium", "B2B", "mixed"),       # Financial markets admin
    "662": ("low",    "B2B", "recurring"),   # Insurance auxiliary
    "682": ("low",    "B2B", "recurring"),   # Real estate management
    "469": ("low",    "B2B", "mixed"),       # Non-specialised wholesale
    "461": ("low",    "B2B", "mixed"),       # Wholesale agents
}

_B2B_SIGNALS = [
    "b2b", "enterprise", "wholesale", "business solutions", "corporate",
    "for businesses", "business clients", "trade", "industry",
    "partners", "api", "platform", "saas", "solutions for",
]

_B2C_SIGNALS = [
    "b2c", "shop", "buy now", "add to cart", "basket", "customers",
    "personal", "individuals", "consumer", "free trial", "sign up",
    "download", "app store", "google play",
]

_RECURRING_SIGNALS = [
    "subscription", "monthly", "annual", "recurring", "membership",
    "plan", "per month", "per year", "renew", "cancel anytime",
    "free trial",
]

_ONE_OFF_SIGNALS = [
    "one-off", "pay now", "checkout", "single payment", "one time",
    "per project", "quote", "invoice", "basket", "cart",
]


_PAYMENT_METHOD_LABELS_LOCAL: dict[str, str] = {
    "card":           "Card payments (debit/credit)",
    "direct_debit":   "Direct Debit (Bacs)",
    "bank_transfer":  "Bank transfer (Faster Payments / BACS)",
    "open_banking":   "Open Banking (account-to-account)",
    "standing_order": "Standing order",
    "invoice_terms":  "Invoice with payment terms",
}


def _method_label(m: str) -> str:
    return _PAYMENT_METHOD_LABELS_LOCAL.get(m, m.replace("_", " ").title())


def analyse_payment_suitability(
    sic_codes: list[str] | None,
    website_results: list[dict],
    director_analysis: dict,
    company_age: dict,
    website_url: str,
) -> dict:
    """Recommend suitable payment methods for the company.

    Combines:
      - SIC industry profile (from `_INDUSTRY_PAYMENT_PROFILE`).
      - Scraped website signals (B2B/B2C, recurring/one-off, delivery gap).
      - Director risk + company age (operational stability).

    Returns a multi-method recommendation — `recommended`, `cautious` and
    `not_advised` — plus business_model, payment_pattern, chargeback_risk,
    and a contextual overall summary. NOT a binary verdict.
    """
    flags: list[str] = []
    positives: list[str] = []
    _has_website = bool(website_url and website_url.strip())

    # ── Track what was searched and what was found ─────────────────────
    search_methodology: list[str] = []
    data_sources_used: list[str] = []
    data_limitations: list[str] = []

    # ── SIC-based analysis ────────────────────────────────────────────
    sic_chargeback = "unknown"
    sic_model = "Unknown"
    sic_payment = "unknown"
    _sic_matched = False
    for code in (sic_codes or []):
        for prefix_len in (3, 2):
            prefix = code[:prefix_len]
            if prefix in _SIC_MERCHANT_PROFILE:
                sic_chargeback, sic_model, sic_payment = _SIC_MERCHANT_PROFILE[prefix]
                _sic_matched = True
                break

    if _sic_matched:
        data_sources_used.append("SIC code industry classification")
        search_methodology.append(
            f"Matched SIC code(s) to merchant profile database — "
            f"industry baseline: {sic_model} / {sic_payment} / chargeback risk: {sic_chargeback}"
        )
    else:
        search_methodology.append(
            "SIC codes did not match any known merchant profile — "
            "industry baseline could not be determined from registration data"
        )

    # ── Website content analysis ──────────────────────────────────────
    web_blob = " ".join(
        (r.get("content") or "") + " " + (r.get("title") or "")
        for r in website_results
    ).lower()

    _web_word_count = len(web_blob.split()) if web_blob.strip() else 0

    b2b_hits = sum(1 for s in _B2B_SIGNALS if s in web_blob)
    b2c_hits = sum(1 for s in _B2C_SIGNALS if s in web_blob)
    recurring_hits = sum(1 for s in _RECURRING_SIGNALS if s in web_blob)
    oneoff_hits = sum(1 for s in _ONE_OFF_SIGNALS if s in web_blob)

    _total_web_signals = b2b_hits + b2c_hits + recurring_hits + oneoff_hits

    if _has_website:
        data_sources_used.append("Company website content analysis")
        search_methodology.append(
            f"Crawled the provided website ({website_url}) — retrieved "
            f"{_web_word_count:,} words across {len(website_results)} indexed page(s). "
            f"Found {_total_web_signals} business model signal(s)."
        )
    elif _web_word_count > 0:
        # We have some web results from a generic name search (no specific website)
        data_sources_used.append("Generic web search results (no website provided)")
        search_methodology.append(
            f"No website was provided. Searched the web for the company name — "
            f"retrieved {_web_word_count:,} words across {len(website_results)} page(s). "
            f"These results may include third-party mentions, directory listings, or unrelated pages. "
            f"Found {_total_web_signals} potential business model signal(s) (lower confidence)."
        )
        data_limitations.append(
            "No company website was provided — business model and payment pattern "
            "are inferred from SIC codes and generic web search results, which may "
            "not accurately reflect the company's actual operations"
        )
    else:
        search_methodology.append(
            "No website was provided and no web content could be retrieved "
            "through a general name search. Business model classification "
            "relies solely on SIC code data (low confidence)."
        )
        data_limitations.append(
            "No website and no web content available — all classifications "
            "are based exclusively on SIC code registration data and may not "
            "reflect the company's current trading activity"
        )

    # Determine business model
    if b2b_hits > b2c_hits + 1:
        web_model = "B2B"
    elif b2c_hits > b2b_hits + 1:
        web_model = "B2C"
    elif b2b_hits > 0 and b2c_hits > 0:
        web_model = "Mixed"
    else:
        web_model = sic_model  # Fallback to SIC

    # Determine payment model
    if recurring_hits > oneoff_hits:
        web_payment = "Recurring"
    elif oneoff_hits > recurring_hits:
        web_payment = "One-off"
    elif recurring_hits > 0 and oneoff_hits > 0:
        web_payment = "Mixed"
    else:
        web_payment = sic_payment.title() if sic_payment != "unknown" else "Unknown"

    # Track confidence level
    _confidence = "high" if _has_website and _total_web_signals >= 2 else (
        "medium" if _has_website or _total_web_signals >= 1 else "low"
    )

    # ── Chargeback risk assessment ────────────────────────────────────
    chargeback_risk = sic_chargeback
    if chargeback_risk == "unknown":
        if web_model == "B2B":
            chargeback_risk = "low"
        elif web_model == "B2C" and web_payment == "One-off":
            chargeback_risk = "medium"
        else:
            chargeback_risk = "low"

    # Delivery-gap industries elevate indemnity risk
    delivery_gap_keywords = [
        "pre-order", "delivery in", "weeks", "lead time",
        "bespoke", "made to order", "custom",
    ]
    if any(k in web_blob for k in delivery_gap_keywords):
        if chargeback_risk == "low":
            chargeback_risk = "medium"
        flags.append(
            "Website suggests service/delivery gap — elevated indemnity risk"
        )

    # ── Director risk for merchant lens ───────────────────────────────
    total_dissolved = sum(
        d.get("dissolved_companies", 0)
        for d in director_analysis.get("directors", [])
    )
    if total_dissolved >= 5:
        flags.append(
            f"Directors associated with {total_dissolved} dissolved companies "
            f"— elevated serial-failure risk"
        )
        if chargeback_risk == "low":
            chargeback_risk = "medium"
    elif total_dissolved >= 2:
        flags.append(
            f"Directors associated with {total_dissolved} dissolved companies "
            f"— informational"
        )

    # ── Company age factor ────────────────────────────────────────────
    age_months = company_age.get("age_months")
    if age_months is not None and age_months < 12:
        flags.append(
            "Company is less than 1 year old — limited trading history"
        )
    elif age_months is not None and age_months >= 36:
        positives.append(
            f"Company has {age_months // 12}+ years of trading history"
        )

    # ── Industry payment profile (from SIC) ───────────────────────────
    # Pull the recommended/cautious/avoid lists from the industry table —
    # this is the BASE recommendation. We then adjust below with website
    # + operational signals.
    from api_clients.companies_house import (
        _INDUSTRY_PAYMENT_PROFILE,
        _PAYMENT_METHOD_LABELS,
    )

    method_state: dict[str, str] = {}  # method -> "recommended" | "cautious" | "avoid"
    industry_categories: list[str] = []
    industry_reasons: list[str] = []

    for code in (sic_codes or []):
        for prefix_len in (5, 4, 3, 2):
            prefix = code[:prefix_len]
            prof = _INDUSTRY_PAYMENT_PROFILE.get(prefix)
            if prof:
                if prof["category"] not in industry_categories:
                    industry_categories.append(prof["category"])
                industry_reasons.append(f"{prof['category']}: {prof['reason']}")
                for m in prof.get("recommended", []):
                    if method_state.get(m) not in {"avoid", "cautious"}:
                        method_state[m] = "recommended"
                for m in prof.get("cautious", []):
                    if method_state.get(m) != "avoid":
                        method_state[m] = "cautious"
                for m in prof.get("avoid", []):
                    method_state[m] = "avoid"
                break

    # Fallback for unmatched companies — conservative default.
    if not method_state:
        method_state = {
            "card":          "recommended",
            "bank_transfer": "recommended",
            "direct_debit":  "cautious",
            "open_banking":  "cautious",
        }
        industry_categories = ["General"]

    # ── Adjust based on website-derived business model ────────────────
    method_rationales: dict[str, list[str]] = {m: [] for m in method_state}

    def _ensure(m: str):
        if m not in method_state:
            method_state[m] = "cautious"
            method_rationales.setdefault(m, [])

    if web_model == "B2B":
        # B2B favours bank transfer / invoice; cards less common
        _ensure("bank_transfer"); _ensure("invoice_terms")
        if method_state["bank_transfer"] != "avoid":
            method_state["bank_transfer"] = "recommended"
            method_rationales["bank_transfer"].append("B2B website signals — invoice flows fit transfers")
        if method_state.get("invoice_terms") != "avoid":
            method_state["invoice_terms"] = "recommended"
            method_rationales["invoice_terms"].append("B2B context — net-terms invoicing standard")
        positives.append("B2B model — typically lower chargeback rates")
    elif web_model == "B2C":
        _ensure("card")
        if method_state["card"] != "avoid":
            method_state["card"] = "recommended"
            method_rationales["card"].append("B2C website signals — card primary for consumer checkout")

    if web_payment == "Recurring":
        # Recurring favours DD + standing order + card-on-file
        _ensure("direct_debit"); _ensure("standing_order")
        for m in ("direct_debit", "standing_order"):
            if method_state[m] != "avoid":
                method_state[m] = "recommended"
                method_rationales[m].append("Recurring/subscription billing pattern detected")
        positives.append("Recurring billing pattern — predictable cash flow")
    elif web_payment == "One-off":
        _ensure("card")
        if method_state["card"] != "avoid":
            method_state["card"] = "recommended"
            method_rationales["card"].append("One-off transactional pattern — card primary")
        # DD is poorly suited to ad-hoc one-off purchases — soften any
        # blanket DD recommendation we got from the industry table.
        if method_state.get("direct_debit") == "recommended":
            method_state["direct_debit"] = "cautious"
            method_rationales["direct_debit"].append("One-off payment pattern — DD requires recurring relationship")

    # Delivery-gap and high-value commerce make card refunds expensive and
    # DD indemnity claims more likely.
    if any(k in web_blob for k in ["pre-order", "made to order", "bespoke"]):
        if method_state.get("direct_debit") in {"recommended", "cautious"}:
            method_state["direct_debit"] = "cautious"
            method_rationales.setdefault("direct_debit", []).append("Future-delivery exposure — indemnity risk")
        if method_state.get("card") in {"recommended", "cautious"}:
            method_state["card"] = "cautious"
            method_rationales.setdefault("card", []).append("Future-delivery exposure — chargeback risk")

    # Young companies + serial-failure director risk: avoid DD pulls until
    # operational track record is established.
    operational_flags = 0
    if age_months is not None and age_months < 12:
        operational_flags += 1
    if total_dissolved >= 5:
        operational_flags += 2
    elif total_dissolved >= 2:
        operational_flags += 1
    if chargeback_risk == "high":
        operational_flags += 1

    if operational_flags >= 3:
        if method_state.get("direct_debit") == "recommended":
            method_state["direct_debit"] = "cautious"
            method_rationales.setdefault("direct_debit", []).append("Operational risk indicators — establish track record first")

    # ── Build output groups ───────────────────────────────────────────
    def _entry(m: str) -> dict:
        return {
            "method": m,
            "label": _PAYMENT_METHOD_LABELS.get(m, _method_label(m)),
            "rationale": "; ".join(method_rationales.get(m, [])) or None,
        }

    recommended = [_entry(m) for m, s in method_state.items() if s == "recommended"]
    cautious    = [_entry(m) for m, s in method_state.items() if s == "cautious"]
    not_advised = [_entry(m) for m, s in method_state.items() if s == "avoid"]

    # ── Overall narrative line (no DD-specific framing) ───────────────
    if recommended:
        rec_labels = ", ".join(e["label"] for e in recommended)
        overall = f"Recommended payment methods: {rec_labels}."
        if cautious:
            caut_labels = ", ".join(e["label"] for e in cautious)
            overall += f" Viable with enhanced monitoring: {caut_labels}."
        if not_advised:
            avoid_labels = ", ".join(e["label"] for e in not_advised)
            overall += f" Not advised given the industry profile: {avoid_labels}."
    elif cautious:
        caut_labels = ", ".join(e["label"] for e in cautious)
        overall = (f"All payment methods require enhanced due diligence — "
                   f"the company profile presents elevated risk. "
                   f"Methods that may be viable with monitoring: {caut_labels}.")
    else:
        overall = "Insufficient data to recommend specific payment methods — manual review required."

    # Aggregate flag for any high-risk industry indicator (replaces the
    # old DD hard-stop). Used by the AML scorer as a contextual signal,
    # not as a binary suitability verdict.
    industry_risk = chargeback_risk
    if any("high" in r.lower() for r in industry_reasons):
        # `chargeback_risk` is web-derived; check the industry table side
        for code in (sic_codes or []):
            for prefix_len in (5, 4, 3, 2):
                prof = _INDUSTRY_PAYMENT_PROFILE.get(code[:prefix_len])
                if prof and prof.get("risk_level") == "high":
                    industry_risk = "high"
                    break

    return {
        "business_model": web_model,
        "payment_pattern": web_payment,
        "chargeback_risk": chargeback_risk,
        "industry_risk": industry_risk,
        "industry_categories": industry_categories,
        "recommended": recommended,
        "cautious": cautious,
        "not_advised": not_advised,
        "overall": overall,
        "sic_analysis": {
            "chargeback_risk": sic_chargeback,
            "typical_model": sic_model,
            "typical_payment": sic_payment,
            "industry_reasons": industry_reasons,
        },
        "website_signals": {
            "b2b_signals": b2b_hits,
            "b2c_signals": b2c_hits,
            "recurring_signals": recurring_hits,
            "oneoff_signals": oneoff_hits,
        },
        "website_provided": _has_website,
        "confidence": _confidence,
        "search_methodology": search_methodology,
        "data_sources_used": data_sources_used,
        "data_limitations": data_limitations,
        "operational_flags": operational_flags,
        "flags": flags,
        "positives": positives,
    }


# Back-compat alias — anyone still importing the old name gets the new
# function (with the new return shape, which old DD-only callers will
# need to handle).
analyse_merchant_suitability = analyse_payment_suitability


# ═══════════════════════════════════════════════════════════════════════════════
# ADDRESS CREDIBILITY ENGINE
# ═══════════════════════════════════════════════════════════════════════════════

def analyse_address_credibility(
    virtual_office: dict,
    cross_ref: dict,
    website_results: list[dict],
) -> dict:
    """Assess operational vs registered address credibility.

    The registered office is often just the accountant's address.
    The website address (if found) is the 'operational truth'.

    Classifies address as: Virtual Office / Commercial Hub / Residential /
    Commercial or Accountant's / Standard.
    """
    findings: list[dict] = []

    # Extract postcodes from website content
    web_postcodes = re.findall(
        r"\b[A-Z]{1,2}\d[A-Z\d]?\s*\d[A-Z]{2}\b",
        " ".join((r.get("content") or "") for r in website_results),
        re.IGNORECASE,
    )
    operational_postcode = web_postcodes[0] if web_postcodes else None

    is_virtual_reg = virtual_office.get("is_virtual", False)
    reg_addr = (virtual_office.get("full_address") or "").lower()

    # ── Classify address type ─────────────────────────────────────────
    residential_kw = [
        "flat", "apartment", "house number", "cottage",
        "bungalow", "detached", "semi-detached", "terraced",
    ]
    commercial_hub_kw = [
        "regus", "wework", "spaces", "hq ", "serviced office",
        "business centre", "business center", "co-working",
    ]

    is_residential = any(kw in reg_addr for kw in residential_kw)
    is_commercial_hub = any(kw in reg_addr for kw in commercial_hub_kw)

    if is_virtual_reg:
        address_type = "Virtual Office"
    elif is_commercial_hub:
        address_type = "Commercial Hub / Serviced Office"
    elif is_residential:
        address_type = "Residential"
    else:
        address_type = "Standard Commercial"

    # ── Build findings ────────────────────────────────────────────────
    if is_virtual_reg and operational_postcode:
        findings.append({
            "type": "informational",
            "note": (
                f"Operating from a **{address_type}** registered address. "
                f"Website shows operational postcode: {operational_postcode}. "
                f"(Verified: Website vs Official Records — common arrangement)"
            ),
        })
    elif is_virtual_reg and not operational_postcode:
        findings.append({
            "type": "informational",
            "note": (
                f"Operating from a **{address_type}** registered address. "
                f"No operational address visible on the website. "
                f"(Informational — many businesses use virtual offices for "
                f"registered address purposes)"
            ),
        })
    elif is_commercial_hub:
        findings.append({
            "type": "informational",
            "note": (
                f"Operating from a **{address_type}** (e.g. Regus, WeWork). "
                f"This is standard for growing businesses."
            ),
        })
    elif is_residential:
        findings.append({
            "type": "informational",
            "note": (
                f"Registered address appears **{address_type}** — typical for "
                f"micro-businesses and sole traders."
            ),
        })
    else:
        findings.append({
            "type": "green",
            "note": f"Registered at a **{address_type}** address.",
        })

    return {
        "findings": findings,
        "address_type": address_type,
        "operational_postcode": operational_postcode,
        "is_virtual_registered": is_virtual_reg,
        "web_postcodes_found": web_postcodes[:5],
    }


# ═══════════════════════════════════════════════════════════════════════════════
# DIRECTOR & OWNERSHIP NETWORK GRAPH (GRAPHVIZ DOT)
# ═══════════════════════════════════════════════════════════════════════════════

def build_director_network_dot(
    company_name: str,
    company_num: str,
    directors: list[dict],
    ubo_chain: dict,
) -> str:
    """Build a Graphviz DOT string for the director + ownership network."""

    _counter = [0]
    _ids: dict[str, str] = {}

    def _nid(key: str) -> str:
        if key not in _ids:
            _counter[0] += 1
            _ids[key] = f"n{_counter[0]}"
        return _ids[key]

    def _e(s: str) -> str:
        return (s or "?").replace('"', "'").replace("\\", "/").replace("\n", " ")[:42]

    lines = [
        "digraph G {",
        '  graph [rankdir=TB, bgcolor=transparent, fontname="Helvetica", '
        "nodesep=0.6, ranksep=0.7, pad=0.3];",
        '  node [fontname="Helvetica", fontsize=9, style=filled, '
        'margin="0.12,0.06"];',
        '  edge [fontname="Helvetica", fontsize=7, color="#94a3b8", '
        'fontcolor="#94a3b8"];',
        "",
    ]

    # ── Target company ────────────────────────────────────────────────
    tid = _nid("__target__")
    lines.append(
        f'  {tid} [label="{_e(company_name)}\\n({company_num})", '
        'shape=box, fillcolor="#2980b9", fontcolor=white, penwidth=2];'
    )
    lines.append("")

    # ── PSC / UBO ownership layers ────────────────────────────────────
    for layer in ubo_chain.get("chain", []):
        co_num_l = layer.get("company_number", "")
        co_name_l = layer.get("company_name", "")
        parent_id = tid if co_num_l == company_num else _nid(f"co_{co_num_l}")

        # Draw intermediate company node
        if co_num_l != company_num:
            lines.append(
                f'  {parent_id} [label="{_e(co_name_l)}\\n({co_num_l})", '
                'shape=box, fillcolor="#d2b4de", fontcolor="#1e293b"];'
            )

        for psc in layer.get("pscs", []):
            nm = _e(psc.get("name", "?"))
            tt = psc.get("terminal_type", "")
            traced_num = psc.get("traced_company_number", "")

            if traced_num:
                psc_id = _nid(f"co_{traced_num}")
                traced_nm = _e(psc.get("traced_company_name", nm))
                lines.append(
                    f'  {psc_id} [label="{traced_nm}\\n({traced_num})", '
                    'shape=box, fillcolor="#d2b4de"];'
                )
            else:
                psc_id = _nid(f"psc_{nm}_{layer.get('depth', 0)}")
                if "Natural Person" in tt:
                    lines.append(
                        f'  {psc_id} [label="{nm}\\n(Person)", '
                        'shape=ellipse, fillcolor="#e67e22", fontcolor=white];'
                    )
                elif "Publicly Traded" in tt:
                    lines.append(
                        f'  {psc_id} [label="{nm}\\n(PLC)", '
                        'shape=doubleoctagon, fillcolor="#27ae60", fontcolor=white];'
                    )
                elif "Foreign" in tt or "End of Trace" in tt:
                    lines.append(
                        f'  {psc_id} [label="{nm}\\n(Foreign/Unresolvable)", '
                        'shape=box, fillcolor="#f39c12", fontcolor=white, '
                        'style="filled,bold"];'
                    )
                elif "Government" in tt or "State" in tt:
                    lines.append(
                        f'  {psc_id} [label="{nm}\\n(State)", '
                        'shape=hexagon, fillcolor="#2c3e50", fontcolor=white];'
                    )
                elif "Protected" in tt:
                    lines.append(
                        f'  {psc_id} [label="{nm}\\n(Protected)", '
                        'shape=box, fillcolor="#7f8c8d", fontcolor=white];'
                    )
                else:
                    lines.append(
                        f'  {psc_id} [label="{nm}", '
                        'shape=ellipse, fillcolor="#abebc6", fontcolor="#1e293b"];'
                    )

            # Ownership edge (owner → company)
            natures = psc.get("natures_of_control", [])
            ctrl = ""
            if natures:
                ctrl = natures[0].replace("-", " ")
                for old, new in [
                    ("ownership of shares", "shares"),
                    ("voting rights", "votes"),
                    ("right to appoint and remove directors", "appoint/remove"),
                    ("significant influence or control", "control"),
                ]:
                    ctrl = ctrl.replace(old, new)
                ctrl = ctrl[:25]
            lines.append(
                f'  {psc_id} -> {parent_id} [label="{ctrl}", '
                'color="#a78bfa", fontcolor="#a78bfa", style=bold];'
            )

    lines.append("")

    # ── Directors (below company) ─────────────────────────────────────
    for i, d in enumerate(directors[:8]):
        dname = _e(d.get("name", f"Dir {i + 1}"))
        nf = len(d.get("flags", []))
        fill = "#c0392b" if nf >= 2 else ("#f39c12" if nf >= 1 else "#27ae60")
        role = d.get("role", "director")
        dissolved = d.get("dissolved_companies", 0)
        extra = f"\\n({dissolved} dissolved)" if dissolved >= 2 else ""
        did = _nid(f"dir_{i}")
        lines.append(
            f'  {did} [label="{dname}{extra}", shape=ellipse, '
            f'fillcolor="{fill}", fontcolor=white];'
        )
        lines.append(f'  {tid} -> {did} [label="{role}"];')

        # Other active appointments (max 3 to keep graph readable)
        for j, a in enumerate(d.get("other_appointments_detail", [])[:3]):
            aname = _e(a.get("company_name", "?"))
            anum = a.get("company_number", "")
            astatus = a.get("company_status", "active")
            aid = _nid(f"oth_{i}_{j}")
            afill = "#d5dbdb" if astatus == "active" else "#fadbd8"
            sl = "" if astatus == "active" else f" [{astatus}]"
            lines.append(
                f'  {aid} [label="{aname}{sl}\\n({anum})", shape=box, '
                f'fillcolor="{afill}", fontcolor="#1e293b", fontsize=7, style="filled,dashed"];'
            )
            lines.append(
                f'  {did} -> {aid} [style=dashed, color="#94a3b8", fontcolor="#94a3b8"];'
            )

    lines.append("")
    lines.append("}")
    return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════════════════════
# OVERALL RISK MATRIX
# ═══════════════════════════════════════════════════════════════════════════════

def build_risk_matrix(
    company_age: dict,
    status_analysis: dict,
    sic_risk: dict,
    virtual_office: dict,
    director_analysis: dict,
    dormancy: dict,
    psc_analysis: dict,
    cross_ref: dict,
    ubo_chain: dict | None = None,
    merchant: dict | None = None,
    fatf_screening: dict | None = None,
    adverse_media: list | None = None,
    accounts_data: dict | None = None,
    restricted_activities: dict | None = None,
    hrob_verticals: dict | None = None,
    actual_industry: dict | None = None,
    search_errors: list | None = None,
) -> dict:
    """Aggregate all risk indicators into a single matrix with an overall score.

    Implements deterministic compliance rules:
      1. Hard Stop Triggers  — dissolved/liquidated/insolvent → instant 100/100
      2. Sanctions / FATF veto — verified sanctions match → instant 100/100
      3. Filing overdue — precise date-math, >18m = 🟡, >24m = 🔴
      4. Geopolitical Director Logic — ≥50% high-risk nationality → 🔴
      5. Final Veto Rule — overrides AI score if hard stops are present

    Focuses on Fraud, Chargebacks, and Beneficial Ownership —
    NOT debt/charges (most companies have them).
    """
    ubo_chain = ubo_chain or {}
    merchant = merchant or {}
    fatf_screening = fatf_screening or {}
    adverse_media = adverse_media or []
    accounts_data = accounts_data or {}
    restricted_activities = restricted_activities or {}
    hrob_verticals = hrob_verticals or {}
    actual_industry = actual_industry or {}
    search_errors = search_errors or []

    # ══════════════════════════════════════════════════════════════════
    # SEARCH ERROR DETECTION — never let API failure = clean
    # ══════════════════════════════════════════════════════════════════
    _adverse_search_failed = any("adverse media" in e.lower() for e in search_errors)
    _fatf_search_failed = any("fatf" in e.lower() for e in search_errors)
    # Also treat FATF "Unknown" risk_level (from API failure) as a search failure
    if (fatf_screening.get("risk_level") or "").lower() == "unknown" or fatf_screening.get("_search_failed"):
        _fatf_search_failed = True
    _fatf_risk = (fatf_screening.get("risk_level") or "low").lower()
    _web_search_failed = any(
        "web search" in e.lower() or "website content" in e.lower()
        for e in search_errors
    )

    # ══════════════════════════════════════════════════════════════════
    # SANCTIONS CROSS-POLLINATION — Adverse Media ↔ FATF must agree
    # If adverse media contains verified sanctions hits, FATF must
    # reflect that. These are NOT two unrelated boxes.
    # ══════════════════════════════════════════════════════════════════
    _SANCTIONS_KEYWORDS = {
        "asset freeze", "ofac", "uk sanctions list", "sanctions designation",
        "hmt sanctions", "designated person", "un sanctions", "eu sanctions",
        "sanctions violation", "sanctions breach", "proscribed",
        "sanctions evasion", "ofsi",
    }
    _adverse_has_sanctions = False
    for adv in adverse_media:
        if not adv.get("_relevant"):
            continue
        snippet = ((adv.get("content") or "") + " " + (adv.get("title") or "")).lower()
        if any(kw in snippet for kw in _SANCTIONS_KEYWORDS):
            _adverse_has_sanctions = True
            break

    # If adverse media found sanctions hits → force FATF to High
    if _adverse_has_sanctions:
        if (fatf_screening.get("risk_level") or "").lower() not in ("high", "very high"):
            fatf_screening["risk_level"] = "High"
            fatf_screening["_escalated_by_adverse_media"] = True
            _existing_cats = set(fatf_screening.get("fatf_categories_detected") or [])
            _existing_cats.add("Sanctions Violations")
            fatf_screening["fatf_categories_detected"] = sorted(_existing_cats)
            if not fatf_screening.get("summary"):
                fatf_screening["summary"] = (
                    "Escalated to High: verified sanctions-related adverse media "
                    "detected. Sanctions evasion/violations is a FATF predicate offence."
                )
            else:
                fatf_screening["summary"] = (
                    fatf_screening["summary"] + " | ESCALATED: verified sanctions-related "
                    "adverse media detected — sanctions evasion is a FATF predicate offence."
                )

    # ══════════════════════════════════════════════════════════════════
    # Hard stops removed. Probitas presents data with context — the buyer
    # decides whether an entity is workable for their own use case. What
    # used to be hard stops (crypto, gambling, insolvent status, sanctions
    # adverse media, etc.) now surface as ELEVATED-CONTEXT signals: each
    # carries a one-line industry/situation note and a typical-controls
    # list so the buyer can act on them with informed judgement, not on
    # a binary veto from us.
    #
    # Kept as an empty list for downstream code that still reads it.
    hard_stops: list[str] = []
    _status = (status_analysis.get("status") or "").lower()

    # (Former hard-stop triggers for FATF and restricted activities are
    # no longer generated here. They become contextual signals further
    # below — see industry context + screening summary in the bundle.)

    # ══════════════════════════════════════════════════════════════════
    # FILING OVERDUE (precise date math)
    # ══════════════════════════════════════════════════════════════════
    filing_risk = accounts_data.get("filing_overdue_risk", "unknown")
    filing_note = accounts_data.get("filing_overdue_note", "")

    # ══════════════════════════════════════════════════════════════════
    # GEOPOLITICAL DIRECTOR LOGIC (≥50% high-risk nationality)
    # ══════════════════════════════════════════════════════════════════
    from config import get_country_risk, is_elevated_risk

    directors = director_analysis.get("directors", [])
    dir_count = len(directors)
    high_risk_dir_count = 0
    for d in directors:
        nat = d.get("nationality", "")
        res = d.get("country_of_residence", "")
        nat_risk = get_country_risk(nat) if nat else ""
        res_risk = get_country_risk(res) if res else ""
        if is_elevated_risk(nat_risk) or is_elevated_risk(res_risk):
            high_risk_dir_count += 1

    # Also check UBO ultimate owners for geopolitical risk
    ultimate = ubo_chain.get("ultimate_owners", [])
    ubo_high_risk = 0
    for u in ultimate:
        u_nat = u.get("nationality", "")
        if u_nat and is_elevated_risk(get_country_risk(u_nat)):
            ubo_high_risk += 1

    geo_director_override = False
    if dir_count > 0 and (high_risk_dir_count / dir_count) >= 0.5:
        geo_director_override = True

    # ── UBO transparency risk ─────────────────────────────────────────
    # Foreign / unresolvable corporate owners are an INFORMATIONAL
    # observation, not an automatic high-risk.  They only escalate to
    # "high" when combined with other substantive compliance concerns
    # (hard stops, sanctions, adverse media, etc.).
    ubo_risk = "low"
    _has_foreign_ubo = False
    if ubo_chain.get("max_depth_reached"):
        ubo_risk = "medium"
    for u in ultimate:
        tt = u.get("terminal_type", "")
        if "Foreign" in tt or "End of Trace" in tt:
            _has_foreign_ubo = True
            if ubo_risk != "high":
                ubo_risk = "medium"  # informational — not auto-high
        if "Protected" in tt or "Max Depth" in tt:
            if ubo_risk != "high":
                ubo_risk = "medium"
    # Only escalate to high if there are OTHER substantive red flags
    # (sanctions, hard stops, adverse media, FATF high risk)
    if _has_foreign_ubo and (hard_stops or _adverse_has_sanctions):
        ubo_risk = "high"

    # ── Director risk ─────────────────────────────────────────────────
    dir_flags = director_analysis.get("risk_flags", [])
    serious_kw = ("fraud", "sanction", "banned", "disqualified")
    serious = [f for f in dir_flags
               if any(kw in f.lower() for kw in serious_kw)]
    if serious:
        dir_risk = "high"
    elif geo_director_override:
        dir_risk = "high"
    elif len(dir_flags) >= 3:
        dir_risk = "medium"
    elif dir_flags:
        dir_risk = "low-medium"
    else:
        dir_risk = "low"

    # ── Merchant / chargeback risk ────────────────────────────────────
    merchant_risk_level = merchant.get("chargeback_risk", "unknown")

    categories = {
        "Company Age": company_age.get("risk", "unknown"),
        "Company Status": status_analysis.get("risk_level", "unknown"),
        "Industry Risk (DD)": sic_risk.get("risk_level", "unknown"),
        "Registered Office": "low",   # Informational — virtual office is normal
        "Director Risk": dir_risk,
        "Dormancy Risk": "high" if dormancy.get("is_dormant_risk") else "low",
        "UBO Transparency": ubo_risk,
        "PSC Risk": "medium" if psc_analysis.get("flags") else "low",
        "Website Cross-Ref": (
            "unknown" if _web_search_failed
            else (
                "high" if cross_ref.get("credibility_level") in ("Low", "Unknown")
                else ("medium" if cross_ref.get("credibility_level") in ("Weak",)
                      else "low")
            )
        ),
        "Merchant / Chargeback": merchant_risk_level,
        "Filing / Accounts": filing_risk if filing_risk != "unknown" else "low",
        "FATF Screening": (
            "unknown" if _fatf_search_failed
            else (
                "high" if _fatf_risk in ("high", "very high")
                else ("medium" if _fatf_risk == "medium"
                      else ("unknown" if _fatf_risk == "unknown" else "low"))
            )
        ),
        "Adverse Media": (
            "unknown" if _adverse_search_failed
            else (
                "high" if any(a.get("_relevant") for a in adverse_media)
                else ("medium" if adverse_media else "low")
            )
        ),
    }

    # ══════════════════════════════════════════════════════════════════
    # SCORING — "MOST SEVERE" logic, NOT average math
    # In compliance, risk is determined by the most severe single flag.
    # If ANY category is 🔴 High, overall cannot be lower than High.
    # ══════════════════════════════════════════════════════════════════
    _scores = {"high": 3, "medium": 2, "low-medium": 1.5, "low": 1, "unknown": 2}
    total = sum(_scores.get(v, 1) for v in categories.values())
    max_possible = len(categories) * 3
    normalised = round(total / max_possible * 100, 1)

    # Count severity levels
    _high_count = sum(1 for v in categories.values() if v == "high")
    _medium_count = sum(1 for v in categories.values() if v in ("medium", "low-medium"))
    _unknown_count = sum(1 for v in categories.values() if v == "unknown")

    # "Most Severe" override — a single High category = overall High minimum
    if _high_count >= 1:
        overall = "High"
        # Ensure the numeric score reflects severity (floor at 70)
        normalised = max(normalised, 70.0)
    elif _unknown_count >= 2 or (_unknown_count >= 1 and _medium_count >= 2):
        # Multiple unknowns or unknown + multiple mediums = High
        overall = "High"
        normalised = max(normalised, 65.0)
    elif _medium_count >= 3 or _unknown_count >= 1:
        overall = "Medium"
        normalised = max(normalised, 45.0)
    elif normalised >= 60:
        overall = "High"
    elif normalised >= 40:
        overall = "Medium"
    else:
        overall = "Low"

    # ══════════════════════════════════════════════════════════════════
    # VETO RULE — hard stops override everything
    # ══════════════════════════════════════════════════════════════════
    if hard_stops:
        normalised = 100.0
        overall = "Critical"

    # Collect all flags
    all_flags: list[str] = []
    # Hard stops first (most important)
    all_flags.extend(hard_stops)
    all_flags.extend(status_analysis.get("flags", []))
    all_flags.extend(director_analysis.get("risk_flags", []))
    all_flags.extend(psc_analysis.get("flags", []))
    all_flags.extend(cross_ref.get("red_flags", []))
    all_flags.extend(merchant.get("flags", []))
    if virtual_office.get("is_virtual"):
        all_flags.append(
            f"Virtual office: {virtual_office.get('note', '')} (informational)"
        )
    if dormancy.get("is_dormant_risk"):
        all_flags.append(dormancy.get("note", "Dormancy risk detected"))
    if company_age.get("risk") in ("high", "medium"):
        all_flags.append(company_age.get("note", ""))
    for u in ultimate:
        tt = u.get("terminal_type", "")
        if "Foreign" in tt or "End of Trace" in tt:
            all_flags.append(
                f"ℹ️ UBO chain ends at foreign/unresolvable entity: "
                f"{u.get('name', '?')} — recommendation: request UBO "
                f"documentation from the applicant"
            )
    # Geopolitical director flag
    if geo_director_override:
        all_flags.append(
            f"🔴 Geopolitical Risk: {high_risk_dir_count}/{dir_count} directors "
            f"({high_risk_dir_count/dir_count*100:.0f}%) are from High/Very High Risk "
            f"jurisdictions — automatic Director Risk escalation to 🔴 High"
        )
    if ubo_high_risk > 0:
        all_flags.append(
            f"🔴 UBO geopolitical risk: {ubo_high_risk} ultimate beneficial "
            f"owner(s) from High/Very High Risk jurisdiction(s)"
        )
    # Filing overdue flag
    if filing_risk in ("high", "medium"):
        all_flags.append(filing_note)

    # Search error flags — API failures must be visible
    for se in search_errors:
        all_flags.append(f"⚠️ SEARCH ERROR: {se} — result marked as UNKNOWN, not clean")

    # Restricted activities flags
    for ra in restricted_activities.get("prohibited", []):
        all_flags.append(
            f"🚫 PROHIBITED activity detected: {ra['category']} "
            f"(keywords: {', '.join(ra['matched_keywords'][:3])})"
        )
    for ra in restricted_activities.get("restricted", []):
        all_flags.append(
            f"⚠️ RESTRICTED activity detected: {ra['category']} "
            f"(keywords: {', '.join(ra['matched_keywords'][:3])})"
        )

    # HROB vertical flags
    if hrob_verticals.get("requires_hrob"):
        for v in hrob_verticals.get("matched_verticals", []):
            if v["confidence"] in ("high", "medium"):
                all_flags.append(
                    f"⚠️ HROB vertical: {v['vertical']} (confidence: {v['confidence']})"
                )

    # Industry mismatch flag
    if actual_industry.get("sic_alignment") == "mismatch":
        all_flags.append(
            f"⚠️ Industry mismatch: SIC says '{actual_industry['sic_declared_industry']}' "
            f"but website evidence suggests '{actual_industry['determined_industry']}'"
        )

    all_positives = cross_ref.get("positives", []) + merchant.get("positives", [])

    return {
        "overall_risk": overall,
        "risk_score": normalised,
        "category_risks": categories,
        "total_flags": len(all_flags),
        "all_flags": all_flags,
        "positives": all_positives,
        "hard_stops": hard_stops,
        "hard_stop_triggered": len(hard_stops) > 0,
        "geopolitical_director_override": geo_director_override,
        "geopolitical_detail": {
            "total_directors": dir_count,
            "high_risk_directors": high_risk_dir_count,
            "high_risk_pct": round(high_risk_dir_count / dir_count * 100, 1) if dir_count > 0 else 0,
            "ubo_high_risk_count": ubo_high_risk,
        },
        "filing_overdue": {
            "risk": filing_risk,
            "gap_days": accounts_data.get("filing_gap_days"),
            "gap_months": accounts_data.get("filing_gap_months"),
            "note": filing_note,
        },
        "search_errors": search_errors,
    }


# ═══════════════════════════════════════════════════════════════════════════════
# MASTER ORCHESTRATOR
# ═══════════════════════════════════════════════════════════════════════════════

def run_company_check(
    company_num: str,
    website_url: str,
    *,
    tavily_search_fn: Callable | None = None,
    adverse_search_fn: Callable | None = None,
    fatf_screen_fn: Callable | None = None,
    online_presence_fn: Callable | None = None,
    social_osint_fn: Callable | None = None,
    social_extract_fn: Callable | None = None,
) -> dict:
    """Run a full company sense-check.

    Parameters
    ----------
    company_num    : Companies House number (e.g. "12345678").
    website_url    : The company's trading website.
    tavily_search_fn  : search_generic(name) → list[dict]
    adverse_search_fn : search_adverse_media(name, context) → list[dict]
    fatf_screen_fn    : screen_entity(name, type) → dict
    online_presence_fn: search_online_presence(name, url) → list[dict]
    social_extract_fn : extract_social_media_from_website(url) → dict

    Returns
    -------
    dict — structured analysis bundle.
    """
    now = datetime.now(timezone.utc).isoformat()

    # ── Phase 1: Companies House data (parallel) ─────────────────────
    profile = fetch_company_full_profile(company_num)
    company_name = profile.get("company_name", "Unknown")

    with ThreadPoolExecutor(max_workers=6) as executor:
        f_officers = executor.submit(fetch_company_officers, company_num)
        f_pscs = executor.submit(fetch_company_pscs, company_num)
        f_filings = executor.submit(fetch_company_filing_history, company_num, 30)
        f_charges = executor.submit(fetch_company_charges, company_num)

        officers = f_officers.result(timeout=30)
        pscs = f_pscs.result(timeout=30)
        filings = f_filings.result(timeout=30)
        charges = f_charges.result(timeout=30)

    # ── Phase 1.5: Recursive UBO trace ───────────────────────────────
    try:
        ubo_chain = trace_ubo_chain(company_num, max_depth=3)
    except Exception:
        ubo_chain = {
            "chain": [], "ultimate_owners": [], "layers_traced": 0,
            "max_depth_reached": False, "graph_edges": [],
        }

    # ── Phase 2: Analysis ────────────────────────────────────────────
    company_age = analyse_company_age(profile.get("date_of_creation", ""))
    status_analysis = analyse_company_status(profile)
    sic_risk = classify_sic_risk(profile.get("sic_codes"))
    virtual_office = detect_virtual_office(profile.get("registered_office_address"))
    director_analysis = analyse_directors(officers, company_num)
    dormancy = detect_dormancy_risk(filings, profile.get("date_of_creation", ""))
    accounts_data = extract_accounts_data(filings, company_num)
    psc_analysis = analyse_pscs(pscs)

    # ── Phase 3: Web intelligence (parallel) ─────────────────────────
    web_results: list[dict] = []
    adverse_results: list[dict] = []
    fatf_results: dict = {}
    online_results: list[dict] = []
    _search_errors: list[str] = []  # Track API failures — never treat as "clean"

    # Deep-scrape the website directly for rich text (not just API snippets)
    from api_clients.tavily_search import scrape_website_deep
    deep_scrape_results: list[dict] = []
    if website_url:
        try:
            deep_scrape_results = scrape_website_deep(
                website_url, max_pages=12, max_chars_per_page=5000,
            )
        except Exception as _scrape_err:
            _search_errors.append(
                f"⚠️ Direct website scrape failed: {type(_scrape_err).__name__}: "
                f"{str(_scrape_err)[:120]}"
            )

    with ThreadPoolExecutor(max_workers=6) as executor:
        futures: dict[str, Any] = {}
        if tavily_search_fn:
            futures["web"] = executor.submit(tavily_search_fn, company_name)
        if adverse_search_fn:
            futures["adverse"] = executor.submit(
                adverse_search_fn, company_name, ["company", "director"])
        if fatf_screen_fn:
            futures["fatf"] = executor.submit(
                fatf_screen_fn, company_name, "company")
        if online_presence_fn:
            futures["online"] = executor.submit(
                online_presence_fn, company_name, website_url)

        # Also search the website itself
        if tavily_search_fn and website_url:
            futures["site"] = executor.submit(tavily_search_fn, f"site:{website_url}")

        # ── Adverse media for directors ───────────────────────────────
        director_names = [
            d.get("name", "")
            for d in director_analysis.get("directors", [])
            if d.get("name")
        ]
        for idx, dir_name in enumerate(director_names[:5]):
            if adverse_search_fn:
                futures[f"adv_dir_{idx}"] = executor.submit(
                    adverse_search_fn, dir_name, [company_name])

        # ── Adverse media for previous company names ──────────────────
        prev_names = [
            pn.get("name", "")
            for pn in profile.get("previous_company_names", [])
            if pn.get("name")
        ]
        for idx, pname in enumerate(prev_names[:3]):
            if adverse_search_fn:
                futures[f"adv_prev_{idx}"] = executor.submit(
                    adverse_search_fn, pname, [company_name])

        for key, future in futures.items():
            try:
                result = future.result(timeout=45)
                if key == "web":
                    web_results = result or []
                elif key == "adverse":
                    adverse_results = result or []
                elif key.startswith("adv_dir_") or key.startswith("adv_prev_"):
                    # Merge director/past-name adverse results, de-dupe by URL
                    seen_urls = {r.get("url") for r in adverse_results}
                    for r in (result or []):
                        if r.get("url") not in seen_urls:
                            adverse_results.append(r)
                            seen_urls.add(r.get("url"))
                elif key == "fatf":
                    fatf_results = result or {}
                elif key == "online":
                    online_results = result or []
                elif key == "site":
                    web_results.extend(result or [])
            except Exception as _search_err:
                # NEVER let a technical failure masquerade as a clean result.
                # Track the error so the risk matrix can flag it as UNKNOWN.
                _err_label = {
                    "web": "Web search",
                    "adverse": "Adverse media search",
                    "fatf": "FATF screening",
                    "online": "Online presence search",
                    "site": "Website content search",
                }.get(key, f"Search ({key})")
                if key.startswith("adv_dir_"):
                    _err_label = f"Director adverse media search ({key})"
                elif key.startswith("adv_prev_"):
                    _err_label = f"Previous-name adverse media search ({key})"
                _search_errors.append(
                    f"⚠️ {_err_label} failed: {type(_search_err).__name__}: "
                    f"{str(_search_err)[:120]}"
                )

    # ── Merge deep-scraped pages into web_results (richer content) ───
    if deep_scrape_results:
        seen_urls = {r.get("url") for r in web_results if r.get("url")}
        for page in deep_scrape_results:
            if page.get("url") not in seen_urls:
                web_results.append(page)
                seen_urls.add(page.get("url"))

    # ── Phase 4: Cross-reference & address credibility ───────────────
    cross_ref = cross_reference_website(profile, web_results, website_url)

    # ── Phase 4.1: Deterministic social media extraction ─────────────
    # Use the same HTML-scraping approach as charity mode for accuracy.
    # This scrapes the actual website HTML for <a> links to social platforms.
    if social_extract_fn and website_url:
        try:
            _html_social = social_extract_fn(website_url)
            display_map_html = {
                "linkedin": "LinkedIn",
                "twitter": "Twitter / X",
                "facebook": "Facebook",
                "instagram": "Instagram",
                "youtube": "YouTube",
            }
            for plat_key, url in (_html_social or {}).items():
                if url:
                    display_name = display_map_html.get(plat_key, plat_key)
                    cross_ref["social_links"][display_name] = url
        except Exception:
            pass

    # ── Phase 4.2: OSINT social pivot (backup for missing) ───────────
    # Only search externally for platforms NOT found via HTML scraping.
    if social_osint_fn:
        existing_social = cross_ref.get("social_links", {})
        _priority_platforms = {"LinkedIn", "Twitter / X", "Facebook"}
        missing = _priority_platforms - set(existing_social.keys())
        if missing:
            try:
                osint_social = social_osint_fn(company_name, website_url)
                _osint_confidence = osint_social.pop("_osint_confidence", "high")
                display_map = {
                    "linkedin": "LinkedIn",
                    "twitter": "Twitter / X",
                    "facebook": "Facebook",
                    "instagram": "Instagram",
                }
                for plat_key, url in osint_social.items():
                    if plat_key.startswith("_"):
                        continue  # Skip metadata keys
                    display_name = display_map.get(plat_key, plat_key)
                    if display_name not in existing_social and url:
                        cross_ref["social_links"][display_name] = url
                        _source_note = "found via external OSINT search"
                        if _osint_confidence == "low":
                            _source_note += " (no website provided — lower confidence)"
                        cross_ref.setdefault("osint_social_sources", []).append(
                            f"{display_name} {_source_note}"
                        )
                # Store confidence level for downstream consumers (LLM prompt, etc.)
                cross_ref["osint_confidence"] = _osint_confidence
            except Exception:
                pass

    address_intel = analyse_address_credibility(
        virtual_office, cross_ref, web_results,
    )
    sic_mismatch = check_sic_website_mismatch(sic_risk, web_results)

    # ── Phase 4.5: Payment-method suitability ────────────────────────
    payment_suitability = analyse_payment_suitability(
        profile.get("sic_codes"),
        web_results,
        director_analysis,
        company_age,
        website_url,
    )

    # ── Phase 4.6: Restricted activities detection ───────────────────
    web_text_blob = " ".join(
        r.get("content", "") + " " + r.get("title", "")
        for r in web_results
    )
    restricted_activities = detect_restricted_activities(
        web_text=web_text_blob,
        sic_risk=sic_risk,
        company_name=company_name,
    )

    # ── Phase 4.7: HROB vertical classification ─────────────────────
    hrob_verticals = classify_hrob_verticals(
        web_text=web_text_blob,
        sic_codes=profile.get("sic_codes"),
        company_name=company_name,
        sic_risk=sic_risk,
    )

    # ── Phase 4.8: Holistic industry classification ──────────────────
    actual_industry = classify_actual_industry(
        web_text=web_text_blob,
        sic_risk=sic_risk,
        company_name=company_name,
        sic_codes=profile.get("sic_codes"),
    )

    # ── Phase 5: Risk matrix (with UBO + payment profile + hard stops) ─
    risk_matrix = build_risk_matrix(
        company_age, status_analysis, sic_risk, virtual_office,
        director_analysis, dormancy, psc_analysis, cross_ref,
        ubo_chain=ubo_chain,
        merchant=payment_suitability,
        fatf_screening=fatf_results,
        adverse_media=adverse_results,
        accounts_data=accounts_data,
        restricted_activities=restricted_activities,
        hrob_verticals=hrob_verticals,
        actual_industry=actual_industry,
        search_errors=_search_errors,
    )

    # ── FCA Regulatory Assessment (for FCA-regulated entities) ─────────
    fca_context = FCAContext()
    sic_codes = profile.get("sic_codes", [])
    industry_category = sic_risk.get("industry_category", "")
    
    # Check if company is FCA-regulated based on SIC codes and industry
    fca_regulated_keywords = ["financial", "investment", "insurance", "banking", "credit", "lending", "payment"]
    is_fca_regulated = any(
        kw in industry_category.lower() for kw in fca_regulated_keywords
    )
    
    fca_assessment = {
        "is_fca_regulated": is_fca_regulated,
        "sic_codes": sic_codes,
        "industry_category": industry_category,
    }
    
    if is_fca_regulated:
        # Extract risk signals from adverse media and other sources
        fca_signals = fca_context.get_fca_risk_signals(adverse_results, company_name)
        
        fca_assessment.update({
            "regulatory_status": "FCA-Regulated Entity",
            "risk_signals": fca_signals,
            "fca_keywords_found": sum(1 for sig in fca_signals if sig.get("severity") in ("critical", "high")),
            "enhanced_search_applied": True,
            "fca_regulation_link": "https://register.fca.org.uk/",
            "guidance_link": "https://www.fca.org.uk/",
        })
    else:
        fca_assessment["regulatory_status"] = "Not FCA-Regulated"

    # ── Phase 6: Director & ownership network graph ──────────────────
    try:
        network_dot = build_director_network_dot(
            company_name, company_num,
            director_analysis.get("directors", []),
            ubo_chain,
        )
    except Exception:
        network_dot = ""

    # ── Phase 6.5: Fraud Detection Suite ─────────────────────────────
    fraud_analysis = {}
    try:
        fraud_analysis = run_uk_fraud_detection_suite(
            company_num=company_num,
            company_name=company_name,
            incorporation_date=profile.get("date_of_creation"),
            status=profile.get("company_status") or "unknown",
            officers=director_analysis.get("directors", []),
            filing_history=filings,
            accounts_data=accounts_data,
            virtual_office=virtual_office,
            director_analysis=director_analysis,
            registered_office=profile.get("registered_office_address", {}),
        )
    except Exception as e:
        fraud_analysis = {"overall_fraud_score": 0, "fraud_risk_level": "Low", "alerts": []}

    # ── Assemble output ──────────────────────────────────────────────
    return {
        "company_number": company_num,
        "company_name": company_name,
        "website_url": website_url,
        "checked_at": now,
        "profile": {
            "status": profile.get("company_status"),
            "type": profile.get("type"),
            "date_of_creation": profile.get("date_of_creation"),
            "sic_codes": profile.get("sic_codes", []),
            "registered_office": profile.get("registered_office_address", {}),
            "previous_names": profile.get("previous_company_names", []),
            "has_charges": profile.get("has_charges", False),
            "has_insolvency_history": profile.get("has_insolvency_history", False),
            "can_file": profile.get("can_file", False),
            "jurisdiction": profile.get("jurisdiction", ""),
            "accounts_next_due": (profile.get("accounts", {}) or {}).get("next_due", ""),
            "confirmation_next_due": (profile.get("confirmation_statement", {}) or {}).get("next_due", ""),
        },
        "company_age": company_age,
        "status_analysis": status_analysis,
        "sic_risk": sic_risk,
        "sic_mismatch": sic_mismatch,
        "virtual_office": virtual_office,
        "director_analysis": director_analysis,
        "dormancy": dormancy,
        "accounts_data": accounts_data,
        "psc_analysis": psc_analysis,
        "ubo_chain": ubo_chain,
        "payment_suitability": payment_suitability,
        # back-compat alias — readers may still expect "merchant_suitability".
        # Same dict; legacy DD-only fields are no longer populated.
        "merchant_suitability": payment_suitability,
        "restricted_activities": restricted_activities,
        "hrob_verticals": hrob_verticals,
        "actual_industry": actual_industry,
        "address_intelligence": address_intel,
        "network_graph_dot": network_dot,
        "charges": [
            {
                "status": c.get("status", ""),
                "classification": (c.get("classification", {}) or {}).get("description", ""),
                "created_on": c.get("created_on", ""),
                "delivered_on": c.get("delivered_on", ""),
            }
            for c in (charges or [])[:10]
        ],
        "filing_history_summary": {
            "total_recent": len(filings),
            "latest_filing": filings[0] if filings else {},
            "accounts_data": accounts_data,
        },
        "cross_reference": cross_ref,
        "adverse_media": adverse_results,
        "fatf_screening": fatf_results,
        "online_presence": online_results,
        "fca_assessment": fca_assessment,
        "fraud_detection": fraud_analysis,
        "risk_matrix": risk_matrix,
        "search_errors": _search_errors,
    }
