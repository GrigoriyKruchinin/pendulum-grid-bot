"""Behaviour every ``BaseExchangeAdapter`` must satisfy. New adapters reuse ``check_adapter_contract``."""

from datetime import timedelta

from pendulum.domain.models import Symbol, Timeframe
from pendulum.exchanges.base import BaseExchangeAdapter


async def check_adapter_contract(adapter: BaseExchangeAdapter, symbol: Symbol) -> None:
    assert adapter.exchange_id
    assert adapter.max_candles_per_request > 0
    assert Timeframe.M1 in adapter.supported_timeframes

    instruments = await adapter.rest.fetch_instruments()
    assert instruments, "adapter must return at least one instrument"
    inst = adapter.find_instrument(instruments, symbol)
    f = inst.filters
    assert f.tick_size > 0 and f.step_size > 0 and f.min_notional >= 0

    tickers = await adapter.rest.fetch_tickers()
    t = next(t for t in tickers if t.symbol == symbol)
    assert 0 < t.bid <= t.ask

    end = t.ts.replace(second=0, microsecond=0) - timedelta(minutes=1)
    candles = await adapter.rest.fetch_candles(inst, Timeframe.M1, end - timedelta(minutes=2), end)
    assert candles, "adapter must return candles"
    times = [c.open_time for c in candles]
    assert times == sorted(times), "candles must be oldest-first"
    assert all(c.open_time.tzinfo is not None for c in candles)

    fee = await adapter.fee_rate(inst)
    assert 0 <= fee.maker < 0.01 and 0 <= fee.taker < 0.01
