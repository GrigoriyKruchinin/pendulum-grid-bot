"""Grid spacing strategies (``IGridStrategy``)."""

from __future__ import annotations

from decimal import Decimal, localcontext
from typing import Protocol

from pendulum.config.settings import GridType


class IGridStrategy(Protocol):
    """Computes raw (unrounded) level prices for a range."""

    grid_type: GridType

    def level_prices(self, lower: Decimal, upper: Decimal, levels: int) -> list[Decimal]:
        """``levels`` ascending prices from ``lower`` to ``upper`` inclusive."""
        ...


def _validate(lower: Decimal, upper: Decimal, levels: int) -> None:
    if lower <= 0 or upper <= lower:
        raise ValueError("require 0 < lower < upper")
    if levels < 2:
        raise ValueError("a grid needs at least 2 levels")


class ArithmeticGrid:
    """Equal absolute step between levels."""

    grid_type = GridType.ARITHMETIC

    def level_prices(self, lower: Decimal, upper: Decimal, levels: int) -> list[Decimal]:
        """Linear spacing."""
        _validate(lower, upper, levels)
        step = (upper - lower) / (levels - 1)
        return [lower + step * i for i in range(levels - 1)] + [upper]


class GeometricGrid:
    """Equal percentage step between levels."""

    grid_type = GridType.GEOMETRIC

    def level_prices(self, lower: Decimal, upper: Decimal, levels: int) -> list[Decimal]:
        """Log spacing: ``lower * r**i`` with ``r = (upper/lower) ** (1/(levels-1))``."""
        _validate(lower, upper, levels)
        with localcontext() as ctx:
            ctx.prec = 34
            ratio = ((upper / lower).ln() / (levels - 1)).exp()
            prices = [lower * ratio**i for i in range(levels - 1)]
        return [+p for p in prices] + [upper]


def strategy_for(grid_type: GridType) -> IGridStrategy:
    """Factory for built-in strategies."""
    strategies: dict[GridType, IGridStrategy] = {
        GridType.ARITHMETIC: ArithmeticGrid(),
        GridType.GEOMETRIC: GeometricGrid(),
    }
    return strategies[grid_type]
