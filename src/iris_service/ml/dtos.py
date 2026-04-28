"""Pydantic DTOs for the churn-prediction REST + MCP surface.

Mirrors the Java records :class:`com.mirador.ml.ChurnPredictionDto`
+ the soft-error DTOs (:class:`ChurnMcpToolService.NotFoundDto` and
:class:`ChurnMcpToolService.ServiceUnavailableDto`) so the same
JSON shapes appear from both backends — a property the
"interchangeable backends" contract (see common ADR-0008) depends on.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from iris_service.ml.risk_band import RiskBand


class ChurnPrediction(BaseModel):
    """Successful churn-prediction payload — same shape as Java."""

    model_config = ConfigDict(extra="forbid")

    customer_id: int = Field(..., description="Database id of the predicted customer.")
    probability: float = Field(
        ...,
        ge=0.0,
        le=1.0,
        description="Churn probability in [0, 1] — sigmoid of the ONNX logit.",
    )
    risk_band: RiskBand = Field(
        ...,
        description="LOW / MEDIUM / HIGH — derived via mirador.churn.risk-thresholds.",
    )
    top_features: list[str] = Field(
        default_factory=list,
        description=(
            "Most influential features for this prediction (Phase E will "
            "fill via SHAP ; Phase C ships the placeholder list)."
        ),
    )
    model_version: str = Field(
        ...,
        description="Identifier of the ONNX model that produced the prediction.",
    )
    predicted_at: datetime = Field(
        ...,
        description="Server-side timestamp at which the prediction was computed.",
    )


class ChurnNotFound(BaseModel):
    """Soft-error DTO — customer id missing or invalid (MCP path)."""

    model_config = ConfigDict(extra="forbid")

    customer_id: int | None = Field(
        ...,
        description="The id that was looked up (None if the caller passed null).",
    )
    message: str = Field(..., description="Human-readable reason.")


class ChurnServiceUnavailable(BaseModel):
    """Soft-error DTO — model not loaded yet (MCP path)."""

    model_config = ConfigDict(extra="forbid")

    message: str = Field(..., description="Why the prediction can't be served right now.")
    hint: str = Field(
        ...,
        description=(
            "Operator hint — what to check next (ConfigMap, model path, etc.). "
            "Mirrors Java's ServiceUnavailableDto exactly."
        ),
    )
