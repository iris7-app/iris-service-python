"""Repository tests against a real Postgres container.

Catches Postgres-specific behaviour the SQLite unit tests miss :
- Server defaults (sa.func.now()) populate created_at automatically.
- UNIQUE constraint violations raise IntegrityError on commit.
- Case-insensitive search works the same way (LOWER + LIKE).
"""

from __future__ import annotations

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from iris_service.customer.repository import CustomerRepository

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_create_persists_to_postgres(postgres_session: AsyncSession) -> None:
    customer = await CustomerRepository.create(postgres_session, name="Alice", email="alice@example.com")
    await postgres_session.commit()
    assert customer.id is not None
    assert customer.created_at is not None  # server-default sa.func.now() populated


@pytest.mark.asyncio
async def test_unique_email_violation_raises_integrity_error(
    postgres_session: AsyncSession,
) -> None:
    """Postgres raises IntegrityError on the INSERT itself (asyncpg),
    not deferred to commit — repository.create()'s session.flush() is
    where the violation surfaces."""
    await CustomerRepository.create(postgres_session, name="Bob", email="dup@example.com")
    await postgres_session.commit()
    with pytest.raises(IntegrityError):
        await CustomerRepository.create(postgres_session, name="Bob2", email="dup@example.com")


@pytest.mark.asyncio
async def test_search_case_insensitive_on_postgres(
    postgres_session: AsyncSession,
) -> None:
    await CustomerRepository.create(postgres_session, name="Alice", email="a@x.com")
    await CustomerRepository.create(postgres_session, name="Bob", email="b@x.com")
    await postgres_session.commit()
    rows, total = await CustomerRepository.find_all(postgres_session, search="ALICE")
    assert total == 1
    assert rows[0].name == "Alice"
