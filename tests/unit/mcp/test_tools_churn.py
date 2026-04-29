"""Unit tests for the predict_customer_churn MCP tool.

Lives in its own file (not test_tools.py) because the churn tool needs
a stub :class:`ChurnPredictor` injected via :class:`Deps`, which isn't
shared with the other 14 tools — the conftest's ``deps`` fixture leaves
``churn_predictor=None`` and the tool returns
:class:`ChurnServiceUnavailable` on that path.

These tests pin the soft-error contract :
- ``churn_predictor=None`` → :class:`ChurnServiceUnavailable` ("unconfigured" path).
- predictor present but ``is_ready()`` is ``False`` → :class:`ChurnServiceUnavailable`
  with the model_path included in the message (helps the operator find the missing file).
- predictor ready, customer absent → :class:`ChurnNotFound`.
- predictor ready, customer present (no orders) → :class:`ChurnPrediction`
  with probability in [0, 1] + non-empty top_features + the model_version
  threaded back from the predictor.
- predictor ready, customer present with orders + lines → exercises the
  feature-extraction path (lines 397-403 in tools.py — the ``select(Order)``
  + ``select(OrderLine)`` branches).

The tool MUST never raise across the MCP boundary — every test asserts a
DTO is returned, never an exception, mirroring the Java sibling's
"interchangeable backends" contract (common ADR-0008).
"""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal

import numpy as np
import pytest

from iris_service.customer.models import Customer
from iris_service.mcp.tools import Deps, predict_customer_churn
from iris_service.ml.dtos import ChurnNotFound, ChurnPrediction, ChurnServiceUnavailable
from iris_service.ml.inference import N_FEATURES, ChurnPredictor
from iris_service.order.models import Order, OrderStatus
from iris_service.order.order_line_models import OrderLine
from iris_service.product.models import Product


class _StubChurnPredictor(ChurnPredictor):
    """Drop-in :class:`ChurnPredictor` stub — deterministic probability.

    Subclasses the real :class:`ChurnPredictor` so it satisfies the
    ``Deps.churn_predictor: ChurnPredictor | None`` annotation under
    strict mypy. Mirrors the ``_StubPredictor`` pattern used in
    ``tests/unit/ml/test_router_churn.py`` — kept independent so future
    drift on either side doesn't cross-break.

    Only the methods :func:`predict_customer_churn` exercises are
    overridden. Everything else (``load_model``, ``predict_probability``
    on raw ndarray) stays as-is from the parent.
    """

    def __init__(
        self,
        *,
        ready: bool = True,
        version: str = "v-stub",
        path: str = "/test/stub.onnx",
        probability: float = 0.5,
    ) -> None:
        # Don't call super().__init__ — it sets _session=None which the
        # parent's is_ready() reads. We override is_ready() entirely.
        self._ready = ready
        self._stub_version = version
        self._stub_path = path
        self._stub_probability = probability

    @property
    def model_version(self) -> str:
        return self._stub_version

    @property
    def model_path(self) -> str:
        return self._stub_path

    def is_ready(self) -> bool:
        return self._ready

    def predict_probability(self, features: np.ndarray) -> float:
        # Pin the contract : the tool MUST hand us an (8,)-shaped vector
        # before calling. If this assertion fires, the feature extraction
        # path is broken.
        assert features.shape == (N_FEATURES,)
        return self._stub_probability


# -- Soft-error : unavailable predictor ---------------------------------------


@pytest.mark.asyncio
async def test_predict_returns_unavailable_when_predictor_is_none(deps: Deps) -> None:
    """deps.churn_predictor=None -> ChurnServiceUnavailable with 'unconfigured' path.

    Pins the early-return branch (line 379) - without a predictor injected,
    the tool returns the soft-error DTO INSTEAD of crashing on a None
    attribute access. Hint must mention the ConfigMap so operators know
    where to look.
    """
    out = await predict_customer_churn(deps, customer_id=42)
    assert isinstance(out, ChurnServiceUnavailable)
    assert "unconfigured" in out.message
    assert "ConfigMap" in out.hint


@pytest.mark.asyncio
async def test_predict_returns_unavailable_when_predictor_not_ready(deps: Deps) -> None:
    """Predictor present but not loaded -> ChurnServiceUnavailable with the model_path.

    Pins the second early-return branch (line 380-388) - the model_path
    surfaces in the message so the operator can grep logs for the missing
    file without reading the source.
    """
    deps_with_predictor = replace(
        deps,
        churn_predictor=_StubChurnPredictor(ready=False, path="/etc/models/missing.onnx"),
    )
    out = await predict_customer_churn(deps_with_predictor, customer_id=42)
    assert isinstance(out, ChurnServiceUnavailable)
    assert "/etc/models/missing.onnx" in out.message


# -- Soft-error : customer not found ------------------------------------------


@pytest.mark.asyncio
async def test_predict_returns_not_found_for_missing_customer(deps: Deps) -> None:
    """Predictor ready but customer id absent -> ChurnNotFound (line 393-396)."""
    deps_with_predictor = replace(deps, churn_predictor=_StubChurnPredictor())
    out = await predict_customer_churn(deps_with_predictor, customer_id=99999)
    assert isinstance(out, ChurnNotFound)
    assert out.customer_id == 99999
    assert "99999" in out.message


