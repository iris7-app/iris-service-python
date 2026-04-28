"""Auth router integration with in-memory SQLite — login + refresh flows."""

from __future__ import annotations

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from iris_service.auth.models import AppUser
from iris_service.auth.passwords import hash_password


@pytest.fixture
async def seeded_admin(db_session: AsyncSession) -> AppUser:
    """Seed an admin user before each test."""
    user = AppUser(
        username="admin",
        password_hash=hash_password("admin-pass"),
        role="ROLE_ADMIN",
        enabled=True,
    )
    db_session.add(user)
    await db_session.flush()
    return user


@pytest.mark.asyncio
async def test_login_with_valid_credentials_returns_token_pair(
    client: AsyncClient,
    seeded_admin: AppUser,
) -> None:
    response = await client.post(
        "/auth/login",
        json={"username": "admin", "password": "admin-pass"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["accessToken"]
    assert body["refreshToken"]
    assert body["tokenType"] == "Bearer"
    assert body["expiresIn"] > 0


@pytest.mark.asyncio
async def test_login_with_wrong_password_returns_401(
    client: AsyncClient,
    seeded_admin: AppUser,
) -> None:
    response = await client.post(
        "/auth/login",
        json={"username": "admin", "password": "wrong"},
    )
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_login_with_unknown_user_returns_401(client: AsyncClient) -> None:
    response = await client.post(
        "/auth/login",
        json={"username": "ghost", "password": "anypwd"},
    )
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_login_with_disabled_user_returns_401(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    db_session.add(
        AppUser(
            username="disabled",
            password_hash=hash_password("any"),
            role="ROLE_USER",
            enabled=False,
        )
    )
    await db_session.flush()

    response = await client.post(
        "/auth/login",
        json={"username": "disabled", "password": "anypwd"},
    )
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_refresh_rotates_token(
    client: AsyncClient,
    seeded_admin: AppUser,
) -> None:
    """First refresh succeeds + issues new pair ; second use of old token fails."""
    login = await client.post(
        "/auth/login",
        json={"username": "admin", "password": "admin-pass"},
    )
    refresh1 = login.json()["refreshToken"]

    # First refresh : OK
    refresh_response = await client.post(
        "/auth/refresh",
        json={"refreshToken": refresh1},
    )
    assert refresh_response.status_code == 200
    refresh2 = refresh_response.json()["refreshToken"]
    assert refresh2 != refresh1  # rotation : new token

    # Replay attack : reuse the OLD refresh token → 401
    replay_response = await client.post(
        "/auth/refresh",
        json={"refreshToken": refresh1},
    )
    assert replay_response.status_code == 401


@pytest.mark.asyncio
async def test_refresh_with_invalid_token_returns_401(client: AsyncClient) -> None:
    response = await client.post(
        "/auth/refresh",
        json={"refreshToken": "not-a-real-jwt"},
    )
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_refresh_rejects_access_token(
    client: AsyncClient,
    seeded_admin: AppUser,
) -> None:
    """Access token is NOT a refresh token — must be rejected."""
    login = await client.post(
        "/auth/login",
        json={"username": "admin", "password": "admin-pass"},
    )
    access = login.json()["accessToken"]

    response = await client.post(
        "/auth/refresh",
        json={"refreshToken": access},  # wrong type
    )
    assert response.status_code == 401
