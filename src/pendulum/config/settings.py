"""Modular application settings.

Every group reads only its own env prefix, so each container can be given just the variables it needs.
Trading credentials live in per-exchange groups (e.g. ``BybitSettings``) that only exchange-facing
processes instantiate.
"""

from __future__ import annotations

from decimal import Decimal
from enum import StrEnum
from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


def _config(prefix: str) -> SettingsConfigDict:
    return SettingsConfigDict(env_prefix=prefix, env_file=".env", extra="ignore", frozen=True)


class GridType(StrEnum):
    """Supported grid spacing modes."""

    ARITHMETIC = "arithmetic"
    GEOMETRIC = "geometric"


class AppSettings(BaseSettings):
    """Process-wide settings."""

    model_config = _config("APP_")

    env: Literal["local", "production", "test"] = "local"
    log_level: str = "INFO"
    log_json: bool = True
    timezone: str = "UTC"


class DatabaseSettings(BaseSettings):
    """PostgreSQL connection."""

    model_config = _config("POSTGRES_")

    host: str = "postgres"
    port: int = 5432
    db: str = "pendulum"
    user: str = "pendulum"
    password: SecretStr = SecretStr("")
    pool_size: int = 5

    @property
    def async_url(self) -> str:
        """SQLAlchemy asyncpg URL (contains the password: never log it)."""
        pw = self.password.get_secret_value()
        return f"postgresql+asyncpg://{self.user}:{pw}@{self.host}:{self.port}/{self.db}"


class RedisSettings(BaseSettings):
    """Redis broker / cache."""

    model_config = _config("REDIS_")

    url: str = "redis://redis:6379/0"


class ExchangeSettings(BaseSettings):
    """Which exchange adapter is active. Credentials live in per-exchange groups."""

    model_config = _config("EXCHANGE_")

    active: str = "bybit"


class ExchangeCredentialsSettings(BaseSettings):
    """Base for per-exchange settings: credentials and fee schedule."""

    api_key: SecretStr | None = None
    api_secret: SecretStr | None = None
    testnet: bool = False
    maker_fee: Decimal = Field(default=Decimal("0.001"), ge=0, lt=Decimal("0.01"))
    taker_fee: Decimal = Field(default=Decimal("0.001"), ge=0, lt=Decimal("0.01"))

    @model_validator(mode="before")
    @classmethod
    def _empty_strings_to_none(cls, data: object) -> object:
        # `.env` keeps `EXCHANGE_BYBIT_API_KEY=` empty when no key is used (Stage 1 public data only).
        if isinstance(data, dict):
            return {k: (None if v == "" else v) for k, v in data.items()}
        return data

    @property
    def has_credentials(self) -> bool:
        """True when both key and secret are configured."""
        return self.api_key is not None and self.api_secret is not None


class BybitSettings(ExchangeCredentialsSettings):
    """Bybit V5 settings."""

    model_config = _config("EXCHANGE_BYBIT_")


class GridSettings(BaseSettings):
    """Grid defaults; per-pair overrides come from ``config/pairs.toml``."""

    model_config = _config("GRID_")

    default_type: GridType = GridType.GEOMETRIC
    default_levels: int = Field(default=20, ge=2, le=200)
    min_step_fee_multiplier: Decimal = Field(default=Decimal("1.5"), ge=1)
    default_width_pct: Decimal = Field(default=Decimal("16"), gt=0, lt=200)
    default_exit_policy: str = "stop_loss+trailing_up"
    quote_asset: str = "USDT"
    pairs_file: str = "config/pairs.toml"


class ScannerSettings(BaseSettings):
    """Pair scanner thresholds."""

    model_config = _config("SCANNER_")

    min_24h_volume_usd: Decimal = Field(default=Decimal("5000000"), ge=0)
    max_spread_bps: Decimal = Field(default=Decimal("10"), gt=0)
    lookback_days: int = Field(default=30, ge=7)
    top_n: int = Field(default=15, ge=1)


class RiskSettings(BaseSettings):
    """Capital and loss limits (enforced live from Stage 2, used for sizing in Stage 1)."""

    model_config = _config("RISK_")

    max_total_capital_usd: Decimal = Field(default=Decimal("500"), gt=0)
    max_pair_capital_pct: Decimal = Field(default=Decimal("30"), gt=0, le=100)
    daily_loss_limit_pct: Decimal = Field(default=Decimal("5"), gt=0, le=100)
    max_open_orders: int = Field(default=100, ge=1)

    @property
    def max_pair_capital_usd(self) -> Decimal:
        """Absolute per-pair capital cap."""
        return self.max_total_capital_usd * self.max_pair_capital_pct / Decimal(100)


class BacktestSettings(BaseSettings):
    """Simulation assumptions."""

    model_config = _config("BACKTEST_")

    slippage_bps: Decimal = Field(default=Decimal("5"), ge=0)
    default_timeframe: Literal["1m", "5m"] = "1m"


class TelegramSettings(BaseSettings):
    """Telegram notifier."""

    model_config = _config("TELEGRAM_")

    bot_token: SecretStr | None = None
    chat_id: str | None = None

    @model_validator(mode="before")
    @classmethod
    def _empty_strings_to_none(cls, data: object) -> object:
        if isinstance(data, dict):
            return {k: (None if v == "" else v) for k, v in data.items()}
        return data

    @property
    def enabled(self) -> bool:
        """True when both token and chat id are configured."""
        return self.bot_token is not None and self.chat_id is not None


class AdminSettings(BaseSettings):
    """Dashboard credentials (Stage 3)."""

    model_config = _config("ADMIN_")

    username: str = "admin"
    password_hash: SecretStr | None = None


@lru_cache
def get_app_settings() -> AppSettings:
    """Cached app settings."""
    return AppSettings()
