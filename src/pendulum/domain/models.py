"""Exchange-agnostic domain models. All monetary fields are ``Decimal``."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum

from pendulum.domain.money import ZERO, ceil_to_step, floor_to_step


class Side(StrEnum):
    """Order side."""

    BUY = "buy"
    SELL = "sell"

    @property
    def opposite(self) -> Side:
        """The other side."""
        return Side.SELL if self is Side.BUY else Side.BUY


class OrderStatus(StrEnum):
    """Order lifecycle states."""

    NEW = "new"
    OPEN = "open"
    PARTIALLY_FILLED = "partially_filled"
    FILLED = "filled"
    CANCELLED = "cancelled"
    REJECTED = "rejected"

    @property
    def is_terminal(self) -> bool:
        """No further transitions possible."""
        return self in {OrderStatus.FILLED, OrderStatus.CANCELLED, OrderStatus.REJECTED}


class Timeframe(StrEnum):
    """Supported candle timeframes."""

    M1 = "1m"
    M5 = "5m"
    H1 = "1h"
    D1 = "1d"

    @property
    def seconds(self) -> int:
        """Duration in seconds."""
        return {"1m": 60, "5m": 300, "1h": 3600, "1d": 86400}[self.value]


@dataclass(frozen=True, slots=True)
class Symbol:
    """Trading pair, canonical form ``BASE/QUOTE``."""

    base: str
    quote: str

    @classmethod
    def parse(cls, text: str) -> Symbol:
        """Parse ``"SOL/USDT"``."""
        base, sep, quote = text.upper().partition("/")
        if not sep or not base or not quote:
            raise ValueError(f"symbol must look like BASE/QUOTE, got {text!r}")
        return cls(base, quote)

    def __str__(self) -> str:
        return f"{self.base}/{self.quote}"


@dataclass(frozen=True, slots=True)
class MarketFilters:
    """Exchange trading rules for a symbol."""

    tick_size: Decimal
    step_size: Decimal
    min_qty: Decimal
    min_notional: Decimal

    def __post_init__(self) -> None:
        if self.tick_size <= 0 or self.step_size <= 0:
            raise ValueError("tick_size and step_size must be positive")
        if self.min_qty < 0 or self.min_notional < 0:
            raise ValueError("min_qty and min_notional must be non-negative")

    def round_price(self, price: Decimal, side: Side) -> Decimal:
        """Buy prices round down, sell prices round up."""
        if side is Side.BUY:
            return floor_to_step(price, self.tick_size)
        return ceil_to_step(price, self.tick_size)

    def round_qty(self, qty: Decimal) -> Decimal:
        """Quantities always round down."""
        return floor_to_step(qty, self.step_size)

    def is_valid_order(self, price: Decimal, qty: Decimal) -> bool:
        """Whether an already-rounded order satisfies min qty and min notional."""
        return qty > ZERO and price > ZERO and qty >= self.min_qty and price * qty >= self.min_notional


@dataclass(frozen=True, slots=True)
class Instrument:
    """A tradable symbol with its exchange filters."""

    symbol: Symbol
    exchange_symbol: str
    filters: MarketFilters


@dataclass(frozen=True, slots=True)
class Candle:
    """OHLCV bar. ``open_time`` is timezone-aware UTC."""

    open_time: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal
    turnover: Decimal = ZERO

    def __post_init__(self) -> None:
        if self.open_time.tzinfo is None:
            raise ValueError("candle open_time must be timezone-aware")
        if not (self.low <= min(self.open, self.close) and self.high >= max(self.open, self.close)):
            raise ValueError(f"inconsistent OHLC at {self.open_time.isoformat()}")


@dataclass(frozen=True, slots=True)
class Ticker:
    """Best bid/ask snapshot."""

    symbol: Symbol
    bid: Decimal
    ask: Decimal
    last: Decimal
    volume_24h_quote: Decimal
    ts: datetime

    @property
    def spread_bps(self) -> Decimal:
        """Relative spread in basis points."""
        mid = (self.bid + self.ask) / 2
        if mid <= 0:
            return Decimal("Infinity")
        return (self.ask - self.bid) / mid * Decimal(10_000)


@dataclass(slots=True)
class Order:
    """A limit order managed by the grid (mutable: status and fills change)."""

    client_id: str
    symbol: Symbol
    side: Side
    price: Decimal
    qty: Decimal
    level_index: int
    created_at: datetime
    status: OrderStatus = OrderStatus.NEW
    filled_qty: Decimal = ZERO
    exchange_id: str | None = None

    @property
    def remaining_qty(self) -> Decimal:
        """Unfilled quantity."""
        return self.qty - self.filled_qty


@dataclass(frozen=True, slots=True)
class Fill:
    """An execution of (part of) an order."""

    client_id: str
    side: Side
    price: Decimal
    qty: Decimal
    fee: Decimal
    fee_asset: str
    ts: datetime
    is_maker: bool = True

    @property
    def notional(self) -> Decimal:
        """price * qty in quote."""
        return self.price * self.qty


@dataclass(frozen=True, slots=True)
class Cycle:
    """A completed buy-low / sell-high round trip."""

    level_index: int
    buy_price: Decimal
    sell_price: Decimal
    qty: Decimal
    fees_quote: Decimal
    opened_at: datetime
    closed_at: datetime

    @property
    def gross_profit(self) -> Decimal:
        """Profit before fees, in quote."""
        return (self.sell_price - self.buy_price) * self.qty

    @property
    def net_profit(self) -> Decimal:
        """Profit after fees, in quote."""
        return self.gross_profit - self.fees_quote


@dataclass(frozen=True, slots=True)
class EquityPoint:
    """Portfolio valuation at a moment in time."""

    ts: datetime
    quote_balance: Decimal
    base_balance: Decimal
    price: Decimal

    @property
    def equity(self) -> Decimal:
        """Total value in quote at ``price``."""
        return self.quote_balance + self.base_balance * self.price


@dataclass(slots=True)
class Balances:
    """Spot balances of one pair's base and quote assets."""

    base: Decimal = ZERO
    quote: Decimal = ZERO
    locked_base: Decimal = ZERO
    locked_quote: Decimal = ZERO

    @property
    def total_base(self) -> Decimal:
        """Free + locked base."""
        return self.base + self.locked_base

    @property
    def total_quote(self) -> Decimal:
        """Free + locked quote."""
        return self.quote + self.locked_quote
