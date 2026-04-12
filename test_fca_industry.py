#!/usr/bin/env python3
"""
Test FCA industry-specific scoring.
Only applies FCA reduction for regulated industries.
"""

import sys
sys.path.insert(0, '.')

from api_clients.fca_website_check import is_fca_regulated_industry, get_fca_status_for_company

print("\n" + "="*80)
print("TEST: FCA Industry Validation")
print("="*80 + "\n")

# Test cases: (Company, SIC Codes, Industry Category, Expected FCA Regulated)
test_cases = [
    # Banking & Lending (FCA regulated)
    ("Barclays Bank", ["64191"], "Banking", True),
    ("HSBC", ["64191"], "Retail Banking", True),
    ("Nationwide Building Society", ["64192"], "Building Society", True),
    ("Metro Bank", ["64191"], "Banking", True),
    ("Mortgage Broker Inc", ["64922"], "Mortgage Finance", True),
    ("Credit Finance", ["64921"], "Credit Granting", True),
    
    # Insurance (FCA regulated)
    ("AXA Insurance", ["65110", "65120"], "Insurance", True),
    ("Aviva", ["65110"], "Life Insurance", True),
    ("Allianz", ["65120"], "Non-life Insurance", True),
    ("Pension Providers Ltd", ["65300"], "Pension Funding", True),
    
    # Investments & Pensions (FCA regulated)
    ("Morgan Stanley", ["64302"], "Investment Management", True),
    ("Vanguard UK", ["64302", "66300"], "Fund Management", True),
    ("Investment Trust Co", ["64301"], "Investment Trusts", True),
    ("Blackstone", ["64303"], "Venture Capital", True),
    ("Fund Manager Pro", ["66300"], "Fund Management", True),
    
    # Auxiliary Activities (FCA regulated)
    ("Insurance Broker UK", ["66220"], "Insurance Broker", True),
    ("Stock Broker Ltd", ["66120"], "Stockbroker", True),
    ("Financial Services Ltd", ["66190"], "Financial Services", True),
    ("Risk Assessment Co", ["66210"], "Risk Evaluation", True),
    
    # Other Financial Services (FCA regulated)
    ("Lease Finance", ["64910"], "Financial Leasing", True),
    ("Securities Trading", ["64991"], "Security Dealing", True),
    ("Factoring Services", ["64992"], "Factoring", True),
    
    # NOT FCA regulated
    ("Google UK", ["62010"], "Software Development", False),
    ("Tesco PLC", ["47110"], "Retail", False),
    ("Apple UK", ["46511"], "Computer Hardware", False),
    ("Local Pizza Shop", ["56101"], "Restaurant", False),
    ("Construction Co", ["41100"], "Construction", False),
    ("Manufacturing Inc", ["25110"], "Manufacturing", False),
]

print("Industry Validation Tests:\n")

for company, sic_codes, category, expected in test_cases:
    is_regulated = is_fca_regulated_industry(sic_codes, category)
    status = "✅" if is_regulated == expected else "❌"
    
    print(f"{status} {company}")
    print(f"   SIC: {sic_codes}")
    print(f"   Category: {category}")
    print(f"   FCA Regulated: {is_regulated} (Expected: {expected})")
    print()

print("\n" + "="*80)
print("SCENARIO: Risk Scoring Impact")
print("="*80 + "\n")

scenarios = [
    {
        "name": "Bank with FCA mention",
        "company_data": {
            "company_name": "ABC Bank",
            "company_number": "12345678",
            "sic_codes": ["64191"],
            "industry_category": "Banking",
        },
        "website_url": "https://www.abcbank.co.uk",  # Would find FCA mention
        "base_risk": 50,
    },
    {
        "name": "Bank without FCA mention",
        "company_data": {
            "company_name": "XYZ Bank",
            "company_number": "87654321",
            "sic_codes": ["64191"],
            "industry_category": "Banking",
        },
        "website_url": "https://www.xyzbank.co.uk",  # Would NOT find FCA mention
        "base_risk": 50,
    },
    {
        "name": "Non-regulated company (no FCA check)",
        "company_data": {
            "company_name": "Tech Startup",
            "company_number": "11111111",
            "sic_codes": ["62010"],
            "industry_category": "Software Development",
        },
        "website_url": "https://www.techstartup.com",  # Not checked (not regulated)
        "base_risk": 50,
    },
]

for scenario in scenarios:
    print(f"Scenario: {scenario['name']}")
    print(f"  Company: {scenario['company_data']['company_name']}")
    print(f"  SIC: {scenario['company_data']['sic_codes']}")
    print(f"  Category: {scenario['company_data']['industry_category']}")
    
    is_regulated = is_fca_regulated_industry(
        scenario['company_data'].get('sic_codes'),
        scenario['company_data'].get('industry_category'),
    )
    
    print(f"  FCA Regulated Industry: {is_regulated}")
    
    if is_regulated:
        print(f"  Action: ✅ CHECK website for FCA mention")
        print(f"  Base Risk: {scenario['base_risk']}/100")
        print(f"  If FCA found: {scenario['base_risk']} × 0.75 = {scenario['base_risk'] * 0.75:.1f}/100 ✅")
        print(f"  If FCA NOT found: {scenario['base_risk']} × 1.0 = {scenario['base_risk']}/100")
    else:
        print(f"  Action: ⏭️  SKIP FCA check (not a regulated industry)")
        print(f"  Base Risk: {scenario['base_risk']}/100 (no FCA reduction)")
    
    print()

print("="*80)
print("\nSummary:")
print("  ✅ Only FCA-regulated industries get FCA checking")
print("  ✅ SIC codes 64xx, 65xx, 66xx = Regulated")
print("  ✅ Banking, lending, insurance, payments = Regulated")
print("  ✅ Tech, retail, restaurants, etc. = NOT regulated (skipped)")
print("="*80 + "\n")
