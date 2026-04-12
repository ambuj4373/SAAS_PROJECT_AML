"""
ui/network_viz.py — Relationship / network analysis visualisation.

Builds node-link diagrams for:
 • Trustee ↔ Charity connections (charity mode)
 • Director / PSC → Company → UBO chain (company mode)
 • Cross-entity links discovered during OSINT

Uses Plotly for interactive network graphs, with a plain HTML/CSS
fallback when Plotly is not installed.
"""

from __future__ import annotations

import math
from typing import Any

try:
    import plotly.graph_objects as go  # type: ignore
    HAS_PLOTLY = True
except ImportError:
    HAS_PLOTLY = False

from ui.charts import BG, FG, GRID, _t


# ═══════════════════════════════════════════════════════════════════════════════
# DATA STRUCTURES
# ═══════════════════════════════════════════════════════════════════════════════

class NetworkNode:
    __slots__ = ("id", "label", "kind", "meta")

    def __init__(self, id: str, label: str, kind: str = "default", meta: dict | None = None):
        self.id = id
        self.label = label
        self.kind = kind  # charity, company, person, jurisdiction
        self.meta = meta or {}


class NetworkEdge:
    __slots__ = ("source", "target", "label", "weight")

    def __init__(self, source: str, target: str, label: str = "", weight: float = 1.0):
        self.source = source
        self.target = target
        self.label = label
        self.weight = weight


class NetworkGraph:
    """Simple container for nodes & edges."""

    def __init__(self):
        self.nodes: dict[str, NetworkNode] = {}
        self.edges: list[NetworkEdge] = []

    def add_node(self, id: str, label: str, kind: str = "default", meta: dict | None = None):
        if id not in self.nodes:
            self.nodes[id] = NetworkNode(id, label, kind, meta)

    def add_edge(self, source: str, target: str, label: str = "", weight: float = 1.0):
        self.edges.append(NetworkEdge(source, target, label, weight))


# ═══════════════════════════════════════════════════════════════════════════════
# GRAPH BUILDERS
# ═══════════════════════════════════════════════════════════════════════════════

def build_charity_network(
    entity_name: str,
    charity_number: str,
    trustees: list[str],
    trustee_appointments: dict | None = None,
    ch_data: dict | None = None,
    country_risk_classified: list[dict] | None = None,
) -> NetworkGraph:
    """
    Build a network graph from charity analysis data.

    Central node: the charity.
    Connected to: trustees, linked company, countries of operation,
    and any cross-appointments held by trustees.
    """
    g = NetworkGraph()

    # Central charity node
    charity_id = f"charity_{charity_number}"
    g.add_node(charity_id, entity_name, "charity")

    # Linked company
    if ch_data:
        co_num = ch_data.get("company_number") or ""
        co_name = ch_data.get("company_name") or f"Company {co_num}"
        if co_num:
            co_id = f"company_{co_num}"
            g.add_node(co_id, co_name, "company")
            g.add_edge(charity_id, co_id, "linked company")

    # Trustees
    appointments = trustee_appointments or {}
    for t_name in trustees:
        t_id = f"person_{t_name}"
        g.add_node(t_id, t_name, "person")
        g.add_edge(charity_id, t_id, "trustee")

        # Cross-appointments
        appts = appointments.get(t_name)
        if isinstance(appts, dict):
            for co_num_a, co_info in appts.items():
                co_name_a = co_info if isinstance(co_info, str) else str(co_info)
                co_id_a = f"company_{co_num_a}"
                g.add_node(co_id_a, co_name_a, "company",
                           {"via_trustee": t_name})
                g.add_edge(t_id, co_id_a, "director/officer")
        elif isinstance(appts, list):
            for appt in appts:
                if isinstance(appt, dict):
                    co_num_a = appt.get("company_number", "")
                    co_name_a = appt.get("company_name", co_num_a)
                    if co_num_a:
                        co_id_a = f"company_{co_num_a}"
                        g.add_node(co_id_a, co_name_a, "company",
                                   {"via_trustee": t_name})
                        g.add_edge(t_id, co_id_a, "director/officer")

    # Countries of operation
    for country in (country_risk_classified or []):
        c_name = country.get("country", "Unknown")
        risk = country.get("risk_level", "Unknown")
        c_id = f"country_{c_name}"
        g.add_node(c_id, c_name, "jurisdiction", {"risk_level": risk})
        g.add_edge(charity_id, c_id, "operates in")

    return g


