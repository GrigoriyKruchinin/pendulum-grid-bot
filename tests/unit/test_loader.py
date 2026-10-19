from datetime import UTC, datetime, timedelta

import pytest

from pendulum.domain.models import Symbol, Timeframe
from pendulum.market_data.loader import HistoryLoader, floor_time, last_closed_open_time
from tests.fakes import FakeAdapter, FakeRest, InMemoryCandleStore, InMemoryInstrumentStore

T0 = datetime(2025, 1, 1, tzinfo=UTC)
M = timedelta(minutes=1)
SOL = Symbol("SOL", "USDT")


def _loader(rest: FakeRest, now: datetime) -> tuple[HistoryLoader, InMemoryCandleStore]:
    store = InMemoryCandleStore()
    return HistoryLoader(FakeAdapter(rest), store, InMemoryInstrumentStore(), now=lambda: now), store


def test_time_alignment() -> None:
    assert floor_time(T0 + 90 * timedelta(seconds=1), Timeframe.M1) == T0 + M
    assert last_closed_open_time(T0 + 5 * M + timedelta(seconds=10), Timeframe.M5) == T0


async def test_loads_in_chunks_and_skips_open_bar() -> None:
    rest = FakeRest(Timeframe.M1, limit=5)
    loader, store = _loader(rest, now=T0 + 12 * M + timedelta(seconds=30))
    report = await loader.load(SOL, Timeframe.M1, T0)
    assert report.end == T0 + 11 * M  # bar opened at 12:00 is still open
    assert report.fetched == 12
    assert report.requests == 3
    assert report.coverage == 1.0
    assert len(store.data[("fake", "SOL/USDT", Timeframe.M1)]) == 12


async def test_resume_fetches_only_new_bars() -> None:
    rest = FakeRest(Timeframe.M1, limit=100)
    loader, _ = _loader(rest, now=T0 + 10 * M)
    await loader.load(SOL, Timeframe.M1, T0)
    rest.calls.clear()
    loader._now = lambda: T0 + 15 * M
    report = await loader.load(SOL, Timeframe.M1, T0)
    assert rest.calls == [(T0 + 10 * M, T0 + 14 * M)]
    assert report.fetched == 5


async def test_exchange_holes_reported_and_not_refetched() -> None:
    holes = {T0 + 3 * M, T0 + 4 * M}
    rest = FakeRest(Timeframe.M1, holes=holes, limit=100)
    loader, _ = _loader(rest, now=T0 + 10 * M)
    report = await loader.load(SOL, Timeframe.M1, T0)
    assert report.missing_bars == 2
    assert report.coverage == 0.8
    rest.calls.clear()
    again = await loader.load(SOL, Timeframe.M1, T0)
    assert rest.calls == []
    assert again.missing_bars == 2
    await loader.load(SOL, Timeframe.M1, T0, refetch_gaps=True)
    assert rest.calls == [(T0 + 3 * M, T0 + 4 * M)]


async def test_backfill_earlier_period() -> None:
    rest = FakeRest(Timeframe.M1, limit=100)
    loader, _ = _loader(rest, now=T0 + 10 * M)
    await loader.load(SOL, Timeframe.M1, T0 + 5 * M)
    rest.calls.clear()
    await loader.load(SOL, Timeframe.M1, T0)
    assert rest.calls == [(T0, T0 + 4 * M)]


async def test_interrupted_backfill_does_not_hide_unfetched_span() -> None:
    rest = FakeRest(Timeframe.M1, limit=3)
    loader, store = _loader(rest, now=T0 + 20 * M)
    loader._concurrency = 1
    await loader.load(SOL, Timeframe.M1, T0 + 10 * M)  # loads 10..19, checkpoint [10, 19]

    class FetchError(Exception):
        pass

    original = rest.fetch_candles
    calls = 0

    async def flaky(*args: object) -> list:  # type: ignore[type-arg]
        nonlocal calls
        calls += 1
        if calls > 1:
            raise FetchError
        return await original(*args)  # type: ignore[arg-type]

    rest.fetch_candles = flaky  # type: ignore[method-assign,assignment]
    with pytest.raises(FetchError):
        await loader.load(SOL, Timeframe.M1, T0)  # gap 0..9 interrupted after the first batch
    cp = store.checkpoints[("fake", "SOL/USDT", Timeframe.M1)]
    assert cp.first_open_time == T0 + 10 * M  # unfinished earlier gap not merged
    rest.fetch_candles = original  # type: ignore[method-assign]
    report = await loader.load(SOL, Timeframe.M1, T0)
    assert report.missing_bars == 0
