# Research: viability of static spot grids on Bybit (October 2026)

> ⚠️ Historical simulation, not a forecast. All results include maker/taker fees, slippage on market
> orders and the unrealized P&L of the remaining inventory. The fill model is described in
> [docs/fill-model.md](../fill-model.md).

## Question

Does a static spot grid (a ladder of limit buy/sell orders inside a price range) earn money on liquid USDT
pairs after all costs, out-of-sample, and does it beat simply holding the coin?

## Summary

On these pairs and periods, **no**. Grid cycles are profitable after fees, but in trending markets the loss
on accumulated inventory (falling markets) or the missed upside (rising markets) outweighs the cycle income.
A regime filter that keeps the grid flat during trends reduces the damage but does not make the strategy
profitable. None of the configurations tested passes the viability thresholds.

## Setup

| Item | Value |
|---|---|
| Exchange | Bybit spot, public V5 market data |
| Pairs | BTC, ETH, SOL, XRP, DOGE, LINK, LTC, AAVE, SUI, ENA (all /USDT): 5 large caps plus 5 mid caps from the top of the pair scanner |
| Data | 1m candles with 100% coverage (ENA from its listing on 2024-04-02), simulated on 5m |
| Capital | $150 per pair (`CAPITAL_TOTAL_USD=500`, `CAPITAL_PAIR_PCT=30`) |
| Validation | walk-forward: 60-day train window → 14-day unseen test window, step 14 days |
| Selection | highest in-sample net return with in-sample max drawdown ≤ 20% and a positive return. If no set qualifies, the window is spent in cash (0%, counted as not positive) |

At $150 per pair, grids of 30 or 40 levels break Bybit's 5 USDT minimum order size. They are rejected, just
as they would be on the exchange.

### Viability thresholds

| Criterion | Threshold |
|---|---|
| Net-positive out-of-sample windows | ≥ 60% |
| Mean out-of-sample annualized return | ≥ 15% and above buy & hold |
| Max out-of-sample drawdown (chained across windows) | ≤ 20% |
| Robustness | the top 2 windows give ≤ 50% of the positive return; ≥ 3 pairs profitable |
| Simulator check | paper trading for ≥ 14 days diverges from a backtest of the same period by ≤ 20% |

## Experiment 1: static grid, last 12 months

Period 2025-10-19 → 2026-10-19 (21 test windows per pair), 96 parameter sets per window:
10/20/30/40 levels × 6/10/16/24% width × geometric/arithmetic × exit policy
(`stop_loss+trailing_up`, `rebuild`, `wait`).

| Pair | Positive windows | OOS return | OOS annualized | Buy & hold (same windows) | Max DD |
|---|---:|---:|---:|---:|---:|
| BTC/USDT | 28.6% | −25.7% | −30.8% | −6.4% | 35.2% |
| ETH/USDT | 23.8% | −46.8% | −54.3% | −12.0% | 52.4% |
| SOL/USDT | 28.6% | −44.4% | −51.8% | −8.6% | 54.9% |
| XRP/USDT | 19.0% | −19.3% | −23.3% | −26.1% | 30.4% |
| DOGE/USDT | 19.0% | −15.8% | −19.2% | −31.7% | 29.0% |
| LINK/USDT | 28.6% | −35.0% | −41.4% | +5.4% | 46.1% |
| LTC/USDT | 28.6% | −8.5% | −10.4% | −15.7% | 24.4% |
| AAVE/USDT | 23.8% | −29.1% | −34.7% | −6.3% | 46.5% |
| SUI/USDT | 33.3% | −57.0% | −65.0% | −23.2% | 63.6% |
| ENA/USDT | 28.6% | +26.9% | +34.4% | +7.5% | 26.1% |

| Criterion | Result |
|---|---|
| Positive OOS windows | ❌ 26.2% |
| Mean OOS annualized | ❌ −29.7% (buy & hold −14.1%) |
| Max OOS drawdown | ❌ 63.6% |
| Top-2 window concentration (worst pair) | ❌ 73.0% |
| Profitable pairs | ❌ 1 of 10 |

**Where the loss comes from.** Fixed parameters over the full year (geometric, 30 levels, 40% width, `wait`
policy, $1000 capital):

