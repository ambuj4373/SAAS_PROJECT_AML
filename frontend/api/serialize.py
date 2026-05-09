"""
frontend/api/serialize.py — Convert reports/* bundles to JSON for the wire.

The reports.charity / reports.company packages return dataclass + Pydantic
hybrids. This module flattens them into plain dicts the frontend can consume
directly via the contract documented in DESIGN_BRIEF.md §5.
"""

from __future__ import annotations

from dataclasses import asdict, is_dataclass
from typing import Any


def bundle_to_dict(bundle: Any) -> dict:
    """Best-effort flatten any of our report bundles into JSON-safe dicts."""
    if bundle is None:
        return {}

    if is_dataclass(bundle):
        out = asdict(bundle)
    elif hasattr(bundle, "model_dump"):
        out = bundle.model_dump()
    elif isinstance(bundle, dict):
        out = dict(bundle)
    else:
        out = {k: getattr(bundle, k) for k in dir(bundle) if not k.startswith("_")}

    # Pydantic-y nested fields
    for key in ("verification", "structured_report", "narrative_check"):
        v = getattr(bundle, key, None)
        if v is not None:
            if hasattr(v, "model_dump"):
                out[key] = v.model_dump()
            elif hasattr(v, "to_dict"):
                out[key] = v.to_dict()
            elif is_dataclass(v):
                out[key] = asdict(v)

    # Drop binary blobs that snuck in
    if "state" in out and isinstance(out["state"], dict):
        for blob_key in ("cc_printout", "uploaded_docs", "uploaded_gov_docs"):
            out["state"].pop(blob_key, None)

    # asdict() loses @property fields (entity_name, etc.) — re-derive from state
    state = out.get("state") or {}
    if not out.get("entity_name"):
        out["entity_name"] = (
            state.get("entity_name")
            or state.get("charity_data", {}).get("charity_name")
            or state.get("company_data", {}).get("name")
            or ""
        )

    # Coerce non-serialisable leaves
    return _coerce(out)


def _coerce(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {str(k): _coerce(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_coerce(v) for v in obj]
    if isinstance(obj, (str, int, float, bool, type(None))):
        return obj
    if hasattr(obj, "model_dump"):
        return _coerce(obj.model_dump())
    if is_dataclass(obj):
        return _coerce(asdict(obj))
    return str(obj)
