# Functionality Test Plan - Pre & Post Refactor

## Purpose
Ensure ZERO regression when refactoring sync_events.py to use wix_client.py

## Critical Functionality That MUST Keep Working

### 1. Event Creation - All Registration Types

| Registration Type | Current Behavior | Test Command | Expected Result |
|-------------------|------------------|--------------|-----------------|
| **RSVP** | Creates RSVP event | `python dev_events.py create "Test RSVP" 7 false RSVP` | Event created with `initialType: "RSVP"` |
| **TICKETING** | Creates ticketed event, shows "Tickets not on sale" | `python dev_events.py create "Test Ticket" 7 false TICKETS` | Event created with `initialType: "TICKETING"` |
| **EXTERNAL** | Creates external registration event | `python dev_events.py create "Test External" 7 false EXTERNAL` | Event created with `initialType: "EXTERNAL"` |
| **NO_REGISTRATION** | Creates display-only event | `python dev_events.py create "Test NoReg" 7 false NO_REGISTRATION` | Event created with `initialType: "NO_REGISTRATION"` |

### 2. Google Sheets Sync (sync_events.py)

| Functionality | Current Behavior | Must Work After Refactor |
|---------------|------------------|--------------------------|
| Read Google Sheet | Fetches events from Sheet1!A2:L100 | ✅ YES |
| Parse event data | Extracts all 12 columns | ✅ YES |
| Convert TICKETS → TICKETING | Changes "TICKETS" to "TICKETING" for REST API | ✅ YES (CRITICAL) |
| Duplicate detection | Checks existing events by title | ✅ YES |
| Image upload from Google Drive | Downloads & uploads event images | ✅ YES |
| Create events in Wix | Posts events to Wix API | ✅ YES |
| Batch processing | Creates multiple events in one run | ✅ YES |

### 3. Event Management (dev_events.py)

| Functionality | Test Command | Must Work |
|---------------|--------------|-----------|
| List events | `python dev_events.py list` | ✅ YES |
| Get event details | `python dev_events.py get <id>` | ✅ YES |
| Update event title | `python dev_events.py update-title <id> "New"` | ✅ YES |
| Publish event | `python dev_events.py publish <id>` | ✅ YES |
| Delete event | `python dev_events.py delete <id> --confirm` | ✅ YES |
| Delete pattern | `python dev_events.py delete-pattern "Test" --confirm` | ✅ YES |
| Delete drafts | `python dev_events.py delete-drafts --confirm` | ✅ YES |
| Search events | `python dev_events.py search "Workshop"` | ✅ YES |
| Create samples | `python dev_events.py create-samples 5` | ✅ YES |

### 4. Ticket Tools (dev_tickets.py)

| Functionality | Status | Must Work |
|---------------|--------|-----------|
| Search events | `python dev_tickets.py search-event "Test"` | ✅ YES |
| Add ticket (API) | `python dev_tickets.py add-ticket <id> "GA" 25 CAD` | ⚠️ May not work (complex API) |
| RSVP creation | `python dev_tickets.py rsvp <id>` | ⚠️ DEPRECATED (404 error) |
| List RSVPs | `python dev_tickets.py list-rsvps <id>` | ⚠️ DEPRECATED (404 error) |

**Note:** RSVP functionality already deprecated - not a regression if still broken

## Pre-Refactor Baseline Tests

### Test 1: TICKETING Event Creation (CRITICAL)

```bash
# Create TICKETING event
python dev_events.py create "Pre-Refactor TICKETING Test" 7 false TICKETS

# Expected output:
✅ Event created successfully!
   Title: Pre-Refactor TICKETING Test
   ID: <some-id>
   Status: UPCOMING

   ✅ Ticketed event created successfully!
   📋 Next steps to add tickets:
      1. Open Wix Dashboard → Events
      2. Click 'Manage Tickets' button
      ...
```

### Test 2: RSVP Event Creation

```bash
# Create RSVP event
python dev_events.py create "Pre-Refactor RSVP Test" 7 false RSVP

# Expected output:
✅ Event created successfully!
   Title: Pre-Refactor RSVP Test
   ID: <some-id>
   Status: UPCOMING
```

### Test 3: Google Sheets Sync

```bash
# Test sync (requires Google Sheet setup)
python sync_events.py validate  # Check credentials
python sync_events.py test      # Test Wix connection
python sync_events.py sync      # Run full sync

# Expected output:
✅ Found X events in Google Sheets
✅ Created Y new events
```

### Test 4: Event Operations

```bash
# List events
python dev_events.py list

# Search events
python dev_events.py search "Test"

# Delete test events
python dev_events.py delete-pattern "Test" --confirm
```

## Post-Refactor Regression Tests

Run ALL tests above again after refactoring. All must pass with identical results.

### Additional Tests After Refactor

1. **Retry Logic Test**
   - Temporarily break network
   - Verify retry with exponential backoff
   - Should NOT have worked pre-refactor, SHOULD work post-refactor

2. **Rate Limit Test**
   - Create 20 events rapidly
   - Verify 429 handling (if triggered)
   - Should fail pre-refactor, succeed post-refactor

3. **Dev Mode Test**
   - Set `ENV_MODE=development` in .env
   - Set `DEV_WIX_*` credentials
   - Verify scripts use dev credentials
   - Should NOT work for sync_events.py pre-refactor, SHOULD work post-refactor

## Critical Preservation Checklist

### sync_events.py Refactor

**Must Preserve:**
- ✅ Google Sheets reading (columns A-L)
- ✅ Image upload from Google Drive
- ✅ Duplicate detection logic
- ✅ TICKETS → TICKETING conversion
- ✅ Batch event creation
- ✅ Error handling and reporting
- ✅ All command-line arguments (validate, test, list, sync)

**Can Change:**
- ✅ How API requests are made (use wix_client.py)
- ✅ Header building (use wix_client._headers())
- ✅ Request retry logic (use wix_client._request())

**Must Add:**
- ✅ Retry logic (via wix_client)
- ✅ Rate limit handling (via wix_client)
- ✅ Dev/production mode (via wix_client)

## Sign-Off Criteria

Before considering refactor complete:

- [ ] All Pre-Refactor Baseline Tests pass
- [ ] All Post-Refactor Regression Tests pass with identical output
- [ ] TICKETING events still create correctly
- [ ] RSVP events still create correctly
- [ ] Google Sheets sync still works end-to-end
- [ ] Image upload from Google Drive still works
- [ ] No new errors or warnings
- [ ] Code is simpler/cleaner (fewer lines)
- [ ] Documentation updated

## Rollback Plan

If ANY test fails after refactor:
1. Git revert to pre-refactor state
2. Investigate failure
3. Fix issue
4. Re-run all tests
5. Only proceed when ALL tests pass
