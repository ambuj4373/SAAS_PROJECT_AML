"""
core/structured_outputs.py — Pydantic models for structured LLM responses.

Instead of parsing free-text LLM output, the structured-output layer asks the
LLM to return JSON conforming to Pydantic schemas.  A thin wrapper around
the LLM call validates the JSON against the model and falls back to
best-effort extraction if the JSON is malformed.

Public API:
    parse_structured_report(raw_text, model_cls)  → pydantic model instance
    build_structured_prompt_suffix(model_cls)      → instruction text
    StructuredCharityReport   — full charity assessment
    StructuredCompanyReport   — full company assessment
"""

from __future__ import annotations

import json
import re
from typing import Any, Optional

from pydantic import BaseModel, Field

from core.models import RiskLevel


# ═══════════════════════════════════════════════════════════════════════════════
# STRUCTURED OUTPUT MODELS
# ═══════════════════════════════════════════════════════════════════════════════

class EvidenceCitation(BaseModel):
    """A single piece of evidence backing a claim."""
    claim: str = Field(..., description="The specific claim or finding")
    source: str = Field("", description="Source name/URL")
    source_type: str = Field("unknown", description="official_register|government|major_media|trade_media|web|unknown")
    confidence: float = Field(0.5, ge=0, le=1, description="0-1 confidence in this evidence")
    quote: str = Field("", description="Verbatim excerpt or data point backing the claim")


class RiskFinding(BaseModel):
    """A single risk finding with evidence chain."""
    category: str = Field(..., description="Risk category: Geography|Financial|Governance|Media|Transparency|Operational")
    title: str = Field(..., description="Short finding headline")
    detail: str = Field("", description="Explanatory paragraph")
    severity: str = Field("Medium", description="Critical|High|Medium|Low|Info")
    evidence: list[EvidenceCitation] = Field(default_factory=list)
    confidence: float = Field(0.5, ge=0, le=1, description="Overall confidence in this finding")
    is_hard_stop: bool = Field(False, description="Whether this is a hard-stop/veto finding")


class GovernanceFinding(BaseModel):
    """Governance assessment result."""
    area: str = Field(..., description="Governance area assessed")
    status: str = Field("Unknown", description="Adequate|Concerns|Inadequate|Unknown")
    detail: str = ""
    evidence: list[EvidenceCitation] = Field(default_factory=list)


class FinancialInsight(BaseModel):
    """A financial pattern or observation."""
    metric: str = Field(..., description="What was measured")
    observation: str = Field(..., description="What was found")
    severity: str = Field("Info", description="Critical|High|Medium|Low|Info")
    trend: str = Field("", description="increasing|decreasing|stable|volatile|unknown")
    years_affected: list[str] = Field(default_factory=list)


class EntityMention(BaseModel):
    """A related entity discovered during analysis."""
    name: str
    entity_type: str = Field("unknown", description="person|company|charity|jurisdiction|other")
    relationship: str = Field("", description="How this entity relates to the subject")
    risk_note: str = Field("", description="Any risk observation about this entity")


class OverallAssessment(BaseModel):
    """Top-level assessment summary."""
    risk_level: str = Field("Medium", description="Critical|High|Medium|Low")
    risk_score: int = Field(50, ge=0, le=100, description="0-100 risk score")
    headline: str = Field("", description="One-sentence risk summary")
    recommendation: str = Field("", description="Recommended action")
    confidence: float = Field(0.5, ge=0, le=1, description="Overall confidence in assessment")
    key_concerns: list[str] = Field(default_factory=list, description="Top 3-5 concerns")
    key_strengths: list[str] = Field(default_factory=list, description="Top 3-5 positive indicators")
    data_gaps: list[str] = Field(default_factory=list, description="Notable data not available")


class StructuredCharityReport(BaseModel):
    """Complete structured output for charity due-diligence."""
    assessment: OverallAssessment = Field(default_factory=OverallAssessment)
    risk_findings: list[RiskFinding] = Field(default_factory=list)
    governance_findings: list[GovernanceFinding] = Field(default_factory=list)
    financial_insights: list[FinancialInsight] = Field(default_factory=list)
    related_entities: list[EntityMention] = Field(default_factory=list)
    narrative_report: str = Field("", description="Full markdown narrative report")


class StructuredCompanyReport(BaseModel):
    """Complete structured output for company sense-check."""
    assessment: OverallAssessment = Field(default_factory=OverallAssessment)
    risk_findings: list[RiskFinding] = Field(default_factory=list)
    governance_findings: list[GovernanceFinding] = Field(default_factory=list)
    financial_insights: list[FinancialInsight] = Field(default_factory=list)
    related_entities: list[EntityMention] = Field(default_factory=list)
    narrative_report: str = Field("", description="Full markdown narrative report")


# ═══════════════════════════════════════════════════════════════════════════════
# PROMPT HELPER — tells the LLM how to produce structured JSON
# ═══════════════════════════════════════════════════════════════════════════════

