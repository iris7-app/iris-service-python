# ADR-0008 : Async-first architecture (no sync mixed in)

**Status** : Accepted
**Date** : 2026-04-25
**Sibling** : `iris-service-java` (sync HTTP layer + async Kafka — mirror
of this decision in the JVM idiom)

## Context

FastAPI supports BOTH async and sync route handlers. SQLAlchemy 2.x supports
both sync and async sessions. The Python ecosystem mixes paradigms — `requests`
(sync) vs `httpx` (both), `psycopg2` (sync) vs `asyncpg` (async only),
`kafka-python` (sync) vs `aiokafka` (async).

Mixing sync + async in the same process is a classic footgun :
- Sync calls inside async handlers BLOCK the event loop → 1 slow query
  freezes ALL concurrent requests on that worker.
- `asyncio.run_in_executor` workaround works but adds thread-pool overhead
  + makes testing harder (sync calls don't see the async context vars,
  e.g. `request_id` injected by middleware).
- Code review burden : reviewers must remember "this can't call sync-only
  libraries here".

## Decision

**Every I/O-bound code path is `async def`.** No sync-in-async, no
sync-handler-with-async-deps. Concretely :

- **HTTP** : FastAPI all routes `async def` (uvicorn workers run the asyncio
  event loop natively).
- **DB** : SQLAlchemy 2.x **async** session via `asyncpg` driver
  (`postgresql+asyncpg://...` DSN). All repositories use
  `async def find_by_id(session: AsyncSession, ...)`.
- **Kafka** : `aiokafka` 0.13 producer + consumer (async APIs only).
- **Redis** : `redis.asyncio.Redis` (the `redis-py` async client, not the
  legacy `aioredis` which was merged into redis-py 4+).
- **HTTP client (outbound)** : `httpx.AsyncClient` for Ollama + JSONPlaceholder
  calls. NO `requests` library.
- **Tests** : `pytest-asyncio` 1.3 with `asyncio_mode = "auto"` so every
  `async def test_*` is automatically wrapped without per-test markers.
- **Resilience** : `tenacity` async-aware retries (`retry` decorator works
  on `async def`).
- **Background tasks** : `apscheduler.AsyncIOScheduler` (cron jobs run on
  the same event loop as the HTTP server).

CPU-bound work (bcrypt hashing, crypto) is allowed sync because :
- It releases the GIL for the C extension's duration → other coroutines
  progress.
- Wrapping it in `run_in_executor` adds latency without parallelism gain
  (one CPU core, one bcrypt at a time).

## Consequences

**Pros** :
- Single concurrency model — reviewers + future contributors don't need to
  switch mental contexts inside the same file.
- Maximum throughput : 100s of concurrent requests on a single uvicorn
  worker (vs ~10 on a sync Django/Flask worker).
- ContextVar propagation works seamlessly (`request_id` middleware → log
  records → DB queries → outbound HTTP — all carry the correlation id).
- Tests reflect production : `pytest-asyncio` runs each test in an event
  loop matching the runtime.

**Cons** :
- **Library compatibility** : a sync-only library (e.g. an SDK that only
  ships `requests`) requires `asyncio.to_thread` wrapping — every such
  case is a smell. Currently zero in this codebase (the `prometheus_client`
  module is intentionally sync but doesn't perform I/O — registry is in-memory).
- **Library bug surface** : async libraries are younger than their sync
  counterparts. Found 1 issue during dev : `aioredis` 1.x was abandoned ;
  migrated to `redis-py 5.x` async API which is better maintained.
- **Debugging** : stack traces include `asyncio` framework frames. Mitigated
  via `loguru`-style traceback hiding in `structlog` config.

**Neutral** :
- Performance vs sync : ~3× higher throughput on I/O-bound workloads (DB
  + Kafka + outbound HTTP), no improvement on CPU-bound. Match the workload.

## Validation

Verified end-to-end :
- `mypy --strict` catches `await` on non-coroutine OR missing `await` on
  coroutine return.
- import-linter contract (ADR-0007 §5) ensures `db/base.py` exports only
  `AsyncSession` / `async_sessionmaker`.
- 127 unit tests + 5 kafka_client integration tests all use async fixtures.
- Production startup : `uvicorn` runs with `asyncio` event loop, 1 worker
  serves 100s of concurrent requests at 90% CPU on a single core.

## See also

- ADR-0001 : Python stack choice (FastAPI + Pydantic v2 + SQLAlchemy 2.x async)
- ADR-0004 : Kafka request-reply pattern (uses aiokafka async producer/consumer)
- ADR-0007 : Industrial Python practices (mypy strict + 90% coverage)
- [PEP 492](https://peps.python.org/pep-0492/) : async/await syntax
- [SQLAlchemy 2.x async](https://docs.sqlalchemy.org/en/20/orm/extensions/asyncio.html)
