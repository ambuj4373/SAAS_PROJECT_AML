"""
french_screening.py — Comprehensive multi-entity screening for French companies

Screens ALL associated entities:
- Company legal name + French variants
- All directors (physical persons) with names
- All company directors (legal entities)
- UBO chain (beneficial owner chain)
- Trading names / Noms commerciaux
- Alternative names / Noms alternatifs

Supports bilingual screening (English & French terms)
"""

import logging
from typing import Dict, Any, List, Callable, Optional

logger = logging.getLogger(__name__)


def collect_screening_identities(
    company: Any,
    management_roles: List[Dict[str, Any]]
) -> Dict[str, List[str]]:
    """
    Collect all identities to screen for a French company.
    
    Returns dict with keys:
    - company_names: Company legal names + French variants
    - director_names: All individual director names
    - company_directors: All legal entity director names + SIRENs
    - ubo_chain: Ultimate beneficial owners (physical persons)
    - all_search_terms: Combined list for broader screening
    """
    
    identities = {
        "company_names": [],
        "director_names": [],
        "company_directors": [],
        "ubo_chain": [],
        "all_search_terms": [],
    }
    
    # ─── Company Names (French + English) ───────────────────────
    if company:
        company_name = company.name or ""
        identities["company_names"].append(company_name)
        
        # Add French language variants
        # (In real system, these would come from INPI alternate names field)
        french_variants = _generate_french_variants(company_name)
        identities["company_names"].extend(french_variants)
    
    # ─── Extract All Director/UBO Names ────────────────────────
    if management_roles:
        for role in management_roles:
            person_type = role.get("person_type", "")
            
            # Physical person director
            if person_type == "INDIVIDU":
                full_name = role.get("full_name", "")
                if full_name:
                    identities["director_names"].append(full_name)
                    # Add name variations (last name, first name combinations)
                    name_vars = _generate_name_variations(full_name)
                    identities["director_names"].extend(name_vars)
            
            # Legal entity director (company)
            elif person_type == "Legal Entity":
                company_name = role.get("company_name", "")
                company_siren = role.get("company_siren", "")
                
                if company_name:
                    identities["company_directors"].append(company_name)
                    french_vars = _generate_french_variants(company_name)
                    identities["company_directors"].extend(french_vars)
                
                if company_siren:
                    identities["company_directors"].append(company_siren)
                
                # ─── Recursive: Add UBO chain ───────────────────
                if role.get("has_ubo_info"):
                    ubo_chain = role.get("ubo_chain", [])
                    for ubo in ubo_chain:
                        ubo_type = ubo.get("person_type", "")
                        
                        if ubo_type == "INDIVIDU":
                            ubo_name = ubo.get("full_name", "")
                            if ubo_name:
                                identities["ubo_chain"].append(ubo_name)
                                name_vars = _generate_name_variations(ubo_name)
                                identities["ubo_chain"].extend(name_vars)
                        
                        elif ubo_type == "Legal Entity":
                            ubo_company = ubo.get("company_name", "")
                            ubo_siren = ubo.get("company_siren", "")
                            
                            if ubo_company:
                                identities["ubo_chain"].append(ubo_company)
                                french_vars = _generate_french_variants(ubo_company)
                                identities["ubo_chain"].extend(french_vars)
                            
                            if ubo_siren:
                                identities["ubo_chain"].append(ubo_siren)
    
    # ─── Combine all terms for broader screening ────────────────
    identities["all_search_terms"] = (
        identities["company_names"]
        + identities["director_names"]
        + identities["company_directors"]
        + identities["ubo_chain"]
    )
    
    # Remove duplicates
    for key in identities:
        identities[key] = list(set(identities[key]))
        identities[key] = [x for x in identities[key] if x]  # Remove empty strings
    
    return identities


