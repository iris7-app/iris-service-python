"""Order FastAPI router — mirrors Java's OrderController.

Foundation MR : list + get + create (empty) + delete. PUT (status
transitions) + OrderLine endpoints deferred.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from iris_service.db.base import get_db_session
from iris_service.order.dtos import (
    OrderCreate,
    OrderPage,
    OrderResponse,
    OrderStatusUpdate,
)
from iris_service.order.models import Order, OrderStatus
from iris_service.order.repository import OrderRepository

router = APIRouter(prefix="/orders", tags=["orders"])

DbSession = Annotated[AsyncSession, Depends(get_db_session)]


@router.get("", response_model=OrderPage)
async def list_orders(
    session: DbSession,
    page: Annotated[int, Query(ge=0)] = 0,
    size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> OrderPage:
    """Paginated list of orders, newest first."""
    repo = OrderRepository(session)
    items, total = await repo.list_paginated(page=page, size=size)
    return OrderPage(
        items=[OrderResponse.from_orm_entity(o) for o in items],
        total=total,
        page=page,
        size=size,
    )


@router.get("/{order_id}", response_model=OrderResponse)
async def get_order(order_id: int, session: DbSession) -> OrderResponse:
    repo = OrderRepository(session)
    order = await repo.get_by_id(order_id)
    if order is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Order not found")
    return OrderResponse.from_orm_entity(order)


@router.post("", response_model=OrderResponse, status_code=status.HTTP_201_CREATED)
async def create_order(payload: OrderCreate, session: DbSession) -> OrderResponse:
    """Create an empty order attached to a customer.

    422 if customer doesn't exist (FK violation on flush). Lines are
    added via subsequent endpoint once OrderLine ships (alembic 0004).
    """
    repo = OrderRepository(session)
    order = Order(
        customer_id=payload.customer_id,
        status=OrderStatus.PENDING.value,
        total_amount=Decimal("0"),
    )
    try:
        saved = await repo.add(order)
    except IntegrityError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Customer {payload.customer_id} not found",
        ) from exc
    return OrderResponse.from_orm_entity(saved)


@router.put("/{order_id}/status", response_model=OrderResponse)
async def update_order_status(
    order_id: int,
    payload: OrderStatusUpdate,
    session: DbSession,
) -> OrderResponse:
    """Update an order's status — state-machine validated.

    Mirrors Java's PUT /orders/{id}/status exactly :
    - 200 + updated OrderResponse on valid transition.
    - 404 if the order doesn't exist.
    - 409 ProblemDetail-shaped body on forbidden transition (current
      and target statuses surfaced so the UI can render a meaningful
      error).
    - 422 if the status is unknown (Pydantic Literal validation).

    State graph (per `OrderStatus.can_transition_to`) :
    PENDING → CONFIRMED, CANCELLED ; CONFIRMED → SHIPPED, CANCELLED ;
    SHIPPED, CANCELLED → terminal. Self-transitions are allowed
    (idempotency for retries).
    """
    repo = OrderRepository(session)
    order = await repo.get_by_id(order_id)
    if order is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Order not found")

    current = OrderStatus(order.status)
    target = OrderStatus(payload.status)
    if not current.can_transition_to(target):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "type": "urn:problem:invalid-status-transition",
                "title": "Invalid order status transition",
                "detail": f"Cannot transition from {current.value} to {target.value}",
                "currentStatus": current.value,
                "targetStatus": target.value,
            },
        )

    order.status = target.value
    await session.flush()
    await session.refresh(order)
    return OrderResponse.from_orm_entity(order)


@router.delete("/{order_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_order(order_id: int, session: DbSession) -> None:
    """Delete an order. CASCADE will remove OrderLines once V9 ships."""
    repo = OrderRepository(session)
    if not await repo.delete(order_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Order not found")
