"""Inference + feature-extraction tests — runtime side, no [ml] extra needed.

Mirrors :class:`org.iris.ml.ChurnPredictorTest` +
:class:`ChurnFeatureExtractorTest` from the Java sibling. The 8-feature
extractor MUST produce numerically-identical output to Java for the
same input — Phase G's cross-language smoke test asserts this end-to-end.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import numpy as np
import pytest

from iris_service.customer.models import Customer
from iris_service.ml.inference import (
    N_FEATURES,
    ChurnPredictor,
    extract_features,
)
from iris_service.order.models import Order, OrderStatus
from iris_service.order.order_line_models import OrderLine, OrderLineStatus

NOW = datetime(2026, 4, 27, tzinfo=UTC)


def _customer(
    *,
    cid: int = 1,
    email: str = "alice@gmail.com",
    created_at: datetime = NOW - timedelta(days=200),
) -> Customer:
    customer = Customer()
    customer.id = cid
    customer.name = f"customer-{cid}"
    customer.email = email
    customer.created_at = created_at
    return customer


def _order(
    *,
    oid: int = 100,
    customer_id: int = 1,
    created_at: datetime = NOW - timedelta(days=10),
    amount: str = "50.00",
) -> Order:
    order = Order()
    order.id = oid
    order.customer_id = customer_id
    order.created_at = created_at
    order.total_amount = Decimal(amount)
    order.status = OrderStatus.PENDING.value
    return order


def _line(*, lid: int = 1, order_id: int = 100, product_id: int = 1) -> OrderLine:
    line = OrderLine()
    line.id = lid
    line.order_id = order_id
    line.product_id = product_id
    line.quantity = 1
    line.unit_price_at_order = Decimal("10.00")
    line.status = OrderLineStatus.PENDING.value
    return line


class TestExtractFeatures:
    """Feature engineering parity with Java's ChurnFeatureExtractor."""

    def test_returns_exactly_eight_features(self) -> None:
        features = extract_features(
            _customer(),
            [_order()],
            [_line()],
            now=NOW,
        )
        assert features.shape == (N_FEATURES,)
        assert features.dtype == np.float32

    def test_revenue_windows_are_inclusive(self) -> None:
        # 3 orders : 10d, 60d, 200d back ; revenue 50 + 30 + 20.
        # 30d window = 50 ; 90d window = 80 ; 365d window = 100.
        recent = _order(oid=100, created_at=NOW - timedelta(days=10), amount="50.00")
        older = _order(oid=101, created_at=NOW - timedelta(days=60), amount="30.00")
        ancient = _order(oid=102, created_at=NOW - timedelta(days=200), amount="20.00")

        features = extract_features(
            _customer(created_at=NOW - timedelta(days=400)),
            [recent, older, ancient],
            [],
            now=NOW,
        )
        # Indices 1, 2, 3 = revenue 30/90/365.
        assert features[1] == pytest.approx(50.0, abs=1e-6)
        assert features[2] == pytest.approx(80.0, abs=1e-6)
        assert features[3] == pytest.approx(100.0, abs=1e-6)

    def test_no_orders_yields_zero_aggregates(self) -> None:
        features = extract_features(
            _customer(created_at=NOW - timedelta(days=50)),
            [],
            [],
            now=NOW,
        )
        # No NaN, sane defaults.
        assert all(np.isfinite(f) for f in features)
        assert features[1] == 0.0  # revenue_30d
        assert features[2] == 0.0  # revenue_90d
        assert features[3] == 0.0  # revenue_365d
        assert features[4] == 0.0  # order_frequency
        assert features[5] == 0.0  # cart_diversity

    def test_cart_diversity_distinct_over_total(self) -> None:
        # 3 lines, 2 distinct products → 2/3.
        order = _order(oid=100, created_at=NOW - timedelta(days=5), amount="10.00")
        lines = [
            _line(lid=1, product_id=10),
            _line(lid=2, product_id=11),
            _line(lid=3, product_id=10),
        ]
        features = extract_features(
            _customer(created_at=NOW - timedelta(days=100)),
            [order],
            lines,
            now=NOW,
        )
        assert features[5] == pytest.approx(2.0 / 3.0, abs=1e-6)

    def test_days_since_last_order_clips_to_zero(self) -> None:
        # Last order in the future (synthetic edge) → clip to 0.
        future = _order(oid=100, created_at=NOW + timedelta(days=5), amount="10.00")
        features = extract_features(
            _customer(created_at=NOW - timedelta(days=100)),
            [future],
            [],
            now=NOW,
        )
        assert features[0] == 0.0

    def test_email_domain_class_buckets(self) -> None:
        # Bucket 1 = mainstream (gmail). Bucket 2 = disposable. Bucket
        # 0 = corporate (anything not in the others). Bucket 3 = unknown.
        gmail = extract_features(_customer(email="a@gmail.com"), [], [], now=NOW)
        disp = extract_features(_customer(email="a@TEMPMAIL.com"), [], [], now=NOW)
        corp = extract_features(_customer(email="a@acme-corp.example"), [], [], now=NOW)
        unknown = extract_features(_customer(email="not-an-email"), [], [], now=NOW)

        assert gmail[6] == 1.0
        assert disp[6] == 2.0
        assert corp[6] == 0.0
        assert unknown[6] == 3.0


