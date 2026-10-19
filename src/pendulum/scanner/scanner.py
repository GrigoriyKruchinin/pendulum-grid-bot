"""Pair scanner: ranks spot pairs by suitability for grid trading.

A good grid pair oscillates a lot (many level crossings) without trending (no bag, no runaway). Metrics use
numpy floats: this is analytics, not money math.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, fields
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import numpy as np

from pendulum.config.settings import ScannerSettings
from pendulum.domain.models import Candle, FeeModel, Instrument, Ticker, Timeframe
from pendulum.domain.ports import IScannerStore
from pendulum.exchanges.base import BaseExchangeAdapter
from pendulum.logging import get_logger

log = get_logger(__name__)
MAX_CANDIDATES = 40
MIN_BARS = 24 * 7
DRIFT_PENALTY = 3  # a 30% move over the lookback roughly halves the score


@dataclass(frozen=True, slots=True)
class PairMetrics:
    """Suitability metrics for one pair (fractions unless stated)."""

    symbol: str
    volume_24h_usd: float
    spread_bps: float
    hourly_range: float  # mean (high - low) / close of 1h bars
    efficiency_ratio: float  # daily |net move| / sum |daily moves| (0 = pure chop, 1 = straight line)
    drift: float  # total return over the lookback
    daily_volatility: float  # stdev of daily log returns
    tick_ratio: float  # tick size / price
    range_to_fee: float  # hourly range / round-trip maker fee
    score: float

    def to_dict(self) -> dict[str, str]:
        """JSON-friendly metrics."""
        return {f.name: f"{getattr(self, f.name):.6g}" for f in fields(self) if f.name != "symbol"}


def compute_metrics(
    symbol: str, candles: Sequence[Candle], ticker: Ticker, tick_size: Decimal, fees: FeeModel
) -> PairMetrics | None:
    """Metrics from 1h candles; None if there is too little history."""
    if len(candles) < MIN_BARS:
        return None
    high = np.array([float(c.high) for c in candles])
    low = np.array([float(c.low) for c in candles])
    close = np.array([float(c.close) for c in candles])
    hourly_range = float(np.mean((high - low) / close))
    # Trend measures use daily closes: on hourly closes the noise path dwarfs any trend.
    daily = close[::24]
    daily_moves = np.abs(np.diff(daily))
    path = float(daily_moves.sum())
    efficiency = abs(float(daily[-1] - daily[0])) / path if path > 0 else 1.0
    drift = float(close[-1] / close[0] - 1)
    daily_vol = float(np.std(np.diff(np.log(daily)))) if len(daily) > 2 else 0.0
    price = float(ticker.last)
    tick_ratio = float(tick_size) / price if price > 0 else 1.0
    round_trip = float(fees.round_trip_maker)
    range_to_fee = hourly_range / round_trip if round_trip > 0 else 0.0
    # Reward oscillation, punish trend and drift; disqualify pairs whose swings cannot pay the fees
    # or whose tick is so coarse that grid levels cannot be placed precisely.
    viable = range_to_fee >= 1.5 and tick_ratio < round_trip / 2
    score = hourly_range * (1 - efficiency) / (1 + DRIFT_PENALTY * abs(drift)) * 1000 if viable else 0.0
    return PairMetrics(
        symbol=symbol,
        volume_24h_usd=float(ticker.volume_24h_quote),
        spread_bps=float(ticker.spread_bps),
        hourly_range=hourly_range,
        efficiency_ratio=efficiency,
        drift=drift,
        daily_volatility=daily_vol,
        tick_ratio=tick_ratio,
        range_to_fee=range_to_fee,
        score=score,
    )


def prefilter(
    instruments: Sequence[Instrument], tickers: Sequence[Ticker], settings: ScannerSettings, quote: str
) -> list[tuple[Instrument, Ticker]]:
    """Liquid, tight-spread, non-risky pairs in ``quote``, highest volume first."""
    by_symbol = {t.symbol: t for t in tickers}
    out: list[tuple[Instrument, Ticker]] = []
    for inst in instruments:
        t = by_symbol.get(inst.symbol)
        if t is None or inst.symbol.quote != quote or inst.risky:
            continue
        if t.volume_24h_quote < settings.min_24h_volume_usd or t.spread_bps > settings.max_spread_bps:
            continue
        out.append((inst, t))
    out.sort(key=lambda it: it[1].volume_24h_quote, reverse=True)
    return out[:MAX_CANDIDATES]


@dataclass(slots=True)
class PairScanner:
    """Fetches data from the exchange, scores pairs and stores the ranking."""

    adapter: BaseExchangeAdapter
    store: IScannerStore
    settings: ScannerSettings
    quote: str = "USDT"

    async def scan(self, now: datetime | None = None) -> list[PairMetrics]:
        """Run a full scan; returns ranked metrics (best first) and persists the top N."""
        run_at = now or datetime.now(UTC)
        instruments = await self.adapter.rest.fetch_instruments()
        tickers = await self.adapter.rest.fetch_tickers()
        candidates = prefilter(instruments, tickers, self.settings, self.quote)
        start = run_at - timedelta(days=self.settings.lookback_days)
        results: list[PairMetrics] = []
        for inst, ticker in candidates:
            rate = await self.adapter.fee_rate(inst)
            fees = FeeModel(rate.maker, rate.taker)
            candles = await self._hourly(inst, start, run_at)
            m = compute_metrics(str(inst.symbol), candles, ticker, inst.filters.tick_size, fees)
            if m is not None:
                results.append(m)
        results.sort(key=lambda m: m.score, reverse=True)
        top = results[: self.settings.top_n]
        await self.store.save_scores(
            run_at,
            self.adapter.exchange_id,
            [(m.symbol, Decimal(str(round(m.score, 8))), m.to_dict()) for m in top],
        )
        log.info(
            "scan_done", candidates=len(candidates), scored=len(results), top=[m.symbol for m in top[:5]]
        )
        return results

    async def _hourly(self, inst: Instrument, start: datetime, end: datetime) -> list[Candle]:
        candles: list[Candle] = []
        cursor = start
        step = timedelta(hours=1)
        limit = self.adapter.max_candles_per_request
        while cursor < end:
            chunk_end = min(end, cursor + step * (limit - 1))
            batch = await self.adapter.rest.fetch_candles(inst, Timeframe.H1, cursor, chunk_end)
            candles.extend(c for c in batch if cursor <= c.open_time <= chunk_end)
            cursor = chunk_end + step
        last_closed = end - step
        return [c for c in candles if c.open_time <= last_closed]
