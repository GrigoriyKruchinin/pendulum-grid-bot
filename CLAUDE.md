# CLAUDE.md — Pendulum (pendulum-grid-bot)

Spot-market **grid trading bot**: places a ladder of limit buy/sell orders inside a price range and earns on
oscillations in sideways markets. Stage 1 is read-only (backtest + paper trading); real money only after an
explicit Go decision by the owner.

## Session start protocol

1. Read this file, then `todo.md`. Find the first unchecked task of the current stage and continue from it.
2. Project state lives in files (`todo.md`, `CLAUDE.md`, git history, `docs/reports/`), not in conversation memory.
3. Never start the next stage without the owner's explicit confirmation. No Python code until the owner says
   **"Start Implementation"** (Stage 0 gate).

## Language policy

- Code, comments, docstrings, prompts, architecture docs, `README.md`, `todo.md`, commit messages: **English**.
- All communication with the owner (chat, questions) and user guides in `docs/guides/`: **Russian**.
- User guides are never pasted into chat. Chat gets one short line with the file path.

## Agreed decisions (Stage 0, 2026-10-19)

| Topic | Decision |
|---|---|
| Exchange | **Bybit** (V5 API) first. Multi-exchange is a core requirement: adding an exchange = new adapter + registry entry; switching = config only (`EXCHANGE_ACTIVE`). |
| Pairs | Liquid top + mid-cap alts quoted in **USDT**. Pair Scanner filters by min 24h volume (default ≥ $5M), spread, tick-size granularity. |
| Capital (Stage 2) | **$500** total, max **30%** per pair; start with ~$100 on one pair. |
| Default grid | **Geometric** (equal % step), overridable per pair. |
| Go/No-Go | ≥ 60% OOS windows net-positive; mean OOS annualized ≥ 15% and > buy & hold; max DD ≤ 20%; paper ≥ 14 days, divergence vs backtest ≤ 20%; not carried by 1–2 lucky windows/pairs. |
| VPS budget | ≤ $10/month (2 vCPU / 4 GB). |
| Dashboard auth | `ADMIN_USERNAME` + `ADMIN_PASSWORD_HASH` (bcrypt) from `.env`. |
| Owner location | Argentina, RU passport — keep exchange availability/KYC in mind, keep adapters swappable. |

## Stack

Python 3.12 · `uv` · pydantic v2 + pydantic-settings · PostgreSQL + SQLAlchemy (async) + Alembic · Redis + Celery ·
ccxt (REST base, hidden behind own adapter interfaces) + native WebSocket · pandas/numpy (backtest/analytics only) ·
pytest + pytest-asyncio + hypothesis · ruff · mypy --strict · Docker Compose.

## Commands (Makefile is created in Stage 1)

```bash
make install      # uv sync --all-extras
make lint         # ruff check . && ruff format --check .
make format       # ruff format . && ruff check --fix .
make typecheck    # mypy --strict src tests
make test         # pytest
make check        # lint + typecheck + test
make up / down    # docker compose -f docker-compose.local.yml up -d / down
make migrate      # alembic upgrade head (in migrations container)
make backtest ARGS="..."   # run backtester CLI
docker compose -f docker-compose.local.yml config   # validate compose after infra changes
```

## Architecture rules

- Layout: `src/pendulum/{config,domain,exchanges,market_data,grid,execution,backtest,scanner,paper,risk,notifications,storage,workers,api,cli}`;
  tests in `tests/{unit,property,integration}`.
- SOLID. Narrow interfaces: `IRestMarketData`, `IWebSocketTicker`, `IOrderGateway`, `IGridStrategy`,
  `IRangeExitPolicy`, `IRiskRule`. Exchanges implement `BaseExchangeAdapter` and register in `ExchangeRegistry`.
- The same grid logic runs on three executors — simulator (backtest), paper, live — swapped only via `IOrderGateway`.
- **Money = `Decimal` only.** No `float` for prices, quantities, fees, PnL. Respect `tickSize`, `stepSize`,
  min notional, min qty. Rounding: buy prices down, sell prices up, quantities down.
- PnL = realized (closed cycles) + unrealized (inventory marked to market). Always report fees, slippage, vs buy & hold.
- A grid step must exceed round-trip fees with margin, otherwise the level is skipped.
- Fill simulator is conservative: limit fills only when price **crosses** the level; worst-case intra-candle order.
- No parameter tuning on the same history it is evaluated on: walk-forward only, OOS results rank first.
- Settings split into Pydantic groups (`AppSettings`, `DatabaseSettings`, `RedisSettings`, `ExchangeSettings`,
  `GridSettings`, `RiskSettings`, `TelegramSettings`, `BacktestSettings`, `AdminSettings`). Each container gets only
  the env vars it needs; trading keys are visible only to the trading worker.
