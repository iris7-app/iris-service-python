# ADR-0005 : Configuration via Pydantic Settings — env > .env > defaults

**Status** : Accepted
**Date** : 2026-04-25
**Sibling** : `../iris-service` (Java side, Spring Boot @ConfigurationProperties)

## Context

The Java iris-service uses Spring Boot's `@ConfigurationProperties` +
`application.yml` profile system to load typed configuration with a clear
precedence : env vars > application-{profile}.yml > application.yml >
@Value defaults. The Python mirror needs the same discipline :

- **Typed access** — `settings.db.host` (str) NOT `os.environ["DB_HOST"]` (str | None,
  may be missing, no validation).
- **Per-environment overrides** — dev / staging / prod can adjust without a
  code change ; secrets stay in env vars (NEVER committed).
- **Fail-fast on misconfiguration** — a missing required var crashes at
  startup with a clear error, not 30 minutes later when the first request
  hits the broken code path.
- **Singleton with caching** — settings get read ONCE per process ; lookups
  are O(1) attribute access, not a re-parse of os.environ on every call.

Standard Python options :

| Option | Decision |
|---|---|
| **pydantic-settings** | ✅ Selected — built on Pydantic v2 BaseModel, typed validation, env + .env + secret files, nested sections via env delimiter. |
| dynaconf | Capable but more sprawl ; manages its own cache + reload semantics that fight Pydantic's validation. |
| python-decouple | Simple but no nested sections, no validation, no fail-fast on missing required. |
| os.environ + custom dataclass | Reinventing the wheel ; loses validation + .env support. |

## Decision

`iris_service/config/settings.py` declares one `Settings` class per
sub-section (`DatabaseSettings`, `RedisSettings`, `KafkaSettings`,
`JwtSettings`) + one top-level `Settings` aggregating them via
`Field(default_factory=...)`.

### Env var conventions

- **`IRIS_` prefix** — all app vars share one prefix, avoids collisions
  with system vars (`HOST`, `PORT`, `USER`).
- **`__` (double underscore) nested delimiter** — `IRIS_DB__HOST`
  populates `Settings.db.host`. `IRIS_KAFKA__BOOTSTRAP_SERVERS` →
  `Settings.kafka.bootstrap_servers`. Configured via
  `model_config = SettingsConfigDict(env_prefix="IRIS_", env_nested_delimiter="__")`.
- **`.env` file** — read in addition to env vars, sane local-dev defaults
  shipped as `.env.example` (committed) ; `.env` itself gitignored. Every
  required var declared in `.env.example` even if it has a default — rule
  "key parity between .env and .env.example".

### Singleton via `lru_cache`

```python
@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
```

- `lru_cache(maxsize=1)` makes `get_settings()` return the same instance
  forever after the first call (no re-parse, no re-validation).
- FastAPI `Depends(get_settings)` works seamlessly because Depends caches
  the dependency tree per-request, AND `lru_cache` caches across requests
  → effectively one instance for the process lifetime.
- Tests can override via `app.dependency_overrides[get_settings] = lambda: test_settings`
  without monkey-patching env vars (cleaner + no test pollution).

### Fail-fast at app startup

`create_app()` calls `get_settings()` BEFORE building the FastAPI instance.
If any required var is missing or fails Pydantic validation, the process
exits with a Pydantic error message naming exactly which field is invalid.

This mirrors Spring's `@PostConstruct` on `@Configuration` beans — broken
config is caught at `mvn spring-boot:run` time, not on first request.

## Consequences

**Pros** :
- Typed access everywhere : `settings.kafka.bootstrap_servers` is `str`,
  IDE auto-completion works, mypy strict catches typos.
- Single source of truth : EVERY var read goes through `Settings` ; no
  rogue `os.environ` calls scattered across the code.
- Test ergonomics : `Settings(otel_endpoint="http://...")` builds a
  one-off override without env juggling.
- Prod parity : the same `Settings` class loads dev / staging / prod ;
  only the env var values differ.

**Cons** :
- `.env.example` and `.env` MUST stay in key parity — drift is a footgun.
  Mitigated by a future CI check : `diff <(grep -oE '^[A-Z_]+' .env.example) <(grep -oE '^[A-Z_]+' .env)`.
- Sub-section nesting via `__` is non-obvious for newcomers (looks like a
  typo). Documented in `.env.example` header + this ADR.
- `lru_cache` makes settings effectively immutable for the process lifetime ;
  config-reload-without-restart is NOT supported. For demos that's fine ;
  for prod systems needing reload, swap `lru_cache` for a manual cache +
  SIGHUP handler.

## Alternatives considered

- **Read os.environ directly in modules** — rejected : no validation, no
  defaults, no nested sections, every module re-parses, type drift.
- **Dynaconf** — rejected : heavier ; its own profile system overlaps
  Pydantic's, two ways to do the same thing.
- **YAML-based config** (à la Spring application.yml) — rejected : adds
  a YAML parser dep, fights pydantic-settings' env model. Use
  pydantic-settings native + .env if you really want a file.

## Validation

`tests/unit/config/test_settings.py` (TODO — not yet written) should cover :
- Default values populate without any env vars set.
- `IRIS_DB__HOST` env var overrides `db.host`.
- Missing required field raises `ValidationError` with the field name.
- `lru_cache` returns the same instance on repeat calls.
- `app.dependency_overrides[get_settings]` works in FastAPI tests.
