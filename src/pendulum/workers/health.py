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
        return True  # nothing to monitor (all pairs stopped or skipped); the process itself is alive
    now = datetime.now(UTC)
    for s in sessions:
        marks = [s.state.get("heartbeat"), s.state.get("last_event")]
        latest = max((datetime.fromisoformat(m) for m in marks if m), default=None)
        if latest is None or now - latest > PAPER_STALE_AFTER:
            return False
    return True
