"""Bybit V5 REST client (spot). Numbers arrive as strings and are parsed straight into Decimal."""

from __future__ import annotations

import hashlib
import hmac
import time
from collections.abc import Awaitable, Callable, Mapping
from datetime import UTC, datetime
from typing import Any, cast
from urllib.parse import urlencode

import httpx

from pendulum.config.settings import BybitSettings
from pendulum.domain.models import Candle, Instrument, MarketFilters, Symbol, Ticker, Timeframe
from pendulum.domain.money import to_decimal
from pendulum.exchanges.base import ExchangeError, FeeRate
from pendulum.exchanges.rate_limit import AsyncTokenBucket, sleep_backoff
from pendulum.logging import get_logger

MAINNET_URL = "https://api.bybit.com"
TESTNET_URL = "https://api-testnet.bybit.com"
MAX_KLINE_LIMIT = 1000
_RECV_WINDOW = "5000"
# retCodes worth retrying: rate limit, server busy/timeouts.
_RETRYABLE_RET_CODES = frozenset({10006, 10016, 10002})
_INTERVALS = {Timeframe.M1: "1", Timeframe.M5: "5", Timeframe.H1: "60", Timeframe.D1: "D"}

log = get_logger(__name__)

Json = dict[str, Any]  # Bybit payloads are loosely typed JSON.


def _ms(dt: datetime) -> int:
    return int(dt.timestamp() * 1000)


def _from_ms(value: str | int) -> datetime:
    return datetime.fromtimestamp(int(value) / 1000, tz=UTC)


