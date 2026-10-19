from datetime import timedelta
from decimal import Decimal

from pendulum.backtest.criteria import evaluate_backtest_criteria
from pendulum.backtest.engine import GridParams, SimulationConfig, run_backtest
from pendulum.backtest.walk_forward import (
    WalkForwardConfig,
    default_param_grid,
    make_windows,
    quick_param_grid,
    run_walk_forward,
    select_best,
)
from pendulum.config.settings import GridType
from pendulum.domain.models import FeeModel
from tests.fakes import SOL
from tests.synthetic import T0, sideways, trend

CFG = SimulationConfig(capital=Decimal(1000), fees=FeeModel(Decimal("0.001"), Decimal("0.001")))
GRID = [
    GridParams(10, Decimal(16), GridType.GEOMETRIC, "wait"),
    GridParams(20, Decimal(16), GridType.GEOMETRIC, "wait"),
    GridParams(10, Decimal(40), GridType.ARITHMETIC, "stop_loss"),
]


def test_windows_roll_by_test_span() -> None:
    w = make_windows(T0, T0 + timedelta(days=30), train_days=10, test_days=5)
    assert len(w) == 4
    assert w[0].train_end == T0 + timedelta(days=10)
    assert w[1].train_start == T0 + timedelta(days=5)
    assert w[-1].test_end <= T0 + timedelta(days=30)
    assert make_windows(T0, T0 + timedelta(days=5), 10, 5) == []


def test_param_grids() -> None:
    assert len(default_param_grid()) == 96
    assert len(quick_param_grid()) == 12
    assert len({p.label() for p in default_param_grid()}) == 96


async def test_sideways_market_passes_and_trend_fails() -> None:
    wf = WalkForwardConfig(train_days=4, test_days=2)
    flat = await run_walk_forward(sideways(288 * 12), SOL, CFG, wf, GRID, workers=1)
    assert len(flat.windows) == 4
    assert all(w.traded for w in flat.windows)
    assert flat.positive_share == 1
    assert flat.oos_compound_return > 0
    assert flat.oos_compound_return > flat.buy_hold_compound_return

    down = await run_walk_forward(trend(288 * 12, total_change=-0.6), SOL, CFG, wf, GRID, workers=1)
    assert down.positive_share == 0
    assert any(not w.traded for w in down.windows)  # nothing qualified in-sample: stays in cash

    checks = {c.name: c for c in evaluate_backtest_criteria([down])}
    assert not checks["positive OOS windows"].passed


async def test_process_pool_matches_sequential() -> None:
    wf = WalkForwardConfig(train_days=4, test_days=2)
    candles = sideways(288 * 8)
    seq = await run_walk_forward(candles, SOL, CFG, wf, GRID, workers=1)
    par = await run_walk_forward(candles, SOL, CFG, wf, GRID, workers=2)
    assert [w.best_params for w in seq.windows] == [w.best_params for w in par.windows]
    assert seq.oos_compound_return == par.oos_compound_return


async def test_select_best_respects_drawdown() -> None:
    candles = sideways(288 * 3)
    results = [await run_backtest(candles, SOL, p, CFG) for p in GRID]
    best = select_best(results, WalkForwardConfig())
    assert best is not None
    assert best.net_return == max(r.net_return for r in results if r.max_drawdown <= Decimal("0.2"))
    assert select_best(results, WalkForwardConfig(max_train_drawdown=Decimal(0))) is None
