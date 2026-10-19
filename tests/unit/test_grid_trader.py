from datetime import UTC, datetime, timedelta
from decimal import Decimal

from pendulum.config.settings import GridType
from pendulum.domain.models import Candle, FeeModel, MarketFilters, Side, Symbol
from pendulum.execution.virtual import VirtualExchange, VirtualOrderGateway
from pendulum.grid.exit_policies import WaitExit
from pendulum.grid.plan import GridSpec, build_plan
from pendulum.grid.trader import GridTrader, Holding

SOL = Symbol("SOL", "USDT")
F = MarketFilters(Decimal("0.01"), Decimal("0.001"), Decimal("0.001"), Decimal(5))
FEES = FeeModel(Decimal("0.001"), Decimal("0.001"))
T0 = datetime(2025, 1, 1, tzinfo=UTC)
M = timedelta(minutes=1)


class Clock:
    def __init__(self) -> None:
        self.now = T0

    def __call__(self) -> datetime:
        return self.now


async def _setup(capital: str = "1000") -> tuple[GridTrader, VirtualExchange, VirtualOrderGateway, Clock]:
    plan = build_plan(GridSpec(Decimal(90), Decimal(110), 5, GridType.ARITHMETIC, Decimal(capital)), F, FEES)
    ex = VirtualExchange(SOL, F, FEES, quote=Decimal(capital), slippage_bps=Decimal(0))
    clock = Clock()
    gw = VirtualOrderGateway(ex, clock)
    gw.last_price = Decimal(100)
    trader = GridTrader(plan, gw, symbol=SOL, filters=F, fees=FEES, exit_policy=WaitExit())
    await trader.start(Decimal(100), T0)
    return trader, ex, gw, clock


async def test_start_places_buys_below_and_sells_above() -> None:
    trader, ex, _, _ = await _setup()
    assert [s.holding for s in trader.slots] == [Holding.QUOTE, Holding.QUOTE, Holding.BASE, Holding.BASE]
    orders = sorted(ex.open_orders(), key=lambda o: o.price)
    assert [(o.side, o.price) for o in orders] == [
        (Side.BUY, Decimal(90)),
        (Side.BUY, Decimal(95)),
        (Side.SELL, Decimal(105)),
        (Side.SELL, Decimal(110)),
    ]
    assert ex.balances.base >= 0
    assert ex.balances.quote >= 0


async def test_full_cycle_and_equity_identity() -> None:
    trader, ex, gw, clock = await _setup()
    start_equity = ex.equity(Decimal(100))
    # Dip through 95 (buy), then rally through 100 (sell of that slot).
    for i, (lo, hi) in enumerate([("94", "100"), ("96", "100.5"), ("99", "101")]):
        bar = Candle(T0 + (i + 1) * M, Decimal(hi), Decimal(hi), Decimal(lo), Decimal(lo), Decimal(1))
        clock.now = bar.open_time + M
        gw.last_price = bar.close
        for fill in ex.match_candle(bar, clock.now):
            await trader.on_fill(fill)
    assert len(trader.cycles) == 1
    c = trader.cycles[0]
    assert (c.buy_price, c.sell_price) == (Decimal(95), Decimal(100))
    assert c.net_profit > 0
    assert c.net_profit < c.gross_profit
    # Equity change = realized + unrealized; with price back at 99 the base slots are under water.
    pnl = ex.equity(Decimal(99)) - start_equity
    assert trader.realized_profit > 0
    assert pnl != trader.realized_profit  # unrealized part is non-zero and must be reported separately


async def test_stop_liquidates_everything() -> None:
    trader, ex, _, _ = await _setup()
    fill = await trader.stop(T0 + M, liquidate=True)
    assert fill is not None
    assert ex.open_orders() == []
    assert ex.balances.base < F.step_size
    assert ex.balances.locked_quote == 0


async def test_rebuild_rebalances_inventory() -> None:
    trader, ex, gw, _ = await _setup()
    gw.last_price = Decimal(130)
    new_plan = build_plan(
        GridSpec(
            Decimal(117), Decimal(143), 5, GridType.ARITHMETIC, ex.equity(Decimal(130)) * Decimal("0.98")
        ),
        F,
        FEES,
    )
    await trader.rebuild(new_plan, Decimal(130), T0 + M)
    assert trader.rebuilds == 1
    sides = sorted((o.price, o.side) for o in ex.open_orders())
    assert all(side is Side.BUY for price, side in sides if price < 130)
    assert all(side is Side.SELL for price, side in sides if price > 130)
    assert ex.balances.quote >= 0


async def test_buy_shrinks_to_affordable_quote() -> None:
    trader, ex, _, _ = await _setup()
    await trader.cancel_all()
    ex.balances.quote -= Decimal(5)  # simulate quote lost to rebalancing costs
    for slot in trader.slots:
        await trader._place(slot, T0)
    assert all(s.order_id is not None for s in trader.slots)
    assert ex.balances.quote >= 0
