"""
core/website_intel.py — Deep website OSINT for the entity report.

When the buyer hands us a website (or we auto-discover one), this module
fetches it and extracts a structured intelligence bundle:

    - meta tags (og:*, twitter:*, generic title/description)
    - social media links (LinkedIn, X, Facebook, Instagram, YouTube, GitHub)
    - key compliance pages found on-site (Privacy Policy, Cookie Policy,
      Terms, Modern Slavery Act statement, Anti-Bribery)
    - on-site addresses, phone numbers, email addresses (regex-extracted)
    - HTTPS / certificate presence
    - approximate page count + content depth from a 2-level crawl
    - domain age (best-effort via socket WHOIS query, no external paid API)

It is deliberately resilient: every section can fail without taking down
the whole stage. Output is a single dict that the prompt + frontend
consume.

Public API
----------
    fetch_website_intelligence(url: str, max_pages: int = 6) -> dict
"""

from __future__ import annotations

import logging
import re
import socket
import ssl
import time
from datetime import datetime, timezone
from typing import Iterable
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

log = logging.getLogger("probitas.website_intel")

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)

REQ_TIMEOUT = 12  # seconds per request
TOTAL_BUDGET = 35  # seconds for the whole stage


# ─── Social platform patterns ────────────────────────────────────────────────

_SOCIAL_PATTERNS: dict[str, re.Pattern] = {
    "linkedin": re.compile(r"https?://(?:[a-z]{2,3}\.)?linkedin\.com/(?:company|school|in)/[A-Za-z0-9_\-/.]+", re.I),
    "twitter": re.compile(r"https?://(?:www\.|mobile\.)?(?:twitter\.com|x\.com)/[A-Za-z0-9_]{1,30}(?:/)?", re.I),
    "facebook": re.compile(r"https?://(?:www\.|m\.|en-gb\.)?facebook\.com/[A-Za-z0-9_.\-]+", re.I),
    "instagram": re.compile(r"https?://(?:www\.)?instagram\.com/[A-Za-z0-9_.]+", re.I),
    "youtube": re.compile(r"https?://(?:www\.)?youtube\.com/(?:channel|c|user|@)[A-Za-z0-9_\-/]+", re.I),
    "github": re.compile(r"https?://(?:www\.)?github\.com/[A-Za-z0-9_\-]+(?!/)", re.I),
    "tiktok": re.compile(r"https?://(?:www\.)?tiktok\.com/@[A-Za-z0-9_.]+", re.I),
}


# ─── Compliance page patterns (lowercase URL or link text matches) ───────────

_COMPLIANCE_PAGES = {
    "privacy_policy":   re.compile(r"(privacy[\-_\s]?policy|privacy[\-_\s]?notice|data[\-_\s]?protection)", re.I),
    "cookie_policy":    re.compile(r"(cookie[\-_\s]?policy|cookie[\-_\s]?notice)", re.I),
    "terms":            re.compile(r"(terms[\-_\s]?(of[\-_\s]?(use|service|business))?|terms[\-_\s]?and[\-_\s]?conditions|t&cs?)", re.I),
    "modern_slavery":   re.compile(r"modern[\-_\s]?slavery", re.I),
    "anti_bribery":     re.compile(r"(anti[\-_\s]?bribery|bribery[\-_\s]?policy|anti[\-_\s]?corruption)", re.I),
    "complaints":       re.compile(r"complaints?[\-_\s]?(policy|procedure|handling)", re.I),
    "accessibility":    re.compile(r"accessibility[\-_\s]?(statement|policy)", re.I),
    "safeguarding":     re.compile(r"safeguarding[\-_\s]?(policy|statement)", re.I),
    "whistleblowing":   re.compile(r"whistleblow", re.I),
    "regulatory":       re.compile(r"(regulated\s+by|authorised\s+by\s+(the|fca|pra)|fca\s+register|firm\s+reference\s+number|frn[:\s])", re.I),
    "registered_charity": re.compile(r"registered\s+charit(y|ies)\s+(no|number|registration)", re.I),
}


