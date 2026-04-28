"""Shared fixtures for the MCP unit tests.

In-memory SQLite session + a Deps factory bound to that session.
Keeps each test isolated (fresh schema per test) without Docker.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest_asyncio
from fastapi import FastAPI
from prometheus_client import CollectorRegistry
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from iris_service.config.settings import get_settings
from iris_service.db.base import Base
from iris_service.mcp.metrics_registry import MetricsRegistryReader
from iris_service.mcp.ring_buffer import RingBufferHandler
from iris_service.mcp.tools import Deps, reset_idempotency_cache


@pytest_asyncio.fixture
async def sqlite_engine() -> AsyncIterator[None]:
    """In-memory SQLite engine bound for the duration of one test.

    Uses a SHARED in-memory DB (`sqlite+aiosqlite:///file::memory:?cache=shared`
    via `?uri=true`) so multiple sessions opened by the tools see the
    same data — required by the tools' `async with await deps.session_factory()`
    pattern (each call opens a fresh AsyncSession).
    """
    # File-shared in-memory DB so sessions stack
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        echo=False,
        connect_args={"check_same_thread": False},
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    # Stash the factory globally for the tests.
    factory = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)
    _state["factory"] = factory
    _state["engine"] = engine
    try:
        yield
    finally:
        await engine.dispose()
        _state.clear()


_state: dict[str, object] = {}


async def _open_session() -> AsyncSession:
    factory = _state["factory"]
    assert callable(factory)
    return factory()  # type: ignore[no-any-return,operator]


@pytest_asyncio.fixture
async def deps(sqlite_engine: None) -> Deps:
    """Deps wired to the in-memory SQLite engine + isolated Prometheus registry."""
    reset_idempotency_cache()
    return Deps(
        app=FastAPI(title="test-app", version="9.9.9", description="mcp tests"),
        settings=get_settings(),
        session_factory=_open_session,
        ring_buffer=RingBufferHandler(capacity=100),
        metrics_reader=MetricsRegistryReader(CollectorRegistry()),
    )
