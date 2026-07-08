# Development Tools

Automation scripts for testing and development without needing to use the live site.

## Overview

The development toolkit includes:

- **event_sync/** - Modular package used by the sync CLI (config, runtime, orchestrator)
- **wix_client.py** - Reusable Python client for Wix APIs
- **dev_events.py** - Full CRUD operations for events (RSVP, TICKETING, EXTERNAL, NO_REGISTRATION)
- **dev_tickets.py** - Ticket management and search tools

⚠️ **Important Note:** RSVP creation via API appears to be deprecated. RSVP commands in `scripts/dev/dev_tickets.py` may not work. Use Wix Dashboard to manage RSVPs.

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
WIX_API_KEY=your_api_key
WIX_SITE_ID=your_dev_site_id       # keep this on the dev site while testing
WIX_ACCOUNT_ID=your_account_id     # optional; Site Media uploads

# Safety: declare which site is the dev site. Destructive dev commands
# (delete-*) refuse to run unless WIX_SITE_ID matches this value.
WIX_DEV_SITE_ID=your_dev_site_id
```

### 3. Logging & Verbosity

- Every `python sync_events.py ...` command accepts `--log-level` (DEBUG, INFO, WARNING, ERROR, CRITICAL).
- Example: `python sync_events.py sync --log-level DEBUG` prints detailed Sheets/Wix calls for troubleshooting.
- The CLI and orchestration modules emit logs via Python's `logging` module, so `LOGGING_LEVEL` environment tweaks or custom handlers work out of the box.

## Event Operations (dev_events.py)

Manage events programmatically without the Wix dashboard.

### List All Events

```bash
# List all events (default: 50)
python scripts/dev/dev_events.py list

# List with custom limit
python scripts/dev/dev_events.py list 100
```

### Get Event Details

```bash
# Get full event details including JSON
python scripts/dev/dev_events.py get <event_id>
```

### Create Events

```bash
# Create a draft RSVP event 7 days from now
python scripts/dev/dev_events.py create "My Test Event"

# Create event X days from now
python scripts/dev/dev_events.py create "Workshop" 14 true RSVP

# Create and publish immediately
python scripts/dev/dev_events.py create "Live Event" 3 false RSVP

# Create event for external registration
python scripts/dev/dev_events.py create "External Event" 7 true EXTERNAL

# Registration types: RSVP, TICKETS, EXTERNAL, NO_REGISTRATION
python scripts/dev/dev_events.py create "Concert" 7 false TICKETS  # Creates ticketed event
```

**TICKETS Event Workflow:**
1. API creates event with `TICKETING` registration type
2. Event shows "Tickets are not on sale" status
3. Add tickets manually via Wix Dashboard → Tickets go on sale

**Important:** Registration type is set at creation and **cannot be changed**. You cannot convert RSVP to TICKETING or vice versa.

### Update Events

```bash
# Update event title
python scripts/dev/dev_events.py update-title <event_id> "New Title"
```

### Publish Events

```bash
# Publish a draft event
python scripts/dev/dev_events.py publish <event_id>
```

### Delete Events

```bash
# Delete single event (requires confirmation)
python scripts/dev/dev_events.py delete <event_id> --confirm

# Delete all draft events
python scripts/dev/dev_events.py delete-drafts --confirm

# Delete all test events (title contains 'test')
python scripts/dev/dev_events.py delete-test --confirm

# Delete events matching a pattern
python scripts/dev/dev_events.py delete-pattern "Workshop" --confirm

# Delete only draft events matching pattern
python scripts/dev/dev_events.py delete-pattern "Concert" --confirm --drafts-only

# Using Makefile shortcuts (includes safety delays)
make dev-clean-drafts     # Delete all drafts (3 second delay)
make dev-clean-test       # Delete all test events (3 second delay)
make dev-clean-all        # Delete ALL events (5 second delay)
```

### Create Sample Events

```bash
# Create 5 sample events for testing (mix of RSVP and TICKETS)
python scripts/dev/dev_events.py create-samples

# Create custom number of samples
python scripts/dev/dev_events.py create-samples 20

# Sample events are prefixed with "-test-" for easy cleanup:
# - "-test- Workshop: Introduction to Python" (RSVP)
# - "-test- Networking Happy Hour" (TICKETS)
# - "-test- Tech Talk: Cloud Architecture" (RSVP)
# - "-test- Team Building Event" (RSVP)
# - "-test- Product Demo Session" (TICKETS)

# Clean up all sample events
python scripts/dev/dev_events.py delete-test --confirm
# or
make dev-clean-test
```

### Search Events

```bash
# Search for events by title
python scripts/dev/dev_events.py search "Workshop"
```

## Ticket Operations (dev_tickets.py)

Automate ticket purchases and RSVPs for testing.

### Create Single RSVP

```bash
# Create RSVP with default test user
python scripts/dev/dev_tickets.py rsvp <event_id>

# Create RSVP with custom details
python scripts/dev/dev_tickets.py rsvp <event_id> "John Doe" "john@example.com" 2
```

### Create Bulk RSVPs

```bash
# Create 10 test RSVPs
python scripts/dev/dev_tickets.py bulk-rsvp <event_id>

# Create custom number of RSVPs
python scripts/dev/dev_tickets.py bulk-rsvp <event_id> 50
```

### List RSVPs

```bash
# List all RSVPs for an event
python scripts/dev/dev_tickets.py list-rsvps <event_id>
```

### List Orders

```bash
# List all ticket orders for an event
python scripts/dev/dev_tickets.py list-orders <event_id>
```

### Search for Events

```bash
# Find event by title to get event_id
python scripts/dev/dev_tickets.py search-event "Workshop"
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
python scripts/dev/dev_events.py create "Test Workshop" 7 false

# 2. Get the event ID from output or search
python scripts/dev/dev_events.py search "Test Workshop"

# 3. Create bulk RSVPs for testing
python scripts/dev/dev_tickets.py bulk-rsvp <event_id> 25

# 4. Verify RSVPs were created
python scripts/dev/dev_tickets.py list-rsvps <event_id>

# 5. Clean up when done
python scripts/dev/dev_events.py delete <event_id> --confirm
```

### Load Testing

```bash
# Create 50 events
python scripts/dev/dev_events.py create-samples 50

# Create 100 RSVPs per event (use in a loop or script)
for event_id in $(python scripts/dev/dev_events.py list | grep "ID:" | awk '{print $2}'); do
    python scripts/dev/dev_tickets.py bulk-rsvp $event_id 100
done
```

### The dev-site guard

Destructive commands (`delete`, `delete-drafts`, `delete-test`,
`delete-pattern`, `delete-after-date`) only run when `WIX_SITE_ID` equals
`WIX_DEV_SITE_ID`. If the guard refuses:

- Confirm `.env` points `WIX_SITE_ID` at the dev site.
- Confirm `WIX_DEV_SITE_ID` is set to that same dev site id.

There is deliberately no override flag — deleting events on the production
site should require editing `.env` twice, on purpose.

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
| `RSVP` | `python scripts/dev/dev_events.py create "Test RSVP" 7 false RSVP` | Event includes `registration.initialType = "RSVP"` |
| `TICKETING` | `python scripts/dev/dev_events.py create "Test Ticket" 7 false TICKETS` | Event converted to `"TICKETING"`; tickets must exist or be added |
| `EXTERNAL` | `python scripts/dev/dev_events.py create "Test External" 7 false EXTERNAL` | Event published with external registration link placeholder |
| `NO_REGISTRATION` | `python scripts/dev/dev_events.py create "Test NoReg" 7 false NO_REGISTRATION` | Display-only event created successfully |

### Sync Flow Essentials

- Sheet ingestion maps headers → `EventRecord` validation (watch for logged skips).
- Duplicate detection keys on `title|date|time`; verify an existing row is skipped.
- Auto-ticket creation is enabled by default when `registration_type == "TICKETING"` and `ticket_price > 0`.
- Run `python sync_events.py sync --no-tickets` to confirm the opt-out path still logs the skip message.

### Config Round-Trip Smoke Tests

- `python sync_events.py pull-config` writes `config_events` + `config_events_last_pull` for every `UPCOMING`/`STARTED` event.
- `python sync_events.py push-config --dry-run` enumerates pending event/category/tax/ticket diffs without touching Wix.
- `python sync_events.py pull-categories` writes the slim 8-column `category_config` tab; rerun with `--scope all` to include past events (sorted future-first).
- `python sync_events.py push-categories --dry-run` lists every `assign`/`unassign` it would issue. With `--scope upcoming` (default), past-event rows are bucketed as `out_of_scope` and skipped — confirm by mixing one `ENDED` row in with `UPCOMING` rows.
- Regression: edit only a description column in `category_config` and run `push-categories`; verify zero API calls related to that event other than the read used for the diff.

### Follow-Up Checks

- `python scripts/dev/dev_events.py list` → confirm newly created events.
- `python scripts/dev/dev_events.py delete-pattern "Test" --confirm` → clean up fixtures.
- Review GitHub Actions (`ci.yml`) to ensure tests run in CI.

## Troubleshooting

### "WIX_API_KEY is required"
- Make sure `.env` file exists with proper credentials
- Run `python sync_events.py validate` to check setup

### "Event not found"
- Verify event ID is correct
- Use `python scripts/dev/dev_events.py search` to find events

### Rate Limiting Errors
- Scripts include built-in delays
- For bulk operations, increase sleep time in code

### Wrong Site/Environment
- Check `WIX_SITE_ID` in `.env` (should be the dev site while testing)
- The client logs the first 8 characters of the site id when initialized
- Destructive commands additionally require `WIX_DEV_SITE_ID` to match

## Next Steps

- Extend `wix_client.py` with more API endpoints as needed
- Create custom automation scripts using the client library
- Set up automated testing workflows
- Build integration tests for your event flows

## API Documentation

For more details on Wix Events API:
- [Wix Events API Docs](https://dev.wix.com/api/rest/wix-events/wix-events)
- [API Reference](https://www.wix.com/velo/reference/wix-events-v2)
