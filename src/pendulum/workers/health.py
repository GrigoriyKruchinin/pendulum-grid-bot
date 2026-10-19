"""Container healthchecks."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from pendulum.config.settings import DatabaseSettings
from pendulum.paper.session import MODE
from pendulum.storage.db import Database
from pendulum.storage.repositories import SessionReadRepository

PAPER_STALE_AFTER = timedelta(minutes=5)


async def paper_healthy() -> bool:
    """Healthy when every running paper session persisted its state recently."""
    db = Database(DatabaseSettings())
    try:
        sessions = await SessionReadRepository(db).list_sessions(MODE, ["running"])
    finally:
        await db.dispose()
    if not sessions:
        return False
    now = datetime.now(UTC)
    for s in sessions:
        last = s.state.get("last_event")
        if last is None or now - datetime.fromisoformat(last) > PAPER_STALE_AFTER:
            return False
    return True
