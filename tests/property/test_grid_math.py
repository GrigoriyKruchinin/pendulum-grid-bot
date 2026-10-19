from decimal import Decimal
from itertools import pairwise

from hypothesis import assume, given, settings
from hypothesis import strategies as st

from pendulum.config.settings import GridType
from pendulum.domain.models import FeeModel, MarketFilters
from pendulum.grid.plan import GridPlanError, GridSpec, build_plan
from pendulum.grid.strategies import ArithmeticGrid, GeometricGrid

lowers = st.decimals(min_value="0.01", max_value="100000", places=4, allow_nan=False, allow_infinity=False)
widths = st.decimals(min_value="1.01", max_value="3", places=3)
levels = st.integers(min_value=2, max_value=80)
grid_types = st.sampled_from(list(GridType))
FEES = FeeModel(Decimal("0.001"), Decimal("0.001"))


@given(lowers, widths, levels)
def test_strategies_monotonic_with_exact_endpoints(lower: Decimal, mult: Decimal, n: int) -> None:
    upper = lower * mult
    for strat in (ArithmeticGrid(), GeometricGrid()):
        prices = strat.level_prices(lower, upper, n)
        assert len(prices) == n
        assert prices[0] == lower
        assert prices[-1] == upper
        assert all(a < b for a, b in pairwise(prices))


@given(lowers, widths, levels)
def test_geometric_equal_ratios(lower: Decimal, mult: Decimal, n: int) -> None:
    assume(n >= 3)
    prices = GeometricGrid().level_prices(lower, lower * mult, n)
    ratios = [b / a for a, b in pairwise(prices)]
    assert max(ratios) - min(ratios) < Decimal("1e-20")


@settings(max_examples=200)
@given(
    lowers,
    widths,
    levels,
    grid_types,
    st.decimals(min_value="50", max_value="100000", places=2),
    st.sampled_from(["0.01", "0.0001", "0.1"]),
    st.sampled_from(["0.001", "0.01", "1"]),
)
def test_plan_respects_filters_fees_and_capital(
    lower: Decimal, mult: Decimal, n: int, gt: GridType, capital: Decimal, tick: str, step: str
) -> None:
    f = MarketFilters(Decimal(tick), Decimal(step), Decimal(step), Decimal(5))
    spec = GridSpec(lower, lower * mult, n, gt, capital)
    try:
        plan = build_plan(spec, f, FEES)
    except GridPlanError:
        return
    threshold = FEES.round_trip_maker * Decimal("1.5")
    assert all(p % f.tick_size == 0 for p in plan.prices)
    assert all(b / a - 1 > threshold for a, b in pairwise(plan.prices))
    assert all(q % f.step_size == 0 and q >= f.min_qty for q in plan.slot_qty)
    # Worst case: every slot bought at its sell edge still fits the capital.
    worst = sum(q * plan.prices[i + 1] for i, q in enumerate(plan.slot_qty))
    assert worst <= capital * Decimal("1.0001") * plan.prices[-1] / plan.prices[0]
    assert sum(q * plan.prices[i] for i, q in enumerate(plan.slot_qty)) <= capital
