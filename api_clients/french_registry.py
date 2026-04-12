"""
French company registry API client using INPI's Registre National des Entreprises (RNE).

Free API access via: https://registre-national-entreprises.inpi.fr/api/

Requires authentication via SSO with INPI account credentials.
"""

import os
import requests
from datetime import datetime, timedelta
from dataclasses import dataclass
from typing import Optional, Dict, Any, List
import logging
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

logger = logging.getLogger(__name__)

# API Configuration
INPI_SSO_URL = "https://registre-national-entreprises.inpi.fr/api/sso/login"
INPI_API_BASE = "https://registre-national-entreprises.inpi.fr/api"
INPI_COMPANIES_ENDPOINT = f"{INPI_API_BASE}/companies"
INPI_ACTES_ENDPOINT = f"{INPI_API_BASE}/actes"  # For PDF documents


@dataclass
class FrenchCompanyBasic:
    """Basic company information from French registry."""
    siren: str
    denomination: str
    forme_juridique: str
    status: str
    creation_date: Optional[str] = None
    address: Optional[str] = None
    postal_code: Optional[str] = None
    city: Optional[str] = None
    code_ape: Optional[str] = None
    ape_code: Optional[str] = None  # Alias for code_ape
    ape_codes: Optional[List[str]] = None  # List format for compatibility
    nombre_salarie: Optional[int] = None
    
    @property
    def name(self):
        """Alias for denomination."""
        return self.denomination
    
    @property
    def legal_form(self):
        """Alias for forme_juridique."""
        return self.forme_juridique
    
    @property
    def employee_count(self):
        """Alias for nombre_salarie."""
        return self.nombre_salarie


@dataclass
class FrenchFinancialRecord:
    """French company financial record."""
    year: int
    revenue: Optional[float] = None
    net_profit: Optional[float] = None


@dataclass
class FrenchFormalityRecord:
    """French company formality/compliance record."""
    date: str
    description: str
    type: str


