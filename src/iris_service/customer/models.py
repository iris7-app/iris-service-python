"""Customer ORM entity.

Mirrors `Customer.java` from the Java sibling : same column types, same
constraints. Uses SQLAlchemy 2.x typed mapped columns (Mapped[T]) for
mypy-friendly definitions.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import DateTime, String
from sqlalchemy.orm import Mapped, mapped_column

from iris_service.db.base import Base


class Customer(Base):
    """`customer` table — the canonical customer entity."""

    __tablename__ = "customer"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    email: Mapped[str] = mapped_column(String(255), nullable=False, unique=True, index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
    )

    def __repr__(self) -> str:
        return f"Customer(id={self.id!r}, name={self.name!r}, email={self.email!r})"
