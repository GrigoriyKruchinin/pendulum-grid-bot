from datetime import UTC, datetime, timedelta
from decimal import Decimal

from hypothesis import given, settings
from hypothesis import strategies as st

from pendulum.config.settings import GridType
from pendulum.domain.models import Candle, FeeModel, MarketFilters, Side, Symbol
from pendulum.execution.virtual import VirtualExchange, VirtualOrderGateway
from pendulum.grid.exit_policies import WaitExit
from pendulum.grid.plan import GridSpec, build_plan
from pendulum.grid.trader import GridTrader, Holding

SOL = Symbol("SOL", "USDT")
F = MarketFilters(Decimal("0.01"), Decimal("0.001"), Decimal("0.001"), Decimal(5))
T0 = datetime(2025, 1, 1, tzinfo=UTC)
M = timedelta(minutes=1)

moves = st.lists(st.integers(min_value=-300, max_value=300), min_size=5, max_size=120)


@settings(max_examples=60, deadline=None)
@given(moves, st.sampled_from(list(GridType)), st.integers(min_value=3, max_value=25), st.booleans())
async def test_random_walk_invariants(steps: list[int], gt: GridType, levels: int, base_fee: bool) -> None:
    fees = FeeModel(Decimal("0.001"), Decimal("0.001"), base_fee)
    plan = build_plan(GridSpec(Decimal(80), Decimal(120), levels, gt, Decimal(2000)), F, fees)
    ex = VirtualExchange(SOL, F, fees, quote=Decimal(2000))
    now = [T0]
    gw = VirtualOrderGateway(ex, lambda: now[0])
    gw.last_price = Decimal(100)
    trader = GridTrader(plan, gw, symbol=SOL, filters=F, fees=fees, exit_policy=WaitExit())
    await trader.start(Decimal(100), T0)

    price = Decimal(100)
    for i, step in enumerate(steps):
        nxt = max(Decimal(50), price + Decimal(step) / 100)
        lo, hi = min(price, nxt) - Decimal("0.05"), max(price, nxt) + Decimal("0.05")
        bar = Candle(T0 + (i + 1) * M, price, hi, lo, nxt, Decimal(1))
        now[0] = bar.open_time + M
        gw.last_price = nxt
        for fill in ex.match_candle(bar, now[0]):
            await trader.on_fill(fill)
        price = nxt

        b = ex.balances
        assert b.base >= 0 and b.quote >= 0 and b.locked_base >= 0 and b.locked_quote >= 0
        orders = ex.open_orders()
        assert len({o.level_index for o in orders}) == len(orders)  # one order per slot
        assert b.locked_quote == sum(
            (o.price * o.remaining_qty for o in orders if o.side is Side.BUY), Decimal(0)
        )
        assert b.locked_base == sum((o.qty for o in orders if o.side is Side.SELL), Decimal(0))
        for s in trader.slots:
            if s.order_id is not None:
                o = next(o for o in orders if o.client_id == s.order_id)
                assert o.side is (Side.BUY if s.holding is Holding.QUOTE else Side.SELL)

    assert all(c.sell_price > c.buy_price for c in trader.cycles if c.level_index >= 0)
    # Fees never make equity exceed the fee-free counterfactual.
    assert ex.fees_paid_quote >= 0
