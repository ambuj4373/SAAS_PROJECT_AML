"""
core/evidence_weighting.py — Source credibility scoring and evidence weighting.

Every OSINT source used in the pipeline is assigned a credibility score based
on its domain/type. Government registers, official databases, and major media
outlets receive higher weight than blogs, forums, or unknown sites.

This module:
  1. Classifies source URLs/names into credibility tiers.
  2. Assigns numeric credibility weights (0.0–1.0).
  3. Ranks and filters search results by credibility.
  4. Produces a source quality summary for the analyst report.

Public API:
    score_source(url, title)           → SourceCredibility
    rank_results_by_credibility(results)  → sorted list
    summarise_source_quality(results)     → SourceQualitySummary
"""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlparse

from pydantic import BaseModel, Field


# ═══════════════════════════════════════════════════════════════════════════════
# MODELS
# ═══════════════════════════════════════════════════════════════════════════════

class SourceCredibility(BaseModel):
    """Credibility assessment for a single source."""
    url: str = ""
    domain: str = ""
    source_type: str = Field("unknown", description="official_register|government|major_media|financial_reg|trade_media|ngo|academic|social_media|blog|forum|unknown")
    credibility_score: float = Field(0.5, ge=0, le=1)
    tier: str = Field("C", description="A (highest) | B | C | D (lowest)")
    reason: str = ""


class SourceQualitySummary(BaseModel):
    """Aggregated quality assessment of all sources used."""
    total_sources: int = 0
    tier_a_count: int = 0
    tier_b_count: int = 0
    tier_c_count: int = 0
    tier_d_count: int = 0
    avg_credibility: float = 0.5
    quality_label: str = "Moderate"
    primary_source_types: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


# ═══════════════════════════════════════════════════════════════════════════════
# DOMAIN PATTERN DATABASE
# ═══════════════════════════════════════════════════════════════════════════════

# Tier A: Official registers, government, regulators (0.90-1.00)
_TIER_A_PATTERNS = {
    r"gov\.uk": ("government", 0.95, "UK Government"),
    r"charity-commission|charitycommission": ("official_register", 0.98, "Charity Commission"),
    r"companieshouse\.gov": ("official_register", 0.98, "Companies House"),
    r"fca\.org\.uk": ("financial_reg", 0.95, "FCA"),
    r"hmrc\.gov": ("government", 0.95, "HMRC"),
    r"ico\.org\.uk": ("government", 0.93, "ICO"),
    r"parliament\.uk": ("government", 0.95, "UK Parliament"),
    r"legislation\.gov": ("government", 0.97, "UK Legislation"),
    r"fatf-gafi\.org": ("government", 0.96, "FATF"),
    r"opencharities|register-of-charities": ("official_register", 0.90, "Charity Register"),
    r"oscr\.org": ("official_register", 0.95, "OSCR Scotland"),
    r"ccni\.org": ("official_register", 0.95, "CCNI N. Ireland"),
    r"europa\.eu": ("government", 0.92, "EU Official"),
    r"un\.org|undp\.org|unicef\.org": ("government", 0.93, "United Nations"),
    r"worldbank\.org": ("government", 0.93, "World Bank"),
    r"transparency\.org": ("ngo", 0.90, "Transparency International"),
    r"sanctionssearch|ofsi\.blog\.gov": ("government", 0.96, "Sanctions"),
}

# Tier B: Major media, established news (0.70-0.89)
_TIER_B_PATTERNS = {
    r"bbc\.co\.uk|bbc\.com": ("major_media", 0.88, "BBC"),
    r"reuters\.com": ("major_media", 0.88, "Reuters"),
    r"theguardian\.com": ("major_media", 0.85, "The Guardian"),
    r"ft\.com|financial ?times": ("major_media", 0.87, "Financial Times"),
    r"thetimes\.co\.uk|times\.com": ("major_media", 0.85, "The Times"),
    r"telegraph\.co\.uk": ("major_media", 0.83, "The Telegraph"),
    r"independent\.co\.uk": ("major_media", 0.82, "The Independent"),
    r"sky\.com|news\.sky": ("major_media", 0.82, "Sky News"),
    r"channel4\.com": ("major_media", 0.82, "Channel 4"),
    r"nytimes\.com": ("major_media", 0.87, "New York Times"),
    r"washingtonpost\.com": ("major_media", 0.85, "Washington Post"),
    r"economist\.com": ("major_media", 0.86, "The Economist"),
    r"bloomberg\.com": ("major_media", 0.86, "Bloomberg"),
    r"cnbc\.com": ("major_media", 0.82, "CNBC"),
    r"apnews\.com": ("major_media", 0.88, "AP News"),
    r"aljazeera\.com": ("major_media", 0.80, "Al Jazeera"),
    r"civilsociety\.co\.uk": ("trade_media", 0.78, "Civil Society"),
    r"thirdsector\.co\.uk": ("trade_media", 0.78, "Third Sector"),
    r"charitytoday|charitytimes": ("trade_media", 0.76, "Charity Media"),
    r"accountancyage|accountingweb": ("trade_media", 0.75, "Accountancy Media"),
    r"lawgazette|legalfutures": ("trade_media", 0.76, "Legal Media"),
}

