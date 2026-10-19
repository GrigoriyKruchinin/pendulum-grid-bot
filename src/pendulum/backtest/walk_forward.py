"""Walk-forward parameter search.

For each window: evaluate every parameter set on the train span, pick the best one that respects the
drawdown limit, then simulate only that set on the following unseen test span. Only test (out-of-sample)
results feed the Go/No-Go decision.
"""

from __future__ import annotations

import asyncio
import bisect
import itertools
import os
from collections.abc import Sequence
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal

from pendulum.backtest.engine import (
    BacktestResult,
    GridParams,
    SimulationConfig,
    buy_and_hold_return,
    run_backtest,
)
from pendulum.config.settings import GridType
from pendulum.domain.models import Candle, Instrument
from pendulum.domain.money import ONE, ZERO

DAYS_PER_YEAR = Decimal(365)


@dataclass(frozen=True, slots=True)
class WalkForwardConfig:
    """Window sizes and selection rules."""

    train_days: int = 60
    test_days: int = 14
    max_train_drawdown: Decimal = Decimal("0.20")
    min_train_return: Decimal = ZERO  # a set must at least break even in-sample to be traded


@dataclass(frozen=True, slots=True)
class Window:
    """One train/test split (half-open spans)."""

    train_start: datetime
    train_end: datetime
    test_end: datetime


def make_windows(start: datetime, end: datetime, train_days: int, test_days: int) -> list[Window]:
    """Rolling windows stepping by ``test_days``; the last test span must fit before ``end``."""
    train, test = timedelta(days=train_days), timedelta(days=test_days)
    windows: list[Window] = []
    t = start
    while t + train + test <= end:
        windows.append(Window(t, t + train, t + train + test))
        t += test
    return windows


def default_param_grid() -> list[GridParams]:
    """Search space: 4 level counts x 4 widths x 2 grid types x 3 exit policies = 96 sets."""
    return [
        GridParams(levels, Decimal(width), grid_type, exit_policy)
        for levels, width, grid_type, exit_policy in itertools.product(
            (10, 20, 30, 40),
            (6, 10, 16, 24),
            (GridType.GEOMETRIC, GridType.ARITHMETIC),
            ("stop_loss+trailing_up", "rebuild", "wait"),
        )
    ]


def quick_param_grid() -> list[GridParams]:
    """Smaller space (12 sets) for fast exploratory runs."""
    return [
        GridParams(levels, Decimal(width), GridType.GEOMETRIC, exit_policy)
        for levels, width, exit_policy in itertools.product(
            (10, 20, 30), (8, 16), ("stop_loss+trailing_up", "rebuild")
        )
    ]


@dataclass(slots=True)
class WindowResult:
    """Outcome of one window."""

    window: Window
    best_params: GridParams | None
    train: BacktestResult | None
    test: BacktestResult | None
    candidates: int
    eligible: int

    @property
    def traded(self) -> bool:
        """False when no parameter set qualified in-sample (the bot stays in cash)."""
        return self.test is not None

    @property
    def oos_return(self) -> Decimal:
        """Test return; zero when not traded."""
        return self.test.net_return if self.test else ZERO

    @property
    def positive(self) -> bool:
        """Net-positive out-of-sample."""
        return self.oos_return > 0


@dataclass(slots=True)
class WalkForwardResult:
    """All windows of one symbol plus aggregate OOS statistics."""

    symbol: str
    windows: list[WindowResult] = field(default_factory=list)
    buy_hold_returns: list[Decimal] = field(default_factory=list)

    @property
    def test_days(self) -> Decimal:
        """Total out-of-sample days."""
        return sum(
            (Decimal((w.window.test_end - w.window.train_end).total_seconds()) / 86400 for w in self.windows),
            ZERO,
        )

    @property
    def positive_share(self) -> Decimal:
        """Share of windows with positive OOS net return (untraded windows count as not positive)."""
        return Decimal(sum(w.positive for w in self.windows)) / len(self.windows) if self.windows else ZERO

    @property
    def oos_compound_return(self) -> Decimal:
        """OOS returns chained across consecutive test windows."""
        growth = ONE
        for w in self.windows:
            growth *= ONE + w.oos_return
        return growth - ONE

    @property
    def buy_hold_compound_return(self) -> Decimal:
        """Buy & hold chained over the same test windows."""
        growth = ONE
        for r in self.buy_hold_returns:
            growth *= ONE + r
        return growth - ONE

    def _annualize(self, total: Decimal) -> Decimal:
        days = self.test_days
        if days <= 0:
            return ZERO
        growth = ONE + total
        return growth ** (DAYS_PER_YEAR / days) - ONE if growth > 0 else -ONE

    @property
    def oos_annualized(self) -> Decimal:
        """Annualized chained OOS return."""
        return self._annualize(self.oos_compound_return)

    @property
    def buy_hold_annualized(self) -> Decimal:
        """Annualized chained buy & hold return."""
        return self._annualize(self.buy_hold_compound_return)

    @property
    def max_oos_drawdown(self) -> Decimal:
        """Worst intra-window drawdown out-of-sample."""
        return max((w.test.max_drawdown for w in self.windows if w.test), default=ZERO)

    @property
    def mean_train_return(self) -> Decimal:
        """Average in-sample return of the selected sets (to compare with OOS: overfitting gap)."""
        vals = [w.train.net_return for w in self.windows if w.train and w.test]
        return sum(vals, ZERO) / len(vals) if vals else ZERO

    @property
    def mean_test_return(self) -> Decimal:
        """Average OOS return of traded windows."""
        vals = [w.oos_return for w in self.windows if w.test]
        return sum(vals, ZERO) / len(vals) if vals else ZERO

    @property
    def top2_profit_concentration(self) -> Decimal:
        """Share of total positive OOS return contributed by the best two windows (1.0 = all of it)."""
        positives = sorted((w.oos_return for w in self.windows if w.oos_return > 0), reverse=True)
        total = sum(positives, ZERO)
        return sum(positives[:2], ZERO) / total if total > 0 else ZERO


