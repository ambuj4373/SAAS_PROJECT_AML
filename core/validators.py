"""
core/validators.py — Data validation and resilience layer for V3.

Provides safe data extraction utilities that gracefully handle missing fields,
inconsistent API responses, null values, and type mismatches. All functions
return sensible defaults rather than raising exceptions.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any


# ═══════════════════════════════════════════════════════════════════════════════
# SAFE EXTRACTION HELPERS
# ═══════════════════════════════════════════════════════════════════════════════

def safe_get(d: dict | None, *keys: str, default: Any = None) -> Any:
    """Safely traverse nested dicts. ``safe_get(d, 'a', 'b', 'c')`` returns
    ``d['a']['b']['c']`` or *default* if any key is missing or d is None."""
    current = d
    for key in keys:
        if not isinstance(current, dict):
            return default
        current = current.get(key)
        if current is None:
            return default
    return current


def safe_int(val: Any, default: int = 0) -> int:
    """Convert value to int, returning *default* on any failure."""
    if val is None:
        return default
    try:
        if isinstance(val, str):
            val = val.strip().replace(",", "").replace("£", "").replace("$", "")
        return int(float(val))
    except (ValueError, TypeError):
        return default


def safe_float(val: Any, default: float = 0.0) -> float:
    """Convert value to float, returning *default* on any failure."""
    if val is None:
        return default
    try:
        if isinstance(val, str):
            val = val.strip().replace(",", "").replace("£", "").replace("$", "")
            # Handle CC-style amounts: "447.45k", "1.2m"
            val_lower = val.lower()
            if val_lower.endswith("k"):
                return float(val_lower[:-1]) * 1_000
            if val_lower.endswith("m"):
                return float(val_lower[:-1]) * 1_000_000
        return float(val)
    except (ValueError, TypeError):
        return default


def safe_str(val: Any, default: str = "") -> str:
    """Convert value to string, returning *default* on None."""
    if val is None:
        return default
    return str(val).strip()


def safe_list(val: Any) -> list:
    """Ensure a value is a list. Wraps non-lists, returns [] for None."""
    if val is None:
        return []
    if isinstance(val, list):
        return val
    return [val]


def safe_dict(val: Any) -> dict:
    """Ensure a value is a dict. Returns {} for non-dicts."""
    if isinstance(val, dict):
        return val
    return {}


# ═══════════════════════════════════════════════════════════════════════════════
# DATE HELPERS
# ═══════════════════════════════════════════════════════════════════════════════

_DATE_FORMATS = (
    "%Y-%m-%dT%H:%M:%S",
    "%Y-%m-%dT%H:%M:%S.%f",
    "%Y-%m-%dT%H:%M:%SZ",
    "%Y-%m-%d",
    "%d/%m/%Y",
    "%d %B %Y",
    "%d %b %Y",
    "%B %d, %Y",
)


def safe_parse_date(val: Any) -> datetime | None:
    """Parse a date string into datetime, trying multiple formats.
    Returns None on failure."""
    if val is None:
        return None
    s = str(val).strip()[:26]  # trim to reasonable length
    if not s:
        return None
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    return None


def safe_date_str(val: Any, fmt: str = "%d %B %Y", default: str = "N/A") -> str:
    """Parse a date and format it for display, returning *default* on failure."""
    dt = safe_parse_date(val)
    if dt:
        return dt.strftime(fmt)
    return default


def years_since(date_str: Any) -> int | None:
    """Return number of years since a given date, or None."""
    dt = safe_parse_date(date_str)
    if dt:
        return (datetime.now() - dt).days // 365
    return None


# ═══════════════════════════════════════════════════════════════════════════════
# FINANCIAL VALIDATION
# ═══════════════════════════════════════════════════════════════════════════════

def validate_financial_history(history: list[dict] | None) -> list[dict]:
    """Clean and validate a financial history list.

    - Removes entries with no income AND no expenditure
    - Ensures all numeric fields are proper numbers
    - Sorts by year ascending
    - De-duplicates by year
    """
    if not history:
        return []

    cleaned = []
    seen_years = set()

    for rec in history:
        if not isinstance(rec, dict):
            continue
        year = safe_str(rec.get("year"), "")
        income = safe_float(rec.get("income"))
        expenditure = safe_float(rec.get("expenditure"))

        if not year or (income == 0 and expenditure == 0):
            continue
        if year in seen_years:
            continue
        seen_years.add(year)
        cleaned.append({
            "year": year,
            "income": income,
            "expenditure": expenditure,
        })

    return sorted(cleaned, key=lambda r: r["year"])


def validate_trustees(trustees: list | None, max_count: int = 200) -> list[str]:
    """Validate and cap a trustee/director list.

    - Removes empty/whitespace-only names
    - De-duplicates (case-insensitive)
    - Caps at max_count to prevent runaway processing
    """
    if not trustees:
        return []

    seen = set()
    result = []
    for t in trustees:
        name = safe_str(t).strip()
        if not name:
            continue
        key = name.lower()
        if key in seen:
            continue
        seen.add(key)
        result.append(name)
        if len(result) >= max_count:
            break
    return result


# ═══════════════════════════════════════════════════════════════════════════════
# API RESPONSE VALIDATION
# ═══════════════════════════════════════════════════════════════════════════════

def validate_api_response(data: Any, expected_type: type = dict,
                          required_keys: list[str] | None = None) -> tuple[bool, str]:
    """Validate an API response meets basic expectations.

    Returns (is_valid, error_message).
    """
    if data is None:
        return False, "Response is None"
    if not isinstance(data, expected_type):
        return False, f"Expected {expected_type.__name__}, got {type(data).__name__}"
    if required_keys and isinstance(data, dict):
        missing = [k for k in required_keys if k not in data]
        if missing:
            return False, f"Missing required keys: {', '.join(missing)}"
    return True, ""


def validate_search_results(results: list | None) -> list[dict]:
    """Validate search results, removing errors and malformed entries."""
    if not results:
        return []
    valid = []
    for r in results:
        if not isinstance(r, dict):
            continue
        if r.get("_error"):
            continue
        if not r.get("title") and not r.get("content"):
            continue
        valid.append(r)
    return valid


# ═══════════════════════════════════════════════════════════════════════════════
# CONTENT CLEANING
# ═══════════════════════════════════════════════════════════════════════════════

def clean_text(text: str | None, max_chars: int = 50000) -> str:
    """Clean extracted text: strip whitespace, limit size, remove null bytes."""
    if not text:
        return ""
    text = text.replace("\x00", "").strip()
    # Collapse multiple newlines to doubles
    text = re.sub(r"\n{3,}", "\n\n", text)
    # Collapse multiple spaces (but not newlines)
    text = re.sub(r"[^\S\n]{2,}", " ", text)
    return text[:max_chars]


def compact(obj: Any) -> Any:
    """Recursively strip None, empty strings, empty lists, empty dicts
    to reduce LLM token consumption."""
    if isinstance(obj, dict):
        return {
            k: compact(v) for k, v in obj.items()
            if v is not None
            and v != ""
            and v != []
            and v != {}
        }
    if isinstance(obj, list):
        return [compact(item) for item in obj if item is not None]
    return obj


def slim_search(results: list[dict], max_items: int = 5,
                max_chars: int = 400) -> list[dict]:
    """Trim search results to essential fields for LLM prompt injection."""
    out = []
    for r in results[:max_items]:
        entry = {
            "title": (r.get("title") or "")[:120],
            "url": r.get("url", ""),
        }
        content = r.get("content") or ""
        if content:
            entry["snippet"] = content[:max_chars]
        relevant = r.get("_relevant")
        if relevant is not None:
            entry["relevant"] = relevant
        out.append(entry)
    return out
