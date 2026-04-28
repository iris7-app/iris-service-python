"""Pydantic DTOs for the Customer API.

Mirrors Java's :
- `CreateCustomerRequest` → CustomerCreate (name + email, validated)
- `PatchCustomerRequest` → CustomerPatch (all fields optional)
- `CustomerDto` (v1) → CustomerResponse
- `CustomerDtoV2` (v2, includes createdAt) → CustomerResponseV2
- `Page<T>` → CustomerPage (paginated wrapper)

Pydantic v2 provides validation (= Bean Validation), serialization
(= Jackson), and auto-OpenAPI schema generation in one library.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, ConfigDict, EmailStr, Field

# ── Request DTOs ──────────────────────────────────────────────────────────────

# Constraint : name 2-120 chars. (strip_whitespace removed — Pydantic v2 deprecated
# inline string constraints on Field, use StringConstraints below if needed.)
NameField = Annotated[str, Field(min_length=2, max_length=120)]


class CustomerCreate(BaseModel):
    """POST /customers body."""

    model_config = ConfigDict(extra="forbid")  # reject unknown fields

    name: NameField
    email: EmailStr


class CustomerPatch(BaseModel):
    """PATCH /customers/{id} body — all fields optional."""

    model_config = ConfigDict(extra="forbid")

    name: NameField | None = None
    email: EmailStr | None = None


# ── Response DTOs ─────────────────────────────────────────────────────────────


class CustomerResponse(BaseModel):
    """v1 response shape — CustomerDto in Java."""

    model_config = ConfigDict(from_attributes=True)  # allows ORM → DTO mapping

    id: int
    name: str
    email: EmailStr


class CustomerResponseV2(CustomerResponse):
    """v2 response shape — adds createdAt (= CustomerDtoV2 in Java)."""

    created_at: datetime = Field(serialization_alias="createdAt")


# ── Pagination ────────────────────────────────────────────────────────────────


class CustomerPage(BaseModel):
    """Paginated customer response (v1 shape) — mirrors Spring's Page<CustomerDto>."""

    content: list[CustomerResponse]
    page: int
    size: int
    total_elements: int = Field(serialization_alias="totalElements")
    total_pages: int = Field(serialization_alias="totalPages")


class CustomerPageV2(BaseModel):
    """Paginated customer response (v2 shape)."""

    content: list[CustomerResponseV2]
    page: int
    size: int
    total_elements: int = Field(serialization_alias="totalElements")
    total_pages: int = Field(serialization_alias="totalPages")
