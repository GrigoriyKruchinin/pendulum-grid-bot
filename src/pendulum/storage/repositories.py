"""PostgreSQL implementations of the storage ports."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select, text, update
from sqlalchemy.dialects.postgresql import insert

from pendulum.domain.models import (
    Candle,
    Cycle,
    EquityPoint,
    Fill,
    Instrument,
    MarketFilters,
    Order,
    Symbol,
    Timeframe,
)
from pendulum.domain.ports import Checkpoint, Gap
from pendulum.storage.db import Database
from pendulum.storage.models import (
    BacktestRunRow,
    CandleRow,
    CycleRow,
    EquityPointRow,
    FillRow,
    InstrumentRow,
    LoadCheckpointRow,
    OrderRow,
    ScannerScoreRow,
    TradingSessionRow,
)

# asyncpg caps bind parameters at 32767 per statement; candles use 10 columns.
_CANDLE_BATCH = 2000


class CandleRepository:
    """``ICandleStore`` on PostgreSQL."""

    def __init__(self, db: Database) -> None:
        self._db = db

    async def upsert_candles(
        self, exchange: str, symbol: str, timeframe: Timeframe, candles: Sequence[Candle]
    ) -> int:
        """Insert or update bars (the latest bar may be revised while it is still open)."""
        if not candles:
            return 0
        async with self._db.transaction() as session:
            for i in range(0, len(candles), _CANDLE_BATCH):
                chunk = candles[i : i + _CANDLE_BATCH]
                stmt = insert(CandleRow).values(
                    [
                        {
                            "exchange": exchange,
                            "symbol": symbol,
                            "timeframe": timeframe.value,
                            "open_time": c.open_time,
                            "open": c.open,
                            "high": c.high,
                            "low": c.low,
                            "close": c.close,
                            "volume": c.volume,
                            "turnover": c.turnover,
                        }
                        for c in chunk
                    ]
                )
                excluded = stmt.excluded
                stmt = stmt.on_conflict_do_update(
                    index_elements=["exchange", "symbol", "timeframe", "open_time"],
                    set_={
                        "open": excluded.open,
                        "high": excluded.high,
                        "low": excluded.low,
                        "close": excluded.close,
                        "volume": excluded.volume,
                        "turnover": excluded.turnover,
                    },
                )
                await session.execute(stmt)
        return len(candles)

    async def get_candles(
        self, exchange: str, symbol: str, timeframe: Timeframe, start: datetime, end: datetime
    ) -> list[Candle]:
        """Bars with ``start <= open_time < end`` in time order."""
        stmt = (
            select(CandleRow)
            .where(
                CandleRow.exchange == exchange,
                CandleRow.symbol == symbol,
                CandleRow.timeframe == timeframe.value,
                CandleRow.open_time >= start,
                CandleRow.open_time < end,
            )
            .order_by(CandleRow.open_time)
        )
        async with self._db.sessions() as session:
            rows = (await session.scalars(stmt)).all()
        return [
            Candle(
                open_time=r.open_time,
                open=r.open,
                high=r.high,
                low=r.low,
                close=r.close,
                volume=r.volume,
                turnover=r.turnover,
            )
            for r in rows
        ]

    async def find_gaps(
        self, exchange: str, symbol: str, timeframe: Timeframe, start: datetime, end: datetime
    ) -> list[Gap]:
        """Missing bars in ``[start, end)``, including leading and trailing holes."""
        step = timedelta(seconds=timeframe.seconds)
        sql = text(
            """
            SELECT open_time, next_time FROM (
                SELECT open_time, LEAD(open_time) OVER (ORDER BY open_time) AS next_time
                FROM candles
                WHERE exchange = :exchange AND symbol = :symbol AND timeframe = :tf
                  AND open_time >= :start AND open_time < :end
            ) t
            WHERE next_time - open_time > :step
            ORDER BY open_time
            """
        )
        bounds = select(func.min(CandleRow.open_time), func.max(CandleRow.open_time)).where(
            CandleRow.exchange == exchange,
            CandleRow.symbol == symbol,
            CandleRow.timeframe == timeframe.value,
            CandleRow.open_time >= start,
            CandleRow.open_time < end,
        )
        params = {"exchange": exchange, "symbol": symbol, "tf": timeframe.value, "start": start, "end": end}
        async with self._db.sessions() as session:
            inner = (await session.execute(sql, {**params, "step": step})).all()
            span = (await session.execute(bounds)).one()
        # MIN/MAX are NULL on an empty range even though the column is NOT NULL.
        first: datetime | None = span[0]
        last: datetime | None = span[1]
        last_slot = end - step
        if first is None or last is None:
            return [Gap(start, last_slot)] if last_slot >= start else []
        gaps: list[Gap] = []
        if first > start:
            gaps.append(Gap(start, first - step))
        gaps.extend(Gap(a + step, b - step) for a, b in inner)
        if last < last_slot:
            gaps.append(Gap(last + step, last_slot))
        return gaps

    async def get_checkpoint(self, exchange: str, symbol: str, timeframe: Timeframe) -> Checkpoint | None:
        """Loaded span for a series, if any."""
        async with self._db.sessions() as session:
            row = await session.get(LoadCheckpointRow, (exchange, symbol, timeframe.value))
        if row is None:
            return None
        return Checkpoint(row.first_open_time, row.last_open_time)

    async def save_checkpoint(
        self, exchange: str, symbol: str, timeframe: Timeframe, checkpoint: Checkpoint
    ) -> None:
        """Create or replace the checkpoint."""
        stmt = insert(LoadCheckpointRow).values(
            exchange=exchange,
            symbol=symbol,
            timeframe=timeframe.value,
            first_open_time=checkpoint.first_open_time,
            last_open_time=checkpoint.last_open_time,
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=["exchange", "symbol", "timeframe"],
            set_={
                "first_open_time": stmt.excluded.first_open_time,
                "last_open_time": stmt.excluded.last_open_time,
                "updated_at": func.now(),
            },
        )
        async with self._db.transaction() as session:
            await session.execute(stmt)


class InstrumentRepository:
    """``IInstrumentStore`` on PostgreSQL."""

    def __init__(self, db: Database) -> None:
        self._db = db

    async def upsert_instruments(self, exchange: str, instruments: Sequence[Instrument]) -> None:
        """Insert or refresh exchange filters."""
        if not instruments:
            return
        stmt = insert(InstrumentRow).values(
            [
                {
                    "exchange": exchange,
                    "symbol": str(i.symbol),
                    "exchange_symbol": i.exchange_symbol,
                    "tick_size": i.filters.tick_size,
                    "step_size": i.filters.step_size,
                    "min_qty": i.filters.min_qty,
                    "min_notional": i.filters.min_notional,
                    "risky": i.risky,
                }
                for i in instruments
            ]
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=["exchange", "symbol"],
            set_={
                "exchange_symbol": stmt.excluded.exchange_symbol,
                "tick_size": stmt.excluded.tick_size,
                "step_size": stmt.excluded.step_size,
                "min_qty": stmt.excluded.min_qty,
                "min_notional": stmt.excluded.min_notional,
                "risky": stmt.excluded.risky,
                "updated_at": func.now(),
            },
        )
        async with self._db.transaction() as session:
            await session.execute(stmt)

    async def get_instrument(self, exchange: str, symbol: str) -> Instrument | None:
        """Stored instrument or None."""
        async with self._db.sessions() as session:
            row = await session.get(InstrumentRow, (exchange, symbol))
        if row is None:
            return None
        return Instrument(
            symbol=Symbol.parse(row.symbol),
            exchange_symbol=row.exchange_symbol,
            filters=MarketFilters(row.tick_size, row.step_size, row.min_qty, row.min_notional),
            risky=row.risky,
        )


class BacktestRunRepository:
    """``IBacktestRunStore`` on PostgreSQL."""

    def __init__(self, db: Database) -> None:
        self._db = db

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
    ) -> int:
        """Persist a run and return its id."""
        row = BacktestRunRow(
            kind=kind,
            git_sha=git_sha,
            exchange=exchange,
            symbol=symbol,
            timeframe=timeframe.value,
            period_start=period_start,
            period_end=period_end,
            params=params,
            metrics=metrics,
            assumptions=assumptions,
            parent_id=parent_id,
        )
        async with self._db.transaction() as session:
            session.add(row)
            await session.flush()
            return row.id


class TradingSessionRepository:
    """``ITradingSessionStore`` on PostgreSQL."""

    def __init__(self, db: Database) -> None:
        self._db = db

    async def create_session(self, *, mode: str, exchange: str, symbol: str, params: dict[str, Any]) -> int:
        """Start a new running session."""
        row = TradingSessionRow(
            mode=mode, exchange=exchange, symbol=symbol, status="running", params=params, state={}
        )
        async with self._db.transaction() as session:
            session.add(row)
            await session.flush()
            return row.id

    async def save_state(self, session_id: int, state: dict[str, Any], status: str | None = None) -> None:
        """Persist a state snapshot (and optionally a status change)."""
        values: dict[str, Any] = {"state": state}
        if status is not None:
            values["status"] = status
            if status != "running":
                values["stopped_at"] = datetime.now(UTC)
        async with self._db.transaction() as session:
            await session.execute(
                update(TradingSessionRow).where(TradingSessionRow.id == session_id).values(**values)
            )

    async def get_running(self, mode: str, exchange: str, symbol: str) -> tuple[int, dict[str, Any]] | None:
        """Most recent running session for a pair (used to resume after restart)."""
        stmt = (
            select(TradingSessionRow)
            .where(
                TradingSessionRow.mode == mode,
                TradingSessionRow.exchange == exchange,
                TradingSessionRow.symbol == symbol,
                TradingSessionRow.status == "running",
            )
            .order_by(TradingSessionRow.id.desc())
            .limit(1)
        )
        async with self._db.sessions() as session:
            row = await session.scalar(stmt)
        return None if row is None else (row.id, row.state)

    async def upsert_order(self, session_id: int, order: Order) -> None:
        """Insert or update an order by ``client_id``."""
        stmt = insert(OrderRow).values(
            session_id=session_id,
            client_id=order.client_id,
            exchange_id=order.exchange_id,
            side=order.side.value,
            price=order.price,
            qty=order.qty,
            filled_qty=order.filled_qty,
            status=order.status.value,
            level_index=order.level_index,
            created_at=order.created_at,
        )
        stmt = stmt.on_conflict_do_update(
            constraint="uq_orders_client_id",
            set_={
                "exchange_id": stmt.excluded.exchange_id,
                "filled_qty": stmt.excluded.filled_qty,
                "status": stmt.excluded.status,
                "updated_at": func.now(),
            },
        )
        async with self._db.transaction() as session:
            await session.execute(stmt)

    async def add_fill(self, session_id: int, fill: Fill) -> None:
        """Record an execution."""
        async with self._db.transaction() as session:
            session.add(
                FillRow(
                    session_id=session_id,
                    client_id=fill.client_id,
                    side=fill.side.value,
                    price=fill.price,
                    qty=fill.qty,
                    fee=fill.fee,
                    fee_asset=fill.fee_asset,
                    is_maker=fill.is_maker,
                    ts=fill.ts,
                )
            )

    async def add_cycle(self, session_id: int, cycle: Cycle) -> None:
        """Record a completed round trip."""
        async with self._db.transaction() as session:
            session.add(
                CycleRow(
                    session_id=session_id,
                    level_index=cycle.level_index,
                    buy_price=cycle.buy_price,
                    sell_price=cycle.sell_price,
                    qty=cycle.qty,
                    fees_quote=cycle.fees_quote,
                    net_profit=cycle.net_profit,
                    opened_at=cycle.opened_at,
                    closed_at=cycle.closed_at,
                )
            )

    async def add_equity_point(self, session_id: int, point: EquityPoint) -> None:
        """Record an equity sample (idempotent per timestamp)."""
        stmt = (
            insert(EquityPointRow)
            .values(
                session_id=session_id,
                ts=point.ts,
                quote_balance=point.quote_balance,
                base_balance=point.base_balance,
                price=point.price,
                equity=point.equity,
            )
            .on_conflict_do_nothing()
        )
        async with self._db.transaction() as session:
            await session.execute(stmt)


class ScannerRepository:
    """``IScannerStore`` on PostgreSQL."""

    def __init__(self, db: Database) -> None:
        self._db = db

    async def save_scores(
        self, run_at: datetime, exchange: str, rows: Sequence[tuple[str, Decimal, dict[str, Any]]]
    ) -> None:
        """Persist ranked scores; ``rows`` must already be sorted best-first."""
        async with self._db.transaction() as session:
            session.add_all(
                ScannerScoreRow(
                    run_at=run_at, exchange=exchange, symbol=sym, rank=i + 1, score=score, metrics=m
                )
                for i, (sym, score, m) in enumerate(rows)
            )