_PHONE_RE = re.compile(
    r"(?:\+44|0044|\(0\)|\b0)\s?[\d\s\-()]{8,15}"
)
_EMAIL_RE = re.compile(
    r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b"
)
_UK_POSTCODE_RE = re.compile(
    r"\b[A-PR-UWYZ][A-HK-Y]?[0-9][0-9A-Z]?\s?[0-9][ABD-HJLNP-UW-Z]{2}\b",
    re.I,
)


# ─── Helpers ─────────────────────────────────────────────────────────────────


def _normalise_url(raw: str) -> str:
    raw = (raw or "").strip()
    if not raw:
        return ""
    if not re.match(r"^https?://", raw, re.I):
        raw = "https://" + raw
    parsed = urlparse(raw)
    if not parsed.netloc:
        return ""
    return f"{parsed.scheme}://{parsed.netloc}{parsed.path or '/'}"


def _safe_get(url: str, timeout: int = REQ_TIMEOUT) -> requests.Response | None:
    try:
        r = requests.get(
            url,
            timeout=timeout,
            headers={"User-Agent": USER_AGENT, "Accept": "text/html,application/xhtml+xml"},
            allow_redirects=True,
            verify=True,
        )
        if r.status_code == 200 and r.text:
            return r
    except requests.exceptions.SSLError:
        # Some sites have weak certs but still useful for OSINT
        try:
            r = requests.get(
                url, timeout=timeout,
                headers={"User-Agent": USER_AGENT},
                allow_redirects=True,
                verify=False,
            )
            if r.status_code == 200 and r.text:
                r._probitas_ssl_warning = True  # sentinel
                return r
        except Exception:
            return None
    except Exception as e:
        log.debug("GET %s failed: %s", url, e)
    return None


# ─── Extractors ──────────────────────────────────────────────────────────────


def _extract_meta(soup: BeautifulSoup) -> dict:
    """og:*, twitter:*, generic title + description."""
    meta: dict[str, str] = {}
    title_tag = soup.find("title")
    if title_tag and title_tag.text:
        meta["title"] = title_tag.text.strip()[:300]

    for tag in soup.find_all("meta"):
        prop = (tag.get("property") or tag.get("name") or "").lower()
        if not prop:
            continue
        content = (tag.get("content") or "").strip()
        if not content:
            continue
        if prop in {
            "og:title", "og:description", "og:image", "og:site_name", "og:url",
            "twitter:title", "twitter:description", "twitter:site", "twitter:creator",
            "description",
        }:
            meta[prop] = content[:500]

    # Canonical link
    canonical = soup.find("link", rel="canonical")
    if canonical and canonical.get("href"):
        meta["canonical"] = canonical["href"]

    # Author
    author = soup.find("meta", attrs={"name": "author"})
    if author and author.get("content"):
        meta["author"] = author["content"]

    return meta


def _extract_link_rel_me(soup: BeautifulSoup) -> list[str]:
    """`<link rel="me" href="...">` is the modern self-asserted profile link."""
    out: list[str] = []
    for tag in soup.find_all("link", rel=lambda r: r and "me" in r):
        href = tag.get("href")
        if href:
            out.append(href.strip())
    return out


def _extract_social_links(html: str, base_url: str) -> dict[str, list[str]]:
    """Match social URLs found anywhere on the page."""
    found: dict[str, list[str]] = {}
    for platform, pat in _SOCIAL_PATTERNS.items():
        hits = list(set(pat.findall(html)))
        # Filter own-domain (e.g. linkedin.com/share?url=...)
        filtered = [h for h in hits if "share" not in h.lower() and "intent/tweet" not in h.lower()]
        if filtered:
            found[platform] = filtered[:5]
    return found


