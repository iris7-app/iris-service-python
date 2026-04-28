"""Product ORM entity — mirrors Java's `Product.java`.

SQLAlchemy 2.x typed `Mapped[T]` for mypy-friendly definitions. `Decimal`
(not `float`) for `unit_price` to preserve exact precision (matches
Postgres NUMERIC(12,2) and Java's BigDecimal).
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import DateTime, Integer, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from iris_service.db.base import Base


class Product(Base):
    """`product` table — entity in the catalogue.

    Invariants (enforced at DB + at API edge) :
    - `unit_price >= 0`
    - `stock_quantity >= 0`
    - `name` unique

    `updated_at` is application-managed via `repository.update()` (no DB
    trigger — matches the Java side's `@PreUpdate` choice for symmetry).
    """

    __tablename__ = "product"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False, unique=True, index=True)
    # Note : description is nullable but typed as Mapped[str] not Mapped[str | None]
    # because of a SQLAlchemy 2.0.36 + Python 3.14 incompat with Union types in
    # Mapped[]. Runtime allows None ; callers should treat as `str | None`.
    description: Mapped[str] = mapped_column(Text, nullable=True)
    unit_price: Mapped[Decimal] = mapped_column(Numeric(precision=12, scale=2), nullable=False)
    stock_quantity: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
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
            f"Product(id={self.id!r}, name={self.name!r}, "
            f"unit_price={self.unit_price!r}, stock_quantity={self.stock_quantity!r})"
        )
