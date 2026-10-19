"""Paper runner: one live trade stream feeding a paper session per enabled pair."""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Callable, Sequence
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from html import escape

from pendulum.backtest.engine import GridParams
from pendulum.config.pairs import PairConfig
from pendulum.config.settings import BacktestSettings, GridSettings, RiskSettings
from pendulum.domain.models import Candle, FeeModel, Instrument, StreamGap, Symbol, Timeframe, TradeTick
from pendulum.domain.ports import ITradingSessionStore
from pendulum.exchanges.base import BaseExchangeAdapter, ExchangeError
from pendulum.grid.plan import GridPlanError
from pendulum.logging import get_logger
from pendulum.market_data.loader import floor_time
from pendulum.notifications.telegram import INotifier
from pendulum.paper.session import MODE, PaperSession

log = get_logger(__name__)
_M1 = timedelta(minutes=1)


def params_for(pair: PairConfig, grid: GridSettings) -> GridParams:
    """Grid parameters for a pair: per-pair overrides on top of global defaults."""
    return GridParams(
        levels=pair.levels or grid.default_levels,
        width_pct=pair.range_width_pct or grid.default_width_pct,
        grid_type=pair.grid_type or grid.default_type,
        exit_policy=grid.default_exit_policy,
    )


class PaperRunner:
    """Creates/resumes sessions and dispatches market data to them."""

    def __init__(
        self,
        *,
        adapter: BaseExchangeAdapter,
        store: ITradingSessionStore,
        notifier: INotifier,
        pairs: Sequence[PairConfig],
        grid: GridSettings,
        risk: RiskSettings,
        backtest: BacktestSettings,
        rearm_stopped: bool = False,
        heartbeat_every: float = 60.0,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.adapter = adapter
        self.store = store
        self.notifier = notifier
        self.pairs = list(pairs)
        self.grid = grid
        self.risk = risk
        self.backtest = backtest
        self._now = now
        self.rearm_stopped = rearm_stopped
        self.heartbeat_every = heartbeat_every
        self.sessions: dict[Symbol, PaperSession] = {}
        self._pending_gap: dict[Symbol, datetime] = {}  # symbol -> gap end (reconnect time)
        self.stream_at: datetime | None = None

    async def setup(self) -> list[Instrument]:
        """Resume running sessions or open new ones at the current price."""
        instruments = await self.adapter.rest.fetch_instruments()
        prices = {t.symbol: t.last for t in await self.adapter.rest.fetch_tickers()}
        total = sum((p.capital_usd or self.risk.max_pair_capital_usd for p in self.pairs), Decimal(0))
        if total > self.risk.max_total_capital_usd:
            log.warning(
                "paper_capital_exceeds_live_limit",
                total=str(total),
                limit=str(self.risk.max_total_capital_usd),
            )
        active: list[Instrument] = []
        for pair in self.pairs:
            try:
                inst = await self._setup_pair(pair, instruments, prices)
            except (GridPlanError, ExchangeError) as exc:
                # One unbuildable pair must not stop the others.
                log.error("paper_pair_skipped", symbol=pair.symbol, error=str(exc))
                await self.notifier.send(
                    f"⚠️ <b>Paper pair skipped</b>: <code>{pair.symbol}</code>\n{escape(str(exc))}"
                )
                continue
            if inst is not None:
                active.append(inst)
        return active

    async def _setup_pair(
        self, pair: PairConfig, instruments: Sequence[Instrument], prices: dict[Symbol, Decimal]
    ) -> Instrument | None:
        symbol = Symbol.parse(pair.symbol)
        inst = self.adapter.find_instrument(instruments, symbol)
        price = prices.get(symbol)
        if price is None:
            log.warning("paper_no_price", symbol=pair.symbol)
            return None
        latest = await self.store.get_latest(MODE, self.adapter.exchange_id, pair.symbol)
        if latest is not None and latest[1] == "stopped" and not self.rearm_stopped:
            log.warning("paper_pair_stopped", symbol=pair.symbol, session_id=latest[0])
            await self.notifier.send(
                f"🛑 <b>{escape(pair.symbol)}</b> stays stopped (session {latest[0]}). "
                "Restart with <code>pendulum paper run --rearm</code> to open a new grid."
            )
            return None
        rate = await self.adapter.fee_rate(inst)
        now = self._now()
        session = PaperSession(
            exchange_id=self.adapter.exchange_id,
            instrument=inst,
            params=params_for(pair, self.grid),
            capital=pair.capital_usd or self.risk.max_pair_capital_usd,
            fees=FeeModel(rate.maker, rate.taker),
            slippage_bps=self.backtest.slippage_bps,
            min_step_fee_multiplier=self.grid.min_step_fee_multiplier,
            store=self.store,
            notifier=self.notifier,
            now=now,
        )
        if latest is not None and latest[1] == "running" and "trader" in latest[2]:
            session_id, _, state = latest
            # Events written after the last snapshot would be replayed by the backfill: drop them first.
            await self.store.prune_after(session_id, datetime.fromisoformat(state["last_event"]))
            await session.resume(session_id, state, price)
            await self.backfill(session, session.last_event, now)
        else:
            await session.open_new(price, now)
        self.sessions[symbol] = session
        log.info("paper_session_ready", symbol=pair.symbol, session_id=session.session_id)
        return inst

    async def backfill(self, session: PaperSession, start: datetime, end: datetime) -> None:
        """Replay closed 1m candles in ``[start, end)`` through the session."""
        first = floor_time(start, Timeframe.M1)
        last_closed = floor_time(end, Timeframe.M1) - _M1
        if last_closed < first:
            return
        candles: list[Candle] = []
        cursor = first
        chunk = _M1 * (self.adapter.max_candles_per_request - 1)
        while cursor <= last_closed:
            chunk_end = min(last_closed, cursor + chunk)
            batch = await self.adapter.rest.fetch_candles(session.instrument, Timeframe.M1, cursor, chunk_end)
            candles.extend(c for c in batch if cursor <= c.open_time <= chunk_end)
            cursor = chunk_end + _M1
        log.info("paper_backfill", symbol=session.symbol, candles=len(candles), start=first.isoformat())
        await session.on_candles(candles)

    async def dispatch(self, event: TradeTick | StreamGap) -> None:
        """Route one stream event."""
        session = self.sessions.get(event.symbol)
        if session is None:
            return
        if isinstance(event, TradeTick):
            self.stream_at = self._now()
            gap_end = self._pending_gap.get(event.symbol)
            if gap_end is not None and floor_time(event.ts, Timeframe.M1) > floor_time(gap_end, Timeframe.M1):
                # The reconnect minute has closed: replay it to cover trades before the first live tick.
                del self._pending_gap[event.symbol]
                minute = floor_time(gap_end, Timeframe.M1)
                candles = await self.adapter.rest.fetch_candles(
                    session.instrument, Timeframe.M1, minute, minute
                )
                for candle in candles:
                    if candle.open_time == minute:
                        await session.replay_candle(candle)
            await session.on_trade(event)
        else:
            # Disconnect detection lags the outage: backfill from the last processed event, not gap start.
            await self.backfill(session, session.last_event, event.end)
            self._pending_gap[event.symbol] = event.end

    async def run(self) -> None:
        """Run until cancelled."""
        instruments = await self.setup()
        if not instruments:
            log.error("paper_no_sessions")
            return
        heartbeat = asyncio.create_task(self._heartbeat_loop())
        try:
            async for event in self.adapter.ws.stream_trades(instruments):
                await self.dispatch(event)
        finally:
            heartbeat.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await heartbeat

    async def _heartbeat_loop(self) -> None:
        while True:
            await asyncio.sleep(self.heartbeat_every)
            now = self._now()
            for session in self.sessions.values():
                try:
                    await session.heartbeat(now, self.stream_at)
                except Exception:  # a failed snapshot must not kill the heartbeat
                    log.exception("paper_heartbeat_failed", symbol=session.symbol)
