"""Unit tests for the 14 MCP tools.

Strategy : in-memory SQLite + isolated Prometheus registry. Each test
exercises one tool's happy + error path. The tool wrappers (added by
register_tools) are tested separately via the FastMCP list_tools()
contract test in test_mount.py.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from iris_service.customer.models import Customer
from iris_service.mcp.auth import ROLE_ADMIN, ROLE_USER, McpForbiddenError, McpUser, set_current_user
from iris_service.mcp.dtos import (
    CancelResult,
    ChaosResult,
    Customer360,
    EnvSnapshot,
    HealthSnapshot,
    InfoBlock,
    LogEvent,
    NotFound,
    OpenApiSummary,
    OrderListItem,
    OrderRef,
    ProductLowStock,
)
from iris_service.mcp.tools import (
    Deps,
    cancel_order,
    create_order,
    find_low_stock_products,
    get_actuator_env,
    get_actuator_info,
    get_customer_360,
    get_health,
    get_health_detail,
    get_metrics,
    get_openapi_spec,
    get_order_by_id,
    list_recent_orders,
    reset_idempotency_cache,
    tail_logs,
    trigger_chaos_experiment,
)
from iris_service.order.models import Order, OrderStatus
from iris_service.product.models import Product

# ── Seed helpers ──────────────────────────────────────────────────────────────


async def _seed_customer_with_orders(deps: Deps) -> int:
    """Insert a customer + 2 orders ; return customer id."""
    async with await deps.session_factory() as session:
        customer = Customer(name="Alice", email="alice@example.com")
        session.add(customer)
        await session.flush()
        for total, status in [(Decimal("10.00"), OrderStatus.PENDING), (Decimal("20.50"), OrderStatus.SHIPPED)]:
            session.add(
                Order(
                    customer_id=customer.id,
                    status=status.value,
                    total_amount=total,
                )
            )
        await session.commit()
        return customer.id


async def _seed_low_stock_products(deps: Deps) -> None:
    async with await deps.session_factory() as session:
        session.add(Product(name="Hot Item", unit_price=Decimal("9.99"), stock_quantity=2))
        session.add(Product(name="In Stock", unit_price=Decimal("19.99"), stock_quantity=50))
        session.add(Product(name="Critical", unit_price=Decimal("4.99"), stock_quantity=0))
        await session.commit()


# ── Domain : list_recent_orders ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_list_recent_orders_empty(deps: Deps) -> None:
    out = await list_recent_orders(deps)
    assert out == []


@pytest.mark.asyncio
async def test_list_recent_orders_returns_newest_first(deps: Deps) -> None:
    await _seed_customer_with_orders(deps)
    out = await list_recent_orders(deps, limit=5)
    assert len(out) == 2
    assert all(isinstance(o, OrderListItem) for o in out)
    assert out[0].id > out[1].id


@pytest.mark.asyncio
async def test_list_recent_orders_status_filter(deps: Deps) -> None:
    await _seed_customer_with_orders(deps)
    pending = await list_recent_orders(deps, status="PENDING")
    shipped = await list_recent_orders(deps, status="SHIPPED")
    assert len(pending) == 1
    assert pending[0].status == "PENDING"
    assert len(shipped) == 1
    assert shipped[0].status == "SHIPPED"


@pytest.mark.asyncio
async def test_list_recent_orders_limit_clamped(deps: Deps) -> None:
    """Limit > 100 is clamped, limit < 1 is clamped to 1 — never raises."""
    out_zero = await list_recent_orders(deps, limit=0)
    assert out_zero == []  # no data, but didn't crash
    out_neg = await list_recent_orders(deps, limit=-100)
    assert out_neg == []
    out_huge = await list_recent_orders(deps, limit=1000)
    assert out_huge == []  # no data, but ran


# ── Domain : get_order_by_id ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_order_by_id_not_found(deps: Deps) -> None:
    out = await get_order_by_id(deps, id=99999)
    assert isinstance(out, NotFound)
    assert out.entity == "Order"
    assert out.id == 99999


@pytest.mark.asyncio
async def test_get_order_by_id_happy(deps: Deps) -> None:
    cust_id = await _seed_customer_with_orders(deps)
    listed = await list_recent_orders(deps)
    target_id = listed[0].id
    out = await get_order_by_id(deps, id=target_id)
    assert isinstance(out, OrderListItem)
    assert out.id == target_id
    assert out.customer_id == cust_id


# ── Domain : create_order ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_create_order_happy(deps: Deps) -> None:
    cust_id = await _seed_customer_with_orders(deps)
    out = await create_order(deps, customer_id=cust_id)
    assert isinstance(out, OrderRef)
    assert out.customer_id == cust_id
    assert out.status == "PENDING"
    assert out.total_amount == Decimal("0")


@pytest.mark.asyncio
async def test_create_order_missing_customer(deps: Deps) -> None:
    """SQLite enforces FK only when PRAGMA is on — Order created cleanly OR
    NotFound returned. Either way, the contract holds : a valid OrderRef
    or a NotFound, never an exception bubbling up."""
    out = await create_order(deps, customer_id=99999)
    assert isinstance(out, (OrderRef, NotFound))


@pytest.mark.asyncio
async def test_create_order_idempotent(deps: Deps) -> None:
    """Same idempotency_key returns the SAME OrderRef on retry."""
    reset_idempotency_cache()
    cust_id = await _seed_customer_with_orders(deps)
    first = await create_order(deps, customer_id=cust_id, idempotency_key="key-1")
    second = await create_order(deps, customer_id=cust_id, idempotency_key="key-1")
    assert isinstance(first, OrderRef)
    assert isinstance(second, OrderRef)
    assert first.id == second.id


@pytest.mark.asyncio
async def test_create_order_different_keys_make_different_orders(deps: Deps) -> None:
    reset_idempotency_cache()
    cust_id = await _seed_customer_with_orders(deps)
    a = await create_order(deps, customer_id=cust_id, idempotency_key="a")
    b = await create_order(deps, customer_id=cust_id, idempotency_key="b")
    assert isinstance(a, OrderRef)
    assert isinstance(b, OrderRef)
    assert a.id != b.id


# ── Domain : cancel_order ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_cancel_order_not_found(deps: Deps) -> None:
    out = await cancel_order(deps, id=99999)
    assert isinstance(out, NotFound)


@pytest.mark.asyncio
async def test_cancel_order_happy(deps: Deps) -> None:
    await _seed_customer_with_orders(deps)
    listed = await list_recent_orders(deps)
    target_id = listed[0].id
    out = await cancel_order(deps, id=target_id)
    assert isinstance(out, CancelResult)
    assert out.cancelled is True
    assert out.previous_status in {"PENDING", "SHIPPED"}
    # Verify state changed.
    fetched = await get_order_by_id(deps, id=target_id)
    assert isinstance(fetched, OrderListItem)
    assert fetched.status == "CANCELLED"


# ── Domain : find_low_stock_products ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_find_low_stock_products_default_threshold(deps: Deps) -> None:
    await _seed_low_stock_products(deps)
    out = await find_low_stock_products(deps)
    assert all(isinstance(p, ProductLowStock) for p in out)
    # Default threshold 10 — only the qty=2 + qty=0 ones match.
    names = {p.name for p in out}
    assert "Hot Item" in names
    assert "Critical" in names
    assert "In Stock" not in names


@pytest.mark.asyncio
async def test_find_low_stock_products_negative_threshold_clamped(deps: Deps) -> None:
    await _seed_low_stock_products(deps)
    out = await find_low_stock_products(deps, threshold=-5)
    # Threshold clamped to 0 ; nothing has stock_quantity < 0.
    assert out == []


# ── Domain : get_customer_360 ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_customer_360_not_found(deps: Deps) -> None:
    out = await get_customer_360(deps, id=99999)
    assert isinstance(out, NotFound)
    assert out.entity == "Customer"


@pytest.mark.asyncio
async def test_get_customer_360_aggregates_orders(deps: Deps) -> None:
    cust_id = await _seed_customer_with_orders(deps)
    out = await get_customer_360(deps, id=cust_id)
    assert isinstance(out, Customer360)
    assert out.id == cust_id
    assert out.order_count == 2
    assert out.total_revenue == Decimal("30.50")
    assert out.last_order_at is not None


@pytest.mark.asyncio
async def test_get_customer_360_no_orders(deps: Deps) -> None:
    """Customer with zero orders : revenue=0, count=0, last=None."""
    async with await deps.session_factory() as session:
        c = Customer(name="No Orders", email="nobuy@example.com")
        session.add(c)
        await session.commit()
        cid = c.id
    out = await get_customer_360(deps, id=cid)
    assert isinstance(out, Customer360)
    assert out.order_count == 0
    assert out.total_revenue == Decimal("0")
    assert out.last_order_at is None


# ── Domain : trigger_chaos_experiment (admin-gated) ───────────────────────────


@pytest.mark.asyncio
async def test_trigger_chaos_requires_admin(deps: Deps) -> None:
    set_current_user(McpUser(username="user", role=ROLE_USER))
    with pytest.raises(McpForbiddenError):
        await trigger_chaos_experiment(deps, scenario="kafka-timeout")


@pytest.mark.asyncio
async def test_trigger_chaos_kafka_timeout_admin(deps: Deps) -> None:
    set_current_user(McpUser(username="admin", role=ROLE_ADMIN))
    out = await trigger_chaos_experiment(deps, scenario="kafka-timeout")
    assert isinstance(out, ChaosResult)
    assert out.scenario == "kafka-timeout"
    assert out.effective is True


@pytest.mark.asyncio
async def test_trigger_chaos_db_failure_admin(deps: Deps) -> None:
    set_current_user(McpUser(username="admin", role=ROLE_ADMIN))
    out = await trigger_chaos_experiment(deps, scenario="db-failure")
    assert out.effective is True
    assert "rejected" in out.detail.lower() or "no such" in out.detail.lower()


# ── Observability : tail_logs ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_tail_logs_returns_recent(deps: Deps) -> None:
    """Ring buffer holds whatever the test logged through the deps' handler."""
    import logging

    log = logging.getLogger("test.tail.logs")
    log.handlers = [deps.ring_buffer]
    log.setLevel(logging.DEBUG)
    log.info("hello world")
    out = await tail_logs(deps, n=10)
    assert len(out) >= 1
    assert all(isinstance(e, LogEvent) for e in out)
    assert any("hello world" in e.message for e in out)


