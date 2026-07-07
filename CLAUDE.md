# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Event automation tool with a **Notion backend**: events are planned in a Notion Events database, enriched from the Catalog DB of templates (`class` rows *and* recurring `event` rows like jams/parties/shows, distinguished by a `Type` select; formerly named "Classes"), and pushed to a Wix Events website via their REST API v3. Runs via GitHub Actions daily and manually via CLI. The previous **Google Sheets pipeline is kept side by side** (commands suffixed `-sheet` plus the original `prepare-sheet`/`pull-config`/`push-config`/`pull-categories`/`push-categories`) until the Notion path is proven; do not delete the sheet code paths without an explicit request.

**Wix safety**: only ever target the "Dev Birdhaus Copy" site while testing — `WIX_SITE_ID` in `.env` must stay on the dev site id, never the production one (commented out in `.env`).

## Commands

```bash
# Setup
make setup                          # Create venv, install deps, create .env template
make install-dev                    # Install production + test dependencies

# One-time Notion bootstrap
python sync_events.py setup-notion   # Create Events/Catalog/Settings/Site Config DBs, print IDs for .env (re-run patches schemas)
python sync_events.py import-classes # class_info sheet -> Catalog DB; defaults tab -> Settings DB
python sync_events.py import-event-templates  # annotated events-export CSV -> Type=event catalog rows (--dry-run, --force, --csv PATH)
python sync_events.py pull           # Wix -> Events DB backfill (--scope all for past events)

# Core workflow (Notion)
python sync_events.py enrich         # Fill blanks on Idea/Draft rows (Idea -> Draft); -m aug sep to filter months
python sync_events.py sync --dry-run # Preview what would change in Wix
python sync_events.py sync           # Push Ready rows (create) + changed Published rows (patch); --draft, -m
python sync_events.py pull-site-config / push-site-config  # Tax-by-location via Notion Site Config DB

# Legacy workflow (Google Sheets, unchanged behavior)
python sync_events.py prepare-sheet  # rolling_schedule + class_info -> generated_events tab
python sync_events.py sync-sheet     # generated_events -> Wix (was `sync` pre-migration)
python sync_events.py pull-config / push-config
python sync_events.py pull-categories / push-categories
python sync_events.py pull-site-config-sheet / push-site-config-sheet

# Other
python sync_events.py validate       # Check Wix + Google + Notion credentials
python sync_events.py test           # Test Wix API connection
python sync_events.py list           # List existing Wix events

# All subcommands accept --log-level DEBUG|INFO|WARNING|ERROR|CRITICAL

# Tests
make unit                            # or: pytest
pytest tests/test_notion_store.py -v # Notion mapping + hash tests (mocked client)
```

## Architecture

**Entrypoint**: `sync_events.py` → thin wrapper that delegates to `event_sync.cli.main()`.

`**event_sync/` package** (core logic):

- `cli.py` — argparse CLI; Notion commands + legacy sheet commands; per-command config validation
- `config.py` — `AppConfig` dataclass from `.env`; `ensure_notion_valid()` / `ensure_wix_valid()` for the Notion pipeline, `ensure_valid()` for the legacy one
- `runtime.py` — `SyncRuntime` lazily holds Google Sheets/Drive services, `WixClient`, and `NotionStore`; download/upload caches
- `notion_store.py` — **all Notion I/O**: database schemas, property builders/parsers, page↔row↔`EventRecord` mapping, paginated queries, write-backs. Pinned to Notion API `2025-09-03` (database id → data-source id resolved once and cached). Rich text chunked at 2000 chars.
- `notion_orchestrator.py` — Notion flows: `setup_notion`, `import_classes`, `pull_events`, `enrich_events`, `notion_sync_events`, `pull_site_config_notion`, `push_site_config_notion`. Composes `notion_store` with the Wix helpers in `orchestrator`.
- `orchestrator.py` — Wix logic shared by both backends (`compute_event_update_plan` / `apply_event_update_plan`, `create_wix_event`, ticket/category/tax helpers) plus the legacy sheet flows
- `generator.py` — [legacy] merges `rolling_schedule` + `class_info` tabs; `_wix_event_to_config_row` is reused by the Notion `pull`
- `sheets.py` — [legacy] sheet readers with flexible header mapping
- `models.py` — Pydantic `EventRecord` (+ `content_hash()` for change detection, bookkeeping fields `notion_page_id`/`wix_event_id`/`status`/`synced_hash`); `TicketSpec`/`parse_tickets` for `;`-separated multi-ticket fields
- `images.py` — image download (Google Drive API or plain HTTP for wixstatic URLs), Pillow resize, Wix Media upload
- `constants.py` — pricing table (`CATEGORY_PRICING`), default location/capacity/tax, column mappings

`**wix_client.py`** (standalone): Wix API client with retry/backoff — events CRUD, ticket definitions, categories, eCommerce tax (`billing/v1`), media upload.

## Key Design Patterns

