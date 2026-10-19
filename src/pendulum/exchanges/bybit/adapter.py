"""Bybit adapter: wires REST, WebSocket and optional account data together."""

from __future__ import annotations

from pendulum.config.settings import BybitSettings
from pendulum.domain.models import Timeframe
from pendulum.exchanges.base import (
    BaseExchangeAdapter,
    FeeRate,
    IAccountData,
    IRestMarketData,
    IWebSocketTicker,
)
from pendulum.exchanges.bybit.rest import MAX_KLINE_LIMIT, BybitRestClient
from pendulum.exchanges.bybit.ws import BybitPublicWs


class BybitAdapter(BaseExchangeAdapter):
    """Bybit V5 spot."""

    exchange_id = "bybit"
    max_candles_per_request = MAX_KLINE_LIMIT
    supported_timeframes = frozenset({Timeframe.M1, Timeframe.M5, Timeframe.H1, Timeframe.D1})

    def __init__(
        self,
        settings: BybitSettings,
        rest: BybitRestClient | None = None,
        ws: BybitPublicWs | None = None,
    ) -> None:
        self._settings = settings
        self._rest = rest or BybitRestClient(settings)
        self._ws = ws or BybitPublicWs(settings)

    @property
    def rest(self) -> IRestMarketData:
        return self._rest

    @property
    def ws(self) -> IWebSocketTicker:
        return self._ws

    @property
    def account(self) -> IAccountData | None:
        return self._rest if self._settings.has_credentials else None

    def default_fee_rate(self) -> FeeRate:
        return FeeRate(maker=self._settings.maker_fee, taker=self._settings.taker_fee)

    async def aclose(self) -> None:
        await self._rest.aclose()
