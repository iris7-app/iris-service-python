"""Customer audit endpoint — `GET /customers/{id}/audit`.

Mirrors Java's `CustomerController.audit` : returns a synthetic audit trail
with placeholder events. In a real system this would query an event store
(Kafka topic replay, DB audit table, OpenTelemetry trace export, etc.).
For the demo, we return a deterministic synthetic trail derived from the
customer's id + created_at.

This is the kind of read-only "compliance" endpoint that proves the
backend has the conceptual hooks for audit without requiring real
event-sourcing infrastructure.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy.exc import NoResultFound
from sqlalchemy.ext.asyncio import AsyncSession

from iris_service.customer.repository import CustomerRepository
from iris_service.db.base import get_db_session

router = APIRouter(prefix="/customers", tags=["Customer — audit"])


class AuditEvent(BaseModel):
    """One row of the audit trail."""

    timestamp: datetime
    event: str
    actor: str
    details: dict[str, str] = Field(default_factory=dict)


class CustomerAuditResponse(BaseModel):
    """Synthetic audit trail for a customer."""

    customer_id: int = Field(serialization_alias="customerId")
    customer_email: EmailStr = Field(serialization_alias="customerEmail")
    events: list[AuditEvent]


@router.get("/{id_}/audit", response_model=CustomerAuditResponse)
async def get_customer_audit(
    id_: int,
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> CustomerAuditResponse:
    """Return synthetic audit trail for a customer.

    Returns 404 if the customer doesn't exist. The events are derived
    deterministically from the customer's id + created_at so the
    response is stable across calls (good for cache + UI snapshot tests).
    """
    try:
        customer = await CustomerRepository.find_by_id_or_raise(db, id_)
    except NoResultFound as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc

    base_ts = customer.created_at or datetime.now(UTC)
    if base_ts.tzinfo is None:
        base_ts = base_ts.replace(tzinfo=UTC)

    # Synthetic event trail — 4 deterministic events mirroring a typical
    # CRUD lifecycle. Real systems would query an event store ; we
    # demonstrate the contract.
    events = [
        AuditEvent(
            timestamp=base_ts,
            event="customer.created",
            actor="system",
            details={"source": "POST /customers", "id": str(customer.id)},
        ),
        AuditEvent(
            timestamp=base_ts + timedelta(seconds=1),
            event="customer.event.published",
            actor="kafka-producer",
            details={"topic": "customer.created", "partition": "0"},
        ),
        AuditEvent(
            timestamp=base_ts + timedelta(minutes=5),
            event="customer.read",
            actor="anonymous",
            details={"source": "GET /customers/{id}"},
        ),
        AuditEvent(
            timestamp=base_ts + timedelta(hours=1),
            event="customer.recent_buffer.added",
            actor="redis-buffer",
            details={"key": "customer-service:recent:customers"},
        ),
    ]
    return CustomerAuditResponse(
        customer_id=customer.id,
        customer_email=customer.email,
        events=events,
    )
