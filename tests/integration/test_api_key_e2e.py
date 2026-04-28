"""End-to-end MCP test — X-API-Key unlocks tools/list + admin tools.

Boots the full FastAPI app + MCP sub-app and verifies that a client
hitting ``/mcp/`` with ``X-API-Key: demo-api-key-2026`` can :

1. Initialise the session (no JWT in flight).
2. List tools.
3. Call admin-only tools (``trigger_chaos_experiment``,
   ``get_health_detail``) without 403.

Without the header → 401 (matches the existing JWT-no-token path in
``tests/integration/test_mcp_auth.py``).

This is the contract that unblocks wiring ``mirador-python`` MCP into
Claude with a single ``--header X-API-Key: ...`` flag, no login flow.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

# DNS-rebinding guard relax — same pattern as tests/integration/test_mcp_auth.py.
# httpx.ASGITransport synthesises Host: test which the SDK rejects by default.
os.environ["IRIS_MCP_DISABLE_HOST_GUARD"] = "1"

from iris_service.app import create_app
from iris_service.config.settings import get_settings

pytestmark = pytest.mark.integration


@pytest_asyncio.fixture
async def app() -> AsyncIterator[FastAPI]:
    """FastAPI with full lifespan — see test_mcp_server.py for rationale."""
    from asgi_lifespan import LifespanManager

    a = create_app()
    async with LifespanManager(a, startup_timeout=30, shutdown_timeout=30):
        yield a


@pytest_asyncio.fixture
async def client(app: FastAPI) -> AsyncIterator[AsyncClient]:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


def _api_key_headers(api_key: str | None) -> dict[str, str]:
    """Build the X-API-Key + MCP wire-protocol headers."""
    base = {
        "Accept": "application/json, text/event-stream",
        "Content-Type": "application/json",
    }
    if api_key is not None:
        base["X-API-Key"] = api_key
    return base


async def _initialized_session(client: AsyncClient, api_key: str | None) -> dict[str, str]:
    """Send the MCP ``initialize`` handshake ; return session-bound headers.

    Returns ``{}`` when the request is rejected (401), letting the test
    assert against the empty dict instead of crashing on ``KeyError``.
    """
    headers = _api_key_headers(api_key)
    res = await client.post(
        "/mcp/",
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "pytest-api-key", "version": "0.0.0"},
            },
        },
        headers=headers,
    )
    if res.status_code == 401:
        return {}
    session_id = res.headers.get("mcp-session-id")
    if session_id:
        headers = {**headers, "mcp-session-id": session_id}
    await client.post(
        "/mcp/",
        json={"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}},
        headers=headers,
    )
    return headers


@pytest.mark.asyncio
async def test_no_api_key_returns_401(client: AsyncClient) -> None:
    """No X-API-Key + no JWT → MCP rejects."""
    headers = _api_key_headers(None)
    res = await client.post(
        "/mcp/",
        json={"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}},
        headers=headers,
    )
    assert res.status_code == 401


@pytest.mark.asyncio
async def test_api_key_can_list_tools(client: AsyncClient) -> None:
    """Valid X-API-Key → tools/list succeeds, no JWT exchange needed."""
    api_key = get_settings().auth.api_key
    headers = await _initialized_session(client, api_key)
    assert headers, "initialize failed for a valid X-API-Key"
    res = await client.post(
        "/mcp/",
        json={"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
        headers=headers,
    )
    assert res.status_code == 200, res.text


@pytest.mark.asyncio
async def test_api_key_can_call_admin_tool(client: AsyncClient) -> None:
    """Valid X-API-Key → admin-only tool ``trigger_chaos_experiment`` runs.

    The Java filter grants ``ROLE_ADMIN`` on API-key match ; this test
    proves the Python side does too.
    """
    api_key = get_settings().auth.api_key
    headers = await _initialized_session(client, api_key)
    assert headers
    res = await client.post(
        "/mcp/",
        json={
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {
                "name": "trigger_chaos_experiment",
                "arguments": {"scenario": "kafka-timeout"},
            },
        },
        headers=headers,
    )
    # 200 = the role gate accepted us (chaos tool returns isError:false).
    assert res.status_code == 200, res.text
    # No "ROLE_ADMIN" rejection in the body — would only appear on a
    # require_role failure.
    assert b"Tool requires role" not in res.content


@pytest.mark.asyncio
async def test_wrong_api_key_returns_401(client: AsyncClient) -> None:
    """Wrong X-API-Key + no JWT → 401 (silent fall-through to JWT path)."""
    headers = _api_key_headers("not-the-real-key")
    res = await client.post(
        "/mcp/",
        json={"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}},
        headers=headers,
    )
    assert res.status_code == 401
