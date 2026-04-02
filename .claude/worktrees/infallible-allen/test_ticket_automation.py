#!/usr/bin/env python3
"""
Test Script for Ticket Automation
Tests automatic ticket creation for TICKETING events
"""

import sys
from datetime import datetime, timedelta
from wix_client import WixClient

# Fix Windows console encoding
if sys.platform == 'win32':
    sys.stdout.reconfigure(encoding='utf-8')
    sys.stderr.reconfigure(encoding='utf-8')


def test_ticket_automation():
    """Test creating a TICKETING event with automatic ticket creation"""
    print("🧪 Testing Ticket Automation\n")
    print("=" * 60)

    # Initialize client
    try:
        client = WixClient()
        print("✅ WixClient initialized\n")
    except Exception as e:
        print(f"❌ Failed to initialize WixClient: {e}")
        return False

    # Create test event data
    start_date = datetime.now() + timedelta(days=7)
    end_date = start_date + timedelta(hours=2)

    event_data = {
        'title': 'Test Ticket Automation Event',
        'dateAndTimeSettings': {
            'dateAndTimeTbd': False,
            'startDate': start_date.strftime('%Y-%m-%dT%H:%M:%S') + 'Z',
            'endDate': end_date.strftime('%Y-%m-%dT%H:%M:%S') + 'Z',
            'timeZoneId': 'America/Toronto'
        },
        'location': {
            'type': 'VENUE',
            'address': {
                'formattedAddress': 'Test Venue - Automated Ticket Test'
            }
        },
        'registration': {
            'initialType': 'TICKETING'
        }
    }

    # Step 1: Create TICKETING event
    print("Step 1: Creating TICKETING event...")
    print("-" * 60)
    try:
        created_event = client.create_event(event_data)
        event_id = created_event.get('id')
        print(f"✅ Event created successfully!")
        print(f"   Event ID: {event_id}")
        print(f"   Title: {created_event.get('title')}")
        print()
    except Exception as e:
        print(f"❌ Failed to create event: {e}")
        return False

    # Step 2: Create ticket definition
    print("Step 2: Creating ticket definition...")
    print("-" * 60)
    ticket_price = 25.00
    ticket_capacity = 50

    try:
        ticket = client.create_ticket_definition(
            event_id=event_id,
            ticket_name="General Admission",
            price=ticket_price,
            capacity=ticket_capacity
        )
        print(f"✅ Ticket created successfully!")
        print(f"   Ticket Name: General Admission")
        print(f"   Price: ${ticket_price:.2f}")
        print(f"   Capacity: {ticket_capacity}")
        print(f"   Ticket ID: {ticket.get('id', 'N/A')}")
        print()
    except Exception as e:
        print(f"❌ Failed to create ticket: {e}")
        print(f"   Event ID: {event_id}")
        print(f"   Note: Event was created but ticket creation failed")
        print(f"   You can add tickets manually via Wix Dashboard")
        return False

    # Step 3: Verify event exists
    print("Step 3: Verifying event in Wix...")
    print("-" * 60)
    try:
        retrieved_event = client.get_event(event_id)
        print(f"✅ Event retrieved successfully!")
        print(f"   Title: {retrieved_event.get('title')}")
        print(f"   Status: {retrieved_event.get('status')}")
        print()
    except Exception as e:
        print(f"⚠️  Could not retrieve event: {e}")
        print()

    # Success summary
    print("=" * 60)
    print("✅ TEST PASSED - Ticket Automation Working!\n")
    print("Next Steps:")
    print("1. Open Wix Dashboard → Events")
    print(f"2. Find event: 'Test Ticket Automation Event'")
    print("3. Verify ticket 'General Admission' exists ($25.00)")
    print("4. Try purchasing a ticket to confirm it works")
    print(f"5. Delete test event when done (ID: {event_id})")
    print()
    print(f"Delete command: python dev_events.py delete {event_id} --confirm")
    print()

    return True


def main():
    """Main entry point"""
    print()
    success = test_ticket_automation()
    print()

    if success:
        print("🎉 All tests passed!")
        sys.exit(0)
    else:
        print("❌ Tests failed")
        sys.exit(1)


if __name__ == "__main__":
    main()
