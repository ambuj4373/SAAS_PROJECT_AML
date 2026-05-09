"""
frontend/api/main.py — Probitas FastAPI bridge.

Serves the static `public/` HTML and exposes the API routes the frontend
talks to:

    GET  /api/preview/{type}/{id}      Free entity preview (registry data only)
    POST /api/runs/start               Kick off a pipeline run (admin bypass for now)
    GET  /api/runs/{run_id}            Fetch the completed run bundle as JSON
    GET  /api/runs/{run_id}/stream     SSE stream of pipeline stage events
    GET  /api/health                   Liveness check

Admin bypass: set PROBITAS_ADMIN_CODE in the project root .env. The
preview page exposes a "Have an admin code?" link that POSTs the code
to /api/runs/start with bypass_code; if it matches, the run is created
free of charge and Stripe is skipped.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import secrets
import sys
from pathlib import Path

# Make project root importable when uvicorn is invoked from anywhere
_HERE = Path(__file__).resolve().parent
_ROOT = _HERE.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

# Load .env BEFORE anything else imports config
try:
    from dotenv import load_dotenv
    load_dotenv(_ROOT / ".env")
except ImportError:
    pass  # python-dotenv optional

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from frontend.api import db, preview_lookup, runner

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] %(levelname)s %(name)s · %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("probitas.api")

PUBLIC_DIR = _HERE.parent / "public"
ADMIN_CODE_ENV = "PROBITAS_ADMIN_CODE"


app = FastAPI(title="Probitas", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # frontend served from same origin in dev
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
async def _startup() -> None:
    db.init_db()
    runner.set_event_loop(asyncio.get_event_loop())
    code = os.getenv(ADMIN_CODE_ENV)
    if code:
        log.info("Admin bypass code is set (length=%d). Use it on /preview to skip Stripe.", len(code))
    else:
        log.warning(
            "No %s set in .env — paid runs will fail until Stripe is wired. "
            "Add a line like: %s=changeme-something-random",
            ADMIN_CODE_ENV, ADMIN_CODE_ENV,
        )


# ─── API: preview ─────────────────────────────────────────────────────────────


@app.get("/api/preview/{entity_type}/{entity_id}")
def get_preview(entity_type: str, entity_id: str) -> dict:
    entity_type = entity_type.lower()
    entity_id = entity_id.strip()
    if not entity_id:
        raise HTTPException(400, detail="entity_id is required")

    try:
        if entity_type == "charity":
            return preview_lookup.preview_charity(entity_id)
        if entity_type == "company":
            return preview_lookup.preview_company(entity_id)
        raise HTTPException(400, detail=f"Unknown entity_type: {entity_type}")
    except LookupError as e:
        raise HTTPException(404, detail=str(e))
    except HTTPException:
        raise
    except Exception as e:
        log.exception("Preview lookup failed")
        raise HTTPException(502, detail=f"Registry lookup failed: {e}")


# ─── API: runs ────────────────────────────────────────────────────────────────


class StartRunBody(BaseModel):
    entity_type: str
    entity_id: str
    bypass_code: str | None = None
    email: str | None = None


@app.post("/api/runs/start")
def start_run(body: StartRunBody) -> dict:
    expected = os.getenv(ADMIN_CODE_ENV)

    # v1 path: admin bypass only (Stripe wired in next session)
    if not body.bypass_code:
        raise HTTPException(
            402,
            detail="Payment not yet wired in this session. Use the admin "
                   "code via the 'Have an admin code?' link on the preview page.",
        )
    if not expected:
        raise HTTPException(
            500,
            detail=f"No {ADMIN_CODE_ENV} configured on the server — set one in .env.",
        )
    if not secrets.compare_digest(body.bypass_code, expected):
        raise HTTPException(401, detail="Invalid admin code")

    entity_type = body.entity_type.lower()
    if entity_type not in ("charity", "company"):
        raise HTTPException(400, detail="entity_type must be 'charity' or 'company'")
    entity_id = body.entity_id.strip()
    if not entity_id:
        raise HTTPException(400, detail="entity_id is required")

    run_id = "r_" + secrets.token_urlsafe(8).replace("-", "").replace("_", "").lower()[:12]
    runner.start_run(
        run_id, entity_type, entity_id,
        email=body.email, bypass_used=True,
    )
    log.info("Started run %s · %s/%s · bypass", run_id, entity_type, entity_id)
    return {"run_id": run_id, "status": "pending"}


@app.get("/api/runs/{run_id}")
def get_run(run_id: str) -> dict:
    row = db.get_run(run_id)
    if row is None:
        raise HTTPException(404, detail=f"Run {run_id} not found")
    if row["status"] == "failed":
        raise HTTPException(500, detail=f"Run failed: {row.get('failed_reason') or 'unknown'}")
    if row["status"] != "done":
        raise HTTPException(425, detail=f"Run still {row['status']}")
    bundle = row.get("bundle") or {}
    return bundle


@app.get("/api/runs/{run_id}/stream")
async def stream_run(run_id: str, request: Request) -> StreamingResponse:
    """SSE: stream pipeline events as they happen.

    Event types pushed (matching progress.html consumer):
      - start         {entity_type, entity_id, entity_name, entity_type_label}
      - stage_start   {stage, meta}
      - stage_end     {stage, elapsed_s, meta}
      - log           {message}
      - done          {run_id, entity_type, total_s}
      - error_event   {message, stage}
    """

    row = db.get_run(run_id)
    if row is None:
        raise HTTPException(404, detail=f"Run {run_id} not found")

    queue = runner.get_queue(run_id)

    async def event_gen():
        # If the run has already finished by the time we connect, replay a final
        # done event so the page can redirect.
        fresh = db.get_run(run_id)
        if fresh and fresh["status"] == "done":
            yield _sse_format("done", {
                "run_id": run_id,
                "entity_type": fresh["entity_type"],
                "total_s": 0,
            })
            return
        if fresh and fresh["status"] == "failed":
            yield _sse_format("error_event", {
                "message": fresh.get("failed_reason") or "Unknown failure",
                "stage": None,
            })
            return

        # Comment line so proxies don't time out before first event
        yield ": connected\n\n"

        while True:
            if await request.is_disconnected():
                return
            try:
                event = await asyncio.wait_for(queue.get(), timeout=15.0)
            except asyncio.TimeoutError:
                # heartbeat keeps the connection alive
                yield ": heartbeat\n\n"
                continue

            if event.get("type") == "_close":
                return

            etype = event.pop("type", "log")
            yield _sse_format(etype, event)

    return StreamingResponse(
        event_gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",  # disable nginx proxy buffering if any
        },
    )


def _sse_format(event_name: str, data: dict) -> str:
    payload = json.dumps(data, default=str)
    return f"event: {event_name}\ndata: {payload}\n\n"


# ─── Health check ─────────────────────────────────────────────────────────────


@app.get("/api/health")
def health() -> dict:
    return {
        "ok": True,
        "admin_bypass_configured": bool(os.getenv(ADMIN_CODE_ENV)),
        "db_path": str(db.DB_PATH),
    }


# ─── Static frontend ──────────────────────────────────────────────────────────


@app.get("/")
async def serve_index():
    return FileResponse(PUBLIC_DIR / "index.html")


# Mount static last so /api/* takes precedence
app.mount("/", StaticFiles(directory=str(PUBLIC_DIR), html=True), name="static")


# ─── Direct-run entrypoint ────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PROBITAS_PORT", "8000"))
    uvicorn.run("frontend.api.main:app", host="0.0.0.0", port=port, reload=False)
