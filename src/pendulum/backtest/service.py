"""Backtest application service: loads data, runs simulations, persists results and reports."""

from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from pendulum.backtest.engine import BacktestResult, GridParams, SimulationConfig, run_backtest
from pendulum.backtest.report import single_report, walk_forward_report
from pendulum.backtest.resample import resample
from pendulum.backtest.walk_forward import WARMUP, WalkForwardConfig, WalkForwardResult, run_walk_forward
from pendulum.config.settings import BacktestSettings
from pendulum.domain.models import Candle, FeeModel, Instrument, Symbol, Timeframe
from pendulum.domain.ports import IBacktestRunStore, ICandleStore
from pendulum.exchanges.base import BaseExchangeAdapter
from pendulum.logging import get_logger
from pendulum.market_data.loader import HistoryLoader, floor_time

log = get_logger(__name__)
REPORTS_DIR = Path("docs/reports/backtests")
WF_REPORTS_DIR = Path("docs/reports/walk-forward")
MAX_MISSING_SHARE = 0.01  # refuse to simulate on data with >1% missing bars
# A multiple of every timeframe: with aligned chunk starts, resample buckets never straddle chunks.
LOAD_CHUNK = timedelta(days=30)


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
        """Stored 1m candles resampled to ``timeframe``; raises on poor data quality.

        Reads in day-aligned chunks so a year of 1m bars never sits in memory at once.
        """
        source = Timeframe(self.settings.default_timeframe)
        out: list[Candle] = []
        present = 0
        first: datetime | None = None
        last: datetime | None = None
        cursor = floor_time(start, timeframe)
        while cursor < end:
            chunk_end = min(end, cursor + LOAD_CHUNK)
            raw = await self.candles.get_candles(
                self.adapter.exchange_id, str(symbol), source, max(cursor, start), chunk_end
            )
            if raw:
                present += len(raw)
                first = first or raw[0].open_time
                last = raw[-1].open_time
                out.extend(resample(raw, source, timeframe))
            cursor = chunk_end
        if first is None or last is None:
            raise DataQualityError(
                f"no {source.value} candles for {symbol} in {start:%F}..{end:%F}; run data load"
            )
        expected = int((last - first).total_seconds()) // source.seconds + 1
        missing = expected - present
        if missing / expected > MAX_MISSING_SHARE:
            raise DataQualityError(f"{symbol}: {missing} of {expected} bars missing (>1%)")
        return out

    async def _warmup(
        self, symbol: Symbol, start: datetime, params: GridParams, config: SimulationConfig
    ) -> list[Candle]:
        """Bars right before ``start`` that feed the regime filter (empty without a filter or history)."""
        if params.regime == "none":
            return []
        try:
            return await self.load_candles(symbol, start - WARMUP, start, config.timeframe)
        except DataQualityError:
            return []  # no earlier history: the filter warms up inside the run

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
        warmup = await self._warmup(symbol, start, params, config)
        result = await run_backtest(candles, instrument, params, config, warmup=warmup)
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

    async def walk_forward(
        self,
        symbol: Symbol,
        start: datetime,
        end: datetime,
        *,
        config: SimulationConfig,
        wf: WalkForwardConfig,
        grid: Sequence[GridParams],
        workers: int | None = None,
    ) -> tuple[WalkForwardResult, Path]:
        """Run walk-forward, persist parent + per-window best train/test runs, write a report."""
        instrument = await self.loader.resolve(symbol)
        candles = await self.load_candles(symbol, start, end, config.timeframe)
        result = await run_walk_forward(candles, instrument, config, wf, grid, workers=workers)
        sha = git_sha()
        assumptions = config.assumptions()
        parent = await self.runs.save_run(
            kind="walk_forward",
            git_sha=sha,
            exchange=self.adapter.exchange_id,
            symbol=str(symbol),
            timeframe=config.timeframe,
            period_start=candles[0].open_time,
            period_end=end,
            params={
                "train_days": wf.train_days,
                "test_days": wf.test_days,
                "grid_size": len(grid),
                "max_train_drawdown": str(wf.max_train_drawdown),
            },
            metrics={
                "windows": len(result.windows),
                "positive_share": str(result.positive_share),
                "oos_compound_return": str(result.oos_compound_return),
                "oos_annualized": str(result.oos_annualized),
                "buy_hold_compound_return": str(result.buy_hold_compound_return),
                "max_oos_drawdown": str(result.max_oos_drawdown),
                "top2_concentration": str(result.top2_profit_concentration),
            },
            assumptions=assumptions,
        )
        for w in result.windows:
            for kind, r in (("wf_train", w.train), ("wf_test", w.test)):
                if r is None:
                    continue
                await self.runs.save_run(
                    kind=kind,
                    git_sha=sha,
                    exchange=self.adapter.exchange_id,
                    symbol=str(symbol),
                    timeframe=config.timeframe,
                    period_start=r.start,
                    period_end=r.end,
                    params=r.params.to_dict(),
                    metrics=r.metrics(),
                    assumptions=assumptions,
                    parent_id=parent,
                )
        await asyncio.to_thread(WF_REPORTS_DIR.mkdir, parents=True, exist_ok=True)
        stamp = datetime.now(UTC).strftime("%Y-%m-%d")
        path = WF_REPORTS_DIR / f"{stamp}-{symbol.base}{symbol.quote}-wf{parent}.md"
        report = walk_forward_report(result, assumptions, sha, len(grid), wf)
        await asyncio.to_thread(path.write_text, report, encoding="utf-8")
        log.info("walk_forward_done", run_id=parent, symbol=str(symbol), windows=len(result.windows))
        return result, path
