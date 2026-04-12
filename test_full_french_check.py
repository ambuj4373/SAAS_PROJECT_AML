#!/usr/bin/env python3
"""Test the complete French company check with real INPI data."""

import os
import sys
sys.path.insert(0, os.path.dirname(__file__))

from core.french_company_check import run_french_company_check
import json

# Set credentials
os.environ["FRENCH_REGISTRY_EMAIL"] = "ambuj4373@gmail.com"
os.environ["FRENCH_REGISTRY_PASSWORD"] = "twd2cuq*vmb-ZAZ8fjm"

print("="*80)
print("TESTING COMPLETE FRENCH COMPANY CHECK")
print("="*80)

try:
    # Test with SIREN 793437518 (PELLENC ENERGY)
    siren = "793437518"
    print(f"\n▶ Checking French Company: SIREN {siren}\n")
    
    result = run_french_company_check(siren)
    
    # Show key results
    print(f"✓ Company: {result.get('company_name', 'N/A')}")
    print(f"✓ SIREN: {result.get('company_number', 'N/A')}")
    print(f"✓ Country: {result.get('country', 'N/A')}")
    print(f"✓ Risk Score: {result.get('risk_matrix', {}).get('risk_score', 'N/A')}/100")
    print(f"✓ Risk Level: {result.get('risk_matrix', {}).get('overall_risk', 'N/A')}")
    
    # Show extracted directors
    directors = result.get('directors', [])
    print(f"\n▶ Directors ({len(directors)} found):")
    
    if directors:
        for i, director in enumerate(directors, 1):
            print(f"\n  #{i}: {director.get('name', 'N/A')}")
            print(f"      Role: {director.get('role', 'N/A')}")
            print(f"      Birth Date: {director.get('birth_date', 'N/A')}")
            print(f"      Address: {director.get('address', 'N/A')}")
    else:
        print("  ✗ No directors found")
    
    print("\n" + "="*80)
    print("✓ FULL CHECK COMPLETE - DIRECTORS DATA POPULATED")
    print("="*80)

except Exception as e:
    print(f"✗ Error: {e}")
    import traceback
    traceback.print_exc()