@pytest.mark.asyncio
async def test_tail_logs_filters_level(deps: Deps) -> None:
    import logging

    log = logging.getLogger("test.tail.level")
    log.handlers = [deps.ring_buffer]
    log.setLevel(logging.DEBUG)
    log.info("info-msg")
    log.error("err-msg")
    only_err = await tail_logs(deps, n=10, level="ERROR")
    assert len(only_err) == 1
    assert only_err[0].message == "err-msg"


# ── Observability : get_metrics ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_metrics_empty_registry(deps: Deps) -> None:
    out = await get_metrics(deps)
    assert isinstance(out, list)


@pytest.mark.asyncio
async def test_get_metrics_with_seed(deps: Deps) -> None:
    from prometheus_client import Counter

    # Seed the deps' isolated registry
    Counter("tools_test_total", "x", registry=deps.metrics_reader._registry).inc()
    out = await get_metrics(deps, name_filter="tools_test")
    names = {s.name for s in out}
    assert "tools_test_total" in names


# ── Observability : health ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_health_returns_snapshot(deps: Deps) -> None:
    out = await get_health(deps)
    assert isinstance(out, HealthSnapshot)
    # SQLite responds to SELECT 1 — should be UP.
    assert out.status == "UP"


@pytest.mark.asyncio
async def test_get_health_detail_requires_admin(deps: Deps) -> None:
    set_current_user(McpUser(username="u", role=ROLE_USER))
    with pytest.raises(McpForbiddenError):
        await get_health_detail(deps)


