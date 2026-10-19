"""Virtual exchange account shared by the backtester and the paper trader.

Fill rules (conservative, see ``docs/fill-model.md``):

* A resting limit order fills only when price **crosses** its level: buy if ``low < price``, sell if
  ``high > price`` (a touch is not a fill). Fill price is the limit price; maker fee applies.
* An order becomes eligible only for market data strictly after it was placed. With candles this means
  no order can be both placed and filled inside the same bar, i.e. no intra-bar round trip is ever
  credited: the pessimistic ordering of events within a bar.
* Market orders execute at the reference price worsened by ``slippage_bps`` and pay the taker fee.
* With ``fee_in_received_asset`` the buy fee is deducted from received base and the sell fee from
  received quote (Bybit spot).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from pendulum.domain.models import (
    Balances,
    Candle,
    FeeModel,
    Fill,
    MarketFilters,
    Order,
    OrderStatus,
    Side,
    Symbol,
    TradeTick,
)
from pendulum.domain.money import ONE, ZERO, bps
from pendulum.execution.gateway import OrderRejectedError


@dataclass(slots=True)
class _Resting:
    order: Order
    placed_at: datetime


class VirtualExchange:
    """Synchronous matching engine + balances for one symbol."""

    def __init__(
        self,
        symbol: Symbol,
        filters: MarketFilters,
        fees: FeeModel,
        *,
        quote: Decimal,
        base: Decimal = ZERO,
        slippage_bps: Decimal = Decimal(5),
    ) -> None:
        self.symbol = symbol
        self.filters = filters
        self.fees = fees
        self.slippage = bps(slippage_bps)
        self.balances = Balances(base=base, quote=quote)
        self._orders: dict[str, _Resting] = {}
        self._best_buy: Decimal | None = None
        self._best_sell: Decimal | None = None
        self.fees_paid_quote = ZERO  # fees valued in quote at fill price
        self.fill_count = 0

    # -- order book -------------------------------------------------------------------------------

    def _refresh_best(self) -> None:
        buys = [r.order.price for r in self._orders.values() if r.order.side is Side.BUY]
        sells = [r.order.price for r in self._orders.values() if r.order.side is Side.SELL]
        self._best_buy = max(buys) if buys else None
        self._best_sell = min(sells) if sells else None

    def open_orders(self) -> list[Order]:
        """Resting orders."""
        return [r.order for r in self._orders.values()]

    def place_limit(self, order: Order, now: datetime) -> Order:
        """Validate, lock funds and rest the order."""
        if order.client_id in self._orders:
            raise OrderRejectedError(f"duplicate client id {order.client_id}")
        if not self.filters.is_valid_order(order.price, order.qty):
            order.status = OrderStatus.REJECTED
            raise OrderRejectedError(f"{order.client_id} below exchange minimums")
        b = self.balances
        if order.side is Side.BUY:
            cost = order.price * order.qty
            if cost > b.quote:
                order.status = OrderStatus.REJECTED
                raise OrderRejectedError(f"{order.client_id} insufficient quote: need {cost}, have {b.quote}")
            b.quote -= cost
            b.locked_quote += cost
        else:
            if order.qty > b.base:
                order.status = OrderStatus.REJECTED
                raise OrderRejectedError(
                    f"{order.client_id} insufficient base: need {order.qty}, have {b.base}"
                )
            b.base -= order.qty
            b.locked_base += order.qty
        order.status = OrderStatus.OPEN
        self._orders[order.client_id] = _Resting(order, now)
        self._refresh_best()
        return order

    def cancel(self, client_id: str) -> None:
        """Remove a resting order and unlock its funds."""
        resting = self._orders.pop(client_id, None)
        if resting is None:
            return
        o = resting.order
        b = self.balances
        if o.side is Side.BUY:
            amount = o.price * o.remaining_qty
            b.locked_quote -= amount
            b.quote += amount
        else:
            b.locked_base -= o.remaining_qty
            b.base += o.remaining_qty
        o.status = OrderStatus.CANCELLED
        self._refresh_best()

    # -- fills ------------------------------------------------------------------------------------

    def _settle(
        self, side: Side, price: Decimal, qty: Decimal, fee_rate: Decimal, *, locked: bool
    ) -> tuple[Decimal, str]:
        """Move balances for an execution; returns ``(fee, fee_asset)``."""
        b = self.balances
        notional = price * qty
        if side is Side.BUY:
            if locked:
                b.locked_quote -= notional
            else:
                b.quote -= notional
            if self.fees.fee_in_received_asset:
                fee, asset = qty * fee_rate, self.symbol.base
                b.base += qty - fee
                fee_quote = fee * price
            else:
                fee, asset = notional * fee_rate, self.symbol.quote
                b.base += qty
                b.quote -= fee
                fee_quote = fee
        else:
            if locked:
                b.locked_base -= qty
            else:
                b.base -= qty
            fee, asset = notional * fee_rate, self.symbol.quote
            b.quote += notional - fee
            fee_quote = fee
        self.fees_paid_quote += fee_quote
        self.fill_count += 1
        return fee, asset

    def _fill_resting(self, resting: _Resting, ts: datetime) -> Fill:
        o = resting.order
        qty = o.remaining_qty
        fee, asset = self._settle(o.side, o.price, qty, self.fees.maker, locked=True)
        o.filled_qty = o.qty
        o.status = OrderStatus.FILLED
        del self._orders[o.client_id]
        return Fill(o.client_id, o.side, o.price, qty, fee, asset, ts, is_maker=True)

    def match_candle(self, candle: Candle, close_time: datetime) -> list[Fill]:
        """Fill every eligible order crossed by the bar; fills are stamped with ``close_time``."""
        hit_buy = self._best_buy is not None and candle.low < self._best_buy
        hit_sell = self._best_sell is not None and candle.high > self._best_sell
        if not (hit_buy or hit_sell):
            return []  # fast path: nothing crossed
        crossed = [
            r
            for r in self._orders.values()
            if r.placed_at <= candle.open_time
            and (
                (r.order.side is Side.BUY and candle.low < r.order.price)
                or (r.order.side is Side.SELL and candle.high > r.order.price)
            )
        ]
        # Deterministic order: buys from high to low, then sells from low to high.
        crossed.sort(
            key=lambda r: (
                r.order.side is Side.SELL,
                -r.order.price if r.order.side is Side.BUY else r.order.price,
            )
        )
        fills = [self._fill_resting(r, close_time) for r in crossed]
        if fills:
            self._refresh_best()
        return fills

    def match_trade(self, tick: TradeTick) -> list[Fill]:
        """Fill every eligible order strictly crossed by a trade print."""
        p = tick.price
        if not (
            (self._best_buy is not None and p < self._best_buy)
            or (self._best_sell is not None and p > self._best_sell)
        ):
            return []
        crossed = [
            r
            for r in self._orders.values()
            if r.placed_at < tick.ts
            and (
                (r.order.side is Side.BUY and p < r.order.price)
                or (r.order.side is Side.SELL and p > r.order.price)
            )
        ]
        fills = [self._fill_resting(r, tick.ts) for r in crossed]
        if fills:
            self._refresh_best()
        return fills

    def market(self, client_id: str, side: Side, qty: Decimal, ref_price: Decimal, ts: datetime) -> Fill:
        """Taker execution at ``ref_price`` worsened by slippage."""
        qty = self.filters.round_qty(qty)
        price = ref_price * (ONE + self.slippage) if side is Side.BUY else ref_price * (ONE - self.slippage)
        if not self.filters.is_valid_order(price, qty):
            raise OrderRejectedError(f"market {side} {qty} below exchange minimums")
        quote_fee = ZERO if self.fees.fee_in_received_asset else self.fees.taker
        if side is Side.BUY and price * qty * (ONE + quote_fee) > self.balances.quote:
            raise OrderRejectedError("insufficient quote for market buy")
        if side is Side.SELL and qty > self.balances.base:
            raise OrderRejectedError("insufficient base for market sell")
        fee, asset = self._settle(side, price, qty, self.fees.taker, locked=False)
        return Fill(client_id, side, price, qty, fee, asset, ts, is_maker=False)

    def equity(self, price: Decimal) -> Decimal:
        """Total account value in quote at ``price``."""
        return self.balances.total_quote + self.balances.total_base * price


class VirtualOrderGateway:
    """``IOrderGateway`` over a ``VirtualExchange``; the driver advances ``clock`` and ``last_price``."""

    def __init__(self, exchange: VirtualExchange, clock: Callable[[], datetime]) -> None:
        self.exchange = exchange
        self._clock = clock
        self.last_price: Decimal | None = None

    async def place_limit(self, order: Order) -> Order:
        """Rest a limit order."""
        return self.exchange.place_limit(order, self._clock())

    async def cancel_order(self, client_id: str) -> None:
        """Cancel a resting order."""
        self.exchange.cancel(client_id)

    async def market_order(self, client_id: str, side: Side, qty: Decimal) -> Fill:
        """Execute at ``last_price`` with slippage."""
        if self.last_price is None:
            raise OrderRejectedError("no reference price yet")
        return self.exchange.market(client_id, side, qty, self.last_price, self._clock())

    async def get_open_orders(self) -> list[Order]:
        """Resting orders."""
        return self.exchange.open_orders()

    async def get_balances(self) -> Balances:
        """Copy of current balances."""
        b = self.exchange.balances
        return Balances(b.base, b.quote, b.locked_base, b.locked_quote)