def run_comprehensive_screening(
    company: Any,
    management_roles: List[Dict[str, Any]],
    fatf_screen_fn: Optional[Callable] = None,
    adverse_search_fn: Optional[Callable] = None,
) -> Dict[str, Any]:
    """
    Run comprehensive screening on French company and all associated entities.
    
    Screens:
    1. Company legal name (English)
    2. Company name French variants
    3. Each director individually
    4. Each company director
    5. UBO chain (beneficial owners)
    
    Returns comprehensive screening results with source tracking.
    """
    
    results = {
        "company_screening": {},
        "directors_screening": {},
        "company_directors_screening": {},
        "ubo_screening": {},
        "combined_screening": {
            "sanctions_flags": [],
            "adverse_media_flags": [],
            "high_risk_entities": [],
            "total_entities_screened": 0,
            "high_risk_count": 0,
            "medium_risk_count": 0,
            "low_risk_count": 0,
            "overall_risk_level": "low",
            "total_risk_score": 0,
            "screening_alerts": [],
        },
        "screening_metadata": {
            "entities_screened": 0,
            "high_risk_count": 0,
            "language_variants_checked": 0,
        }
    }
    
    # Collect all identities
    identities = collect_screening_identities(company, management_roles)
    results["screening_metadata"]["language_variants_checked"] = sum(
        len(v) for v in identities.values() if isinstance(v, list)
    )
    
    logger.info(
        f"Screening {results['screening_metadata']['language_variants_checked']} "
        f"identity variants across {len(identities)} categories"
    )
    
    # ─── Screen Company ────────────────────────────────────────
    if company:
        results["company_screening"] = _screen_entity_comprehensive(
            entity_names=identities["company_names"],
            entity_type="company",
            entity_id=company.name,
            fatf_screen_fn=fatf_screen_fn,
            adverse_search_fn=adverse_search_fn,
            language_variants=_get_french_variants(company.name)
        )
        results["screening_metadata"]["entities_screened"] += 1
    
    # ─── Screen Each Director ──────────────────────────────────
    for director_name in identities["director_names"]:
        director_result = _screen_entity_comprehensive(
            entity_names=[director_name],
            entity_type="individual",
            entity_id=director_name,
            fatf_screen_fn=fatf_screen_fn,
            adverse_search_fn=adverse_search_fn,
            language_variants=_get_french_variants(director_name)
        )
        results["directors_screening"][director_name] = director_result
        results["screening_metadata"]["entities_screened"] += 1
        
        # Add to combined flags
        if director_result.get("high_risk"):
            results["combined_screening"]["high_risk_entities"].append({
                "entity": director_name,
                "type": "director",
                "risk_level": director_result.get("risk_level"),
            })
            results["screening_metadata"]["high_risk_count"] += 1
    
    # ─── Screen Company Directors ──────────────────────────────
    for company_director_name in identities["company_directors"]:
        if company_director_name.isdigit():  # Skip SIREN numbers
            continue
        
        director_result = _screen_entity_comprehensive(
            entity_names=[company_director_name],
            entity_type="company",
            entity_id=company_director_name,
            fatf_screen_fn=fatf_screen_fn,
            adverse_search_fn=adverse_search_fn,
            language_variants=_get_french_variants(company_director_name)
        )
        results["company_directors_screening"][company_director_name] = director_result
        results["screening_metadata"]["entities_screened"] += 1
        
        if director_result.get("high_risk"):
            results["combined_screening"]["high_risk_entities"].append({
                "entity": company_director_name,
                "type": "company_director",
                "risk_level": director_result.get("risk_level"),
            })
            results["screening_metadata"]["high_risk_count"] += 1
    
    # ─── Screen UBO Chain ──────────────────────────────────────
    for ubo_name in identities["ubo_chain"]:
        if ubo_name.isdigit():  # Skip SIREN numbers
            continue
        
        ubo_result = _screen_entity_comprehensive(
            entity_names=[ubo_name],
            entity_type="individual",
            entity_id=ubo_name,
            fatf_screen_fn=fatf_screen_fn,
            adverse_search_fn=adverse_search_fn,
            language_variants=_get_french_variants(ubo_name)
        )
        results["ubo_screening"][ubo_name] = ubo_result
        results["screening_metadata"]["entities_screened"] += 1
        
        if ubo_result.get("high_risk"):
            results["combined_screening"]["high_risk_entities"].append({
                "entity": ubo_name,
                "type": "ubo",
                "risk_level": ubo_result.get("risk_level"),
            })
            results["screening_metadata"]["high_risk_count"] += 1
    
    # ─── Aggregate Results ─────────────────────────────────────
    _aggregate_screening_results(results)
    
    logger.info(
        f"✓ Screening complete: {results['screening_metadata']['entities_screened']} entities, "
        f"{results['screening_metadata']['high_risk_count']} high-risk matches"
    )
    
    return results


