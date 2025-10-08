#!/usr/bin/env python3
"""
Wix Events + Google Sheets Sync
Simple script to sync events from Google Sheets to Wix Events API
"""

import json
import os
import re
import sys
import time
from typing import List, Dict, Any, Set
from io import BytesIO

import requests
from google.oauth2 import service_account
from googleapiclient.discovery import build
from dotenv import load_dotenv

# Fix Windows console encoding for emojis
if sys.platform == 'win32':
    sys.stdout.reconfigure(encoding='utf-8')
    sys.stderr.reconfigure(encoding='utf-8')

# Load environment variables
load_dotenv()

# Import Wix Client (refactored to use shared library)
from wix_client import WixClient

# Configuration
WIX_API_KEY = os.getenv('WIX_API_KEY')
WIX_ACCOUNT_ID = os.getenv('WIX_ACCOUNT_ID')
WIX_SITE_ID = os.getenv('WIX_SITE_ID')
GOOGLE_SHEET_ID = os.getenv('GOOGLE_SHEET_ID')
GOOGLE_CREDENTIALS = os.getenv('GOOGLE_CREDENTIALS')

# Constants
SHEET_RANGE = 'Sheet1!A2:L100'  # Updated to include image column (L)
TIMEZONE = 'America/Toronto'


def validate_credentials() -> bool:
    """Check if all required credentials are set"""
    print("🔍 Validating credentials and configuration...\n")

    checks = {
        'WIX_API_KEY': WIX_API_KEY,
        'WIX_ACCOUNT_ID': WIX_ACCOUNT_ID,
        'WIX_SITE_ID': WIX_SITE_ID,
        'GOOGLE_SHEET_ID': GOOGLE_SHEET_ID,
        'GOOGLE_CREDENTIALS': GOOGLE_CREDENTIALS
    }

    all_valid = True
    for name, value in checks.items():
        if not value:
            print(f"❌ {name} is missing")
            all_valid = False
        else:
            if name == 'GOOGLE_CREDENTIALS':
                try:
                    creds = json.loads(value)
                    if 'client_email' in creds:
                        print(f"✅ {name} is valid JSON")
                        print(f"   Service account: {creds['client_email']}")
                    else:
                        print(f"❌ {name} is invalid (missing client_email)")
                        all_valid = False
                except json.JSONDecodeError:
                    print(f"❌ {name} is not valid JSON")
                    all_valid = False
            else:
                print(f"✅ {name} is set")

    print()
    if all_valid:
        print("✅ All credentials are configured correctly!\n")
        print("Next steps:")
        print("  1. Run: python sync_events.py test")
        print("  2. Run: python sync_events.py sync")
    else:
        print("❌ Some credentials are missing or invalid. Check .env file.\n")

    return all_valid


def get_google_sheets_service():
    """Create Google Sheets API service"""
    try:
        creds_dict = json.loads(GOOGLE_CREDENTIALS)
        credentials = service_account.Credentials.from_service_account_info(
            creds_dict,
            scopes=['https://www.googleapis.com/auth/spreadsheets.readonly']
        )
        return build('sheets', 'v4', credentials=credentials)
    except Exception as e:
        print(f"❌ Failed to authenticate with Google Sheets: {e}")
        raise


def fetch_events_from_sheet() -> List[Dict[str, Any]]:
    """Fetch events from Google Sheet"""
    print("📊 Fetching events from Google Sheets...")

    service = get_google_sheets_service()

    try:
        result = service.spreadsheets().values().get(
            spreadsheetId=GOOGLE_SHEET_ID,
            range=SHEET_RANGE
        ).execute()

        rows = result.get('values', [])

        if not rows:
            print("No data found in spreadsheet.")
            return []

        events = []
        for row in rows:
            # Pad row to ensure we have all columns
            while len(row) < 12:
                row.append('')

            # Handle registration type (convert TICKETS → TICKETING for REST API)
            reg_type = row[10] or 'RSVP'
            if reg_type == 'TICKETS':
                print(f'   📋 Note: "{row[0]}" uses TICKETS - creating TICKETING event (add tickets via Dashboard)')
                reg_type = 'TICKETING'  # REST API uses "TICKETING" not "TICKETS"

            events.append({
                'name': row[0],
                'event_type': row[1],
                'start_date': row[2],  # YYYY-MM-DD
                'start_time': row[3],  # HH:MM
                'end_date': row[4],
                'end_time': row[5],
                'location': row[6],
                'description': row[7],
                'ticket_price': float(row[8]) if row[8] else 0.0,
                'capacity': int(row[9]) if row[9] else 100,
                'registration_type': reg_type,
                'image_url': row[11]  # Google Drive URL or file ID
            })

        print(f"Found {len(events)} events in spreadsheet\n")
        return events

    except Exception as e:
        print(f"❌ Error fetching from Google Sheets: {e}")
        raise


def test_wix_connection() -> bool:
    """Test connection to Wix API using WixClient"""
    try:
        # WixClient initialization already tests connection
        client = WixClient()
        # Try to list one event to verify API access
        client.list_events(limit=1)
        print("✅ Wix API connection successful!")
        return True
    except Exception as e:
        print(f"❌ Wix API connection failed: {e}")
        return False


