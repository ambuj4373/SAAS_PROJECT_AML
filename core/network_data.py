"""
core/network_data.py — Build a frontend-ready ownership + director network.

The existing pipeline produces a Graphviz DOT string (network_graph_dot),
which renders to a static SVG. The buyer wants something interactive —
drag, pan, zoom, click to expand. So we also emit a {nodes, edges} JSON
that the frontend can hand to vis-network for a true network diagram.

Public API
----------
    build_company_network_json(company_check: dict) -> dict
        Returns { "nodes": [...], "edges": [...], "meta": {...} }

Node groups (each gets distinct styling in the frontend):
    - subject        the company being checked (centre)
    - ubo            ultimate beneficial owner
    - psc            person of significant control (live)
    - psc_ceased     historical PSC
    - director       active director
    - director_dissolved   director also linked to dissolved companies
    - other_company  another UK Ltd a director also sits on the board of
    - other_company_dissolved   …and that company is dissolved
"""

from __future__ import annotations

import re
from typing import Any


# ─── ID helpers ──────────────────────────────────────────────────────────────


_ID_SAFE = re.compile(r"[^a-z0-9]+")


def _slugify(s: str) -> str:
    out = _ID_SAFE.sub("_", (s or "").strip().lower())
    return out.strip("_") or "x"


# ─── Builder ─────────────────────────────────────────────────────────────────


def build_company_network_json(check: dict) -> dict:
    """Translate the company_check sub-tree into a vis-network nodes/edges dict.

    Tolerant of missing fields: if ubo_chain or director_analysis are
    empty, we still return a structurally-valid graph (just smaller).
    """
    if not isinstance(check, dict) or not check:
        return {"nodes": [], "edges": [], "meta": {"reason": "no data"}}

    company_name = check.get("company_name") or "Subject"
    company_number = check.get("company_number") or ""
    subject_id = "subject_" + _slugify(company_number or company_name)

    nodes: list[dict] = [{
        "id": subject_id,
        "label": _wrap(company_name) + (f"\n({company_number})" if company_number else ""),
        "group": "subject",
        "title": f"{company_name}<br/>Companies House: {company_number}",
        "level": 0,
    }]
    edges: list[dict] = []
    seen_ids = {subject_id}

    # ── PSCs / UBOs ──────────────────────────────────────────────────────
    psc_block = check.get("psc_summary") or {}
    pscs = (psc_block.get("active") or psc_block.get("active_pscs") or [])
    if not pscs:
        # Fallback to top-level pscs
        pscs = check.get("pscs") or []

    ubo_chain = check.get("ubo_chain") or {}
    chain_layers = ubo_chain.get("chain") or []

    # If we have a real UBO chain, walk it (each layer becomes a node up
    # the tree). The subject is at level 0; UBOs higher up.
    if chain_layers:
        for i, layer in enumerate(chain_layers, start=1):
            l_name = (
                layer.get("name")
                or layer.get("ubo_name")
                or layer.get("entity_name")
                or "Unknown owner"
            )
            l_type = layer.get("entity_type") or layer.get("type") or ""
            node_id = _unique(seen_ids, "ubo_" + _slugify(l_name))
            label = _wrap(l_name)
            if l_type:
                label += f"\n({l_type})"
            nodes.append({
                "id": node_id,
                "label": label,
                "group": "ubo",
                "title": _ubo_tooltip(layer),
                "level": -i,  # levels go up = parents
            })
            edges.append({
                "from": node_id,
                "to": chain_layers[i - 1].get("_node_id") if i > 1 and "_node_id" in chain_layers[i - 1] else subject_id,
                "label": _percent_label(layer),
                "arrows": "to",
                "color": {"color": "#a78bfa"},
                "width": 2,
            })
            layer["_node_id"] = node_id  # remember for next layer

    else:
        # No UBO chain — fall back to direct PSC list
        for psc in pscs:
            if not isinstance(psc, dict):
                continue
            if psc.get("ceased"):
                continue
            name = psc.get("name") or "Unnamed PSC"
            node_id = _unique(seen_ids, "psc_" + _slugify(name))
            nodes.append({
                "id": node_id,
                "label": _wrap(name),
                "group": "psc",
                "title": _psc_tooltip(psc),
                "level": -1,
            })
            edges.append({
                "from": node_id, "to": subject_id,
                "label": _psc_share_label(psc),
                "arrows": "to",
                "color": {"color": "#a78bfa"},
                "width": 2,
            })

    # ── Directors and their cross-appointments ──────────────────────────
    director_analysis = check.get("director_analysis") or {}
    directors = director_analysis.get("directors") or []

    for d in directors[:25]:  # cap to keep graph readable
        if not isinstance(d, dict):
            continue
        d_name = d.get("name") or "Director"
        dissolved_count = int(d.get("dissolved_companies_count") or 0)
        active_count = int(d.get("other_appointments_count") or 0)
        flags = d.get("risk_flags") or []
        node_id = _unique(seen_ids, "dir_" + _slugify(d_name))
        group = "director_dissolved" if dissolved_count >= 2 else "director"

        label = _wrap(d_name)
        if d.get("nationality"):
            label += f"\n({d['nationality']})"

        title_parts = [f"<b>{d_name}</b>"]
        if d.get("role"):
            title_parts.append(d["role"])
        if d.get("nationality"):
            title_parts.append(f"Nat: {d['nationality']}")
        title_parts.append(
            f"Other appts: {active_count} active, {dissolved_count} dissolved"
        )
        if flags:
            title_parts.append("Flags: " + ", ".join(flags[:3]))

        nodes.append({
            "id": node_id,
            "label": label,
            "group": group,
            "title": "<br/>".join(title_parts),
            "level": 1,
        })
        edges.append({
            "from": subject_id, "to": node_id,
            "label": d.get("role") or "director",
            "arrows": "to",
            "color": {"color": "#cbd5e1"},
        })

        # Cross-appointments (the killer feature — show what other UK
        # companies the director sits on the board of)
        other = d.get("other_appointments_detail") or []
        for oc in other[:5]:
            if not isinstance(oc, dict):
                continue
            oc_name = oc.get("company_name") or "Unknown company"
            oc_status = (oc.get("status") or "").lower()
            oc_id = _unique(seen_ids, "co_" + _slugify(oc_name))
            oc_group = "other_company_dissolved" if oc_status in (
                "dissolved", "liquidation", "in liquidation",
            ) else "other_company"

            oc_label = _wrap(oc_name)
            if oc.get("company_number"):
                oc_label += f"\n({oc['company_number']})"

            oc_title = [f"<b>{oc_name}</b>"]
            if oc.get("company_number"):
                oc_title.append(f"CH: {oc['company_number']}")
            if oc.get("status"):
                oc_title.append(f"Status: {oc['status']}")
            if oc.get("role"):
                oc_title.append(f"Role: {oc['role']}")

            nodes.append({
                "id": oc_id,
                "label": oc_label,
                "group": oc_group,
                "title": "<br/>".join(oc_title),
                "level": 2,
            })
            edges.append({
                "from": node_id, "to": oc_id,
                "label": oc.get("role") or "appt",
                "arrows": "to",
                "color": {"color": "#e5e7eb"},
                "dashes": True if oc_group == "other_company_dissolved" else False,
            })

    return {
        "nodes": nodes,
        "edges": edges,
        "meta": {
            "subject": company_name,
            "subject_number": company_number,
            "node_count": len(nodes),
            "edge_count": len(edges),
            "directors_shown": min(len(directors), 25),
            "directors_total": len(directors),
        },
    }


