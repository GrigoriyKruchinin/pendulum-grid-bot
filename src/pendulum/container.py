"""Composition root: builds concrete implementations from settings (dependency injection by hand)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass

from pendulum.backtest.service import BacktestService
from pendulum.config.settings import (
    BacktestSettings,
    DatabaseSettings,
    ExchangeSettings,
    GridSettings,
    ScannerSettings,
    get_app_settings,
)
from pendulum.exchanges.base import BaseExchangeAdapter
from pendulum.exchanges.registry import default_registry
from pendulum.logging import configure_logging
from pendulum.market_data.loader import HistoryLoader
from pendulum.scanner.scanner import PairScanner
from pendulum.storage.db import Database
from pendulum.storage.repositories import (
    BacktestRunRepository,
    CandleRepository,
    InstrumentRepository,
    ScannerRepository,
    TradingSessionRepository,
)


@dataclass(slots=True)
class Container:
    """Wired services for one process."""

    db: Database
    adapter: BaseExchangeAdapter
    candles: CandleRepository
    instruments: InstrumentRepository
    backtests: BacktestRunRepository
    sessions: TradingSessionRepository
    scanner: ScannerRepository

    @property
    def loader(self) -> HistoryLoader:
        """History loader bound to the active exchange."""
        return HistoryLoader(self.adapter, self.candles, self.instruments)

    @property
    def pair_scanner(self) -> PairScanner:
        """Pair scanner bound to the active exchange."""
        return PairScanner(self.adapter, self.scanner, ScannerSettings(), GridSettings().quote_asset)

    @property
    def backtest_service(self) -> BacktestService:
        """Backtest service bound to the active exchange."""
        return BacktestService(self.adapter, self.candles, self.backtests, self.loader, BacktestSettings())


def setup_logging() -> None:
    """Configure logging from ``AppSettings``."""
    app = get_app_settings()
    configure_logging(app.log_level, json=app.log_json)


@asynccontextmanager
async def build_container() -> AsyncIterator[Container]:
    """Create all services and dispose of them on exit."""
    db = Database(DatabaseSettings())
    adapter = default_registry().create_active(ExchangeSettings())
    try:
        yield Container(
            db=db,
            adapter=adapter,
            candles=CandleRepository(db),
            instruments=InstrumentRepository(db),
            backtests=BacktestRunRepository(db),
            sessions=TradingSessionRepository(db),
            scanner=ScannerRepository(db),
        )
    finally:
        await adapter.aclose()
        await db.dispose()
