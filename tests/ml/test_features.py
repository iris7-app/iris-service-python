"""Feature engineering tests — determinism + edge cases.

These exercise the TRAINING-side feature pipeline (pandas-vectorised),
which depends on the ``[ml]`` extra. Runtime serving tests in
``test_inference.py`` use the lightweight per-customer extractor and
run unconditionally.
"""

from __future__ import annotations

import pytest

pytest.importorskip("pandas", reason="training-side feature tests need [ml] extra")

from datetime import UTC, datetime, timedelta

import pandas as pd

from iris_service.ml.feature_engineering import (
    FEATURE_NAMES,
    build_features,
    classify_email_domain,
    label_churn,
)

_NOW = datetime(2026, 4, 27, tzinfo=UTC)


def _customer_row(cid: int, email: str, age_days: int, first_offset: int, last_offset: int) -> dict[str, object]:
    return {
        "id": cid,
        "name": f"User {cid}",
        "email": email,
        "created_at": _NOW - timedelta(days=age_days),
        "first_order_at": _NOW - timedelta(days=first_offset),
        "last_order_at": _NOW - timedelta(days=last_offset),
    }


def test_email_domain_classifier_buckets() -> None:
    assert classify_email_domain("alice@gmail.com") == 1
    assert classify_email_domain("alice@TEMPMAIL.com") == 2  # case-insensitive
    assert classify_email_domain("alice@acme-corp.example") == 0
    assert classify_email_domain("") == 3
    assert classify_email_domain("not-an-email") == 3


def test_label_churn_matches_sql_definition() -> None:
    customers = pd.DataFrame(
        [
            _customer_row(1, "a@x.com", age_days=200, first_offset=180, last_offset=100),  # churned
            _customer_row(2, "b@x.com", age_days=200, first_offset=180, last_offset=10),  # active
            _customer_row(3, "c@x.com", age_days=50, first_offset=40, last_offset=100),  # too young
            _customer_row(4, "d@x.com", age_days=200, first_offset=100, last_offset=95),  # one-shot
        ]
    )
    labels = label_churn(customers, now=_NOW)
    assert labels.tolist() == [True, False, False, False]


def test_build_features_shape_and_order() -> None:
    customers = pd.DataFrame([_customer_row(1, "a@gmail.com", age_days=200, first_offset=180, last_offset=10)])
    orders = pd.DataFrame(
        [
            {"id": 100, "customer_id": 1, "created_at": _NOW - timedelta(days=10), "total_amount": 50.0},
            {"id": 101, "customer_id": 1, "created_at": _NOW - timedelta(days=60), "total_amount": 30.0},
        ]
    )
    order_lines = pd.DataFrame(
        [
            {"id": 1, "order_id": 100, "product_id": 1, "quantity": 2, "unit_price_at_order": 25.0},
            {"id": 2, "order_id": 100, "product_id": 2, "quantity": 1, "unit_price_at_order": 25.0},
            {"id": 3, "order_id": 101, "product_id": 1, "quantity": 1, "unit_price_at_order": 30.0},
        ]
    )

    feats = build_features(customers, orders, order_lines, now=_NOW)
    assert list(feats.columns) == list(FEATURE_NAMES)
    row = feats.iloc[0]
    assert row["days_since_last_order"] == 10
    assert row["total_revenue_30d"] == 50.0
    assert row["total_revenue_90d"] == 80.0
    assert row["email_domain_class"] == 1  # gmail = mainstream


def test_build_features_handles_zero_orders() -> None:
    """Customers with no orders should yield non-NaN feature rows."""
    customers = pd.DataFrame([_customer_row(1, "a@gmail.com", age_days=50, first_offset=0, last_offset=0)])
    orders = pd.DataFrame(columns=["id", "customer_id", "created_at", "total_amount"])
    order_lines = pd.DataFrame(columns=["id", "order_id", "product_id", "quantity", "unit_price_at_order"])

    feats = build_features(customers, orders, order_lines, now=_NOW)
    assert not feats.isna().any().any()
    assert feats["total_revenue_30d"].iloc[0] == 0.0
    assert feats["order_frequency"].iloc[0] == 0.0
    assert feats["cart_diversity"].iloc[0] == 0.0


def test_build_features_is_deterministic_given_same_inputs() -> None:
    """Running build_features twice on the same inputs returns identical rows."""
    customers = pd.DataFrame([_customer_row(1, "a@x.com", age_days=200, first_offset=180, last_offset=10)])
    orders = pd.DataFrame(
        [
            {"id": 100, "customer_id": 1, "created_at": _NOW - timedelta(days=5), "total_amount": 10.0},
        ]
    )
    order_lines = pd.DataFrame(
        [
            {"id": 1, "order_id": 100, "product_id": 1, "quantity": 1, "unit_price_at_order": 10.0},
        ]
    )

    f1 = build_features(customers, orders, order_lines, now=_NOW)
    f2 = build_features(customers, orders, order_lines, now=_NOW)
    pd.testing.assert_frame_equal(f1, f2)
