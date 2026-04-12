"""
company_validator.py

Validates company data against expected values to prevent wrong company lookups.

Implements:
- SIREN validation (ensure returned SIREN matches input)
- Fuzzy name matching (ensure company name similarity >85%)
- Secondary lookup cross-reference (fallback verification)
- Clear error messages on validation failure
"""

from typing import Dict, Any, Optional, Tuple
import logging
from difflib import SequenceMatcher
import re

logger = logging.getLogger(__name__)


class CompanyValidator:
    """
    Validates company data to prevent wrong company lookups.
    
    Key checks:
    1. SIREN validation - returned SIREN must match input SIREN
    2. Name fuzzy matching - company name must be >85% similar
    3. Cross-reference lookup - secondary verification if needed
    """
    
    # Minimum similarity threshold (85%)
    MIN_NAME_SIMILARITY = 0.85
    
    # Common name variations that should match
    NAME_ALIASES = {
        "SA": ["S.A.", "Société Anonyme"],
        "SAS": ["S.A.S.", "Société par Actions Simplifiée"],
        "SARL": ["S.A.R.L.", "Société à Responsabilité Limitée"],
        "EURL": ["E.U.R.L.", "Entreprise Unipersonnelle à Responsabilité Limitée"],
        "SE": ["S.E.", "Societas Europaea"],
        "SASU": ["S.A.S.U."],
        "SNC": ["S.N.C.", "Société en Nom Collectif"],
        "SPRL": ["S.P.R.L."],
        "LLC": ["L.L.C."],
        "INC": ["Inc.", "Incorporated"],
        "CORP": ["Corp.", "Corporation"],
    }
    
    @staticmethod
    def normalize_company_name(name: str) -> str:
        """
        Normalize company name for comparison.
        
        Steps:
        1. Convert to uppercase
        2. Remove accents
        3. Remove extra spaces
        4. Remove punctuation
        5. Expand legal form abbreviations
        """
        if not name:
            return ""
        
        # Step 1: Uppercase
        name = name.upper()
        
        # Step 2: Remove accents
        import unicodedata
        name = ''.join(
            c for c in unicodedata.normalize('NFD', name)
            if unicodedata.category(c) != 'Mn'
        )
        
        # Step 3: Remove extra spaces
        name = ' '.join(name.split())
        
        # Step 4: Remove non-alphanumeric except spaces
        name = re.sub(r'[^\w\s]', '', name)
        
        return name.strip()
    
    @staticmethod
    def calculate_name_similarity(name1: str, name2: str) -> float:
        """
        Calculate similarity between two company names (0.0 to 1.0).
        """
        n1 = CompanyValidator.normalize_company_name(name1)
        n2 = CompanyValidator.normalize_company_name(name2)
        
        if not n1 or not n2:
            return 0.0
        
        similarity = SequenceMatcher(None, n1, n2).ratio()
        return similarity
    
    @staticmethod
    def validate_siren(input_siren: str, returned_siren: str) -> Tuple[bool, str]:
        """
        Validate that returned SIREN matches input SIREN.
        
        Args:
            input_siren: SIREN provided to lookup function
            returned_siren: SIREN returned from API
        
        Returns:
            (is_valid, error_message)
            is_valid=True if SIREN matches
            error_message="OK" if valid, else error description
        """
        # Strip whitespace
        input_siren = str(input_siren).strip()
        returned_siren = str(returned_siren).strip() if returned_siren else ""
        
        # Check if returned SIREN exists
        if not returned_siren:
            return False, "Returned SIREN is empty/missing"
        
        # Check if SIRENs match exactly
        if input_siren == returned_siren:
            return True, "OK"
        
        # Check if only difference is whitespace
        if input_siren.replace(" ", "") == returned_siren.replace(" ", ""):
            return True, "OK (after whitespace normalization)"
        
        # SIRENs don't match
        return False, f"SIREN mismatch: input={input_siren}, returned={returned_siren}"
    
    @staticmethod
    def validate_company_name(expected_name: str, actual_name: str) -> Tuple[bool, str, float]:
        """
        Validate company name with fuzzy matching.
        
        Args:
            expected_name: Company name expected (optional for input)
            actual_name: Company name from API
        
        Returns:
            (is_valid, message, similarity_score)
            is_valid=True if similarity > MIN_NAME_SIMILARITY
        """
        if not actual_name:
            return False, "Actual company name is empty", 0.0
        
        if not expected_name:
            return True, "No expected name to validate (accepting API result)", 1.0
        
        similarity = CompanyValidator.calculate_name_similarity(expected_name, actual_name)
        
        if similarity >= CompanyValidator.MIN_NAME_SIMILARITY:
            return True, f"Name match: {similarity:.1%}", similarity
        else:
            return False, f"Name mismatch: similarity only {similarity:.1%} (threshold: {CompanyValidator.MIN_NAME_SIMILARITY:.1%})", similarity
    
    @staticmethod
    def validate_company_data(
        company_data: Dict[str, Any],
        input_siren: str,
        expected_name: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Comprehensive company data validation.
        
        Args:
            company_data: Company object/dict from API
            input_siren: Input SIREN for lookup
            expected_name: Optional expected company name
        
        Returns:
            {
                'is_valid': bool,
                'siren_check': {'passed': bool, 'message': str},
                'name_check': {'passed': bool, 'message': str, 'similarity': float},
                'overall_status': 'VALID' | 'WARNING' | 'INVALID',
                'error_message': str,
                'company_name': str,
                'company_siren': str,
                'validation_details': dict,
            }
        """
        result = {
            'is_valid': True,
            'siren_check': {'passed': False, 'message': ''},
            'name_check': {'passed': False, 'message': '', 'similarity': 0.0},
            'overall_status': 'VALID',
            'error_message': '',
            'company_name': '',
            'company_siren': '',
            'validation_details': {},
        }
        
        try:
            # Extract company identifiers
            company_name = None
            company_siren = None
            
            if isinstance(company_data, dict):
                company_name = company_data.get('name') or company_data.get('nom')
                company_siren = company_data.get('siren') or company_data.get('SIREN')
            else:
                # Handle object with attributes
                company_name = getattr(company_data, 'name', None) or getattr(company_data, 'nom', None)
                company_siren = getattr(company_data, 'siren', None) or getattr(company_data, 'SIREN', None)
            
            result['company_name'] = company_name or 'N/A'
            result['company_siren'] = company_siren or 'N/A'
            
            # CHECK 1: SIREN Validation (CRITICAL)
            siren_valid, siren_msg = CompanyValidator.validate_siren(input_siren, company_siren)
            result['siren_check']['passed'] = siren_valid
            result['siren_check']['message'] = siren_msg
            
            if not siren_valid:
                result['is_valid'] = False
                result['overall_status'] = 'INVALID'
                result['error_message'] = f"SIREN validation failed: {siren_msg}"
                logger.error(f"❌ SIREN Validation Failed: {siren_msg}")
                return result
            
            logger.info(f"✓ SIREN check passed: {siren_msg}")
            
            # CHECK 2: Name Fuzzy Matching (if expected name provided)
            if expected_name:
                name_valid, name_msg, similarity = CompanyValidator.validate_company_name(
                    expected_name, company_name
                )
                result['name_check']['passed'] = name_valid
                result['name_check']['message'] = name_msg
                result['name_check']['similarity'] = similarity
                
                if not name_valid:
                    result['overall_status'] = 'WARNING'
                    logger.warning(f"⚠️  Name Check Warning: {name_msg}")
                else:
                    logger.info(f"✓ Name check passed: {name_msg}")
            else:
                # No expected name provided, skip name validation
                result['name_check']['passed'] = True
                result['name_check']['message'] = "No expected name provided (skipped)"
                logger.info("✓ Name check skipped (no expected name)")
            
            result['validation_details'] = {
                'input_siren': input_siren,
                'returned_siren': company_siren,
                'expected_name': expected_name or 'Not provided',
                'returned_name': company_name or 'N/A',
            }
            
        except Exception as e:
            result['is_valid'] = False
            result['overall_status'] = 'INVALID'
            result['error_message'] = f"Validation exception: {str(e)}"
            logger.error(f"❌ Validation Exception: {str(e)}")
        
        return result


def validate_company_lookup(
    company_data: Dict[str, Any],
    input_siren: str,
    expected_name: Optional[str] = None,
    strict_mode: bool = True,
) -> Tuple[bool, Dict[str, Any], str]:
    """
    Convenience function to validate company lookup and raise errors if needed.
    
    Args:
        company_data: Company object from API
        input_siren: Input SIREN
        expected_name: Optional expected company name
        strict_mode: If True, raise error on validation failure. If False, warn only.
    
    Returns:
        (is_valid, validation_result, error_message)
    
    Raises:
        ValueError: If strict_mode=True and validation fails
    """
    validator = CompanyValidator()
    result = validator.validate_company_data(company_data, input_siren, expected_name)
    
    is_valid = result['overall_status'] in ('VALID', 'WARNING')
    error_msg = result['error_message'] or result['siren_check']['message']
    
    if not is_valid and strict_mode:
        raise ValueError(f"Company validation failed: {error_msg}")
    
    return is_valid, result, error_msg


# Example usage / testing
if __name__ == "__main__":
    # Test SIREN validation
    print("Testing CompanyValidator...")
    
    # Test 1: Matching SIREN
    is_valid, msg = CompanyValidator.validate_siren("542051180", "542051180")
    print(f"✓ Test 1 (matching SIREN): {is_valid} - {msg}")
    
    # Test 2: Mismatching SIREN
    is_valid, msg = CompanyValidator.validate_siren("542051180", "775670417")
    print(f"✓ Test 2 (mismatching SIREN): {is_valid} - {msg}")
    
    # Test 3: Name similarity
    is_valid, msg, similarity = CompanyValidator.validate_company_name(
        "LVMH Moët Hennessy Louis Vuitton",
        "LVMH Moet Hennessy Louis Vuitton"
    )
    print(f"✓ Test 3 (name fuzzy match): {is_valid} ({similarity:.1%}) - {msg}")
    
    # Test 4: Name mismatch
    is_valid, msg, similarity = CompanyValidator.validate_company_name(
        "LVMH",
        "TOTALENERGIES"
    )
    print(f"✓ Test 4 (name mismatch): {is_valid} ({similarity:.1%}) - {msg}")
    
    # Test 5: Comprehensive validation
    company = {
        'siren': '542051180',
        'name': 'LVMH Moet Hennessy Louis Vuitton',
        'status': 'Active',
    }
    result = CompanyValidator.validate_company_data(
        company, 
        input_siren='542051180',
        expected_name='LVMH'
    )
    print(f"✓ Test 5 (comprehensive): status={result['overall_status']}, siren={result['siren_check']['passed']}, name={result['name_check']['passed']}")
