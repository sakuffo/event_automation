#!/usr/bin/env python3
"""
Wix API Client
Reusable client for interacting with Wix Events and related APIs
"""

import os
import requests
from typing import Dict, Any, List, Optional
from dotenv import load_dotenv

load_dotenv()


class WixClient:
    """Client for Wix API operations"""

    def __init__(self, api_key: str = None, site_id: str = None, account_id: str = None, use_dev: bool = None):
        """
        Initialize Wix API client

        Args:
            api_key: Wix API key (optional, reads from env)
            site_id: Wix site ID (optional, reads from env)
            account_id: Wix account ID (optional, reads from env)
            use_dev: Use development credentials if True, otherwise check ENV_MODE
        """
        # Determine if we should use dev credentials
        env_mode = os.getenv('ENV_MODE', 'production')
        use_dev_mode = use_dev if use_dev is not None else (env_mode == 'development')

        # Load credentials based on mode
        if use_dev_mode and os.getenv('DEV_WIX_API_KEY'):
            self.api_key = api_key or os.getenv('DEV_WIX_API_KEY')
            self.site_id = site_id or os.getenv('DEV_WIX_SITE_ID')
            self.account_id = account_id or os.getenv('DEV_WIX_ACCOUNT_ID')
            self.mode = 'development'
        else:
            self.api_key = api_key or os.getenv('WIX_API_KEY')
            self.site_id = site_id or os.getenv('WIX_SITE_ID')
            self.account_id = account_id or os.getenv('WIX_ACCOUNT_ID')
            self.mode = 'production'

        self.base_url = 'https://www.wixapis.com'

        if not all([self.api_key, self.site_id]):
            raise ValueError("WIX_API_KEY and WIX_SITE_ID are required")

        print(f"[*] Wix Client initialized in {self.mode.upper()} mode")

    def _headers(self, content_type: str = 'application/json') -> Dict[str, str]:
        """Get standard headers for Wix API requests"""
        headers = {
            'Authorization': self.api_key,
            'wix-site-id': self.site_id,
            'Content-Type': content_type
        }

        # Add account ID if available (required for some APIs like Site Media)
        if self.account_id:
            headers['wix-account-id'] = self.account_id

        return headers

    def _request(self, method: str, endpoint: str, **kwargs) -> requests.Response:
        """Make a request to Wix API with retry logic and error handling"""
        import time

        url = f"{self.base_url}{endpoint}"
        max_retries = 3
        timeout = kwargs.pop('timeout', 30)  # Default 30 second timeout

        for attempt in range(max_retries):
            try:
                response = requests.request(
                    method,
                    url,
                    headers=self._headers(),
                    timeout=timeout,
                    **kwargs
                )
                response.raise_for_status()
                return response

            except requests.exceptions.HTTPError as e:
                # Handle rate limiting (429) with exponential backoff
                if e.response is not None and e.response.status_code == 429:
                    if attempt < max_retries - 1:
                        wait_time = 2 ** attempt  # Exponential backoff: 1s, 2s, 4s
                        print(f"[!] Rate limited. Retrying in {wait_time}s... (attempt {attempt + 1}/{max_retries})")
                        time.sleep(wait_time)
                        continue
                # Print detailed error for debugging
                if e.response is not None:
                    try:
                        error_body = e.response.json()
                        print(f"[!] API Error: {error_body}")
                    except:
                        print(f"[!] API Error: {e.response.text}")
                # Re-raise other HTTP errors or final retry
                raise

            except requests.exceptions.Timeout:
                if attempt < max_retries - 1:
                    print(f"[!] Request timeout. Retrying... (attempt {attempt + 1}/{max_retries})")
                    continue
                raise

            except requests.exceptions.ConnectionError:
                if attempt < max_retries - 1:
                    wait_time = 2 ** attempt
                    print(f"[!] Connection error. Retrying in {wait_time}s... (attempt {attempt + 1}/{max_retries})")
                    time.sleep(wait_time)
                    continue
                raise

        # Should not reach here, but just in case
        raise Exception(f"Failed to complete request after {max_retries} attempts")

    # Event Operations

    def list_events(self, limit: int = 50, offset: int = 0, include_drafts: bool = True,
                    status_filter: str = None) -> List[Dict[str, Any]]:
        """List all events

        Args:
            limit: Maximum number of events to return
            offset: Number of events to skip
            include_drafts: If True, attempts to include draft events (may not work with API)
            status_filter: Optional status to filter by (e.g., 'DRAFT', 'PUBLISHED')
        """
        query = {
            'paging': {
                'limit': limit,
                'offset': offset
            }
        }

        # Add status filter if specified
        if status_filter:
            query['filter'] = {
                'status': {
                    '$eq': status_filter
                }
            }

        response = self._request(
            'POST',
            '/events/v3/events/query',
            json={'query': query}
        )
        return response.json().get('events', [])

    def get_event(self, event_id: str, include_registration: bool = True) -> Dict[str, Any]:
        """Get a specific event by ID"""
        params = {}
        if include_registration:
            params['fieldsets'] = 'FULL'

        response = self._request('GET', f'/events/v3/events/{event_id}', params=params)
        return response.json().get('event', {})

    def create_event(self, event_data: Dict[str, Any]) -> Dict[str, Any]:
        """Create a new event with V3 API

        Supports registration types: RSVP, TICKETS, RSVP_AND_TICKETS
        """
        response = self._request(
            'POST',
            '/events/v3/events',
            json={'event': event_data}
        )
        return response.json().get('event', {})

    def update_event(self, event_id: str, event_data: Dict[str, Any]) -> Dict[str, Any]:
        """Update an existing event"""
        response = self._request(
            'PATCH',
            f'/events/v3/events/{event_id}',
            json={'event': event_data}
        )
        return response.json().get('event', {})

    def delete_event(self, event_id: str) -> bool:
        """Delete an event"""
        try:
            self._request('DELETE', f'/events/v3/events/{event_id}')
            return True
        except Exception:
            return False

    def publish_event(self, event_id: str) -> Dict[str, Any]:
        """Publish a draft event (changes status from DRAFT to UPCOMING)"""
        response = self._request('POST', f'/events/v3/events/{event_id}/publish')
        return response.json().get('event', {})

    # Ticket/Registration Operations

    def create_rsvp(self, event_id: str, contact_info: Dict[str, Any],
                    guest_count: int = 1, form_response: Dict[str, Any] = None) -> Dict[str, Any]:
        """
        Create an RSVP for an event

        ⚠️ DEPRECATED: This endpoint (/events/v3/rsvps) returns 404.
        The RSVP API appears to be deprecated. Use Wix Dashboard instead.
        """
        payload = {
            'eventId': event_id,
            'contact': contact_info,
            'guestCount': guest_count
        }

        if form_response:
            payload['formResponse'] = form_response

        response = self._request(
            'POST',
            '/events/v3/rsvps',
            json=payload
        )
        return response.json()

    def get_rsvps(self, event_id: str = None, limit: int = 50) -> List[Dict[str, Any]]:
        """Get RSVPs for an event"""
        query = {'paging': {'limit': limit}}

        if event_id:
            query['filter'] = {'eventId': event_id}

        response = self._request(
            'POST',
            '/events/v3/rsvps/query',
            json={'query': query}
        )
        return response.json().get('rsvps', [])

    def create_ticket_order(self, event_id: str, tickets: List[Dict[str, Any]],
                           checkout_info: Dict[str, Any]) -> Dict[str, Any]:
        """Create a ticket order (for paid tickets)"""
        payload = {
            'eventId': event_id,
            'tickets': tickets,
            'checkoutInfo': checkout_info
        }

        response = self._request(
            'POST',
            '/events/v3/orders',
            json=payload
        )
        return response.json()

    def get_orders(self, event_id: str = None, limit: int = 50) -> List[Dict[str, Any]]:
        """Get ticket orders"""
        query = {'paging': {'limit': limit}}

        if event_id:
            query['filter'] = {'eventId': event_id}

        response = self._request(
            'POST',
            '/events/v3/orders/query',
            json={'query': query}
        )
        return response.json().get('orders', [])

    # Media Operations

    def upload_image(self, image_data: bytes, filename: str, mime_type: str) -> Dict[str, Any]:
        """Upload an image to Wix Media Manager and return file descriptor"""
        # Get upload URL
        response = self._request(
            'POST',
            '/site-media/v1/files/generate-upload-url',
            json={
                'mimeType': mime_type,
                'fileName': filename
            }
        )

        upload_data = response.json()
        upload_url = upload_data.get('uploadUrl')

        # Upload file
        upload_response = requests.put(
            upload_url,
            data=image_data,
            headers={'Content-Type': mime_type}
        )
        upload_response.raise_for_status()

        # Get file descriptor from upload response
        upload_result = upload_response.json()
        if 'file' in upload_result:
            return upload_result['file']

        # Fallback (shouldn't happen)
        raise Exception("Upload succeeded but no file descriptor returned")

    def create_ticket_definition(self, event_id: str, ticket_name: str, price: float,
                                 capacity: Optional[int] = None, currency: str = "CAD") -> Dict[str, Any]:
        """
        Create a ticket definition for a TICKETING event

        Args:
            event_id: The event ID to create tickets for
            ticket_name: Name of the ticket (e.g., "General Admission")
            price: Ticket price (e.g., 25.00)
            capacity: Maximum number of tickets available (optional)
            currency: Currency code (default: CAD)

        Returns:
            Dict containing the created ticket definition

        Note:
            - Only works for events with registration.initialType = "TICKETING"
            - Uses simple defaults suitable for small business use
            - Buyer pays fees (standard Wix configuration)
        """
        ticket_data = {
            "ticketDefinition": {
                "eventId": event_id,  # Required in body
                "name": ticket_name,
                "limitPerCheckout": 10,  # Max tickets per order
                "pricingMethod": {
                    "fixedPrice": {
                        "value": str(price),
                        "currency": currency
                    }
                },
                "feeType": "FEE_ADDED_AT_CHECKOUT"  # Required: Buyer pays fees
            }
        }

        # Add capacity limit if specified
        if capacity:
            ticket_data["ticketDefinition"]["limited"] = True
            ticket_data["ticketDefinition"]["quantity"] = capacity

        response = self._request(
            'POST',
            '/events-ticket-definitions/v3/ticket-definitions',
            json=ticket_data
        )
        return response.json().get('ticketDefinition', {})

    # Utility Methods

    def search_events_by_title(self, title: str) -> List[Dict[str, Any]]:
        """Search for events by title"""
        all_events = self.list_events(limit=100)
        return [e for e in all_events if title.lower() in e.get('title', '').lower()]

    def get_event_by_title(self, title: str) -> Optional[Dict[str, Any]]:
        """Get the first event matching a title"""
        results = self.search_events_by_title(title)
        return results[0] if results else None
