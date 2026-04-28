"""Integration tests for invariant 6 of shared ADR-0059 — cascade safety.

Mirrors `iris-service-java`'s `OrderCascadeITest`. Exercises the REAL
FK constraints from alembic 0003 (orders) + 0004 (order_line) :

  order_line.order_id   REFERENCES orders(id)  ON DELETE CASCADE
  order_line.product_id REFERENCES product(id) ON DELETE RESTRICT

ADR-0059 :
https://gitlab.com/iris-7/iris-service-shared/-/blob/main/docs/adr/0059-customer-order-product-data-model.md
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from iris_service.customer.models import Customer
from iris_service.order.models import Order, OrderStatus
from iris_service.order.order_line_models import OrderLine, OrderLineStatus
from iris_service.product.models import Product

pytestmark = pytest.mark.integration


async def _seed(session: AsyncSession) -> tuple[int, int, int]:
    """Insert one Customer + one Product + one Order + one OrderLine ;
    return (product_id, order_id, line_id). Customer is auto-created since
    the testcontainer Postgres has no demo seed data — orders.customer_id
    is FK-constrained to customer(id)."""
    # Postgres testcontainer is empty — create a Customer for the FK
    customer = Customer(name="Cascade Tester", email=f"cascade-{id(session)}@example.com")
    session.add(customer)
    await session.flush()

    product = Product(
        name=f"Cascade-test-widget-{id(session)}",
        unit_price=Decimal("9.99"),
        stock_quantity=10,
    )
    session.add(product)
    await session.flush()

    order = Order(
        customer_id=customer.id,
        status=OrderStatus.PENDING.value,
        total_amount=Decimal("9.99"),
    )
    session.add(order)
    await session.flush()

    line = OrderLine(
        order_id=order.id,
        product_id=product.id,
        quantity=1,
        unit_price_at_order=Decimal("9.99"),
        status=OrderLineStatus.PENDING.value,
    )
    session.add(line)
    await session.flush()
    await session.commit()

    return product.id, order.id, line.id


@pytest.mark.asyncio
async def test_deleting_order_cascades_lines_keeps_product(
    postgres_session: AsyncSession,
) -> None:
    """Invariant 6 — happy cascade : DELETE order removes its lines via
    DB-level CASCADE, but the referenced Product is NOT touched (RESTRICT
    side preserved)."""
    product_id, order_id, line_id = await _seed(postgres_session)

    # Pre-conditions
    assert await postgres_session.get(Order, order_id) is not None
    assert await postgres_session.get(OrderLine, line_id) is not None
    assert await postgres_session.get(Product, product_id) is not None

    # Act
    order = await postgres_session.get(Order, order_id)
    await postgres_session.delete(order)
    await postgres_session.commit()

    # Post-conditions
    assert await postgres_session.get(Order, order_id) is None, "order removed"
    assert await postgres_session.get(OrderLine, line_id) is None, "line cascade-removed via FK ON DELETE CASCADE"
    assert await postgres_session.get(Product, product_id) is not None, (
        "product NOT touched — FK RESTRICT side preserved"
    )


@pytest.mark.asyncio
async def test_deleting_product_referenced_by_line_is_rejected(
    postgres_session: AsyncSession,
) -> None:
    """Invariant 6 — RESTRICT side : Postgres rejects DELETE product when
    an OrderLine still references it. Surfaces as IntegrityError on commit."""
    product_id, _order_id, _line_id = await _seed(postgres_session)

    product = await postgres_session.get(Product, product_id)
    await postgres_session.delete(product)

    with pytest.raises(IntegrityError):
        await postgres_session.commit()

    # Recovery : need a fresh session because the failed commit poisoned
    # the current one. Verify product still exists from the row count.
    await postgres_session.rollback()
    stmt = select(Product).where(Product.id == product_id)
    result = (await postgres_session.execute(stmt)).scalar_one_or_none()
    assert result is not None, "product survives the rejected delete"


@pytest.mark.asyncio
async def test_deleting_order_with_multiple_lines_cascades_all(
    postgres_session: AsyncSession,
) -> None:
    """Invariant 6 — multi-line cascade : N lines all get removed, not just
    one. Validates the FK CASCADE applies symmetrically to a row set."""
    product_id, order_id, _line_id = await _seed(postgres_session)

    # Add 2 more lines on the same order (3 total)
    for _ in range(2):
        extra = OrderLine(
            order_id=order_id,
            product_id=product_id,
            quantity=1,
            unit_price_at_order=Decimal("9.99"),
            status=OrderLineStatus.PENDING.value,
        )
        postgres_session.add(extra)
    await postgres_session.commit()

    stmt = select(OrderLine).where(OrderLine.order_id == order_id)
    lines_before = (await postgres_session.execute(stmt)).scalars().all()
    assert len(lines_before) == 3, "3 lines seeded"

    # Act
    order = await postgres_session.get(Order, order_id)
    await postgres_session.delete(order)
    await postgres_session.commit()

    # All 3 lines gone
    stmt2 = select(OrderLine).where(OrderLine.order_id == order_id)
    lines_after = (await postgres_session.execute(stmt2)).scalars().all()
    assert len(lines_after) == 0, "all lines cascade-removed"

    # Product still here
    assert await postgres_session.get(Product, product_id) is not None