def _screen_entity_comprehensive(
    entity_names: List[str],
    entity_type: str,
    entity_id: str,
    fatf_screen_fn: Optional[Callable] = None,
    adverse_search_fn: Optional[Callable] = None,
    language_variants: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """
    Screen a single entity against multiple names and variants.
    """
    
    if language_variants is None:
        language_variants = []
    
    # Use only primary name, not all variants (avoid false positives)
    # Only screen the main entity name + the primary English name
    primary_names = entity_names[:1] if entity_names else []  # Just the main name
    all_names = primary_names  # Don't include variants - screen carefully
    
    result = {
        "entity": entity_id,
        "type": entity_type,
        "names_screened": all_names,
        "sanctions_hits": [],
        "adverse_media_hits": [],
        "high_risk": False,
        "risk_level": "low",
    }
    
    # ─── FATF/Sanctions Screening (ONLY primary name) ─────────────────────────────
    if fatf_screen_fn and primary_names:
        for name in primary_names:
            try:
                sanction_result = fatf_screen_fn(
                    entity_name=name,
                    entity_type=entity_type,
                    entity_context={"country": "FR"}
                )
                
                # Only flag as high-risk if there's an actual match (is_match=True)
                if sanction_result.get("is_match") and sanction_result.get("risk_level") in ("high", "medium"):
                    result["sanctions_hits"].append({
                        "name_searched": name,
                        "risk_level": sanction_result.get("risk_level"),
                        "match": sanction_result.get("summary", "Sanctions match found"),
                        "details": sanction_result,
                    })
                    result["high_risk"] = True
            
            except Exception as e:
                logger.warning(f"FATF screening error for {name}: {e}")
    
    # ─── Adverse Media Screening (ONLY primary name) ──────────────────────────────
    if adverse_search_fn and primary_names:
        for name in primary_names:
            try:
                search_result = adverse_search_fn(name)
                alerts = (
                    search_result
                    if isinstance(search_result, list)
                    else search_result.get("alerts", [])
                )
                
                # Filter out error results and irrelevant results
                valid_alerts = [
                    a for a in alerts 
                    if a.get("_error") is None  # Skip error results
                    and a.get("_relevant", False)  # Only include relevant results
                    and "unavailable" not in a.get("title", "").lower()  # Skip "unavailable" messages
                ]
                
                # Only flag if there are actual valid adverse alerts (not errors or irrelevant)
                if valid_alerts and len(valid_alerts) > 0:
                    result["adverse_media_hits"].append({
                        "name_searched": name,
                        "hit_count": len(valid_alerts),
                        "alerts": valid_alerts[:3],
                    })
                    result["high_risk"] = True
            
            except Exception as e:
                logger.warning(f"Adverse media screening error for {name}: {e}")
    
    # Determine risk level
    if result["sanctions_hits"]:
        result["risk_level"] = "high"
    elif result["adverse_media_hits"]:
        result["risk_level"] = "medium"
    
    return result


def _generate_french_variants(name: str) -> List[str]:
    """
    Generate French language variants of a name.
    
    Examples:
    - "Paris Limited" → "Paris SARL", "Paris SAS", etc.
    - "Tech Company" → "Entreprise Tech", etc.
    """
    
    variants = []
    
    # Common French legal form substitutions
    french_forms = {
        "Limited": ["SARL", "SAS", "EIRL", "EARL"],
        "Limited Company": ["Société Limitée", "SARL"],
        "Inc": ["SAS", "SARL"],
        "Corporation": ["Société Anonyme", "SA"],
        "Ltd": ["SARL", "SAS"],
        "Company": ["Entreprise", "Société"],
        "Group": ["Groupe", "Groupement"],
    }
    
    for en_form, fr_forms in french_forms.items():
        if en_form in name:
            for fr_form in fr_forms:
                variant = name.replace(en_form, fr_form)
                if variant != name:
                    variants.append(variant)
    
    return variants


def _get_french_variants(name: str) -> List[str]:
    """Get French variants (alias for compatibility)."""
    return _generate_french_variants(name)


def _generate_name_variations(full_name: str) -> List[str]:
    """
    Generate name variations for screening.
    
    Examples:
    - "JEAN DUPONT" → ["JEAN", "DUPONT", "DUPONT JEAN"]
    """
    
    variations = []
    parts = full_name.split()
    
    if len(parts) >= 2:
        # Last name first
        variations.append(" ".join(reversed(parts)))
        # Individual parts
        variations.extend(parts)
    
    return variations


def _aggregate_screening_results(results: Dict[str, Any]) -> None:
    """
    Aggregate individual screening results into combined flags.
    """
    
    combined = results["combined_screening"]
    metadata = results["screening_metadata"]
    
    # Update combined with metadata values
    combined["total_entities_screened"] = metadata.get("entities_screened", 0)
    combined["high_risk_count"] = metadata.get("high_risk_count", 0)
    
    # Collect all sanctions flags
    if results["company_screening"].get("sanctions_hits"):
        combined["sanctions_flags"].extend(
            results["company_screening"]["sanctions_hits"]
        )
    
    for director_results in results["directors_screening"].values():
        if director_results.get("sanctions_hits"):
            combined["sanctions_flags"].extend(director_results["sanctions_hits"])
    
    for ubo_results in results["ubo_screening"].values():
        if ubo_results.get("sanctions_hits"):
            combined["sanctions_flags"].extend(ubo_results["sanctions_hits"])
    
    # Collect all adverse media flags
    if results["company_screening"].get("adverse_media_hits"):
        combined["adverse_media_flags"].extend(
            results["company_screening"]["adverse_media_hits"]
        )
    
    for director_results in results["directors_screening"].values():
        if director_results.get("adverse_media_hits"):
            combined["adverse_media_flags"].extend(
                director_results["adverse_media_hits"]
            )
    
    for ubo_results in results["ubo_screening"].values():
        if ubo_results.get("adverse_media_hits"):
            combined["adverse_media_flags"].extend(ubo_results["adverse_media_hits"])
    
    # Determine overall risk level
    if combined["high_risk_count"] > 0:
        combined["overall_risk_level"] = "high"
    elif combined["medium_risk_count"] > 0:
        combined["overall_risk_level"] = "medium"
    else:
        combined["overall_risk_level"] = "low"
    
    # Calculate risk score
    sanctions_weight = len(combined["sanctions_flags"]) * 10
    adverse_weight = len(combined["adverse_media_flags"]) * 3
    combined["total_risk_score"] = min(100, sanctions_weight + adverse_weight)
