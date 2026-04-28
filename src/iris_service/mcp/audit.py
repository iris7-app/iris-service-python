"""Audit trail for MCP tool calls.

The Java sibling persists every MCP tool call as a row in the
``audit_event`` table (single trail with login audits, see ADR-0062
§"Audit"). The Python service does NOT yet have an audit_event DB
table — adding one would be a schema migration outside this MR's
scope. Until that lands, we emit a structured log entry per call :

- INFO log line with action=MCP_TOOL_CALL + tool name + args hash + user
- routed through the ring buffer + structlog pipeline
- ends up in Loki via the existing OTel exporter when the deploy
  has Loki ; stays local in the ring buffer otherwise

This is the same audit-payload SHAPE the Java sibling ships : when the
Python schema gains the ``audit_event`` table (tracked in TASKS.md),
:func:`record_tool_call` will additionally write the row ; downstream
consumers don't care which path produced the event.

NEVER log the raw args (could leak PII like customer emails, idempotency
keys, JWT subs). Args are hashed to an 8-char SHA-256 prefix — enough
to dedupe identical retries while stripping sensitive content.
"""

from __future__ import annotations

import hashlib
import json
import logging
from typing import Any

logger = logging.getLogger("iris_service.mcp.audit")

#: Tag the audit ring-buffer entries carry — fixed string, easy to grep.
AUDIT_ACTION = "MCP_TOOL_CALL"


def hash_args(args: dict[str, Any]) -> str:
    """Return a stable 8-char SHA-256 prefix of the args payload.

    Keys are sorted before hashing so dict-ordering quirks don't produce
    different hashes for the same call. Non-JSON-serialisable values
    (Decimal, datetime, …) are coerced via ``default=str`` — exact
    string format is not material since we only use this hash for
    correlation, not signature verification.
    """
    payload = json.dumps(args, sort_keys=True, default=str)
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    return digest[:8]


def record_tool_call(
    *,
    tool_name: str,
    args: dict[str, Any],
    user: str | None,
    role: str | None,
) -> None:
    """Emit a structured audit log for one MCP tool invocation.

    Single INFO line — picked up by :class:`RingBufferHandler` (so the
    ``tail_logs`` tool can return audit events too) AND by structlog's
    JSON renderer (Loki-friendly when the OTel exporter is wired).

    Anonymous calls (``user is None``) are explicitly logged — the trail
    must include un-auth attempts, not just authenticated ones. The mount
    layer will reject pre-auth before tools fire, but the logging contract
    is still defensive.
    """
    logger.info(
        "%s tool=%s args_hash=%s user=%s role=%s",
        AUDIT_ACTION,
        tool_name,
        hash_args(args),
        user or "<anonymous>",
        role or "<no-role>",
        extra={
            "audit_action": AUDIT_ACTION,
            "tool_name": tool_name,
            "args_hash": hash_args(args),
            "user": user,
            "role": role,
        },
    )
