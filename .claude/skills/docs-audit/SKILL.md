---
name: docs-audit
description: Find claims in AGENTS.md and docs/ that the code now contradicts. Read-only staleness audit — reports and proposes fixes, applies nothing.
---

# Docs Audit

Documentation drifts; agents keep trusting it. This audit finds claims the code no longer supports. It is **read-only**: report findings and proposed fixes, but change no files unless the user separately asks.

## What to check

Enumerate concrete, checkable claims from `AGENTS.md`, `docs/INVARIANTS.md`, `docs/README.md`, and any other docs/ file the user names, then verify each against the code with Grep/Read:

1. **Commands and flags** — every CLI command/flag mentioned in docs exists in `event_sync/cli.py` (the `COMMANDS` dispatch table and argparse definitions), and vice versa: every real command is documented.
2. **Function, class, and file names** — every backticked identifier (e.g. `event_match_key`, `_apply_row_defaults`, `compute_event_update_plan`) exists where the docs say it does in `event_sync/`.
3. **Environment variables** — names in AGENTS.md match `event_sync/config.py` and `.env.example`, including old-name fallbacks.
4. **Seeded defaults** — Settings seed names and values (`default_capacity` 24, `default_ticket_price` 30, `default_duration_hours` 2, `default_ticket_limit_per_order` 4, …) match `event_sync/notion_store.py` / `event_sync/constants.py`.
5. **Status names** — the lifecycle statuses in docs match the code's status constants and select options.
6. **Test pins** — every "pinned by tests/test_X.py" claim points at a test file that exists and actually covers that behavior (spot-check by test name/content).
7. **Workflow claims** — statements about `.github/workflows/*.yml` (triggers, guards, concurrency) match the YAML.

## Output

A table: `claim | doc location | code evidence | verdict (OK / STALE / UNVERIFIABLE)`, followed by proposed doc fixes for every STALE row. Keep OK rows to one line; expand only problems.

## Cadence

Run after any doc restructure, then roughly monthly, and always before a release.
