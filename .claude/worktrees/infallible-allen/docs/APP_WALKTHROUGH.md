# Event Automation App - Full Walkthrough

## Who This Document Is For

You're a solution architect learning Python, inheriting a vibe-coded app that handles one of the most important operational moments for Birdhaus: **publishing classes to the website**. This document explains what the app does, how it works, and maps Python concepts to system architecture concepts you already know.

---

## What Does This App Actually Do?

**One sentence:** It reads event data from a Google Sheet and creates/updates those events on your Wix website automatically.

Think of it as an **ETL pipeline** (Extract-Transform-Load):

1. **Extract** - Pull event rows from Google Sheets (the "source of truth")
2. **Transform** - Validate data, format descriptions as HTML, compress images, convert timezones
3. **Load** - Push events into Wix via their REST API

It also has a **generator** step that can run *before* the ETL: it merges two source tabs (`rolling_schedule` + `class_info`) into a ready-to-sync sheet tab.

```
                        GENERATOR (optional, Step 0)
                        ============================

  rolling_schedule tab          class_info tab           defaults tab
  (dates, times, instructors)   (descriptions, images)   (default_img, etc.)
         |                            |                        |
         +----------+  +-----------+  +                        |
                    |  |           |                            |
                    v  v           v                            v
              merge_event_data() -- applies pricing, HST, combines fields
                         |
                         v
              generated_events tab (or CSV to stdout)


                        SYNC (the main job)
                        ===================

  generated_events tab (Google Sheet)
         |
         v
  fetch_events() -- reads rows, validates with Pydantic
         |
         v
  get_existing_event_keys() -- queries Wix for what already exists
         |                      builds lookup key: "title|date|time"
         v
  For each event:
     Already exists + no changes?  --> SKIP
     Already exists + changes?     --> UPDATE via Wix API
     New event?                    --> CREATE via Wix API
                                       + upload image (Google Drive --> Wix Media)
                                       + create ticket definition (if ticketed)
                                       + optionally publish
```

---

## The Architecture (System Concepts You Already Know)

### It's a CLI Application, Not a Web Service

There's no server running. No Flask, no Django, no always-on process. This is a **batch job** -- a script you run, it does its work, it exits. Think of it like a cron job or a scheduled task.

**How it runs:**
- **Locally:** `python sync_events.py sync`
- **Automated:** GitHub Actions runs it daily at 9 AM EST (defined in `.github/workflows/sync-events.yml`)
- **On-demand:** You can trigger it manually from the GitHub Actions UI ("Run workflow" button)

### Service Accounts, Not User Auth

The app uses a **Google Service Account** (like a robot identity) instead of OAuth user login. This is the right pattern for server-to-server automation -- no browser popup, no token refresh dance. The service account JSON key lives in the `GOOGLE_CREDENTIALS` environment variable.

Wix uses a simpler **API Key** authentication model -- just a static key in the `WIX_API_KEY` env var.

### Environment-Driven Configuration

