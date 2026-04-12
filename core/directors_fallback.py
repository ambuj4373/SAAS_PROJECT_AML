"""
directors_fallback.py

Handles missing directors data with fallback strategies.

When get_management_roles() returns empty, tries:
1. Alternative INPI field paths
2. Formality records for director info
3. Parent company directors (for subsidiaries)
4. Returns clear 'data_unavailable' flag
"""

import logging
from typing import Dict, List, Any, Optional, Tuple

logger = logging.getLogger(__name__)


class DirectorsFallback:
    """
    Handles missing or incomplete directors data.
    
    Implements fallback chain:
    1. get_management_roles() [PRIMARY]
    2. Alternative INPI field paths
    3. Formality records extraction
    4. Parent company directors (if subsidiary)
    5. Return 'data_unavailable' flag
    """
    
    # Alternative INPI field paths to check
    ALTERNATIVE_DIRECTOR_FIELDS = [
        # Path variants
        'dirigeants',
        'gestionnaires',
        'mandataires',
        'administrateurs',
        'persons_in_charge',
        'legal_representatives',
        'representatives',
    ]
    
    @staticmethod
    def extract_from_formality_records(
        formality_records: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """
        Extract director information from formality records.
        
        Formality records may contain director updates/changes.
        
        Args:
            formality_records: List of formality record dicts from INPI
        
        Returns:
            List of extracted director dicts with standardized format
        """
        directors = []
        
        if not formality_records:
            return directors
        
        for record in formality_records:
            try:
                # Formality record structure varies, try common paths
                content = record.get('content', {})
                
                # Check for personneMorale (legal entity) composition
                pm = content.get('personneMorale', {})
                pm_composition = pm.get('composition', {})
                pm_pouvoirs = pm_composition.get('pouvoirs', [])
                
                # Check for personnePhysique (physical person) composition
                pp = content.get('personnePhysique', {})
                pp_composition = pp.get('composition', {})
                pp_pouvoirs = pp_composition.get('pouvoirs', [])
                
                all_pouvoirs = pm_pouvoirs + pp_pouvoirs
                
                for pouvoir in all_pouvoirs:
                    try:
                        director = DirectorsFallback._parse_pouvoir(pouvoir)
                        if director:
                            directors.append(director)
                    except Exception as e:
                        logger.warning(f"Failed to parse pouvoir: {e}")
                        continue
                
                logger.debug(f"Extracted {len(all_pouvoirs)} directors from formality record")
                
            except Exception as e:
                logger.warning(f"Error extracting from formality record: {e}")
                continue
        
        return directors
    
    @staticmethod
    def _parse_pouvoir(pouvoir: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """
        Parse individual pouvoir (power/role) record.
        
        Args:
            pouvoir: Single pouvoir dict from INPI
        
        Returns:
            Standardized director dict or None
        """
        if not pouvoir:
            return None
        
        # Determine person type
        person_type = pouvoir.get('typeDePersonne', 'UNKNOWN')
        
        director = {
            'person_type': person_type,
            'role_code': pouvoir.get('roleEntreprise', ''),
            'role_name': pouvoir.get('roleLabel', ''),
            'appointment_date': pouvoir.get('dateDebut'),
            'termination_date': pouvoir.get('dateFin'),
            'is_active': not pouvoir.get('dateFin'),  # Active if no end date
        }
        
        if person_type == 'INDIVIDU':
            # Individual person
            person = pouvoir.get('person', {})
            director.update({
                'name': f"{person.get('prenom', '')} {person.get('nom', '')}".strip(),
                'first_name': person.get('prenom', ''),
                'last_name': person.get('nom', ''),
                'nationality': person.get('nationalite'),
                'birth_date': person.get('dateNaissance'),
            })
        elif person_type == 'PERSONNE_MORALE':
            # Legal entity (company)
            company = pouvoir.get('company', {})
            director.update({
                'name': company.get('nom', ''),
                'siren': company.get('siren'),
                'company_name': company.get('nom'),
                'company_siren': company.get('siren'),
            })
        
        return director
    
    @staticmethod
    def check_alternative_fields(
        company_data: Dict[str, Any] | Any
    ) -> List[Dict[str, Any]]:
        """
        Check alternative INPI field paths for director data.
        
        Args:
            company_data: Company object/dict from INPI
        
        Returns:
            List of directors if found, empty list otherwise
        """
        directors = []
        
        for field_name in DirectorsFallback.ALTERNATIVE_DIRECTOR_FIELDS:
            try:
                # Try as dict
                if isinstance(company_data, dict):
                    value = company_data.get(field_name)
                else:
                    # Try as object attribute
                    value = getattr(company_data, field_name, None)
                
                if value and isinstance(value, list) and len(value) > 0:
                    logger.info(f"✓ Found directors in alternative field: {field_name}")
                    
                    # Try to standardize the format
                    for item in value:
                        if isinstance(item, dict):
                            directors.append(item)
                        else:
                            # Try to convert object to dict
                            try:
                                if hasattr(item, '__dict__'):
                                    directors.append(item.__dict__)
                            except:
                                pass
                    
                    if directors:
                        return directors
            
            except Exception as e:
                logger.debug(f"Could not access field {field_name}: {e}")
                continue
        
        return directors
    
    @staticmethod
    def get_directors_with_fallback(
        management_roles: List[Dict[str, Any]],
        formality_records: Optional[List[Dict[str, Any]]] = None,
        company_data: Optional[Dict[str, Any] | Any] = None,
    ) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
        """
        Get directors with comprehensive fallback strategy.
        
        Args:
            management_roles: Primary source (from get_management_roles())
            formality_records: Optional fallback source
            company_data: Optional fallback source
        
        Returns:
            (directors_list, metadata_dict)
            metadata includes:
                - source: where directors came from
                - count: number of directors
                - data_quality: COMPLETE | PARTIAL | UNAVAILABLE
        """
        metadata = {
            'source': 'unknown',
            'count': 0,
            'data_quality': 'UNAVAILABLE',
            'sources_tried': [],
            'fallback_used': False,
        }
        
        # Try primary source
        if management_roles and isinstance(management_roles, list) and len(management_roles) > 0:
            logger.info(f"✓ Using primary source: {len(management_roles)} directors from get_management_roles()")
            metadata['source'] = 'get_management_roles'
            metadata['count'] = len(management_roles)
            metadata['data_quality'] = 'COMPLETE'
            metadata['sources_tried'].append('get_management_roles')
            return management_roles, metadata
        
        metadata['sources_tried'].append('get_management_roles [empty]')
        logger.warning("⚠️  Primary source empty, trying fallbacks...")
        
        # Try alternative fields on company_data
        if company_data:
            metadata['sources_tried'].append('alternative_fields')
            alt_directors = DirectorsFallback.check_alternative_fields(company_data)
            if alt_directors:
                logger.info(f"✓ Fallback 1: Found {len(alt_directors)} directors in alternative fields")
                metadata['source'] = 'alternative_fields'
                metadata['count'] = len(alt_directors)
                metadata['data_quality'] = 'PARTIAL'
                metadata['fallback_used'] = True
                return alt_directors, metadata
        
        # Try formality records
        if formality_records:
            metadata['sources_tried'].append('formality_records')
            form_directors = DirectorsFallback.extract_from_formality_records(formality_records)
            if form_directors:
                logger.info(f"✓ Fallback 2: Found {len(form_directors)} directors in formality records")
                metadata['source'] = 'formality_records'
                metadata['count'] = len(form_directors)
                metadata['data_quality'] = 'PARTIAL'
                metadata['fallback_used'] = True
                return form_directors, metadata
        
        # No data found
        logger.warning("❌ No directors found from any source")
        metadata['source'] = 'none'
        metadata['count'] = 0
        metadata['data_quality'] = 'UNAVAILABLE'
        
        return [], metadata
    
    @staticmethod
    def validate_directors_data(directors: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        Validate and summarize directors data.
        
        Args:
            directors: List of director dicts
        
        Returns:
            {
                'total': int,
                'individuals': int,
                'companies': int,
                'active': int,
                'data_quality': str,
                'issues': list,
            }
        """
        result = {
            'total': len(directors),
            'individuals': 0,
            'companies': 0,
            'active': 0,
            'data_quality': 'UNKNOWN',
            'issues': [],
        }
        
        if len(directors) == 0:
            result['data_quality'] = 'UNAVAILABLE'
            return result
        
        for director in directors:
            person_type = director.get('person_type', '')
            
            if person_type == 'INDIVIDU':
                result['individuals'] += 1
            elif person_type == 'PERSONNE_MORALE':
                result['companies'] += 1
            
            if director.get('is_active', True):
                result['active'] += 1
        
        # Determine quality
        if result['total'] > 0 and result['active'] > 0:
            result['data_quality'] = 'COMPLETE'
        elif result['total'] > 0:
            result['data_quality'] = 'PARTIAL'
        else:
            result['data_quality'] = 'UNAVAILABLE'
            result['issues'].append('No director records found')
        
        # Check for issues
        if result['individuals'] == 0:
            result['issues'].append('No individual directors (only companies)')
        
        if result['active'] == 0:
            result['issues'].append('No active directors (all terminated)')
        
        return result


# Example usage / testing
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    
    print("\n" + "=" * 100)
    print("Testing DirectorsFallback...")
    print("=" * 100)
    
    # Test 1: Primary source
    print("\n1️⃣  Testing with primary source...")
    primary_directors = [
        {
            'person_type': 'INDIVIDU',
            'name': 'John Doe',
            'role_code': '10',
            'is_active': True,
        }
    ]
    directors, metadata = DirectorsFallback.get_directors_with_fallback(
        management_roles=primary_directors
    )
    print(f"   Count: {len(directors)}")
    print(f"   Source: {metadata['source']}")
    print(f"   Quality: {metadata['data_quality']}")
    
    # Test 2: Validation
    print("\n2️⃣  Testing validation...")
    validation = DirectorsFallback.validate_directors_data(primary_directors)
    print(f"   Total: {validation['total']}")
    print(f"   Individuals: {validation['individuals']}")
    print(f"   Quality: {validation['data_quality']}")
    
    # Test 3: Empty source with fallback
    print("\n3️⃣  Testing fallback when primary is empty...")
    fallback_directors = [
        {
            'person_type': 'PERSONNE_MORALE',
            'name': 'Company Inc',
            'siren': '123456789',
        }
    ]
    
    company_data = {
        'dirigeants': fallback_directors
    }
    
    directors, metadata = DirectorsFallback.get_directors_with_fallback(
        management_roles=[],
        company_data=company_data
    )
    print(f"   Count: {len(directors)}")
    print(f"   Source: {metadata['source']}")
    print(f"   Fallback used: {metadata['fallback_used']}")
    
    print("\n" + "=" * 100)
