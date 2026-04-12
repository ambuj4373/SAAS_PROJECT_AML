"""
EXAMPLE: FCA Integration into Company Checking Pipeline

This file shows how to integrate FCA lookups into your existing
company screening workflow. Copy the pattern into your actual
pipeline/nodes.py file.
"""

# ═══════════════════════════════════════════════════════════════════════════════
# STEP 1: Add FCA lookup to pipeline node
# ═══════════════════════════════════════════════════════════════════════════════

def run_analysis_engines_with_fca(state: dict) -> dict:
    """Enhanced analysis node that includes FCA regulatory check.
    
    OPTIMIZED: Looks up by Companies House number first (most reliable),
    then falls back to firm name if needed.
    """
    from api_clients.fca_register import lookup_firm_by_ch_number
    from core.logging_config import get_logger
    
    log = get_logger("pipeline.nodes")
    updates = {}
    
    try:
        ch_data = state.get("ch_data", {})
        company_name = state.get("company_name", "")
        
        # Get Companies House number (the KEY to FCA lookup)
        ch_number = ch_data.get("company_number", "") if ch_data else ""
        
        if ch_number:
            log.info(f"[FCA] Looking up by CH number: {ch_number}")
            # BEST: Lookup by Companies House number
            fca_result = lookup_firm_by_ch_number(ch_number, firm_name=company_name)
            
            if fca_result.found:
                log.info(f"[FCA] ✅ Found via CH number: {fca_result.firm_name}")
                updates["fca_regulation"] = fca_result.model_dump()
            else:
                log.info(f"[FCA] Not FCA-regulated (CH#{ch_number})")
                updates["fca_regulation"] = None
        else:
            log.debug(f"[FCA] No CH number available, skipping lookup")
            updates["fca_regulation"] = None
        
        return updates
    
    except Exception as e:
        log.error(f"[FCA] Error during lookup: {e}")
        updates["fca_regulation"] = None
        return updates


# ═══════════════════════════════════════════════════════════════════════════════
# STEP 2: Modify risk scorer to use FCA data
# ═══════════════════════════════════════════════════════════════════════════════