All secrets and settings come from environment variables (loaded from a `.env` file locally, or GitHub Secrets in CI). This is the [Twelve-Factor App](https://12factor.net/) pattern -- config lives outside the code.

---

## File-by-File Walkthrough

### Entry Points (the files you actually run)

| File | What It Does |
|------|-------------|
| `sync_events.py` | The main script. Just a 19-line wrapper that calls `event_sync.cli.main()`. Think of it as a thin "launcher". |
| `dev_events.py` | A developer tool for manually creating/deleting/listing events in Wix. Not used in production. |
| `dev_tickets.py` | A developer tool for managing tickets. Not used in production. |

### The Core Package: `event_sync/`

This is where the real logic lives. Python packages are just folders with an `__init__.py` file.

#### `cli.py` -- The Front Door

**What it does:** Parses command-line arguments and routes to the right function.

**Key concept -- argparse:** Python's built-in library for building CLIs. It defines what commands exist (`validate`, `test`, `list`, `sync`, `generate`, `prepare-sheet`) and what flags they accept (`--no-tickets`, `--publish`, `--log-level`).

**How it flows:**
```
User types: python sync_events.py sync --publish
                                   |       |
                                   v       v
                          args.command  args.publish
                                   |
                                   v
                          sync_events(runtime, auto_publish=True)
```

The `_ensure_command_config()` function is a **guard** -- it validates that the right env vars exist *before* the command runs, rather than failing halfway through.

#### `config.py` -- Configuration as a Data Structure

**What it does:** Reads environment variables into a Python `dataclass` called `AppConfig`.

**Key concept -- dataclass:** A Python class that's mostly just a container for data. Like a typed struct. Instead of passing around loose strings, the app passes one `AppConfig` object that holds all settings.

```python
@dataclass
class AppConfig:
    wix_api_key: Optional[str]      # From WIX_API_KEY env var
    wix_site_id: Optional[str]      # From WIX_SITE_ID env var
    google_sheet_id: Optional[str]  # From GOOGLE_SHEET_ID env var
    timezone: str = "America/Toronto"  # Hardcoded default
    ...
```

The `ensure_valid()` method is the **fail-fast pattern** -- if required config is missing, the app raises an error immediately instead of letting you discover it 10 API calls later.

#### `runtime.py` -- The Service Locator

**What it does:** Lazily creates and caches the API clients (Google Sheets, Google Drive, Wix).

**Key concept -- Lazy Initialization:** The clients aren't created when the app starts. They're created the first time something asks for them. If you run `validate` (which doesn't need API clients), no clients are created. This is the **lazy factory** pattern.

**Key concept -- Caching:** `SyncRuntime` also holds two in-memory caches:
- **Drive download cache** -- If the same image appears on 3 events, it downloads once
- **Wix upload cache** -- If the same image is uploaded, it reuses the media ID

This is important because API calls are slow and rate-limited. Caching avoids redundant network calls within a single run.

```python
class SyncRuntime:
    def get_wix_client(self):
        if self._wix_client is None:         # First time? Create it.
            self._wix_client = WixClient(...) # Cache it.
        return self._wix_client               # Return cached version.
```

#### `models.py` -- The Data Contract

**What it does:** Defines what a valid event looks like using Pydantic.

**Key concept -- Pydantic:** A library that validates data automatically. When you create an `EventRecord`, Pydantic checks every field:

```python
class EventRecord(BaseModel):
    name: str = Field(..., min_length=1)    # Must exist, can't be empty
    start_date: str                          # Gets validated + normalized
    ticket_price: float = 0.0                # Default is free
    registration_type: str = "RSVP"          # Default is RSVP
```

**The validators are the safety net.** They handle messy spreadsheet data:
- Dates in `MM/DD/YYYY` or `YYYY-MM-DD`? Both work -- normalized to `YYYY-MM-DD`
- Someone typed `TICKETS` instead of `TICKETING`? Auto-corrected
- Negative ticket price? Clamped to 0
- Empty optional fields? Converted to `None`

This is the **input validation at the boundary** pattern -- clean the data once, at the edge, so everything downstream can trust it.

#### `sheets.py` -- Reading the Spreadsheet

**What it does:** Fetches rows from Google Sheets and converts them into `EventRecord` objects.

**Key design choice -- Flexible headers:** The app doesn't require exact column names. It uses a mapping (`COLUMN_MAPPING` in `constants.py`) that says "the event name column could be called 'Event Name', 'Name', 'title', or 'event_name'". This makes the sheet more forgiving for non-technical users editing it.

**The flow:**
1. Fetch all rows from the configured sheet range
2. Read the first row as headers
3. Map headers to known field names (using `COLUMN_MAPPING`)
4. For each data row, extract fields by position and create an `EventRecord`
5. If a row fails validation, log the error and skip it (don't crash the whole run)

#### `orchestrator.py` -- The Brain

**What it does:** Coordinates the entire sync operation. This is the largest file (~640 lines) and the most important one.

**Key functions:**

| Function | Purpose |
|----------|---------|
| `sync_events()` | The main loop. For each sheet event: check if it exists in Wix, decide to create/update/skip |
| `get_existing_event_keys()` | Queries ALL events from Wix, builds a lookup dict keyed by `"title|date|time"` |
| `needs_update()` | Compares a sheet event against its Wix counterpart field-by-field |
| `create_wix_event()` | Uploads image + creates event + creates ticket + optionally publishes |
| `update_wix_event()` | Uploads image (if changed) + updates event |
| `format_description_as_html()` | Converts plain text from Sheets into HTML (paragraphs, bullet lists) |
| `_wix_timestamp()` | Converts local time to UTC for the Wix API |

**The duplicate detection is the key design decision.** The app builds a composite key: `{title}|{start_date}|{start_time}`. If this key already exists in Wix, it's a duplicate. This means:
- Same class on different dates = different events (correct)
- Same class on the same date = duplicate (skip or update)
- The key is timezone-aware: Wix stores UTC, so the app converts back to local time for comparison

**Rate limiting:** There's a `time.sleep(1)` between each event creation. Wix's API will return 429 (Too Many Requests) if you go too fast. The 1-second pause is a simple throttle.

#### `generator.py` -- The Data Prep Step

**What it does:** Merges two "source" tabs into the format the sync expects.

**Why it exists:** The business workflow is:
1. Someone maintains a `rolling_schedule` tab (when classes happen, who teaches)
2. Someone maintains a `class_info` tab (descriptions, images, taglines)
3. The generator **joins** these two datasets (like a SQL JOIN on class name) and outputs the combined data

**Business logic baked in:**
- **Pricing** comes from a category lookup table (`CATEGORY_PRICING` in `constants.py`), not from the sheet
- **HST (13% tax)** is multiplied onto the base price automatically
- **Instructor names** are built from `instructor` + `model` columns (e.g., "Alice & Bob")
- **Default image** is used when a class has no image (from the `defaults` tab)
- **Placeholder events** like `[No Class]`, `[TBD]`, `N/A` are automatically filtered out

#### `images.py` -- Image Pipeline

**What it does:** Downloads images from Google Drive, compresses them if needed, uploads to Wix Media.

**Why compression matters:** Wix has a 25MB upload limit. The app uses Pillow (Python's image library) to progressively shrink images:
1. Try original size at quality 90, 85, 80...
2. If still too big, scale down to 90%, 80%, 70%... of original dimensions
3. Each combination of scale + quality is tried until it fits under 25MB
4. If nothing works, the image is skipped (event still gets created, just without an image)

This is the **graceful degradation** pattern -- partial success is better than total failure.

#### `constants.py` -- Business Rules as Code

**What it does:** Holds hardcoded values that define business behavior:
- Category pricing table
- Default location (the physical address)
- Default capacity (24)
- Default registration type (TICKETS)
- HST multiplier (1.13)
- Column name mappings
- Max image size (25MB)

**Important:** If class pricing changes, the address changes, or capacity limits change, this is where you update it.

### The External Client: `wix_client.py`

**What it does:** Wraps all Wix REST API calls into Python methods.

**Key concept -- REST Client Wrapper:** Instead of writing raw HTTP calls everywhere, the app centralizes them in one class. Every Wix interaction goes through `WixClient`:

```python
client = WixClient(api_key="...", site_id="...")
client.create_event({...})     # POST /events/v3/events
client.update_event(id, {...}) # POST /events/v3/events/{id}
client.upload_image(bytes, filename, mime_type)
client.create_ticket_definition(event_id, name, price, capacity)
```

**Resilience features:**
- **Retry logic** with exponential backoff (1s, 2s, 4s waits) for rate limits and timeouts
- **Pagination** via `_paged_post()` -- Wix returns events in pages of 100; this iterator handles fetching all pages automatically
- **Dev/Prod mode** -- Can switch credentials via `ENV_MODE=development`

---

## How the Data Flows End-to-End

Here's the complete journey of one event, from spreadsheet to website:

```
1. GOOGLE SHEET (rolling_schedule tab)
   Row: "March | 15 | 3/15/2025 | 19:00 | 21:00 | Complex Harnesses | Chest Harness | Alice | Bob | ..."

2. GOOGLE SHEET (class_info tab)
   Row: "Chest Harness | Complex Harnesses | https://drive.google.com/... | | Learn the chest harness | ..."

3. GENERATOR (merge_event_data)
   - Joins on class name "Chest Harness"
   - Looks up "Complex Harnesses" -> $35 base price
   - Applies HST: $35 * 1.13 = $39.55
   - Sets location to "1233R Queen St W..."
   - Builds description: "Instructors: Alice & Bob\n\nLearn the chest harness"
   - Writes to generated_events tab

4. SYNC (fetch_events)
   - Reads generated_events tab
   - Creates EventRecord with validated fields
   - Normalizes date to 2025-03-15, time stays 19:00

5. SYNC (get_existing_event_keys)
   - Queries Wix: "Give me all events"
   - Builds lookup: {"Chest Harness|2025-03-15|19:00": {id: "abc123", event: {...}}}

6. SYNC (decision)
   - Key "Chest Harness|2025-03-15|19:00" not found? CREATE
   - Found but description changed? UPDATE
   - Found and identical? SKIP

7. CREATE PATH:
   a. Download image from Google Drive (if URL provided)
   b. Compress if > 25MB
   c. Upload to Wix Media -> get file descriptor with media ID
   d. Convert 19:00 America/Toronto to UTC -> 2025-03-16T00:00:00Z
   e. Build JSON payload with all event fields
   f. POST to /events/v3/events -> event created as DRAFT
   g. POST to /events-ticket-definitions/v3/ticket-definitions -> ticket at $39.55 CAD
   h. If --publish: POST to /events/v3/events/{id}/publish

8. WIX WEBSITE
   Event appears (as draft or published) with image, description, pricing, and capacity
```

---

## How to Run It

### Daily Production Sync

```bash
# Two-step process:
# Step 1: Generate the events sheet from source tabs
python sync_events.py prepare-sheet -m april

# Step 2: Sync to Wix
python sync_events.py sync --publish
```

Or use Make shortcuts:
```bash
make sync       # Runs sync without --publish (creates drafts)
```

### Debugging / Checking State

```bash
python sync_events.py validate   # Check all env vars are set
python sync_events.py test       # Verify Wix API connectivity
python sync_events.py list       # Show what events exist in Wix
python sync_events.py sync --log-level DEBUG  # Verbose output
```

### The GitHub Actions Automation

The `.github/workflows/sync-events.yml` file runs the sync daily. It:
1. Checks out the repo
2. Installs Python + dependencies
3. Sets env vars from GitHub Secrets
4. Runs `python sync_events.py sync`

---

## Key Python Concepts Used in This App

| Concept | Where It's Used | What It Does |
|---------|----------------|-------------|
| **Packages** (`__init__.py`) | `event_sync/` folder | Groups related modules together; the `__init__.py` makes it importable |
| **Dataclasses** | `config.py: AppConfig` | Typed data containers (like a struct/record) |
| **Pydantic Models** | `models.py: EventRecord` | Data validation + normalization at the boundary |
| **Type Hints** | Everywhere (`Optional[str]`, `Dict[str, Any]`, `List[EventRecord]`) | Documentation + IDE support, not enforced at runtime |
| **Decorators** (`@field_validator`) | `models.py` | Functions that modify other functions; Pydantic uses them for custom validation |
| **Properties** (`@property`) | `config.py: google_credentials` | Methods that look like attributes; used for lazy parsing |
| **Generators/Iterators** (`yield`) | `wix_client.py: _paged_post()` | Produce items one-at-a-time instead of loading all into memory |
| **Context managers** (try/except) | Throughout | Error handling; the app prefers logging + skipping over crashing |
| **f-strings** | Throughout | String interpolation: `f"Found {count} events"` |
| **Dictionary comprehensions** | `sheets.py`, `orchestrator.py` | Build dicts in one expression |
| **`from __future__ import annotations`** | Top of every module | Allows forward references in type hints (Python 3.7+ compatibility) |

---

## What to Watch Out For

### Things That Can Break

1. **Google credentials expire or get revoked** -- The service account key doesn't expire, but if someone deletes the service account in Google Cloud Console, everything stops
2. **Wix API changes** -- The app uses Wix Events API v3. If Wix deprecates endpoints, the `wix_client.py` calls will start failing
3. **Sheet structure changes** -- If someone renames columns to something not in `COLUMN_MAPPING`, those fields will be silently empty
4. **Category pricing not updated** -- New class categories default to $30 if not in `CATEGORY_PRICING`
5. **Timezone issues** -- The app assumes `America/Toronto`. If Birdhaus operates in a different timezone, this needs updating in `config.py`

### The "Vibe Coded" Artifacts

Things that smell like they were generated and may need cleanup:
- `CATEGORY_PRICING` has duplicate entries with different capitalizations (e.g., "Simple harnesses" and "Simple Harnesses") -- works but messy
- The `wix_client.py` lives at the repo root instead of inside the `event_sync` package -- architectural inconsistency
- Some error messages have emoji that may not render in all terminals/log systems

---

## Cost

This app costs $0/month to run:
- Google Sheets/Drive API: Free tier
- Wix Events API: Included with Wix subscription
- GitHub Actions: Free tier covers daily runs easily (~15 min/month used)

---

## Quick Reference: Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `WIX_API_KEY` | Yes | Wix API key for authentication |
| `WIX_SITE_ID` | Yes | Your Wix site ID |
| `WIX_ACCOUNT_ID` | Yes (for images) | Your Wix account ID |
| `GOOGLE_SHEET_ID` | Yes | The Google Sheet ID (from the URL) |
| `GOOGLE_CREDENTIALS` | Yes | Full service account JSON, on one line |
| `SOURCE_SHEET_ID` | No | Separate sheet for generator source data |
| `ENV_MODE` | No | `production` (default) or `development` |
