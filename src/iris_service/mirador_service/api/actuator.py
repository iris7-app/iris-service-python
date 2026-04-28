"""Actuator endpoints — health, info, prometheus.

Mirrors Spring Boot Actuator's contract :
- GET /actuator/health           composite (liveness + readiness)
- GET /actuator/health/liveness  process is alive (no downstream check)
- GET /actuator/health/readiness ready to serve (DB + Redis + Kafka up)
- GET /actuator/info             build info, git, jvm-equivalent runtime info
- GET /actuator/prometheus       prometheus exposition format
"""

from __future__ import annotations

import platform
import sys
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Response, status
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from iris_service import __version__
from iris_service.db.base import get_db_session

router = APIRouter(prefix="/actuator", tags=["Actuator"])


@router.get("/health/liveness")
async def liveness() -> dict[str, str]:
    """Process is alive. Returns UP unconditionally — used by k8s liveness probe."""
    return {"status": "UP"}


@router.get("/health/readiness")
async def readiness(
    db: Annotated[AsyncSession, Depends(get_db_session)],
    response: Response,
) -> dict[str, Any]:
    """Composite readiness check — DB + (TODO Redis + Kafka).

    Returns 503 if any component is DOWN. Used by k8s readiness probe to
    stop routing traffic to a pod that can't serve.
    """
    components: dict[str, dict[str, str]] = {}
    overall_up = True

    # DB check : SELECT 1
    try:
        await db.execute(text("SELECT 1"))
        components["db"] = {"status": "UP"}
    except Exception as exc:
        components["db"] = {"status": "DOWN", "error": str(exc)}
        overall_up = False

    # TODO : Redis ping, Kafka admin client describe

    if not overall_up:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE

    return {
        "status": "UP" if overall_up else "DOWN",
        "components": components,
    }


@router.get("/health")
async def health(
    db: Annotated[AsyncSession, Depends(get_db_session)],
    response: Response,
) -> dict[str, Any]:
    """Composite health = readiness (Spring Boot's default behaviour)."""
    return await readiness(db, response)


@router.get("/info")
async def info() -> dict[str, Any]:
    """Build + runtime info — mirror of Spring's /actuator/info."""
    return {
        "service": "iris-service-python",
        "version": __version__,
        "runtime": {
            "name": "CPython",
            "version": sys.version.split()[0],
            "implementation": platform.python_implementation(),
            "platform": platform.platform(),
        },
    }


@router.get("/prometheus", response_class=Response)
async def prometheus() -> Response:
    """Prometheus exposition format — used by /scrape jobs.

    starlette-prometheus middleware (registered in app factory) populates
    the registry with HTTP request metrics ; custom application metrics
    declared via prometheus_client.Counter/Gauge/Histogram are also
    surfaced here.
    """
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)
