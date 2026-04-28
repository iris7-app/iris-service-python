"""Process-wide :class:`ChurnPredictor` singleton + DI provider.

Loaded once at app startup (see :mod:`iris_service.app`). Tools
that consume it (:mod:`router`, :mod:`mcp_tool`) get the same
instance via :func:`get_churn_predictor` — no re-load per request.

Tests that need a fresh predictor (e.g. with a stub ONNX model)
override the dependency via FastAPI's ``app.dependency_overrides``
just like every other DI provider in this codebase.
"""

from __future__ import annotations

import os
from functools import lru_cache

from iris_service.ml.inference import ChurnPredictor

#: Path env-var (parallels Java's ``mirador.churn.model-path``).
#: Default matches the ConfigMap mount point from shared ADR-0062.
ENV_MODEL_PATH = "IRIS_CHURN_MODEL_PATH"
ENV_MODEL_VERSION = "IRIS_CHURN_MODEL_VERSION"

DEFAULT_MODEL_PATH = "/etc/models/churn_predictor.onnx"
DEFAULT_MODEL_VERSION = "unspecified"


@lru_cache(maxsize=1)
def _build_singleton() -> ChurnPredictor:
    path = os.environ.get(ENV_MODEL_PATH, DEFAULT_MODEL_PATH)
    version = os.environ.get(ENV_MODEL_VERSION, DEFAULT_MODEL_VERSION)
    predictor = ChurnPredictor(model_path=path, model_version=version)
    predictor.load_model()
    return predictor


def get_churn_predictor() -> ChurnPredictor:
    """FastAPI :func:`Depends` provider — returns the process singleton."""
    return _build_singleton()


def reset_churn_predictor() -> None:
    """Drop the cached singleton — used by tests to swap in a stub model."""
    _build_singleton.cache_clear()
