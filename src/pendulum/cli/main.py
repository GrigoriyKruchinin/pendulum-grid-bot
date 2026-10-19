"""Command-line interface: ``pendulum <group> <command>``."""

from __future__ import annotations

import asyncio
from collections.abc import Coroutine
from datetime import UTC, datetime
from typing import Annotated, Any

import typer

from pendulum.container import build_container, setup_logging
from pendulum.domain.models import Symbol, Timeframe

app = typer.Typer(help="Pendulum grid bot.", no_args_is_help=True)
data_app = typer.Typer(help="Market data.", no_args_is_help=True)
app.add_typer(data_app, name="data")


def run[T](coro: Coroutine[Any, Any, T]) -> T:
    """Run a coroutine from a sync CLI command."""
    setup_logging()
    return asyncio.run(coro)


def parse_date(value: str) -> datetime:
    """Parse ``YYYY-MM-DD`` or ISO datetime as UTC."""
    dt = datetime.fromisoformat(value)
    return dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt.astimezone(UTC)


def parse_pairs(value: str) -> list[Symbol]:
    """Parse ``"BTC/USDT,ETH/USDT"``."""
    return [Symbol.parse(p.strip()) for p in value.split(",") if p.strip()]


PairsOpt = Annotated[str, typer.Option("--pairs", help="Comma-separated, e.g. BTC/USDT,SOL/USDT")]
TfOpt = Annotated[Timeframe, typer.Option("--tf", help="Candle timeframe")]
SinceOpt = Annotated[str, typer.Option("--since", help="Start date, YYYY-MM-DD (UTC)")]
UntilOpt = Annotated[str | None, typer.Option("--until", help="End date, YYYY-MM-DD (UTC)")]


@data_app.command("instruments")
def data_instruments() -> None:
    """Fetch and store exchange instruments (filters)."""

    async def _main() -> None:
        async with build_container() as c:
            insts = await c.loader.sync_instruments()
            typer.echo(f"{len(insts)} instruments stored for {c.adapter.exchange_id}")

    run(_main())


@data_app.command("load")
def data_load(
    pairs: PairsOpt,
    since: SinceOpt,
    tf: TfOpt = Timeframe.M1,
    until: UntilOpt = None,
    refetch_gaps: Annotated[bool, typer.Option(help="Retry holes already attempted")] = False,
) -> None:
    """Load (or resume loading) historical candles and print a data-quality summary."""

    async def _main() -> None:
        async with build_container() as c:
            loader = c.loader
            for symbol in parse_pairs(pairs):
                report = await loader.load(
                    symbol,
                    tf,
                    parse_date(since),
                    parse_date(until) if until else None,
                    refetch_gaps=refetch_gaps,
                )
                longest = max(
                    ((g.end - g.start).total_seconds() // tf.seconds + 1 for g in report.remaining_gaps),
                    default=0,
                )
                typer.echo(
                    f"{report.symbol} {tf.value}: fetched={report.fetched} expected={report.expected_bars} "
                    f"coverage={report.coverage:.4%} gaps={len(report.remaining_gaps)} "
                    f"longest_gap_bars={int(longest)}"
                )

    run(_main())


if __name__ == "__main__":
    app()
