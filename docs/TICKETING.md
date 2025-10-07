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

## Why Manual Ticket Creation?

### Ticket Definitions API is Complex

The Wix Ticket Definitions API requires:
- Complex fee configuration (FEE_ADDED_AT_CHECKOUT vs FEE_INCLUDED)
- Detailed pricing structures
- Currency and tax handling
- Policy management

### Dashboard is Simpler

For a small business (<2000 customers):
- ✅ Visual interface for pricing
- ✅ Easy to modify tickets
- ✅ No API complexity
- ✅ Better for non-technical staff

### Automated Ticket Creation (If Needed)

If you need to automate ticket creation in the future:

```python
# Ticket Definitions API v1 (complex)
ticket_data = {
    'eventId': event_id,
    'definition': {
        'name': 'General Admission',
        'limitPerCheckout': 10,
        'pricingMethod': 'FIXED_PRICE',
        'price': {
            'amount': '25.0',
            'currency': 'USD'
        },
        'feeConfig': {
            'type': 'FEE_ADDED_AT_CHECKOUT',  # Buyer pays fees
            'feeAmount': {
                'amount': '2.0',
                'currency': 'USD'
            }
        }
    }
}

response = requests.post(
    'https://www.wixapis.com/events/v1/ticket-definitions',
    headers={'Authorization': API_KEY, 'wix-site-id': SITE_ID},
    json=ticket_data
)
```

**Note:** This is intentionally not implemented to keep the codebase simple.

## Testing

### Test All Registration Formats

```bash
python test_registration_api.py
```

This script tests 10 different registration field formats to verify which works.

**Result:** Only `initialType: "TICKETING"` succeeds.

### Create Test Event

```bash
# Create ticketed event placeholder
python dev_events.py create "Test Ticket Event" 7 false TICKETS

# Verify event shows "Tickets are not on sale" on your Wix site
# Add tickets via Dashboard
# Verify tickets go on sale
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
✅ **Workflow:** API creates event → Dashboard adds tickets
✅ **Simple:** Perfect for small business automation
✅ **Maintainable:** One language (Python), one approach (REST API)
