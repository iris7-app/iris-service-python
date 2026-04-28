"""RecentCustomerBuffer + /customers/recent tests with fakeredis."""

from __future__ import annotations

import pytest
from httpx import AsyncClient
from redis.asyncio import Redis

from iris_service.customer.dtos import CustomerResponse
from iris_service.customer.recent_buffer import KEY, MAX_SIZE, RecentCustomerBuffer


@pytest.mark.asyncio
async def test_add_then_get_recent_returns_in_lifo_order(fake_redis: Redis) -> None:
    buffer = RecentCustomerBuffer(fake_redis)
    for i in range(3):
        await buffer.add(CustomerResponse(id=i, name=f"User{i}", email=f"u{i}@x.com"))

    recent = await buffer.get_recent()
    assert len(recent) == 3
    # LPUSH = newest first
    assert recent[0].id == 2
    assert recent[2].id == 0


@pytest.mark.asyncio
async def test_buffer_caps_at_max_size(fake_redis: Redis) -> None:
    buffer = RecentCustomerBuffer(fake_redis)
    for i in range(MAX_SIZE + 5):
        await buffer.add(CustomerResponse(id=i, name=f"User{i}", email=f"u{i}@x.com"))

    recent = await buffer.get_recent()
    assert len(recent) == MAX_SIZE
    # Most recent are kept ; oldest are dropped
    assert recent[0].id == MAX_SIZE + 4


@pytest.mark.asyncio
async def test_get_recent_empty_when_no_entries(fake_redis: Redis) -> None:
    buffer = RecentCustomerBuffer(fake_redis)
    assert await buffer.get_recent() == []


@pytest.mark.asyncio
async def test_size_returns_redis_llen(fake_redis: Redis) -> None:
    buffer = RecentCustomerBuffer(fake_redis)
    await buffer.add(CustomerResponse(id=1, name="A", email="a@x.com"))
    await buffer.add(CustomerResponse(id=2, name="B", email="b@x.com"))
    assert await buffer.size() == 2


@pytest.mark.asyncio
async def test_get_recent_skips_malformed_entries(fake_redis: Redis) -> None:
    """Malformed JSON in Redis (corrupted entry) is silently dropped."""
    await fake_redis.lpush(KEY, "not-valid-json")  # type: ignore[misc]
    await fake_redis.lpush(KEY, '{"id":1,"name":"Alice","email":"a@x.com"}')  # type: ignore[misc]

    buffer = RecentCustomerBuffer(fake_redis)
    recent = await buffer.get_recent()
    # Only the valid entry is returned
    assert len(recent) == 1
    assert recent[0].id == 1


@pytest.mark.asyncio
async def test_post_customer_populates_buffer(client: AsyncClient) -> None:
    """End-to-end : POST /customers → buffer add → GET /customers/recent surfaces it."""
    await client.post(
        "/customers",
        json={"name": "Buffered", "email": "buf@example.com"},
    )

    response = await client.get("/customers/recent")
    assert response.status_code == 200
    body = response.json()
    assert len(body) == 1
    assert body[0]["name"] == "Buffered"


@pytest.mark.asyncio
async def test_get_recent_empty_when_no_creates(client: AsyncClient) -> None:
    response = await client.get("/customers/recent")
    assert response.status_code == 200
    assert response.json() == []
