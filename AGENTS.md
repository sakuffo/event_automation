# AGENTS.md

Canonical agent context for this repository — read by Cursor natively and imported by CLAUDE.md for Claude Code. Full normative invariants live in [docs/INVARIANTS.md](docs/INVARIANTS.md); the summaries below link into it.

## Project Overview

Event automation tool with a **Notion backend**: events are planned in the Event Scheduling database (scheduled events plus ideas yet to be scheduled), enriched from the Catalog DB of templates (`class` rows *and* recurring `event` rows like jams/parties/shows, distinguished by a `Type` select), and pushed to a Wix Events website via their REST API v3. Two commands drive the flow: `sync` (Wix → Notion refresh: pull pass + enrich pass + Published refresh; **never writes to Wix**) runs via GitHub Actions every 30 minutes — with `--production`, since it only reads the live site — and `push` (Notion → Wix: the only command that mutates Wix events) is an explicit human action, run locally or through the guarded, manual-only `push-events.yml` workflow.

The old **Google Sheets pipeline was deleted** in the Notion-only cleanup — it is recoverable from the git tag `legacy-sheets-final` if ever needed. **Google is not entirely gone**: `GOOGLE_CREDENTIALS` (service account) is still required to download Drive-hosted event images, which are the human-managed image source of truth.

**Wix safety**: only ever target the "Dev Birdhaus Copy" site while testing — `WIX_SITE_ID` in `.env` must stay on the dev site id, never the production one. `WIX_DEV_SITE_ID` declares which site is the dev site; the destructive `scripts/dev` commands (`delete-*`) refuse to run unless `WIX_SITE_ID` matches it. `WIX_PROD_SITE_ID` declares which site is production: every Wix-touching CLI command (`validate`/`test`/`list`/`pull`/`sync`/`push`/`pull-site-config`/`push-site-config`) refuses to run when `WIX_SITE_ID` equals it, and the only way to target production is the explicit `--production` flag, which retargets the run onto `WIX_PROD_SITE_ID` (guard: `cli._enforce_site_guard`, pinned by `tests/test_cli.py`). **The `--production` flag is human-only: AI agents must NEVER run any command with `--production` (nor point `WIX_SITE_ID` at the production id), and must never trigger the production push workflow. If a production run needs testing, ask the user to run it themselves.** The sanctioned workflow uses are the scheduled `sync --production` job (safe because `sync` never writes to Wix) and a human-dispatched `push-events.yml` run. The push workflow defaults to dry-run, requires selecting `push` plus typing `PUSH` for writes, and only runs from `main`; it is never scheduled.

Defense-in-depth: in Claude Code, a PreToolUse hook (`.claude/hooks/block_production.py`, wired in `.claude/settings.json`) hard-blocks any agent shell command containing `--production`. Cursor has no equivalent hook — there, this file's rule plus the code guard (`cli._enforce_site_guard`) are the protection. The code guard remains authoritative in both.

## Commands