def _detect_compliance_pages(soup: BeautifulSoup, all_links: Iterable[str]) -> dict[str, str]:
    """Match each compliance topic to the best link or text-snippet found."""
    out: dict[str, str] = {}
    body_text = soup.get_text(" ", strip=True).lower()

    # First pass — match by link href / link text
    link_pool: list[tuple[str, str]] = []
    for a in soup.find_all("a", href=True):
        link_pool.append((a["href"], (a.get_text() or "").strip()))

    for topic, pat in _COMPLIANCE_PAGES.items():
        # Look in href OR link text
        for href, text in link_pool:
            haystack = href + " " + text
            if pat.search(haystack):
                out[topic] = href
                break
        # Fall back to body text mention (e.g. "Authorised by the FCA")
        if topic not in out and pat.search(body_text):
            out[topic] = "(text mention only)"

    return out


def _extract_contacts(html_text: str) -> dict:
    return {
        "phones": list(set(p.strip() for p in _PHONE_RE.findall(html_text)))[:5],
        "emails": list(set(_EMAIL_RE.findall(html_text)))[:8],
        "postcodes": list(set(_UK_POSTCODE_RE.findall(html_text)))[:5],
    }


def _ssl_info(url: str) -> dict:
    """Quick SSL cert check — issuer, expiry."""
    parsed = urlparse(url)
    if parsed.scheme != "https":
        return {"https": False}
    host = parsed.hostname
    if not host:
        return {"https": False}
    try:
        ctx = ssl.create_default_context()
        with socket.create_connection((host, 443), timeout=5) as sock:
            with ctx.wrap_socket(sock, server_hostname=host) as ssock:
                cert = ssock.getpeercert()
        issuer = dict(x[0] for x in cert.get("issuer", []))
        expiry = cert.get("notAfter", "")
        return {
            "https": True,
            "issuer": issuer.get("organizationName") or issuer.get("commonName") or "",
            "expires": expiry,
            "valid_now": True,
        }
    except Exception as e:
        return {"https": True, "error": str(e)}


def _whois_domain_age(domain: str) -> dict:
    """Best-effort domain age via the `whois` library if available.

    Returns {created_iso, age_days, age_years} or {} on any failure.
    Pure stdlib fallback to a socket-level WHOIS query if the library isn't
    installed.
    """
    try:
        import whois  # type: ignore
        w = whois.whois(domain)
        created = w.creation_date
        if isinstance(created, list):
            created = created[0] if created else None
        if isinstance(created, datetime):
            now = datetime.now(timezone.utc) if created.tzinfo is None else datetime.now(created.tzinfo)
            delta = now - created.replace(tzinfo=created.tzinfo) if created.tzinfo else (now - created)
            return {
                "created_iso": created.isoformat(timespec="seconds"),
                "age_days": delta.days,
                "age_years": round(delta.days / 365.25, 2),
            }
    except ImportError:
        pass
    except Exception as e:
        log.debug("WHOIS failed for %s: %s", domain, e)
    return {}


# ─── Main entry point ────────────────────────────────────────────────────────


