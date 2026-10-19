# Pendulum — TODO

Legend: `[x]` done and verified · `[ ]` open (status note in parentheses if partially done).
Current stage: **Stage 1** (branch `stage-1-mvp`).

---

## Stage 0 — Infrastructure

- [x] Clarifying questions answered (exchange, pairs, capital, Go/No-Go, grid type, VPS budget, admin auth)
- [x] `CLAUDE.md` (commands, style, workflow, branches, fork protocol, compaction block, decisions)
- [x] `.claude/agents/`: `implementation-agent`, `security-trading-risk-critic`
- [x] `.claude/skills/`: `check-code-quality`, `run-tests`, `audit-risks`, `run-backtest`
- [x] `todo.md` with Stages 1–3
- [x] Git: `.gitignore` (project additions), `.env.example`, `origin` connected, first commit + push
- [x] `README.md` header + `docs/assets/logo.svg`
- [x] `docs/guides/stage-1-user-guide.md`
- [x] Owner says "Start Implementation"

---

## Stage 1 — MVP + data (read-only, no real money) · branch `stage-1-mvp` · tag `v0.1.0`

### 1.1 Project skeleton
- [x] `pyproject.toml` (uv, Python 3.12), `uv.lock`, src layout `src/pendulum/`
- [x] ruff + mypy strict + pytest/pytest-asyncio/hypothesis config
- [x] `Makefile` (install, lint, format, typecheck, test, check, up, down, migrate, data-load, backtest)
- [x] Structured logging with secret redaction
- [x] Multi-stage `Dockerfile` (non-root user, uv cache)

### 1.2 Configuration
- [x] Modular Pydantic Settings: `AppSettings`, `DatabaseSettings`, `RedisSettings`, `ExchangeSettings`,
      `GridSettings`, `RiskSettings`, `TelegramSettings`, `BacktestSettings`, `AdminSettings`
- [x] Per-pair overrides (grid type, levels, range) via config file (YAML/TOML) validated by pydantic
- [x] `.env.example` kept in sync; tests for settings validation

### 1.3 Domain & money
- [x] Domain models: `Symbol`, `MarketFilters` (tickSize, stepSize, minQty, minNotional), `Candle`, `Order`,
      `Fill`, `GridLevel`, `Cycle`, `EquityPoint` — all `Decimal`
- [x] Money/rounding helpers (buy down, sell up, qty down) + property tests

### 1.4 Storage
- [x] SQLAlchemy async models: candles, symbols/filters, grid states, orders, fills, cycles, equity curve,
      backtest runs (params + git SHA + metrics), paper sessions, scanner scores
- [x] Alembic setup + initial migration; migrations run in a dedicated one-shot container
- [x] Repositories (narrow interfaces) + integration tests

### 1.5 Exchange layer (multi-exchange ready)
- [x] Interfaces: `IRestMarketData`, `IWebSocketTicker`, `IOrderGateway`; `BaseExchangeAdapter`
- [x] `ExchangeRegistry` / factory: adapters registered by `ExchangeId`; active exchange chosen by
      `EXCHANGE_ACTIVE`; per-exchange credentials (optional in Stage 1: public data only, fees from settings) and fee schedule in settings
- [x] Bybit V5 adapter: REST (instruments info, klines, tickers with best bid/ask, account fee rate) via own `httpx` client
- [x] Bybit V5 public WebSocket (public trades) with reconnect, resubscribe, heartbeat, silence watchdog, gap events
- [x] Rate-limit handling (token bucket) + retries with backoff
- [x] Contract test suite every adapter must pass (so a 2nd exchange can be added without core changes)

### 1.6 Market data collector
- [x] Historical candle loader (1m/5m) with checkpoints, resume, idempotent upserts
- [x] Gap detection + backfill; data-quality report
- [x] Celery task for periodic incremental sync
- [x] CLI: `pendulum data load --pairs ... --tf 1m --since ...`

### 1.7 Grid engine
- [x] `IGridStrategy` + `ArithmeticGrid`, `GeometricGrid` (default)
- [x] Level/size calculation respecting exchange filters; skip levels whose step ≤ round-trip fees + margin
- [x] Capital allocation per level (quote/base split at start price)
- [x] `IRangeExitPolicy`: `StopLossExit`, `WaitExit`, `RebuildExit`, `TrailingUpExit`
- [x] Property tests (monotonic levels, filters respected, no negative balances)

