"""Pydantic v2 DTOs surfaced via MCP tool return types.

Why a dedicated DTO module — never expose ORM entities to the LLM :

1. **Lazy collections** — SQLAlchemy ``selectin``/``lazy`` proxies serialise
   into hundreds of nested rows when fed to ``model_dump()``. The LLM
   would consume a 50 KB JSON for "give me a customer".
2. **Public-API contract** — once an LLM client depends on a field name,
   renaming a column under it is a breaking change. DTO indirection
   lets us evolve the schema without breaking existing tool calls.
3. **Privacy** — entities can carry internal columns (password hashes,
   refresh-token rows) that must NEVER reach an LLM context.

All read DTOs are ``frozen=True`` to make them hashable + signal
"this is a snapshot, not a write target". Decimals stay Decimals
(``json_schema_extra={"format": "decimal"}``) — Pydantic v2 serialises
them as strings to preserve precision (matches Java's BigDecimal +
the Postgres NUMERIC(12,2) chosen for monetary columns).
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

# ── Domain DTOs ───────────────────────────────────────────────────────────────


class Customer360(BaseModel):
    """Aggregate read of a customer + the rolled-up order stats.

    Returned by the ``get_customer_360`` MCP tool. Computed in one read
    transaction (customer row + count(*) + sum(total_amount) over the
    customer's orders) — the LLM gets a single-shot summary instead of
    making 3 separate calls.
    """

    model_config = ConfigDict(frozen=True)

    id: int
    name: str
    email: str
    order_count: int
    total_revenue: Decimal
    last_order_at: datetime | None


# ── Observability DTOs ────────────────────────────────────────────────────────


class LogEvent(BaseModel):
    """One ring-buffer log entry surfaced via the ``tail_logs`` tool.

    Sourced from :class:`iris_service.mcp.ring_buffer.RingBufferHandler`
    — the in-process ring buffer attached to the root logger at app
    startup. NO external Loki call ; the buffer is bounded
    (``IRIS_MCP_RING_BUFFER_SIZE`` env, default 500) so memory stays
    flat under sustained log throughput.
    """

    model_config = ConfigDict(frozen=True)

    timestamp: datetime
    level: str
    logger: str
    message: str
    request_id: str | None = None
    trace_id: str | None = None


class MetricSnapshot(BaseModel):
    """One sample from the in-process prometheus REGISTRY.

    Returned by the ``get_metrics`` tool. ``type`` is one of the
    prometheus metric kinds (``counter``, ``gauge``, ``histogram``,
    ``summary``, ``untyped``) — helps the LLM interpret the value
    (``counter`` is monotonically increasing, ``gauge`` is point-in-time,
    etc.).
    """

    model_config = ConfigDict(frozen=True)

    name: str
    tags: dict[str, str]
    type: Literal["counter", "gauge", "histogram", "summary", "untyped"]
    value: float
    timestamp: datetime


class ComponentStatus(BaseModel):
    """Single component health entry inside :class:`HealthSnapshot`."""

    model_config = ConfigDict(frozen=True)

    status: Literal["UP", "DOWN", "UNKNOWN"]
    details: dict[str, str] = Field(default_factory=dict)


class HealthSnapshot(BaseModel):
    """Composite health snapshot — mirror of Spring Boot Actuator's shape.

    ``status`` is the rollup (DOWN if any component is DOWN, UP otherwise).
    ``components`` carries the per-subsystem details (db, redis, kafka, …).
    Returned by both ``get_health`` (sanitized) and ``get_health_detail``
    (admin-gated, includes raw error messages).
    """

    model_config = ConfigDict(frozen=True)

    status: Literal["UP", "DOWN", "UNKNOWN"]
    components: dict[str, ComponentStatus]


class EnvSnapshot(BaseModel):
    """Filtered Settings dump returned by ``get_actuator_env``.

    Already redacted upstream — secret-ish values (anything matching
    ``(?i).*(password|secret|token|key|credential).*`` in the key) are
    replaced with ``"***REDACTED***"`` BEFORE the snapshot is built.
    Defence in depth : even if a future caller bypasses the prefix
    filter, the redaction still kicks in.
    """

    model_config = ConfigDict(frozen=True)

    properties: dict[str, str]


class InfoBlock(BaseModel):
    """Build / runtime info — top-level of :class:`OpenApiSummary`."""

    model_config = ConfigDict(frozen=True)

    title: str
    version: str
    description: str | None = None


class OpenApiSummary(BaseModel):
    """Compact OpenAPI summary — paths + verbs only, no schemas.

    Returned by ``get_openapi_spec(summary=True)``. Useful when the LLM
    needs to know what endpoints exist without paying the token cost
    of the full 200 KB OpenAPI document. ``paths_by_verb`` maps each
    HTTP verb (lowercase) to the sorted list of paths that support it.
    """

    model_config = ConfigDict(frozen=True)

    info: InfoBlock
    paths_by_verb: dict[str, list[str]]


# ── Generic error DTO ─────────────────────────────────────────────────────────


class NotFound(BaseModel):
    """Returned by lookup tools when the entity doesn't exist.

    Structured 404-equivalent — gives the LLM a parseable signal instead
    of relying on it to interpret a magic ``None``. The MCP spec lets
    a tool succeed with a "soft failure" payload like this without
    raising — better UX than throwing on every missed lookup.
    """

    model_config = ConfigDict(frozen=True)

    not_found: bool = True
    entity: str
    id: int

    @classmethod
    def for_(cls, entity: str, id_: int) -> NotFound:
        """Builder — ``NotFound.for_("Order", 42)``."""
        return cls(entity=entity, id=id_)


# ── Mutation tool result DTOs ─────────────────────────────────────────────────


class OrderRef(BaseModel):
    """Skinny order reference returned by ``create_order`` and friends."""

    model_config = ConfigDict(frozen=True)

    id: int
    customer_id: int
    status: str
    total_amount: Decimal
    created_at: datetime


class OrderListItem(BaseModel):
    """One row in the ``list_recent_orders`` response."""

    model_config = ConfigDict(frozen=True)

    id: int
    customer_id: int
    status: str
    total_amount: Decimal
    created_at: datetime


class ProductLowStock(BaseModel):
    """One row in the ``find_low_stock_products`` response."""

    model_config = ConfigDict(frozen=True)

    id: int
    name: str
    unit_price: Decimal
    stock_quantity: int


class ChaosResult(BaseModel):
    """Result of the ``trigger_chaos_experiment`` tool.

    ``effective`` reports whether the experiment actually fired (some
    scenarios — e.g. ``kafka-timeout`` — return synthetic 504s without
    a real broker call ; the field is honest about that).
    """

    model_config = ConfigDict(frozen=True)

    scenario: str
    effective: bool
    detail: str


class CancelResult(BaseModel):
    """Result of the ``cancel_order`` tool."""

    model_config = ConfigDict(frozen=True)

    id: int
    cancelled: bool
    previous_status: str | None = None


# ── Public tool-name list (kept in sync with tools.py) ───────────────────────

#: snake_case, verb-first tool identifiers — same wire-name as the Java sibling
#: per ADR-0062 §"Tool naming convention". Importing this list from tests +
#: docs prevents the catalogue from silently drifting between Python and Java.
TOOL_NAMES: tuple[str, ...] = (
    # Domain
    "list_recent_orders",
    "get_order_by_id",
    "create_order",
    "cancel_order",
    "find_low_stock_products",
    "get_customer_360",
    "trigger_chaos_experiment",
    # ML inference (Phase C of shared ADR-0061)
    "predict_customer_churn",
    # Observability (backend-local only)
    "tail_logs",
    "get_metrics",
    "get_health",
    "get_health_detail",
    "get_actuator_env",
    "get_actuator_info",
    "get_openapi_spec",
)
