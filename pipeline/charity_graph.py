"""
pipeline/charity_graph.py — LangGraph charity due-diligence pipeline.

Defines a 6-node sequential graph that mirrors the existing 7-step
charity analysis flow but with structured state, logging, and scoring.
"""

from __future__ import annotations

import time
from typing import Any, Callable, Optional

from core.logging_config import get_logger, PipelineMetrics

log = get_logger("pipeline.charity_graph")

# ── State type ────────────────────────────────────────────────────────────

# We use a plain dict as the state container rather than TypedDict
# so we stay compatible with both LangGraph (if installed) and a
# simple sequential fallback runner.

CHARITY_STATE_DEFAULTS: dict[str, Any] = {
    "charity_number": "",
    "entity_name": "",
    "website_override": "",
    "manual_social_links": {},
    "cc_printout": None,
    "uploaded_docs": [],
    "uploaded_gov_docs": [],
    # ── Populated by nodes ──
    "charity_data": None,
    "financial_history": [],
    "cc_governance": {},
    "ch_data": None,
    "trustees": [],
    "trustee_appointments": {},
    "website_url": "",
    "cc_pdf_result": {},
    "cc_pdf_text": "",
    "uploaded_texts": [],
    "gov_doc_texts": [],
    "extraction_metadata": [],
    "partners_discovered": [],
    "adverse_org": [],
    "adverse_trustees": {},
    "positive_media": [],
    "online_presence": [],
    "policy_results": [],
    "policy_audit": [],
    "policy_doc_links": [],
    "policy_classification": [],
    "social_links": {},
    "hrcob_core_controls": {},
    "partnership_results": [],
    "fca_details": None,
    "fatf_org_screen": None,
    "fatf_trustee_screens": {},
    "search_failures": [],
    "governance_indicators": {},
    "structural_governance": {},
    "financial_anomalies": {},
    "country_risk_classified": [],
    "risk_score": {},
    "llm_prompt": "",
    "llm_report": "",
    "errors": [],
    "warnings": [],
    "stage_timings": {},
}


# ── Node ordering ─────────────────────────────────────────────────────────

# Import the node functions
from pipeline.nodes import (
    fetch_registry_data,
    extract_documents,
    run_web_intelligence,
    run_analysis_engines,
    screen_sanctions,
    compute_risk_score,
    generate_llm_report,
)

# The charity pipeline runs these nodes in sequence.
# Each node reads the full state and returns a partial update dict.
CHARITY_NODES = [
    ("fetch_registry", fetch_registry_data),
    ("extract_documents", extract_documents),
    ("web_intelligence", run_web_intelligence),
    ("analysis_engines", run_analysis_engines),
    ("screen_sanctions", screen_sanctions),
    ("compute_risk_score", compute_risk_score),
    ("generate_llm_report", generate_llm_report),
]

# Human-readable labels for each stage (for progress UI)
CHARITY_STAGE_LABELS = {
    "fetch_registry": {
        "icon": "🔍",
        "step": "1/7",
        "title": "Charity Commission & Companies House records",
        "desc": (
            "Retrieving the charity's official record, trustees, "
            "governance policies, financial summary, and linked "
            "company data."
        ),
        "est_time": "~8s",
    },
    "extract_documents": {
        "icon": "📄",
        "step": "2/7",
        "title": "Document extraction & enrichment",
        "desc": (
            "Parsing uploaded PDFs: CC printout, accounts, "
            "governance documents. Extracting partners and "
            "financial tables."
        ),
        "est_time": "~5s",
    },
    "web_intelligence": {
        "icon": "🌐",
        "step": "3/7",
        "title": "Web intelligence & OSINT",
        "desc": (
            "Running parallel adverse-media checks, positive media, "
            "online presence, policy search, partnerships, and "
            "FATF screening across multiple search engines."
        ),
        "est_time": "~30s",
    },
    "analysis_engines": {
        "icon": "⚙️",
        "step": "4/7",
        "title": "Governance & financial analysis",
        "desc": (
            "Assessing governance indicators, structural governance, "
            "financial anomaly detection, and country risk "
            "classification."
        ),
        "est_time": "~5s",
    },
    "screen_sanctions": {
        "icon": "🛡️",
        "step": "5/7",
        "title": "Sanctions screening",
        "desc": (
            "Matching the charity and every trustee against the OFSI "
            "UK consolidated sanctions list (HM Treasury). Designed "
            "to extend to OFAC, EU, UN, and OpenSanctions."
        ),
        "est_time": "~3s",
    },
    "compute_risk_score": {
        "icon": "📊",
        "step": "6/7",
        "title": "Risk scoring",
        "desc": (
            "Computing numerical risk score (0–100) across six "
            "categories: Geography, Financial, Governance, Media, "
            "Transparency, and Operational."
        ),
        "est_time": "~2s",
    },
    "generate_llm_report": {
        "icon": "🤖",
        "step": "7/7",
        "title": "Building analysis prompt",
        "desc": (
            "Assembling all gathered intelligence into the structured "
            "LLM prompt for the AI analyst."
        ),
        "est_time": "~2s",
    },
}