| Pair | Realized (cycles) | Unrealized (inventory) | Fees | Net | Buy & hold |
|---|---:|---:|---:|---:|---:|
| BTC/USDT | +56.0 | −177.5 | 9.5 | −12.2% | −23.5% |
| ETH/USDT | +68.0 | −334.9 | 11.1 | −26.7% | −38.5% |
| LTC/USDT | +166.1 | −324.6 | 22.8 | −15.8% | −34.8% |
| ENA/USDT | +68.0 | −567.3 | 8.9 | −49.9% | −59.7% |

The grid loses less than buy & hold in a falling market. However, the inventory it accumulates on the way
down costs 2–8× more than all the cycle profit.

## Experiment 2: regime filter, 3 years

**Hypothesis:** trading only while the market is range-bound fixes the trend problem. The filter is a rolling
efficiency ratio of hourly closes (`er72`, `er168`) with hysteresis. When a trend starts, the grid moves to
USDT; when the range returns, it re-opens around the current price.

**Setup:**
- Period: 2023-10-19 → 2026-10-19, which covers bull, bear and sideways phases (74 test windows per pair).
- Search space: 144 sets = 10/20 levels × 6/10/16/24% width × 2 grid types × 3 exit policies × `none`/`er72`/`er168`.
- Coverage: completed for 3 of the 10 pairs. The remaining runs were stopped by memory limits on the
  research machine.

| Pair | Positive windows | OOS return (3 y) | OOS annualized | Buy & hold (same windows) | Max DD |
|---|---:|---:|---:|---:|---:|
| BTC/USDT | 54.1% | −28.6% | −11.2% | +77.6% | 53.9% |
| ETH/USDT | 29.7% | −63.7% | −30.0% | −1.8% | 76.2% |
| SOL/USDT | 44.6% | −49.3% | −21.3% | +31.5% | 73.3% |

**Effect of the filter.** Average OOS return per test window, grouped by the filter chosen in-sample:

| Pair | `none` | `er72` | `er168` | Untraded windows |
|---|---:|---:|---:|---:|
| BTC/USDT | −0.31% (47 windows) | +0.93% (1) | −0.84% (12) | 14 |
| ETH/USDT | −2.33% (31) | −1.84% (10) | +0.28% (9) | 24 |
| SOL/USDT | −1.40% (30) | +0.67% (11) | −0.90% (15) | 18 |

The filter moves the per-window average from about −1…−2% to about 0%. That is not enough to make the
strategy profitable. The incomplete pairs cannot change the overall result: the drawdown criterion is
evaluated on the worst pair and is already 76%.

## Observations

- **Overfitting.** Parameter sets chosen in-sample earned +10…+21% per 60-day window but averaged
  −5…+2% per 14-day test window. Grid parameters that fit the past do not carry over.
- **Trend asymmetry.** In falling markets the grid accumulates inventory. In rising markets it sells too early:
  BTC rose 78% over three years while the grid ended negative.
- **Mechanics are sound.** Cycles are profitable after fees, and fees are a small share of gross cycle profit.
  The losses come from market exposure, not from costs.
- **Simulator optimism.** The model ignores queue position and partial fills, which favours the grid. A more
  realistic model would not improve the result.

## Reproduce

```bash
make data-load ARGS="--pairs BTC/USDT,ETH/USDT,SOL/USDT,XRP/USDT,DOGE/USDT,LINK/USDT,LTC/USDT,AAVE/USDT,SUI/USDT,ENA/USDT --since 2023-10-19"
# Experiment 1
make walk-forward ARGS="--pairs BTC/USDT,ETH/USDT,SOL/USDT,XRP/USDT,DOGE/USDT,LINK/USDT,LTC/USDT,AAVE/USDT,SUI/USDT,ENA/USDT --since 2025-10-19"
# Experiment 2 (allow several hours; reduce --workers on machines with little RAM)
make walk-forward ARGS="--pairs BTC/USDT,ETH/USDT,SOL/USDT --since 2023-10-19 --grid regime --workers 3"
```

## Open directions

- Complete Experiment 2 for all pairs and run the 48-set `--grid baseline` ablation on the same data.
- Market-neutral variants: a grid hedged with a short perpetual, or stablecoin/stablecoin pairs.
- Volatility-scaled range width and dynamic per-pair capital allocation.
