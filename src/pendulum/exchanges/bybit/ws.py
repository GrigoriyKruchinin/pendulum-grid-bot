"""Bybit V5 public WebSocket (spot trades) with heartbeat, reconnect, resubscribe and gap signalling."""

from __future__ import annotations

import asyncio
import contextlib
import json
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from contextlib import AbstractAsyncContextManager
from datetime import UTC, datetime
from typing import Any, Protocol

from websockets.asyncio.client import connect as ws_connect
from websockets.exceptions import WebSocketException

from pendulum.config.settings import BybitSettings
from pendulum.domain.models import Instrument, StreamGap, Symbol, TradeTick
from pendulum.domain.money import to_decimal
from pendulum.exchanges.rate_limit import sleep_backoff
from pendulum.logging import get_logger

MAINNET_WS = "wss://stream.bybit.com/v5/public/spot"
TESTNET_WS = "wss://stream-testnet.bybit.com/v5/public/spot"
_MAX_ARGS_PER_SUBSCRIBE = 10  # Bybit spot limit per subscribe request

log = get_logger(__name__)


class WsConnection(Protocol):
    """Minimal WebSocket connection surface used by the client."""

    async def send(self, message: str) -> None: ...

    async def recv(self) -> str | bytes: ...


ConnectFn = Callable[[str], AbstractAsyncContextManager[WsConnection]]


def _default_connect(url: str) -> AbstractAsyncContextManager[WsConnection]:
    # App-level JSON pings are used instead of protocol pings (Bybit requirement).
    return ws_connect(url, ping_interval=None, open_timeout=10, close_timeout=5, max_queue=4096)


class StreamSilentError(Exception):
    """No message received within the silence timeout."""


def parse_trade_message(message: dict[str, Any], symbols: dict[str, Symbol]) -> list[TradeTick]:
    """Convert a ``publicTrade.*`` push into ticks (other messages yield nothing)."""
    topic = message.get("topic", "")
    if not isinstance(topic, str) or not topic.startswith("publicTrade."):
        return []
    ticks: list[TradeTick] = []
    for item in message.get("data", []):
        symbol = symbols.get(item.get("s", ""))
        if symbol is None:
            continue
        ticks.append(
            TradeTick(
                symbol=symbol,
                ts=datetime.fromtimestamp(int(item["T"]) / 1000, tz=UTC),
                price=to_decimal(item["p"]),
                qty=to_decimal(item["v"]),
            )
        )
    return ticks


class BybitPublicWs:
    """``IWebSocketTicker`` for Bybit spot."""

    def __init__(
        self,
        settings: BybitSettings,
        *,
        connect: ConnectFn = _default_connect,
        ping_interval: float = 20.0,
        silence_timeout: float = 60.0,
        sleep: Callable[[int], Awaitable[None]] = sleep_backoff,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
        max_reconnects: int | None = None,
    ) -> None:
        self._url = TESTNET_WS if settings.testnet else MAINNET_WS
        self._connect = connect
        self._ping_interval = ping_interval
        self._silence_timeout = silence_timeout
        self._sleep = sleep
        self._now = now
        self._max_reconnects = max_reconnects

    async def _pinger(self, ws: WsConnection) -> None:
        while True:
            await asyncio.sleep(self._ping_interval)
            await ws.send(json.dumps({"op": "ping"}))

    async def _subscribe(self, ws: WsConnection, exchange_symbols: Sequence[str]) -> None:
        topics = [f"publicTrade.{s}" for s in exchange_symbols]
        for i in range(0, len(topics), _MAX_ARGS_PER_SUBSCRIBE):
            await ws.send(json.dumps({"op": "subscribe", "args": topics[i : i + _MAX_ARGS_PER_SUBSCRIBE]}))

    async def stream_trades(self, instruments: Sequence[Instrument]) -> AsyncIterator[TradeTick | StreamGap]:
        """Yield trades forever; after each reconnect yield a ``StreamGap`` per symbol."""
        symbols = {i.exchange_symbol: i.symbol for i in instruments}
        disconnected_at: datetime | None = None
        attempt = 0
        reconnects = 0
        while True:
            try:
                async with self._connect(self._url) as ws:
                    await self._subscribe(ws, list(symbols))
                    log.info("ws_connected", url=self._url, symbols=len(symbols))
                    if disconnected_at is not None:
                        gap_end = self._now()
                        for sym in symbols.values():
                            yield StreamGap(sym, disconnected_at, gap_end)
                        disconnected_at = None
                    attempt = 0
                    pinger = asyncio.create_task(self._pinger(ws))
                    try:
                        while True:
                            try:
                                async with asyncio.timeout(self._silence_timeout):
                                    raw = await ws.recv()
                            except TimeoutError as exc:
                                raise StreamSilentError from exc
                            message = json.loads(raw)
                            if message.get("success") is False:
                                log.error("ws_subscribe_failed", ret_msg=message.get("ret_msg"))
                            for tick in parse_trade_message(message, symbols):
                                yield tick
                    finally:
                        pinger.cancel()
                        with contextlib.suppress(asyncio.CancelledError):
                            await pinger
            except (WebSocketException, OSError, StreamSilentError, json.JSONDecodeError) as exc:
                log.warning("ws_disconnected", error=type(exc).__name__, attempt=attempt + 1)
            if disconnected_at is None:
                disconnected_at = self._now()
            reconnects += 1
            if self._max_reconnects is not None and reconnects > self._max_reconnects:
                return
            await self._sleep(attempt)
            attempt += 1
