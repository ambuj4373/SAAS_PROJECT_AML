"""
core/fatf_screener.py — FATF predicate-offence adverse media screening.

Three-stage pipeline:
  1. **Query Builder** — generates Boolean search strings from FATF predicate
     offence categories for a given entity name.
  2. **Hunter** (Tavily) — executes advanced-depth searches and captures the
     top-5 result snippets + URLs.
  3. **Analyst** (LLM) — performs entity-resolution and materiality analysis,
     returning structured JSON with risk_level, is_match, and a summary.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Any

from config import tavily, openai_client, gemini_client, _calc_cost

# ═══════════════════════════════════════════════════════════════════════════════
# FATF PREDICATE OFFENCE CATEGORIES
# Based on FATF 40 Recommendations, Glossary "Designated categories of offences"
# ═══════════════════════════════════════════════════════════════════════════════
FATF_CATEGORIES: dict[str, list[str]] = {
    "Fraud": [
        "fraud", "fraudulent", "deception", "swindle",
        "embezzlement", "misappropriation",
    ],
    "Corruption & Bribery": [
        "corruption", "corrupt", "bribery", "bribe", "kickback",
        "graft", "abuse of office",
    ],
    "Money Laundering": [
        "money laundering", "laundering proceeds", "illicit funds",
        "proceeds of crime", "hawala", "structuring",
    ],
    "Terrorism Financing": [
        "terrorism financing", "terrorist financing",
        "financing of terrorism", "terror finance",
        "designated entity", "proscribed organisation",
    ],
    "Tax Evasion": [
        "tax evasion", "tax fraud", "offshore evasion",
    ],
    "Sanctions Violations": [
        "sanctions violation", "sanctions breach", "OFAC",
        "UN sanctions", "EU sanctions", "HMT sanctions",
        "designated person", "asset freeze",
    ],
    "Organised Crime": [
        "organised crime", "organized crime", "criminal network",
        "racketeering", "trafficking", "smuggling",
    ],
    "Proliferation Financing": [
        "proliferation financing", "WMD financing",
        "dual-use goods", "export control violation",
    ],
}

# Flattened set for quick keyword matching
_ALL_FATF_KEYWORDS: set[str] = set()
for _terms in FATF_CATEGORIES.values():
    _ALL_FATF_KEYWORDS.update(t.lower() for t in _terms)

# Additional contextual keywords that strengthen a match
_LEGAL_ACTION_KEYWORDS: set[str] = {
    "charged", "convicted", "prosecuted", "sentenced", "arrested",
    "indicted", "investigation", "inquiry", "tribunal", "penalty",
    "fine", "banned", "disqualified", "struck off", "seized",
    "frozen", "forfeiture", "confiscation",
}


# ═══════════════════════════════════════════════════════════════════════════════
# STEP 1 — QUERY BUILDER
# ═══════════════════════════════════════════════════════════════════════════════

def build_fatf_query(entity_name: str, *, categories: list[str] | None = None) -> str:
    """Build a Boolean search query: ``"entity_name" AND (term1 OR term2 …)``.

    Parameters
    ----------
    entity_name : str
        Charity name or trustee name.
    categories : list[str] | None
        Subset of FATF category names to search (default: all).

    Returns
    -------
    str
        A fully-formed Boolean search string suitable for Tavily.
    """
    if categories:
        selected = {c: v for c, v in FATF_CATEGORIES.items() if c in categories}
    else:
        selected = FATF_CATEGORIES

    # Pick the most distinctive term from each category to keep the query
    # concise (Tavily handles ~300-char queries well).
    terms: list[str] = []
    for cat_terms in selected.values():
        for t in cat_terms:
            quoted = f'"{t}"' if " " in t else t
            if quoted not in terms:
                terms.append(quoted)

    or_clause = " OR ".join(terms)
    return f'"{entity_name}" AND ({or_clause})'


def build_osint_dork_query(entity_name: str, entity_type: str = "charity") -> str:
    """Build a Google-dorking-style query targeting official UK records.

    Prioritises high-confidence sources: gov.uk, Charity Commission,
    Companies House, legal gazettes, courts, and regulatory bodies.
    """
    site_filter = (
        'site:gov.uk OR site:org.uk OR site:judiciary.uk '
        'OR site:charitycommission.gov.uk '
        'OR site:companieshouse.gov.uk '
        'OR site:sfo.gov.uk '
        'OR site:nationalcrimeagency.gov.uk '
        'OR site:thegazette.co.uk '
        'OR site:ofsi.blog.gov.uk'
    )
    offence_terms = (
        'inquiry OR investigation OR misconduct OR "financial crime" '
        'OR "regulatory action" OR "disqualified" OR "struck off" '
        'OR "sanctions" OR prosecution OR tribunal'
    )
    if entity_type == "trustee":
        return f'({site_filter}) "{entity_name}" AND ({offence_terms}) AND (trustee OR director OR charity)'
    return f'({site_filter}) "{entity_name}" AND ({offence_terms})'


# ═══════════════════════════════════════════════════════════════════════════════
# STEP 2 — HUNTER  (Tavily advanced-depth search)
# ═══════════════════════════════════════════════════════════════════════════════

def fatf_hunt(entity_name: str, entity_type: str = "charity",
              *, max_results: int = 5) -> list[dict[str, Any]]:
    """Run a FATF predicate-offence search via Tavily.

    Executes TWO search strategies:
      1. **FATF Boolean query** — broad adverse terms from FATF categories.
      2. **OSINT Dork query** — targets official UK gov/regulatory sites for
         inquiries, investigations, prosecutions, and sanctions.

    Results are de-duplicated by URL, tagged with ``searched_at`` (ISO-8601)
    and ``query_strategy`` metadata for audit purposes.

    Parameters
    ----------
    entity_name : str
        The charity or trustee name.
    entity_type : str
        ``"charity"`` or ``"trustee"`` — used to refine the query context.
    max_results : int
        Maximum Tavily results to return **per query** (default 5).

    Returns
    -------
    list[dict]
        Each dict has ``title``, ``url``, ``content`` (snippet), ``score``,
        ``searched_at`` (ISO timestamp), and ``query_strategy``.
    """
    if tavily is None:
        return []

    _now = datetime.now(timezone.utc).isoformat()

    # ── Strategy 1: FATF Boolean query ──────────────────────────────────
    fatf_query = build_fatf_query(entity_name)
    if entity_type == "trustee":
        fatf_query += ' AND (trustee OR director OR charity)'
    else:
        fatf_query += ' AND (charity OR "charity commission" OR UK)'

    all_results: list[dict[str, Any]] = []
    seen_urls: set[str] = set()

    for strategy, query in [("fatf_boolean", fatf_query),
                             ("osint_dork", build_osint_dork_query(entity_name, entity_type))]:
        try:
            raw = tavily.search(
                query=query,
                search_depth="advanced",
                max_results=max_results,
            )
            for r in raw.get("results", []):
                url = r.get("url", "")
                if url in seen_urls:
                    continue
                seen_urls.add(url)
                all_results.append({
                    "title": (r.get("title") or "")[:200],
                    "url": url,
                    "content": (r.get("content") or "")[:1500],
                    "score": r.get("score", 0),
                    "searched_at": _now,
                    "query_strategy": strategy,
                })
        except Exception as exc:
            all_results.append({
                "title": f"Search unavailable ({strategy})",
                "url": "",
                "content": str(exc),
                "_error": True,
                "searched_at": _now,
                "query_strategy": strategy,
            })

    return all_results


# ═══════════════════════════════════════════════════════════════════════════════
# STEP 3 — ANALYST  (LLM entity-resolution + materiality analysis)
# ═══════════════════════════════════════════════════════════════════════════════

_ANALYST_SYSTEM = (
    "You are a senior AML/CFT compliance officer conducting adverse media "
    "screening against FATF predicate offences for UK charities and their "
    "trustees. You must be precise and evidence-based. You follow OSINT "
    "audit best practices — every finding must cite the source URL."
)

_ANALYST_PROMPT_TEMPLATE = """\
## Task
You are screening **{entity_name}** ({entity_type}) for FATF predicate offences.

