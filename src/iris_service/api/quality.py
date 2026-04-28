"""Quality endpoint — `GET /actuator/quality`.

Mirrors Java's `/actuator/quality` aggregator endpoint : returns a snapshot
of the project's code-quality signals (test count, coverage, lint status,
last build time, last successful pipeline). Consumed by the Angular
frontend Quality page.

Returns synthetic / cached values for the demo. In a real deployment, the
values would be sourced from :
- Test count + coverage : pytest-cov XML report uploaded by CI
- Lint status : ruff + mypy + import-linter exit codes
- Pipeline status : GitLab CI API
- Last build time : Docker image label / OCI annotation

We return static placeholders that match the shape consumed by the UI's
`api.service.ts → getQuality()` call.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel, Field

from iris_service import __version__

router = APIRouter(prefix="/actuator", tags=["Actuator"])

# Traffic-light status discriminator. Literal narrows the type to exactly
# these three values — Pydantic v2 enforces at validation time
# (extra value → 422 ; mypy refuses constructor call with `status="grun"`
# at static-analysis time).
type QualityStatus = Literal["green", "yellow", "red"]


class QualitySignal(BaseModel):
    """One quality signal — name + value + status (green / yellow / red)."""

    name: str
    value: str
    status: QualityStatus
    details: dict[str, str] = Field(default_factory=dict)


class QualityResponse(BaseModel):
    """Aggregated quality snapshot."""

    service: str
    version: str
    timestamp: datetime
    signals: list[QualitySignal]
    overall_status: QualityStatus = Field(serialization_alias="overallStatus")


@router.get("/quality", response_model=QualityResponse)
async def quality() -> QualityResponse:
    """Aggregate quality signals — single GET for the UI Quality page.

    Currently returns static placeholder values matching the shape the
    Angular UI consumes. Wire to real data sources (pytest-cov XML,
    ruff/mypy exit codes, GitLab CI API) when the demo expands beyond
    the static-snapshot phase.
    """
    signals = [
        QualitySignal(
            name="tests",
            value="87 passing",
            status="green",
            details={"unit": "82", "integration": "5", "coverage": "82.46%"},
        ),
        QualitySignal(
            name="lint",
            value="ruff clean + mypy strict",
            status="green",
            details={"ruff_rules": "250+", "mypy_files": "36"},
        ),
        QualitySignal(
            name="security",
            value="bandit S* via ruff",
            status="green",
            details={"hotspots_reviewed": "100%"},
        ),
        QualitySignal(
            name="dependencies",
            value="pinned exactly",
            status="green",
            details={"renovate_managed": "false", "outdated_count": "0"},
        ),
        QualitySignal(
            name="ci_pipeline",
            value="see GitLab CI",
            status="yellow",
            details={"reason": "runner offline ; pipelines pending"},
        ),
    ]
    # mypy narrows this to QualityStatus thanks to the Literal aliases
    # — passing "green"/"yellow"/"red" as a bare str would error at the
    # QualityResponse() call below.
    overall: QualityStatus = (
        "red"
        if any(s.status == "red" for s in signals)
        else "yellow"
        if any(s.status == "yellow" for s in signals)
        else "green"
    )
    return QualityResponse(
        service="iris-service-python",
        version=__version__,
        timestamp=datetime.now(UTC),
        signals=signals,
        overall_status=overall,
    )
