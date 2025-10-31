#!/usr/bin/env python3
"""
Development Event Operations
Full CRUD operations for events without needing the live site
"""

import sys
import json
from datetime import datetime, timedelta
from typing import Dict, Any, Optional

# Fix Windows console encoding for emojis
if sys.platform == 'win32':
    import codecs
    sys.stdout.reconfigure(encoding='utf-8')
    sys.stderr.reconfigure(encoding='utf-8')

from wix_client import WixClient


def list_all_events(client: WixClient, limit: int = 50):
    """List all events"""
    print(f"📋 Fetching events (limit: {limit})...\n")

    try:
        events = client.list_events(limit=limit)

        if not events:
            print("No events found.")
            return

        print(f"Found {len(events)} event(s):\n")
        for i, event in enumerate(events, 1):
            title = event.get('title', 'Untitled')
            event_id = event.get('id', 'N/A')
            start = event.get('dateAndTimeSettings', {}).get('startDate', 'N/A')
            status = event.get('status', 'UNKNOWN')

            print(f"{i}. {title}")
            print(f"   ID: {event_id}")
            print(f"   Start: {start}")
            print(f"   Status: {status}")
            print()

    except Exception as e:
        print(f"❌ Failed to fetch events: {e}")


def get_event_details(client: WixClient, event_id: str):
    """Get detailed information about an event"""
    print(f"📄 Fetching event details for {event_id}...\n")

    try:
        event = client.get_event(event_id)

        if not event:
            print("Event not found.")
            return

        print("Event Details:")
        print("=" * 50)
        print(f"Title: {event.get('title')}")
        print(f"ID: {event.get('id')}")

        date_settings = event.get('dateAndTimeSettings', {})
        print(f"Start: {date_settings.get('startDate', 'N/A')}")
        print(f"End: {date_settings.get('endDate', 'N/A')}")
        print(f"Timezone: {date_settings.get('timeZoneId', 'N/A')}")

        location = event.get('location', {})
        if location:
            print(f"Location: {location.get('address', {}).get('formattedAddress', 'N/A')}")

        registration = event.get('registration', {})
        if registration:
            print(f"Registration Initial Type: {registration.get('initialType', 'N/A')}")
            print(f"Registration Current Type: {registration.get('type', 'N/A')}")
            print(f"Registration Status: {registration.get('status', 'N/A')}")
        else:
            print(f"Registration: Not configured")

        print(f"Event Status: {event.get('status', 'UNKNOWN')}")

        # Show full JSON if needed
        print("\n" + "=" * 50)
        print("Full JSON:")
        print(json.dumps(event, indent=2))

    except Exception as e:
        print(f"❌ Failed to fetch event: {e}")


