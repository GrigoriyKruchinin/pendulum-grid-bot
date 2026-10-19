"""``IOrderGateway``: the only seam between grid logic and an executor (simulator, paper, live)."""

from __future__ import annotations

from decimal import Decimal
from typing import Protocol

from pendulum.domain.models import Balances, Fill, Order, Side


class OrderRejectedError(Exception):
    """The executor refused an order (insufficient funds, below minimums, exchange reject)."""


class IOrderGateway(Protocol):
    """Order operations for one symbol. Fills of resting orders are delivered by the driver."""

    async def place_limit(self, order: Order) -> Order:
        """Submit a maker limit order; returns it with status OPEN (raises ``OrderRejectedError``)."""
        ...

    async def cancel_order(self, client_id: str) -> None:
        """Cancel a resting order (no-op if already gone)."""
        ...

    async def market_order(self, client_id: str, side: Side, qty: Decimal) -> Fill:
        """Execute immediately as taker; returns the aggregated fill."""
        ...

    async def get_open_orders(self) -> list[Order]:
        """Currently resting orders."""
        ...

    async def get_balances(self) -> Balances:
        """Base/quote balances (free and locked)."""
        ...
