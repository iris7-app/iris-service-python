"""Order async repository — mirror of Java's OrderRepository."""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from iris_service.order.models import Order
from iris_service.order.order_line_models import OrderLine


class OrderRepository:
    """Async DAO for `Order`."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def list_paginated(self, page: int, size: int) -> tuple[list[Order], int]:
        offset = page * size
        items_stmt = select(Order).order_by(Order.id.desc()).offset(offset).limit(size)
        count_stmt = select(func.count()).select_from(Order)
        items = (await self.session.execute(items_stmt)).scalars().all()
        total = (await self.session.execute(count_stmt)).scalar_one()
        return list(items), total

    async def get_by_id(self, order_id: int) -> Order | None:
        return await self.session.get(Order, order_id)

    async def add(self, order: Order) -> Order:
        self.session.add(order)
        await self.session.flush()
        await self.session.refresh(order)
        return order

    async def delete(self, order_id: int) -> bool:
        order = await self.session.get(Order, order_id)
        if order is None:
            return False
        await self.session.delete(order)
        await self.session.flush()
        return True

    async def list_by_product_id(
        self,
        product_id: int,
        page: int,
        size: int,
    ) -> tuple[list[Order], int]:
        """All orders that contain at least one line for ``product_id``.

        Replaces the UI-side fan-out previously implemented as "list 50
        recent orders + filter client-side" in
        ``ProductDetailComponent#findConsumerOrders``. Server-side filter
        keeps the query bounded regardless of order volume.

        Uses ``DISTINCT`` since a single order may contain multiple lines
        for the same product (rare — re-order scenario where the user
        added the same product twice with different quantities). Without
        ``DISTINCT`` that order would appear twice in the page.

        Order is by ``Order.id`` DESC (newest first) — same default the
        plain ``list_paginated`` uses, so the two endpoints feel
        consistent on the consumer side.
        """
        offset = page * size
        items_stmt = (
            select(Order)
            .join(OrderLine, OrderLine.order_id == Order.id)
            .where(OrderLine.product_id == product_id)
            .order_by(Order.id.desc())
            .offset(offset)
            .limit(size)
            .distinct()
        )
        count_stmt = (
            select(func.count(func.distinct(Order.id)))
            .select_from(Order)
            .join(OrderLine, OrderLine.order_id == Order.id)
            .where(OrderLine.product_id == product_id)
        )
        items = (await self.session.execute(items_stmt)).scalars().all()
        total = (await self.session.execute(count_stmt)).scalar_one()
        return list(items), total
