"""
frontend/api/db.py — SQLite storage for runs.

Single file at frontend/data/probitas.db. Three tables:
  runs            one row per pipeline run, includes the JSON bundle
  stripe_events   webhook idempotency (unused in v1, ready for next session)
  access_tokens   signed-link access for emailed reports (unused in v1)
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, Optional

_HERE = Path(__file__).resolve().parent
_DATA_DIR = _HERE.parent / "data"
_DATA_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = _DATA_DIR / "probitas.db"


SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    id                  TEXT PRIMARY KEY,
    entity_type         TEXT NOT NULL,
    entity_id           TEXT NOT NULL,
    entity_name         TEXT,
    email               TEXT,
    bundle_json         TEXT,
    status              TEXT NOT NULL DEFAULT 'pending',
    risk_level          TEXT,
    risk_score          REAL,
    cost_usd            REAL,
    created_at          TEXT NOT NULL,
    completed_at        TEXT,
    failed_reason       TEXT,
    bypass_used         INTEGER NOT NULL DEFAULT 0,
    stripe_session_id   TEXT
);

CREATE INDEX IF NOT EXISTS idx_runs_entity ON runs(entity_type, entity_id);
CREATE INDEX IF NOT EXISTS idx_runs_status ON runs(status);

CREATE TABLE IF NOT EXISTS stripe_events (
    event_id        TEXT PRIMARY KEY,
    processed_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS access_tokens (
    token       TEXT PRIMARY KEY,
    run_id      TEXT NOT NULL REFERENCES runs(id),
    expires_at  TEXT NOT NULL
);
"""


@contextmanager
def connect() -> Iterator[sqlite3.Connection]:
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db() -> None:
    with connect() as c:
        c.executescript(SCHEMA)


def insert_run(
    run_id: str,
    entity_type: str,
    entity_id: str,
    *,
    email: Optional[str] = None,
    bypass_used: bool = False,
    stripe_session_id: Optional[str] = None,
) -> None:
    from datetime import datetime, timezone

    with connect() as c:
        c.execute(
            """
            INSERT INTO runs (id, entity_type, entity_id, status, created_at,
                              email, bypass_used, stripe_session_id)
            VALUES (?, ?, ?, 'pending', ?, ?, ?, ?)
            """,
            (
                run_id, entity_type, entity_id,
                datetime.now(timezone.utc).isoformat(timespec="seconds"),
                email, 1 if bypass_used else 0, stripe_session_id,
            ),
        )


def update_run_status(run_id: str, status: str, **kwargs) -> None:
    cols = ["status = ?"]
    vals: list = [status]
    if "entity_name" in kwargs:
        cols.append("entity_name = ?")
        vals.append(kwargs["entity_name"])
    if "bundle" in kwargs:
        cols.append("bundle_json = ?")
        vals.append(json.dumps(kwargs["bundle"], default=str))
    if "risk_level" in kwargs:
        cols.append("risk_level = ?")
        vals.append(kwargs["risk_level"])
    if "risk_score" in kwargs:
        cols.append("risk_score = ?")
        vals.append(kwargs["risk_score"])
    if "cost_usd" in kwargs:
        cols.append("cost_usd = ?")
        vals.append(kwargs["cost_usd"])
    if "failed_reason" in kwargs:
        cols.append("failed_reason = ?")
        vals.append(kwargs["failed_reason"])
    if status in ("done", "failed"):
        from datetime import datetime, timezone
        cols.append("completed_at = ?")
        vals.append(datetime.now(timezone.utc).isoformat(timespec="seconds"))

    vals.append(run_id)
    with connect() as c:
        c.execute(f"UPDATE runs SET {', '.join(cols)} WHERE id = ?", vals)


def get_run(run_id: str) -> Optional[dict]:
    with connect() as c:
        row = c.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
        if row is None:
            return None
        d = dict(row)
        if d.get("bundle_json"):
            try:
                d["bundle"] = json.loads(d["bundle_json"])
            except Exception:
                d["bundle"] = None
        return d
