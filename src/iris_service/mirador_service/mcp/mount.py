"""Mount the FastMCP streamable-http server onto the existing FastAPI app.

Public entry point : :func:`mount_mcp_server`. Called from
:mod:`iris_service.app` once routers are registered.

Why streamable-http transport — gives us a single HTTP endpoint
(``/mcp``) with bidirectional JSON-RPC + Server-Sent Events for
streaming progress notifications. Matches the Java sibling's
``POST /mcp/message`` + ``GET /mcp/sse`` shape (FastMCP's
streamable-http path covers both verbs on the same URL).

Why not stdio — stdio is for desktop Claude / Inspector workflows ;
our deployment is a server with multiple HTTP clients, streamable-http
is the right transport.

Auth — the FastMCP server's ``token_verifier`` runs BEFORE every tool
call. We pass :class:`McpTokenVerifier`, which decodes the same JWTs
the REST API uses and stashes the (username, role) in a contextvar
that tool bodies read via :func:`get_current_user`.

Idempotency, rate-limit, OTel tracing — INHERITED from the existing
FastAPI middleware stack because the streamable-http app is mounted
as a sub-app at ``/mcp`` (Starlette propagates middleware).
"""

from __future__ import annotations

import contextlib
import logging
import os
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING

from mcp.server.auth.settings import AuthSettings
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from pydantic import AnyHttpUrl
from sqlalchemy.ext.asyncio import AsyncSession

from iris_service.config.settings import get_settings
from iris_service.db.base import get_session_factory
from iris_service.mcp.auth import ROLE_USER, McpTokenVerifier
from iris_service.mcp.metrics_registry import get_metrics_reader
from iris_service.mcp.ring_buffer import attach_ring_buffer
from iris_service.mcp.tools import Deps, register_tools

if TYPE_CHECKING:
    from fastapi import FastAPI

logger = logging.getLogger(__name__)

#: Sub-mount path. The ``/mcp`` path is conventional for streamable-http
#: clients (matches Anthropic's CLI default + the Java sibling).
MCP_MOUNT_PATH = "/mcp"


async def _default_session_opener() -> AsyncSession:
    """Open a fresh AsyncSession from the lazy module-level factory.

    Tools each take a ``session_factory`` callable in their Deps. We
    centralise the construction here so the tool bodies stay decoupled
    from the SQLAlchemy boot-time wiring (and tests can swap in an
    in-memory SQLite session opener).

    Note: ``factory()`` returns an AsyncSession instance ready for
    ``async with``-style use — the tool bodies wrap it with
    ``async with await deps.session_factory()`` so the session is
    closed on the way out.
    """
    factory = get_session_factory()
    return factory()


