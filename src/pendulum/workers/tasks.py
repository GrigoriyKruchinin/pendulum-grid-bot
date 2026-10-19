"""Celery tasks. Each task runs its async body in a fresh event loop."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

from pendulum.config.pairs import load_pairs
from pendulum.config.settings import BacktestSettings, GridSettings
from pendulum.container import build_container, setup_logging
from pendulum.domain.models import Symbol, Timeframe
from pendulum.logging import get_logger
from pendulum.workers.celery_app import celery_app

log = get_logger(__name__)
INITIAL_SYNC_DAYS = 3


async def sync_candles_async() -> None:
    """Incrementally sync candles for all enabled pairs."""
    pairs = load_pairs(GridSettings().pairs_file).enabled()
    tf = Timeframe(BacktestSettings().default_timeframe)
    async with build_container() as c:
        loader = c.loader
        for pair in pairs:
            symbol = Symbol.parse(pair.symbol)
            cp = await c.candles.get_checkpoint(c.adapter.exchange_id, str(symbol), tf)
            since = cp.first_open_time if cp else datetime.now(UTC) - timedelta(days=INITIAL_SYNC_DAYS)
            try:
                await loader.load(symbol, tf, since)
            except Exception:  # one bad pair must not stop the others
                log.exception("sync_failed", symbol=str(symbol))


@celery_app.task(name="pendulum.sync_candles")  # type: ignore[untyped-decorator]
def sync_candles() -> None:
    """Beat: every 5 minutes."""
    setup_logging()
    asyncio.run(sync_candles_async())


async def scan_pairs_async() -> None:
    """Weekly pair scan."""
    async with build_container() as c:
        await c.pair_scanner.scan()


@celery_app.task(name="pendulum.scan_pairs")  # type: ignore[untyped-decorator]
def scan_pairs() -> None:
    """Beat: Mondays 01:00 UTC."""
    setup_logging()
    asyncio.run(scan_pairs_async())
