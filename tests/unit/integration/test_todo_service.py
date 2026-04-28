"""TodoService tests with respx — no real HTTP calls to JSONPlaceholder."""

from __future__ import annotations

import httpx
import pytest
import respx

from iris_service.integration.todo_service import TodoService


@pytest.mark.asyncio
async def test_get_todos_returns_payload_on_200() -> None:
    payload = [
        {"userId": 1, "id": 1, "title": "delectus aut autem", "completed": False},
        {"userId": 1, "id": 2, "title": "quis ut nam", "completed": True},
    ]
    async with respx.mock:
        respx.get("https://jsonplaceholder.typicode.com/users/1/todos").mock(
            return_value=httpx.Response(200, json=payload)
        )
        service = TodoService()
        try:
            result = await service.get_todos(1)
        finally:
            await service.aclose()
    assert len(result) == 2
    # Result items are now Pydantic Todo models (migrated 2026-04-25 from
    # dict[str, Any]) — access via attribute, not subscript.
    assert result[0].title == "delectus aut autem"
    assert result[0].user_id == 1
    assert result[1].completed is True


@pytest.mark.asyncio
async def test_get_todos_returns_empty_on_5xx_after_retries() -> None:
    """After 3 failed attempts (all 503), returns [] graceful fallback."""
    async with respx.mock:
        respx.get("https://jsonplaceholder.typicode.com/users/2/todos").mock(return_value=httpx.Response(503))
        service = TodoService()
        try:
            result = await service.get_todos(2)
        finally:
            await service.aclose()
    assert result == []


@pytest.mark.asyncio
async def test_get_todos_returns_empty_on_network_error() -> None:
    """ConnectError after retries → [] (graceful degradation)."""
    async with respx.mock:
        respx.get("https://jsonplaceholder.typicode.com/users/3/todos").mock(
            side_effect=httpx.ConnectError("DNS failure")
        )
        service = TodoService()
        try:
            result = await service.get_todos(3)
        finally:
            await service.aclose()
    assert result == []
