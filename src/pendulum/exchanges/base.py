"""Exchange interfaces.

Adding an exchange means implementing these interfaces in a new subpackage and registering a factory in
``pendulum.exchanges.registry``; the grid core never imports exchange-specific code.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Protocol

from pendulum.domain.models import Candle, Instrument, StreamGap, Symbol, Ticker, Timeframe, TradeTick


class ExchangeError(Exception):
    """Exchange returned an error or could not be reached after retries."""


@dataclass(frozen=True, slots=True)
class FeeRate:
    """Maker/taker fee fractions for a symbol."""

    maker: Decimal
    taker: Decimal


class IRestMarketData(Protocol):
    """Public market data over REST."""

    async def fetch_instruments(self) -> list[Instrument]:
        """All spot instruments currently trading."""
        ...

    async def fetch_candles(
        self, instrument: Instrument, timeframe: Timeframe, start: datetime, end: datetime
    ) -> list[Candle]:
        """Bars with ``start <= open_time <= end`` (at most ``max_candles_per_request``), oldest first."""
        ...

    async def fetch_tickers(self) -> list[Ticker]:
        """24h tickers with best bid/ask for all spot symbols."""
        ...


class IWebSocketTicker(Protocol):
    """Live public trade stream."""

    def stream_trades(self, instruments: Sequence[Instrument]) -> AsyncIterator[TradeTick | StreamGap]:
        """Yield trade prints; yields ``StreamGap`` after a reconnect so callers can backfill."""
        ...


class IAccountData(Protocol):
    """Authenticated read-only account data (optional in Stage 1)."""

    async def fetch_fee_rate(self, instrument: Instrument) -> FeeRate:
        """Actual maker/taker fee for the account tier."""
        ...


class BaseExchangeAdapter(ABC):
    """Bundle of an exchange's capabilities behind narrow interfaces."""

    exchange_id: str
    max_candles_per_request: int
    supported_timeframes: frozenset[Timeframe]

    @property
    @abstractmethod
    def rest(self) -> IRestMarketData:
        """Public REST market data."""

    @property
    @abstractmethod
    def ws(self) -> IWebSocketTicker:
        """Public WebSocket stream."""

    @property
    @abstractmethod
    def account(self) -> IAccountData | None:
        """Authenticated account data, or None when no credentials are configured."""

    @abstractmethod
    def default_fee_rate(self) -> FeeRate:
        """Fee schedule from settings (used when account data is unavailable)."""

    async def fee_rate(self, instrument: Instrument) -> FeeRate:
        """Account fee rate when credentials exist, otherwise the configured default."""
        if self.account is None:
            return self.default_fee_rate()
        return await self.account.fetch_fee_rate(instrument)

    def find_instrument(self, instruments: Sequence[Instrument], symbol: Symbol) -> Instrument:
        """Pick ``symbol`` from a fetched instrument list."""
        for inst in instruments:
            if inst.symbol == symbol:
                return inst
        raise ExchangeError(f"{symbol} is not traded on {self.exchange_id}")

    @abstractmethod
    async def aclose(self) -> None:
        """Release network resources."""
