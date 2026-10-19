"""HTML message templates for Telegram. All dynamic text is escaped."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from html import escape

DIVIDER = "━━━━━━━━━━━━━━━"
FOOTER = "<i>Paper trading: virtual orders on live Bybit data. Not a forecast.</i>"


def _pct(x: Decimal) -> str:
    sign = "+" if x > 0 else ""
    return f"{sign}{x * 100:.2f}%"


def _usd(x: Decimal) -> str:
    sign = "+" if x > 0 else ""
    return f"{sign}{x:,.2f} $"


def _icon(x: Decimal) -> str:
    return "🟢" if x > 0 else ("🔴" if x < 0 else "⚪")


@dataclass(frozen=True, slots=True)
class PairSummary:
    """Per-pair numbers for summaries."""

    symbol: str
    equity: Decimal
    capital: Decimal
    realized: Decimal
    cycles: int
    cycles_period: int
    price: Decimal
    lower: Decimal
    upper: Decimal
    buy_hold_return: Decimal

    @property
    def pnl(self) -> Decimal:
        """Equity change since start."""
        return self.equity - self.capital

    @property
    def unrealized(self) -> Decimal:
        """PnL not from closed cycles."""
        return self.pnl - self.realized

    @property
    def in_range(self) -> bool:
        """Price inside the grid."""
        return self.lower <= self.price <= self.upper


def paper_started(
    symbol: str,
    *,
    lower: Decimal,
    upper: Decimal,
    levels: int,
    grid_type: str,
    capital: Decimal,
    price: Decimal,
    resumed: bool,
) -> str:
    """Session start / resume."""
    title = "♻️ <b>Paper grid resumed</b>" if resumed else "🚀 <b>Paper grid started</b>"
    return "\n".join(
        [
            title,
            DIVIDER,
            f"<b>Pair:</b> <code>{escape(symbol)}</code>",
            f"<b>Range:</b> <code>{lower}</code> – <code>{upper}</code>",
            f"<b>Grid:</b> {levels} levels, {escape(grid_type)}",
            f"<b>Capital:</b> {capital:,.2f} $",
            f"<b>Price now:</b> <code>{price}</code>",
            DIVIDER,
            FOOTER,
        ]
    )


def range_exit(symbol: str, price: Decimal, lower: Decimal, upper: Decimal, action: str) -> str:
    """Price left the grid range."""
    direction = "⬆️ above" if price > upper else "⬇️ below"
    return "\n".join(
        [
            "⚠️ <b>Range exit</b>",
            DIVIDER,
            f"<b>Pair:</b> <code>{escape(symbol)}</code>",
            f"<b>Price:</b> <code>{price}</code> ({direction} range)",
            f"<b>Range:</b> <code>{lower}</code> – <code>{upper}</code>",
            f"<blockquote>Policy action: <b>{escape(action)}</b></blockquote>",
        ]
    )


def daily_summary(day: datetime, pairs: Sequence[PairSummary], period_label: str = "Daily") -> str:
    """Daily (or weekly) summary across virtual pairs."""
    total_cap = sum((p.capital for p in pairs), Decimal(0))
    total_pnl = sum((p.pnl for p in pairs), Decimal(0))
    total_real = sum((p.realized for p in pairs), Decimal(0))
    lines = [f"📊 <b>{escape(period_label)} paper summary</b> · {day:%Y-%m-%d}", DIVIDER]
    for p in pairs:
        range_mark = "✅ in range" if p.in_range else "🚧 out of range"
        lines += [
            f"{_icon(p.pnl)} <b>{escape(p.symbol)}</b>  {_usd(p.pnl)} ({_pct(p.pnl / p.capital)})",
            f"   cycles: {p.cycles_period} (total {p.cycles}) · realized {_usd(p.realized)}",
            f"   unrealized {_usd(p.unrealized)} · B&amp;H {_pct(p.buy_hold_return)} · {range_mark}",
        ]
    pct_total = total_pnl / total_cap if total_cap > 0 else Decimal(0)
    lines += [
        DIVIDER,
        f"<b>Total:</b> {_usd(total_pnl)} ({_pct(pct_total)}) · realized {_usd(total_real)}",
        FOOTER,
    ]
    return "\n".join(lines)


def paper_stopped(symbol: str, reason: str, equity: Decimal, capital: Decimal) -> str:
    """Session stopped by policy."""
    pnl = equity - capital
    return "\n".join(
        [
            "🛑 <b>Paper grid stopped</b>",
            DIVIDER,
            f"<b>Pair:</b> <code>{escape(symbol)}</code>",
            f"<b>Reason:</b> {escape(reason)}",
            f"<b>Result:</b> {_usd(pnl)} ({_pct(pnl / capital)})",
        ]
    )
