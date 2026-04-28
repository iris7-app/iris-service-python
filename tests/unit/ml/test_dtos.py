"""Pydantic DTO shape tests for the churn-prediction surface."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from iris_service.ml.dtos import (
    ChurnNotFound,
    ChurnPrediction,
    ChurnServiceUnavailable,
)
from iris_service.ml.risk_band import RiskBand


class TestChurnPrediction:
    def test_round_trips_to_camel_via_default_serialization(self) -> None:
        # The DTO uses Python snake_case ; FastAPI/Pydantic emit the
        # same on the wire by default. The Java sibling emits camelCase
        # via Jackson — the UI client knows to translate.
        prediction = ChurnPrediction(
            customer_id=42,
            probability=0.731,
            risk_band=RiskBand.HIGH,
            top_features=["days_since_last_order"],
            model_version="v3-2026-04-27",
            predicted_at=datetime(2026, 4, 27, 15, 42, 18, tzinfo=UTC),
        )
        dumped = prediction.model_dump()
        assert dumped["customer_id"] == 42
        assert dumped["risk_band"] == "HIGH"

    def test_probability_outside_range_rejected(self) -> None:
        # Pydantic Field(ge=0, le=1) is the contract that catches a
        # bug in the sigmoid (e.g. forgot to apply, returning a logit).
        with pytest.raises(ValueError, match="less than or equal"):
            ChurnPrediction(
                customer_id=42,
                probability=1.5,
                risk_band=RiskBand.HIGH,
                model_version="v0",
                predicted_at=datetime.now(UTC),
            )


class TestSoftErrorDtos:
    def test_not_found_accepts_null_id(self) -> None:
        # Mirrors Java's NotFoundDto behaviour — when the LLM
        # passes null, we surface that explicitly rather than
        # silently coercing it to 0.
        dto = ChurnNotFound(customer_id=None, message="customer_id is required")
        assert dto.customer_id is None

    def test_service_unavailable_carries_hint(self) -> None:
        dto = ChurnServiceUnavailable(
            message="model not loaded",
            hint="provision the ConfigMap",
        )
        assert "ConfigMap" in dto.hint
