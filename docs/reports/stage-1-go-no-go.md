# Stage 1 Go / No-Go report

> **Status: PRELIMINARY.** The backtest part is complete. The paper-trading part (≥ 14 days of live
> paper trading compared against a backtest of the same period) is still pending. The final
> recommendation is made after the paper run.
>
> ⚠️ All numbers below are historical simulations, not a forecast. They include maker/taker fees,
> slippage on market orders, and unrealized P&L of the remaining inventory.

- Date: 2026-10-19
- Exchange: Bybit spot (public V5 data), quote asset USDT
- Code version: branch `stage-1-mvp` (see `git log`), fill model in [docs/fill-model.md](../fill-model.md)
- Capital: **$150 per pair**, i.e. the agreed $500 total with a 30% per-pair cap

## 1. Data

| Item | Value |
|---|---|
| Pairs | BTC, ETH, SOL, XRP, DOGE, LINK, LTC, AAVE, SUI, ENA (all /USDT) |
| Pair choice | 5 large caps + 5 mid caps from the top of the pair scanner (2026-10-19) |
| History | 1m candles, 2025-10-19 → 2026-10-19 (≈ 526k bars per pair) |
| Coverage | 100% for every pair (no missing bars) |
| Simulation timeframe | 5m (resampled from 1m) |

**Market regime:** for 9 of the 10 pairs, the year was bearish. Buy & hold over the full year ranged from
−23.5% (BTC) to −59.7% (ENA). LINK was the only pair that rose over the test windows.

## 2. Method

- **Walk-forward:** train on 60 days, test on the next 14 days, step 14 days. That gives 21 out-of-sample
  windows per pair, 210 in total.
- **Search space:** 96 parameter sets: 10/20/30/40 levels × 6/10/16/24% range width × geometric/arithmetic
  grid × exit policies `stop_loss+trailing_up`, `rebuild`, `wait`.
- **Selection:** the parameter set with the highest in-sample net return, subject to an in-sample max
  drawdown ≤ 20% and a positive in-sample return. If no set qualifies, the bot stays in cash for that window.
  Such a window counts as **not positive** and has a 0% return.
- **Exchange limits:** with $150 per pair, grids of 30 or 40 levels break Bybit's 5 USDT minimum order size.
  They are rejected, which matches how they would behave live.

Reproduce:

```bash
make data-load ARGS="--pairs BTC/USDT,ETH/USDT,SOL/USDT,XRP/USDT,DOGE/USDT,LINK/USDT,LTC/USDT,AAVE/USDT,SUI/USDT,ENA/USDT --since 2025-10-19"
make walk-forward ARGS="--pairs BTC/USDT,ETH/USDT,SOL/USDT,XRP/USDT,DOGE/USDT,LINK/USDT,LTC/USDT,AAVE/USDT,SUI/USDT,ENA/USDT --since 2025-10-19"
```

## 3. Out-of-sample results per pair

All returns are chained across the 21 test windows (≈ 294 days). "Untraded" counts the windows in which no
parameter set qualified in-sample.

| Pair | Positive windows | OOS return | OOS annualized | Buy & hold (same windows) | Max DD (chained) | Untraded | In-sample → OOS mean |
|---|---:|---:|---:|---:|---:|---:|---|
| BTC/USDT | 28.6% | −25.7% | −30.8% | −6.4% | 35.2% | 10 | +10.9% → −2.3% |
| ETH/USDT | 23.8% | −46.8% | −54.3% | −12.0% | 52.4% | 10 | +15.8% → −4.9% |
| SOL/USDT | 28.6% | −44.4% | −51.8% | −8.6% | 54.9% | 8 | +13.5% → −3.7% |
| XRP/USDT | 19.0% | −19.3% | −23.3% | −26.1% | 30.4% | 10 | +10.0% → −1.8% |
| DOGE/USDT | 19.0% | −15.8% | −19.2% | −31.7% | 29.0% | 11 | +12.7% → −1.6% |
| LINK/USDT | 28.6% | −35.0% | −41.4% | +5.4% | 46.1% | 8 | +14.2% → −2.8% |
| LTC/USDT | 28.6% | −8.5% | −10.4% | −15.7% | 24.4% | 11 | +10.8% → −0.8% |
| AAVE/USDT | 23.8% | −29.1% | −34.7% | −6.3% | 46.5% | 10 | +12.2% → −2.4% |
| SUI/USDT | 33.3% | −57.0% | −65.0% | −23.2% | 63.6% | 6 | +10.8% → −4.8% |
| ENA/USDT | 28.6% | **+26.9%** | **+34.4%** | +7.5% | 26.1% | 9 | +20.8% → +2.3% |

