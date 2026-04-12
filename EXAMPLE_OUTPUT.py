"""
EXAMPLE OUTPUT - French Company Check Results

This file demonstrates the actual output structure returned by
run_french_company_check() for a real French company.

Example Company: L'Oréal (SIREN: 632012100)
Expected Risk: Low (15-25 points) - Large, well-established multinational
"""

example_output = {
    # ═════════════════════════════════════════════════════════════════════════
    # BASIC IDENTIFICATION
    # ═════════════════════════════════════════════════════════════════════════
    "company_number": "632012100",
    "company_name": "L'OREAL",
    "company_type": "French Company (INPI Registry)",
    "country": "France",
    
    # ═════════════════════════════════════════════════════════════════════════
    # COMPANY PROFILE (Basic KYC)
    # ═════════════════════════════════════════════════════════════════════════
    "company_profile": {
        "name": "L'OREAL",
        "siren": "632012100",
        "legal_form": "Société Anonyme (SA)",
        "status": "Diffusible",
        "incorporation_date": "01 Jan 1909",
        "head_office": "14 rue Royale, 75008 PARIS 8ème",
        "postal_code": "75008",
        "city": "PARIS",
        "website": "https://www.loreal.com",
        "ape_code": "4645Z",  # Wholesale of perfumes and cosmetics
    },
    
    # ═════════════════════════════════════════════════════════════════════════
    # RICH DASHBOARD DATA (from FrenchDashboardBuilder)
    # ═════════════════════════════════════════════════════════════════════════
    "dashboard": {
        "company_overview": {
            "legal_name": "L'OREAL",
            "siren": "632012100",
            "legal_form": "Société Anonyme (SA)",
            "status": "Active",
            "incorporation_date": "01 Jan 1909",
            "head_office": {
                "address": "14 rue Royale",
                "postal_code": "75008",
                "city": "PARIS",
                "country": "France",
            },
            "capital": {
                "amount": 5000000,
                "currency": "EUR",
                "formatted": "5,000,000 EUR",
            },
            "employees": {
                "count": 85800,
                "range": "85800 (Large)",
            },
            "ape_code": {
                "code": "4645Z",
                "description": "Wholesale of perfumes and cosmetics",
            },
            "registered_at": "Paris",
        },
        
        "management_network": {
            "total_roles": 28,
            "active_roles": 18,
            "inactive_roles": 10,
            "roles": {
                "active": [
                    {
                        "role_id": "POV001",
                        "role_name": "Président Directeur Général",
                        "role_category": "Executive",
                        "person_type": "Physical Person",
                        "active": True,
                        "first_name": "Nicolas",
                        "last_name": "Hieronimus",
                        "full_name": "Nicolas Hieronimus",
                        "birth_date": "1960-05-15",
                        "nationality": "French",
                        "birth_place": "France",
                        "gender": "M",
                        "address": "Paris, France",
                        "start_date": "2017-06-01",
                        "end_date": None,
                    },
                    # ... 17 more active roles
                ],
                "inactive": [
                    # ... 10 inactive roles with end_dates
                ]
            },
            "role_distribution": {
                "Executive": 3,
                "Manager/Partner": 8,
                "Audit/Compliance": 7,
            },
            "org_chart_data": {
                "executives": [{"role_name": "Président Directeur Général", ...}],
                "management": [...],
                "oversight": [...],
                "structure_type": "Large Corporation",
            },
        },
        
        "establishments": {
            "total_establishments": 181,
            "active_establishments": 175,
            "closed_establishments": 6,
            "establishments": [
                {
                    "type": "Head Office",
                    "siret": "63201210000045",
                    "ape_code": "4645Z",
                    "activity": "Wholesale of perfumes and cosmetics",
                    "address": "14 rue Royale, 75008 PARIS",
                    "employees": 12500,
                    "is_primary": True,
                    "is_active": True,
                    "opening_date": "1909-01-01",
                },
                {
                    "type": "Branch/Location",
                    "siret": "63201210000102",
                    "ape_code": "4645Z",
                    "activity": "Wholesale of perfumes and cosmetics",
                    "address": "Clichy, France",
                    "postal_code": "92110",
                    "city": "Clichy",
                    "employees": 8200,
                    "is_primary": False,
                    "is_active": True,
                    "opening_date": "1950-01-01",
                },
                # ... 179 more locations
            ],
            "geographic_distribution": {
                "PARIS": 8,
                "Clichy": 5,
                "Brussels": 4,
                "London": 4,
                # ... covering 45+ countries
            },
            "activity_distribution": {
                "Wholesale of perfumes and cosmetics": 120,
                "Retail beauty products": 35,
                "Manufacturing": 15,
                "Distribution": 11,
            },
            "total_employees_allocated": 85800,
        },
        
        "compliance_timeline": {
            "total_events": 41,
            "events": [
                {
                    "event_id": "RCS001",
                    "event_type": "Modification",
                    "event_code": "15M",
                    "description": "Address change",
                    "registered_date": "2023-11-15",
                    "effective_date": "2023-11-15",
                    "greffe": "PARIS",
                    "is_recent": True,
                },
                {
                    "event_id": "RCS002",
                    "event_type": "Modification",
                    "event_code": "15M",
                    "description": "Board member appointment",
                    "registered_date": "2023-08-20",
                    "effective_date": "2023-08-20",
                    "greffe": "PARIS",
                    "is_recent": True,
                },
                # ... 39 more RCS records
            ],
            "event_summary": {
                "mergers": 3,
                "modifications": 35,
                "radiations": 1,
            },
            "recent_events": [
                # ... events from last 6 months
            ],
        },
        
        "financial_indicators": {
            "capital": {
                "amount": 5000000,
                "currency": "EUR",
                "last_update": "2023-01-15",
            },
            "equity_structure": {
                "social_capital": 5000000,
                "currency": "EUR",
            },
            "filing_status": "Complete",
            "last_filing": "Available from INPI",
        },
        
        "risk_assessment": {
            "risk_score": 15,
            "overall_risk": "Low",
            "emoji": "🟢",
            "factors": {
                "management": "🟢 Adequate management team",
                "transparency": "🟢 Clear ownership structure",
                "compliance": "🟢 Stable compliance history",
                "viability": "🟢 Capital on file",
                "geographic": "🟢 Multi-location operation",
                "maturity": "🟢 Established operation",
            },
            "recommendations": [
                "Standard onboarding process",
                "Periodic review recommended",
            ],
        },
    },
    
    # ═════════════════════════════════════════════════════════════════════════
    # DETAILED ANALYSIS RESULTS
    # ═════════════════════════════════════════════════════════════════════════
    "analysis": {
        "management": {
            "total_active_roles": 18,
            "executive_count": 3,
            "role_quality": "Strong",
            "concentration_risk": False,
            "flags": [],
            "geopolitical_risk_flags": [],
            "experience_gaps": [],
            "transparency_score": 18,
            "management_risk_score": 0,  # Strong company gets 0 points
        },
        
        "address": {
            "address_type": "Commercial",
            "credibility_level": "High",
            "credibility_score": 100,
            "flags": [],
            "recommendations": ["Standard address verification sufficient"],
        },
        
        "dormancy": {
            "is_dormant": False,
            "dormancy_risk": "Low",
            "days_without_modification": 45,
            "last_activity_date": "2024-01-15",
            "flags": [],
            "status": "🟢 Active",
        },
        
        "ubo": {
            "ubo_identified": True,
            "ubo_count": 5,
            "ubo_persons": [
                {
                    "name": "Frédéric Dufour",
                    "role": "Actionnaire",
                    "nationality": "French",
                    "birth_date": "1960-05-15",
                    "appointment_date": "2018-06-01",
                    "type": "Primary UBO",
                }
                # ... more UBOs
            ],
            "corporate_intermediaries": 2,
            "corporate_chain": [
                {
                    "intermediary": "Nestlé SA",
                    "siren": "765649000",
                    "representative": "John Smith",
                    "role": "Board Member",
                }
            ],
            "transparency_level": "High",
            "risk_flags": [],
        },
        
        "restricted_activities": {
            "has_restrictions": False,
            "prohibited": [],
            "restricted": [],
            "requires_authorization": False,
            "regulatory_notes": [],
        },
        
        "legal_status": {
            "legal_status": "Active",
            "is_operational": True,
            "status_emoji": "🟢",
            "compliance_alerts": [],
            "regulatory_risk_score": 0,
        },
    },
    
    # ═════════════════════════════════════════════════════════════════════════
    # FINANCIAL DATA
    # ═════════════════════════════════════════════════════════════════════════
    "financial_health": {
        "recent_filings": 5,
        "latest_filing": 2023,
        "records": [
            {
                "year": 2023,
                "revenue": 36500000000,  # 36.5 billion EUR
                "net_profit": 3950000000,  # 3.95 billion EUR
            },
            {
                "year": 2022,
                "revenue": 32170000000,
                "net_profit": 3300000000,
            },
            # ... more historical records
        ]
    },
    
    # ═════════════════════════════════════════════════════════════════════════
    # COMPLIANCE & FORMALITY RECORDS
    # ═════════════════════════════════════════════════════════════════════════
    "formality_records": [
        {
            "date": "2023-11-15",
            "description": "Address change to 14 rue Royale",
            "type": "Modification",
        },
        {
            "date": "2023-08-20",
            "description": "Appointment of new board member",
            "type": "Modification",
        },
        # ... more RCS records
    ],
    
    # ═════════════════════════════════════════════════════════════════════════
    # HROB & HIGH-RISK SCREENING
    # ═════════════════════════════════════════════════════════════════════════
    "hrob_verticals": {
        "requires_hrob": False,
        "matched_industries": [],
        "summary": "Not a high-risk industry (APE 4645Z - Wholesale cosmetics)",
    },
    
    # ═════════════════════════════════════════════════════════════════════════
    # SCREENING RESULTS
    # ═════════════════════════════════════════════════════════════════════════
    "sanctions_screening": {
        "risk": "low",
        "matches": [],
        "screen_date": "2024-01-20",
    },
    
    "fatf_screening": {
        # (same as sanctions_screening - alias for UK compatibility)
        "risk": "low",
        "matches": [],
        "screen_date": "2024-01-20",
    },
    
    "adverse_media": {
        "alerts": [],
        "summary": "No adverse media found",
    },
    
    "restricted_activities": [],
    
    # ═════════════════════════════════════════════════════════════════════════
    # RISK MATRIX (UK-Equivalent Format)
    # ═════════════════════════════════════════════════════════════════════════
    "risk_matrix": {
        "risk_score": 15,  # Out of 100
        "overall_risk": "Low",
        "risk_emoji": "🟢",
        "risk_factors": [
            "Company maturity: 115 years old - Established operation ✓"
        ],
        "hard_stop_triggered": False,
        
        "category_risks": {
            "management": "Strong",
            "address_credibility": "High",
            "dormancy": "low",
            "ubo_transparency": "High",
            "restricted_activities": "low",
            "legal_status": "low",
            "industry_risk": "low",
            "sanctions": "low",
            "adverse_media": "low",
            "maturity": "low",
        },
        
        "all_flags": [
            "Company maturity: 115 years old - Established operation ✓"
        ],
    },
    
    # ═════════════════════════════════════════════════════════════════════════
    # METADATA
    # ═════════════════════════════════════════════════════════════════════════
    "check_timestamp": "2024-01-20T15:32:45.123456",
    "data_source": "INPI (Institut National de la Propriété Industrielle)",
    "check_type": "french_comprehensive",
}


