# Changelog

All notable changes to this project will be documented in this file.

## [2025-10-07] - Code Cleanup & Fixes

### Fixed

- **RSVP Event Creation**: Fixed missing `registration.initialType` field for RSVP events
  - Previously only TICKETING events had registration field
  - Now all event types (RSVP, TICKETING, EXTERNAL, NO_REGISTRATION) properly include registration configuration
  - File: `dev_events.py`

- **TICKETING Event Support in sync_events.py**: Fixed incorrect conversion of TICKETS → RSVP
  - Changed to convert TICKETS → TICKETING (correct REST API enum value)
  - Events from Google Sheets with "TICKETS" registration type now create proper ticketed events
  - File: `sync_events.py`

### Deprecated

- **RSVP API Methods**: Marked RSVP creation methods as deprecated
  - The Wix Events RSVP v3 API endpoint (`/events/v3/rsvps`) returns 404 Not Found
  - RSVP v2 API may exist but documentation is unclear
  - Added deprecation warnings to all RSVP functions in `dev_tickets.py`:
    - `create_test_rsvp()` - Create single RSVP
    - `create_bulk_rsvps()` - Create multiple RSVPs
    - `list_event_rsvps()` - Query RSVPs
  - **Recommendation**: Use Wix Dashboard to manage RSVPs until API is clarified

### Added

- **UTF-8 Encoding Fix**: Added Windows console UTF-8 configuration to `dev_tickets.py`
  - Prevents emoji encoding errors on Windows
  - Matches implementation in `dev_events.py` and `sync_events.py`

### Documentation Updates

- Updated `DEV_TOOLS.md` with deprecation notice for RSVP API
- Updated `README.md` with clarification on registration types
- Updated `TICKETING.md` with complete technical guide
- Updated `.claude/claude.md` with solution documentation

## [2025-10-07] - TICKETING Events Solution

### Fixed

- **TICKETING Event Creation**: Discovered correct enum value for REST API
  - REST API v3 requires `initialType: "TICKETING"` (not "TICKETS")
  - JavaScript SDK uses `"TICKETS"` (different from REST API)
  - Created comprehensive test suite to verify all registration type formats
  - File: `dev_events.py`, `wix_client.py`

### Added

- **Complete TICKETING Documentation**: Created `TICKETING.md`
  - Explains REST API vs JavaScript SDK differences
  - Provides working code examples
  - Troubleshooting guide for common errors
  - Best practices for small business use

- **Python REST API Justification**: Added "Why Python REST API?" section to `README.md`
  - Explains decision to use Python over JavaScript SDK
  - Perfect for automated scripts with Google Sheets integration
  - Simpler for small business (<2000 customers)

### Changed

- **Event Creation Workflow**: Updated to support manual ticket creation
  - API creates TICKETING event → Shows "Tickets are not on sale"
  - User adds tickets via Wix Dashboard → Tickets go on sale
  - Removed complex automated ticket creation code (over-engineered)

## [Earlier] - Initial Implementation

### Added

- Google Sheets to Wix Events sync automation
- GitHub Actions workflow for daily sync
- Development tools for event CRUD operations
- Support for event images from Google Drive
- Duplicate detection to prevent re-creating events
