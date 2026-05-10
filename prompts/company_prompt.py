"""
prompts/company_prompt.py — Company Sense-Check LLM prompt template.

The prompt is parameterised — call ``build_company_prompt(...)`` with
the analysis bundle and pre-computed verdicts to get the final string.
"""

from __future__ import annotations

import json
from typing import Any


_SEVERITY_ICON = {
    "mandatory": "🛑",
    "recommended": "🟡",
    "informational": "ℹ️",
}


def _render_website_intel_block(intel: dict) -> str:
    """Pre-render Section 4 (Digital Footprint) from the website OSINT scrape.

    Same idea as Section 1B: hand the LLM ready markdown so it features
    the data instead of trying to extract it from a JSON dump.
    """
    if not intel or not intel.get("ok"):
        err = (intel or {}).get("error", "no website available or fetch failed")
        return (
            "## 4. Digital Footprint & Website Verification\n\n"
            f"_(No verified website data: {err}. The buyer should request the "
            f"entity's primary URL and a copy of their privacy / terms / "
            f"modern-slavery statements.)_\n"
        )

    url = intel.get("url", "")
    domain = intel.get("domain", "")
    meta = intel.get("meta") or {}
    social = intel.get("social") or {}
    rel_me = intel.get("social_rel_me") or []
    pages = intel.get("compliance_pages") or {}
    contacts = intel.get("contacts") or {}
    ssl = intel.get("ssl") or {}
    age = intel.get("domain_age") or {}
    signals = intel.get("signals") or []

    # Header line
    age_part = ""
    if age.get("age_years") is not None:
        age_part = f"  ·  **Domain age:** {age['age_years']} years"

    https_part = "✓ HTTPS" if ssl.get("https") else "✗ no HTTPS"
    if ssl.get("issuer"):
        https_part += f" (issued by {ssl['issuer']})"

    # Social links table (render only if any)
    if social or rel_me:
        social_rows = ""
        for plat, links in sorted(social.items()):
            if not links:
                continue
            social_rows += f"| {plat.title()} | [{links[0]}]({links[0]}) |\n"
        for me in rel_me[:3]:
            social_rows += f"| (rel=me) | [{me}]({me}) |\n"
        social_block = (
            "**Verified social-media accounts** (extracted from the website "
            "footer / meta tags — not guessed from text search):\n\n"
            "| Platform | Link |\n|----------|------|\n"
            f"{social_rows.rstrip()}\n"
        )
    else:
        social_block = (
            "**Social-media accounts:** none detected on the website. This is "
            "informational — many B2B firms do not maintain external social presences.\n"
        )

    # Compliance pages table
    if pages:
        comp_rows = ""
        for topic, href in sorted(pages.items()):
            label = topic.replace("_", " ").title()
            target = href if href and href != "(text mention only)" else "_(mentioned in body, no dedicated page)_"
            comp_rows += f"| {label} | {target} |\n"
        comp_block = (
            "**Compliance pages found on-site:**\n\n"
            "| Topic | Location |\n|-------|----------|\n"
            f"{comp_rows.rstrip()}\n"
        )
    else:
        comp_block = (
            "**Compliance pages:** none detected on a 5-page crawl. "
            "Notable absences: Privacy Policy, Cookie Policy, Modern Slavery "
            "Statement (if turnover ≥ £36m). These are required under UK law "
            "in many sectors.\n"
        )

    # Contacts
    contact_lines = []
    if contacts.get("emails"):
        contact_lines.append(f"  - Emails: {', '.join(contacts['emails'][:3])}")
    if contacts.get("phones"):
        contact_lines.append(f"  - Phones: {', '.join(contacts['phones'][:2])}")
    if contacts.get("postcodes"):
        contact_lines.append(f"  - On-site postcodes: {', '.join(contacts['postcodes'][:3])}")
    contact_block = "\n".join(contact_lines) if contact_lines else "  - None visible."

    # Signals as bullets
    sig_lines = "\n".join(f"- {s}" for s in signals[:6])

    return f"""## 4. Digital Footprint & Website Verification

> **Verified URL:** [{url}]({url})  ·  **Domain:** `{domain}`  ·  **TLS:** {https_part}{age_part}

The data below was extracted directly from a live fetch of the entity's
website (not from a third-party search index). If the entity disputes any
of it, that is itself a useful signal.

**Site identity (from meta tags):**
- Title: {meta.get('title') or '_(no title set)_'}
- og:title: {meta.get('og:title') or '_(not set)_'}
- og:description: {meta.get('og:description') or '_(not set)_'}
- og:site_name: {meta.get('og:site_name') or '_(not set)_'}

{social_block}

{comp_block}

**Contact information found on-site:**
{contact_block}

**Site-level signals:**
{sig_lines if sig_lines else '_(none)_'}

In the analyst commentary that follows, cross-reference the on-site
contact details against the registered office address from §1, and flag
any social account whose handle does not match the registered name.
"""


