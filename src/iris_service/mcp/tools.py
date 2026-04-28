"""MCP tool implementations — 15 tools (14 baseline + churn-prediction per ADR-0061 Phase C).

Each tool is a thin async function that :

1. Calls :func:`audit.record_tool_call` FIRST (the audit must succeed
   even if the tool body errors out — a failed call is interesting too).
2. Optionally enforces role via :func:`auth.require_role`.
3. Delegates to existing services (Order/Product/Customer repositories,
   actuator builders, ring-buffer reader, metrics-registry reader).
4. Returns a frozen Pydantic DTO — never an ORM entity.

All tools take a :class:`Deps` value object as their first argument
(injected by the mount layer). This keeps the signatures pure ; mocking
in unit tests is just a fresh ``Deps(...)`` construction.

Tool registration with FastMCP is in :func:`register_tools`. The
function takes the FastMCP instance + a Deps factory and binds each
tool with the right dependency closure, so tests can build a tool set
against in-memory mocks without touching real DB / Redis / Kafka.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from contextlib import suppress
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Final, Literal

from fastapi import FastAPI
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from iris_service.config.settings import Settings
from iris_service.customer.models import Customer
from iris_service.mcp.actuator import (
    build_env_snapshot,
    build_health_snapshot,
    build_info_block,
    build_openapi,
)
from iris_service.mcp.audit import record_tool_call
from iris_service.mcp.auth import (
    ROLE_ADMIN,
    McpAuthError,
    get_current_user,
    require_role,
)
from iris_service.mcp.dtos import (
    CancelResult,
    ChaosResult,
    Customer360,
    EnvSnapshot,
    HealthSnapshot,
    InfoBlock,
    LogEvent,
    MetricSnapshot,
    NotFound,
    OpenApiSummary,
    OrderListItem,
    OrderRef,
    ProductLowStock,
)
from iris_service.mcp.metrics_registry import MetricsRegistryReader
from iris_service.mcp.ring_buffer import RingBufferHandler
from iris_service.ml.dtos import (
    ChurnNotFound,
    ChurnPrediction,
    ChurnServiceUnavailable,
)
from iris_service.ml.inference import ChurnPredictor, extract_features
from iris_service.ml.risk_band import classify_risk
from iris_service.order.models import Order, OrderStatus
from iris_service.order.order_line_models import OrderLine
from iris_service.product.models import Product

logger = logging.getLogger(__name__)

# ── Constants ────────────────────────────────────────────────────────────────

#: Hard cap on list_recent_orders. Mirrors the Java sibling's @ToolParam
#: "Max results, 1..100". Anthropic's tool-use guidance recommends keeping
#: list returns small to preserve LLM context budget.
MAX_RECENT_ORDERS: Final[int] = 100

#: Default low-stock threshold — mirrors Java sibling's default of 10.
DEFAULT_LOW_STOCK_THRESHOLD: Final[int] = 10

#: Allowed chaos scenarios. Limiting the literal set protects against
#: an LLM picking a typo'd scenario name and breaking the demo silently.
ChaosScenario = Literal["slow-query", "db-failure", "kafka-timeout"]
ALLOWED_CHAOS_SCENARIOS: Final[tuple[ChaosScenario, ...]] = (
    "slow-query",
    "db-failure",
    "kafka-timeout",
)

#: Allowed status filter for list_recent_orders. Same shape as Java's
#: ``OrderStatus`` enum + the existing OrderStatusLiteral in dtos.
OrderStatusFilter = Literal["PENDING", "CONFIRMED", "SHIPPED", "CANCELLED"]


# ── Dependency container ──────────────────────────────────────────────────────


@dataclass
class Deps:
    """Bundle of every dependency the tools need.

    Pure value class — the mount layer constructs one per process,
    tests construct one per case with mocks. NO global module state
    leaks into tool bodies.
    """

    app: FastAPI
    settings: Settings
    session_factory: Callable[[], Awaitable[AsyncSession]]
    ring_buffer: RingBufferHandler
    metrics_reader: MetricsRegistryReader
    churn_predictor: ChurnPredictor | None = None


# ── Tool helpers ──────────────────────────────────────────────────────────────


def _safe_user_role() -> tuple[str | None, str | None]:
    """Pull (user, role) from the auth context without raising.

    Used by the audit logger — we want to record the call attribution
    without exploding the audit path on missing context (e.g. tests
    that bypass auth).
    """
    try:
        user = get_current_user()
    except McpAuthError:
        return None, None
    return user.username, user.role


async def _audit(tool_name: str, args: dict[str, Any]) -> None:
    """Single-line audit emission helper."""
    user, role = _safe_user_role()
    record_tool_call(tool_name=tool_name, args=args, user=user, role=role)


# ── Tool implementations — Domain (7) ─────────────────────────────────────────


async def list_recent_orders(
    deps: Deps,
    limit: int = 20,
    status: OrderStatusFilter | None = None,
) -> list[OrderListItem]:
    """Newest-first list of orders. ``limit`` is clamped at 100.

    Args:
        deps: dependencies (mount-layer injected).
        limit: max results, 1..100. Values outside the range are clamped.
        status: optional status filter (one of PENDING/CONFIRMED/SHIPPED/CANCELLED).

    Returns:
        list of :class:`OrderListItem`. Empty list if no orders match.
    """
    await _audit("list_recent_orders", {"limit": limit, "status": status})
    capped = max(1, min(limit, MAX_RECENT_ORDERS))
    stmt = select(Order).order_by(Order.id.desc()).limit(capped)
    if status is not None:
        stmt = stmt.where(Order.status == status)
    async with await deps.session_factory() as session:
        rows = (await session.execute(stmt)).scalars().all()
    return [OrderListItem.model_validate(o, from_attributes=True) for o in rows]


async def get_order_by_id(deps: Deps, id: int) -> OrderListItem | NotFound:
    """Look up a single order ; returns NotFound on miss."""
    await _audit("get_order_by_id", {"id": id})
    async with await deps.session_factory() as session:
        order = await session.get(Order, id)
    if order is None:
        return NotFound.for_("Order", id)
    return OrderListItem.model_validate(order, from_attributes=True)


async def create_order(
    deps: Deps,
    customer_id: int,
    idempotency_key: str | None = None,
) -> OrderRef | NotFound:
    """Create a new empty order for a customer.

    The optional ``idempotency_key`` lets an LLM that retries the same
    call get the original order back instead of creating a duplicate.
    The key is hashed into the audit log for correlation. Idempotency
    storage is per-process (no Redis hop) — the same key sent twice
    inside one process always returns the same OrderRef.
    """
    await _audit(
        "create_order",
        {"customer_id": customer_id, "idempotency_key": idempotency_key},
    )
    if idempotency_key is not None:
        cached = _idempotency_cache.get(idempotency_key)
        if cached is not None:
            return cached
    async with await deps.session_factory() as session:
        order = Order(
            customer_id=customer_id,
            status=OrderStatus.PENDING.value,
            total_amount=Decimal("0"),
        )
        session.add(order)
        try:
            await session.flush()
        except IntegrityError:
            await session.rollback()
            return NotFound.for_("Customer", customer_id)
        await session.refresh(order)
        await session.commit()
    ref = OrderRef.model_validate(order, from_attributes=True)
    if idempotency_key is not None:
        _idempotency_cache[idempotency_key] = ref
    return ref


async def cancel_order(deps: Deps, id: int) -> CancelResult | NotFound:
    """Mark an order CANCELLED ; FK CASCADE removes its lines."""
    await _audit("cancel_order", {"id": id})
    async with await deps.session_factory() as session:
        order = await session.get(Order, id)
        if order is None:
            return NotFound.for_("Order", id)
        previous = order.status
        order.status = OrderStatus.CANCELLED.value
        await session.flush()
        await session.commit()
    return CancelResult(id=id, cancelled=True, previous_status=previous)


async def find_low_stock_products(
    deps: Deps,
    threshold: int = DEFAULT_LOW_STOCK_THRESHOLD,
) -> list[ProductLowStock]:
    """Products whose ``stock_quantity`` is strictly below ``threshold``."""
    await _audit("find_low_stock_products", {"threshold": threshold})
    threshold = max(0, threshold)
    stmt = select(Product).where(Product.stock_quantity < threshold).order_by(Product.stock_quantity)
    async with await deps.session_factory() as session:
        rows = (await session.execute(stmt)).scalars().all()
    return [ProductLowStock.model_validate(p, from_attributes=True) for p in rows]


async def get_customer_360(deps: Deps, id: int) -> Customer360 | NotFound:
    """Customer + roll-up of their orders.

    Single-query aggregate (count + sum + max(created_at)) over all
    the customer's orders. Returns Decimal("0") for revenue + 0 for
    count when the customer has no orders ; ``last_order_at`` is None
    in that case.
    """
    await _audit("get_customer_360", {"id": id})
    async with await deps.session_factory() as session:
        customer = await session.get(Customer, id)
        if customer is None:
            return NotFound.for_("Customer", id)
        agg_stmt = select(
            func.count(Order.id),
            func.coalesce(func.sum(Order.total_amount), Decimal("0")),
            func.max(Order.created_at),
        ).where(Order.customer_id == id)
        row = (await session.execute(agg_stmt)).one()
    order_count, total_revenue, last_order_at = row
    return Customer360(
        id=customer.id,
        name=customer.name,
        email=customer.email,
        order_count=int(order_count or 0),
        total_revenue=Decimal(total_revenue or 0),
        last_order_at=last_order_at,
    )


async def trigger_chaos_experiment(
    deps: Deps,
    scenario: ChaosScenario,
) -> ChaosResult:
    """Wraps the existing chaos endpoints. Admin-gated.

    Emulates each scenario in-process (no HTTP self-call) :

    - ``slow-query`` runs ``SELECT pg_sleep(2)`` if Postgres is connected,
      else a 2-second ``asyncio.sleep`` (so the demo works on SQLite-backed
      test envs too).
    - ``db-failure`` runs an intentionally-bad SQL — ``effective`` is
      True if the DB rejects it (the expected outcome).
    - ``kafka-timeout`` returns a synthetic 504-equivalent payload — same
      shape as the existing /customers/diagnostic/kafka-timeout endpoint.

    Marked admin-only because chaos experiments degrade observability
    panels (visible burn-rate spike, etc.) — viewers shouldn't be able
    to fire them.
    """
    require_role(ROLE_ADMIN)
    await _audit("trigger_chaos_experiment", {"scenario": scenario})
    if scenario not in ALLOWED_CHAOS_SCENARIOS:
        return ChaosResult(scenario=scenario, effective=False, detail="unknown scenario")
    if scenario == "slow-query":
        return await _chaos_slow_query(deps)
    if scenario == "db-failure":
        return await _chaos_db_failure(deps)
    return ChaosResult(
        scenario="kafka-timeout",
        effective=True,
        detail="synthetic 504 — no real broker call (matches /customers/diagnostic/kafka-timeout)",
    )


async def _chaos_slow_query(deps: Deps) -> ChaosResult:
    """Run pg_sleep(2) ; fall back to asyncio.sleep on SQLite."""
    from sqlalchemy import text
    from sqlalchemy.exc import DBAPIError

    async with await deps.session_factory() as session:
        try:
            await session.execute(text("SELECT pg_sleep(2)"))
        except DBAPIError:
            await asyncio.sleep(2)
    return ChaosResult(scenario="slow-query", effective=True, detail="2 s deliberate slow query")


async def _chaos_db_failure(deps: Deps) -> ChaosResult:
    """Run intentionally-bad SQL ; effective=True if DB rejects."""
    from sqlalchemy import text
    from sqlalchemy.exc import DBAPIError

    async with await deps.session_factory() as session:
        try:
            await session.execute(text("SELECT 1 FROM nonexistent_table_for_chaos"))
        except DBAPIError as exc:
            return ChaosResult(
                scenario="db-failure",
                effective=True,
                detail=f"db rejected as expected: {type(exc).__name__}",
            )
    # If we somehow reach here without a DBAPIError, the chaos didn't fire.
    return ChaosResult(
        scenario="db-failure",
        effective=False,
        detail="bad SQL was unexpectedly accepted",
    )


# ── Tool implementations — ML inference (1) ──────────────────────────────────


async def predict_customer_churn(
    deps: Deps,
    customer_id: int,
) -> ChurnPrediction | ChurnNotFound | ChurnServiceUnavailable:
    """Predict churn probability for ``customer_id`` via the loaded ONNX model.

    Soft-error contract — never throws across the MCP boundary :

    - Returns :class:`ChurnServiceUnavailable` if the predictor isn't
      ready (model file missing). The early-return wins over the
      not-found path because retrying with a different id wouldn't
      help — UX is clearer this way (matches Java sibling).
    - Returns :class:`ChurnNotFound` for missing ids.
    - Returns :class:`ChurnPrediction` on success.

    Mirrors the Java :class:`org.iris.ml.ChurnMcpToolService`
    behaviour exactly so an LLM sees identical shapes regardless of
    which backend handled the call.
    """
    await _audit("predict_customer_churn", {"customer_id": customer_id})
    if deps.churn_predictor is None or not deps.churn_predictor.is_ready():
        path = deps.churn_predictor.model_path if deps.churn_predictor is not None else "unconfigured"
        return ChurnServiceUnavailable(
            message=f"churn model not loaded (path={path})",
            hint=(
                "Provision /etc/models/churn_predictor.onnx via the "
                "iris-churn-model ConfigMap (shared ADR-0062), then "
                "restart the pod or call the future hot-reload endpoint."
            ),
        )
    if customer_id is None:
        return ChurnNotFound(customer_id=None, message="customer_id is required")
    async with await deps.session_factory() as session:
        customer = await session.get(Customer, customer_id)
        if customer is None:
            return ChurnNotFound(
                customer_id=customer_id,
                message=f"customer {customer_id} not found",
            )
        orders = list((await session.execute(select(Order).where(Order.customer_id == customer_id))).scalars().all())
        order_ids = [o.id for o in orders]
        if order_ids:
            order_lines = list(
                (await session.execute(select(OrderLine).where(OrderLine.order_id.in_(order_ids)))).scalars().all()
            )
        else:
            order_lines = []
    from datetime import UTC, datetime  # local import — keeps top imports tidy

    now = datetime.now(UTC)
    features = extract_features(customer, orders, order_lines, now=now)
    probability = deps.churn_predictor.predict_probability(features)
    band = classify_risk(probability)
    return ChurnPrediction(
        customer_id=customer_id,
        probability=round(probability, 6),
        risk_band=band,
        top_features=["days_since_last_order", "total_revenue_90d", "order_frequency"],
        model_version=deps.churn_predictor.model_version,
        predicted_at=now,
    )


# ── Tool implementations — Backend-local observability (7) ────────────────────


async def tail_logs(
    deps: Deps,
    n: int = 50,
    level: str | None = None,
    request_id: str | None = None,
) -> list[LogEvent]:
    """Return the last ``n`` events from the ring buffer.

    Filtered by optional log level (case-insensitive) and request_id
    (exact match against the structlog contextvar). NO Loki call —
    everything comes from the in-process buffer (default 500 entries,
    configurable via IRIS_MCP_RING_BUFFER_SIZE).
    """
    await _audit("tail_logs", {"n": n, "level": level, "request_id": request_id})
    capped = max(1, min(n, deps.ring_buffer.capacity))
    return deps.ring_buffer.snapshot(n=capped, level=level, request_id=request_id)


async def get_metrics(
    deps: Deps,
    name_filter: str | None = None,
    tags_filter: dict[str, str] | None = None,
) -> list[MetricSnapshot]:
    """Snapshot the prometheus REGISTRY through the 5-second TTL cache.

    ``name_filter`` is a case-sensitive substring (e.g. ``http_request``
    matches ``http_requests_total`` + ``http_request_duration_seconds``).
    ``tags_filter`` is a subset match — every key/value must be present
    on the sample's labels.
    """
    await _audit(
        "get_metrics",
        {"name_filter": name_filter, "tags_filter": tags_filter},
    )
    return deps.metrics_reader.list_samples(
        name_filter=name_filter,
        tags_filter=tags_filter,
    )


async def get_health(deps: Deps) -> HealthSnapshot:
    """Composite health — same DB ping as /actuator/health/readiness."""
    await _audit("get_health", {})
    db: AsyncSession | None = None
    try:
        db = await deps.session_factory()
        snapshot = await build_health_snapshot(db, include_details=False)
    finally:
        if db is not None:
            with suppress(Exception):
                await db.close()
    return snapshot


async def get_health_detail(deps: Deps) -> HealthSnapshot:
    """Same as get_health but with raw error strings ; admin-gated."""
    require_role(ROLE_ADMIN)
    await _audit("get_health_detail", {})
    db: AsyncSession | None = None
    try:
        db = await deps.session_factory()
        snapshot = await build_health_snapshot(db, include_details=True)
    finally:
        if db is not None:
            with suppress(Exception):
                await db.close()
    return snapshot


async def get_actuator_env(deps: Deps, prefix: str | None = None) -> EnvSnapshot:
    """Read settings ; redact secrets matching the canonical pattern."""
    await _audit("get_actuator_env", {"prefix": prefix})
    return build_env_snapshot(deps.settings, prefix=prefix)


async def get_actuator_info(deps: Deps) -> InfoBlock:
    """Build / runtime info."""
    await _audit("get_actuator_info", {})
    return build_info_block(deps.app)


async def get_openapi_spec(
    deps: Deps,
    summary: bool = True,
) -> OpenApiSummary | dict[str, Any]:
    """Either a paths-only summary or the full OpenAPI dict.

    Default ``summary=True`` is the LLM-friendly form (~3 KB for our
    service vs ~50 KB full). Switch to ``summary=False`` only when
    schema-driven code generation needs the raw spec.
    """
    await _audit("get_openapi_spec", {"summary": summary})
    return build_openapi(deps.app, summary=summary)


# ── Idempotency cache for create_order ────────────────────────────────────────

#: Per-process LRU-equivalent. Bounded so a runaway LLM that sends
#: thousands of distinct keys can't OOM the process.
_idempotency_cache: dict[str, OrderRef] = {}
_IDEMPOTENCY_MAX: Final[int] = 1024


def _evict_oldest_if_full() -> None:
    """Best-effort LRU — drop the oldest insertion if we hit the cap.

    Python dicts preserve insertion order since 3.7 so ``next(iter(...))``
    gives us the oldest key without an extra structure.
    """
    if len(_idempotency_cache) > _IDEMPOTENCY_MAX:
        oldest = next(iter(_idempotency_cache))
        del _idempotency_cache[oldest]


def reset_idempotency_cache() -> None:
    """Test hook — clear the per-process cache."""
    _idempotency_cache.clear()


# Re-bind create_order so the cache eviction runs on each successful insert.
_original_create_order = create_order


async def create_order_cached(
    deps: Deps,
    customer_id: int,
    idempotency_key: str | None = None,
) -> OrderRef | NotFound:
    """Public ``create_order`` — wraps the inner version with bounded cache."""
    result = await _original_create_order(deps, customer_id, idempotency_key)
    if idempotency_key is not None and isinstance(result, OrderRef):
        _evict_oldest_if_full()
    return result


# Install the cached version under the original name — registration uses this.
# (Re-binding the symbol makes call-sites elsewhere in this module pick up the
# bounded-cache wrapper without an extra import alias.)
create_order = create_order_cached


# ── Registration ──────────────────────────────────────────────────────────────


def register_tools(mcp: Any, deps: Deps) -> None:
    """Bind every tool to the FastMCP instance with the ``deps`` closure.

    Imported lazily by :func:`mount.mount_mcp_server` so importing this
    module in isolation (tests, doc tooling) doesn't pull in the full
    FastMCP runtime.

    Split into 4 helper registers (orders, products, customers+chaos,
    observability) to keep each below the cyclomatic-complexity ceiling
    (≤ 10 per the project's ruff C90 rule). The split also gives us
    a natural unit-test boundary — each register can be tested with a
    mock FastMCP that just records ``@tool`` calls.
    """
    _register_order_tools(mcp, deps)
    _register_product_tools(mcp, deps)
    _register_customer_chaos_tools(mcp, deps)
    _register_ml_tools(mcp, deps)
    _register_observability_tools(mcp, deps)


def _register_order_tools(mcp: Any, deps: Deps) -> None:
    """list_recent_orders + get_order_by_id + create_order + cancel_order."""

    @mcp.tool(
        name="list_recent_orders",
        description=(
            "Lists recent orders, newest-first. Optional status filter "
            "(PENDING, CONFIRMED, SHIPPED, CANCELLED). Limit capped at 100."
        ),
    )
    async def _list_recent_orders(
        limit: int = 20,
        status: OrderStatusFilter | None = None,
    ) -> list[OrderListItem]:
        return await list_recent_orders(deps, limit, status)

    @mcp.tool(
        name="get_order_by_id",
        description="Returns a single order by ID. Returns a NotFound shape if absent.",
    )
    async def _get_order_by_id(id: int) -> OrderListItem | NotFound:
        return await get_order_by_id(deps, id)

    @mcp.tool(
        name="create_order",
        description=(
            "Creates an empty order attached to a customer. "
            "Optional Idempotency-Key — re-sending the same key returns the same order."
        ),
    )
    async def _create_order(
        customer_id: int,
        idempotency_key: str | None = None,
    ) -> OrderRef | NotFound:
        return await create_order(deps, customer_id, idempotency_key)

    @mcp.tool(
        name="cancel_order",
        description="Marks an order CANCELLED ; cascades line removal via FK.",
    )
    async def _cancel_order(id: int) -> CancelResult | NotFound:
        return await cancel_order(deps, id)


def _register_product_tools(mcp: Any, deps: Deps) -> None:
    """find_low_stock_products."""

    @mcp.tool(
        name="find_low_stock_products",
        description="Products with stock_quantity strictly below threshold (default 10).",
    )
    async def _find_low_stock_products(
        threshold: int = DEFAULT_LOW_STOCK_THRESHOLD,
    ) -> list[ProductLowStock]:
        return await find_low_stock_products(deps, threshold)


def _register_customer_chaos_tools(mcp: Any, deps: Deps) -> None:
    """get_customer_360 + trigger_chaos_experiment."""

    @mcp.tool(
        name="get_customer_360",
        description="Customer + roll-up : order_count, total_revenue, last_order_at.",
    )
    async def _get_customer_360(id: int) -> Customer360 | NotFound:
        return await get_customer_360(deps, id)

    @mcp.tool(
        name="trigger_chaos_experiment",
        description=(
            "Runs a deliberate failure scenario (slow-query, db-failure, kafka-timeout). "
            "Admin-only — viewers cannot trigger demo-degrading actions."
        ),
    )
    async def _trigger_chaos_experiment(scenario: ChaosScenario) -> ChaosResult:
        return await trigger_chaos_experiment(deps, scenario)


def _register_ml_tools(mcp: Any, deps: Deps) -> None:
    """predict_customer_churn — ONNX inference + feature parity with Java."""

    @mcp.tool(
        name="predict_customer_churn",
        description=(
            "Predict the churn probability for one customer (0..1) + risk band "
            "(LOW/MEDIUM/HIGH). Returns a NotFound shape if the customer is absent, "
            "or a ServiceUnavailable shape if the ONNX model isn't loaded yet "
            "(ConfigMap pending — see shared ADR-0062)."
        ),
    )
    async def _predict_customer_churn(
        customer_id: int,
    ) -> ChurnPrediction | ChurnNotFound | ChurnServiceUnavailable:
        return await predict_customer_churn(deps, customer_id)


def _register_observability_tools(mcp: Any, deps: Deps) -> None:
    """tail_logs + get_metrics + get_health(_detail) + get_actuator_* + get_openapi_spec."""

    @mcp.tool(
        name="tail_logs",
        description=(
            "Returns the last n log events from the in-process ring buffer. "
            "Filter by level + request_id. NOT a Loki query."
        ),
    )
    async def _tail_logs(
        n: int = 50,
        level: str | None = None,
        request_id: str | None = None,
    ) -> list[LogEvent]:
        return await tail_logs(deps, n, level, request_id)

    @mcp.tool(
        name="get_metrics",
        description=(
            "Returns prometheus_client REGISTRY samples. "
            "Filter by name (substring) + tags (subset match). 5-second cache."
        ),
    )
    async def _get_metrics(
        name_filter: str | None = None,
        tags_filter: dict[str, str] | None = None,
    ) -> list[MetricSnapshot]:
        return await get_metrics(deps, name_filter, tags_filter)

    @mcp.tool(
        name="get_health",
        description="Composite UP/DOWN with per-component statuses. Sanitized for viewers.",
    )
    async def _get_health() -> HealthSnapshot:
        return await get_health(deps)

    @mcp.tool(
        name="get_health_detail",
        description="Same as get_health but with raw error strings. Admin-only.",
    )
    async def _get_health_detail() -> HealthSnapshot:
        return await get_health_detail(deps)

    @mcp.tool(
        name="get_actuator_env",
        description=(
            "Filtered Settings dump. Secrets matching (?i).*(password|secret|token|key|credential).* are redacted."
        ),
    )
    async def _get_actuator_env(prefix: str | None = None) -> EnvSnapshot:
        return await get_actuator_env(deps, prefix)

    @mcp.tool(
        name="get_actuator_info",
        description="Build + runtime info — title, version, Python interpreter.",
    )
    async def _get_actuator_info() -> InfoBlock:
        return await get_actuator_info(deps)

    @mcp.tool(
        name="get_openapi_spec",
        description=(
            "Either a paths-by-verb summary (default, ~3 KB) or the full OpenAPI dict. "
            "Use summary=False only when you need the raw schemas."
        ),
    )
    async def _get_openapi_spec(summary: bool = True) -> OpenApiSummary | dict[str, Any]:
        return await get_openapi_spec(deps, summary)
