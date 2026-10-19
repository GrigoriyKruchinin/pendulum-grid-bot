# CLAUDE.md — Pendulum (pendulum-grid-bot)

Pendulum is a **grid strategy analyzer** for crypto spot markets. It does four things:
- loads market data;
- simulates grid strategies with a conservative fill model;
- optimises parameters walk-forward and ranks pairs;
- paper-trades on live data.

Exchange access is read-only. Pendulum never places real orders.

## Session start protocol

1. Read this file, then `todo.md`.
2. Continue with the first unchecked item under **In progress**. If that section is empty, wait for a task
   from the owner.
3. Project state lives in files (`todo.md`, `CHANGELOG.md`, git history, `docs/reports/`), not in
   conversation memory.

## Language policy

- English: code, comments, docstrings, prompts, architecture docs, `README.md`, `todo.md`, `CHANGELOG.md`,
  commit messages.
- Russian: all communication with the owner, and user guides in `docs/guides/`.

## Task workflow (owner request → todo → implement → review)

1. **Capture.** When the owner gives a task or a development plan, write it into `todo.md` under
   **In progress**:
   - break a plan into small, verifiable checklist items (`- [ ]`);
   - add acceptance notes where useful.

   Ideas that are not started go under **Backlog**. Commit the updated `todo.md` (`chore: plan <topic>`).
2. **Implement.** Take the items one at a time with the `implementation-agent`, or work directly for small
   changes:
   - code + tests;
   - `make check` (plus `docker compose ... config` on infra changes).
3. **Review.** Run `/audit` (the `security-quality-critic` agent) on the change. Fix every CRITICAL/HIGH
   finding. Add MEDIUM/LOW findings that you do not fix to **Backlog**.
4. **Close.** Tick the item (`[x]` only for verified work) and make an atomic Conventional Commit that includes
   `todo.md`. Push.
5. **Release.** When a group of items forms a user-visible change:
   - update `CHANGELOG.md` and `README.md` (features, configuration, usage);
   - bump the version in `pyproject.toml` and `src/pendulum/__init__.py`;
   - tag `vX.Y.Z` (semver) and push the tags.
6. **Report.** After a large task is pushed, tell the owner in Russian: "Задача закоммичена, `todo.md`
   актуален. Если контекст разросся, можно сделать `/clear` и написать "продолжай": я продолжу по `todo.md`".

The `/deliver` skill runs steps 1–4 for a request.

## Commands

```bash
make install          # uv sync
make lint             # ruff check . && ruff format --check .
make format           # ruff format . && ruff check --fix .
make typecheck        # mypy --strict src tests
make test             # pytest (unit + property)
make test-integration # Postgres via POSTGRES_* (isolated <db>_test database); `-m live` tests need Bybit access
make check            # lint + typecheck + test
make up / down / ps / logs        # local stack (docker-compose.local.yml)
make data-load | backtest | walk-forward | scan | paper-summary | paper-compare   # CLI via the tools container
docker compose -f docker-compose.local.yml config   # validate compose after infra changes
docker compose -f docker-compose.yml config         # reference server stack (needs POSTGRES_PASSWORD set)
```

## Architecture rules

- Layout: `src/pendulum/{config,domain,exchanges,market_data,grid,execution,backtest,scanner,paper,notifications,storage,workers,cli}`;
  tests in `tests/{unit,property,integration,contract}`.
- **SOLID.**
  - Use narrow interfaces: `IRestMarketData`, `IWebSocketTicker`, `IAccountData`, `IOrderGateway`,
    `IGridStrategy`, `IRangeExitPolicy`, `IRegimeFilter`, storage ports in `domain/ports.py`.
  - Exchanges implement `BaseExchangeAdapter`, register in `ExchangeRegistry` and pass `tests/contract/`.
- **One grid logic.** `GridTrader` runs on `IOrderGateway`; backtests and paper trading share
  `VirtualExchange`.
- **Money is `Decimal` only.** No `float` for prices, quantities, fees or P&L.
  - Respect `tickSize`, `stepSize`, min notional and min qty.
  - Round buy prices down, sell prices up, quantities down.
  - Floats are allowed only in scanner and regime analytics.
- **P&L reporting.** P&L = realized (closed cycles) + unrealized (inventory marked to market). Always report
  fees, slippage and the comparison with buy & hold.
- **Grid steps.** A step must exceed round-trip fees with margin, otherwise the level is skipped.
- **Fill model.** Stay conservative: a limit order fills only when price crosses the level, and a newly placed
  order is not eligible inside the same bar. Document every change in `docs/fill-model.md`.
- **No look-ahead.** Never tune parameters on the history they are scored on: use walk-forward only, rank by
  out-of-sample results, and warm filters up on strictly past data.
- **Settings.** Use Pydantic groups per env prefix (`APP_`, `POSTGRES_`, `REDIS_`, `EXCHANGE_`, `GRID_`,
  `SCANNER_`, `CAPITAL_`, `BACKTEST_`, `TELEGRAM_`). Each container gets only the variables it needs.
- **Performance.** No pandas/numpy in grid or execution code. Long simulations use the `forkserver` process
  pool; keep memory per worker small.

## Code style

- ruff (line length 110), mypy strict, full type hints. No `Any` without a comment explaining why.
- Short Google-style docstrings on public classes and functions. Comments explain *why*, not *what*.
- Async I/O for network and DB; no blocking calls in event loops.
- Logs are structured and never contain keys, secrets or private data.
- Tests:
  - unit tests for logic, hypothesis property tests for grid and money math;
  - `@pytest.mark.integration` for DB tests, `@pytest.mark.live` for real-exchange tests.

## Git workflow

- `main` is stable. Work in `feat/<slug>`, `fix/<slug>` or `chore/<slug>` branches and merge into `main` after
  `make check` and `/audit` pass.
- Conventional Commits in English (`feat(scope): ...`, `fix:`, `refactor:`, `test:`, `docs:`, `chore:`,
  `perf:`, `ci:`). One commit is one finished item.
- Before every commit, check `git diff --staged` for secrets. Never commit `.env`, keys, DB dumps or large data.
- Forbidden without the owner's explicit permission: force-pushing to `main`, rewriting history, deleting
  branches.
- If a push fails: make one fix attempt (`git pull --rebase`, check the remote), then report to the owner in
  Russian.

## Context compaction

When compacting, always keep:
- the current item from `todo.md`;
- the changed files;
- the check commands (`make check`);
- the decisions made;
- open questions for the owner.

Delegate heavy research (exchange API docs, large dumps, wide code search) to a subagent that returns a
short summary. Never print large logs, candles or tables into the chat: write them to a file and show a
summary and the path.

## Safety rules

- Secrets live only in `.env` (gitignored, `chmod 600`); `.env.example` holds placeholders.
- Exchange keys are optional and read-only (they are used for the account's fee tier).
- Pendulum does not place real orders. A feature that would trade real funds is a scope change: only on the
  owner's explicit request, and it must pass `/audit` with a dedicated risk review.
- Every report labels results as historical or simulated, never as a forecast or guarantee.
- The deploy workflow is a reference: manual only and disabled unless `DEPLOY_ENABLED=true`.