class FrenchRegistryClient:
    """Client for INPI French company registry API."""

    def __init__(self, email: Optional[str] = None, password: Optional[str] = None):
        """Initialize the French registry client."""
        self.email = email or os.getenv("FRENCH_REGISTRY_EMAIL")
        self.password = password or os.getenv("FRENCH_REGISTRY_PASSWORD")
        
        if not self.email or not self.password:
            raise ValueError(
                "INPI credentials required. Set FRENCH_REGISTRY_EMAIL and "
                "FRENCH_REGISTRY_PASSWORD environment variables."
            )
        
        # NEW: Resilient HTTP client (Priority #2 - handle timeouts)
        from core.resilient_http_client import ResilientHTTPClient
        self.http_client = ResilientHTTPClient(
            max_retries=3,
            timeout_seconds=15,
            backoff_factor=2.0,
            cache_ttl_seconds=86400,  # 24h cache
        )
        
        self.token = None
        self.token_expires_at = None
        self._authenticate()

    def _authenticate(self) -> str:
        """Authenticate with INPI SSO and get JWT token with retry on 502."""
        import time
        max_retries = 3
        retry_delay = 2
        last_error = None
        
        for attempt in range(max_retries):
            try:
                response = requests.post(
                    INPI_SSO_URL,
                    json={
                        "username": self.email,
                        "password": self.password
                    },
                    headers={"Content-Type": "application/json"},
                    verify=False,
                    timeout=10
                )
                
                # Handle 502 Bad Gateway with retry
                if response.status_code == 502:
                    if attempt < max_retries - 1:
                        logger.warning(f"⚠️ INPI returned 502, retrying... (attempt {attempt + 1}/{max_retries})")
                        time.sleep(retry_delay)
                        continue
                    else:
                        raise ValueError(f"Authentication failed: INPI server error (502) after {max_retries} attempts")
                
                if response.status_code != 200:
                    raise ValueError(f"Authentication failed: {response.status_code}")
                
                data = response.json()
                self.token = data.get("token")
                expires_in = data.get("expiresIn", 86400)
                self.token_expires_at = datetime.now() + timedelta(seconds=expires_in)
                
                logger.info(f"✅ INPI authentication successful")
                return self.token
                
            except requests.exceptions.RequestException as e:
                last_error = e
                if attempt < max_retries - 1:
                    logger.warning(f"⚠️ Connection error, retrying... (attempt {attempt + 1}/{max_retries})")
                    time.sleep(retry_delay)
                    continue
                else:
                    raise ValueError(f"Failed to connect to INPI SSO after {max_retries} attempts: {str(e)}")
            except ValueError:
                raise
        
        # Fallback (should not reach here)
        raise ValueError(f"Authentication failed after {max_retries} attempts")

    def _ensure_valid_token(self):
        """Re-authenticate if token is expired."""
        if not self.token or (self.token_expires_at and datetime.now() >= self.token_expires_at):
            logger.info("Token expired, re-authenticating...")
            self._authenticate()

    def _get_headers(self) -> Dict[str, str]:
        """Get headers with authentication token."""
        self._ensure_valid_token()
        return {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json"
        }

    def get_company_by_siren(self, siren: str) -> Optional[FrenchCompanyBasic]:
        """Get company information by SIREN."""
        if not siren or not siren.isdigit() or len(siren) != 9:
            raise ValueError(f"Invalid SIREN format: {siren}. Must be 9 digits.")

        try:
            response = requests.get(
                f"{INPI_COMPANIES_ENDPOINT}/{siren}",
                headers=self._get_headers(),
                verify=False,
                timeout=10
            )

            if response.status_code == 404:
                logger.warning(f"Company with SIREN {siren} not found")
                return None

            if response.status_code != 200:
                logger.error(f"API error {response.status_code}: {response.text}")
                return None

            data = response.json()
            return self._parse_company_data(data)

        except requests.exceptions.RequestException as e:
            logger.error(f"Failed to fetch company data: {str(e)}")
            return None

    def _parse_company_data(self, data: Dict[str, Any]) -> FrenchCompanyBasic:
        """Parse INPI API response into FrenchCompanyBasic object."""
        try:
            siren = data.get("siren", "")

            # Navigate through: formality -> content -> personneMorale -> identite -> entreprise
            content = data.get("formality", {}).get("content", {})
            personne_morale = content.get("personneMorale", {})
            identite = personne_morale.get("identite", {})
            entreprise = identite.get("entreprise", {})

            denomination = entreprise.get("denomination", "Unknown")
            forme_juridique = entreprise.get("formeJuridique", "")
            date_immat = entreprise.get("dateImmat", "")
            code_ape = entreprise.get("codeApe", "")
            nombre_salarie = entreprise.get("nombreSalarie", None)

            # Extract real status from INPI data instead of hardcoding
            # INPI uses etatAdministratif: "A" = Active, "C" = Closed/Ceased
            etat_admin = entreprise.get("etatAdministratif", "")
            # Also check for closure indicators in formality data
            nature_cessation = content.get("natureCessation", {})
            has_cessation = bool(nature_cessation and nature_cessation.get("dateCessationActivite"))

            if has_cessation:
                status = "Ceased"
            elif etat_admin == "C":
                status = "Closed"
            elif etat_admin == "A":
                status = "Active"
            elif etat_admin:
                status = etat_admin  # Pass through unknown codes
            else:
                # No explicit status — infer from available data
                # If the company has an immatriculation date and no cessation, assume active
                status = "Active (assumed)" if date_immat and not has_cessation else "Unknown"

            # Also extract address from principal establishment
            etab_principal = personne_morale.get("etablissementPrincipal", {})
            address = etab_principal.get("adresseEtablissement", {}).get("libelle", "")
            postal_code = etab_principal.get("adresseEtablissement", {}).get("codePostal", "")
            city = etab_principal.get("adresseEtablissement", {}).get("commune", "")

            return FrenchCompanyBasic(
                siren=siren,
                denomination=denomination,
                forme_juridique=forme_juridique,
                creation_date=date_immat,
                code_ape=code_ape,
                ape_code=code_ape,  # Set both for compatibility
                ape_codes=[code_ape] if code_ape else [],  # List format
                nombre_salarie=nombre_salarie,
                address=address,
                postal_code=postal_code,
                city=city,
                status=status,
            )

        except (KeyError, TypeError) as e:
            logger.error(f"Error parsing company data: {str(e)}")
            raise ValueError(f"Unexpected API response format: {str(e)}")

    def search_companies(self, query: str, limit: int = 10) -> List[FrenchCompanyBasic]:
        """Search for companies by name."""
        logger.warning("Company search not yet implemented for INPI API")
        return []

    def get_company_details(self, siren: str) -> Optional[Dict[str, Any]]:
        """Get full company details (raw API response)."""
        if not siren or not siren.isdigit() or len(siren) != 9:
            raise ValueError(f"Invalid SIREN format: {siren}. Must be 9 digits.")

        try:
            response = requests.get(
                f"{INPI_COMPANIES_ENDPOINT}/{siren}",
                headers=self._get_headers(),
                verify=False,
                timeout=10
            )

            if response.status_code == 404:
                return None

            if response.status_code != 200:
                logger.error(f"API error {response.status_code}: {response.text}")
                return None

            return response.json()

        except requests.exceptions.RequestException as e:
            logger.error(f"Failed to fetch company details: {str(e)}")
            return None

    def get_financial_records(self, siren: str) -> List[FrenchFinancialRecord]:
        """Get financial records for a company."""
        details = self.get_company_details(siren)
        if not details:
            return []
        
        financial_records = []
        try:
            content = details.get("formality", {}).get("content", {})
            pm = content.get("personneMorale", {})
            
            # Try to extract financial data from various possible locations
            # INPI may include accounting period info (exercice comptable)
            comptable_info = pm.get("comptabilite", {})
            if comptable_info and comptable_info.get("dateClotureExercice"):
                year = int(comptable_info.get("dateClotureExercice", "")[:4])
                financial_records.append(
                    FrenchFinancialRecord(
                        year=year,
                        revenue=comptable_info.get("chiffre_affaires"),
                        net_profit=comptable_info.get("resultat_net"),
                    )
                )
        except (KeyError, TypeError, ValueError) as e:
            logger.warning(f"Could not parse financial records: {str(e)}")
        
        return financial_records

    def get_formality_records(self, siren: str) -> List[FrenchFormalityRecord]:
        """Get formality/compliance records for a company."""
        details = self.get_company_details(siren)
        if not details:
            return []

        formality_records = []
        
        try:
            content = details.get("formality", {}).get("content", {})
            observations = content.get("personneMorale", {}).get("observations", {})
            rcs_records = observations.get("rcs", [])
            
            for record in rcs_records:
                formality_records.append(
                    FrenchFormalityRecord(
                        date=record.get("dateGreffe", ""),
                        description=record.get("texte", ""),
                        type=record.get("etatObs", "")
                    )
                )
        except (KeyError, TypeError) as e:
            logger.warning(f"Could not parse formality records: {str(e)}")

        return formality_records
    
    def get_management_roles(self, siren: str, depth: int = 0, max_depth: int = 3) -> List[Dict[str, Any]]:
        """
        Extract management roles/powers (pouvoirs) from INPI data.
        
        Supports RECURSIVE lookup:
        - If a director is a legal entity (company), fetch that company's directors
        - Traces ultimate beneficial owners through corporate chains
        - Prevents infinite loops with max_depth parameter
        
        Processes the actual INPI API structure which has:
        - composition.pouvoirs[]: Array of management roles
        - Each pouvoir contains:
          - roleEntreprise: Role code (e.g., "73" for director)
          - typeDePersonne: "INDIVIDU" or "PERSONNE_MORALE"
          - individu: Physical person data (if INDIVIDU)
            - descriptionPersonne: name, birth date, etc.
            - adresseDomicile: home address
          - personneMorale: Company data (if PERSONNE_MORALE)
            - denomination: Company name
            - siren: Company SIREN
        
        Args:
            siren: Company SIREN to look up
            depth: Current recursion depth (internal use)
            max_depth: Maximum recursion depth (default 3 to prevent infinite loops)
        
        Returns list of management roles with person/company details and UBO chain.
        """
        details = self.get_company_details(siren)
        if not details:
            return []
        
        management_roles = []
        
        try:
            content = details.get("formality", {}).get("content", {})
            pm = content.get("personneMorale", {})
            composition = pm.get("composition", {})
            pouvoirs = composition.get("pouvoirs", [])
            
            logger.info(f"Found {len(pouvoirs)} management roles in JSON (depth={depth})")
            
            # Process each pouvoir entry in composition
            if pouvoirs and len(pouvoirs) > 0:
                for pouvoir in pouvoirs:
                    # Map role codes to human-readable names
                    role_code = pouvoir.get("roleEntreprise", "")
                    role_name = self._map_role_code(role_code)
                    
                    # Determine if it's a physical person or legal entity
                    person_type = pouvoir.get("typeDePersonne", "UNKNOWN")
                    
                    role_data = {
                        "role_id": pouvoir.get("representantId", ""),
                        "role_code": role_code,
                        "role_name": role_name,
                        "person_type": person_type,
                        "source": "inpi_json",
                        "depth": depth,  # Track recursion depth
                    }
                    
                    # ─── Extract Physical Person Data (INDIVIDU) ────────────────
                    if person_type == "INDIVIDU" and pouvoir.get("individu"):
                        individu = pouvoir.get("individu", {})
                        desc = individu.get("descriptionPersonne", {})
                        
                        # Extract name components
                        nom = desc.get("nom", "")
                        prenoms = desc.get("prenoms", [])
                        prenom = prenoms[0] if isinstance(prenoms, list) and prenoms else ""
                        
                        role_data.update({
                            "first_name": prenom,
                            "last_name": nom,
                            "full_name": f"{prenom} {nom}".strip(),
                            "birth_date": desc.get("dateDeNaissance", ""),
                            "birth_place": desc.get("lieuDeNaissance", ""),
                            "nationality": self._map_country_code(desc.get("codePaysNaissance", "")),
                            "active": not desc.get("dateEffetRoleDeclarant"),  # If no end date, active
                            "is_ultimate_owner": True,  # Physical person = ultimate owner
                        })
                        
                        # Add home address if available
                        if individu.get("adresseDomicile"):
                            addr = individu.get("adresseDomicile", {})
                            role_data["address"] = self._format_address(addr)
                    
                    # ─── Extract Legal Entity Data (PERSONNE_MORALE) ────────────
                    elif person_type == "PERSONNE_MORALE" and pouvoir.get("personneMorale"):
                        pm_data = pouvoir.get("personneMorale", {})
                        company_siren = pm_data.get("siren", "")
                        
                        role_data.update({
                            "company_name": pm_data.get("denomination", ""),
                            "company_siren": company_siren,
                            "person_type": "Legal Entity",
                            "is_ultimate_owner": False,  # Company = not ultimate owner (yet)
                        })
                        
                        # ─── RECURSIVE LOOKUP: Get this company's directors ───
                        if company_siren and depth < max_depth:
                            logger.info(
                                f"Recursive lookup: Fetching directors of {pm_data.get('denomination', '?')} "
                                f"(SIREN {company_siren}, depth {depth+1}/{max_depth})"
                            )
                            try:
                                # Recursively fetch directors of the parent company
                                parent_directors = self.get_management_roles(
                                    siren=company_siren,
                                    depth=depth + 1,
                                    max_depth=max_depth
                                )
                                
                                # If we found physical persons at the parent level, mark them as ultimate owners
                                # and add them to the chain
                                if parent_directors:
                                    # Mark any physical persons as ultimate owners
                                    for parent_dir in parent_directors:
                                        if parent_dir.get("person_type") == "INDIVIDU":
                                            parent_dir["is_ultimate_owner"] = True
                                    
                                    # Store the UBO chain
                                    role_data["ubo_chain"] = parent_directors
                                    role_data["has_ubo_info"] = True
                                    logger.info(f"✓ Found {len(parent_directors)} directors in parent company")
                                else:
                                    role_data["ubo_chain"] = []
                                    role_data["has_ubo_info"] = False
                            
                            except Exception as e:
                                logger.warning(
                                    f"Could not fetch directors for {company_siren}: {str(e)}"
                                )
                                role_data["ubo_chain"] = []
                                role_data["has_ubo_info"] = False
                        
                        elif company_siren and depth >= max_depth:
                            logger.info(
                                f"Max recursion depth ({max_depth}) reached - not fetching further for {company_siren}"
                            )
                            role_data["ubo_chain"] = []
                            role_data["has_ubo_info"] = False
                            role_data["recursion_limit_reached"] = True
                    
                    management_roles.append(role_data)
                
                logger.info(f"✓ Extracted {len(management_roles)} management roles from INPI JSON")
                return management_roles
            
            # If no pouvoirs found, log explanation
            else:
                logger.info(
                    f"No pouvoirs found in composition for SIREN {siren}. "
                    f"This is normal - INPI may not have filed this data yet."
                )
        
        except (KeyError, TypeError) as e:
            logger.warning(f"Error extracting management roles: {str(e)}", exc_info=True)
        
        return management_roles
    
    def _map_role_code(self, role_code: str) -> str:
        """Map INPI role codes to human-readable role names."""
        role_mapping = {
            "50": "Gérant",
            "51": "Co-gérant",
            "52": "Liquidateur",
            "53": "Représentant légal / Dirigeant",
            "65": "Administrateur",
            "70": "Président",
            "71": "Vice-président",
            "72": "Secrétaire",
            "73": "Directeur Général / Gérant",
            "74": "Directeur Général Délégué",
            "75": "Directeur",
            "76": "Directeur délégué",
            "80": "Administrateur",
            "81": "Administrateur délégué",
            "82": "Commissaire aux comptes",
            "85": "Membre du conseil",
            "90": "Gérant (SARL/EIRL)",
            "91": "Co-gérant",
            "92": "Associé",
            "93": "Associé commanditaire",
            "94": "Associé commandité",
            "99": "Autre (Dirigeant/Responsable)",
        }
        return role_mapping.get(role_code, f"Role {role_code}")
    
    def _map_country_code(self, country_code: str) -> str:
        """Map country codes to country names."""
        if not country_code:
            return ""
        
        # Simple mapping - expand as needed
        country_map = {
            "FR": "France",
            "US": "United States",
            "DE": "Germany",
            "IT": "Italy",
            "ES": "Spain",
            "GB": "United Kingdom",
            "CH": "Switzerland",
            "BE": "Belgium",
        }
        return country_map.get(country_code, country_code)
    
    def _format_address(self, address: Dict[str, Any]) -> str:
        """Format an INPI address dict into a readable string."""
        parts = []
        
        if address.get("numVoie"):
            parts.append(str(address.get("numVoie")))
        if address.get("typeVoie"):
            parts.append(address.get("typeVoie"))
        if address.get("voie"):
            parts.append(address.get("voie"))
        if address.get("codePostal"):
            parts.append(address.get("codePostal"))
        if address.get("commune"):
            parts.append(address.get("commune"))
        
        return " ".join(str(p) for p in parts if p)
    
    # NOTE: /api/actes endpoint returns 500 error - disabled
    # def get_actes_list(self, siren: str) -> List[Dict[str, Any]]:
    #     """PDF document endpoint not functional in current INPI API."""
    #     return []
    # 
    # def extract_directors_from_pdf(self, acte_id: str) -> Dict[str, Any]:
    #     """PDF extraction not available - endpoint returns 500."""
    #     return {"directors": [], "shareholders": [], "source": "pdf_unavailable"}


# Global client instance (lazy loaded)
_client = None


def get_client() -> FrenchRegistryClient:
    """Get or create the global INPI client."""
    global _client
    if _client is None:
        _client = FrenchRegistryClient()
    return _client


def search_french_companies(name: str, limit: int = 10) -> List[FrenchCompanyBasic]:
    """Search for French companies by name."""
    client = get_client()
    return client.search_companies(name, limit)


def get_company_by_siren(siren: str) -> Optional[FrenchCompanyBasic]:
    """Get French company by SIREN."""
    client = get_client()
    return client.get_company_by_siren(siren)