def _merge_state(state: dict, updates: dict) -> dict:
    """Deep-merge node output into pipeline state."""
    for k, v in updates.items():
        if k == "stage_timings" and isinstance(v, dict):
            old = state.get("stage_timings", {})
            old.update(v)
            state["stage_timings"] = old
        elif k in ("errors", "warnings") and isinstance(v, list):
            state[k] = state.get(k, []) + [
                item for item in v if item not in state.get(k, [])
            ]
        else:
            state[k] = v
    return state


# ── Try LangGraph first, fallback to sequential runner ────────────────────

_HAS_LANGGRAPH = False
try:
    from langgraph.graph import StateGraph, END  # type: ignore
    _HAS_LANGGRAPH = True
except ImportError:
    log.info("langgraph not installed — using sequential fallback runner")


def _build_langgraph():
    """Build a compiled LangGraph graph (if library available)."""
    if not _HAS_LANGGRAPH:
        return None

    from langgraph.graph import StateGraph, END  # type: ignore

    # State is a plain dict
    graph = StateGraph(dict)

    # Register nodes
    for name, fn in CHARITY_NODES:
        graph.add_node(name, fn)

    # Linear edges
    graph.set_entry_point("fetch_registry")
    for i in range(len(CHARITY_NODES) - 1):
        graph.add_edge(CHARITY_NODES[i][0], CHARITY_NODES[i + 1][0])
    graph.add_edge(CHARITY_NODES[-1][0], END)

    return graph.compile()


# Pre-compile once at import
_compiled_graph = None


def _get_graph():
    global _compiled_graph
    if _compiled_graph is None:
        _compiled_graph = _build_langgraph()
    return _compiled_graph


# ── Public API ────────────────────────────────────────────────────────────

def run_charity_pipeline(
    charity_number: str,
    *,
    website_override: str = "",
    manual_social_links: dict | None = None,
    cc_printout=None,
    uploaded_docs: list | None = None,
    uploaded_gov_docs: list | None = None,
    on_stage_start: Callable[[str, dict], None] | None = None,
    on_stage_end: Callable[[str, dict, float], None] | None = None,
) -> dict:
    """
    Run the full charity due-diligence pipeline.

    Parameters
    ----------
    charity_number : str
        Charity Commission registration number.
    website_override : str
        Optional website URL override.
    manual_social_links : dict
        Optional dict of manually-entered social media URLs.
    cc_printout : UploadedFile or None
        CC register printout PDF.
    uploaded_docs : list
        List of uploaded PDF files (accounts, reports).
    uploaded_gov_docs : list
        List of uploaded governance documents.
    on_stage_start : callable
        ``(stage_name, stage_meta) → None`` — called before each node.
    on_stage_end : callable
        ``(stage_name, stage_meta, elapsed_seconds) → None`` — called after.

    Returns
    -------
    dict
        Final pipeline state with all collected intelligence.
    """
    metrics = PipelineMetrics(pipeline_name="charity_pipeline")
    metrics.start()

    # Initialise state
    state = dict(CHARITY_STATE_DEFAULTS)
    state["charity_number"] = charity_number
    state["website_override"] = website_override
    state["manual_social_links"] = manual_social_links or {}
    state["cc_printout"] = cc_printout
    state["uploaded_docs"] = uploaded_docs or []
    state["uploaded_gov_docs"] = uploaded_gov_docs or []

    graph = _get_graph()

    if graph is not None:
        log.info("Running charity pipeline via LangGraph")
        # LangGraph handles state merging internally
        # but we still want progress callbacks
        for name, fn in CHARITY_NODES:
            meta = CHARITY_STAGE_LABELS.get(name, {})
            if on_stage_start:
                on_stage_start(name, meta)

            t0 = time.time()
            # Run via node function directly for callback granularity
            updates = fn(state)
            state = _merge_state(state, updates)
            elapsed = time.time() - t0

            if on_stage_end:
                on_stage_end(name, meta, elapsed)
    else:
        log.info("Running charity pipeline via sequential fallback")
        for name, fn in CHARITY_NODES:
            meta = CHARITY_STAGE_LABELS.get(name, {})
            if on_stage_start:
                on_stage_start(name, meta)

            t0 = time.time()
            try:
                updates = fn(state)
                state = _merge_state(state, updates)
            except Exception as e:
                log.error(f"Node {name} failed: {e}")
                state["errors"] = state.get("errors", []) + [
                    f"Stage {name}: {e}"
                ]
            elapsed = time.time() - t0

            if on_stage_end:
                on_stage_end(name, meta, elapsed)

    metrics.finish()
    state["pipeline_metrics"] = {
        "total_seconds": metrics.total_duration_s,
        "stage_count": len(CHARITY_NODES),
        "error_count": len(state.get("errors", [])),
    }

    log.info(
        f"Charity pipeline completed in {metrics.total_duration_s:.1f}s "
        f"with {len(state.get('errors', []))} errors"
    )
    return state
