#!/usr/bin/env python3
"""
Test script to verify capacity fix works correctly
"""

from wix_client import WixClient
from dev_events import create_test_event
import json
import sys
from datetime import datetime, timedelta

# Fix Windows console encoding
if sys.platform == 'win32':
    sys.stdout.reconfigure(encoding='utf-8')
    sys.stderr.reconfigure(encoding='utf-8')

def main():
    client = WixClient()

    print("=" * 60)
    print("CAPACITY FIX TEST")
    print("=" * 60)

    # Create a test event
    print("\n1. Creating test TICKETING event...")

    start_date = datetime.now() + timedelta(days=30)
    event_title = f"Capacity Test Event {start_date.strftime('%Y%m%d_%H%M%S')}"

    try:
        event_data = {
            'title': event_title,
            'dateAndTimeSettings': {
                'dateAndTimeTbd': False,
                'startDate': start_date.isoformat() + 'Z',
                'endDate': (start_date + timedelta(hours=2)).isoformat() + 'Z',
                'timeZoneId': 'America/Toronto'
            },
            'location': {
                'type': 'VENUE',
                'address': {
                    'formattedAddress': 'Test Location'
                }
            },
            'registration': {
                'initialType': 'TICKETING'
            }
        }

        created_event = client.create_event(event_data)
        event_id = created_event.get('id')
        print(f"✅ Created event: {event_title}")
        print(f"   Event ID: {event_id}")

    except Exception as e:
        print(f"❌ Failed to create event: {e}")
        return

    # Create ticket with capacity limit
    print("\n2. Creating ticket with capacity=15...")

    try:
        ticket = client.create_ticket_definition(
            event_id=event_id,
            ticket_name="Test Ticket",
            price=25.00,
            capacity=15,  # This should now work!
            currency="CAD"
        )

        ticket_id = ticket.get('id')
        print(f"✅ Created ticket: {ticket.get('name')}")
        print(f"   Ticket ID: {ticket_id}")
        print(f"   Price: ${ticket.get('pricingMethod', {}).get('fixedPrice', {}).get('value')}")

    except Exception as e:
        print(f"❌ Failed to create ticket: {e}")
        import traceback
        traceback.print_exc()
        return

    # Query ticket definition to verify capacity fields
    print("\n3. Verifying ticket definition structure...")

    try:
        response = client._request(
            'POST',
            '/events-ticket-definitions/v3/ticket-definitions/query',
            json={'query': {'filter': {'eventId': event_id}}}
        )

        result = response.json()
        ticket_def = result['ticketDefinitions'][0] if result.get('ticketDefinitions') else {}

        print("\nTicket Definition Fields:")
        print(f"  limited: {ticket_def.get('limited')}")
        print(f"  quantity: {ticket_def.get('quantity')}")
        print(f"  limitPerCheckout: {ticket_def.get('limitPerCheckout')}")

        # Check for other capacity-related fields
        capacity_fields = {k: v for k, v in ticket_def.items()
                          if any(term in k.lower() for term in ['capacity', 'quantity', 'limit', 'total', 'available'])}

        print(f"\nAll capacity-related fields:")
        print(json.dumps(capacity_fields, indent=2))

        # Verify fix worked
        if ticket_def.get('limited') == True:
            print("\n✅ SUCCESS: 'limited' is set to True")

            if ticket_def.get('quantity') == 15:
                print("✅ SUCCESS: 'quantity' is set to 15")
                print("\n🎉 CAPACITY FIX WORKS!")
                print(f"\nNext step: Check Wix Dashboard for event '{event_title}'")
                print(f"It should show 'Limited number of tickets: 15'")
            else:
                print(f"⚠️  WARNING: 'quantity' field value is {ticket_def.get('quantity')} (expected 15)")
                print("\nLet's check if capacity is stored in a different field...")

                # Look for the actual capacity value
                for key, value in ticket_def.items():
                    if value == 15:
                        print(f"   Found value 15 in field: '{key}'")
        else:
            print(f"\n❌ FAILED: 'limited' is {ticket_def.get('limited')} (expected True)")
            print("The 'limited' field was not set correctly.")

    except Exception as e:
        print(f"❌ Error verifying ticket: {e}")
        import traceback
        traceback.print_exc()

    print(f"\n{'=' * 60}")
    print(f"Test Event Created: {event_title}")
    print(f"Event ID: {event_id}")
    print(f"{'=' * 60}")
    print("\nPlease check the Wix Dashboard to verify the ticket shows as limited.")
    print(f"You can delete this test event when done: python dev_events.py delete {event_id} --confirm")

if __name__ == '__main__':
    main()
