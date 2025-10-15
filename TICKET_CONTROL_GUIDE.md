# Ticket Creation Control Guide

## Overview

The sync system gives you **full control** over ticket creation. You can create events with or without tickets, and the system intelligently detects when tickets should be created based on your Google Sheets data.

## How Ticket Creation Works

### Automatic Detection (Default Behavior)

The system automatically creates tickets when **ALL** of these conditions are met:

1. ✅ Event has `registration_type = "TICKETING"` (Column K)
2. ✅ Event has `ticket_price > 0` (Column I)
3. ✅ Auto-ticket creation is enabled (default)

**Example Google Sheets Row:**

| A | B | C | D | E | F | G | H | I | J | K | L |
|---|---|---|---|---|---|---|---|---|---|---|---|
| Workshop 2024 | Workshop | 2025-10-14 | 14:00 | 2025-10-14 | 16:00 | Studio A | Learn... | 25.00 | 50 | TICKETS | url |

**Result:**
```
✅ Created event: Workshop 2024
   🎫 Creating ticket definition...
   ✅ Ticket created: $25.00 (capacity: 50)
```

### Skip Ticket Creation

If you want TICKETING events **without** automatic tickets:

**Option 1: Set price to 0 in Google Sheets**
```
Column I (ticket_price): 0 or leave empty
```

**Option 2: Use --no-tickets flag**
```bash
python sync_events.py sync --no-tickets
```

**Result:**
```
✅ Created event: Workshop 2024
   ℹ️  Ticket creation skipped (use --auto-tickets to enable)
   💡 Add tickets manually via Wix Dashboard
```

## Usage Examples

### 1. Full Automation (Default)

Create events and tickets automatically:

```bash
python sync_events.py sync
```

**Output:**
```
🚀 Starting Google Sheets → Wix Events sync...
🎫 Auto-ticket creation: ENABLED

✅ Created event: Workshop A
   🎫 Creating ticket definition...
   ✅ Ticket created: $25.00 (capacity: 50)

✅ Created event: Workshop B
   🎫 Creating ticket definition...
   ✅ Ticket created: $30.00 (capacity: 30)
```

### 2. Events Only (No Tickets)

Create events but skip ticket creation:

```bash
python sync_events.py sync --no-tickets
```

**Output:**
```
🚀 Starting Google Sheets → Wix Events sync...
🎫 Auto-ticket creation: DISABLED

✅ Created event: Workshop A
   ℹ️  Ticket creation skipped (use --auto-tickets to enable)
   💡 Add tickets manually via Wix Dashboard
```

### 3. Mixed Approach

**Google Sheets:**
- Row 1: `ticket_price = 25.00` → Will create ticket
- Row 2: `ticket_price = 0` → Will NOT create ticket
- Row 3: `ticket_price = (empty)` → Will NOT create ticket

```bash
python sync_events.py sync
```

**Output:**
```
✅ Created event: Workshop A
   🎫 Creating ticket definition...
   ✅ Ticket created: $25.00 (capacity: 50)

✅ Created event: Workshop B
   (no ticket creation message - price was 0)

✅ Created event: Workshop C
   (no ticket creation message - price was empty)
```

## Google Sheets Column Reference

| Column | Field | Required | Purpose |
|--------|-------|----------|---------|
| A | Event Name | Yes | Event title |
| B | Event Type | No | Category (Workshop, etc.) |
| C | Start Date | Yes | YYYY-MM-DD format |
| D | Start Time | Yes | HH:MM format |
| E | End Date | Yes | YYYY-MM-DD format |
| F | End Time | Yes | HH:MM format |
| G | Location | Yes | Venue address |
| H | Description | No | Event description |
| **I** | **Ticket Price** | **For tickets** | **e.g., 25.00** |
| **J** | **Capacity** | **For tickets** | **e.g., 50** |
| **K** | **Registration Type** | Yes | **TICKETS or RSVP** |
| L | Image URL | No | Google Drive link |

