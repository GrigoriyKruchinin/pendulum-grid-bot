"""Candle-driven backtester running ``GridTrader`` on a ``VirtualExchange``."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

from pendulum.config.settings import GridType
from pendulum.domain.models import Candle, FeeModel, Instrument, Timeframe
from pendulum.domain.money import ONE, ZERO, bps
from pendulum.execution.virtual import VirtualExchange, VirtualOrderGateway
from pendulum.grid.exit_policies import ExitAction, make_exit_policy
from pendulum.grid.plan import GridPlan, GridPlanError, GridSpec, build_plan
from pendulum.grid.trader import GridTrader

DAYS_PER_YEAR = Decimal(365)


@dataclass(frozen=True, slots=True)
class GridParams:
    """Tunable strategy parameters."""

    levels: int
    width_pct: Decimal
    grid_type: GridType
    exit_policy: str = "stop_loss+trailing_up"

    def to_dict(self) -> dict[str, Any]:
        """JSON-friendly form."""
        return {
            "levels": self.levels,
            "width_pct": str(self.width_pct),
            "grid_type": self.grid_type.value,
            "exit_policy": self.exit_policy,
        }

    def label(self) -> str:
        """Short human label."""
        return f"{self.grid_type.value[:4]}/{self.levels}L/{self.width_pct}%/{self.exit_policy}"


@dataclass(frozen=True, slots=True)
class SimulationConfig:
    """Market assumptions."""

    capital: Decimal
    fees: FeeModel
    slippage_bps: Decimal = Decimal(5)
    min_step_fee_multiplier: Decimal = Decimal("1.5")
    timeframe: Timeframe = Timeframe.M5

    def assumptions(self) -> dict[str, str]:
        """Documented assumptions stored with each run."""
        return {
            "fill_rule": "limit fills only on strict cross (low < buy, high > sell) at limit price",
            "intra_bar": "orders placed in a bar are eligible from the next bar (no intra-bar round trips)",
            "stop_exit_price": "candle low with slippage (worst case)",
            "maker_fee": str(self.fees.maker),
            "taker_fee": str(self.fees.taker),
            "fee_in_received_asset": str(self.fees.fee_in_received_asset),
            "slippage_bps": str(self.slippage_bps),
            "min_step_fee_multiplier": str(self.min_step_fee_multiplier),
            "timeframe": self.timeframe.value,
            "end_valuation": "mark-to-market at last close (inventory not liquidated)",
        }


@dataclass(slots=True)
class BacktestResult:
    """Metrics and curves of one run. Ratios are fractions (0.05 = 5%)."""

    params: GridParams
    start: datetime
    end: datetime
    initial_capital: Decimal
    final_equity: Decimal
    realized: Decimal
    fees_paid: Decimal
    cycles: int
    max_drawdown: Decimal
    bars: int
    bars_in_range: int
    range_exits: int
    rebuilds: int
    stopped: bool
    buy_hold_return: Decimal
    error: str | None = None
    equity_curve: list[tuple[datetime, Decimal]] = field(default_factory=list)

    @property
    def days(self) -> Decimal:
        """Length of the period in days."""
        return Decimal((self.end - self.start).total_seconds()) / Decimal(86400)

    @property
    def net_return(self) -> Decimal:
        """Total return after all costs, inventory marked to market."""
        return self.final_equity / self.initial_capital - ONE

    @property
    def unrealized(self) -> Decimal:
        """Equity change not explained by completed cycles (inventory P&L, dust, entry costs)."""
        return self.final_equity - self.initial_capital - self.realized

    @property
    def annualized_return(self) -> Decimal:
        """Compounded annual rate implied by ``net_return`` over the period."""
        if self.days <= 0:
            return ZERO
        growth = ONE + self.net_return
        if growth <= 0:
            return -ONE
        return growth ** (DAYS_PER_YEAR / self.days) - ONE

    @property
    def buy_hold_annualized(self) -> Decimal:
        """Annualized buy & hold return over the same period."""
        if self.days <= 0:
            return ZERO
        growth = ONE + self.buy_hold_return
        return growth ** (DAYS_PER_YEAR / self.days) - ONE if growth > 0 else -ONE

    @property
    def cycles_per_day(self) -> Decimal:
        """Completed round trips per day."""
        return Decimal(self.cycles) / self.days if self.days > 0 else ZERO

    @property
    def fee_share(self) -> Decimal:
        """Fees as a share of gross cycle profit (realized + fees)."""
        gross = self.realized + self.fees_paid
        return self.fees_paid / gross if gross > 0 else ZERO

    @property
    def time_in_range(self) -> Decimal:
        """Share of bars that closed inside the active grid range."""
        return Decimal(self.bars_in_range) / Decimal(self.bars) if self.bars else ZERO

    def metrics(self) -> dict[str, str | int | bool | None]:
        """JSON-friendly metrics (Decimals as strings, rounded for readability)."""

        def r(x: Decimal) -> str:
            # Annualizing very short periods can yield astronomically large numbers.
            if x.is_finite() and x.adjusted() < 15:
                return str(x.quantize(Decimal("0.000001")))
            return f"{x:.6e}"

        return {
            "initial_capital": r(self.initial_capital),
            "final_equity": r(self.final_equity),
            "net_return": r(self.net_return),
            "annualized_return": r(self.annualized_return),
            "realized": r(self.realized),
            "unrealized": r(self.unrealized),
            "fees_paid": r(self.fees_paid),
            "fee_share": r(self.fee_share),
            "max_drawdown": r(self.max_drawdown),
            "cycles": self.cycles,
            "cycles_per_day": r(self.cycles_per_day),
            "time_in_range": r(self.time_in_range),
            "range_exits": self.range_exits,
            "rebuilds": self.rebuilds,
            "stopped": self.stopped,
            "buy_hold_return": r(self.buy_hold_return),
            "buy_hold_annualized": r(self.buy_hold_annualized),
            "excess_vs_buy_hold": r(self.net_return - self.buy_hold_return),
            "days": r(self.days),
            "error": self.error,
        }


class _Clock:
    def __init__(self, now: datetime) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now


def buy_and_hold_return(first: Candle, last: Candle, config: SimulationConfig) -> Decimal:
    """Buy at the first open (taker fee + slippage), mark at the last close."""
    entry = first.open * (ONE + bps(config.slippage_bps))
    if config.fees.fee_in_received_asset:
        return last.close * (ONE - config.fees.taker) / entry - ONE
    return last.close / (entry * (ONE + config.fees.taker)) - ONE


class _Simulation:
    """State of one backtest run."""

    def __init__(
        self, candles: Sequence[Candle], instrument: Instrument, params: GridParams, config: SimulationConfig
    ) -> None:
        self.candles = candles
        self.instrument = instrument
        self.params = params
        self.config = config
        self.tf = timedelta(seconds=config.timeframe.seconds)
        first, last = candles[0], candles[-1]
        self.result = BacktestResult(
            params=params,
            start=first.open_time,
            end=last.open_time + self.tf,
            initial_capital=config.capital,
            final_equity=config.capital,
            realized=ZERO,
            fees_paid=ZERO,
            cycles=0,
            max_drawdown=ZERO,
            bars=len(candles),
            bars_in_range=0,
            range_exits=0,
            rebuilds=0,
            stopped=False,
            buy_hold_return=buy_and_hold_return(first, last, config),
        )
        self.exchange = VirtualExchange(
            instrument.symbol,
            instrument.filters,
            config.fees,
            quote=config.capital,
            slippage_bps=config.slippage_bps,
        )
        self.clock = _Clock(first.open_time)
        self.gateway = VirtualOrderGateway(self.exchange, self.clock)
        self.gateway.last_price = first.open
        self.trader: GridTrader | None = None
        self.was_in_range = True
        self.peak = ZERO

    def make_plan(self, price: Decimal, capital: Decimal) -> GridPlan:
        p = self.params
        spec = GridSpec.around(price, p.width_pct, p.levels, p.grid_type, capital)
        return build_plan(
            spec,
            self.instrument.filters,
            self.config.fees,
            min_step_fee_multiplier=self.config.min_step_fee_multiplier,
            entry_cost_buffer=self.config.fees.taker + bps(self.config.slippage_bps) * 2,
        )

    async def start(self) -> bool:
        first = self.candles[0]
        try:
            plan = self.make_plan(first.open, self.config.capital)
        except GridPlanError as exc:
            self.result.error = str(exc)
            self.result.equity_curve = [
                (self.result.start, self.config.capital),
                (self.result.end, self.config.capital),
            ]
            return False
        self.trader = GridTrader(
            plan,
            self.gateway,
            symbol=self.instrument.symbol,
            filters=self.instrument.filters,
            fees=self.config.fees,
            exit_policy=make_exit_policy(self.params.exit_policy),
            id_prefix="bt",
        )
        await self.trader.start(first.open, first.open_time)
        return True

    async def handle_exit(self, trader: GridTrader, candle: Candle, now: datetime) -> None:
        in_range = trader.plan.lower <= candle.close <= trader.plan.upper
        self.result.bars_in_range += int(in_range)
        if self.was_in_range and not in_range:
            self.result.range_exits += 1
        self.was_in_range = in_range
        action = await trader.evaluate_exit(candle.close, now, low=candle.low)
        if action is ExitAction.STOP:
            self.gateway.last_price = candle.low  # worst price of the bar
            await trader.stop(now, liquidate=True)
            self.gateway.last_price = candle.close
            self.result.stopped = True
        elif action is ExitAction.REBUILD:
            try:
                plan = self.make_plan(candle.close, self.exchange.equity(candle.close))
            except GridPlanError:
                await trader.stop(now, liquidate=True)
                self.result.stopped = True
            else:
                await trader.rebuild(plan, candle.close, now)
                self.was_in_range = True

    def track_equity(self, candle: Candle, now: datetime, sample: bool) -> Decimal:
        equity = self.exchange.equity(candle.close)
        self.peak = max(self.peak, equity, self.exchange.equity(candle.high))
        if self.peak > 0:
            # The grid is long inventory, so the bar low is the intra-bar equity trough.
            trough = min(equity, self.exchange.equity(candle.low))
            self.result.max_drawdown = max(self.result.max_drawdown, (self.peak - trough) / self.peak)
        if sample:
            self.result.equity_curve.append((now, equity))
        return equity

    async def run(self, equity_sample: timedelta) -> BacktestResult:
        if not await self.start():
            return self.result
        trader = self.trader
        assert trader is not None  # noqa: S101 - set by start()
        next_sample = self.result.start
        equity = self.config.capital
        for candle in self.candles:
            now = candle.open_time + self.tf
            fills = self.exchange.match_candle(candle, now)
            self.clock.now = now
            self.gateway.last_price = candle.close
            for fill in fills:
                await trader.on_fill(fill)
            if trader.active:
                await self.handle_exit(trader, candle, now)
            sample = now >= next_sample
            equity = self.track_equity(candle, now, sample)
            if sample:
                next_sample = now + equity_sample
        r = self.result
        r.final_equity = equity
        r.realized = trader.realized_profit
        r.fees_paid = self.exchange.fees_paid_quote
        r.cycles = trader.cycle_count
        r.rebuilds = trader.rebuilds
        return r


async def run_backtest(
    candles: Sequence[Candle],
    instrument: Instrument,
    params: GridParams,
    config: SimulationConfig,
    *,
    equity_sample: timedelta = timedelta(hours=1),
) -> BacktestResult:
    """Simulate ``params`` over ``candles`` (oldest first, gap-free preferred)."""
    if not candles:
        raise ValueError("no candles")
    return await _Simulation(candles, instrument, params, config).run(equity_sample)
