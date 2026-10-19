---
name: run-backtest
description: Run a Pendulum backtest or walk-forward optimization and summarize honest metrics (net of fees, with unrealized PnL, vs buy & hold). Arguments - pair(s), period, grid params or "walk-forward".
---

1. Ensure the local stack is up (`make up`) and candles for the requested pairs/period are loaded and gap-free
   (run the history loader / gap check if not).
2. Run the backtester CLI via `make backtest ARGS="..."`. For parameter search always use walk-forward mode;
   never select parameters on the window they are evaluated on.
3. Store full results in PostgreSQL (with parameters and git SHA) and write a detailed report to
   `docs/reports/backtests/<YYYY-MM-DD>-<pairs>.md`. Do not paste large tables into chat.
4. Summarize in chat (Russian), per pair: net return, annualized return, max drawdown, cycles/day, fee share,
   time in range, range exits, buy & hold return, and for walk-forward: share of positive OOS windows and OOS vs
   in-sample gap.
5. Flag explicitly: results carried by 1–2 windows, OOS much worse than in-sample, large unrealized loss in
   inventory, grid steps close to round-trip fees.
6. Always label results as historical simulation, not a forecast.
