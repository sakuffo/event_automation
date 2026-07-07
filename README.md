# Wix Events Automation with a Notion Backend (Python)

Events are planned in a Notion workspace and pushed to the Wix Events website
by a Python CLI — run locally or on a GitHub Actions schedule. Anyone on the
team can drop an event placeholder into Notion; the system enriches it from
the class catalog, a human flips it to Ready, and the sync publishes it to
Wix with tickets, categories, and images.

> The previous Google Sheets pipeline still works side by side (see
> [Legacy Google Sheets pipeline](#legacy-google-sheets-pipeline)) and will be
> removed once the Notion flow has proven itself through a full posting cycle.

## What This Does

1. Team members add events to the **Events** database in Notion (a name that
   matches a catalog template + a date is enough)
2. `enrich` fills in the blanks (categories, price, description, image) from
   the **Catalog** (class + recurring-event templates) and **Settings** defaults
3. A human reviews the Draft in Notion and flips Status to **Ready**
4. `sync` creates the event in Wix (tickets, categories, image included) and
   writes back the Wix ID, sync time, and status — so Notion always shows
   what's posted, what's pending, and what failed
5. Editing an already-Published row is picked up automatically on the next
   sync — no snapshot tabs, no status juggling

Full backend reference: [docs/NOTION_BACKEND.md](docs/NOTION_BACKEND.md)

## Quick Start

```bash
# Unix/Linux/Mac
./setup.sh            # Run setup script
# OR
make setup           # Using Makefile

# Windows
setup.bat            # Run setup script

# Configure .env (see Environment Variables below), then:
python sync_events.py validate      # Check credentials
python sync_events.py test          # Test Wix connection

# One-time Notion bootstrap
python sync_events.py setup-notion   # Create the 4 databases, print their IDs
#   -> copy the printed NOTION_*_DB_ID values into .env
python sync_events.py import-classes # Seed the Catalog DB from the class_info sheet
python sync_events.py pull           # Backfill Events DB from live Wix events
```

## The Routine (day to day)

1. **Add events in Notion** — new row in the Events DB with Status `Idea`,
   a Name matching a catalog template (or a linked Template), and a Date.
   Anyone can do this. Set up the default "New Event" template once (see
   [docs/NOTION_BACKEND.md](docs/NOTION_BACKEND.md#default-new-event-template-create-by-hand--the-api-cant-create-templates))
   so new rows come pre-filled with the never-changing fields (HST 13% at
   checkout, fee at checkout, location, capacity); the pipeline also fills
   any blanks from the Settings DB automatically.

```bash
# 2. Fill in the blanks from the class catalog (Idea -> Draft)
python sync_events.py enrich

# 3. Review the Draft rows in Notion, fix anything, flip Status to Ready.

# 4. Preview, then push to Wix
python sync_events.py sync --dry-run
python sync_events.py sync
```

Tips:

- Month filter: `python sync_events.py enrich -m aug sep` / `sync -m aug`.
- `sync --draft` creates events as Wix drafts; re-run `sync` without the flag
  to publish them.
- Re-running `sync` is always safe — unchanged rows are skipped (content
  hash), changed Published rows are patched, failures land in the row's
  `Sync Error` with Status `Error`.
- To edit live events, just edit the Published row in Notion and run `sync`.

## Commands

```bash
# Setup / diagnostics
python sync_events.py validate            # Validate credentials (Wix + Google + Notion)
python sync_events.py test                # Test Wix API connection
python sync_events.py list                # List existing Wix events

# Notion pipeline
python sync_events.py setup-notion        # One-time: create Notion databases
python sync_events.py import-classes      # One-time: class_info sheet -> Catalog DB
python sync_events.py pull                # Wix -> Notion backfill/refresh (--scope all for past events)
python sync_events.py enrich              # Fill blanks on Idea/Draft rows (-m for months)
python sync_events.py sync                # Push Ready + changed Published rows (--dry-run, --draft, -m)

# Site config: eCommerce tax-by-location (pay-link checkout tax)
python sync_events.py pull-site-config    # Wix tax regions/mappings -> Notion Site Config DB
python sync_events.py push-site-config --dry-run
python sync_events.py push-site-config    # Apply rates (e.g. 13% HST) to every location
```

All CLI subcommands accept `--log-level` (e.g., `python sync_events.py sync --log-level DEBUG`).

## Notion Data Model

Four databases, created by `setup-notion` (details and property tables in
[docs/NOTION_BACKEND.md](docs/NOTION_BACKEND.md)):

- **Events** — one row per event; the single source of truth. Lifecycle:
  `Idea → Draft → Ready → Published` (plus `Error`, `Skip`). Flip a row to
  `Cancel` to cancel it on Wix (row becomes `Cancelled`), or `Delete` to
  remove it from Wix entirely (row becomes `Removed`). Sync bookkeeping
  (`Wix Event ID`, `Last Synced`, `Synced Hash`, `Sync Error`) is code-owned.
- **Catalog** — class and recurring-event templates (`Type` = class/event;
  categories, tagline, description, image, optional price/capacity
  overrides). Enrichment matches by relation or name.
- **Settings** — key/value defaults (`default_img`, …).
- **Site Config** — one row per tax location; only name/type/rate are pushed.

Recommended views to add by hand: Calendar on Date, Board by Status, a
"Needs attention" table filtered to non-empty Sync Error.

## Automation (GitHub Actions)

[.github/workflows/sync-events.yml](.github/workflows/sync-events.yml) runs
`enrich` + `sync` daily at 9 AM EST, on manual dispatch, and on
`repository_dispatch` (type `notion-sync`) so a Notion button/automation
webhook can trigger an instant run — see
[docs/NOTION_BACKEND.md](docs/NOTION_BACKEND.md#triggering-runs).

Required repo secrets: `WIX_API_KEY`, `WIX_ACCOUNT_ID`, `WIX_SITE_ID`,
`GOOGLE_CREDENTIALS` (Drive images), `NOTION_ACCESS_TOKEN`, and the four
`NOTION_*_DB_ID` values.

## Environment Variables

Create a `.env` file with:

```bash
# Wix
WIX_API_KEY=your_wix_api_key
WIX_ACCOUNT_ID=your_account_id
WIX_SITE_ID=your_site_id

# Notion
NOTION_ACCESS_TOKEN=ntn_...                 # integration token (share the parent page with it)
NOTION_PARENT_PAGE_ID=...                   # only needed by setup-notion
NOTION_EVENTS_DB_ID=...                     # printed by setup-notion
NOTION_CATALOG_DB_ID=...                    # was NOTION_CLASSES_DB_ID (old name still accepted)
NOTION_SETTINGS_DB_ID=...
NOTION_SITE_CONFIG_DB_ID=...

# Google (Drive image downloads + legacy sheet commands)
GOOGLE_CREDENTIALS={"type":"service_account"...}  # Full JSON on one line
GOOGLE_SHEET_ID=your_spreadsheet_id               # legacy pipeline + import-classes
SOURCE_SHEET_ID=your_source_spreadsheet_id        # optional; falls back to GOOGLE_SHEET_ID
```

Optional: `ENV_MODE=development` + `DEV_WIX_*` for a sandbox Wix site, and the
legacy tab-name overrides (`GENERATED_EVENTS_TAB`, `CATEGORY_CONFIG_TAB`,
`SITE_CONFIG_TAB`, `ROLLING_SCHEDULE_TAB`, `CLASS_INFO_TAB`, `DEFAULTS_TAB`).

## How It Works

- **Modular Python package (`event_sync/`)** — `notion_store.py` owns all
  Notion I/O (API version `2025-09-03`, data-source aware);
  `notion_orchestrator.py` composes Notion with the existing Wix call paths
- **Wix Events API v3** creates/patches events via REST (`wix_client.py`)
- **Google Drive** still hosts event images; `images.py` downloads (Drive API
  or plain HTTP), resizes with Pillow, and uploads to Wix Media
- **Duplicate detection** — rows match Wix by `Wix Event ID` first, then by
  title+date+time; unchanged rows are skipped via a content hash
- **Registration type normalization** — `TICKETS` in Notion maps to
  `TICKETING` in the Wix API
- **Category pricing** — `CATEGORY_PRICING` in `event_sync/constants.py`
  (class `Price Override` in Notion wins when set; unknown categories default
  to $30)

## Legacy Google Sheets pipeline

Kept working during the transition; every command has the same behavior as
before the Notion migration:

```bash
python sync_events.py prepare-sheet            # rolling_schedule + class_info -> generated_events tab
python sync_events.py sync-sheet               # generated_events tab -> Wix (was: sync)
python sync_events.py pull-config              # Wix -> config_events tab
python sync_events.py push-config --dry-run    # config_events tab -> Wix
python sync_events.py pull-categories          # Wix -> category_config tab
python sync_events.py push-categories          # category_config tab -> Wix
python sync_events.py pull-site-config-sheet   # Wix tax -> site_config tab (was: pull-site-config)
python sync_events.py push-site-config-sheet   # site_config tab -> Wix (was: push-site-config)
python sync_events.py generate                 # merged CSV to stdout
python sync_events.py clean-synced             # delete rope+class events matching the generated tab
python sync_events.py publish-drafts           # publish drafts matching the generated tab
```

The sheet format, round-trip semantics, and tab names are documented in the
git history of this README and in [docs/HISTORY.md](docs/HISTORY.md).

## Project Structure

```
.
├── event_sync/                # Modular sync package
│   ├── cli.py                 # CLI (Notion commands + legacy sheet commands)
│   ├── config.py              # Env-driven settings (Wix, Google, Notion)
│   ├── constants.py           # Pricing table, defaults, column mappings
│   ├── images.py              # Drive/HTTP image download -> Wix Media upload
│   ├── models.py              # EventRecord (+ content_hash), TicketSpec
│   ├── notion_store.py        # All Notion I/O: schemas, mapping, queries
│   ├── notion_orchestrator.py # enrich / sync / pull / site-config flows
│   ├── orchestrator.py        # Wix flows shared by both backends + legacy sheet flows
│   ├── generator.py           # [legacy] sheet merge + tab writers
│   ├── sheets.py              # [legacy] sheet readers
│   └── runtime.py             # Lazy Google/Wix/Notion clients + caches
├── sync_events.py             # CLI entrypoint
├── wix_client.py              # Reusable Wix API client
├── scripts/                   # Dev helpers (create test Idea rows, set status)
├── docs/NOTION_BACKEND.md     # Notion data model + lifecycle + triggers
├── .github/workflows/         # ci.yml, sync-events.yml
└── tests/                     # pytest suite
```

## Testing & Quality Checks

- `make install-dev` – install production + testing dependencies.
- `make unit` – run the pytest suite locally (includes Notion property-mapping
  and hash-diff tests with a mocked Notion client).
- GitHub Actions (`.github/workflows/ci.yml`) runs the same tests on every
  push/pull request.

## Development Tools

Test against the dev/sandbox Wix site without touching the live one! See
[docs/DEV_TOOLS.md](docs/DEV_TOOLS.md).

```bash
ENV_MODE=development
DEV_WIX_API_KEY=your_dev_api_key
DEV_WIX_SITE_ID=your_dev_site_id
```

Dev helpers for the Notion flow:

```bash
python scripts/create_test_idea_row.py "Your First Rope Class" 2026-08-12 19:00 22:00
python scripts/set_event_status.py <page_id> Ready
```

## Documentation

- [SETUP.md](SETUP.md) — Complete setup instructions
- [docs/NOTION_BACKEND.md](docs/NOTION_BACKEND.md) — Notion databases, lifecycle, triggers
- [docs/DEV_TOOLS.md](docs/DEV_TOOLS.md) — Development tools and commands
- [docs/TICKETING.md](docs/TICKETING.md) — Creating ticketed events (technical guide)
- [docs/HISTORY.md](docs/HISTORY.md) — Project history and change log
- [docs/CODE_AUDIT.md](docs/CODE_AUDIT.md) — Architecture analysis

## License

MIT
