import time
from decimal import Decimal

from pendulum.backtest.engine import GridParams, SimulationConfig, run_backtest
from pendulum.backtest.resample import count_missing, resample
from pendulum.config.settings import GridType
from pendulum.domain.models import FeeModel, Timeframe
from tests.fakes import SOL
from tests.synthetic import series, sideways, trend

CFG = SimulationConfig(capital=Decimal(1000), fees=FeeModel(Decimal("0.001"), Decimal("0.001")))
PARAMS = GridParams(levels=15, width_pct=Decimal(16), grid_type=GridType.GEOMETRIC, exit_policy="wait")


async def test_sideways_market_makes_cycles_and_beats_flat_hold() -> None:
    candles = sideways(288 * 10)  # 10 days of 5m bars, 10 full oscillations
    r = await run_backtest(candles, SOL, PARAMS, CFG)
    assert r.error is None
    assert r.cycles > 50
    assert r.realized > 0
    assert r.net_return > 0
    assert r.time_in_range == 1
    assert r.range_exits == 0
    assert r.fees_paid > 0
    assert 0 < r.fee_share < 1
    assert r.net_return > r.buy_hold_return
    m = r.metrics()
    assert m["cycles"] == r.cycles
    assert r.equity_curve[0][1] <= CFG.capital * Decimal("1.01")


async def test_downtrend_leaves_bag_and_loses() -> None:
    r = await run_backtest(trend(288 * 5, total_change=-0.4), SOL, PARAMS, CFG)
    assert r.net_return < 0
    assert r.unrealized < 0  # the "bag": inventory marked below cost
    assert r.range_exits == 1
    assert r.max_drawdown > Decimal("0.05")
    assert r.buy_hold_return < Decimal("-0.39")


async def test_stop_loss_limits_downtrend_loss() -> None:
    stop = GridParams(15, Decimal(16), GridType.GEOMETRIC, exit_policy="stop_loss")
    wait = await run_backtest(trend(288 * 5, total_change=-0.4), SOL, PARAMS, CFG)
    stopped = await run_backtest(trend(288 * 5, total_change=-0.4), SOL, stop, CFG)
    assert stopped.stopped
    assert stopped.net_return > wait.net_return


async def test_uptrend_trailing_rebuilds() -> None:
    p = GridParams(15, Decimal(16), GridType.GEOMETRIC, exit_policy="stop_loss+trailing_up")
    r = await run_backtest(trend(288 * 5, total_change=0.6), SOL, p, CFG)
    assert r.rebuilds >= 1
    assert r.net_return > 0
    assert r.net_return < r.buy_hold_return  # grid underperforms a strong trend: expected and reported


async def test_impossible_plan_is_reported_not_raised() -> None:
    tiny = SimulationConfig(capital=Decimal(20), fees=CFG.fees)
    r = await run_backtest(sideways(100), SOL, PARAMS, tiny)
    assert r.error is not None
    assert r.net_return == 0


def test_resample_and_missing() -> None:
    one_min = series([100 + i * 0.1 for i in range(11)], minutes=1)
    five = resample(one_min, Timeframe.M1, Timeframe.M5)
    assert len(five) == 2
    assert five[0].open == one_min[0].open
    assert five[0].close == one_min[4].close
    assert five[0].high == max(c.high for c in one_min[:5])
    assert count_missing(one_min[:3] + one_min[5:], Timeframe.M1) == 2


async def test_speed_budget() -> None:
    candles = sideways(288 * 30)  # one month of 5m bars
    t = time.perf_counter()
    await run_backtest(candles, SOL, PARAMS, CFG)
    elapsed = time.perf_counter() - t
    assert elapsed < 5, f"backtest too slow: {elapsed:.2f}s"
