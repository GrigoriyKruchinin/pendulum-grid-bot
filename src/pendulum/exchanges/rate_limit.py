"""Async rate limiting and retry helpers."""

from __future__ import annotations

import asyncio
import random
import time
from collections.abc import Awaitable, Callable


class AsyncTokenBucket:
    """Token bucket: ``rate`` tokens per second, bursts up to ``capacity``."""

    def __init__(self, rate: float, capacity: int, clock: Callable[[], float] = time.monotonic) -> None:
        if rate <= 0 or capacity <= 0:
            raise ValueError("rate and capacity must be positive")
        self._rate = rate
        self._capacity = float(capacity)
        self._tokens = float(capacity)
        self._clock = clock
        self._updated = clock()
        self._lock = asyncio.Lock()

    def _refill(self) -> None:
        now = self._clock()
        self._tokens = min(self._capacity, self._tokens + (now - self._updated) * self._rate)
        self._updated = now

    async def acquire(self, tokens: float = 1.0) -> None:
        """Wait until ``tokens`` are available and consume them."""
        async with self._lock:
            while True:
                self._refill()
                if self._tokens >= tokens:
                    self._tokens -= tokens
                    return
                await asyncio.sleep((tokens - self._tokens) / self._rate)


def backoff_delay(attempt: int, base: float = 0.5, cap: float = 30.0) -> float:
    """Exponential backoff with full jitter (attempt starts at 0)."""
    return random.uniform(0, min(cap, base * 2**attempt))  # noqa: S311 - jitter, not crypto


async def sleep_backoff(attempt: int, sleep: Callable[[float], Awaitable[None]] = asyncio.sleep) -> None:
    """Sleep for the backoff delay of ``attempt``."""
    await sleep(backoff_delay(attempt))
