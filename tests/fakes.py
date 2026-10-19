"""In-memory fakes for storage ports and exchange adapters."""

from collections.abc import AsyncIterator, Sequence
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

from pendulum.domain.models import (
    Candle,
    Instrument,
    MarketFilters,
    StreamGap,
    Symbol,
    Ticker,
    Timeframe,
    TradeTick,
)
from pendulum.domain.ports import Checkpoint, Gap
from pendulum.exchanges.base import (
    BaseExchangeAdapter,
    FeeRate,
    IAccountData,
    IRestMarketData,
    IWebSocketTicker,
)

FILTERS = MarketFilters(Decimal("0.01"), Decimal("0.001"), Decimal("0.001"), Decimal(5))
SOL = Instrument(Symbol("SOL", "USDT"), "SOLUSDT", FILTERS)


def flat_candle(ts: datetime, price: str = "100") -> Candle:
    p = Decimal(price)
    return Candle(ts, p, p, p, p, Decimal(1))


class InMemoryCandleStore:
    def __init__(self) -> None:
        self.data: dict[tuple[str, str, Timeframe], dict[datetime, Candle]] = {}
        self.checkpoints: dict[tuple[str, str, Timeframe], Checkpoint] = {}

    async def upsert_candles(
        self, exchange: str, symbol: str, timeframe: Timeframe, candles: Sequence[Candle]
    ) -> int:
        series = self.data.setdefault((exchange, symbol, timeframe), {})
        for c in candles:
            series[c.open_time] = c
        return len(candles)

    async def get_candles(
        self, exchange: str, symbol: str, timeframe: Timeframe, start: datetime, end: datetime
    ) -> list[Candle]:
        series = self.data.get((exchange, symbol, timeframe), {})
        return [series[t] for t in sorted(series) if start <= t < end]

    async def find_gaps(
        self, exchange: str, symbol: str, timeframe: Timeframe, start: datetime, end: datetime
    ) -> list[Gap]:
        series = self.data.get((exchange, symbol, timeframe), {})
        step = timedelta(seconds=timeframe.seconds)
        gaps: list[Gap] = []
        t = start
        gap_start: datetime | None = None
        while t < end:
            if t not in series and gap_start is None:
                gap_start = t
            if t in series and gap_start is not None:
                gaps.append(Gap(gap_start, t - step))
                gap_start = None
            t += step
        if gap_start is not None:
            gaps.append(Gap(gap_start, end - step))
        return gaps

    async def get_checkpoint(self, exchange: str, symbol: str, timeframe: Timeframe) -> Checkpoint | None:
        return self.checkpoints.get((exchange, symbol, timeframe))

    async def save_checkpoint(
        self, exchange: str, symbol: str, timeframe: Timeframe, checkpoint: Checkpoint
    ) -> None:
        self.checkpoints[(exchange, symbol, timeframe)] = checkpoint


class InMemoryInstrumentStore:
    def __init__(self) -> None:
        self.data: dict[tuple[str, str], Instrument] = {}

    async def upsert_instruments(self, exchange: str, instruments: Sequence[Instrument]) -> None:
        for i in instruments:
            self.data[(exchange, str(i.symbol))] = i

    async def get_instrument(self, exchange: str, symbol: str) -> Instrument | None:
        return self.data.get((exchange, symbol))


class FakeRest:
    """Serves flat candles for every slot except ``holes``."""

    def __init__(self, timeframe: Timeframe, holes: set[datetime] | None = None, limit: int = 5) -> None:
        self.timeframe = timeframe
        self.holes = holes or set()
        self.limit = limit
        self.calls: list[tuple[datetime, datetime]] = []
        self.tickers: list[Ticker] = []
        self.instruments: list[Instrument] = [SOL]

    async def fetch_instruments(self) -> list[Instrument]:
        return self.instruments

    async def fetch_candles(
        self, instrument: Instrument, timeframe: Timeframe, start: datetime, end: datetime
    ) -> list[Candle]:
        self.calls.append((start, end))
        step = timedelta(seconds=timeframe.seconds)
        out: list[Candle] = []
        t = start
        while t <= end and len(out) < self.limit:
            if t not in self.holes:
                out.append(flat_candle(t))
            t += step
        return out

    async def fetch_tickers(self) -> list[Ticker]:
        return self.tickers