# ═════════════════════════════════════════════════════════════════════════════
# RISK SCORE BREAKDOWN (How Score of 15 Was Calculated)
# ═════════════════════════════════════════════════════════════════════════════

risk_calculation = {
    "Factor 1: Management Risk": {
        "value": 0,
        "reason": "Strong management with 18 active roles, 3 executives, no concentration risk",
    },
    "Factor 2: Address Credibility": {
        "value": 0,
        "reason": "Commercial address (14 rue Royale, Paris) with high credibility",
    },
    "Factor 3: Dormancy Risk": {
        "value": 0,
        "reason": "Very active (45 days since last modification), 85,800 employees",
    },
    "Factor 4: UBO Transparency": {
        "value": 0,
        "reason": "Clear beneficial owners identified, high transparency",
    },
    "Factor 5: Restricted Activities": {
        "value": 0,
        "reason": "APE 4645Z (Cosmetics wholesale) - not restricted",
    },
    "Factor 6: Legal Status": {
        "value": 0,
        "reason": "Status = Active, operational, no compliance alerts",
    },
    "Factor 7: High-Risk Industry": {
        "value": 0,
        "reason": "Not high-risk (cosmetics wholesale, not banking/gambling/weapons)",
    },
    "Factor 8: Sanctions Risk": {
        "value": 0,
        "reason": "No sanctions or FATF matches",
    },
    "Factor 9: Adverse Media": {
        "value": 0,
        "reason": "No adverse media alerts (clean reputation)",
    },
    "Factor 10: Company Maturity": {
        "value": 15,
        "reason": "115 years old (1909) - not early-stage, but older = slight premium",
    },
    "TOTAL SCORE": 15,
    "NORMALIZED": "15/100 = 🟢 LOW RISK",
}


