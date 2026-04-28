"""GET /customers/{id}/enrich endpoint tests with mocked EnrichmentService."""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from httpx import AsyncClient

from iris_service.messaging.dtos import (
    CustomerEnrichReply,
    CustomerEnrichRequest,
)
from iris_service.messaging.enrichment import EnrichmentService
from iris_service.messaging.kafka_client import get_enrichment_service

if TYPE_CHECKING:
    pass


@pytest.mark.asyncio
async def test_enrich_returns_200_with_display_name(client: AsyncClient, app: FastAPI) -> None:
    # First create a customer so /enrich can find it
    create = await client.post("/customers", json={"name": "Bob", "email": "bob@x.com"})
    assert create.status_code == 201
    customer_id = create.json()["id"]

    # Inject a mock EnrichmentService that returns a canned reply
    fake = AsyncMock(spec=EnrichmentService)
    fake.request_reply.return_value = CustomerEnrichReply(
        id=customer_id,
        name="Bob",
        email="bob@x.com",
        display_name="Bob <bob@x.com>",
    )
    app.dependency_overrides[get_enrichment_service] = lambda: fake

    try:
        response = await client.get(f"/customers/{customer_id}/enrich")
    finally:
        del app.dependency_overrides[get_enrichment_service]

    assert response.status_code == 200
    body = response.json()
    assert body["id"] == customer_id
    assert body["displayName"] == "Bob <bob@x.com>"

    # Sanity : the service was called with the right request payload
    fake.request_reply.assert_awaited_once()
    sent: CustomerEnrichRequest = fake.request_reply.await_args.args[0]
    assert sent.id == customer_id
    assert sent.email == "bob@x.com"


@pytest.mark.asyncio
async def test_enrich_returns_404_for_unknown_customer(client: AsyncClient, app: FastAPI) -> None:
    fake = AsyncMock(spec=EnrichmentService)
    app.dependency_overrides[get_enrichment_service] = lambda: fake
    try:
        response = await client.get("/customers/999/enrich")
    finally:
        del app.dependency_overrides[get_enrichment_service]
    assert response.status_code == 404
    fake.request_reply.assert_not_awaited()


@pytest.mark.asyncio
async def test_enrich_returns_504_on_kafka_timeout(client: AsyncClient, app: FastAPI) -> None:
    create = await client.post("/customers", json={"name": "Carol", "email": "carol@x.com"})
    customer_id = create.json()["id"]

    fake = AsyncMock(spec=EnrichmentService)
    fake.request_reply.side_effect = TimeoutError()
    app.dependency_overrides[get_enrichment_service] = lambda: fake
    try:
        response = await client.get(f"/customers/{customer_id}/enrich")
    finally:
        del app.dependency_overrides[get_enrichment_service]

    assert response.status_code == 504
    assert "did not reply" in response.json()["detail"].lower()


@pytest.mark.asyncio
async def test_enrich_returns_503_when_kafka_not_started(
    client: AsyncClient,
) -> None:
    """Without overriding get_enrichment_service, the real one runs and 503s.

    Lifespan never started Kafka in unit tests (ASGITransport doesn't trigger
    it) so the singleton is None → HTTPException 503.
    """
    create = await client.post("/customers", json={"name": "Dave", "email": "dave@x.com"})
    customer_id = create.json()["id"]

    response = await client.get(f"/customers/{customer_id}/enrich")
    assert response.status_code == 503
