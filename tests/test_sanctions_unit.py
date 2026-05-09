"""
tests/test_sanctions_unit.py — Unit tests for the sanctions matching layer.

These tests need the OFSI / OFAC CSV files cached locally (which happens
automatically on first call), but no live API access beyond that initial
download. Once cached, subsequent runs are offline.

Validates the matching contract that the rest of the pipeline depends on:
    - Known-sanctioned names produce high-confidence hits
    - Clean names produce no hits
    - Common-name false-positive guard kills bogus single-word alias matches
    - Each provider attaches its own source label and citation
    - The two providers complement each other (OFSI for UK, OFAC for US)

Run only these:
    python3 -m pytest tests/test_sanctions_unit.py -v
"""

from __future__ import annotations

import pytest

from core.sanctions import (
    OfacProvider,
    OfsiProvider,
    SanctionsHit,
    UnProvider,
    default_providers,
    screen_against_providers,
)


# ─── Module-scoped fixtures so the 16 MB OFSI + 5.5 MB OFAC files are
# loaded once per test session, not per test. ───────────────────────────

@pytest.fixture(scope="module")
def ofsi():
    p = OfsiProvider()
    p._ensure_loaded()
    return p


@pytest.fixture(scope="module")
def ofac():
    p = OfacProvider()
    p._ensure_loaded()
    return p


@pytest.fixture(scope="module")
def un():
    p = UnProvider()
    p._ensure_loaded()
    return p


# ─── OFSI matching ────────────────────────────────────────────────────────


@pytest.mark.integration
class TestOfsiMatching:
    """Asserts OFSI returns sane results for known cases."""

    def test_putin_high_confidence(self, ofsi):
        hits = ofsi.screen("Vladimir Putin", schema="person")
        assert hits, "Putin should hit OFSI"
        top = hits[0]
        assert top.confidence == "high"
        assert top.score >= 95
        assert top.source == "OFSI"
        assert "Russia" in top.regime

    def test_wagner_group_aliases_resolve(self, ofsi):
        hits = ofsi.screen("Wagner Group", schema="entity")
        assert hits, "Wagner Group should hit OFSI"
        # Wagner Group matches both Africa Corps (Libya) and CHVK Vagner (Russia)
        primaries = {h.primary_name for h in hits}
        assert any(p for p in primaries), "should have at least one primary"

    def test_clean_org_no_hit(self, ofsi):
        hits = ofsi.screen("British Red Cross Society", schema="entity")
        assert hits == [], "Clean charity should produce zero OFSI hits"

    def test_clean_person_no_hit(self, ofsi):
        # Boris Johnson is a public figure but not on OFSI
        hits = ofsi.screen("Boris Johnson", schema="person")
        assert hits == [], "Boris Johnson should not hit OFSI"

    def test_common_name_guard_blocks_partial_alias(self, ofsi):
        """A multi-word query must NOT match a single-word family-name alias.

        This is the false-positive guard that fired during BRC verification
        when OFSI happened to have a row aliased simply as 'AHMED'.
        """
        hits = ofsi.screen("Yaseer Ahmed", schema="person")
        # No high-confidence hits — only common-name partials, if any
        highs = [h for h in hits if h.confidence == "high"]
        assert highs == [], (
            "Yaseer Ahmed must not produce high-confidence OFSI hits via "
            "single-word family-name aliases"
        )

    def test_short_single_word_returns_no_hits(self, ofsi):
        hits = ofsi.screen("Smith", schema="person")
        # Plain "Smith" is too generic to match — neither OFSI list has
        # a single-token "SMITH" record we'd want to surface
        assert hits == [], "Single common surname should not produce hits"

    def test_citation_format(self, ofsi):
        hits = ofsi.screen("Vladimir Putin", schema="person")
        top = hits[0]
        assert "OFSI Consolidated List" in top.citation
        assert top.source_id, "Citation should include OFSI Group ID"


# ─── OFAC matching ────────────────────────────────────────────────────────


