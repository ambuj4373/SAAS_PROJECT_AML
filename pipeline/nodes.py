"""
pipeline/nodes.py — Shared node functions for LangGraph pipelines.

Each node is a pure function: ``(state: dict) → dict`` that reads from
the state, performs work, and returns a partial update dict to merge back.
Nodes are composed into graphs by ``charity_graph.py`` and ``company_graph.py``.
"""

from __future__ import annotations

import json
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Callable

from core.logging_config import get_logger, PipelineMetrics, track_stage
from core.validators import compact, slim_search, safe_list

log = get_logger("pipeline.nodes")


# ─── State read helpers — guard against None coming through ────────────────
# CHARITY_STATE_DEFAULTS pre-populates many keys with None or empty
# containers. ``state.get(key, default)`` returns the *stored* None when
# the key is present, not the default. These helpers coerce None → the
# safe empty type so downstream code never NoneType-crashes when an
# upstream node failed to populate the field.

def _dict(state: dict, key: str) -> dict:
    """Return state[key] if it's a dict, else {}."""
    v = state.get(key)
    return v if isinstance(v, dict) else {}


def _list(state: dict, key: str) -> list:
    """Return state[key] if it's a list, else []."""
    v = state.get(key)
    return v if isinstance(v, list) else []


def _discover_website_url(check_result: dict) -> str:
    """Find a usable website URL from the company-check sub-results."""
    if not isinstance(check_result, dict):
        return ""

    # Try the cross_reference block (web search → likely site)
    xref = check_result.get("cross_reference") or {}
    candidates: list[str] = []

    domain_info = xref.get("domain_info") or {}
    if isinstance(domain_info, dict) and domain_info.get("domain"):
        candidates.append(str(domain_info["domain"]))

    if xref.get("primary_url"):
        candidates.append(str(xref["primary_url"]))
    if xref.get("website_url"):
        candidates.append(str(xref["website_url"]))

    # Online presence often contains a discovered URL
    op = check_result.get("online_presence") or []
    if isinstance(op, list):
        for hit in op[:3]:
            if isinstance(hit, dict) and hit.get("url"):
                candidates.append(str(hit["url"]))

    # First reasonable candidate wins
    for c in candidates:
        c = c.strip()
        if c and "." in c and "@" not in c:
            return c
    return ""


def _str(state: dict, key: str) -> str:
    """Return state[key] if it's a non-empty string, else ''."""
    v = state.get(key)
    return v if isinstance(v, str) else ""


# ═══════════════════════════════════════════════════════════════════════════════
# CHARITY PIPELINE NODES
# ═══════════════════════════════════════════════════════════════════════════════

def fetch_registry_data(state: dict) -> dict:
    """Node 1: Fetch charity & company registry data from CC and CH APIs."""
    from api_clients.charity_commission import (
        fetch_charity_data, build_cc_governance_intel, fetch_financial_history,
    )
    from api_clients.companies_house import fetch_ch_data, fetch_trustee_appointments

    charity_num = state["charity_number"]
    updates: dict[str, Any] = {"stage_timings": dict(state.get("stage_timings", {}))}
    t0 = time.time()

    try:
        charity_data = fetch_charity_data(charity_num)
        if not charity_data:
            updates["errors"] = state.get("errors", []) + [
                f"No charity data returned for {charity_num}"
            ]
            return updates

        entity_name = charity_data.get("charity_name", f"Charity {charity_num}")
        updates["charity_data"] = charity_data
        updates["entity_name"] = entity_name
        updates["website_url"] = (
            state.get("website_override")
            or charity_data.get("web", "")
        )

        # Trustees — CC API returns list[str] of names; older code assumed
        # list[dict]. Accept either shape defensively.
        raw_trustees = charity_data.get("trustees") or []
        trustees = []
        for t in raw_trustees:
            if isinstance(t, str):
                if t:
                    trustees.append(t)
            elif isinstance(t, dict):
                name = t.get("trustee_name") or t.get("name") or ""
                if name:
                    trustees.append(name)
        updates["trustees"] = trustees

        # Financial history
        fin_hist = fetch_financial_history(charity_num)
        updates["financial_history"] = fin_hist or []

        # CC governance — takes the charity_data dict, not the number
        cc_gov = build_cc_governance_intel(charity_data)
        updates["cc_governance"] = cc_gov or {}

        # Companies House cross-reference
        linked_co = (charity_data.get("company_number") or "").strip()
        if linked_co:
            try:
                ch_data = fetch_ch_data(linked_co)
                updates["ch_data"] = ch_data
            except Exception as e:
                log.warning(f"CH fetch failed for {linked_co}: {e}")
                updates["ch_data"] = None

        # Trustee appointments (if CH data available)
        ch = updates.get("ch_data") or state.get("ch_data")
        if ch:
            try:
                ta = fetch_trustee_appointments(ch)
                updates["trustee_appointments"] = ta or {}
            except Exception as e:
                log.warning(f"Trustee appointments fetch failed: {e}")

    except Exception as e:
        log.error(f"Registry data fetch failed: {e}")
        updates["errors"] = state.get("errors", []) + [str(e)]

    updates["stage_timings"]["fetch_registry"] = round(time.time() - t0, 2)
    return updates


