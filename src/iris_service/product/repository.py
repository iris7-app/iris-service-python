"""Product async repository — SQLAlchemy 2.x async session pattern.

Mirrors Java's `ProductRepository` (Spring Data JpaRepository) but
made async since the whole Python service is async-first (cf. ADR-0008).
"""

from __future__ import annotations

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from iris_service.product.models import Product


class ProductRepository:
    """Async DAO for `Product`. Stateless, takes the session per call."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def list_paginated(
        self,
        page: int,
        size: int,
        search: str | None = None,
    ) -> tuple[list[Product], int]:
        """Return (page_items, total_count) for the requested page.

        Page is 0-indexed (matches Java's Spring Data default). Size is
        clamped at the router level to avoid pathological queries.

        ``search`` is an optional case-insensitive substring filter on
        ``name`` + ``description``. Mirrors Java's
        :meth:`ProductRepository.search`. Whitespace-only / empty values
        are treated as ``None`` (no filter) so the SQL doesn't degrade
        to a no-op ``LIKE '%%'`` index scan.
        """
        offset = page * size
        normalised = search.strip() if search else None
        if normalised:
            pattern = f"%{normalised.lower()}%"
            where = or_(
                func.lower(Product.name).like(pattern),
                func.lower(Product.description).like(pattern),
            )
            items_stmt = select(Product).where(where).order_by(Product.id).offset(offset).limit(size)
            count_stmt = select(func.count()).select_from(Product).where(where)
        else:
            items_stmt = select(Product).order_by(Product.id).offset(offset).limit(size)
            count_stmt = select(func.count()).select_from(Product)
        items = (await self.session.execute(items_stmt)).scalars().all()
        total = (await self.session.execute(count_stmt)).scalar_one()
        return list(items), total

    async def get_by_id(self, product_id: int) -> Product | None:
        return await self.session.get(Product, product_id)

    async def find_by_name(self, name: str) -> Product | None:
        """Cheap pre-create dup check — avoid catching IntegrityError."""
        stmt = select(Product).where(Product.name == name)
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def add(self, product: Product) -> Product:
        self.session.add(product)
        await self.session.flush()
        await self.session.refresh(product)
        return product

    async def update(
        self,
        product_id: int,
        name: str,
        description: str | None,
        unit_price: object,
        stock_quantity: int,
    ) -> Product | None:
        """Replace mutable fields. Returns the updated entity, or None if absent.

        Per shared ADR-0059, mutating `unit_price` here MUST NOT propagate
        to existing `OrderLine.unit_price_at_order` (snapshots stay
        immutable). The repo only writes Product columns ; OrderLines are
        untouched.
        """
        product = await self.session.get(Product, product_id)
        if product is None:
            return None
        product.name = name
        # description is typed Mapped[str] but nullable=True at the DB level
        # (SQLAlchemy 2.0.36 + Python 3.14 incompat workaround — see models.py).
        product.description = description  # type: ignore[assignment]
        product.unit_price = unit_price  # type: ignore[assignment]
        product.stock_quantity = stock_quantity
        await self.session.flush()
        await self.session.refresh(product)
        return product

    async def delete(self, product_id: int) -> bool:
        product = await self.session.get(Product, product_id)
        if product is None:
            return False
        await self.session.delete(product)
        await self.session.flush()
        return True
