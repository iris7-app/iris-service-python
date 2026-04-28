"""Customer Churn — main training entry point (Phase A).

Reads training data (synthetic Faker by default ; ``--data-source
postgres`` for real data — Phase A migration path), engineers
features, trains the :class:`iris_service.ml.model.ChurnMLP`, evaluates
metrics, exports to ONNX, and logs everything to MLflow.

Pipeline :

    [data source] ─→ [feature_engineering.build_features]
                  ─→ [label_churn]
                  ─→ [train/val split (stratified)]
                  ─→ [StandardScaler fit on train, transform both]
                  ─→ [PyTorch training loop (Adam + early stop on val AUC)]
                  ─→ [evaluation (AUC, P/R, calibration)]
                  ─→ [acceptance gate (AUC ≥ tool.churn.training.auc_gate)]
                  ─→ [ONNX export]
                  ─→ [MLflow log_artifact + register_model]

Usage :

    # Local synthetic run (no MLflow server needed) :
    uv run python -m iris_service.ml.train_churn

    # With MLflow tracking server :
    MLFLOW_TRACKING_URI=http://localhost:5000 uv run python -m iris_service.ml.train_churn

    # Real Postgres data (read-only — connection string from env) :
    uv run python -m iris_service.ml.train_churn --data-source postgres

The script is intentionally CLI-driven (no Python API beyond the
helpers). The training pipeline is the contract ; downstream
phases (B, C, D, E) consume the produced ``.onnx`` artefact via
MLflow.
"""

from __future__ import annotations

import argparse
import logging
import sys
import tomllib
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import torch
import torch.nn as nn
from sklearn.metrics import (
    average_precision_score,
    brier_score_loss,
    confusion_matrix,
    roc_auc_score,
)
from sklearn.model_selection import train_test_split
from sklearn.preprocessing import StandardScaler
from torch.utils.data import DataLoader, TensorDataset

from iris_service.ml.feature_engineering import FEATURE_NAMES, build_features, label_churn
from iris_service.ml.model import ChurnMLP, export_to_onnx, predict_proba
from iris_service.ml.seed_demo_data import generate_dataset

logger = logging.getLogger(__name__)

_REPO_ROOT = Path(__file__).resolve().parents[2]
_PYPROJECT = _REPO_ROOT / "pyproject.toml"


def _load_config() -> dict[str, Any]:
    """Read ``[tool.churn]`` + ``[tool.churn.training]`` from pyproject.toml."""
    with _PYPROJECT.open("rb") as f:
        data = tomllib.load(f)
    churn_cfg: dict[str, Any] = data["tool"]["churn"]
    return churn_cfg


def _set_global_seed(seed: int) -> None:
    """Seed Python, numpy, and PyTorch — determinism across runs."""
    import random

    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)
    torch.cuda.manual_seed_all(seed)


def _build_dataset(args: argparse.Namespace, cfg: dict[str, Any]) -> tuple[np.ndarray, np.ndarray]:
    """Return ``(features, labels)`` as numpy float32 arrays."""
    now = datetime(2026, 4, 27, tzinfo=UTC)
    if args.data_source == "synthetic":
        ds = generate_dataset(n_customers=args.n_customers, seed=cfg["training"]["random_seed"], now=now)
    elif args.data_source == "postgres":
        # Phase A migration path : read from Postgres via SQLAlchemy.
        # NOT implemented in v1 — flagged in ADR-0061 §"Training data
        # — synthetic for v1". Raises a clear NotImplementedError so a
        # future MR can drop in the loader without changing the API.
        msg = (
            "data-source=postgres not yet implemented (Phase A v1 ships "
            "synthetic only ; see shared ADR-0061 §Training-data for the "
            "migration plan). Re-run with --data-source synthetic."
        )
        raise NotImplementedError(msg)
    else:
        raise ValueError(f"unknown data-source: {args.data_source!r}")

    features = build_features(ds.customers, ds.orders, ds.order_lines, now=now)
    labels = label_churn(
        ds.customers,
        now=now,
        churn_window_days=cfg["churn_window_days"],
        min_active_period_days=cfg["min_active_period_days"],
        min_account_age_days=cfg["min_account_age_days"],
    )
    return features.to_numpy(dtype=np.float32), labels.to_numpy(dtype=np.float32)


def _train_one_epoch(
    model: ChurnMLP,
    loader: DataLoader,
    optimizer: torch.optim.Optimizer,
    criterion: nn.Module,
) -> float:
    """Single epoch training pass — returns mean batch loss."""
    model.train()
    total_loss = 0.0
    for x_batch, y_batch in loader:
        optimizer.zero_grad()
        logits = model(x_batch).squeeze(-1)
        loss = criterion(logits, y_batch)
        loss.backward()
        optimizer.step()
        total_loss += float(loss.item()) * x_batch.size(0)
    return total_loss / len(loader.dataset)  # type: ignore[arg-type]


def _evaluate(
    model: ChurnMLP,
    x_val: torch.Tensor,
    y_val: np.ndarray,
) -> dict[str, float]:
    """Evaluate model on validation set — returns metrics dict."""
    probs = predict_proba(model, x_val).numpy()
    preds_binary = (probs >= 0.5).astype(int)
    tn, fp, fn, tp = confusion_matrix(y_val, preds_binary, labels=[0, 1]).ravel()
    return {
        "auc": float(roc_auc_score(y_val, probs)),
        "average_precision": float(average_precision_score(y_val, probs)),
        "brier": float(brier_score_loss(y_val, probs)),
        "true_negatives": int(tn),
        "false_positives": int(fp),
        "false_negatives": int(fn),
        "true_positives": int(tp),
    }


