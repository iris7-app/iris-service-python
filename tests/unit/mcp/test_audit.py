"""Unit tests for :mod:`iris_service.mcp.audit`."""

from __future__ import annotations

import logging
from decimal import Decimal

from iris_service.mcp.audit import AUDIT_ACTION, hash_args, record_tool_call


def test_hash_args_stable_for_same_payload() -> None:
    a = hash_args({"a": 1, "b": "x"})
    b = hash_args({"b": "x", "a": 1})  # different key order
    assert a == b


def test_hash_args_8_chars() -> None:
    h = hash_args({"x": 1})
    assert len(h) == 8
    # Hex prefix.
    int(h, 16)


def test_hash_args_handles_non_json() -> None:
    """Decimal + datetime fall back to default=str — must not raise."""
    h = hash_args({"price": Decimal("9.99")})
    assert isinstance(h, str)
    assert len(h) == 8


def test_hash_args_diff_for_diff_payload() -> None:
    assert hash_args({"a": 1}) != hash_args({"a": 2})


def test_record_tool_call_emits_info_log(caplog: object) -> None:
    """Audit must produce a record on the named logger."""
    import pytest

    cap = caplog  # type: ignore[assignment]
    assert isinstance(cap, pytest.LogCaptureFixture)
    cap.set_level(logging.INFO, logger="iris_service.mcp.audit")
    record_tool_call(tool_name="t", args={"x": 1}, user="alice", role="ROLE_USER")
    assert any(AUDIT_ACTION in rec.getMessage() for rec in cap.records)


def test_record_tool_call_anonymous_marker(caplog: object) -> None:
    import pytest

    cap = caplog  # type: ignore[assignment]
    assert isinstance(cap, pytest.LogCaptureFixture)
    cap.set_level(logging.INFO, logger="iris_service.mcp.audit")
    record_tool_call(tool_name="t", args={}, user=None, role=None)
    msgs = [rec.getMessage() for rec in cap.records]
    assert any("<anonymous>" in m for m in msgs)
    assert any("<no-role>" in m for m in msgs)


def test_record_tool_call_carries_extras(caplog: object) -> None:
    """The extra dict must surface tool_name + args_hash for structured backends."""
    import pytest

    cap = caplog  # type: ignore[assignment]
    assert isinstance(cap, pytest.LogCaptureFixture)
    cap.set_level(logging.INFO, logger="iris_service.mcp.audit")
    record_tool_call(tool_name="my_tool", args={"x": "y"}, user="u", role="ROLE_USER")
    rec = next(r for r in cap.records if AUDIT_ACTION in r.getMessage())
    assert rec.tool_name == "my_tool"
    assert rec.audit_action == AUDIT_ACTION
    assert len(rec.args_hash) == 8
