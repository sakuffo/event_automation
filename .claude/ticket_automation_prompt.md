# Ticket Automation Implementation Prompt

## Context

This Python-based event automation project syncs events from Google Sheets to Wix Events API. We recently completed a major refactor:

- **Refactored Architecture:** All scripts now use `wix_client.py` shared library (DRY principle)
- **TICKETING Events Work:** Successfully creating events with `initialType: "TICKETING"`
- **Current Workflow:** API creates TICKETING events → Shows "Tickets are not on sale" → User adds tickets manually via Dashboard
- **Goal:** Automate the ticket creation step to complete the end-to-end automation

## Project Structure

```
event_automation/
├── wix_client.py              # Core: Reusable Wix API client (ALL API calls go here)
├── dev_events.py              # CLI: Event CRUD operations
├── dev_tickets.py             # CLI: Ticket tools (ticket creation attempted but complex)
├── sync_events.py             # Production: Google Sheets → Wix sync
└── docs/
    ├── TICKETING.md          # TICKETING events technical guide
    └── DEV_TOOLS.md          # Development tools reference
```

## What We Know

### Current State

**TICKETING Event Creation - WORKS:**
```python
# In wix_client.py / dev_events.py / sync_events.py
event_data = {
    'title': 'My Event',
    'dateAndTimeSettings': {...},
    'location': {...},
    'registration': {
        'initialType': 'TICKETING'  # Creates ticketed event placeholder
    }
}
```

**Result:** Event created, shows "Tickets are not on sale" ✅

### Google Sheets Data Available

From `sync_events.py`, we have:
- Column I: `ticket_price` (e.g., "25.00")
- Column J: `capacity` (e.g., "30")
- Column K: `registration_type` (e.g., "TICKETS")

### Previous Ticket Creation Attempts

**Wix Ticket Definitions API v1** was attempted but complex:
```python
# From old code (removed for simplicity)
POST /events/v1/ticket-definitions
{
  "eventId": "event-id",
  "definition": {
    "name": "General Admission",
    "limitPerCheckout": 10,
    "pricingMethod": "FIXED_PRICE",
    "price": {
      "amount": "25.0",
      "currency": "CAD"
    },
    "feeConfig": {
      "type": "FEE_ADDED_AT_CHECKOUT",  # Complex fee configuration
      ...
    }
  }
}
```

**Issues encountered:**
- Complex fee configuration (who pays fees?)
- Tax handling
- Currency formatting
- Policy management
- Was removed to keep code simple for small business

## Your Task

**Research and implement automatic ticket creation for TICKETING events.**

### Requirements

1. **Research the Wix Ticket Definitions API**
   - Find official documentation (Wix Events API v1, v2, or v3)
   - Determine the correct endpoint and payload structure
   - Understand fee configuration options
   - Find simplest working approach

2. **Implement in wix_client.py**
   - Add a `create_ticket_definition()` method to WixClient class
   - Follow existing patterns (retry logic, error handling)
   - Keep it simple - use sensible defaults for small business
   - Use data from Google Sheets (ticket_price, capacity)

3. **Integrate into sync_events.py**
   - After creating TICKETING event, automatically create ticket
   - Use "General Admission" as default ticket name
   - Handle errors gracefully (if ticket creation fails, event still exists)
   - Only create tickets for events with `registration_type == 'TICKETING'`

4. **Test thoroughly**
   - Create test event with ticket
   - Verify ticket shows in Wix Dashboard
   - Verify ticket can be purchased
   - Ensure no breaking changes to existing functionality

### Constraints

- **Must use wix_client.py:** All API calls go through shared library
- **Simple defaults:** For small business (<2000 customers)
- **Graceful degradation:** If ticket creation fails, event should still exist
- **No breaking changes:** All existing functionality must continue working
- **Follow existing patterns:** Retry logic, error handling, dev/production mode

### Suggested Defaults

For simplicity, use these defaults:
```python
{
    "name": "General Admission",
    "limitPerCheckout": 10,
    "pricingMethod": "FIXED_PRICE",
    "price": {
        "amount": str(ticket_price),  # From Google Sheets
        "currency": "CAD"
    },
    "feeConfig": {
        "type": "FEE_ADDED_AT_CHECKOUT"  # Buyer pays fees (standard)
    }
}
```

### API Research Starting Points

1. **Wix Developer Documentation:**
   - https://dev.wix.com/docs/rest/business-solutions/events/ticket-definitions-v3/introduction
   - https://dev.wix.com/docs/rest/business-solutions/events/introduction

2. **Look for:**
   - Ticket Definitions API endpoint
   - Required vs optional fields
   - Fee configuration options
   - Simplest working payload structure
   - Code examples

3. **Test with:**
   - Real API calls (use dev credentials if available)
   - Check Wix Dashboard to verify tickets appear
   - Try purchasing a ticket to ensure it works

### Success Criteria

✅ Tickets automatically created for TICKETING events
✅ Tickets visible in Wix Dashboard
✅ Tickets can be purchased by customers
✅ Code follows existing patterns in wix_client.py
✅ No breaking changes to existing functionality
✅ Graceful error handling if ticket creation fails
✅ Documentation updated (add notes to docs/TICKETING.md)

### Files You'll Modify

1. **wix_client.py** - Add `create_ticket_definition()` method
2. **sync_events.py** - Call ticket creation after event creation
3. **docs/TICKETING.md** - Document the automated ticket creation

### Files for Reference

- **docs/TICKETING.md** - Previous ticket API research
- **dev_events.py** - See how TICKETING events are created
- **sync_events.py** - See where to integrate ticket creation
- **.claude/claude.md** - Complete project context

### Testing Checklist

- [ ] Research Wix Ticket Definitions API documentation
- [ ] Find simplest working payload structure
- [ ] Implement `create_ticket_definition()` in wix_client.py
- [ ] Add retry logic and error handling
- [ ] Integrate into sync_events.py
- [ ] Test creating event with ticket via Google Sheets
- [ ] Verify ticket appears in Wix Dashboard
- [ ] Try purchasing ticket to ensure it works
- [ ] Ensure existing functionality still works (RSVP events, etc.)
- [ ] Update documentation

## Important Notes

- **Don't over-engineer:** Simple is better for small business
- **Graceful failure:** Event should exist even if ticket creation fails
- **Use wix_client.py:** Maintain DRY architecture
- **Test thoroughly:** Ensure no regressions
- **Document findings:** Add API notes to docs/TICKETING.md

## Questions to Answer

1. What is the correct Wix Ticket Definitions API endpoint?
2. What is the minimal required payload?
3. How should fees be configured (buyer pays vs seller pays)?
4. What happens if ticket creation fails after event exists?
5. Can we update tickets if event already has "no tickets on sale"?

Good luck! This will complete the end-to-end automation for ticketed events.