def score_company_with_fca(
    company_data: dict,
    financial_history: list[dict],
    governance_indicators: dict,
    adversity_signals: list[dict],
    fca_regulation: dict | None = None,  # NEW
    **kwargs
):
    """Score company with FCA regulatory status adjustments.
    
    FCA regulation provides:
    1. Base risk reduction (multiply by 0.75 for active regulation)
    2. Signal: regulatory oversight (confidence boost)
    3. Governance credit: regular compliance audits
    """
    from core.models import RiskScore, RiskLevel, RiskSignal
    
    signals = []
    category_scores = {
        "Finance": 0.0,
        "Governance": 0.0,
        "Legal": 0.0,
        "Reputational": 0.0,
    }
    
    # ────────────────────────────────────────────────────────────────
    # Existing scoring logic (simplified)
    # ────────────────────────────────────────────────────────────────
    
    # Financial scoring (simplified example)
    if financial_history:
        # Your existing financial pattern detection would go here
        pass
    
    # Governance scoring
    if governance_indicators:
        # Your existing governance analysis
        pass
    
    # ────────────────────────────────────────────────────────────────
    # NEW: FCA REGULATION SCORING
    # ────────────────────────────────────────────────────────────────
    
    fca_risk_reduction = 1.0  # Default: no reduction
    
    if fca_regulation:
        fca_found = fca_regulation.get("found", False)
        fca_is_regulated = fca_regulation.get("is_regulated", False)
        fca_status = fca_regulation.get("authorisation_status", "")
        fca_reduction = fca_regulation.get("risk_reduction_factor", 1.0)
        if isinstance(fca_reduction, (int, float)):
            fca_reduction = max(0.0, min(1.0, float(fca_reduction)))
        else:
            fca_reduction = 1.0
        
        if fca_found:
            # Signal: FCA Regulation Found
            signals.append(RiskSignal(
                category="Governance",
                description=f"Registered with FCA Financial Services Register ({fca_status})",
                severity=RiskLevel.NONE,
                source="FCA_Register",
                score_impact=-5.0,  # Reduce score by 5 points
                evidence=f"Firm Reference Number: {fca_regulation.get('frn', 'N/A')}",
            ))
            
            # Governance credit: regulatory oversight
            if fca_is_regulated:
                signals.append(RiskSignal(
                    category="Governance",
                    description="Subject to FCA compliance audits and ongoing supervision",
                    severity=RiskLevel.NONE,
                    source="FCA_Register",
                    score_impact=-3.0,
                    evidence="Active regulatory oversight provides additional assurance",
                ))
                category_scores["Governance"] -= 8.0  # Total: -5 + -3
                fca_risk_reduction = fca_reduction  # 0.75 for active
            else:
                # Formerly regulated: slight trust but lower assurance
                signals.append(RiskSignal(
                    category="Governance",
                    description="Formerly registered with FCA (no longer authorised)",
                    severity=RiskLevel.LOW,
                    source="FCA_Register",
                    score_impact=-1.0,
                    evidence="Historic regulatory relationship may indicate prior compliance",
                ))
                category_scores["Governance"] -= 6.0  # Total: -5 + -1
                fca_risk_reduction = fca_reduction  # 0.95 for former
            
            # List regulated activities if available
            activities = fca_regulation.get("regulated_activities", [])
            if activities and isinstance(activities, list):
                activities_str = ", ".join(str(a) for a in activities[:3])
                if len(activities) > 3:
                    activities_str += f", +{len(activities) - 3} more"
                
                signals.append(RiskSignal(
                    category="Governance",
                    description=f"Regulated activities: {activities_str}",
                    severity=RiskLevel.NONE,
                    source="FCA_Register",
                    score_impact=0.0,
                    evidence="FCA activities within Handbook scope",
                ))
    
    # ────────────────────────────────────────────────────────────────
    # Calculate overall score
    # ────────────────────────────────────────────────────────────────
    
    # Sum category scores (clamped to 0-100 range)
    overall_score = sum(category_scores.values())
    overall_score = max(0.0, min(100.0, overall_score))
    
    # Apply FCA risk reduction multiplier
    overall_score = overall_score * fca_risk_reduction
    overall_score = max(0.0, min(100.0, overall_score))
    
    # Determine risk level
    if overall_score >= 65:
        overall_level = RiskLevel.CRITICAL
    elif overall_score >= 40:
        overall_level = RiskLevel.HIGH
    elif overall_score >= 20:
        overall_level = RiskLevel.MEDIUM
    else:
        overall_level = RiskLevel.LOW
    
    return RiskScore(
        overall_score=overall_score,
        overall_level=overall_level,
        category_scores=category_scores,
        signals=signals,
        confidence=0.85,
        methodology_notes=[
            f"FCA regulation multiplier applied: {fca_risk_reduction:.2f}x"
            if fca_risk_reduction != 1.0
            else ""
        ],
    )


# ═══════════════════════════════════════════════════════════════════════════════
# STEP 3: Display FCA status in UI
# ═══════════════════════════════════════════════════════════════════════════════