def extract_documents(state: dict) -> dict:
    """Node 2: Extract text from uploaded PDFs and CC printouts."""
    from core.pdf_parser import (
        parse_cc_printout, extract_pdf_text, extract_pdf_with_vision,
        extract_partners_from_text, compute_extraction_confidence,
    )

    updates: dict[str, Any] = {"stage_timings": dict(state.get("stage_timings", {}))}
    t0 = time.time()

    extraction_metadata = []
    uploaded_texts = []
    gov_doc_texts = []

    # CC printout
    cc_printout = state.get("cc_printout")
    if cc_printout:
        try:
            cc_data = parse_cc_printout(cc_printout.read())
            updates["cc_pdf_result"] = cc_data
            updates["cc_pdf_text"] = json.dumps(cc_data, default=str)
        except Exception as e:
            log.warning(f"CC printout parse failed: {e}")
            updates["warnings"] = state.get("warnings", []) + [
                f"CC printout parse failed: {e}"
            ]

    # Uploaded documents
    for doc in state.get("uploaded_docs", []):
        try:
            text, meta = extract_pdf_text(doc.read())
            extraction_metadata.append(meta)
            if text:
                uploaded_texts.append(text)
        except Exception as e:
            log.warning(f"Document extraction failed: {e}")

    # Governance documents
    for doc in state.get("uploaded_gov_docs", []):
        try:
            text, meta = extract_pdf_text(doc.read())
            extraction_metadata.append(meta)
            if text:
                gov_doc_texts.append(text)
        except Exception as e:
            log.warning(f"Governance doc extraction failed: {e}")

    # Partner extraction from documents
    all_text = " ".join(uploaded_texts + gov_doc_texts)
    entity_name = state.get("entity_name", "")
    if all_text:
        try:
            partners = extract_partners_from_text(all_text, entity_name)
            updates["partners_discovered"] = partners
        except Exception:
            pass

    updates["uploaded_texts"] = uploaded_texts
    updates["gov_doc_texts"] = gov_doc_texts
    updates["extraction_metadata"] = extraction_metadata
    updates["stage_timings"]["extract_documents"] = round(time.time() - t0, 2)
    return updates


