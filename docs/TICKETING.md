# TICKETING Events - Technical Documentation

Complete guide to creating ticketed events using the Wix Events V3 REST API.

## Overview

This document explains how to create TICKETING events (events that sell tickets) using Python and the Wix REST API. This approach creates event placeholders that show "Tickets are not on sale" until tickets are added manually via the Wix Dashboard.

## The Solution

### Critical Discovery

The Wix Events V3 REST API uses **different enum values** than the JavaScript SDK:

| API Type | Enum Value | Status |
|----------|------------|--------|
| REST API v3 | `"TICKETING"` | ✅ Works |
| JavaScript SDK | `"TICKETS"` | ✅ Works (SDK only) |
| REST API v3 | `"TICKETS"` | ❌ Fails with "value is required" |

### Working Code

```python
import requests

event_data = {
    'title': 'My Ticketed Event',
    'dateAndTimeSettings': {
        'dateAndTimeTbd': False,
        'startDate': '2025-10-14T12:00:00Z',
        'endDate': '2025-10-14T14:00:00Z',
        'timeZoneId': 'America/Toronto'
    },
    'location': {
        'type': 'VENUE',
        'address': {
            'formattedAddress': 'Test Location'
        }
    },
    'registration': {
        'initialType': 'TICKETING'  # ⚠️ MUST be "TICKETING" not "TICKETS"
    }
}

response = requests.post(
    'https://www.wixapis.com/events/v3/events',
    headers={
        'Authorization': API_KEY,
        'wix-site-id': SITE_ID,
        'Content-Type': 'application/json'
    },
    json={'event': event_data}
)

result = response.json()
print(f"Event created: {result['event']['id']}")
```

## API Response Behavior

### Creation Response (POST)

When you create a TICKETING event, the API returns the full registration configuration:

```json
{
  "event": {
    "id": "abc123...",
    "title": "My Ticketed Event",
    "registration": {
      "type": "TICKETING",
      "status": "CLOSED_AUTOMATICALLY",
      "initialType": "TICKETING",
      "registrationPaused": false,
      "registrationDisabled": false,
      "tickets": {
        "guestsAssignedSeparately": false,
        "ticketLimitPerOrder": 50,
        "reservationDurationInMinutes": 20,
        "gracePeriodInMinutes": 120,
        "checkoutType": "EVENTS_APP"
      },
      "rsvp": { ... }
    }
  }
}
```

### Get Event Response (GET)

**Important:** The registration field is **NOT included** in GET /events/{id} responses.

```json
{
  "event": {
    "id": "abc123...",
    "title": "My Ticketed Event",
    "status": "UPCOMING"
    // ❌ registration field is missing
  }
}
```

This is expected API behavior. The registration configuration is only returned during event creation.

## Workflow

### 1. Create TICKETING Event via API

```bash
python dev_events.py create "Concert 2025" 7 false TICKETS
```

**Result:**
- Event is created with `initialType: "TICKETING"`
- Event shows "Tickets are not on sale" on your Wix site
- Event is ready for tickets to be added

### 2. Add Tickets via Wix Dashboard

1. Open Wix Dashboard → Events
2. Click on your event
3. Click "Manage Tickets" button
4. Add ticket types with pricing
5. Tickets automatically go on sale when added

## Important Constraints

### Registration Type is Immutable

Once an event is created, the registration type **CANNOT** be changed:

- ❌ Cannot convert RSVP → TICKETING
- ❌ Cannot convert TICKETING → RSVP
- ✅ Must create new event with desired type

This is by design in the Wix Events API.

### Available Registration Types

| Type | Use Case | Can Add Tickets? |
|------|----------|------------------|
| `RSVP` | Free events with RSVP | ❌ No |
| `TICKETING` | Paid ticket events | ✅ Yes |
| `EXTERNAL` | External registration platform | ❌ No |
| `NO_REGISTRATION` | Display-only events | ❌ No |

## Troubleshooting

### Error: "initialType value is required"

**Cause:** Using `"TICKETS"` instead of `"TICKETING"`

**Solution:**
```python
# ❌ Wrong
'registration': {'initialType': 'TICKETS'}

# ✅ Correct
'registration': {'initialType': 'TICKETING'}
```