def _render_compliance_block(guidance: dict) -> str:
    """Pre-render Section 1B as markdown.

    The LLM is unreliable at pulling structured tables out of a deep JSON
    dump, so we hand it ready-to-emit markdown and tell it to feature it
    verbatim. This is the killer-feature section: industry classification
    + regulator-specific document checklist + automated cross-checks +
    regime red flags.
    """
    if not guidance:
        return (
            "## 1B. Compliance Document Checklist & Industry Classification\n"
            "_(Compliance guidance not available — render baseline KYB requirements only.)_"
        )

    industry = guidance.get("industry") or {}
    regime_label = industry.get("regime_label", "General business")
    matched_sic = industry.get("matched_sic", []) or []
    confidence = industry.get("confidence", "medium")
    summary_line = guidance.get("summary_line", "")

    requirements = guidance.get("requirements") or []
    cross_checks = guidance.get("cross_checks") or []
    red_flags = guidance.get("red_flags_to_test") or []

    # Documents table
    doc_rows = ""
    for i, r in enumerate(requirements, 1):
        icon = _SEVERITY_ICON.get(r.get("severity", "recommended"), "🟡")
        doc_rows += (
            f"| {i} | **{r.get('title', '')}** | {r.get('detail', '')} | "
            f"{icon} {r.get('severity', '')} | "
            f"{r.get('verification_method', '')} |\n"
        )

    # Cross-checks table
    cc_rows = ""
    for r in cross_checks:
        cc_rows += (
            f"| {r.get('title', '')} | {r.get('source_authority', '')} | "
            f"{r.get('verification_method', '')} |\n"
        )

    # Red-flags bullets — one per regime-specific risk to test
    rf_lines = ""
    if red_flags:
        rf_lines = "\n".join(f"- **Test:** {q}" for q in red_flags)
    else:
        rf_lines = "_(No regime-specific red flags apply to this industry.)_"

    block = f"""## 1B. Compliance Document Checklist & Industry Classification

> **Regulated regime:** {regime_label}  ·  **SIC matched:** {", ".join(matched_sic) or "—"}  ·  **Confidence:** {confidence}

{summary_line}

### Documents to request from the entity (the buyer's checklist)

| # | Document | Why it's needed | Severity | How to verify |
|---|----------|-----------------|----------|---------------|
{doc_rows.rstrip()}

### Cross-checks Probitas runs automatically

| Check | Authority | Method |
|-------|-----------|--------|
{cc_rows.rstrip()}

### Regime-specific red flags to test

{rf_lines}
"""

    return block


