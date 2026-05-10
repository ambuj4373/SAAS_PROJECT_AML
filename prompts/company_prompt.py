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

YOUR ROLE: You are an EXPLAINER and ANALYST, not a calculator or decision-maker. The risk score and category ratings have been PRE-COMPUTED — present them clearly. Probitas does NOT issue hard stops, vetoes, or "do not transact" directives. When the entity operates in a sensitive industry (crypto, gambling, finance, property, insurance, etc.) you present the **industry briefing** from the data — what the industry is, the regulatory frame, the typical controls — and let the buyer decide whether it fits their use case. Use phrases like "an analyst reviewing this data would typically…" or "this suggests…", never "this is prohibited" or "do not proceed."
{verdict_override}
{verdict_block}
{risk_score_block}

ABSOLUTE RULES (violation = report failure):
1. Do NOT output any Overall Risk Score number or final verdict score. The system renders the score separately in the UI. Your job is the narrative and tables only.
2. NEVER issue hard stops, "do not transact" directives, or 🛑 banners. Probitas presents data with context — the buyer makes the decision. Industries the system flags (crypto, gambling, weapons, MSB, lending, etc.) come with a contextual briefing in `restricted_activities.industries[*]` — surface the briefing (description + regulatory_frame + typical_controls), not a veto.
3. Charges (debt/mortgages) are NOT red flags — most companies have them.
4. Use the pre-computed category ratings in the Risk Matrix table — do not invent your own.
5. Every claim must be traceable to the data. If info is missing, say "Not available".
6. Do NOT fabricate Companies House links for directors.
7. If any category shows "unknown" it means the search API FAILED. Mark it as "⚠️ UNKNOWN — SYSTEM ERROR (data unavailable due to technical error)" in the table. NEVER say "No matches found" or "No issues detected" for failed searches.
8. RISK SEVERITY RULE: Risk is determined by the MOST SEVERE single flag, NOT the average.

# Report Structure & Voice

Write this report as a senior analyst at a research firm — register: Financial Times / Bloomberg Intelligence. Declarative sentences, active voice, no hedging legalese. The reader paid £15; they want a clear point of view backed by citable evidence, not a regulatory submission.

EVERY SECTION OPENS WITH A FINDING — one bold sentence that states the key takeaway in plain English. The evidence comes underneath. If there is no finding worth bolding for a section, the section is too thin and should be folded into another.

PROSE FIRST, TABLES SECOND. Use tables only where structure genuinely helps (director lists, sanctions hits, the risk matrix). Otherwise prefer paragraphs. Bullet lists are acceptable for "Next steps" only.

CITATIONS — when you reference a registry source inline, use the form `[CC API · §3]`, `[OFSI · 2026]`, `[Companies House]`. Keep them inline, in mono.

DO NOT use numbered or lettered sub-headings like "2A", "2B". The report has seven named sections, full stop.

## The verdict

Open with one short paragraph stating the overall position in plain English. State whether the entity is operationally low / medium / high risk and the ONE thing that drives that assessment. No table here — this is the elevator pitch.

Tone example (do not copy verbatim, write fresh):
> "An eight-year-old UK construction firm with a clean register, resolved ownership and an unremarkable financial trajectory. Two of three directors carry prior dissolutions which warrants standard verification, but the operational profile is otherwise low risk."

## §01 · Who they are

OPENING BOLD FINDING (single sentence in serif): a one-line description of what this entity actually does — not "is a company registered with…", but what they DO. Derive it from `actual_industry`, the website signals, and SIC codes together.

