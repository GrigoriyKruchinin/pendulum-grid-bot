"""Candle resampling (e.g. 1m -> 5m) and gap checks for simulation input."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timedelta

from pendulum.domain.models import Candle, Timeframe
from pendulum.domain.money import ZERO


def resample(candles: Sequence[Candle], source: Timeframe, target: Timeframe) -> list[Candle]:
    """Aggregate ``source`` bars into ``target`` bars aligned to the epoch. Partial buckets are kept."""
    if target.seconds % source.seconds != 0:
        raise ValueError(f"cannot resample {source} into {target}")
    if source == target:
        return list(candles)
    buckets: dict[datetime, list[Candle]] = {}
    for c in candles:
        offset = int(c.open_time.timestamp()) % target.seconds
        buckets.setdefault(c.open_time - timedelta(seconds=offset), []).append(c)
    return [_merge(start, bucket) for start, bucket in sorted(buckets.items())]


def _merge(start: datetime, bucket: list[Candle]) -> Candle:
    return Candle(
        open_time=start,
        open=bucket[0].open,
        high=max(c.high for c in bucket),
        low=min(c.low for c in bucket),
        close=bucket[-1].close,
        volume=sum((c.volume for c in bucket), ZERO),
        turnover=sum((c.turnover for c in bucket), ZERO),
    )


def count_missing(candles: Sequence[Candle], timeframe: Timeframe) -> int:
    """Number of missing bars between the first and last candle."""
    if len(candles) < 2:
        return 0
    span = int((candles[-1].open_time - candles[0].open_time).total_seconds()) // timeframe.seconds + 1
    return span - len(candles)
