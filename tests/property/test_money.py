from decimal import Decimal

import pytest
from hypothesis import given
from hypothesis import strategies as st

from pendulum.domain.models import MarketFilters, Side
from pendulum.domain.money import ceil_to_step, floor_to_step, to_decimal

prices = st.decimals(
    min_value="0.00000001", max_value="1000000", places=8, allow_nan=False, allow_infinity=False
)
steps = st.sampled_from(
    [Decimal(s) for s in ("0.00000001", "0.0001", "0.001", "0.01", "0.1", "1", "0.5", "0.25")]
)


@given(prices, steps)
def test_floor_is_multiple_and_not_above(value: Decimal, step: Decimal) -> None:
    r = floor_to_step(value, step)
    assert r <= value
    assert value - r < step
    assert (r / step) == (r / step).to_integral_value()


@given(prices, steps)
def test_ceil_is_multiple_and_not_below(value: Decimal, step: Decimal) -> None:
    r = ceil_to_step(value, step)
    assert r >= value
    assert r - value < step
    assert (r / step) == (r / step).to_integral_value()


@given(prices, steps, steps)
def test_filters_rounding_is_conservative(price: Decimal, tick: Decimal, step: Decimal) -> None:
    f = MarketFilters(tick_size=tick, step_size=step, min_qty=Decimal(0), min_notional=Decimal(0))
    assert f.round_price(price, Side.BUY) <= price
    assert f.round_price(price, Side.SELL) >= price
    assert f.round_qty(price) <= price


def test_to_decimal_rejects_float() -> None:
    with pytest.raises(TypeError):
        to_decimal(0.1)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="non-finite"):
        to_decimal("NaN")
    assert to_decimal("0.1") == Decimal("0.1")