def list_wix_events() -> List[Dict[str, Any]]:
    """List existing events in Wix using WixClient"""
    try:
        client = WixClient()
        events = client.list_events(limit=50)

        print("\n📅 Existing Events in Wix:\n")
        for event in events:
            start_date = event.get('dateAndTimeSettings', {}).get('startDate', 'No date')
            print(f"  • {event.get('title', 'Untitled')} - {start_date}")

        return events

    except Exception as e:
        print(f"❌ Failed to list events: {e}")
        return []


def get_existing_event_keys() -> Set[str]:
    """Get unique keys of existing events for duplicate detection using WixClient"""
    print("🔍 Checking for existing events in Wix...")

    try:
        client = WixClient()
        events = client.list_events(limit=100)

        existing_keys = set()
        for event in events:
            title = event.get('title', '')
            start_date = event.get('dateAndTimeSettings', {}).get('startDate', '')
            if start_date:
                date_part = start_date.split('T')[0]
                key = f"{title}|{date_part}"
                existing_keys.add(key)

        print(f"Found {len(existing_keys)} existing events\n")
        return existing_keys

    except Exception as e:
        print(f"Warning: Could not fetch existing events: {e}")
        return set()


def extract_google_drive_file_id(url: str) -> str:
    """Extract file ID from Google Drive URL"""
    patterns = [
        r'/file/d/([a-zA-Z0-9_-]+)',
        r'id=([a-zA-Z0-9_-]+)',
        r'^([a-zA-Z0-9_-]+)$'  # Just the ID
    ]

    for pattern in patterns:
        match = re.search(pattern, url)
        if match:
            return match.group(1)

    return None


def download_from_google_drive(file_id: str) -> tuple:
    """Download file from Google Drive and return (image_data, filename, mime_type)"""
    try:
        # Use the service account credentials to access Google Drive
        creds_dict = json.loads(GOOGLE_CREDENTIALS)
        credentials = service_account.Credentials.from_service_account_info(
            creds_dict,
            scopes=['https://www.googleapis.com/auth/drive.readonly']
        )

        drive_service = build('drive', 'v3', credentials=credentials)

        # Get file metadata
        file_metadata = drive_service.files().get(
            fileId=file_id,
            fields='name,mimeType'
        ).execute()

        # Download file content
        request = drive_service.files().get_media(fileId=file_id)
        file_data = BytesIO()

        downloader = request.execute()
        file_data.write(downloader)
        file_data.seek(0)

        return file_data.read(), file_metadata['name'], file_metadata['mimeType']

    except Exception as e:
        print(f"❌ Failed to download from Google Drive: {e}")
        return None, None, None


def upload_image_to_wix(image_url: str, event_name: str) -> str:
    """Upload an image from Google Drive to Wix and return the media ID"""
    if not image_url:
        return None

    try:
        # Extract Google Drive file ID
        file_id = extract_google_drive_file_id(image_url)
        if not file_id:
            print(f"⚠️  Invalid Google Drive URL: {image_url}")
            return None

        # Download from Google Drive
        print(f"📥 Downloading image from Google Drive for: {event_name}")
        image_data, filename, mime_type = download_from_google_drive(file_id)

        if not image_data:
            print(f"⚠️  Failed to download image for: {event_name}")
            return None

        # Ensure supported mime type
        if not mime_type.startswith('image/'):
            print(f"⚠️  Unsupported file type: {mime_type}")
            return None

        # Upload to Wix Media Manager using WixClient
        client = WixClient()
        wix_file_id = client.upload_image(image_data, filename, mime_type)

        print(f"✅ Uploaded image for: {event_name}")
        return wix_file_id

    except Exception as e:
        print(f"⚠️  Failed to upload image for {event_name}: {e}")
        return None


