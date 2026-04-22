# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Event automation tool that syncs events from Google Sheets to a Wix Events website. It reads event data from a source spreadsheet (rolling schedule + class info tabs), merges and enriches it, then creates/updates events in Wix via their REST API v3. Runs via GitHub Actions daily and manually via CLI.

## Commands

```bash
# Setup
make setup              # Create venv, install deps, create .env template
make install-dev        # Install production + test dependencies

# Core workflow
python sync_events.py prepare-sheet           # Step 1: Merge source tabs → generated_events tab
python sync_events.py prepare-sheet -m apr    # Step 1 with month filter
python sync_events.py sync                    # Step 2: Push generated_events to Wix
python sync_events.py sync --draft            # Create as drafts (publish later)
python sync_events.py list                    # List existing Wix events

# Config round-trip (pull from Wix, edit in sheet, push back)
python sync_events.py pull-config             # Pull Wix events → config_events tab
python sync_events.py push-config --dry-run   # Preview changes
python sync_events.py push-config             # Push edits back to Wix

# Categories-only round-trip (only the `categories` column is editable)
python sync_events.py pull-categories                       # default --scope upcoming
python sync_events.py pull-categories --scope all           # past + present + future
python sync_events.py push-categories                       # default --scope upcoming
python sync_events.py push-categories --scope all
python sync_events.py push-categories --scope all --dry-run # preview only

# Other
python sync_events.py validate                # Check credentials
python sync_events.py test                    # Test Wix API connection
python sync_events.py generate                # Output merged CSV to stdout

# All subcommands accept --log-level DEBUG|INFO|WARNING|ERROR|CRITICAL

# Tests
make unit     # or: pytest
pytest tests/test_models.py -v                # Run a single test file
```

## Architecture

**Entrypoint**: `sync_events.py` → thin wrapper that delegates to `event_sync.cli.main()`.

**`event_sync/` package** (core logic):
- `cli.py` — argparse CLI with subcommands; routes to orchestrator/generator functions
- `config.py` — `AppConfig` dataclass populated from `.env` via `python-dotenv`; all settings are env-var driven
- `runtime.py` — `SyncRuntime` holds lazily-initialized Google Sheets/Drive services and `WixClient`; also manages download/upload caches
- `orchestrator.py` — main business logic: `sync_events()` reads from Sheets, diffs against existing Wix events (keyed by title+start_date+start_time), creates/patches/skips as needed; also handles ticket creation, category assignment, image upload
- `generator.py` — merges `rolling_schedule` + `class_info` tabs from a source spreadsheet, applies category-based pricing from `constants.py`, writes to a destination tab or CSV
- `sheets.py` — `fetch_events()` and `fetch_config_events()` read from Google Sheets with flexible header mapping (`COLUMN_MAPPING` in constants)
- `models.py` — Pydantic `EventRecord` model with field validators (date normalization, registration type mapping); `TicketSpec` for multi-ticket parsing
- `images.py` — downloads images from Google Drive, resizes with Pillow, uploads to Wix Media Manager
- `constants.py` — pricing table (`CATEGORY_PRICING`), column mappings, default values (location, capacity, tax)

**`wix_client.py`** (standalone, not in the package): Reusable Wix API client with retry/backoff. Covers events CRUD, ticket definitions, orders, categories, and media upload. Uses cursor/offset pagination via `_paged_post()`.

**Dev tools** (not part of the sync pipeline):
- `dev_events.py` — create/list/delete test events
- `dev_tickets.py` — ticket and RSVP operations for testing

## Key Design Patterns

- **Two-step workflow**: `prepare-sheet` generates event data into a Google Sheet tab, then `sync` reads that tab and pushes to Wix. This allows manual review between steps.
- **Duplicate detection**: Events are matched by `(title, start_date, start_time)`. Existing events are patched if fields changed; identical events are skipped.
- **Registration type normalization**: `TICKETS` in sheets maps to `TICKETING` in the Wix API.
- **Category-based pricing**: `CATEGORY_PRICING` in constants maps class categories to ticket prices. Unknown categories default to $30.
- **Config round-trip**: `pull-config` snapshots live Wix events into a `config_events` sheet tab; `push-config` diffs that tab against Wix and patches changes (ticket prices, descriptions, etc.).
- **Categories-only round-trip**: `pull-categories` / `push-categories` write/read only the `categories` column on a separate `category_config` tab (env: `CATEGORY_CONFIG_TAB`, default `category_config`). Descriptions, dates, status, and event id are pulled for context but never pushed — the only Wix calls on the push path are `iter_events`, `query_categories`, `create_category`, `assign_event_to_category`, and `unassign_event_from_category`. `--scope` (default `upcoming`) controls whether past events are included on pull and whether non-`UPCOMING`/`STARTED` rows are acted on at push time (out-of-scope rows are bucketed and skipped).
- **Env-var driven config**: All credentials and tab names come from `.env`. `SOURCE_SHEET_ID` can differ from `GOOGLE_SHEET_ID` to separate source data from sync target.

## Environment Variables

Required in `.env`: `WIX_API_KEY`, `WIX_SITE_ID`, `GOOGLE_SHEET_ID`, `GOOGLE_CREDENTIALS` (full service account JSON on one line). Optional: `WIX_ACCOUNT_ID`, `SOURCE_SHEET_ID`, `DEFAULTS_TAB`, `GENERATED_EVENTS_TAB`, `ROLLING_SCHEDULE_TAB`, `CLASS_INFO_TAB`, `CATEGORY_CONFIG_TAB`, `ENV_MODE=development` + `DEV_WIX_*` for sandbox.
