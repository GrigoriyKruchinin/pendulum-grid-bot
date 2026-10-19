"""GridTrader: executor-agnostic grid logic on top of ``IOrderGateway``.

The trader owns slot state and cycle accounting; balances live in the executor. Total PnL is always
measured from executor equity; cycles give the realized part.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any

from pendulum.config.settings import GridType
from pendulum.domain.models import Cycle, FeeModel, Fill, MarketFilters, Order, Side, Symbol
from pendulum.domain.money import ZERO
from pendulum.execution.gateway import IOrderGateway, OrderRejectedError
from pendulum.grid.exit_policies import ExitAction, IRangeExitPolicy
from pendulum.grid.plan import GridPlan, GridSpec
from pendulum.logging import get_logger

log = get_logger(__name__)


class Holding(StrEnum):
    """What a slot currently holds."""

    QUOTE = "quote"  # waiting to buy at the lower edge
    BASE = "base"  # waiting to sell at the upper edge


@dataclass(slots=True)
class Slot:
    """One grid interval."""

    index: int
    buy_price: Decimal
    sell_price: Decimal
    target_qty: Decimal
    holding: Holding = Holding.QUOTE
    base_qty: Decimal = ZERO  # base actually received for this slot (after base-coin fees)
    cost_quote: Decimal = ZERO  # quote spent to acquire base_qty (incl. quote fees)
    entry_price: Decimal = ZERO
    opened_at: datetime | None = None
    order_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """JSON-friendly snapshot."""
        return {
            "index": self.index,
            "holding": self.holding.value,
            "base_qty": str(self.base_qty),
            "cost_quote": str(self.cost_quote),
            "entry_price": str(self.entry_price),
            "opened_at": self.opened_at.isoformat() if self.opened_at else None,
            "order_id": self.order_id,
        }


class GridTrader:
    """Runs one grid for one symbol."""

    def __init__(
        self,
        plan: GridPlan,
        gateway: IOrderGateway,
        *,
        symbol: Symbol,
        filters: MarketFilters,
        fees: FeeModel,
        exit_policy: IRangeExitPolicy,
        id_prefix: str = "pdl",
    ) -> None:
        self.plan = plan
        self.gateway = gateway
        self.symbol = symbol
        self.filters = filters
        self.fees = fees
        self.exit_policy = exit_policy
        self._prefix = id_prefix
        self._seq = 0
        self.slots: list[Slot] = []
        self._by_order: dict[str, Slot] = {}
        self.cycles: list[Cycle] = []
        self.active = False
        self.rebuilds = 0
        # Totals carried over from before a restore (cycles themselves are persisted elsewhere).
        self._realized_carry = ZERO
        self._cycles_carry = 0

    # -- helpers ----------------------------------------------------------------------------------

    def _next_id(self, tag: str) -> str:
        self._seq += 1
        return f"{self._prefix}-{tag}{self._seq}"

    def _received_base(self, fill: Fill) -> Decimal:
        return fill.qty - fill.fee if fill.fee_asset == self.symbol.base else fill.qty

    def _spent_quote(self, fill: Fill) -> Decimal:
        return fill.notional + (fill.fee if fill.fee_asset == self.symbol.quote else ZERO)

    def _init_slots(self, plan: GridPlan) -> None:
        p = plan.prices
        self.slots = [Slot(i, p[i], p[i + 1], plan.slot_qty[i]) for i in range(plan.slots)]
        self._by_order = {}

    async def _place(self, slot: Slot, now: datetime) -> None:
        if slot.holding is Holding.QUOTE:
            side, price, qty = Side.BUY, slot.buy_price, slot.target_qty
            # Market rebalancing (fees, slippage) can leave slightly less quote than planned:
            # shrink the order to what is affordable instead of leaving the slot idle.
            free_quote = (await self.gateway.get_balances()).quote
            if qty * price > free_quote:
                qty = self.filters.round_qty(free_quote / price)
        else:
            side, price, qty = Side.SELL, slot.sell_price, self.filters.round_qty(slot.base_qty)
        if not self.filters.is_valid_order(price, qty):
            log.warning("slot_idle_below_minimum", slot=slot.index, side=side.value, qty=str(qty))
            slot.order_id = None
            return
        order = Order(self._next_id(side.value[0]), self.symbol, side, price, qty, slot.index, now)
        try:
            await self.gateway.place_limit(order)
        except OrderRejectedError as exc:
            log.warning("order_rejected", slot=slot.index, side=side.value, error=str(exc))
            slot.order_id = None
            return
        slot.order_id = order.client_id
        self._by_order[order.client_id] = slot

    # -- lifecycle --------------------------------------------------------------------------------

    async def start(self, price: Decimal, now: datetime) -> None:
        """Buy base for slots above ``price`` at market, then place all grid orders."""
        self._init_slots(self.plan)
        await self._acquire_base_for_slots(price, now)
        for slot in self.slots:
            await self._place(slot, now)
        self.active = True
        log.info("grid_started", symbol=str(self.symbol), slots=len(self.slots), price=str(price))

    async def _acquire_base_for_slots(self, price: Decimal, now: datetime) -> None:
        """Mark slots entirely above ``price`` as holding base, using free base first, then a market buy."""
        base_slots = [s for s in self.slots if s.buy_price >= price]
        needed = sum((s.target_qty for s in base_slots), ZERO)
        if needed == ZERO:
            return
        balances = await self.gateway.get_balances()
        free_base = balances.base
        to_buy = self.filters.round_qty(needed - free_base) if needed > free_base else ZERO
        received, spent, entry = ZERO, ZERO, price
        if to_buy > ZERO and self.filters.is_valid_order(price, to_buy):
            fill = await self.gateway.market_order(self._next_id("m"), Side.BUY, to_buy)
            received, spent, entry = self._received_base(fill), self._spent_quote(fill), fill.price
        available = min(free_base, needed) + received
        # Existing free base is valued at the current price as its cost basis for cycle accounting.
        cost_total = spent + min(free_base, needed) * price
        for slot in base_slots:
            share = slot.target_qty / needed
            slot.holding = Holding.BASE
            slot.base_qty = available * share
            slot.cost_quote = cost_total * share
            slot.entry_price = entry
            slot.opened_at = now

    async def on_fill(self, fill: Fill) -> Cycle | None:
        """Flip the slot whose order filled and place the opposite order. Returns a completed cycle."""
        slot = self._by_order.pop(fill.client_id, None)
        if slot is None:
            return None
        slot.order_id = None
        cycle: Cycle | None = None
        if fill.side is Side.BUY:
            slot.holding = Holding.BASE
            slot.base_qty = self._received_base(fill)
            slot.cost_quote = self._spent_quote(fill)
            slot.entry_price = fill.price
            slot.opened_at = fill.ts
        else:
            proceeds = fill.notional - (fill.fee if fill.fee_asset == self.symbol.quote else ZERO)
            cost = slot.cost_quote * fill.qty / slot.base_qty if slot.base_qty > 0 else ZERO
            net = proceeds - cost
            gross = (fill.price - slot.entry_price) * fill.qty
            cycle = Cycle(
                level_index=slot.index,
                buy_price=slot.entry_price,
                sell_price=fill.price,
                qty=fill.qty,
                fees_quote=gross - net,
                opened_at=slot.opened_at or fill.ts,
                closed_at=fill.ts,
            )
            self.cycles.append(cycle)
            # Leftover dust (base_qty - sold) stays in the account and is marked to market via equity.
            slot.holding = Holding.QUOTE
            slot.base_qty = ZERO
            slot.cost_quote = ZERO
            slot.opened_at = None
        await self._place(slot, fill.ts)
        return cycle

    async def evaluate_exit(self, price: Decimal, now: datetime, *, low: Decimal | None = None) -> ExitAction:
        """Ask the range-exit policy what to do at ``price``."""
        return self.exit_policy.decide(price, now, self.plan.lower, self.plan.upper, low=low)

    async def cancel_all(self) -> None:
        """Cancel every resting grid order."""
        for slot in self.slots:
            if slot.order_id is not None:
                await self.gateway.cancel_order(slot.order_id)
                self._by_order.pop(slot.order_id, None)
                slot.order_id = None

    async def stop(self, now: datetime, *, liquidate: bool) -> Fill | None:
        """Cancel orders and optionally sell all base at market."""
        await self.cancel_all()
        self.active = False
        if not liquidate:
            return None
        base = self.filters.round_qty((await self.gateway.get_balances()).base)
        if base <= ZERO:
            return None
        try:
            return await self.gateway.market_order(self._next_id("x"), Side.SELL, base)
        except OrderRejectedError as exc:
            log.warning("liquidation_rejected", error=str(exc))
            return None

    async def rebuild(self, plan: GridPlan, price: Decimal, now: datetime) -> None:
        """Replace the grid with ``plan`` at the current price, rebalancing inventory at market."""
        await self.cancel_all()
        self.plan = plan
        self._init_slots(plan)
        base_needed = sum((s.target_qty for s in self.slots if s.buy_price >= price), ZERO)
        free_base = (await self.gateway.get_balances()).base
        excess = self.filters.round_qty(free_base - base_needed)
        if excess > ZERO and self.filters.is_valid_order(price, excess):
            await self.gateway.market_order(self._next_id("r"), Side.SELL, excess)
        await self._acquire_base_for_slots(price, now)
        for slot in self.slots:
            await self._place(slot, now)
        self.exit_policy.reset()
        self.rebuilds += 1
        self.active = True
        log.info("grid_rebuilt", symbol=str(self.symbol), lower=str(plan.lower), upper=str(plan.upper))

    # -- reporting --------------------------------------------------------------------------------

    @property
    def realized_profit(self) -> Decimal:
        """Sum of completed cycle net profits (including cycles before a restore)."""
        return self._realized_carry + sum((c.net_profit for c in self.cycles), ZERO)

    @property
    def cycle_count(self) -> int:
        """Completed cycles (including those before a restore)."""
        return self._cycles_carry + len(self.cycles)

    def snapshot(self) -> dict[str, Any]:
        """State for persistence and resume."""
        plan = self.plan
        return {
            "plan": plan.spec.to_dict(),
            "prices": [str(p) for p in plan.prices],
            "slot_qty": [str(q) for q in plan.slot_qty],
            "skipped_levels": plan.skipped_levels,
            "min_step_ratio": str(plan.min_step_ratio),
            "slots": [s.to_dict() for s in self.slots],
            "seq": self._seq,
            "cycles": self.cycle_count,
            "realized": str(self.realized_profit),
            "rebuilds": self.rebuilds,
            "active": self.active,
            "exit_policy": self.exit_policy.snapshot(),
        }

    def restore(self, state: dict[str, Any]) -> None:
        """Load a ``snapshot()``; orders must already exist in the executor."""
        self.plan = plan_from_state(state)
        self._init_slots(self.plan)
        for slot, d in zip(self.slots, state["slots"], strict=True):
            slot.holding = Holding(d["holding"])
            slot.base_qty = Decimal(d["base_qty"])
            slot.cost_quote = Decimal(d["cost_quote"])
            slot.entry_price = Decimal(d["entry_price"])
            slot.opened_at = datetime.fromisoformat(d["opened_at"]) if d["opened_at"] else None
            slot.order_id = d["order_id"]
            if slot.order_id is not None:
                self._by_order[slot.order_id] = slot
        self._seq = int(state["seq"])
        self.cycles = []
        self._cycles_carry = int(state["cycles"])
        self._realized_carry = Decimal(state["realized"])
        self.rebuilds = int(state["rebuilds"])
        self.active = bool(state["active"])
        self.exit_policy.restore(state.get("exit_policy", {}))


def plan_from_state(state: dict[str, Any]) -> GridPlan:
    """Rebuild the exact ``GridPlan`` stored in a trader snapshot."""
    spec_d = state["plan"]
    spec = GridSpec(
        Decimal(spec_d["lower"]),
        Decimal(spec_d["upper"]),
        int(spec_d["levels"]),
        GridType(spec_d["grid_type"]),
        Decimal(spec_d["capital_quote"]),
    )
    return GridPlan(
        spec,
        tuple(Decimal(p) for p in state["prices"]),
        tuple(Decimal(q) for q in state["slot_qty"]),
        int(state["skipped_levels"]),
        Decimal(state["min_step_ratio"]),
    )
