"""Per-pair configuration loaded from a TOML file."""

from __future__ import annotations

import tomllib
from decimal import Decimal
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, model_validator

from pendulum.config.settings import GridType


class PairConfig(BaseModel):
    """Overrides for one trading pair. ``None`` means "use the global default"."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    symbol: str = Field(pattern=r"^[A-Z0-9]+/[A-Z0-9]+$")
    enabled: bool = True
    grid_type: GridType | None = None
    levels: int | None = Field(default=None, ge=2, le=200)
    lower_price: Decimal | None = Field(default=None, gt=0)
    upper_price: Decimal | None = Field(default=None, gt=0)
    range_width_pct: Decimal | None = Field(default=None, gt=0, lt=100)
    capital_usd: Decimal | None = Field(default=None, gt=0)

    @model_validator(mode="after")
    def _check_range(self) -> PairConfig:
        if (self.lower_price is None) != (self.upper_price is None):
            raise ValueError("lower_price and upper_price must be set together")
        if self.lower_price is not None and self.upper_price is not None:
            if self.lower_price >= self.upper_price:
                raise ValueError("lower_price must be below upper_price")
            if self.range_width_pct is not None:
                raise ValueError("use either explicit prices or range_width_pct, not both")
        return self


class PairsFile(BaseModel):
    """Root of ``config/pairs.toml``."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    pairs: list[PairConfig] = Field(default_factory=list)

    @model_validator(mode="after")
    def _unique(self) -> PairsFile:
        symbols = [p.symbol for p in self.pairs]
        if len(symbols) != len(set(symbols)):
            raise ValueError("duplicate symbols in pairs file")
        return self

    def enabled(self) -> list[PairConfig]:
        """Pairs with ``enabled = true``."""
        return [p for p in self.pairs if p.enabled]


def load_pairs(path: str | Path) -> PairsFile:
    """Parse and validate a pairs TOML file. Missing file yields an empty config."""
    p = Path(path)
    if not p.exists():
        return PairsFile()
    with p.open("rb") as fh:
        raw = tomllib.load(fh)
    return PairsFile.model_validate(raw)
