"""
tests/test_narrative_verifier.py — Unit tests for the programmatic
narrative verifier.

These exercise specific hallucination patterns the verifier is designed
to catch. No I/O, no network — pure-Python rule logic.
"""

from __future__ import annotations

import pytest

from core.narrative_verifier import (
    NarrativeIssue,
    NarrativeVerifierResult,
    verify_narrative,
)


def _state(**overrides) -> dict:
    base = {
        "charity_data": {
            "in_administration": False,
            "date_of_removal": "",
        },
        "trustees": ["Alice Test", "Bob Sample"],
        "sanctions_screening": {
            "any_high_confidence": False,
            "entity": [],
            "trustees": {},
        },
        "adverse_org": [],
    }
    base.update(overrides)
    return base


# ─── Sanctions claim guard ────────────────────────────────────────────────


class TestSanctionsClaimGuard:

    def test_clean_narrative_passes(self):
        narrative = (
            "## 6B. Sanctions List Screening\n\n"
            "No matches against any of the lists checked."
        )
        result = verify_narrative(narrative, _state())
        sanctions_issues = [
            i for i in result.issues
            if i.rule == "sanctions_claim_without_high_confidence_hit"
        ]
        assert sanctions_issues == []

    def test_confirmed_sanctions_without_hit_fires(self):
        narrative = "Confirmed sanctions exposure identified for the charity."
        result = verify_narrative(narrative, _state())
        sanctions_issues = [
            i for i in result.issues
            if i.rule == "sanctions_claim_without_high_confidence_hit"
        ]
        assert sanctions_issues, "Should flag confirmed-sanctions claim with no hit"
        assert sanctions_issues[0].severity == "critical"

    def test_confirmed_sanctions_with_real_hit_passes(self):
        narrative = "Confirmed sanctions exposure identified for the charity."
        s = _state()
        s["sanctions_screening"]["any_high_confidence"] = True
        result = verify_narrative(narrative, s)
        sanctions_issues = [
            i for i in result.issues
            if i.rule == "sanctions_claim_without_high_confidence_hit"
        ]
        assert sanctions_issues == [], (
            "Real high-confidence hit should justify the claim"
        )


# ─── Administration / removal claim guard ────────────────────────────────


class TestAdministrationClaim:

    def test_clean_narrative_passes(self):
        narrative = "The charity continues normal operations."
        result = verify_narrative(narrative, _state())
        admin_issues = [
            i for i in result.issues
            if "administration" in i.rule
        ]
        assert admin_issues == []

    def test_in_administration_fires_when_data_says_no(self):
        narrative = (
            "The charity is currently in administration following "
            "financial difficulties."
        )
        result = verify_narrative(narrative, _state())
        issues = [i for i in result.issues if i.rule == "claims_in_administration_not_in_data"]
        assert issues, "Should flag in-administration claim without data backing"

    def test_in_administration_passes_when_data_agrees(self):
        narrative = "The charity is in administration."
        s = _state()
        s["charity_data"]["in_administration"] = True
        result = verify_narrative(narrative, s)
        issues = [i for i in result.issues if i.rule == "claims_in_administration_not_in_data"]
        assert issues == []


# ─── Verified adverse media count guard ──────────────────────────────────


class TestAdverseMediaCount:

    def test_inflated_count_fires(self):
        narrative = "The charity has 5 verified adverse media findings."
        s = _state(adverse_org=[
            {"verified_adverse": True, "title": "x"},
            {"verified_adverse": False, "title": "y"},
        ])
        result = verify_narrative(narrative, s)
        issues = [i for i in result.issues if i.rule == "verified_adverse_count_inflated"]
        assert issues, "5 claimed > 1 actual should fire"

    def test_correct_count_passes(self):
        narrative = "The charity has 1 verified adverse media finding."
        s = _state(adverse_org=[
            {"verified_adverse": True, "title": "x"},
        ])
        result = verify_narrative(narrative, s)
        issues = [i for i in result.issues if i.rule == "verified_adverse_count_inflated"]
        assert issues == []

    def test_zero_claim_with_zero_actual_passes(self):
        narrative = "No verified adverse media found."  # No number near "verified adverse"
        result = verify_narrative(narrative, _state())
        issues = [i for i in result.issues if i.rule == "verified_adverse_count_inflated"]
        assert issues == []


# ─── Result aggregation ──────────────────────────────────────────────────


class TestResultShape:

    def test_empty_narrative_no_issues(self):
        result = verify_narrative("", _state())
        assert result.issues == []
        assert result.is_clean is True

    def test_clean_result_serializable(self):
        result = verify_narrative("Charity operates normally.", _state())
        d = result.to_dict()
        assert "issues" in d
        assert "rules_run" in d
        assert "is_clean" in d
        assert d["is_clean"] is True

    def test_critical_count_correct(self):
        narrative = (
            "Confirmed sanctions exposure identified. "
            "The charity is in administration. "
            "Direct sanctions exposure noted."
        )
        result = verify_narrative(narrative, _state())
        assert result.critical_count >= 2
        assert result.is_clean is False
