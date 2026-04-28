"""Coverage uplift — targeted tests for previously-uncovered branches.

Per ADR-0007 §3 (target 90 %+) — this file batches small targeted tests
that close the obvious gaps surfaced by the cov report :
- auth/passwords.py : malformed-hash branch (the silent ValueError fallback).
- auth/cleanup.py : start_scheduler + stop_scheduler (idempotent path).
- integration/redis_client.py : get_redis singleton + close_redis lifecycle.
- messaging/customer_event.py : producer-None + producer-raises branches.
- db/base.py : reset_engine on a non-None engine + double-call idempotence.

Each test is small + isolated. No real services started — pure unit tests.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from iris_service.auth import cleanup
from iris_service.auth.passwords import verify_password
from iris_service.db import base as db_base
from iris_service.integration import redis_client
from iris_service.messaging.customer_event import (
    CustomerCreatedEvent,
    publish_customer_created,
)

# ── auth/passwords.py — malformed-hash branch ─────────────────────────────────


def test_verify_password_returns_false_on_malformed_hash() -> None:
    """bcrypt.checkpw raises ValueError on bad hash format → caught → False."""
    assert verify_password("anything", "not-a-bcrypt-hash") is False


def test_verify_password_returns_false_on_empty_hash() -> None:
    """Edge case : empty hash string → ValueError → False (no crash)."""
    assert verify_password("anything", "") is False


# ── auth/cleanup.py — scheduler lifecycle ─────────────────────────────────────


@pytest.mark.asyncio
async def test_start_then_stop_scheduler_is_idempotent() -> None:
    """start → stop → start → stop sequence must not crash and leave
    the module-level _scheduler singleton in a clean state.

    Async wrapper because APScheduler's AsyncIOScheduler requires a running
    event loop to bind to ; the FastAPI lifespan provides it in production,
    pytest-asyncio provides it here.
    """
    # Ensure clean baseline (other tests may have run first).
    cleanup.stop_scheduler()
    assert cleanup._scheduler is None

    cleanup.start_scheduler()
    assert cleanup._scheduler is not None

    # Second start is a no-op (returns early).
    cleanup.start_scheduler()
    assert cleanup._scheduler is not None

    cleanup.stop_scheduler()
    assert cleanup._scheduler is None

    # Second stop is also a no-op.
    cleanup.stop_scheduler()
    assert cleanup._scheduler is None


# ── integration/redis_client.py — singleton lifecycle ─────────────────────────


@pytest.mark.asyncio
async def test_get_redis_returns_singleton_then_close() -> None:
    """get_redis() returns the same instance on repeat calls, close() clears it."""
    # Reset baseline (other tests may have populated the singleton).
    await redis_client.close_redis()
    assert redis_client._client is None

    instance1 = redis_client.get_redis()
    instance2 = redis_client.get_redis()
    assert instance1 is instance2

    # close drops the singleton. Wrap in try/except : if Redis isn't reachable
    # locally the close still nulls _client.
    await redis_client.close_redis()
    assert redis_client._client is None

    # Calling close twice is a no-op (idempotent).
    await redis_client.close_redis()
    assert redis_client._client is None


# ── messaging/customer_event.py — degraded paths ──────────────────────────────


@pytest.mark.asyncio
async def test_publish_customer_created_returns_false_when_producer_is_none() -> None:
    """The fire-and-forget publish must return False (not raise) when the
    Kafka producer singleton hasn't been initialised — happens when Kafka
    is down at app start (best-effort startup).
    """
    event = CustomerCreatedEvent(id=1, name="Alice", email="alice@example.com")
    result = await publish_customer_created(producer=None, topic="customer.created", event=event)
    assert result is False


@pytest.mark.asyncio
async def test_publish_customer_created_returns_false_when_producer_raises() -> None:
    """If the broker rejects the publish (network error, topic missing, etc.),
    the function must log + return False, NEVER let the exception bubble up
    into the calling HTTP handler (which would 500 the create flow)."""
    producer = MagicMock()
    producer.send_and_wait = AsyncMock(side_effect=RuntimeError("simulated broker outage"))

    event = CustomerCreatedEvent(id=2, name="Bob", email="bob@example.com")
    result = await publish_customer_created(producer=producer, topic="customer.created", event=event)
    assert result is False
    # Confirm the call was actually attempted.
    producer.send_and_wait.assert_awaited_once()


@pytest.mark.asyncio
async def test_publish_customer_created_returns_true_on_success() -> None:
    """Happy path : producer.send_and_wait returns normally → True."""
    producer = MagicMock()
    producer.send_and_wait = AsyncMock(return_value=None)

    event = CustomerCreatedEvent(id=3, name="Carol", email="carol@example.com")
    result = await publish_customer_created(producer=producer, topic="customer.created", event=event)
    assert result is True
    producer.send_and_wait.assert_awaited_once()


# ── db/base.py — engine lifecycle ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_reset_engine_on_initialised_engine() -> None:
    """get_engine() builds a singleton ; reset_engine() disposes + clears it.

    Note : we do NOT start a real Postgres connection — get_engine() builds
    the engine LAZILY (no connection until first query). Disposing an idle
    engine is safe.
    """
    # Build the engine.
    engine = db_base.get_engine()
    assert engine is not None
    assert db_base._engine is engine

    # Disposing it nulls the module-level singleton.
    await db_base.reset_engine()
    assert db_base._engine is None
    assert db_base._session_factory is None

    # Calling again with no engine is safe (idempotent — no AttributeError).
    await db_base.reset_engine()
    assert db_base._engine is None


def test_get_session_factory_lazy_singleton() -> None:
    """get_session_factory() returns the same factory across calls."""
    factory1 = db_base.get_session_factory()
    factory2 = db_base.get_session_factory()
    assert factory1 is factory2


# ── enrichment_router.py — lazy DI factory singletons ────────────────────────


def test_get_todo_service_returns_singleton() -> None:
    """The Depends provider for TodoService returns the same lazy singleton
    across calls — same httpx pool reused. Reset baseline to exercise both
    branches (None → instantiate, not-None → return).
    """
    from iris_service.customer import enrichment_router

    enrichment_router._todo_service = None
    instance1 = enrichment_router.get_todo_service()
    instance2 = enrichment_router.get_todo_service()
    assert instance1 is instance2


def test_get_bio_service_returns_singleton() -> None:
    """Same singleton contract for BioService (Ollama client)."""
    from iris_service.customer import enrichment_router

    enrichment_router._bio_service = None
    instance1 = enrichment_router.get_bio_service()
    instance2 = enrichment_router.get_bio_service()
    assert instance1 is instance2
