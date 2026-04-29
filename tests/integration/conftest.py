"""Integration test fixtures — Postgres + Kafka, env-driven OR testcontainers.

These fixtures spin up real backends for end-to-end coverage of the
lifecycle code that unit tests can't reach :
- db/base.py engine + session factory (real async DB connection).
- messaging/kafka_client.py producer + 2 consumer loops (real broker).

Two execution modes — picked automatically per env var presence :

1. **GitLab CI (services-driven)** — when ``IRIS_DB__HOST`` is set, the
   fixture builds the URL from ``IRIS_DB__{HOST,PORT,USER,PASSWORD,NAME}``
   env vars provided by the GitLab ``services:`` block. Same for Kafka :
   when ``IRIS_KAFKA_BOOTSTRAP_SERVERS`` is set, it's used verbatim. No
   testcontainers spawned, no Docker socket required.

2. **Local dev (testcontainers fallback)** — when the env vars are
   missing, the fixture spawns a Postgres / Kafka container via
   testcontainers as before. Same UX for ``pytest -m integration``
   on the laptop.

Why this design : the macbook-local GitLab runner has a network bridge
issue that prevented testcontainers' spawned containers from being
reachable from the job container (172.17.0.1:NNNN → connection refused,
0/23 consecutive green runs on main 2026-04-27). GitLab ``services:``
sidesteps the bridge entirely — services share the job's network
namespace and are reachable via their alias hostname.

Test API stays unchanged : ``postgres_session`` (AsyncSession) and
``kafka_bootstrap`` (str) keep the same names and shapes ; only the
backing source flips at fixture-setup time.

Cost : ~1s per fixture start in CI mode (env-only) vs ~10-15s in
testcontainers mode (image pull + container init). Tests are marked
``@pytest.mark.integration`` and skipped by default in unit runs ;
opt in with ``pytest -m integration``.

CI : runs on every MR via the dedicated ``integration-tests`` GitLab CI
job (separate stage, parallel to lint + unit).
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator, Iterator

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from iris_service.db.base import Base


@pytest.fixture(scope="session")
def postgres_url() -> Iterator[str]:
    """Sync SQLAlchemy Postgres URL — env-driven in CI, testcontainer locally.

    Returns a ``postgresql+psycopg2://...`` URL ; consumers swap the
    driver to ``+asyncpg`` themselves (the historical contract from
    when this fixture returned a ``PostgresContainer`` instance).
    """
    if host := os.environ.get("IRIS_DB__HOST"):
        # CI mode : GitLab ``services:`` block exposes Postgres at
        # ``$IRIS_DB__HOST`` (typically the service alias, ``postgres``).
        # The ``services:`` block waits for a TCP connection on the
        # service port before starting the job, so no extra readiness
        # poll is needed here.
        port = os.environ.get("IRIS_DB__PORT", "5432")
        user = os.environ.get("IRIS_DB__USER", "iris")
        password = os.environ.get("IRIS_DB__PASSWORD", "iris")
        name = os.environ.get("IRIS_DB__NAME", "iris")
        yield f"postgresql+psycopg2://{user}:{password}@{host}:{port}/{name}"
        return

    # Local fallback : import + start a testcontainer. Lazy import keeps
    # the testcontainers dependency optional in CI mode (the ``services:``
    # block doesn't need it).
    from testcontainers.postgres import PostgresContainer

    with PostgresContainer("postgres:16.6-alpine") as container:
        yield container.get_connection_url()


@pytest_asyncio.fixture
async def postgres_session(postgres_url: str) -> AsyncIterator[AsyncSession]:
    """AsyncSession against Postgres — fresh schema per test.

    Drops + recreates ``Base.metadata`` at fixture setup so each test
    starts from a clean slate. Engine is per-test (not session-scoped)
    because asyncio event loops can't be shared between tests cleanly.
    """
    # The fixture above returns ``postgresql+psycopg2://...`` for
    # historical compatibility ; swap to asyncpg here.
    async_url = postgres_url.replace("postgresql+psycopg2", "postgresql+asyncpg")
    engine = create_async_engine(async_url, echo=False)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)

    factory = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        yield session

    await engine.dispose()


@pytest.fixture(scope="session")
def kafka_bootstrap() -> Iterator[str]:
    """Kafka bootstrap servers string — env-driven in CI, testcontainer locally.

    Returns the ``host:port`` string the AIOKafkaProducer / Consumer
    take as ``bootstrap_servers=`` argument.
    """
    if env_bootstrap := os.environ.get("IRIS_KAFKA_BOOTSTRAP_SERVERS"):
        # CI mode : GitLab ``services:`` exposes Kafka via its alias
        # (``kafka``). Bitnami's ``bitnami/kafka:3.7`` image runs in
        # KRaft mode (no zookeeper) and listens on 9092.
        yield env_bootstrap
        return

    # Local fallback : testcontainer with the historical
    # ``confluentinc/cp-kafka`` image. Lazy import keeps the
    # testcontainers dep optional in CI.
    from testcontainers.kafka import KafkaContainer

    with KafkaContainer("confluentinc/cp-kafka:7.6.1") as container:
        yield str(container.get_bootstrap_server())
