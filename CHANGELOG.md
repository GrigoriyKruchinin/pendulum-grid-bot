# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses [Semantic Versioning](https://semver.org/).

## [1.0.0] - 2026-10-19

First release of Pendulum as a grid strategy analyzer: backtesting, walk-forward optimisation, pair scanning
and paper trading on live data, with read-only exchange access.

### Added
- Bybit V5 market data (REST + WebSocket) behind pluggable exchange adapters with contract tests.
- Resumable 1m candle loader with checkpoints, concurrent fetching, gap detection and periodic sync.
- Arithmetic and geometric grid engine with fee-aware step filtering and exchange minimums.
- Conservative fill simulator shared by backtests and paper trading (`docs/fill-model.md`).
- Backtester with realized/unrealized P&L, intra-bar drawdown and buy & hold benchmark.
- Walk-forward optimisation with chained out-of-sample statistics and a viability check.
- Range-exit policies and a regime filter (rolling efficiency ratio with hysteresis).
- Pair scanner ranking liquid USDT pairs by oscillation vs trend.
- Restart-safe paper trading with stream-gap backfill and a paper-vs-backtest comparison.
- Telegram notifications and summaries.
- Local Docker Compose stack, reference server stack and manual deploy workflow, CI pipeline.
- Research report on the viability of static spot grids (`docs/reports/research-grid-viability-2026-10.md`).

### Changed
- Project scope narrowed to analysis and simulation: configuration focuses on simulated capital
  (`CAPITAL_*`), and settings that only served order execution or a dashboard were removed.

### Notes
- Commit `fe915ad` (`docs: reposition project as a grid strategy analyzer`) also introduced the CI workflow,
  the reference `docker-compose.yml` and `deploy.yml`.