### 1.8 Fill simulator & backtester
- [x] Conservative fill simulator: cross-not-touch, worst-case intra-candle sequence, maker fee per fill,
      taker fee + slippage for market exits, fee-in-base-coin option; assumptions documented in `docs/fill-model.md`
- [x] `VirtualOrderGateway` implementing `IOrderGateway` (shared by backtest and paper)
- [x] Backtester: realized, unrealized, net return, annualized, max drawdown, cycles/day, fee share,
      time in range, range exits, buy & hold comparison
- [x] Results persisted with parameters and git SHA; CLI + markdown report output

### 1.9 Walk-forward parameter search
- [x] Parameter grid (levels, range width, grid type, exit policy): 96-set default, 12-set quick
- [x] Walk-forward: optimize on train window, evaluate on next unseen window; aggregate OOS metrics
- [x] Robustness checks: share of positive OOS windows, per-pair/per-window concentration

### 1.10 Pair scanner
- [x] Score pairs: volatility, range-boundness (daily efficiency ratio), drift, 24h volume ≥ threshold, spread,
      fee-to-range ratio, tick granularity; risky (innovation/ST) tokens excluded
- [x] Weekly Celery beat recalculation; results stored

### 1.11 Paper trader
- [x] Paper sessions on live WS trades via `VirtualOrderGateway` (same fill rules), resume after restart, candle backfill of stream gaps
- [x] Paper sessions persisted; comparison vs backtest over the same period (`pendulum paper compare`)
- [ ] Runs ≥ 14 days on selected pairs (owner started the stack on 2026-10-19; plans a few days first, then `make paper-compare`)

### 1.12 Telegram notifier
- [x] HTML-formatted messages: start/resume, daily virtual summary, range-exit signal, stop, weekly results
- [x] Rate-limited sender, retries, no secrets in messages or logs

### 1.13 Local infrastructure
- [x] `docker-compose.local.yml`: postgres, redis, migrations, collector (Celery `data` queue), telegram-worker
      (Celery `notify` queue), beat, paper-trader, tools (backtests/CLI); healthchecks + `depends_on: service_healthy`;
      per-service env subsets
- [x] `docker compose config` validated; full stack built and verified healthy end to end

### 1.14 Go / No-Go
- [x] Go/No-Go criteria (agreed 2026-10-19):
  - net return after fees positive in **≥ 60%** of out-of-sample windows;
  - mean OOS annualized return **> buy & hold** and **≥ 15%/year** with max drawdown **≤ 20%**;
  - paper trading **≥ 14 days** diverges from backtest of the same period by **≤ 20%**;
  - not carried by 1–2 lucky windows or pairs.
- [ ] `docs/reports/stage-1-go-no-go.md` (metrics, best pairs/params, risks, recommendation) (preliminary: backtest part done 2026-10-19, all 6 backtest criteria fail; paper part pending)
- [ ] `/audit-risks` passed (first run 2026-10-19: FIX BEFORE RELEASE → findings fixed, re-audit before tag; see `docs/reports/audits/2026-10-19-stage-1-mvp.md`)
  - [x] H1 chained OOS drawdown
  - [x] M1 intra-bar drawdown at candle low
  - [x] M2 paper restart duplicates (snapshot per fill batch, prune on resume)
  - [x] M3 gap backfill from last event + trailing minute
  - [x] M4 stopped sessions require explicit re-arm
  - [x] L1–L7 low findings
  - [x] Re-audit follow-ups N1–N6
- [ ] `docs/guides/stage-2-user-guide.md` (only if Go)
- [ ] README updated (features, quick start, architecture, status badge) · `chore: complete stage 1` · tag `v0.1.0`

---

## Post-Stage-1 fork

- [ ] Discuss `docs/reports/stage-1-go-no-go.md` with the owner (Russian, honest)
- [x] Owner chose **Iterate** (2026-10-19), iteration 1 runs in parallel with the paper test

### Iteration 1 (of max 2): regime filter + multi-regime history
Hypothesis: the grid loses because it keeps trading through trends. Trading only while the market is
range-bound (rolling efficiency ratio low) and sitting in USDT during trends turns the OOS result positive.
Criteria: **unchanged** (same six backtest criteria + paper divergence), evaluated on the same 10 pairs.
- [ ] Regime filter (`IRegimeFilter`): rolling hourly efficiency ratio with hysteresis; grid liquidates to USDT
      when a trend starts and re-enters around the current price when the range returns
