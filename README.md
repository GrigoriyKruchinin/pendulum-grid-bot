<div align="center">

<img src="docs/assets/logo.jpg" alt="Pendulum logo" width="180">

# Pendulum

**Async grid trading bot for the crypto spot market — backtest, paper-trade, then trade sideways ranges.**

Pulls market data from Bybit (V5 REST + WebSocket; more exchanges via pluggable adapters), evaluates grid
strategies net of maker/taker fees, slippage and unrealized inventory loss, and benchmarks them against buy & hold
with walk-forward validation. Results land in PostgreSQL; alerts and reports go to Telegram.

![Python](https://img.shields.io/badge/python-3.12-3776AB?logo=python&logoColor=white)
![mypy](https://img.shields.io/badge/mypy-strict-2A6DB2)
![ruff](https://img.shields.io/badge/lint-ruff-D7FF64?logo=ruff&logoColor=black)
![Docker Compose](https://img.shields.io/badge/docker-compose-2496ED?logo=docker&logoColor=white)
![Status](https://img.shields.io/badge/status-V0.1%20paper%20trading-yellow)

</div>

> ⚠️ Pendulum is research software. All backtest and paper-trading results are historical simulations, not a
> forecast or a guarantee of profit.

## Features

- **Market data** — resumable 1m candle loader for Bybit V5 with checkpoints, gap detection and backfill;
  incremental sync every 5 minutes.
- **Grid engine** — arithmetic and geometric grids, levels aligned to `tickSize`, quantities to `stepSize`,
  minimum order checks, and automatic removal of steps that cannot cover round-trip fees.
- **Conservative fill simulator** — limit orders fill only when price *crosses* the level, no intra-bar round
  trips, maker/taker fees (charged in the received asset, as on Bybit), slippage on market orders.
  See [docs/fill-model.md](docs/fill-model.md).
- **Backtester** — realized and unrealized P&L, annualized return, max drawdown, cycles per day, fee share,
  time in range, range exits, comparison with buy & hold. Results stored with parameters and git SHA.
- **Walk-forward optimisation** — parameters chosen on a train window, judged only on the next unseen window;
  Go/No-Go criteria evaluated across pairs.
- **Range-exit policies** — stop-loss, wait, rebuild, trailing-up and combinations.
- **Pair scanner** — ranks liquid USDT pairs by oscillation vs trend (efficiency ratio, drift, range vs fees).
- **Paper trader** — the same grid logic on live WebSocket trades with virtual orders; survives restarts and
  backfills stream gaps; compared against a backtest of the same period.
- **Telegram** — HTML alerts: session start, range exits, daily and weekly summaries.

## Quick start

Requirements: Docker with Compose v2, `make`. For development also [uv](https://docs.astral.sh/uv/).

```bash
cp .env.example .env          # set POSTGRES_PASSWORD, TELEGRAM_* (Bybit key optional in Stage 1)
make up                       # build, migrate, start collector, workers, beat and paper trader
make data-load ARGS="--pairs BTC/USDT,SOL/USDT --since 2025-10-01"
make walk-forward ARGS="--pairs BTC/USDT,SOL/USDT --since 2025-10-01"
make backtest ARGS="--pair SOL/USDT --since 2026-01-01 --levels 20 --width 16"
make scan                     # rank pairs for grid suitability
make paper-summary            # current paper results
```

Paper-traded pairs and per-pair overrides live in [config/pairs.toml](config/pairs.toml).

Development:

```bash
make install                  # uv sync
make check                    # ruff + mypy --strict + pytest
make test-integration         # needs Postgres (POSTGRES_* env) and network access to Bybit
```

## Architecture

```text
                 ┌──────────────── BaseExchangeAdapter (registry: EXCHANGE_ACTIVE) ───────────────┐
                 │  IRestMarketData (httpx)      IWebSocketTicker (websockets)    IAccountData    │
                 └───────┬──────────────────────────────┬──────────────────────────────────────────┘
                         │ candles / instruments        │ live trades
              ┌──────────▼─────────┐            ┌───────▼────────┐
              │ HistoryLoader      │            │ PaperRunner    │
              │ PairScanner        │            │  PaperSession  │──── INotifier (Telegram)
              └──────────┬─────────┘            └───────┬────────┘
                         │ ICandleStore                 │
   ┌─────────────────────▼──────┐          ┌────────────▼─────────────┐
   │ Backtester / Walk-forward  │          │ GridTrader (grid logic)  │◄── IGridStrategy, IRangeExitPolicy
   │  run_backtest(candles)     ├─────────►│   uses IOrderGateway     │
   └────────────────────────────┘          └────────────┬─────────────┘
                                                        │
                     VirtualOrderGateway + VirtualExchange (backtest, paper)  ·  live gateway (Stage 2)
```

- **One grid logic, three executors.** `GridTrader` talks only to `IOrderGateway`. The backtester and paper
  trader use the virtual exchange; Stage 2 adds a live Bybit gateway without touching the grid core.
- **Exchanges are plugins.** Implement `BaseExchangeAdapter` in `pendulum/exchanges/<name>/`, add an
  `EXCHANGE_<NAME>_*` settings class, register a factory in `exchanges/registry.py`, then pass the contract
  tests in `tests/contract/`. Switch with `EXCHANGE_ACTIVE=<name>`.
- **Money is `Decimal`.** Exchange numbers are parsed from strings; floats appear only in scanner analytics.
- **Containers get only the secrets they need** (see `docker-compose.local.yml`).

| Package | Responsibility |
|---|---|
| `config` | Pydantic settings groups, per-pair TOML |
| `domain` | Decimal models, rounding, storage ports |
| `exchanges` | Adapter interfaces, registry, Bybit V5 REST/WS |
| `market_data` | Candle loader, checkpoints, gaps |
| `grid` | Strategies, plan builder, exit policies, `GridTrader` |
| `execution` | `IOrderGateway`, virtual exchange |
| `backtest` | Engine, walk-forward, criteria, reports |
| `scanner` | Pair scoring |
| `paper` | Paper sessions, runner, reporting |
| `notifications` | Telegram notifier and templates |
| `storage` | SQLAlchemy models, Alembic migrations, repositories |
| `workers` | Celery app, tasks, healthchecks |

## Configuration

All settings come from environment variables (`.env`), grouped by prefix:

| Prefix | Purpose |
|---|---|
| `APP_` | environment, log level/format |
| `POSTGRES_`, `REDIS_` | infrastructure |
| `EXCHANGE_` | active exchange; `EXCHANGE_BYBIT_*` key/secret (read-only, optional in Stage 1), fees |
| `GRID_` | default grid type, levels, width, exit policy, min step vs fees |
| `SCANNER_` | volume/spread filters, lookback, top N |
| `RISK_` | total capital ($500) and per-pair cap (30%) |
| `BACKTEST_` | slippage, source timeframe |
| `TELEGRAM_` | bot token and chat id |

See [.env.example](.env.example) for every variable.

## Roadmap

- **Stage 1** — MVP + data: backtest, walk-forward, pair scanner, paper trading, Go/No-Go report (`v0.1.0`).
- **Stage 2** — Production on VPS with CI/CD, live execution, risk manager and kill-switch (`v0.2.0`).
- **Stage 3** — Multi-pair/multi-exchange, pair rotation, admin dashboard with analytics (`v0.3.0`).
