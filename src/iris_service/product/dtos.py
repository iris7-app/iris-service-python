"""Pydantic DTOs for the Product API.

Mirrors Java's :
- `CreateProductRequest` → `ProductCreate`
- `ProductDto` → `ProductResponse`
- `Page<ProductDto>` → `ProductPage`

Pydantic v2 validation = Bean Validation. Decimal (not float) preserves
precision matching the DB NUMERIC(12,2) + Java BigDecimal.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field

from iris_service.product.models import Product

# ── Request DTOs ──────────────────────────────────────────────────────────────

NameField = Annotated[str, Field(min_length=1, max_length=255)]
DescriptionField = Annotated[str | None, Field(default=None, max_length=10_000)]
PriceField = Annotated[Decimal, Field(ge=0, max_digits=12, decimal_places=2)]
StockField = Annotated[int, Field(ge=0)]


class ProductCreate(BaseModel):
    """`POST /products` body. All invariants validated at the edge."""

    name: NameField
    description: DescriptionField = None
    unit_price: PriceField
    stock_quantity: StockField


# ── Response DTOs ─────────────────────────────────────────────────────────────


class ProductResponse(BaseModel):
    """Outbound representation. Never serialize the ORM entity directly."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    description: str | None
    unit_price: Decimal
    stock_quantity: int
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_orm_entity(cls, p: Product) -> ProductResponse:
        """Explicit converter (avoids Pydantic's `.from_orm` quirks)."""
        return cls(
            id=p.id,
            name=p.name,
            description=p.description,
            unit_price=p.unit_price,
            stock_quantity=p.stock_quantity,
            created_at=p.created_at,
            updated_at=p.updated_at,
        )


class ProductPage(BaseModel):
    """Paginated list response — mirrors Java's `Page<ProductDto>`."""

    items: list[ProductResponse]
    total: int
    page: int
    size: int
