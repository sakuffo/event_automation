# Wix Events + Google Sheets Auto-Sync (Python)

Automatically sync events from Google Sheets to your Wix Events website using GitHub Actions. Simple Python script, runs daily, zero cost.

## What This Does

1. Reads events from a Google Sheet (your event planning spreadsheet)
2. Creates those events in Wix Events via API v3
3. Runs automatically every day at 9 AM EST
4. Costs $0/month (uses free tiers)

## Quick Start

```bash
# Unix/Linux/Mac
./setup.sh            # Run setup script
# OR
make setup           # Using Makefile

# Windows
setup.bat            # Run setup script

# Test and run
python sync_events.py validate  # Check credentials
python sync_events.py test      # Test Wix connection
python sync_events.py sync      # Run the sync
```

## Non-Techie Walkthrough

Once setup is done (above), this is the routine for actually getting events onto Wix. Open a terminal in this project folder and run each command in order — wait for one to finish before starting the next. If anything errors out, stop and ask before re-running.

### Part 1 — Push new events to Wix (the main upload)

This takes your planning spreadsheet and publishes everything to Wix.

```bash
# 1. Rebuild the "generated_events" tab from your source spreadsheet
#    (merges rolling_schedule + class_info, applies pricing).
python sync_events.py prepare-sheet

# 2. Open your Google Sheet and review the "generated_events" tab in your browser.
#    Fix any typos, descriptions, or image links directly in that tab.

# 3. Push everything from "generated_events" up to Wix.
python sync_events.py sync
```

Tips:

- To work on just one month, add `-m march` (or `mar`, `MAR`, etc.) to step 1: `python sync_events.py prepare-sheet -m march`.
- To upload as drafts first so you can review on Wix before going live, use `python sync_events.py sync --draft` for step 3.
- Re-running `sync` is safe — events that already exist on Wix are updated if they changed, and skipped if they didn't.

### Part 2 — Edit events that are already live on Wix

After events are uploaded, you can keep editing them from a spreadsheet instead of clicking around the Wix dashboard. Pick **one** of the two options below depending on what you need to change.

**Option A — change anything (descriptions, prices, dates, location, etc.)**

```bash
# 1. Pull the current live events from Wix into the "config_events" tab.
python sync_events.py pull-config

# 2. Edit values in the "config_events" tab in Google Sheets.

# 3. Preview what will change (recommended — nothing is sent to Wix yet).
python sync_events.py push-config --dry-run

# 4. If the preview looks right, push your edits to Wix for real.
python sync_events.py push-config
```

**Option B — only change categories (safer; nothing else can be touched)**

```bash
# 1. Pull events into the "category_config" tab.
python sync_events.py pull-categories

# 2. Edit only the "categories" column in that tab. Other columns are read-only.

# 3. Push the category changes back to Wix.
python sync_events.py push-categories
```

Add `--scope all` to either `pull-categories` or `push-categories` if you also need to retag past events (default is upcoming/in-progress only). Use `--dry-run` with `push-categories` to preview first.

## Commands

```bash
# Using Python directly
python sync_events.py validate  # Validate credentials
python sync_events.py test      # Test Wix API connection
python sync_events.py list      # List existing Wix events
python sync_events.py prepare-sheet  # Step 1: Rebuild generated_events tab
python sync_events.py prepare-sheet -m mar  # Step 1 for March only
python sync_events.py sync      # Sync events from Google Sheets
python sync_events.py generate --output-sheet my_tab  # Custom generation target
python sync_events.py generate --output-sheet my_tab -m March  # Custom tab + month filter

# Config round-trip (pull from Wix → edit in sheet → push back)
python sync_events.py pull-config              # Snapshot live Wix events into config_events tab
python sync_events.py push-config --dry-run    # Preview pending edits
python sync_events.py push-config              # Push edits back to Wix

# Categories-only round-trip (only the `categories` column is editable)
python sync_events.py pull-categories                       # default --scope upcoming
python sync_events.py pull-categories --scope all           # past + present + future
python sync_events.py push-categories                       # default --scope upcoming
python sync_events.py push-categories --scope all
python sync_events.py push-categories --scope all --dry-run # preview only

# Site config: bulk eCommerce tax-by-location (pay-link checkout tax)
python sync_events.py pull-site-config            # Snapshot tax regions/mappings into site_config tab
python sync_events.py push-site-config --dry-run  # Preview rate changes
python sync_events.py push-site-config            # Apply rates (e.g. 13% HST) to every location

# Using Make shortcuts
make setup          # Complete setup
make validate       # Validate credentials
make test          # Test connection
make list          # List events
make sync          # Run sync
make install-dev   # Install dev/test dependencies
make unit          # Run pytest suite
make clean         # Clean up files
```

