"""OrderLine ORM entity — mirrors Java's `OrderLine.java`.

Stored in `order_line` table. Carries quantity + price snapshot + status.
Status independent of parent Order (per-line PENDING/SHIPPED/REFUNDED).
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum

from sqlalchemy import DateTime, ForeignKey, Integer, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column

from iris_service.db.base import Base


class OrderLineStatus(StrEnum):
    """Per-line status (independent of parent Order status)."""

    PENDING = "PENDING"
    SHIPPED = "SHIPPED"
    REFUNDED = "REFUNDED"

    def can_transition_to(self, target: OrderLineStatus | None) -> bool:
        """Pure state-machine check — is the transition self → target allowed ?

        Per shared ADR-0059, valid graph : PENDING → SHIPPED → REFUNDED.
        No skip (cannot REFUND a PENDING line — must SHIP first ; audit
        requirement). Self-transitions allowed. REFUNDED is terminal.
        """
        if target is None:
            return False
        if self == target:
            return True
        match self:
            case OrderLineStatus.PENDING:
                return target == OrderLineStatus.SHIPPED
            case OrderLineStatus.SHIPPED:
                return target == OrderLineStatus.REFUNDED
            case OrderLineStatus.REFUNDED:
                return False


class OrderLine(Base):
    """`order_line` table."""

    __tablename__ = "order_line"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    order_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("orders.id", ondelete="CASCADE", name="fk_order_line_order"),
        nullable=False,
        index=True,
    )
    product_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("product.id", ondelete="RESTRICT", name="fk_order_line_product"),
        nullable=False,
        index=True,
    )
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    unit_price_at_order: Mapped[Decimal] = mapped_column(
        Numeric(precision=12, scale=2),
        nullable=False,
    )
    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default=OrderLineStatus.PENDING.value,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
    )

    def __repr__(self) -> str:
        return (
            f"OrderLine(id={self.id!r}, order_id={self.order_id!r}, "
            f"product_id={self.product_id!r}, quantity={self.quantity!r})"
        )
