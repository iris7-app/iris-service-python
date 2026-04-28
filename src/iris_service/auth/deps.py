"""Auth FastAPI dependencies — current_user injection from Bearer token.

Usage in routers :
```
from iris_service.auth.deps import current_user, require_role

@router.get("/protected")
async def protected(user: Annotated[AuthenticatedUser, Depends(current_user)]):
    return {"sub": user.username}

@router.post("/admin-only")
async def admin_only(user: Annotated[AuthenticatedUser, Depends(require_role("ROLE_ADMIN"))]):
    ...
```

Two authentication paths are supported transparently :

1. **JWT Bearer** — ``Authorization: Bearer <jwt>`` (standard).
2. **Static API key** — ``X-API-Key: <secret>`` (machine-to-machine
   fallback for CI / monitoring / Claude MCP). Wired via
   :class:`iris_service.auth.api_key.ApiKeyMiddleware` which
   short-circuits the JWT path on header match, populating
   ``request.state.api_key_user`` with an admin principal.

The API-key path is checked FIRST so admin tools work even when the
JWT secret is misconfigured (e.g. fresh deployment, secret rotation
in flight).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from iris_service.auth.api_key import ApiKeyPrincipal
from iris_service.auth.jwt import ACCESS_TOKEN, JwtError, decode_token
from iris_service.config.settings import Settings, get_settings


@dataclass(frozen=True)
class AuthenticatedUser:
    """Lightweight value object surfaced to routers via DI."""

    username: str
    role: str


_bearer = HTTPBearer(auto_error=False)


def current_user(
    request: Request,
    creds: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> AuthenticatedUser:
    """Decode the Bearer token (or honour an X-API-Key principal) +
    return the authenticated user.

    Resolution order :

    1. ``request.state.api_key_user`` set by
       :class:`ApiKeyMiddleware` → return immediately as admin.
    2. ``Authorization: Bearer <jwt>`` → decode + verify.
    3. Otherwise → 401.
    """
    api_key_principal = getattr(request.state, "api_key_user", None)
    if isinstance(api_key_principal, ApiKeyPrincipal):
        return AuthenticatedUser(
            username=api_key_principal.username,
            role=api_key_principal.role,
        )
    if creds is None or creds.scheme.lower() != "bearer":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing Bearer token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    try:
        claims = decode_token(settings.jwt, creds.credentials, expected_type=ACCESS_TOKEN)
    except JwtError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(exc),
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc
    return AuthenticatedUser(username=claims["sub"], role=claims["role"])


def require_role(role: str) -> Callable[[AuthenticatedUser], AuthenticatedUser]:
    """Dependency factory — returns a sub-dependency that 403s if the user
    doesn't have the required role.

    Usage : `Depends(require_role("ROLE_ADMIN"))`

    Admin-as-superset semantics : a user with ``ROLE_ADMIN`` passes
    ``require_role("ROLE_USER")`` checks too. This matches Spring
    Security's ``hasRole("USER")`` granting access to any user with the
    USER authority — admins have BOTH authorities by virtue of being
    admin (see Java's :java:`ApiKeyAuthenticationFilter` granting both
    ``ROLE_USER`` and ``ROLE_ADMIN``). Without this, an API-key client
    (granted ``ROLE_ADMIN``) would 403 on a plain ``require_role("ROLE_USER")``
    endpoint, breaking parity.
    """

    def _checker(user: Annotated[AuthenticatedUser, Depends(current_user)]) -> AuthenticatedUser:
        if user.role == role:
            return user
        # Admin includes user — admins satisfy any non-admin role check.
        if user.role == "ROLE_ADMIN":
            return user
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Requires role: {role}",
        )

    return _checker
