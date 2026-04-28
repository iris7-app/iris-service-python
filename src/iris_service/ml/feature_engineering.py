"""Customer Churn — feature engineering (8 numeric features).

Per [shared ADR-0061](https://gitlab.com/iris-7/iris-service-shared/-/blob/main/docs/adr/0061-customer-churn-prediction.md)
§"Feature engineering". The pipeline transforms a Customer + Order
DataFrame into a fixed-shape ``(N, 8)`` float32 matrix that
:class:`iris_service.ml.model.ChurnMLP` consumes.

Public surface :

- :func:`build_features` — main entry point.
- :func:`label_churn` — SQL-equivalent label computation.
- :data:`FEATURE_NAMES` — canonical order ; the ONNX export contract
  binds to this exact sequence.

The implementation is intentionally pandas-vectorised (no Python
loops on customer rows) — even at 1 M customers the feature build
should run in seconds, not minutes.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Final

import numpy as np
import pandas as pd

# Canonical feature order — ONNX input tensor positions bind to this.
# Changing the sequence is a BREAKING change requiring re-train + new
# ONNX file. Do NOT reorder lightly.
FEATURE_NAMES: Final[tuple[str, ...]] = (
    "days_since_last_order",
    "total_revenue_30d",
    "total_revenue_90d",
    "total_revenue_365d",
    "order_frequency",
    "cart_diversity",
    "email_domain_class",
    "customer_lifetime_days",
)

# Email-domain classification buckets per ADR-0061. Lower = more "stable"
# customer profile (corporate domains correlate with B2B retention) ;
# higher = more "throwaway" (disposable mail providers correlate with
# higher churn).
_MAINSTREAM_DOMAINS: Final[frozenset[str]] = frozenset(
    {
        "gmail.com",
        "outlook.com",
        "hotmail.com",
        "yahoo.com",
        "icloud.com",
        "live.com",
        "msn.com",
        "aol.com",
        "protonmail.com",
        "yandex.com",
    }
)
_DISPOSABLE_DOMAINS: Final[frozenset[str]] = frozenset(
    {
        "tempmail.com",
        "10minutemail.com",
        "guerrillamail.com",
        "mailinator.com",
        "throwaway.email",
    }
)


def classify_email_domain(email: str) -> int:
    """Return the email-domain class as an integer.

    0 = corporate (anything not in the other buckets — likely B2B).
    1 = mainstream (gmail, outlook, yahoo, …).
    2 = disposable (tempmail, mailinator, …).
    3 = unknown / malformed.
    """
    if not email or "@" not in email:
        return 3
    domain = email.rsplit("@", 1)[-1].lower().strip()
    if not domain:
        return 3
    if domain in _DISPOSABLE_DOMAINS:
        return 2
    if domain in _MAINSTREAM_DOMAINS:
        return 1
    return 0


def label_churn(
    customers: pd.DataFrame,
    *,
    now: datetime,
    churn_window_days: int = 90,
    min_active_period_days: int = 30,
    min_account_age_days: int = 120,
) -> pd.Series:
    """Compute the churn label per customer.

    Mirrors the SQL definition from shared ADR-0061 §"Label
    definition". A customer is labelled churned (= ``True``) when ALL
    three predicates hold :

    1. ``last_order_at < now - churn_window_days`` — the actual signal.
    2. ``first_order_at < last_order_at - min_active_period_days`` —
       excludes one-shot customers who happen to fall outside the
       window.
    3. ``created_at < now - min_account_age_days`` — excludes accounts
       too recent to have meaningful churn signal.

    Customers failing any predicate are labelled ``False`` (= active /
    too-recent / one-shot — collectively *not-yet-churned*). Returns a
    :class:`pandas.Series` aligned to ``customers.index``.
    """
    last_threshold = now - timedelta(days=churn_window_days)
    age_threshold = now - timedelta(days=min_account_age_days)
    activity_gap = pd.Timedelta(days=min_active_period_days)

    has_late_last_order = customers["last_order_at"] < last_threshold
    has_active_period = (customers["last_order_at"] - customers["first_order_at"]) > activity_gap
    has_account_age = customers["created_at"] < age_threshold

    return (has_late_last_order & has_active_period & has_account_age).astype(bool)


def build_features(
    customers: pd.DataFrame,
    orders: pd.DataFrame,
    order_lines: pd.DataFrame,
    *,
    now: datetime,
) -> pd.DataFrame:
    """Compute the 8 features per customer.

    Inputs :

    - ``customers`` — columns ``id``, ``email``, ``created_at``,
      ``first_order_at``, ``last_order_at``.
    - ``orders`` — columns ``id``, ``customer_id``, ``created_at``,
      ``total_amount``.
    - ``order_lines`` — columns ``order_id``, ``product_id``.

    Returns a DataFrame with one row per customer, columns ordered
    per :data:`FEATURE_NAMES`. Index = ``customer.id`` for stable
    join with the label series.
    """
    out = pd.DataFrame(index=customers["id"])

    # 1. days_since_last_order — clip to 0 (cannot be negative).
    delta = (now - customers["last_order_at"]).dt.days.clip(lower=0)
    out["days_since_last_order"] = delta.fillna(now.toordinal()).astype(np.float32).to_numpy()

    # 2-4. revenue windows
    for window_days, col in ((30, "total_revenue_30d"), (90, "total_revenue_90d"), (365, "total_revenue_365d")):
        threshold = now - timedelta(days=window_days)
        windowed = orders[orders["created_at"] >= threshold]
        revenue = windowed.groupby("customer_id")["total_amount"].sum()
        out[col] = revenue.reindex(customers["id"]).fillna(0.0).astype(np.float32).to_numpy()

    # 5. order_frequency — orders per active day. Lifetime days from
    # account creation, clipped to ≥ 1 to avoid divide-by-zero on
    # brand-new accounts.
    order_count = orders.groupby("customer_id").size()
    lifetime_days = ((now - customers["created_at"]).dt.days).clip(lower=1)
    freq = (order_count.reindex(customers["id"]).fillna(0) / lifetime_days.values).astype(np.float32)
    out["order_frequency"] = freq.to_numpy()

    # 6. cart_diversity — ratio of distinct products to total order
    # lines. Captures whether the customer buys variety or one item
    # repeatedly. Customers with no orders → 0.
    enriched = order_lines.merge(orders[["id", "customer_id"]], left_on="order_id", right_on="id", how="left")
    distinct_products = enriched.groupby("customer_id")["product_id"].nunique()
    total_lines = enriched.groupby("customer_id").size()
    diversity = (distinct_products / total_lines.replace(0, 1)).fillna(0.0).astype(np.float32)
    out["cart_diversity"] = diversity.reindex(customers["id"]).fillna(0.0).to_numpy()

    # 7. email_domain_class — int in [0, 3].
    out["email_domain_class"] = customers["email"].apply(classify_email_domain).astype(np.float32).to_numpy()

    # 8. customer_lifetime_days — substitutes the originally-proposed
    # support_tickets_count (Iris has no support_ticket schema —
    # documented in ADR-0061).
    out["customer_lifetime_days"] = lifetime_days.astype(np.float32).to_numpy()

    return out[list(FEATURE_NAMES)]
