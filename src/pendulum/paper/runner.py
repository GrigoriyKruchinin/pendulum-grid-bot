"""Paper runner: one live trade stream feeding a paper session per enabled pair."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from pendulum.backtest.engine import GridParams
from pendulum.config.pairs import PairConfig
from pendulum.config.settings import BacktestSettings, GridSettings, RiskSettings
from pendulum.domain.models import Candle, FeeModel, Instrument, StreamGap, Symbol, Timeframe, TradeTick
from pendulum.domain.ports import ITradingSessionStore
from pendulum.exchanges.base import BaseExchangeAdapter
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
        self.sessions: dict[Symbol, PaperSession] = {}

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
            symbol = Symbol.parse(pair.symbol)
            inst = self.adapter.find_instrument(instruments, symbol)
            price = prices.get(symbol)
            if price is None:
                log.warning("paper_no_price", symbol=pair.symbol)
                continue
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
            running = await self.store.get_running(MODE, self.adapter.exchange_id, pair.symbol)
            if running is not None and "trader" in running[1]:
                await session.resume(running[0], running[1], price)
                await self.backfill(session, session.last_event, now)
            else:
                await session.open_new(price, now)
            self.sessions[symbol] = session
            active.append(inst)
            log.info("paper_session_ready", symbol=pair.symbol, session_id=session.session_id)
        return active

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
            await session.on_trade(event)
        else:
            await self.backfill(session, event.start, event.end)

    async def run(self) -> None:
        """Run until cancelled."""
        instruments = await self.setup()
        if not instruments:
            log.error("paper_no_sessions")
            return
        async for event in self.adapter.ws.stream_trades(instruments):
            await self.dispatch(event)
