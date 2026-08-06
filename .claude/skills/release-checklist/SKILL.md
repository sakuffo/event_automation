---
name: release-checklist
description: Pre-release go/no-go gate for this repo. Read-only apart from running tests and lint; prints the human-only tag/dispatch commands rather than running them.
---

# Release Checklist

Run every gate, report each as PASS/FAIL with evidence, and end with a single go/no-go verdict. Fix nothing during the run — a failed gate is a finding, not a task.

## Gates

1. **Clean, synced main** — `git status --short` is empty; `git fetch` then confirm `git rev-parse HEAD` == `git rev-parse origin/main`; current branch is `main`.
2. **Tests** — `pytest -q` passes in full. Report the count.
3. **Lint** — `ruff check .` clean.
4. **Production guard** — `pytest tests/test_cli.py -q` passes (pins `_enforce_site_guard` for both `sync` and `push`).
5. **Workflows match origin** — `git diff origin/main -- .github/workflows/` is empty (no un-pushed workflow edits).
6. **Config surface** — every env var read by `event_sync/config.py` appears in `.env.example`, and AGENTS.md's Environment Variables section lists the same set.
7. **Safety docs spot-check** — AGENTS.md "Wix safety" paragraph still names `cli._enforce_site_guard`, the `--production` human-only rule, and the push workflow's dry-run default; confirm `.claude/hooks/block_production.py` exists and `.claude/settings.json` wires it.
8. **Docs freshness** — run the docs-audit skill's checks 1–3 (commands, identifiers, env vars) as a quick pass; a STALE finding is a soft fail (flag it, human decides).

## Output

- Per-gate PASS/FAIL table with one line of evidence each.
- Verdict: **GO** (all hard gates pass) or **NO-GO** (list blockers).
- If GO, print — do **not** run — the human-only release commands:
  ```
  git tag -a v1.0.0 -m "1.0.0"
  git push origin v1.0.0
  ```
  and remind: production pushes go through the push-events.yml workflow (human-dispatched, select `push`, type `PUSH`).
