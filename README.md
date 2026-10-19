<div align="center">

<img src="docs/assets/logo.jpg" alt="Pendulum logo" width="180">

# Pendulum

**Grid strategy analyzer for crypto spot markets: backtesting, walk-forward optimisation, pair scanning and paper trading.**

Pendulum pulls market data from Bybit (V5 REST + WebSocket; other exchanges plug in as adapters) and runs grid
strategies through a conservative simulator. Results are net of maker/taker fees, slippage and the unrealized
P&L of inventory, and every strategy is benchmarked against buy & hold on unseen data. Results are stored in
PostgreSQL; reports are written as Markdown and sent to Telegram.

![Python](https://img.shields.io/badge/python-3.12-3776AB?logo=python&logoColor=white)
![mypy](https://img.shields.io/badge/mypy-strict-2A6DB2)
![ruff](https://img.shields.io/badge/lint-ruff-D7FF64?logo=ruff&logoColor=black)
![Docker Compose](https://img.shields.io/badge/docker-compose-2496ED?logo=docker&logoColor=white)
![Status](https://img.shields.io/badge/status-v1.0%20analysis%20toolkit-success)

</div>

> ⚠️ All results are historical simulations or paper trades, not a forecast or a guarantee of profit.
> Pendulum never places real orders: exchange access is read-only.

## Why

Grid bots are sold as "passive income from volatility". Whether a grid actually earns money depends on the
pair, the range, the level spacing, the fees and, above all, the market regime. Most grid backtests flatter
the strategy: fills on a mere touch of the level, no fees in the base coin, no mark-to-market of the coins
the grid is left holding, and parameters tuned on the same history they are scored on.

Pendulum answers the question honestly:

- **Would this grid have made money after all costs, including the coins left in the bag?**
- **Does it beat simply holding the coin?**
- **Do parameters chosen on the past still work on the next, unseen period?**
- **Which pairs currently behave like a range, and which are trending?**
- **Does the simulator agree with live market data?** (paper trading compared with a backtest)

## Features

| Area | What you get |
|---|---|
| Market data | Resumable 1m candle loader with checkpoints, concurrent fetching, gap detection and backfill; incremental sync every 5 minutes |
| Grid engine | Arithmetic and geometric grids; levels aligned to `tickSize`, quantities to `stepSize`; minimum order checks; steps that cannot cover round-trip fees are dropped automatically |
| Fill simulator | Limit orders fill only when price **crosses** the level; no round trip within one bar; maker/taker fees (charged in the received asset, as on Bybit); slippage on market orders. See [docs/fill-model.md](docs/fill-model.md) |
| Backtester | Realized and unrealized P&L, annualized return, max drawdown (intra-bar), cycles per day, fee share, time in range, range exits, buy & hold comparison; results stored with parameters and git SHA |
| Walk-forward | Parameters selected on a train window, scored only on the next unseen window; chained out-of-sample equity and drawdown; parallel workers |
| Exit policies | `stop_loss`, `wait`, `rebuild`, `trailing_up` and combinations (`stop_loss+trailing_up`) |
| Regime filter | Rolling efficiency ratio with hysteresis (`er72`, `er168`): the grid moves to cash during trends and re-opens when the range returns |
| Viability check | Strict multi-criteria verdict: out-of-sample consistency, return vs buy & hold, drawdown, concentration across windows and pairs |
| Pair scanner | Ranks liquid USDT pairs by oscillation vs trend (efficiency ratio, drift, range vs fees, tick granularity); excludes high-risk listings |
| Paper trading | The same grid logic on live WebSocket trades with virtual orders; survives restarts, backfills stream gaps, compares itself with a backtest of the same period |
| Telegram | HTML alerts: session start, range exits, stops, daily and weekly summaries |
| Engineering | Decimal money math, mypy strict, ruff, 110+ tests (unit, property-based, integration), Docker Compose with per-service secrets and healthchecks, CI |

## Quick start

Requirements: Docker with Compose v2 and `make`. For development, also [uv](https://docs.astral.sh/uv/).

```bash
cp .env.example .env && chmod 600 .env   # set POSTGRES_PASSWORD; Bybit key and Telegram are optional
make up                                  # build, migrate, start collector, workers, scheduler, paper trader
make data-load ARGS="--pairs BTC/USDT,SOL/USDT --since 2025-10-01"
make backtest ARGS="--pair SOL/USDT --since 2026-01-01 --levels 20 --width 16"
make walk-forward ARGS="--pairs BTC/USDT,SOL/USDT --since 2025-10-01"
make scan
make paper-summary
```

A step-by-step guide (in Russian) is in [docs/guides/user-guide.md](docs/guides/user-guide.md).

## Usage

### Commands

| Command | Purpose |
|---|---|
| `make up` / `make down` / `make ps` / `make logs` | Start, stop and inspect the local stack |
| `make data-load ARGS="--pairs A/USDT,B/USDT --since YYYY-MM-DD [--until ...] [--tf 1m] [--refetch-gaps]"` | Load or resume historical candles and print a data-quality summary |
| `make backtest ARGS="--pair A/USDT --since ... [options]"` | Backtest one parameter set and write a report |
| `make walk-forward ARGS="--pairs ... --since ... [--grid default\|regime\|baseline\|quick] [--train-days 60] [--test-days 14] [--workers N]"` | Walk-forward optimisation and viability check |
| `make scan` | Rank pairs for grid suitability (stored in the database) |
| `make paper-summary [ARGS="--days 7"]`, `make paper-compare`, `make paper-logs` | Paper trading results, comparison with a backtest, live logs |

Backtest options: `--levels`, `--width` (total range width, %), `--grid-type geometric|arithmetic`,
`--exit stop_loss|wait|rebuild|trailing_up|<down>+<up>`, `--regime none|er72|er168`, `--capital`, `--tf 1m|5m`.

Parameter spaces for walk-forward:

| `--grid` | Sets | Content |
|---|---:|---|
| `default` | 96 | 10/20/30/40 levels × 6/10/16/24% width × 2 grid types × 3 exit policies |
| `regime` | 144 | 10/20 levels × 4 widths × 2 types × 3 exits × regime filter `none`/`er72`/`er168` |
| `baseline` | 48 | `regime` without filters (ablation) |
| `quick` | 12 | small geometric space for exploration |

Reports are written to `docs/reports/backtests/`, `docs/reports/walk-forward/` and `docs/reports/paper/`
(git-ignored). All runs are also stored in PostgreSQL (`backtest_runs`, `trading_sessions`, `cycles`,
`equity_points`, `scanner_scores`).

### Reading the results

- **Net / annualized:** after all fees and slippage, with remaining inventory marked to market.
- **Realized vs unrealized:** cycle profit vs the P&L of coins still held. A large negative unrealized value
  means the grid accumulated a bag in a falling market.
- **Buy & hold:** the same entry costs, held over the same period.
- **Walk-forward:** only out-of-sample windows count. A large gap between in-sample and out-of-sample returns
  means the parameters were overfitted.
- **Viability check:** passes only if all criteria hold. Defaults:

  | Criterion | Default |
  |---|---|
  | Net-positive out-of-sample windows | ≥ 60% |
  | Mean out-of-sample annualized return | ≥ 15% and above buy & hold |
  | Max out-of-sample drawdown (chained) | ≤ 20% |
  | Concentration | top-2 windows ≤ 50% of positive return; ≥ 3 profitable pairs |
  | Paper vs backtest divergence (≥ 14 days) | ≤ 20% |

  Thresholds live in `ViabilityCriteria` (`src/pendulum/backtest/criteria.py`).

## Configuration

Settings come from environment variables (`.env`), grouped by prefix. Every container receives only the
groups it needs.

| Variable | Default | Meaning |
|---|---|---|
| `APP_ENV` | `local` | `local`, `production` or `test` |
| `APP_LOG_LEVEL` / `APP_LOG_JSON` | `INFO` / `true` | Log level and JSON output (secrets are always redacted) |
| `POSTGRES_HOST` / `_PORT` / `_DB` / `_USER` / `_PASSWORD` | `postgres` / `5432` / `pendulum` / `pendulum` / — | Database |
| `REDIS_URL` | `redis://redis:6379/0` | Celery broker |
| `EXCHANGE_ACTIVE` | `bybit` | Exchange adapter from the registry |
| `EXCHANGE_BYBIT_API_KEY` / `_API_SECRET` | empty | Optional **read-only** key, used only for your real fee tier |
| `EXCHANGE_BYBIT_MAKER_FEE` / `_TAKER_FEE` | `0.001` / `0.001` | Fees used when no key is set |
| `EXCHANGE_BYBIT_TESTNET` | `false` | Use Bybit testnet endpoints |
| `GRID_DEFAULT_TYPE` | `geometric` | `geometric` (equal %) or `arithmetic` (equal price step) |
| `GRID_DEFAULT_LEVELS` / `GRID_DEFAULT_WIDTH_PCT` | `20` / `16` | Default grid for paper trading |
| `GRID_DEFAULT_EXIT_POLICY` | `stop_loss+trailing_up` | Default range-exit policy |
| `GRID_MIN_STEP_FEE_MULTIPLIER` | `1.5` | A step must exceed round-trip maker fees × this |
| `GRID_QUOTE_ASSET` / `GRID_PAIRS_FILE` | `USDT` / `config/pairs.toml` | Quote asset; per-pair overrides file |
| `SCANNER_MIN_24H_VOLUME_USD` / `SCANNER_MAX_SPREAD_BPS` | `5000000` / `10` | Scanner liquidity filters |
| `SCANNER_LOOKBACK_DAYS` / `SCANNER_TOP_N` | `30` / `15` | Scanner history and ranking size |
| `CAPITAL_TOTAL_USD` / `CAPITAL_PAIR_PCT` | `500` / `30` | Simulated capital; default per-pair capital = total × pct |
| `BACKTEST_SLIPPAGE_BPS` | `5` | Slippage on market orders |
| `BACKTEST_DEFAULT_TIMEFRAME` | `1m` | Stored candle timeframe used as simulation source |
| `TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID` | empty | Optional Telegram notifications |

Per-pair overrides for paper trading live in [config/pairs.toml](config/pairs.toml):

```toml
[[pairs]]
symbol = "SOL/USDT"
enabled = true
grid_type = "arithmetic"   # optional
levels = 30                # optional
range_width_pct = 12       # optional
capital_usd = 200          # optional
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
   │ Backtester / Walk-forward  │          │ GridTrader (grid logic)  │◄── IGridStrategy, IRangeExitPolicy,
   │  run_backtest(candles)     ├─────────►│   uses IOrderGateway     │    IRegimeFilter
   └────────────────────────────┘          └────────────┬─────────────┘
                                                        │
                              VirtualOrderGateway + VirtualExchange (shared by backtests and paper)
```

- **One grid logic for every executor.** `GridTrader` talks only to `IOrderGateway`, so backtests and paper
  trading run the same code.
- **Exchanges are plugins.** To add one:
  1. Implement `BaseExchangeAdapter` in `pendulum/exchanges/<name>/`.
  2. Add an `EXCHANGE_<NAME>_*` settings class.
  3. Register a factory in `exchanges/registry.py`.
  4. Pass the contract tests in `tests/contract/`.

  Switch exchanges with `EXCHANGE_ACTIVE=<name>`.
- **Money is `Decimal`.** Exchange numbers are parsed from strings. Floats appear only in scanner and
  regime analytics.

| Package | Responsibility |
|---|---|
| `config` | Pydantic settings groups, per-pair TOML |
| `domain` | Decimal models, rounding, storage ports |
| `exchanges` | Adapter interfaces, registry, Bybit V5 REST/WS |
| `market_data` | Candle loader, checkpoints, gaps |
| `grid` | Strategies, plan builder, exit policies, regime filters, `GridTrader` |
| `execution` | `IOrderGateway`, virtual exchange |
| `backtest` | Engine, walk-forward, viability criteria, reports |
| `scanner` | Pair scoring |
| `paper` | Paper sessions, runner, reporting |
| `notifications` | Telegram notifier and templates |
| `storage` | SQLAlchemy models, Alembic migrations, repositories |
| `workers` | Celery app, tasks, healthchecks |

## Research findings (October 2026)

Pendulum was used to test static spot grids on 10 liquid Bybit USDT pairs (BTC, ETH, SOL, XRP, DOGE,
LINK, LTC, AAVE, SUI, ENA) with $150 per pair:

- **12 months, 96 parameter sets, 210 out-of-sample windows.**
  - 26% of windows were net-positive.
  - Mean out-of-sample annualized return was −30%, versus −14% for buy & hold.
  - Max drawdown was 64%.
  - 1 of 10 pairs was profitable.
  - Cycles were profitable after fees, but inventory losses in falling markets were 2–8× larger.
- **3 years with a trend filter** (BTC, ETH, SOL completed).
  - The filter lifted the average out-of-sample result per window from about −1…−2% to about 0%.
  - The grid still lost money and lagged buy & hold strongly in rising markets (BTC +78%).
- **Result:** no configuration passed the viability check. The full methodology and per-pair tables are in
  [docs/reports/research-grid-viability-2026-10.md](docs/reports/research-grid-viability-2026-10.md).

## Development

```bash
make install            # uv sync
make check              # ruff + mypy --strict + pytest (unit and property tests)
make test-integration   # Postgres via POSTGRES_* (uses a separate <db>_test database) + Bybit access
make format             # auto-format and fix lint
```

CI ([.github/workflows/ci.yml](.github/workflows/ci.yml)) runs on every push and pull request:
- lint, type checks and unit tests;
- integration tests against Postgres, with migrations checked against the models;
- validation of both compose files and a Docker image build.

## Deployment (reference)

The analyzer can run unattended on a small server (2 vCPU / 4 GB), collecting data and paper trading 24/7:

- [docker-compose.yml](docker-compose.yml) contains the same services as the local stack, using a prebuilt
  image from GHCR, with memory limits, log rotation and `restart: always`;
- [.github/workflows/deploy.yml](.github/workflows/deploy.yml) is an example SSH deployment: build and
  publish the image, then pull it, run migrations as a one-shot container, start the stack and wait for every
  healthcheck. To roll back, run it with an older image tag.

The deploy workflow runs only on manual dispatch, and only when the repository variable
`DEPLOY_ENABLED=true` and the secrets `DEPLOY_HOST`, `DEPLOY_USER` and `DEPLOY_SSH_KEY` are set. The
server keeps its own `.env`. **No deployment has been performed for this project;** the files are statically
validated in CI only.
