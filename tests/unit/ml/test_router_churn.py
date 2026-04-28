"""POST /customers/{id}/churn-prediction — endpoint tests.

Covers the 3 paths explicit in the Java sibling :

- 503 when the model isn't loaded (graceful-degradation per ADR-0062).
- 404 when the customer doesn't exist (predictor IS loaded).
- 200 on the happy path (predictor loaded, customer + orders present).

The predictor is overridden via :func:`app.dependency_overrides` —
mirrors how every other router test in this codebase swaps real
infra for in-memory stubs (no real ONNX file needed for unit tests ;
the cross-language smoke test in Phase G covers the real-model path).
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pytest
from fastapi import FastAPI
from httpx import AsyncClient

from iris_service.ml.inference import N_FEATURES, ChurnPredictor
from iris_service.ml.predictor_singleton import get_churn_predictor


class _StubPredictor:
    """Drop-in :class:`ChurnPredictor` stub — deterministic 0.5 probability.

    Only the methods the router calls are implemented.
    """

    def __init__(self, *, ready: bool = True, version: str = "v-stub") -> None:
        self._ready = ready
        self._version = version
        self.model_path = "/test/stub.onnx"

    @property
    def model_version(self) -> str:
        return self._version

    def is_ready(self) -> bool:
        return self._ready

    def predict_probability(self, features: np.ndarray) -> float:
        assert features.shape == (N_FEATURES,)
        return 0.5


async def _create_customer(client: AsyncClient, name: str = "alice") -> int:
    response = await client.post(
        "/customers",
        json={"name": name, "email": f"{name}@gmail.com"},
    )
    assert response.status_code == 201
    return int(response.json()["id"])


@pytest.mark.asyncio
async def test_returns_503_when_model_not_loaded(app: FastAPI, client: AsyncClient) -> None:
    """503 wins over not-found when the predictor isn't ready."""
    app.dependency_overrides[get_churn_predictor] = lambda: _StubPredictor(ready=False)

    cid = await _create_customer(client, "alice")
    response = await client.post(f"/customers/{cid}/churn-prediction")
    assert response.status_code == 503
    assert "ConfigMap" in response.json()["detail"]


@pytest.mark.asyncio
async def test_returns_404_when_customer_missing(app: FastAPI, client: AsyncClient) -> None:
    """Predictor ready but the customer id has no row → 404."""
    app.dependency_overrides[get_churn_predictor] = lambda: _StubPredictor()

    response = await client.post("/customers/99999/churn-prediction")
    assert response.status_code == 404
    assert "99999" in response.json()["detail"]


@pytest.mark.asyncio
async def test_returns_200_with_prediction_payload(app: FastAPI, client: AsyncClient) -> None:
    """Happy path : 200 + ChurnPrediction shape with stub probability."""
    app.dependency_overrides[get_churn_predictor] = lambda: _StubPredictor()

    cid = await _create_customer(client, "alice")
    response = await client.post(f"/customers/{cid}/churn-prediction")
    assert response.status_code == 200
    body = response.json()
    assert body["customer_id"] == cid
    assert body["probability"] == pytest.approx(0.5, abs=1e-6)
    assert body["risk_band"] == "MEDIUM"  # 0.5 → MEDIUM with default thresholds
    assert body["model_version"] == "v-stub"
    assert "predicted_at" in body
    assert isinstance(body["top_features"], list)
    assert body["top_features"]


@pytest.mark.asyncio
async def test_rejects_zero_or_negative_id(client: AsyncClient) -> None:
    """Path validation : Path(ge=1) → 422 on bad ids."""
    bad = await client.post("/customers/0/churn-prediction")
    assert bad.status_code == 422


@pytest.mark.asyncio
async def test_real_predictor_singleton_is_used_by_default(app: FastAPI) -> None:
    """Without an override, the singleton from predictor_singleton is wired."""
    predictor: Any = app.dependency_overrides.get(get_churn_predictor, get_churn_predictor)()
    assert isinstance(predictor, ChurnPredictor)
    # The default model path is not provisioned in tests → not ready,
    # which is the expected boot-without-model state.
    assert predictor.is_ready() is False
