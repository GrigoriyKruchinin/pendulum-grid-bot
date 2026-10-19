"""Shared test helpers."""

from typing import Any

from pendulum.config.settings import BybitSettings


def bybit_settings(**overrides: Any) -> BybitSettings:
    """Bybit settings isolated from the developer's `.env`."""
    return BybitSettings(_env_file=None, **overrides)
