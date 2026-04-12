"""
pipeline/company_graph.py — LangGraph company sense-check pipeline.

3-node graph: data collection → risk scoring → prompt generation.
"""

from __future__ import annotations

import time
from typing import Any, Callable

from core.logging_config import get_logger, PipelineMetrics

log = get_logger("pipeline.company_graph")

# ── State defaults ────────────────────────────────────────────────────────

COMPANY_STATE_DEFAULTS: dict[str, Any] = {
    "company_number": "",
    "website_url": "",
    # ── Populated by nodes ──
    "company_check": {},
    "risk_score": {},
    "llm_prompt": "",
    "llm_report": "",
    "errors": [],
    "warnings": [],
    "stage_timings": {},
}

# ── Node ordering ─────────────────────────────────────────────────────────

from pipeline.nodes import (
    run_company_check_node,
    compute_company_risk_score,
    generate_company_prompt,
)

COMPANY_NODES = [
    ("company_check", run_company_check_node),
    ("company_risk_score", compute_company_risk_score),
    ("generate_prompt", generate_company_prompt),
]

COMPANY_STAGE_LABELS = {
    "company_check": {
        "icon": "📡",
        "step": "1/3",
        "title": "Retrieving company records & OSINT",
        "desc": (
            "Fetching full Companies House profile, officers, "
            "ownership structure, filing history, adverse media, "
            "FATF screening, and web presence analysis."
        ),
        "est_time": "~30s",
    },
    "company_risk_score": {
        "icon": "📊",
        "step": "2/3",
        "title": "Risk scoring",
        "desc": (
            "Computing numerical risk score (0–100) across "
            "company-specific categories including entity age, "
            "status, directors, UBO, merchant suitability."
        ),
        "est_time": "~2s",
    },
    "generate_prompt": {
        "icon": "🤖",
        "step": "3/3",
        "title": "Building analysis prompt",
        "desc": (
            "Assembling all intelligence and pre-computed verdicts "
            "into the structured AI analyst prompt."
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


# ── Try LangGraph, fallback to sequential runner ─────────────────────────

_HAS_LANGGRAPH = False
try:
    from langgraph.graph import StateGraph, END  # type: ignore
    _HAS_LANGGRAPH = True
except ImportError:
    pass


def _build_langgraph():
    if not _HAS_LANGGRAPH:
        return None
    from langgraph.graph import StateGraph, END  # type: ignore

    graph = StateGraph(dict)
    for name, fn in COMPANY_NODES:
        graph.add_node(name, fn)

    graph.set_entry_point("company_check")
    for i in range(len(COMPANY_NODES) - 1):
        graph.add_edge(COMPANY_NODES[i][0], COMPANY_NODES[i + 1][0])
    graph.add_edge(COMPANY_NODES[-1][0], END)

    return graph.compile()


_compiled_graph = None


def _get_graph():
    global _compiled_graph
    if _compiled_graph is None:
        _compiled_graph = _build_langgraph()
    return _compiled_graph


# ── Public API ────────────────────────────────────────────────────────────

def run_company_pipeline(
    company_number: str,
    *,
    website_url: str = "",
    on_stage_start: Callable[[str, dict], None] | None = None,
    on_stage_end: Callable[[str, dict, float], None] | None = None,
) -> dict:
    """
    Run the full company sense-check pipeline.

    Parameters
    ----------
    company_number : str
        Companies House registration number.
    website_url : str
        Optional website URL.
    on_stage_start / on_stage_end : callable
        Progress callbacks for the UI.

    Returns
    -------
    dict
        Final pipeline state including ``company_check``, ``risk_score``,
        and ``llm_prompt``.
    """
    metrics = PipelineMetrics()
    metrics.start("company_pipeline")

    state = dict(COMPANY_STATE_DEFAULTS)
    state["company_number"] = company_number
    state["website_url"] = website_url

    for name, fn in COMPANY_NODES:
        meta = COMPANY_STAGE_LABELS.get(name, {})
        if on_stage_start:
            on_stage_start(name, meta)

        t0 = time.time()
        try:
            updates = fn(state)
            state = _merge_state(state, updates)
        except Exception as e:
            log.error(f"Node {name} failed: {e}")
            state["errors"] = state.get("errors", []) + [f"Stage {name}: {e}"]
        elapsed = time.time() - t0

        if on_stage_end:
            on_stage_end(name, meta, elapsed)

    metrics.finish()
    state["pipeline_metrics"] = {
        "total_seconds": metrics.total_seconds,
        "stage_count": len(COMPANY_NODES),
        "error_count": len(state.get("errors", [])),
    }

    log.info(
        f"Company pipeline completed in {metrics.total_seconds:.1f}s "
        f"with {len(state.get('errors', []))} errors"
    )
    return state
