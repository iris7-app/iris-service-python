"""Auth dependency tests — current_user + require_role on a probe router."""

from __future__ import annotations

from typing import Annotated

import pytest
from fastapi import APIRouter, Depends, FastAPI
from httpx import AsyncClient

from iris_service.auth.deps import (
    AuthenticatedUser,
    current_user,
    require_role,
)
from iris_service.auth.jwt import issue_access_token
from iris_service.config.settings import get_settings


def _build_probe(app: FastAPI) -> APIRouter:
    """Mount minimal /probe/* routes that exercise current_user + require_role."""
    router = APIRouter(prefix="/probe")

    @router.get("/me")
    async def me(user: Annotated[AuthenticatedUser, Depends(current_user)]) -> dict[str, str]:
        return {"username": user.username, "role": user.role}

    @router.get("/admin")
    async def admin(
        user: Annotated[AuthenticatedUser, Depends(require_role("ROLE_ADMIN"))],
    ) -> dict[str, str]:
        return {"username": user.username}

    app.include_router(router)
    return router


@pytest.mark.asyncio
async def test_current_user_returns_401_without_bearer(client: AsyncClient, app: FastAPI) -> None:
    _build_probe(app)
    response = await client.get("/probe/me")
    assert response.status_code == 401
    assert response.headers.get("www-authenticate") == "Bearer"


@pytest.mark.asyncio
async def test_current_user_returns_401_for_invalid_token(client: AsyncClient, app: FastAPI) -> None:
    _build_probe(app)
    response = await client.get(
        "/probe/me",
        headers={"Authorization": "Bearer not-a-real-jwt"},
    )
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_current_user_decodes_valid_access_token(client: AsyncClient, app: FastAPI) -> None:
    _build_probe(app)
    settings = get_settings()
    token, _ = issue_access_token(settings.jwt, username="alice", role="ROLE_USER")
    response = await client.get(
        "/probe/me",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200
    assert response.json() == {"username": "alice", "role": "ROLE_USER"}


@pytest.mark.asyncio
async def test_current_user_rejects_refresh_token_on_access_route(client: AsyncClient, app: FastAPI) -> None:
    """A refresh token MUST NOT be accepted as an access token."""
    _build_probe(app)
    from iris_service.auth.jwt import issue_refresh_token

    settings = get_settings()
    refresh, _ = issue_refresh_token(settings.jwt, username="alice", role="ROLE_USER")
    response = await client.get(
        "/probe/me",
        headers={"Authorization": f"Bearer {refresh}"},
    )
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_require_role_returns_403_for_wrong_role(client: AsyncClient, app: FastAPI) -> None:
    _build_probe(app)
    settings = get_settings()
    token, _ = issue_access_token(settings.jwt, username="alice", role="ROLE_USER")
    response = await client.get(
        "/probe/admin",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 403
    assert "ROLE_ADMIN" in response.json()["detail"]


@pytest.mark.asyncio
async def test_require_role_passes_for_matching_role(client: AsyncClient, app: FastAPI) -> None:
    _build_probe(app)
    settings = get_settings()
    token, _ = issue_access_token(settings.jwt, username="bob", role="ROLE_ADMIN")
    response = await client.get(
        "/probe/admin",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200
    assert response.json() == {"username": "bob"}
