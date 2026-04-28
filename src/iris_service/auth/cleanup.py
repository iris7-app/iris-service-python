"""Refresh token cleanup job — async APScheduler cron.

Mirrors Java's @Scheduled(cron = "0 0 3 * * *") job that purges revoked +
expired refresh tokens from the DB. Without this cleanup, the
``refresh_token`` table grows unbounded — every login + refresh appends
a row, and revoked rows live forever.

Schedule : daily at 03:00 local time. Async via AsyncIOScheduler so it
runs inside the FastAPI event loop without blocking the request thread.

Wired in ``app.lifespan`` startup ; cancelled on shutdown for clean exit.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy import delete, or_

from iris_service.auth.models import RefreshToken
from iris_service.db.base import get_session_factory

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

_scheduler: AsyncIOScheduler | None = None


async def cleanup_refresh_tokens() -> int:
    """Delete revoked + expired refresh tokens. Returns count deleted.

    Idempotent : safe to call multiple times (just deletes nothing on
    repeat). Used both by the cron job AND by tests that want to
    verify the cleanup behaviour without waiting for 03:00.
    """
    factory = get_session_factory()
    async with factory() as session:
        now = datetime.now(UTC)
        result = await session.execute(
            delete(RefreshToken).where(
                or_(
                    RefreshToken.revoked.is_(True),
                    RefreshToken.expires_at < now,
                )
            )
        )
        await session.commit()
        count = result.rowcount or 0
        logger.info("refresh_token_cleanup deleted=%d", count)
        return count


def start_scheduler() -> None:
    """Bootstrap the AsyncIOScheduler + register the daily cleanup job.

    Idempotent : second call is a no-op.
    """
    global _scheduler
    if _scheduler is not None:
        return
    _scheduler = AsyncIOScheduler(timezone="UTC")
    _scheduler.add_job(
        cleanup_refresh_tokens,
        trigger="cron",
        hour=3,
        minute=0,
        id="refresh_token_cleanup",
        name="Refresh token cleanup (revoked + expired)",
        replace_existing=True,
        misfire_grace_time=3600,  # if app was down at 03:00, run within the next hour
    )
    _scheduler.start()
    logger.info("scheduler_started job=refresh_token_cleanup cron=0:3:0_UTC")


def stop_scheduler() -> None:
    """Shutdown the scheduler cleanly. Called from app.lifespan shutdown."""
    global _scheduler
    if _scheduler is None:
        return
    _scheduler.shutdown(wait=False)
    _scheduler = None
    logger.info("scheduler_stopped")
