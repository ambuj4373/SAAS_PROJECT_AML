"""
core/models.py — Pydantic data models for V3 structured intelligence pipeline.

All pipeline stages produce and consume typed models, ensuring data integrity
across the intelligence workflow and providing clear contracts between modules.
"""

from __future__ import annotations

from datetime import date, datetime
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field, field_validator


# ═══════════════════════════════════════════════════════════════════════════════
# ENUMERATIONS
# ═══════════════════════════════════════════════════════════════════════════════

class RiskLevel(str, Enum):
    """Standardised risk levels used across the platform."""
    CRITICAL = "Critical"
    HIGH = "High"
    MEDIUM = "Medium"
    LOW = "Low"
    NONE = "None"
    UNKNOWN = "Unknown"


class RAGStatus(str, Enum):
    """Red-Amber-Green traffic-light status."""
    RED = "Red"
    AMBER = "Amber"
    GREEN = "Green"
    GREY = "Grey"


class EntityType(str, Enum):
    CHARITY = "charity"
    COMPANY = "company"
    TRUSTEE = "trustee"
    DIRECTOR = "director"
    PSC = "psc"


class AnalysisMode(str, Enum):
    DONOR_OVERVIEW = "donor_overview"
    FULL_DUE_DILIGENCE = "full_due_diligence"
    COMPANY_CHECK = "company_check"


class PolicyStatus(str, Enum):
    FOUND = "Found"
    PARTIAL = "Partial"
    NOT_LOCATED = "Not Located"


# ═══════════════════════════════════════════════════════════════════════════════
# CORE DATA MODELS
# ═══════════════════════════════════════════════════════════════════════════════

class CostInfo(BaseModel):
    """Token usage and cost tracking for a single LLM call."""
    model: str = ""
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    cost_usd: float = 0.0


class RiskSignal(BaseModel):
    """A single risk signal/flag raised by any analysis stage."""
    category: str = Field(..., description="Risk category (e.g., Geography, Financial, Governance)")
    description: str = Field(..., description="Human-readable risk observation")
    severity: RiskLevel = RiskLevel.MEDIUM
    source: str = Field("", description="Which analysis stage produced this signal")
    evidence: str = Field("", description="Supporting evidence or data reference")
    score_impact: float = Field(0.0, description="Points added to risk score (0-100 scale)")


class RiskScore(BaseModel):
    """Structured numerical risk score with category breakdown."""
    overall_score: float = Field(0.0, ge=0, le=100, description="0=safe, 100=critical")
    overall_level: RiskLevel = RiskLevel.UNKNOWN
    category_scores: dict[str, float] = Field(default_factory=dict)
    category_levels: dict[str, RiskLevel] = Field(default_factory=dict)
    signals: list[RiskSignal] = Field(default_factory=list)
    hard_stops: list[str] = Field(default_factory=list)
    confidence: float = Field(0.5, ge=0, le=1, description="Confidence in score accuracy")
    methodology_notes: list[str] = Field(default_factory=list)

    @property
    def has_hard_stops(self) -> bool:
        return len(self.hard_stops) > 0


# ═══════════════════════════════════════════════════════════════════════════════
# ENTITY MODELS
# ═══════════════════════════════════════════════════════════════════════════════

class PersonEntity(BaseModel):
    """A natural person (trustee, director, PSC, UBO)."""
    name: str
    role: str = ""
    nationality: str = ""
    country_of_residence: str = ""
    appointed_on: str = ""
    ceased_on: str = ""
    is_active: bool = True
    flags: list[str] = Field(default_factory=list)
    other_appointments: list[dict[str, Any]] = Field(default_factory=list)
    ownership_band: str | None = None


class CorporateEntity(BaseModel):
    """A corporate entity (company, corporate PSC, parent)."""
    name: str
    company_number: str = ""
    jurisdiction: str = ""
    legal_form: str = ""
    is_active: bool = True
    terminal_type: str = ""
    flags: list[str] = Field(default_factory=list)


class UBOChain(BaseModel):
    """Ultimate Beneficial Ownership trace result."""
    chain: list[dict[str, Any]] = Field(default_factory=list)
    ultimate_owners: list[dict[str, Any]] = Field(default_factory=list)
    layers_traced: int = 0
    max_depth_reached: bool = False
    graph_edges: list[tuple[str, str, str]] = Field(default_factory=list)


# ═══════════════════════════════════════════════════════════════════════════════
# FINANCIAL MODELS
# ═══════════════════════════════════════════════════════════════════════════════