_STRUCTURED_SUFFIX_TEMPLATE = """

─── STRUCTURED OUTPUT INSTRUCTIONS ────────────────────────────────────────
In addition to your full markdown narrative report, you MUST also output a
JSON block wrapped in ```json ... ``` fences at the VERY END of your response.

The JSON MUST conform to this schema:

{schema_json}

RULES:
1. The "narrative_report" field inside the JSON should be empty string "" —
   your full markdown report above the JSON block IS the narrative.
2. Every risk_finding MUST include at least one evidence citation.
3. Confidence values range 0.0 (no evidence) to 1.0 (verified from official source).
4. source_type must be one of: official_register, government, major_media,
   trade_media, web, unknown.
5. Do NOT invent evidence. If you lack data, set confidence low and note data_gaps.
──────────────────────────────────────────────────────────────────────────
"""


def build_structured_prompt_suffix(model_cls: type[BaseModel] = StructuredCharityReport) -> str:
    """Return the instruction block to append to prompts for structured output."""
    schema = model_cls.model_json_schema()
    # Compact but readable
    schema_str = json.dumps(schema, indent=2, default=str)
    # Truncate deeply nested $defs to keep prompt size manageable
    if len(schema_str) > 4000:
        # Keep top-level properties + first-level $defs descriptions
        top_schema = {k: v for k, v in schema.items() if k != "$defs"}
        defs_summary = {}
        for name, defn in schema.get("$defs", {}).items():
            defs_summary[name] = {
                "type": "object",
                "description": defn.get("description", ""),
                "properties": {pk: {"type": pv.get("type", "string")} for pk, pv in defn.get("properties", {}).items()},
            }
        top_schema["$defs_summary"] = defs_summary
        schema_str = json.dumps(top_schema, indent=2, default=str)

    return _STRUCTURED_SUFFIX_TEMPLATE.format(schema_json=schema_str)


# ═══════════════════════════════════════════════════════════════════════════════
# PARSER — extract and validate structured JSON from LLM response
# ═══════════════════════════════════════════════════════════════════════════════

def _extract_json_block(text: str) -> str | None:
    """Find the last ```json ... ``` fenced block in text."""
    pattern = r"```json\s*\n?(.*?)```"
    matches = re.findall(pattern, text, re.DOTALL)
    if matches:
        return matches[-1].strip()
    # Fallback: look for lone JSON object at end
    stripped = text.rstrip()
    if stripped.endswith("}"):
        # Walk back to find matching {
        depth, idx = 0, len(stripped) - 1
        while idx >= 0:
            if stripped[idx] == "}":
                depth += 1
            elif stripped[idx] == "{":
                depth -= 1
                if depth == 0:
                    candidate = stripped[idx:]
                    try:
                        json.loads(candidate)
                        return candidate
                    except json.JSONDecodeError:
                        break
            idx -= 1
    return None


def parse_structured_report(
    raw_text: str,
    model_cls: type[BaseModel] = StructuredCharityReport,
) -> tuple[BaseModel | None, str]:
    """Parse LLM response into structured model + clean narrative.

    Returns:
        (structured_model, narrative_text)
        structured_model is None if JSON parsing failed.
        narrative_text is the report text with JSON block removed.
    """
    json_str = _extract_json_block(raw_text)
    narrative = raw_text

    if json_str:
        # Remove the JSON block from narrative
        # Find and remove the last ```json ... ``` block
        pattern = r"```json\s*\n?" + re.escape(json_str) + r"\s*```"
        narrative = re.sub(pattern, "", raw_text).rstrip()
        # Also clean up any trailing structure instruction leftover
        narrative = re.sub(r"\n─── STRUCTURED OUTPUT.*$", "", narrative, flags=re.DOTALL).rstrip()

        try:
            data = json.loads(json_str)
            # Inject narrative into the model
            if "narrative_report" in data and not data["narrative_report"]:
                data["narrative_report"] = narrative
            model = model_cls.model_validate(data)
            return model, narrative
        except (json.JSONDecodeError, Exception):
            pass  # Fall through to None

    return None, narrative


def extract_findings_from_narrative(raw_text: str) -> dict[str, Any]:
    """Best-effort extraction of key findings from unstructured narrative.

    Used as fallback when LLM doesn't produce valid JSON output.
    Returns a dict with partial findings that can supplement display.
    """
    findings: dict[str, Any] = {
        "risk_findings": [],
        "key_concerns": [],
        "key_strengths": [],
    }

    lines = raw_text.split("\n")
    current_section = ""

    for line in lines:
        stripped = line.strip()
        lower = stripped.lower()

        # Detect section headers
        if "risk" in lower and ("finding" in lower or "concern" in lower or "flag" in lower):
            current_section = "risk"
        elif "strength" in lower or "positive" in lower or "mitigant" in lower:
            current_section = "strength"
        elif any(kw in lower for kw in ["concern", "adverse", "red flag", "warning"]):
            current_section = "concern"

        # Extract bullet points
        if stripped.startswith(("- ", "• ", "* ", "→ ")):
            item = stripped.lstrip("-•*→ ").strip()
            if item and len(item) > 10:
                if current_section == "risk":
                    findings["risk_findings"].append(item)
                elif current_section == "concern":
                    findings["key_concerns"].append(item)
                elif current_section == "strength":
                    findings["key_strengths"].append(item)

    # Deduplicate
    for key in findings:
        findings[key] = list(dict.fromkeys(findings[key]))[:10]

    return findings
