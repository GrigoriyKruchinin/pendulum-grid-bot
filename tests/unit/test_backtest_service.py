from datetime import timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from pendulum.backtest import service
from pendulum.backtest.engine import GridParams, SimulationConfig
from pendulum.backtest.resample import resample
from pendulum.backtest.service import BacktestService, DataQualityError, git_sha
from pendulum.config.settings import BacktestSettings, GridType
from pendulum.domain.models import FeeModel, Timeframe
from pendulum.market_data.loader import HistoryLoader
from tests.fakes import SOL, FakeAdapter, InMemoryCandleStore, InMemoryInstrumentStore, InMemoryRunStore
from tests.synthetic import T0, series

PARAMS = GridParams(10, Decimal(10), GridType.GEOMETRIC, "wait")


def _svc(store: InMemoryCandleStore, runs: InMemoryRunStore) -> BacktestService:
    adapter = FakeAdapter()
    loader = HistoryLoader(adapter, store, InMemoryInstrumentStore())
    return BacktestService(adapter, store, runs, loader, BacktestSettings(_env_file=None))


async def test_service_resamples_persists_and_reports(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    store, runs = InMemoryCandleStore(), InMemoryRunStore()
    one_min = series([100 + (i % 20) * 0.3 for i in range(600)], minutes=1)
    await store.upsert_candles("fake", "SOL/USDT", Timeframe.M1, one_min)
    svc = _svc(store, runs)
    fees = await svc.fee_model(SOL)
    assert fees == FeeModel(Decimal("0.001"), Decimal("0.001"))
    cfg = SimulationConfig(Decimal(1000), fees, timeframe=Timeframe.M5)
    run_id, result, path = await svc.run(SOL.symbol, T0, T0 + timedelta(hours=10), PARAMS, cfg)
    assert run_id == 1
    assert result.bars == 120
    assert runs.runs[0]["params"] == PARAMS.to_dict()
    assert runs.runs[0]["git_sha"]
    assert path is not None
    assert "Historical simulation" in path.read_text()


async def test_service_refuses_gappy_data() -> None:
    store = InMemoryCandleStore()
    one_min = series([100.0] * 200, minutes=1)
    await store.upsert_candles("fake", "SOL/USDT", Timeframe.M1, one_min[:50] + one_min[100:])
    svc = _svc(store, InMemoryRunStore())
    with pytest.raises(DataQualityError, match="missing"):
        await svc.load_candles(SOL.symbol, T0, T0 + timedelta(hours=4), Timeframe.M5)
    with pytest.raises(DataQualityError, match="no 1m candles"):
        await svc.load_candles(SOL.symbol, T0 + timedelta(days=9), T0 + timedelta(days=10), Timeframe.M5)


def test_git_sha_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GIT_SHA", "deadbeef")
    assert git_sha() == "deadbeef"


async def test_chunked_load_matches_single_resample(monkeypatch: pytest.MonkeyPatch) -> None:

    store = InMemoryCandleStore()
    one_min = series([100 + (i % 37) * 0.1 for i in range(3 * 1440)], minutes=1)
    await store.upsert_candles("fake", "SOL/USDT", Timeframe.M1, one_min)
    monkeypatch.setattr(service, "LOAD_CHUNK", timedelta(hours=7))
    start = T0 + timedelta(minutes=3)  # not aligned to 5m
    got = await _svc(store, InMemoryRunStore()).load_candles(
        SOL.symbol, start, T0 + timedelta(days=3), Timeframe.M5
    )
    expected = resample([c for c in one_min if c.open_time >= start], Timeframe.M1, Timeframe.M5)
    assert got == expected