class FinancialYear(BaseModel):
    """A single year of financial data."""
    year: str
    income: float = 0.0
    expenditure: float = 0.0
    surplus: float = 0.0

    @field_validator("surplus", mode="before")
    @classmethod
    def compute_surplus(cls, v, info):
        if v == 0.0 and "income" in info.data and "expenditure" in info.data:
            return info.data["income"] - info.data["expenditure"]
        return v


class FinancialAnomaly(BaseModel):
    """Result from financial anomaly detection."""
    flags: list[str] = Field(default_factory=list)
    income_volatility: float = 0.0
    expenditure_volatility: float = 0.0
    yoy_income: list[dict[str, Any]] = Field(default_factory=list)
    yoy_expenditure: list[dict[str, Any]] = Field(default_factory=list)
    ratio_shifts: list[dict[str, Any]] = Field(default_factory=list)
    anomaly_count: int = 0
    summary: str = ""


# ═══════════════════════════════════════════════════════════════════════════════
# SEARCH / SCREENING MODELS
# ═══════════════════════════════════════════════════════════════════════════════

class SearchResult(BaseModel):
    """A single search result from any provider."""
    title: str = ""
    url: str = ""
    content: str = ""
    date: str = ""
    source: str = ""  # tavily, serper_news, serper_web
    is_relevant: bool = False
    is_error: bool = False


class AdverseMediaReport(BaseModel):
    """Aggregated adverse media screening results for one entity."""
    entity_name: str
    entity_type: EntityType = EntityType.CHARITY
    results: list[SearchResult] = Field(default_factory=list)
    true_adverse_count: int = 0
    sources_searched: list[str] = Field(default_factory=list)


class FATFScreenResult(BaseModel):
    """FATF predicate-offence screening result for one entity."""
    entity_name: str
    risk_level: str = "Unknown"
    is_match: bool = False
    summary: str = ""
    categories_matched: list[str] = Field(default_factory=list)
    evidence: list[dict[str, Any]] = Field(default_factory=list)
    cost_info: CostInfo = Field(default_factory=CostInfo)


class PolicyAssessment(BaseModel):
    """Assessment of a single policy area."""
    policy_name: str
    status: PolicyStatus = PolicyStatus.NOT_LOCATED
    confidence: str = ""  # high, medium, low
    source_url: str = ""
    source_label: str = ""
    evidence: str = ""


# ═══════════════════════════════════════════════════════════════════════════════
# COUNTRY / GEOGRAPHY MODELS
# ═══════════════════════════════════════════════════════════════════════════════

class CountryRisk(BaseModel):
    """Risk classification for a single country."""
    country: str
    risk_level: str = "Unknown"
    context: str = ""  # How this country relates to the entity
    continent: str = ""


# ═══════════════════════════════════════════════════════════════════════════════
# PIPELINE STATE MODELS
# ═══════════════════════════════════════════════════════════════════════════════

