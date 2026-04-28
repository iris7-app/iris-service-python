"""TodoService — fetch todos from the JSONPlaceholder external API.

Mirror of Java's `TodoService` (Resilience4j circuit-breaker + retry +
empty-list fallback). Python equivalent : `tenacity` for retry-with-
exponential-backoff + a try/except wrapping for the empty-list fallback.

JSONPlaceholder is a public test API at https://jsonplaceholder.typicode.com.
Endpoint contract : `GET /users/{id}/todos` returns a JSON array of
`{ "userId": int, "id": int, "title": str, "completed": bool }`.

Resilience contract :
- Retry up to 3 times with exponential backoff (250ms → 500ms → 1s).
- Per-attempt timeout 5s.
- On final failure (network down, 5xx, timeout), return [] — graceful
  degradation. The customer page renders "No todos available" instead of
  a 500.
"""

from __future__ import annotations

import logging
from typing import Final

import httpx
from pydantic import BaseModel, ConfigDict, Field
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

logger = logging.getLogger(__name__)

# Public test API — JSONPlaceholder serves canned /users/{id}/todos.
# Final[str] on the URL : prevents accidental rebind from a test setup file.
JSONPLACEHOLDER_BASE_URL: Final[str] = "https://jsonplaceholder.typicode.com"

# 5 s per attempt, 3 attempts total = ~17 s wall-clock worst case (incl. backoff).
# Matches Java's Resilience4j retry config (2 retries + 5 s call timeout).
PER_ATTEMPT_TIMEOUT_S: Final[float] = 5.0


class Todo(BaseModel):
    """JSONPlaceholder todo DTO.

    Wire shape : ``{ userId: int, id: int, title: str, completed: bool }``.
    Migrated from the previous ``type Todo = dict[str, Any]`` alias on
    2026-04-25 (ADR-0007 §"Migrate dict aliases" follow-up). Pydantic
    validates the wire shape AND lets consumers do typed field access
    (e.g. `todo.completed` vs `todo["completed"]`).
    """

    # populate_by_name=True : accept both wire camelCase (userId) and
    # Python snake_case (user_id). extra="ignore" : JSONPlaceholder may
    # add fields we don't model.
    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    user_id: int = Field(validation_alias="userId", serialization_alias="userId")
    id: int
    title: str
    completed: bool


class TodoService:
    """Async client for JSONPlaceholder /todos."""

    def __init__(
        self,
        base_url: str = JSONPLACEHOLDER_BASE_URL,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._base_url = base_url
        # Allow injecting a pre-configured client (tests use respx).
        self._client = client or httpx.AsyncClient(timeout=PER_ATTEMPT_TIMEOUT_S)

    async def get_todos(self, user_id: int) -> list[Todo]:
        """Fetch todos for a user. Returns [] on any failure (graceful degradation)."""
        try:
            return await self._fetch_with_retry(user_id)
        except Exception as exc:
            logger.warning(
                "todo_fetch_failed user_id=%s reason=%s — returning empty list",
                user_id,
                exc,
            )
            return []

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=0.25, min=0.25, max=1.0),
        retry=retry_if_exception_type((httpx.HTTPError, httpx.TimeoutException)),
        reraise=True,
    )
    async def _fetch_with_retry(self, user_id: int) -> list[Todo]:
        """Internal — issue the GET with tenacity retry. Reraises after 3 attempts."""
        url = f"{self._base_url}/users/{user_id}/todos"
        response = await self._client.get(url)
        response.raise_for_status()
        # Pydantic validates the wire shape ; malformed entries fail-fast
        # (caught by the outer try/except in get_todos → empty list fallback).
        return [Todo.model_validate(item) for item in response.json()]

    async def aclose(self) -> None:
        """Close the underlying HTTP client. Called from app.lifespan shutdown."""
        await self._client.aclose()