### Error: "Could not parse JSON"

**Cause:** Using proto/gRPC enum wrapper format

**Solution:**
```python
# ❌ Wrong
'registration': {'initialType': {'value': 'TICKETING'}}

# ✅ Correct
'registration': {'initialType': 'TICKETING'}
```

### Error: "initialType is unexpected"

**Cause:** Missing registration object entirely

**Solution:**
```python
# ❌ Wrong
event_data = {
    'title': 'My Event',
    'dateAndTimeSettings': { ... }
    # Missing registration
}

# ✅ Correct
event_data = {
    'title': 'My Event',
    'dateAndTimeSettings': { ... },
    'registration': {'initialType': 'TICKETING'}
}
```

## Automated Ticket Creation ✅

### Ticket Automation Now Available!

As of the latest update, this project **automatically creates tickets** for TICKETING events during Google Sheets sync.

### How It Works

When sync pushes a Ready/Update row from the Event Scheduling DB:

1. **Event Created** → TICKETING event with `initialType: "TICKETING"`
2. **Tickets Auto-Created** → named tickets from `Ticket Names/Prices/Capacities`
   when set, otherwise a single "Single Ticket" from `Ticket Price`/`Capacity`
3. **Tickets On Sale** → immediately available for purchase

### Ticket Creation Controls

Automatic ticketing only fires when **all** of these conditions are true:

1. `Registration Type` resolves to `TICKETING` (`TICKETS` is auto-normalised).
2. The row has `Ticket Names` (multi-ticket path) **or** a non-blank
   `Ticket Price` — including an explicit `0`, which creates a **free ticket**
   (Wix accepts a `fixedPrice` of `0` and marks the ticket free) so people
   can still register for free events. Only a genuinely blank price creates
   nothing.
3. You run `python sync_events.py sync` **without** the `--no-tickets` flag.

In practice a ticketed row can no longer reach sync with a blank price: the
enrich pass guarantees one via the fill hierarchy — template
`Default Ticket Names/Prices/Capacities` → template `Price Override` →
`CATEGORY_PRICING` by tag → the `default_ticket_price` Setting (seeded 30).
Blank ticket-entry capacities inherit the row `Capacity` (template
`Default Capacity` → `default_capacity` Setting).

