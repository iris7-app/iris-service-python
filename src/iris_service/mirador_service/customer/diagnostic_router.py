"""Diagnostic scenario endpoints — provoke specific failure modes for demo.

Mirrors Java's diagnostic endpoints : `slow-query`, `db-down`,
`kafka-timeout`. Each one DELIBERATELY induces a failure mode so the
demo can show the observability stack catching it (Tempo trace with a
slow span, Loki log with the error, Mimir metric with the spike).

Routes :
- `GET /customers/diagnostic/slow-query?seconds=N` — sleeps N seconds in
  the DB query to surface a long span in Tempo + p99 spike in Mimir.
- `GET /customers/diagnostic/db-failure` — opens a connection then closes
  it server-side mid-query to surface a connection error in the logs.
- `GET /customers/diagnostic/kafka-timeout` — calls a Kafka request-reply
  with an artificially short timeout to surface a 504 + Kafka latency
  histogram tail.

These endpoints are NOT hidden behind auth — they're for demo purposes
and intentionally accessible. In production they'd be gated by
`@require_role("ROLE_ADMIN")` or removed entirely.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from iris_service.db.base import get_db_session

router = APIRouter(prefix="/customers/diagnostic", tags=["Customer — diagnostic"])


@router.get("/slow-query")
async def slow_query(
    db: Annotated[AsyncSession, Depends(get_db_session)],
    seconds: Annotated[int, Query(ge=1, le=10, description="Sleep duration in seconds (1-10)")] = 3,
) -> dict[str, Any]:
    """Run a deliberately slow query.

    Uses pg_sleep on Postgres (real demo path) ; falls back to Python
    asyncio.sleep on SQLite (test path — pg_sleep doesn't exist).
    The slow span shows up in Tempo as a long `db.query` span.
    """
    started = datetime.now(UTC)
    try:
        await db.execute(text(f"SELECT pg_sleep({seconds})"))
    except DBAPIError:
        # SQLite doesn't have pg_sleep — fallback for tests.
        await asyncio.sleep(seconds)

    elapsed = (datetime.now(UTC) - started).total_seconds()
    return {
        "scenario": "slow-query",
        "requested_seconds": seconds,
        "actual_seconds": round(elapsed, 2),
        "note": "Look at the Tempo trace for this request — span db.query duration ≈ requested_seconds.",
    }


@router.get("/db-failure")
async def db_failure(
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> dict[str, Any]:
    """Trigger a deliberate DB error.

    Executes invalid SQL to provoke a DBAPIError. The error surfaces in
    the structured logs (Loki) + actuator/health/readiness flips DOWN
    briefly until the pool recovers.
    """
    try:
        await db.execute(text("SELECT 1 FROM nonexistent_table_for_demo_failure"))
    except DBAPIError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"db-failure scenario triggered : {type(exc).__name__}",
        ) from exc
    # If for some reason the bad SQL didn't raise, return 500 anyway.
    raise HTTPException(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        detail="db-failure scenario : expected error did not fire",
    )


@router.get("/kafka-timeout")
async def kafka_timeout() -> dict[str, Any]:
    """Simulate a Kafka enrichment timeout.

    Returns 504 immediately with a synthetic timeout payload — same
    shape as the real /customers/{id}/enrich would return on Kafka
    timeout. Useful for demoing the Problem+JSON error contract +
    UI's degraded-state rendering without needing to actually break
    Kafka.
    """
    raise HTTPException(
        status_code=status.HTTP_504_GATEWAY_TIMEOUT,
        detail="kafka-timeout scenario : synthetic 504 (no real broker call made)",
    )
