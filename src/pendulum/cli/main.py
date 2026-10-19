"""Command-line interface: ``pendulum <group> <command>``."""

from __future__ import annotations

import asyncio
from collections.abc import Coroutine
from datetime import UTC, datetime
from decimal import Decimal
from typing import Annotated, Any

import typer

from pendulum.backtest.engine import GridParams, SimulationConfig
from pendulum.backtest.report import pct
from pendulum.config.settings import BacktestSettings, GridType, RiskSettings
from pendulum.container import build_container, setup_logging
from pendulum.domain.models import Symbol, Timeframe

app = typer.Typer(help="Pendulum grid bot.", no_args_is_help=True)
data_app = typer.Typer(help="Market data.", no_args_is_help=True)
app.add_typer(data_app, name="data")
backtest_app = typer.Typer(help="Backtests and walk-forward optimisation.", no_args_is_help=True)
app.add_typer(backtest_app, name="backtest")


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


@backtest_app.command("run")
def backtest_run(
    pair: Annotated[str, typer.Option("--pair", help="e.g. SOL/USDT")],
    since: SinceOpt,
    until: UntilOpt = None,
    tf: Annotated[Timeframe, typer.Option("--tf", help="Simulation timeframe")] = Timeframe.M5,
    levels: Annotated[int, typer.Option(help="Number of grid levels")] = 20,
    width: Annotated[str, typer.Option(help="Total range width in percent")] = "20",
    grid_type: Annotated[GridType, typer.Option("--grid-type")] = GridType.GEOMETRIC,
    exit_policy: Annotated[str, typer.Option("--exit", help="Range-exit policy")] = "stop_loss+trailing_up",
    capital: Annotated[str | None, typer.Option(help="Quote capital (default: per-pair risk cap)")] = None,
) -> None:
    """Backtest one parameter set on stored candles and write a report."""

    async def _main() -> None:
        async with build_container() as c:
            svc = c.backtest_service
            symbol = Symbol.parse(pair)
            instrument = await c.loader.resolve(symbol)
            bt = BacktestSettings()
            config = SimulationConfig(
                capital=Decimal(capital) if capital else RiskSettings().max_pair_capital_usd,
                fees=await svc.fee_model(instrument),
                slippage_bps=bt.slippage_bps,
                timeframe=tf,
            )
            params = GridParams(levels, Decimal(width), grid_type, exit_policy)
            end = parse_date(until) if until else datetime.now(UTC)
            run_id, r, path = await svc.run(symbol, parse_date(since), end, params, config)
            if r.error:
                typer.echo(f"run {run_id}: grid not buildable: {r.error}")
                return
            typer.echo(
                f"run {run_id} {symbol} {params.label()}: net={pct(r.net_return)} "
                f"annual={pct(r.annualized_return)} "
                f"b&h={pct(r.buy_hold_return)} maxDD={pct(r.max_drawdown)} cycles/day={r.cycles_per_day:.2f} "
                f"in_range={pct(r.time_in_range)} report={path}"
            )

    run(_main())


if __name__ == "__main__":
    app()