class CharityPipelineState(BaseModel):
    """Complete state for the charity analysis LangGraph pipeline."""

    class Config:
        arbitrary_types_allowed = True

    # ── Input ────────────────────────────────────────────────────────
    charity_number: str = ""
    mode: AnalysisMode = AnalysisMode.FULL_DUE_DILIGENCE
    llm_provider: str = ""
    llm_model: str = ""
    use_vision: bool = False
    uploaded_docs: list[Any] = Field(default_factory=list)
    uploaded_gov_docs: list[Any] = Field(default_factory=list)
    cc_printout: Any = None
    manual_social_links: dict[str, str] = Field(default_factory=dict)
    website_override: str = ""

    # ── Step 1: Registry Data ────────────────────────────────────────
    charity_data: dict[str, Any] = Field(default_factory=dict)
    cc_governance: dict[str, Any] = Field(default_factory=dict)
    financial_history: list[dict[str, Any]] = Field(default_factory=list)
    ch_data: dict[str, Any] | None = None
    trustees: list[str] = Field(default_factory=list)
    entity_name: str = ""
    website_url: str = ""

    # ── Step 2: Document Extraction ──────────────────────────────────
    cc_pdf_result: dict[str, Any] | None = None
    cc_pdf_text: str = ""
    uploaded_texts: list[str] = Field(default_factory=list)
    gov_doc_texts: list[str] = Field(default_factory=list)
    extraction_metadata: list[dict[str, Any]] = Field(default_factory=list)
    partners_discovered: list[dict[str, Any]] = Field(default_factory=list)

    # ── Step 3: OSINT / Web Intelligence ─────────────────────────────
    adverse_org: list[dict[str, Any]] = Field(default_factory=list)
    adverse_trustees: dict[str, list[dict[str, Any]]] = Field(default_factory=dict)
    positive_media: list[dict[str, Any]] = Field(default_factory=list)
    online_presence: list[dict[str, Any]] = Field(default_factory=list)
    policy_results: list[dict[str, Any]] = Field(default_factory=list)
    policy_audit: list[dict[str, Any]] = Field(default_factory=list)
    policy_doc_links: list[dict[str, Any]] = Field(default_factory=list)
    policy_classification: list[dict[str, Any]] = Field(default_factory=list)
    social_links: dict[str, str] = Field(default_factory=dict)
    hrcob_core_controls: dict[str, Any] = Field(default_factory=dict)
    partnership_results: list[dict[str, Any]] = Field(default_factory=list)
    fatf_org_screen: dict[str, Any] | None = None
    fatf_trustee_screens: dict[str, dict[str, Any]] = Field(default_factory=dict)
    search_failures: list[str] = Field(default_factory=list)

    # ── Step 4: Governance Analysis ──────────────────────────────────
    governance_indicators: dict[str, Any] = Field(default_factory=dict)
    structural_governance: dict[str, Any] = Field(default_factory=dict)
    trustee_appointments: dict[str, list[dict[str, Any]]] = Field(default_factory=dict)
    financial_anomalies: dict[str, Any] = Field(default_factory=dict)

    # ── Step 5: Geography ────────────────────────────────────────────
    country_risk_classified: list[dict[str, Any]] = Field(default_factory=list)
    country_kyc_profiles: list[dict[str, Any]] = Field(default_factory=list)

    # ── Step 6: Risk Scoring ─────────────────────────────────────────
    risk_score: dict[str, Any] = Field(default_factory=dict)

    # ── Step 7: LLM Report ───────────────────────────────────────────
    llm_prompt: str = ""
    llm_report: str = ""
    cost_info: dict[str, Any] = Field(default_factory=dict)

    # ── Pipeline Metadata ────────────────────────────────────────────
    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    stage_timings: dict[str, float] = Field(default_factory=dict)
    db_row_id: int | None = None


class CompanyPipelineState(BaseModel):
    """Complete state for the company analysis LangGraph pipeline."""

    class Config:
        arbitrary_types_allowed = True

    # ── Input ────────────────────────────────────────────────────────
    company_number: str = ""
    website_url: str = ""
    llm_provider: str = ""
    llm_model: str = ""

    # ── Step 1: Company Check Bundle ─────────────────────────────────
    company_check: dict[str, Any] = Field(default_factory=dict)

    # ── Step 2: Risk Scoring ─────────────────────────────────────────
    risk_score: dict[str, Any] = Field(default_factory=dict)

    # ── Step 3: LLM Report ───────────────────────────────────────────
    llm_prompt: str = ""
    llm_report: str = ""
    cost_info: dict[str, Any] = Field(default_factory=dict)

    # ── Pipeline Metadata ────────────────────────────────────────────
    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    stage_timings: dict[str, float] = Field(default_factory=dict)
    db_row_id: int | None = None


# ═══════════════════════════════════════════════════════════════════════════════
# FCA REGULATION MODELS
# ═══════════════════════════════════════════════════════════════════════════════

class FCARegulationStatus(BaseModel):
    """FCA Financial Services Register lookup result."""
    found: bool = False
    firm_name: str = ""
    frn: str = ""  # FCA Firm Reference Number
    is_regulated: bool = False
    authorisation_status: str = ""  # "Authorised", "No longer authorised", etc.
    regulated_activities: list[str] = Field(default_factory=list)
    risk_reduction_factor: float = Field(1.0, ge=0.0, le=1.0, description="Multiplier to apply to risk score")
    compliance_benefits: list[str] = Field(default_factory=list)
    lookup_timestamp: str = ""
    raw_fca_data: dict[str, Any] = Field(default_factory=dict)

    @property
    def risk_impact_summary(self) -> str:
        """Human-readable summary of FCA regulation impact."""
        if not self.found:
            return "Not FCA regulated — higher compliance scrutiny required"
        if self.is_regulated:
            return f"FCA regulated ({self.authorisation_status}) — regulatory oversight reduces risk by {(1 - self.risk_reduction_factor) * 100:.0f}%"
        else:
            return f"Formerly FCA regulated — {self.authorisation_status}"
