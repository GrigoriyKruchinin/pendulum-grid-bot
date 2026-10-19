---
name: audit-risks
description: Security and trading-risk audit of the current branch or a given path, delegated to the security-trading-risk-critic agent. Mandatory before every release/tag and before merging stage branches.
---

1. Determine scope: `git diff main...HEAD` (default) or the path given in arguments.
2. Launch the `security-trading-risk-critic` subagent with the scope and ask it to apply its full checklist.
3. Additionally run a quick secret scan yourself:
   `git diff main...HEAD | grep -nEi "(api[_-]?key|secret|token|password)\s*[:=]\s*['\"]?[A-Za-z0-9]{12,}"`
   and confirm `.env` is not tracked (`git ls-files .env` returns nothing).
4. Save the critic's report to `docs/reports/audits/<YYYY-MM-DD>-<scope>.md`.
5. If verdict is `BLOCK` or `FIX BEFORE RELEASE`: add the findings as `- [ ]` items to `todo.md` under the current
   stage and do not tag a release until they are resolved.
6. Report to the owner (Russian): verdict, number of findings by severity, report path.