class BybitRestClient:
    """Thin async Bybit V5 client with rate limiting, retries and optional HMAC auth."""

    def __init__(
        self,
        settings: BybitSettings,
        *,
        client: httpx.AsyncClient | None = None,
        bucket: AsyncTokenBucket | None = None,
        max_attempts: int = 5,
        sleep: Callable[[int], Awaitable[None]] = sleep_backoff,
    ) -> None:
        self._settings = settings
        base_url = TESTNET_URL if settings.testnet else MAINNET_URL
        self._client = client or httpx.AsyncClient(base_url=base_url, timeout=httpx.Timeout(10.0))
        # Public IP limit is 600 req / 5 s; stay well below it.
        self._bucket = bucket or AsyncTokenBucket(rate=50, capacity=50)
        self._max_attempts = max_attempts
        self._sleep = sleep

    async def aclose(self) -> None:
        """Close the HTTP client."""
        await self._client.aclose()

    def _sign_headers(self, query: str) -> dict[str, str]:
        key = self._settings.api_key
        secret = self._settings.api_secret
        if key is None or secret is None:
            raise ExchangeError("Bybit credentials are not configured")
        ts = str(int(time.time() * 1000))
        payload = ts + key.get_secret_value() + _RECV_WINDOW + query
        sign = hmac.new(secret.get_secret_value().encode(), payload.encode(), hashlib.sha256).hexdigest()
        return {
            "X-BAPI-API-KEY": key.get_secret_value(),
            "X-BAPI-TIMESTAMP": ts,
            "X-BAPI-RECV-WINDOW": _RECV_WINDOW,
            "X-BAPI-SIGN": sign,
        }

    async def get(self, path: str, params: Mapping[str, str | int], *, auth: bool = False) -> Json:
        """GET ``path`` and return ``result``; retries transient failures with backoff."""
        query = urlencode(sorted(params.items()))
        last_error = "unknown error"
        for attempt in range(self._max_attempts):
            await self._bucket.acquire()
            headers = self._sign_headers(query) if auth else {}
            try:
                resp = await self._client.get(f"{path}?{query}", headers=headers)
            except httpx.TransportError as exc:
                last_error = f"transport error: {type(exc).__name__}"
            else:
                if resp.status_code in (403, 429) or resp.status_code >= 500:
                    last_error = f"HTTP {resp.status_code}"
                elif resp.status_code != 200:
                    raise ExchangeError(f"Bybit {path} HTTP {resp.status_code}")
                else:
                    body = cast(Json, resp.json())
                    code = int(body.get("retCode", -1))
                    if code == 0:
                        return cast(Json, body["result"])
                    if code not in _RETRYABLE_RET_CODES:
                        raise ExchangeError(f"Bybit {path} retCode={code} retMsg={body.get('retMsg')}")
                    last_error = f"retCode={code}"
            log.warning("bybit_retry", path=path, attempt=attempt + 1, error=last_error)
            await self._sleep(attempt)
        raise ExchangeError(f"Bybit {path} failed after {self._max_attempts} attempts: {last_error}")

    async def fetch_instruments(self) -> list[Instrument]:
        """Spot instruments with ``status == Trading``."""
        result = await self.get("/v5/market/instruments-info", {"category": "spot"})
        instruments: list[Instrument] = []
        for item in result.get("list", []):
            if item.get("status") != "Trading":
                continue
            lot = item["lotSizeFilter"]
            instruments.append(
                Instrument(
                    symbol=Symbol(item["baseCoin"], item["quoteCoin"]),
                    exchange_symbol=item["symbol"],
                    filters=MarketFilters(
                        tick_size=to_decimal(item["priceFilter"]["tickSize"]),
                        step_size=to_decimal(lot["basePrecision"]),
                        min_qty=to_decimal(lot["minOrderQty"]),
                        min_notional=to_decimal(lot["minOrderAmt"]),
                    ),
                    risky=item.get("innovation") == "1" or item.get("stTag") == "1",
                )
            )
        return instruments

    async def fetch_candles(
        self, instrument: Instrument, timeframe: Timeframe, start: datetime, end: datetime
    ) -> list[Candle]:
        """Up to 1000 bars in ``[start, end]``, oldest first."""
        result = await self.get(
            "/v5/market/kline",
            {
                "category": "spot",
                "symbol": instrument.exchange_symbol,
                "interval": _INTERVALS[timeframe],
                "start": _ms(start),
                "end": _ms(end),
                "limit": MAX_KLINE_LIMIT,
            },
        )
        rows = result.get("list", [])
        candles = [
            Candle(
                open_time=_from_ms(r[0]),
                open=to_decimal(r[1]),
                high=to_decimal(r[2]),
                low=to_decimal(r[3]),
                close=to_decimal(r[4]),
                volume=to_decimal(r[5]),
                turnover=to_decimal(r[6]),
            )
            for r in rows
        ]
        candles.sort(key=lambda c: c.open_time)  # Bybit returns newest first
        return candles

    async def fetch_tickers(self) -> list[Ticker]:
        """24h spot tickers. Symbols are split using the instrument list."""
        instruments = {i.exchange_symbol: i.symbol for i in await self.fetch_instruments()}
        result = await self.get("/v5/market/tickers", {"category": "spot"})
        now = datetime.now(UTC)
        tickers: list[Ticker] = []
        for item in result.get("list", []):
            symbol = instruments.get(item["symbol"])
            if symbol is None or not item.get("bid1Price") or not item.get("ask1Price"):
                continue
            tickers.append(
                Ticker(
                    symbol=symbol,
                    bid=to_decimal(item["bid1Price"]),
                    ask=to_decimal(item["ask1Price"]),
                    last=to_decimal(item["lastPrice"]),
                    volume_24h_quote=to_decimal(item["turnover24h"]),
                    ts=now,
                )
            )
        return tickers

    async def fetch_fee_rate(self, instrument: Instrument) -> FeeRate:
        """Account maker/taker fee for ``instrument`` (requires a read-only key)."""
        result = await self.get(
            "/v5/account/fee-rate", {"category": "spot", "symbol": instrument.exchange_symbol}, auth=True
        )
        items = result.get("list", [])
        if not items:
            raise ExchangeError(f"no fee rate returned for {instrument.symbol}")
        return FeeRate(maker=to_decimal(items[0]["makerFeeRate"]), taker=to_decimal(items[0]["takerFeeRate"]))
