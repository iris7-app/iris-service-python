"""Auth router — /auth/login + /auth/refresh.

Mirrors Java's AuthController. Refresh token rotation : each /auth/refresh
issues a NEW refresh token AND revokes the previous one (defense in depth
against stolen refresh tokens).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from iris_service.auth.deps import AuthenticatedUser, current_user
from iris_service.auth.dtos import LoginRequest, RefreshRequest, TokenResponse
from iris_service.auth.jwt import REFRESH_TOKEN, JwtError, decode_token, issue_access_token, issue_refresh_token
from iris_service.auth.models import AppUser, RefreshToken
from iris_service.auth.passwords import verify_password
from iris_service.config.settings import Settings, get_settings
from iris_service.db.base import get_db_session

router = APIRouter(prefix="/auth", tags=["Auth"])


@router.get("/me")
async def me(
    user: Annotated[AuthenticatedUser, Depends(current_user)],
) -> dict[str, str]:
    """Return the currently authenticated user's username + role.

    Used by the Angular frontend to populate the topbar after login (no
    extra DB lookup — claims come from the validated access-token JWT).
    Returns 401 if the Bearer token is missing / invalid / expired.
    """
    return {"username": user.username, "role": user.role}


@router.post("/login", response_model=TokenResponse)
async def login(
    body: LoginRequest,
    db: Annotated[AsyncSession, Depends(get_db_session)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> TokenResponse:
    """Authenticate username/password ; issue access + refresh tokens.

    Returns 401 on bad credentials (constant-time : same response for
    'unknown user' and 'wrong password' to prevent user enumeration).
    """
    user = (await db.execute(select(AppUser).where(AppUser.username == body.username))).scalar_one_or_none()
    if user is None or not user.enabled or not verify_password(body.password, user.password_hash):
        # Same response for any failure — defense against username enumeration
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid credentials",
        )

    return await _issue_token_pair(db, settings, user.username, user.role)


@router.post("/refresh", response_model=TokenResponse)
async def refresh(
    body: RefreshRequest,
    db: Annotated[AsyncSession, Depends(get_db_session)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> TokenResponse:
    """Exchange a refresh token for a new (access, refresh) pair.

    The old refresh token is REVOKED (rotation). If the same refresh token
    is presented twice (replay attack), the second call fails with 401.
    """
    # 1. Verify token signature + type
    try:
        claims = decode_token(settings.jwt, body.refresh_token, expected_type=REFRESH_TOKEN)
    except JwtError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc

    # 2. Check it's still in the registry + not revoked
    record = (
        await db.execute(select(RefreshToken).where(RefreshToken.token == body.refresh_token))
    ).scalar_one_or_none()
    # Normalise tz : SQLite returns naive datetimes ; Postgres returns aware.
    # Compare via assume-UTC : if record.expires_at is naive, treat it as UTC.
    expires_at = record.expires_at if record else None
    now = datetime.now(UTC)
    if expires_at is not None and expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=UTC)
    if record is None or record.revoked or (expires_at is not None and expires_at < now):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Refresh token revoked or expired",
        )

    # 3. Rotate : revoke the old + mint new pair
    record.revoked = True
    await db.flush()

    username = claims["sub"]
    role = claims["role"]
    return await _issue_token_pair(db, settings, username, role)


async def _issue_token_pair(
    db: AsyncSession,
    settings: Settings,
    username: str,
    role: str,
) -> TokenResponse:
    """Mint (access, refresh) pair and persist the refresh token."""
    access_token, access_ttl = issue_access_token(settings.jwt, username, role)
    refresh_token, refresh_ttl = issue_refresh_token(settings.jwt, username, role)

    # Persist the refresh token so we can revoke it on rotation/logout.
    db.add(
        RefreshToken(
            token=refresh_token,
            username=username,
            expires_at=datetime.now(UTC) + timedelta(seconds=refresh_ttl),
        )
    )
    await db.flush()

    return TokenResponse(
        access_token=access_token,
        refresh_token=refresh_token,
        token_type="Bearer",  # noqa: S106 — OAuth2 / RFC 6750 spec value, not a password
        expires_in=access_ttl,
    )
