---
name: check-code-quality
description: Run ruff lint/format check and mypy --strict for Pendulum and fix reported issues. Use before every commit.
---

1. Run `make lint` (ruff check + ruff format --check). If it fails, run `make format`, then re-run `make lint`.
   Fix remaining issues manually — do not blanket-add `# noqa`.
2. Run `make typecheck` (mypy --strict). Fix types properly; `# type: ignore[code]` only with a justification comment.
3. Grep the diff for forbidden patterns:
   - `float(` or float literals in money/price/qty code under `src/pendulum/{grid,backtest,execution,risk,paper}`;
   - `print(` in `src/`;
   - `import pandas` / `import numpy` outside `backtest/`, `scanner/`, `analytics`.
4. Report a one-paragraph summary: pass/fail per check and what was fixed.
