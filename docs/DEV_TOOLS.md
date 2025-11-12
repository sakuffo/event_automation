# Development Tools

Automation scripts for testing and development without needing to use the live site.

## Overview

The development toolkit includes:

- **event_sync/** - Modular package used by the sync CLI (config, runtime, orchestrator)
- **wix_client.py** - Reusable Python client for Wix APIs
- **dev_events.py** - Full CRUD operations for events (RSVP, TICKETING, EXTERNAL, NO_REGISTRATION)
- **dev_tickets.py** - Ticket management and search tools

⚠️ **Important Note:** RSVP creation via API appears to be deprecated. RSVP commands in `dev_tickets.py` may not work. Use Wix Dashboard to manage RSVPs.

## Setup

### 1. Install Dependencies

```bash
# If you haven't already
bash setup.sh
# or
make setup

# For test tooling / CI parity
make install-dev
make unit
```

### 2. Configure Environment

Edit `.env` file:

```bash
# Production credentials (required)
WIX_API_KEY=your_production_api_key
WIX_SITE_ID=your_production_site_id
WIX_ACCOUNT_ID=your_production_account_id

# Development/Sandbox credentials (optional)
DEV_WIX_API_KEY=your_dev_api_key
DEV_WIX_SITE_ID=your_dev_site_id
DEV_WIX_ACCOUNT_ID=your_dev_account_id

# Environment mode
ENV_MODE=development  # or production
```

When `ENV_MODE=development` and `DEV_*` credentials are set, all dev scripts will use the sandbox site instead of production.

### 3. Logging & Verbosity

- Every `python sync_events.py ...` command accepts `--log-level` (DEBUG, INFO, WARNING, ERROR, CRITICAL).
- Example: `python sync_events.py sync --log-level DEBUG` prints detailed Sheets/Wix calls for troubleshooting.
- The CLI and orchestration modules emit logs via Python's `logging` module, so `LOGGING_LEVEL` environment tweaks or custom handlers work out of the box.

## Event Operations (dev_events.py)

Manage events programmatically without the Wix dashboard.

### List All Events

```bash
# List all events (default: 50)
python dev_events.py list

# List with custom limit
python dev_events.py list 100
```

### Get Event Details

```bash
# Get full event details including JSON
python dev_events.py get <event_id>
```

### Create Events

```bash
# Create a draft RSVP event 7 days from now
python dev_events.py create "My Test Event"

# Create event X days from now
python dev_events.py create "Workshop" 14 true RSVP

# Create and publish immediately
python dev_events.py create "Live Event" 3 false RSVP

# Create event for external registration
python dev_events.py create "External Event" 7 true EXTERNAL

# Registration types: RSVP, TICKETS, EXTERNAL, NO_REGISTRATION
python dev_events.py create "Concert" 7 false TICKETS  # Creates ticketed event
```

**TICKETS Event Workflow:**
1. API creates event with `TICKETING` registration type
2. Event shows "Tickets are not on sale" status
3. Add tickets manually via Wix Dashboard → Tickets go on sale

**Important:** Registration type is set at creation and **cannot be changed**. You cannot convert RSVP to TICKETING or vice versa.

### Update Events

```bash
# Update event title
python dev_events.py update-title <event_id> "New Title"
```

### Publish Events

```bash
# Publish a draft event
python dev_events.py publish <event_id>
```

### Delete Events

```bash
# Delete single event (requires confirmation)
python dev_events.py delete <event_id> --confirm

# Delete all draft events
python dev_events.py delete-drafts --confirm

# Delete all test events (title contains 'test')
python dev_events.py delete-test --confirm

# Delete events matching a pattern
python dev_events.py delete-pattern "Workshop" --confirm

# Delete only draft events matching pattern
python dev_events.py delete-pattern "Concert" --confirm --drafts-only

# Using Makefile shortcuts (includes safety delays)
make dev-clean-drafts     # Delete all drafts (3 second delay)
make dev-clean-test       # Delete all test events (3 second delay)
make dev-clean-all        # Delete ALL events (5 second delay)
```

### Create Sample Events

```bash
# Create 5 sample events for testing (mix of RSVP and TICKETS)
python dev_events.py create-samples

# Create custom number of samples
python dev_events.py create-samples 20

# Sample events are prefixed with "-test-" for easy cleanup:
# - "-test- Workshop: Introduction to Python" (RSVP)
# - "-test- Networking Happy Hour" (TICKETS)
# - "-test- Tech Talk: Cloud Architecture" (RSVP)
# - "-test- Team Building Event" (RSVP)
# - "-test- Product Demo Session" (TICKETS)

# Clean up all sample events
python dev_events.py delete-test --confirm
# or
make dev-clean-test
```

### Search Events

```bash
# Search for events by title
python dev_events.py search "Workshop"
```

## Ticket Operations (dev_tickets.py)

Automate ticket purchases and RSVPs for testing.

### Create Single RSVP

```bash
# Create RSVP with default test user
python dev_tickets.py rsvp <event_id>

# Create RSVP with custom details
python dev_tickets.py rsvp <event_id> "John Doe" "john@example.com" 2
```

### Create Bulk RSVPs

```bash
# Create 10 test RSVPs
python dev_tickets.py bulk-rsvp <event_id>

# Create custom number of RSVPs
python dev_tickets.py bulk-rsvp <event_id> 50
```

### List RSVPs

```bash
# List all RSVPs for an event
python dev_tickets.py list-rsvps <event_id>
```

### List Orders

```bash
# List all ticket orders for an event
python dev_tickets.py list-orders <event_id>
```

### Search for Events

```bash
# Find event by title to get event_id
python dev_tickets.py search-event "Workshop"
```

## Using the Wix Client Library

You can also use `wix_client.py` directly in your own Python scripts:

```python
from wix_client import WixClient

# Initialize client (uses credentials from .env)
client = WixClient()

# Or force development mode
client = WixClient(use_dev=True)

# List events
events = client.list_events(limit=10)

# Get specific event
event = client.get_event('event_id_here')

# Create event
new_event = client.create_event({
    'title': 'My Event',
    'dateAndTimeSettings': {
        'startDate': '2024-12-01T14:00:00Z',
        'endDate': '2024-12-01T16:00:00Z',
        'timeZoneId': 'America/Toronto'
    },
    'location': {
        'type': 'VENUE',
        'address': {'formattedAddress': 'Test Location'}
    },
    'registration': {'initialType': 'RSVP'},
    'draft': True
})

# Create RSVP
rsvp = client.create_rsvp(
    event_id='event_id_here',
    contact_info={
        'firstName': 'John',
        'lastName': 'Doe',
        'email': 'john@example.com'
    },
    guest_count=2
)

# Search events
results = client.search_events_by_title('Workshop')
```

## Common Workflows

### Testing Event Creation and Registration Flow

```bash
# 1. Create a test event
python dev_events.py create "Test Workshop" 7 false

# 2. Get the event ID from output or search
python dev_events.py search "Test Workshop"

# 3. Create bulk RSVPs for testing
python dev_tickets.py bulk-rsvp <event_id> 25

# 4. Verify RSVPs were created
python dev_tickets.py list-rsvps <event_id>

# 5. Clean up when done
python dev_events.py delete <event_id> --confirm
```

### Load Testing

```bash
# Create 50 events
python dev_events.py create-samples 50

# Create 100 RSVPs per event (use in a loop or script)
for event_id in $(python dev_events.py list | grep "ID:" | awk '{print $2}'); do
    python dev_tickets.py bulk-rsvp $event_id 100
done
```

### Development vs Production

```bash
# Test on sandbox/dev site
export ENV_MODE=development
python dev_events.py create "Dev Test Event"

# Switch to production
export ENV_MODE=production
python dev_events.py list
```

Or set in `.env`:
```bash
ENV_MODE=development  # Uses DEV_* credentials
ENV_MODE=production   # Uses regular credentials (default)
```

## Available Wix Client Methods

### Events
- `list_events(limit, offset)` - List events
- `get_event(event_id)` - Get event details
- `create_event(event_data)` - Create new event
- `update_event(event_id, event_data)` - Update event
- `delete_event(event_id)` - Delete event
- `publish_event(event_id)` - Publish draft event
- `search_events_by_title(title)` - Search by title
- `get_event_by_title(title)` - Get first match by title

### Registrations/Tickets
- `create_rsvp(event_id, contact_info, guest_count, form_response)` - Create RSVP
- `get_rsvps(event_id, limit)` - List RSVPs
- `create_ticket_order(event_id, tickets, checkout_info)` - Create ticket order
- `get_orders(event_id, limit)` - List orders

### Media
- `upload_image(image_data, filename, mime_type)` - Upload image to Wix

## Tips

1. **Always test on dev/sandbox first** - Set up `DEV_*` credentials for a test site
2. **Use draft mode** - Create events as drafts (`draft=true`) for testing
3. **Rate limiting** - Scripts include delays to avoid API rate limits
4. **Clean up** - Delete test events/RSVPs when done testing
5. **Search first** - Use search to find events instead of copying IDs manually

## Testing & Regression Checklist

Run this mini-regression before shipping changes to the sync pipeline:

1. `make install-dev`
2. `make unit`
3. `python sync_events.py validate --log-level DEBUG`
4. `python sync_events.py test`
5. `python sync_events.py sync` (against a sandbox sheet)

### Event Creation Coverage

| Registration Type | Command | Expected Result |
|-------------------|---------|-----------------|
| `RSVP` | `python dev_events.py create "Test RSVP" 7 false RSVP` | Event includes `registration.initialType = "RSVP"` |
| `TICKETING` | `python dev_events.py create "Test Ticket" 7 false TICKETS` | Event converted to `"TICKETING"`; tickets must exist or be added |
| `EXTERNAL` | `python dev_events.py create "Test External" 7 false EXTERNAL` | Event published with external registration link placeholder |
| `NO_REGISTRATION` | `python dev_events.py create "Test NoReg" 7 false NO_REGISTRATION` | Display-only event created successfully |

### Sync Flow Essentials

- Sheet ingestion maps headers → `EventRecord` validation (watch for logged skips).
- Duplicate detection keys on `title|date|time`; verify an existing row is skipped.
- Auto-ticket creation is enabled by default when `registration_type == "TICKETING"` and `ticket_price > 0`.
- Run `python sync_events.py sync --no-tickets` to confirm the opt-out path still logs the skip message.

### Follow-Up Checks

- `python dev_events.py list` → confirm newly created events.
- `python dev_events.py delete-pattern "Test" --confirm` → clean up fixtures.
- Review GitHub Actions (`ci.yml`) to ensure tests run in CI.

## Troubleshooting

### "WIX_API_KEY is required"
- Make sure `.env` file exists with proper credentials
- Run `python sync_events.py validate` to check setup

### "Event not found"
- Verify event ID is correct
- Use `python dev_events.py search` to find events

### Rate Limiting Errors
- Scripts include built-in delays
- For bulk operations, increase sleep time in code

### Wrong Site/Environment
- Check `ENV_MODE` in `.env`
- Verify `DEV_*` credentials are set if using development mode
- Client will print which mode it's using when initialized

## Next Steps

- Extend `wix_client.py` with more API endpoints as needed
- Create custom automation scripts using the client library
- Set up automated testing workflows
- Build integration tests for your event flows

## API Documentation

For more details on Wix Events API:
- [Wix Events API Docs](https://dev.wix.com/api/rest/wix-events/wix-events)
- [API Reference](https://www.wix.com/velo/reference/wix-events-v2)
