"""Paper reporting: periodic summaries and paper-vs-backtest comparison."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal

from pendulum.backtest.engine import GridParams, SimulationConfig, run_backtest
from pendulum.config.settings import GridType
from pendulum.domain.models import FeeModel, Symbol, Timeframe
from pendulum.domain.money import ONE, ZERO
from pendulum.domain.ports import ICandleStore, ISessionReader, SessionInfo
from pendulum.exchanges.base import BaseExchangeAdapter
from pendulum.market_data.loader import HistoryLoader, floor_time
from pendulum.notifications.templates import PairSummary
from pendulum.paper.session import MODE

DIVERGENCE_FLOOR = Decimal("0.01")  # of capital; avoids dividing by ~0 when the backtest P&L is tiny


def _params(info: SessionInfo) -> GridParams:
    p = info.params
    return GridParams(int(p["levels"]), Decimal(p["width_pct"]), GridType(p["grid_type"]), p["exit_policy"])


async def build_summaries(reader: ISessionReader, period: timedelta, now: datetime) -> list[PairSummary]:
    """Per-pair numbers for running paper sessions."""
    out: list[PairSummary] = []
    for info in await reader.list_sessions(MODE, ["running", "stopped"]):
        state = info.state
        if "trader" not in state:
            continue
        if info.status == "stopped" and info.started_at < now - period - timedelta(days=1):
            continue  # long-finished sessions are not part of the digest
        trader = state["trader"]
        cycles_period, _ = await reader.cycle_stats(info.id, now - period)
        start_price = Decimal(state["start_price"])
        price = Decimal(state["last_price"])
        prices = trader["prices"]
        out.append(
            PairSummary(
                symbol=info.symbol,
                equity=Decimal(state["equity"]),
                capital=Decimal(info.params["capital"]),
                realized=Decimal(trader["realized"]),
                cycles=int(trader["cycles"]),
                cycles_period=cycles_period,
                price=price,
                lower=Decimal(prices[0]),
                upper=Decimal(prices[-1]),
                buy_hold_return=price / start_price - ONE if start_price > 0 else ZERO,
            )
        )
    return out


@dataclass(frozen=True, slots=True)
class PaperComparison:
    """Paper result vs a backtest of the same pair, parameters and period."""

    symbol: str
    session_id: int
    start: datetime
    end: datetime
    capital: Decimal
    paper_pnl: Decimal
    backtest_pnl: Decimal
    paper_cycles: int
    backtest_cycles: int
    error: str | None = None

    @property
    def days(self) -> Decimal:
        """Compared span in days."""
        return Decimal((self.end - self.start).total_seconds()) / Decimal(86400)

    @property
    def divergence(self) -> Decimal:
        """|paper - backtest| P&L relative to |backtest P&L| (floored at 1% of capital)."""
        base = max(abs(self.backtest_pnl), self.capital * DIVERGENCE_FLOOR)
        return abs(self.paper_pnl - self.backtest_pnl) / base


async def compare_session(
    info: SessionInfo,
    candles: ICandleStore,
    loader: HistoryLoader,
    adapter: BaseExchangeAdapter,
) -> PaperComparison:
    """Backtest the session's parameters on stored 1m candles over the session's lifetime."""
    state = info.state
    start = floor_time(info.started_at, Timeframe.M1) + timedelta(minutes=1)
    end = floor_time(datetime.fromisoformat(state["last_event"]), Timeframe.M1)
    capital = Decimal(info.params["capital"])
    paper_pnl = Decimal(state["equity"]) - capital
    paper_cycles = int(state["trader"]["cycles"])
    base = PaperComparison(info.symbol, info.id, start, end, capital, paper_pnl, ZERO, paper_cycles, 0)
    if end <= start:
        return _with_error(base, "session too short")
    await loader.load(Symbol.parse(info.symbol), Timeframe.M1, start, end)
    bars = await candles.get_candles(adapter.exchange_id, info.symbol, Timeframe.M1, start, end)
    if not bars:
        return _with_error(base, "no candles for the session period")
    instrument = await loader.resolve(Symbol.parse(info.symbol))
    p = info.params
    config = SimulationConfig(
        capital=capital,
        fees=FeeModel(Decimal(p["maker_fee"]), Decimal(p["taker_fee"])),
        slippage_bps=Decimal(p["slippage_bps"]),
        timeframe=Timeframe.M1,
    )
    result = await run_backtest(bars, instrument, _params(info), config)
    return PaperComparison(
        info.symbol,
        info.id,
        start,
        end,
        capital,
        paper_pnl,
        result.final_equity - capital,
        paper_cycles,
        result.cycles,
        result.error,
    )


def _with_error(c: PaperComparison, error: str) -> PaperComparison:
    return PaperComparison(
        c.symbol,
        c.session_id,
        c.start,
        c.end,
        c.capital,
        c.paper_pnl,
        c.backtest_pnl,
        c.paper_cycles,
        0,
        error,
    )


def comparison_lines(rows: list[PaperComparison], max_divergence: Decimal) -> list[str]:
    """Markdown table rows for reports."""
    lines = [
        "| Pair | Session | Days | Paper P&L | Backtest P&L | Divergence | Paper cycles | BT cycles | OK |",
        "|---|---:|---:|---:|---:|---:|---:|---:|:-:|",
    ]
    for c in rows:
        if c.error:
            lines.append(f"| {c.symbol} | {c.session_id} | {c.days:.1f} | — | — | — | — | — | {c.error} |")
            continue
        ok = "✅" if c.divergence <= max_divergence else "❌"
        lines.append(
            f"| {c.symbol} | {c.session_id} | {c.days:.1f} | {c.paper_pnl:,.2f} | {c.backtest_pnl:,.2f} "
            f"| {c.divergence * 100:.1f}% | {c.paper_cycles} | {c.backtest_cycles} | {ok} |"
        )
    return lines
