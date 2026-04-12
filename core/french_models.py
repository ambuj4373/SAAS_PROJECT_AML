"""
French company data models for comprehensive INPI analysis.

These dataclasses represent the enhanced data structures needed for
a full-featured French company risk assessment dashboard.
"""

from dataclasses import dataclass, field
from typing import Optional, Dict, Any, List
from datetime import datetime
from enum import Enum


class ManagementRoleType(Enum):
    """Types of management roles in French companies"""
    PDG = "Président-Directeur Général"
    PRESIDENT = "Président"
    VICE_PRESIDENT = "Vice-Président"
    DIRECTEUR = "Directeur"
    DIRECTEUR_GENERAL = "Directeur Général"
    GERANT = "Gérant"
    ADMINISTRATEUR = "Administrateur"
    COMMISSAIRE = "Commissaire aux Comptes"
    ASSOCIE = "Associé"
    MEMBRE_DIRECTOIRE = "Membre du Directoire"
    PRESIDENT_CONSEIL_SURVEILLANCE = "Président du Conseil de Surveillance"
    MEMBER_CONSEIL_SURVEILLANCE = "Membre du Conseil de Surveillance"
    OTHER = "Autre"


class EstablishmentActivityType(Enum):
    """Types of establishment activities"""
    HEAD_OFFICE = "Siège Social"
    BRANCH = "Succursale"
    SECONDARY_OFFICE = "Bureau Secondaire"
    WAREHOUSE = "Entrepôt"
    PRODUCTION = "Production"
    RESEARCH = "Recherche"
    DISTRIBUTION = "Distribution"
    SALES_OFFICE = "Bureau Commercial"
    OTHER = "Autre"


class ComplianceEventType(Enum):
    """Types of compliance/regulatory events"""
    MERGER = "Fusion"
    ACQUISITION = "Acquisition"
    DISSOLUTION = "Dissolution"
    RADIATION = "Radiation"
    CREATION = "Création"
    MODIFICATION = "Modification"
    ADDRESS_CHANGE = "Changement d'Adresse"
    ACTIVITY_CHANGE = "Changement d'Activité"
    CAPITAL_CHANGE = "Changement de Capital"
    MANAGEMENT_CHANGE = "Changement de Direction"
    STRUCTURE_CHANGE = "Changement de Structure"
    OTHER = "Autre"


class RiskLevel(Enum):
    """Risk assessment levels"""
    GREEN = "Faible Risque"
    YELLOW = "Risque Moyen"
    RED = "Risque Élevé"
    CRITICAL = "Risque Critique"


@dataclass
class Address:
    """French address structure"""
    street: str
    postal_code: str
    city: str
    country: str = "France"
    complement: Optional[str] = None
    
    def __str__(self) -> str:
        return f"{self.street}, {self.postal_code} {self.city}, {self.country}"


@dataclass
class FrenchManagementRole:
    """Management person or entity with detailed information"""
    id: str
    type: str  # "INDIVIDU" | "ENTREPRISE"
    role: ManagementRoleType
    start_date: str
    end_date: Optional[str] = None
    status: str = "actif"  # "actif" | "démissionnaire"
    is_active: bool = True
    
    # For individuals
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    birth_date: Optional[str] = None  # Format: YYYY-MM (may be masked for RGPD)
    gender: Optional[str] = None  # "M" | "F"
    nationality: Optional[str] = None
    address: Optional[Address] = None
    
    # For enterprises
    enterprise_siren: Optional[str] = None
    enterprise_name: Optional[str] = None
    enterprise_legal_form: Optional[str] = None
    
    @property
    def display_name(self) -> str:
        if self.type == "INDIVIDU":
            return f"{self.first_name or ''} {self.last_name or ''}".strip()
        else:
            return self.enterprise_name or "Unknown"
    
    @property
    def role_display(self) -> str:
        return self.role.value
    
    @property
    def is_current(self) -> bool:
        return self.end_date is None and self.is_active


@dataclass
class FrenchEstablishment:
    """Company establishment/branch with complete information"""
    nic: str  # National Identification Code (5 digits)
    address: Address
    activity_code: str  # APE code (e.g., "7740Z")
    activity_description: str
    establishment_type: EstablishmentActivityType
    opening_date: str
    closing_date: Optional[str] = None
    number_of_employees: Optional[int] = None
    is_primary: bool = False
    
    @property
    def is_open(self) -> bool:
        return self.closing_date is None
    
    @property
    def years_operating(self) -> int:
        from datetime import datetime
        try:
            open_date = datetime.strptime(self.opening_date, "%Y-%m-%d")
            close_date = datetime.strptime(
                self.closing_date, "%Y-%m-%d"
            ) if self.closing_date else datetime.now()
            return (close_date - open_date).days // 365
        except:
            return 0


@dataclass
class FrenchComplianceRecord:
    """Historical compliance event from RCS records"""
    number: str  # Official observation number
    date_added: str
    date_effective: str
    date_greffe: str
    description: str
    event_type: ComplianceEventType
    status: str  # "Ajout" | "Rectification" | "Radiation"
    official_notes: Optional[str] = None
    
    @property
    def is_recent(self) -> bool:
        """Check if event is from last 6 months"""
        from datetime import datetime, timedelta
        try:
            event_date = datetime.strptime(self.date_effective, "%Y-%m-%d")
            return (datetime.now() - event_date).days < 180
        except:
            return False


