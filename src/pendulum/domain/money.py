"""Decimal helpers for prices and quantities.

Rounding policy is conservative: buy prices round down, sell prices round up, quantities round down,
so a rounded order never costs more or sells for less than intended.
"""

from __future__ import annotations

from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal, InvalidOperation

ZERO = Decimal(0)
ONE = Decimal(1)


def to_decimal(value: str | int | Decimal) -> Decimal:
    """Convert an exact representation to Decimal. Floats are rejected on purpose."""
    if isinstance(value, bool) or not isinstance(value, str | int | Decimal):
        raise TypeError(f"refusing to build Decimal from {type(value).__name__}")
    try:
        result = Decimal(value)
    except InvalidOperation as exc:
        raise ValueError(f"invalid decimal: {value!r}") from exc
    if not result.is_finite():
        raise ValueError(f"non-finite decimal: {value!r}")
    return result


def _quantize_to_step(value: Decimal, step: Decimal, rounding: str) -> Decimal:
    if step <= 0:
        raise ValueError("step must be positive")
    units = (value / step).to_integral_value(rounding=rounding)
    return (units * step).quantize(step)


def floor_to_step(value: Decimal, step: Decimal) -> Decimal:
    """Largest multiple of ``step`` that is <= ``value``."""
    return _quantize_to_step(value, step, ROUND_FLOOR)


def ceil_to_step(value: Decimal, step: Decimal) -> Decimal:
    """Smallest multiple of ``step`` that is >= ``value``."""
    return _quantize_to_step(value, step, ROUND_CEILING)


def bps(value: Decimal) -> Decimal:
    """Basis points to fraction (5 bps -> 0.0005)."""
    return value / Decimal(10_000)