If ticket creation fails anyway (API error), the event still publishes but
the row gets a **Sync Error** note ("Published but ticket creation failed —
no tickets are on sale…") instead of failing silently — fix the row and flip
Status to `Update` to retry.

#### Skip Ticket Creation

- **Per event:** rows pulled from Wix with no tickets keep a blank price and
  create nothing when flipped to `Update`. For a new row, use an RSVP
  registration type or `--no-tickets` — an explicit `0` price now means
  "create a free ticket", not "skip".
- **Entire run:** call `python sync_events.py sync --no-tickets`. The log will remind you to rerun without the flag when you are ready.

#### Mixed Rows Example

| Title | Price | Capacity | Registration | Auto tickets? |
|-------|-------|----------|--------------|----------------|
| Workshop A | `25.00` | `50` | `TICKETS` | ✅ Yes |
| Community Jam | `0` | `50` | `TICKETS` | ✅ Yes — free ticket |
| Wix-pulled row | *(blank)* | `50` | `TICKETS` | ❌ No (no price at all) |
| Webinar C | `15` | `200` | `RSVP` | ❌ No (registration) |

#### Command Reference

```bash
# Default behaviour (auto tickets when eligible)
python sync_events.py sync

# Skip ticket creation for this run
python sync_events.py sync --no-tickets
```

### Implementation

The ticket creation uses the Wix Ticket Definitions V3 API:

```python
from wix_client import WixClient

client = WixClient()

# Create TICKETING event first
event = client.create_event({
    'title': 'My Event',
    'dateAndTimeSettings': {...},
    'location': {...},
    'registration': {'initialType': 'TICKETING'}
})

# Automatically create ticket
ticket = client.create_ticket_definition(
    event_id=event['id'],
    ticket_name="General Admission",
    price=25.00,
    capacity=50
)
```

### API Payload Structure (V3)

The correct payload structure for Ticket Definitions V3:

```python
ticket_data = {
    "ticketDefinition": {
        "eventId": event_id,  # Required in body (not query param)
        "name": "General Admission",
        "pricingMethod": {  # Object format (not string)
            "fixedPrice": {
                "value": "25.00",
                "currency": "CAD"
            }
        },
        "feeType": "FEE_ADDED_AT_CHECKOUT",  # Buyer pays fees
        "initialLimit": 50  # Optional: total sellable tickets (inventory)
    }
}
```

### Global Ticket Policy Blurb (`policyText`)

Each ticket definition has a writable `policyText` field (max 1000 chars) —
the policy text shown with the ticket a buyer receives. Wix only offers it
per ticket per event, but the pipeline treats it as **one global setting**:
the `default_ticket_policy` row in the Notion Settings DB (seeded blank by
`setup-notion`).

- **New tickets**: every ticket definition the pipeline creates (single-price
  and multi-ticket alike) carries the blurb.
- **Existing tickets**: the update plan diffs `policyText` on an event's live
  ticket definitions whenever the event is diffed (a Ready row matching a
  live event, or a row flipped to `Update`) and patches any that drift.
- **Blank setting = not managed**: nothing is sent and hand-written policies
  in the dashboard are never touched (same semantics as a blank
  `Ticket Limit Per Order`).
- **Backfill**: `python scripts/apply_ticket_policy.py` previews (dry run by
  default) and `--apply` patches the blurb onto every ticket of every
  upcoming event — use it once after first setting the policy so tickets
  already on sale get it immediately.

#### Ticket Policy Status (read-only column)

The Event Scheduling DB has a code-owned **`Ticket Policy Status`** column
showing whether an event's live tickets carry the `default_ticket_policy`.
Humans never edit it; sync (Published refresh, Update/Ready pushes) and
`pull` write it:

- Blank — the Settings policy is blank (feature off) or the event has no
  tickets.
- `OK (3 tickets)` — every ticket definition's `policyText` matches.
- `2 of 3 tickets missing policy` / `1 of 2 tickets different policy` —
  drift, e.g. a ticket added or edited in the Wix dashboard. Flip the row to
  `Update` (or just wait for the next daily sync's diff) to converge it.

The status is not part of the row content hash, so dashboard-side policy
drift alone still gets written on the next refresh even when nothing else
changed.

### Checkout Limits: The Two `limit` Fields

Easy to confuse — only one of them is writable:

| Field | Where | Writable? | Meaning |
|-------|-------|-----------|---------|
| `registration.tickets.ticketLimitPerOrder` | Event (create/update event) | ✅ Yes (0–50) | Max tickets a buyer can purchase in one checkout. **Wix defaults it to 20 when never set.** |
| `limitPerCheckout` | Ticket definition | ❌ Read-only | Derived by Wix from remaining stock and the event-level limit. Sending it in a create request is silently ignored. |

The pipeline exposes the writable one as the **`Ticket Limit Per Order`**
column in the Event Scheduling DB (enrich fills blanks from the
`default_ticket_limit_per_order` setting, seeded at 4). It is sent inside
`registration.tickets` at event-creation time and patched via update-event
when an `Update` row changes it. A ticket definition's `initialLimit`
(the `Capacity` / `Ticket Capacities` columns) is total inventory, not a
per-order limit.

### Checkout Form: One Form Per Ticket or Per Order

Wix's `registration.tickets.guestsAssignedSeparately` boolean controls
whether the registration form must be filled out separately for **each
ticket** in an order (true) or once **per order** (false, the Wix default).
The pipeline exposes it as the **`Checkout Form`** select column in the
Event Scheduling DB:

| Select value | Wix `guestsAssignedSeparately` | Meaning |
|--------------|--------------------------------|---------|
| `PER_TICKET` | `true` | Every attendee fills their own form (per-ticket check-in) |
| `PER_ORDER` | `false` | One form for the whole order |
| *(blank)* | *(not sent)* | Not managed — the Wix dashboard setting is left alone |

Semantics mirror `Ticket Limit Per Order` exactly:

- **Create**: a non-blank value is sent inside `registration.tickets`.
- **Update**: the plan diffs it only when the row value is non-blank and
  patches via the same `registration.tickets` update.
- **Pull / Published refresh**: the live value is read back into the row
  (`PER_TICKET`/`PER_ORDER`), so Wix stays authoritative for Published rows.
- **Default**: the `default_checkout_form` Settings row (seeded blank = not
  managed). When set, enrich fills blank `Checkout Form` on ticketed rows.

### Key Discoveries

**API Endpoint:**
- ✅ `POST /events-ticket-definitions/v3/ticket-definitions`

**Required Fields:**
- `eventId` - Must be in request body (not query parameter)
- `pricingMethod` - Must be an object with nested pricing type (e.g., `fixedPrice`)
- `feeType` - Must be `"FEE_ADDED_AT_CHECKOUT"` (not `"BUYER_PAYS"`)

**Common Errors Resolved:**
- ❌ `"pricingMethod": "FIXED_PRICE"` → ✅ `"pricingMethod": {"fixedPrice": {...}}`
- ❌ `"feeType": "BUYER_PAYS"` → ✅ `"feeType": "FEE_ADDED_AT_CHECKOUT"`
- ❌ `eventId` in URL params → ✅ `eventId` in request body

### Graceful Failure Handling

If ticket creation fails, the event is still created successfully:

```
✅ Created event: My Event
   🎫 Creating ticket definition...
   ⚠️  Failed to create ticket (event still exists): API error
   💡 You can add tickets manually via Wix Dashboard
```

This ensures your events are never lost due to ticket creation issues. The
failure is also surfaced on the Notion row as a **Sync Error** note
("Published but ticket creation failed — no tickets are on sale. Check the
sync logs, then set Status to Update to retry."), so a published-but-unsellable
event is always visible in the Event Scheduling DB.

### Manual Ticket Creation (Still Supported)

You can still add tickets manually via Wix Dashboard:

1. Open Wix Dashboard → Events
2. Click on your event
3. Click "Manage Tickets" button
4. Add ticket types with pricing
5. Tickets automatically go on sale when added

This is useful for:
- ✅ Complex ticket configurations (early bird, VIP, etc.)
- ✅ Multiple ticket tiers
- ✅ Custom policies or descriptions
- ✅ Events that need manual review before going on sale

## Testing

### Test Ticket Automation

```bash
# Test end-to-end ticket automation
python test_ticket_automation.py
```

This script:
1. Creates a TICKETING event
2. Automatically creates a "General Admission" ticket ($25, capacity 50)
3. Verifies the event and ticket were created successfully

**Expected Output:**
```
✅ TEST PASSED - Ticket Automation Working!
```

### Test All Registration Formats

```bash
python test_registration_api.py
```

This script tests 10 different registration field formats to verify which works.

**Result:** Only `initialType: "TICKETING"` succeeds.

### Create Test Event (Manual)

```bash
# Create ticketed event with automatic ticket creation
python dev_events.py create "Test Ticket Event" 7 false TICKETS

# Verify event and ticket appear in Wix Dashboard
# Try purchasing a ticket to confirm it works
```

## Best Practices

1. **Use TICKETING for paid events** - Set at creation time
2. **Add tickets via Dashboard** - Simpler than API automation
3. **Plan event types before creation** - Cannot change registration type later
4. **Test on sandbox site first** - Use DEV_* environment variables
5. **Document ticket pricing** - Keep pricing info in Google Sheets comments

## References

- [Wix Events V3 API Documentation](https://dev.wix.com/docs/rest/business-solutions/events/events-v3/introduction)
- [Event Object Structure](https://dev.wix.com/docs/api-reference/business-solutions/events/events-v3/event-object)
- [Ticket Definitions API](https://dev.wix.com/docs/rest/business-solutions/events/ticket-definitions-v3/introduction)

## Summary

✅ **Solution Found:** Use `registration.initialType = "TICKETING"` (not "TICKETS")
✅ **Automated Workflow:** API creates event → API creates tickets → Tickets on sale immediately
✅ **Graceful Fallback:** If ticket creation fails, manual Dashboard option available
✅ **Simple:** Perfect for small business automation with end-to-end automation
✅ **Maintainable:** One language (Python), one approach (REST API), shared WixClient library