def build_company_network(
    company_name: str,
    company_number: str,
    co_check: dict,
) -> NetworkGraph:
    """
    Build a network graph from company check data.

    Central node: the company.
    Connected to: directors, PSCs, UBO chain entities.
    """
    g = NetworkGraph()

    co_id = f"company_{company_number}"
    g.add_node(co_id, company_name, "company")

    # Directors
    dirs = co_check.get("directors", {})
    active_dirs = dirs.get("active", []) if isinstance(dirs, dict) else []
    for d in active_dirs:
        d_name = d.get("name", "Unknown")
        d_id = f"person_{d_name}"
        role = d.get("role", "director")
        nationality = d.get("nationality", "")
        g.add_node(d_id, d_name, "person",
                   {"nationality": nationality, "role": role})
        g.add_edge(co_id, d_id, role)

    # PSCs
    pscs = co_check.get("pscs", {})
    active_pscs = pscs.get("active", []) if isinstance(pscs, dict) else []
    for p in active_pscs:
        p_name = p.get("name", "Unknown")
        kind = p.get("kind", "")
        band = p.get("ownership_band", "")
        p_id = f"psc_{p_name}"

        if "corporate" in kind.lower():
            g.add_node(p_id, p_name, "company", {"ownership_band": band})
        else:
            g.add_node(p_id, p_name, "person", {"ownership_band": band})
        g.add_edge(co_id, p_id, f"PSC ({band})" if band else "PSC")

    # UBO chain
    ubo = co_check.get("ubo", {})
    chain = ubo.get("chain", [])
    for layer in chain:
        depth = layer.get("depth", 0)
        layer_co = layer.get("company_number", "")
        layer_name = layer.get("company_name", layer_co)
        if layer_co:
            layer_id = f"company_{layer_co}"
            g.add_node(layer_id, layer_name, "company", {"depth": depth})

            for psc in layer.get("pscs", []):
                psc_name = psc.get("name", "?")
                traced_co = psc.get("traced_company_number", "")
                terminal = psc.get("terminal_type", "")

                if traced_co:
                    traced_id = f"company_{traced_co}"
                    traced_name = psc.get("traced_company_name", traced_co)
                    g.add_node(traced_id, traced_name, "company", {"depth": depth + 1})
                    g.add_edge(layer_id, traced_id, f"owns via {psc_name}")
                else:
                    psc_id = f"psc_{psc_name}_{depth}"
                    g.add_node(psc_id, psc_name, "person",
                               {"terminal_type": terminal})
                    g.add_edge(layer_id, psc_id, "UBO")

    return g


# ═══════════════════════════════════════════════════════════════════════════════
# LAYOUT: simple circular layout (no networkx dependency)
# ═══════════════════════════════════════════════════════════════════════════════

def _circular_layout(graph: NetworkGraph) -> dict[str, tuple[float, float]]:
    """Assign (x, y) positions in a circular layout."""
    nodes = list(graph.nodes.keys())
    n = len(nodes)
    pos = {}
    for i, nid in enumerate(nodes):
        angle = 2 * math.pi * i / max(n, 1)
        pos[nid] = (math.cos(angle), math.sin(angle))
    return pos


def _hierarchical_layout(graph: NetworkGraph) -> dict[str, tuple[float, float]]:
    """
    Simple hierarchical layout: central entity at centre, connected
    nodes arranged in concentric rings by hop distance.
    """
    nodes = list(graph.nodes.keys())
    if not nodes:
        return {}

    # BFS from first node
    adj: dict[str, list[str]] = {n: [] for n in nodes}
    for e in graph.edges:
        if e.source in adj:
            adj[e.source].append(e.target)
        if e.target in adj:
            adj[e.target].append(e.source)

    root = nodes[0]
    visited = {root: 0}
    queue = [root]
    while queue:
        curr = queue.pop(0)
        for nb in adj.get(curr, []):
            if nb not in visited:
                visited[nb] = visited[curr] + 1
                queue.append(nb)

    # Place unvisited nodes at max depth + 1
    max_d = max(visited.values()) if visited else 0
    for n in nodes:
        if n not in visited:
            visited[n] = max_d + 1

    # Assign positions by ring
    rings: dict[int, list[str]] = {}
    for n, d in visited.items():
        rings.setdefault(d, []).append(n)

    pos = {}
    for depth, ring_nodes in rings.items():
        r = depth * 1.5  # ring radius
        for i, nid in enumerate(ring_nodes):
            angle = 2 * math.pi * i / max(len(ring_nodes), 1)
            if depth == 0:
                pos[nid] = (0, 0)
            else:
                pos[nid] = (r * math.cos(angle), r * math.sin(angle))
    return pos


# ═══════════════════════════════════════════════════════════════════════════════
# PLOTLY RENDERING
# ═══════════════════════════════════════════════════════════════════════════════

