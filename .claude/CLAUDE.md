# Event Automation Project - Technical Guide for AI Assistants

## Project Overview

Production-ready Python automation for syncing events from Google Sheets to Wix Events API with **automatic ticket creation**. Designed for small business (<2000 customers) with focus on simplicity, maintainability, and zero-cost automation.

**Status:** ✅ Production Ready (v2.0 - Ticket Automation Complete)
**Last Updated:** 2025-10-15

## Architecture (Updated 2025-10-15)

### Core Principle: DRY (Don't Repeat Yourself)

**All Wix API calls go through `wix_client.py`** - single source of truth.

```
wix_client.py (Shared API Library - 351 lines)
    ↓ Used by ALL scripts
    ├─ sync_events.py       → Production: Google Sheets sync + auto-tickets
    ├─ dev_events.py        → Development: Event CRUD operations
    ├─ dev_tickets.py       → Development: Ticket/search tools
    └─ test_ticket_automation.py → Testing: Ticket automation verification
```

### File Structure

```
event_automation/
├── wix_client.py              # Core: Reusable Wix API client (351 lines)
├── sync_events.py             # Production: Google Sheets sync (641 lines)
├── dev_events.py              # Development: Event CRUD CLI (603 lines)
├── dev_tickets.py             # Development: Ticket tools CLI (328 lines)
├── test_ticket_automation.py  # Testing: Ticket automation (138 lines)
├── requirements.txt           # Python dependencies
├── .env / .env.example        # Credentials (gitignored)
├── setup.sh / setup.bat       # Setup scripts
├── Makefile                   # Command shortcuts
├── README.md                  # User guide
├── SETUP.md                   # Setup instructions
├── CHECKLIST.md               # Setup checklist
├── TICKET_CONTROL_GUIDE.md    # Ticket automation user guide
├── .github/workflows/         # GitHub Actions automation
├── .claude/
│   └── CLAUDE.md             # This file
└── docs/                      # Technical documentation
    ├── README.md             # Documentation index
    ├── HISTORY.md            # Complete project history & changelog ⭐
    ├── TICKETING.md          # TICKETING events technical guide
    ├── DEV_TOOLS.md          # Development tools reference
    ├── CODE_AUDIT.md         # Architecture analysis
    └── FUNCTIONALITY_TEST_PLAN.md  # Test procedures
```

**Total Codebase:** ~2,061 lines of Python (0% duplication)

## Core Requirement: TICKETING Events

**IMPORTANT:** This project creates TICKETING type events, NOT RSVP events.

### Event Types Supported

| Type | Use Case | API Value | Tickets? |
|------|----------|-----------|----------|
| **TICKETING** | Paid tickets | `"TICKETING"` | ✅ Via Dashboard |
| RSVP | Free registration | `"RSVP"` | ❌ No |
| EXTERNAL | External platform | `"EXTERNAL"` | ❌ No |
| NO_REGISTRATION | Display only | `"NO_REGISTRATION"` | ❌ No |

### TICKETING Workflow

1. **API creates event** → `initialType: "TICKETING"`
2. **Event shows:** "Tickets are not on sale" ✅ (This is correct!)
3. **User adds tickets** → Via Wix Dashboard
4. **Tickets go on sale** → Event is live

### Critical API Discovery

**REST API vs JavaScript SDK:**
```python
# ✅ REST API (CORRECT)
'registration': {'initialType': 'TICKETING'}

# ❌ JavaScript SDK value (WRONG for REST API)
'registration': {'initialType': 'TICKETS'}  # Returns "value is required"
```

## Code Implementation

### Using WixClient (Recommended)

```python
from wix_client import WixClient

# Initialize client
client = WixClient()  # Auto-loads credentials from .env

# Create TICKETING event
event_data = {
    'title': 'My Event',
    'dateAndTimeSettings': {
        'startDate': '2025-10-14T12:00:00Z',
        'endDate': '2025-10-14T14:00:00Z',
        'timeZoneId': 'America/Toronto'
    },
    'location': {
        'type': 'VENUE',
        'address': {'formattedAddress': 'Event Location'}
    },
    'registration': {
        'initialType': 'TICKETING'  # CRITICAL: "TICKETING" not "TICKETS"
    }
}

# Create event
event = client.create_event(event_data)
print(f"Created: {event['id']}")

# List events
events = client.list_events(limit=50)

# Upload image
file_id = client.upload_image(image_bytes, 'event.jpg', 'image/jpeg')
```

### WixClient Features

- ✅ Automatic retry logic (3 attempts with exponential backoff)
- ✅ Rate limit handling (429 errors)
- ✅ Timeout handling
- ✅ Dev/production mode support
- ✅ Consistent error handling

## Registration Type Constraints

**CRITICAL:** Registration type is **immutable** after creation:
- ❌ Cannot convert RSVP → TICKETING
- ❌ Cannot convert TICKETING → RSVP
- ✅ Must create new event with desired type

## Known Issues & Limitations

### RSVP API Deprecated

The RSVP creation API endpoints appear to be deprecated:
- `/events/v3/rsvps` (POST) → 404 Not Found
- `/events/v3/rsvps/query` (POST) → 404 Not Found

**Workaround:** Use Wix Dashboard to manage RSVPs

**Status in Code:**
- `dev_tickets.py` RSVP functions marked as deprecated
- Functions kept with warnings in case API returns
- RSVP **events** work fine (creating events with `initialType: "RSVP"`)
- Only RSVP **registration/guest management** is broken

