"""Unit tests for :mod:`iris_service.mcp.ring_buffer`."""

from __future__ import annotations

import logging
import os

import pytest

from iris_service.mcp import ring_buffer
from iris_service.mcp.ring_buffer import (
    DEFAULT_RING_BUFFER_SIZE,
    RingBufferHandler,
    attach_ring_buffer,
    get_ring_buffer,
    set_ring_buffer,
)


@pytest.fixture(autouse=True)
def _reset_singleton() -> None:
    """Each test gets a clean slate — no state leak across cases."""
    set_ring_buffer(None)
    # Also remove any previously-attached handler from the root logger to
    # avoid leak across the whole pytest session.
    root = logging.getLogger()
    root.handlers = [h for h in root.handlers if not isinstance(h, RingBufferHandler)]


def test_capacity_default_when_env_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("IRIS_MCP_RING_BUFFER_SIZE", raising=False)
    h = RingBufferHandler()
    assert h.capacity == DEFAULT_RING_BUFFER_SIZE


def test_capacity_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("IRIS_MCP_RING_BUFFER_SIZE", "42")
    h = RingBufferHandler()
    assert h.capacity == 42


def test_capacity_invalid_env_falls_back(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("IRIS_MCP_RING_BUFFER_SIZE", "not-an-int")
    h = RingBufferHandler()
    assert h.capacity == DEFAULT_RING_BUFFER_SIZE


def test_capacity_negative_env_falls_back(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("IRIS_MCP_RING_BUFFER_SIZE", "-5")
    h = RingBufferHandler()
    assert h.capacity == DEFAULT_RING_BUFFER_SIZE


def test_capacity_zero_in_constructor_raises() -> None:
    with pytest.raises(ValueError):
        RingBufferHandler(capacity=0)


def test_emit_appends_in_order() -> None:
    h = RingBufferHandler(capacity=4)
    log = logging.getLogger("test.emit")
    log.handlers = [h]
    log.setLevel(logging.DEBUG)
    log.info("first")
    log.warning("second")
    snap = h.snapshot(n=10)
    assert [e.message for e in snap] == ["first", "second"]
    assert [e.level for e in snap] == ["INFO", "WARNING"]


def test_emit_evicts_oldest_when_full() -> None:
    h = RingBufferHandler(capacity=2)
    log = logging.getLogger("test.evict")
    log.handlers = [h]
    log.setLevel(logging.DEBUG)
    for i in range(5):
        log.info("msg-%d", i)
    snap = h.snapshot(n=10)
    # Only the last 2 messages survive.
    assert [e.message for e in snap] == ["msg-3", "msg-4"]


def test_snapshot_filters_by_level() -> None:
    h = RingBufferHandler(capacity=10)
    log = logging.getLogger("test.level")
    log.handlers = [h]
    log.setLevel(logging.DEBUG)
    log.info("info-1")
    log.warning("warn-1")
    log.info("info-2")
    log.error("err-1")
    only_info = h.snapshot(n=10, level="INFO")
    only_warn = h.snapshot(n=10, level="warning")  # case-insensitive
    assert [e.message for e in only_info] == ["info-1", "info-2"]
    assert [e.message for e in only_warn] == ["warn-1"]


def test_snapshot_filters_by_request_id() -> None:
    h = RingBufferHandler(capacity=10)
    log = logging.getLogger("test.reqid")
    log.handlers = [h]
    log.setLevel(logging.DEBUG)
    log.info("a", extra={"request_id": "r-1"})
    log.info("b", extra={"request_id": "r-2"})
    log.info("c", extra={"request_id": "r-1"})
    only_r1 = h.snapshot(n=10, request_id="r-1")
    assert [e.message for e in only_r1] == ["a", "c"]


def test_snapshot_n_zero_returns_empty() -> None:
    h = RingBufferHandler(capacity=10)
    h.emit(_make_record("hi"))
    assert h.snapshot(n=0) == []


def test_snapshot_n_caps_at_buffer_size() -> None:
    h = RingBufferHandler(capacity=3)
    for i in range(3):
        h.emit(_make_record(f"m{i}"))
    snap = h.snapshot(n=100)
    assert len(snap) == 3


def test_attach_ring_buffer_idempotent() -> None:
    a = attach_ring_buffer(capacity=5)
    b = attach_ring_buffer(capacity=999)  # ignored — singleton already exists
    assert a is b
    assert a.capacity == 5


def test_get_ring_buffer_lazy_attaches() -> None:
    set_ring_buffer(None)
    h = get_ring_buffer()
    assert isinstance(h, RingBufferHandler)


def test_default_capacity_constant() -> None:
    # Sanity : DEFAULT_RING_BUFFER_SIZE matches the env-doc default.
    assert ring_buffer.DEFAULT_RING_BUFFER_SIZE == 500


def _make_record(msg: str) -> logging.LogRecord:
    """Helper to build a synthetic LogRecord without going through a logger."""
    return logging.LogRecord(
        name="t",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg=msg,
        args=None,
        exc_info=None,
    )


def test_emit_handles_exception_gracefully() -> None:
    """Broken record conversion must NOT bubble up to the caller."""
    h = RingBufferHandler(capacity=2)
    # A record with a bad msg arg formatter — getMessage() would raise.
    # We ensure handleError is invoked silently.
    rec = _make_record("ok %s")
    rec.args = (object(),)  # str()-able, so this case won't actually fail ;
    # Instead, monkey-patch _to_event to raise to assert defensive path.
    original = h._to_event

    def boom(_: logging.LogRecord) -> None:
        raise RuntimeError("forced")

    h._to_event = boom  # type: ignore[method-assign]
    # handleError prints to stderr by default ; we just need it not to raise.
    try:
        h.emit(rec)
    except Exception as exc:  # pragma: no cover — failure mode
        raise AssertionError("emit must swallow conversion errors") from exc
    finally:
        h._to_event = original  # type: ignore[method-assign]


def test_env_var_name() -> None:
    """Sanity : the env var name in the module matches what CLAUDE.md docs."""
    assert ring_buffer._ENV_VAR == "IRIS_MCP_RING_BUFFER_SIZE"
    # Also cross-check with os.environ (no leakage from previous test).
    os.environ.pop(ring_buffer._ENV_VAR, None)