# -- Happy path : customer with no orders -------------------------------------


@pytest.mark.asyncio
async def test_predict_happy_no_orders(deps: Deps) -> None:
    """Customer present, zero orders -> ChurnPrediction with the predictor's
    probability rounded to 6 decimals + the predictor's model_version threaded
    back. Exercises the empty-orders branch (line 401 ``order_lines = []``).
    """
    async with await deps.session_factory() as session:
        cust = Customer(name="Lonely", email="lonely@example.com")
        session.add(cust)
        await session.commit()
        cid = cust.id

    deps_with_predictor = replace(
        deps,
        churn_predictor=_StubChurnPredictor(version="v-test-1", probability=0.42),
    )
    out = await predict_customer_churn(deps_with_predictor, customer_id=cid)
    assert isinstance(out, ChurnPrediction)
    assert out.customer_id == cid
    assert out.probability == pytest.approx(0.42, abs=1e-9)
    assert 0.0 <= out.probability <= 1.0
    assert out.model_version == "v-test-1"
    # top_features is the placeholder list per Phase C - pin it so a
    # change to the list shows up in code review.
    assert out.top_features == [
        "days_since_last_order",
        "total_revenue_90d",
        "order_frequency",
    ]


# -- Happy path : customer with orders + order lines --------------------------


@pytest.mark.asyncio
async def test_predict_happy_with_orders_and_lines(deps: Deps) -> None:
    """Customer + 2 orders + 1 order line -> exercises the
    ``select(OrderLine).where(order_id.in_(...))`` branch (lines 397-399).
    Without this test, that select stays uncovered - the no-orders test
    only hits the ``order_lines = []`` short-circuit.
    """
    async with await deps.session_factory() as session:
        cust = Customer(name="Buyer", email="buy@example.com")
        product = Product(name="Widget", unit_price=Decimal("9.99"), stock_quantity=100)
        session.add_all([cust, product])
        await session.flush()
        order = Order(
            customer_id=cust.id,
            status=OrderStatus.SHIPPED.value,
            total_amount=Decimal("19.98"),
        )
        session.add(order)
        await session.flush()
        line = OrderLine(
            order_id=order.id,
            product_id=product.id,
            quantity=2,
            unit_price_at_order=Decimal("9.99"),
        )
        session.add(line)
        await session.commit()
        cid = cust.id

    deps_with_predictor = replace(
        deps,
        churn_predictor=_StubChurnPredictor(probability=0.73),
    )
    out = await predict_customer_churn(deps_with_predictor, customer_id=cid)
    assert isinstance(out, ChurnPrediction)
    assert out.customer_id == cid
    assert out.probability == pytest.approx(0.73, abs=1e-9)
    # risk_band is computed from probability - 0.73 falls in HIGH
    # under the default thresholds (0.3, 0.6). Pin so a threshold
    # tweak surfaces here AND in the dedicated risk_band tests.
    assert out.risk_band.value == "HIGH"


# -- Probability rounding contract --------------------------------------------


@pytest.mark.asyncio
async def test_predict_rounds_probability_to_six_decimals(deps: Deps) -> None:
    """Pins the ``round(probability, 6)`` step (line 413). Without rounding,
    the JSON shape would diverge between Java (which uses 6-digit rounding
    in :class:`ChurnPredictionDto`) and Python - break the
    "interchangeable backends" contract.
    """
    async with await deps.session_factory() as session:
        cust = Customer(name="Pi", email="pi@example.com")
        session.add(cust)
        await session.commit()
        cid = cust.id

    # Probability with 7+ decimals -> must come back rounded to 6.
    deps_with_predictor = replace(
        deps,
        churn_predictor=_StubChurnPredictor(probability=0.123456789),
    )
    out = await predict_customer_churn(deps_with_predictor, customer_id=cid)
    assert isinstance(out, ChurnPrediction)
    assert out.probability == pytest.approx(0.123457, abs=1e-9)


# -- Risk band thresholds -----------------------------------------------------


@pytest.mark.parametrize(
    ("probability", "expected_band"),
    [
        (0.05, "LOW"),     # < 0.3
        (0.45, "MEDIUM"),  # [0.3, 0.6)
        (0.85, "HIGH"),    # >= 0.6
    ],
)
@pytest.mark.asyncio
async def test_predict_classifies_risk_band_per_threshold(
    deps: Deps, probability: float, expected_band: str
) -> None:
    """Pins the ``classify_risk(probability)`` mapping (line 412). Three
    samples, one per band - without these the band field is set but never
    asserted, so a threshold flip would slip through. Mirrors the Java
    sibling's :class:`RiskBandTest` parametric coverage.
    """
    async with await deps.session_factory() as session:
        cust = Customer(name="Rb", email="rb@example.com")
        session.add(cust)
        await session.commit()
        cid = cust.id

    deps_with_predictor = replace(
        deps,
        churn_predictor=_StubChurnPredictor(probability=probability),
    )
    out = await predict_customer_churn(deps_with_predictor, customer_id=cid)
    assert isinstance(out, ChurnPrediction)
    assert out.risk_band.value == expected_band
