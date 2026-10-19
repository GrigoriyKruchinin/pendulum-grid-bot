"""Command-line interface: ``pendulum <group> <command>``."""

from __future__ import annotations

import asyncio
from collections.abc import Coroutine
from datetime import UTC, datetime
from decimal import Decimal
from typing import Annotated, Any

import typer

from pendulum.backtest.criteria import ViabilityCriteria, evaluate_viability
from pendulum.backtest.engine import GridParams, SimulationConfig
from pendulum.backtest.report import pct
from pendulum.backtest.service import DataQualityError
from pendulum.backtest.walk_forward import (
    WalkForwardConfig,
    baseline_param_grid,
    default_param_grid,
    quick_param_grid,
    regime_param_grid,
)
from pendulum.config.pairs import load_pairs
from pendulum.config.settings import BacktestSettings, CapitalSettings, GridSettings, GridType
from pendulum.container import build_container, setup_logging
from pendulum.domain.models import Symbol, Timeframe
from pendulum.notifications.telegram import make_notifier
from pendulum.paper.reporting import comparison_lines
from pendulum.paper.runner import PaperRunner
from pendulum.paper.service import compare_all, send_summary

app = typer.Typer(help="Pendulum grid bot.", no_args_is_help=True)
data_app = typer.Typer(help="Market data.", no_args_is_help=True)
app.add_typer(data_app, name="data")
scan_app = typer.Typer(help="Pair scanner.", no_args_is_help=True)
app.add_typer(scan_app, name="scan")
paper_app = typer.Typer(help="Paper trading on live data.", no_args_is_help=True)
app.add_typer(paper_app, name="paper")
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
    regime: Annotated[str, typer.Option(help="Regime filter: none, er72, er168")] = "none",
    capital: Annotated[
        str | None, typer.Option(help="Quote capital (default: CAPITAL_TOTAL_USD x CAPITAL_PAIR_PCT)")
    ] = None,
) -> None:
    """Backtest one parameter set on stored candles and write a report."""

    async def _main() -> None:
        async with build_container() as c:
            svc = c.backtest_service
            symbol = Symbol.parse(pair)
            instrument = await c.loader.resolve(symbol)
            bt = BacktestSettings()
            config = SimulationConfig(
                capital=Decimal(capital) if capital else CapitalSettings().pair_usd,
                fees=await svc.fee_model(instrument),
                slippage_bps=bt.slippage_bps,
                timeframe=tf,
            )
            params = GridParams(levels, Decimal(width), grid_type, exit_policy, regime)
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


