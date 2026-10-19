"""Integration fixtures.

Tests run against a dedicated ``<POSTGRES_DB>_test`` database on the server given by ``POSTGRES_*`` env vars.
The test database is created if missing and its tables are dropped and recreated per test, so the real
database (candle history, paper sessions) is never touched.
"""

from collections.abc import AsyncIterator

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from pendulum.config.settings import DatabaseSettings
from pendulum.storage.db import Database
from pendulum.storage.models import Base

TEST_SUFFIX = "_test"


def _test_settings() -> DatabaseSettings:
    base = DatabaseSettings(_env_file=None)
    db = base.db if base.db.endswith(TEST_SUFFIX) else base.db + TEST_SUFFIX
    return base.model_copy(update={"db": db})


async def _ensure_database(settings: DatabaseSettings) -> None:
    admin_url = settings.async_url.rsplit("/", 1)[0] + "/postgres"
    engine = create_async_engine(admin_url, isolation_level="AUTOCOMMIT")
    try:
        async with engine.connect() as conn:
            query = text("SELECT 1 FROM pg_database WHERE datname = :n")
            exists = await conn.scalar(query, {"n": settings.db})
            if not exists:
                await conn.execute(text(f'CREATE DATABASE "{settings.db}"'))
    finally:
        await engine.dispose()


@pytest.fixture
async def db() -> AsyncIterator[Database]:
    settings = _test_settings()
    assert settings.db.endswith(TEST_SUFFIX)  # never run destructive fixtures on a real database
    try:
        await _ensure_database(settings)
    except OSError as exc:  # pragma: no cover - environment dependent
        pytest.skip(f"postgres not reachable: {exc}")
    database = Database(settings)
    async with database.engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    yield database
    await database.dispose()
