# ADR-0002 : Auth — JWT access + refresh token rotation, bcrypt password hashing

**Status** : Accepted
**Date** : 2026-04-25
**Sibling** : `../iris-service` (Java side, Spring Security)

## Context

iris-service-python needs an authentication mechanism that :
- Mirrors the Java side's contract (same /auth/login + /auth/refresh routes,
  same Bearer-token semantics) so the same Angular frontend can talk to either
  backend without code changes.
- Supports stateless replicas (no shared in-memory session — JWT scales
  horizontally without sticky sessions).
- Allows revocation (a leaked token must be killable without invalidating
  every user's session).
- Uses production-grade password hashing (bcrypt, NOT MD5/SHA-1/PBKDF2-with-low-rounds).

Standard Python options :

| Choice | Pros | Cons |
|---|---|---|
| **python-jose** + **passlib[bcrypt]** | Battle-tested combo, JWT + JWS + JWE, swappable backends | passlib 1.7.4 + bcrypt 5.x incompatibility (fixed by pinning bcrypt==3.2.2) |
| PyJWT + bcrypt directly | Smaller dep surface | Reinventing JWS/JWE not worth it for Demo+API |
| OAuth2 with full provider (Authlib + DB) | Standards-compliant | Massive overkill for demo ; no IDP requirement |
| FastAPI-Users / FastAPI-Login | Batteries-included | Coupled to specific user model ; harder to mirror Java contract precisely |

## Decision

**python-jose** for token issuance / decoding, **passlib[bcrypt]** for password
hashing (with bcrypt pinned to 3.2.2 to dodge the 5.x AttributeError on
`bcrypt.__about__`).

### Token model

- **Access token** : short-lived (15 min default), bearer-style, used for
  every API request. Carries `sub`, `role`, `type=access`, `exp`, `iat`,
  `jti`.
- **Refresh token** : long-lived (30 days default), used ONLY against
  /auth/refresh. Carries `sub`, `type=refresh`, `exp`, `iat`, `jti`.
  Persisted in DB (`refresh_token` table) so we can revoke individually.

### Token rotation

Every /auth/refresh call :
1. Verifies the refresh token's signature + expiry + presence in DB + not revoked.
2. **Marks the old refresh token as revoked** in DB.
3. Issues a NEW pair (access + refresh) with fresh `jti` UUIDs.
4. Persists the new refresh token.

This means : if a leaked refresh token is used by an attacker, the legitimate
user's next /auth/refresh call gets 401 (their token was revoked by the
attacker's refresh). The discrepancy is detectable + alertable.

### Why `jti` (UUID) on every token

JWT signing is deterministic : same payload + same Unix-second timestamp
produces the same token. Two refreshes within the same second would
violate the `refresh_token.token` UNIQUE constraint. `jti=uuid4()` makes
every issued token globally unique without depending on timestamp resolution.

### Password hashing

bcrypt with cost factor 12 (passlib default). Bcrypt is intentionally slow
(~250ms per verification) → brute-force resistant. The cost factor doubles
verification time per increment ; bump to 13 in 2-3 years as hardware speeds up.

`passlib.context.CryptContext(schemes=["bcrypt"], deprecated="auto")` —
the `deprecated="auto"` flag means : if we ever change the default scheme,
existing hashes are auto-rehashed on next successful login.

### Endpoint shape

```
POST /auth/login
  body: { username: str, password: str }
  resp: { accessToken: str, refreshToken: str, tokenType: "Bearer", expiresIn: int }
  errors: 401 (bad creds), 422 (validation), 429 (rate limit via slowapi)

POST /auth/refresh
  body: { refreshToken: str }
  resp: { accessToken, refreshToken, tokenType, expiresIn } (NEW pair)
  errors: 401 (invalid / revoked / expired refresh)

GET /me  (any authenticated route)
  Bearer access token in Authorization header
  resp: { username, role }
  errors: 401 (missing / invalid / expired access)
```

camelCase on the wire (matches Spring Security default + Angular
expectations). Pydantic v2 `populate_by_name=True` + `serialization_alias`
handle the snake_case ↔ camelCase mapping.

### FastAPI dependency wiring

`iris_service.auth.deps.current_user` extracts + validates the Bearer
token, returns `AppUser`. `require_role("ADMIN")` is a factory returning a
dependency that 403s on role mismatch. Used like :

```python
@router.delete("/customers/{id}")
async def delete_customer(
    id: int,
    user: Annotated[AppUser, Depends(require_role("ADMIN"))],
    ...
): ...
```

## Consequences

**Pros** :
- Stateless verification (signature + expiry) on every request — no DB hit
  for the access token.
- Revocation works for refresh tokens (DB lookup) — the most-leaked token
  type (lives 30 days vs 15 minutes).
- Token rotation makes leak-detection automatic (legitimate user's next
  refresh fails → alert).
- Same wire shape as Java side → frontend interop with zero changes.

**Cons** :
- DB hit on every /auth/refresh — acceptable since refresh is rare (~once
  per 15 min per user) vs every API call.
- Refresh token storage grows unbounded → cleanup job to delete revoked +
  expired entries (TODO : add in Étape 9 with a cron via `pg_cron` or a
  Python APScheduler).

## Alternatives considered

- **Session cookies** — rejected : couples backend to session store
  (Redis), breaks horizontal scaling without sticky sessions.
- **Opaque tokens** (random hex) — rejected : every API request needs a
  DB lookup (instead of stateless JWT signature verify). Doesn't scale.
- **Single long-lived JWT (no refresh)** — rejected : if leaked, the
  attacker has the user's session for the full TTL with no way to kill it
  short of rotating the JWT secret + invalidating EVERY session.

## Validation

`tests/unit/auth/test_auth_router.py` (7 tests) covers : login success,
login wrong password, login disabled user, refresh success rotates token,
refresh with revoked token fails, refresh with expired token fails,
/me returns user. `test_jwt.py` (6) + `test_passwords.py` (4) cover the
primitives.
