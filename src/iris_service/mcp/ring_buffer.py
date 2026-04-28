"""Custom Python `logging.Handler` keeping the last N log records in memory.

Mirrors Logback's `cyclic-buffer-appender` from the Java sibling — the
in-process buffer the MCP `tail_logs` tool reads. NO external Loki
call ; the backend stays infrastructure-agnostic per ADR-0062 (Java
sibling) §"backend-LOCAL only".

Invariants :
- **Bounded** : ``collections.deque(maxlen=N)`` evicts the oldest entry
  on overflow ; memory stays O(N) regardless of log throughput.
- **Lock-free reads** — Python's GIL makes the deque iteration thread-safe
  when we materialise via ``list(self._buffer)``. Writes (``append``)
  are atomic at the bytecode level.
- **Filtering at read-time** — ``snapshot()`` applies the level / request_id
  filter while iterating ; cheaper than maintaining N indexes.

Wired in :func:`iris_service.mcp.mount.mount_mcp_server` at app startup
on the root logger so EVERY logger (including ``uvicorn.access``,
``sqlalchemy.engine``, third-party libs) flows through it.
"""

from __future__ import annotations

import logging
import os
from collections import deque
from datetime import UTC, datetime
from threading import Lock
from typing import Final

from iris_service.mcp.dtos import LogEvent

#: Default capacity if ``IRIS_MCP_RING_BUFFER_SIZE`` is unset.
#:
#: 500 chosen as the sweet spot : enough to cover the last ~1-2 minutes of a
#: chatty service (HTTP access log + slowapi + sqlalchemy), small enough that
#: a worst-case 500 x 1 KB log line = 500 KB stays in process RSS.
DEFAULT_RING_BUFFER_SIZE: Final[int] = 500

_ENV_VAR: Final[str] = "IRIS_MCP_RING_BUFFER_SIZE"


def _read_capacity() -> int:
    """Read the buffer size from env, fall back to the sane default.

    Tolerates malformed input by falling back to the default — a typo in
    a deploy manifest must NOT crash the boot. The misconfigure surfaces
    as a structured warning log instead.
    """
    raw = os.environ.get(_ENV_VAR)
    if raw is None:
        return DEFAULT_RING_BUFFER_SIZE
    try:
        size = int(raw)
    except ValueError:
        logging.getLogger(__name__).warning(
            "ring_buffer_size_malformed env=%s value=%r — falling back to %d",
            _ENV_VAR,
            raw,
            DEFAULT_RING_BUFFER_SIZE,
        )
        return DEFAULT_RING_BUFFER_SIZE
    if size <= 0:
        logging.getLogger(__name__).warning(
            "ring_buffer_size_invalid env=%s value=%d (must be > 0) — falling back to %d",
            _ENV_VAR,
            size,
            DEFAULT_RING_BUFFER_SIZE,
        )
        return DEFAULT_RING_BUFFER_SIZE
    return size


