"""Hypothesis property tests for Product invariants from shared ADR-0059.

Mirrors `iris-service-java`'s `ProductInvariantsPropertyTest.java` :
each test captures one invariant on the `ProductCreate` DTO (which
runs the same validation as the Java `CreateProductRequest` Bean
Validation annotations).

ADR-0059 reference :
https://gitlab.com/iris-7/iris-service-shared/-/blob/main/docs/adr/0059-customer-order-product-data-model.md
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from hypothesis import given
from hypothesis import strategies as st
from pydantic import ValidationError

from iris_service.product.dtos import ProductCreate


# ── stock_quantity ≥ 0 ─────────────────────────────────────────────────────────


@given(st.integers(min_value=0, max_value=1_000_000))
def test_positive_or_zero_stock_is_accepted(stock: int) -> None:
    """ADR-0059 invariant : `stock_quantity ≥ 0` (Field(ge=0)).

    Any non-negative integer must validate. Counterpart of Java's
    `positiveOrZeroStock_isAccepted`.
    """
    dto = ProductCreate(
        name="Probe",
        unit_price=Decimal("9.99"),
        stock_quantity=stock,
    )
    assert dto.stock_quantity == stock


@given(st.integers(min_value=-1_000_000, max_value=-1))
def test_negative_stock_is_rejected(stock: int) -> None:
    """ADR-0059 invariant : negative stock is rejected at validation time.

    Counterpart of Java's `negativeStock_isRejected`.
    """
    with pytest.raises(ValidationError):
        ProductCreate(
            name="Probe",
            unit_price=Decimal("9.99"),
            stock_quantity=stock,
        )


def test_zero_stock_is_explicitly_allowed() -> None:
    """Boundary case : exactly 0 stock must validate (out-of-stock ≠ invalid).

    Counterpart of Java's `zeroStock_isExplicitlyAllowed` — the boundary
    matters because `>` would have rejected 0 ; `>=` (Field(ge=0)) accepts.
    """
    dto = ProductCreate(name="Probe", unit_price=Decimal("9.99"), stock_quantity=0)
    assert dto.stock_quantity == 0


# ── unit_price ≥ 0 ────────────────────────────────────────────────────────────


@given(
    st.decimals(
        min_value=Decimal("0"),
        max_value=Decimal("9999999999.99"),
        allow_nan=False,
        allow_infinity=False,
        places=2,
    )
)
def test_non_negative_unit_price_is_accepted(price: Decimal) -> None:
    """ADR-0059 invariant : `unit_price ≥ 0` (Field(ge=0)).

    Free items + paid items both validate ; only negative is rejected.
    Counterpart of Java's `nonNegativeUnitPrice_isAccepted`.
    """
    dto = ProductCreate(
        name="Probe",
        unit_price=price,
        stock_quantity=1,
    )
    assert dto.unit_price == price


@given(
    st.decimals(
        min_value=Decimal("-9999999999.99"),
        max_value=Decimal("-0.01"),
        allow_nan=False,
        allow_infinity=False,
        places=2,
    )
)
def test_negative_unit_price_is_rejected(price: Decimal) -> None:
    """ADR-0059 invariant : negative price is rejected at validation time.

    Counterpart of Java's `negativeUnitPrice_isRejected`.
    """
    with pytest.raises(ValidationError):
        ProductCreate(
            name="Probe",
            unit_price=price,
            stock_quantity=1,
        )


# ── name length boundary ──────────────────────────────────────────────────────


@given(st.text(min_size=1, max_size=255))
def test_name_within_length_bounds_is_accepted(name: str) -> None:
    """name : str(min_length=1, max_length=255). Empty + 256+ rejected."""
    dto = ProductCreate(name=name, unit_price=Decimal("0"), stock_quantity=0)
    assert dto.name == name


def test_empty_name_is_rejected() -> None:
    """Empty name violates min_length=1."""
    with pytest.raises(ValidationError):
        ProductCreate(name="", unit_price=Decimal("0"), stock_quantity=0)


@given(st.text(min_size=256, max_size=512))
def test_overlong_name_is_rejected(name: str) -> None:
    """Name beyond 255 chars violates max_length."""
    with pytest.raises(ValidationError):
        ProductCreate(name=name, unit_price=Decimal("0"), stock_quantity=0)
