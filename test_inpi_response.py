#!/usr/bin/env python3
"""
Quick test to see what INPI API actually returns for a company.
"""
import os
import json
from api_clients.french_registry import FrenchRegistryClient

# Test SIREN from your example
TEST_SIREN = "793437518"  # PELLENC ENERGY

try:
    print(f"Testing INPI API for SIREN {TEST_SIREN}...")
    client = FrenchRegistryClient()
    
    # Get raw response
    details = client.get_company_details(TEST_SIREN)
    
    if details:
        print("\n✅ GOT RESPONSE FROM INPI")
        print("\n" + "="*80)
        print("FULL RAW RESPONSE (formatted JSON):")
        print("="*80)
        print(json.dumps(details, indent=2, default=str)[:5000])  # First 5000 chars
        
        # Check for key fields
        print("\n" + "="*80)
        print("FIELD AVAILABILITY CHECK:")
        print("="*80)
        
        content = details.get("formality", {}).get("content", {})
        pm = content.get("personneMorale", {})
        
        print(f"✓ formality.content exists: {'content' in details.get('formality', {})}")
        print(f"✓ personneMorale exists: {'personneMorale' in content}")
        print(f"✓ composition exists: {'composition' in pm}")
        print(f"✓ identite exists: {'identite' in pm}")
        print(f"✓ observations exists: {'observations' in pm}")
        print(f"✓ établissementPrincipal exists: {'etablissementPrincipal' in pm}")
        print(f"✓ autresEtablissements exists: {'autresEtablissements' in pm}")
        
        # Check composition
        composition = pm.get("composition", {})
        print(f"\nComposition keys: {list(composition.keys())}")
        
        pouvoirs = composition.get("pouvoirs", [])
        print(f"Number of 'pouvoirs' (management roles): {len(pouvoirs)}")
        
        if pouvoirs:
            print(f"\nFirst pouvoir structure:")
            print(json.dumps(pouvoirs[0], indent=2, default=str)[:1000])
        
        # Check identite
        identite = pm.get("identite", {})
        print(f"\nIdentite keys: {list(identite.keys())}")
        
        entreprise = identite.get("entreprise", {})
        print(f"Entreprise keys: {list(entreprise.keys())}")
        
    else:
        print("❌ NO RESPONSE FROM INPI - Company not found or API error")
        
except Exception as e:
    print(f"❌ ERROR: {e}")
    import traceback
    traceback.print_exc()