- **Status lifecycle instead of staging tabs**: Events rows move `Idea → Draft → Ready → Published` (plus `Error`, `Skip`). `enrich` fills only *empty* fields (from Class relation/name-match, Settings `default_*` rows with constants fallback, `CATEGORY_PRICING`) and promotes Idea→Draft; humans flip Draft→Ready; `sync` flips Ready→Published and records failures as Error with the reason in `Sync Error`.
- **Catalog template types**: the Catalog DB (title property `Template`; Events link to it via the `Template` relation) has a `Type` select distinguishing `class` templates (blank = `class` for pre-redesign rows) from recurring `event` templates. In `_apply_row_defaults`, only class templates get the `rope`/`class` baseline tags and the guaranteed price (Price Override → `CATEGORY_PRICING` → $30); event templates contribute exactly their own categories and price only from `Price Override` (a $0 override is honored for free events). `setup-notion` patches the Type select into an existing Catalog DB via `ensure_template_type_options` and performs the one-time classes→catalog renames via `migrate_catalog_naming` (DB title `Classes`→`Catalog`, `Class` properties→`Template`); `import-event-templates` seeds event templates from the annotated events-export CSV (latest feed-eligible instance per `default_event` family; Tinker Tuesday skips "Sunday" titles and requires the $25 base price — see `TEMPLATE_SOURCE_RULES`).
- **Cancel/Delete actions**: humans flip a row to `Cancel` (sync calls `WixClient.cancel_event`, row becomes `Cancelled`) or `Delete` (sync calls `delete_event(force=True)`, row becomes `Removed`). These branches run *before* record validation in `notion_sync_events` — an incomplete row can still be cancelled/deleted; matching only needs the Wix Event ID (or title+date+time). Wix drafts can't be cancelled (use Delete); Wix can't un-cancel (duplicate the row without the Wix Event ID to recreate). `pull` maps Wix `CANCELED` events to `Cancelled` rows, and `setup-notion` patches missing Status select options into an existing Events DB via `ensure_event_status_options`.
- **Defaults**: `setup-notion` seeds `default_location`/`default_capacity`/`default_registration_type`/`default_tax_*`/`default_fee_type` rows into the Settings DB (edit there, not in code). The shared `_apply_row_defaults` helper (in `notion_orchestrator.py`) is used by `enrich` on Idea/Draft rows and by `sync` as a safety net on Ready rows (with write-back), so Notion always shows exactly what was pushed. A manual default "New Event" database template gives creation-time defaults in the UI (documented in `docs/NOTION_BACKEND.md`; templates can't be created via API).
- **Hash-based change detection**: after each successful push, `EventRecord.content_hash()` is stored in `Synced Hash`. Published rows re-push only when the hash differs (or a prior error is recorded) — replaces the `*_last_pull` snapshot tabs. The hash canonicalizes formatting (`35.0`≡`35`, None≡"") and hashes `ticket_price_raw` over the derived `ticket_price`.
- **Matching**: rows match Wix by `Wix Event ID` first, falling back to `(title, start_date, start_time)`. A Ready row matching a Wix draft gets *published*; matching a live event gets *updated/linked* — never duplicated.
- **Pull is non-destructive**: `pull` creates/refreshes only `Published` (code-owned) rows; rows in any human status are linked (Wix ID written) but their fields are never overwritten. Wix events too incomplete to validate still land in Notion with a `Sync Error` note (`upsert_event_from_raw_row`).
- **Notion property conventions**: `Registration Type` select shows `TICKETS` (mapped to Wix `TICKETING` by the model validator); multi-ticket fields stay semicolon-separated text (`Ticket Names/Prices/Capacities`); single price lives in the `Ticket Price` number property; long descriptions are chunked/rejoined across 2000-char rich_text segments; select options must not contain commas (`_sanitize_option`).
- **Timezones**: Notion dates are written as naive local datetimes with `time_zone: America/Toronto`; reads convert UTC-offset datetimes back to local via `zoneinfo` (`tzdata` dependency on Windows).
- **Site-config round-trip (tax by location)**: same semantics as the sheet version but rows live in the Site Config DB; only `tax_name`/`tax_type`/`tax_rate` (percent) are editable; push updates/bulk-creates mappings, never deletes; the row-processing core is `orchestrator.process_site_config_rows`, shared by both backends. Requires the eCommerce **Manage Orders** scope.
- **Env-var driven config**: `NOTION_ACCESS_TOKEN` (or `NOTION_TOKEN`), `NOTION_PARENT_PAGE_ID` (setup only), `NOTION_EVENTS_DB_ID`, `NOTION_CATALOG_DB_ID` (old name `NOTION_CLASSES_DB_ID` still accepted as fallback), `NOTION_SETTINGS_DB_ID`, `NOTION_SITE_CONFIG_DB_ID`. `GOOGLE_CREDENTIALS` is still needed for Drive images; `GOOGLE_SHEET_ID`/`SOURCE_SHEET_ID` only for legacy commands + `import-classes`.
- **Views can't be created via API** — calendar/board/filtered views are added by hand in Notion (documented in `docs/NOTION_BACKEND.md`).

## Environment Variables

Required in `.env`: `WIX_API_KEY`, `WIX_SITE_ID` (dev site!), `NOTION_ACCESS_TOKEN`, `NOTION_*_DB_ID` (from `setup-notion`), `GOOGLE_CREDENTIALS` (Drive images). Optional: `WIX_ACCOUNT_ID`, `NOTION_PARENT_PAGE_ID`, `GOOGLE_SHEET_ID`, `SOURCE_SHEET_ID`, legacy tab names, `ENV_MODE=development` + `DEV_WIX_*` for sandbox.
