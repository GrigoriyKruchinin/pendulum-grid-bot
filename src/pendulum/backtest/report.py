"""Markdown reports for backtests. Every report is labelled as a historical simulation."""

from __future__ import annotations

from collections.abc import Sequence
from decimal import Decimal

from pendulum.backtest.engine import BacktestResult

DISCLAIMER = (
    "> ⚠️ Historical simulation, not a forecast. Results include maker/taker fees, slippage on market "
    "orders and unrealized P&L of remaining inventory; real execution can be worse."
)


def pct(x: Decimal) -> str:
    """Format a fraction as a percentage."""
    if x.is_finite() and x.adjusted() < 6:
        return f"{x * 100:.2f}%"
    return f"{x * 100:.2e}%"


def money(x: Decimal) -> str:
    """Format a quote amount."""
    return f"{x:,.2f}"


def result_table(results: Sequence[tuple[str, BacktestResult]]) -> str:
    """Comparison table of labelled results."""
    header = (
        "| Run | Net | Annual | B&H | Max DD | Realized | Unrealized | Fees | Fee share | Cycles/day "
        "| In range | Exits | Rebuilds | Stopped |\n"
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|:-:|\n"
    )
    rows = []
    for label, r in results:
        if r.error:
            blanks = " — |" * 9
            rows.append(f"| {label} | — | — | {pct(r.buy_hold_return)} |{blanks} {r.error} |")
            continue
        rows.append(
            f"| {label} | {pct(r.net_return)} | {pct(r.annualized_return)} | {pct(r.buy_hold_return)} "
            f"| {pct(r.max_drawdown)} | {money(r.realized)} | {money(r.unrealized)} | {money(r.fees_paid)} "
            f"| {pct(r.fee_share)} | {r.cycles_per_day:.2f} | {pct(r.time_in_range)} | {r.range_exits} "
            f"| {r.rebuilds} | {'yes' if r.stopped else 'no'} |"
        )
    return header + "\n".join(rows) + "\n"


def single_report(symbol: str, r: BacktestResult, assumptions: dict[str, str], git_sha: str) -> str:
    """Report for one backtest run."""
    lines = [
        f"# Backtest: {symbol} — {r.params.label()}",
        "",
        DISCLAIMER,
        "",
        f"- Period: {r.start:%Y-%m-%d %H:%M} → {r.end:%Y-%m-%d %H:%M} UTC ({r.days:.1f} days, {r.bars} bars)",
        f"- Capital: {money(r.initial_capital)} → equity {money(r.final_equity)}",
        f"- Code version: `{git_sha}`",
        "",
        "## Metrics",
        "",
        result_table([(r.params.label(), r)]),
        "## Assumptions",
        "",
        *[f"- **{k}**: {v}" for k, v in assumptions.items()],
        "",
    ]
    return "\n".join(lines)
