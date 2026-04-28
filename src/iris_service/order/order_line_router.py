"""OrderLine FastAPI router — nested under /orders/{order_id}/lines.

Mirrors Java's OrderLineController. Snapshots Product.unit_price at
add-time + recomputes Order.total_amount in same transaction.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from iris_service.db.base import get_db_session
from iris_service.order.models import Order
from iris_service.order.order_line_models import OrderLine, OrderLineStatus
from iris_service.product.models import Product

router = APIRouter(prefix="/orders/{order_id}/lines", tags=["order-lines"])

DbSession = Annotated[AsyncSession, Depends(get_db_session)]

OrderLineStatusLiteral = Literal["PENDING", "SHIPPED", "REFUNDED"]


class OrderLineCreate(BaseModel):
    """`POST /orders/{order_id}/lines` body. Price is snapshotted server-side."""

    product_id: Annotated[int, Field(gt=0)]
    quantity: Annotated[int, Field(gt=0)]


class OrderLineResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    order_id: int
    product_id: int
    quantity: int
    unit_price_at_order: Decimal
    status: OrderLineStatusLiteral
    created_at: object  # datetime, kept loose for serialization


@router.get("", response_model=list[OrderLineResponse])
async def list_lines(order_id: int, session: DbSession) -> list[OrderLineResponse]:
    """List all lines of an order."""
    stmt = select(OrderLine).where(OrderLine.order_id == order_id).order_by(OrderLine.id)
    lines = (await session.execute(stmt)).scalars().all()
    return [OrderLineResponse.model_validate(line) for line in lines]


@router.post("", response_model=OrderLineResponse, status_code=status.HTTP_201_CREATED)
async def add_line(
    order_id: int,
    payload: OrderLineCreate,
    session: DbSession,
) -> OrderLineResponse:
    """Add a line to an order. Snapshots Product.unit_price + recomputes
    Order.total_amount in same transaction."""
    # Verify order exists
    order = await session.get(Order, order_id)
    if order is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Order not found")
    # Verify product exists + grab its current price for snapshot
    product = await session.get(Product, payload.product_id)
    if product is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Product {payload.product_id} not found",
        )

    line = OrderLine(
        order_id=order_id,
        product_id=payload.product_id,
        quantity=payload.quantity,
        unit_price_at_order=product.unit_price,  # SNAPSHOT
        status=OrderLineStatus.PENDING.value,
    )
    session.add(line)
    await session.flush()
    await session.refresh(line)

    # Recompute Order.total_amount
    await _recompute_order_total(session, order)

    return OrderLineResponse.model_validate(line)


class OrderLineStatusUpdate(BaseModel):
    """Body for `PATCH /orders/{order_id}/lines/{line_id}/status`.

    Per [shared ADR-0063](https://gitlab.com/iris-7/iris-service-shared/-/blob/main/docs/adr/0063-order-line-refund-state-machine.md)
    §"Audit", any transition writes to the audit_event row carrying
    the (optional but recommended) ``reason``. The reason is capped at
    500 chars to keep the audit row manageable.
    """

    status: OrderLineStatusLiteral
    reason: Annotated[str | None, Field(default=None, max_length=500)] = None


@router.patch("/{line_id}/status", response_model=OrderLineResponse)
async def update_line_status(
    order_id: int,
    line_id: int,
    payload: OrderLineStatusUpdate,
    session: DbSession,
) -> OrderLineResponse:
    """Update an order line's status — state-machine validated per
    [shared ADR-0063](https://gitlab.com/iris-7/iris-service-shared/-/blob/main/docs/adr/0063-order-line-refund-state-machine.md).

    Forward-only graph : ``PENDING → SHIPPED → REFUNDED``. The skip
    ``PENDING → REFUNDED`` is rejected — a refund must follow a
    shipment for audit traceability. Self-transitions allowed
    (idempotency for retries).

    Refunding does NOT mutate ``unit_price_at_order`` snapshot, so
    ``order.total_amount`` stays unchanged. Money flow (issuing the
    actual refund through a payment processor) is OUT OF SCOPE per
    ADR-0063 §"Negative consequences" — the state transition is the
    trigger event ; an orchestrator listens to it and handles the
    financial side.

    Returns :
        - 200 + updated :class:`OrderLineResponse` on valid transition.
        - 404 if order_id / line_id missing OR mismatch (URL-spoofing
          safety — don't leak existence by branching on the order).
        - 409 ProblemDetail-shaped detail with currentStatus +
          targetStatus + reason on forbidden transition.
        - 422 if status is unknown (Pydantic Literal rejects).
    """
    line = await session.get(OrderLine, line_id)
    if line is None or line.order_id != order_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="OrderLine not found")

    current = OrderLineStatus(line.status)
    target = OrderLineStatus(payload.status)
    if not current.can_transition_to(target):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "type": "urn:problem:invalid-line-status-transition",
                "title": "Invalid order line status transition",
                "detail": f"Cannot transition line from {current.value} to {target.value}",
                "currentStatus": current.value,
                "targetStatus": target.value,
                "reason": payload.reason or "",
            },
        )

    line.status = target.value
    await session.flush()
    await session.refresh(line)
    # Order.total_amount stays unchanged — see ADR-0063 §"Refund
    # refunds the snapshot". The audit-event hook (existing JWT
    # correlation) carries the (current, target, reason) triple.
    return OrderLineResponse.model_validate(line)


@router.delete("/{line_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_line(order_id: int, line_id: int, session: DbSession) -> None:
    """Delete a line + recompute Order.total_amount."""
    line = await session.get(OrderLine, line_id)
    if line is None or line.order_id != order_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="OrderLine not found")
    await session.delete(line)
    await session.flush()

    order = await session.get(Order, order_id)
    if order is not None:
        await _recompute_order_total(session, order)


async def _recompute_order_total(session: AsyncSession, order: Order) -> None:
    """Recompute order.total_amount = Σ(line.qty * line.unit_price_at_order)."""
    stmt = select(OrderLine).where(OrderLine.order_id == order.id)
    lines = (await session.execute(stmt)).scalars().all()
    total = sum((line.unit_price_at_order * line.quantity for line in lines), Decimal("0"))
    order.total_amount = total
    await session.flush()
