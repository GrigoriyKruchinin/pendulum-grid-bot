"""Deterministic synthetic markets for backtest tests."""

import math
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from pendulum.domain.models import Candle

T0 = datetime(2025, 1, 1, tzinfo=UTC)


def _q(x: float) -> Decimal:
    return Decimal(str(round(x, 2)))  # test data generator; float is fine here


def series(prices: list[float], minutes: int = 5) -> list[Candle]:
    out: list[Candle] = []
    for i in range(1, len(prices)):
        o, c = prices[i - 1], prices[i]
        hi, lo = max(o, c) * 1.001, min(o, c) * 0.999
        out.append(
            Candle(T0 + timedelta(minutes=minutes * (i - 1)), _q(o), _q(hi), _q(lo), _q(c), Decimal(10))
        )
    return out


def sideways(n: int, center: float = 100, amp: float = 6, period: int = 288) -> list[Candle]:
    return series([center + amp * math.sin(2 * math.pi * i / period) for i in range(n + 1)])


def trend(n: int, start: float = 100, total_change: float = -0.5) -> list[Candle]:
    return series([start * (1 + total_change * i / n) for i in range(n + 1)])
