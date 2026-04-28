"""Order ORM entity — mirrors Java's `Order.java`.

Stored in the `orders` table (plural — `order` is SQL reserved).
Status enum stored as VARCHAR(20) + DB CHECK constraint (mirrors Java's
@Enumerated(EnumType.STRING)). total_amount denormalised, app-managed.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum

from sqlalchemy import DateTime, ForeignKey, Integer, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column

from iris_service.db.base import Base


class OrderStatus(StrEnum):
    """Order lifecycle states. StrEnum for direct serialization to DB VARCHAR."""

    PENDING = "PENDING"
    CONFIRMED = "CONFIRMED"
    SHIPPED = "SHIPPED"
    CANCELLED = "CANCELLED"

    def can_transition_to(self, target: OrderStatus | None) -> bool:
        """Pure state-machine check — is the transition self → target allowed ?

        Per shared ADR-0059, valid graph :
            PENDING → CONFIRMED → SHIPPED
                ↘            ↘
                 CANCELLED   CANCELLED

        Self-transitions allowed (idempotent re-affirm). Backwards forbidden.
        CANCELLED is terminal. Null target always rejected.
        """
        if target is None:
            return False
        if self == target:
            return True
        match self:
            case OrderStatus.PENDING:
                return target in (OrderStatus.CONFIRMED, OrderStatus.CANCELLED)
            case OrderStatus.CONFIRMED:
                return target in (OrderStatus.SHIPPED, OrderStatus.CANCELLED)
            case OrderStatus.SHIPPED | OrderStatus.CANCELLED:
                return False


class Order(Base):
    """`orders` table — header. Lines come via OrderLine (alembic 0004)."""

    __tablename__ = "orders"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    customer_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("customer.id", ondelete="RESTRICT", name="fk_orders_customer"),
        nullable=False,
        index=True,
    )
    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default=OrderStatus.PENDING.value,
        index=True,
    )
    total_amount: Mapped[Decimal] = mapped_column(
        Numeric(precision=12, scale=2),
        nullable=False,
        default=Decimal("0"),
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )

    def __repr__(self) -> str:
        return (
            f"Order(id={self.id!r}, customer_id={self.customer_id!r}, "
            f"status={self.status!r}, total={self.total_amount!r})"
        )