def run_web_intelligence(state: dict) -> dict:
    """Node 3: Parallel web searches (OSINT, adverse media, policies, FATF)."""
    from api_clients.tavily_search import (
        search_adverse_media_hybrid, search_positive_media,
        search_online_presence, search_policies,
        search_partnerships,
    )
    from core.fatf_screener import screen_entity

    updates: dict[str, Any] = {"stage_timings": dict(state.get("stage_timings", {}))}
    t0 = time.time()

    entity_name = state.get("entity_name", "")
    website_url = state.get("website_url", "")
    trustees = state.get("trustees", [])
    search_failures = []

    # ─── FCA AWARENESS ────────────────────────────────────────────────
    fca_details = state.get("fca_details", {})
    is_fca_regulated = fca_details.get("industry_regulated", False)

    # ─── Deep website OSINT (charity pipeline) ────────────────────────
    # Same as the company side: scrape og: tags, social accounts, key
    # compliance pages, contact info, SSL. Adds real value vs only
    # relying on Tavily/Serper text search.
    if website_url:
        try:
            from core.website_intel import fetch_website_intelligence
            log.info("Scraping charity website OSINT: %s", website_url)
            updates["website_intel"] = fetch_website_intelligence(
                website_url, max_pages=5
            )
        except Exception as we:
            log.warning("Charity website intel failed: %s", we)
            updates["website_intel"] = {"ok": False, "error": str(we)}
    else:
        updates["website_intel"] = {"ok": False, "error": "no website url available"}

    futures = {}
    with ThreadPoolExecutor(max_workers=10) as pool:
        # Adverse media - organisation (FCA-aware)
        futures["adverse_org"] = pool.submit(
            search_adverse_media_hybrid, entity_name, None, is_fca_regulated)

        # Adverse media - trustees (limit to first 10) (FCA-aware)
        for t_name in trustees[:10]:
            futures[f"adverse_trustee_{t_name}"] = pool.submit(
                search_adverse_media_hybrid, t_name, [entity_name], is_fca_regulated)

        # Positive media
        futures["positive_media"] = pool.submit(
            search_positive_media, entity_name, "")

        # Online presence
        if website_url:
            futures["online_presence"] = pool.submit(
                search_online_presence, entity_name, website_url)

        # Policy search
        if website_url:
            futures["policies"] = pool.submit(
                search_policies, entity_name, website_url)

        # Partnerships
        futures["partnerships"] = pool.submit(
            search_partnerships, entity_name, website_url)

        # FATF screening - organisation
        futures["fatf_org"] = pool.submit(screen_entity, entity_name)

        # FATF screening - trustees (first 5)
        for t_name in trustees[:5]:
            futures[f"fatf_trustee_{t_name}"] = pool.submit(
                screen_entity, t_name)

    # Collect results
    adverse_trustees = {}
    fatf_trustee_screens = {}

    for key, future in futures.items():
        try:
            result = future.result(timeout=120)
        except Exception as e:
            search_failures.append(f"{key}: {e}")
            log.warning(f"Search failed: {key} — {e}")
            result = None

        if key == "adverse_org":
            updates["adverse_org"] = result or []
        elif key == "positive_media":
            updates["positive_media"] = result or []
        elif key == "online_presence":
            updates["online_presence"] = result or []
        elif key == "policies":
            if result and isinstance(result, tuple):
                (pol_results, pol_audit, pol_docs,
                 pol_class, social, hrcob) = result
                updates["policy_results"] = pol_results or []
                updates["policy_audit"] = pol_audit or []
                updates["policy_doc_links"] = pol_docs or []
                updates["policy_classification"] = pol_class or []
                updates["social_links"] = social or {}
                updates["hrcob_core_controls"] = hrcob or {}
        elif key == "partnerships":
            if result and isinstance(result, tuple):
                updates["partnership_results"] = result[0] or []
        elif key == "fatf_org":
            updates["fatf_org_screen"] = result
        elif key.startswith("adverse_trustee_"):
            t_name = key[len("adverse_trustee_"):]
            adverse_trustees[t_name] = result or []
        elif key.startswith("fatf_trustee_"):
            t_name = key[len("fatf_trustee_"):]
            fatf_trustee_screens[t_name] = result or {}

    # Merge manual social links
    manual = state.get("manual_social_links", {})
    if manual:
        social = dict(updates.get("social_links", {}))
        for k, v in manual.items():
            if v:
                social[k] = v
        updates["social_links"] = social

    updates["adverse_trustees"] = adverse_trustees
    updates["fatf_trustee_screens"] = fatf_trustee_screens
    updates["search_failures"] = search_failures
    updates["stage_timings"]["web_intelligence"] = round(time.time() - t0, 2)
    return updates


