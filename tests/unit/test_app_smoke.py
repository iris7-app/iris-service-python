"""Smoke tests for the FastAPI app — no external dependencies needed."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from iris_service.app import create_app


@pytest.fixture
def client() -> TestClient:
    return TestClient(create_app())


def test_root_returns_service_metadata(client: TestClient) -> None:
    response = client.get("/")
    assert response.status_code == 200
    body = response.json()
    assert body["service"] == "iris-service-python"
    assert "version" in body


def test_openapi_docs_available(client: TestClient) -> None:
    response = client.get("/docs")
    assert response.status_code == 200


def test_openapi_schema_available(client: TestClient) -> None:
    response = client.get("/openapi.json")
    assert response.status_code == 200
    schema = response.json()
    assert schema["info"]["title"] == "Iris Customer Service (Python)"
