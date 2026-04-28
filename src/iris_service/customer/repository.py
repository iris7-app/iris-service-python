"""Customer repository — data access layer.

Mirrors Spring Data's `JpaRepository<Customer, Long>` + custom query methods.
SQLAlchemy 2.x async API : awaitable session.execute() + select() statements.

Conventions :
- All methods take `AsyncSession` as the first arg (DI from FastAPI).
- Returns ORM entities (`Customer`) ; conversion to DTOs happens in the
  service layer (one layer up).
- No business logic here ; pure CRUD + queries.
"""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.exc import NoResultFound
from sqlalchemy.ext.asyncio import AsyncSession

from iris_service.customer.models import Customer


class CustomerRepository:
    """Data access for the `customer` table."""

    @staticmethod
    async def find_all(
        session: AsyncSession,
        *,
        page: int = 0,
        size: int = 20,
        search: str | None = None,
    ) -> tuple[list[Customer], int]:
        """Paginated find — returns (rows, total_count).

        `search` filters on name/email (case-insensitive, partial match).
        """
        # Build base query
        stmt = select(Customer)
        count_stmt = select(func.count()).select_from(Customer)
        if search:
            pattern = f"%{search.lower()}%"
            stmt = stmt.where(func.lower(Customer.name).like(pattern) | func.lower(Customer.email).like(pattern))
            count_stmt = count_stmt.where(
                func.lower(Customer.name).like(pattern) | func.lower(Customer.email).like(pattern)
            )

        # Count for pagination metadata
        total = (await session.execute(count_stmt)).scalar_one()

        # Page slice
        stmt = stmt.order_by(Customer.id).offset(page * size).limit(size)
        rows = list((await session.execute(stmt)).scalars().all())
        return rows, total

    @staticmethod
    async def find_by_id(session: AsyncSession, id_: int) -> Customer | None:
        """Returns the customer or None."""
        return await session.get(Customer, id_)

    @staticmethod
    async def find_by_id_or_raise(session: AsyncSession, id_: int) -> Customer:
        """Returns the customer or raises NoResultFound (404 mapped at API layer)."""
        customer = await session.get(Customer, id_)
        if customer is None:
            raise NoResultFound(f"Customer not found: id={id_}")
        return customer

    @staticmethod
    async def create(session: AsyncSession, *, name: str, email: str) -> Customer:
        """Insert a new customer ; caller commits."""
        customer = Customer(name=name, email=email)
        session.add(customer)
        await session.flush()  # populate the auto-generated id
        return customer

    @staticmethod
    async def update(
        session: AsyncSession,
        id_: int,
        *,
        name: str,
        email: str,
    ) -> Customer:
        """Replace name + email of existing customer."""
        customer = await CustomerRepository.find_by_id_or_raise(session, id_)
        customer.name = name
        customer.email = email
        await session.flush()
        return customer

    @staticmethod
    async def patch(
        session: AsyncSession,
        id_: int,
        *,
        name: str | None = None,
        email: str | None = None,
    ) -> Customer:
        """Partial update — only fields explicitly passed are mutated."""
        customer = await CustomerRepository.find_by_id_or_raise(session, id_)
        if name is not None:
            customer.name = name
        if email is not None:
            customer.email = email
        await session.flush()
        return customer

    @staticmethod
    async def delete(session: AsyncSession, id_: int) -> None:
        """Delete by id ; raises NoResultFound if missing."""
        customer = await CustomerRepository.find_by_id_or_raise(session, id_)
        await session.delete(customer)
        await session.flush()
