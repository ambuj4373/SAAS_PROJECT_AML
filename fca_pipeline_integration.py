"""
Integration: Add FCA check to company screening pipeline.

Checks company website for FCA mentions.
Quick copy-paste into your pipeline/nodes.py or company_graph.py
"""

def run_company_analysis_with_fca(state: dict) -> dict:
    """
    Enhanced company analysis that includes FCA website check.
    
    Add this to your pipeline nodes.
    """
    from api_clients.fca_website_check import check_company_website_for_fca
    from core.logging_config import get_logger
    
    log = get_logger("pipeline.nodes")
    updates = {}
    
    try:
        company_name = state.get("company_name", "")
        website_url = state.get("website_url", "")
        ch_number = state.get("ch_number", "")
        
        if not website_url:
            log.debug("[FCA] No website URL available")
            return {"fca_details": None}
        
        # Check company's website for FCA mentions
        fca_check = check_company_website_for_fca(website_url, company_name)
        
        updates["fca_details"] = {
            "fca_found": fca_check.found_fca_mention,
            "frn": fca_check.frn,
            "mentions": fca_check.mentions,
            "risk_reduction": fca_check.risk_reduction_factor,
            "source": "company_website",
            "ch_number": ch_number,
            "company_name": company_name,
        }
        
        if fca_check.found_fca_mention:
            frn_str = f" (FRN: {fca_check.frn})" if fca_check.frn else ""
            log.info(f"[FCA] ✅ Found on website: {company_name}{frn_str}")
        else:
            log.info(f"[FCA] No FCA mention found on website")
        
        return updates
    
    except Exception as e:
        log.error(f"[FCA] Error: {e}")
        return {"fca_details": None}


def apply_fca_risk_adjustment(risk_score: float, fca_details: dict | None) -> float:
    """
    Apply FCA regulatory risk reduction to the company's risk score.
    
    Use this in your risk_scorer.py
    """
    if not fca_details or not fca_details.get("fca_found"):
        return risk_score  # No adjustment
    
    # Get risk reduction multiplier from FCA details
    multiplier = fca_details.get("risk_reduction", 1.0)
    
    # Apply: score * multiplier
    adjusted_score = risk_score * multiplier
    
    # Example: 45 (High) * 0.75 = 33.75 (Medium)
    return adjusted_score


def render_fca_section_in_ui(st_mod, fca_details: dict | None):
    """
    Display FCA details in Streamlit dashboard.
    
    Add to your app.py or ui components.
    """
    import streamlit as st
    
    if not fca_details:
        st_mod.info("⚠️ FCA status: Unknown (not checked)")
        return
    
    if not fca_details.get("fca_found"):
        st_mod.warning(f"⚠️ Not FCA Regulated - {fca_details.get('reason', 'Not found')}")
        return
    
    # Regulated
    st_mod.markdown("### FCA Financial Services Register")
    
    col1, col2, col3 = st_mod.columns(3)
    
    with col1:
        status = fca_details.get("status", "Unknown")
        if fca_details.get("is_authorised"):
            st_mod.success(f"✅ {status}")
        else:
            st_mod.warning(f"⚠️ {status}")
    
    with col2:
        multiplier = fca_details.get("risk_reduction", 1.0)
        reduction_pct = (1 - multiplier) * 100
        st_mod.metric("Risk Reduction", f"-{reduction_pct:.0f}%")
    
    with col3:
        frn = fca_details.get("frn", "N/A")
        st_mod.write(f"**FRN:** {frn}")
    
    # Firm details
    st_mod.markdown("#### Firm Details")
    detail_cols = st_mod.columns(2)
    
    with detail_cols[0]:
        st_mod.write(f"**Name:** {fca_details.get('firm_name', 'N/A')}")
        st_mod.write(f"**Authorised:** {fca_details.get('authorised_since', 'N/A')}")
        st_mod.write(f"**Phone:** {fca_details.get('phone', 'N/A')}")
    
    with detail_cols[1]:
        st_mod.write(f"**Address:** {fca_details.get('address', 'N/A')}")
        st_mod.write(f"**Email:** {fca_details.get('email', 'N/A')}")
        st_mod.write(f"**Website:** {fca_details.get('website', 'N/A')}")
    
    # Regulated activities
    activities = fca_details.get("regulated_activities", [])
    if activities:
        st_mod.markdown("#### Regulated Activities")
        for activity in activities:
            st_mod.write(f"• {activity}")
    
    # Client money restrictions
    restrictions = fca_details.get("client_money_restrictions", [])
    if restrictions:
        st_mod.markdown("#### Client Money")
        for restriction in restrictions:
            st_mod.write(f"⚠️ {restriction}")
    
    # Requirements
    reqs = fca_details.get("requirements", [])
    if reqs:
        st_mod.markdown("#### Requirements/Restrictions")
        with st_mod.expander("View requirements"):
            for req in reqs:
                st_mod.write(f"• {req}")
    
    # Link to FCA page
    page_url = fca_details.get("page_url")
    if page_url:
        st_mod.markdown(f"[📋 View on FCA Register]({page_url})")


if __name__ == "__main__":
    import json
    from api_clients.fca_website_check import check_company_website_for_fca
    
    # Test with real company websites
    print("\n" + "="*80)
    print("TEST: FCA Website Check - Barclays")
    print("="*80)
    
    result = check_company_website_for_fca("https://www.barclays.co.uk", "Barclays Bank")
    
    print(f"\n✅ FCA Found on Website: {result.found_fca_mention}")
    if result.found_fca_mention:
        print(f"  FRN: {result.frn or 'Not extracted'}")
        print(f"  Risk Reduction: {result.risk_reduction_factor:.2f}x ({(1-result.risk_reduction_factor)*100:.0f}%)")
        print(f"  Mentions Found: {len(result.mentions)}")
        if result.mentions:
            print(f"  Sample: {result.mentions[0][:80]}...")
    
    print("\n" + "="*80)
    print("TEST: FCA Website Check - Generic Company (no FCA)")
    print("="*80)
    
    result2 = check_company_website_for_fca("https://www.google.com", "Google")
    
    print(f"\n✅ FCA Found on Website: {result2.found_fca_mention}")
    if not result2.found_fca_mention:
        print(f"  No FCA mention found (expected for non-regulated company)")
    
    print("\n" + "="*80)
