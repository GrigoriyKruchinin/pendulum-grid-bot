"""Historical candle loader with checkpoints, resume and gap backfill.

Strategy: ask the store which bars are missing in the requested window and fetch only those. A checkpoint
records the span already attempted, so holes the exchange itself has (downtime, pre-listing) are not
re-requested on every run unless ``refetch_gaps`` is set.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from pendulum.domain.models import Instrument, Symbol, Timeframe
from pendulum.domain.ports import Checkpoint, Gap, ICandleStore, IInstrumentStore
from pendulum.exchanges.base import BaseExchangeAdapter, ExchangeError
from pendulum.logging import get_logger

log = get_logger(__name__)


def floor_time(ts: datetime, timeframe: Timeframe) -> datetime:
    """Align ``ts`` down to the timeframe grid (UTC epoch based)."""
    seconds = int(ts.timestamp())
    return datetime.fromtimestamp(seconds - seconds % timeframe.seconds, tz=UTC)


def last_closed_open_time(now: datetime, timeframe: Timeframe) -> datetime:
    """Open time of the most recent fully closed bar."""
    return floor_time(now, timeframe) - timedelta(seconds=timeframe.seconds)


@dataclass(slots=True)
class LoadReport:
    """Outcome of a load run for one series."""

    symbol: str
    timeframe: Timeframe
    start: datetime
    end: datetime
    fetched: int = 0
    requests: int = 0
    expected_bars: int = 0
    remaining_gaps: list[Gap] = field(default_factory=list)

    @property
    def missing_bars(self) -> int:
        """Bars still absent after the run."""
        step = self.timeframe.seconds
        return sum(int((g.end - g.start).total_seconds()) // step + 1 for g in self.remaining_gaps)

    @property
    def coverage(self) -> float:
        """Share of expected bars present (0..1); float is fine for a quality ratio."""
        if self.expected_bars == 0:
            return 1.0
        return 1 - self.missing_bars / self.expected_bars


def _advance(
    cp: Checkpoint | None, gap: Gap, batch_start: datetime, batch_end: datetime, step: timedelta
) -> Checkpoint:
    """Grow the attempted span only where it stays contiguous.

    The checkpoint marks ``[first, last]`` as fully attempted (holes inside are exchange holes). A gap before
    the span is merged only once it is finished; a batch after the span only if it starts right at its end.
    Otherwise an interrupted run would hide the unfetched remainder as a hole.
    """
    if cp is None:
        return Checkpoint(gap.start, batch_end)
    first, last = cp.first_open_time, cp.last_open_time
    if gap.start < first and batch_end >= gap.end and gap.end + step >= first:
        first = gap.start
    if batch_start <= last + step:
        last = max(last, batch_end)
    return Checkpoint(first, last)


class HistoryLoader:
    """Loads and backfills candles for one exchange."""

    def __init__(
        self,
        adapter: BaseExchangeAdapter,
        candles: ICandleStore,
        instruments: IInstrumentStore,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
        concurrency: int = 8,
    ) -> None:
        self._adapter = adapter
        self._concurrency = max(1, concurrency)
        self._candles = candles
        self._instruments = instruments
        self._now = now
        self._exchange = adapter.exchange_id

    async def sync_instruments(self) -> list[Instrument]:
        """Fetch exchange filters and persist them."""
        instruments = await self._adapter.rest.fetch_instruments()
        await self._instruments.upsert_instruments(self._exchange, instruments)
        log.info("instruments_synced", exchange=self._exchange, count=len(instruments))
        return instruments

    async def resolve(self, symbol: Symbol) -> Instrument:
        """Stored instrument, syncing from the exchange if unknown."""
        inst = await self._instruments.get_instrument(self._exchange, str(symbol))
        if inst is not None:
            return inst
        return self._adapter.find_instrument(await self.sync_instruments(), symbol)

    async def load(
        self,
        symbol: Symbol,
        timeframe: Timeframe,
        since: datetime,
        until: datetime | None = None,
        *,
        refetch_gaps: bool = False,
    ) -> LoadReport:
        """Ensure all closed bars in ``[since, until]`` are stored; returns a quality report."""
        if timeframe not in self._adapter.supported_timeframes:
            raise ExchangeError(f"{self._exchange} does not support {timeframe}")
        step = timedelta(seconds=timeframe.seconds)
        start = floor_time(since, timeframe)
        last_closed = last_closed_open_time(self._now(), timeframe)
        end = min(floor_time(until, timeframe), last_closed) if until else last_closed
        report = LoadReport(str(symbol), timeframe, start, end)
        if end < start:
            return report
        report.expected_bars = int((end - start) / step) + 1

        inst = await self.resolve(symbol)
        sym = str(symbol)
        checkpoint = await self._candles.get_checkpoint(self._exchange, sym, timeframe)
        gaps = await self._candles.find_gaps(self._exchange, sym, timeframe, start, end + step)
        chunk = step * (self._adapter.max_candles_per_request - 1)

        for gap in gaps:
            if (
                not refetch_gaps
                and checkpoint is not None
                and checkpoint.first_open_time <= gap.start
                and gap.end <= checkpoint.last_open_time
            ):
                continue  # already attempted: the exchange has no data here
            spans: list[tuple[datetime, datetime]] = []
            cursor = gap.start
            while cursor <= gap.end:
                spans.append((cursor, min(gap.end, cursor + chunk)))
                cursor = spans[-1][1] + step
            # Fetch a batch of chunks concurrently (network latency dominates), then store them in order
            # and advance the checkpoint, so an interrupted run resumes after the last stored batch.
            for i in range(0, len(spans), self._concurrency):
                batch = spans[i : i + self._concurrency]
                results = await asyncio.gather(
                    *(self._adapter.rest.fetch_candles(inst, timeframe, a, b) for a, b in batch)
                )
                report.requests += len(batch)
                rows = [
                    c
                    for (a, b), got in zip(batch, results, strict=True)
                    for c in got
                    if a <= c.open_time <= b
                ]
                report.fetched += await self._candles.upsert_candles(self._exchange, sym, timeframe, rows)
                checkpoint = _advance(checkpoint, gap, batch[0][0], batch[-1][1], step)
                await self._candles.save_checkpoint(self._exchange, sym, timeframe, checkpoint)

        report.remaining_gaps = await self._candles.find_gaps(
            self._exchange, sym, timeframe, start, end + step
        )
        log.info(
            "candles_loaded",
            symbol=sym,
            timeframe=timeframe.value,
            fetched=report.fetched,
            requests=report.requests,
            coverage=round(report.coverage, 6),
            gaps=len(report.remaining_gaps),
        )
        return report
