#!/usr/bin/env python3
"""
Diagnostic script to inspect ticket definitions structure
"""

from wix_client import WixClient
import json
import sys

# Fix Windows console encoding
if sys.platform == 'win32':
    sys.stdout.reconfigure(encoding='utf-8')
    sys.stderr.reconfigure(encoding='utf-8')

def main():
    client = WixClient()

    print("Fetching TICKETING events...")
    events = client.list_events(limit=10)

    # Find a TICKETING event
    ticketing_events = [e for e in events if e.get('registration', {}).get('type') == 'TICKETING']

    if not ticketing_events:
        print("No TICKETING events found")
        return

    event = ticketing_events[0]
    event_id = event['id']
    event_title = event['title']

    print(f"\n{'='*60}")
    print(f"Event: {event_title}")
    print(f"Event ID: {event_id}")
    print(f"{'='*60}\n")

    # Query ticket definitions for this event
    try:
        response = client._request(
            'POST',
            '/events-ticket-definitions/v3/ticket-definitions/query',
            json={'query': {'filter': {'eventId': event_id}}}
        )

        result = response.json()

        print("Ticket Definitions API Response:")
        print(json.dumps(result, indent=2))

        # Check if there are ticket definitions
        if 'ticketDefinitions' in result:
            print(f"\n\nFound {len(result['ticketDefinitions'])} ticket definition(s)")

            for i, ticket in enumerate(result['ticketDefinitions'], 1):
                print(f"\n--- Ticket {i} ---")
                print(f"Name: {ticket.get('name')}")
                print(f"Price: {ticket.get('pricingMethod', {}).get('fixedPrice', {}).get('value')}")

                # Look for capacity-related fields
                capacity_fields = {}
                for key in ticket.keys():
                    if any(term in key.lower() for term in ['capacity', 'quantity', 'limit', 'total', 'available']):
                        capacity_fields[key] = ticket[key]

                if capacity_fields:
                    print(f"Capacity-related fields found: {json.dumps(capacity_fields, indent=2)}")
                else:
                    print("⚠️  No capacity-related fields found")

    except Exception as e:
        print(f"❌ Error querying ticket definitions: {e}")
        import traceback
        traceback.print_exc()

if __name__ == '__main__':
    main()
