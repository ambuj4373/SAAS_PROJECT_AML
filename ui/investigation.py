"""
ui/investigation.py — Investigation-style drill-down UI components.

Provides interactive expandable panels for exploring trustees, related
organisations, jurisdictions, and other entities directly from the dashboard.

Components render as styled Streamlit expanders / cards that allow the analyst
to dig deeper into specific areas without leaving the page.

Public API:
    render_trustee_drilldown(trustees, adverse, fatf, appointments)
    render_company_officer_drilldown(directors, pscs)
    render_jurisdiction_drilldown(country_risk)
    render_related_entities_panel(overlaps, similarities)
    render_investigation_hub(data_dict)     — master panel for charity
    render_company_investigation_hub(data)  — master panel for company
"""

from __future__ import annotations

from typing import Any


def _esc(text: str) -> str:
    """Basic HTML escaping."""
    return str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


# ═══════════════════════════════════════════════════════════════════════════════
# TRUSTEE DRILL-DOWN
# ═══════════════════════════════════════════════════════════════════════════════

def render_trustee_drilldown(
    trustees: list[str],
    adverse_trustees: dict[str, list[dict]] | None = None,
    fatf_trustee_screens: dict[str, dict] | None = None,
    trustee_appointments: dict[str, list[dict]] | None = None,
    structural_governance: dict | None = None,
) -> str:
    """Render interactive trustee investigation cards."""
    if not trustees:
        return "<p style='color:#666;font-size:13px;'>No trustee data available.</p>"

    adverse_trustees = adverse_trustees or {}
    fatf_trustee_screens = fatf_trustee_screens or {}
    trustee_appointments = trustee_appointments or {}
    directorships = (structural_governance or {}).get("trustee_directorships", {})

    cards = ""
    for i, name in enumerate(trustees, 1):
        # Adverse media summary
        adv_results = adverse_trustees.get(name, [])
        adv_count = len([r for r in adv_results if r.get("is_relevant") or r.get("title")])
        adv_badge = f'<span style="background:#dc3545;color:#fff;padding:2px 8px;border-radius:10px;font-size:10px;">{adv_count} adverse</span>' if adv_count > 0 else '<span style="background:#28a745;color:#fff;padding:2px 8px;border-radius:10px;font-size:10px;">Clear</span>'

        # FATF screening
        fatf = fatf_trustee_screens.get(name, {})
        fatf_risk = fatf.get("risk_level", "Unknown")
        fatf_color = {"High": "#dc3545", "Medium": "#ffc107", "Low": "#28a745"}.get(fatf_risk, "#6c757d")
        fatf_badge = f'<span style="color:{fatf_color};font-size:11px;font-weight:600;">FATF: {fatf_risk}</span>'

        # Appointments
        appts = trustee_appointments.get(name, [])
        dir_info = directorships.get(name, {})
        dir_count = dir_info.get("count", len(appts))

        # Build appointment list
        appt_html = ""
        if dir_info.get("entities"):
            appt_rows = ""
            for ent in dir_info["entities"][:8]:
                co_name = _esc(ent.get("company_name", ""))
                co_num = ent.get("company_number", "")
                co_status = ent.get("company_status", "")
                role = ent.get("officer_role", "")
                status_color = "#28a745" if "active" in co_status.lower() else "#dc3545"
                ch_link = f"https://find-and-update.company-information.service.gov.uk/company/{co_num}" if co_num else "#"
                appt_rows += f"""
                <tr style="border-bottom:1px solid #f0f0f0;">
                    <td style="padding:3px 6px;font-size:11px;"><a href="{ch_link}" target="_blank">{co_name[:50]}</a></td>
                    <td style="padding:3px 6px;font-size:11px;">{role}</td>
                    <td style="padding:3px 6px;font-size:11px;color:{status_color};">{co_status}</td>
                </tr>"""
            appt_html = f"""
            <div style="margin-top:6px;">
                <div style="font-size:11px;font-weight:600;color:#444;">Other Appointments ({dir_count}):</div>
                <table style="width:100%;border-collapse:collapse;margin-top:2px;"><tbody>{appt_rows}</tbody></table>
            </div>"""

        # Adverse detail
        adv_html = ""
        if adv_count > 0:
            adv_items = ""
            for r in adv_results[:5]:
                title = _esc(r.get("title", "Untitled")[:80])
                url = r.get("url", "#")
                adv_items += f'<li style="font-size:11px;"><a href="{url}" target="_blank">{title}</a></li>'
            adv_html = f'<div style="margin-top:6px;"><div style="font-size:11px;font-weight:600;color:#dc3545;">Adverse Media:</div><ul style="margin:2px 0;padding-left:16px;">{adv_items}</ul></div>'

        # Google News link
        from urllib.parse import quote_plus
        google_link = f"https://news.google.com/search?q={quote_plus(name)}"

        cards += f"""
        <details style="margin-bottom:8px;border:1px solid #e0e0e0;border-radius:8px;overflow:hidden;">
            <summary style="padding:10px 14px;cursor:pointer;background:#fafafa;
                            display:flex;align-items:center;gap:10px;font-size:13px;">
                <span style="font-weight:600;color:#333;">👤 {_esc(name)}</span>
                {adv_badge} {fatf_badge}
                <span style="font-size:11px;color:#666;margin-left:auto;">
                    {dir_count} appointment(s) · <a href="{google_link}" target="_blank" style="color:#1a73e8;">Google News ↗</a>
                </span>
            </summary>
            <div style="padding:10px 14px;background:#fff;">
                {appt_html}
                {adv_html}
            </div>
        </details>
        """

    return f"""
    <div style="margin:8px 0;">
        <div style="font-size:14px;font-weight:600;margin-bottom:8px;color:#333;">
            🔍 Trustee Investigation Panel ({len(trustees)} trustees)
        </div>
        {cards}
    </div>
    """


