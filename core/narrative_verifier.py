"""
core/narrative_verifier.py — Programmatic claim verification for LLM outputs.

The existing ``core/self_verification.py`` asks the LLM to audit its own
report. That's useful but biased — the same model that generated the
text is unlikely to find its own hallucinations.

This module adds a **deterministic** verification layer that runs purely
in Python: it scans the narrative for specific factual claims and
cross-checks them against the structured state. No additional LLM call,
no cost, no latency to speak of.

What it catches
---------------
- Trustee names mentioned in the narrative but not in ``state['trustees']``
- "Confirmed sanctions" / "OFSI match" wording when no high-confidence
  hit exists in ``state['sanctions_screening']``
- Adverse media claims like "verified adverse hits" when no
  ``verified_adverse: true`` entries exist
- "Charity is in administration" / "removed from the register" claims
  when the registry data says otherwise

What it doesn't catch
---------------------
- Subjective interpretations (e.g. "good governance framework")
- Subtle paraphrasing where the narrative restates true facts
- Claims about content the verifier doesn't have a rule for

Treat the output as a *signal* — moderate or critical concerns warrant
analyst review; low-confidence concerns are informational.

Public API
----------
- verify_narrative(narrative, state) -> NarrativeVerifierResult
- NarrativeVerifierResult — list of issues + summary
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any

log = logging.getLogger("hrcob.core.narrative_verifier")


# ─── Result types ──────────────────────────────────────────────────────────

@dataclass
class NarrativeIssue:
    """One specific concern raised by a programmatic check."""

    severity: str  # "critical" | "warning" | "info"
    rule: str  # name of the rule that fired
    excerpt: str  # snippet from the narrative that triggered the rule
    detail: str  # explanation of why this is a concern

    def to_dict(self) -> dict:
        return {
            "severity": self.severity,
            "rule": self.rule,
            "excerpt": self.excerpt[:300],
            "detail": self.detail,
        }


@dataclass
class NarrativeVerifierResult:
    """Output of running every programmatic check against a narrative."""

    issues: list[NarrativeIssue] = field(default_factory=list)
    rules_run: list[str] = field(default_factory=list)
    narrative_chars: int = 0

    @property
    def critical_count(self) -> int:
        return sum(1 for i in self.issues if i.severity == "critical")

    @property
    def warning_count(self) -> int:
        return sum(1 for i in self.issues if i.severity == "warning")

    @property
    def is_clean(self) -> bool:
        return self.critical_count == 0 and self.warning_count == 0

    def to_dict(self) -> dict:
        return {
            "issues": [i.to_dict() for i in self.issues],
            "rules_run": list(self.rules_run),
            "narrative_chars": self.narrative_chars,
            "critical_count": self.critical_count,
            "warning_count": self.warning_count,
            "is_clean": self.is_clean,
        }


# ─── Helpers ───────────────────────────────────────────────────────────────

def _excerpt_around(text: str, position: int, radius: int = 120) -> str:
    """Return a small excerpt of text around the given position."""
    start = max(0, position - radius)
    end = min(len(text), position + radius)
    snippet = text[start:end].replace("\n", " ")
    return f"…{snippet}…" if start > 0 or end < len(text) else snippet


def _normalise_name(name: str) -> str:
    return " ".join(name.lower().strip().split())


# ─── Individual rules ──────────────────────────────────────────────────────


def _rule_sanctions_claim_without_hit(
    narrative: str, state: dict
) -> list[NarrativeIssue]:
    """If the report claims a confirmed sanctions match, the screening must have
    flagged a high-confidence hit. Otherwise the claim is hallucinated.
    """
    issues: list[NarrativeIssue] = []
    ss = state.get("sanctions_screening") or {}
    has_high = bool(ss.get("any_high_confidence"))

    # Phrases that imply a confirmed match (case-insensitive)
    confirmed_patterns = [
        r"\bconfirmed sanctions exposure\b",
        r"\bsanctions match (?:identified|confirmed)\b",
        r"\bdirect sanctions exposure\b",
        r"\bcharity is on the OFSI list\b",
        r"\btrustee is sanctioned\b",
        r"\bsanctioned individual\b",
    ]

    if has_high:
        return issues  # claim is justified

    # Negation guard — phrases like "no confirmed sanctions exposure" or
    # "absence of … sanctions exposure" should NOT fire. We check the
    # ~40 chars preceding the match for a negation token.
    neg_re = re.compile(
        r"\b(?:no|not|without|absence\s+of|no\s+evidence\s+of|free\s+of|"
        r"clear\s+of|nor)\b[^.]{0,40}$",
        re.IGNORECASE,
    )

    for pat in confirmed_patterns:
        for m in re.finditer(pat, narrative, re.IGNORECASE):
            preceding = narrative[max(0, m.start() - 60):m.start()]
            if neg_re.search(preceding):
                continue  # negated claim, not a hallucination
            issues.append(NarrativeIssue(
                severity="critical",
                rule="sanctions_claim_without_high_confidence_hit",
                excerpt=_excerpt_around(narrative, m.start()),
                detail=(
                    "Narrative implies a confirmed sanctions match but "
                    "sanctions_screening.any_high_confidence is False. The "
                    "LLM may have escalated a 'possible' partial match."
                ),
            ))
    return issues


def _rule_trustee_not_in_list(
    narrative: str, state: dict
) -> list[NarrativeIssue]:
    """Catch trustee names mentioned in the report that aren't in the
    state-supplied trustees list.

    Conservative implementation: only flag fully-capitalised "First Last" or
    "First Middle Last" tokens that appear in the trustees-table section
    but don't match any normalised state['trustees'] entry. Anything more
    aggressive would flag too many real names mentioned for context (e.g.
    historical figures, partner orgs).
    """
    issues: list[NarrativeIssue] = []
    raw_trustees = state.get("trustees") or []
    if not raw_trustees:
        return issues

    known = {_normalise_name(t) for t in raw_trustees if isinstance(t, str)}

    # Scope to JUST the "Trustees Table" subsection — Section 4 as a whole
    # contains the financial table, where column headers like
    # "Income Volatility" or "Charitable Activities" would false-positive
    # against this regex. If we can't find the table heading, skip.
    m = re.search(
        r"###?\s*[Tt]rustees?\s*(?:Table|List)?",
        narrative,
    )
    if m is None:
        return issues
    section_start = m.start()
    # End at the next ## or ### subheading (whichever comes first)
    after = narrative[m.end():]
    end_match = re.search(r"\n##+\s", after)
    section_end = m.end() + end_match.start() if end_match else len(narrative)
    section = narrative[section_start:section_end]

    # Names: "Title-cased First Last" sequences (very simple heuristic)
    # We only check the trustee-table-ish lines. This will not catch all
    # trustee names but its false-positive rate is low.
    name_pattern = re.compile(
        r"(?:\|\s*)?([A-Z][a-zà-ÿ]+(?:\s+[A-Z][a-zà-ÿ]+){1,3})\s*\|"
    )
    for m in name_pattern.finditer(section):
        candidate = m.group(1).strip()
        # Skip common non-name tokens that pass this regex
        # (column headers, table-cell labels, role names that are Title-Cased)
        if candidate.lower() in {
            "registered office", "charity name", "trustees table",
            "charity number", "company number",
            "trustee name", "other active", "active directorships",
            "notable entities", "no notable", "none reported",
            "not specified", "not available",
        }:
            continue
        norm_c = _normalise_name(candidate)
        if any(norm_c in k or k in norm_c for k in known):
            continue
        # Heuristic: only flag if the candidate looks like a person name
        # (≥2 words, each Title-cased, total length 6-50 chars)
        if 6 <= len(candidate) <= 50 and " " in candidate:
            issues.append(NarrativeIssue(
                severity="warning",
                rule="trustee_name_not_in_state",
                excerpt=_excerpt_around(section, m.start()),
                detail=(
                    f"The narrative names {candidate!r} in the trustees "
                    f"section but this name does not appear in "
                    f"state['trustees']. Possible hallucination or "
                    f"out-of-date trustee mention."
                ),
            ))
    return issues


def _rule_in_administration_claim(
    narrative: str, state: dict
) -> list[NarrativeIssue]:
    """If the narrative says the charity is in administration / has been
    removed, the registry data must back this up.
    """
    issues: list[NarrativeIssue] = []
    cd = state.get("charity_data") or {}
    in_admin = bool(cd.get("in_administration"))
    removed = bool(cd.get("date_of_removal"))

    if not in_admin:
        for m in re.finditer(r"\bin administration\b", narrative, re.IGNORECASE):
            issues.append(NarrativeIssue(
                severity="critical",
                rule="claims_in_administration_not_in_data",
                excerpt=_excerpt_around(narrative, m.start()),
                detail=(
                    "Narrative claims the charity is 'in administration' "
                    "but charity_data.in_administration is False."
                ),
            ))

    if not removed:
        for m in re.finditer(
            r"\b(?:removed|de-?registered|struck off)\s+from\s+the\s+register\b",
            narrative,
            re.IGNORECASE,
        ):
            issues.append(NarrativeIssue(
                severity="critical",
                rule="claims_removal_not_in_data",
                excerpt=_excerpt_around(narrative, m.start()),
                detail=(
                    "Narrative claims the charity has been removed from "
                    "the register but charity_data.date_of_removal is empty."
                ),
            ))
    return issues


def _rule_verified_adverse_count_consistency(
    narrative: str, state: dict
) -> list[NarrativeIssue]:
    """If the report quotes "N verified adverse media findings" the count
    should match the state data. Catches inflated adverse-media counts.
    """
    issues: list[NarrativeIssue] = []
    adverse_org = state.get("adverse_org") or []
    verified = sum(
        1 for r in adverse_org
        if isinstance(r, dict) and r.get("verified_adverse") is True
    )
    # Look for explicit count claims — any number near "verified adverse"
    pat = re.compile(
        r"\b(\d+)\s+verified\s+adverse\b",
        re.IGNORECASE,
    )
    for m in pat.finditer(narrative):
        claimed = int(m.group(1))
        if claimed > verified:
            issues.append(NarrativeIssue(
                severity="warning",
                rule="verified_adverse_count_inflated",
                excerpt=_excerpt_around(narrative, m.start()),
                detail=(
                    f"Narrative claims {claimed} verified adverse media "
                    f"hits but state has only {verified} verified entries."
                ),
            ))
    return issues


_RULES = {
    "sanctions_claim_without_hit": _rule_sanctions_claim_without_hit,
    "trustee_not_in_list": _rule_trustee_not_in_list,
    "in_administration_claim": _rule_in_administration_claim,
    "verified_adverse_count": _rule_verified_adverse_count_consistency,
}


# ─── Top-level entry point ────────────────────────────────────────────────


def verify_narrative(
    narrative: str,
    state: dict[str, Any],
) -> NarrativeVerifierResult:
    """Run every programmatic check against the narrative + state.

    Cheap (~milliseconds), deterministic, no LLM call. Use the result
    alongside the LLM's own self-verification — they catch different
    failure modes.
    """
    result = NarrativeVerifierResult(narrative_chars=len(narrative or ""))
    if not narrative:
        return result

    for name, rule in _RULES.items():
        result.rules_run.append(name)
        try:
            for issue in rule(narrative, state):
                result.issues.append(issue)
        except Exception as e:
            log.warning(f"Narrative verifier rule {name!r} raised: {e}")
            result.issues.append(NarrativeIssue(
                severity="info",
                rule=name,
                excerpt="",
                detail=f"Rule {name} crashed: {e}",
            ))

    return result
