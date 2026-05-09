"""
frontend/api/preview_lookup.py — Fast (<2s) entity preview before payment.

Calls the existing api_clients/* lookups directly (NOT the full pipeline)
to give the user enough info to confirm they entered the right number
before parting with £15.

Returns a dict shaped for the preview.html JS consumer:
    entity_name        str
    is_active          bool
    status_meta        str
    tags               list[str]
    rows               list[[label, html_value]]
    people             list[str]
"""

from __future__ import annotations

import logging
from typing import Any

log = logging.getLogger("probitas.preview")


def preview_charity(charity_number: str) -> dict:
    from api_clients.charity_commission import fetch_charity_data

    raw = fetch_charity_data(charity_number) or {}
    if raw.get("error"):
        raise LookupError(str(raw["error"]))
    if not raw.get("charity_name"):
        raise LookupError(f"Charity {charity_number} not found on the Charity Commission register")

    name = raw.get("charity_name") or "Unknown"
    reg_date_iso = raw.get("date_of_registration") or ""
    reg_date_display = _format_iso_date(reg_date_iso)
    in_admin = bool(raw.get("in_administration"))
    removed = bool(raw.get("date_of_removal"))
    is_active = not in_admin and not removed

    if removed:
        status_label = "Removed from the register"
    elif in_admin:
        status_label = "In administration"
    else:
        status_label = "Registered & active"

    address = raw.get("address") or "—"

    trustees = raw.get("trustees") or []
    if isinstance(trustees, list):
        people = [t if isinstance(t, str) else (t.get("name", "") if isinstance(t, dict) else str(t))
                  for t in trustees]
    else:
        people = []
    people = [p for p in people if p][:8]
    if len(trustees) > 8:
        people.append(f"+ {len(trustees) - 8} more")

    income = raw.get("latest_income") or 0
    expenditure = raw.get("latest_expenditure") or 0
    employees = raw.get("employees") or 0

    rows = [
        ["Registered name", _esc(name)],
        ["Charity number", f'<span class="mono">{_esc(charity_number)}</span>'],
        ["Registration date", _esc(reg_date_display) or "—"],
        ["Status", _esc(status_label)],
        ["Registered address", _esc(address)],
        ["Trustees on file", f'{len(trustees)} on register'],
    ]
    if income:
        rows.append(["Latest income",
                     f'<span class="mono">£{int(income):,}</span>'])
    if expenditure:
        rows.append(["Latest expenditure",
                     f'<span class="mono">£{int(expenditure):,}</span>'])
    if employees:
        rows.append(["Employees", f'<span class="mono">{int(employees):,}</span>'])
    if raw.get("activities"):
        rows.append(["Stated activities", _truncate(_esc(raw["activities"]), 240)])
    if raw.get("countries"):
        cs = raw["countries"]
        if isinstance(cs, list):
            rows.append(["Countries of operation", _esc(", ".join(str(c) for c in cs[:8]))])

    tags = []
    if raw.get("charity_type"):
        tags.append(str(raw["charity_type"]))
    if raw.get("cio_ind"):
        tags.append("CIO")
    if raw.get("company_number"):
        tags.append(f"CH {raw['company_number']}")

    return {
        "entity_name": name,
        "is_active": is_active,
        "status_meta": (f"Registered {reg_date_display}" if reg_date_display
                        else "Registered with the Charity Commission"),
        "tags": tags,
        "rows": rows,
        "people": people,
    }


def preview_company(company_number: str) -> dict:
    from api_clients.companies_house import fetch_ch_data

    raw = fetch_ch_data(company_number) or {}
    if not raw.get("name"):
        raise LookupError(f"Company {company_number} not found on Companies House")

    name = raw.get("name") or "Unknown"
    inc_date = raw.get("date_of_creation") or ""
    inc_date_display = _format_iso_date(inc_date)
    status = (raw.get("status") or "active").lower()
    is_active = status in ("active",)

    addr = raw.get("registered_office") or {}
    if isinstance(addr, dict):
        address = ", ".join(str(v) for v in [
            addr.get("address_line_1"), addr.get("address_line_2"),
            addr.get("locality"), addr.get("region"),
            addr.get("postal_code"),
        ] if v)
    else:
        address = str(addr or "—")

    sic = raw.get("sic_codes") or []
    sic_display = ", ".join(str(s) for s in sic[:3])

    officers = raw.get("officers") or []
    officer_names = raw.get("officer_names") or []
    if officer_names:
        people = [n for n in officer_names if n][:6]
    elif officers:
        people = [o.get("name", "") if isinstance(o, dict) else str(o)
                  for o in officers if o][:6]
    else:
        people = []

    rows = [
        ["Registered name", _esc(name)],
        ["Company number", f'<span class="mono">{_esc(company_number)}</span>'],
        ["Incorporation", _esc(inc_date_display) or "—"],
        ["Status", _esc(status.title())],
        ["Registered office", _esc(address) or "—"],
        ["Officers on file", f'{len(officers)} on register'],
    ]
    if sic_display:
        rows.append(["SIC code", f'<span class="mono">{_esc(sic_display)}</span>'])
    if raw.get("type"):
        rows.append(["Legal form", _esc(str(raw["type"]).replace("-", " ").title())])

    tags = [_esc(status.title())]
    if raw.get("type"):
        tags.append(str(raw["type"]).replace("-", " ").title())

    return {
        "entity_name": name,
        "is_active": is_active,
        "status_meta": (f"Incorporated {inc_date_display}" if inc_date_display
                        else "Registered at Companies House"),
        "tags": tags,
        "rows": rows,
        "people": people,
    }


def _format_iso_date(s: str) -> str:
    """'1963-03-21T00:00:00' -> '21 March 1963'."""
    if not s:
        return ""
    try:
        from datetime import datetime
        d = datetime.fromisoformat(s.split(".")[0].rstrip("Z"))
        return d.strftime("%-d %B %Y") if hasattr(d, "strftime") else str(d.date())
    except Exception:
        return s.split("T")[0] if "T" in s else s


def _esc(s: Any) -> str:
    if s is None:
        return ""
    return (str(s).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


def _truncate(s: str, n: int) -> str:
    return s if len(s) <= n else s[: n - 1] + "…"