def mount_mcp_server(app: FastAPI) -> FastMCP:
    """Wire the MCP server into the FastAPI app at ``/mcp``.

    Idempotent — re-calling on the same FastAPI instance is a no-op
    (matters for tests that share a fixture-built app across cases).
    Returns the FastMCP instance so tests / introspection can list
    registered tools.

    Wiring order (matters) :

    1. Attach the ring-buffer logging handler — must happen BEFORE the
       first tool call so the buffer captures any startup noise too.
    2. Construct FastMCP with the token verifier + json_response=True
       (we want POST replies to be plain JSON for easier debugging,
       reserving SSE for streaming progress notifications).
    3. Register all 14 tools with a Deps closure.
    4. Mount the streamable-http sub-app at ``/mcp`` and wire the
       lifespan context manager so the SDK's session manager starts
       + shuts down cleanly with the parent FastAPI lifespan.
    """
    if getattr(app.state, "mcp_server", None) is not None:
        return app.state.mcp_server  # type: ignore[no-any-return]

    # 1. Ring buffer — read the size from env once so we don't import-loop.
    ring = attach_ring_buffer()

    # 2. FastMCP instance with our JWT verifier wired in.
    #    AuthSettings is required by the SDK as soon as we pass a token
    #    verifier — it powers the RFC 9728 protected-resource-metadata
    #    endpoint. Since we OWN the JWT (HS256, in-process issuance via
    #    auth/jwt.py), the issuer + resource URLs both point at our base
    #    URL ; the actual verification is delegated to McpTokenVerifier
    #    which calls our own decode_token().
    settings = get_settings()
    base_url = AnyHttpUrl(f"http://{settings.server_host}:{settings.server_port}")
    # In dev_mode (or when IRIS_MCP_DISABLE_HOST_GUARD=1), relax the SDK's
    # DNS-rebinding guard so ASGI-style HTTP tests with a synthetic Host
    # header (e.g. ``http://test`` from ``httpx.ASGITransport``) reach the
    # tools. Two ways to opt in :
    #   - ``settings.dev_mode == True`` (typical local dev path)
    #   - ``IRIS_MCP_DISABLE_HOST_GUARD=1`` env (test path — bypasses
    #     the lru_cache around get_settings() so test code that toggles env
    #     mid-session still works).
    # In prod both env knobs stay off ; the SDK enforces a real Host header
    # match against ``resource_server_url``.
    relax_guard = settings.dev_mode or os.environ.get("IRIS_MCP_DISABLE_HOST_GUARD") == "1"
    transport_security: TransportSecuritySettings | None = (
        TransportSecuritySettings(enable_dns_rebinding_protection=False) if relax_guard else None
    )
    mcp = FastMCP(
        name="iris-service-python",
        instructions=(
            "Iris customer service — MCP tools to query orders, products, "
            "customers ; trigger chaos experiments ; tail logs / metrics / "
            "health from the running backend. All data is in-process — NO "
            "external Loki/Mimir/Grafana calls."
        ),
        json_response=True,
        token_verifier=McpTokenVerifier(),
        auth=AuthSettings(
            issuer_url=base_url,
            resource_server_url=base_url,
            required_scopes=[ROLE_USER],
        ),
        # Mount path used by streamable_http_app() ; aligns the URL the
        # FastMCP sub-app sees with where we attach it on the parent app.
        streamable_http_path="/",
        transport_security=transport_security,
    )

    # 3. Build the Deps closure + register the 15 tools (14 baseline +
    #    predict_customer_churn from Phase C — see shared ADR-0061).
    from iris_service.ml.predictor_singleton import get_churn_predictor

    deps = Deps(
        app=app,
        settings=settings,
        session_factory=_default_session_opener,
        ring_buffer=ring,
        metrics_reader=get_metrics_reader(),
        churn_predictor=get_churn_predictor(),
    )
    register_tools(mcp, deps)

    # 4. Mount the streamable-http sub-app and chain the lifespan.
    app.mount(MCP_MOUNT_PATH, mcp.streamable_http_app())
    _wire_mcp_lifespan(app, mcp)

    app.state.mcp_server = mcp
    logger.info("mcp_mounted path=%s tools=15", MCP_MOUNT_PATH)
    return mcp


def _wire_mcp_lifespan(app: FastAPI, mcp: FastMCP) -> None:
    """Chain the FastMCP session manager into the FastAPI lifespan.

    FastMCP's streamable_http transport needs ``session_manager.run()``
    to be active for the duration of the HTTP server. The parent
    FastAPI app already declares a lifespan in :mod:`iris_service.app`
    — we extend it by registering an ASGI ``startup`` / ``shutdown``
    handler pair that bracket the session manager around the existing
    lifespan body.

    This is the FastAPI-recommended pattern for "extend an existing
    lifespan" : we wrap the original ``lifespan`` attribute of the
    Starlette router with a chained context manager.
    """
    original_lifespan = app.router.lifespan_context

    @contextlib.asynccontextmanager
    async def chained_lifespan(parent_app: FastAPI) -> AsyncIterator[None]:
        async with mcp.session_manager.run():
            async with original_lifespan(parent_app):
                yield

    app.router.lifespan_context = chained_lifespan