class RingBufferHandler(logging.Handler):
    """Bounded in-memory buffer of recent log records.

    Stores :class:`LogEvent` instances (already-marshalled DTOs, NOT raw
    ``LogRecord``) so the read path is allocation-free and consumers
    don't need to import the stdlib logging types.

    Pop-on-overflow eviction via ``deque(maxlen=...)``. Per-handler
    capacity is fixed at construction time ; the runtime never resizes
    (matches Logback's CyclicBuffer behaviour).
    """

    def __init__(self, capacity: int | None = None) -> None:
        """Construct with an explicit capacity OR pick from env / default."""
        super().__init__(level=logging.DEBUG)  # capture everything ; filters on read
        cap = capacity if capacity is not None else _read_capacity()
        if cap <= 0:
            # Direct-instantiation guard — env path already coerces ; this
            # catches programmatic mis-use during tests / DI wiring.
            raise ValueError(f"capacity must be > 0, got {cap}")
        self._buffer: deque[LogEvent] = deque(maxlen=cap)
        # Read-side lock — list materialisation under the GIL is safe but
        # we want a strict snapshot semantics (writers can't extend the
        # deque mid-iteration). Tiny critical section ; non-blocking for
        # producers in practice.
        self._lock = Lock()
        self._capacity = cap

    @property
    def capacity(self) -> int:
        """Maximum number of records retained."""
        return self._capacity

    def emit(self, record: logging.LogRecord) -> None:
        """Convert the LogRecord to a LogEvent and store it.

        Errors during conversion are swallowed via ``handleError`` (stdlib
        convention — a broken log emit must NEVER kill the request that
        produced it).
        """
        try:
            self._buffer.append(self._to_event(record))
        except Exception:  # pragma: no cover — defensive ; stdlib pattern
            self.handleError(record)

    def snapshot(
        self,
        *,
        n: int = 50,
        level: str | None = None,
        request_id: str | None = None,
    ) -> list[LogEvent]:
        """Return the last ``n`` events matching optional level / request_id.

        Filtering is applied IN-ORDER from the most recent entry backwards,
        then reversed so the result reads chronologically (oldest →
        newest), which is what humans + LLMs expect from tail output.
        """
        if n <= 0:
            return []
        wanted_level = level.upper() if level else None
        # Lock is brief : we just clone the deque under it.
        with self._lock:
            buffered = list(self._buffer)
        # Walk newest→oldest so we can short-circuit at n matches.
        result: list[LogEvent] = []
        for event in reversed(buffered):
            if wanted_level is not None and event.level != wanted_level:
                continue
            if request_id is not None and event.request_id != request_id:
                continue
            result.append(event)
            if len(result) >= n:
                break
        return list(reversed(result))

    @staticmethod
    def _to_event(record: logging.LogRecord) -> LogEvent:
        """Materialise the LogRecord into a frozen LogEvent DTO.

        Pulls the request-id and trace-id from the record ``extra`` dict
        if present (set by structlog's ``contextvars`` merger or explicit
        ``logger.info(..., extra={"request_id": ...})`` calls). Plain stdlib
        loggers without those fields just leave them as ``None``.
        """
        return LogEvent(
            timestamp=datetime.fromtimestamp(record.created, tz=UTC),
            level=record.levelname,
            logger=record.name,
            message=record.getMessage(),
            request_id=getattr(record, "request_id", None),
            trace_id=getattr(record, "trace_id", None),
        )


# Module-level singleton — installed once per process by `attach_ring_buffer()`
# below. Tests can swap it via `set_ring_buffer()` for isolation.
_handler_singleton: RingBufferHandler | None = None


def attach_ring_buffer(capacity: int | None = None) -> RingBufferHandler:
    """Install the handler on the root logger (idempotent).

    Call from app startup BEFORE the first log emission so we don't lose
    the boot-sequence entries. Subsequent calls return the same instance
    (mounting the handler twice would duplicate every emitted record).

    If capacity is None, reads ``IRIS_MCP_RING_BUFFER_SIZE`` env or
    falls back to :data:`DEFAULT_RING_BUFFER_SIZE`.
    """
    global _handler_singleton
    if _handler_singleton is not None:
        return _handler_singleton
    handler = RingBufferHandler(capacity=capacity)
    logging.getLogger().addHandler(handler)
    _handler_singleton = handler
    return handler


def get_ring_buffer() -> RingBufferHandler:
    """Return the installed handler ; lazy-attach if missing.

    Matches the stdlib ``logging.getLogger`` ergonomics : never returns
    None, callers don't need to handle the not-yet-attached case.
    """
    if _handler_singleton is None:
        return attach_ring_buffer()
    return _handler_singleton


def set_ring_buffer(handler: RingBufferHandler | None) -> None:
    """Test hook — swap the singleton (or clear it for fresh attach)."""
    global _handler_singleton
    _handler_singleton = handler
