# ADR-0006 : Structured logging via structlog (over stdlib logging alone)

**Status** : Accepted
**Date** : 2026-04-25
**Sibling** : `../iris-service` (Java side, logback-spring.xml dual-profile)

## Context

The Java iris-service uses logback with profile-specific layouts :
- dev : human-readable single-line format with colour codes.
- prod : JSON one-line-per-event format consumable by Loki without regex.

The Python mirror needs the same dual-profile behaviour AND something
extra : **automatic context binding** from the request-id middleware so
every log line within an HTTP request scope carries `request_id=<uuid>`.
Plain stdlib `logging` doesn't do this without thread-local hackery
(which breaks with asyncio anyway).

Standard Python options :

| Choice | Decision |
|---|---|
| **structlog** | ✅ Selected — structured-by-design, contextvars-based binding, JSON renderer for prod / console renderer for dev, foreign-logger interop. |
| stdlib logging + python-json-logger | Works for JSON output but no contextvars binding ; would need every log call to manually pass `extra={"request_id": ...}`. Tedious + drift-prone. |
| loguru | Nice ergonomics but a fully alternative API ; doesn't play well with stdlib loggers used by uvicorn / sqlalchemy / aiokafka without aggressive monkey-patching. |
| stdlib logging + custom Filter | Possible but reinventing structlog's processor pipeline. |

## Decision

`iris_service/middleware/logging.py` exposes `configure_logging(*, dev_mode: bool)`
called from `create_app()` BEFORE any other setup so the boot sequence
itself logs in the chosen format.

### Processor chain

```python
shared_processors = [
    structlog.contextvars.merge_contextvars,      # auto-binds request_id from middleware
    structlog.stdlib.add_logger_name,              # logger=<module>
    structlog.stdlib.add_log_level,                # level=info / warn / error
    structlog.processors.TimeStamper(fmt="iso", utc=True),
    structlog.processors.StackInfoRenderer(),
    structlog.processors.format_exc_info,
]

# Dev : ConsoleRenderer (colour-coded, multi-line stack traces)
# Prod : JSONRenderer (one JSON object per line)
```

### Foreign-logger interop

`stdlib logging`'s root logger gets a single handler whose formatter is
`structlog.stdlib.ProcessorFormatter`. This routes EVERY stdlib log line
(uvicorn, sqlalchemy, aiokafka) through the SAME processor chain — so
in prod every log line is JSON, regardless of which library emitted it.
No more "uvicorn logs are different from app logs" pain in Loki.

### Context binding from middleware

`RequestIdMiddleware` calls `structlog.contextvars.bind_contextvars(request_id=...)`
at the start of each request. Because contextvars are async-task-scoped
(NOT thread-local), every `await` chain within the request inherits the
binding correctly. On request end, `clear_contextvars()` resets so the
next request doesn't inherit.

Then any code in the request scope :

```python
logger = structlog.get_logger()
logger.info("created_customer", id=customer.id, email=customer.email)
```

emits :

```json
{"event": "created_customer", "id": 42, "email": "x@y.com",
 "request_id": "abc-123", "logger": "iris_service.customer.router",
 "level": "info", "timestamp": "2026-04-25T10:00:00Z"}
```

The `request_id` is injected automatically — the call site doesn't pass it.

### Tame noisy third-party loggers

```python
for noisy in ("uvicorn.access", "aiokafka", "asyncio", "watchfiles"):
    logging.getLogger(noisy).setLevel(logging.WARNING)
```

Default INFO is too noisy for these — uvicorn.access logs every request
(redundant with starlette-prometheus metrics + the request-id-bound app
logs), aiokafka's heartbeat logs every 30s, watchfiles spams on hot-reload.

## Consequences

**Pros** :
- One log format per env (JSON in prod, console in dev) for ALL log lines
  — first-party AND third-party.
- Free request_id correlation across the entire async chain — grep Loki
  by request_id to see every log for a single request, no matter which
  module emitted it.
- structlog's processor chain is transparent : add a new field to all
  logs (e.g. `tenant_id`) by appending one line to `shared_processors`.
- Test ergonomics : `caplog` from pytest still captures the output (the
  ProcessorFormatter routes through stdlib).

**Cons** :
- One more dep to learn ; team needs to use `structlog.get_logger()`
  not `logging.getLogger()` to access contextvars binding (stdlib loggers
  still work but don't inherit `request_id`).
- Multi-line stack traces in dev are long ; mitigated by the colour
  ConsoleRenderer's clear delimiters.
- Slight overhead per log call (~5-10 µs) for the processor chain.
  Negligible vs the I/O cost of writing the line.

## Alternatives considered

- **Stdlib only** — rejected : the contextvars-binding ergonomics are
  worth the dep.
- **loguru** — rejected : alternative API ; doesn't capture stdlib loggers
  cleanly.
- **OTel logs SDK** — interesting but less mature than structlog ; OTel
  log shipping can be layered on top by adding an OTel-export processor
  to structlog's chain (deferred to a later ADR if/when needed).

## Validation

No dedicated tests — the structlog config doesn't have branches worth
unit-testing in isolation. End-to-end : `bin/demo-up.sh` + a couple of
HTTP requests, observe (a) console output is colour-coded with request_id
in each line, (b) Loki ingests JSON cleanly with the request_id field
indexed.
