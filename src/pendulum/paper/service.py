"""Paper reporting use cases shared by the CLI and Celery tasks."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path

import typer

from pendulum.backtest.criteria import ViabilityCriteria
from pendulum.backtest.report import DISCLAIMER
from pendulum.container import Container
from pendulum.notifications import templates
from pendulum.notifications.telegram import make_notifier
from pendulum.paper.reporting import PaperComparison, build_summaries, compare_session, comparison_lines
from pendulum.paper.session import MODE

PAPER_REPORTS_DIR = Path("docs/reports/paper")


async def send_summary(c: Container, days: int, *, send: bool, echo: bool = False) -> str | None:
    """Build the summary for the last ``days``; send it to Telegram when ``send``."""
    now = datetime.now(UTC)
    rows = await build_summaries(c.session_reader, timedelta(days=days), now)
    if not rows:
        if echo:
            typer.echo("no paper sessions yet")
        return None
    label = "Daily" if days == 1 else ("Weekly" if days == 7 else f"{days}-day")
    html = templates.daily_summary(now, rows, label)
    if echo:
        typer.echo(html)
    if send:
        await make_notifier().send(html)
    return html


async def compare_all(c: Container) -> tuple[list[PaperComparison], Path]:
    """Compare all paper sessions with backtests and write a markdown report."""
    rows = [
        await compare_session(info, c.candles, c.loader, c.adapter)
        for info in await c.session_reader.list_sessions(MODE, ["running", "stopped"])
        if "trader" in info.state
    ]
    crit = ViabilityCriteria()
    lines = [
        "# Paper trading vs backtest",
        "",
        DISCLAIMER,
        "",
        f"Divergence = |paper P&L − backtest P&L| / max(|backtest P&L|, 1% of capital); "
        f"criterion ≤ {crit.max_paper_divergence * 100:.0f}% after ≥ {crit.min_paper_days} days.",
        "Paper fills on live trade prints; the backtest replays 1m candles with the same fill rules.",
        "",
        *comparison_lines(rows, crit.max_paper_divergence),
        "",
    ]
    await asyncio.to_thread(PAPER_REPORTS_DIR.mkdir, parents=True, exist_ok=True)
    path = PAPER_REPORTS_DIR / f"{datetime.now(UTC):%Y-%m-%d}-paper-vs-backtest.md"
    await asyncio.to_thread(path.write_text, "\n".join(lines), encoding="utf-8")
    return rows, path
