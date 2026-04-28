"""FastAPI router — POST /customers/{id}/churn-prediction.

Mirrors the Java :class:`org.iris.ml.ChurnController` :

- 200 + :class:`ChurnPrediction` on success.
- 404 if the customer doesn't exist.
- 503 if the ONNX model isn't loaded yet (file missing — the
  graceful-degradation path from shared ADR-0062).

Auth identical to the rest of the API : the global JWT or
X-API-Key middleware (see :mod:`iris_service.middleware`)
already runs in front of every router ; nothing to wire here.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from iris_service.customer.models import Customer
from iris_service.db.base import get_db_session
from iris_service.ml.dtos import ChurnPrediction
from iris_service.ml.inference import (
    ChurnPredictor,
    extract_features,
)
from iris_service.ml.predictor_singleton import get_churn_predictor
from iris_service.ml.risk_band import classify_risk
from iris_service.order.models import Order
from iris_service.order.order_line_models import OrderLine

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/customers", tags=["Churn"])

#: Placeholder list of "top features" — Phase E (MLflow + SHAP)
#: will replace this with a real per-prediction explanation. Until
#: then we ship the canonical-priority sequence so the UI has a
#: stable shape to render.
_PLACEHOLDER_TOP_FEATURES: tuple[str, ...] = (
    "days_since_last_order",
    "total_revenue_90d",
    "order_frequency",
)


@router.post(
    "/{customer_id}/churn-prediction",
    response_model=ChurnPrediction,
    summary="Predict churn probability for one customer",
    responses={
        404: {"description": "Customer not found"},
        503: {"description": "ONNX model not loaded (ConfigMap pending)"},
    },
)
async def predict_customer_churn(
    customer_id: Annotated[int, Path(ge=1, description="Customer database id")],
    db: Annotated[AsyncSession, Depends(get_db_session)],
    predictor: Annotated[ChurnPredictor, Depends(get_churn_predictor)],
) -> ChurnPrediction:
    """Compute the churn probability for ``customer_id``.

    Same logic as :class:`org.iris.ml.ChurnController` :

    1. 503 early-return if the predictor isn't ready (model file
       missing — operator hint points at the ConfigMap from
       ADR-0062).
    2. 404 if the customer doesn't exist.
    3. Load the customer's orders + order lines (one query each).
    4. Build the 8-feature vector via
       :func:`iris_service.ml.inference.extract_features`.
    5. Run ONNX inference, apply sigmoid, classify the band, return.
    """
    if not predictor.is_ready():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                "Churn model not loaded yet. Provision "
                "/etc/models/churn_predictor.onnx via the "
                "iris-churn-model ConfigMap (shared ADR-0062)."
            ),
        )

    customer = await db.get(Customer, customer_id)
    if customer is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"customer {customer_id} not found",
        )

    orders_result = await db.execute(select(Order).where(Order.customer_id == customer_id))
    orders = list(orders_result.scalars().all())
    order_ids = [o.id for o in orders]

    if order_ids:
        lines_result = await db.execute(select(OrderLine).where(OrderLine.order_id.in_(order_ids)))
        order_lines = list(lines_result.scalars().all())
    else:
        order_lines = []

    now = datetime.now(UTC)
    features = extract_features(customer, orders, order_lines, now=now)
    probability = predictor.predict_probability(features)
    band = classify_risk(probability)

    return ChurnPrediction(
        customer_id=customer_id,
        probability=round(probability, 6),
        risk_band=band,
        top_features=list(_PLACEHOLDER_TOP_FEATURES),
        model_version=predictor.model_version,
        predicted_at=now,
    )
