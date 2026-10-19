from datetime import timedelta
from decimal import Decimal

from pendulum.backtest.engine import GridParams
from pendulum.config.pairs import PairConfig
from pendulum.config.settings import BacktestSettings, GridSettings, GridType, RiskSettings
from pendulum.domain.models import FeeModel, OrderStatus, StreamGap, Symbol, Ticker, Timeframe, TradeTick
from pendulum.notifications.telegram import NullNotifier
from pendulum.paper.runner import PaperRunner, params_for
from pendulum.paper.session import PaperSession
from tests.fakes import SOL, FakeAdapter, FakeRest, FakeWs, InMemorySessionStore
from tests.synthetic import T0, series

PARAMS = GridParams(5, Decimal(20), GridType.ARITHMETIC, "wait")
S = timedelta(seconds=1)


def _session(store: InMemorySessionStore, notifier: NullNotifier) -> PaperSession:
    return PaperSession(
        exchange_id="fake",
        instrument=SOL,
        params=PARAMS,
        capital=Decimal(1000),
        fees=FeeModel(Decimal("0.001"), Decimal("0.001")),
        slippage_bps=Decimal(0),
        min_step_fee_multiplier=Decimal("1.5"),
        store=store,
        notifier=notifier,
        now=T0,
    )


def _tick(sec: int, price: str) -> TradeTick:
    return TradeTick(SOL.symbol, T0 + sec * S, Decimal(price), Decimal(1))


async def test_session_trades_persists_and_notifies() -> None:
    store, notifier = InMemorySessionStore(), NullNotifier()
    s = _session(store, notifier)
    await s.open_new(Decimal(100), T0)
    assert "Paper grid started" in notifier.sent[0]
    assert len(store.fills) == 1  # initial market buy for slots above the price
    assert any(status is OrderStatus.OPEN for status, _ in store.orders.values())

    await s.on_trade(_tick(1, "94.9"))  # crosses the 95 buy
    await s.on_trade(_tick(2, "100.1"))  # crosses the 100 sell of that slot
    assert len(store.cycles) == 1
    assert store.cycles[0].net_profit > 0
    assert sum(1 for status, _ in store.orders.values() if status is OrderStatus.FILLED) == 2

    await s.on_trade(_tick(120, "130"))  # out of range
    assert any("Range exit" in m for m in notifier.sent)
    assert store.equity
    assert store.sessions[1]["status"] == "running"


async def test_resume_restores_identical_state() -> None:
    store, notifier = InMemorySessionStore(), NullNotifier()
    s = _session(store, notifier)
    await s.open_new(Decimal(100), T0)
    await s.on_trade(_tick(1, "94.9"))
    await s.persist(T0 + 2 * S, force=True)
    state = store.sessions[1]["state"]

    s2 = _session(store, notifier)
    await s2.resume(1, state, Decimal(96))
    assert s2.exchange.balances == s.exchange.balances
    assert sorted(o.client_id for o in s2.exchange.open_orders()) == sorted(
        o.client_id for o in s.exchange.open_orders()
    )
    assert [x.holding for x in s2.trader.slots] == [x.holding for x in s.trader.slots]  # type: ignore[union-attr]
    # The resumed session keeps trading: the slot bought at 95 sells at 100.
    await s2.on_trade(_tick(3, "100.1"))
    assert len(store.cycles) == 1
    assert "resumed" in notifier.sent[-1]


async def test_candle_backfill_fills_missed_crossings() -> None:
    store, notifier = InMemorySessionStore(), NullNotifier()
    s = _session(store, notifier)
    await s.open_new(Decimal(100), T0)
    candles = series([100, 94, 100.5], minutes=1)  # dip below 95 then back above 100
    await s.on_candles(candles, Timeframe.M1)
    assert len(store.cycles) == 1


def test_params_for_overrides() -> None:
    grid = GridSettings(_env_file=None)
    p = params_for(PairConfig(symbol="SOL/USDT", levels=30), grid)
    assert p.levels == 30
    assert p.width_pct == grid.default_width_pct
    assert p.grid_type is GridType.GEOMETRIC


async def test_runner_opens_sessions_and_backfills_gaps() -> None:
    rest = FakeRest(Timeframe.M1, limit=1000)
    rest.tickers = [
        Ticker(Symbol("SOL", "USDT"), Decimal("99.9"), Decimal("100.1"), Decimal(100), Decimal(1), T0)
    ]
    events: list[TradeTick | StreamGap] = [
        _tick(1, "94.9"),
        StreamGap(SOL.symbol, T0 + 2 * S, T0 + timedelta(minutes=5)),
        _tick(400, "100.2"),
    ]
    store, notifier = InMemorySessionStore(), NullNotifier()
    runner = PaperRunner(
        adapter=FakeAdapter(rest, FakeWs(events)),
        store=store,
        notifier=notifier,
        pairs=[
            PairConfig(
                symbol="SOL/USDT", levels=5, range_width_pct=Decimal(20), grid_type=GridType.ARITHMETIC
            )
        ],
        grid=GridSettings(_env_file=None, default_exit_policy="wait"),
        risk=RiskSettings(_env_file=None),
        backtest=BacktestSettings(_env_file=None),
        now=lambda: T0,
    )
    await runner.run()
    assert SOL.symbol in runner.sessions
    assert rest.calls  # gap backfill requested candles
    assert len(store.cycles) >= 1

    # A restart resumes the running session instead of opening a new one.
    runner2 = PaperRunner(
        adapter=FakeAdapter(rest, FakeWs([])),
        store=store,
        notifier=notifier,
        pairs=runner.pairs,
        grid=runner.grid,
        risk=runner.risk,
        backtest=runner.backtest,
        now=lambda: T0 + timedelta(minutes=10),
    )
    await runner2.setup()
    assert len(store.sessions) == 1