- pandas/numpy never in the order hot path.

## Code style

- ruff (line length 110), mypy strict, full type hints, no `Any` without a comment explaining why.
- Docstrings on public classes/functions (Google style, short). Comments explain *why*, not *what*.
- Async I/O everywhere for network/DB. No blocking calls in event loops.
- Logs are structured and never contain keys, secrets or private data.
- Tests: unit for logic, hypothesis property tests for grid math and money, integration tests behind a marker.

## Git workflow

- Branches: `main` (stable) ← `stage-1-mvp`, `stage-2-prod`, `stage-3-dashboard`; merge into `main` after all
  stage checks pass. Stage 0 commits go directly to `main`.
- Per task: implement → `make lint && make typecheck && make test` (+ `docker compose config` on infra changes) →
  tick `todo.md` (`[x]` only for verified work) → atomic Conventional Commit (`feat(scope): ...`, `fix:`, `test:`,
  `docs:`, `chore:`, `refactor:`) including `todo.md` → `git push` → next task.
- Before every commit: `git diff --staged` check for secrets. Never commit `.env`, keys, DB dumps, large data.
- Forbidden without explicit permission: force-push to `main`, history rewrites, branch deletion.
- Push failure: one fix attempt (`git pull --rebase`, check remote), then report to the owner in Russian.
- Stage completion: all stage items ticked → `chore: complete stage N` → tag (`v0.1.0` / `v0.2.0` / `v0.3.0`) →
  update README status badge → push commits + tags → short Russian summary → recommend `/clear`.
- After a large task is pushed, tell the owner (Russian): "Задача закоммичена, `todo.md` актуален. Если контекст
  разросся, можно сделать `/clear` и написать "продолжай": я продолжу по `todo.md`".

## User guide protocol

Guides in Russian, in `docs/guides/stage-N-user-guide.md`, created right before stage N (numbered steps, checkboxes,
⚠️ warnings, "what NOT to do", placeholders only). Next stage starts only after the owner confirms the guide is done.

## Post-Stage-1 fork protocol (Go / Iterate / No-Go)

After Stage 1, do **not** proceed automatically. Open `docs/reports/stage-1-go-no-go.md`, discuss results honestly
with the owner in Russian (metrics, risks, data weaknesses, no massaging), and propose one of:

1. **Go** — criteria met: write `docs/guides/stage-2-user-guide.md`, wait for confirmation, start Stage 2, then 3.
2. **Iterate** — borderline: at most 1–2 iterations (hypothesis, pairs/sources, parameters, data period); goals and
   criteria recorded in `todo.md`; then decide Go / No-Go again.
3. **No-Go → analysis toolkit** — only after the owner explicitly confirms "we won't develop it as an income source".
   Reposition as "Grid Strategy Backtesting & Paper-Trading Toolkit": rewrite README (neutral, exact results),
   CLAUDE.md and `.claude/` (Risk Critic → Security & Code Quality Critic, drop live-execution skills and any mention
   of later stages), `todo.md` (drop Stages 2–3, add "Polish & docs"), remove live-execution code and trading env
   vars; optionally (ask the owner) add a deployability showcase (prod compose, `ci.yml`, manual-only `deploy.yml`,
   no real deploy, static validation only, README "Deployment (reference)"); neutral CHANGELOG entry; tag `v1.0.0`.
   The word "pet" never appears in repo files, commits or tags. Without explicit confirmation nothing is deleted.

## Context compaction

When compacting, always keep: current stage and current task from `todo.md`, list of changed files, check commands
(`make lint`, `make typecheck`, `make test`), decisions made, open questions to the owner.

- Heavy research (exchange API docs, large dumps, wide code search) → subagent returning a short summary.
- Never print large logs/candles/tables into chat: write to a file, show summary + path.

## Safety rules (always)

- Secrets only in `.env` (gitignored); `.env.example` has placeholders.
- Stage 1 keys: read-only. Live keys: spot trading only, **no withdrawal**, IP-whitelisted.
- Any real-money action only after explicit owner confirmation of Stage 2.
- Before each release: `make lint`, `make typecheck`, `make test`, `/audit-risks`.
- All reports label results as historical/simulated, never as a forecast or guarantee.