## Development vs Production

### Environment Modes

```bash
# .env file
ENV_MODE=production  # or development

# Production credentials
WIX_API_KEY=prod_key
WIX_SITE_ID=prod_site

# Development/Sandbox credentials (optional)
DEV_WIX_API_KEY=dev_key
DEV_WIX_SITE_ID=dev_site
```

When `ENV_MODE=development`, all scripts use `DEV_*` credentials automatically.

## Common Commands

### Event Operations

```bash
# Create events (all types work)
python dev_events.py create "Event Name" 7 false TICKETING
python dev_events.py create "Event Name" 7 false RSVP

# List/search
python dev_events.py list
python dev_events.py search "Workshop"

# Delete
python dev_events.py delete <event_id> --confirm
python dev_events.py delete-pattern "Test" --confirm
```

### Google Sheets Sync

```bash
# Validate credentials
python sync_events.py validate

# Test Wix connection
python sync_events.py test

# List existing events
python sync_events.py list

# Run sync
python sync_events.py sync
```

## Google Sheets Format

Spreadsheet should have columns A-L:

| Column | Field | Example |
|--------|-------|---------|
| A | Event Name | "Workshop 2024" |
| B | Event Type | "Workshop" |
| C | Start Date | "2024-03-15" (YYYY-MM-DD) |
| D | Start Time | "14:00" (HH:MM) |
| E | End Date | "2024-03-15" |
| F | End Time | "16:00" |
| G | Location | "Room 101" |
| H | Description | "Learn basics..." |
| I | Ticket Price | "25.00" |
| J | Capacity | "30" |
| K | Registration Type | "TICKETING" or "RSVP" |
| L | Image URL | Google Drive link |

**Note:** Column K should use `"TICKETS"` (user-friendly), code automatically converts to `"TICKETING"` (API requirement)

## Why Python REST API (Not JavaScript SDK)

This project uses Python + Wix REST API because:
- ✅ Simple Google Sheets integration
- ✅ Perfect for GitHub Actions automation
- ✅ Creates TICKETING event placeholders (tickets added via Dashboard)
- ✅ One language/codebase for simplicity
- ✅ No complex automated ticket creation needed

**JavaScript SDK would be better for:**
- Web applications or Node.js backends
- Real-time webhook processing
- Automated ticket creation/pricing via API

## Recent Changes (2025-10-07)

### Code Refactor
- ✅ Refactored `sync_events.py` to use `wix_client.py`
- ✅ Eliminated 102 lines of duplicated code
- ✅ Added automatic retry/rate-limit to sync
- ✅ Fixed UTF-8 encoding across all scripts
- ✅ Fixed RSVP event creation (missing registration field)
- ✅ All scripts now use shared `WixClient` library

### Documentation
- ✅ Moved technical docs to `docs/` folder
- ✅ Created comprehensive architecture analysis
- ✅ Documented refactor with test results
- ✅ Updated all cross-references

See [docs/CHANGELOG.md](../docs/CHANGELOG.md) for complete change history.

## Documentation Index

### User Documentation
- [README.md](../README.md) - Project overview and quick start
- [SETUP.md](../SETUP.md) - Complete setup guide
- [CHECKLIST.md](../CHECKLIST.md) - Setup checklist

### Technical Documentation
- [docs/TICKETING.md](../docs/TICKETING.md) - Complete TICKETING events guide
- [docs/DEV_TOOLS.md](../docs/DEV_TOOLS.md) - Development tools reference
- [docs/CODE_AUDIT.md](../docs/CODE_AUDIT.md) - Architecture analysis
- [docs/REFACTOR_COMPLETE.md](../docs/REFACTOR_COMPLETE.md) - Refactor summary
- [docs/CHANGELOG.md](../docs/CHANGELOG.md) - Version history

## Testing & Validation

All functionality verified with zero regression:
- ✅ TICKETING events create correctly
- ✅ RSVP events create correctly
- ✅ Google Sheets sync works end-to-end
- ✅ Image upload from Google Drive works
- ✅ All CLI commands functional

See [docs/FUNCTIONALITY_TEST_PLAN.md](../docs/FUNCTIONALITY_TEST_PLAN.md) for test procedures.

## Maintenance Philosophy

**For Small Business (<2000 customers):**
- ✅ Simple, readable code over clever abstractions
- ✅ DRY (Don't Repeat Yourself) principle
- ✅ Single source of truth for API operations
- ✅ Manual ticket setup (Dashboard) over complex automation
- ✅ Clear documentation over extensive comments

**Code Quality Metrics:**
- Total lines: ~488 lines (down from ~590)
- Duplicated code: 0 lines (was ~170)
- Single API client library used by all scripts
- All tests passing with zero regression

## Support & Troubleshooting

Common issues and solutions:

1. **UTF-8 Encoding Errors (Windows)**
   - All scripts now auto-configure UTF-8
   - Fixed in all Python files

2. **"initialType value is required"**
   - Using `"TICKETS"` instead of `"TICKETING"`
   - See [docs/TICKETING.md](../docs/TICKETING.md) for details

3. **RSVP creation fails (404)**
   - RSVP API deprecated, use Wix Dashboard
   - RSVP events still work, only guest management broken

4. **Rate limiting**
   - WixClient handles automatically with retry
   - Exponential backoff on 429 errors

## Project Status

✅ **Production Ready**
- All core functionality working
- Code refactored and optimized
- Documentation complete and up-to-date
- Zero known bugs or regressions
- Perfect for small business automation