def create_test_event(client: WixClient, title: str, days_from_now: int = 7,
                     duration_hours: int = 2, location: str = "Test Location",
                     draft: bool = True, registration_type: str = 'RSVP') -> Optional[Dict[str, Any]]:
    """
    Create a test event

    Args:
        registration_type: RSVP, TICKETS, EXTERNAL, or NO_REGISTRATION
    """
    print(f"Creating test event: {title}...")
    print(f"Registration type: {registration_type}")

    # Calculate dates
    start = datetime.now() + timedelta(days=days_from_now)
    end = start + timedelta(hours=duration_hours)

    # Create TICKETING events (shows "Tickets are not on sale" until tickets added via Dashboard)
    create_tickets = (registration_type == 'TICKETS')

    if create_tickets:
        print("   📋 Creating TICKETING event (tickets must be added via Dashboard)...")

    event_data = {
        'title': title,
        'dateAndTimeSettings': {
            'dateAndTimeTbd': False,
            'startDate': start.strftime('%Y-%m-%dT%H:%M:%S') + 'Z',
            'endDate': end.strftime('%Y-%m-%dT%H:%M:%S') + 'Z',
            'timeZoneId': 'America/Toronto'
        },
        'location': {
            'type': 'VENUE',
            'address': {
                'formattedAddress': location
            }
        }
    }

    # Add registration type (required for all events)
    # REST API requires "TICKETING" not "TICKETS" for ticketed events
    if create_tickets:
        event_data['registration'] = {
            'initialType': 'TICKETING'
        }
    elif registration_type == 'RSVP':
        event_data['registration'] = {
            'initialType': 'RSVP'
        }
    elif registration_type == 'EXTERNAL':
        event_data['registration'] = {
            'initialType': 'EXTERNAL'
        }
    elif registration_type == 'NO_REGISTRATION':
        event_data['registration'] = {
            'initialType': 'NO_REGISTRATION'
        }

    try:
        result = client.create_event(event_data)
        print(f"✅ Event created successfully!")
        print(f"   Title: {result.get('title')}")
        event_id = result.get('id')
        print(f"   ID: {event_id}")
        print(f"   Start: {result.get('dateAndTimeSettings', {}).get('startDate')}")

        # Show both what we requested and what API returned
        actual_status = result.get('status', 'UNKNOWN')
        requested_status = 'DRAFT' if draft else 'PUBLISHED'
        print(f"   Status: {actual_status} (requested: {requested_status})")

        if draft and actual_status != 'DRAFT':
            print(f"   ⚠️  WARNING: Requested DRAFT but API created {actual_status} event!")

        # Show next steps for ticketed events
        if create_tickets and event_id:
            print(f"\n   ✅ Ticketed event created successfully!")
            print(f"   📋 Next steps to add tickets:")
            print(f"      1. Open Wix Dashboard → Events → '{title}'")
            print(f"      2. Click 'Manage Tickets' button")
            print(f"      3. Add ticket types with pricing")
            print(f"      4. Tickets will automatically go on sale when added")

        return result
    except Exception as e:
        print(f"❌ Failed to create event: {e}")
        if hasattr(e, 'response') and e.response is not None:
            try:
                error_detail = e.response.json()
                print(f"   Error details: {json.dumps(error_detail, indent=2)}")
            except:
                print(f"   Response text: {e.response.text}")
        return None


def update_event_title(client: WixClient, event_id: str, new_title: str):
    """Update event title"""
    print(f"Updating event {event_id}...")

    try:
        event = client.get_event(event_id)
        event['title'] = new_title
        result = client.update_event(event_id, event)

        print(f"✅ Event updated successfully!")
        print(f"   New Title: {result.get('title')}")
        return result
    except Exception as e:
        print(f"❌ Failed to update event: {e}")
        return None


def publish_event(client: WixClient, event_id: str):
    """Publish a draft event"""
    print(f"Publishing event {event_id}...")

    try:
        result = client.publish_event(event_id)
        print(f"✅ Event published successfully!")
        print(f"   Title: {result.get('title')}")
        print(f"   Status: {result.get('status', 'UNKNOWN')}")
        return result
    except Exception as e:
        print(f"❌ Failed to publish event: {e}")
        return None


def delete_event(client: WixClient, event_id: str, confirm: bool = False):
    """Delete an event"""
    if not confirm:
        print("⚠️  Are you sure you want to delete this event?")
        print("   Run with --confirm flag to delete")
        return

    print(f"Deleting event {event_id}...")

    try:
        success = client.delete_event(event_id)
        if success:
            print(f"✅ Event deleted successfully!")
        else:
            print(f"❌ Failed to delete event")
    except Exception as e:
        print(f"❌ Failed to delete event: {e}")


def bulk_delete_events(client: WixClient, pattern: str = None, drafts_only: bool = False,
                      confirm: bool = False):
    """Delete multiple events matching criteria"""
    print("🔍 Fetching events to delete...\n")

    all_events = client.list_events(limit=100, include_drafts=True)

    # Filter events
    events_to_delete = []
    for event in all_events:
        # Filter by pattern if provided
        if pattern and pattern.lower() not in event.get('title', '').lower():
            continue

        # Filter drafts only if specified
        if drafts_only and event.get('status') != 'DRAFT':
            continue

        events_to_delete.append(event)

    if not events_to_delete:
        print("No events found matching criteria.")
        return

    print(f"Found {len(events_to_delete)} event(s) to delete:\n")
    for i, event in enumerate(events_to_delete, 1):
        title = event.get('title', 'Untitled')
        event_id = event.get('id')
        status = event.get('status', 'UNKNOWN')
        print(f"{i}. {title} ({status})")
        print(f"   ID: {event_id}")

    print()

    if not confirm:
        print("⚠️  Add --confirm flag to proceed with deletion")
        return

    print("\n🗑️  Deleting events...\n")

    deleted = 0
    failed = 0

    for event in events_to_delete:
        event_id = event.get('id')
        title = event.get('title', 'Untitled')

        try:
            success = client.delete_event(event_id)
            if success:
                print(f"✅ Deleted: {title}")
                deleted += 1
            else:
                print(f"❌ Failed: {title}")
                failed += 1
        except Exception as e:
            print(f"❌ Failed: {title} - {e}")
            failed += 1

        # Rate limiting
        import time
        time.sleep(0.3)

    print(f"\n📊 Results: {deleted} deleted, {failed} failed")


