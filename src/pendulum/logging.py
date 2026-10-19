"""Structured logging with secret redaction."""

from __future__ import annotations

import logging
import re
import sys
from collections.abc import MutableMapping
from typing import Any

import structlog

_SENSITIVE_KEY = re.compile(r"(secret|token|password|api_?key|signature|authorization)", re.IGNORECASE)
# Telegram bot tokens look like "<digits>:<35 url-safe chars>" and can leak inside URLs.
_TELEGRAM_TOKEN = re.compile(r"(?<!\d)\d{6,12}:[A-Za-z0-9_-]{30,}")
REDACTED = "***"


def redact_value(value: str) -> str:
    """Mask known secret shapes inside a free-form string."""
    return _TELEGRAM_TOKEN.sub(REDACTED, value)


def _is_sensitive(key: object) -> bool:
    return isinstance(key, str) and _SENSITIVE_KEY.search(key) is not None


def _redact(obj: Any) -> Any:
    # Event dicts contain arbitrary user values, hence Any.
    if isinstance(obj, dict):
        return {k: REDACTED if _is_sensitive(k) else _redact(v) for k, v in obj.items()}
    if isinstance(obj, list | tuple):
        return type(obj)(_redact(v) for v in obj)
    if isinstance(obj, str):
        return redact_value(obj)
    return obj


def redact_processor(_: Any, __: str, event_dict: MutableMapping[str, Any]) -> MutableMapping[str, Any]:
    """structlog processor that masks sensitive keys and token-shaped strings."""
    redacted: MutableMapping[str, Any] = _redact(dict(event_dict))
    return redacted


def configure_logging(level: str = "INFO", *, json: bool = True) -> None:
    """Configure structlog + stdlib logging once per process."""
    log_level = logging.getLevelNamesMapping().get(level.upper(), logging.INFO)
    logging.basicConfig(stream=sys.stdout, level=log_level, format="%(message)s")
    # httpx logs full request URLs at INFO, which may contain tokens (Telegram).
    logging.getLogger("httpx").setLevel(logging.WARNING)
    renderer: structlog.typing.Processor = (
        structlog.processors.JSONRenderer() if json else structlog.dev.ConsoleRenderer()
    )
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            redact_processor,
            renderer,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(log_level),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str) -> structlog.typing.FilteringBoundLogger:
    """Return a bound structured logger."""
    logger: structlog.typing.FilteringBoundLogger = structlog.get_logger(name)
    return logger
