import math
from datetime import timedelta
from decimal import Decimal

from pendulum.config.settings import ScannerSettings
from pendulum.domain.models import Candle, FeeModel, Instrument, Symbol, Ticker, Timeframe
from pendulum.scanner.scanner import PairScanner, compute_metrics, prefilter
from tests.fakes import FILTERS, FakeAdapter, FakeRest, InMemoryScannerStore
from tests.synthetic import T0

FEES = FeeModel(Decimal("0.001"), Decimal("0.001"))
SETTINGS = ScannerSettings(_env_file=None)


def _hourly(prices: list[float], swing: float = 0.01) -> list[Candle]:
    out = []
    for i, p in enumerate(prices):
        d = Decimal(str(round(p, 4)))
        s = Decimal(str(round(p * swing, 4)))
        out.append(Candle(T0 + timedelta(hours=i), d, d + s, d - s, d, Decimal(1)))
    return out


def _ticker(sym: str, vol: str = "10000000", bid: str = "99.99", ask: str = "100.01") -> Ticker:
    return Ticker(Symbol.parse(sym), Decimal(bid), Decimal(ask), Decimal(100), Decimal(vol), T0)


def test_choppy_scores_higher_than_trending() -> None:
    n = 24 * 30
    chop = _hourly([100 + 3 * math.sin(i / 3) for i in range(n)])
    trend = _hourly([100 * (1 + i / n) for i in range(n)])
    t = _ticker("A/USDT")
    m_chop = compute_metrics("A/USDT", chop, t, Decimal("0.01"), FEES)
    m_trend = compute_metrics("B/USDT", trend, t, Decimal("0.01"), FEES)
    assert m_chop is not None and m_trend is not None
    assert m_chop.efficiency_ratio < 0.1 < m_trend.efficiency_ratio
    assert m_chop.score > m_trend.score
    assert compute_metrics("C/USDT", chop[:10], t, Decimal("0.01"), FEES) is None


def test_low_volatility_or_coarse_tick_disqualified() -> None:
    flat = _hourly([100 + 0.01 * math.sin(i) for i in range(24 * 10)], swing=0.0001)
    m = compute_metrics("A/USDT", flat, _ticker("A/USDT"), Decimal("0.01"), FEES)
    assert m is not None and m.score == 0
    chop = _hourly([100 + 3 * math.sin(i / 3) for i in range(24 * 10)])
    coarse = compute_metrics("A/USDT", chop, _ticker("A/USDT"), Decimal(1), FEES)
    assert coarse is not None and coarse.score == 0


def test_prefilter() -> None:
    insts = [
        Instrument(Symbol("A", "USDT"), "AUSDT", FILTERS),
        Instrument(Symbol("B", "USDT"), "BUSDT", FILTERS, risky=True),
        Instrument(Symbol("C", "USDT"), "CUSDT", FILTERS),
        Instrument(Symbol("D", "USDC"), "DUSDC", FILTERS),
        Instrument(Symbol("E", "USDT"), "EUSDT", FILTERS),
    ]
    tickers = [
        _ticker("A/USDT", vol="6000000"),
        _ticker("B/USDT"),
        _ticker("C/USDT", vol="100"),
        _ticker("D/USDC"),
        _ticker("E/USDT", vol="9000000", bid="99", ask="101"),
    ]
    assert [str(i.symbol) for i, _ in prefilter(insts, tickers, SETTINGS, "USDT")] == ["A/USDT"]


async def test_scan_end_to_end() -> None:
    rest = FakeRest(Timeframe.H1, limit=1000)
    rest.tickers = [_ticker("SOL/USDT")]
    store = InMemoryScannerStore()
    scanner = PairScanner(FakeAdapter(rest), store, ScannerSettings(_env_file=None, lookback_days=7))
    results = await scanner.scan(now=T0 + timedelta(days=8))
    assert [m.symbol for m in results] == ["SOL/USDT"]
    assert store.saved[0][0] == "SOL/USDT"
