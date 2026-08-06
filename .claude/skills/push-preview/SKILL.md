---
name: push-preview
description: Preview what `push` would change in Wix and summarize it for human review. Runs push --dry-run only — never performs a real push, never touches production, never edits Notion statuses.
---

# Push Preview

Produce a human-reviewable summary of what `python sync_events.py push` would do right now, without mutating anything.

## Hard rules

- **Never omit `--dry-run`.** This skill previews; it does not push.
- **Never use `--production`** (a PreToolUse hook blocks it anyway — see AGENTS.md "Wix safety").
- **Never edit Notion row statuses** to change what the preview shows.

## Steps

1. Confirm the environment targets the dev site: run `python sync_events.py validate` and check it passes. Do not print secret values.
2. Run the preview: `python sync_events.py push --dry-run --log-level INFO`. If the user scoped by month, pass their `-m` values through (e.g. `-m aug sep`).
3. Summarize the output by bucket, in this order:
   - **Creates** — Ready rows that would become new Wix events (title, date, tickets that would be created).
   - **Updates** — Update rows, with the specific field diffs the plan would apply. **Loudly flag any ticket capacity shrink and any price change** — call these out in bold at the top of the summary, since capacity shrinks reduce sellable inventory and price changes affect live listings.
   - **Cancels / Deletes** — rows flipped to Cancel or Delete and which Wix event each matches.
   - **Ticket changes** — new ticket definitions, policy-text patches.
   - **Would-error rows** — rows that would land in Error, with the validation reason.
4. Close with: "This was a dry run against the dev site. A real production push is human-only: run `push --production` yourself or dispatch the push-events.yml workflow (select `push`, type `PUSH`)."

## Reference

Full invariants for what push may and may not do: `docs/INVARIANTS.md` (status lifecycle, guaranteed ticket defaults, capacity-shrink protection).
