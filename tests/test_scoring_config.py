"""
tests/test_scoring_config.py — Sanity tests for the scoring config.

These tests freeze the contract of the scoring config so accidental
edits to threshold values surface as test failures and prompt a
rationale-documentation update at the same time.

The tests are intentionally fast — they exercise pure-Python logic
with no I/O.
"""

from __future__ import annotations

import pytest

from core import scoring_config as sc


class TestRiskLevelBoundaries:

    def test_thresholds_are_monotonically_increasing(self):
        assert sc.MEDIUM_SCORE_THRESHOLD < sc.HIGH_SCORE_THRESHOLD
        assert sc.HIGH_SCORE_THRESHOLD < sc.CRITICAL_SCORE_THRESHOLD

    def test_thresholds_in_0_100_range(self):
        for v in (
            sc.MEDIUM_SCORE_THRESHOLD,
            sc.HIGH_SCORE_THRESHOLD,
            sc.CRITICAL_SCORE_THRESHOLD,
        ):
            assert 0 < v < 100, f"Threshold {v} outside 0–100"

    @pytest.mark.parametrize(
        "score,expected",
        [
            (0, "Low"),
            (10, "Low"),
            (19.99, "Low"),
            (20, "Medium"),
            (39.99, "Medium"),
            (40, "High"),
            (64.99, "High"),
            (65, "Critical"),
            (100, "Critical"),
        ],
    )
    def test_level_from_score(self, score, expected):
        assert sc.level_from_score(score) == expected


class TestRiskCategories:

    def test_six_top_level_categories(self):
        assert len(sc.RISK_CATEGORIES) == 6

    def test_categories_are_unique_strings(self):
        assert len(set(sc.RISK_CATEGORIES)) == len(sc.RISK_CATEGORIES)
        assert all(isinstance(c, str) and c for c in sc.RISK_CATEGORIES)

    def test_categories_match_scorer(self):
        # If the scorer expects different category names, that's a contract
        # break — it'd cause silent miscategorisation.
        from core.risk_scorer import score_charity  # noqa: F401
        # Just make sure categories include the key ones the prompt expects
        assert "Geography" in sc.RISK_CATEGORIES
        assert "Financial" in sc.RISK_CATEGORIES
        assert "Governance" in sc.RISK_CATEGORIES


class TestAnomalyThresholds:

    def test_yoy_threshold_is_proportional(self):
        # Must be expressed as a fraction (e.g. 0.30 = 30%), not a percent
        assert 0 < sc.ANOMALY_YOY_JUMP <= 1.0

    def test_thresholds_are_reasonable(self):
        # Common-sense bounds: too tight → noisy reports; too loose → misses
        assert 0.10 <= sc.ANOMALY_YOY_JUMP <= 0.50
        assert 0.10 <= sc.ANOMALY_VOLATILITY_CV <= 0.50
        assert 0.05 <= sc.ANOMALY_RATIO_SHIFT <= 0.30


class TestSeverityPoints:

    def test_severity_ordering(self):
        # Critical must score more than high, high more than medium, etc.
        s = sc.SEVERITY_POINTS
        assert s["critical"] > s["high"] > s["medium"] > s["low"] >= s["info"]

    def test_severity_points_helper(self):
        assert sc.severity_points("critical", 1.0) == sc.SEVERITY_POINTS["critical"]
        assert sc.severity_points("high", 0.5) == sc.SEVERITY_POINTS["high"] * 0.5
        assert sc.severity_points("unknown") == 0

    def test_severity_confidence_clamped(self):
        # Confidence outside [0, 1] should be clamped, not amplified
        assert sc.severity_points("high", 1.5) == sc.SEVERITY_POINTS["high"]
        assert sc.severity_points("high", -0.5) == 0


class TestHardStops:

    def test_hard_stops_present(self):
        assert sc.HARD_STOPS, "HARD_STOPS list must not be empty"
        assert any("sanctions" in h.lower() for h in sc.HARD_STOPS), (
            "Sanctions-match hard stop must be in the list"
        )