@pytest.mark.asyncio
async def test_get_health_detail_admin_ok(deps: Deps) -> None:
    set_current_user(McpUser(username="a", role=ROLE_ADMIN))
    out = await get_health_detail(deps)
    assert isinstance(out, HealthSnapshot)


# ── Observability : env / info / openapi ──────────────────────────────────────


@pytest.mark.asyncio
async def test_get_actuator_env_redacts(deps: Deps) -> None:
    out = await get_actuator_env(deps)
    assert isinstance(out, EnvSnapshot)
    assert out.properties["jwt.secret"] == "***REDACTED***"


@pytest.mark.asyncio
async def test_get_actuator_env_prefix(deps: Deps) -> None:
    out = await get_actuator_env(deps, prefix="db.")
    assert all(k.startswith("db.") for k in out.properties)


@pytest.mark.asyncio
async def test_get_actuator_info(deps: Deps) -> None:
    out = await get_actuator_info(deps)
    assert isinstance(out, InfoBlock)
    assert out.title == "test-app"
    assert "Runtime: CPython" in (out.description or "")


@pytest.mark.asyncio
async def test_get_openapi_spec_summary_default(deps: Deps) -> None:
    out = await get_openapi_spec(deps)
    assert isinstance(out, OpenApiSummary)


@pytest.mark.asyncio
async def test_get_openapi_spec_full(deps: Deps) -> None:
    out = await get_openapi_spec(deps, summary=False)
    assert isinstance(out, dict)
    assert "openapi" in out
