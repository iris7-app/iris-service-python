"""Actuator endpoint smoke tests."""

from __future__ import annotations

import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_liveness_returns_up(client: AsyncClient) -> None:
    response = await client.get("/actuator/health/liveness")
    assert response.status_code == 200
    assert response.json() == {"status": "UP"}


@pytest.mark.asyncio
async def test_readiness_returns_up_when_db_ok(client: AsyncClient) -> None:
    response = await client.get("/actuator/health/readiness")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "UP"
    assert body["components"]["db"]["status"] == "UP"


@pytest.mark.asyncio
async def test_health_alias(client: AsyncClient) -> None:
    response = await client.get("/actuator/health")
    assert response.status_code == 200
    assert response.json()["status"] == "UP"


@pytest.mark.asyncio
async def test_info_returns_runtime_metadata(client: AsyncClient) -> None:
    response = await client.get("/actuator/info")
    assert response.status_code == 200
    body = response.json()
    assert body["service"] == "iris-service-python"
    assert "version" in body
    assert body["runtime"]["name"] == "CPython"


@pytest.mark.asyncio
async def test_prometheus_returns_exposition(client: AsyncClient) -> None:
    response = await client.get("/actuator/prometheus")
    assert response.status_code == 200
    assert "text/plain" in response.headers["content-type"]
    # prometheus exposition format starts with HELP / TYPE comments
    body = response.text
    assert "# HELP" in body or body == ""  # might be empty if no metrics yet
