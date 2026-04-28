"""GET /auth/me endpoint tests."""

from __future__ import annotations

import pytest
from httpx import AsyncClient

from iris_service.auth.jwt import issue_access_token
from iris_service.config.settings import get_settings


@pytest.mark.asyncio
async def test_me_returns_401_without_bearer(client: AsyncClient) -> None:
    response = await client.get("/auth/me")
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_me_returns_user_claims_with_valid_token(client: AsyncClient) -> None:
    settings = get_settings()
    token, _ = issue_access_token(settings.jwt, username="alice", role="ROLE_USER")
    response = await client.get(
        "/auth/me",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200
    assert response.json() == {"username": "alice", "role": "ROLE_USER"}


@pytest.mark.asyncio
async def test_me_rejects_refresh_token(client: AsyncClient) -> None:
    """A refresh token must NOT pass /me (access tokens only)."""
    from iris_service.auth.jwt import issue_refresh_token

    settings = get_settings()
    refresh, _ = issue_refresh_token(settings.jwt, username="alice", role="ROLE_USER")
    response = await client.get(
        "/auth/me",
        headers={"Authorization": f"Bearer {refresh}"},
    )
    assert response.status_code == 401