### Ticket Creation Rules

**Will create ticket:**
- Column I: `25.00` ✅
- Column J: `50` ✅
- Column K: `TICKETS` ✅

**Will NOT create ticket:**
- Column I: `0` or empty ❌
- Column K: `RSVP` ❌ (RSVP events don't support tickets)

## Command Reference

### Basic Commands

```bash
# Validate credentials
python sync_events.py validate

# Test Wix API connection
python sync_events.py test

# List existing events
python sync_events.py list

# Sync with automatic ticket creation (default)
python sync_events.py sync

# Sync without automatic ticket creation
python sync_events.py sync --no-tickets
```

### Help

```bash
python sync_events.py
# Shows complete usage information
```

## How It Matches Your Workflow

Based on your `test_ticket_automation.py` workflow:

### Step 1: Create Event
```python
client.create_event({
    'title': 'My Event',
    'registration': {'initialType': 'TICKETING'}
})
```
✅ This is what `sync_events.py` does first

### Step 2: Create Ticket
```python
client.create_ticket_definition(
    event_id=event_id,
    ticket_name="General Admission",
    price=25.00,
    capacity=50
)
```
✅ This is what `sync_events.py` does second (if enabled)

### Result
- Event exists with TICKETING type
- Ticket "General Admission" created
- Tickets on sale immediately

## Graceful Failure Handling

If ticket creation fails, the event still exists:

```
✅ Created event: Workshop 2024
   🎫 Creating ticket definition...
   ⚠️  Failed to create ticket (event still exists): API error
   💡 You can add tickets manually via Wix Dashboard
```

**Your data is never lost!**

## When to Use Each Option

### Use Default (Auto-Tickets)
- ✅ Simple ticketed events with one price
- ✅ Events with predictable pricing
- ✅ Bulk event creation
- ✅ Full automation workflow

### Use --no-tickets
- ✅ Complex ticket structures (multiple tiers)
- ✅ Events requiring manual review before sale
- ✅ Early bird pricing (add manually)
- ✅ VIP packages or custom configurations

### Use Empty Price in Sheets
- ✅ Mix of ticketed and non-ticketed events
- ✅ Some events need manual ticket setup
- ✅ Control on per-event basis

## Technical Details

### Code Flow

```python
# In sync_events.py

def create_wix_event(event, auto_create_tickets=True):
    # 1. Create event
    created_event = client.create_event(event_data)

    # 2. Check if should create ticket
    should_create_ticket = (
        auto_create_tickets and                    # Flag enabled
        event['registration_type'] == 'TICKETING'  # TICKETING event
        event.get('ticket_price', 0) > 0           # Price > 0
    )

    # 3. Create ticket if conditions met
    if should_create_ticket:
        client.create_ticket_definition(...)
```

### WixClient Method

The underlying API call:

```python
client.create_ticket_definition(
    event_id=event_id,
    ticket_name="General Admission",
    price=25.00,
    capacity=50,
    currency="USD"  # Optional, defaults to USD
)
```

**API Endpoint:** `POST /events-ticket-definitions/v3/ticket-definitions`

**Payload Structure:**
```python
{
    "ticketDefinition": {
        "eventId": "...",
        "name": "General Admission",
        "limitPerCheckout": 10,
        "pricingMethod": {
            "fixedPrice": {
                "value": "25.00",
                "currency": "USD"
            }
        },
        "feeType": "FEE_ADDED_AT_CHECKOUT",
        "capacity": 50
    }
}
```

## Summary

✅ **Default behavior:** Automatically creates tickets when price > 0
✅ **Full control:** Use `--no-tickets` flag to disable
✅ **Per-event control:** Set price to 0 in Google Sheets
✅ **Graceful failure:** Event exists even if ticket creation fails
✅ **Matches your workflow:** Create event → Create ticket
✅ **Production ready:** Tested and documented

You have **complete flexibility** to create events with or without tickets based on your needs!
