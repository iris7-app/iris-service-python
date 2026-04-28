"""ApiKeyMiddleware tests — header match / miss / mismatch + REST integration.

Exercises three scenarios :

1. ``X-API-Key`` matches ``IRIS_API_KEY`` → ``current_user`` returns
   the synthetic admin principal without any JWT in flight.
2. ``X-API-Key`` missing → fall through to JWT path (or 401 if no JWT).
3. ``X-API-Key`` present but doesn't match → fall through to JWT path
   (no 401 from the middleware itself ; only a 401 if JWT is also
   missing/invalid).

Plus :

4. ``require_role("ROLE_ADMIN")`` accepts the API-key principal.
5. ``require_role("ROLE_USER")`` accepts the admin principal (admin-as-
   superset semantics).

These test the middleware in isolation through a probe router. The MCP
end-to-end path is covered by tests/integration/test_api_key_e2e.py.
"""

from __future__ import annotations

from typing import Annotated

import pytest
from fastapi import APIRouter, Depends, FastAPI
from httpx import AsyncClient

from iris_service.auth.api_key import API_KEY_USERNAME
from iris_service.auth.deps import (
    AuthenticatedUser,
    current_user,
    require_role,
)
from iris_service.auth.jwt import issue_access_token
from iris_service.config.settings import get_settings


def _build_probe(app: FastAPI) -> APIRouter:
    """Mount minimal probe routes mirroring test_deps.py."""
    router = APIRouter(prefix="/api-key-probe")

    @router.get("/me")
    async def me(user: Annotated[AuthenticatedUser, Depends(current_user)]) -> dict[str, str]:
        return {"username": user.username, "role": user.role}

    @router.get("/admin")
    async def admin(
        user: Annotated[AuthenticatedUser, Depends(require_role("ROLE_ADMIN"))],
    ) -> dict[str, str]:
        return {"username": user.username}

    @router.get("/user")
    async def user_route(
        user: Annotated[AuthenticatedUser, Depends(require_role("ROLE_USER"))],
    ) -> dict[str, str]:
        return {"username": user.username}

    app.include_router(router)
    return router


@pytest.mark.asyncio
async def test_matching_api_key_returns_admin_principal(client: AsyncClient, app: FastAPI) -> None:
    """X-API-Key match short-circuits JWT decode and yields admin."""
    _build_probe(app)
    settings = get_settings()
    response = await client.get(
        "/api-key-probe/me",
        headers={"X-API-Key": settings.auth.api_key},
    )
    assert response.status_code == 200
    assert response.json() == {"username": API_KEY_USERNAME, "role": "ROLE_ADMIN"}


@pytest.mark.asyncio
async def test_missing_api_key_falls_through_to_jwt_path(client: AsyncClient, app: FastAPI) -> None:
    """No X-API-Key + no Bearer → JWT path responds with 401."""
    _build_probe(app)
    response = await client.get("/api-key-probe/me")
    # Standard JWT path 401 — the middleware is invisible when the header
    # is absent.
    assert response.status_code == 401
    assert response.headers.get("www-authenticate") == "Bearer"


@pytest.mark.asyncio
async def test_wrong_api_key_falls_through_to_jwt_path(client: AsyncClient, app: FastAPI) -> None:
    """Wrong X-API-Key + no Bearer → JWT path 401, NOT a middleware 401.

    Mirrors Java filter : header mismatch is silent — no error response,
    just falls through. The 401 here comes from the JWT path that finds
    no Bearer token, not from the API-key middleware itself.
    """
    _build_probe(app)
    response = await client.get(
        "/api-key-probe/me",
        headers={"X-API-Key": "wrong-key"},
    )
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_wrong_api_key_with_valid_jwt_uses_jwt(client: AsyncClient, app: FastAPI) -> None:
    """Wrong X-API-Key + valid Bearer → JWT path authenticates the user."""
    _build_probe(app)
    settings = get_settings()
    token, _ = issue_access_token(settings.jwt, username="alice", role="ROLE_USER")
    response = await client.get(
        "/api-key-probe/me",
        headers={
            "X-API-Key": "wrong-key",
            "Authorization": f"Bearer {token}",
        },
    )
    assert response.status_code == 200
    assert response.json() == {"username": "alice", "role": "ROLE_USER"}


@pytest.mark.asyncio
async def test_api_key_takes_precedence_over_jwt(client: AsyncClient, app: FastAPI) -> None:
    """When BOTH X-API-Key and Bearer JWT are present, API-key wins.

    Matches Java's ``ApiKeyAuthenticationFilter`` running BEFORE
    ``JwtAuthenticationFilter`` — once API-key auth has populated the
    SecurityContext, the JWT filter sees an already-authenticated request
    and skips. This guarantees the same precedence on the Python side.
    """
    _build_probe(app)
    settings = get_settings()
    # JWT for "alice/ROLE_USER" — would be a different principal if used.
    token, _ = issue_access_token(settings.jwt, username="alice", role="ROLE_USER")
    response = await client.get(
        "/api-key-probe/me",
        headers={
            "X-API-Key": settings.auth.api_key,
            "Authorization": f"Bearer {token}",
        },
    )
    assert response.status_code == 200
    # API-key principal, NOT the JWT principal.
    assert response.json() == {"username": API_KEY_USERNAME, "role": "ROLE_ADMIN"}


@pytest.mark.asyncio
async def test_api_key_grants_admin_role_check(client: AsyncClient, app: FastAPI) -> None:
    """API-key principal passes ``require_role("ROLE_ADMIN")``."""
    _build_probe(app)
    settings = get_settings()
    response = await client.get(
        "/api-key-probe/admin",
        headers={"X-API-Key": settings.auth.api_key},
    )
    assert response.status_code == 200
    assert response.json() == {"username": API_KEY_USERNAME}


@pytest.mark.asyncio
async def test_api_key_satisfies_user_role_via_admin_superset(client: AsyncClient, app: FastAPI) -> None:
    """API-key admin passes ``require_role("ROLE_USER")`` (admin ⊇ user).

    Without admin-as-superset semantics, an API-key client (admin) would
    403 on a plain user endpoint, breaking parity with Java's
    ``hasRole("USER")`` which an admin always satisfies.
    """
    _build_probe(app)
    settings = get_settings()
    response = await client.get(
        "/api-key-probe/user",
        headers={"X-API-Key": settings.auth.api_key},
    )
    assert response.status_code == 200
    assert response.json() == {"username": API_KEY_USERNAME}
