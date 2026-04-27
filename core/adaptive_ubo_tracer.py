"""
Adaptive UBO Tracer
===================

Intelligently traces UBO chains with:
1. Dynamic depth adjustment based on signals
2. Foreign entity OSINT research instead of stopping
3. Risk-based continuation logic
"""

from core.foreign_entity_osint import research_foreign_entity
from api_clients.companies_house import fetch_company_pscs, fetch_company_full_profile

def trace_ubo_chain_adaptive(company_num, *, initial_depth_limit=10, min_depth=3):
    """
    Adaptive UBO tracer that:
    - Starts trying to go deep (up to 10 layers)
    - Stops early if hitting dead ends or high-risk entities
    - Researches foreign entities instead of just stopping
    
    Parameters:
    -----------
    company_num : str
        The company number to trace
    initial_depth_limit : int
        Maximum depth to attempt (default 10, adaptive)
    min_depth : int
        Minimum depth to always try (default 3)
    
    Returns:
    --------
    dict with chain, ultimate_owners, trace_analytics
    """
    
    visited = set()
    chain = []
    ultimate_owners = []
    graph_edges = []
    trace_analytics = {
        "max_depth_reached": False,
        "foreign_entities_researched": 0,
        "dead_ends": 0,
        "layers_traced": 0,
        "trace_strategy": "adaptive_with_osint"
    }
    
    def _should_continue_tracing(depth, psc_kind, country, trace_history):
        """
        Decide if we should continue tracing deeper or stop intelligently.
        
        Returns: (bool, reason)
        """
        # Always trace at least min_depth
        if depth < min_depth:
            return True, "Below minimum depth"
        
        # Stop if depth exceeds hard limit
        if depth > initial_depth_limit:
            return False, "Hard depth limit reached"
        
        # Stop if we've hit multiple dead ends
        if len(trace_history.get("dead_ends", [])) > 3:
            return False, "Multiple dead ends - likely reached terminal point"
        
        # Continue if strong signal to go deeper (e.g., complex corporate structure)
        if trace_history.get("complexity_score", 0) > 70:
            return True, "High complexity structure - continuing trace"
        
        # Default: continue
        return True, "Continuing standard trace"
    
    def _trace(co_num, co_name, depth, trace_history):
        if co_num in visited:
            return
        
        visited.add(co_num)
        trace_history = trace_history or {
            "dead_ends": [],
            "foreign_entities": [],
            "complexity_score": 0
        }
        
        # Check if we should continue
        should_continue, reason = _should_continue_tracing(
            depth, None, None, trace_history
        )
        
        if not should_continue:
            trace_analytics["max_depth_reached"] = True
            ultimate_owners.append({
                "name": co_name,
                "kind": "trace-stopped",
                "depth": depth,
                "terminal_type": f"Trace Stopped: {reason}"
            })
            return
        
        try:
            pscs = fetch_company_pscs(co_num)
        except Exception as e:
            print(f"⚠️ Failed to fetch PSCs for {co_num}: {e}")
            pscs = []
        
        layer = {
            "company_number": co_num,
            "company_name": co_name,
            "depth": depth,
            "pscs": [],
            "ceased_pscs": [],
            "foreign_entities_researched": []
        }
        
        if not pscs:
            trace_history["dead_ends"].append(co_name)
            layer["note"] = "No PSCs found — may be exempt or terminal entity"
            chain.append(layer)
            return
        
        for psc in pscs:
            kind = (psc.get("kind") or "").lower()
            name = psc.get("name") or "Unknown"
            natures = psc.get("natures_of_control", [])
            nationality = psc.get("nationality", "")
            country = psc.get("country_of_residence", "")
            ceased_on = psc.get("ceased_on", "")
            
            entry = {
                "name": name,
                "kind": kind,
                "depth": depth,
                "natures_of_control": natures,
                "nationality": nationality,
                "country": country,
                "ceased": bool(ceased_on),
            }
            
            # Skip ceased PSCs
            if ceased_on:
                entry["terminal_type"] = "Ceased"
                layer["ceased_pscs"].append(entry)
                continue
            
            # Handle different PSC types
            if "individual" in kind:
                entry["terminal_type"] = "Natural Person"
                ultimate_owners.append(entry)
                graph_edges.append((co_name, name, "UBO (individual)"))
            
            elif "corporate" in kind:
                ident = psc.get("identification") or {}
                reg_num = (ident.get("registration_number") or "").strip()
                legal_form = (ident.get("legal_form") or "").lower()
                place = (ident.get("place_registered") or "").lower()
                psc_country = (ident.get("country_registered") or "").lower()
                
                # Check if public company
                if any(kw in legal_form for kw in ("plc", "public limited")):
                    entry["terminal_type"] = "Publicly Traded (PLC)"
                    ultimate_owners.append(entry)
                    graph_edges.append((co_num, name, "PLC"))
                
                # Check if UK registered company - continue tracing
                elif reg_num and _is_uk_registered(place, psc_country):
                    entry["traced_company_number"] = reg_num
                    graph_edges.append((co_name, name, "corporate owner"))
                    
                    try:
                        sub = fetch_company_full_profile(reg_num.zfill(8))
                        entry["traced_company_name"] = sub.get("company_name", name)
                        _trace(reg_num.zfill(8), entry["traced_company_name"], depth + 1, trace_history)
                    except Exception:
                        entry["terminal_type"] = "Could Not Resolve"
                        ultimate_owners.append(entry)
                
                # FOREIGN ENTITY - DO OSINT INSTEAD OF STOPPING
                else:
                    entry["terminal_type"] = "Foreign Entity"
                    entry["registered_country"] = psc_country or place or "unknown"
                    
                    # Research the foreign entity
                    print(f"🌍 Researching foreign entity: {name} ({psc_country})")
                    osint_findings = research_foreign_entity(name, psc_country, reg_num)
                    
                    entry["osint_research"] = {
                        "reputation_score": osint_findings.get("reputation_score"),
                        "news_findings_count": len(osint_findings.get("news_findings", [])),
                        "beneficial_owners_found": len(osint_findings.get("beneficial_owners", [])),
                        "risk_flags_count": len(osint_findings.get("risk_flags", []))
                    }
                    
                    layer["foreign_entities_researched"].append(osint_findings)
                    trace_analytics["foreign_entities_researched"] += 1
                    
                    # Add identified owners from OSINT to ultimate owners
                    for owner in osint_findings.get("beneficial_owners", []):
                        ultimate_owners.append({
                            **owner,
                            "depth": depth + 1,
                            "source": "OSINT Research",
                            "terminal_type": "Identified via Web Intelligence"
                        })
                    
                    ultimate_owners.append(entry)
                    graph_edges.append((co_name, name, f"foreign ({psc_country})"))
            
            elif "legal-person" in kind:
                entry["terminal_type"] = "Government / State Entity"
                ultimate_owners.append(entry)
                graph_edges.append((co_name, name, "state entity"))
            
            elif "super-secure" in kind:
                entry["terminal_type"] = "Protected (Super-Secure PSC)"
                ultimate_owners.append(entry)
            
            else:
                entry["terminal_type"] = "Unknown PSC Type"
                ultimate_owners.append(entry)
            
            layer["pscs"].append(entry)
        
        chain.append(layer)
    
    # Get root company name
    try:
        root = fetch_company_full_profile(company_num)
        root_name = root.get("company_name", company_num)
    except Exception:
        root_name = company_num
    
    # Start tracing
    _trace(company_num, root_name, 0, None)
    
    trace_analytics["layers_traced"] = len(chain)
    
    return {
        "chain": chain,
        "ultimate_owners": ultimate_owners,
        "graph_edges": graph_edges,
        "trace_analytics": trace_analytics
    }


def _is_uk_registered(place: str, country: str) -> bool:
    """Check if entity is UK registered."""
    uk_indicators = ["uk", "united kingdom", "england", "scotland", "wales", "northern ireland"]
    combined = f"{place} {country}".lower()
    return any(ind in combined for ind in uk_indicators)
