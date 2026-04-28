"""JWT issuance + verification — pyjwt wrapper.

Migrated 2026-04-25 from python-jose (semi-abandoned, last release 2022-12)
to pyjwt (actively maintained by Jose Padilla et al.). API is similar :
``jwt.encode(payload, key, algorithm=...)`` + ``jwt.decode(token, key,
algorithms=[...])``. Exception class renamed JWTError → InvalidTokenError ;
ExpiredSignatureError stays.

Mirrors Java's JwtTokenProvider. Two token types :
- Access token : short-lived (15 min default), bearer in Authorization header
- Refresh token : long-lived (30 days default), used to mint new access tokens

Claims :
- sub : username
- role : single role string (= Spring Security ROLE_*)
- exp : expiry (Unix seconds)
- iat : issued at
- type : "access" | "refresh" (segregates token uses ; refresh can't auth API
  calls and access can't be exchanged for a new access)
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any, Final, Literal

import jwt
from jwt.exceptions import ExpiredSignatureError, InvalidTokenError

from iris_service.config.settings import JwtSettings

# Token type discriminator — Literal narrows the universe to exactly these two
# strings. mypy refuses `decode_token(..., expected_type="acces")` (typo) at
# static-analysis time instead of at runtime when the token gets rejected.
# PEP 695 `type` keyword (Python 3.12+) — lazy evaluation + cleaner than the
# older `TokenType: TypeAlias = ...` form.
type TokenType = Literal["access", "refresh"]

# Concrete values — Final[TokenType] = the literal IS the type, so
# `ACCESS_TOKEN: Final[TokenType] = "access"` enforces both immutability
# (Final) and value-set membership (Literal).
ACCESS_TOKEN: Final[TokenType] = "access"  # noqa: S105 — token-type discriminator string, not a credential
REFRESH_TOKEN: Final[TokenType] = "refresh"  # noqa: S105 — same: discriminator string

# JWT claims dict is heterogeneous (str + int + UUID-as-str), so dict[str, Any]
# is honest. Aliased so consumers read `JwtClaims` instead of the noisy form.
type JwtClaims = dict[str, Any]


class JwtError(Exception):
    """Raised when token verification fails (invalid signature, expired,
    wrong type, etc.). API layer maps to 401."""


def issue_access_token(settings: JwtSettings, username: str, role: str) -> tuple[str, int]:
    """Returns (token, expires_in_seconds)."""
    expires_in = settings.access_token_expire_minutes * 60
    return _encode(settings, username, role, ACCESS_TOKEN, expires_in), expires_in


def issue_refresh_token(settings: JwtSettings, username: str, role: str) -> tuple[str, int]:
    """Returns (token, expires_in_seconds)."""
    expires_in = settings.refresh_token_expire_days * 24 * 60 * 60
    return _encode(settings, username, role, REFRESH_TOKEN, expires_in), expires_in


def decode_token(settings: JwtSettings, token: str, expected_type: TokenType) -> JwtClaims:
    """Verify signature + expiry + token type ; return claims dict.

    Raises JwtError on any verification failure.
    """
    try:
        claims: JwtClaims = jwt.decode(
            token,
            settings.secret,
            algorithms=[settings.algorithm],
        )
    except ExpiredSignatureError as exc:
        raise JwtError("Token expired") from exc
    except InvalidTokenError as exc:
        raise JwtError(f"Invalid token: {exc}") from exc

    if claims.get("type") != expected_type:
        raise JwtError(f"Wrong token type: expected {expected_type}, got {claims.get('type')}")
    return claims


def _encode(
    settings: JwtSettings,
    username: str,
    role: str,
    token_type: TokenType,
    expires_in_seconds: int,
) -> str:
    now = datetime.now(UTC)
    claims = {
        "sub": username,
        "role": role,
        "type": token_type,
        # `jti` (JWT ID) : UUID v4 to guarantee unique tokens even when issued
        # within the same Unix second. Otherwise the iat-based payload is
        # identical → identical JWT → DB UNIQUE constraint failure on
        # refresh_token.token (rotation flow issues 2 tokens for same user
        # in <1 ms during refresh).
        "jti": str(uuid.uuid4()),
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(seconds=expires_in_seconds)).timestamp()),
    }
    encoded: str = jwt.encode(claims, settings.secret, algorithm=settings.algorithm)
    return encoded