```bash
# Setup
make setup                          # Create venv, install deps, create .env template
make install-dev                    # Install production + test dependencies

# One-time Notion bootstrap
python sync_events.py setup-notion   # Create Event Scheduling/Catalog/Settings/Site Config DBs, print IDs for .env (re-run patches schemas)
python sync_events.py import-event-templates  # annotated events-export CSV -> Type=event catalog rows (--dry-run, --force, --csv PATH)
python sync_events.py pull           # Wix -> Event Scheduling DB backfill (--scope all for past events)

# Core workflow
python sync_events.py enrich         # Fill blanks on Idea/Draft rows (Idea -> Draft); -m aug sep to filter months; optional — sync runs it too
python sync_events.py sync           # Wix -> Notion refresh, NEVER writes to Wix: pull pass (upcoming scope), enrich pass, Published refresh; Ready/Update/Cancel/Delete rows are only reported as waiting for push; --no-pull, --no-enrich, --dry-run, -m. Scheduled every 30 min in CI.
python sync_events.py push --dry-run # Preview what would change in Wix
python sync_events.py push           # Notion -> Wix (the ONLY Wix-mutating command): create Ready rows, patch Update rows, run Cancel/Delete flips; --draft, --no-tickets, --dry-run, -m. Explicit human action (local or manual push workflow), never scheduled.
python sync_events.py pull-site-config / push-site-config  # Tax-by-location via Notion Site Config DB (push supports --dry-run)

# Other
python sync_events.py validate       # Check Wix + Notion (+ Google Drive) credentials
python sync_events.py test           # Test Wix API connection
python sync_events.py list           # List existing Wix events

# All subcommands accept --log-level DEBUG|INFO|WARNING|ERROR|CRITICAL

# Quality gates (run both before committing)
make lint                            # ruff check .
make unit                            # or: pytest — confined to tests/ via pyproject.toml;
                                     # manual dev scripts in scripts/dev/ are never collected
pytest tests/test_notion_store.py -v # Notion mapping + hash tests (mocked client)
pytest tests/test_sync_loop.py -v    # Sync-loop status-lifecycle characterization tests
```

## Architecture

**Entrypoint**: `sync_events.py` → thin wrapper that delegates to `event_sync.cli.main()`.

**`event_sync/` package**:

- `cli.py` — argparse CLI; a `COMMANDS` dispatch table with per-command lazy imports and per-command config validation
- `config.py` — `AppConfig` dataclass from `.env`; `ensure_notion_valid()` / `ensure_wix_valid()`
- `runtime.py` — `SyncRuntime` lazily holds `WixClient`, `NotionStore`, and the Google Drive client (the Google SDK import lives inside `get_drive_service` so Notion-only commands never pay it); image download/upload caches; `last_image_failure`/`last_ticket_failure` bookkeeping
- `notion_store.py` — **all Notion I/O**: database schemas, property builders/parsers, page↔row↔`EventRecord` mapping, paginated queries, write-backs. Pinned to Notion API `2025-09-03` (database id → data-source id resolved once and cached). Rich text chunked at 2000 chars.
- `notion_orchestrator.py` — the pipeline flows: `setup_notion`, `import_event_templates`, `pull_events`, `enrich_events`, `notion_sync_events` (Wix-read-only refresh), `notion_push_events` (the only Wix-mutating flow), `pull_site_config_notion`, `push_site_config_notion`. Both event loops share `_run_status_loop`; the row dispatchers are `_refresh_row` (sync) and `_push_row` (push). Composes `notion_store` with `wix_mapping`/`wix_flows`.
- `wix_mapping.py` — **pure converters, no I/O**: `wix_timestamp`/`localize_wix_start`/`normalize_wix_timestamp`, `format_description_as_html`, `build_wix_event_payload`, `diff_event_fields`, `wix_event_to_config_row` (Wix event → row dict; the read side of "Wix is authoritative"), `event_match_key`/`wix_event_match_key` (the single owners of the `title|date|time` fallback key), `select_schedule_wix_event_ids` (Wix current-status + recurring/Tinker window policy), `parse_month_value`, site-config row builders
- `wix_flows.py` — **Wix mutations**: `create_wix_event`, `update_wix_event`, `compute_event_update_plan`/`apply_event_update_plan`, ticket helpers (`ensure_ticket_definition`, `create_tickets_from_config`), category helpers, `index_events_by_id_and_key`, `process_site_config_rows`, plus `validate_credentials`/`test_wix_connection`/`list_wix_events`
- `notion_dashboard.py` — the generated, display-only Events Dashboard page: `build_dashboard_blocks` (pure block builder) + `refresh_dashboard` (rewrites the page at the end of every non-dry `sync`; page id lives in the auto-managed `dashboard_page_id` Setting)
- `models.py` — Pydantic `EventRecord` (+ `content_hash()` for change detection, bookkeeping fields `notion_page_id`/`wix_event_id`/`status`/`synced_hash`/`hidden_from_schedule`, pull-only sales fields `tickets_sold`/`tickets_sold_by_type`/`revenue`); `TicketSpec`/`parse_tickets` for `;`-separated multi-ticket fields
- `images.py` — image download (Google Drive API or plain HTTP for wixstatic URLs), Pillow resize, Wix Media upload
- `wix_client.py` — Wix API client with retry/backoff — events CRUD, ticket definitions, categories, eCommerce tax (`billing/v1`), media upload
- `constants.py` — pricing table (`CATEGORY_PRICING`), default location/capacity/tax, tax-rate conversions

