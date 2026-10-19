from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from pendulum.config.settings import GridType
from pendulum.domain.models import FeeModel, MarketFilters
from pendulum.grid.exit_policies import (
    CompositeExit,
    ExitAction,
    RebuildExit,
    StopLossExit,
    TrailingUpExit,
    WaitExit,
    make_exit_policy,
)
from pendulum.grid.plan import GridPlanError, GridSpec, build_plan

F = MarketFilters(Decimal("0.01"), Decimal("0.001"), Decimal("0.001"), Decimal(5))
FEES = FeeModel(Decimal("0.001"), Decimal("0.001"))
T0 = datetime(2025, 1, 1, tzinfo=UTC)
LO, HI = Decimal(90), Decimal(110)


def test_around_centres_range() -> None:
    spec = GridSpec.around(Decimal(100), Decimal(20), 10, GridType.GEOMETRIC, Decimal(1000))
    assert (spec.lower, spec.upper) == (Decimal(90), Decimal(110))


def test_too_narrow_grid_rejected_or_thinned() -> None:
    # 0.05% steps cannot beat 0.2% round-trip fees x 1.5.
    with pytest.raises(GridPlanError, match="no profitable grid"):
        build_plan(GridSpec(Decimal(100), Decimal("100.05"), 2, GridType.ARITHMETIC, Decimal(1000)), F, FEES)
    plan = build_plan(GridSpec(Decimal(100), Decimal(101), 21, GridType.ARITHMETIC, Decimal(1000)), F, FEES)
    assert plan.skipped_levels > 0
    assert plan.min_step_ratio > Decimal("0.003")


def test_small_capital_rejected() -> None:
    with pytest.raises(GridPlanError, match="too small"):
        build_plan(GridSpec(Decimal(90), Decimal(110), 30, GridType.GEOMETRIC, Decimal(50)), F, FEES)


def test_stop_loss_uses_low() -> None:
    p = StopLossExit(Decimal(5))
    assert p.decide(Decimal(100), T0, LO, HI) is ExitAction.NONE
    assert p.decide(Decimal(100), T0, LO, HI, low=Decimal("85.5")) is ExitAction.STOP


def test_rebuild_after_sustained_breakout() -> None:
    p = RebuildExit(after=timedelta(minutes=10))
    assert p.decide(Decimal(111), T0, LO, HI) is ExitAction.NONE
    assert p.decide(Decimal(100), T0 + timedelta(minutes=5), LO, HI) is ExitAction.NONE  # back inside resets
    assert p.decide(Decimal(80), T0 + timedelta(minutes=6), LO, HI) is ExitAction.NONE
    assert p.decide(Decimal(80), T0 + timedelta(minutes=16), LO, HI) is ExitAction.REBUILD


def test_trailing_up_ignores_downside_and_composite() -> None:
    t = TrailingUpExit(after=timedelta(minutes=1))
    assert t.decide(Decimal(50), T0, LO, HI) is ExitAction.NONE
    assert t.decide(Decimal(50), T0 + timedelta(hours=1), LO, HI) is ExitAction.NONE
    c = CompositeExit(StopLossExit(Decimal(5)), TrailingUpExit(after=timedelta(minutes=1)))
    assert c.name == "stop_loss+trailing_up"
    assert c.decide(Decimal(120), T0, LO, HI) is ExitAction.NONE
    assert c.decide(Decimal(120), T0 + timedelta(minutes=2), LO, HI) is ExitAction.REBUILD
    assert c.decide(Decimal(80), T0 + timedelta(minutes=3), LO, HI) is ExitAction.STOP
    assert WaitExit().decide(Decimal(1), T0, LO, HI) is ExitAction.NONE


def test_make_exit_policy() -> None:
    assert make_exit_policy("stop_loss+trailing_up").name == "stop_loss+trailing_up"
    with pytest.raises(ValueError, match="unknown"):
        make_exit_policy("yolo")


def test_exit_policy_timer_survives_snapshot() -> None:
    p = make_exit_policy("stop_loss+trailing_up")
    p.decide(Decimal(120), T0, LO, HI)
    state = p.snapshot()
    q = make_exit_policy("stop_loss+trailing_up")
    q.restore(state)
    assert q.decide(Decimal(120), T0 + timedelta(minutes=31), LO, HI) is ExitAction.REBUILD