def select_best(results: Sequence[BacktestResult], cfg: WalkForwardConfig) -> BacktestResult | None:
    """Highest in-sample net return among sets within the drawdown limit and above the minimum return."""
    eligible = [
        r
        for r in results
        if r.error is None
        and r.max_drawdown <= cfg.max_train_drawdown
        and r.net_return > cfg.min_train_return
    ]
    if not eligible:
        return None
    return max(eligible, key=lambda r: (r.net_return, -r.max_drawdown))


# -- process pool workers -----------------------------------------------------------------------------

_WORKER_STATE: tuple[list[Candle], Instrument, SimulationConfig] | None = None


def _init_worker(candles: list[Candle], instrument: Instrument, config: SimulationConfig) -> None:
    global _WORKER_STATE  # noqa: PLW0603 - per-process cache avoids re-pickling candles per task
    _WORKER_STATE = (candles, instrument, config)


def _evaluate(params: GridParams) -> BacktestResult:
    assert _WORKER_STATE is not None  # noqa: S101 - set by the pool initializer
    candles, instrument, config = _WORKER_STATE
    result = asyncio.run(run_backtest(candles, instrument, params, config))
    result.equity_curve = []
    return result


def _slice(
    candles: Sequence[Candle], times: Sequence[datetime], start: datetime, end: datetime
) -> list[Candle]:
    return list(candles[bisect.bisect_left(times, start) : bisect.bisect_left(times, end)])


async def _evaluate_grid(
    candles: list[Candle],
    instrument: Instrument,
    config: SimulationConfig,
    grid: Sequence[GridParams],
    workers: int,
) -> list[BacktestResult]:
    if workers <= 1:
        out = []
        for p in grid:
            r = await run_backtest(candles, instrument, p, config)
            r.equity_curve = []
            out.append(r)
        return out
    loop = asyncio.get_running_loop()
    with ProcessPoolExecutor(
        max_workers=workers, initializer=_init_worker, initargs=(candles, instrument, config)
    ) as pool:
        return list(await asyncio.gather(*(loop.run_in_executor(pool, _evaluate, p) for p in grid)))


async def run_walk_forward(
    candles: Sequence[Candle],
    instrument: Instrument,
    config: SimulationConfig,
    wf: WalkForwardConfig,
    grid: Sequence[GridParams],
    *,
    workers: int | None = None,
) -> WalkForwardResult:
    """Run the full walk-forward procedure on ``candles`` (oldest first)."""
    result = WalkForwardResult(str(instrument.symbol))
    if not candles:
        return result
    n_workers = workers if workers is not None else max(1, (os.cpu_count() or 2) - 1)
    times = [c.open_time for c in candles]
    tf = timedelta(seconds=config.timeframe.seconds)
    windows = make_windows(candles[0].open_time, candles[-1].open_time + tf, wf.train_days, wf.test_days)
    for window in windows:
        train = _slice(candles, times, window.train_start, window.train_end)
        test = _slice(candles, times, window.train_end, window.test_end)
        if not train or not test:
            continue
        train_results = await _evaluate_grid(train, instrument, config, grid, n_workers)
        best = select_best(train_results, wf)
        eligible = sum(
            1 for r in train_results if r.error is None and r.max_drawdown <= wf.max_train_drawdown
        )
        test_result = await run_backtest(test, instrument, best.params, config) if best is not None else None
        result.windows.append(
            WindowResult(window, best.params if best else None, best, test_result, len(grid), eligible)
        )
        bh = buy_and_hold_return(test[0], test[-1], config)
        result.buy_hold_returns.append(bh)
    return result