def build_company_prompt(
    *,
    company_name: str,
    company_number: str,
    co_check_data: dict[str, Any],
    verdict_override: str,
    verdict_block: str,
    risk_matrix: dict[str, Any],
    recommendation_instructions: str,
    data_json: str,
    risk_score_summary: str = "",
) -> str:
    """Build the full company sense-check LLM prompt.

    Parameters
    ----------
    company_name            : Legal name of the company.
    company_number          : Companies House number.
    co_check_data           : Full analysis bundle from ``run_company_check()``.
    verdict_override        : Pre-computed verdict override text (hard stops, etc.).
    verdict_block           : Pre-computed category ratings block.
    risk_matrix             : The risk_matrix dict from the analysis bundle.
    recommendation_instructions : Advisory tone instructions.
    data_json               : JSON-serialised analysis data.
    risk_score_summary      : V3 numerical risk score summary (optional).

    Returns
    -------
    str — the complete prompt ready for ``llm_generate()``.
    """
    # Build risk matrix table rows
    rm_rows = ""
    for cat, rating in (risk_matrix.get("category_risks") or {}).items():
        if rating == "high":
            icon = "🔴"
        elif rating in ("medium", "low-medium"):
            icon = "🟡"
        elif rating == "unknown":
            icon = "⚠️"
        else:
            icon = "🟢"
        rm_rows += f"| {cat} | {icon} {rating} | From data |\n"

    # ── Pre-render compliance guidance as markdown ─────────────────────────
    # The LLM has trouble extracting structured tables from a deep JSON dump
    # so we hand it the markdown directly. It just adapts the wording.
    compliance_block = _render_compliance_block(co_check_data.get("compliance_guidance") or {})

    # ── Pre-render website OSINT block (Section 4) ────────────────────────
    website_block = _render_website_intel_block(co_check_data.get("website_intel") or {})

    risk_score_block = ""
    if risk_score_summary:
        risk_score_block = f"""
PRE-COMPUTED RISK SCORE (V3 Numerical Scoring Engine):
{risk_score_summary}
The above score is computed deterministically from all analysis signals. Reference it in the report but do NOT override it.
"""

    return f"""You are a **Senior Payment Underwriter & AML Analyst** writing up a Company Sense-Check for **{company_name}** (Companies House No. {company_number}).

YOUR ROLE: You are an EXPLAINER and ANALYST, not a calculator or decision-maker. All compliance scores, risk ratings, hard stops, and flags have been PRE-COMPUTED by deterministic engines. Your job is to present them clearly and write the narrative. NEVER override, soften, recalculate, or contradict the pre-computed verdicts. You provide DATA, ANALYSIS, and ADVISORY OBSERVATIONS — not directives, orders, or instructions. Never tell the reader what they must or must not do. Frame guidance as "an analyst reviewing this data would typically…" or "this suggests…".
{verdict_override}
{verdict_block}
{risk_score_block}

ABSOLUTE RULES (violation = report failure):
1. Do NOT output any Overall Risk Score number or final verdict score. The system renders the score separately in the UI. Your job is the narrative and tables only.
2. If Hard Stop Triggered = YES, the report MUST say CRITICAL with 🛑 banners. No exceptions.
3. Charges (debt/mortgages) are NOT red flags — most companies have them.
4. Use the pre-computed category ratings in the Risk Matrix table — do not invent your own.
5. Every claim must be traceable to the data. If info is missing, say "Not available".
6. Do NOT fabricate Companies House links for directors.
7. If any category shows "unknown" it means the search API FAILED. Mark it as "⚠️ UNKNOWN — SYSTEM ERROR (data unavailable due to technical error)" in the table. NEVER say "No matches found" or "No issues detected" for failed searches.
8. CROSS-REFERENCE RULE: Adverse Media and FATF Screening are NOT independent. If Section 7 contains verified sanctions hits, Section 8 MUST reflect a High FATF risk for Sanctions Violations.
9. RISK SEVERITY RULE: Risk is determined by the MOST SEVERE single flag, NOT the average.

# Report Structure

## 1. Company Overview
| Field | Value |
|-------|-------|
| Legal Name | {company_name} |
| Company Number | {company_number} |
| Status | From data |
| Type | From data |
| Incorporated | From data |
| Company Age | From company_age |
| SIC Codes | List each with description |
| Registered Office | Full address |
| Jurisdiction | From data |

{compliance_block}

## 2. Corporate Structure & Governance — Ownership chain & control

This section is the SECOND most important. It must read like a relationship
diagram in prose.

### 2A. UBO Chain (lead with this)
Use the `ubo_chain` data. Walk the ownership LAYER BY LAYER, top down:

1. **Ultimate beneficial owner(s)** — name(s), nationality, % control. If a
   foreign / unresolvable entity sits at the top, state so explicitly and
   recommend requesting a UBO declaration.
2. **Intermediate layers** — for each holding company in the chain, name it,
   its CH number (if UK), and its share.
3. **The subject company** — at the bottom of the chain.

If `ubo_chain.layers_traced` is 0, write "No ownership chain resolved — request
a UBO declaration".

If the chain reaches `max_depth_reached: true`, state explicitly that the
chain extends beyond what the public register exposes and an enhanced UBO
declaration should be requested.

CEASED PSCs are HISTORICAL — exclude from current ownership %; mention only
if relevant ("Y was a PSC until [date]").

### 2B. Persons of Significant Control (live)
Table:

| PSC | Nature of control | Notified | Nationality | Other CH connections |
|-----|-------------------|----------|-------------|----------------------|

Pull `other_directorships_count` per PSC if present in the data.

### 2C. Company Status & Age
Report status and age. If hard_stop_triggered is YES: display 🛑 HARD STOP banner.

### 2D. Registered Office & Address Intelligence
Report address with the pre-computed verdict. If `address_intelligence`
contains `is_virtual_office: true` or `same_address_companies: > 5`, surface
this prominently — these are not flags by default but they are facts a
KYB analyst would want quoted.

### 2E. Industry Classification
Report the pre-computed `actual_industry` classification. Pair with the
`compliance_guidance.industry.regime_label` from §1B.

### 2F. Dormancy & Shelf Company Assessment
Report dormancy analysis. If a dormant→active transition occurred recently,
state the date and surface as a fact for the analyst to weigh.

### 2G. Accounts & Filings
Report accounts data. Use pre-computed filing_overdue verdict exactly as given.

### 2H. Charges — INFORMATIONAL ONLY
Summarise briefly. Do NOT treat as negative.

## 3. Director & Leadership Network

This section reads like a network description. The goal: tell the analyst
WHO they are dealing with and WHO ELSE these people are connected to.

For each appointed director, present:

| # | Director | Nationality | Age | Appointed | Other CH appointments (live + historical) | Dissolved companies | Disqualified? |
|---|----------|-------------|-----|-----------|--------------------------------------------|---------------------|---------------|

Use `director_analysis.directors[*].other_appointments_detail` for the
"Other CH appointments" column. List up to 5 with company name + role + status
(live / dissolved / liquidation). If more than 5, state "+ N more".

Below the table, surface any of these patterns explicitly if present:

- **Multiple dissolved companies** (≥3 dissolved under one director) — quote
  the count and the most recent dissolution date.
- **Director age clustering** — if `director_age_clustering` is flagged.
- **Same-day mass appointment** — if multiple directors were appointed on the
  same date (signal for nominee directors).
- **Disqualified directors** — quote the disqualification date if present.

These come from `director_analysis` and `fraud_detection`.

If `network_graph_dot` is populated, mention that a network graph is
available (the frontend will render it separately). Do not embed the DOT
source in the narrative.

{website_block}

After the rendered Section 4 above, add a short analyst paragraph that
cross-references the on-site evidence against the registered identity:
- Is the site title / og:site_name consistent with the legal name in §1?
- Is the on-site postcode the same as the registered office?
- Do the social accounts use a handle that matches the entity name (or
  carries credible follower counts that fit the entity's claimed size)?
- If the site is HTTPS-broken, very young (<1 year), or has no policies,
  surface that as a finding.

## 5. Business & Payment Profile
Describe business model: B2B/B2C/Mixed, payment pattern, chargeback risk.

## 6. Restricted Activities & High-Risk Onboarding Assessment

### 6A. Restricted Activities
Report pre-computed restricted_activities results.

### 6B. High Risk Onboarding (high-risk onboarding) Verticals
Report pre-computed hrob_verticals results.

## 7. Adverse Media & Reputation
Report ONLY results where `_relevant` is true. Include source URLs as clickable hyperlinks.
CRITICAL: Sanctions-related adverse media constitutes a FATF predicate offence — cross-reference in Section 8.

## 8. FATF Predicate Offence Screening
Report FATF screening result. Use pre-computed FATF risk level exactly.
CRITICAL CROSS-REFERENCE: If Section 7 shows sanctions hits, this section MUST show 🔴 High risk.

## 9. Overall Risk Matrix

Use the PRE-COMPUTED ratings:

| Risk Category | Rating | Detail |
|---------------|--------|--------|
{rm_rows}

## 10. Analyst Observations & Advisory Notes
{recommendation_instructions}

TONE RULE: You are an analyst presenting findings — NOT an authority issuing instructions.

## 11. Recommended Next Steps (concrete, actionable)

End the report with a numbered list of the SPECIFIC actions the buyer should
take next. This must be ACTIONABLE — no platitudes, no "consider further
review". Examples of good next-step language:

- "Request a copy of the firm's FCA Part 4A permission certificate and
  cross-check the FRN at register.fca.org.uk."
- "Obtain a UBO declaration covering the foreign holding company at layer 2
  of the ownership chain."
- "Verify each director's identity (passport + proof of address) given the
  3 dissolved companies under [Director Name]."
- "Request the Modern Slavery Act statement from the entity's website
  (turnover crosses the £36m threshold)."

Build this list by walking the `compliance_guidance.requirements` (mandatory
items first), the regime-specific red flags from §1B, and any specific
director / address findings from §2 and §3. Aim for 5–10 concrete items.

If the entity is general business with no findings, the list should be
short and focused on baseline KYB ("Standard customer due diligence under
MLR 2017; no industry-specific licences required").

--- STRUCTURED DATA ---
{data_json}
"""
