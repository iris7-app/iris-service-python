"""Kafka fire-and-forget — `CustomerCreatedEvent` on `customer.created`.

Mirrors Java's KafkaCustomerEventPublisher : POST /customers persists the
customer, then publishes a CustomerCreatedEvent on `customer.created`
without waiting for a reply. A consumer in the same service (or in a
sibling microservice) listens + reacts (logs, indexes, sends email...).

Best-effort : a Kafka outage MUST NOT break the create flow. Publication
errors are logged + swallowed so the HTTP 201 still goes back to the
client. Same posture as the Redis recent-buffer add (Étape 3).
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from pydantic import BaseModel, EmailStr

if TYPE_CHECKING:
    from aiokafka import AIOKafkaProducer

logger = logging.getLogger(__name__)


class CustomerCreatedEvent(BaseModel):
    """Wire DTO for the customer.created topic."""

    id: int
    name: str
    email: EmailStr


async def publish_customer_created(
    producer: AIOKafkaProducer | None,
    topic: str,
    event: CustomerCreatedEvent,
) -> bool:
    """Best-effort publish ; returns True on success, False on any failure.

    Designed to be called inside the POST /customers handler AFTER the DB
    commit. The Kafka producer is the singleton owned by kafka_client.py.
    Caller passes ``None`` if Kafka isn't started — function logs +
    returns False in that case (degraded ; CRUD continues).
    """
    if producer is None:
        logger.warning(
            "customer_created_publish_skipped reason=producer_not_initialised id=%s",
            event.id,
        )
        return False
    try:
        await producer.send_and_wait(
            topic,
            key=str(event.id).encode(),
            value=event.model_dump_json().encode(),
        )
        return True
    except Exception as exc:
        logger.warning(
            "customer_created_publish_failed id=%s reason=%s — degraded path",
            event.id,
            exc,
        )
        return False
