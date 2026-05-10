"""
frontend/api/runner.py — Background pipeline executor + per-run event queues.

When a run is started, we kick the existing reports/ pipeline off in a
worker thread and hook into its on_stage_start / on_stage_end callbacks
to push events into a per-run asyncio queue. The SSE endpoint reads that
queue and streams events to the browser as they happen.
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from datetime import datetime, timezone
from typing import Any, Optional

from frontend.api import db, serialize

log = logging.getLogger("probitas.runner")

# Per-run event queues. Created on stream subscribe, populated by the
# pipeline thread, drained by the SSE handler.
_QUEUES: dict[str, asyncio.Queue] = {}
_QUEUE_LOOP: Optional[asyncio.AbstractEventLoop] = None
_QUEUE_LOCK = threading.Lock()


def set_event_loop(loop: asyncio.AbstractEventLoop) -> None:
    """Called once on FastAPI startup so the worker thread can post events
    into the main asyncio loop's queues."""
    global _QUEUE_LOOP
    _QUEUE_LOOP = loop


def get_queue(run_id: str) -> asyncio.Queue:
    """Get or create the queue for a run. Thread-safe."""
    with _QUEUE_LOCK:
        if run_id not in _QUEUES:
            _QUEUES[run_id] = asyncio.Queue()
        return _QUEUES[run_id]


def _post(run_id: str, event: dict) -> None:
    """Push an event into a run's queue from any thread."""
    if _QUEUE_LOOP is None:
        return
    q = get_queue(run_id)
    asyncio.run_coroutine_threadsafe(q.put(event), _QUEUE_LOOP)


def start_run(
    run_id: str,
    entity_type: str,
    entity_id: str,
    *,
    email: Optional[str] = None,
    bypass_used: bool = False,
    website_url: Optional[str] = None,
) -> None:
    """Insert the run row and kick off the pipeline in a worker thread."""
    db.insert_run(run_id, entity_type, entity_id, email=email, bypass_used=bypass_used)
    t = threading.Thread(
        target=_execute_pipeline,
        args=(run_id, entity_type, entity_id, website_url),
        daemon=True,
        name=f"probitas-run-{run_id[:8]}",
    )
    t.start()


def _execute_pipeline(run_id: str, entity_type: str, entity_id: str,
                      website_url: Optional[str] = None) -> None:
    """Run the existing reports/ pipeline, streaming progress to the queue."""
    from reports import generate_charity_report, generate_company_report

    started = time.time()
    db.update_run_status(run_id, "running")

    def on_start(stage: str, meta: dict) -> None:
        _post(run_id, {
            "type": "stage_start",
            "stage": stage,
            "meta": meta or {},
        })

    def on_end(stage: str, meta: dict, elapsed: float) -> None:
        _post(run_id, {
            "type": "stage_end",
            "stage": stage,
            "elapsed_s": float(elapsed or 0),
            "meta": meta or {},
        })

    def on_rate_limit(label: str, attempt: int, wait: int, status: str) -> None:
        _post(run_id, {
            "type": "log",
            "message": f"⏳ Rate limit on {label} · attempt {attempt} · waiting {wait}s ({status})",
        })

    try:
        # Pull the actual stage list from the pipeline definition so the
        # frontend can render rows that match what will actually run.
        # Source of truth: pipeline/{charity,company}_graph.py.
        stages_payload: list[dict] = []
        try:
            if entity_type == "charity":
                from pipeline.charity_graph import CHARITY_NODES, CHARITY_STAGE_LABELS
                node_names = [n for n, _ in CHARITY_NODES]
                labels = CHARITY_STAGE_LABELS
            elif entity_type == "company":
                from pipeline.company_graph import COMPANY_NODES, COMPANY_STAGE_LABELS
                node_names = [n for n, _ in COMPANY_NODES]
                labels = COMPANY_STAGE_LABELS
            else:
                node_names, labels = [], {}
            for n in node_names:
                meta = labels.get(n, {}) or {}
                stages_payload.append({
                    "key": n,
                    "title": meta.get("title", n.replace("_", " ").title()),
                    "desc": meta.get("desc", ""),
                    "est_time": meta.get("est_time", ""),
                })
        except Exception as ex:
            log.warning(f"Could not introspect pipeline stages for {entity_type}: {ex}")

        # Initial start event for the SSE consumer
        _post(run_id, {
            "type": "start",
            "entity_type": entity_type,
            "entity_id": entity_id,
            "entity_type_label": "Charity " + entity_id if entity_type == "charity" else "Company " + entity_id,
            "stages": stages_payload,
        })

        if entity_type == "charity":
            bundle = generate_charity_report(
                entity_id,
                website_override=website_url or "",
                on_stage_start=on_start,
                on_stage_end=on_end,
                on_rate_limit=on_rate_limit,
                skip_db_log=True,  # we maintain our own runs table here
            )
        elif entity_type == "company":
            bundle = generate_company_report(
                entity_id,
                website_url=website_url or "",
                on_stage_start=on_start,
                on_stage_end=on_end,
                on_rate_limit=on_rate_limit,
                skip_db_log=True,
            )
        else:
            raise ValueError(f"Unknown entity_type: {entity_type}")

        # Persist the bundle to the runs row
        bundle_dict = serialize.bundle_to_dict(bundle)
        # Add a generated_at timestamp the frontend likes
        bundle_dict["generated_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds") + " UTC"

        risk_score = bundle_dict.get("state", {}).get("risk_score", {}) or {}
        cost_info = bundle_dict.get("cost_info", {}) or {}

        db.update_run_status(
            run_id,
            "done",
            entity_name=bundle_dict.get("entity_name") or "",
            bundle=bundle_dict,
            risk_level=str(risk_score.get("overall_level") or ""),
            risk_score=float(risk_score.get("overall_score") or 0),
            cost_usd=float(cost_info.get("cost_usd") or 0),
        )

        total_s = time.time() - started
        _post(run_id, {
            "type": "done",
            "run_id": run_id,
            "entity_type": entity_type,
            "total_s": total_s,
        })

    except Exception as e:
        log.exception(f"Pipeline run {run_id} failed")
        db.update_run_status(run_id, "failed", failed_reason=str(e))
        _post(run_id, {
            "type": "error_event",
            "message": str(e),
            "stage": None,
        })
    finally:
        # Mark the queue with a sentinel so the SSE handler knows to close
        _post(run_id, {"type": "_close"})
