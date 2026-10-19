"""Viability criteria: when is a grid configuration worth running on a market.

Defaults are deliberately demanding: a strategy must be robust out-of-sample, beat buy & hold, keep the
drawdown bounded and not depend on a few lucky windows or pairs. All thresholds are configurable.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal

from pendulum.backtest.walk_forward import WalkForwardResult
from pendulum.domain.money import ZERO


@dataclass(frozen=True, slots=True)
class ViabilityCriteria:
    """Thresholds (fractions)."""

    min_positive_window_share: Decimal = Decimal("0.60")
    min_oos_annualized: Decimal = Decimal("0.15")
    max_drawdown: Decimal = Decimal("0.20")
    max_paper_divergence: Decimal = Decimal("0.20")
    min_paper_days: int = 14
    max_top2_concentration: Decimal = Decimal("0.50")


@dataclass(frozen=True, slots=True)
class CriterionCheck:
    """One evaluated criterion."""

    name: str
    passed: bool
    value: str
    threshold: str


def evaluate_viability(
    results: Sequence[WalkForwardResult], c: ViabilityCriteria | None = None
) -> list[CriterionCheck]:
    """Backtest-side criteria across all pairs (paper divergence is checked separately)."""
    c = c or ViabilityCriteria()
    windows = [w for r in results for w in r.windows]
    if not windows:
        return [CriterionCheck("data", False, "no windows", ">= 1 window")]
    positive_share = Decimal(sum(w.positive for w in windows)) / len(windows)
    annual = [r.oos_annualized for r in results if r.windows]
    mean_annual = sum(annual, ZERO) / len(annual)
    bh = [r.buy_hold_annualized for r in results if r.windows]
    mean_bh = sum(bh, ZERO) / len(bh)
    max_dd = max((r.max_oos_drawdown for r in results), default=ZERO)
    concentration = max((r.top2_profit_concentration for r in results if len(r.windows) > 3), default=ZERO)
    profitable_pairs = sum(1 for r in results if r.oos_compound_return > 0)

    def p(x: Decimal) -> str:
        return f"{x * 100:.1f}%"

    return [
        CriterionCheck(
            "positive OOS windows",
            positive_share >= c.min_positive_window_share,
            p(positive_share),
            f">= {p(c.min_positive_window_share)}",
        ),
        CriterionCheck(
            "mean OOS annualized return",
            mean_annual >= c.min_oos_annualized,
            p(mean_annual),
            f">= {p(c.min_oos_annualized)}",
        ),
        CriterionCheck(
            "beats buy & hold (annualized)", mean_annual > mean_bh, p(mean_annual), f"> {p(mean_bh)}"
        ),
        CriterionCheck("max OOS drawdown", max_dd <= c.max_drawdown, p(max_dd), f"<= {p(c.max_drawdown)}"),
        CriterionCheck(
            "not carried by 1-2 windows (worst pair)",
            concentration <= c.max_top2_concentration,
            p(concentration),
            f"<= {p(c.max_top2_concentration)} of positive return from top-2 windows",
        ),
        CriterionCheck(
            "not carried by 1-2 pairs",
            profitable_pairs >= min(3, len(results)),
            f"{profitable_pairs}/{len(results)} pairs profitable",
            f">= {min(3, len(results))} pairs",
        ),
    ]
