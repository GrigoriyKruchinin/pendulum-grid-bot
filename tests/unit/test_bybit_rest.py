from datetime import UTC, datetime
from decimal import Decimal

import httpx
import pytest
import respx
from pydantic import SecretStr

from pendulum.domain.models import Instrument, Symbol, Timeframe
from pendulum.exchanges.base import ExchangeError
from pendulum.exchanges.bybit.adapter import BybitAdapter
from pendulum.exchanges.bybit.rest import MAINNET_URL, BybitRestClient
from tests.fixtures import bybit as fx
from tests.helpers import bybit_settings


async def _no_sleep(_: int) -> None:
    return None


def _client(**kw: object) -> BybitRestClient:
    settings = bybit_settings(**kw)
    return BybitRestClient(settings, sleep=_no_sleep, max_attempts=3)


@respx.mock
async def test_instruments_parsed_and_filtered() -> None:
    respx.get(f"{MAINNET_URL}/v5/market/instruments-info").mock(
        return_value=httpx.Response(200, json=fx.INSTRUMENTS)
    )
    insts = await _client().fetch_instruments()
    assert [str(i.symbol) for i in insts] == ["SOL/USDT", "MEME/USDT"]
    sol, meme = insts
    assert sol.filters.tick_size == Decimal("0.01")
    assert sol.filters.step_size == Decimal("0.0001")
    assert sol.filters.min_notional == Decimal(5)
    assert not sol.risky and meme.risky


@respx.mock
async def test_klines_oldest_first_decimal() -> None:
    route = respx.get(f"{MAINNET_URL}/v5/market/kline").mock(return_value=httpx.Response(200, json=fx.KLINES))
    insts = [i for i in await _instruments() if i.symbol == Symbol("SOL", "USDT")]
    start = datetime(2026, 10, 19, tzinfo=UTC)
    candles = await _client().fetch_candles(insts[0], Timeframe.M1, start, start)
    assert [c.close for c in candles] == [Decimal("104.54"), Decimal("104.5"), Decimal("104.47")]
    assert candles[0].open_time < candles[-1].open_time
    params = route.calls.last.request.url.params
    assert params["symbol"] == "SOLUSDT" and params["interval"] == "1" and params["limit"] == "1000"


async def _instruments() -> list[Instrument]:
    with respx.mock:
        respx.get(f"{MAINNET_URL}/v5/market/instruments-info").mock(
            return_value=httpx.Response(200, json=fx.INSTRUMENTS)
        )
        return await _client().fetch_instruments()


@respx.mock
async def test_retries_rate_limit_then_succeeds() -> None:
    route = respx.get(f"{MAINNET_URL}/v5/market/instruments-info").mock(
        side_effect=[
            httpx.Response(200, json=fx.RATE_LIMITED),
            httpx.Response(503),
            httpx.ConnectError("boom"),
            httpx.Response(200, json=fx.INSTRUMENTS),
        ]
    )
    client = BybitRestClient(bybit_settings(), sleep=_no_sleep, max_attempts=4)
    assert len(await client.fetch_instruments()) == 2
    assert route.call_count == 4


@respx.mock
async def test_gives_up_after_max_attempts() -> None:
    respx.get(f"{MAINNET_URL}/v5/market/instruments-info").mock(return_value=httpx.Response(429))
    with pytest.raises(ExchangeError, match="after 3 attempts"):
        await _client().fetch_instruments()


@respx.mock
async def test_non_retryable_error_raises_immediately() -> None:
    route = respx.get(f"{MAINNET_URL}/v5/market/kline").mock(
        return_value=httpx.Response(200, json={"retCode": 10001, "retMsg": "params error"})
    )
    with pytest.raises(ExchangeError, match="10001"):
        await _client().get("/v5/market/kline", {"category": "spot"})
    assert route.call_count == 1


@respx.mock
async def test_fee_rate_requires_and_signs_credentials() -> None:
    route = respx.get(f"{MAINNET_URL}/v5/account/fee-rate").mock(
        return_value=httpx.Response(200, json=fx.FEE_RATE)
    )
    with pytest.raises(ExchangeError, match="credentials"):
        await _client().get("/v5/account/fee-rate", {"category": "spot"}, auth=True)
    client = _client(api_key=SecretStr("k"), api_secret=SecretStr("s"))
    await client.get("/v5/account/fee-rate", {"category": "spot", "symbol": "SOLUSDT"}, auth=True)
    headers = route.calls.last.request.headers
    assert headers["X-BAPI-API-KEY"] == "k"
    assert len(headers["X-BAPI-SIGN"]) == 64


@respx.mock
async def test_tickers_skip_unknown_symbols() -> None:
    respx.get(f"{MAINNET_URL}/v5/market/instruments-info").mock(
        return_value=httpx.Response(200, json=fx.INSTRUMENTS)
    )
    respx.get(f"{MAINNET_URL}/v5/market/tickers").mock(return_value=httpx.Response(200, json=fx.TICKERS))
    tickers = await _client().fetch_tickers()
    assert [str(t.symbol) for t in tickers] == ["SOL/USDT"]
    assert tickers[0].volume_24h_quote == Decimal("250000000.5")


async def test_adapter_uses_default_fee_without_credentials() -> None:
    adapter = BybitAdapter(bybit_settings(maker_fee=Decimal("0.0008")))
    assert adapter.account is None
    insts = await _instruments()
    fee = await adapter.fee_rate(insts[0])
    assert fee.maker == Decimal("0.0008")
    await adapter.aclose()
