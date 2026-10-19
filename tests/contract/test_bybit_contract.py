import asyncio

import httpx
import pytest
import respx

from pendulum.domain.models import Symbol, TradeTick
from pendulum.exchanges.bybit.adapter import BybitAdapter
from pendulum.exchanges.bybit.rest import MAINNET_URL
from tests.contract.adapter_contract import check_adapter_contract
from tests.fixtures import bybit as fx
from tests.helpers import bybit_settings


@respx.mock
async def test_bybit_adapter_contract_offline() -> None:
    respx.get(f"{MAINNET_URL}/v5/market/instruments-info").mock(
        return_value=httpx.Response(200, json=fx.INSTRUMENTS)
    )
    respx.get(f"{MAINNET_URL}/v5/market/tickers").mock(return_value=httpx.Response(200, json=fx.TICKERS))
    respx.get(f"{MAINNET_URL}/v5/market/kline").mock(return_value=httpx.Response(200, json=fx.KLINES))
    adapter = BybitAdapter(bybit_settings())
    await check_adapter_contract(adapter, Symbol("SOL", "USDT"))
    await adapter.aclose()


@pytest.mark.integration
@pytest.mark.live
async def test_bybit_adapter_contract_live() -> None:
    adapter = BybitAdapter(bybit_settings())
    try:
        await check_adapter_contract(adapter, Symbol("BTC", "USDT"))
    finally:
        await adapter.aclose()


@pytest.mark.integration
@pytest.mark.live
async def test_bybit_ws_live_trades() -> None:
    adapter = BybitAdapter(bybit_settings())
    try:
        insts = await adapter.rest.fetch_instruments()
        btc = adapter.find_instrument(insts, Symbol("BTC", "USDT"))
        stream = adapter.ws.stream_trades([btc])
        async with asyncio.timeout(30):
            first = await anext(aiter(stream))
        assert isinstance(first, TradeTick)
        assert first.price > 0
        await stream.aclose()  # type: ignore[attr-defined]
    finally:
        await adapter.aclose()