def delete_all_drafts(client: WixClient, confirm: bool = False):
    """Delete all draft events"""
    bulk_delete_events(client, pattern=None, drafts_only=True, confirm=confirm)


def delete_test_events(client: WixClient, confirm: bool = False):
    """Delete all events with 'Test' in the title"""
    bulk_delete_events(client, pattern='test', drafts_only=False, confirm=confirm)


def delete_events_after_date(client: WixClient, cutoff_date: str, confirm: bool = False):
    """Delete all events starting on or after a specific date

    Args:
        client: WixClient instance
        cutoff_date: Date string in YYYY-MM-DD format
        confirm: If True, actually delete the events
    """
    from datetime import datetime

    print(f"🔍 Fetching events starting on or after {cutoff_date}...\n")

    # Parse cutoff date (make it timezone-aware UTC)
    try:
        from datetime import timezone
        cutoff = datetime.strptime(cutoff_date, '%Y-%m-%d').replace(tzinfo=timezone.utc)
    except ValueError:
        print(f"❌ Invalid date format. Use YYYY-MM-DD (e.g., 2026-01-01)")
        return

    all_events = client.list_events(limit=100, include_drafts=True)

    # Filter events by date
    events_to_delete = []
    for event in all_events:
        start_datetime = event.get('dateAndTimeSettings', {}).get('startDate', '')
        if not start_datetime:
            continue

        try:
            event_date = datetime.fromisoformat(start_datetime.replace('Z', '+00:00'))
            if event_date >= cutoff:
                events_to_delete.append(event)
        except Exception as e:
            print(f"⚠️  Could not parse date for event {event.get('id')}: {e}")
            continue

    if not events_to_delete:
        print(f"No events found starting on or after {cutoff_date}")
        return

    print(f"Found {len(events_to_delete)} event(s) to delete:\n")
    for i, event in enumerate(events_to_delete, 1):
        title = event.get('title', 'Untitled')
        event_id = event.get('id')
        start = event.get('dateAndTimeSettings', {}).get('startDate', 'N/A')
        status = event.get('status', 'UNKNOWN')
        print(f"{i}. {title}")
        print(f"   Start: {start}")
        print(f"   Status: {status}")
        print(f"   ID: {event_id}")
        print()

    if not confirm:
        print("⚠️  Add --confirm flag to proceed with deletion")
        return

    print("\n🗑️  Deleting events...\n")

    deleted = 0
    failed = 0

    for event in events_to_delete:
        event_id = event.get('id')
        title = event.get('title', 'Untitled')

        try:
            success = client.delete_event(event_id)
            if success:
                print(f"✅ Deleted: {title}")
                deleted += 1
            else:
                print(f"❌ Failed: {title}")
                failed += 1
        except Exception as e:
            print(f"❌ Failed: {title} - {e}")
            failed += 1

        # Rate limiting
        import time
        time.sleep(0.3)

    print(f"\n📊 Results: {deleted} deleted, {failed} failed")


def create_sample_events(client: WixClient, count: int = 5):
    """Create multiple sample events for testing"""
    print(f"\n📝 Creating {count} sample events...\n")

    event_types = [
        ("Workshop: Introduction to Python", "Conference Room A", "RSVP"),
        ("Networking Happy Hour", "Downtown Bar & Grill", "TICKETS"),
        ("Tech Talk: Cloud Architecture", "Main Auditorium", "RSVP"),
        ("Team Building Event", "City Park", "RSVP"),
        ("Product Demo Session", "Innovation Lab", "TICKETS")
    ]

    created = []
    for i in range(count):
        event_type = event_types[i % len(event_types)]
        title = f"-test- {event_type[0]} #{i+1}"
        location = event_type[1]
        reg_type = event_type[2]

        result = create_test_event(
            client,
            title=title,
            days_from_now=i+1,
            duration_hours=2,
            location=location,
            draft=True,
            registration_type=reg_type
        )

        if result:
            created.append(result)

        # Rate limiting
        import time
        time.sleep(0.5)

    print(f"\n✅ Created {len(created)}/{count} sample events")
    return created


