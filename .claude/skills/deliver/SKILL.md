---
name: deliver
description: Take an owner request (single task or development plan) through the full Pendulum workflow - record it in todo.md, implement each item, review with the critic, commit and push. Arguments - the task or plan text.
---

1. **Capture.**
   - Restate the request in one line.
   - Break it into small, verifiable items (one commit each) with short acceptance notes.
   - Add the items to `todo.md` under **In progress**. Unscheduled ideas go to **Backlog**.
   - Commit: `chore: plan <topic>`.
   - If the request is ambiguous in a way that changes the result, ask the owner (in Russian) before
     implementing.
2. **Branch.** Create `feat/<slug>` (or `fix/<slug>`) from `main`.
3. **Implement.** For each item, run the `implementation-agent` (or work directly for trivial edits) until
   `make check` passes. Tick the item and commit.
4. **Review.** Run `/audit` on `main...HEAD`. Fix CRITICAL/HIGH findings, re-run the checks and commit the
   fixes. Add the remaining findings to **Backlog**.
5. **Merge.** Fast-forward or merge into `main`, then push.
   - For a user-visible change, update `CHANGELOG.md` and `README.md`, bump the version and tag `vX.Y.Z`.
6. **Report** to the owner in Russian:
   - what changed;
   - the check results;
   - the audit verdict;
   - follow-ups left in **Backlog**.
