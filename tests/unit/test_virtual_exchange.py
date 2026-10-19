from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from pendulum.domain.models import (
    Candle,
    FeeModel,
    MarketFilters,
    Order,
    OrderStatus,
    Side,
    Symbol,
    TradeTick,
)
from pendulum.execution.gateway import OrderRejectedError
from pendulum.execution.virtual import VirtualExchange

SOL = Symbol("SOL", "USDT")
F = MarketFilters(Decimal("0.01"), Decimal("0.001"), Decimal("0.001"), Decimal(5))
T0 = datetime(2025, 1, 1, tzinfo=UTC)
M = timedelta(minutes=1)


def _ex(base_fee: bool = True) -> VirtualExchange:
    return VirtualExchange(
        SOL, F, FeeModel(Decimal("0.001"), Decimal("0.002"), base_fee), quote=Decimal(1000)
    )


def _bar(i: int, low: str, high: str) -> Candle:
    lo, hi = Decimal(low), Decimal(high)
    return Candle(T0 + i * M, hi, hi, lo, lo, Decimal(1))


def _order(cid: str, side: Side, price: str, qty: str = "1") -> Order:
    return Order(cid, SOL, side, Decimal(price), Decimal(qty), 0, T0)


def test_touch_is_not_fill_cross_is() -> None:
    ex = _ex()
    ex.place_limit(_order("b", Side.BUY, "100"), T0)
    assert ex.match_candle(_bar(0, "100", "101"), T0 + M) == []
    fills = ex.match_candle(_bar(1, "99.99", "101"), T0 + 2 * M)
    assert len(fills) == 1
    assert fills[0].price == Decimal(100)
    assert fills[0].fee == Decimal("0.001")  # in base
    assert ex.balances.base == Decimal("0.999")
    assert ex.balances.locked_quote == 0
    assert ex.balances.quote == Decimal(900)


def test_order_not_eligible_in_bar_it_was_placed() -> None:
    ex = _ex()
    ex.place_limit(_order("s", Side.BUY, "100"), T0 + 30 * timedelta(seconds=1))
    assert ex.match_candle(_bar(0, "90", "110"), T0 + M) == []
    assert len(ex.match_candle(_bar(1, "90", "110"), T0 + 2 * M)) == 1


def test_quote_fee_mode_and_sell() -> None:
    ex = VirtualExchange(
        SOL, F, FeeModel(Decimal("0.001"), Decimal("0.001"), False), quote=Decimal(0), base=Decimal(2)
    )
    ex.place_limit(_order("s", Side.SELL, "100"), T0)
    fills = ex.match_trade(TradeTick(SOL, T0 + M, Decimal("100.01"), Decimal(1)))
    assert fills[0].fee == Decimal("0.1")
    assert ex.balances.quote == Decimal("99.9")
    assert ex.fees_paid_quote == Decimal("0.1")


def test_rejections_and_cancel_unlocks() -> None:
    ex = _ex()
    with pytest.raises(OrderRejectedError, match="insufficient quote"):
        ex.place_limit(_order("big", Side.BUY, "100", "11"), T0)
    with pytest.raises(OrderRejectedError, match="minimums"):
        ex.place_limit(_order("tiny", Side.BUY, "100", "0.01"), T0)
    o = ex.place_limit(_order("b", Side.BUY, "100"), T0)
    assert ex.balances.quote == Decimal(900)
    ex.cancel("b")
    assert o.status is OrderStatus.CANCELLED
    assert ex.balances.quote == Decimal(1000)
    assert ex.balances.locked_quote == 0


def test_market_slippage_and_taker_fee() -> None:
    ex = _ex()
    fill = ex.market("m", Side.BUY, Decimal(1), Decimal(100), T0)
    assert fill.price == Decimal("100.05")
    assert fill.fee == Decimal("0.002")
    assert not fill.is_maker
    assert ex.equity(Decimal(100)) < Decimal(1000)
