"""Risk-band classification — boundary contract tests.

Mirrors :class:`com.mirador.ml.RiskBandTest` from the Java sibling
exactly. Same boundary semantics ensure the UI sees the same band
regardless of which backend served the prediction (the
"interchangeable backends" contract from common ADR-0008).
"""

from __future__ import annotations

import pytest

from iris_service.ml.risk_band import RiskBand, classify_risk


class TestClassifyAtLowBoundary:
    """``probability ≤ low_threshold`` → LOW (≤ inclusive)."""

    def test_zero_is_low(self) -> None:
        assert classify_risk(0.0) == RiskBand.LOW

    def test_low_threshold_is_low(self) -> None:
        # Exactly 0.30 must classify as LOW — same as Java's
        # boundary semantics. Inverting this would silently shift
        # the band cut-off and break the parity contract.
        assert classify_risk(0.30) == RiskBand.LOW


class TestClassifyAboveLowBoundary:
    """``low < p ≤ high`` → MEDIUM."""

    def test_just_above_low_is_medium(self) -> None:
        assert classify_risk(0.31) == RiskBand.MEDIUM

    def test_midpoint_is_medium(self) -> None:
        assert classify_risk(0.50) == RiskBand.MEDIUM

    def test_high_threshold_is_medium(self) -> None:
        # Exactly 0.70 must classify as MEDIUM — the high boundary
        # is ≤ inclusive on the medium side, > exclusive on high.
        assert classify_risk(0.70) == RiskBand.MEDIUM


class TestClassifyAboveHighBoundary:
    """``probability > high_threshold`` → HIGH."""

    def test_just_above_high_is_high(self) -> None:
        assert classify_risk(0.71) == RiskBand.HIGH

    def test_high_value_is_high(self) -> None:
        assert classify_risk(0.99) == RiskBand.HIGH

    def test_max_is_high(self) -> None:
        assert classify_risk(1.0) == RiskBand.HIGH


class TestCustomThresholds:
    """Tighter / looser thresholds for ops re-tuning (Phase E)."""

    def test_tight_window(self) -> None:
        # 0.5 / 0.6 — common pattern for stricter triggers.
        assert classify_risk(0.50, low_threshold=0.5, high_threshold=0.6) == RiskBand.LOW
        assert classify_risk(0.55, low_threshold=0.5, high_threshold=0.6) == RiskBand.MEDIUM
        assert classify_risk(0.65, low_threshold=0.5, high_threshold=0.6) == RiskBand.HIGH


class TestInvertedThresholds:
    """Inverted thresholds (low > high) must fail loudly."""

    def test_inverted_raises(self) -> None:
        with pytest.raises(ValueError, match="must be ≤"):
            classify_risk(0.5, low_threshold=0.7, high_threshold=0.3)