def run_analysis_engines(state: dict) -> dict:
    """Node 4: Governance analysis, financial anomalies, country risk."""
    from core.risk_engine import (
        assess_governance_indicators, assess_structural_governance,
        detect_financial_anomalies,
    )
    from config import get_country_risk

    updates: dict[str, Any] = {"stage_timings": dict(state.get("stage_timings", {}))}
    t0 = time.time()

    charity_data = _dict(state, "charity_data")
    cc_governance = _dict(state, "cc_governance")
    ch_data = state.get("ch_data")  # may legitimately be None when no linked company
    trustees = _list(state, "trustees")
    financial_history = _list(state, "financial_history")
    trustee_appointments = _dict(state, "trustee_appointments")

    # Early bail: if registry fetch failed, charity_data will be empty.
    # Run downstream analysis with empty inputs so the rest of the
    # pipeline gets safe defaults — but skip the heavy lifting and
    # record a warning so the report can flag the gap.
    if not charity_data:
        log.warning(
            "analysis_engines: charity_data is empty (likely registry fetch "
            "failure); producing empty analysis defaults."
        )
        updates["governance_indicators"] = {}
        updates["structural_governance"] = {}
        updates["financial_anomalies"] = {}
        updates["country_risk_classified"] = []
        updates["fca_details"] = None
        updates["warnings"] = state.get("warnings", []) + [
            "Analysis engines skipped: registry data unavailable"
        ]
        updates["stage_timings"]["analysis_engines"] = round(time.time() - t0, 2)
        return updates

    # Governance indicators
    try:
        gov_indicators = assess_governance_indicators(
            cc_governance, charity_data, ch_data)
        updates["governance_indicators"] = gov_indicators
    except Exception as e:
        log.warning(f"Governance indicators failed: {e}")
        updates["governance_indicators"] = {}

    # Structural governance
    try:
        struct_gov = assess_structural_governance(
            charity_data, ch_data, trustees, trustee_appointments)
        updates["structural_governance"] = struct_gov
    except Exception as e:
        log.warning(f"Structural governance failed: {e}")
        updates["structural_governance"] = {}

    # Financial anomalies
    try:
        anomalies = detect_financial_anomalies(financial_history)
        updates["financial_anomalies"] = anomalies
    except Exception as e:
        log.warning(f"Financial anomalies failed: {e}")
        updates["financial_anomalies"] = {}

    # Country risk classification
    try:
        areas = charity_data.get("areas_of_operation", [])
        classified = []
        for area in areas:
            name = area.get("aoo_name", "")
            if name:
                risk = get_country_risk(name)
                classified.append({
                    "country": name,
                    "risk_level": risk,
                    "aoo_type": area.get("aoo_type", ""),
                    "continent": area.get("continent", ""),
                })
        updates["country_risk_classified"] = classified
    except Exception as e:
        log.warning(f"Country risk classification failed: {e}")

    # FCA regulation check (if company linked to charity)
    try:
        website_url = state.get("website_url", "")
        entity_name = state.get("entity_name", "")
        ch_data = state.get("ch_data", {}) if ch_data is None else ch_data
        ch_number = ch_data.get("company_number", "") if ch_data else ""
        
        # Get industry info for FCA regulated industry check
        sic_codes = ch_data.get("sic_codes", []) if ch_data else []
        industry_category = ch_data.get("industry_category", "") if ch_data else ""
        
        if website_url and ch_data:
            from api_clients.fca_website_check import get_fca_status_for_company
            fca_details = get_fca_status_for_company(
                ch_data,
                website_url,
                sic_codes=sic_codes,
                industry_category=industry_category,
            )
            updates["fca_details"] = fca_details
            if fca_details.get("fca_found"):
                log.info(f"FCA: ✅ Found on website for {entity_name}")
        else:
            updates["fca_details"] = None
    except Exception as e:
        log.warning(f"FCA check failed: {e}")
        updates["fca_details"] = None

    updates["stage_timings"]["analysis_engines"] = round(time.time() - t0, 2)
    return updates


