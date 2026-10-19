import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from websockets.exceptions import ConnectionClosedError

from pendulum.domain.models import Instrument, MarketFilters, StreamGap, Symbol, TradeTick
from pendulum.exchanges.bybit.ws import BybitPublicWs, WsConnection, parse_trade_message
from tests.helpers import bybit_settings

F = MarketFilters(Decimal("0.01"), Decimal("0.001"), Decimal("0.001"), Decimal(5))
SOL = Instrument(Symbol("SOL", "USDT"), "SOLUSDT", F)


def _trade(price: str, ts: int = 1792428300000) -> str:
    return json.dumps(
        {"topic": "publicTrade.SOLUSDT", "data": [{"T": ts, "s": "SOLUSDT", "p": price, "v": "1.5"}]}
    )


class FakeConn:
    def __init__(self, messages: list[str]) -> None:
        self.messages = messages
        self.sent: list[str] = []

    async def send(self, message: str) -> None:
        self.sent.append(message)

    async def recv(self) -> str:
        if not self.messages:
            raise ConnectionClosedError(None, None)
        return self.messages.pop(0)


def test_parse_ignores_non_trade_messages() -> None:
    syms = {"SOLUSDT": SOL.symbol}
    assert parse_trade_message({"op": "pong"}, syms) == []
    ticks = parse_trade_message(json.loads(_trade("104.5")), syms)
    assert ticks[0].price == Decimal("104.5")
    assert ticks[0].ts.tzinfo is UTC


async def test_reconnect_resubscribes_and_emits_gap() -> None:
    conns = [
        FakeConn([_trade("100"), json.dumps({"success": True, "op": "subscribe"})]),
        FakeConn([_trade("101")]),
    ]
    used: list[FakeConn] = []
    clock = iter([datetime(2026, 1, 1, tzinfo=UTC) + timedelta(seconds=s) for s in range(10)])

    @asynccontextmanager
    async def connect(url: str) -> AsyncIterator[WsConnection]:
        conn = conns.pop(0)
        used.append(conn)
        yield conn

    async def no_sleep(_: int) -> None:
        return None

    ws = BybitPublicWs(
        bybit_settings(),
        connect=connect,
        sleep=no_sleep,
        now=lambda: next(clock),
        max_reconnects=1,
    )
    events = [e async for e in ws.stream_trades([SOL])]
    assert [type(e) for e in events] == [TradeTick, StreamGap, TradeTick]
    gap = events[1]
    assert isinstance(gap, StreamGap) and gap.end > gap.start
    for conn in used:
        assert json.loads(conn.sent[0]) == {"op": "subscribe", "args": ["publicTrade.SOLUSDT"]}


async def test_subscribe_chunks_of_ten() -> None:
    conn = FakeConn([])
    insts = [Instrument(Symbol(f"C{i}", "USDT"), f"C{i}USDT", F) for i in range(23)]

    @asynccontextmanager
    async def connect(url: str) -> AsyncIterator[WsConnection]:
        yield conn

    async def no_sleep(_: int) -> None:
        return None

    ws = BybitPublicWs(bybit_settings(), connect=connect, sleep=no_sleep, max_reconnects=0)
    _ = [e async for e in ws.stream_trades(insts)]
    assert [len(json.loads(m)["args"]) for m in conn.sent] == [10, 10, 3]
