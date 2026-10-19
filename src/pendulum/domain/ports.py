"""Storage ports (narrow interfaces) consumed by application services.

Implementations live in ``pendulum.storage.repositories``; tests may use in-memory fakes.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any, Protocol

from pendulum.domain.models import Candle, Cycle, EquityPoint, Fill, Instrument, Order, Timeframe


@dataclass(frozen=True, slots=True)
class Gap:
    """Missing candles: every bar with ``start <= open_time <= end`` is absent."""

    start: datetime
    end: datetime


@dataclass(frozen=True, slots=True)
class Checkpoint:
    """Loaded candle span for one series."""

    first_open_time: datetime
    last_open_time: datetime


class ICandleStore(Protocol):
    """Candle persistence."""

    async def upsert_candles(
        self, exchange: str, symbol: str, timeframe: Timeframe, candles: Sequence[Candle]
    ) -> int: ...

    async def get_candles(
        self, exchange: str, symbol: str, timeframe: Timeframe, start: datetime, end: datetime
    ) -> list[Candle]: ...

    async def find_gaps(
        self, exchange: str, symbol: str, timeframe: Timeframe, start: datetime, end: datetime
    ) -> list[Gap]: ...

    async def get_checkpoint(self, exchange: str, symbol: str, timeframe: Timeframe) -> Checkpoint | None: ...

    async def save_checkpoint(
        self, exchange: str, symbol: str, timeframe: Timeframe, checkpoint: Checkpoint
    ) -> None: ...


class IInstrumentStore(Protocol):
    """Instrument (exchange filters) persistence."""

    async def upsert_instruments(self, exchange: str, instruments: Sequence[Instrument]) -> None: ...

    async def get_instrument(self, exchange: str, symbol: str) -> Instrument | None: ...


class IBacktestRunStore(Protocol):
    """Backtest result persistence."""

    async def save_run(
        self,
        *,
        kind: str,
        git_sha: str,
        exchange: str,
        symbol: str,
        timeframe: Timeframe,
        period_start: datetime,
        period_end: datetime,
        params: dict[str, Any],
        metrics: dict[str, Any],
        assumptions: dict[str, Any],
        parent_id: int | None = None,
    ) -> int: ...


class ITradingSessionStore(Protocol):
    """Paper/live session persistence."""

    async def create_session(
        self, *, mode: str, exchange: str, symbol: str, params: dict[str, Any]
    ) -> int: ...

    async def save_state(self, session_id: int, state: dict[str, Any], status: str | None = None) -> None: ...

    async def get_running(
        self, mode: str, exchange: str, symbol: str
    ) -> tuple[int, dict[str, Any]] | None: ...

    async def upsert_order(self, session_id: int, order: Order) -> None: ...

    async def add_fill(self, session_id: int, fill: Fill) -> None: ...

    async def add_cycle(self, session_id: int, cycle: Cycle) -> None: ...

    async def add_equity_point(self, session_id: int, point: EquityPoint) -> None: ...


@dataclass(frozen=True, slots=True)
class SessionInfo:
    """Read model of a trading session."""

    id: int
    mode: str
    exchange: str
    symbol: str
    status: str
    params: dict[str, Any]
    state: dict[str, Any]
    started_at: datetime


class ISessionReader(Protocol):
    """Read side for reporting on paper/live sessions."""

    async def list_sessions(self, mode: str, statuses: Sequence[str]) -> list[SessionInfo]: ...

    async def cycle_stats(self, session_id: int, since: datetime) -> tuple[int, Decimal]: ...


class IScannerStore(Protocol):
    """Pair scanner results persistence."""

    async def save_scores(
        self, run_at: datetime, exchange: str, rows: Sequence[tuple[str, Decimal, dict[str, Any]]]
    ) -> None: ...
