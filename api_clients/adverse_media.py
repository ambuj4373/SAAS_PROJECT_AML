"""
api_clients/adverse_media.py — Intelligence logging bridge.

Provides a thin convenience wrapper that logs AI-generated risk assessments
to the SQLite intelligence database.  Designed to be called from the main
app pipeline immediately after the LLM produces its output.

Public API
----------
log_ai_assessment(entity_name, ai_json_output, **kw)
    → row_id (int) — the database primary key for this assessment.

log_fatf_assessment(entity_name, fatf_screen_dict)
    → row_id (int) — logs a single FATF screening result.
"""

from __future__ import annotations

import json
from typing import Any

from core.database import (
    init_intelligence_db,
    log_ai_assessment as _db_log,
)

# Ensure the database + table exist on first import.
init_intelligence_db()


def log_ai_assessment(
    entity_name: str,
    ai_json_output: dict | str,
    *,
    entity_type: str = "charity",
    assessment_type: str = "full_report",
    risk_level: str = "",
    model_used: str = "",
) -> int:
    """
    Log an AI-generated risk report to the intelligence database.

    Parameters
    ----------
    entity_name      : The charity or trustee name being assessed.
    ai_json_output   : The raw LLM output (string or dict).
    entity_type      : 'charity' | 'trustee' | 'company'.
    assessment_type  : 'full_report' | 'fatf_screen' | 'adverse_media'.
    risk_level       : Overall risk tag from the LLM (e.g. "High").
    model_used       : Which model produced this (e.g. "gpt-4.1-mini").

    Returns
    -------
    int — the database row id of the newly created log entry.
    """
    return _db_log(
        entity_name,
        ai_json_output,
        entity_type=entity_type,
        assessment_type=assessment_type,
        risk_level=risk_level,
        model_used=model_used,
    )


def log_fatf_assessment(
    entity_name: str,
    fatf_screen: dict[str, Any],
    *,
    entity_type: str = "charity",
) -> int:
    """
    Convenience wrapper to log a FATF screening result.

    Extracts risk_level automatically from the screening dict.
    """
    risk = fatf_screen.get("risk_level", "") if fatf_screen else ""
    return _db_log(
        entity_name,
        fatf_screen or {},
        entity_type=entity_type,
        assessment_type="fatf_screen",
        risk_level=risk,
        model_used=fatf_screen.get("cost_info", {}).get("model", "") if fatf_screen else "",
    )
