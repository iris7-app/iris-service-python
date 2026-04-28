"""Single-customer feature extraction + ONNX inference for churn.

Mirrors the Java pair :class:`org.iris.ml.ChurnFeatureExtractor`
+ :class:`org.iris.ml.ChurnPredictor`. Same 8 features, same
canonical order, same graceful-degradation contract — boots without
the model, returns 503 until the ConfigMap (per shared ADR-0062)
provides ``/etc/models/churn_predictor.onnx``.

Why a separate module from :mod:`feature_engineering` :

The training-time module operates on pandas DataFrames (vectorised
across millions of customers). At inference time we have ONE
customer's rows already loaded as ORM entities ; vectorisation buys
nothing and pandas is a heavy import. Plain Python computes the
8 floats in microseconds — and the reduced surface makes the
cross-language smoke test (Phase G) easier to keep aligned with
Java.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Final

import numpy as np

from iris_service.customer.models import Customer
from iris_service.ml.feature_engineering import (
    FEATURE_NAMES,
    classify_email_domain,
)
from iris_service.order.models import Order
from iris_service.order.order_line_models import OrderLine

logger = logging.getLogger(__name__)

#: Total feature count — bound to the ONNX input tensor shape
#: (per shared ADR-0060 §"ONNX export contract"). Identical to Java
#: :data:`ChurnFeatureExtractor.N_FEATURES`.
N_FEATURES: Final[int] = 8


def extract_features(
    customer: Customer,
    orders: Iterable[Order],
    order_lines: Iterable[OrderLine],
    *,
    now: datetime | None = None,
) -> np.ndarray:
    """Build the 8-feature vector for one customer.

    Returns a ``(8,) float32`` :class:`numpy.ndarray` in the
    :data:`feature_engineering.FEATURE_NAMES` order. Identical
    semantics to Java's :meth:`ChurnFeatureExtractor.extract`.

    Robust to mixed timezone awareness on the input rows : SQLite
    stores DateTime as naive (no tzinfo) while Postgres returns
    aware datetimes. We normalise everything to UTC before the
    arithmetic so the test env (SQLite) and prod (Postgres) produce
    the same feature vector — defensive, NOT a silent timezone
    re-interpretation. The DateTime column on the SQLAlchemy side
    is declared ``DateTime(timezone=True)`` so the convention is
    "always UTC", even if the DB driver elides the marker.
    """
    if now is None:
        now = datetime.now(UTC)

    orders_list = list(orders)
    lines_list = list(order_lines)

    customer_created_at = _ensure_utc(customer.created_at)
    lifetime_days = max(1, (now - customer_created_at).days)

    last_order_at = max(
        (_ensure_utc(o.created_at) for o in orders_list if o.created_at is not None),
        default=customer_created_at,
    )
    days_since_last = max(0, (now - last_order_at).days)

    rev30 = _sum_revenue_within(orders_list, now, timedelta(days=30))
    rev90 = _sum_revenue_within(orders_list, now, timedelta(days=90))
    rev365 = _sum_revenue_within(orders_list, now, timedelta(days=365))

    frequency = len(orders_list) / lifetime_days
    diversity = _compute_diversity(lines_list)
    domain_class = classify_email_domain(customer.email or "")

    features = np.array(
        [
            float(days_since_last),
            float(rev30),
            float(rev90),
            float(rev365),
            float(frequency),
            float(diversity),
            float(domain_class),
            float(lifetime_days),
        ],
        dtype=np.float32,
    )
    if features.shape[0] != N_FEATURES:
        msg = f"feature shape mismatch — expected ({N_FEATURES},) got {features.shape} ; FEATURE_NAMES={FEATURE_NAMES}"
        raise RuntimeError(msg)
    return features


def _ensure_utc(value: datetime) -> datetime:
    """Treat naive datetimes as UTC ; pass aware ones through.

    SQLAlchemy's ``DateTime(timezone=True)`` stores UTC but SQLite's
    driver returns naive datetimes (the timezone is dropped on the
    way out). Postgres returns proper aware datetimes. Normalising
    here keeps the inference path identical regardless of backend.
    """
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value


def _sum_revenue_within(
    orders: list[Order],
    now: datetime,
    window: timedelta,
) -> float:
    """Sum of ``order.total_amount`` for orders within ``[now - window, now]``."""
    threshold = now - window
    total = Decimal("0")
    for order in orders:
        if order.created_at is None:
            continue
        created = _ensure_utc(order.created_at)
        if created < threshold:
            continue
        amount = order.total_amount
        if amount is None:
            continue
        total += amount if isinstance(amount, Decimal) else Decimal(str(amount))
    return float(total)


def _compute_diversity(lines: list[OrderLine]) -> float:
    """Distinct products / total lines — captures variety vs repetition."""
    if not lines:
        return 0.0
    distinct = {line.product_id for line in lines}
    total = len(lines)
    return len(distinct) / total if total > 0 else 0.0


class ChurnPredictor:
    """ONNX-runtime wrapper around the trained churn model.

    Loaded eagerly at :meth:`load_model` (called from
    :func:`iris_service.app.create_app`). Graceful-degradation
    contract identical to Java's :class:`ChurnPredictor` :

    - File missing → :meth:`is_ready` returns ``False`` ; REST
      endpoint serves 503 ; MCP tool returns
      :class:`ChurnServiceUnavailable`.
    - File present but malformed → log + treat as missing (the
      jar must boot regardless ; ONNX corruption shouldn't take
      the whole stack down).

    Sigmoid is applied IN CODE (the ONNX graph exports raw logits,
    per shared ADR-0061 §"ONNX export contract"). Keeping the export
    sigmoid-free lets us swap calibration logic without re-export.
    """

    def __init__(self, model_path: str, model_version: str = "unspecified") -> None:
        self._model_path = model_path
        self._model_version = model_version
        self._session: object | None = None  # ort.InferenceSession lazily set
        self._input_name: str | None = None

    @property
    def model_version(self) -> str:
        return self._model_version

    @property
    def model_path(self) -> str:
        return self._model_path

    def is_ready(self) -> bool:
        """``True`` iff the ONNX session is loaded and ready to predict."""
        return self._session is not None

    def load_model(self) -> None:
        """Load the ONNX model from :attr:`model_path` if it exists.

        No-op when the file is missing ; logs a single warning so the
        operator sees why predictions return 503. Safe to call
        multiple times — re-loading replaces the existing session
        (used by the future hot-reload watcher in Phase F).
        """
        path = Path(self._model_path)
        if not path.is_file():
            logger.warning(
                "churn_model_not_loaded path=%s — predictions will return 503 "
                "until the model is provisioned via the iris-churn-model "
                "ConfigMap (shared ADR-0062)",
                self._model_path,
            )
            self._session = None
            self._input_name = None
            return
        try:
            import onnxruntime as ort  # heavy import — defer to load time
        except ImportError:
            logger.warning(
                "onnxruntime_missing — install the runtime extra "
                "(`uv sync` ships it as a regular dep). Predictions disabled."
            )
            self._session = None
            self._input_name = None
            return
        try:
            session = ort.InferenceSession(self._model_path, providers=["CPUExecutionProvider"])
        except Exception as exc:
            logger.warning(
                "churn_model_load_failed path=%s reason=%s — graceful degradation, REST endpoint will serve 503",
                self._model_path,
                exc,
            )
            self._session = None
            self._input_name = None
            return
        self._session = session
        self._input_name = session.get_inputs()[0].name
        logger.info(
            "churn_model_loaded path=%s version=%s input=%s",
            self._model_path,
            self._model_version,
            self._input_name,
        )

    def predict_probability(self, features: np.ndarray) -> float:
        """Run inference + sigmoid → probability ∈ [0, 1].

        Raises :exc:`RuntimeError` if the model isn't loaded (callers
        MUST gate on :meth:`is_ready` first ; the REST + MCP wrappers
        do this).
        """
        if self._session is None or self._input_name is None:
            msg = f"churn model not loaded (path={self._model_path}) — call is_ready() before predict_probability()"
            raise RuntimeError(msg)
        if features.shape != (N_FEATURES,):
            msg = f"feature shape mismatch — expected ({N_FEATURES},) got {features.shape}"
            raise ValueError(msg)
        batch = features.reshape(1, N_FEATURES).astype(np.float32)
        # mypy: onnxruntime is typed in stubs but we accept the dynamic
        # session.run signature — outputs[0] is the raw logits tensor.
        outputs = self._session.run(None, {self._input_name: batch})  # type: ignore[attr-defined]
        logit = float(outputs[0][0][0])
        return _sigmoid(logit)


def _sigmoid(logit: float) -> float:
    """Numerically-stable scalar sigmoid — same as Java's implementation."""
    if logit >= 0:
        return float(1.0 / (1.0 + np.exp(-logit)))
    exp_logit = float(np.exp(logit))
    return exp_logit / (1.0 + exp_logit)