def screen_sanctions(state: dict) -> dict:
    """Pipeline node: Screen the entity and trustees against sanctions lists.

    Currently uses OFSI (UK Treasury consolidated list). Designed via the
    SanctionsProvider abstraction so OFAC, EU, UN, OpenSanctions can be
    added without changing this node.

    Output schema:
        sanctions_screening: {
            "entity": [SanctionsHit.to_dict(), ...],   # hits for the org
            "trustees": {                              # one entry per trustee
                "<name>": [SanctionsHit.to_dict(), ...],
                ...
            },
            "providers": ["OFSI", ...],
            "any_high_confidence": bool,
        }
    """
    from core.sanctions import default_providers, screen_against_providers

    updates: dict[str, Any] = {"stage_timings": dict(state.get("stage_timings", {}))}
    t0 = time.time()

    providers = default_providers()
    if not providers:
        updates["sanctions_screening"] = {
            "entity": [], "trustees": {}, "providers": [],
            "any_high_confidence": False,
        }
        updates["stage_timings"]["screen_sanctions"] = round(time.time() - t0, 2)
        return updates

    entity_name = state.get("entity_name", "")
    trustees = state.get("trustees", []) or []
    any_high = False

    entity_hits = []
    if entity_name:
        try:
            hits = screen_against_providers(entity_name, schema="entity", providers=providers)
            entity_hits = [h.to_dict() for h in hits]
            if any(h.confidence == "high" for h in hits):
                any_high = True
        except Exception as e:
            log.warning(f"Entity sanctions screening failed: {e}")

    trustee_hits: dict[str, list] = {}
    for t_name in trustees:
        try:
            hits = screen_against_providers(t_name, schema="person", providers=providers)
            trustee_hits[t_name] = [h.to_dict() for h in hits]
            if any(h.confidence == "high" for h in hits):
                any_high = True
        except Exception as e:
            log.warning(f"Trustee sanctions screening failed for {t_name!r}: {e}")
            trustee_hits[t_name] = []

    updates["sanctions_screening"] = {
        "entity": entity_hits,
        "trustees": trustee_hits,
        "providers": [p.name for p in providers],
        "any_high_confidence": any_high,
    }

    log.info(
        f"Sanctions screening complete: entity_hits={len(entity_hits)}, "
        f"trustees_with_hits={sum(1 for h in trustee_hits.values() if h)}, "
        f"any_high_confidence={any_high}"
    )

    updates["stage_timings"]["screen_sanctions"] = round(time.time() - t0, 2)
    return updates


def compute_risk_score(state: dict) -> dict:
    """Node 5: Compute the V3 numerical risk score."""
    from core.risk_scorer import score_charity

    updates: dict[str, Any] = {"stage_timings": dict(state.get("stage_timings", {}))}
    t0 = time.time()

    try:
        score = score_charity(
            charity_data=_dict(state, "charity_data"),
            financial_history=_list(state, "financial_history"),
            financial_anomalies=_dict(state, "financial_anomalies"),
            governance_indicators=_dict(state, "governance_indicators"),
            structural_governance=_dict(state, "structural_governance"),
            country_risk_classified=_list(state, "country_risk_classified"),
            adverse_org=_list(state, "adverse_org"),
            adverse_trustees=_dict(state, "adverse_trustees"),
            fatf_org_screen=state.get("fatf_org_screen"),
            fatf_trustee_screens=_dict(state, "fatf_trustee_screens"),
            hrcob_core_controls=_dict(state, "hrcob_core_controls"),
            policy_classification=_list(state, "policy_classification"),
            social_links=_dict(state, "social_links"),
            online_presence=_list(state, "online_presence"),
            cc_governance=_dict(state, "cc_governance"),
            ch_data=state.get("ch_data"),
            fca_details=state.get("fca_details"),
        )
        updates["risk_score"] = score.model_dump()
    except Exception as e:
        log.error(f"Risk scoring failed: {e}")
        updates["risk_score"] = {}
        updates["errors"] = state.get("errors", []) + [f"Risk scoring: {e}"]

    updates["stage_timings"]["risk_scoring"] = round(time.time() - t0, 2)
    return updates