All CLI subcommands accept `--log-level` (e.g., `python sync_events.py sync --log-level DEBUG`).

## Two-Step Workflow (Manual Step 2)

```bash
# Step 1: Rebuild destination tab in GOOGLE_SHEET_ID from SOURCE_SHEET_ID
python sync_events.py prepare-sheet
# Optional month filter: mar, MAR, March, etc.
python sync_events.py prepare-sheet -m March

# Step 2 (manual): Push events from GOOGLE_SHEET_ID into Wix
python sync_events.py sync
```

Notes:

- `prepare-sheet` fully clears and rewrites the destination tab each run.
- Destination tab defaults to `generated_events` and can be changed via `GENERATED_EVENTS_TAB`.
- Source tabs default to `rolling_schedule` and `class_info` and can be changed via `ROLLING_SCHEDULE_TAB` and `CLASS_INFO_TAB`.
- `defaults.default_img` is used as the fallback `image_url` when `class_info.image_link` is empty.

## Config Round-Trips

Two pull/push pairs let you edit live Wix events from a Google Sheet and push the edits back. They share `GOOGLE_SHEET_ID` but use separate tabs so the workflows never collide.

### Full config (`config_events`)

```bash
python sync_events.py pull-config              # Snapshot Wix → config_events + config_events_last_pull
python sync_events.py push-config --dry-run    # Preview every change
python sync_events.py push-config              # Push edits to Wix
```

`pull-config` writes every published Wix event (status `UPCOMING`/`STARTED`) into the `config_events` tab plus a `config_events_last_pull` snapshot for diffing. `push-config` reads the editable tab and patches matching Wix events — covers descriptions, dates, location, registration type, ticket prices, and tax. Match key: `(title, start_date, start_time)`.

### Categories only (`category_config`)

```bash
python sync_events.py pull-categories                       # default --scope upcoming
python sync_events.py pull-categories --scope all           # past + present + future
python sync_events.py push-categories                       # default --scope upcoming
python sync_events.py push-categories --scope all
python sync_events.py push-categories --scope all --dry-run # preview only
```

A slim 8-column round-trip whose only editable field is `categories`. Useful when you want to retag a backlog of events without risking accidental edits to descriptions, prices, or dates.

- **Scope flag**: `--scope upcoming` (default) keeps only `UPCOMING`/`STARTED` events on pull and silently skips out-of-scope rows on push (counted as `out_of_scope`). `--scope all` includes every non-draft event ever published, sorted future-first on pull.
- **Tab layout**: columns are exactly `event_name`, `categories`, `short_description`, `detailed_description`, `start_date`, `start_time`, `status`, `event_id`. Read-only headers are prefixed with `(ro) ` and the header row is frozen. The descriptions are pulled for context but are silently ignored on push.
- **Matching**: rows match by `event_id` first; rows with a blank `event_id` fall back to `(title, start_date, start_time)`, so you can hand-add rows in a pinch.
- **Wix call surface on push**: only `iter_events`, `query_categories`, `create_category`, `assign_event_to_category`, and `unassign_event_from_category`. No `update_event`, no ticket calls, no media calls.
- **Tab names**: `category_config` (editable) plus `category_config_last_pull` (snapshot). Override the live tab name via `CATEGORY_CONFIG_TAB`.

### Site config — tax by location (`site_config`)

```bash
python sync_events.py pull-site-config            # Snapshot Wix tax regions/mappings → site_config + site_config_last_pull
python sync_events.py push-site-config --dry-run  # Preview rate changes
python sync_events.py push-site-config            # Apply rates (e.g. 13% HST) to every location
```

A site-wide settings tab, starting with eCommerce **tax by location**. This is the tax that applies at checkout for **pay links** and other eCommerce purchases — it is completely separate from the per-event **ticket tax** handled by `config_events`/`push-config`.

