---
name: audit
description: Security, simulation-honesty and code-quality review of the current branch, a commit range or a path, delegated to the security-quality-critic agent. Run after each task and before every release tag.
---

1. Determine the scope: by default `git diff main...HEAD` (or the last task's commits on `main`), otherwise
   the range or path given in the arguments.
2. Launch the `security-quality-critic` subagent with the scope and ask it to apply its full checklist.
3. Run a quick secret scan yourself as well:
   `git diff <scope> | grep -nEi "(api[_-]?key|secret|token|password)\s*[:=]\s*['\"]?[A-Za-z0-9]{12,}"`,
   and confirm that `.env` is not tracked (`git ls-files .env` returns nothing).
4. Save the critic's report to `docs/reports/audits/<YYYY-MM-DD>-<topic>.md`.
5. Fix every CRITICAL/HIGH finding before closing the task or tagging a release. Add the remaining findings
   to `todo.md` under **Backlog**.
6. Report to the owner in Russian: the verdict, the number of findings by severity and the report path.
