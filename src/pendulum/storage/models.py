"""SQLAlchemy ORM models.

Prices and quantities use ``NUMERIC(38, 18)`` to hold exact Decimals for both large (BTC price) and tiny
(meme-coin price) values. JSON columns store Decimals as strings.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    Numeric,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}

Money = Numeric(38, 18)
TsTz = DateTime(timezone=True)


class Base(DeclarativeBase):
    """Declarative base with deterministic constraint names (stable Alembic diffs)."""

    metadata = MetaData(naming_convention=NAMING_CONVENTION)
    # JSON payloads are free-form by design.
    type_annotation_map = {dict[str, Any]: JSONB}  # noqa: RUF012


class InstrumentRow(Base):
    """Exchange trading rules per symbol."""

    __tablename__ = "instruments"

    exchange: Mapped[str] = mapped_column(String(32), primary_key=True)
    symbol: Mapped[str] = mapped_column(String(32), primary_key=True)
    exchange_symbol: Mapped[str] = mapped_column(String(32))
    tick_size: Mapped[Decimal] = mapped_column(Money)
    step_size: Mapped[Decimal] = mapped_column(Money)
    min_qty: Mapped[Decimal] = mapped_column(Money)
    min_notional: Mapped[Decimal] = mapped_column(Money)
    risky: Mapped[bool] = mapped_column(server_default="false")
    updated_at: Mapped[datetime] = mapped_column(TsTz, server_default=func.now(), onupdate=func.now())


class CandleRow(Base):
    """OHLCV bars."""

    __tablename__ = "candles"

    exchange: Mapped[str] = mapped_column(String(32), primary_key=True)
    symbol: Mapped[str] = mapped_column(String(32), primary_key=True)
    timeframe: Mapped[str] = mapped_column(String(8), primary_key=True)
    open_time: Mapped[datetime] = mapped_column(TsTz, primary_key=True)
    open: Mapped[Decimal] = mapped_column(Money)
    high: Mapped[Decimal] = mapped_column(Money)
    low: Mapped[Decimal] = mapped_column(Money)
    close: Mapped[Decimal] = mapped_column(Money)
    volume: Mapped[Decimal] = mapped_column(Money)
    turnover: Mapped[Decimal] = mapped_column(Money)


class LoadCheckpointRow(Base):
    """Progress of historical candle loading (enables resume)."""

    __tablename__ = "load_checkpoints"

    exchange: Mapped[str] = mapped_column(String(32), primary_key=True)
    symbol: Mapped[str] = mapped_column(String(32), primary_key=True)
    timeframe: Mapped[str] = mapped_column(String(8), primary_key=True)
    first_open_time: Mapped[datetime] = mapped_column(TsTz)
    last_open_time: Mapped[datetime] = mapped_column(TsTz)
    updated_at: Mapped[datetime] = mapped_column(TsTz, server_default=func.now(), onupdate=func.now())


class BacktestRunRow(Base):
    """One backtest (single run or one walk-forward window)."""

    __tablename__ = "backtest_runs"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    created_at: Mapped[datetime] = mapped_column(TsTz, server_default=func.now())
    parent_id: Mapped[int | None] = mapped_column(ForeignKey("backtest_runs.id", ondelete="CASCADE"))
    kind: Mapped[str] = mapped_column(String(16))  # single | walk_forward | wf_train | wf_test
    git_sha: Mapped[str] = mapped_column(String(40))
    exchange: Mapped[str] = mapped_column(String(32))
    symbol: Mapped[str] = mapped_column(String(32))
    timeframe: Mapped[str] = mapped_column(String(8))
    period_start: Mapped[datetime] = mapped_column(TsTz)
    period_end: Mapped[datetime] = mapped_column(TsTz)
    params: Mapped[dict[str, Any]]
    metrics: Mapped[dict[str, Any]]
    assumptions: Mapped[dict[str, Any]]

    __table_args__ = (Index("ix_backtest_runs_symbol_created", "symbol", "created_at"),)


class TradingSessionRow(Base):
    """A paper (Stage 1) or live (Stage 2) grid session."""

    __tablename__ = "trading_sessions"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    mode: Mapped[str] = mapped_column(String(8))  # paper | live
    exchange: Mapped[str] = mapped_column(String(32))
    symbol: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(16))  # running | stopped | halted
    params: Mapped[dict[str, Any]]
    state: Mapped[dict[str, Any]]
    started_at: Mapped[datetime] = mapped_column(TsTz, server_default=func.now())
    stopped_at: Mapped[datetime | None] = mapped_column(TsTz)

    __table_args__ = (Index("ix_trading_sessions_status", "status"),)


class OrderRow(Base):
    """Grid orders (virtual in paper mode)."""

    __tablename__ = "orders"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    session_id: Mapped[int] = mapped_column(ForeignKey("trading_sessions.id", ondelete="CASCADE"), index=True)
    client_id: Mapped[str] = mapped_column(String(64))
    exchange_id: Mapped[str | None] = mapped_column(String(64))
    side: Mapped[str] = mapped_column(String(4))
    price: Mapped[Decimal] = mapped_column(Money)
    qty: Mapped[Decimal] = mapped_column(Money)
    filled_qty: Mapped[Decimal] = mapped_column(Money)
    status: Mapped[str] = mapped_column(String(20))
    level_index: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(TsTz)
    updated_at: Mapped[datetime] = mapped_column(TsTz, server_default=func.now(), onupdate=func.now())

    __table_args__ = (UniqueConstraint("client_id", name="uq_orders_client_id"),)


class FillRow(Base):
    """Executions."""

    __tablename__ = "fills"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    session_id: Mapped[int] = mapped_column(ForeignKey("trading_sessions.id", ondelete="CASCADE"), index=True)
    client_id: Mapped[str] = mapped_column(String(64), index=True)
    side: Mapped[str] = mapped_column(String(4))
    price: Mapped[Decimal] = mapped_column(Money)
    qty: Mapped[Decimal] = mapped_column(Money)
    fee: Mapped[Decimal] = mapped_column(Money)
    fee_asset: Mapped[str] = mapped_column(String(16))
    is_maker: Mapped[bool]
    ts: Mapped[datetime] = mapped_column(TsTz)

    # Orders fill once (no partial fills in the virtual exchange); replays after a crash become no-ops.
    __table_args__ = (UniqueConstraint("session_id", "client_id", name="uq_fills_session_client"),)


class CycleRow(Base):
    """Completed buy/sell round trips."""

    __tablename__ = "cycles"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    session_id: Mapped[int] = mapped_column(ForeignKey("trading_sessions.id", ondelete="CASCADE"), index=True)
    level_index: Mapped[int] = mapped_column(Integer)
    buy_price: Mapped[Decimal] = mapped_column(Money)
    sell_price: Mapped[Decimal] = mapped_column(Money)
    qty: Mapped[Decimal] = mapped_column(Money)
    fees_quote: Mapped[Decimal] = mapped_column(Money)
    net_profit: Mapped[Decimal] = mapped_column(Money)
    opened_at: Mapped[datetime] = mapped_column(TsTz)
    closed_at: Mapped[datetime] = mapped_column(TsTz, index=True)

    __table_args__ = (
        UniqueConstraint("session_id", "level_index", "closed_at", name="uq_cycles_session_level_closed"),
    )


class EquityPointRow(Base):
    """Equity curve samples."""

    __tablename__ = "equity_points"

    session_id: Mapped[int] = mapped_column(
        ForeignKey("trading_sessions.id", ondelete="CASCADE"), primary_key=True
    )
    ts: Mapped[datetime] = mapped_column(TsTz, primary_key=True)
    quote_balance: Mapped[Decimal] = mapped_column(Money)
    base_balance: Mapped[Decimal] = mapped_column(Money)
    price: Mapped[Decimal] = mapped_column(Money)
    equity: Mapped[Decimal] = mapped_column(Money)


class ScannerScoreRow(Base):
    """Pair scanner results per run."""

    __tablename__ = "scanner_scores"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    run_at: Mapped[datetime] = mapped_column(TsTz, index=True)
    exchange: Mapped[str] = mapped_column(String(32))
    symbol: Mapped[str] = mapped_column(String(32))
    rank: Mapped[int] = mapped_column(Integer)
    score: Mapped[Decimal] = mapped_column(Money)
    metrics: Mapped[dict[str, Any]]
