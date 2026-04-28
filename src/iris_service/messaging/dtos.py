"""Kafka payload DTOs for the customer enrichment flow.

Mirrors Java's :
- `CustomerEnrichRequest(id, name, email)` → CustomerEnrichRequest
- `CustomerEnrichReply(id, name, email, displayName)` → CustomerEnrichReply
- `EnrichedCustomerDto(id, name, email, displayName)` → EnrichedCustomerResponse

Wire format is JSON (UTF-8). camelCase on the wire ; snake_case in Python via
`serialization_alias` + `validation_alias`. populate_by_name=True allows both
forms (programmatic Python ↔ JSON wire interop).
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, EmailStr, Field


class CustomerEnrichRequest(BaseModel):
    """Outbound to ``customer.enrich.request``. Triggers a request-reply round-trip."""

    id: int
    name: str
    email: EmailStr


class CustomerEnrichReply(BaseModel):
    """Inbound on ``customer.enrich.reply``. Carries the computed displayName.

    populate_by_name=True so we can build it both from Python code (display_name=...)
    and from a JSON message that uses the camelCase wire field (displayName).
    """

    model_config = ConfigDict(populate_by_name=True)

    id: int
    name: str
    email: EmailStr
    display_name: str = Field(
        serialization_alias="displayName",
        validation_alias="displayName",
    )


class EnrichedCustomerResponse(BaseModel):
    """HTTP response for ``GET /customers/{id}/enrich``.

    Same shape as `CustomerEnrichReply` but kept distinct : the HTTP DTO can
    diverge from the wire DTO without breaking Kafka consumers.
    """

    id: int
    name: str
    email: EmailStr
    display_name: str = Field(serialization_alias="displayName")