def render_fca_section(st_mod, fca_regulation: dict | None):
    """Render FCA regulation status in Streamlit dashboard.
    
    Shows:
    - Registration status (badge: ✅ Regulated / ⚠️ Not regulated)
    - Risk reduction impact
    - Regulated activities
    - Compliance benefits
    """
    if not fca_regulation or not fca_regulation.get("found"):
        st_mod.info(
            "⚠️ **Not FCA Regulated** — This company is not registered with the "
            "Financial Services Register. Higher due-diligence scrutiny is recommended."
        )
        return
    
    # ────────────────────────────────────────────────────────────────
    # Header with status badge
    # ────────────────────────────────────────────────────────────────
    
    col1, col2, col3 = st_mod.columns([2, 1, 1])
    
    with col1:
        is_regulated = fca_regulation.get("is_regulated", False)
        status = fca_regulation.get("authorisation_status", "Unknown")
        
        if is_regulated:
            st_mod.success(
                f"✅ **FCA Regulated** — {status}"
            )
        else:
            st_mod.warning(
                f"⚠️ **No Longer Regulated** — {status}"
            )
    
    with col2:
        reduction_factor = fca_regulation.get("risk_reduction_factor", 1.0)
        reduction_pct = (1 - reduction_factor) * 100
        if reduction_pct > 0:
            st_mod.metric("Risk Reduction", f"-{reduction_pct:.0f}%")
    
    with col3:
        frn = fca_regulation.get("frn", "N/A")
        st_mod.write(f"**FRN:** {frn}")
    
    # ────────────────────────────────────────────────────────────────
    # Regulated Activities
    # ────────────────────────────────────────────────────────────────
    
    activities = fca_regulation.get("regulated_activities", [])
    if activities:
        st_mod.markdown("### Regulated Activities")
        cols = st_mod.columns(min(3, len(activities)))
        for idx, activity in enumerate(activities[:6]):
            cols[idx % 3].caption(f"• {activity}")
    
    # ────────────────────────────────────────────────────────────────
    # Compliance Benefits
    # ────────────────────────────────────────────────────────────────
    
    benefits = fca_regulation.get("compliance_benefits", [])
    if benefits:
        st_mod.markdown("### Compliance Benefits")
        with st_mod.expander("View compliance benefits", expanded=True):
            for benefit in benefits:
                st_mod.write(f"✓ {benefit}")
    
    # ────────────────────────────────────────────────────────────────
    # Firm Details
    # ────────────────────────────────────────────────────────────────
    
    st_mod.markdown("### Firm Details")
    detail_cols = st_mod.columns(2)
    
    with detail_cols[0]:
        firm_name = fca_regulation.get("firm_name", "N/A")
        firm_type = fca_regulation.get("firm_type", "N/A")
        auth_date = fca_regulation.get("authorised_date", "N/A")
        
        st_mod.write(f"**Firm Name:** {firm_name}")
        st_mod.write(f"**Type:** {firm_type}")
        st_mod.write(f"**Authorised:** {auth_date}")
    
    with detail_cols[1]:
        address = fca_regulation.get("principal_address", "N/A")
        telephone = fca_regulation.get("telephone", "N/A")
        ch_number = fca_regulation.get("companies_house_number", "N/A")
        
        st_mod.write(f"**Address:** {address}")
        st_mod.write(f"**Tel:** {telephone}")
        st_mod.write(f"**Companies House:** {ch_number}")


# ═══════════════════════════════════════════════════════════════════════════════
# USAGE EXAMPLE
# ═══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    from api_clients.fca_register import lookup_firm_by_name
    
    # Example 1: Lookup a known regulated company
    print("\n=== Example 1: Lookup Barclays ===")
    result = lookup_firm_by_name("Barclays Bank PLC")
    print(f"Found: {result.found}")
    print(f"Is Regulated: {result.is_regulated}")
    print(f"Status: {result.authorisation_status}")
    print(f"Risk Reduction: {result.risk_reduction_factor}")
    
    # Example 2: Score company with FCA data
    print("\n=== Example 2: Score with FCA ===")
    fca_data = result.model_dump()
    
    score = score_company_with_fca(
        company_data={"company_name": "Barclays Bank PLC"},
        financial_history=[],
        governance_indicators={},
        adversity_signals=[],
        fca_regulation=fca_data,
    )
    print(f"Risk Score: {score.overall_score:.1f}")
    print(f"Risk Level: {score.overall_level}")
    print(f"FCA Signals: {len([s for s in score.signals if 'FCA' in s.source])}")
    
    # Example 3: Non-regulated company
    print("\n=== Example 3: Non-Regulated Company ===")
    result = lookup_firm_by_name("John's Tech Startup Ltd")
    print(f"Found: {result.found}")
    print(f"Risk Reduction: {result.risk_reduction_factor}")
