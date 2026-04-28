"""ONNX export round-trip tests — proves cross-language inference parity.

Per [shared ADR-0060](https://gitlab.com/iris-7/iris-service-shared/-/blob/main/docs/adr/0060-onnx-cross-language-ml-inference.md)
§"Verification protocol" : the ONNX runtime running the same .onnx
file must yield identical predictions to PyTorch eager mode (≤ 1e-6
floating-point tolerance).

This is the core invariant that makes Phase B (Java inference) and
Phase C (Python inference) work without re-validation per language.
"""

from __future__ import annotations

import pytest

pytest.importorskip("torch", reason="ONNX export round-trip needs [ml] extra (torch)")

import tempfile
from pathlib import Path

import numpy as np
import onnxruntime as ort  # type: ignore[import-untyped]
import torch

from iris_service.ml.model import N_FEATURES, ChurnMLP, export_to_onnx


def test_onnx_export_creates_valid_file() -> None:
    """Exported .onnx file is non-empty and parseable by onnxruntime."""
    model = ChurnMLP()
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "churn.onnx"
        export_to_onnx(model, output_path=path)
        assert path.exists()
        assert path.stat().st_size > 100  # non-empty
        # Successful session creation = ONNX file is valid.
        session = ort.InferenceSession(str(path))
        assert "input" in {i.name for i in session.get_inputs()}
        assert "logits" in {o.name for o in session.get_outputs()}


def test_onnx_runtime_matches_pytorch_eager() -> None:
    """Same inputs → same logits, byte-for-byte (modulo 1e-6 fp diff)."""
    torch.manual_seed(42)
    model = ChurnMLP()
    model.eval()

    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "churn.onnx"
        export_to_onnx(model, output_path=path)
        session = ort.InferenceSession(str(path))

        # 100 random batches of varying size — covers the dynamic batch axis.
        rng = np.random.default_rng(seed=0)
        for batch_size in (1, 4, 16, 64):
            x_np = rng.standard_normal((batch_size, N_FEATURES)).astype(np.float32)
            x_torch = torch.from_numpy(x_np)

            with torch.no_grad():
                eager_out = model(x_torch).numpy()

            (onnx_out,) = session.run(None, {"input": x_np})

            np.testing.assert_allclose(eager_out, onnx_out, atol=1e-6, rtol=1e-6)


def test_onnx_supports_dynamic_batch() -> None:
    """The dynamic batch axis lets a single .onnx serve any batch size."""
    model = ChurnMLP()
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "churn.onnx"
        export_to_onnx(model, output_path=path)
        session = ort.InferenceSession(str(path))

        for batch_size in (1, 7, 100, 1000):
            x = np.random.RandomState(0).randn(batch_size, N_FEATURES).astype(np.float32)
            (out,) = session.run(None, {"input": x})
            assert out.shape == (batch_size, 1)
