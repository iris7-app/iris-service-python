"""CustomerRepository tests against in-memory SQLite — direct unit tests.

Cover repository edge cases that are hard to reach via HTTP integration
tests : pagination boundaries, search filter, None checks on find_by_id,
patch with partial fields.
"""

from __future__ import annotations

import pytest
from sqlalchemy.exc import NoResultFound
from sqlalchemy.ext.asyncio import AsyncSession

from iris_service.customer.repository import CustomerRepository


@pytest.mark.asyncio
async def test_find_by_id_returns_none_when_missing(db_session: AsyncSession) -> None:
    result = await CustomerRepository.find_by_id(db_session, 999)
    assert result is None


@pytest.mark.asyncio
async def test_find_by_id_or_raise_raises_when_missing(db_session: AsyncSession) -> None:
    with pytest.raises(NoResultFound, match="id=999"):
        await CustomerRepository.find_by_id_or_raise(db_session, 999)


@pytest.mark.asyncio
async def test_find_all_pagination(db_session: AsyncSession) -> None:
    # Seed 25 customers
    for i in range(25):
        await CustomerRepository.create(db_session, name=f"User{i}", email=f"u{i}@x.com")
    await db_session.commit()

    page0, total = await CustomerRepository.find_all(db_session, page=0, size=10)
    assert total == 25
    assert len(page0) == 10
    assert page0[0].name == "User0"

    page2, total = await CustomerRepository.find_all(db_session, page=2, size=10)
    assert total == 25
    assert len(page2) == 5  # remainder
    assert page2[0].name == "User20"


@pytest.mark.asyncio
async def test_find_all_search_filters_by_name_or_email(
    db_session: AsyncSession,
) -> None:
    await CustomerRepository.create(db_session, name="Alice", email="alice@x.com")
    await CustomerRepository.create(db_session, name="Bob", email="bob@y.com")
    await CustomerRepository.create(db_session, name="Carol", email="carol@x.com")
    await db_session.commit()

    # Match by partial email domain (case-insensitive)
    rows, total = await CustomerRepository.find_all(db_session, search="@X.com")
    assert total == 2
    names = {r.name for r in rows}
    assert names == {"Alice", "Carol"}

    # Match by partial name
    rows, total = await CustomerRepository.find_all(db_session, search="bob")
    assert total == 1
    assert rows[0].email == "bob@y.com"

    # No match
    rows, total = await CustomerRepository.find_all(db_session, search="zzz")
    assert total == 0
    assert rows == []


@pytest.mark.asyncio
async def test_patch_with_no_fields_is_noop(db_session: AsyncSession) -> None:
    created = await CustomerRepository.create(db_session, name="Original", email="original@x.com")
    await db_session.commit()
    patched = await CustomerRepository.patch(db_session, created.id, name=None, email=None)
    assert patched.name == "Original"
    assert patched.email == "original@x.com"


@pytest.mark.asyncio
async def test_patch_only_name(db_session: AsyncSession) -> None:
    created = await CustomerRepository.create(db_session, name="OldName", email="keep@x.com")
    await db_session.commit()
    patched = await CustomerRepository.patch(db_session, created.id, name="NewName", email=None)
    assert patched.name == "NewName"
    assert patched.email == "keep@x.com"


@pytest.mark.asyncio
async def test_update_replaces_both_fields(db_session: AsyncSession) -> None:
    created = await CustomerRepository.create(db_session, name="A", email="a@x.com")
    await db_session.commit()
    updated = await CustomerRepository.update(db_session, created.id, name="B", email="b@x.com")
    assert updated.name == "B"
    assert updated.email == "b@x.com"


@pytest.mark.asyncio
async def test_update_raises_when_missing(db_session: AsyncSession) -> None:
    with pytest.raises(NoResultFound):
        await CustomerRepository.update(db_session, 999, name="x", email="x@x.com")


@pytest.mark.asyncio
async def test_delete_raises_when_missing(db_session: AsyncSession) -> None:
    with pytest.raises(NoResultFound):
        await CustomerRepository.delete(db_session, 999)


@pytest.mark.asyncio
async def test_delete_removes_existing(db_session: AsyncSession) -> None:
    created = await CustomerRepository.create(db_session, name="ToDelete", email="del@x.com")
    await db_session.commit()
    await CustomerRepository.delete(db_session, created.id)
    assert await CustomerRepository.find_by_id(db_session, created.id) is None
