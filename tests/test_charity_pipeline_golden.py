"""
tests/test_charity_pipeline_golden.py — Golden-set integration tests.

Runs the full charity pipeline (data-gathering layer only — no LLM,
no verification, no DB log) against a fixed set of UK charities and
asserts on the structured outputs. Locks in the contract that future
code changes must not silently regress.

The LLM is skipped to keep tests fast and free; the LLM narrative is
not the contract — the *data feeding* into it is.

Run:
    python3 -m pytest tests/test_charity_pipeline_golden.py -v -m integration

Cost: free (data-gathering only). Wall time: ~30s for first run (downloads
OFSI + OFAC), ~10s per charity afterward.

Adding a new golden charity: append to GOLDEN_CHARITIES below with a
dict of expected invariants. Keep the asserts lenient — assert on
*shape* and *invariants*, not exact numbers, so legitimate changes in
upstream data don't break the suite.
"""

from __future__ import annotations

from typing import Any

import pytest

from reports import generate_charity_report


# ─── Golden set ────────────────────────────────────────────────────────────

# Three charities verified end-to-end on 2026-05-09:
#  - 220949   British Red Cross — large, complex, international, clean
#  - 700859   Acorns Children's Hospice — mid-sized regional, clean
#  - 1191591  St Austell Revival Together (START) — small local, sparse data
#
# Add more rows as we onboard a "dodgy" charity (CC inquiry) and one with
# foreign UBO complexity. Asserts should describe the *shape* of expected
# outputs, not exact figures.

GOLDEN_CHARITIES: list[dict[str, Any]] = [
    {
        "id": "220949",
        "label": "British Red Cross — large international clean",
        "expected_name_substring": "RED CROSS",
        "min_trustees": 5,
        "max_risk_score": 50,  # any score is acceptable but not catastrophic
        "expect_sanctions_high_confidence": False,
    },
    {
        "id": "700859",
        "label": "Acorns Children's Hospice — mid regional clean",
        "expected_name_substring": "ACORNS",
        "min_trustees": 3,
        "max_risk_score": 50,
        "expect_sanctions_high_confidence": False,
    },
    {
        "id": "1191591",
        "label": "St Austell Revival Together — small local",
        "expected_name_substring": "ST AUSTELL",
        "min_trustees": 1,
        "max_risk_score": 60,  # smaller charities often score higher (less data)
        "expect_sanctions_high_confidence": False,
    },
]


# ─── Module-scoped fixture: run each charity once, share the bundle ───────


@pytest.fixture(scope="module", params=GOLDEN_CHARITIES, ids=lambda c: c["id"])
def bundle(request):
    """Run the pipeline once per charity, no LLM, share across tests."""
    case = request.param
    bundle = generate_charity_report(
        case["id"],
        skip_llm=True,
        skip_verification=True,
        skip_structured_parsing=True,
        skip_db_log=True,
    )
    bundle._case = case  # type: ignore[attr-defined]  # stash for assertions
    return bundle


# ─── Asserts on the gathered data ─────────────────────────────────────────


