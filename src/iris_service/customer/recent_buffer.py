"""Redis-backed ring buffer for recent customers.

Mirror of Java's RecentCustomerBuffer :
- LPUSH on add()
- LTRIM to MAX_SIZE entries
- LRANGE on get_recent()

Stored as JSON strings (Pydantic serialised). Survives pod restarts +
shared across replicas (single Redis = single source of truth).
"""

from __future__ import annotations

import json
from typing import Final

from redis.asyncio import Redis

from iris_service.customer.dtos import CustomerResponse

# Redis key namespace mirrors Java side ("customer-service:recent:customers").
# Final[str] : reassignment is a static-type error (mypy catches a future
# refactor that accidentally rebinds it).
KEY: Final[str] = "customer-service:recent:customers"

# LIFO buffer capacity. 10 = same as Java mirror's RecentCustomerBuffer.
# Final[int] : interface contract — anyone raising this past, say, 1000
# should think about Redis memory + LRANGE latency first.
MAX_SIZE: Final[int] = 10

# Tuple of exception classes caught when decoding a buffer entry. Hoisted
# to module-level so we don't recreate it per loop iteration AND so the
# tuple syntax never trips the "multiple exception types must be
# parenthesized" syntax error (ruff/black sometimes inline-reformat the
# `except (A, B):` form back into the bare comma form which is illegal
# in Python 3 — using a name dodges that).
_DECODE_ERRORS: Final[tuple[type[Exception], ...]] = (json.JSONDecodeError, ValueError)


class RecentCustomerBuffer:
    """Async Redis-backed ring buffer."""

    def __init__(self, redis: Redis) -> None:
        self._redis = redis

    async def add(self, customer: CustomerResponse) -> None:
        """Prepend + trim. LPUSH + LTRIM are not atomic together but the
        worst-case race is a list of MAX_SIZE+1 briefly — acceptable for a
        non-critical display buffer."""
        payload = customer.model_dump_json()
        await self._redis.lpush(KEY, payload)  # type: ignore[misc]
        await self._redis.ltrim(KEY, 0, MAX_SIZE - 1)  # type: ignore[misc]

    async def get_recent(self) -> list[CustomerResponse]:
        """Return up to MAX_SIZE recent customers, newest first."""
        raw: list[bytes | str] = await self._redis.lrange(KEY, 0, MAX_SIZE - 1)  # type: ignore[misc]
        result: list[CustomerResponse] = []
        for item in raw:
            try:
                payload = item.decode("utf-8") if isinstance(item, bytes) else item
                result.append(CustomerResponse.model_validate(json.loads(payload)))
            except _DECODE_ERRORS:
                # Malformed entry : skip silently — the buffer is best-effort.
                continue
        return result

    async def size(self) -> int:
        """Return current buffer size (for Prometheus gauge)."""
        return int(await self._redis.llen(KEY))  # type: ignore[misc]
