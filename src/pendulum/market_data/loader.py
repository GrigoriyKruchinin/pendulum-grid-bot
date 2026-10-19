"""Historical candle loader with checkpoints, resume and gap backfill.

Strategy: ask the store which bars are missing in the requested window and fetch only those. A checkpoint
records the span already attempted, so holes the exchange itself has (downtime, pre-listing) are not
re-requested on every run unless ``refetch_gaps`` is set.
"""

from __future__ import annotations

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


class HistoryLoader:
    """Loads and backfills candles for one exchange."""

    def __init__(
        self,
        adapter: BaseExchangeAdapter,
        candles: ICandleStore,
        instruments: IInstrumentStore,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._adapter = adapter
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
            cursor = gap.start
            while cursor <= gap.end:
                chunk_end = min(gap.end, cursor + chunk)
                candles = await self._adapter.rest.fetch_candles(inst, timeframe, cursor, chunk_end)
                report.requests += 1
                candles = [c for c in candles if cursor <= c.open_time <= chunk_end]
                report.fetched += await self._candles.upsert_candles(self._exchange, sym, timeframe, candles)
                checkpoint = Checkpoint(
                    min(checkpoint.first_open_time, gap.start) if checkpoint else gap.start,
                    max(checkpoint.last_open_time, chunk_end) if checkpoint else chunk_end,
                )
                await self._candles.save_checkpoint(self._exchange, sym, timeframe, checkpoint)
                cursor = chunk_end + step

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