# ─── Internals ───────────────────────────────────────────────────────────────


def _unique(seen: set, base: str) -> str:
    if base not in seen:
        seen.add(base)
        return base
    n = 2
    while f"{base}_{n}" in seen:
        n += 1
    final = f"{base}_{n}"
    seen.add(final)
    return final


def _wrap(s: str, width: int = 22) -> str:
    """Soft-wrap labels at word boundaries so vis-network nodes stay tidy."""
    if not s:
        return ""
    words = s.split()
    lines: list[str] = []
    cur = ""
    for w in words:
        if not cur:
            cur = w
        elif len(cur) + 1 + len(w) <= width:
            cur += " " + w
        else:
            lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return "\n".join(lines[:3])  # cap at 3 lines per node


def _percent_label(layer: dict) -> str:
    pct = layer.get("ownership_percentage") or layer.get("percentage")
    if pct is None:
        nature = layer.get("nature_of_control") or ""
        m = re.search(r"(\d+\s*to\s*\d+|\d+%|less\s+than\s+\d+%)", nature, re.I)
        return m.group(0) if m else "owns"
    if isinstance(pct, (int, float)):
        return f"{pct:.0f}%"
    return str(pct)


def _psc_share_label(psc: dict) -> str:
    nat = psc.get("natures_of_control") or psc.get("nature_of_control") or []
    if isinstance(nat, list) and nat:
        first = str(nat[0])
        m = re.search(r"(\d+(?:-\d+|\s+to\s+\d+)?\s*percent|\d+%)", first, re.I)
        if m:
            return m.group(0).replace("percent", "%").replace(" to ", "-")
    return "PSC"


def _ubo_tooltip(layer: dict) -> str:
    bits = []
    if layer.get("entity_type"):
        bits.append(f"Type: {layer['entity_type']}")
    if layer.get("nature_of_control"):
        bits.append(f"Control: {layer['nature_of_control']}")
    if layer.get("nationality"):
        bits.append(f"Nationality: {layer['nationality']}")
    if layer.get("country_of_residence"):
        bits.append(f"Residence: {layer['country_of_residence']}")
    return "<br/>".join(bits) if bits else "Ultimate beneficial owner"


def _psc_tooltip(psc: dict) -> str:
    bits = [f"<b>{psc.get('name', '')}</b>"]
    if psc.get("kind"):
        bits.append(f"Kind: {psc['kind']}")
    if psc.get("nationality"):
        bits.append(f"Nationality: {psc['nationality']}")
    nat = psc.get("natures_of_control")
    if isinstance(nat, list):
        bits.append("Control: " + "; ".join(str(n) for n in nat[:3]))
    if psc.get("notified_on"):
        bits.append(f"Notified: {psc['notified_on']}")
    return "<br/>".join(bits)
