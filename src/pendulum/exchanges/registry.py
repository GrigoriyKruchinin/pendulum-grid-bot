"""Exchange registry: maps exchange ids to adapter factories.

To add an exchange: implement ``BaseExchangeAdapter`` in ``pendulum.exchanges.<name>``, give it a settings
class with prefix ``EXCHANGE_<NAME>_``, and register a factory below. Switching is ``EXCHANGE_ACTIVE=<name>``.
"""

from __future__ import annotations

from collections.abc import Callable

from pendulum.config.settings import ExchangeSettings
from pendulum.exchanges.base import BaseExchangeAdapter

AdapterFactory = Callable[[], BaseExchangeAdapter]


class ExchangeRegistry:
    """Registry of adapter factories keyed by exchange id."""

    def __init__(self) -> None:
        self._factories: dict[str, AdapterFactory] = {}

    def register(self, exchange_id: str, factory: AdapterFactory) -> None:
        """Register a factory; ids are case-insensitive and unique."""
        key = exchange_id.lower()
        if key in self._factories:
            raise ValueError(f"exchange {exchange_id!r} already registered")
        self._factories[key] = factory

    def available(self) -> list[str]:
        """Registered exchange ids."""
        return sorted(self._factories)

    def create(self, exchange_id: str) -> BaseExchangeAdapter:
        """Instantiate the adapter for ``exchange_id``."""
        try:
            factory = self._factories[exchange_id.lower()]
        except KeyError:
            raise ValueError(
                f"unknown exchange {exchange_id!r}; available: {', '.join(self.available())}"
            ) from None
        return factory()

    def create_active(self, settings: ExchangeSettings | None = None) -> BaseExchangeAdapter:
        """Instantiate the adapter selected by ``EXCHANGE_ACTIVE``."""
        return self.create((settings or ExchangeSettings()).active)


def _bybit() -> BaseExchangeAdapter:
    # Imported lazily so unused exchanges do not load (or need settings for) their dependencies.
    from pendulum.config.settings import BybitSettings  # noqa: PLC0415
    from pendulum.exchanges.bybit.adapter import BybitAdapter  # noqa: PLC0415

    return BybitAdapter(BybitSettings())


def default_registry() -> ExchangeRegistry:
    """Registry with all built-in adapters."""
    registry = ExchangeRegistry()
    registry.register("bybit", _bybit)
    return registry
