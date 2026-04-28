"""Customer Churn — PyTorch MLP + ONNX export.

Per [shared ADR-0061](https://gitlab.com/iris-7/iris-service-shared/-/blob/main/docs/adr/0061-customer-churn-prediction.md)
§"Model architecture (3-layer MLP, PyTorch)".

The model is intentionally small (~5K params) — the goal is
demonstrating the full PyTorch + ONNX lifecycle, not maximising AUC
on tabular data (where sklearn's GradientBoosting would beat this).
For 8 features and ≤ 50K customers, the MLP is sufficient and the
ONNX export is byte-identical across PyTorch versions.

ONNX export contract per shared ADR-0061 §"ONNX export contract" :

- Input  : ``input``  — float32 tensor, shape ``[batch_size, 8]``.
- Output : ``logits`` — float32 tensor, shape ``[batch_size, 1]``.
- Sigmoid is applied IN INFERENCE CODE (Java + Python), NOT in the
  ONNX graph. Keeps the export simple and lets us swap calibration
  (Platt scaling, isotonic regression) without re-export.
"""

from __future__ import annotations

from pathlib import Path
from typing import Final

import torch
import torch.nn as nn

# Number of input features — must match :data:`iris_service.ml.feature_engineering.FEATURE_NAMES`.
N_FEATURES: Final[int] = 8

# Hidden layer width per ADR-0061 §"Model architecture". Two hidden
# layers of width 16 with ReLU + Dropout.
_HIDDEN_DIM: Final[int] = 16
_DROPOUT: Final[float] = 0.2


class ChurnMLP(nn.Module):
    """3-layer MLP for binary churn prediction.

    Forward returns RAW LOGITS (pre-sigmoid). Apply ``torch.sigmoid``
    at inference time to convert to probability ∈ [0, 1]. This split
    keeps the loss function (:class:`torch.nn.BCEWithLogitsLoss`)
    numerically stable during training.

    Architecture :
        Linear(8, 16) → ReLU → Dropout(0.2)
      → Linear(16, 16) → ReLU → Dropout(0.2)
      → Linear(16, 1)

    Parameters : ~5K. Trains in ~30s on CPU for 10K rows.
    """

    def __init__(self, n_features: int = N_FEATURES) -> None:
        super().__init__()
        self.net = nn.Sequential(
            nn.Linear(n_features, _HIDDEN_DIM),
            nn.ReLU(),
            nn.Dropout(_DROPOUT),
            nn.Linear(_HIDDEN_DIM, _HIDDEN_DIM),
            nn.ReLU(),
            nn.Dropout(_DROPOUT),
            nn.Linear(_HIDDEN_DIM, 1),
        )

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        """Return raw logits ; apply sigmoid downstream for probability."""
        return self.net(x)


def export_to_onnx(
    model: ChurnMLP,
    *,
    output_path: Path,
    opset_version: int = 17,
) -> None:
    """Export the trained model to ONNX with the canonical contract.

    Per shared ADR-0061 §"ONNX export contract" :
    - Input name : ``input``, shape ``[batch_size, 8]``.
    - Output name : ``logits``, shape ``[batch_size, 1]``.
    - Dynamic batch axis (model accepts any batch size at inference).
    - Opset 17 — stable across recent ONNX runtime versions.

    The model is set to eval mode (Dropout disabled) before export so
    inference returns deterministic predictions.
    """
    model.eval()
    dummy = torch.randn(1, N_FEATURES)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    torch.onnx.export(
        model,
        (dummy,),
        str(output_path),
        input_names=["input"],
        output_names=["logits"],
        dynamic_axes={
            "input": {0: "batch_size"},
            "logits": {0: "batch_size"},
        },
        opset_version=opset_version,
    )


def predict_proba(model: ChurnMLP, features: torch.Tensor) -> torch.Tensor:
    """Apply the model + sigmoid to yield probabilities ∈ [0, 1].

    Inference helper used by the training script for evaluation
    metrics (AUC, calibration). Production inference (Phase B + C)
    re-implements this in onnxruntime — same forward + same sigmoid,
    different runtime.
    """
    model.eval()
    with torch.no_grad():
        logits = model(features)
        return torch.sigmoid(logits).squeeze(-1)