# ═══════════════════════════════════════════════════════════════════════════════
# COMPANY OFFICER DRILL-DOWN
# ═══════════════════════════════════════════════════════════════════════════════

def render_company_officer_drilldown(
    directors: list[dict[str, Any]],
    pscs: list[dict[str, Any]] | None = None,
) -> str:
    """Render interactive officer investigation cards for company mode."""
    if not directors:
        return "<p style='color:#666;font-size:13px;'>No officer data available.</p>"

    cards = ""
    for d in directors:
        name = _esc(d.get("name", "Unknown"))
        role = d.get("role", "")
        nationality = d.get("nationality", "Unknown")
        age = d.get("approx_age", "?")
        flags = d.get("flags", [])
        other_appts = d.get("other_active_appointments", 0)
        dissolved = d.get("dissolved_companies", 0)
        officer_id = d.get("officer_id", "")

        flag_badges = ""
        for f in flags[:3]:
            flag_badges += f'<span style="background:#fff3cd;color:#856404;padding:2px 6px;border-radius:4px;font-size:10px;margin-right:4px;">{_esc(f)}</span>'

        ch_link = f"https://find-and-update.company-information.service.gov.uk/officers/{officer_id}/appointments" if officer_id else "#"

        appt_detail = ""
        for appt in (d.get("other_appointments_detail") or [])[:6]:
            co_name = _esc(appt.get("company_name", "")[:50])
            co_status = appt.get("company_status", "")
            appt_role = appt.get("officer_role", "")
            status_color = "#28a745" if "active" in co_status.lower() else "#dc3545"
            appt_detail += f'<div style="font-size:11px;padding:2px 0;"><span style="color:{status_color};">●</span> {co_name} — {appt_role}</div>'

        google_link = f"https://news.google.com/search?q={_esc(name).replace(' ', '+')}"

        cards += f"""
        <details style="margin-bottom:6px;border:1px solid #e0e0e0;border-radius:8px;overflow:hidden;">
            <summary style="padding:8px 12px;cursor:pointer;background:#fafafa;font-size:13px;
                            display:flex;align-items:center;gap:8px;flex-wrap:wrap;">
                <span style="font-weight:600;">👤 {name}</span>
                <span style="font-size:11px;color:#666;">{role} · {nationality} · Age ~{age}</span>
                {flag_badges}
                <span style="font-size:11px;color:#666;margin-left:auto;">
                    {other_appts} appts · {dissolved} dissolved ·
                    <a href="{ch_link}" target="_blank" style="color:#1a73e8;">CH ↗</a> ·
                    <a href="{google_link}" target="_blank" style="color:#1a73e8;">News ↗</a>
                </span>
            </summary>
            <div style="padding:8px 12px;background:#fff;">
                {f'<div style="margin-bottom:6px;"><strong style="font-size:11px;">Other Appointments:</strong>{appt_detail}</div>' if appt_detail else '<div style="font-size:11px;color:#888;">No other active appointments.</div>'}
            </div>
        </details>
        """

    # PSCs section
    psc_html = ""
    if pscs:
        psc_cards = ""
        for p in pscs:
            pname = _esc(p.get("name", "Unknown"))
            nat = p.get("nationality", "?")
            ownership = p.get("ownership_band", "Unknown")
            kind = p.get("kind", "")
            natures = ", ".join(p.get("natures_of_control", [])[:3])
            pflags = p.get("flags", [])
            ceased = p.get("ceased", False)

            if ceased:
                continue

            pflag_html = ""
            for f in pflags[:2]:
                pflag_html += f'<span style="background:#fff3cd;color:#856404;padding:1px 5px;border-radius:3px;font-size:10px;">{_esc(f)}</span> '

            psc_cards += f"""
            <div style="padding:6px 10px;border-bottom:1px solid #f0f0f0;font-size:12px;">
                <strong>{pname}</strong>
                <span style="color:#666;">· {nat} · {ownership} · {kind}</span>
                {pflag_html}
                <div style="font-size:10px;color:#888;">{natures}</div>
            </div>
            """
        if psc_cards:
            psc_html = f"""
            <div style="margin-top:12px;border:1px solid #e0e0e0;border-radius:8px;overflow:hidden;">
                <div style="padding:8px 12px;background:#f5f5f5;font-size:12px;font-weight:600;">
                    🏢 Persons with Significant Control (PSCs)
                </div>
                {psc_cards}
            </div>
            """

    return f"""
    <div style="margin:8px 0;">
        <div style="font-size:14px;font-weight:600;margin-bottom:8px;">🔍 Officer Investigation Panel</div>
        {cards}
        {psc_html}
    </div>
    """