Under the hood these are Wix [manual tax mappings](https://dev.wix.com/docs/api-reference/business-solutions/e-commerce/extensions/tax/manual-tax-mappings/create-manual-tax-mapping): a tax rate attached to a (tax region + tax group) pair. Setting them by hand in the dashboard is one-at-a-time; this round-trip lets you set them all at once from a sheet.

- **Tab layout**: columns are `setting_type`, `jurisdiction`, `region`, `tax_name`, `tax_type`, `tax_rate`, `region_id`, `group_id`, `mapping_id`, `revision`. Only `tax_name`, `tax_type`, and `tax_rate` are editable; read-only headers are prefixed with `(ro) ` and the header row is frozen. `setting_type` is `tax_location` for these rows (the column leaves room for other site settings later).
- **Rates are percentages**: enter `13` for 13% HST. The tool converts to/from the Wix decimal form (`0.13`) automatically.
- **Pull** lists one row per existing tax mapping, plus a blank-rate row for any tax region that has no mapping yet (so you can fill in a rate and create it on push).
- **Push** updates a mapping when its rate/name/type differ, and bulk-creates a mapping for any region+group that has none. Blank `tax_rate` rows are skipped, and mappings are **never deleted**.
- **Wix call surface on push**: only `query_manual_tax_mappings`, `update_manual_tax_mapping`, and `bulk_create_manual_tax_mappings` (all under `billing/v1`). No event, ticket, category, or media calls.
- **Tab names**: `site_config` (editable) plus `site_config_last_pull` (snapshot). Override the live tab name via `SITE_CONFIG_TAB`.
- **API permission**: the Wix API key must include the eCommerce **Manage Orders** scope for the tax endpoints to work. If `pull-site-config` reports no regions/mappings, that scope (or having any tax regions configured) is the usual cause.

## Google Sheet Format

Your spreadsheet should have these columns (A-L):

| Event Name | Event Type | Start Date | Start Time | End Date | End Time | Location | Description | Ticket Price | Capacity | Registration Type | Image URL |
|------------|------------|------------|------------|----------|----------|----------|-------------|--------------|----------|-------------------|-----------|
| Workshop 1 | Workshop   | 2024-03-15 | 14:00      | 2024-03-15 | 16:00   | Room 101 | Learn basics | 0           | 30       | RSVP             | https://drive.google.com/file/d/ABC123... |

- **Dates**: YYYY-MM-DD format
- **Times**: HH:MM (24-hour format)
- **Registration Types**:
  - `RSVP` - Free RSVP events
  - `TICKETS` - Creates ticketed event (shows "Tickets are not on sale" until you add tickets via Dashboard)
  - `EXTERNAL` - External registration
  - `NO_REGISTRATION` - Display only
- **Image URL** (Column L): Google Drive link to event image (optional)
  - Accepts full URL, short URL, or just the file ID
  - Must be shared with service account or set to "Anyone with the link"

## How It Works

- **Modular Python package (`event_sync/`)** - reusable components for Sheets, Drive, and Wix
- **Google Sheets API** reads your spreadsheet using service account auth
- **Wix Events API v3** creates events on your Wix site via REST API
- **GitHub Actions** runs the sync automatically (daily + manual trigger)
- **Duplicate detection & updates** - skips identical events and patches changes from Sheets

### Why Python REST API?

This project uses Python + Wix REST API instead of JavaScript SDK because:

- ✅ Simple Google Sheets integration (Python libraries)
- ✅ Perfect for automated scripts (GitHub Actions)
- ✅ Creates TICKETING event placeholders - tickets added manually via Dashboard
- ✅ One language/codebase for small business simplicity
- ✅ No complex ticket pricing automation needed

**JavaScript SDK would be better for:** Web apps, Node.js backends, real-time webhooks, or automated ticket creation/pricing via API.

### Update Behavior

- Events are keyed by title, start date, and start time so the sync can spot existing records.
- When the sheet changes schedule, location, registration type, teaser, description, or title, the Wix event is patched instead of skipped.
- Unchanged rows log as skipped to avoid unnecessary API calls.
- Ticket definitions are still created only on insert; adjust them manually in Wix if pricing or capacity changes later.

## Project Structure

```
.
├── event_sync/                # Modular sync package (config, CLI, services)
│   ├── __init__.py
│   ├── cli.py
│   ├── config.py
│   ├── constants.py
│   ├── images.py
│   ├── logging_utils.py
│   ├── models.py
│   ├── orchestrator.py
│   ├── runtime.py
│   └── sheets.py
├── sync_events.py             # Compatibility wrapper (delegates to package CLI)
├── wix_client.py              # Reusable Wix API client library
├── dev_events.py              # Development: Event CRUD operations
├── dev_tickets.py             # Development: Ticket/RSVP automation
├── requirements.txt            # Python dependencies
├── requirements-dev.txt        # Dev/test dependencies (pytest)
├── .env                       # Your credentials (create manually; see SETUP.md)
├── setup.sh                   # Unix/Mac setup script
├── setup.bat                  # Windows setup script
├── Makefile                   # Command shortcuts
├── .github/
│   └── workflows/
│       ├── ci.yml             # CI pipeline (lint + tests)
│       └── sync-events.yml    # GitHub Actions workflow
├── README.md                  # This file
├── SETUP.md                   # Detailed setup guide
├── DEV_TOOLS.md              # Development tools documentation
├── docs/CODE_AUDIT.md        # Architecture snapshot & hardening checklist
└── CHECKLIST.md              # Setup checklist
```

## Environment Variables

Create a `.env` file with:

```bash
WIX_API_KEY=your_wix_api_key
WIX_ACCOUNT_ID=your_account_id
WIX_SITE_ID=your_site_id
GOOGLE_SHEET_ID=your_spreadsheet_id
SOURCE_SHEET_ID=your_source_spreadsheet_id  # optional; falls back to GOOGLE_SHEET_ID
DEFAULTS_TAB=defaults
GENERATED_EVENTS_TAB=generated_events
CATEGORY_CONFIG_TAB=category_config         # optional; tab name for pull/push-categories
SITE_CONFIG_TAB=site_config                 # optional; tab name for pull/push-site-config (tax by location)
GOOGLE_CREDENTIALS={"type":"service_account"...}  # Full JSON on one line
```

## Cost Breakdown

**$0/month** using free tiers:
- Google Sheets API: 100 requests/100 seconds (free)
- Wix Events API: Included with Wix site
- GitHub Actions: 2,000 minutes/month free
- Daily sync uses ~30 seconds = 15 minutes/month

## Requirements

- Python 3.8+
- Wix website with Events app
- Google Cloud project with Sheets API and Drive API enabled
- GitHub account for automation

## Development Tools

Test and develop without using the live site! See [docs/DEV_TOOLS.md](docs/DEV_TOOLS.md) for complete documentation.

Quick examples:

```bash
# Create test events (prefixed with "-test-" for easy cleanup)
make dev-samples              # Create 5 sample events (RSVP + TICKETS mix)
make dev-create               # Create single RSVP event
make dev-create-ticket        # Create single TICKETED event

# List events
make dev-list                 # List all events

# Create test RSVPs
make dev-rsvp EVENT_ID=abc123           # Single RSVP
make dev-bulk-rsvp EVENT_ID=abc123      # 10 RSVPs

# Search
make dev-search QUERY='Workshop'        # Search events

# Or use directly with more control
python dev_events.py create "Concert" 7 false TICKETS  # Creates ticketed event placeholder
python dev_events.py create "Workshop" 7 true RSVP     # Creates RSVP event

# TICKETS events workflow:
# 1. API creates event with TICKETING registration type → Shows "Tickets are not on sale"
# 2. You add tickets manually via Wix Dashboard → Tickets go on sale
# Note: Registration type cannot be changed after creation

# Cleanup dev/test events
make dev-clean-drafts       # Delete all draft events
make dev-clean-test         # Delete all test events
python dev_events.py delete-pattern "Workshop" --confirm
```

Full command list: `make dev-help`

**Dev/Sandbox Mode:**
Configure separate credentials for testing in `.env`:
```bash
ENV_MODE=development
DEV_WIX_API_KEY=your_dev_api_key
DEV_WIX_SITE_ID=your_dev_site_id
```

## Testing & Quality Checks

- `make install-dev` – install production + testing dependencies.
- `make unit` – run the pytest suite locally (includes new event model + image helpers).
- GitHub Actions (`.github/workflows/ci.yml`) runs the same tests on every push/pull request.
- Scheduled workflow (`sync-events.yml`) continues to run the production sync once credentials are validated.

## Documentation

### Getting Started

- [SETUP.md](SETUP.md) - Complete setup instructions
- [CHECKLIST.md](CHECKLIST.md) - Setup checklist

### Technical Documentation

- [docs/DEV_TOOLS.md](docs/DEV_TOOLS.md) - Development tools and commands
- [docs/TICKETING.md](docs/TICKETING.md) - Creating ticketed events (technical guide)
- [docs/HISTORY.md](docs/HISTORY.md) - Project history and change log
- [docs/CODE_AUDIT.md](docs/CODE_AUDIT.md) - Architecture analysis

## License

MIT