# Tier C: Secondary sources (0.40-0.69)
_TIER_C_PATTERNS = {
    r"wikipedia\.org": ("academic", 0.55, "Wikipedia"),
    r"linkedin\.com": ("social_media", 0.50, "LinkedIn"),
    r"companies\.wiki|opencorporates": ("trade_media", 0.60, "Corporate Database"),
    r"dnb\.com|dunandbradstreet": ("trade_media", 0.65, "D&B"),
    r"glassdoor|indeed\.com": ("social_media", 0.45, "Employment Site"),
    r"trustpilot|reviews\.io": ("social_media", 0.40, "Review Site"),
    r"twitter\.com|x\.com": ("social_media", 0.40, "Twitter/X"),
    r"facebook\.com": ("social_media", 0.40, "Facebook"),
    r"reddit\.com": ("forum", 0.35, "Reddit"),
    r"quora\.com": ("forum", 0.30, "Quora"),
    r"youtube\.com": ("social_media", 0.40, "YouTube"),
    r"instagram\.com": ("social_media", 0.35, "Instagram"),
    r"tiktok\.com": ("social_media", 0.30, "TikTok"),
    r"medium\.com": ("blog", 0.40, "Medium"),
}

# Tier D: Low credibility (0.00-0.39)
_TIER_D_PATTERNS = {
    r"blogspot|wordpress\.com|tumblr": ("blog", 0.25, "Blog Platform"),
    r"pinterest\.com": ("social_media", 0.20, "Pinterest"),
    r"scribd\.com": ("unknown", 0.25, "Scribd"),
}


def _match_domain_patterns(domain: str, url: str) -> tuple[str, float, str] | None:
    """Try matching domain against all tier patterns."""
    combined = f"{domain} {url}".lower()
    for patterns, tier_label in [
        (_TIER_A_PATTERNS, "A"),
        (_TIER_B_PATTERNS, "B"),
        (_TIER_C_PATTERNS, "C"),
        (_TIER_D_PATTERNS, "D"),
    ]:
        for pattern, (src_type, score, reason) in patterns.items():
            if re.search(pattern, combined, re.IGNORECASE):
                return src_type, score, reason
    return None


# ═══════════════════════════════════════════════════════════════════════════════
# PUBLIC API
# ═══════════════════════════════════════════════════════════════════════════════

def score_source(url: str = "", title: str = "") -> SourceCredibility:
    """Assign a credibility score to a source URL/title."""
    domain = ""
    if url:
        try:
            parsed = urlparse(url if "://" in url else f"https://{url}")
            domain = parsed.netloc.lower().lstrip("www.")
        except Exception:
            domain = url.lower()

    # Try pattern matching
    match = _match_domain_patterns(domain, url)
    if match:
        src_type, score, reason = match
        tier = "A" if score >= 0.90 else ("B" if score >= 0.70 else ("C" if score >= 0.40 else "D"))
        return SourceCredibility(
            url=url, domain=domain,
            source_type=src_type, credibility_score=score,
            tier=tier, reason=reason,
        )

    # Heuristic scoring for unknown domains
    score = 0.45
    src_type = "unknown"
    reason = "Unclassified source"

    # Boost for .gov, .org, .ac.uk, .edu domains
    if re.search(r"\.(gov|govt)\.", domain):
        score = 0.88
        src_type = "government"
        reason = "Government domain"
    elif re.search(r"\.(ac\.uk|edu)\b", domain):
        score = 0.72
        src_type = "academic"
        reason = "Academic institution"
    elif domain.endswith(".org") or domain.endswith(".org.uk"):
        score = 0.55
        src_type = "ngo"
        reason = "Organisation domain (.org)"
    elif re.search(r"\.(co\.uk|com)\b", domain):
        score = 0.45
        src_type = "unknown"
        reason = "Commercial domain"

    # Boost for title keywords suggesting official content
    title_lower = (title or "").lower()
    if any(kw in title_lower for kw in ["annual report", "accounts", "filing", "register"]):
        score = min(score + 0.10, 1.0)
        reason += " + official-content keywords"

    tier = "A" if score >= 0.90 else ("B" if score >= 0.70 else ("C" if score >= 0.40 else "D"))

    return SourceCredibility(
        url=url, domain=domain,
        source_type=src_type, credibility_score=score,
        tier=tier, reason=reason,
    )


