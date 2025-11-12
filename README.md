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

## Commands

```bash
# Using Python directly
python sync_events.py validate  # Validate credentials
python sync_events.py test      # Test Wix API connection
python sync_events.py list      # List existing Wix events
python sync_events.py sync      # Sync events from Google Sheets

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