"""EnrichmentService request-reply broker tests — pure async, no Kafka."""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock

import pytest

from iris_service.messaging.dtos import (
    CustomerEnrichReply,
    CustomerEnrichRequest,
)
from iris_service.messaging.enrichment import (
    EnrichmentService,
    compute_enrichment,
)


def _request(id_: int = 1) -> CustomerEnrichRequest:
    return CustomerEnrichRequest(id=id_, name="Alice", email="alice@example.com")


# ── compute_enrichment (pure function) ───────────────────────────────────────


def test_compute_enrichment_builds_display_name() -> None:
    request = _request()
    reply = compute_enrichment(request)
    assert reply.id == 1
    assert reply.name == "Alice"
    assert reply.email == "alice@example.com"
    assert reply.display_name == "Alice <alice@example.com>"


# ── request_reply ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_request_reply_completes_when_reply_delivered() -> None:
    producer = AsyncMock()
    service = EnrichmentService(
        producer=producer,
        request_topic="req",
        reply_topic="rep",
    )

    # Start the request_reply coroutine, then deliver the reply once the
    # producer has been called (so the future is registered).
    task = asyncio.create_task(service.request_reply(_request(), timeout_s=2.0))
    # Yield once so the producer.send_and_wait awaits + registers correlation id.
    await asyncio.sleep(0)
    await asyncio.sleep(0)

    # Extract the correlation id from the producer call's headers
    assert producer.send_and_wait.await_count == 1
    kwargs = producer.send_and_wait.await_args.kwargs
    headers: list[tuple[str, bytes]] = kwargs["headers"]
    correlation_id = next(v.decode() for k, v in headers if k == "correlation-id")

    reply = CustomerEnrichReply(
        id=1,
        name="Alice",
        email="alice@example.com",
        display_name="Alice <alice@example.com>",
    )
    service.deliver_reply(correlation_id, reply)

    result = await task
    assert result.display_name == "Alice <alice@example.com>"


@pytest.mark.asyncio
async def test_request_reply_raises_timeout_when_no_reply() -> None:
    producer = AsyncMock()
    service = EnrichmentService(producer=producer, request_topic="req", reply_topic="rep")

    with pytest.raises(asyncio.TimeoutError):
        await service.request_reply(_request(), timeout_s=0.1)


@pytest.mark.asyncio
async def test_request_reply_cleans_up_pending_on_timeout() -> None:
    producer = AsyncMock()
    service = EnrichmentService(producer=producer, request_topic="req", reply_topic="rep")

    with pytest.raises(asyncio.TimeoutError):
        await service.request_reply(_request(), timeout_s=0.05)

    # Internal pending dict is empty after timeout — no leak
    assert service._pending == {}


@pytest.mark.asyncio
async def test_deliver_reply_unknown_correlation_id_is_silent() -> None:
    producer = AsyncMock()
    service = EnrichmentService(producer=producer, request_topic="req", reply_topic="rep")

    # No future registered — should not raise
    reply = CustomerEnrichReply(id=1, name="A", email="a@x.com", display_name="A <a@x.com>")
    service.deliver_reply("unknown-cid", reply)


# ── handle_request ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_handle_request_publishes_reply_with_correlation_id() -> None:
    producer = AsyncMock()
    service = EnrichmentService(producer=producer, request_topic="req", reply_topic="rep")

    await service.handle_request(_request(id_=42), correlation_id="cid-abc")

    producer.send_and_wait.assert_awaited_once()
    args, kwargs = producer.send_and_wait.call_args
    assert args[0] == "rep"  # reply topic
    assert kwargs["key"] == b"42"
    headers: list[tuple[str, Any]] = kwargs["headers"]
    assert ("correlation-id", b"cid-abc") in headers
    # Payload contains displayName in camelCase (by_alias=True)
    payload = kwargs["value"].decode()
    assert '"displayName"' in payload
    assert "Alice <alice@example.com>" in payload
