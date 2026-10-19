# Pendulum — TODO

Legend: `[x]` done and verified · `[ ]` open (status note in parentheses if partially done).
Workflow: owner request → items under **In progress** → implementation → `/audit` → tick + commit (see `CLAUDE.md`).

---

## In progress

_Nothing in progress. New tasks and plans from the owner are added here._

---

## Backlog

- [ ] Complete the regime-filter walk-forward for all 10 research pairs (3 years) and the `--grid baseline` ablation
- [ ] Market-neutral variants: grid hedged with a short perpetual; stablecoin/stablecoin pairs
- [ ] Volatility-scaled range width and dynamic per-pair capital allocation
- [ ] Configurable viability thresholds via CLI/settings instead of code defaults
- [ ] Faster candle ingestion (asyncpg `COPY`) and lighter in-memory candle representation for multi-year runs
- [ ] Regime filter support in paper sessions (currently backtest/walk-forward only)
- [ ] Loader: track attempted spans as a list instead of a single contiguous checkpoint
- [ ] HTML/Chart.js report for walk-forward results (equity curves, per-window table)
- [ ] Second exchange adapter (e.g. OKX or Binance) passing `tests/contract/`

---

## Done

### Core
- [x] Project skeleton: uv, Python 3.12, ruff, mypy strict, pytest/hypothesis, Makefile, structured logging with secret redaction
- [x] Modular Pydantic settings, per-pair TOML overrides, `.env.example`
- [x] Decimal domain models and conservative rounding (property-tested)
- [x] PostgreSQL schema (SQLAlchemy async + Alembic), repositories, idempotent fills/cycles

### Data and exchanges
- [x] Exchange interfaces, registry (`EXCHANGE_ACTIVE`), adapter contract tests
- [x] Bybit V5 REST (instruments, klines, tickers, fee rate) with rate limiting and retries
- [x] Bybit V5 public WebSocket with heartbeat, reconnect, resubscribe and gap events
- [x] Resumable candle loader (checkpoints, concurrent fetching, gap detection and backfill), CLI and periodic sync

### Analysis
- [x] Grid strategies (arithmetic, geometric), plan builder with fee-aware step filtering and exchange minimums
- [x] Range-exit policies (stop-loss, wait, rebuild, trailing-up, composite)
- [x] Regime filter (rolling efficiency ratio with hysteresis) with re-entry
- [x] Conservative virtual exchange shared by backtests and paper trading (`docs/fill-model.md`)
- [x] Backtester with realized/unrealized P&L, intra-bar drawdown, buy & hold comparison, reports and DB persistence
- [x] Walk-forward optimisation (parameter spaces `default`, `regime`, `baseline`, `quick`), chained OOS drawdown, forkserver workers
- [x] Viability check (OOS consistency, return vs buy & hold, drawdown, concentration)
- [x] Pair scanner (oscillation vs trend scoring, liquidity filters) with weekly refresh

### Paper trading and notifications
- [x] Paper sessions on live trades: restart-safe snapshots, stream-gap backfill, stop/re-arm handling
- [x] Paper vs backtest comparison (`pendulum paper compare`)
- [x] Telegram notifier (HTML, rate limited, never raises) with start, range-exit, stop, daily and weekly messages

### Infrastructure and quality
- [x] `docker-compose.local.yml` with per-service secrets, healthchecks and startup order
- [x] Reference server stack `docker-compose.yml` and manual-only `deploy.yml`
- [x] CI: lint, types, unit and integration tests, compose validation, image build
- [x] Security & correctness audits (`docs/reports/audits/`), all findings fixed
- [x] Research report: `docs/reports/research-grid-viability-2026-10.md`
