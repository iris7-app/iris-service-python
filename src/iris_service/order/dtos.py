"""Pydantic DTOs for the Order API.

Mirrors Java's `CreateOrderRequest` + `OrderDto` + `Page<OrderDto>`.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from iris_service.order.models import Order

# Status type — mirrors Java's OrderStatus enum
OrderStatusLiteral = Literal["PENDING", "CONFIRMED", "SHIPPED", "CANCELLED"]


class OrderCreate(BaseModel):
    """`POST /orders` body — empty order attached to a customer.

    Lines are added via separate endpoint (foundation MR creates header
    only ; OrderLine support ships in alembic 0004).
    """

    customer_id: Annotated[int, Field(gt=0)]


class OrderStatusUpdate(BaseModel):
    """`PUT /orders/{id}/status` body — single status field.

    Pydantic's Literal validation rejects unknown statuses with a 422
    BEFORE the controller runs, so the state-machine check inside
    OrderStatus.can_transition_to() only has to worry about VALID
    target statuses (no need to handle gibberish).
    """

    status: OrderStatusLiteral


class OrderResponse(BaseModel):
    """Outbound representation. Header only — line list comes once
    OrderLine entity ships."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    customer_id: int
    status: OrderStatusLiteral
    total_amount: Decimal
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_orm_entity(cls, o: Order) -> OrderResponse:
        return cls(
            id=o.id,
            customer_id=o.customer_id,
            status=o.status,
            total_amount=o.total_amount,
            created_at=o.created_at,
            updated_at=o.updated_at,
        )


class OrderPage(BaseModel):
    items: list[OrderResponse]
    total: int
    page: int
    size: int
