"""Refresh token cleanup tests — direct against in-memory SQLite."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from iris_service.auth.cleanup import cleanup_refresh_tokens
from iris_service.auth.models import RefreshToken
from iris_service.db.base import reset_engine


@pytest.fixture(autouse=True)
async def _swap_factory(db_session: AsyncSession, monkeypatch):
    """Make cleanup_refresh_tokens reuse the per-test SQLite session.

    cleanup.py calls ``get_session_factory()`` directly (not through DI),
    so we monkeypatch it to return a factory that hands back the SAME
    test session each time. Because cleanup uses ``async with factory()
    as session``, we wrap the test session in a context manager that
    is a no-op on enter/exit.
    """

    class _SessionContext:
        def __init__(self, session: AsyncSession) -> None:
            self._session = session

        async def __aenter__(self) -> AsyncSession:
            return self._session

        async def __aexit__(self, *args: object) -> None:
            pass

    def fake_factory():
        return _SessionContext(db_session)

    monkeypatch.setattr(
        "iris_service.auth.cleanup.get_session_factory",
        lambda: fake_factory,
    )
    yield


@pytest.mark.asyncio
async def test_cleanup_deletes_revoked_tokens(db_session: AsyncSession) -> None:
    db_session.add(
        RefreshToken(
            token="revoked-1",
            username="alice",
            expires_at=datetime.now(UTC) + timedelta(days=10),
            revoked=True,
        )
    )
    db_session.add(
        RefreshToken(
            token="active-1",
            username="alice",
            expires_at=datetime.now(UTC) + timedelta(days=10),
            revoked=False,
        )
    )
    await db_session.commit()

    deleted = await cleanup_refresh_tokens()
    assert deleted == 1

    remaining = (await db_session.execute(select(RefreshToken))).scalars().all()
    assert len(remaining) == 1
    assert remaining[0].token == "active-1"


@pytest.mark.asyncio
async def test_cleanup_deletes_expired_tokens(db_session: AsyncSession) -> None:
    db_session.add(
        RefreshToken(
            token="expired-1",
            username="bob",
            expires_at=datetime.now(UTC) - timedelta(days=1),
            revoked=False,
        )
    )
    db_session.add(
        RefreshToken(
            token="active-2",
            username="bob",
            expires_at=datetime.now(UTC) + timedelta(days=10),
            revoked=False,
        )
    )
    await db_session.commit()

    deleted = await cleanup_refresh_tokens()
    assert deleted == 1

    remaining = (await db_session.execute(select(RefreshToken))).scalars().all()
    assert len(remaining) == 1
    assert remaining[0].token == "active-2"


@pytest.mark.asyncio
async def test_cleanup_deletes_both_revoked_and_expired(
    db_session: AsyncSession,
) -> None:
    db_session.add(
        RefreshToken(
            token="revoked",
            username="carol",
            expires_at=datetime.now(UTC) + timedelta(days=10),
            revoked=True,
        )
    )
    db_session.add(
        RefreshToken(
            token="expired",
            username="carol",
            expires_at=datetime.now(UTC) - timedelta(days=1),
            revoked=False,
        )
    )
    db_session.add(
        RefreshToken(
            token="active",
            username="carol",
            expires_at=datetime.now(UTC) + timedelta(days=10),
            revoked=False,
        )
    )
    await db_session.commit()

    deleted = await cleanup_refresh_tokens()
    assert deleted == 2

    remaining = (await db_session.execute(select(RefreshToken))).scalars().all()
    assert len(remaining) == 1
    assert remaining[0].token == "active"


@pytest.mark.asyncio
async def test_cleanup_idempotent_on_empty_table(db_session: AsyncSession) -> None:
    deleted = await cleanup_refresh_tokens()
    assert deleted == 0
    deleted = await cleanup_refresh_tokens()
    assert deleted == 0


@pytest.mark.asyncio
async def test_reset_engine_does_not_break_anything() -> None:
    """Sanity : import + call reset_engine without prior init."""
    await reset_engine()
