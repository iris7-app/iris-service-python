# ADR-0010 : SQLAlchemy 2.x async over Tortoise / Beanie / SQLModel

**Status** : Accepted
**Date** : 2026-04-25
**Sibling** : `iris-service-java` uses Spring Data JPA / Hibernate — same
pattern (ORM + repository abstraction + transaction management).

## Context

The Python async ecosystem has 4 mainstream ORM-ish libraries :

| Library | Style | Transactions | Migrations | Type checking |
|---|---|---|---|---|
| **SQLAlchemy 2.x async** | Imperative (Core + ORM) | ✓ | Alembic | ✓ via `Mapped[T]` |
| **Tortoise ORM** | Django-like ActiveRecord | ✓ | Aerich | partial |
| **Beanie** (MongoDB) | Pydantic-based | ✓ | manual | ✓ |
| **SQLModel** | Pydantic + SQLAlchemy hybrid | ✓ | Alembic | ✓ |
| **Raw asyncpg** | Manual SQL strings | manual | manual | n/a |

Iris-service needs : Postgres, ACID transactions, schema migrations,
strict mypy compatibility, and parity with the Java side's Spring Data
JPA repository pattern.

## Decision

**SQLAlchemy 2.x async** with Alembic for migrations.

Specifically :
- `sqlalchemy[asyncio]==2.0.36` (with `[asyncio]` extra → pulls greenlet).
- `asyncpg==0.31.0` driver (cp314 wheels per ADR-0007 §9 fix).
- `async_sessionmaker` + `AsyncSession` per request via FastAPI `Depends`.
- ORM declarative mapping with `Mapped[T]` type hints (mypy-friendly).
- Repository pattern : 1 repo class per entity (`CustomerRepository`),
  static methods taking the session as first arg → mirrors the Java
  `CrudRepository<Customer, Long>` pattern.
- Alembic for migrations (`alembic/versions/`), `alembic upgrade head`
  in app startup OR a separate CI job.

## Consequences

**Pros** :
- **Industry standard** : SQLAlchemy is the de-facto Python ORM ; recruiters
  recognise it, libraries integrate with it, the maintenance is solid
  (active since 2006).
- **Type safety** : `Mapped[int]` / `Mapped[str | None]` makes mypy strict
  catch column-type mismatches at static-analysis time.
- **Dual API** : Core (SQL Expression Language) + ORM (declarative classes)
  in the same library. Use ORM for CRUD, drop to Core for complex queries
  (the customer search in `repository.py` uses Core's `select(...).where(or_(...))`).
- **Alembic is mature** : auto-generation works for 90% of migrations,
  manual review catches the 10% that auto-gen mishandles.
- **Async + sync bridge** : `greenlet` lets sync code (driver internals)
  cooperate with async (top-level API). Coverage hooks need
  `concurrency = ["greenlet", "thread"]` in pytest-cov config — documented
  in ADR-0007 §3.

**Cons** :
- **Verbose** : ORM declarative classes + Mapped types + relationship
  decorators take 30+ lines for a simple entity. Java's Lombok or
  Tortoise's class-attribute syntax are more compact.
- **Greenlet quirk** : SQLAlchemy async uses greenlets internally to bridge
  async ↔ sync internals. Coverage misses greenlet-executed code by
  default (fixed in pyproject — see ADR-0007 §3).
- **Async API still 2nd-class in some places** : a few SQLAlchemy features
  (async event hooks, hybrid_property with async expression) are sync-only
  or require workarounds. Currently not blocking for this project.

**Alternatives considered** :

| Library | Why not |
|---|---|
| **Tortoise ORM** | Less mature, smaller community, ORM-only (no Core fallback for complex queries), Aerich migrations less battle-tested than Alembic |
| **Beanie** | MongoDB-only ; we're on Postgres |
| **SQLModel** | Promising but still young (1.x not yet released) ; complicates the type model (Pydantic + SQLAlchemy in one class) without clear win |
| **Raw asyncpg + manual schema** | Rejected outright — no migration story, no type safety, repository code becomes hand-written SQL |
| **Encode/databases** | Lower-level than SQLAlchemy, requires writing SQL strings ; sustainability uncertain (low recent commits) |

## Validation

- 127 unit tests pass with in-memory SQLite (`aiosqlite` driver, `:memory:`).
- 3 integration tests use a real Postgres 16.6 testcontainer
  (`test_repository_postgres.py`).
- Migrations : `alembic upgrade head` in CI before integration tests ;
  `alembic check` in pre-push hook (verifies no auto-generated drift).
- Repository search uses SQL Expression Language directly :
  `stmt.where(func.lower(Customer.name).like(pattern))`.

## See also

- ADR-0001 : Python stack choice
- ADR-0008 : Async-first architecture
- [SQLAlchemy 2.x async docs](https://docs.sqlalchemy.org/en/20/orm/extensions/asyncio.html)
- [Alembic docs](https://alembic.sqlalchemy.org/en/latest/)
