"""
ui/french_registry_ui.py — French Registry Search UI Components

Provides Streamlit UI components for searching and displaying
French Registry (INPI) company information.
"""

import streamlit as st
from typing import Dict, List, Optional, Any
from datetime import datetime
import logging

from api_clients.french_registry import (
    search_french_companies,
    get_company_details,
    get_financial_records,
    get_formality_records,
    get_french_registry_profile,
    validate_credentials,
    FrenchCompanyBasic,
    FrenchFinancialRecord,
    FrenchFormalityRecord,
)

logger = logging.getLogger(__name__)


def display_french_registry_search():
    """Main French Registry search interface."""
    
    st.title("🇫🇷 French Registry (INPI) Search")
    st.markdown(
        "Search for French companies and access their financial records, "
        "formal documents, and regulatory filings."
    )
    
    # ─── Check authentication ────────────────────────────────────────────────
    st.markdown("---")
    
    if not validate_credentials():
        st.error(
            "❌ **Authentication Failed**\n\n"
            "French Registry credentials are not configured or invalid.\n\n"
            "**To enable this feature:**\n"
            "1. Edit `.env` file in the app directory\n"
            "2. Add your French Registry credentials:\n"
            "   ```\n"
            "   FRENCH_REGISTRY_EMAIL=your_email@example.com\n"
            "   FRENCH_REGISTRY_PASSWORD=your_password\n"
            "   ```\n"
            "3. Restart the application"
        )
        return
    
    st.success("✅ Connected to French Registry (INPI)")
    
    # ─── Search Form ────────────────────────────────────────────────────────
    st.markdown("---")
    st.subheader("🔍 Search Companies")
    
    search_col1, search_col2 = st.columns([3, 1])
    
    with search_col1:
        company_search = st.text_input(
            "Company Name",
            placeholder="e.g., MICHELIN, Orange, L'Oréal",
            help="Enter the French company name to search for"
        )
    
    with search_col2:
        search_button = st.button(
            "Search",
            key="french_search_btn",
            use_container_width=True,
            type="primary"
        )
    
    # ─── Execute Search ─────────────────────────────────────────────────────
    if search_button and company_search:
        with st.spinner(f"🔍 Searching for '{company_search}' in French Registry..."):
            companies = search_french_companies(company_search)
        
        if companies:
            st.success(f"✅ Found {len(companies)} matching companies")
            
            # Display results
            st.markdown("---")
            st.subheader("📋 Search Results")
            
            # Create selection tabs for each company
            selected_idx = st.selectbox(
                "Select a company to view details:",
                range(len(companies)),
                format_func=lambda i: f"{companies[i].name} ({companies[i].siren})",
                key="french_company_select"
            )
            
            selected_company = companies[selected_idx]
            
            # Display selected company details
            _display_company_details(selected_company)
            
            # Get and display additional records
            st.markdown("---")
            st.subheader("📚 Company Records")
            
            tabs = st.tabs([
                "📊 Financial Records",
                "📄 Formality Documents",
                "ℹ️ Full Profile"
            ])
            
            with tabs[0]:
                _display_financial_records(selected_company.siren)
            
            with tabs[1]:
                _display_formality_records(selected_company.siren)
            
            with tabs[2]:
                _display_full_profile(selected_company.siren, company_search)
        
        else:
            st.warning(
                f"❌ No companies found matching '{company_search}'\n\n"
                "Try searching with:\n"
                "• The company's legal name\n"
                "• A partial name\n"
                "• The SIREN number (9 digits)"
            )
    
    # ─── Direct SIREN Lookup ────────────────────────────────────────────────
    st.markdown("---")
    st.subheader("🔢 Direct SIREN Lookup")
    
    siren_col1, siren_col2 = st.columns([3, 1])
    
    with siren_col1:
        siren_input = st.text_input(
            "SIREN Number",
            placeholder="e.g., 732043259 (9 digits)",
            help="Enter the 9-digit SIREN number for direct lookup",
            key="french_siren_input"
        )
    
    with siren_col2:
        siren_button = st.button(
            "Lookup",
            key="french_siren_btn",
            use_container_width=True,
            type="primary"
        )
    
    if siren_button and siren_input:
        # Validate SIREN format
        siren_clean = siren_input.replace(" ", "").strip()
        
        if len(siren_clean) != 9 or not siren_clean.isdigit():
            st.error(
                "❌ Invalid SIREN format\n\n"
                "SIREN must be exactly 9 digits (e.g., 732043259)"
            )
        else:
            with st.spinner(f"🔍 Looking up SIREN {siren_clean}..."):
                company = get_company_details(siren_clean)
            
            if company:
                st.success(f"✅ Found company: {company.name}")
                
                st.markdown("---")
                _display_company_details(company)
                
                st.markdown("---")
                st.subheader("📚 Company Records")
                
                tabs = st.tabs([
                    "📊 Financial Records",
                    "📄 Formality Documents",
                    "ℹ️ Full Profile"
                ])
                
                with tabs[0]:
                    _display_financial_records(company.siren)
                
                with tabs[1]:
                    _display_formality_records(company.siren)
                
                with tabs[2]:
                    _display_full_profile(company.siren, company.name)
            else:
                st.error(f"❌ Company with SIREN {siren_clean} not found")
    
    # ─── Information Panel ───────────────────────────────────────────────────
    st.markdown("---")
    
    with st.expander("ℹ️ About French Registry (INPI)", expanded=False):
        st.markdown("""
        ### What is INPI?
        
        **INPI** (Institut National de la Propriété Industrielle) is the official French Registry
        that maintains records of:
        
        **Key Information Provided:**
        - **Company Registration**: Legal name, SIREN, SIRET, legal form
        - **Contact Details**: Address, postal code, city
        - **Status**: Current operational status (Active, Inactive, etc.)
        - **Financial Records**: Annual financial statements (comptes annuels)
        - **Formality Documents**: Regulatory filings and formal declarations
        
        **What is SIREN?**
        
        SIREN (Système d'Identification du Répertoire des Entreprises) is a 9-digit number
        uniquely identifying each French business entity.
        
        Example: `732043259` (Michelin & Cie)
        
        **How to Use This Tool:**
        
        1. **Search by Name**: Enter the company name (searches registered legal name)
        2. **Search by SIREN**: For precise lookups, use the 9-digit SIREN number
        3. **View Records**: Access financial statements and formality documents
        4. **Export Data**: View full company profile information
        
        **Data Availability:**
        
        - Recent registrations may take a few days to appear
        - Some information may be restricted for privacy reasons
        - Financial records are typically available after filing deadlines
        
        **For More Information:**
        
        Visit: https://www.inpi.fr/ (French) or https://www.inpi.fr/en (English)
        """)