**`scripts/`** — operational one-offs (`diag_hashes.py`, `set_event_status.py`, `export_events_csv.py`, `create_test_idea_row.py`, `apply_ticket_policy.py`, `archive_recurring_rows.py`, `migrate_capacity_columns.py`); **`scripts/dev/`** — manual Wix dev tools (`dev_events.py`, `dev_tickets.py`, `manual_*_check.py`, `inspect_tickets.py`) that hit the live (dev) site and are deliberately outside pytest's `testpaths`.

## Invariants (summaries — full normative text in docs/INVARIANTS.md)

- **Status lifecycle**: rows move `Idea → Draft → Ready → Published` (plus `Update`, `Error`, `Skip`, and the `Cancel`/`Delete` actions). `enrich` fills only empty fields and promotes Idea→Draft; humans flip Draft→Ready; only `push` mutates Wix; `sync` never writes to Wix and reports Ready/Update/Cancel/Delete as `pending_push`. → [full text](docs/INVARIANTS.md#status-lifecycle) *(tests/test_sync_loop.py)*
- **Sync direction**: once Published, **Wix is authoritative** — sync refreshes Notion from the live event. Local edits go out only via an explicit Update flip + `push`, which always diffs (no hash fast-path). → [full text](docs/INVARIANTS.md#sync-direction-by-status) *(tests/test_sync_direction.py)*
- **Tickets & capacity**: there is **no event-level Capacity column** — per-ticket inventory lives solely in the semicolon `Ticket Capacities` column. Every ticketed row is guaranteed creatable tickets (Template → Settings → constants); blank/invalid capacity tokens never touch live inventory, so an Update can never silently shrink it; unlimited tickets are dashboard-only; an explicit `0` price makes a free ticket, a blank price makes none. → [full text](docs/INVARIANTS.md#guaranteed-ticket-defaults-template--settings--constants) *(tests/test_ticket_capacity.py, tests/test_defaults.py)*
- **Hash change detection**: `content_hash()` → `Synced Hash` short-circuits Published refreshes. Canonicalizes formatting (`35.0`≡`35`); empty semicolon tokens are positional (`20; ; 4` ≢ `20; 4`) — never collapse them; bookkeeping fields are never hashed. → [full text](docs/INVARIANTS.md#hash-based-change-detection) *(tests/test_notion_store.py)*
- **Sales columns are pull-only**: `Tickets Sold` / `Tickets Sold By Type` / `Revenue` are code-owned, written only by sync/pull from live Wix sales data (sold counts from ticket definitions' `salesDetails`, revenue from the read-only orders-summary endpoint) — never hashed, never in any Wix payload, so they can never cause a Wix write; a failed sales read leaves existing values alone. `sync` also rewrites the generated, display-only **Events Dashboard** page (`notion_dashboard.py`; page id in the auto-managed `dashboard_page_id` Setting). → [full text](docs/INVARIANTS.md#sales-columns-are-pull-only-tickets-sold--tickets-sold-by-type--revenue) *(tests/test_sales_dashboard.py)*
- **Matching**: Wix Event ID first, fallback `(title, start_date, start_time)` — key owned by `wix_mapping.event_match_key`; Ready-matches-draft publishes, Ready-matches-live updates/links, never duplicates. → [full text](docs/INVARIANTS.md#matching) *(tests/test_sync_loop.py)*
- **Pull is non-destructive**: only code-owned `Published`/`Cancelled` rows are written; human-status rows are linked but never overwritten. → [full text](docs/INVARIANTS.md#pull-is-non-destructive)
- **Cancel/Delete run before validation** — an incomplete row can still be cancelled/deleted. → [full text](docs/INVARIANTS.md#canceldelete-actions)
- **Schedule visibility & recurring events**: `Hidden from Schedule` retains ended/cancelled rows as history while removing them from operational views/dashboard Upcoming. Ordinary recurring series keep one `RECURRING_UPCOMING` row; exact-title Tinker Tuesday overrides that rule with the earliest four Wix `UPCOMING`/`STARTED` occurrences. Push matching remains unfiltered. → [full text](docs/INVARIANTS.md#schedule-visibility-and-recurring-series)
- **Image preservation**: human-entered (non-wixstatic) Image URLs are never blanked by sync/pull; failed uploads still create the event and note the failure in `Sync Error`. → [full text](docs/INVARIANTS.md#image-preservation)
- **Timezones**: Notion dates are naive local + `time_zone: America/Toronto`; reads convert back via `zoneinfo` (`tzdata` on Windows). → [full text](docs/INVARIANTS.md#timezones)
- Template types, Settings defaults, ticket policy blurb & status column, Checkout Form, Notion property conventions, site-config round-trip: see [docs/INVARIANTS.md](docs/INVARIANTS.md).

## Working Agreements

- **Plan first** for any change touching 3+ files or any sync/push loop behavior: present the plan and get approval before editing.
- **Definition of done**: a behavior change ships with its matching test change **and** its doc update (summary here, full text in `docs/INVARIANTS.md`) in the same commit.
- **Commit messages**: imperative mood, name the behavior ("Guard capacity shrink in update plan") — never "sync 30" / "notion fix"-style stubs.
- **Checkpoint before agent runs**: commit or stash first; multi-file agent work goes on a branch, not straight onto `main`.
- **Run `make lint` and `make unit` before committing.** Both must be green; CI enforces the same pair.
- Never commit `.env` or CSV exports; never force-push `main`.

## Environment Variables

Required in `.env`: `WIX_API_KEY`, `WIX_SITE_ID` (dev site!), `NOTION_ACCESS_TOKEN` (old name `NOTION_TOKEN` still accepted), the four DB ids from `setup-notion` — `NOTION_EVENT_SCHEDULING_DB_ID` (old name `NOTION_EVENTS_DB_ID` still accepted), `NOTION_CATALOG_DB_ID` (old name `NOTION_CLASSES_DB_ID` still accepted), `NOTION_SETTINGS_DB_ID`, `NOTION_SITE_CONFIG_DB_ID` — and `GOOGLE_CREDENTIALS` (Drive images). Optional: `WIX_ACCOUNT_ID` (Site Media), `NOTION_PARENT_PAGE_ID` (setup only), `WIX_DEV_SITE_ID` (enables the destructive dev-script guard), `WIX_PROD_SITE_ID` (declares the production site for the `--production` guard; human-only flag).

## Tests

`pyproject.toml` confines pytest to `tests/`. `tests/test_sync_loop.py` pins the status-lifecycle invariants (sync never mutates Wix and reports pending pushes, push's Cancel/Delete act before validation, Ready-matches-draft publishes, dry-run writes nothing, pull links-but-never-overwrites human rows, ordinary one-row recurring behavior plus the four-event Tinker window, image preservation); `tests/test_notion_store.py` pins property round-trips and hash stability; `tests/test_defaults.py` pins the enrich fill rules; `tests/test_cli.py` pins the production-site guard for both `sync` and `push`. When refactoring the sync/push loops, change behavior only with a matching test change.
