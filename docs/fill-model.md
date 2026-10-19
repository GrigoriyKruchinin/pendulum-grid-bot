# Fill model and simulation assumptions

Pendulum's backtester and paper trader share one matching engine (`pendulum.execution.virtual.VirtualExchange`).
Its rules are deliberately pessimistic, so simulated results should be an upper bound on what is realistic
only where noted, and a conservative estimate everywhere else.

## Limit orders (grid orders)

| Rule | Backtest (candles) | Paper (live trades) |
|---|---|---|
| Fill condition | buy: `low < price`; sell: `high > price` (strict **cross**, a touch is not a fill) | buy: trade `price < level`; sell: trade `price > level` |
| Fill price | the limit price (never better) | the limit price |
| Fee | maker fee on every fill | maker fee |
| Eligibility | an order placed while processing bar *t* can only fill from bar *t+1* | only trades strictly after placement |
| Partial fills | not modelled: an order fills fully once crossed | same |
| Queue position | ignored: crossing by one tick is assumed enough | same |

**Intra-bar ordering.** Because a newly placed order is not eligible inside the bar that triggered it, no
buy→sell round trip is ever credited within a single bar, even if the bar's range would allow it. Orders that
were already resting fill independently of the path inside the bar, so the order of high and low does not
matter for them. This is the worst-case ordering for a grid.

## Market orders (initial purchase, rebalancing, exits)

- Executed at the reference price worsened by `BACKTEST_SLIPPAGE_BPS` (default 5 bps), plus the taker fee.
- Reference price: the bar close for initial purchase and rebuilds; **the bar low** for stop-loss exits (worst case).

## Fees

- Default 0.1% maker / 0.1% taker (Bybit spot, no VIP). If a read-only API key is configured, the account's
  real fee rate is used.
- Bybit charges the buy fee in the received base coin and the sell fee in quote. The simulator does the same:
  a buy of `q` yields `q × (1 − fee)` base. The following sell uses the received amount rounded down to the
  step size; the remainder ("dust") stays in the balance and is marked to market.

## Grid construction

- Levels are rounded down to `tickSize`; quantities are rounded down to `stepSize`.
- A level is dropped if its step over the previous kept level does not exceed
  `2 × maker fee × GRID_MIN_STEP_FEE_MULTIPLIER` (default 1.5).
- Every order must satisfy `minOrderQty` and `minOrderAmt` (also after the base-coin fee on the sell side);
  otherwise the plan is rejected as "capital too small".
- Slot budget = `capital / slots / (1 + taker + 2 × slippage)`, so the plan always fits the capital.

## Valuation and metrics

- **Equity** = quote + base × close price (free + locked). The end of the period is valued mark-to-market;
  inventory is not liquidated.
- **Realized** = sum of net profits of completed buy→sell cycles.
- **Unrealized** = equity change − realized (inventory gains/losses, entry costs, dust).
- **Buy & hold** buys at the first open with taker fee + slippage and is valued at the last close.
- **Max drawdown** is computed on every bar close.
- **Annualized return** compounds the period return to 365 days. Short periods produce extreme
  annualized figures. Read them together with the period length.

## Known optimistic simplifications

- No queue position or partial fills: in thin books a cross by one tick may not fill a real order.
- No exchange downtime, rejects or latency.
- Candles with missing bars are refused when more than 1% of bars are missing; smaller gaps are bridged.

Paper trading over at least 14 days, compared with a backtest of the same period, is the check that these
simplifications do not distort the results (one of the viability criteria).