# ─── HELPER FUNCTIONS ───────────────────────────────────────────────────────

def _display_company_details(company: FrenchCompanyBasic):
    """Display company basic information."""
    
    col1, col2, col3 = st.columns(3)
    
    with col1:
        st.metric("Company Name", company.name)
    
    with col2:
        st.metric("SIREN", company.siren)
    
    with col3:
        st.metric("Legal Form", company.legal_form)
    
    # Detailed information
    details_col1, details_col2 = st.columns(2)
    
    with details_col1:
        st.markdown(f"""
        **Status:** {company.status}
        
        **Created:** {company.creation_date or 'N/A'}
        """)
    
    with details_col2:
        st.markdown(f"""
        **Address:** {company.address or 'N/A'}
        
        **City:** {company.city or 'N/A'} {company.postal_code or ''}
        """)


def _display_financial_records(siren: str):
    """Display company financial records."""
    
    with st.spinner("📊 Loading financial records..."):
        records = get_financial_records(siren)
    
    if records:
        st.success(f"✅ Found {len(records)} financial records")
        
        # Create a table of financial records
        for record in records:
            with st.expander(
                f"📄 {record.document_type} ({record.filing_year})",
                expanded=(records.index(record) == 0 if isinstance(records, list) else False)
            ):
                col1, col2, col3 = st.columns(3)
                
                with col1:
                    st.metric("Filing Year", record.filing_year)
                
                with col2:
                    st.metric("Filing Date", record.filing_date)
                
                with col3:
                    st.metric("Status", record.status)
                
                if record.url:
                    st.markdown(f"[📥 Download Document]({record.url})")
                else:
                    st.info("Document URL not available")
    else:
        st.info("📭 No financial records found for this company")


def _display_formality_records(siren: str):
    """Display company formality records."""
    
    with st.spinner("📄 Loading formality records..."):
        records = get_formality_records(siren)
    
    if records:
        st.success(f"✅ Found {len(records)} formality records")
        
        for idx, record in enumerate(records):
            with st.expander(
                f"📋 {record.formality_type} ({record.filing_date})",
                expanded=(idx == 0)
            ):
                st.markdown(f"**Type:** {record.formality_type}")
                st.markdown(f"**Date:** {record.filing_date}")
                st.markdown(f"**Description:** {record.description}")
                
                if record.url:
                    st.markdown(f"[📥 View Document]({record.url})")
    else:
        st.info("📭 No formality records found for this company")


def _display_full_profile(siren: str, company_name: str):
    """Display complete company profile."""
    
    with st.spinner("⏳ Loading complete profile..."):
        profile = get_french_registry_profile(company_name)
    
    if profile.get('found'):
        st.success("✅ Complete profile loaded")
        
        # Display as JSON for full transparency
        st.json(profile)
        
        # Download button
        import json
        profile_json = json.dumps(profile, indent=2, default=str, ensure_ascii=False)
        
        st.download_button(
            label="📥 Download Profile (JSON)",
            data=profile_json,
            file_name=f"french_registry_{siren}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json",
            mime="application/json",
            use_container_width=True
        )
    else:
        st.error(f"❌ Could not load complete profile: {profile.get('error', 'Unknown error')}")


if __name__ == "__main__":
    display_french_registry_search()
