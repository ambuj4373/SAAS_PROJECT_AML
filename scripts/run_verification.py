"""
scripts/run_verification.py — Run the new reports/ path against a charity
or company and dump everything to verification_runs/ for review.

Usage:
    python3 scripts/run_verification.py charity <charity_number>
    python3 scripts/run_verification.py company <company_number>

Writes:
    verification_runs/<id>_<timestamp>/
        bundle.json       — full pipeline state + scoring + verification
        narrative.md      — the LLM markdown report
        prompt.txt        — the master prompt sent to the LLM
        meta.json         — timings, costs, errors, warnings
"""

from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

# Make project root importable when run from anywhere
_HERE = Path(__file__).resolve().parent
_ROOT = _HERE.parent
sys.path.insert(0, str(_ROOT))

from reports import generate_charity_report, generate_company_report  # noqa: E402


def _stage_start(name: str, meta: dict) -> None:
    label = meta.get("step", "?")
    title = meta.get("title", name)
    print(f"  [{label}] {title}…", flush=True)


def _stage_end(name: str, meta: dict, elapsed: float) -> None:
    print(f"        done in {elapsed:.1f}s", flush=True)


def _rate_limit(label: str, attempt: int, wait: int, status: str) -> None:
    if status == "retrying":
        print(f"  ⏳ Rate limited on {label}, retry in {wait}s…", flush=True)
    else:
        print(f"  ↩  {label} exhausted, falling through…", flush=True)


def run_charity(charity_number: str, model_label: str | None = None) -> None:
    out_dir = _ROOT / "verification_runs" / f"charity_{charity_number}_{_ts()}"
    out_dir.mkdir(parents=True, exist_ok=True)
    print(f"→ Running charity {charity_number}, output: {out_dir}")
    t0 = time.time()

    bundle = generate_charity_report(
        charity_number,
        model_label=model_label,
        on_stage_start=_stage_start,
        on_stage_end=_stage_end,
        on_rate_limit=_rate_limit,
    )

    elapsed = time.time() - t0
    print(f"\n=== Run complete in {elapsed:.1f}s ===")
    print(f"Entity:        {bundle.entity_name}")
    print(f"Risk score:    {bundle.risk_score.get('overall_score', '?')}/100 "
          f"({bundle.risk_score.get('overall_level', '?')})")
    print(f"Cost:          ${bundle.total_cost_usd:.4f} "
          f"({bundle.cost_info.get('model', '?')})")
    print(f"Errors:        {len(bundle.errors)}")
    print(f"Warnings:      {len(bundle.warnings)}")
    if bundle.verification:
        v = bundle.verification
        print(f"Verification:  {getattr(v, 'reliability_label', '?')} "
              f"({getattr(v, 'overall_reliability', 0):.0%})")
    print(f"Narrative:     {len(bundle.narrative_report):,} chars")

    _write_outputs(out_dir, bundle)
    print(f"\nFiles written to: {out_dir.relative_to(_ROOT)}")


def run_company(company_number: str, model_label: str | None = None) -> None:
    out_dir = _ROOT / "verification_runs" / f"company_{company_number}_{_ts()}"
    out_dir.mkdir(parents=True, exist_ok=True)
    print(f"→ Running company {company_number}, output: {out_dir}")
    t0 = time.time()

    bundle = generate_company_report(
        company_number,
        model_label=model_label,
        on_stage_start=_stage_start,
        on_stage_end=_stage_end,
        on_rate_limit=_rate_limit,
    )

    elapsed = time.time() - t0
    print(f"\n=== Run complete in {elapsed:.1f}s ===")
    print(f"Entity:        {bundle.company_name}")
    print(f"Risk score:    {bundle.risk_score.get('overall_score', '?')}/100 "
          f"({bundle.risk_score.get('overall_level', '?')})")
    print(f"Cost:          ${bundle.total_cost_usd:.4f}")
    print(f"Narrative:     {len(bundle.narrative_report):,} chars")

    _write_outputs(out_dir, bundle)


def _ts() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def _write_outputs(out_dir: Path, bundle) -> None:
    # Bundle as JSON (state, risk_score, etc., minus binary blobs)
    state = dict(bundle.state)
    # Strip non-serializable file handles if any
    for k in ("cc_printout", "uploaded_docs", "uploaded_gov_docs"):
        state.pop(k, None)

    payload = {
        "entity_id": getattr(bundle, "charity_number", None) or getattr(bundle, "company_number", None),
        "entity_name": getattr(bundle, "entity_name", None) or getattr(bundle, "company_name", None),
        "risk_score": bundle.risk_score,
        "cost_info": bundle.cost_info,
        "verification": bundle.verification.model_dump() if bundle.verification else None,
        "structured_report": bundle.structured_report.model_dump() if bundle.structured_report else None,
        "narrative_check": bundle.narrative_check.to_dict() if bundle.narrative_check else None,
        "errors": bundle.errors,
        "warnings": bundle.warnings,
        "timings": bundle.timings,
        "state_keys": sorted(state.keys()),
    }
    (out_dir / "bundle.json").write_text(json.dumps(payload, indent=2, default=str))

    # Full state for forensics
    (out_dir / "state.json").write_text(json.dumps(state, indent=2, default=str))

    # Narrative
    (out_dir / "narrative.md").write_text(bundle.narrative_report or "")

    # Prompt
    (out_dir / "prompt.txt").write_text(bundle.llm_prompt or "")


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("usage: run_verification.py charity|company <id> [model_label]")
        sys.exit(2)

    mode = sys.argv[1]
    eid = sys.argv[2]
    model = sys.argv[3] if len(sys.argv) > 3 else None

    if mode == "charity":
        run_charity(eid, model)
    elif mode == "company":
        run_company(eid, model)
    else:
        print(f"unknown mode: {mode}")
        sys.exit(2)