def generate_llm_report(state: dict) -> dict:
    """Node 6: Build prompt and generate LLM report."""
    from prompts.charity_prompt import build_charity_prompt
    from core.validators import compact, slim_search

    updates: dict[str, Any] = {"stage_timings": dict(state.get("stage_timings", {}))}
    t0 = time.time()

    # Build data payload — every read is None-safe so a registry-fetch
    # failure upstream still produces a coherent (if mostly empty) prompt.
    data_payload = compact({
        "charity_data": _dict(state, "charity_data"),
        "cc_governance_intelligence": _dict(state, "cc_governance"),
        "governance_indicators": _dict(state, "governance_indicators"),
        "structural_governance": _dict(state, "structural_governance"),
        "financial_history": _list(state, "financial_history"),
        "financial_anomalies": _dict(state, "financial_anomalies"),
        "ch_data": state.get("ch_data"),
        "adverse_org_results": slim_search(_list(state, "adverse_org")),
        "adverse_trustee_results": {
            k: slim_search(v) for k, v in _dict(state, "adverse_trustees").items()
        },
        "positive_media": slim_search(_list(state, "positive_media")),
        "online_presence": slim_search(_list(state, "online_presence")),
        "policies_found": slim_search(_list(state, "policy_results")),
        "policy_classification": _list(state, "policy_classification"),
        "policy_doc_links": _list(state, "policy_doc_links"),
        "hrcob_core_controls": _dict(state, "hrcob_core_controls"),
        "social_media_links": _dict(state, "social_links"),
        "partnership_results": slim_search(_list(state, "partnership_results")),
        "fatf_org_screen": state.get("fatf_org_screen"),
        "fatf_trustee_screens": _dict(state, "fatf_trustee_screens"),
        "sanctions_screening": _dict(state, "sanctions_screening"),
        "country_risk_classified": _list(state, "country_risk_classified"),
        "risk_score_v3": _dict(state, "risk_score"),
        "search_failures": _list(state, "search_failures"),
    })

    all_data = json.dumps(data_payload, indent=2, default=str)

    # Document context
    doc_parts = []
    if state.get("cc_pdf_text"):
        doc_parts.append(f"--- CC REGISTER PRINTOUT ---\n{state['cc_pdf_text'][:15000]}")
    for i, txt in enumerate(state.get("uploaded_texts", [])):
        doc_parts.append(f"--- UPLOADED DOCUMENT {i+1} ---\n{txt[:10000]}")
    for i, txt in enumerate(state.get("gov_doc_texts", [])):
        doc_parts.append(f"--- GOVERNANCE DOCUMENT {i+1} ---\n{txt[:10000]}")
    doc_context = "\n\n".join(doc_parts)

    # Risk score summary for prompt
    rs = state.get("risk_score", {})
    risk_summary = ""
    if rs:
        risk_summary = (
            f"Score: {rs.get('overall_score', 'N/A')}/100 "
            f"({rs.get('overall_level', 'Unknown')})\n"
            f"Category scores: {json.dumps(rs.get('category_scores', {}))}\n"
            f"Hard stops: {rs.get('hard_stops', [])}"
        )

    prompt = build_charity_prompt(
        all_data=all_data,
        doc_context=doc_context,
        risk_score_summary=risk_summary,
    )

    updates["llm_prompt"] = prompt
    updates["stage_timings"]["generate_report"] = round(time.time() - t0, 2)
    return updates


# ═══════════════════════════════════════════════════════════════════════════════
# COMPANY PIPELINE NODES
# ═══════════════════════════════════════════════════════════════════════════════

def run_company_check_node(state: dict) -> dict:
    """Node: Execute the full company check analysis."""
    from core.company_check import run_company_check
    from api_clients.tavily_search import (
        search_adverse_media_hybrid, search_generic,
        search_social_osint, search_online_presence,
    )
    from core.fatf_screener import screen_entity

    updates: dict[str, Any] = {"stage_timings": dict(state.get("stage_timings", {}))}
    t0 = time.time()

    try:
        # Keyword names must match core.company_check.run_company_check
        # signature exactly. Previous code passed search_fn / social_search_fn /
        # online_search_fn — those parameters don't exist on the function and
        # the call raised TypeError, which the bare except below swallowed.
        # Result: every company report came back "Unknown entity" with empty
        # data, while the LLM hallucinated narrative around the void.
        result = run_company_check(
            state["company_number"],
            state.get("website_url", ""),
            tavily_search_fn=search_generic,
            adverse_search_fn=search_adverse_media_hybrid,
            social_osint_fn=search_social_osint,
            online_presence_fn=search_online_presence,
            fatf_screen_fn=screen_entity,
        )

        # ── Industry-aware compliance guidance ─────────────────────────
        # Translates SIC codes into a buyer-actionable checklist:
        # documents to request, registers to cross-check, regime-specific
        # red flags to test for. This is the unique-value layer that
        # turns the report from "data aggregation" into "decision support".
        try:
            from core.document_requirements import requirements_for
            sic_codes = (result.get("profile") or {}).get("sic_codes") or []
            status = (result.get("profile") or {}).get("status") or ""
            guidance = requirements_for(sic_codes, company_status=status)
            result["compliance_guidance"] = guidance.to_dict()
        except Exception as ce:
            log.warning("Compliance guidance failed: %s", ce)
            result["compliance_guidance"] = {}

        # ── Deep website OSINT ─────────────────────────────────────────
        # If a website URL is available (user-supplied OR auto-discovered
        # from cross_reference/online_presence), scrape it for og: tags,
        # social accounts, key compliance pages, contact info, SSL, and
        # domain age. This is what turns the report from "looked up" into
        # "verified". Failures are non-fatal; logged + skipped.
        try:
            from core.website_intel import fetch_website_intelligence
            url = state.get("website_url") or _discover_website_url(result)
            if url:
                log.info("Scraping website OSINT for %s: %s",
                         state.get("company_number"), url)
                result["website_intel"] = fetch_website_intelligence(url, max_pages=5)
            else:
                result["website_intel"] = {"ok": False, "error": "no website url available"}
        except Exception as we:
            log.warning("Website intel failed: %s", we)
            result["website_intel"] = {"ok": False, "error": str(we)}

        updates["company_check"] = result

        # Add FCA details to company_check if present
        fca_details = state.get("fca_details")
        if fca_details:
            updates["company_check"]["fca_details"] = fca_details
    except Exception as e:
        log.exception("Company check failed for %s",
                      state.get("company_number"))
        updates["errors"] = state.get("errors", []) + [
            f"Company check failed: {type(e).__name__}: {e}"
        ]
        updates["company_check"] = {}

    updates["stage_timings"]["company_check"] = round(time.time() - t0, 2)
    return updates