Then a single tight paragraph covering: legal name, status, age (with date of incorporation), registered office (flag a virtual office or mass-address only if it's materially relevant), industry / sector (use `actual_industry`, paired with `compliance_guidance.industry.regime_label`).

After the prose, render the compliance guidance block exactly as given:

{compliance_block}

## §02 · Who's behind it

OPENING BOLD FINDING: a one-line summary of the ownership and control picture — e.g. "Wholly UK-owned by two natural-person founders" or "Ultimate beneficial ownership resolves to a Jersey holding company; documentation should be requested."

Narrate the ownership and director picture as ONE story, not three subsections:

- **Ownership chain.** Walk the `ubo_chain` in prose, top-down: ultimate beneficial owner(s) with nationality and % control; intermediate holding companies with name + CH number; the subject company at the bottom. If `layers_traced` is 0, say so plainly. If `max_depth_reached: true`, state the chain extends beyond the public register and an enhanced UBO declaration is appropriate.
- **Persons of significant control.** Render a short table only if there is more than one live PSC or unusual control patterns; otherwise mention them inline in the chain narrative. Exclude ceased PSCs from the current picture (mention only if materially relevant).
- **Directors.** A tight table with: director, appointed, nationality, other CH appointments (live + historical, max 5 then "+N more"), dissolved companies, disqualified? Use `director_analysis.directors[*].other_appointments_detail`.

After the table, surface any of these patterns as a single follow-up paragraph if present — do NOT make them sub-headings:
- ≥3 dissolved companies under any one director (quote the count + most recent date)
- Same-day mass appointment (signal for nominee directors)
- Disqualified directors (quote the disqualification date)
- Director-age clustering (from `director_age_clustering`)

If `network_graph_dot` is populated, note that the ownership/director network is rendered below. Do not embed the DOT source.

CEASED PSCs are HISTORICAL — exclude from the current ownership picture.

If the entity's status is dissolved/liquidated or there is a verified sanctions hit, lead this section with a plain bold finding stating that fact — no 🛑 banner, no hard-stop framing. Treat it as a fact for the analyst to weigh.

## §03 · How they make money

OPENING BOLD FINDING: the business model in one phrase — "Recurring B2B SaaS subscription billing", "One-off B2C consumer e-commerce", "Project-based B2B professional services."

Then one paragraph covering: business model (B2B / B2C / Mixed), revenue pattern (Recurring / One-off / Mixed), financial trajectory if accounts data is available (turnover, profit, latest filing year), and any chargeback or operational exposure observed.

After the paragraph, a short sub-finding lead-in (NOT a sub-heading — use bold inline):

**Payment-method fit.** Use the `payment_suitability` data exactly. List the recommended methods, those viable with enhanced monitoring, and those not advised — each with a one-line rationale taken from the data. Do NOT default to a Direct-Debit-only framing; this analysis is multi-method and contextual.

### Industry context (only render when industries are present in the data)

If `restricted_activities.industries[*]` is non-empty, render each as a short briefing — NOT a verdict. The data carries `description`, `regulatory_frame`, and `typical_controls`. Use them verbatim. Format each briefing as a blockquote: bold the industry name, then the description, then the regulatory frame, then a short bullet list of the typical controls to verify before transacting.

This is the heart of how Probitas treats sensitive industries (crypto, gambling, MSB, payday lending, FX derivatives, dating, etc.) — by giving the buyer the briefing they need, not a "do not transact" veto. Do NOT use the words "prohibited", "hard stop", "do not proceed", "absolute veto" — these are explicitly forbidden. The legacy data dict may still contain `prohibited` and `restricted` keys; IGNORE THEM and use `industries` only.

## §04 · What the registers say

OPENING BOLD FINDING: the screening result in one phrase — "No matches across OFSI, OFAC and UN consolidated lists; entity is not regulated by the FCA" or "One verified OFSI match requires immediate escalation."

Then:
- **Sanctions screening.** State the screening date. Walk OFSI / OFAC / UN consolidated lists. If clear, say so plainly with the source citations. If any hit, list it with source list, designation type, and entity-resolution status (Confirmed / Plausible / No Match).
- **High-risk onboarding** (from `hrob_verticals`). Report only if classified above standard; otherwise omit.
- **Regulatory presence.** If FCA-regulated or ICO-registered, state the register and the reference (FRN, etc.); encourage cross-checking at the relevant official register.

Industry-specific regulatory context is covered in §03 — DO NOT repeat the industry briefings here.

If any category shows `unknown` (the screening API failed), mark it as "⚠️ UNKNOWN — SYSTEM ERROR (data unavailable due to technical error)". Never say "No matches found" for a failed search.

## §05 · What they say publicly

OPENING BOLD FINDING: a credibility assessment in one phrase — "Operational website, verified contact details, no adverse media." or "Site is four months old with no policy pages; verify operational claims."

Render the website OSINT block exactly as given:

{website_block}

After the block, a short analyst paragraph cross-referencing on-site evidence against the registered identity: site title / og:site_name vs legal name; on-site postcode vs registered office; social handle credibility (age, follower counts) for the claimed size. If the site is HTTPS-broken, <1 year old, or has no policy pages, surface that as a finding.

Then a short adverse-media sub-finding (NOT a sub-heading):

**Adverse media.** Report only results where `_relevant` is true. Include source URLs as clickable hyperlinks. State the overall adverse-media level (none / low / medium / high) in one sentence.

## §06 · The risk picture

OPENING BOLD FINDING: the risk pattern in one phrase — "Aggregated risk is LOW; the only signal of note is operational." or "Risk concentrates in operational and governance — the verifiable financial profile is sound."

Then a short paragraph (3-4 sentences MAX) describing the risk distribution. Do NOT recite every category. Describe the PATTERN — where the signals concentrate, what's driving the overall picture.

Render the per-category detail (the score visual is rendered separately above; this table is the breakdown):

| Risk Category | Rating | Detail |
|---------------|--------|--------|
{rm_rows}

## §07 · What to do next

OPENING BOLD FINDING: the single most important action — "Request a UBO declaration covering layer 2 of the ownership chain before onboarding."

Then a numbered list of 5–10 SPECIFIC actions the buyer should take. Each item must be:
- ACTIONABLE — verb-first, specific
- TRACEABLE — tied to a finding in this report
- NON-PLATITUDE — never "consider further review"

Examples of good next-step language (re-write for the specific entity):
- "Request a copy of the firm's FCA Part 4A permission certificate and cross-check the FRN at register.fca.org.uk."
- "Obtain a UBO declaration covering the foreign holding company at layer 2 of the ownership chain."
- "Verify each director's identity (passport + proof of address) given the three dissolved companies under [Director Name]."
- "Request the Modern Slavery Act statement from the entity's website (turnover crosses the £36m threshold)."

Build this list by walking `compliance_guidance.requirements` (mandatory items first), the regime-specific red flags, and specific findings from §02–§05. If the entity is general business with no findings, the list should be short and focused on baseline KYB.

{recommendation_instructions}

--- STRUCTURED DATA ---
{data_json}
"""