class FakeWs:
    def __init__(self, events: list[TradeTick | StreamGap] | None = None) -> None:
        self.events = events or []

    async def stream_trades(self, instruments: Sequence[Instrument]) -> AsyncIterator[TradeTick | StreamGap]:
        for e in self.events:
            yield e


class FakeAdapter(BaseExchangeAdapter):
    exchange_id = "fake"
    supported_timeframes = frozenset({Timeframe.M1, Timeframe.M5})

    def __init__(self, rest: FakeRest | None = None, ws: FakeWs | None = None) -> None:
        self._rest = rest or FakeRest(Timeframe.M1)
        self._ws = ws or FakeWs()
        self.max_candles_per_request = self._rest.limit

    @property
    def rest(self) -> IRestMarketData:
        return self._rest

    @property
    def ws(self) -> IWebSocketTicker:
        return self._ws

    @property
    def account(self) -> IAccountData | None:
        return None

    def default_fee_rate(self) -> FeeRate:
        return FeeRate(Decimal("0.001"), Decimal("0.001"))

    async def aclose(self) -> None:
        return None


class InMemoryRunStore:
    def __init__(self) -> None:
        self.runs: list[dict[str, object]] = []

    async def save_run(self, **kwargs: object) -> int:
        self.runs.append(kwargs)
        return len(self.runs)


class InMemoryScannerStore:
    def __init__(self) -> None:
        self.saved: list[tuple[str, Decimal, dict[str, Any]]] = []

    async def save_scores(
        self, run_at: datetime, exchange: str, rows: Sequence[tuple[str, Decimal, dict[str, Any]]]
    ) -> None:
        self.saved = list(rows)


class InMemorySessionStore:
    def __init__(self) -> None:
        self.sessions: dict[int, dict[str, Any]] = {}
        self.orders: dict[str, Any] = {}
        self.fills: list[Any] = []
        self.cycles: list[Any] = []
        self.equity: list[Any] = []

    async def create_session(self, *, mode: str, exchange: str, symbol: str, params: dict[str, Any]) -> int:
        sid = len(self.sessions) + 1
        self.sessions[sid] = {
            "mode": mode,
            "exchange": exchange,
            "symbol": symbol,
            "params": params,
            "status": "running",
            "state": {},
        }
        return sid

    async def save_state(self, session_id: int, state: dict[str, Any], status: str | None = None) -> None:
        self.sessions[session_id]["state"] = state
        if status is not None:
            self.sessions[session_id]["status"] = status

    async def get_latest(
        self, mode: str, exchange: str, symbol: str
    ) -> tuple[int, str, dict[str, Any]] | None:
        for sid in sorted(self.sessions, reverse=True):
            s = self.sessions[sid]
            if (s["mode"], s["exchange"], s["symbol"]) == (mode, exchange, symbol):
                return sid, s["status"], s["state"]
        return None

    async def prune_after(self, session_id: int, ts: datetime) -> None:
        self.fills = [f for f in self.fills if f.ts <= ts]
        self.cycles = [c for c in self.cycles if c.closed_at <= ts]
        self.pruned_at = ts

    async def upsert_order(self, session_id: int, order: Any) -> None:
        self.orders[order.client_id] = (order.status, order.filled_qty)

    async def add_fill(self, session_id: int, fill: Any) -> bool:
        if any(f.client_id == fill.client_id for f in self.fills):
            return False
        self.fills.append(fill)
        return True

    async def add_cycle(self, session_id: int, cycle: Any) -> None:
        if any((c.level_index, c.closed_at) == (cycle.level_index, cycle.closed_at) for c in self.cycles):
            return
        self.cycles.append(cycle)

    async def add_equity_point(self, session_id: int, point: Any) -> None:
        self.equity.append(point)
