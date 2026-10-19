from datetime import UTC, datetime
from decimal import Decimal

import pytest

from pendulum.domain.models import Candle, Cycle, MarketFilters, Side, Symbol, Ticker

T0 = datetime(2025, 1, 1, tzinfo=UTC)


def test_symbol_parse() -> None:
    assert str(Symbol.parse("sol/usdt")) == "SOL/USDT"
    with pytest.raises(ValueError, match="BASE/QUOTE"):
        Symbol.parse("SOLUSDT")


def test_candle_validation() -> None:
    with pytest.raises(ValueError, match="timezone"):
        Candle(datetime(2025, 1, 1), Decimal(1), Decimal(2), Decimal(1), Decimal(1), Decimal(0))  # noqa: DTZ001
    with pytest.raises(ValueError, match="inconsistent"):
        Candle(T0, Decimal(1), Decimal(1), Decimal(2), Decimal(1), Decimal(0))


def test_filters_min_notional() -> None:
    f = MarketFilters(Decimal("0.01"), Decimal("0.001"), Decimal("0.001"), Decimal(5))
    assert not f.is_valid_order(Decimal(100), Decimal("0.04"))
    assert f.is_valid_order(Decimal(100), Decimal("0.05"))
    assert Side.BUY.opposite is Side.SELL


def test_cycle_profit() -> None:
    c = Cycle(0, Decimal(100), Decimal(102), Decimal(1), Decimal("0.202"), T0, T0)
    assert c.gross_profit == Decimal(2)
    assert c.net_profit == Decimal("1.798")


def test_spread_bps() -> None:
    t = Ticker(Symbol("BTC", "USDT"), Decimal("99.99"), Decimal("100.01"), Decimal(100), Decimal(0), T0)
    assert t.spread_bps == Decimal(2)
