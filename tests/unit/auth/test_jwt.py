"""JWT issuance + decode unit tests."""

from __future__ import annotations

import time

import pytest

from iris_service.auth.jwt import (
    ACCESS_TOKEN,
    REFRESH_TOKEN,
    JwtError,
    decode_token,
    issue_access_token,
    issue_refresh_token,
)
from iris_service.config.settings import JwtSettings


@pytest.fixture
def jwt_settings() -> JwtSettings:
    return JwtSettings(
        secret="test-secret-do-not-use-in-prod-very-long-key-for-hs256",
        algorithm="HS256",
        access_token_expire_minutes=15,
        refresh_token_expire_days=30,
    )


def test_issue_access_token_round_trip(jwt_settings: JwtSettings) -> None:
    token, ttl = issue_access_token(jwt_settings, "alice", "ROLE_ADMIN")
    claims = decode_token(jwt_settings, token, expected_type=ACCESS_TOKEN)
    assert claims["sub"] == "alice"
    assert claims["role"] == "ROLE_ADMIN"
    assert claims["type"] == ACCESS_TOKEN
    assert ttl == 15 * 60


def test_issue_refresh_token_round_trip(jwt_settings: JwtSettings) -> None:
    token, ttl = issue_refresh_token(jwt_settings, "bob", "ROLE_USER")
    claims = decode_token(jwt_settings, token, expected_type=REFRESH_TOKEN)
    assert claims["sub"] == "bob"
    assert claims["type"] == REFRESH_TOKEN
    assert ttl == 30 * 24 * 60 * 60


def test_decode_rejects_wrong_token_type(jwt_settings: JwtSettings) -> None:
    """Access token can't be used where refresh expected (and vice versa)."""
    access, _ = issue_access_token(jwt_settings, "alice", "ROLE_USER")
    with pytest.raises(JwtError, match="Wrong token type"):
        decode_token(jwt_settings, access, expected_type=REFRESH_TOKEN)


def test_decode_rejects_invalid_signature(jwt_settings: JwtSettings) -> None:
    token, _ = issue_access_token(jwt_settings, "alice", "ROLE_USER")
    # Tamper with the token
    tampered = token + "x"
    with pytest.raises(JwtError, match="Invalid token"):
        decode_token(jwt_settings, tampered, expected_type=ACCESS_TOKEN)


def test_decode_rejects_wrong_secret(jwt_settings: JwtSettings) -> None:
    token, _ = issue_access_token(jwt_settings, "alice", "ROLE_USER")
    other = JwtSettings(secret="different-secret-key-cannot-decode-other")
    with pytest.raises(JwtError, match="Invalid token"):
        decode_token(other, token, expected_type=ACCESS_TOKEN)


def test_decode_rejects_expired_token() -> None:
    """Expired token must fail with `Token expired` message."""
    settings = JwtSettings(
        secret="test-secret-very-long-for-hs256-rejection-test",
        access_token_expire_minutes=0,  # expires immediately
    )
    token, _ = issue_access_token(settings, "alice", "ROLE_USER")
    # Sleep just past the iat second so exp < now
    time.sleep(1.1)
    with pytest.raises(JwtError, match="expired"):
        decode_token(settings, token, expected_type=ACCESS_TOKEN)
