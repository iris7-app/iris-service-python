"""JWT verification + role-based authorization for MCP tool calls.

Reuses the existing :mod:`iris_service.auth.jwt` decoder so the MCP
endpoint authenticates with the SAME tokens REST callers use — no
parallel auth tree to maintain.

The wiring is split so each tool can declare its required role
declaratively :

- :class:`McpTokenVerifier` — implements the MCP SDK's
  :class:`TokenVerifier` protocol ; checks JWT validity, role membership,
  and stores the resolved (user, role) tuple in a contextvar.
- :func:`get_current_user` — pulls the (user, role) pair from the
  contextvar at tool-execution time.
- :func:`require_role` — raises :class:`PermissionError` if the current
  user's role does not match (admin-only tools call this at the top of
  their body).

The MCP SDK's TokenVerifier returns :class:`AccessToken` instances ;
mirroring its own contract avoids reimplementing the OAuth-RS flow.
"""

from __future__ import annotations

import logging
import time
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Final

from mcp.server.auth.provider import AccessToken, TokenVerifier

from iris_service.auth.api_key import API_KEY_USERNAME
from iris_service.auth.jwt import ACCESS_TOKEN, JwtError, decode_token
from iris_service.config.settings import get_settings

logger = logging.getLogger(__name__)

#: The two roles the Iris app issues. Admin gets the chaos +
#: get_health_detail tools ; user (= viewer) gets read-only ones.
#: Mirrors the Java sibling's USER / ADMIN. ROLE_ prefix kept for
#: parity with Spring Security's GrantedAuthority convention.
ROLE_USER: Final[str] = "ROLE_USER"
ROLE_ADMIN: Final[str] = "ROLE_ADMIN"

#: Number of seconds the synthetic API-key AccessToken is reported as
#: valid for. Matches a comfortable buffer over typical MCP session
#: lifetimes (initialize + tool calls) without being so long that a
#: cached token survives a key rotation by hours. The MCP SDK's
#: ``BearerAuthBackend`` checks ``expires_at < now`` ; we set this to
#: ``now + API_KEY_TOKEN_TTL_SECONDS`` per request, so a rotated key
#: takes effect on the very next request (the OLD key's
#: ``verify_token`` returns None at that point because the configured
#: secret has changed).
API_KEY_TOKEN_TTL_SECONDS: Final[int] = 3600


@dataclass(frozen=True)
class McpUser:
    """Resolved identity surfaced to tool bodies."""

    username: str
    role: str

    @property
    def is_admin(self) -> bool:
        return self.role == ROLE_ADMIN


# Per-async-task user context — set by :class:`McpTokenVerifier.verify_token`
# right before the tool body runs, read by :func:`get_current_user` from
# inside the tool. ContextVar (not threading.local) because FastMCP runs
# tools in async tasks ; ContextVar copies properly across `await` boundaries.
_current_user: ContextVar[McpUser | None] = ContextVar("mcp_current_user", default=None)


class McpAuthError(Exception):
    """Raised when JWT verification fails — translated to 401 by the SDK."""


class McpForbiddenError(PermissionError):
    """Raised by :func:`require_role` — translated to a tool error."""


class McpTokenVerifier(TokenVerifier):
    """Hand the MCP SDK a verifier that delegates to our existing JWT module.

    Returns ``None`` on any verification failure (matches the SDK's
    contract — None is "reject"). Successful verifications stash the
    resolved (username, role) in :data:`_current_user` so downstream
    tool code can pull it via :func:`get_current_user` without re-parsing
    the token.

    The :class:`AccessToken` payload returned to the SDK carries the
    username + the single role as the OAuth ``scopes`` field — keeps
    integration with the SDK's RFC 9728 resource-metadata discovery
    consistent (``required_scopes=[ROLE_USER]`` on a tool would Just Work
    if we ever switch to per-tool scope checks).
    """

    async def verify_token(self, token: str) -> AccessToken | None:
        # Path 1 : static API key (machine-to-machine).
        # Mirrors :java:`ApiKeyAuthenticationFilter` running BEFORE the JWT
        # filter — the API key takes precedence over JWT. Used by Claude
        # MCP clients that send ``X-API-Key: demo-api-key-2026`` (rewritten
        # to ``Authorization: Bearer demo-api-key-2026`` by
        # :class:`ApiKeyMiddleware`) and by direct callers that pass the
        # API key as a Bearer token. The API key always grants admin
        # scopes — same as the Java filter granting ROLE_ADMIN.
        api_key = get_settings().auth.api_key
        if api_key and token == api_key:
            _current_user.set(McpUser(username=API_KEY_USERNAME, role=ROLE_ADMIN))
            return AccessToken(
                token=token,
                client_id=API_KEY_USERNAME,
                scopes=[ROLE_USER, ROLE_ADMIN],
                expires_at=int(time.time()) + API_KEY_TOKEN_TTL_SECONDS,
            )

        # Path 2 : JWT bearer token (interactive).
        settings = get_settings()
        try:
            claims = decode_token(settings.jwt, token, expected_type=ACCESS_TOKEN)
        except JwtError as exc:
            logger.info("mcp_auth_token_rejected reason=%s", exc)
            return None

        username = str(claims.get("sub") or "")
        role = str(claims.get("role") or "")
        if not username or not role:
            logger.info("mcp_auth_claims_incomplete sub=%r role=%r", username, role)
            return None

        # Stash for the tool body to read. ContextVar is the right primitive
        # here because the MCP SDK runs each tool call as a separate asyncio
        # task ; a thread-local would not propagate across `await`.
        _current_user.set(McpUser(username=username, role=role))

        # Echo the role through OAuth `scopes` so the SDK's RFC 9728 metadata
        # surfaces the role to clients that introspect. Admin is treated as
        # a superset of viewer — admins carry BOTH scopes so the SDK's
        # ``required_scopes=[ROLE_USER]`` gate (declared in mount.py) accepts
        # admin requests too. The role-specific gates inside individual
        # tools (require_role(ROLE_ADMIN)) still kick in for admin-only ones.
        scopes = [ROLE_USER, ROLE_ADMIN] if role == ROLE_ADMIN else [role]
        return AccessToken(
            token=token,
            client_id=username,
            scopes=scopes,
            expires_at=int(claims.get("exp") or (time.time() + 60)),
        )


def get_current_user() -> McpUser:
    """Return the user context set by :class:`McpTokenVerifier`.

    Raises :class:`McpAuthError` if no user is set — defence in depth
    against a tool body running outside the verified-call path (would
    indicate a wiring bug, not a malicious caller). The mount middleware
    guarantees verification runs first when streamable-http is used.
    """
    user = _current_user.get()
    if user is None:
        raise McpAuthError("No authenticated user — token verifier did not run")
    return user


def set_current_user(user: McpUser | None) -> None:
    """Test hook — directly set / clear the contextvar."""
    _current_user.set(user)


def require_role(role: str) -> None:
    """Raise :class:`McpForbiddenError` if the current user lacks ``role``.

    Called inline at the top of admin-only tools (mirrors the Java
    sibling's ``@PreAuthorize`` annotations on ``get_health_detail`` +
    ``trigger_chaos_experiment``).
    """
    user = get_current_user()
    if user.role != role:
        raise McpForbiddenError(f"Tool requires role {role}, current role is {user.role}")
