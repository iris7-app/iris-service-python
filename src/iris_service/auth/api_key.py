"""Static API-key authentication — machine-to-machine fallback to JWT.

Mirrors the Java sibling's
:java:`com.mirador.auth.ApiKeyAuthenticationFilter`. Reads an
``X-API-Key`` request header, compares against the configured secret
(``IRIS_API_KEY`` env, default ``demo-api-key-2026``), and on match :

- For REST routes : populates ``request.state.api_key_user`` so
  :func:`iris_service.auth.deps.current_user` can short-circuit JWT
  decoding and return an admin-scoped :class:`AuthenticatedUser`.
- For the MCP sub-app at ``/mcp/`` : injects a synthetic
  ``Authorization: Bearer <api-key>`` header into the ASGI scope so the
  MCP SDK's :class:`mcp.server.auth.middleware.bearer_auth.BearerAuthBackend`
  picks the API key up. :class:`iris_service.mcp.auth.McpTokenVerifier`
  recognises the API-key token (path 2 in its ``verify_token``) and
  returns an admin-scoped :class:`AccessToken`.

Header miss / mismatch : pure pass-through. JWT chain handles auth as
usual. No 401 is emitted from this middleware — the API key is purely
additive to JWT, never restrictive.

The synthetic principal is ``api-key-user`` with role ``ROLE_ADMIN`` —
identical to the Java filter so admin-only MCP tools (e.g.
``trigger_chaos_experiment``, ``get_health_detail``) work without a
JWT login flow. Production deployments must inject the key from a
secrets manager (Vault, AWS SSM) — never hardcode in env files
checked into the repo.
"""

from __future__ import annotations

import logging
from typing import Final

from starlette.types import ASGIApp, Receive, Scope, Send

from iris_service.config.settings import get_settings

logger = logging.getLogger(__name__)

#: Header name — character-for-character match with the Java sibling's
#: ``API_KEY_HEADER`` constant. Lower-cased on lookup because ASGI
#: normalises header names to lowercase byte-strings.
API_KEY_HEADER: Final[str] = "x-api-key"

#: Synthetic principal exposed to handlers when API-key auth succeeds.
#: Same value as Java's ``UsernamePasswordAuthenticationToken`` principal.
API_KEY_USERNAME: Final[str] = "api-key-user"

#: Role granted on API-key match. The Java filter grants BOTH
#: ``ROLE_USER`` and ``ROLE_ADMIN`` ; the Python side encodes this as
#: a single ``ROLE_ADMIN`` because the MCP layer treats admin as a
#: superset of user (see :mod:`iris_service.mcp.auth` ``scopes``
#: assembly logic — ``ROLE_ADMIN`` → ``[ROLE_USER, ROLE_ADMIN]``). REST
#: side uses :func:`require_role` which checks one role at a time, so
#: the admin grant covers both ``require_role("ROLE_ADMIN")`` and
#: ``require_role("ROLE_USER")``-equivalent paths via explicit checks.
API_KEY_ROLE: Final[str] = "ROLE_ADMIN"


