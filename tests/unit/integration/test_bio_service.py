"""BioService tests with respx — no real Ollama calls."""

from __future__ import annotations

import httpx
import pytest
import respx

from iris_service.integration.bio_service import BioService


@pytest.mark.asyncio
async def test_generate_bio_returns_payload_on_200() -> None:
    async with respx.mock:
        respx.post("http://localhost:11434/api/generate").mock(
            return_value=httpx.Response(
                200,
                json={"response": "Alice is a friendly customer.", "done": True},
            )
        )
        service = BioService()
        try:
            text = await service.generate_bio("Alice", "alice@x.com")
        finally:
            await service.aclose()
    assert text == "Alice is a friendly customer."


@pytest.mark.asyncio
async def test_generate_bio_falls_back_on_5xx() -> None:
    """Ollama outage → synthetic fallback (no exception)."""
    async with respx.mock:
        respx.post("http://localhost:11434/api/generate").mock(return_value=httpx.Response(503))
        service = BioService()
        try:
            text = await service.generate_bio("Bob", "bob@x.com")
        finally:
            await service.aclose()
    assert "Bob is a customer" in text
    assert "unavailable" in text


@pytest.mark.asyncio
async def test_generate_bio_falls_back_on_empty_response() -> None:
    """Ollama responds 200 but with empty text → fallback."""
    async with respx.mock:
        respx.post("http://localhost:11434/api/generate").mock(
            return_value=httpx.Response(200, json={"response": "", "done": True})
        )
        service = BioService()
        try:
            text = await service.generate_bio("Carol", "carol@x.com")
        finally:
            await service.aclose()
    assert "Carol is a customer" in text
