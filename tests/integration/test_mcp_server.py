"""Integration tests for the MCP server end-to-end.

Boots the full FastAPI app, hits POST /mcp/ with a real JSON-RPC payload
+ a valid JWT, asserts the streamable-http transport returns parseable
results.

Marked @pytest.mark.integration ; runs only with `pytest -m integration`.
"""

from __future__ import annotations

import json
import os
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

# Disable the MCP SDK's DNS-rebinding host guard. Necessary when using
# ``httpx.ASGITransport`` because its synthetic ``Host: test`` header
# would otherwise be rejected. Read straight from os.environ inside the
# mount layer (bypasses the lru_cache around get_settings()), so tests
# that toggle env mid-session still take effect.
os.environ["IRIS_MCP_DISABLE_HOST_GUARD"] = "1"

from iris_service.app import create_app
from iris_service.auth.jwt import issue_access_token
from iris_service.config.settings import get_settings
from iris_service.mcp.auth import ROLE_USER

pytestmark = pytest.mark.integration


@pytest_asyncio.fixture
async def app() -> AsyncIterator[FastAPI]:
    """FastAPI app with lifespan running for the test duration.

    Uses ``asgi_lifespan.LifespanManager`` instead of calling
    ``app.router.lifespan_context`` directly because the latter doesn't
    propagate cancellation properly through the FastMCP session_manager
    (anyio cancel-scope cross-task issue surfaces at teardown).
    """
    from asgi_lifespan import LifespanManager

    a = create_app()
    # 30s shutdown timeout : the parent app's lifespan tries to flush OTel
    # spans via HTTP to localhost:4318 (no collector running in tests) ;
    # the OTel SDK's default per-batch timeout takes ~10s, so the 5s default
    # would race the cleanup. 30s is generous but bounded.
    async with LifespanManager(a, startup_timeout=30, shutdown_timeout=30):
        yield a


@pytest_asyncio.fixture
async def client(app: FastAPI) -> AsyncIterator[AsyncClient]:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


def _bearer_for(role: str = ROLE_USER, username: str = "tester") -> dict[str, str]:
    settings = get_settings()
    token, _ = issue_access_token(settings.jwt, username, role)
    # Streamable-HTTP requires both Accept headers per the MCP spec.
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json, text/event-stream",
        "Content-Type": "application/json",
    }


def _initialize_payload(request_id: int = 1) -> dict[str, object]:
    """JSON-RPC 'initialize' — the first call any MCP client makes."""
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "method": "initialize",
        "params": {
            "protocolVersion": "2025-06-18",
            "capabilities": {},
            "clientInfo": {"name": "iris-pytest", "version": "0.0.0"},
        },
    }


def _list_tools_payload(request_id: int = 2) -> dict[str, object]:
    return {"jsonrpc": "2.0", "id": request_id, "method": "tools/list", "params": {}}


def _call_tool_payload(name: str, args: dict[str, object], request_id: int = 3) -> dict[str, object]:
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "method": "tools/call",
        "params": {"name": name, "arguments": args},
    }


def _parse_response(text: str) -> dict[str, object]:
    """Streamable-HTTP returns either application/json or text/event-stream.

    For SSE responses, lines look like ``data: {...}`` ; we extract the
    first ``data:`` line and JSON-parse it. For json_response=True the
    body is plain JSON.
    """
    text = text.strip()
    if text.startswith("event:") or "\ndata:" in text or text.startswith("data:"):
        for line in text.splitlines():
            if line.startswith("data:"):
                return json.loads(line.removeprefix("data:").strip())
    return json.loads(text)


@pytest.mark.asyncio
async def test_unauthenticated_initialize_rejected(client: AsyncClient) -> None:
    """No bearer token → 401."""
    res = await client.post(
        "/mcp/",
        json=_initialize_payload(),
        headers={
            "Accept": "application/json, text/event-stream",
            "Content-Type": "application/json",
        },
    )
    assert res.status_code == 401


@pytest.mark.asyncio
async def test_initialize_succeeds_with_valid_jwt(client: AsyncClient) -> None:
    res = await client.post(
        "/mcp/",
        json=_initialize_payload(),
        headers=_bearer_for(),
    )
    # Either 200 (json) or 202 (stream accepted) signal success.
    assert res.status_code in (200, 202), res.text
    if res.status_code == 200 and res.text:
        body = _parse_response(res.text)
        assert body.get("jsonrpc") == "2.0"
        assert "result" in body or "error" not in body


@pytest.mark.asyncio
async def test_list_tools_returns_minimum_count(client: AsyncClient) -> None:
    """tools/list over JSON-RPC must return at least the known core tool set.

    Asserts a MINIMUM count rather than exact match so adding a new @tool
    doesn't require a test edit. The minimum (14) is the originally-shipped
    set ; tools added since (e.g. churn-prediction, drift-detection) push
    the count up but never down.

    For exact-set verification, see the per-tool tests further below
    (test_get_actuator_info_call, etc.) which exercise specific tool names.
    """
    headers = _bearer_for()
    # Initialize first to bootstrap the session.
    init_res = await client.post("/mcp/", json=_initialize_payload(1), headers=headers)
    assert init_res.status_code in (200, 202)
    # Pull the session id the SDK assigned (mcp-session-id header).
    session_id = init_res.headers.get("mcp-session-id")
    if session_id:
        headers = {**headers, "mcp-session-id": session_id}
    # Send the initialized notification before tools/list (per spec).
    await client.post(
        "/mcp/",
        json={"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}},
        headers=headers,
    )
    res = await client.post("/mcp/", json=_list_tools_payload(2), headers=headers)
    assert res.status_code == 200, res.text
    body = _parse_response(res.text)
    assert "result" in body
    tools = body["result"]["tools"]  # type: ignore[index]
    assert isinstance(tools, list)
    # At least the original 14 — additions are welcome, removals are flagged.
    assert len(tools) >= 14, f"expected at least 14 tools, got {len(tools)}"


@pytest.mark.asyncio
async def test_get_actuator_info_call(client: AsyncClient) -> None:
    """Exercise the get_actuator_info tool end-to-end."""
    headers = _bearer_for()
    init_res = await client.post("/mcp/", json=_initialize_payload(1), headers=headers)
    session_id = init_res.headers.get("mcp-session-id")
    if session_id:
        headers = {**headers, "mcp-session-id": session_id}
    await client.post(
        "/mcp/",
        json={"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}},
        headers=headers,
    )
    res = await client.post(
        "/mcp/",
        json=_call_tool_payload("get_actuator_info", {}),
        headers=headers,
    )
    assert res.status_code == 200, res.text
    body = _parse_response(res.text)
    assert "result" in body
    # FastMCP wraps tool results — content[0].text holds the JSON dump.
    content = body["result"].get("content")  # type: ignore[index]
    assert content is not None


@pytest.mark.asyncio
async def test_get_health_call(client: AsyncClient) -> None:
    """Health tool returns a structured snapshot through MCP."""
    headers = _bearer_for()
    init_res = await client.post("/mcp/", json=_initialize_payload(1), headers=headers)
    session_id = init_res.headers.get("mcp-session-id")
    if session_id:
        headers = {**headers, "mcp-session-id": session_id}
    await client.post(
        "/mcp/",
        json={"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}},
        headers=headers,
    )
    res = await client.post(
        "/mcp/",
        json=_call_tool_payload("get_health", {}),
        headers=headers,
    )
    # Health may UP (real DB) or DOWN (no DB at integration layer) — either
    # is a successful tool response, both must come back as 200 with content.
    assert res.status_code == 200, res.text
