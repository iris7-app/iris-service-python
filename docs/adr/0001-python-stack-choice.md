# ADR-0001 : Python stack choice for iris-service-python

**Status** : Accepted
**Date** : 2026-04-25
**Sibling project** : `../iris-service` (Java/Spring Boot 4 — canonical)

## Context

User asked for a Python mirror of the Java iris-service to demonstrate that
the same industrial-demo philosophy (observability, security, event-driven,
caching, CI/CD discipline) can be built equally well on the Python ecosystem.

## Decision

| Layer | Choice | Why over alternatives |
|---|---|---|
| Web framework | **FastAPI** | Modern async-first, type hints, auto OpenAPI docs. Mature ecosystem (vs Litestar = newer/faster but smaller community). |
| DTO + validation | **Pydantic v2** | De-facto standard ; FastAPI native integration ; runtime validation + serialisation + OpenAPI schema in one |
| ORM | **SQLAlchemy 2.x async** | Mature, full SQL escape hatch, async support GA in 2.0 (vs Tortoise = simpler but less escape hatch ; vs SQLModel = nice DX but less battle-tested) |
| Migrations | **Alembic** | The SQLAlchemy-native solution ; same template+upgrade flow as Flyway |
| JWT auth | **python-jose** + **passlib** | Standard combo ; python-jose for tokens, passlib for bcrypt password hashing |
| Kafka | **aiokafka** | Native async ; aligns with FastAPI's async-first runtime (vs confluent-kafka-python = more features but blocking) |
| Redis | **redis-py** with `redis.asyncio` | The official client ; native async support |
| Observability | **OpenTelemetry SDK** + **prometheus-client** | Same OTel specs as the Java side (cross-language traces) |
| Logging | **structlog** | Structured JSON-friendly logging |
| Rate limiting | **slowapi** | Starlette/FastAPI integration ; equivalent to bucket4j+filter |
| Package manager | **uv** | Rust-based, ~100× faster than pip/poetry, lock files, PEP 517/518 compliant |
| Test framework | **pytest** + **pytest-asyncio** + **httpx** | De-facto Python testing |
| Lint/Format | **ruff** + **mypy** | ruff replaces flake8/black/isort/pylint with one tool ~100× faster |
| Arch tests | **import-linter** | Pythonic ArchUnit equivalent — declarative dep boundaries |
| Container tests | **testcontainers-python** | Same Docker-based container API as Java side |

## Versions pinned (matches Java's "pin every upstream reference" rule)

See `pyproject.toml` for exact versions. All deps pinned to specific patch
versions ; renovate-style updates handled via dedicated MR.

## Consequences

**Pros** :
- Modern async-first stack (= Spring WebFlux equivalent without the Reactor learning curve)
- Type-safe end-to-end with mypy + Pydantic (= same safety as Java's compile-time)
- Smaller memory footprint than JVM (~80 MB Python vs ~400 MB Spring Boot warmup)
- Faster startup (~1s Python vs ~6-10s Spring Boot)
- Cross-language OTel traces with the Java sibling

**Cons** :
- 2 codebases to maintain in lockstep (when Java side adds an endpoint, Python
  side has to add the same endpoint — no automatic sync)
- Python ecosystem version churn higher than Java (libraries change APIs more
  often) — pinning + dedicated upgrade waves required
- mypy strict mode is more permissive than Java compile-time checks — runtime
  surprises possible if `Any` slips in

## Cross-references

- `../iris-service/docs/adr/0060-sb3-compat-prod-grade.md` — same prod-grade
  philosophy applies here for compat versions (Python 3.11/3.12/3.13/3.14)
- `pyproject.toml` — pinned versions
- `CLAUDE.md` — claude session rules + tech stack reminder
