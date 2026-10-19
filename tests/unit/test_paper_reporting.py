from collections.abc import Sequence
from datetime import datetime, timedelta
from decimal import Decimal

from pendulum.backtest.engine import GridParams
from pendulum.config.settings import GridType
from pendulum.domain.models import FeeModel, Timeframe, TradeTick
from pendulum.domain.ports import SessionInfo
from pendulum.market_data.loader import HistoryLoader
from pendulum.notifications.telegram import NullNotifier
from pendulum.paper.reporting import PaperComparison, build_summaries, compare_session, comparison_lines
from pendulum.paper.session import PaperSession
from tests.fakes import (
    SOL,
    FakeAdapter,
    FakeRest,
    InMemoryCandleStore,
    InMemoryInstrumentStore,
    InMemorySessionStore,
)
from tests.synthetic import T0


class Reader:
    def __init__(self, store: InMemorySessionStore) -> None:
        self.store = store

    async def list_sessions(self, mode: str, statuses: Sequence[str]) -> list[SessionInfo]:
        return [
            SessionInfo(sid, s["mode"], s["exchange"], s["symbol"], s["status"], s["params"], s["state"], T0)
            for sid, s in self.store.sessions.items()
            if s["mode"] == mode and s["status"] in statuses
        ]

    async def cycle_stats(self, session_id: int, since: datetime) -> tuple[int, Decimal]:
        cs = [c for c in self.store.cycles if c.closed_at >= since]
        return len(cs), sum((c.net_profit for c in cs), Decimal(0))


async def _paper_session() -> InMemorySessionStore:
    store = InMemorySessionStore()
    s = PaperSession(
        exchange_id="fake",
        instrument=SOL,
        params=GridParams(5, Decimal(20), GridType.ARITHMETIC, "wait"),
        capital=Decimal(1000),
        fees=FeeModel(Decimal("0.001"), Decimal("0.001")),
        slippage_bps=Decimal(0),
        min_step_fee_multiplier=Decimal("1.5"),
        store=store,
        notifier=NullNotifier(),
        now=T0,
    )
    await s.open_new(Decimal(100), T0)
    for sec, price in ((61, "94.9"), (122, "100.1")):
        await s.on_trade(TradeTick(SOL.symbol, T0 + timedelta(seconds=sec), Decimal(price), Decimal(1)))
    await s.persist(T0 + timedelta(minutes=10), force=True)
    return store


async def test_build_summaries() -> None:
    store = await _paper_session()
    rows = await build_summaries(Reader(store), timedelta(days=1), T0 + timedelta(hours=1))
    assert len(rows) == 1
    r = rows[0]
    assert r.cycles == 1
    assert r.cycles_period == 1
    assert r.realized > 0
    assert r.lower < r.upper


async def test_compare_session_with_backtest() -> None:
    store = await _paper_session()
    info = (await Reader(store).list_sessions("paper", ["running"]))[0]
    candles = InMemoryCandleStore()
    adapter = FakeAdapter(FakeRest(Timeframe.M1, limit=1000))
    loader = HistoryLoader(adapter, candles, InMemoryInstrumentStore(), now=lambda: T0 + timedelta(hours=1))
    c = await compare_session(info, candles, loader, adapter)
    assert c.error is None
    assert c.paper_cycles == 1
    # Flat fake candles never cross a level: the backtest has no cycles, so divergence is visible.
    assert c.backtest_cycles == 0
    assert c.divergence > 0


def test_divergence_floor_and_lines() -> None:
    c = PaperComparison(
        "SOL/USDT", 1, T0, T0 + timedelta(days=14), Decimal(1000), Decimal(11), Decimal(10), 5, 4
    )
    assert c.divergence == Decimal("0.1")
    tiny = PaperComparison("SOL/USDT", 1, T0, T0, Decimal(1000), Decimal("0.5"), Decimal("0.1"), 1, 1)
    assert tiny.divergence == Decimal("0.04")  # floored at 1% of capital
    lines = comparison_lines([c], Decimal("0.2"))
    assert "✅" in lines[-1]