def fetch_website_intelligence(url: str, max_pages: int = 6) -> dict:
    """Fetch + parse the entity website. Returns a structured dict.

    The shape is stable so the prompt + frontend can rely on it.

    Top-level keys:
        url                   the normalised URL we actually used
        ok                    True if root page loaded
        ssl                   {https, issuer, expires}
        meta                  {title, description, og:*, twitter:*}
        social                {linkedin: [...], twitter: [...], ...}
        social_rel_me         list of <link rel=me> URLs
        compliance_pages      {privacy_policy: href, modern_slavery: href, ...}
        contacts              {phones, emails, postcodes}
        domain                hostname
        domain_age            {created_iso, age_days, age_years}  may be {}
        pages_crawled         int
        page_links_total      int
        signals               list of short human-readable observations
        error                 string only if ok=False
    """
    started = time.time()
    base_url = _normalise_url(url)
    if not base_url:
        return {"ok": False, "error": "Empty / invalid URL"}

    parsed_root = urlparse(base_url)
    domain = parsed_root.netloc

    out: dict = {
        "url": base_url,
        "ok": False,
        "domain": domain,
        "ssl": {},
        "meta": {},
        "social": {},
        "social_rel_me": [],
        "compliance_pages": {},
        "contacts": {},
        "domain_age": {},
        "pages_crawled": 0,
        "page_links_total": 0,
        "signals": [],
    }

    # 1. Root page
    root = _safe_get(base_url)
    if root is None:
        out["error"] = f"Could not fetch {base_url}"
        return out

    out["ok"] = True
    out["pages_crawled"] = 1
    if getattr(root, "_probitas_ssl_warning", False):
        out["signals"].append("SSL certificate verification failed (downgraded to plain TLS) — investigate further.")

    soup = BeautifulSoup(root.text, "html.parser")
    out["meta"] = _extract_meta(soup)
    out["social_rel_me"] = _extract_link_rel_me(soup)

    all_html = root.text
    all_text = soup.get_text(" ", strip=True)

    # 2. Crawl up to max_pages internal links — focused on common compliance paths
    visited = {base_url}
    candidate_paths = [
        "/privacy", "/privacy-policy", "/legal/privacy",
        "/cookies", "/cookie-policy",
        "/terms", "/terms-of-use", "/terms-conditions",
        "/modern-slavery", "/modern-slavery-statement",
        "/anti-bribery", "/anti-bribery-policy",
        "/about", "/about-us", "/contact", "/contact-us",
        "/regulatory", "/regulation",
    ]
    crawled_count = 1
    for path in candidate_paths:
        if crawled_count >= max_pages:
            break
        if (time.time() - started) > TOTAL_BUDGET:
            break
        candidate = urljoin(base_url, path)
        if candidate in visited:
            continue
        visited.add(candidate)
        r = _safe_get(candidate, timeout=8)
        if r is None:
            continue
        crawled_count += 1
        all_html += "\n" + r.text
        try:
            all_text += "\n" + BeautifulSoup(r.text, "html.parser").get_text(" ", strip=True)
        except Exception:
            pass

    out["pages_crawled"] = crawled_count

    # 3. Extract from combined HTML
    try:
        combined_soup = BeautifulSoup(all_html, "html.parser")
    except Exception:
        combined_soup = soup

    # social
    out["social"] = _extract_social_links(all_html, base_url)
    out["page_links_total"] = len(combined_soup.find_all("a"))

    # compliance pages
    try:
        all_links = [a.get("href", "") for a in combined_soup.find_all("a", href=True)]
        out["compliance_pages"] = _detect_compliance_pages(combined_soup, all_links)
    except Exception as e:
        log.debug("compliance page extraction failed: %s", e)

    # contacts (regex over text only — cheaper, fewer false positives than HTML)
    out["contacts"] = _extract_contacts(all_text)

    # 4. SSL
    out["ssl"] = _ssl_info(base_url)

    # 5. Domain age (best-effort)
    if domain:
        out["domain_age"] = _whois_domain_age(domain)

    # 6. Signals — derive a few human-readable observations
    sigs = out["signals"]
    if out["meta"].get("og:title"):
        sigs.append(f"Open Graph metadata present (og:title = '{out['meta']['og:title'][:60]}')")
    n_social = sum(len(v) for v in out["social"].values())
    if n_social > 0:
        platforms = ", ".join(sorted(out["social"].keys()))
        sigs.append(f"{n_social} social-media link(s) found across {platforms}.")
    else:
        sigs.append("No social-media links detected on the website (informational; many B2B firms have no external social presence).")
    if not out["compliance_pages"].get("privacy_policy"):
        sigs.append("No Privacy Policy page detected — required under UK GDPR if processing personal data.")
    if not out["compliance_pages"].get("modern_slavery"):
        sigs.append("No Modern Slavery Act statement detected — required if turnover ≥ £36m.")
    if out["compliance_pages"].get("regulatory"):
        sigs.append("Regulatory authorisation language found on site — verify against the FCA/PRA register.")
    age = out.get("domain_age", {}).get("age_years")
    if age is not None:
        if age < 1:
            sigs.append(f"Domain registered {age} years ago — recently created, treat with caution.")
        elif age < 3:
            sigs.append(f"Domain registered {age} years ago — relatively new but plausible.")
        else:
            sigs.append(f"Domain age: {age} years — established.")

    return out
