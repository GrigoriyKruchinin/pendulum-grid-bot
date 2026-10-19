"""Market regime filters (``IRegimeFilter``): trade the grid only while the market is range-bound.

The filter consumes closed bars and answers whether a grid should be running. Signals use floats: they are
analytics, never money.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from itertools import pairwise
from typing import Any, Protocol


class IRegimeFilter(Protocol):
    """Decides whether grid trading is allowed in the current regime."""

    @property
    def name(self) -> str:
        """Config name."""
        ...

    def update(self, close_time: datetime, close: Decimal) -> None:
        """Feed a closed bar (any timeframe that closes on hour boundaries)."""
        ...

    def allows_trading(self) -> bool:
        """True while the regime suits a grid."""
        ...

    def snapshot(self) -> dict[str, Any]:
        """Persistable state."""
        ...

    def restore(self, state: dict[str, Any]) -> None:
        """Load a ``snapshot()``."""
        ...


@dataclass
class NoFilter:
    """Always allows trading (baseline behaviour)."""

    name: str = "none"

    def update(self, close_time: datetime, close: Decimal) -> None:
        """Ignored."""

    def allows_trading(self) -> bool:
        """Always True."""
        return True

    def snapshot(self) -> dict[str, Any]:
        """Stateless."""
        return {}

    def restore(self, state: dict[str, Any]) -> None:
        """Stateless."""


def efficiency_ratio(closes: Sequence[float]) -> float:
    """|net move| / sum of |moves|: 0 = pure chop, 1 = straight line."""
    path = sum(abs(b - a) for a, b in pairwise(closes))
    return abs(closes[-1] - closes[0]) / path if path > 0 else 1.0


@dataclass
class EfficiencyRatioFilter:
    """Rolling efficiency ratio of hourly closes with hysteresis.

    Trading stops when ER rises above ``off_above`` (a trend started) and resumes only after ER falls below
    ``on_below``. Until ``lookback_hours`` of history exist, trading is not allowed.
    """

    lookback_hours: int
    off_above: float
    on_below: float
    name: str = "er"
    _closes: deque[float] = field(default_factory=deque, repr=False)
    _allowed: bool | None = field(default=None, repr=False)

    def update(self, close_time: datetime, close: Decimal) -> None:
        """Record the close of every bar ending on an hour boundary."""
        if close_time.minute != 0 or close_time.second != 0:
            return
        self._closes.append(float(close))
        while len(self._closes) > self.lookback_hours + 1:
            self._closes.popleft()
        if len(self._closes) <= self.lookback_hours:
            return
        er = efficiency_ratio(list(self._closes))
        if self._allowed is None:
            self._allowed = er < self.off_above
        elif er > self.off_above:
            self._allowed = False
        elif er < self.on_below:
            self._allowed = True

    @property
    def current_er(self) -> float | None:
        """Latest efficiency ratio (None during warm-up)."""
        if len(self._closes) <= self.lookback_hours:
            return None
        return efficiency_ratio(list(self._closes))

    def allows_trading(self) -> bool:
        """True in a range-bound regime; False during warm-up and trends."""
        return bool(self._allowed)

    def snapshot(self) -> dict[str, Any]:
        """Hourly closes and the hysteresis state."""
        return {"closes": list(self._closes), "allowed": self._allowed}

    def restore(self, state: dict[str, Any]) -> None:
        """Load closes and state."""
        self._closes = deque(float(c) for c in state.get("closes", []))
        self._allowed = state.get("allowed")


# Thresholds sit well above the efficiency ratio of a random walk over the lookback
# (~sqrt(2/(pi*n)): 0.09 for 72 h, 0.06 for 168 h), so only clear trends switch the grid off.
_FILTERS: dict[str, tuple[int, float, float]] = {
    "er72": (72, 0.30, 0.20),
    "er168": (168, 0.20, 0.12),
}

MAX_LOOKBACK_HOURS = max(v[0] for v in _FILTERS.values())


def make_regime_filter(name: str) -> IRegimeFilter:
    """Build a filter from its config name: ``none``, ``er72``, ``er168``."""
    if name == "none":
        return NoFilter()
    try:
        lookback, off, on = _FILTERS[name]
    except KeyError:
        raise ValueError(f"unknown regime filter {name!r}") from None
    return EfficiencyRatioFilter(lookback, off, on, name=name)