## Known Entity Context (from PDF filings & Charity Commission data)
Use the following verified context to perform **Entity Resolution** — only \
confirm a match if the news/record refers to THIS specific entity, not a \
different person or organisation with a similar name.
{entity_context}

Perform TWO analyses on the search results below:

### 1. Entity Resolution
Determine whether each search result genuinely refers to **this specific** \
{entity_type} (not a different person/organisation with a similar name). \
Cross-reference against the Known Entity Context above:
- Compare names, locations, jurisdictions, and roles.
- A result mentioning "{entity_name}" in a country where this charity does \
NOT operate is likely a false positive.
- A result mentioning the same charity number, address, or trustee names \
from the context above is a strong match.
- Common names (e.g. "Islamic Relief") require extra care — verify the \
specific branch/entity matches.

### 2. Materiality Analysis
For results that DO match the entity, assess whether the content describes an \
**actual** FATF predicate offence (fraud, corruption, money laundering, \
terrorism financing, sanctions violation, etc.) vs. a benign mention (e.g. \
the charity *combats* fraud, or a passing reference with no substance).

## Search Results
{results_json}

## Required Output
Return ONLY valid JSON (no markdown fences, no commentary) with this schema:
{{
  "entity_name": "{entity_name}",
  "entity_type": "{entity_type}",
  "risk_level": "High" | "Medium" | "Low",
  "is_match": true | false,
  "match_count": <int>,
  "total_results": <int>,
  "summary": "<2-3 sentence analyst summary>",
  "fatf_categories_detected": ["category1", ...],
  "results": [
    {{
      "title": "...",
      "url": "...",
      "source_domain": "...",
      "is_entity_match": true | false,
      "entity_resolution_reasoning": "<1 sentence explaining WHY this is/isn't \
a match based on context comparison>",
      "is_material": true | false,
      "fatf_category": "..." | null,
      "reasoning": "<1 sentence>"
    }}
  ]
}}