def _train_with_early_stop(
    model: ChurnMLP,
    x_train: torch.Tensor,
    y_train: torch.Tensor,
    x_val: torch.Tensor,
    y_val: np.ndarray,
    cfg: dict[str, Any],
) -> tuple[ChurnMLP, dict[str, float], int]:
    """Run the full training loop with early stopping on validation AUC.

    Returns the best model state (loaded back into ``model``), the
    final-epoch metrics, and the epoch at which best AUC was hit.
    """
    train_cfg = cfg["training"]
    optimizer = torch.optim.Adam(
        model.parameters(),
        lr=train_cfg["learning_rate"],
        weight_decay=train_cfg["weight_decay"],
    )
    criterion = nn.BCEWithLogitsLoss()

    dataset = TensorDataset(x_train, y_train)
    loader = DataLoader(dataset, batch_size=train_cfg["batch_size"], shuffle=True)

    best_auc = 0.0
    best_state = {k: v.clone() for k, v in model.state_dict().items()}
    best_epoch = 0
    patience_counter = 0
    metrics: dict[str, float] = {}

    for epoch in range(1, train_cfg["max_epochs"] + 1):
        train_loss = _train_one_epoch(model, loader, optimizer, criterion)
        metrics = _evaluate(model, x_val, y_val)
        metrics["train_loss"] = train_loss
        logger.info(
            "epoch %d : train_loss=%.4f val_auc=%.4f val_ap=%.4f val_brier=%.4f",
            epoch,
            train_loss,
            metrics["auc"],
            metrics["average_precision"],
            metrics["brier"],
        )
        if metrics["auc"] > best_auc:
            best_auc = metrics["auc"]
            best_state = {k: v.clone() for k, v in model.state_dict().items()}
            best_epoch = epoch
            patience_counter = 0
        else:
            patience_counter += 1
            if patience_counter >= train_cfg["patience"]:
                logger.info("early stop at epoch %d (best epoch=%d, best_auc=%.4f)", epoch, best_epoch, best_auc)
                break

    model.load_state_dict(best_state)
    metrics = _evaluate(model, x_val, y_val)
    metrics["best_epoch"] = float(best_epoch)
    return model, metrics, best_epoch


def _log_to_mlflow(metrics: dict[str, float], onnx_path: Path, cfg: dict[str, Any]) -> None:
    """Push run params + metrics + ONNX artefact to MLflow.

    No-op (warning) if MLflow is not reachable — local runs without
    a tracking server still produce the .onnx file ; only the
    registry side is skipped.
    """
    try:
        import mlflow
    except ImportError:
        logger.warning("mlflow not installed — skipping registry log. Install via uv sync --extra ml.")
        return

    try:
        with mlflow.start_run(run_name="churn_predictor"):
            mlflow.log_params(
                {
                    "feature_names": list(FEATURE_NAMES),
                    **{f"churn.{k}": v for k, v in cfg.items() if k != "training"},
                    **{f"training.{k}": v for k, v in cfg["training"].items()},
                }
            )
            mlflow.log_metrics({k: v for k, v in metrics.items() if isinstance(v, (int, float))})
            mlflow.log_artifact(str(onnx_path), artifact_path="model")
            mlflow.register_model(
                model_uri=f"runs:/{mlflow.active_run().info.run_id}/model",
                name="ChurnPredictor",
            )
            logger.info("✓ logged run + registered model in MLflow")
    except Exception as exc:
        logger.warning("MLflow logging failed (run continues without registry log) : %s", exc)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Train Customer Churn predictor + export ONNX + log to MLflow.")
    parser.add_argument("--data-source", choices=["synthetic", "postgres"], default="synthetic")
    parser.add_argument("--n-customers", type=int, default=1000)
    parser.add_argument("--output-dir", type=Path, default=Path("./.models"))
    parser.add_argument("--log-level", default="INFO")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=args.log_level,
        format="%(asctime)s [%(levelname)s] %(name)s : %(message)s",
    )

    cfg = _load_config()
    _set_global_seed(cfg["training"]["random_seed"])

    logger.info("Building dataset (%s, n=%d)…", args.data_source, args.n_customers)
    features_np, labels_np = _build_dataset(args, cfg)
    logger.info(
        "Dataset : %d samples, %d features, churn rate=%.2f",
        len(features_np),
        features_np.shape[1],
        labels_np.mean(),
    )

    x_tr_np, x_va_np, y_tr_np, y_va_np = train_test_split(
        features_np,
        labels_np,
        test_size=cfg["training"]["val_split"],
        stratify=labels_np,
        random_state=cfg["training"]["random_seed"],
    )
    scaler = StandardScaler().fit(x_tr_np)
    x_tr = torch.from_numpy(scaler.transform(x_tr_np).astype(np.float32))
    x_va = torch.from_numpy(scaler.transform(x_va_np).astype(np.float32))
    y_tr = torch.from_numpy(y_tr_np)

    model = ChurnMLP()
    logger.info("Training MLP (params=%d)", sum(p.numel() for p in model.parameters()))
    model, metrics, best_epoch = _train_with_early_stop(model, x_tr, y_tr, x_va, y_va_np, cfg)

    auc_gate = cfg["training"]["auc_gate"]
    if metrics["auc"] < auc_gate:
        logger.error("AUC gate failed : %.4f < %.4f (gate)", metrics["auc"], auc_gate)
        return 1

    onnx_path = args.output_dir / "churn_predictor.onnx"
    export_to_onnx(model, output_path=onnx_path)
    logger.info("✓ exported ONNX → %s", onnx_path)

    _log_to_mlflow(metrics, onnx_path, cfg)
    logger.info("✓ training complete : best_epoch=%d AUC=%.4f", best_epoch, metrics["auc"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
