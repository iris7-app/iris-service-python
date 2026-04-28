"""Iris MCP server — exposes domain + observability tools to LLM clients.

Mounts at `/mcp` on the existing FastAPI app (see :mod:`.mount`). Tool
catalogue mirrors the Java sibling (see ADR-0062 in the Java repo) :
7 domain tools + 7 backend-local observability tools, all returning
typed Pydantic DTOs (NEVER raw ORM entities).

Architectural constraint mirrored from the Java side : the Iris
backend MUST stay infrastructure-agnostic. The MCP server only exposes
what the backend already PRODUCES in-process — Python `logging` ring
buffer, `prometheus_client` REGISTRY, FastAPI's auto-OpenAPI, the
domain services. Zero HTTP clients to Loki / Mimir / Grafana / GitLab /
GitHub / k8s. Those are external community MCPs the Claude session
adds independently via `claude mcp add`.

Public entry point : :func:`mount_mcp_server` in :mod:`.mount`.
"""

from __future__ import annotations

# Re-exported here so external callers can write
# ``from iris_service.mcp import mount_mcp_server``. The actual
# implementation lives in :mod:`iris_service.mcp.mount`.
from iris_service.mcp.mount import mount_mcp_server

__all__ = ["mount_mcp_server"]
