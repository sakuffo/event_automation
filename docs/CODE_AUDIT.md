# Code Audit: Duplication & Architecture Analysis

## Current Architecture

### File Purposes

| File | Purpose | Uses wix_client.py? | Status |
|------|---------|---------------------|--------|
| **wix_client.py** | Core API client library | N/A (IS the client) | ✅ Clean |
| **dev_events.py** | Event CRUD operations | ✅ YES | ✅ Clean |
| **dev_tickets.py** | Ticket/RSVP tools | ✅ YES | ⚠️ Has deprecated methods |
| **sync_events.py** | Google Sheets → Wix sync | ❌ **NO** | ⚠️ **DUPLICATED CODE** |

## Code Duplication Issues

### 🔴 CRITICAL: sync_events.py Duplicates wix_client.py

`sync_events.py` reimplements the SAME functionality that already exists in `wix_client.py`:

#### Duplicated Functions

| Functionality | sync_events.py | wix_client.py | Lines Duplicated |
|---------------|----------------|---------------|------------------|
| List events | `list_wix_events()` | `list_events()` | ~30 lines |
| Test connection | `test_wix_connection()` | Built into `__init__` | ~20 lines |
| Create event | `create_wix_event()` | `create_event()` | ~40 lines |
| Upload image | Inside `create_wix_event()` | `upload_image()` | ~50 lines |
| Build headers | Inline in each function | `_headers()` | Repeated 5+ times |
| Query events | `check_existing_events()` | `list_events()` | ~30 lines |

**Total Duplicated Code: ~170+ lines**

### Specific Duplication Examples

#### 1. Headers - Repeated 5+ times in sync_events.py

```python
# sync_events.py - Repeated everywhere
headers={
    'Authorization': WIX_API_KEY,
    'wix-site-id': WIX_SITE_ID,
    'Content-Type': 'application/json'
}

# wix_client.py - DRY (Don't Repeat Yourself)
def _headers(self) -> Dict[str, str]:
    return {
        'Authorization': self.api_key,
        'wix-site-id': self.site_id,
        'Content-Type': 'application/json'
    }
```

#### 2. List Events - Duplicate Logic

```python
# sync_events.py lines 173-183
response = requests.post(
    f"{WIX_BASE_URL}/events/query",
    json={'query': {'paging': {'limit': 50}}},
    headers={
        'Authorization': WIX_API_KEY,
        'wix-site-id': WIX_SITE_ID,
        'Content-Type': 'application/json'
    }
)
response.raise_for_status()
return response.json().get('events', [])

# wix_client.py lines 134-139 - SAME LOGIC
response = self._request(
    'POST',
    '/events/v3/events/query',
    json={'query': query}
)
return response.json().get('events', [])
```

#### 3. Upload Image - Duplicate Logic

```python
# sync_events.py lines 308-338 (~30 lines)
response = requests.post(
    upload_url,
    json={'mimeType': mime_type, 'fileName': filename},
    headers={...}
)
upload_data = response.json()
upload_url = upload_data.get('uploadUrl')
upload_response = requests.put(...)
return upload_data.get('fileId')

# wix_client.py lines 254-277 - IDENTICAL
response = self._request(
    'POST',
    '/media-manager/v1/files/upload/url',
    json={'mimeType': mime_type, 'fileName': filename}
)
upload_data = response.json()
upload_url = upload_data.get('uploadUrl')
upload_response = requests.put(...)
return upload_data.get('fileId')
```

## Problems with Current Duplication

### 1. **Maintenance Burden**
- Bug fixes need to be applied in TWO places
- Features need to be added in TWO places
- Changes to Wix API require updating TWO files

### 2. **Inconsistency Risk**
- `wix_client.py` has retry logic with exponential backoff
- `sync_events.py` does NOT have retry logic
- `wix_client.py` handles rate limiting (429 errors)
- `sync_events.py` does NOT handle rate limiting

### 3. **Missing Features in sync_events.py**
- ❌ No retry on timeout
- ❌ No retry on connection error
- ❌ No rate limit handling
- ❌ No exponential backoff
- ❌ No dev/production mode switching

### 4. **Code Smell**
- Violates DRY (Don't Repeat Yourself) principle
- Makes codebase harder to understand
- Increases chance of bugs

## Recommended Refactor

### Option 1: Refactor sync_events.py to use wix_client.py ✅ RECOMMENDED

**Changes Required:**

```python
# OLD (sync_events.py lines 1-32)
import requests
# ... manual config ...

# NEW - Add one line
from wix_client import WixClient

# Initialize client
wix = WixClient()

# Replace all manual requests with:
wix.list_events()        # Instead of list_wix_events()
wix.create_event(data)   # Instead of create_wix_event()
wix.upload_image(...)    # Instead of inline image upload
```

**Benefits:**
- ✅ Remove ~170 lines of duplicated code
- ✅ Automatic retry logic
- ✅ Automatic rate limiting
- ✅ Dev/production mode support
- ✅ Single source of truth
- ✅ Easier to maintain

**Effort:** ~1-2 hours

### Option 2: Keep as-is (NOT RECOMMENDED)

**When this might make sense:**
- If sync_events.py needs to remain completely standalone
- If deploying without wix_client.py dependency

**Problems:**
- Still have all the duplication issues
- Still missing retry/rate-limit logic
- Higher maintenance cost

## Impact Analysis

### Files That Import wix_client.py

```
✅ dev_events.py
✅ dev_tickets.py
❌ sync_events.py  ← SHOULD import but doesn't
```

### Functions That Would Be Eliminated

If we refactor sync_events.py to use wix_client.py:

```python
# Can DELETE these from sync_events.py:
- test_wix_connection()      # Use: WixClient().__init__
- list_wix_events()          # Use: wix.list_events()
- check_existing_events()    # Use: wix.list_events() + search
- create_wix_event()         # Use: wix.create_event()
- Image upload logic         # Use: wix.upload_image()
- All header building        # Built into wix_client

# KEEP in sync_events.py (Google Sheets specific):
- validate_credentials()
- fetch_events_from_sheets()
- parse_google_drive_url()
- download_image_from_drive()
- sync_events()             # Main orchestration
```

## Recommendation Summary

### 🎯 Action Items (Priority Order)

1. **HIGH PRIORITY:** Refactor `sync_events.py` to use `wix_client.py`
   - Eliminates ~170 lines of duplicated code
   - Adds retry/rate-limit logic automatically
   - Single source of truth for API calls

2. **MEDIUM PRIORITY:** Consider removing deprecated RSVP methods
   - From `wix_client.py`
   - From `dev_tickets.py`
   - Only if confirmed permanently unavailable

3. **LOW PRIORITY:** Add type hints consistency
   - Some functions have type hints, some don't
   - Consider using mypy for type checking

### Why This Matters for Small Business

**Current state:**
- 3 separate scripts
- 2 ways of calling Wix API (wix_client + direct requests)
- ~170 lines of duplicated code
- Missing features in production sync script

**After refactor:**
- 3 scripts, but all using same client library
- 1 way of calling Wix API (wix_client)
- ~170 fewer lines to maintain
- Production sync gets retry/rate-limit for free

**Simple = Maintainable = Better for small business (<2000 customers)**

---

## ✅ REFACTOR COMPLETE (2025-10-07)

All action items completed successfully. See [REFACTOR_COMPLETE.md](REFACTOR_COMPLETE.md) for details.
