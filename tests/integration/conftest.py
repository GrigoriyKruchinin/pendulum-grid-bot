"""Integration fixtures: require a migrated Postgres reachable via POSTGRES_* env vars."""

from collections.abc import AsyncIterator

import pytest
from sqlalchemy import text

from pendulum.config.settings import DatabaseSettings
from pendulum.storage.db import Database
from pendulum.storage.models import Base


@pytest.fixture
async def db() -> AsyncIterator[Database]:
    database = Database(DatabaseSettings())
    try:
        async with database.engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
    except OSError as exc:  # pragma: no cover - environment dependent
        await database.dispose()
        pytest.skip(f"postgres not reachable: {exc}")
    tables = ", ".join(t.name for t in Base.metadata.sorted_tables)
    async with database.transaction() as session:
        await session.execute(text(f"TRUNCATE {tables} RESTART IDENTITY CASCADE"))
    yield database
    await database.dispose()