class TestChurnPredictorGracefulDegradation:
    """Boots without the model — endpoint serves 503 until provisioned."""

    def test_not_ready_when_file_missing(self) -> None:
        predictor = ChurnPredictor("/nonexistent/path/churn.onnx", "v0-test")
        predictor.load_model()
        assert predictor.is_ready() is False

    def test_predict_raises_when_not_loaded(self) -> None:
        predictor = ChurnPredictor("/nonexistent/path/churn.onnx", "v0-test")
        predictor.load_model()
        with pytest.raises(RuntimeError, match="not loaded"):
            predictor.predict_probability(np.zeros(N_FEATURES, dtype=np.float32))

    def test_model_version_label_exposed(self) -> None:
        predictor = ChurnPredictor("/nonexistent/path/churn.onnx", "v3-2026-04-27")
        assert predictor.model_version == "v3-2026-04-27"


class TestChurnPredictorWithStubSession:
    """Mock onnxruntime.InferenceSession to test the inference path.

    Uses monkeypatch to replace InferenceSession with a stub that
    returns a deterministic logit. Verifies the sigmoid wraps the
    raw logit correctly + the result is in [0, 1].
    """

    def test_predict_probability_with_stub_logit(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> None:
        # 1. Place a sentinel file at the model path so is_file() returns True.
        model_file = tmp_path / "stub.onnx"
        model_file.write_bytes(b"stub")  # not a real ONNX, never parsed by stub

        # 2. Stub InferenceSession — returns logit=0 → sigmoid=0.5.
        class _StubInputMeta:
            name = "input"

        class _StubSession:
            def __init__(self, *_: Any, **__: Any) -> None:
                pass

            def get_inputs(self) -> list[_StubInputMeta]:
                return [_StubInputMeta()]

            def run(self, _outputs: Any, _inputs: dict[str, Any]) -> list[np.ndarray]:
                return [np.array([[0.0]], dtype=np.float32)]

        monkeypatch.setattr("onnxruntime.InferenceSession", _StubSession)

        predictor = ChurnPredictor(str(model_file), "v-stub")
        predictor.load_model()
        assert predictor.is_ready() is True

        features = np.zeros(N_FEATURES, dtype=np.float32)
        probability = predictor.predict_probability(features)
        # logit=0 → sigmoid=0.5 (within float32 tolerance).
        assert probability == pytest.approx(0.5, abs=1e-6)
        assert 0.0 <= probability <= 1.0

    def test_predict_rejects_wrong_feature_shape(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> None:
        model_file = tmp_path / "stub.onnx"
        model_file.write_bytes(b"stub")

        class _StubInputMeta:
            name = "input"

        class _StubSession:
            def __init__(self, *_: Any, **__: Any) -> None:
                pass

            def get_inputs(self) -> list[_StubInputMeta]:
                return [_StubInputMeta()]

            def run(self, _outputs: Any, _inputs: dict[str, Any]) -> list[np.ndarray]:
                return [np.array([[0.0]], dtype=np.float32)]

        monkeypatch.setattr("onnxruntime.InferenceSession", _StubSession)

        predictor = ChurnPredictor(str(model_file), "v-stub")
        predictor.load_model()
        with pytest.raises(ValueError, match="feature shape mismatch"):
            predictor.predict_probability(np.zeros(3, dtype=np.float32))

    def test_load_failure_keeps_predictor_unready(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> None:
        # File exists but onnxruntime barfs — graceful degradation, not a crash.
        model_file = tmp_path / "corrupt.onnx"
        model_file.write_bytes(b"not really onnx")

        def _raising_session(*_: Any, **__: Any) -> None:
            msg = "deliberate stub corruption"
            raise RuntimeError(msg)

        monkeypatch.setattr("onnxruntime.InferenceSession", _raising_session)

        predictor = ChurnPredictor(str(model_file), "v-stub")
        predictor.load_model()
        assert predictor.is_ready() is False
