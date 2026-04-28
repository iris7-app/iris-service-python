"""Synthetic training data for Customer Churn — Faker-based v1.

Per [shared ADR-0061](https://gitlab.com/iris-7/iris-service-shared/-/blob/main/docs/adr/0061-customer-churn-prediction.md)
§"Training data — synthetic for v1". Generates a deterministic
1000-customer / 10K-order dataset with ~20 % churn rate at the
default `now()` reference point, suitable for training the v1
model.

Determinism is critical for reproducibility — both the Faker
provider and the numpy random state are seeded from a single
``random_seed`` argument. Running twice with the same seed yields
byte-identical output.

Usage :

    python -m iris_service.ml.seed_demo_data --output ./training_data.parquet

OR programmatically :

    from iris_service.ml.seed_demo_data import generate_dataset
    customers, orders, lines = generate_dataset(n_customers=1000, seed=42)

The "production migration" path (real Postgres data) is documented
in ADR-0061 §"Phase A scope" — this script's only purpose is to
unblock training before historical labels exist.
"""

from __future__ import annotations

import argparse
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import NamedTuple

import numpy as np
import pandas as pd
from faker import Faker

# At seed=42 with the parameters below, the SQL label from
# iris_service.ml.feature_engineering.label_churn() yields ~22 % churned at
# now=2026-04-27. The number drifts ±2 % depending on the
# `now` reference, but the seed pins everything else.
_CHURN_TARGET_RATE: float = 0.20

# Time horizon : oldest customer = ~2y old, max order age = ~1y.
_MAX_CUSTOMER_AGE_DAYS: int = 730
_MAX_ORDER_AGE_DAYS: int = 365

# Order statistics — log-normal distribution skewed towards small
# carts but with a long tail (some big spenders).
_MEAN_ORDERS_PER_CUSTOMER: float = 10.0
_MEAN_LINES_PER_ORDER: float = 2.0


class SyntheticDataset(NamedTuple):
    """Returned bundle from :func:`generate_dataset`."""

    customers: pd.DataFrame
    orders: pd.DataFrame
    order_lines: pd.DataFrame