@pytest.mark.integration
class TestOfacMatching:
    """OFAC has different coverage from OFSI — Cuba, OFAC-specific programs."""

    def test_aerocaribbean_hits_ofac(self, ofac):
        # Aerocaribbean is on OFAC (Cuba program) but not on OFSI
        hits = ofac.screen("Aerocaribbean", schema="entity")
        assert hits, "Aerocaribbean should hit OFAC (Cuba program)"
        assert hits[0].source == "OFAC"
        assert "CUBA" in hits[0].regime.upper()

    def test_putin_hits_ofac(self, ofac):
        hits = ofac.screen("Vladimir Putin", schema="person")
        assert hits, "Putin should also hit OFAC"
        # OFAC's score may be ~96 (different name format) — accept any high
        highs = [h for h in hits if h.confidence == "high"]
        assert highs, "Putin should have at least one high-confidence OFAC hit"
        assert "RUSSIA" in highs[0].regime.upper()

    def test_clean_org_no_hit(self, ofac):
        hits = ofac.screen("British Red Cross Society", schema="entity")
        assert hits == [], "Clean charity should produce zero OFAC hits"


# ─── UN matching ─────────────────────────────────────────────────────────


@pytest.mark.integration
class TestUnMatching:
    """UN Security Council Consolidated List."""

    def test_clean_org_no_hit(self, un):
        hits = un.screen("British Red Cross Society", schema="entity")
        assert hits == [], "Clean charity should produce zero UN hits"

    def test_un_listed_isil_alqaida_finds_someone(self, un):
        # Most ISIL/Al-Qaida UN listees are also on OFSI/OFAC. Pick any
        # that's almost certainly there: search for "Al-Qaida" itself
        # (the entity).
        hits = un.screen("Al-Qaida", schema="entity")
        # At least one UN entity should match; don't pin the exact one.
        assert any(
            "qaida" in h.matched_alias.lower() or "qaeda" in h.matched_alias.lower()
            for h in hits
        ), "UN should list Al-Qaida-related entities"

    def test_citation_includes_un_reference(self, un):
        hits = un.screen("Al-Qaida", schema="entity")
        if hits:
            cite = hits[0].citation
            assert "UN Security Council" in cite
            assert hits[0].source == "UN"


# ─── Multi-provider screening ─────────────────────────────────────────────


@pytest.mark.integration
class TestMultiProviderScreening:
    """The aggregating screen_against_providers should merge results."""

    def test_putin_appears_on_both_lists(self):
        hits = screen_against_providers("Vladimir Putin", schema="person")
        sources = {h.source for h in hits}
        assert "OFSI" in sources
        assert "OFAC" in sources

    def test_aerocaribbean_only_ofac(self):
        hits = screen_against_providers("Aerocaribbean", schema="entity")
        sources = {h.source for h in hits}
        assert "OFAC" in sources
        # OFSI should NOT have it (UK doesn't sanction Cuba)
        assert "OFSI" not in sources

    def test_results_sorted_by_score(self):
        hits = screen_against_providers("Vladimir Putin", schema="person")
        scores = [h.score for h in hits]
        assert scores == sorted(scores, reverse=True), \
            "Hits should be sorted by score descending"

    def test_clean_org_zero_hits(self):
        hits = screen_against_providers("British Red Cross Society", schema="entity")
        assert hits == [], "Clean entity should produce zero hits across all providers"

    def test_default_providers_is_singleton(self):
        # Ensure providers are cached so the heavy CSV files aren't re-parsed
        a = default_providers()
        b = default_providers()
        assert a is b, "default_providers() must return cached singletons"

    def test_default_providers_includes_un(self):
        provs = default_providers()
        names = [p.name for p in provs]
        assert "UN" in names, f"UN missing from default providers: {names}"


# ─── SanctionsHit shape ───────────────────────────────────────────────────


def test_hit_to_dict_serializable_keys():
    """Bundle.to_dict() output must contain every field required by the prompt."""
    h = SanctionsHit(
        queried_name="Test Name",
        matched_alias="Test Alias",
        primary_name="Test Primary",
        score=92.0,
        confidence="high",
        source="OFSI",
        source_id="X1",
        schema="person",
        regime="Russia",
    )
    d = h.to_dict()
    required = {
        "queried_name", "matched_alias", "primary_name", "score",
        "confidence", "source", "source_id", "schema", "regime",
        "listed_on", "country", "nationality", "dob",
        "statement_of_reasons", "citation",
    }
    assert required.issubset(d.keys()), \
        f"Missing keys from to_dict(): {required - d.keys()}"
