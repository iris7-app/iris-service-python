"""Password hashing — direct bcrypt 4.x+ API (no passlib wrapper).

Migrated 2026-04-25 from passlib[bcrypt] (semi-abandoned, last release
2020-10) to bcrypt>=4.0 directly. This unblocks bcrypt 5.x adoption +
removes the 3.2.2 pin that passlib 1.7.4 forced (incompatible with
bcrypt 5.x because of `bcrypt.__about__` removal upstream).

Bcrypt with cost factor 12 (Java mirror's BCryptPasswordEncoder default).
Verification ≈ 250 ms on M-series Mac — brute-force-resistant. Bump cost
to 13 in 2-3 years as hardware doubles.
"""

from __future__ import annotations

from typing import Final

import bcrypt

# Cost factor 12 = ~250 ms verify on Apple M3, matches Java side.
# Each increment doubles the time → 13 next, 14 in ~5 years.
# Final[int] : mypy strict refuses any reassignment in this module — the
# cost is a security knob, not a runtime configurable.
_COST: Final[int] = 12


def hash_password(plain: str) -> str:
    """Hash a plaintext password for storage. Returns the str form of the bcrypt hash."""
    salted = bcrypt.hashpw(plain.encode("utf-8"), bcrypt.gensalt(rounds=_COST))
    return salted.decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    """Constant-time comparison of plaintext against stored bcrypt hash.

    Returns False (not raises) on malformed hashes — caller treats as
    auth failure same as wrong password.
    """
    try:
        return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))
    except ValueError:
        # Malformed hash (e.g. wrong format, garbled DB row) — treat as auth fail.
        return False
