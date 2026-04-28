"""BioService — generates a customer bio via local Ollama LLM.

Mirror of Java's BioService (Resilience4j circuit-breaker + retry +
fallback). Python equivalent : `tenacity` for retry-with-exponential-
backoff + a try/except fallback returning a synthetic bio on Ollama
outage.

Ollama runs locally (or in the dev-stack docker-compose `llm` profile)
at http://localhost:11434. POST /api/generate with prompt → streamed
response (we use stream=False for simpler handling).

Resilience contract :
- Retry up to 2 times (LLM calls are slow ; 3 attempts of 30s = 90s
  wall-clock worst case).
- Per-attempt timeout 30s (LLM cold start can be 5-10s).
- On final failure, return synthetic bio "<Name> is a customer at our
  service. Bio currently unavailable." — graceful degradation.
"""

from __future__ import annotations

import logging
from typing import Final

import httpx
from pydantic import BaseModel, ConfigDict
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

logger = logging.getLogger(__name__)

# Default Ollama coordinates — local dev. Production overrides via env vars
# resolved upstream (BioService(base_url=settings.ollama_base_url, ...)).
# Final[str] : module-level constants protected from accidental rebind.
OLLAMA_BASE_URL: Final[str] = "http://localhost:11434"
OLLAMA_MODEL: Final[str] = "llama3.2"

# Per-attempt timeout — Ollama cold-start can take 5-10 s on first call ;
# we leave headroom up to 30 s. tenacity retries up to 2x = 90 s wall-clock
# worst case. Final[float] flags this as a tunable knob, not a literal.
PER_ATTEMPT_TIMEOUT_S: Final[float] = 30.0


class OllamaResponse(BaseModel):
    """Ollama /api/generate response body — typed shape replaces the
    previous ``dict[str, Any]`` alias (migrated 2026-04-25 per ADR-0007
    follow-up). Lets the BioService consumer access ``response.text``
    directly with type narrowing.

    extra="ignore" : Ollama adds many fields (model, total_duration,
    load_duration, prompt_eval_count, eval_count, eval_duration, ...)
    that we don't need ; ignoring keeps the model lean.
    """

    model_config = ConfigDict(extra="ignore")

    response: str = ""
    done: bool = False


class BioService:
    """Async client for Ollama /api/generate."""

    def __init__(
        self,
        base_url: str = OLLAMA_BASE_URL,
        model: str = OLLAMA_MODEL,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._base_url = base_url
        self._model = model
        self._client = client or httpx.AsyncClient(timeout=PER_ATTEMPT_TIMEOUT_S)

    async def generate_bio(self, name: str, email: str) -> str:
        """Generate a short professional bio. Returns synthetic fallback on failure."""
        prompt = (
            f"Write a single concise (≤ 30 words) professional bio for a customer "
            f"named {name}, email {email}. Plain prose, no greeting, no markdown."
        )
        try:
            return await self._generate_with_retry(prompt)
        except Exception as exc:
            logger.warning(
                "bio_generate_failed name=%s reason=%s — synthetic fallback",
                name,
                exc,
            )
            return f"{name} is a customer at our service. Bio currently unavailable."

    @retry(
        stop=stop_after_attempt(2),
        wait=wait_exponential(multiplier=1.0, min=1.0, max=5.0),
        retry=retry_if_exception_type((httpx.HTTPError, httpx.TimeoutException)),
        reraise=True,
    )
    async def _generate_with_retry(self, prompt: str) -> str:
        response = await self._client.post(
            f"{self._base_url}/api/generate",
            json={
                "model": self._model,
                "prompt": prompt,
                "stream": False,
                "options": {"temperature": 0.3, "num_predict": 80},
            },
        )
        response.raise_for_status()
        body = OllamaResponse.model_validate(response.json())
        text = body.response.strip()
        if not text:
            raise ValueError("Ollama returned empty response")
        return text

    async def aclose(self) -> None:
        """Close the underlying HTTP client. Called from app.lifespan shutdown."""
        await self._client.aclose()