@dataclass
class CapitalStructure:
    """Company capital information"""
    amount: float
    currency: str = "EUR"
    is_variable: bool = False
    change_history: List[Dict[str, Any]] = field(default_factory=list)
    
    @property
    def formatted_amount(self) -> str:
        return f"€{self.amount:,.2f}"


@dataclass
class RiskFactor:
    """Individual risk assessment factor"""
    name: str
    description: str
    severity: RiskLevel
    evidence: str
    recommendation: str
    weight: float = 1.0  # Multiplier for score calculation


@dataclass
class FrenchCompanyRiskAssessment:
    """Complete risk assessment for a French company"""
    siren: str
    overall_risk_level: RiskLevel
    risk_score: float  # 0-100 scale
    
    management_stability_score: float
    operational_transparency_score: float
    regulatory_compliance_score: float
    financial_viability_score: float
    geographic_complexity_score: float
    
    risk_factors: List[RiskFactor] = field(default_factory=list)
    
    # Detailed findings
    management_concerns: List[str] = field(default_factory=list)
    compliance_issues: List[str] = field(default_factory=list)
    operational_red_flags: List[str] = field(default_factory=list)
    positive_indicators: List[str] = field(default_factory=list)
    
    assessment_date: str = field(default_factory=lambda: datetime.now().isoformat())
    
    @property
    def risk_color(self) -> str:
        if self.overall_risk_level == RiskLevel.GREEN:
            return "#00AA00"
        elif self.overall_risk_level == RiskLevel.YELLOW:
            return "#FFAA00"
        elif self.overall_risk_level == RiskLevel.RED:
            return "#DD0000"
        else:
            return "#000000"
    
    @property
    def average_component_score(self) -> float:
        scores = [
            self.management_stability_score,
            self.operational_transparency_score,
            self.regulatory_compliance_score,
            self.financial_viability_score,
            self.geographic_complexity_score,
        ]
        return sum(scores) / len(scores) if scores else 0


@dataclass
class FrenchCompanyFull:
    """Complete French company data from INPI"""
    
    # Identity
    siren: str
    denomination: str
    legal_form: str
    legal_form_code: str
    
    # Basic Info
    creation_date: str
    status: str
    ape_code: str
    ape_label: str
    number_of_employees: int
    capital: CapitalStructure
    
    # Address
    head_office: FrenchEstablishment
    
    # Operations
    establishments: List[FrenchEstablishment] = field(default_factory=list)
    management: List[FrenchManagementRole] = field(default_factory=list)
    
    # Compliance
    compliance_history: List[FrenchComplianceRecord] = field(default_factory=list)
    
    # Metadata
    last_updated: str = field(default_factory=lambda: datetime.now().isoformat())
    data_source: str = "INPI RNE API"
    
    # Risk Assessment (computed)
    risk_assessment: Optional[FrenchCompanyRiskAssessment] = None
    
    @property
    def total_establishments(self) -> int:
        return len(self.establishments) + 1  # +1 for head office
    
    @property
    def open_establishments(self) -> int:
        return sum(1 for e in self.establishments if e.is_open) + (
            1 if self.head_office.is_open else 0
        )
    
    @property
    def closed_establishments(self) -> int:
        return self.total_establishments - self.open_establishments
    
    @property
    def management_count(self) -> int:
        return len([m for m in self.management if m.is_current])
    
    @property
    def recent_compliance_events(self) -> List[FrenchComplianceRecord]:
        return [e for e in self.compliance_history if e.is_recent]
    
    @property
    def is_stable(self) -> bool:
        """Check if company appears stable based on available data"""
        # Stable if: no recent major changes, no radiations, active management
        has_radiations = any(
            "radiation" in e.description.lower() 
            for e in self.compliance_history
        )
        has_recent_changes = len(self.recent_compliance_events) > 2
        has_active_management = self.management_count > 0
        
        return not has_radiations and not has_recent_changes and has_active_management
    
    @property
    def transparency_score(self) -> float:
        """Calculate transparency score based on disclosed information"""
        score = 50.0  # Base score
        
        # More establishments = more transparency (public operations)
        score += min(self.total_establishments * 0.5, 20)
        
        # More management roles disclosed = more transparency
        score += min(self.management_count * 2, 15)
        
        # Detailed compliance history = transparency
        score += min(len(self.compliance_history) * 0.5, 15)
        
        return min(score, 100)


@dataclass
class FrenchCompanyComparison:
    """Compare two French companies"""
    company1: FrenchCompanyFull
    company2: FrenchCompanyFull
    
    @property
    def size_difference_percent(self) -> float:
        if self.company2.number_of_employees == 0:
            return 0
        diff = self.company1.number_of_employees - self.company2.number_of_employees
        return (diff / self.company2.number_of_employees) * 100
    
    @property
    def capital_difference_percent(self) -> float:
        if self.company2.capital.amount == 0:
            return 0
        diff = self.company1.capital.amount - self.company2.capital.amount
        return (diff / self.company2.capital.amount) * 100
    
    @property
    def geographic_spread_difference(self) -> int:
        return self.company1.total_establishments - self.company2.total_establishments
    
    @property
    def age_difference_years(self) -> int:
        from datetime import datetime
        try:
            date1 = datetime.strptime(self.company1.creation_date, "%Y-%m-%d")
            date2 = datetime.strptime(self.company2.creation_date, "%Y-%m-%d")
            return abs((date1 - date2).days // 365)
        except:
            return 0
