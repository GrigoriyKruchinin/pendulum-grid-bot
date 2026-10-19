import time

import pytest

from pendulum.config.settings import ExchangeSettings
from pendulum.exchanges.bybit.adapter import BybitAdapter
from pendulum.exchanges.rate_limit import AsyncTokenBucket, backoff_delay
from pendulum.exchanges.registry import ExchangeRegistry, default_registry


async def test_registry_creates_active_adapter(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EXCHANGE_ACTIVE", "BYBIT")
    adapter = default_registry().create_active(ExchangeSettings(_env_file=None))
    assert isinstance(adapter, BybitAdapter)
    await adapter.aclose()


def test_registry_unknown_and_duplicate() -> None:
    reg = default_registry()
    with pytest.raises(ValueError, match="available: bybit"):
        reg.create("kraken")
    with pytest.raises(ValueError, match="already registered"):
        reg.register("Bybit", lambda: BybitAdapter.__new__(BybitAdapter))
    assert ExchangeRegistry().available() == []


async def test_token_bucket_throttles() -> None:
    bucket = AsyncTokenBucket(rate=100, capacity=2)
    start = time.monotonic()
    for _ in range(4):
        await bucket.acquire()
    assert time.monotonic() - start >= 0.015


def test_backoff_bounded() -> None:
    assert all(0 <= backoff_delay(a) <= 30 for a in range(20))