@backtest_app.command("walk-forward")
def backtest_walk_forward(
    pairs: PairsOpt,
    since: SinceOpt,
    until: UntilOpt = None,
    tf: Annotated[Timeframe, typer.Option("--tf", help="Simulation timeframe")] = Timeframe.M5,
    train_days: Annotated[int, typer.Option(help="Train window length")] = 60,
    test_days: Annotated[int, typer.Option(help="Test window length (also the step)")] = 14,
    grid_name: Annotated[
        str,
        typer.Option(
            "--grid",
            help="Parameter space: default (96), regime (144), baseline (48, regime w/o filters), quick (12)",
        ),
    ] = "default",
    workers: Annotated[int | None, typer.Option(help="Parallel processes (default: CPUs - 1)")] = None,
    capital: Annotated[
        str | None, typer.Option(help="Quote capital (default: CAPITAL_TOTAL_USD x CAPITAL_PAIR_PCT)")
    ] = None,
) -> None:
    """Walk-forward optimisation per pair, then the viability check across all pairs."""

    async def _main() -> None:
        async with build_container() as c:
            svc = c.backtest_service
            bt = BacktestSettings()
            wf = WalkForwardConfig(train_days=train_days, test_days=test_days)
            grids = {
                "default": default_param_grid,
                "regime": regime_param_grid,
                "baseline": baseline_param_grid,
                "quick": quick_param_grid,
            }
            if grid_name not in grids:
                raise typer.BadParameter(f"unknown grid {grid_name!r}")
            grid = grids[grid_name]()
            end = parse_date(until) if until else datetime.now(UTC)
            results = []
            for symbol in parse_pairs(pairs):
                instrument = await c.loader.resolve(symbol)
                config = SimulationConfig(
                    capital=Decimal(capital) if capital else CapitalSettings().pair_usd,
                    fees=await svc.fee_model(instrument),
                    slippage_bps=bt.slippage_bps,
                    timeframe=tf,
                )
                try:
                    r, path = await svc.walk_forward(
                        symbol, parse_date(since), end, config=config, wf=wf, grid=grid, workers=workers
                    )
                except DataQualityError as exc:
                    typer.echo(f"{symbol}: skipped: {exc}")
                    continue
                results.append(r)
                typer.echo(
                    f"{symbol}: windows={len(r.windows)} positive={pct(r.positive_share)} "
                    f"oos={pct(r.oos_compound_return)} (annual {pct(r.oos_annualized)}) "
                    f"b&h={pct(r.buy_hold_compound_return)} maxDD={pct(r.max_oos_drawdown)} report={path}"
                )
            if results:
                typer.echo("\nViability check (out-of-sample, all pairs):")
                for check in evaluate_viability(results):
                    mark = "PASS" if check.passed else "FAIL"
                    typer.echo(f"  [{mark}] {check.name}: {check.value} (need {check.threshold})")

    run(_main())


@scan_app.command("run")
def scan_run() -> None:
    """Rank liquid spot pairs by grid suitability and store the top N."""

    async def _main() -> None:
        async with build_container() as c:
            results = await c.pair_scanner.scan()
            header = (
                f"{'#':>3} {'pair':<12} {'score':>7} {'1h range':>9} {'ER':>6} {'drift':>8} {'vol $M':>8}"
            )
            typer.echo(header)
            for i, m in enumerate(results[:20], 1):
                typer.echo(
                    f"{i:>3} {m.symbol:<12} {m.score:>7.2f} {m.hourly_range:>9.2%} "
                    f"{m.efficiency_ratio:>6.3f} {m.drift:>8.1%} {m.volume_24h_usd / 1e6:>8.1f}"
                )

    run(_main())


@paper_app.command("run")
def paper_run(
    rearm: Annotated[
        bool, typer.Option(help="Open new grids for pairs whose last session was stopped")
    ] = False,
) -> None:
    """Run paper trading for enabled pairs in config/pairs.toml (long-running)."""

    async def _main() -> None:
        async with build_container() as c:
            grid = GridSettings()
            notifier = make_notifier()
            runner = PaperRunner(
                adapter=c.adapter,
                store=c.sessions,
                notifier=notifier,
                pairs=load_pairs(grid.pairs_file).enabled(),
                grid=grid,
                capital=CapitalSettings(),
                backtest=BacktestSettings(),
                rearm_stopped=rearm,
            )
            await runner.run()

    run(_main())


@paper_app.command("summary")
def paper_summary(
    days: Annotated[int, typer.Option(help="Period in days (1 = daily, 7 = weekly)")] = 1,
    send: Annotated[bool, typer.Option(help="Also send to Telegram")] = False,
) -> None:
    """Print (and optionally send) the paper summary."""

    async def _main() -> None:
        async with build_container() as c:
            await send_summary(c, days, send=send, echo=True)

    run(_main())


@paper_app.command("compare")
def paper_compare() -> None:
    """Compare every paper session with a backtest of the same period; writes a report."""

    async def _main() -> None:
        async with build_container() as c:
            rows, path = await compare_all(c)
            for line in comparison_lines(rows, ViabilityCriteria().max_paper_divergence):
                typer.echo(line)
            typer.echo(f"report: {path}")

    run(_main())


if __name__ == "__main__":
    app()