def generate_dataset(
    *,
    n_customers: int = 1000,
    n_products: int = 50,
    seed: int = 42,
    now: datetime | None = None,
    churn_rate: float = _CHURN_TARGET_RATE,
) -> SyntheticDataset:
    """Generate the 3-table synthetic dataset deterministically.

    A fraction ``churn_rate`` of customers is forced into the
    "churned" pattern (no order in the last 90 days, but had ≥ 1
    order > 30 days before that). The remaining (1 - churn_rate) is
    "active" with at least one recent order.

    The reference time ``now`` defaults to ``datetime(2026, 4, 27,
    tzinfo=UTC)`` for total reproducibility — pinning the date keeps
    the dataset stable across CI runs.
    """
    if now is None:
        now = datetime(2026, 4, 27, tzinfo=UTC)

    rng = np.random.default_rng(seed)
    fake = Faker()
    Faker.seed(seed)

    n_churned = round(n_customers * churn_rate)
    n_active = n_customers - n_churned

    # Account ages — uniform from 120 days (min for label) to MAX.
    churned_ages = rng.integers(120, _MAX_CUSTOMER_AGE_DAYS, size=n_churned)
    active_ages = rng.integers(0, _MAX_CUSTOMER_AGE_DAYS, size=n_active)
    ages = np.concatenate([churned_ages, active_ages])

    # Customer rows.
    customer_records: list[dict[str, object]] = []
    for cid in range(1, n_customers + 1):
        age_days = int(ages[cid - 1])
        created_at = now - timedelta(days=age_days)
        is_churned_target = cid <= n_churned

        # Name + email via Faker (deterministic given seed).
        name = fake.name()
        email = _domain_skewed_email(fake, rng, is_churned_target)
        customer_records.append(
            {
                "id": cid,
                "name": name,
                "email": email,
                "created_at": created_at,
            }
        )

    # Orders — for each customer, decide :
    #  - if churned : last_order between 91 and (account_age - 30) days ago
    #  - if active  : at least one recent order in the last 30 days
    order_records: list[dict[str, object]] = []
    next_order_id = 1
    for cust in customer_records:
        cid = cust["id"]
        is_churned_target = cid <= n_churned
        age_days = (now - cust["created_at"]).days
        n_orders = max(1, int(rng.poisson(_MEAN_ORDERS_PER_CUSTOMER)))

        if is_churned_target:
            # Spread orders between (age_days - 1) and 91 days ago.
            # last_order at least 91 days ago.
            window_max = age_days - 1
            window_min = max(91, window_max - n_orders * 5)
            if window_min >= window_max:
                window_min = 91
            order_ages = rng.integers(window_min, window_max + 1, size=n_orders)
        else:
            # At least one recent order. Spread other orders across
            # the customer's active life.
            order_ages = rng.integers(0, age_days + 1, size=n_orders)
            # Force the most recent order to be ≤ 30 days ago.
            order_ages[order_ages.argmin()] = int(rng.integers(0, 31))

        for age in sorted(order_ages, reverse=True):
            created_at = now - timedelta(days=int(age))
            n_lines = max(1, int(rng.poisson(_MEAN_LINES_PER_ORDER)))
            total_amount = round(rng.lognormal(mean=3.0, sigma=0.6), 2)
            order_records.append(
                {
                    "id": next_order_id,
                    "customer_id": cid,
                    "created_at": created_at,
                    "total_amount": total_amount,
                    "_n_lines": n_lines,
                }
            )
            next_order_id += 1

    # OrderLine rows.
    line_records: list[dict[str, object]] = []
    next_line_id = 1
    for order in order_records:
        for _ in range(order["_n_lines"]):  # type: ignore[arg-type]
            line_records.append(
                {
                    "id": next_line_id,
                    "order_id": order["id"],
                    "product_id": int(rng.integers(1, n_products + 1)),
                    "quantity": int(rng.integers(1, 5)),
                    "unit_price_at_order": round(rng.lognormal(mean=2.5, sigma=0.5), 2),
                }
            )
            next_line_id += 1

    customers = pd.DataFrame(customer_records)
    orders = pd.DataFrame([{k: v for k, v in r.items() if not k.startswith("_")} for r in order_records])
    order_lines = pd.DataFrame(line_records)

    # Annotate customer rows with first/last order timestamps so the
    # caller can compute the label without re-aggregating.
    agg = (
        orders.groupby("customer_id")["created_at"]
        .agg(["min", "max"])
        .rename(
            columns={"min": "first_order_at", "max": "last_order_at"},
        )
    )
    customers = customers.merge(agg, left_on="id", right_index=True, how="left")

    return SyntheticDataset(customers=customers, orders=orders, order_lines=order_lines)


def _domain_skewed_email(fake: Faker, rng: np.random.Generator, is_churned: bool) -> str:
    """Generate an email with churn-correlated domain distribution.

    Realistic-feeling but biased : churned customers are more likely
    to use disposable / mainstream domains, active customers more
    likely to use corporate domains. The bias is mild (60/40 split)
    so the model learns a real but imperfect signal.
    """
    user = fake.user_name()
    if is_churned:
        domain = rng.choice(
            ["gmail.com", "yahoo.com", "tempmail.com", "outlook.com", "company.com"],
            p=[0.30, 0.20, 0.10, 0.20, 0.20],
        )
    else:
        domain = rng.choice(
            ["gmail.com", "outlook.com", "company.com", "acme.io", "globex.example"],
            p=[0.20, 0.15, 0.30, 0.20, 0.15],
        )
    return f"{user}@{domain}"


def _main() -> None:
    parser = argparse.ArgumentParser(description="Generate synthetic Customer + Order + OrderLine training data.")
    parser.add_argument("--output", type=Path, default=Path("./training_data.parquet"))
    parser.add_argument("--n-customers", type=int, default=1000)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    ds = generate_dataset(n_customers=args.n_customers, seed=args.seed)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    customers_path = args.output.with_suffix(".customers.parquet")
    orders_path = args.output.with_suffix(".orders.parquet")
    lines_path = args.output.with_suffix(".lines.parquet")
    ds.customers.to_parquet(customers_path, index=False)
    ds.orders.to_parquet(orders_path, index=False)
    ds.order_lines.to_parquet(lines_path, index=False)

    print(f"✓ {len(ds.customers)} customers → {customers_path}")
    print(f"✓ {len(ds.orders)} orders   → {orders_path}")
    print(f"✓ {len(ds.order_lines)} lines    → {lines_path}")


if __name__ == "__main__":
    _main()
