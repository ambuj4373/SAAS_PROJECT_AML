#!/usr/bin/env python3
"""
Test recursive UBO tracing with actual INPI API.
"""

import os
import sys
sys.path.insert(0, os.path.dirname(__file__))

from api_clients.french_registry import FrenchRegistryClient

# Set credentials
os.environ["FRENCH_REGISTRY_EMAIL"] = "ambuj4373@gmail.com"
os.environ["FRENCH_REGISTRY_PASSWORD"] = "twd2cuq*vmb-ZAZ8fjm"

print("="*80)
print("TESTING RECURSIVE UBO TRACING")
print("="*80)

try:
    client = FrenchRegistryClient()
    
    # Test with SIREN 793437518 (PELLENC ENERGY)
    siren = "793437518"
    print(f"\n▶ Testing UBO tracing for SIREN: {siren}\n")
    
    # Get management roles with recursive lookup
    roles = client.get_management_roles(siren, max_depth=3)
    
    if roles:
        print(f"✓ Found {len(roles)} management roles\n")
        
        for i, role in enumerate(roles, 1):
            person_type = role.get("person_type")
            print(f"Director #{i}:")
            print(f"  Person type: {person_type}")
            
            if person_type == "INDIVIDU":
                print(f"  Name: {role.get('full_name', 'N/A')}")
                print(f"  Role: {role.get('role_name', 'N/A')}")
                print(f"  Birth: {role.get('birth_date', 'N/A')}")
                print(f"  Is Ultimate Owner: {role.get('is_ultimate_owner', False)}")
            
            elif person_type == "Legal Entity":
                print(f"  Company: {role.get('company_name', 'N/A')}")
                print(f"  SIREN: {role.get('company_siren', 'N/A')}")
                print(f"  Role: {role.get('role_name', 'N/A')}")
                print(f"  Is Ultimate Owner: {role.get('is_ultimate_owner', False)}")
                print(f"  Has UBO Chain: {role.get('has_ubo_info', False)}")
                
                # Show UBO chain if available
                if role.get("has_ubo_info"):
                    ubo_chain = role.get("ubo_chain", [])
                    print(f"  UBO Chain ({len(ubo_chain)} entries):")
                    
                    for j, ubo in enumerate(ubo_chain, 1):
                        ubo_type = ubo.get("person_type")
                        if ubo_type == "INDIVIDU":
                            print(f"    {j}. {ubo.get('full_name', 'N/A')} (PHYSICAL PERSON)")
                        else:
                            print(f"    {j}. {ubo.get('company_name', 'N/A')} (COMPANY)")
                
                elif role.get("recursion_limit_reached"):
                    print(f"  Recursion limit reached at depth {role.get('depth', 0)}")
            
            print()
    else:
        print("✗ No management roles found")
    
    print("="*80)
    print("✓ UBO TRACING TEST COMPLETE")
    print("="*80)

except Exception as e:
    print(f"✗ Error: {e}")
    import traceback
    traceback.print_exc()
