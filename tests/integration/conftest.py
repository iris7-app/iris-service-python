"""Integration test fixtures — testcontainers Postgres + Kafka.

These fixtures spin up real container backends for end-to-end coverage
of the lifecycle code that unit tests can't reach :
- db/base.py engine + session factory (real async DB connection).
- messaging/kafka_client.py producer + 2 consumer loops (real broker).

Cost : ~10s per container pull (cached after first run), ~3s per
container start. Tests are marked `@pytest.mark.integration` and skipped
by default in `unit` runs ; opt in with `pytest -m integration`.

CI : runs on every MR via the dedicated `integration-tests` GitLab CI
job (separate stage, parallel to lint + unit).
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from testcontainers.kafka import KafkaContainer
from testcontainers.postgres import PostgresContainer

from iris_service.db.base import Base


@pytest.fixture(scope="session")
def postgres_container() -> Iterator[PostgresContainer]:
    """Real Postgres in a Docker container — session-scoped (one start per test run)."""
    with PostgresContainer("postgres:16.6-alpine") as container:
        yield container


@pytest_asyncio.fixture
async def postgres_session(
    postgres_container: PostgresContainer,
) -> AsyncIterator[AsyncSession]:
    """AsyncSession against the real Postgres container — fresh schema per test."""
    sync_url = postgres_container.get_connection_url()
    # testcontainers returns sqlalchemy-style URL with psycopg2 driver — swap to asyncpg.
    async_url = sync_url.replace("postgresql+psycopg2", "postgresql+asyncpg")
    engine = create_async_engine(async_url, echo=False)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)

    factory = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        yield session

    await engine.dispose()


@pytest.fixture(scope="session")
def kafka_container() -> Iterator[KafkaContainer]:
    """Real Kafka in a Docker container — session-scoped."""
    with KafkaContainer("confluentinc/cp-kafka:7.6.1") as container:
        yield container


@pytest.fixture
def kafka_bootstrap(kafka_container: KafkaContainer) -> str:
    """Bootstrap servers string for the running Kafka container."""
    return str(kafka_container.get_bootstrap_server())
