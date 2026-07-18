# Capacity → Ticket Capacities migration (production runbook)

The event-level **Capacity** column (Event Scheduling) and **Default
Capacity** column (Catalog) are retired. Per-ticket inventory (Wix
`initialLimit`) now lives solely in the semicolon **Ticket Capacities** /
**Default Ticket Capacities** columns: a single value covers every ticket
type, missing tail entries inherit the last one, and enrich guarantees every
ticketed row a value (template → `default_capacity` Setting).

Why: Capacity was never sent to Wix as an event property. Its only jobs
(fallback ticket inventory) moved to Ticket Capacities, which — unlike
Capacity — actually round-trips from Wix. On top of that, the old Published
refresh reset Capacity to 24 on every row (the column was 100% stale data in
practice), and an `Update` flip could silently shrink live ticket inventory
because create and update read different fallbacks. Both bugs are structural
non-issues in the new model.

This exact sequence was executed and verified on the dev Notion +
"Dev Birdhaus Copy" Wix pair on 2026-07-16 (65 templates + 1 row copied;
end-to-end publish produced a ticket with the template's inventory of 80).

## What the script does

`scripts/migrate_capacity_columns.py` — **Notion-only** (never talks to Wix),
dry-run by default, idempotent (re-runs copy nothing and it survives the
columns already being gone):

1. Catalog: copies `Default Capacity` into `Default Ticket Capacities`
   where the target is blank (non-positive values are skipped with a
   warning — the old validator would have rejected them anyway).
2. Event Scheduling: copies `Capacity` into `Ticket Capacities` on
   human-status rows with a blank target. Published/Cancelled/Removed rows
   are skipped — they are code-owned and their Capacity is stale bookkeeping.
3. Settings: rewords the `default_capacity` row's Notes for its new job
   (the value — 24 — stays).
4. `--drop-columns`: deletes the two retired properties from the schemas.

## Runbook

Prereqs: `.env` pointing at the **production** Notion databases
(`NOTION_EVENT_SCHEDULING_DB_ID`, `NOTION_CATALOG_DB_ID`,
`NOTION_SETTINGS_DB_ID`). The Wix variables are irrelevant to the script.

1. **Preview the copy** (writes nothing):

   ```bash
   python scripts/migrate_capacity_columns.py
   ```

   Review the `COPY:` lines — every template with a `Default Capacity`
   should appear once; expect very few event rows (only human-status rows
   that have a Capacity but no Ticket Capacities).

2. **Apply the copy** (safe under the old code too — nothing reads the new
   values until the new code deploys):

   ```bash
   python scripts/migrate_capacity_columns.py --apply
   ```

3. **Deploy the new code** (merge this branch so GitHub Actions runs it).

4. **Expect a one-time full refresh** on the first sync: removing
   `capacity` from the hashed fields changes every row's content hash, so
   each Published row gets rewritten once from Wix and settles. This is the
   same one-time settle the Checkout Form introduction caused.

5. **Drop the retired columns** (only after step 3 — the old code would
   error writing to missing properties):

   ```bash
   python scripts/migrate_capacity_columns.py --apply --drop-columns
   ```

   Or by hand in Notion: Event Scheduling → `Capacity` property → Delete;
   Catalog → `Default Capacity` property → Delete. (A deleted property is
   recoverable from the database's deleted-properties list if needed.)

6. **Manual UI touch-ups** (the API can't reach these):
   - Edit the manual **"New Event" database template**: remove the
     `Capacity: 24` pre-fill (nothing replaces it — enrich guarantees
     Ticket Capacities).
   - Any saved **views** that sort/filter on Capacity.
   - The instructions page's Settings blurb if it mentions "capacity"
     (the Settings row's own Notes were already reworded by step 2).

## Semantics cheat-sheet (after migration)

- `Ticket Capacities: 80` with `Ticket Names: GA; VIP` → both tickets get
  inventory 80. `80; 10` → GA 80, VIP 10.
- Single-ticket rows (Ticket Price, no names) use the **first** value.
- Values must be positive numbers (decimals round to whole tickets).
  Blank/zero/negative/typo entries fall back to the `default_capacity`
  Setting (seeded 24) at ticket-creation time. Unlimited tickets can only
  be made in the Wix dashboard; the pipeline never creates one and never
  touches an existing one.
- Capacities are **managed per entry** on the Update path: only explicit
  positive values (or the single-value-covers-all tail rule) are diffed —
  a blank or invalid entry, like a fully blank column, leaves that live
  ticket's inventory alone (same convention as Ticket Limit Per Order /
  Checkout Form).
- Cannot reduce a ticket's capacity below its sold count via Update (the
  plan blocks it with a warning).
