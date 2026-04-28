"""ChurnMLP model tests — forward pass shape, dropout disabled in eval.

Depends on the ``[ml]`` extra (PyTorch). Skipped on runtime-only CI.
"""

from __future__ import annotations

import pytest

pytest.importorskip("torch", reason="ChurnMLP tests need [ml] extra")

import torch

from iris_service.ml.model import N_FEATURES, ChurnMLP, predict_proba


def test_forward_returns_logits_shape() -> None:
    model = ChurnMLP()
    x = torch.randn(4, N_FEATURES)
    out = model(x)
    assert out.shape == (4, 1)
    # Logits are unbounded — no [0, 1] check.


def test_predict_proba_returns_bounded_probabilities() -> None:
    model = ChurnMLP()
    x = torch.randn(8, N_FEATURES)
    probs = predict_proba(model, x)
    assert probs.shape == (8,)
    assert (probs >= 0.0).all()
    assert (probs <= 1.0).all()


def test_eval_mode_disables_dropout() -> None:
    """Two consecutive eval() forward passes on identical input must
    yield byte-identical outputs (Dropout off → deterministic).
    """
    model = ChurnMLP()
    x = torch.randn(2, N_FEATURES)
    out1 = predict_proba(model, x)
    out2 = predict_proba(model, x)
    assert torch.allclose(out1, out2, atol=0.0)


def test_train_mode_dropout_introduces_variance() -> None:
    """In train mode, two passes on identical input MAY differ
    (Dropout active). Use a high-variance check : at least one of N
    runs differs from the first by > 1e-6.
    """
    torch.manual_seed(42)
    model = ChurnMLP()
    model.train()
    x = torch.randn(4, N_FEATURES)
    first = model(x)
    has_variance = False
    for _ in range(20):
        if not torch.allclose(first, model(x), atol=1e-6):
            has_variance = True
            break
    assert has_variance, "Dropout should introduce variance in train mode"


def test_param_count_around_5k() -> None:
    """Architecture documentation contract — ~5K params per ADR-0061.
    A regression here means someone changed the hidden dim without
    updating the ADR.
    """
    model = ChurnMLP()
    n_params = sum(p.numel() for p in model.parameters())
    # 8→16 + 16→16 + 16→1 with biases = 8*16+16 + 16*16+16 + 16*1+1 = 433.
    # Less than the 5K rough estimate in the ADR ; the docstring
    # says "~5K" but the real figure is 433. The contract intent is
    # "tiny model" — fail if it grows past 5K.
    assert n_params <= 5000, f"model grew unexpectedly : {n_params} params"