def rank_results_by_credibility(
    results: list[dict[str, Any]],
    min_credibility: float = 0.0,
) -> list[dict[str, Any]]:
    """Sort search results by source credibility (highest first).

    Each result dict should have 'url' and optionally 'title' keys.
    Adds '_credibility' key to each result.
    """
    scored = []
    for r in results or []:
        url = r.get("url", r.get("link", ""))
        title = r.get("title", "")
        cred = score_source(url, title)
        r["_credibility"] = {
            "score": cred.credibility_score,
            "tier": cred.tier,
            "source_type": cred.source_type,
            "reason": cred.reason,
        }
        if cred.credibility_score >= min_credibility:
            scored.append(r)

    scored.sort(key=lambda x: x.get("_credibility", {}).get("score", 0), reverse=True)
    return scored


def summarise_source_quality(results: list[dict[str, Any]]) -> SourceQualitySummary:
    """Produce an aggregate quality summary of sources used in analysis."""
    if not results:
        return SourceQualitySummary(quality_label="No Sources")

    scores = []
    tiers = {"A": 0, "B": 0, "C": 0, "D": 0}
    types_seen: set[str] = set()
    warnings: list[str] = []

    for r in results:
        cred = r.get("_credibility")
        if not cred:
            url = r.get("url", r.get("link", ""))
            sr = score_source(url, r.get("title", ""))
            cred = {"score": sr.credibility_score, "tier": sr.tier, "source_type": sr.source_type}

        scores.append(cred["score"])
        tier = cred.get("tier", "C")
        tiers[tier] = tiers.get(tier, 0) + 1
        types_seen.add(cred.get("source_type", "unknown"))

    avg = sum(scores) / len(scores) if scores else 0.5

    if tiers["A"] == 0:
        warnings.append("No Tier A (official/government) sources found")
    if tiers["D"] > len(results) * 0.3:
        warnings.append(f"{tiers['D']} of {len(results)} sources are low-credibility (Tier D)")
    if avg < 0.4:
        warnings.append("Overall source quality is low — findings should be treated with caution")

    if avg >= 0.75:
        label = "High"
    elif avg >= 0.55:
        label = "Good"
    elif avg >= 0.40:
        label = "Moderate"
    else:
        label = "Low"

    return SourceQualitySummary(
        total_sources=len(results),
        tier_a_count=tiers["A"],
        tier_b_count=tiers["B"],
        tier_c_count=tiers["C"],
        tier_d_count=tiers["D"],
        avg_credibility=round(avg, 2),
        quality_label=label,
        primary_source_types=sorted(types_seen),
        warnings=warnings,
    )


def render_source_quality_badge(summary: SourceQualitySummary) -> str:
    """Return HTML for a source quality indicator badge."""
    color_map = {"High": "#28a745", "Good": "#17a2b8", "Moderate": "#ffc107", "Low": "#dc3545", "No Sources": "#6c757d"}
    color = color_map.get(summary.quality_label, "#6c757d")

    bar_segments = ""
    total = summary.total_sources or 1
    for tier, count, tier_color in [
        ("A", summary.tier_a_count, "#28a745"),
        ("B", summary.tier_b_count, "#17a2b8"),
        ("C", summary.tier_c_count, "#ffc107"),
        ("D", summary.tier_d_count, "#dc3545"),
    ]:
        pct = count / total * 100
        if pct > 0:
            bar_segments += f'<div style="width:{pct}%;background:{tier_color};height:6px;display:inline-block;" title="Tier {tier}: {count}"></div>'

    warnings_html = ""
    if summary.warnings:
        warnings_html = "<br>".join(f"<span style='font-size:11px;color:#856404;'>⚠ {w}</span>" for w in summary.warnings)

    return f"""
    <div style="padding:10px 14px;border-radius:8px;background:{color}10;border:1px solid {color}30;margin:6px 0;">
        <div style="display:flex;align-items:center;gap:8px;margin-bottom:4px;">
            <span style="font-size:13px;font-weight:600;color:{color};">Source Quality: {summary.quality_label}</span>
            <span style="font-size:11px;color:#666;">{summary.total_sources} sources · avg {int(summary.avg_credibility*100)}%</span>
        </div>
        <div style="display:flex;height:6px;border-radius:3px;overflow:hidden;background:#eee;margin:4px 0;">
            {bar_segments}
        </div>
        <div style="font-size:10px;color:#888;margin-top:2px;">
            🟢 Tier A: {summary.tier_a_count} · 🔵 Tier B: {summary.tier_b_count} · 🟡 Tier C: {summary.tier_c_count} · 🔴 Tier D: {summary.tier_d_count}
        </div>
        {f'<div style="margin-top:4px;">{warnings_html}</div>' if warnings_html else ''}
    </div>
    """