### Risk-Level Guidance
- **High**: ≥1 material, entity-matched result describing an actual FATF \
predicate offence (conviction, prosecution, formal investigation, sanctions \
designation). Sources from gov.uk, SFO, NCA, or court records carry highest \
weight.
- **Medium**: Entity-matched results mentioning allegations, inquiries, \
regulatory warnings, or associations with high-risk actors — but no \
confirmed offence.
- **Low**: No entity-matched material results, or results are benign \
mentions / false positives.
"""


def fatf_analyse(
    entity_name: str,
    entity_type: str,
    search_results: list[dict[str, Any]],
    *,
    entity_context: dict[str, Any] | None = None,
    llm_provider: str | None = None,
    llm_model: str | None = None,
) -> dict[str, Any]:
    """Send search results to the LLM for entity-resolution & materiality analysis.

    Parameters
    ----------
    entity_name : str
    entity_type : str  (``"charity"`` or ``"trustee"``)
    search_results : list[dict]
        Output from :func:`fatf_hunt`.
    entity_context : dict | None
        Known context from PDF/CC data (charity number, address, countries,
        trustee names) for cross-referencing in entity resolution.
    llm_provider : str | None
        ``"openai"`` or ``"gemini"``. Auto-detected if ``None``.
    llm_model : str | None
        Model name override. Auto-detected if ``None``.

    Returns
    -------
    dict
        Structured analysis with ``risk_level``, ``is_match``, ``summary``, etc.
        On failure, returns a fallback dict with ``risk_level="Low"`` and an
        error message.
    """
    _now = datetime.now(timezone.utc).isoformat()

    # ── Fallback when no results or all errors ──────────────────────────
    _has_errors = any(r.get("_error") for r in (search_results or []))
    _all_errors = search_results and all(r.get("_error") for r in search_results)

    if not search_results or _all_errors:
        # Distinguish "search failed" from "search succeeded with 0 hits"
        if _has_errors or _all_errors:
            return {
                "entity_name": entity_name,
                "entity_type": entity_type,
                "risk_level": "Unknown",
                "is_match": False,
                "match_count": 0,
                "total_results": 0,
                "summary": (
                    "FATF screening data unavailable due to technical error "
                    "(search API failed). This does NOT mean the entity is clean "
                    "— it means the check could not be completed."
                ),
                "fatf_categories_detected": [],
                "results": [],
                "cost_info": None,
                "screened_at": _now,
                "_search_failed": True,
            }
        return {
            "entity_name": entity_name,
            "entity_type": entity_type,
            "risk_level": "Low",
            "is_match": False,
            "match_count": 0,
            "total_results": 0,
            "summary": "No search results available for FATF screening.",
            "fatf_categories_detected": [],
            "results": [],
            "cost_info": None,
            "screened_at": _now,
        }

    # ── Build prompt ────────────────────────────────────────────────────
    # Trim results to essential fields for the LLM
    slim = []
    for r in search_results:
        if r.get("_error"):
            continue
        slim.append({
            "title": r.get("title", ""),
            "url": r.get("url", ""),
            "snippet": r.get("content", "")[:1200],
            "source_strategy": r.get("query_strategy", "unknown"),
        })

    # Build entity-context block for entity resolution
    if entity_context:
        ctx_lines = []
        if entity_context.get("charity_number"):
            ctx_lines.append(f"- Charity Number: {entity_context['charity_number']}")
        if entity_context.get("charity_name"):
            ctx_lines.append(f"- Registered Name: {entity_context['charity_name']}")
        if entity_context.get("address"):
            ctx_lines.append(f"- Address: {entity_context['address']}")
        if entity_context.get("countries"):
            ctx_lines.append(f"- Operating Countries: {', '.join(entity_context['countries'])}")
        if entity_context.get("trustees"):
            ctx_lines.append(f"- Known Trustees: {', '.join(entity_context['trustees'][:10])}")
        if entity_context.get("registration_date"):
            ctx_lines.append(f"- Registration Date: {entity_context['registration_date']}")
        if entity_context.get("linked_company"):
            ctx_lines.append(f"- Linked Company Number: {entity_context['linked_company']}")
        ctx_block = "\n".join(ctx_lines) if ctx_lines else "No additional context available."
    else:
        ctx_block = "No additional context available."

    prompt = _ANALYST_PROMPT_TEMPLATE.format(
        entity_name=entity_name,
        entity_type=entity_type,
        results_json=json.dumps(slim, indent=2),
        entity_context=ctx_block,
    )

    # ── Select LLM ─────────────────────────────────────────────────────
    if llm_provider is None:
        if openai_client:
            llm_provider = "openai"
            llm_model = llm_model or "gpt-4.1-mini"
        elif gemini_client:
            llm_provider = "gemini"
            llm_model = llm_model or "gemini-2.0-flash"
        else:
            return _fallback_keyword_analysis(entity_name, entity_type, search_results)

    # ── Call LLM ────────────────────────────────────────────────────────
    try:
        if llm_provider == "openai":
            resp = openai_client.chat.completions.create(
                model=llm_model,
                messages=[
                    {"role": "system", "content": _ANALYST_SYSTEM},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.1,
                max_tokens=2000,
                response_format={"type": "json_object"},
            )
            if not resp.choices:
                return _fallback_keyword_analysis(entity_name, entity_type, search_results)
            text = resp.choices[0].message.content or ""
            usage = resp.usage
            cost_info = {
                "model": llm_model,
                "provider": "openai",
                "prompt_tokens": usage.prompt_tokens if usage else 0,
                "completion_tokens": usage.completion_tokens if usage else 0,
                "total_tokens": (usage.prompt_tokens + usage.completion_tokens) if usage else 0,
                "cost_usd": _calc_cost(
                    llm_model,
                    usage.prompt_tokens if usage else 0,
                    usage.completion_tokens if usage else 0,
                ),
            }
        else:
            resp = gemini_client.models.generate_content(
                model=llm_model,
                contents=f"{_ANALYST_SYSTEM}\n\n{prompt}",
            )
            text = resp.text or ""
            usage = getattr(resp, "usage_metadata", None)
            p_tok = getattr(usage, "prompt_token_count", 0) or 0
            c_tok = getattr(usage, "candidates_token_count", 0) or 0
            cost_info = {
                "model": llm_model,
                "provider": "gemini",
                "prompt_tokens": p_tok,
                "completion_tokens": c_tok,
                "total_tokens": p_tok + c_tok,
                "cost_usd": _calc_cost(llm_model, p_tok, c_tok),
            }
    except Exception as exc:
        # LLM unavailable — fall back to keyword heuristic
        result = _fallback_keyword_analysis(entity_name, entity_type, search_results)
        result["_analyst_error"] = str(exc)
        return result

    # ── Parse JSON response ─────────────────────────────────────────────
    try:
        # Strip markdown fences if present
        cleaned = re.sub(r"^```(?:json)?\s*", "", text.strip())
        cleaned = re.sub(r"```\s*$", "", cleaned.strip())
        parsed = json.loads(cleaned)
    except (json.JSONDecodeError, TypeError):
        parsed = {
            "entity_name": entity_name,
            "entity_type": entity_type,
            "risk_level": "Low",
            "is_match": False,
            "match_count": 0,
            "total_results": len(slim),
            "summary": f"LLM returned unparseable response. Raw: {text[:300]}",
            "fatf_categories_detected": [],
            "results": [],
        }

    # Ensure required fields exist
    parsed.setdefault("entity_name", entity_name)
    parsed.setdefault("entity_type", entity_type)
    parsed.setdefault("risk_level", "Low")
    parsed.setdefault("is_match", False)
    parsed.setdefault("match_count", 0)
    parsed.setdefault("total_results", len(slim))
    parsed.setdefault("summary", "")
    parsed.setdefault("fatf_categories_detected", [])
    parsed.setdefault("results", [])
    parsed["cost_info"] = cost_info
    parsed["screened_at"] = _now
    return parsed


# ═══════════════════════════════════════════════════════════════════════════════
# FALLBACK — keyword-only heuristic when LLM is unavailable
# ═══════════════════════════════════════════════════════════════════════════════

def _fallback_keyword_analysis(
    entity_name: str,
    entity_type: str,
    search_results: list[dict[str, Any]],
) -> dict[str, Any]:
    """Deterministic fallback when no LLM is available."""
    _now = datetime.now(timezone.utc).isoformat()
    name_parts = [p.lower() for p in entity_name.split() if len(p) > 2]
    matched_results = []
    cats_detected: set[str] = set()

    for r in search_results:
        if r.get("_error"):
            continue
        text = ((r.get("content") or "") + " " + (r.get("title") or "")).lower()

        # Entity resolution — at least one name part present
        name_hit = any(re.search(r"\b" + re.escape(p) + r"\b", text) for p in name_parts)
        if not name_hit:
            continue

        # FATF keyword matching
        fatf_hits: list[str] = []
        for cat, terms in FATF_CATEGORIES.items():
            for t in terms:
                if t.lower() in text:
                    fatf_hits.append(cat)
                    cats_detected.add(cat)
                    break

        # Legal-action keyword boost
        legal_hit = any(kw in text for kw in _LEGAL_ACTION_KEYWORDS)

        if fatf_hits:
            matched_results.append({
                "title": r.get("title", ""),
                "url": r.get("url", ""),
                "is_entity_match": True,
                "is_material": legal_hit,
                "fatf_category": fatf_hits[0],
                "reasoning": f"Keyword match: {', '.join(fatf_hits)}. "
                             f"{'Legal-action language detected.' if legal_hit else 'No legal-action language.'}",
            })

    material_count = sum(1 for r in matched_results if r.get("is_material"))
    match_count = len(matched_results)

    if material_count > 0:
        risk = "High"
    elif match_count > 0:
        risk = "Medium"
    else:
        risk = "Low"

    return {
        "entity_name": entity_name,
        "entity_type": entity_type,
        "risk_level": risk,
        "is_match": match_count > 0,
        "match_count": match_count,
        "total_results": len([r for r in search_results if not r.get("_error")]),
        "summary": (
            f"Keyword-only analysis (LLM unavailable). "
            f"{match_count} result(s) matched entity + FATF terms, "
            f"{material_count} with legal-action language."
        ),
        "fatf_categories_detected": sorted(cats_detected),
        "results": matched_results,
        "cost_info": None,
        "screened_at": _now,
        "_fallback": True,
    }


# ═══════════════════════════════════════════════════════════════════════════════
# CONVENIENCE — run full pipeline for one entity
# ═══════════════════════════════════════════════════════════════════════════════

def screen_entity(
    entity_name: str,
    entity_type: str = "charity",
    *,
    max_results: int = 5,
    entity_context: dict[str, Any] | None = None,
    llm_provider: str | None = None,
    llm_model: str | None = None,
) -> dict[str, Any]:
    """End-to-end FATF screen: hunt → analyse → structured result.

    Returns a dict with keys: ``risk_level``, ``is_match``, ``summary``,
    ``fatf_categories_detected``, ``results``, ``search_results_raw``,
    ``cost_info``, ``screened_at``.
    """
    raw_results = fatf_hunt(entity_name, entity_type, max_results=max_results)
    analysis = fatf_analyse(
        entity_name, entity_type, raw_results,
        entity_context=entity_context,
        llm_provider=llm_provider, llm_model=llm_model,
    )
    analysis["search_results_raw"] = raw_results
    return analysis