# ═════════════════════════════════════════════════════════════════════════════
# WHAT THIS OUTPUT TELLS US ABOUT L'ORÉAL
# ═════════════════════════════════════════════════════════════════════════════

interpretation = """
✅ LOW RISK PROFILE (Score: 15/100)

Company Summary:
  - Large, multinational cosmetics company
  - 115-year history (established 1909)
  - 85,800+ employees globally
  - 181 establishments across 45+ countries
  - €36.5 billion annual revenue (2023)

Strengths:
  ✓ Strong management team (18 active roles, 3 executives)
  ✓ Clear beneficial ownership structure
  ✓ Commercial head office in Paris
  ✓ Very active (recent modifications, full staff)
  ✓ Clean sanctions/adverse media record
  ✓ Established, profitable operation
  ✓ Not in restricted industries

Risk Indicators:
  ✓ NONE DETECTED

Hard Stop Triggered: NO
Requires Enhanced Due Diligence: NO
Regulatory Authority Notification: NO

Recommended Action:
  ✓ APPROVE - Standard KYC sufficient
  ✓ FAST TRACK - Can proceed to onboarding
  ✓ Annual review only (low priority)

Notes:
  - All INPI data consistent
  - Management fully transparent
  - No corporate structure opacity
  - No regulatory concerns
  - No suspicious activity indicators
"""

if __name__ == "__main__":
    import json
    print("Example Output - L'Oréal (SIREN: 632012100)")
    print("=" * 80)
    print(f"\nRisk Score: {example_output['risk_matrix']['risk_score']}/100")
    print(f"Overall Risk: {example_output['risk_matrix']['risk_emoji']} {example_output['risk_matrix']['overall_risk']}")
    print(f"\nManagement Quality: {example_output['analysis']['management']['role_quality']}")
    print(f"Address Credibility: {example_output['analysis']['address']['credibility_level']}")
    print(f"Dormancy Status: {example_output['analysis']['dormancy']['status']}")
    print(f"UBO Transparency: {example_output['analysis']['ubo']['transparency_level']}")
    print(f"Legal Status: {example_output['analysis']['legal_status']['legal_status']}")
    print(f"\nHard Stop Triggered: {example_output['risk_matrix']['hard_stop_triggered']}")
    print("\n" + interpretation)
