---
name: run-tests
description: Run the Pendulum pytest suite (unit, property, optionally integration) and summarize results compactly.
---

1. Run `make test` (unit + property). Write full output to a scratch file; show only the summary line and failures.
2. If the change touches the DB, Celery or exchange adapters, also run integration tests
   (`make test-integration`; needs Postgres via `POSTGRES_*`, tests use an isolated `<db>_test` database;
   `-m live` tests also need network access to Bybit).
3. For failures: find the root cause and fix the code (or the test, if the test is wrong — say why). Re-run.
4. Never mark a todo item `[x]` with failing or skipped-for-convenience tests.
5. Report: passed/failed/skipped counts, coverage of touched modules if available, file path of the full log.
