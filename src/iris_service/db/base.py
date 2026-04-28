"""SQLAlchemy declarative base + async session factory.

Mirrors Spring's `JpaConfig` / `EntityManagerFactory` setup. The session
factory is bound to the configured Postgres DSN ; FastAPI dependency
injection (`get_db_session`) hands out a session per request and closes
it in the dependency teardown.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from iris_service.config.settings import get_settings


class Base(DeclarativeBase):
    """Declarative base for all ORM entities."""


_engine: AsyncEngine | None = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


def get_engine() -> AsyncEngine:
    """Lazy singleton engine — created on first call."""
    global _engine
    if _engine is None:
        settings = get_settings()
        _engine = create_async_engine(
            settings.db.url,
            echo=settings.dev_mode,
            pool_pre_ping=True,  # detect dead connections (= Hikari's connectionTestQuery)
            pool_size=10,
            max_overflow=20,
        )
    return _engine


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    """Lazy singleton session factory bound to the engine."""
    global _session_factory
    if _session_factory is None:
        _session_factory = async_sessionmaker(
            bind=get_engine(),
            class_=AsyncSession,
            expire_on_commit=False,  # so DTOs can read attributes after commit
        )
    return _session_factory


async def get_db_session() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency : yields a session, commits on success, rolls back
    on exception, closes always.

    Usage : `async def endpoint(db: Annotated[AsyncSession, Depends(get_db_session)])`
    """
    factory = get_session_factory()
    async with factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def reset_engine() -> None:
    """Close the engine — called by app lifespan shutdown + by tests."""
    global _engine, _session_factory
    if _engine is not None:
        await _engine.dispose()
        _engine = None
        _session_factory = None
