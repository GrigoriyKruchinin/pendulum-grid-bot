"""Async engine and session factory."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from pendulum.config.settings import DatabaseSettings


def create_engine(settings: DatabaseSettings) -> AsyncEngine:
    """Create an asyncpg engine. Connection URL is never logged (echo off)."""
    return create_async_engine(
        settings.async_url,
        pool_size=settings.pool_size,
        pool_pre_ping=True,
        echo=False,
    )


def create_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    """Session factory with expire_on_commit disabled (objects stay usable after commit)."""
    return async_sessionmaker(engine, expire_on_commit=False)


class Database:
    """Owns the engine and hands out transactional sessions."""

    def __init__(self, settings: DatabaseSettings) -> None:
        self.engine = create_engine(settings)
        self.sessions = create_session_factory(self.engine)

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[AsyncSession]:
        """Session that commits on success and rolls back on error."""
        async with self.sessions() as session, session.begin():
            yield session

    async def dispose(self) -> None:
        """Close all pooled connections."""
        await self.engine.dispose()