- [ ] Backtester supports re-entry after filter/stop exits; filter is a walk-forward parameter
- [ ] History extended to 2023-10-19 → 2026-10-19 (bull, bear and sideways phases)
- [ ] Walk-forward on 10 pairs: filtered vs unfiltered on the same 3-year data
- [ ] Iteration section in `docs/reports/stage-1-go-no-go.md`; decision Go / Iterate (last) / No-Go
- [ ] Decision recorded: Go → Stage 2 · Iterate (max 1–2, goals + criteria added here) · No-Go → reposition as
      analysis toolkit (README, CLAUDE.md, `.claude/`, todo, code cleanup, optional deployability showcase, `v1.0.0`)

---

## Stage 2 — Production, real money (only after Go) · branch `stage-2-prod` · tag `v0.2.0`

### 2.1 Live execution
- [ ] `BybitOrderGateway` (`IOrderGateway`): place/cancel limit orders, order status, idempotent `clientOrderId`
- [ ] Private WebSocket (orders, executions, wallet) with reconnect
- [ ] Order Manager: order + grid state machine (partial fills, cancels, rejects, retries)
- [ ] Startup reconciliation (local state vs exchange, no duplicate orders after restart)
- [ ] Testnet run before mainnet

### 2.2 Risk manager
- [ ] `IRiskRule` implementations: max capital per pair (30%), max total ($500), daily loss limit,
      max open orders, range-exit stop-loss
- [ ] Kill-switch: Telegram command + automatic triggers
- [ ] Trading halt on anomalies (state divergence, stale data, suspicious prices)
- [ ] Start with ~$100 on one pair; scale only after owner approval

### 2.3 Production infrastructure & CI/CD
- [ ] Prod `docker-compose.yml` (healthchecks, startup order, per-role secrets)
- [ ] `.github/workflows/ci.yml`: ruff, mypy, pytest, `docker compose config`, image build; required for merge
- [ ] Image publishing to GHCR (version + SHA tags) on push to `main` / tag `v*`
- [ ] `.github/workflows/deploy.yml`: SSH deploy → pull → Alembic migrations container → `up -d`
- [ ] Healthcheck verification + automatic rollback + manual rollback via `workflow_dispatch`
- [ ] Telegram notification for every deploy
- [ ] `production` environment with required reviewer
- [ ] First successful automated deploy to VPS

### 2.4 Telegram (live)
- [ ] 🟢 closed cycle: pair, buy/sell prices, qty, fees, net USD profit
- [ ] ⚠️ warnings: range break, partial fill, reject, connection loss, limits/kill-switch
- [ ] 📊 daily digest + monthly report on the 1st

### 2.5 Completion
- [ ] `/audit-risks` passed
- [ ] `docs/guides/stage-3-user-guide.md`
- [ ] README status badge · `chore: complete stage 2` · tag `v0.2.0`

---

## Stage 3 — Growth & analytics (only after Go) · branch `stage-3-dashboard` · tag `v0.3.0`

- [ ] Multi-pair concurrent grids + capital allocation between pairs
- [ ] Second exchange adapter as plugin (passes adapter contract tests)
- [ ] Automatic pair rotation from Pair Scanner results
- [ ] Trailing grid / dynamic range recalculation
- [ ] FastAPI admin panel with auth (`ADMIN_USERNAME` + bcrypt `ADMIN_PASSWORD_HASH`, JWT/Basic)
- [ ] Analytics API: TOP profit by day/week/month/all-time, TOP cycles, TOP pairs/exchanges/param sets
- [ ] Chart.js UI: cumulative PnL (realized / with unrealized), equity vs buy & hold, drawdown,
      cycles per day, capital utilization
- [ ] Tables: open grids, recent trades, range breaks, errors
- [ ] Backtest vs reality comparison per pair (degradation detection)
- [ ] System health: worker uptime, data lag, API errors, rate-limit headroom
- [ ] Backups, monitoring (Healthchecks/Sentry/Prometheus as needed), profiling
- [ ] `/audit-risks` passed · README status badge · `chore: complete stage 3` · tag `v0.3.0`
