"""Quality endpoint tests."""

from __future__ import annotations

import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_quality_returns_aggregated_signals(client: AsyncClient) -> None:
    response = await client.get("/actuator/quality")
    assert response.status_code == 200
    body = response.json()
    assert body["service"] == "iris-service-python"
    assert "version" in body
    assert "signals" in body
    signals = body["signals"]
    assert len(signals) == 5
    expected_names = {"tests", "lint", "security", "dependencies", "ci_pipeline"}
    actual_names = {s["name"] for s in signals}
    assert expected_names == actual_names


@pytest.mark.asyncio
async def test_quality_overall_status_propagates(client: AsyncClient) -> None:
    response = await client.get("/actuator/quality")
    body = response.json()
    # ci_pipeline is yellow (runner offline) → overall ≥ yellow
    assert body["overallStatus"] in {"yellow", "red"}
