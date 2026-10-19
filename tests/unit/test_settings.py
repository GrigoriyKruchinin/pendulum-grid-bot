from decimal import Decimal
from pathlib import Path

import pytest
from pydantic import ValidationError

from pendulum.config.pairs import PairConfig, load_pairs
from pendulum.config.settings import BybitSettings, DatabaseSettings, GridType, RiskSettings, TelegramSettings


@pytest.fixture(autouse=True)
def _no_dotenv(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    # Isolate from the developer's real .env.
    monkeypatch.chdir(tmp_path)


def test_empty_bybit_key_means_no_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EXCHANGE_BYBIT_API_KEY", "")
    monkeypatch.setenv("EXCHANGE_BYBIT_API_SECRET", "")
    s = BybitSettings()
    assert not s.has_credentials
    assert s.maker_fee == Decimal("0.001")


def test_fees_are_decimal_and_bounded(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EXCHANGE_BYBIT_MAKER_FEE", "0.0008")
    assert BybitSettings().maker_fee == Decimal("0.0008")
    monkeypatch.setenv("EXCHANGE_BYBIT_MAKER_FEE", "0.5")
    with pytest.raises(ValidationError):
        BybitSettings()


def test_secret_not_in_repr(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("POSTGRES_PASSWORD", "hunter2")
    s = DatabaseSettings()
    assert "hunter2" not in repr(s)
    assert "hunter2" in s.async_url


def test_risk_pair_cap() -> None:
    assert RiskSettings().max_pair_capital_usd == Decimal("150")


def test_telegram_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    assert not TelegramSettings().enabled
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "1:x")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "42")
    assert TelegramSettings().enabled


def test_pairs_file(tmp_path: Path) -> None:
    f = tmp_path / "pairs.toml"
    f.write_text(
        '[[pairs]]\nsymbol = "SOL/USDT"\ngrid_type = "arithmetic"\nlevels = 30\n'
        '[[pairs]]\nsymbol = "BTC/USDT"\nenabled = false\n'
    )
    cfg = load_pairs(f)
    assert [p.symbol for p in cfg.enabled()] == ["SOL/USDT"]
    assert cfg.pairs[0].grid_type is GridType.ARITHMETIC


def test_pairs_missing_file_is_empty(tmp_path: Path) -> None:
    assert load_pairs(tmp_path / "nope.toml").pairs == []


def test_pair_range_validation() -> None:
    with pytest.raises(ValidationError):
        PairConfig(symbol="SOL/USDT", lower_price=Decimal(10))
    with pytest.raises(ValidationError):
        PairConfig(symbol="SOL/USDT", lower_price=Decimal(20), upper_price=Decimal(10))
    with pytest.raises(ValidationError):
        PairConfig(symbol="sol-usdt")


def test_repo_pairs_file_is_valid() -> None:
    root = Path(__file__).resolve().parents[2]
    assert len(load_pairs(root / "config/pairs.toml").pairs) >= 1
