"""Microbenchmarks for security-critical + frequently-called code paths.

Per ADR-0007 §13 — equivalent of Java's JMH. Tracked over time via
``pytest --benchmark-json=output.json`` + the CI `bench` job that
compares deltas across MRs.

Hot paths benched :
- JWT encode (issue_access_token + issue_refresh_token)
- JWT decode (decode_token round-trip)
- bcrypt hash (hash_password — _COST=12, intentionally slow)
- bcrypt verify (verify_password — same cost)

Why these :
- JWT verify runs on EVERY authenticated request (worst-case latency
  driver) — a regression here directly raises p99 latency.
- bcrypt hash runs on /auth/login — a regression makes login slower
  (= worse UX) AND it's the right knob to bump for security
  (cost factor 12 → 13 doubles time AND brute-force resistance).
- bcrypt verify runs on every login attempt — same impact as hash.

Excluded :
- Pydantic model validation (pydantic-core is C-implemented, ~50 ns per
  small DTO — not in our control).
- Repository SQLAlchemy queries — DB-bound, micro-bench would just measure
  SQLite/Postgres latency, not our code.
- Kafka request-reply — async + I/O, needs testcontainers (covered by
  integration suite separately).

Excluded from regular `pytest` runs : the `benchmarks` mark + addopts
`-m 'not benchmarks'` keeps unit-test runtime fast.
"""

from __future__ import annotations

import pytest

from iris_service.auth.jwt import (
    ACCESS_TOKEN,
    REFRESH_TOKEN,
    decode_token,
    issue_access_token,
    issue_refresh_token,
)
from iris_service.auth.passwords import hash_password, verify_password
from iris_service.config.settings import JwtSettings

pytestmark = pytest.mark.benchmarks

_jwt_settings = JwtSettings(
    secret="benchmark-test-secret-very-long-for-hs256-signing-purposes",
    algorithm="HS256",
    access_token_expire_minutes=15,
    refresh_token_expire_days=30,
)


# ── JWT encode ────────────────────────────────────────────────────────────────


def test_bench_issue_access_token(benchmark) -> None:
    """JWT HS256 encode + uuid.uuid4() jti generation. Target : < 100 µs."""
    benchmark(issue_access_token, _jwt_settings, "alice", "ROLE_USER")


def test_bench_issue_refresh_token(benchmark) -> None:
    """Same as above but with the longer expiry — should be identical timing."""
    benchmark(issue_refresh_token, _jwt_settings, "bob", "ROLE_ADMIN")


# ── JWT decode (verify) ───────────────────────────────────────────────────────


def test_bench_decode_access_token(benchmark) -> None:
    """JWT HS256 decode + signature verify + claims dict build.
    Runs on every authenticated request — most critical hot path.
    Target : < 200 µs.
    """
    token, _ = issue_access_token(_jwt_settings, "alice", "ROLE_USER")
    benchmark(decode_token, _jwt_settings, token, ACCESS_TOKEN)


def test_bench_decode_refresh_token(benchmark) -> None:
    """Same as above for refresh token type."""
    token, _ = issue_refresh_token(_jwt_settings, "bob", "ROLE_ADMIN")
    benchmark(decode_token, _jwt_settings, token, REFRESH_TOKEN)


# ── bcrypt ────────────────────────────────────────────────────────────────────


def test_bench_hash_password(benchmark) -> None:
    """bcrypt hash with _COST=12. INTENTIONALLY slow (~250 ms on M3) to
    resist brute force. Single-iteration benchmark (no min_rounds) since
    each call burns CPU.
    """
    benchmark.pedantic(hash_password, args=("plaintext-password-of-reasonable-length",), iterations=1, rounds=3)


def test_bench_verify_password(benchmark) -> None:
    """bcrypt verify — same cost as hash. Runs on every login attempt."""
    plain = "plaintext-password-of-reasonable-length"
    hashed = hash_password(plain)
    benchmark.pedantic(verify_password, args=(plain, hashed), iterations=1, rounds=3)
