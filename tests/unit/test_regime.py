import math
from datetime import timedelta
from decimal import Decimal

import pytest

from pendulum.backtest import engine
from pendulum.backtest.engine import GridParams, SimulationConfig, run_backtest
from pendulum.backtest.walk_forward import WARMUP, baseline_param_grid, regime_param_grid
from pendulum.config.settings import GridType
from pendulum.domain.models import FeeModel, Timeframe
from pendulum.grid.regime import EfficiencyRatioFilter, NoFilter, efficiency_ratio, make_regime_filter
from tests.fakes import SOL
from tests.synthetic import T0, series

H = timedelta(hours=1)


def test_efficiency_ratio_extremes() -> None:
    assert efficiency_ratio([1, 2, 3, 4]) == 1
    assert efficiency_ratio([1, 2, 1, 2, 1]) == 0
    assert efficiency_ratio([5, 5]) == 1  # no movement: treated as non-oscillating


def test_filter_warmup_and_hysteresis() -> None:
    f = EfficiencyRatioFilter(lookback_hours=4, off_above=0.5, on_below=0.2)
    for i, c in enumerate([100, 101, 100, 101]):
        f.update(T0 + i * H, Decimal(c))
    assert not f.allows_trading()  # warm-up: 4 closes < lookback + 1
    f.update(T0 + 4 * H, Decimal(100))
    assert f.allows_trading()  # choppy: ER = 0
    for i, c in enumerate([102, 104, 106, 108], start=5):
        f.update(T0 + i * H, Decimal(c))
    assert not f.allows_trading()  # trend
    f.update(T0 + 9 * H, Decimal(107))
    assert not f.allows_trading()  # still above on_below: hysteresis keeps it off
    state = f.snapshot()
    g = EfficiencyRatioFilter(lookback_hours=4, off_above=0.5, on_below=0.2)
    g.restore(state)
    assert g.allows_trading() == f.allows_trading()
    assert g.current_er == f.current_er


def test_non_hour_bars_ignored_and_factory() -> None:
    f = make_regime_filter("er72")
    f.update(T0 + timedelta(minutes=5), Decimal(1))
    assert f.snapshot()["closes"] == []
    assert isinstance(make_regime_filter("none"), NoFilter)
    with pytest.raises(ValueError, match="unknown"):
        make_regime_filter("magic")


def _trend_then_range() -> list:  # type: ignore[type-arg]
    n_trend, n_range = 288 * 4, 288 * 12
    trend = [100 * (1 - 0.35 * i / n_trend) for i in range(n_trend)]
    base = trend[-1]
    rng = [base + 3 * math.sin(2 * math.pi * i / 144) for i in range(n_range + 1)]
    return series(trend + rng)


async def test_filter_avoids_trend_and_reenters_in_range() -> None:
    cfg = SimulationConfig(capital=Decimal(1000), fees=FeeModel(Decimal("0.001"), Decimal("0.001")))
    candles = _trend_then_range()
    warm = series([100 + 2 * math.sin(i / 3) for i in range(12 * 200)])  # choppy history before the run
    shift = candles[0].open_time - warm[-1].open_time - timedelta(minutes=5)
    warm = [type(c)(c.open_time + shift, c.open, c.high, c.low, c.close, c.volume) for c in warm]
    plain = await run_backtest(candles, SOL, GridParams(15, Decimal(16), GridType.GEOMETRIC, "wait"), cfg)
    filtered = await run_backtest(
        candles, SOL, GridParams(15, Decimal(16), GridType.GEOMETRIC, "wait", "er72"), cfg, warmup=warm
    )
    assert filtered.regime_exits >= 1
    assert filtered.time_active < 1
    assert filtered.cycles > 0  # re-entered once the range returned
    assert filtered.net_return > plain.net_return
    assert filtered.metrics()["regime_exits"] == filtered.regime_exits


def test_regime_grid_and_warmup_window() -> None:
    grid = regime_param_grid()
    assert len(grid) == 144
    assert {p.regime for p in grid} == {"none", "er72", "er168"}
    assert timedelta(hours=169) == WARMUP


class ScriptedFilter:
    """Test double: allows trading from bar ``start_bar`` on (counting every update)."""

    name = "er72"

    def __init__(self, start_bar: int = 0) -> None:
        self.start_bar = start_bar
        self.bars = 0

    def update(self, close_time: object, close: Decimal) -> None:
        self.bars += 1

    def allows_trading(self) -> bool:
        return self.bars >= self.start_bar

    def snapshot(self) -> dict[str, object]:
        return {}

    def restore(self, state: dict[str, object]) -> None:
        return None


CFG = SimulationConfig(capital=Decimal(1000), fees=FeeModel(Decimal("0.001"), Decimal("0.001")))


def _crash_then_range() -> list:  # type: ignore[type-arg]
    rng1 = [100 + 2 * math.sin(2 * math.pi * i / 72) for i in range(288)]
    crash = [100 - 30 * i / 24 for i in range(1, 25)]  # -30% in two hours: hits the stop-loss
    rng2 = [70 + 2 * math.sin(2 * math.pi * i / 72) for i in range(288 * 3)]
    return series(rng1 + crash + rng2)


async def test_stop_loss_cooldown_and_totals_across_grids(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(engine, "make_regime_filter", lambda name: ScriptedFilter())
    candles = _crash_then_range()
    sim = engine._Simulation(
        candles, SOL, GridParams(10, Decimal(10), GridType.GEOMETRIC, "stop_loss", "er72"), CFG
    )
    r = await sim.run([], timedelta(hours=1))
    assert r.stopped
    assert len(sim.traders) >= 2  # re-entered after the stop
    entries = [f for f in sim.gateway.market_fills if "-m" in f.client_id]
    stops = [f for f in sim.gateway.market_fills if "-x" in f.client_id]
    second_entry = next(f for f in entries if f.client_id.startswith("bt1-"))
    assert second_entry.ts - stops[0].ts >= engine.STOP_COOLDOWN
    assert r.cycles == sum(t.cycle_count for t in sim.traders)
    assert r.realized == sum((t.realized_profit for t in sim.traders), Decimal(0))


async def test_plan_error_on_first_delayed_entry_keeps_cash(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(engine, "make_regime_filter", lambda name: ScriptedFilter(start_bar=10))
    tiny = SimulationConfig(capital=Decimal(20), fees=CFG.fees)
    params = GridParams(10, Decimal(10), GridType.GEOMETRIC, "wait", "er72")
    r = await engine.run_backtest(series([100.0] * 50), SOL, params, tiny)
    assert r.error is not None
    assert r.bars_active == 0
    assert r.net_return == 0


async def test_regime_filter_rejects_coarse_timeframes() -> None:
    cfg = SimulationConfig(capital=Decimal(1000), fees=CFG.fees, timeframe=Timeframe.H1)
    with pytest.raises(ValueError, match="1m or 5m"):
        await engine.run_backtest(
            series([100.0] * 5), SOL, GridParams(10, Decimal(10), GridType.GEOMETRIC, "wait", "er72"), cfg
        )


def test_baseline_grid_is_regime_grid_without_filters() -> None:
    base = baseline_param_grid()
    assert len(base) == 48
    assert all(p.regime == "none" for p in base)