_KIND_SYMBOL = {
    "charity":      dict(color="#3b82f6", size=22, symbol="diamond"),
    "company":      dict(color="#7c3aed", size=18, symbol="square"),
    "person":       dict(color="#10b981", size=14, symbol="circle"),
    "jurisdiction": dict(color="#f59e0b", size=12, symbol="triangle-up"),
    "default":      dict(color="#94a3b8", size=12, symbol="circle"),
}


def render_network(
    graph: NetworkGraph,
    theme: str = "Light",
    title: str = "Entity Relationship Network",
    height: int = 500,
):
    """
    Render a NetworkGraph as an interactive Plotly figure.
    Falls back to HTML/CSS table if Plotly is unavailable.

    Returns
    -------
    go.Figure | str
        Plotly Figure or HTML string.
    """
    if not graph.nodes:
        return None

    pos = _hierarchical_layout(graph)
    th = _t(theme)

    if HAS_PLOTLY:
        # Edge traces
        edge_x, edge_y = [], []
        for e in graph.edges:
            if e.source in pos and e.target in pos:
                x0, y0 = pos[e.source]
                x1, y1 = pos[e.target]
                edge_x += [x0, x1, None]
                edge_y += [y0, y1, None]

        edge_trace = go.Scatter(
            x=edge_x, y=edge_y, mode="lines",
            line=dict(width=1, color=GRID[th]),
            hoverinfo="none",
        )

        # Node traces — one per kind for legend
        node_traces = []
        for kind, style in _KIND_SYMBOL.items():
            kind_nodes = [n for n in graph.nodes.values() if n.kind == kind]
            if not kind_nodes:
                continue
            xs = [pos[n.id][0] for n in kind_nodes if n.id in pos]
            ys = [pos[n.id][1] for n in kind_nodes if n.id in pos]
            labels = [n.label for n in kind_nodes if n.id in pos]
            hovers = []
            for n in kind_nodes:
                parts = [f"<b>{n.label}</b>", f"Type: {n.kind}"]
                for mk, mv in n.meta.items():
                    parts.append(f"{mk}: {mv}")
                hovers.append("<br>".join(parts))

            node_traces.append(go.Scatter(
                x=xs, y=ys, mode="markers+text",
                marker=dict(
                    size=style["size"],
                    color=style["color"],
                    symbol=style["symbol"],
                    line=dict(width=1, color=BG[th]),
                ),
                text=labels,
                textposition="top center",
                textfont=dict(size=9, color=FG[th]),
                hovertext=hovers,
                hoverinfo="text",
                name=kind.title(),
            ))

        fig = go.Figure(data=[edge_trace] + node_traces)
        fig.update_layout(
            title=dict(text=title, font=dict(size=14, color=FG[th])),
            paper_bgcolor=BG[th],
            plot_bgcolor=BG[th],
            font=dict(color=FG[th]),
            height=height,
            margin=dict(l=20, r=20, t=50, b=20),
            xaxis=dict(showgrid=False, zeroline=False, visible=False),
            yaxis=dict(showgrid=False, zeroline=False, visible=False),
            showlegend=True,
            legend=dict(bgcolor="rgba(0,0,0,0)", font=dict(size=10)),
            hovermode="closest",
        )
        return fig

    # ── HTML fallback ────────────────────────────────────────────────────
    rows = []
    for e in graph.edges:
        src = graph.nodes.get(e.source)
        tgt = graph.nodes.get(e.target)
        if src and tgt:
            rows.append(
                f"<tr><td>{src.label}</td><td>{e.label or '→'}</td>"
                f"<td>{tgt.label}</td></tr>"
            )

    html = (
        f"<h4>{title}</h4>"
        f"<table style='width:100%; border-collapse:collapse;'>"
        f"<tr style='background:{GRID[th]};color:{FG[th]};'>"
        f"<th style='padding:6px;text-align:left;'>Source</th>"
        f"<th style='padding:6px;text-align:left;'>Relationship</th>"
        f"<th style='padding:6px;text-align:left;'>Target</th></tr>"
        + "".join(rows)
        + "</table>"
    )
    return html


# ═══════════════════════════════════════════════════════════════════════════════
# HELPER: display in Streamlit
# ═══════════════════════════════════════════════════════════════════════════════

def show_network(st_module, graph: NetworkGraph, theme: str = "Light", **kwargs):
    """Render and display a NetworkGraph in Streamlit."""
    result = render_network(graph, theme=theme, **kwargs)
    if result is None:
        return
    if HAS_PLOTLY and isinstance(result, go.Figure):
        st_module.plotly_chart(result, use_container_width=True)
    elif isinstance(result, str):
        st_module.markdown(result, unsafe_allow_html=True)