@pytest.mark.integration
class TestCharityPipelineGolden:

    def test_no_pipeline_errors(self, bundle):
        case = bundle._case
        assert bundle.errors == [], (
            f"{case['label']} produced pipeline errors: {bundle.errors}"
        )

    def test_entity_name_resolved(self, bundle):
        case = bundle._case
        name = bundle.entity_name.upper()
        assert case["expected_name_substring"] in name, (
            f"Expected {case['expected_name_substring']!r} in {name!r}"
        )

    def test_charity_data_populated(self, bundle):
        cd = bundle.state.get("charity_data") or {}
        assert cd, "charity_data should be a non-empty dict"
        assert cd.get("charity_name"), "charity_name field is required"

    def test_trustees_listed(self, bundle):
        case = bundle._case
        trustees = bundle.state.get("trustees", [])
        assert isinstance(trustees, list)
        assert len(trustees) >= case["min_trustees"], (
            f"Expected >= {case['min_trustees']} trustees, got {len(trustees)}"
        )
        assert all(isinstance(t, str) and t.strip() for t in trustees), (
            "Every trustee should be a non-empty string"
        )

    def test_risk_score_in_range(self, bundle):
        case = bundle._case
        rs = bundle.risk_score
        assert rs, "risk_score should be populated"
        score = rs.get("overall_score")
        assert score is not None, "overall_score is required"
        assert 0 <= score <= 100, f"Score {score} out of valid range"
        assert score <= case["max_risk_score"], (
            f"{case['label']} risk score {score} unexpectedly high"
        )

    def test_sanctions_screening_ran(self, bundle):
        ss = bundle.state.get("sanctions_screening")
        assert ss is not None, (
            "sanctions_screening node must populate state['sanctions_screening']"
        )
        assert "providers" in ss
        assert ss["providers"], "At least one sanctions provider must be active"
        assert "OFSI" in ss["providers"]
        # OFAC should also be active by default
        assert "OFAC" in ss["providers"]
        # UN added in third sanctions iteration
        assert "UN" in ss["providers"]

    def test_no_high_confidence_sanctions_for_clean_charities(self, bundle):
        case = bundle._case
        ss = bundle.state["sanctions_screening"]
        assert ss["any_high_confidence"] == case["expect_sanctions_high_confidence"], (
            f"{case['label']} sanctions any_high_confidence "
            f"= {ss['any_high_confidence']}, expected "
            f"{case['expect_sanctions_high_confidence']}"
        )

    def test_llm_prompt_built(self, bundle):
        # Even with skip_llm, the prompt should be assembled
        assert bundle.llm_prompt, "llm_prompt should be populated for inspection"
        assert len(bundle.llm_prompt) > 5000, (
            "Prompt looks suspiciously short — should be 30KB+"
        )
        # The OFSI section must be present in every report
        assert "Sanctions List Screening" in bundle.llm_prompt, (
            "Section 6B (Sanctions List Screening) must be in the prompt"
        )

    def test_pipeline_stage_timings_recorded(self, bundle):
        timings = bundle.timings
        assert timings, "stage_timings should be recorded"
        # All 7 stages should have a timing entry
        for stage in (
            "fetch_registry",
            "extract_documents",
            "web_intelligence",
            "analysis_engines",
            "screen_sanctions",
        ):
            assert stage in timings, f"Missing timing for stage {stage!r}"


# ─── Hardening: pipeline must survive registry-fetch failure ──────────────


@pytest.mark.integration
def test_pipeline_survives_invalid_charity_number():
    """A nonexistent charity number must not crash the pipeline.

    Before the hardening pass, downstream nodes would NoneType-crash
    because state['charity_data'] stayed at its None default. After
    hardening, every node reads state with None-safe coercion and the
    analysis_engines stage bails early with a warning when registry
    data is missing.
    """
    from reports import generate_charity_report

    bundle = generate_charity_report(
        "9999999",  # Invalid — should fail registry fetch
        skip_llm=True,
        skip_verification=True,
        skip_structured_parsing=True,
        skip_db_log=True,
    )

    # The pipeline should complete every stage without raising
    timings = bundle.timings
    assert "screen_sanctions" in timings, (
        "Sanctions stage must run even if registry failed"
    )
    # Note: stage_timings keys are not the node names — they're set
    # internally by each node. compute_risk_score writes "risk_scoring";
    # generate_llm_report writes "generate_report".
    assert "risk_scoring" in timings, (
        "Risk scoring must run (even if it produces empty score)"
    )
    assert "generate_report" in timings, (
        "Prompt builder must run"
    )

    # We expect errors (registry fetch failed) but no Python crashes
    assert bundle.errors, "Should have recorded the registry-fetch error"

    # The risk_score may be empty {} but should not be None
    assert bundle.risk_score is not None

    # sanctions_screening should still have run with empty inputs
    ss = bundle.state.get("sanctions_screening")
    assert ss is not None
    assert ss["entity"] == []
    assert ss["trustees"] == {}