def compute_company_risk_score(state: dict) -> dict:
    """Node: Compute V3 numerical risk score for company."""
    from core.risk_scorer import score_company

    updates: dict[str, Any] = {"stage_timings": dict(state.get("stage_timings", {}))}
    t0 = time.time()

    try:
        check = state.get("company_check", {})
        score = score_company(check)
        updates["risk_score"] = score.model_dump()
    except Exception as e:
        log.error(f"Company risk scoring failed: {e}")
        updates["risk_score"] = {}

    updates["stage_timings"]["company_risk_scoring"] = round(time.time() - t0, 2)
    return updates


def generate_company_prompt(state: dict) -> dict:
    """Node: Build the company sense-check LLM prompt."""
    from prompts.company_prompt import build_company_prompt
    from core.validators import compact

    updates: dict[str, Any] = {"stage_timings": dict(state.get("stage_timings", {}))}
    t0 = time.time()

    check = state.get("company_check", {})
    rm = check.get("risk_matrix", {})

    # Build verdict blocks (same logic as existing app.py)
    hard_stops = rm.get("hard_stops", [])
    if hard_stops:
        verdict_override = (
            "⚠️ HARD STOP TRIGGERED: " + "; ".join(hard_stops) +
            "\nThe report MUST be flagged as CRITICAL RISK."
        )
    else:
        verdict_override = ""

    cats = rm.get("category_risks", {})
    verdict_lines = [f"- {k}: {v}" for k, v in cats.items()]
    verdict_block = ("PRE-COMPUTED RISK MATRIX:\n" + "\n".join(verdict_lines)
                     if verdict_lines else "")

    # Recommendation instructions
    overall = rm.get("overall_risk", "unknown")
    if overall in ("critical", "high") or hard_stops:
        rec = ("This entity has CRITICAL/HIGH risk. Frame observations around the "
               "specific risk drivers. Do not suggest this is routine.")
    elif overall == "medium":
        rec = ("This entity has MEDIUM risk. Note areas requiring attention. "
               "Frame as advisory observations for the analyst.")
    else:
        rec = ("This entity has LOW risk. Frame observations as routine "
               "compliance notes. Acknowledge clean aspects positively.")

    # Risk score summary
    rs = state.get("risk_score", {})
    risk_summary = ""
    if rs:
        risk_summary = (
            f"Score: {rs.get('overall_score', 'N/A')}/100 "
            f"({rs.get('overall_level', 'Unknown')})\n"
            f"Category scores: {json.dumps(rs.get('category_scores', {}))}\n"
            f"Hard stops: {rs.get('hard_stops', [])}"
        )

    data_json = json.dumps(compact(check), indent=2, default=str)

    prompt = build_company_prompt(
        company_name=check.get("company_name", "Unknown"),
        company_number=state.get("company_number", ""),
        co_check_data=check,
        verdict_override=verdict_override,
        verdict_block=verdict_block,
        risk_matrix=rm,
        recommendation_instructions=rec,
        data_json=data_json,
        risk_score_summary=risk_summary,
    )

    updates["llm_prompt"] = prompt
    updates["stage_timings"]["generate_company_prompt"] = round(time.time() - t0, 2)
    return updates
