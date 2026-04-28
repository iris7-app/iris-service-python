"""Unit tests for the MCP DTOs.

Light : just confirms the frozen / Decimal / Literal contracts that
downstream tools rely on. The interesting behaviour is in tools.py
(tested separately with mocked session factories).
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from pydantic import ValidationError

from iris_service.mcp.dtos import (
    TOOL_NAMES,
    CancelResult,
    ChaosResult,
    ComponentStatus,
    Customer360,
    EnvSnapshot,
    HealthSnapshot,
    InfoBlock,
    LogEvent,
    MetricSnapshot,
    NotFound,
    OpenApiSummary,
    OrderListItem,
    OrderRef,
    ProductLowStock,
)


def test_customer360_is_frozen() -> None:
    c = Customer360(
        id=1,
        name="Alice",
        email="a@b.c",
        order_count=2,
        total_revenue=Decimal("99.50"),
        last_order_at=datetime.now(UTC),
    )
    with pytest.raises(ValidationError):
        c.id = 2  # type: ignore[misc]


def test_customer360_total_revenue_is_decimal() -> None:
    c = Customer360(
        id=1,
        name="A",
        email="a@b.c",
        order_count=0,
        total_revenue=Decimal("0"),
        last_order_at=None,
    )
    # Decimal stays Decimal — never demoted to float (precision-critical for €).
    assert isinstance(c.total_revenue, Decimal)


def test_log_event_optionals() -> None:
    e = LogEvent(
        timestamp=datetime.now(UTC),
        level="INFO",
        logger="x",
        message="hello",
    )
    assert e.request_id is None
    assert e.trace_id is None


def test_metric_snapshot_type_is_literal() -> None:
    # ``type`` value outside the canonical 5 must be rejected.
    with pytest.raises(ValidationError):
        MetricSnapshot(
            name="x",
            tags={},
            type="not-a-real-kind",  # type: ignore[arg-type]
            value=1.0,
            timestamp=datetime.now(UTC),
        )


def test_health_snapshot_status_literal() -> None:
    snap = HealthSnapshot(
        status="UP",
        components={"db": ComponentStatus(status="UP")},
    )
    assert snap.status == "UP"
    assert snap.components["db"].status == "UP"


def test_env_snapshot_just_a_dict() -> None:
    snap = EnvSnapshot(properties={"db.host": "localhost", "db.password": "***REDACTED***"})
    assert snap.properties["db.password"] == "***REDACTED***"


def test_openapi_summary_shape() -> None:
    summary = OpenApiSummary(
        info=InfoBlock(title="t", version="1"),
        paths_by_verb={"get": ["/a", "/b"], "post": ["/c"]},
    )
    assert summary.paths_by_verb["get"] == ["/a", "/b"]


def test_notfound_builder() -> None:
    nf = NotFound.for_("Order", 42)
    assert nf.entity == "Order"
    assert nf.id == 42
    assert nf.not_found is True


def test_order_ref_and_listitem_carry_decimal() -> None:
    ts = datetime.now(UTC)
    ref = OrderRef(id=1, customer_id=1, status="PENDING", total_amount=Decimal("0"), created_at=ts)
    li = OrderListItem(id=1, customer_id=1, status="PENDING", total_amount=Decimal("0"), created_at=ts)
    assert isinstance(ref.total_amount, Decimal)
    assert isinstance(li.total_amount, Decimal)


def test_product_low_stock_decimal_price() -> None:
    p = ProductLowStock(id=1, name="Widget", unit_price=Decimal("3.50"), stock_quantity=2)
    assert isinstance(p.unit_price, Decimal)


def test_chaos_and_cancel_results_frozen() -> None:
    cr = ChaosResult(scenario="slow-query", effective=True, detail="2s")
    cancel = CancelResult(id=1, cancelled=True, previous_status="PENDING")
    with pytest.raises(ValidationError):
        cr.scenario = "x"  # type: ignore[misc]
    with pytest.raises(ValidationError):
        cancel.cancelled = False  # type: ignore[misc]


def test_tool_names_canonical_count() -> None:
    # 7 domain + 1 ML inference (Phase C) + 7 observability = 15.
    # Updated 2026-04-27 with predict_customer_churn from shared ADR-0061.
    assert len(TOOL_NAMES) == 15
    # Tool names are unique (otherwise FastMCP would silently last-write-wins).
    assert len(set(TOOL_NAMES)) == 15
