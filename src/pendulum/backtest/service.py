"""Backtest application service: loads data, runs simulations, persists results and reports."""

from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from pendulum.backtest.engine import BacktestResult, GridParams, SimulationConfig, run_backtest
from pendulum.backtest.report import single_report
from pendulum.backtest.resample import count_missing, resample
from pendulum.config.settings import BacktestSettings
from pendulum.domain.models import Candle, FeeModel, Instrument, Symbol, Timeframe
from pendulum.domain.ports import IBacktestRunStore, ICandleStore
from pendulum.exchanges.base import BaseExchangeAdapter
from pendulum.logging import get_logger
from pendulum.market_data.loader import HistoryLoader

log = get_logger(__name__)
REPORTS_DIR = Path("docs/reports/backtests")
MAX_MISSING_SHARE = 0.01  # refuse to simulate on data with >1% missing bars


class DataQualityError(ValueError):
    """Candle data is too incomplete to simulate honestly."""


def git_sha() -> str:
    """Code version: ``GIT_SHA`` env (set in Docker builds) or the local git HEAD."""
    env = os.environ.get("GIT_SHA")
    if env:
        return env[:40]
    git = shutil.which("git")
    if git is None:
        return "unknown"
    try:
        # Fixed argv with a resolved git binary: no untrusted input reaches the subprocess.
        out = subprocess.run(  # noqa: S603
            [git, "rev-parse", "HEAD"], capture_output=True, text=True, timeout=5, check=True
        )
    except (subprocess.SubprocessError, OSError):
        return "unknown"
    return out.stdout.strip()[:40] or "unknown"


@dataclass(slots=True)
class BacktestService:
    """Glue between storage, exchange metadata and the simulation engine."""

    adapter: BaseExchangeAdapter
    candles: ICandleStore
    runs: IBacktestRunStore
    loader: HistoryLoader
    settings: BacktestSettings

    async def fee_model(self, instrument: Instrument) -> FeeModel:
        """Account fee rate if a key is configured, else settings."""
        rate = await self.adapter.fee_rate(instrument)
        return FeeModel(rate.maker, rate.taker, fee_in_received_asset=True)

    async def load_candles(
        self, symbol: Symbol, start: datetime, end: datetime, timeframe: Timeframe
    ) -> list[Candle]:
        """Stored 1m candles resampled to ``timeframe``; raises on poor data quality."""
        source = Timeframe(self.settings.default_timeframe)
        raw = await self.candles.get_candles(self.adapter.exchange_id, str(symbol), source, start, end)
        if not raw:
            raise DataQualityError(
                f"no {source.value} candles for {symbol} in {start:%F}..{end:%F}; run data load"
            )
        missing = count_missing(raw, source)
        if missing / (len(raw) + missing) > MAX_MISSING_SHARE:
            raise DataQualityError(f"{symbol}: {missing} of {len(raw) + missing} bars missing (>1%)")
        return resample(raw, source, timeframe)

    async def run(
        self,
        symbol: Symbol,
        start: datetime,
        end: datetime,
        params: GridParams,
        config: SimulationConfig,
        *,
        write_report: bool = True,
    ) -> tuple[int, BacktestResult, Path | None]:
        """Run, persist and (optionally) write a markdown report."""
        instrument = await self.loader.resolve(symbol)
        candles = await self.load_candles(symbol, start, end, config.timeframe)
        result = await run_backtest(candles, instrument, params, config)
        sha = git_sha()
        run_id = await self.runs.save_run(
            kind="single",
            git_sha=sha,
            exchange=self.adapter.exchange_id,
            symbol=str(symbol),
            timeframe=config.timeframe,
            period_start=result.start,
            period_end=result.end,
            params=params.to_dict(),
            metrics=result.metrics(),
            assumptions=config.assumptions(),
        )
        path: Path | None = None
        if write_report:
            await asyncio.to_thread(REPORTS_DIR.mkdir, parents=True, exist_ok=True)
            stamp = datetime.now(UTC).strftime("%Y-%m-%d")
            path = REPORTS_DIR / f"{stamp}-{symbol.base}{symbol.quote}-run{run_id}.md"
            report = single_report(str(symbol), result, config.assumptions(), sha)
            await asyncio.to_thread(path.write_text, report, encoding="utf-8")
        log.info("backtest_done", run_id=run_id, symbol=str(symbol), net_return=str(result.net_return))
        return run_id, result, path
