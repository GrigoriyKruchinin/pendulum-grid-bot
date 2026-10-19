"""Range-exit policies (``IRangeExitPolicy``): what to do when price leaves the grid range."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal
from enum import StrEnum
from typing import Any, Protocol

from pendulum.domain.money import ONE


class ExitAction(StrEnum):
    """Decision of an exit policy."""

    NONE = "none"  # keep the grid as is
    STOP = "stop"  # cancel orders, sell all base at market, stop trading
    REBUILD = "rebuild"  # cancel orders, re-centre the grid at the current price


class IRangeExitPolicy(Protocol):
    """Stateful policy evaluated on every price update."""

    @property
    def name(self) -> str:
        """Config name of the policy."""
        ...

    def decide(
        self, price: Decimal, ts: datetime, lower: Decimal, upper: Decimal, *, low: Decimal | None = None
    ) -> ExitAction:
        """Return the action for the current price. ``low`` is the worst price since the last call
        (candle low); only stop-style policies use it."""
        ...

    def reset(self) -> None:
        """Forget state (called after a rebuild)."""
        ...

    def snapshot(self) -> dict[str, Any]:
        """Persistable state (timers)."""
        ...

    def restore(self, state: dict[str, Any]) -> None:
        """Load a ``snapshot()``."""
        ...


@dataclass
class _OutOfRangeTimer:
    since: datetime | None = None

    def to_dict(self) -> dict[str, Any]:
        return {"since": self.since.isoformat() if self.since else None}

    @classmethod
    def from_dict(cls, state: dict[str, Any]) -> _OutOfRangeTimer:
        since = state.get("since")
        return cls(datetime.fromisoformat(since) if since else None)

    def update(self, outside: bool, ts: datetime) -> timedelta:
        if not outside:
            self.since = None
            return timedelta(0)
        if self.since is None:
            self.since = ts
        return ts - self.since


@dataclass
class WaitExit:
    """Never act: orders stay in place and wait for price to come back."""

    name: str = "wait"

    def decide(
        self, price: Decimal, ts: datetime, lower: Decimal, upper: Decimal, *, low: Decimal | None = None
    ) -> ExitAction:
        """Always NONE."""
        return ExitAction.NONE

    def reset(self) -> None:
        """Stateless."""

    def snapshot(self) -> dict[str, Any]:
        """Stateless."""
        return {}

    def restore(self, state: dict[str, Any]) -> None:
        """Stateless."""


@dataclass
class StopLossExit:
    """Stop when price falls ``stop_pct`` percent below the lower bound."""

    stop_pct: Decimal = Decimal("5")
    name: str = "stop_loss"

    def decide(
        self, price: Decimal, ts: datetime, lower: Decimal, upper: Decimal, *, low: Decimal | None = None
    ) -> ExitAction:
        """STOP when the worst price reaches ``lower * (1 - stop_pct/100)``."""
        if (low if low is not None else price) <= lower * (ONE - self.stop_pct / Decimal(100)):
            return ExitAction.STOP
        return ExitAction.NONE

    def reset(self) -> None:
        """Stateless."""

    def snapshot(self) -> dict[str, Any]:
        """Stateless."""
        return {}

    def restore(self, state: dict[str, Any]) -> None:
        """Stateless."""


@dataclass
class RebuildExit:
    """Re-centre the grid after price stays outside the range (either side) for ``after``."""

    after: timedelta = timedelta(hours=1)
    name: str = "rebuild"
    _timer: _OutOfRangeTimer = field(default_factory=_OutOfRangeTimer, repr=False)

    def decide(
        self, price: Decimal, ts: datetime, lower: Decimal, upper: Decimal, *, low: Decimal | None = None
    ) -> ExitAction:
        """REBUILD once out of range long enough."""
        if self._timer.update(price < lower or price > upper, ts) >= self.after:
            return ExitAction.REBUILD
        return ExitAction.NONE

    def reset(self) -> None:
        """Clear the timer."""
        self._timer = _OutOfRangeTimer()

    def snapshot(self) -> dict[str, Any]:
        """Out-of-range timer."""
        return self._timer.to_dict()

    def restore(self, state: dict[str, Any]) -> None:
        """Load the timer."""
        self._timer = _OutOfRangeTimer.from_dict(state)


@dataclass
class TrailingUpExit:
    """Follow an uptrend: re-centre after price stays above the range for ``after``; ignore drops."""

    after: timedelta = timedelta(minutes=30)
    name: str = "trailing_up"
    _timer: _OutOfRangeTimer = field(default_factory=_OutOfRangeTimer, repr=False)

    def decide(
        self, price: Decimal, ts: datetime, lower: Decimal, upper: Decimal, *, low: Decimal | None = None
    ) -> ExitAction:
        """REBUILD only on sustained upside breakout."""
        if self._timer.update(price > upper, ts) >= self.after:
            return ExitAction.REBUILD
        return ExitAction.NONE

    def reset(self) -> None:
        """Clear the timer."""
        self._timer = _OutOfRangeTimer()

    def snapshot(self) -> dict[str, Any]:
        """Out-of-range timer."""
        return self._timer.to_dict()

    def restore(self, state: dict[str, Any]) -> None:
        """Load the timer."""
        self._timer = _OutOfRangeTimer.from_dict(state)


@dataclass
class CompositeExit:
    """Delegate downside breaks to ``down`` and upside breaks to ``up``."""

    down: IRangeExitPolicy
    up: IRangeExitPolicy

    @property
    def name(self) -> str:
        """Combined name, e.g. ``stop_loss+trailing_up``."""
        return f"{self.down.name}+{self.up.name}"

    def decide(
        self, price: Decimal, ts: datetime, lower: Decimal, upper: Decimal, *, low: Decimal | None = None
    ) -> ExitAction:
        """Ask both; STOP wins over REBUILD."""
        mid = (lower + upper) / 2
        down = self.down.decide(min(price, mid), ts, lower, upper, low=low)
        up = self.up.decide(max(price, mid), ts, lower, upper)
        if ExitAction.STOP in (down, up):
            return ExitAction.STOP
        if ExitAction.REBUILD in (down, up):
            return ExitAction.REBUILD
        return ExitAction.NONE

    def reset(self) -> None:
        """Reset both."""
        self.down.reset()
        self.up.reset()

    def snapshot(self) -> dict[str, Any]:
        """Both sides' state."""
        return {"down": self.down.snapshot(), "up": self.up.snapshot()}

    def restore(self, state: dict[str, Any]) -> None:
        """Load both sides."""
        self.down.restore(state.get("down", {}))
        self.up.restore(state.get("up", {}))


def make_exit_policy(name: str) -> IRangeExitPolicy:
    """Build a policy from its config name (``stop_loss``, ``wait``, ``rebuild``, ``trailing_up``,
    or ``<down>+<up>`` for a composite)."""
    if "+" in name:
        down, up = name.split("+", 1)
        return CompositeExit(make_exit_policy(down), make_exit_policy(up))
    builders: dict[str, IRangeExitPolicy] = {
        "wait": WaitExit(),
        "stop_loss": StopLossExit(),
        "rebuild": RebuildExit(),
        "trailing_up": TrailingUpExit(),
    }
    try:
        return builders[name]
    except KeyError:
        raise ValueError(f"unknown exit policy {name!r}") from None
