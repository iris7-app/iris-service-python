"""Pytest fixtures shared across all tests.

Strategy : SQLite in-memory for unit tests (fast, no Docker), Postgres
testcontainer for integration tests (real DB behavior). Switching is via
the `db_session` fixture variant.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import fakeredis.aioredis
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from iris_service.app import create_app
from iris_service.db.base import Base, get_db_session
from iris_service.integration.redis_client import get_redis


@pytest_asyncio.fixture
async def db_session() -> AsyncIterator[AsyncSession]:
    """In-memory SQLite session — fast, isolated, no Docker.

    Each test gets a fresh schema (CREATE TABLE on connect, DROP after).
    """
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    factory = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        yield session

    await engine.dispose()


@pytest_asyncio.fixture
async def fake_redis() -> AsyncIterator[Redis]:
    """In-memory fake Redis — fakeredis emulates the real protocol async."""
    client = fakeredis.aioredis.FakeRedis(decode_responses=True)
    yield client
    await client.aclose()


@pytest_asyncio.fixture
async def app(db_session: AsyncSession, fake_redis: Redis) -> AsyncIterator[FastAPI]:
    """FastAPI app instance with test DB + Redis injected via DI overrides."""
    app = create_app()

    async def override_db_session() -> AsyncIterator[AsyncSession]:
        # Reuse the test's session — same transaction across the request.
        yield db_session

    def override_redis() -> Redis:
        return fake_redis

    app.dependency_overrides[get_db_session] = override_db_session
    app.dependency_overrides[get_redis] = override_redis
    yield app
    app.dependency_overrides.clear()


@pytest_asyncio.fixture
async def client(app: FastAPI) -> AsyncIterator[AsyncClient]:
    """httpx async client bound to the test app — no real network."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
