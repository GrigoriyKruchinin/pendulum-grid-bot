"""Markdown reports for backtests. Every report is labelled as a historical simulation."""

from __future__ import annotations

from collections.abc import Sequence
from decimal import Decimal
from typing import TYPE_CHECKING

from pendulum.backtest.engine import BacktestResult
from pendulum.backtest.walk_forward import WalkForwardConfig, WalkForwardResult

if TYPE_CHECKING:
    from pendulum.backtest.criteria import CriterionCheck

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


def walk_forward_report(
    wf: WalkForwardResult, assumptions: dict[str, str], git_sha: str, grid_size: int, cfg: WalkForwardConfig
) -> str:
    """Report for one symbol's walk-forward run."""
    rows = []
    for i, w in enumerate(wf.windows, 1):
        span = f"{w.window.train_end:%Y-%m-%d} → {w.window.test_end:%Y-%m-%d}"
        bh = pct(wf.buy_hold_returns[i - 1])
        if w.test is None or w.train is None or w.best_params is None:
            note = f"no set qualified ({w.eligible}/{w.candidates} within DD)"
            rows.append(f"| {i} | {span} | {note} | — | 0.00% | {bh} | — | — |")
            continue
        rows.append(
            f"| {i} | {span} | {w.best_params.label()} | {pct(w.train.net_return)} "
            f"| {pct(w.test.net_return)} | {bh} | {pct(w.test.max_drawdown)} | {w.test.cycles_per_day:.2f} |"
        )
    return "\n".join(
        [
            f"# Walk-forward: {wf.symbol}",
            "",
            DISCLAIMER,
            "",
            f"- Windows: {len(wf.windows)} (train {cfg.train_days}d → test {cfg.test_days}d, "
            f"step {cfg.test_days}d)",
            f"- Parameter sets per window: {grid_size}; selection: best in-sample net return with "
            f"max DD ≤ {pct(cfg.max_train_drawdown)} and return > {pct(cfg.min_train_return)}",
            f"- Code version: `{git_sha}`",
            "",
            "## Out-of-sample summary",
            "",
            f"- Positive OOS windows: **{pct(wf.positive_share)}**",
            f"- Chained OOS return: **{pct(wf.oos_compound_return)}** over {wf.test_days:.0f} days "
            f"(annualized {pct(wf.oos_annualized)})",
            f"- Buy & hold over the same test windows: {pct(wf.buy_hold_compound_return)} "
            f"(annualized {pct(wf.buy_hold_annualized)})",
            f"- Max OOS drawdown: {pct(wf.max_oos_drawdown)} (chained across windows "
            f"{pct(wf.chained_drawdown)}, worst single window {pct(wf.max_window_drawdown)})",
            f"- Mean in-sample return of selected sets: {pct(wf.mean_train_return)} vs mean OOS "
            f"{pct(wf.mean_test_return)} (gap = overfitting signal)",
            f"- Top-2 windows share of positive OOS return: {pct(wf.top2_profit_concentration)}",
            "",
            "## Windows",
            "",
            "| # | Test span | Selected params | Train net | OOS net | B&H | OOS max DD | Cycles/day |",
            "|---|---|---|---:|---:|---:|---:|---:|",
            *rows,
            "",
            "## Assumptions",
            "",
            *[f"- **{k}**: {v}" for k, v in assumptions.items()],
            "",
        ]
    )


def criteria_table(checks: Sequence[CriterionCheck]) -> str:
    """Markdown table of Go/No-Go checks."""
    lines = ["| Criterion | Result | Value | Threshold |", "|---|:-:|---:|---|"]
    lines += [f"| {c.name} | {'✅' if c.passed else '❌'} | {c.value} | {c.threshold} |" for c in checks]
    return "\n".join(lines) + "\n"
