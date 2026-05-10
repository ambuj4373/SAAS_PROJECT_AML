"""
reports/company.py — End-to-end company sense-check report orchestrator.

Mirror of reports/charity.py for company analysis. Composes the company
pipeline with the LLM call, self-verification, structured-output parsing,
and audit logging.

Public API
----------
- generate_company_report(company_number, **opts) -> CompanyReportBundle
- CompanyReportBundle  — dataclass with all the pieces a UI / API needs
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
    StructuredCompanyReport,
    parse_structured_report,
)
from pipeline.company_graph import run_company_pipeline

log = get_logger("reports.company")


@dataclass
class CompanyReportBundle:
    """Everything needed to render a final company sense-check report."""

    company_number: str

    state: dict[str, Any] = field(default_factory=dict)

    narrative_report: str = ""
    llm_prompt: str = ""
    cost_info: dict[str, Any] = field(default_factory=dict)

    verification: Optional[VerificationResult] = None
    structured_report: Optional[StructuredCompanyReport] = None
    narrative_check: Optional[NarrativeVerifierResult] = None
    db_row_id: Optional[int] = None

    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    timings: dict[str, float] = field(default_factory=dict)

    @property
    def company_name(self) -> str:
        check = self.state.get("company_check", {}) or {}
        return check.get("company_name", f"Company {self.company_number}")

    @property
    def risk_score(self) -> dict[str, Any]:
        return self.state.get("risk_score", {})

    @property
    def total_cost_usd(self) -> float:
        return float(self.cost_info.get("cost_usd", 0.0))


def generate_company_report(
    company_number: str,
    *,
    website_url: str = "",
    model_label: Optional[str] = None,
    on_stage_start: Optional[Callable[[str, dict], None]] = None,
    on_stage_end: Optional[Callable[[str, dict, float], None]] = None,
    on_rate_limit: Optional[RateLimitCallback] = None,
    skip_llm: bool = False,
    skip_verification: bool = False,
    skip_structured_parsing: bool = False,
    skip_db_log: bool = False,
) -> CompanyReportBundle:
    """Run the full company sense-check flow end-to-end.

    See ``reports.charity.generate_charity_report`` for the parameter
    semantics; this is the company equivalent.
    """
    bundle = CompanyReportBundle(company_number=company_number)

    state = run_company_pipeline(
        company_number,
        website_url=website_url,
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

    if not skip_structured_parsing:
        try:
            structured, _ = parse_structured_report(
                narrative, StructuredCompanyReport
            )
            bundle.structured_report = structured
        except Exception as e:
            log.warning(f"Structured parsing failed: {e}")
            bundle.warnings.append(f"Structured parsing: {e}")

    # Programmatic narrative verifier — same deterministic checks as charity
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

    if not skip_db_log:
        try:
            from core.database import log_ai_assessment

            bundle.db_row_id = log_ai_assessment(
                bundle.company_name,
                bundle.narrative_report,
                entity_type="company",
                assessment_type="full_report",
                risk_level=bundle.risk_score.get("overall_level", ""),
                model_used=bundle.cost_info.get("model", ""),
            )
        except Exception as e:
            log.warning(f"DB log failed: {e}")
            bundle.warnings.append(f"DB log: {e}")

    return bundle


def _build_data_summary(state: dict) -> str:
    check = state.get("company_check", {}) or {}
    summary = {
        "company_name": check.get("company_name"),
        "company_number": state.get("company_number"),
        "risk_matrix": check.get("risk_matrix", {}),
        "risk_score": state.get("risk_score", {}),
        "ubo_chain": check.get("ubo_chain", {}),
    }
    return json.dumps(summary, indent=2, default=str)


def _accumulate_cost(running: dict, addition: dict) -> None:
    if not addition:
        return
    for key in ("cost_usd", "prompt_tokens", "completion_tokens", "total_tokens"):
        running[key] = running.get(key, 0) + addition.get(key, 0)
