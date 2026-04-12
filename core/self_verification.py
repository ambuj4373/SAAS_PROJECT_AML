"""
core/self_verification.py — AI self-verification layer.

After the LLM produces its initial report, this module asks it to review its
own conclusions, check whether each claim is grounded in the supplied evidence,
and flag any unsupported assertions.

The verification prompt is deliberately shorter and focused: it receives the
original report plus the structured data summary and must produce a compact
verification digest.

Public API:
    build_verification_prompt(report_text, data_summary)  → str
    parse_verification_result(raw_text)                   → VerificationResult
    render_verification_badge(result)                     → HTML string
"""

from __future__ import annotations

import json
import re
from typing import Any

from pydantic import BaseModel, Field


# ═══════════════════════════════════════════════════════════════════════════════
# MODELS
# ═══════════════════════════════════════════════════════════════════════════════

class ClaimVerification(BaseModel):
    """Verification of a single claim in the report."""
    claim: str = Field(..., description="The claim from the report")
    supported: bool = Field(True, description="Whether evidence supports this claim")
    evidence_ref: str = Field("", description="Source or data that supports/contradicts")
    confidence: float = Field(0.5, ge=0, le=1)
    note: str = Field("", description="Verification note")


class VerificationResult(BaseModel):
    """Complete self-verification output."""
    claims_checked: int = 0
    claims_supported: int = 0
    claims_unsupported: int = 0
    claims_uncertain: int = 0
    overall_reliability: float = Field(0.5, ge=0, le=1, description="0-1 reliability score")
    verification_notes: list[str] = Field(default_factory=list)
    unsupported_claims: list[ClaimVerification] = Field(default_factory=list)
    all_claims: list[ClaimVerification] = Field(default_factory=list)

    @property
    def reliability_label(self) -> str:
        if self.overall_reliability >= 0.85:
            return "High"
        if self.overall_reliability >= 0.65:
            return "Good"
        if self.overall_reliability >= 0.45:
            return "Moderate"
        return "Low"

    @property
    def reliability_color(self) -> str:
        if self.overall_reliability >= 0.85:
            return "#28a745"
        if self.overall_reliability >= 0.65:
            return "#17a2b8"
        if self.overall_reliability >= 0.45:
            return "#ffc107"
        return "#dc3545"


# ═══════════════════════════════════════════════════════════════════════════════
# PROMPT BUILDER
# ═══════════════════════════════════════════════════════════════════════════════

_VERIFICATION_PROMPT = """You are an independent auditor reviewing an AI-generated due-diligence report.

Your task is to check whether EACH key claim in the report below is supported
by the evidence data provided. You must NOT evaluate the quality of the entity —
only whether the report's statements are grounded in the supplied data.

══════ REPORT TO VERIFY ══════
{report_text}

══════ EVIDENCE DATA ══════
{data_summary}

══════ INSTRUCTIONS ══════
1. Identify every factual claim and risk finding in the report.
2. For each claim, check if it is supported by the evidence data.
3. Mark claims as: SUPPORTED (evidence found), UNSUPPORTED (no evidence),
   or UNCERTAIN (partial evidence or ambiguous).
4. Give an overall reliability score (0.0 to 1.0).

Output a JSON block wrapped in ```json ... ``` fences:
{{
  "claims_checked": <int>,
  "claims_supported": <int>,
  "claims_unsupported": <int>,
  "claims_uncertain": <int>,
  "overall_reliability": <float 0-1>,
  "verification_notes": ["<note1>", ...],
  "unsupported_claims": [
    {{"claim": "<text>", "supported": false, "evidence_ref": "<what's missing>", "confidence": <float>, "note": "<explanation>"}}
  ],
  "all_claims": [
    {{"claim": "<text>", "supported": true/false, "evidence_ref": "<source>", "confidence": <float>, "note": ""}}
  ]
}}

Be rigorous but fair. Claims about risk levels derived from the data ARE supported
if the data backs them up. Only flag claims that have NO evidence or are
contradicted by the data.
"""