def search_events(client: WixClient, query: str):
    """Search for events by title"""
    print(f"🔍 Searching for events matching '{query}'...\n")

    try:
        events = client.search_events_by_title(query)

        if not events:
            print(f"No events found matching '{query}'")
            return

        print(f"Found {len(events)} event(s):\n")
        for event in events:
            print(f"• {event.get('title')}")
            print(f"  ID: {event.get('id')}")
            start = event.get('dateAndTimeSettings', {}).get('startDate', 'N/A')
            print(f"  Start: {start}")
            print()

    except Exception as e:
        print(f"❌ Failed to search events: {e}")


def check_ticket_support(client: WixClient, event_id: str):
    """Check if an event can have tickets added"""
    print(f"🔍 Checking ticket support for event {event_id}...\n")

    try:
        event = client.get_event(event_id)
        can_add, reason = client.can_add_tickets(event_id)

        print(f"Event: {event.get('title')}")
        print(f"ID: {event_id}")
        print(f"Status: {event.get('status', 'UNKNOWN')}")

        reg = event.get('registration', {})
        print(f"Registration Type: {reg.get('initialType', 'N/A')}")
        print(f"Current Type: {reg.get('type', 'N/A')}")

        print(f"\n{'✅' if can_add else '❌'} Can Add Tickets: {can_add}")
        print(f"Reason: {reason}")

        if not can_add and reg.get('initialType') == 'RSVP':
            print(f"\n💡 To add tickets, you must:")
            print(f"   1. Create a NEW event with registration type 'TICKETING'")
            print(f"   2. You cannot convert RSVP events to ticketed events")

    except Exception as e:
        print(f"❌ Failed to check event: {e}")


