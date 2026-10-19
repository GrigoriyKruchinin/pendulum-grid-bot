"""Grid plan: exchange-valid level prices and per-slot quantities for a given capital."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from pendulum.config.settings import GridType
from pendulum.domain.models import FeeModel, MarketFilters
from pendulum.domain.money import ONE, ZERO, floor_to_step
from pendulum.grid.strategies import IGridStrategy, strategy_for


class GridPlanError(ValueError):
    """The requested grid cannot be built (too narrow, too small capital, ...)."""


@dataclass(frozen=True, slots=True)
class GridSpec:
    """User-level grid parameters."""

    lower: Decimal
    upper: Decimal
    levels: int
    grid_type: GridType
    capital_quote: Decimal

    @classmethod
    def around(
        cls, price: Decimal, width_pct: Decimal, levels: int, grid_type: GridType, capital_quote: Decimal
    ) -> GridSpec:
        """Range centred on ``price`` spanning ``width_pct`` percent in total."""
        half = width_pct / Decimal(200)
        if not ZERO < half < ONE:
            raise GridPlanError("width_pct must be in (0, 200)")
        return cls(price * (ONE - half), price * (ONE + half), levels, grid_type, capital_quote)

    def to_dict(self) -> dict[str, str | int]:
        """JSON-friendly representation (Decimals as strings)."""
        return {
            "lower": str(self.lower),
            "upper": str(self.upper),
            "levels": self.levels,
            "grid_type": self.grid_type.value,
            "capital_quote": str(self.capital_quote),
        }


@dataclass(frozen=True, slots=True)
class GridPlan:
    """Validated grid.

    ``prices`` are ascending tick-aligned levels. Slot ``i`` spans ``prices[i]`` -> ``prices[i+1]`` and
    trades ``slot_qty[i]`` base units: buy at the lower edge, sell at the upper edge.
    """

    spec: GridSpec
    prices: tuple[Decimal, ...]
    slot_qty: tuple[Decimal, ...]
    skipped_levels: int
    min_step_ratio: Decimal

    @property
    def lower(self) -> Decimal:
        """Lowest level."""
        return self.prices[0]

    @property
    def upper(self) -> Decimal:
        """Highest level."""
        return self.prices[-1]

    @property
    def slots(self) -> int:
        """Number of intervals."""
        return len(self.prices) - 1


def build_plan(
    spec: GridSpec,
    filters: MarketFilters,
    fees: FeeModel,
    *,
    min_step_fee_multiplier: Decimal = Decimal("1.5"),
    entry_cost_buffer: Decimal = Decimal("0.005"),
    strategy: IGridStrategy | None = None,
) -> GridPlan:
    """Round levels to the tick grid, drop unprofitable steps and size every slot.

    A step is kept only if ``upper/lower - 1 > round_trip_maker_fee * min_step_fee_multiplier``.
    Each slot gets ``capital / slots`` of quote budget, shrunk by ``entry_cost_buffer`` (taker fee +
    slippage of the initial purchase), so the whole plan always fits within ``capital_quote``.
    """
    strat = strategy or strategy_for(spec.grid_type)
    raw = strat.level_prices(spec.lower, spec.upper, spec.levels)
    rounded = sorted({floor_to_step(p, filters.tick_size) for p in raw if p >= filters.tick_size})
    threshold = fees.round_trip_maker * min_step_fee_multiplier

    kept: list[Decimal] = []
    for price in rounded:
        if not kept or price / kept[-1] - ONE > threshold:
            kept.append(price)
    skipped = len(raw) - len(kept)
    if len(kept) < 2:
        raise GridPlanError(
            f"no profitable grid: every step <= {threshold:.4%} "
            f"(round-trip maker fee x {min_step_fee_multiplier})"
        )

    slots = len(kept) - 1
    budget = spec.capital_quote / slots / (ONE + entry_cost_buffer)
    quantities: list[Decimal] = []
    for i in range(slots):
        qty = filters.round_qty(budget / kept[i])
        # The sell side must also be valid after a buy fee is taken from the received base coin.
        sell_qty = filters.round_qty(qty * (ONE - fees.maker)) if fees.fee_in_received_asset else qty
        if not filters.is_valid_order(kept[i], qty) or not filters.is_valid_order(kept[i + 1], sell_qty):
            raise GridPlanError(
                f"capital {spec.capital_quote} too small for {slots} slots: "
                f"order {qty} @ {kept[i]} below exchange minimums"
            )
        quantities.append(qty)

    min_ratio = min(kept[i + 1] / kept[i] - ONE for i in range(slots))
    return GridPlan(spec, tuple(kept), tuple(quantities), skipped, min_ratio)
