"""Paper trading session for one symbol: GridTrader on a VirtualExchange fed by live trades."""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

from pendulum.backtest.engine import GridParams
from pendulum.domain.models import (
    Candle,
    EquityPoint,
    FeeModel,
    Fill,
    Instrument,
    Order,
    Timeframe,
    TradeTick,
)
from pendulum.domain.money import bps
from pendulum.domain.ports import ITradingSessionStore
from pendulum.execution.virtual import VirtualExchange, VirtualOrderGateway
from pendulum.grid.exit_policies import ExitAction, make_exit_policy
from pendulum.grid.plan import GridPlan, GridPlanError, GridSpec, build_plan
from pendulum.grid.trader import GridTrader, plan_from_state
from pendulum.logging import get_logger
from pendulum.notifications import templates
from pendulum.notifications.telegram import INotifier

log = get_logger(__name__)
MODE = "paper"
PERSIST_EVERY = timedelta(minutes=1)


class _Clock:
    def __init__(self, now: datetime) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now


class PaperSession:
    """Owns the virtual account, the trader and persistence for one pair."""

    def __init__(
        self,
        *,
        exchange_id: str,
        instrument: Instrument,
        params: GridParams,
        capital: Decimal,
        fees: FeeModel,
        slippage_bps: Decimal,
        min_step_fee_multiplier: Decimal,
        store: ITradingSessionStore,
        notifier: INotifier,
        now: datetime,
    ) -> None:
        self.exchange_id = exchange_id
        self.instrument = instrument
        self.symbol = str(instrument.symbol)
        self.params = params
        self.capital = capital
        self.fees = fees
        self.slippage_bps = slippage_bps
        self.min_step_fee_multiplier = min_step_fee_multiplier
        self.store = store
        self.notifier = notifier
        self.exchange = VirtualExchange(
            instrument.symbol, instrument.filters, fees, quote=capital, slippage_bps=slippage_bps
        )
        self.clock = _Clock(now)
        self.gateway = VirtualOrderGateway(self.exchange, self.clock)
        self.trader: GridTrader | None = None
        self.session_id: int | None = None
        self.last_event: datetime = now
        self._next_persist = now
        self._was_in_range = True
        self.start_price: Decimal | None = None
        self._orders: dict[str, Order] = {}  # resting orders by client id, for status persistence
        # Market data handlers and the heartbeat interleave on one event loop; the lock keeps every
        # snapshot consistent with the events already written.
        self.lock = asyncio.Lock()
        self.heartbeat_at: datetime = now

    # -- setup ------------------------------------------------------------------------------------

    def _plan(self, price: Decimal, capital: Decimal) -> GridPlan:
        p = self.params
        spec = GridSpec.around(price, p.width_pct, p.levels, p.grid_type, capital)
        return build_plan(
            spec,
            self.instrument.filters,
            self.fees,
            min_step_fee_multiplier=self.min_step_fee_multiplier,
            entry_cost_buffer=self.fees.taker + bps(self.slippage_bps) * 2,
        )

    def _new_trader(self, plan: GridPlan) -> GridTrader:
        return GridTrader(
            plan,
            self.gateway,
            symbol=self.instrument.symbol,
            filters=self.instrument.filters,
            fees=self.fees,
            exit_policy=make_exit_policy(self.params.exit_policy),
            id_prefix=f"pp{self.session_id}",
        )

    def session_params(self) -> dict[str, Any]:
        """Params persisted with the session (enough to re-run the same backtest)."""
        return {
            **self.params.to_dict(),
            "capital": str(self.capital),
            "maker_fee": str(self.fees.maker),
            "taker_fee": str(self.fees.taker),
            "slippage_bps": str(self.slippage_bps),
            "min_step_fee_multiplier": str(self.min_step_fee_multiplier),
            "start_price": str(self.start_price) if self.start_price is not None else None,
        }

    async def open_new(self, price: Decimal, now: datetime) -> None:
        """Create a DB session and start the grid at ``price``."""
        self.clock.now = now
        self.gateway.last_price = price
        self.start_price = price
        plan = self._plan(price, self.capital)
        self.session_id = await self.store.create_session(
            mode=MODE, exchange=self.exchange_id, symbol=self.symbol, params=self.session_params()
        )
        self.trader = self._new_trader(plan)
        await self.trader.start(price, now)
        await self._flush_events()
        await self.persist(now, force=True)
        await self.notifier.send(
            templates.paper_started(
                self.symbol,
                lower=plan.lower,
                upper=plan.upper,
                levels=len(plan.prices),
                grid_type=self.params.grid_type.value,
                capital=self.capital,
                price=price,
                resumed=False,
            )
        )

    async def resume(self, session_id: int, state: dict[str, Any], price: Decimal) -> None:
        """Restore account and grid from a persisted snapshot."""
        self.session_id = session_id
        self.exchange.restore(state["exchange"])
        self.trader = self._new_trader(plan_from_state(state["trader"]))
        self.trader.restore(state["trader"])
        self.last_event = datetime.fromisoformat(state["last_event"])
        self.start_price = Decimal(state["start_price"])
        self.clock.now = self.last_event
        self.gateway.last_price = Decimal(state["last_price"])
        self._next_persist = self.last_event
        self._orders = {o.client_id: o for o in self.exchange.open_orders()}
        plan = self.trader.plan
        await self.notifier.send(
            templates.paper_started(
                self.symbol,
                lower=plan.lower,
                upper=plan.upper,
                levels=len(plan.prices),
                grid_type=self.params.grid_type.value,
                capital=self.capital,
                price=price,
                resumed=True,
            )
        )

    # -- market data ------------------------------------------------------------------------------

    async def on_trade(self, tick: TradeTick) -> None:
        """Process one live trade print."""
        async with self.lock:
            if self.trader is None or not self.trader.active or tick.ts < self.last_event:
                return  # stopped, or already covered by a candle backfill
            self.clock.now = tick.ts
            self.gateway.last_price = tick.price
            fills = self.exchange.match_trade(tick)
            await self._handle_fills(fills)
            await self._check_exit(tick.price, tick.ts, low=tick.price)
            self.last_event = tick.ts
            await self.persist(tick.ts, force=bool(fills))

    async def on_candles(self, candles: Sequence[Candle], timeframe: Timeframe = Timeframe.M1) -> None:
        """Backfill a stream gap with closed candles using the conservative candle rules."""
        async with self.lock:
            if self.trader is None or not self.trader.active:
                return
            tf = timedelta(seconds=timeframe.seconds)
            for candle in candles:
                close_time = candle.open_time + tf
                if close_time <= self.last_event:
                    continue
                fills = self.exchange.match_candle(candle, close_time)
                self.clock.now = close_time
                self.gateway.last_price = candle.close
                await self._handle_fills(fills)
                await self._check_exit(candle.close, close_time, low=candle.low)
                self.last_event = close_time
                if fills:
                    await self.persist(close_time, force=True)
            await self.persist(self.last_event, force=True)

    async def heartbeat(self, now: datetime) -> None:
        """Periodic liveness mark and snapshot, also when a quiet pair has no trades."""
        async with self.lock:
            self.heartbeat_at = now
            if (
                self.session_id is not None
                and self.trader is not None
                and self.gateway.last_price is not None
            ):
                status = "running" if self.trader.active else "stopped"
                await self.store.save_state(
                    self.session_id, self.snapshot(self.gateway.last_price), status=status
                )

    # -- internals --------------------------------------------------------------------------------

    async def _flush_events(self) -> None:
        assert self.session_id is not None  # noqa: S101 - set before trading
        placed, market_fills = self.gateway.drain_events()
        for order in placed:
            self._orders[order.client_id] = order
            await self.store.upsert_order(self.session_id, order)
        for fill in market_fills:
            await self.store.add_fill(self.session_id, fill)

    async def _handle_fills(self, fills: list[Fill]) -> None:
        if not fills or self.trader is None or self.session_id is None:
            return
        for fill in fills:
            await self.store.add_fill(self.session_id, fill)
            order = self._orders.pop(fill.client_id, None)
            if order is not None:
                await self.store.upsert_order(self.session_id, order)  # now FILLED
            cycle = await self.trader.on_fill(fill)
            if cycle is not None:
                await self.store.add_cycle(self.session_id, cycle)
        await self._flush_events()

    async def _check_exit(self, price: Decimal, now: datetime, *, low: Decimal) -> None:
        trader = self.trader
        if trader is None or not trader.active:
            return
        plan = trader.plan
        in_range = plan.lower <= price <= plan.upper
        action = await trader.evaluate_exit(price, now, low=low)
        if self._was_in_range and not in_range:
            await self.notifier.send(
                templates.range_exit(self.symbol, price, plan.lower, plan.upper, action.value)
            )
        self._was_in_range = in_range
        if action is ExitAction.STOP:
            self.gateway.last_price = low
            await trader.stop(now, liquidate=True)
            await self._flush_events()
            await self._finish("stop-loss below range", price)
        elif action is ExitAction.REBUILD:
            try:
                new_plan = self._plan(price, self.exchange.equity(price))
            except GridPlanError as exc:
                await trader.stop(now, liquidate=True)
                await self._flush_events()
                await self._finish(f"cannot rebuild: {exc}", price)
                return
            await trader.rebuild(new_plan, price, now)
            await self._flush_events()
            self._was_in_range = True
            await self.notifier.send(
                templates.range_exit(self.symbol, price, new_plan.lower, new_plan.upper, "rebuilt grid here")
            )

    async def _finish(self, reason: str, price: Decimal) -> None:
        assert self.session_id is not None  # noqa: S101
        await self.store.save_state(self.session_id, self.snapshot(price), status="stopped")
        await self.notifier.send(
            templates.paper_stopped(self.symbol, reason, self.exchange.equity(price), self.capital)
        )

    def snapshot(self, price: Decimal) -> dict[str, Any]:
        """Full resumable state."""
        assert self.trader is not None  # noqa: S101
        return {
            "exchange": self.exchange.snapshot(),
            "trader": self.trader.snapshot(),
            "last_event": self.last_event.isoformat(),
            "last_price": str(price),
            "start_price": str(self.start_price),
            "equity": str(self.exchange.equity(price)),
            "heartbeat": self.heartbeat_at.isoformat(),
        }

    async def persist(self, now: datetime, *, force: bool = False) -> None:
        """Save an equity point and a state snapshot at most once per ``PERSIST_EVERY``."""
        if self.session_id is None or self.trader is None:
            return
        if not force and now < self._next_persist:
            return
        price = self.gateway.last_price
        if price is None:
            return
        b = self.exchange.balances
        await self.store.add_equity_point(
            self.session_id, EquityPoint(now.replace(microsecond=0), b.total_quote, b.total_base, price)
        )
        status = "running" if self.trader.active else "stopped"
        await self.store.save_state(self.session_id, self.snapshot(price), status=status)
        self._next_persist = now + PERSIST_EVERY
