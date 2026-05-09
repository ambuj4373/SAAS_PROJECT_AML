"""
reports/charity.py — End-to-end charity due-diligence report orchestrator.

Composes the data-gathering pipeline with the LLM call, self-verification,
structured-output parsing, and audit logging into a single function that
returns a self-contained ``CharityReportBundle``.

Public API
----------
- generate_charity_report(charity_number, **opts) -> CharityReportBundle
- CharityReportBundle  — dataclass with all the pieces a UI / API needs

This module has no Streamlit dependency.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from core.llm_client import RateLimitCallback, llm_generate
from core.logging_config import get_logger
from core.narrative_verifier import (
    NarrativeVerifierResult,
    verify_narrative,
)
from core.self_verification import (
    VerificationResult,
    build_verification_prompt,
    parse_verification_result,
)
from core.structured_outputs import (
    StructuredCharityReport,
    parse_structured_report,
)
from pipeline.charity_graph import run_charity_pipeline

log = get_logger("reports.charity")


# ─── Bundle ────────────────────────────────────────────────────────────────

@dataclass
class CharityReportBundle:
    """Everything needed to render a final charity report.

    ``state`` is the raw pipeline state (the data-gathering output) for
    advanced consumers. The convenience fields below are derived from it
    plus the post-pipeline LLM passes.
    """

    charity_number: str

    # Raw pipeline output (the data-gathering layer)
    state: dict[str, Any] = field(default_factory=dict)

    # LLM narrative pass
    narrative_report: str = ""
    llm_prompt: str = ""
    cost_info: dict[str, Any] = field(default_factory=dict)

    # Optional post-processing
    verification: Optional[VerificationResult] = None
    structured_report: Optional[StructuredCharityReport] = None
    narrative_check: Optional[NarrativeVerifierResult] = None
    db_row_id: Optional[int] = None

    # Bookkeeping
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    timings: dict[str, float] = field(default_factory=dict)

    @property
    def entity_name(self) -> str:
        return self.state.get("entity_name", f"Charity {self.charity_number}")

    @property
    def risk_score(self) -> dict[str, Any]:
        return self.state.get("risk_score", {})

    @property
    def total_cost_usd(self) -> float:
        return float(self.cost_info.get("cost_usd", 0.0))


# ─── Orchestrator ──────────────────────────────────────────────────────────

def generate_charity_report(
    charity_number: str,
    *,
    website_override: str = "",
    manual_social_links: Optional[dict] = None,
    cc_printout: Any = None,
    uploaded_docs: Optional[list] = None,
    uploaded_gov_docs: Optional[list] = None,
    model_label: Optional[str] = None,
    on_stage_start: Optional[Callable[[str, dict], None]] = None,
    on_stage_end: Optional[Callable[[str, dict, float], None]] = None,
    on_rate_limit: Optional[RateLimitCallback] = None,
    skip_llm: bool = False,
    skip_verification: bool = False,
    skip_structured_parsing: bool = False,
    skip_db_log: bool = False,
) -> CharityReportBundle:
    """Run the full charity due-diligence flow end-to-end.

    Parameters
    ----------
    charity_number : str
        Charity Commission registration number.
    website_override : str
        Override the auto-detected website URL.
    manual_social_links : dict
        Manually-supplied social media URLs (overrides auto-discovery).
    cc_printout : file-like or None
        Optional CC register printout PDF.
    uploaded_docs : list
        Optional accounts / annual report PDFs.
    uploaded_gov_docs : list
        Optional governance / policy PDFs.
    model_label : str, optional
        Key from ``config.LLM_PROVIDERS``. None → first available.
    on_stage_start, on_stage_end : callable
        Pipeline progress callbacks (forwarded to ``run_charity_pipeline``).
    on_rate_limit : callable
        LLM rate-limit notification hook.
    skip_llm : bool
        If True, return after data-gathering. ``narrative_report`` will be
        empty. Useful for unit tests of the pipeline stages.
    skip_verification : bool
        If True, skip the second LLM call (self-verification).
    skip_structured_parsing : bool
        If True, leave ``structured_report`` as None.
    skip_db_log : bool
        If True, do not persist the assessment to SQLite.

    Returns
    -------
    CharityReportBundle
    """
    bundle = CharityReportBundle(charity_number=charity_number)

    # ── Stage 1: Data gathering pipeline ──
    state = run_charity_pipeline(
        charity_number,
        website_override=website_override,
        manual_social_links=manual_social_links,
        cc_printout=cc_printout,
        uploaded_docs=uploaded_docs,
        uploaded_gov_docs=uploaded_gov_docs,
        on_stage_start=on_stage_start,
        on_stage_end=on_stage_end,
    )
    bundle.state = state
    bundle.llm_prompt = state.get("llm_prompt", "")
    bundle.errors = list(state.get("errors", []))
    bundle.warnings = list(state.get("warnings", []))
    bundle.timings = dict(state.get("stage_timings", {}))

    if skip_llm:
        return bundle

    if not bundle.llm_prompt:
        bundle.errors.append("LLM prompt was empty; skipping LLM call")
        return bundle

    # ── Stage 2: Primary LLM narrative ──
    try:
        narrative, cost_info = llm_generate(
            bundle.llm_prompt,
            model_label=model_label,
            on_rate_limit=on_rate_limit,
        )
        bundle.narrative_report = narrative
        bundle.cost_info = dict(cost_info)
    except Exception as e:
        log.error(f"LLM narrative call failed: {e}")
        bundle.errors.append(f"LLM narrative: {e}")
        return bundle

    # ── Stage 3: Self-verification (optional second LLM pass) ──
    if not skip_verification:
        try:
            data_summary = _build_data_summary(state)
            verif_prompt = build_verification_prompt(narrative, data_summary)
            verif_raw, verif_cost = llm_generate(
                verif_prompt,
                model_label=model_label,
                on_rate_limit=on_rate_limit,
            )
            bundle.verification = parse_verification_result(verif_raw)
            _accumulate_cost(bundle.cost_info, verif_cost)
        except Exception as e:
            log.warning(f"Self-verification failed: {e}")
            bundle.warnings.append(f"Self-verification: {e}")

    # ── Stage 4: Structured-output parsing ──
    if not skip_structured_parsing:
        try:
            structured, _ = parse_structured_report(
                narrative, StructuredCharityReport
            )
            bundle.structured_report = structured
        except Exception as e:
            log.warning(f"Structured parsing failed: {e}")
            bundle.warnings.append(f"Structured parsing: {e}")

    # ── Stage 4b: Programmatic narrative verifier ──
    # Cheap deterministic check that the narrative isn't claiming things
    # not present in the state (sanctions hits, fake trustees, etc.).
    # Runs even when the LLM-based self-verification was skipped, since
    # this one has no LLM cost.
    try:
        bundle.narrative_check = verify_narrative(narrative, state)
        if not bundle.narrative_check.is_clean:
            bundle.warnings.append(
                f"Narrative verifier flagged "
                f"{bundle.narrative_check.critical_count} critical, "
                f"{bundle.narrative_check.warning_count} warning issues"
            )
    except Exception as e:
        log.warning(f"Narrative verifier failed: {e}")
        bundle.warnings.append(f"Narrative verifier: {e}")

    # ── Stage 5: Audit log ──
    if not skip_db_log:
        try:
            from core.database import log_ai_assessment

            bundle.db_row_id = log_ai_assessment(
                bundle.entity_name,
                bundle.narrative_report,
                entity_type="charity",
                assessment_type="full_report",
                risk_level=bundle.risk_score.get("overall_level", ""),
                model_used=bundle.cost_info.get("model", ""),
            )
        except Exception as e:
            log.warning(f"DB log failed: {e}")
            bundle.warnings.append(f"DB log: {e}")

    return bundle


# ─── Internals ─────────────────────────────────────────────────────────────

def _build_data_summary(state: dict) -> str:
    """Compact JSON summary of pipeline state for the verification prompt."""
    summary = {
        "charity_data": state.get("charity_data"),
        "financial_history": state.get("financial_history", []),
        "risk_score": state.get("risk_score", {}),
        "fatf_org_screen": state.get("fatf_org_screen"),
        "adverse_org_count": len(state.get("adverse_org", []) or []),
        "policies_found_count": len(state.get("policy_results", []) or []),
    }
    return json.dumps(summary, indent=2, default=str)


def _accumulate_cost(running: dict, addition: dict) -> None:
    """Add a follow-up LLM call's cost into the running cost_info dict."""
    if not addition:
        return
    for key in ("cost_usd", "prompt_tokens", "completion_tokens", "total_tokens"):
        running[key] = running.get(key, 0) + addition.get(key, 0)
