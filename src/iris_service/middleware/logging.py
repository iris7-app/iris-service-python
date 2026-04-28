"""structlog configuration — JSON logs in prod, pretty console in dev.

Mirrors the Java side's logback-spring.xml dual-profile setup. Wired
once at app startup (``configure_logging`` called from ``app.lifespan``).

Why structlog vs stdlib logging :
- Structured-by-design : every log entry is a dict, JSON-rendered for
  Loki ingestion without regex magic.
- contextvars-based context binding : ``structlog.contextvars.bind_contextvars``
  in the request-id middleware automatically attaches request_id to every
  log within the request scope (no thread-local hackery).
- Foreign-logger interop : stdlib logging output (uvicorn, sqlalchemy,
  aiokafka) gets routed through structlog's processor chain, so EVERY
  log line — ours OR third-party — is JSON in prod.
"""

from __future__ import annotations

import logging
import sys
from typing import Any

import structlog


def configure_logging(*, dev_mode: bool) -> None:
    """Wire structlog + stdlib logging.

    dev_mode=True → ConsoleRenderer (colour-coded human-readable).
    dev_mode=False → JSONRenderer (one JSON object per line, Loki-friendly).

    Idempotent : safe to call multiple times (replaces the existing config).
    """
    timestamper = structlog.processors.TimeStamper(fmt="iso", utc=True)

    shared_processors: list[Any] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        timestamper,
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]

    structlog.configure(
        processors=[
            *shared_processors,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    final_renderer: Any = (
        structlog.dev.ConsoleRenderer(colors=True) if dev_mode else structlog.processors.JSONRenderer()
    )

    formatter = structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=shared_processors,
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            final_renderer,
        ],
    )

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)

    root_logger = logging.getLogger()
    # Replace any pre-existing handlers (uvicorn / pytest may have wired some).
    root_logger.handlers = [handler]
    root_logger.setLevel(logging.INFO)

    # Tame noisy third-party loggers — keep them at WARNING by default.
    for noisy in ("uvicorn.access", "aiokafka", "asyncio", "watchfiles"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
