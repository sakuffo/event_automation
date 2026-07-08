#!/usr/bin/env python3
"""
Development Ticket Purchase Automation
Automate RSVP and ticket purchases for testing without using live site
"""

import sys
import json
from typing import Dict, Any, Optional
# Allow running from anywhere: repo root holds wix_client.py
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from event_sync.wix_client import WixClient

# Fix Windows console encoding for emojis
if sys.platform == 'win32':
    sys.stdout.reconfigure(encoding='utf-8')
    sys.stderr.reconfigure(encoding='utf-8')


def add_ticket_to_event(client: WixClient, event_id: str, name: str = "General Admission",
                        price: float = 25.0, currency: str = "CAD", quantity: Optional[int] = None) -> Optional[Dict[str, Any]]:
    """Add a ticket definition to an existing event"""
    print(f"Adding ticket to event {event_id}...")
    print(f"   Ticket: {name}")
    print(f"   Price: ${price} {currency}")
    print(f"   Quantity: {'Unlimited' if quantity is None else quantity}\n")

    existing = client.get_ticket_definitions(event_id)
    existing_names = [d.get("name", "") for d in existing]
    if name in existing_names:
        print(f"⚠️  Ticket '{name}' already exists on this event — skipping to avoid duplicates")
        return None

    try:
        result = client.create_ticket_definition(
            event_id=event_id,
            ticket_name=name,
            price=price,
            currency=currency,
            capacity = quantity
        )
        print(f"✅ Ticket definition created successfully!")
        ticket_def = result.get('ticketDefinition', {})
        print(f"   Ticket ID: {ticket_def.get('id')}")
        print(f"   Name: {ticket_def.get('name')}")
        return result
    except Exception as e:
        print(f"❌ Failed to create ticket definition: {e}")
        if hasattr(e, 'response') and e.response is not None: #type: ignore
            try:
                error_detail = e.response.json() #type: ignore
                print(f"   Error details: {json.dumps(error_detail, indent=2)}")
            except:
                print(f"   Response: {e.response.text}") #type: ignore
        return None


def create_test_ticket_order(client: WixClient, event_id: str, ticket_definitions: List[Dict],
                             buyer_name: str = "Test Buyer", buyer_email: str = "buyer@example.com") -> Optional[Dict[str, Any]]:
    """Create a test ticket order (for paid tickets)"""
    print(f"Creating ticket order for event {event_id}...")

    checkout_info = {
        'buyerInfo': {
            'firstName': buyer_name.split()[0] if ' ' in buyer_name else buyer_name,
            'lastName': buyer_name.split()[1] if ' ' in buyer_name else 'Buyer',
            'email': buyer_email
        }
    }

    try:
        result = client.create_ticket_order(
            event_id=event_id,
            tickets=ticket_definitions,
            checkout_info=checkout_info
        )
        print(f"✅ Ticket order created successfully!")
        print(f"   Order ID: {result.get('order', {}).get('id')}")
        print(f"   Buyer: {buyer_name}")
        print(f"   Email: {buyer_email}")
        return result
    except Exception as e:
        print(f"❌ Failed to create ticket order: {e}")
        return None


def list_event_orders(client: WixClient, event_id: str):
    """List all ticket orders for an event"""
    print(f"📋 Fetching orders for event {event_id}...\n")

    try:
        orders = client.get_orders(event_id=event_id)

        if not orders:
            print("No orders found for this event.")
            return

        print(f"Found {len(orders)} orders:\n")
        for i, order in enumerate(orders, 1):
            buyer = order.get('buyerInfo', {})
            name = f"{buyer.get('firstName', '')} {buyer.get('lastName', '')}".strip()
            email = buyer.get('email', 'N/A')
            status = order.get('status', 'UNKNOWN')
            total = order.get('totals', {}).get('total', 0)

            print(f"{i}. {name}")
            print(f"   Email: {email}")
            print(f"   Status: {status}")
            print(f"   Total: ${total/100:.2f}" if total else "   Total: $0.00")
            print(f"   Order ID: {order.get('id')}")
            print()

    except Exception as e:
        print(f"❌ Failed to fetch orders: {e}")


def main():
    """Main entry point"""
    if len(sys.argv) < 2:
        print("""
Development Ticket Automation Tool

Usage:
  python dev_tickets.py add-ticket <event_id> [name] [price] [currency]
  python dev_tickets.py list-orders <event_id>
  python dev_tickets.py search-event <title>

Examples:
  # Add ticket to existing event
  python dev_tickets.py add-ticket abc123 "General Admission" 25 CAD
  python dev_tickets.py add-ticket abc123 "VIP Pass" 50 CAD

  # List orders for an event
  python dev_tickets.py list-orders abc123

  # Search for event by title
  python dev_tickets.py search-event "Workshop"
""")
        sys.exit(0)

    command = sys.argv[1]
    client = WixClient()

    try:
        if command == 'add-ticket':
            if len(sys.argv) < 3:
                print("Error: event_id required")
                sys.exit(1)

            event_id = sys.argv[2]
            ticket_name = sys.argv[3] if len(sys.argv) > 3 else "General Admission"
            price = float(sys.argv[4]) if len(sys.argv) > 4 else 25.0
            currency = sys.argv[5] if len(sys.argv) > 5 else "CAD"

            add_ticket_to_event(client, event_id, ticket_name, price, currency)

        elif command == 'list-orders':
            if len(sys.argv) < 3:
                print("Error: event_id required")
                sys.exit(1)

            event_id = sys.argv[2]
            list_event_orders(client, event_id)

        elif command == 'search-event':
            if len(sys.argv) < 3:
                print("Error: title required")
                sys.exit(1)

            title = sys.argv[2]
            events = client.search_events_by_title(title)

            if not events:
                print(f"No events found matching '{title}'")
                sys.exit(0)

            print(f"\nFound {len(events)} event(s):\n")
            for event in events:
                print(f"• {event.get('title')}")
                print(f"  ID: {event.get('id')}")
                start = event.get('dateAndTimeSettings', {}).get('startDate', 'N/A')
                print(f"  Start: {start}")
                print()

        else:
            print(f"Unknown command: {command}")
            sys.exit(1)

    except KeyboardInterrupt:
        print("\n\nOperation cancelled")
        sys.exit(1)
    except Exception as e:
        print(f"\n❌ Error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