def main():
    """Main entry point"""
    if len(sys.argv) < 2:
        print("""
Development Event Operations Tool

Usage:
  python dev_events.py list [limit]
  python dev_events.py get <event_id>
  python dev_events.py create <title> [days_from_now] [draft] [reg_type]
  python dev_events.py update-title <event_id> <new_title>
  python dev_events.py publish <event_id>
  python dev_events.py delete <event_id> [--confirm]
  python dev_events.py delete-drafts [--confirm]
  python dev_events.py delete-test [--confirm]
  python dev_events.py delete-pattern <pattern> [--confirm] [--drafts-only]
  python dev_events.py delete-after-date <YYYY-MM-DD> [--confirm]
  python dev_events.py create-samples [count]
  python dev_events.py search <query>
  python dev_events.py check-tickets <event_id>

Registration Types: RSVP, EXTERNAL, NO_REGISTRATION, TICKETS

  TICKETS - Creates ticketed event (shows "Tickets are not on sale")
            Add tickets manually via Wix Dashboard after creation

IMPORTANT: Registration type is SET AT CREATION and CANNOT be changed!
  - RSVP events CANNOT be converted to ticketed events
  - TICKETING events CANNOT be converted to RSVP events
  - To switch types, you must create a new event with the desired type

Note: The API uses registration.initialType = "TICKETING" (not "TICKETS")
      Tickets must be added via Dashboard (API ticket creation is complex)

Examples:
  # List all events
  python dev_events.py list

  # Get event details
  python dev_events.py get abc123

  # Create a draft RSVP event 7 days from now
  python dev_events.py create "My Test Event" 7 true RSVP

  # Create event for tickets (created as RSVP, set pricing in dashboard)
  python dev_events.py create "Concert" 3 false TICKETS

  # Create event with default registration (RSVP)
  python dev_events.py create "Workshop" 7 true

  # Update event title
  python dev_events.py update-title abc123 "New Title"

  # Publish a draft event
  python dev_events.py publish abc123

  # Delete an event (with confirmation)
  python dev_events.py delete abc123 --confirm

  # Delete all draft events
  python dev_events.py delete-drafts --confirm

  # Delete all test events (title contains 'test')
  python dev_events.py delete-test --confirm

  # Delete events matching a pattern
  python dev_events.py delete-pattern "Workshop" --confirm

  # Delete only draft events matching pattern
  python dev_events.py delete-pattern "Concert" --confirm --drafts-only

  # Delete all events starting from January 2026 onwards
  python dev_events.py delete-after-date 2026-01-01 --confirm

  # Create 10 sample events (mix of RSVP and TICKETS, prefixed with "-test-")
  python dev_events.py create-samples 10

  # Clean up all sample events
  python dev_events.py delete-test --confirm

  # Search for events
  python dev_events.py search "Workshop"

  # Check if an event supports tickets
  python dev_events.py check-tickets abc123
""")
        sys.exit(0)

    command = sys.argv[1]
    client = WixClient()

    try:
        if command == 'list':
            limit = int(sys.argv[2]) if len(sys.argv) > 2 else 50
            list_all_events(client, limit)

        elif command == 'get':
            if len(sys.argv) < 3:
                print("Error: event_id required")
                sys.exit(1)
            event_id = sys.argv[2]
            get_event_details(client, event_id)

        elif command == 'create':
            if len(sys.argv) < 3:
                print("Error: title required")
                sys.exit(1)

            title = sys.argv[2]
            days_from_now = int(sys.argv[3]) if len(sys.argv) > 3 else 7
            draft = sys.argv[4].lower() == 'true' if len(sys.argv) > 4 else True
            reg_type = sys.argv[5].upper() if len(sys.argv) > 5 else 'RSVP'

            # Validate registration type
            valid_types = ['RSVP', 'TICKETS', 'EXTERNAL', 'NO_REGISTRATION']
            if reg_type not in valid_types:
                print(f"Error: Invalid registration type '{reg_type}'")
                print(f"Valid types: {', '.join(valid_types)}")
                sys.exit(1)

            create_test_event(client, title, days_from_now, draft=draft, registration_type=reg_type)

        elif command == 'update-title':
            if len(sys.argv) < 4:
                print("Error: event_id and new_title required")
                sys.exit(1)

            event_id = sys.argv[2]
            new_title = sys.argv[3]
            update_event_title(client, event_id, new_title)

        elif command == 'publish':
            if len(sys.argv) < 3:
                print("Error: event_id required")
                sys.exit(1)

            event_id = sys.argv[2]
            publish_event(client, event_id)

        elif command == 'delete':
            if len(sys.argv) < 3:
                print("Error: event_id required")
                sys.exit(1)

            event_id = sys.argv[2]
            confirm = '--confirm' in sys.argv
            delete_event(client, event_id, confirm)

        elif command == 'delete-drafts':
            confirm = '--confirm' in sys.argv
            delete_all_drafts(client, confirm)

        elif command == 'delete-test':
            confirm = '--confirm' in sys.argv
            delete_test_events(client, confirm)

        elif command == 'delete-pattern':
            if len(sys.argv) < 3:
                print("Error: pattern required")
                sys.exit(1)

            pattern = sys.argv[2]
            confirm = '--confirm' in sys.argv
            drafts_only = '--drafts-only' in sys.argv
            bulk_delete_events(client, pattern=pattern, drafts_only=drafts_only, confirm=confirm)

        elif command == 'delete-after-date':
            if len(sys.argv) < 3:
                print("Error: date required (format: YYYY-MM-DD)")
                sys.exit(1)

            cutoff_date = sys.argv[2]
            confirm = '--confirm' in sys.argv
            delete_events_after_date(client, cutoff_date, confirm)

        elif command == 'create-samples':
            count = int(sys.argv[2]) if len(sys.argv) > 2 else 5
            create_sample_events(client, count)

        elif command == 'search':
            if len(sys.argv) < 3:
                print("Error: query required")
                sys.exit(1)

            query = sys.argv[2]
            search_events(client, query)

        elif command == 'check-tickets':
            if len(sys.argv) < 3:
                print("Error: event_id required")
                sys.exit(1)

            event_id = sys.argv[2]
            check_ticket_support(client, event_id)

        else:
            print(f"Unknown command: {command}")
            sys.exit(1)

    except KeyboardInterrupt:
        print("\n\nOperation cancelled")
        sys.exit(1)
    except Exception as e:
        print(f"\n❌ Error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