def build_verification_prompt(
    report_text: str,
    data_summary: str,
    max_report_chars: int = 8000,
    max_data_chars: int = 6000,
) -> str:
    """Build the self-verification prompt, truncating if needed."""
    _report = report_text[:max_report_chars] if len(report_text) > max_report_chars else report_text
    _data = data_summary[:max_data_chars] if len(data_summary) > max_data_chars else data_summary
    return _VERIFICATION_PROMPT.format(report_text=_report, data_summary=_data)


# ═══════════════════════════════════════════════════════════════════════════════
# PARSER
# ═══════════════════════════════════════════════════════════════════════════════

def parse_verification_result(raw_text: str) -> VerificationResult:
    """Parse the verification LLM response into a VerificationResult."""
    # Extract JSON block
    pattern = r"```json\s*\n?(.*?)```"
    matches = re.findall(pattern, raw_text, re.DOTALL)

    if matches:
        json_str = matches[-1].strip()
        try:
            data = json.loads(json_str)
            return VerificationResult.model_validate(data)
        except (json.JSONDecodeError, Exception):
            pass

    # Fallback: extract numbers from text
    result = VerificationResult()
    numbers = re.findall(r"(\d+)\s+claims?\s+(checked|supported|unsupported|uncertain)", raw_text.lower())
    for num_str, label in numbers:
        num = int(num_str)
        if label == "checked":
            result.claims_checked = num
        elif label == "supported":
            result.claims_supported = num
        elif label == "unsupported":
            result.claims_unsupported = num
        elif label == "uncertain":
            result.claims_uncertain = num

    # Try to find reliability score
    rel_match = re.search(r"reliability[:\s]+(\d+\.?\d*)", raw_text.lower())
    if rel_match:
        val = float(rel_match.group(1))
        result.overall_reliability = val if val <= 1 else val / 100

    if result.claims_checked > 0:
        result.overall_reliability = result.claims_supported / result.claims_checked

    return result


# ═══════════════════════════════════════════════════════════════════════════════
# UI HELPER
# ═══════════════════════════════════════════════════════════════════════════════

def render_verification_badge(result: VerificationResult) -> str:
    """Return HTML for a compact verification badge."""
    pct = int(result.overall_reliability * 100)
    label = result.reliability_label
    color = result.reliability_color

    badge_html = f"""
    <div style="display:inline-flex;align-items:center;gap:10px;padding:8px 16px;
                border-radius:8px;background:{color}18;border:1px solid {color}40;
                margin:8px 0;">
        <div style="font-size:24px;font-weight:700;color:{color};">{pct}%</div>
        <div>
            <div style="font-size:13px;font-weight:600;color:{color};">
                AI Verification: {label} Reliability
            </div>
            <div style="font-size:11px;color:#666;">
                {result.claims_supported}/{result.claims_checked} claims evidence-backed
                {f' · {result.claims_unsupported} unsupported' if result.claims_unsupported else ''}
            </div>
        </div>
    </div>
    """
    return badge_html


def render_verification_details(result: VerificationResult) -> str:
    """Return HTML for detailed verification breakdown."""
    rows = ""
    for claim in (result.unsupported_claims or [])[:10]:
        icon = "❌" if not claim.supported else "⚠️"
        rows += f"""
        <tr>
            <td style="padding:6px;font-size:12px;">{icon}</td>
            <td style="padding:6px;font-size:12px;">{claim.claim[:120]}</td>
            <td style="padding:6px;font-size:12px;color:#666;">{claim.note or claim.evidence_ref}</td>
        </tr>"""

    if not rows:
        return "<p style='color:#28a745;font-size:13px;'>✅ All claims are supported by evidence.</p>"

    return f"""
    <table style="width:100%;border-collapse:collapse;margin-top:8px;">
        <thead>
            <tr style="border-bottom:1px solid #ddd;">
                <th style="padding:6px;font-size:11px;text-align:left;width:30px;"></th>
                <th style="padding:6px;font-size:11px;text-align:left;">Claim</th>
                <th style="padding:6px;font-size:11px;text-align:left;">Issue</th>
            </tr>
        </thead>
        <tbody>{rows}</tbody>
    </table>
    """
