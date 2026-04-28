"""Unit tests for :mod:`iris_service.mcp.mount`.

Verifies the wiring contract :
- the mount path is /mcp (matches Java sibling + ADR-0062)
- exactly 15 tools are registered (14 baseline + predict_customer_churn
  shipped in Phase C of shared ADR-0061)
- tool names match the canonical TOOL_NAMES tuple
- mounting is idempotent (re-call is a no-op)
"""

from __future__ import annotations

import pytest

from iris_service.app import create_app
from iris_service.mcp.dtos import TOOL_NAMES
from iris_service.mcp.mount import MCP_MOUNT_PATH, mount_mcp_server


def test_mount_path_is_canonical() -> None:
    assert MCP_MOUNT_PATH == "/mcp"


@pytest.mark.asyncio
async def test_mount_registers_15_tools() -> None:
    app = create_app()
    tools = await app.state.mcp_server.list_tools()
    assert len(tools) == 15


@pytest.mark.asyncio
async def test_tool_names_match_canonical() -> None:
    app = create_app()
    tools = await app.state.mcp_server.list_tools()
    names = {t.name for t in tools}
    assert names == set(TOOL_NAMES)


@pytest.mark.asyncio
async def test_each_tool_has_description() -> None:
    """LLM disambiguation hinges on per-tool descriptions ; none can be empty."""
    app = create_app()
    tools = await app.state.mcp_server.list_tools()
    empty_descriptions = [t.name for t in tools if not (t.description or "").strip()]
    assert empty_descriptions == [], f"tools missing description: {empty_descriptions}"


def test_mount_idempotent() -> None:
    """Calling mount_mcp_server twice on the same app returns the same instance."""
    app = create_app()
    first = app.state.mcp_server
    second = mount_mcp_server(app)
    assert first is second


@pytest.mark.asyncio
async def test_admin_only_tools_marked_in_description() -> None:
    """Admin-gated tools should advertise the gating to the LLM."""
    app = create_app()
    tools = {t.name: t for t in await app.state.mcp_server.list_tools()}
    assert "Admin" in (tools["get_health_detail"].description or "")
    assert "Admin" in (tools["trigger_chaos_experiment"].description or "")


def test_mounted_route_visible_on_app() -> None:
    """The /mcp Mount must appear in the FastAPI route table."""
    app = create_app()
    mcp_routes = [r for r in app.routes if getattr(r, "path", "") == "/mcp"]
    assert mcp_routes, "expected at least one route at /mcp"