def create_wix_event(event: Dict[str, Any], auto_create_tickets: bool = True) -> bool:
    """
    Create an event in Wix using WixClient

    Args:
        event: Event data dictionary from Google Sheets
        auto_create_tickets: If True, automatically create tickets for TICKETING events
    """
    # Upload image if URL provided
    media_id = None
    if event.get('image_url'):
        media_id = upload_image_to_wix(event['image_url'], event['name'])

    # Build event data
    event_data = {
        'title': event['name'],
        'dateAndTimeSettings': {
            'dateAndTimeTbd': False,
            'startDate': f"{event['start_date']}T{event['start_time']}:00Z",
            'endDate': f"{event['end_date']}T{event['end_time']}:00Z",
            'timeZoneId': TIMEZONE
        },
        'location': {
            'type': 'VENUE',
            'address': {
                'formattedAddress': event['location']
            }
        },
        'registration': {
            'initialType': event['registration_type']
        }
    }

    # Add main image if uploaded successfully
    if media_id:
        event_data['mainImage'] = {
            'id': media_id
        }

    try:
        client = WixClient()
        created_event = client.create_event(event_data)
        event_id = created_event.get('id')

        print(f"✅ Created event: {event['name']}")

        # Automatically create tickets for TICKETING events if enabled
        should_create_ticket = (
            auto_create_tickets and
            event['registration_type'] == 'TICKETING' and
            event.get('ticket_price', 0) > 0
        )

        if should_create_ticket:
            try:
                print(f"   🎫 Creating ticket definition...")
                client.create_ticket_definition(
                    event_id=event_id,
                    ticket_name="General Admission",
                    price=event['ticket_price'],
                    capacity=event['capacity']
                )
                print(f"   ✅ Ticket created: ${event['ticket_price']:.2f} (capacity: {event['capacity']})")
            except Exception as ticket_error:
                print(f"   ⚠️  Failed to create ticket (event still exists): {ticket_error}")
                print(f"   💡 You can add tickets manually via Wix Dashboard")
        elif event['registration_type'] == 'TICKETING' and not auto_create_tickets:
            print(f"   ℹ️  Ticket creation skipped (use --auto-tickets to enable)")
            print(f"   💡 Add tickets manually via Wix Dashboard")

        return True

    except Exception as e:
        print(f"❌ Failed to create event {event['name']}: {e}")
        return False


def sync_events(auto_create_tickets: bool = True):
    """
    Main sync function

    Args:
        auto_create_tickets: If True, automatically create tickets for TICKETING events
    """
    print("🚀 Starting Google Sheets → Wix Events sync...\n")

    if auto_create_tickets:
        print("🎫 Auto-ticket creation: ENABLED")
    else:
        print("🎫 Auto-ticket creation: DISABLED")
    print()

    try:
        # Fetch events from Google Sheets
        events = fetch_events_from_sheet()

        # Get existing events to avoid duplicates
        existing_keys = get_existing_event_keys()

        # Track results
        results = {
            'success': [],
            'failed': [],
            'skipped': []
        }

        # Process each event
        print("📅 Creating new events in Wix...\n")

        for event in events:
            # Create unique key for duplicate detection
            event_key = f"{event['name']}|{event['start_date']}"

            # Skip if already exists
            if event_key in existing_keys:
                print(f"⏭️  Skipped: {event['name']} on {event['start_date']} (already exists)")
                results['skipped'].append(event['name'])
                continue

            # Create the event (with optional ticket creation)
            if create_wix_event(event, auto_create_tickets=auto_create_tickets):
                results['success'].append(event['name'])
            else:
                results['failed'].append(event['name'])

            # Rate limiting
            time.sleep(1)

        # Print summary
        print("\n📈 Sync Complete!\n")

        print(f"✅ Successfully created: {len(results['success'])} events")
        if results['success']:
            for name in results['success']:
                print(f"  • {name}")

        if results['skipped']:
            print(f"\n⏭️  Skipped (already exist): {len(results['skipped'])} events")
            for name in results['skipped']:
                print(f"  • {name}")

        if results['failed']:
            print(f"\n❌ Failed: {len(results['failed'])} events")
            for name in results['failed']:
                print(f"  • {name}")

        return len(results['failed']) == 0

    except Exception as e:
        print(f"Fatal error during sync: {e}")
        return False


def main():
    """Main entry point"""
    if len(sys.argv) < 2:
        print("""
Wix Events + Google Sheets Integration

Usage:
  python sync_events.py validate       - Validate all credentials
  python sync_events.py test           - Test Wix API connection
  python sync_events.py list           - List existing events in Wix
  python sync_events.py sync           - Sync events from Google Sheets to Wix
  python sync_events.py sync --no-tickets  - Sync without auto-creating tickets

Ticket Creation:
  By default, tickets are automatically created for TICKETING events that have
  a ticket_price > 0 in the Google Sheets (Column I).

  Use --no-tickets flag to disable automatic ticket creation:
    python sync_events.py sync --no-tickets

  When disabled, TICKETING events are created but you'll need to add tickets
  manually via the Wix Dashboard.

Setup:
1. Create a .env file with your credentials
2. Install dependencies: pip install -r requirements.txt
3. Run the sync: python sync_events.py sync
""")
        sys.exit(0)

    command = sys.argv[1]

    # Parse flags
    auto_tickets = '--no-tickets' not in sys.argv

    try:
        if command == 'validate':
            success = validate_credentials()
            sys.exit(0 if success else 1)

        elif command == 'test':
            if not validate_credentials():
                sys.exit(1)
            success = test_wix_connection()
            sys.exit(0 if success else 1)

        elif command == 'list':
            if not validate_credentials():
                sys.exit(1)
            list_wix_events()
            sys.exit(0)

        elif command == 'sync':
            if not validate_credentials():
                sys.exit(1)
            success = sync_events(auto_create_tickets=auto_tickets)
            sys.exit(0 if success else 1)

        else:
            print(f"Unknown command: {command}")
            sys.exit(1)

    except KeyboardInterrupt:
        print("\n\nOperation cancelled by user")
        sys.exit(1)
    except Exception as e:
        print(f"\n❌ Error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()