## 4. Go/No-Go criteria (backtest part)

| Criterion (agreed 2026-10-19) | Result | Value | Threshold |
|---|:-:|---:|---|
| Positive OOS windows | ❌ | 26.2% | ≥ 60% |
| Mean OOS annualized return | ❌ | −29.7% | ≥ 15% |
| Beats buy & hold (annualized) | ❌ | −29.7% | > −14.1% |
| Max OOS drawdown | ❌ | 63.6% | ≤ 20% |
| Not carried by 1–2 windows (worst pair) | ❌ | 73.0% | ≤ 50% of positive return from the top 2 windows |
| Not carried by 1–2 pairs | ❌ | 1/10 pairs profitable | ≥ 3 pairs |
| Paper vs backtest divergence over ≥ 14 days | ⏳ | pending | ≤ 20% |

**The backtest part fails all six criteria.**

## 5. Why the grid loses: realized vs unrealized

Fixed parameters over the full year (no optimisation), capital $1000, so that the grids can be denser:

| Pair | Grid | Realized (cycles) | Unrealized (inventory) | Fees | Net | Buy & hold |
|---|---|---:|---:|---:|---:|---:|
| BTC/USDT | geom 30L / 40% / wait | +56.0 | −177.5 | 9.5 | −12.2% | −23.5% |
| ETH/USDT | geom 30L / 40% / wait | +68.0 | −334.9 | 11.1 | −26.7% | −38.5% |
| LTC/USDT | geom 30L / 40% / wait | +166.1 | −324.6 | 22.8 | −15.8% | −34.8% |
| ENA/USDT | geom 30L / 40% / wait | +68.0 | −567.3 | 8.9 | −49.9% | −59.7% |

The mechanics work as intended: cycles are profitable after fees, and fees take a small share of the gross
cycle profit. In a falling market, though, the grid keeps buying the way down, and the loss on the inventory
it holds (the "bag") is 2–8× larger than everything the cycles earn. The grid loses less than buy & hold,
but it still loses money, and the agreed target is an absolute return.

A denser grid with more capital ($1000, 30 levels) does not change the picture. It produces more cycles, but
the inventory loss grows with it.

## 6. Robustness and weak points of this analysis

- **Overfitting:** in every pair, the parameter sets chosen in-sample earned +10…+21% per 60-day train
  window but averaged −5…+2% per 14-day test window. Past parameters do not carry over.
- **One regime only:** the year tested is mostly bearish. A grid is expected to do better in a sideways
  market. Adding older history (2023–2025) would show whether the result depends on the regime. That is an
  "Iterate" option, not something this report can claim.
- **ENA** is the only profitable pair, and 73% of its positive return comes from two windows. One pair and a
  few windows do not make a robust result.
- **Optimistic simplifications** in the simulator (no queue position, no partial fills) favour the grid. The
  conclusion would not improve with a more realistic model.
- **Single-run stop-loss:** the fixed-parameter table uses `wait` because `stop_loss+trailing_up` gets stopped
  early in a long run and then sits in cash. Walk-forward avoids this, because every window starts again.

## 7. Preliminary recommendation

**Backtest evidence: No-Go for a grid on these pairs in the current regime.** None of the criteria come close.

Before the final decision:

1. Run paper trading for ≥ 14 days (in progress after `make up`). It checks the simulator against live data.
   It cannot reverse a backtest failure of this size.
2. Then choose one of:
   - **Iterate** (at most 1–2 iterations), for example:
     - a trend filter (the grid trades only while the pair scanner's efficiency ratio and drift say "range");
     - a longer history covering several regimes;
     - a range-bound pair universe;
     - re-checking against the same criteria.
   - **No-Go:** keep Pendulum as a backtesting & paper-trading toolkit.

The final section of this report is completed after the paper run.
