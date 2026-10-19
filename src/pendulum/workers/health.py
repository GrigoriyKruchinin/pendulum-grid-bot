"""Container healthchecks."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from pendulum.config.settings import DatabaseSettings
from pendulum.paper.session import MODE
from pendulum.storage.db import Database
from pendulum.storage.repositories import SessionReadRepository

STREAM_STALE_AFTER = timedelta(minutes=5)
SYMBOL_STALE_AFTER = timedelta(minutes=60)


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
        # Stream liveness: the runner records when any trade last arrived (heartbeat snapshots carry it).
        stream_at = s.state.get("stream_at")
        if stream_at is None or now - datetime.fromisoformat(stream_at) > STREAM_STALE_AFTER:
            return False
        # A liquid pair silent this long on a live connection means a broken subscription.
        last = s.state.get("last_event")
        if last is None or now - datetime.fromisoformat(last) > SYMBOL_STALE_AFTER:
            return False
    return True
