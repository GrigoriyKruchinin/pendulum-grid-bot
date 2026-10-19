from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from pendulum.domain.models import (
    Candle,
    Cycle,
    EquityPoint,
    Fill,
    Instrument,
    MarketFilters,
    Order,
    OrderStatus,
    Side,
    Symbol,
    Timeframe,
)
from pendulum.domain.ports import Checkpoint, Gap
from pendulum.storage.db import Database
from pendulum.storage.repositories import (
    BacktestRunRepository,
    CandleRepository,
    InstrumentRepository,
    ScannerRepository,
    SessionReadRepository,
    TradingSessionRepository,
)

pytestmark = pytest.mark.integration
T0 = datetime(2025, 1, 1, tzinfo=UTC)
M = timedelta(minutes=1)


def _candle(i: int, close: str = "100") -> Candle:
    c = Decimal(close)
    return Candle(T0 + i * M, c, c + 1, c - 1, c, Decimal("1.5"), Decimal("150.123456789"))


async def test_candles_upsert_read_and_gaps(db: Database) -> None:
    repo = CandleRepository(db)
    present = [i for i in range(10) if i not in (3, 4, 7)]
    await repo.upsert_candles("bybit", "SOL/USDT", Timeframe.M1, [_candle(i) for i in present])
    # Revised last bar is updated, not duplicated.
    await repo.upsert_candles("bybit", "SOL/USDT", Timeframe.M1, [_candle(9, "101")])
    got = await repo.get_candles("bybit", "SOL/USDT", Timeframe.M1, T0, T0 + 10 * M)
    assert len(got) == 7
    assert got[-1].close == Decimal(101)
    assert got[0].turnover == Decimal("150.123456789")

    gaps = await repo.find_gaps("bybit", "SOL/USDT", Timeframe.M1, T0 - 2 * M, T0 + 12 * M)
    assert gaps == [
        Gap(T0 - 2 * M, T0 - M),
        Gap(T0 + 3 * M, T0 + 4 * M),
        Gap(T0 + 7 * M, T0 + 7 * M),
        Gap(T0 + 10 * M, T0 + 11 * M),
    ]
    empty = await repo.find_gaps("bybit", "BTC/USDT", Timeframe.M1, T0, T0 + 5 * M)
    assert empty == [Gap(T0, T0 + 4 * M)]


async def test_checkpoint_roundtrip(db: Database) -> None:
    repo = CandleRepository(db)
    assert await repo.get_checkpoint("bybit", "SOL/USDT", Timeframe.M1) is None
    await repo.save_checkpoint("bybit", "SOL/USDT", Timeframe.M1, Checkpoint(T0, T0 + M))
    await repo.save_checkpoint("bybit", "SOL/USDT", Timeframe.M1, Checkpoint(T0, T0 + 5 * M))
    assert await repo.get_checkpoint("bybit", "SOL/USDT", Timeframe.M1) == Checkpoint(T0, T0 + 5 * M)


async def test_instruments(db: Database) -> None:
    repo = InstrumentRepository(db)
    inst = Instrument(
        Symbol("SOL", "USDT"),
        "SOLUSDT",
        MarketFilters(Decimal("0.01"), Decimal("0.001"), Decimal("0.001"), Decimal(5)),
    )
    await repo.upsert_instruments("bybit", [inst])
    await repo.upsert_instruments("bybit", [inst])
    assert await repo.get_instrument("bybit", "SOL/USDT") == inst
    assert await repo.get_instrument("bybit", "NOPE/USDT") is None


async def test_backtest_and_scanner(db: Database) -> None:
    run_id = await BacktestRunRepository(db).save_run(
        kind="single",
        git_sha="abc",
        exchange="bybit",
        symbol="SOL/USDT",
        timeframe=Timeframe.M1,
        period_start=T0,
        period_end=T0 + M,
        params={"levels": 20},
        metrics={"net_return": "0.01"},
        assumptions={"fill": "cross"},
    )
    assert run_id == 1
    await ScannerRepository(db).save_scores(T0, "bybit", [("SOL/USDT", Decimal("0.9"), {"vol": "0.03"})])


async def test_trading_session_flow(db: Database) -> None:
    repo = TradingSessionRepository(db)
    sid = await repo.create_session(mode="paper", exchange="bybit", symbol="SOL/USDT", params={"levels": 10})
    order = Order("pdl-1-b-3", Symbol("SOL", "USDT"), Side.BUY, Decimal(100), Decimal(1), 3, T0)
    await repo.upsert_order(sid, order)
    order.status = OrderStatus.FILLED
    order.filled_qty = Decimal(1)
    await repo.upsert_order(sid, order)
    await repo.add_fill(
        sid, Fill("pdl-1-b-3", Side.BUY, Decimal(100), Decimal(1), Decimal("0.1"), "USDT", T0)
    )
    await repo.add_cycle(sid, Cycle(3, Decimal(100), Decimal(102), Decimal(1), Decimal("0.2"), T0, T0 + M))
    point = EquityPoint(T0, Decimal(50), Decimal("0.5"), Decimal(100))
    await repo.add_equity_point(sid, point)
    await repo.add_equity_point(sid, point)
    await repo.save_state(sid, {"orders": 1})
    assert await repo.get_running("paper", "bybit", "SOL/USDT") == (sid, {"orders": 1})
    await repo.save_state(sid, {"orders": 0}, status="stopped")
    assert await repo.get_running("paper", "bybit", "SOL/USDT") is None


async def test_session_reader(db: Database) -> None:
    repo = TradingSessionRepository(db)
    sid = await repo.create_session(mode="paper", exchange="bybit", symbol="SOL/USDT", params={"levels": 10})
    await repo.add_cycle(sid, Cycle(1, Decimal(100), Decimal(102), Decimal(1), Decimal("0.2"), T0, T0 + M))
    await repo.add_cycle(
        sid, Cycle(1, Decimal(100), Decimal(102), Decimal(1), Decimal("0.2"), T0, T0 + 10 * M)
    )
    reader = SessionReadRepository(db)
    sessions = await reader.list_sessions("paper", ["running"])
    assert [s.id for s in sessions] == [sid]
    assert await reader.cycle_stats(sid, T0 + 5 * M) == (1, Decimal("1.8"))
    assert await reader.list_sessions("live", ["running"]) == []
