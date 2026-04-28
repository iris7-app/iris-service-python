"""Unit tests for :mod:`iris_service.mcp.auth`."""

from __future__ import annotations

import time

import pytest

from iris_service.auth.jwt import issue_access_token, issue_refresh_token
from iris_service.config.settings import get_settings
from iris_service.mcp.auth import (
    ROLE_ADMIN,
    ROLE_USER,
    McpAuthError,
    McpForbiddenError,
    McpTokenVerifier,
    McpUser,
    get_current_user,
    require_role,
    set_current_user,
)


@pytest.fixture(autouse=True)
def _reset_user_ctx() -> None:
    set_current_user(None)


def test_role_constants() -> None:
    assert ROLE_USER == "ROLE_USER"
    assert ROLE_ADMIN == "ROLE_ADMIN"


def test_user_is_admin_helper() -> None:
    assert McpUser(username="x", role=ROLE_ADMIN).is_admin is True
    assert McpUser(username="x", role=ROLE_USER).is_admin is False


def test_get_current_user_raises_when_unset() -> None:
    set_current_user(None)
    with pytest.raises(McpAuthError):
        get_current_user()


def test_set_get_round_trip() -> None:
    u = McpUser(username="alice", role=ROLE_USER)
    set_current_user(u)
    assert get_current_user() == u


def test_require_role_passes_for_match() -> None:
    set_current_user(McpUser(username="a", role=ROLE_ADMIN))
    require_role(ROLE_ADMIN)  # no raise


def test_require_role_raises_for_mismatch() -> None:
    set_current_user(McpUser(username="a", role=ROLE_USER))
    with pytest.raises(McpForbiddenError):
        require_role(ROLE_ADMIN)


@pytest.mark.asyncio
async def test_token_verifier_valid_access_token() -> None:
    settings = get_settings()
    token, _ = issue_access_token(settings.jwt, "bob", ROLE_USER)
    verifier = McpTokenVerifier()
    result = await verifier.verify_token(token)
    assert result is not None
    assert result.client_id == "bob"
    assert ROLE_USER in result.scopes
    # Side-effect : context stash worked.
    user = get_current_user()
    assert user.username == "bob"
    assert user.role == ROLE_USER


@pytest.mark.asyncio
async def test_token_verifier_rejects_garbage() -> None:
    verifier = McpTokenVerifier()
    assert await verifier.verify_token("not-a-jwt") is None


@pytest.mark.asyncio
async def test_token_verifier_rejects_refresh_token() -> None:
    """Refresh tokens MUST NOT authenticate API calls (token-type discriminator)."""
    settings = get_settings()
    refresh, _ = issue_refresh_token(settings.jwt, "bob", ROLE_USER)
    verifier = McpTokenVerifier()
    assert await verifier.verify_token(refresh) is None


@pytest.mark.asyncio
async def test_token_verifier_admin_role_propagates() -> None:
    settings = get_settings()
    token, _ = issue_access_token(settings.jwt, "admin-bob", ROLE_ADMIN)
    verifier = McpTokenVerifier()
    result = await verifier.verify_token(token)
    assert result is not None
    user = get_current_user()
    assert user.is_admin is True


@pytest.mark.asyncio
async def test_token_verifier_admin_carries_both_scopes() -> None:
    """Admin tokens carry BOTH ROLE_USER and ROLE_ADMIN.

    The SDK's ``required_scopes=[ROLE_USER]`` (set in mount.py) gate must
    accept admin-issued tokens ; the per-tool ``require_role(ROLE_ADMIN)``
    in admin-only tools still discriminates.
    """
    settings = get_settings()
    token, _ = issue_access_token(settings.jwt, "admin", ROLE_ADMIN)
    verifier = McpTokenVerifier()
    result = await verifier.verify_token(token)
    assert result is not None
    assert ROLE_USER in result.scopes
    assert ROLE_ADMIN in result.scopes


@pytest.mark.asyncio
async def test_token_verifier_user_only_carries_user_scope() -> None:
    """Plain ROLE_USER tokens carry only [ROLE_USER]."""
    settings = get_settings()
    token, _ = issue_access_token(settings.jwt, "u", ROLE_USER)
    verifier = McpTokenVerifier()
    result = await verifier.verify_token(token)
    assert result is not None
    assert result.scopes == [ROLE_USER]


@pytest.mark.asyncio
async def test_token_verifier_expires_at_propagates() -> None:
    settings = get_settings()
    token, _ = issue_access_token(settings.jwt, "u", ROLE_USER)
    verifier = McpTokenVerifier()
    result = await verifier.verify_token(token)
    assert result is not None
    assert result.expires_at is not None
    assert result.expires_at > int(time.time())


@pytest.mark.asyncio
async def test_token_verifier_rejects_when_role_missing() -> None:
    """Synthetic JWT with empty role must be rejected."""
    settings = get_settings()
    # Build a JWT that has type=access but role missing.
    import jwt as pyjwt

    payload = {
        "sub": "no-role",
        "role": "",
        "type": "access",
        "iat": int(time.time()),
        "exp": int(time.time()) + 60,
    }
    token = pyjwt.encode(payload, settings.jwt.secret, algorithm=settings.jwt.algorithm)
    verifier = McpTokenVerifier()
    assert await verifier.verify_token(token) is None