class ApiKeyMiddleware:
    """ASGI middleware — translates ``X-API-Key`` into auth context.

    Registered as the OUTERMOST middleware (runs FIRST on inbound
    requests) so it can rewrite the scope's headers BEFORE the MCP
    sub-app's bearer-auth backend runs. Order matches Spring's
    ``ApiKeyAuthenticationFilter`` running before
    ``JwtAuthenticationFilter`` in the filter chain.

    Behaviour :

    1. No ``X-API-Key`` header → pure pass-through. JWT path runs as
       usual.
    2. ``X-API-Key`` present but doesn't match configured key → pure
       pass-through (still no 401 emitted ; the JWT path will reject
       if it can't authenticate either). The Java filter behaves
       identically — silent drop, fall through to next filter.
    3. ``X-API-Key`` matches → marks the scope so REST handlers see
       the API-key principal, AND injects a synthetic
       ``Authorization: Bearer <api-key>`` header so the MCP SDK's
       bearer-auth backend picks it up downstream.

    The "set state ON SCOPE not ON request" detail matters : Starlette's
    ``request.state`` is a per-request namespace stored ON THE SCOPE,
    so writing ``scope['state']['api_key_user']`` here is observable
    via ``request.state.api_key_user`` in the FastAPI dependency layer.
    Sub-apps mounted at ``/mcp`` see the same scope (Starlette propagates
    it through ``app.mount``), so the synthetic Bearer header is
    visible to the MCP SDK without any extra wiring.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        # Only HTTP-style requests carry headers + state. Skip lifespan +
        # websocket scopes — they don't authenticate via X-API-Key.
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        provided = _extract_header(scope, API_KEY_HEADER)
        if provided is None:
            await self.app(scope, receive, send)
            return

        # Lazy lookup so settings overrides in tests (env var changes mid
        # session, dependency_overrides) take effect — the lru_cache around
        # get_settings keeps this cheap on hot paths.
        configured = get_settings().auth.api_key
        if not configured or provided != configured:
            # Wrong key — silent fall-through, mirrors Java filter. JWT
            # chain will reject if no Bearer token either.
            await self.app(scope, receive, send)
            return

        # Match — populate request state + inject Bearer header for MCP.
        # We also stash the api-key principal directly so REST handlers
        # short-circuit JWT decoding (faster + works with no JWT secret
        # configured at all).
        _attach_api_key_principal(scope)
        _inject_bearer_header(scope, configured)
        logger.debug("Authenticated via X-API-Key header — principal=%s", API_KEY_USERNAME)

        await self.app(scope, receive, send)


def _extract_header(scope: Scope, name: str) -> str | None:
    """Return the value of ``name`` (case-insensitive) from an ASGI scope.

    ASGI headers are a list of (name_bytes, value_bytes) tuples with
    name lower-cased. The MCP SDK's bearer extractor walks ``conn.headers``
    which is already case-insensitive ; we walk the ASGI list directly
    because :class:`starlette.requests.Request` isn't constructed at
    this layer.
    """
    target = name.lower().encode("latin-1")
    for header_name, header_value in scope.get("headers", []):
        if header_name == target:
            try:
                # Explicit cast to str — latin-1.decode() returns str at
                # runtime but mypy resolves bytes.decode to Any due to
                # codec polymorphism. The cast is local + safe.
                return str(header_value.decode("latin-1"))
            except UnicodeDecodeError:
                # Header values that aren't latin-1 bytes are malformed —
                # treat as missing. Real API keys are ASCII.
                return None
    return None


def _attach_api_key_principal(scope: Scope) -> None:
    """Mark the scope so REST handlers see the API-key user.

    Starlette stores ``request.state.X`` in ``scope['state']`` under the
    hood (see ``starlette.requests.Request.state`` + ``State`` proxy).
    Writing here pre-populates that dict so dependency injection picks
    it up without touching the request body.
    """
    state = scope.setdefault("state", {})
    # Sentinel object — :func:`iris_service.auth.deps.current_user`
    # reads this attribute and short-circuits when present.
    state["api_key_user"] = ApiKeyPrincipal(
        username=API_KEY_USERNAME,
        role=API_KEY_ROLE,
    )


def _inject_bearer_header(scope: Scope, api_key: str) -> None:
    """Add ``Authorization: Bearer <api-key>`` to the scope headers.

    Replaces any pre-existing Authorization header — the API key
    intentionally takes precedence over a Bearer JWT (matches Java's
    "API key filter runs FIRST" ordering ; presenting both produces
    the API-key auth path, not the JWT one).

    The synthetic header lets the MCP SDK's
    :class:`BearerAuthBackend` extract the API key as a "token" and
    pass it to :class:`McpTokenVerifier.verify_token`, which then
    recognises the API-key value and returns admin scopes.
    """
    new_value = f"Bearer {api_key}".encode("latin-1")
    auth_target = b"authorization"
    headers: list[tuple[bytes, bytes]] = list(scope.get("headers", []))
    # Remove any pre-existing Authorization header — last writer wins,
    # API-key auth takes precedence over JWT.
    headers = [(name, value) for name, value in headers if name != auth_target]
    headers.append((auth_target, new_value))
    scope["headers"] = headers


class ApiKeyPrincipal:
    """Plain value-object for the API-key authenticated principal.

    Kept as a class (rather than a dataclass) so the import surface
    of this module stays minimal — the dependency layer only reads
    ``.username`` and ``.role``. A frozen dataclass would also work
    but adds the dataclasses import for no behavioural difference.
    """

    __slots__ = ("role", "username")

    def __init__(self, username: str, role: str) -> None:
        self.username = username
        self.role = role

    def __repr__(self) -> str:  # pragma: no cover — debug only
        return f"ApiKeyPrincipal(username={self.username!r}, role={self.role!r})"