# ═══════════════════════════════════════════════════════════════════════════════
# JURISDICTION DRILL-DOWN
# ═══════════════════════════════════════════════════════════════════════════════

def render_jurisdiction_drilldown(
    country_risk_classified: list[dict[str, Any]],
) -> str:
    """Interactive jurisdiction investigation panel."""
    if not country_risk_classified:
        return "<p style='font-size:13px;color:#666;'>No geographic data available.</p>"

    color_map = {
        "High": "#dc3545", "Very High": "#dc3545",
        "Medium": "#ffc107", "Low": "#28a745",
        "Unknown": "#6c757d",
    }

    cards = ""
    for c in sorted(country_risk_classified, key=lambda x: {"Very High": 0, "High": 1, "Medium": 2, "Low": 3}.get(x.get("risk_level", "Unknown"), 4)):
        country = _esc(c.get("country", "Unknown"))
        risk = c.get("risk_level", "Unknown")
        context = _esc(c.get("context", ""))
        continent = c.get("continent", "")
        color = color_map.get(risk, "#6c757d")

        # Google search link for country + entity context
        search_link = f"https://www.google.com/search?q={country.replace(' ', '+')}+charity+regulation+risk"

        cards += f"""
        <details style="margin-bottom:4px;border-left:3px solid {color};padding-left:0;">
            <summary style="padding:6px 10px;cursor:pointer;font-size:12px;display:flex;align-items:center;gap:8px;">
                <span style="font-weight:600;color:{color};">{risk}</span>
                <span style="font-weight:600;">{country}</span>
                <span style="font-size:11px;color:#888;">{continent}</span>
                <a href="{search_link}" target="_blank" style="font-size:10px;color:#1a73e8;margin-left:auto;">Research ↗</a>
            </summary>
            <div style="padding:6px 10px;font-size:11px;color:#555;background:#fafafa;">
                {context if context else 'No additional context available.'}
            </div>
        </details>
        """

    high_count = len([c for c in country_risk_classified if c.get("risk_level") in ("High", "Very High")])
    return f"""
    <div style="margin:8px 0;">
        <div style="font-size:14px;font-weight:600;margin-bottom:8px;">
            🌍 Jurisdiction Investigation ({len(country_risk_classified)} countries
            {f' · <span style="color:#dc3545;">{high_count} high-risk</span>' if high_count else ''})
        </div>
        {cards}
    </div>
    """


# ═══════════════════════════════════════════════════════════════════════════════
# MASTER INVESTIGATION HUB
# ═══════════════════════════════════════════════════════════════════════════════

def render_investigation_hub_html(
    trustees: list[str] | None = None,
    adverse_trustees: dict | None = None,
    fatf_trustee_screens: dict | None = None,
    trustee_appointments: dict | None = None,
    structural_governance: dict | None = None,
    country_risk_classified: list[dict] | None = None,
) -> dict[str, str]:
    """Generate all investigation HTML components as a dict.

    Returns dict with keys: 'trustees', 'jurisdictions'
    """
    return {
        "trustees": render_trustee_drilldown(
            trustees or [], adverse_trustees, fatf_trustee_screens,
            trustee_appointments, structural_governance,
        ),
        "jurisdictions": render_jurisdiction_drilldown(country_risk_classified or []),
    }
