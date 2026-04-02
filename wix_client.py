#!/usr/bin/env python3
"""
Wix API Client
Reusable client for interacting with Wix Events and related APIs
"""

import logging
import os
import time
from copy import deepcopy
from itertools import islice
from typing import Any, Dict, Iterator, List, Optional

import requests
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)


class WixApiError(Exception):
    """Raised when a Wix API operation fails."""


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

        logger.info("Wix Client initialized in %s mode", self.mode.upper())

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
                        logger.warning("Rate limited. Retrying in %ds... (attempt %d/%d)", wait_time, attempt + 1, max_retries)
                        time.sleep(wait_time)
                        continue
                # Log detailed error for debugging
                if e.response is not None:
                    try:
                        error_body = e.response.json()
                        logger.error("API Error: %s", error_body)
                    except (ValueError, KeyError):
                        logger.error("API Error: %s", e.response.text)
                # Re-raise other HTTP errors or final retry
                raise

            except requests.exceptions.Timeout:
                if attempt < max_retries - 1:
                    logger.warning("Request timeout. Retrying... (attempt %d/%d)", attempt + 1, max_retries)
                    continue
                raise

            except requests.exceptions.ConnectionError:
                if attempt < max_retries - 1:
                    wait_time = 2 ** attempt
                    logger.warning("Connection error. Retrying in %ds... (attempt %d/%d)", wait_time, attempt + 1, max_retries)
                    time.sleep(wait_time)
                    continue
                raise

        # Should not reach here, but just in case
        raise WixApiError(f"Failed to complete request after {max_retries} attempts")

    # Event Operations

    def _paged_post(
        self,
        endpoint: str,
        array_key: str,
        base_query: Optional[Dict[str, Any]],
        page_size: int,
        *,
        initial_offset: int = 0,
        extra_body: Optional[Dict[str, Any]] = None,
    ) -> Iterator[Dict[str, Any]]:
        """Yield results across all pages for Wix POST query endpoints."""

        if page_size <= 0:
            raise ValueError("page_size must be a positive integer")

        cursor: Optional[str] = None
        offset = max(initial_offset, 0)

        while True:
            query: Dict[str, Any] = deepcopy(base_query) if base_query else {}
            paging = query.setdefault('paging', {})
            paging['limit'] = page_size

            if cursor:
                paging.pop('offset', None)
                paging['cursor'] = cursor
            else:
                paging.pop('cursor', None)
                if offset:
                    paging['offset'] = offset
                elif 'offset' in paging:
                    paging['offset'] = 0

            body: Dict[str, Any] = {'query': query}
            if extra_body:
                body.update(extra_body)

            response = self._request('POST', endpoint, json=body)
            payload = response.json() or {}
            items = payload.get(array_key, []) or []

            for item in items:
                yield item

            metadata = payload.get('pagingMetadata') or {}
            cursor = metadata.get('nextCursor')

            if cursor:
                continue

            if len(items) < page_size:
                break

            if not items:
                break

            offset += page_size

    def list_events(self, limit: int = 50, offset: int = 0, include_drafts: bool = True,
                    status_filter: str = None) -> List[Dict[str, Any]]:
        """Return up to ``limit`` events starting at ``offset``."""

        iterator = self.iter_events(
            page_size=max(limit, 1),
            include_drafts=include_drafts,
            status_filter=status_filter,
            offset=offset,
        )
        return list(islice(iterator, limit))

    def iter_events(
        self,
        *,
        page_size: int = 100,
        include_drafts: bool = True,
        status_filter: Optional[str] = None,
        offset: int = 0,
        fieldsets: Optional[List[str]] = None,
    ) -> Iterator[Dict[str, Any]]:
        """Yield events across all pages with cursor/offset pagination."""

        base_query: Dict[str, Any] = {}
        if status_filter:
            base_query['filter'] = {'status': {'$eq': status_filter}}
        elif not include_drafts:
            base_query['filter'] = {'status': {'$ne': 'DRAFT'}}

        extra: Optional[Dict[str, Any]] = None
        if fieldsets:
            extra = {'fieldsets': fieldsets}

        yield from self._paged_post(
            '/events/v3/events/query',
            'events',
            base_query,
            page_size,
            initial_offset=offset,
            extra_body=extra,
        )

    def get_event(self, event_id: str, include_registration: bool = True) -> Dict[str, Any]:
        """Get a specific event by ID"""
        params = {}
        if include_registration:
            params['fieldsets'] = 'FULL'

        response = self._request('GET', f'/events/v3/events/{event_id}', params=params)
        return response.json().get('event', {})

    def create_event(self, event_data: Dict[str, Any], draft: bool = False) -> Dict[str, Any]:
        """Create a new event. Pass ``draft=True`` to create as a draft."""
        payload: Dict[str, Any] = {'event': event_data}
        if draft:
            payload['draft'] = True
        response = self._request(
            'POST',
            '/events/v3/events',
            json=payload,
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

    def has_orders(self, event_id: str) -> bool:
        """Check if an event has any ticket orders."""
        try:
            orders = list(islice(self.iter_orders(event_id=event_id, page_size=1), 1))
            return len(orders) > 0
        except Exception:
            return True

    def delete_event(self, event_id: str, force: bool = False) -> bool:
        """Delete an event. Refuses if the event has orders unless force=True."""
        try:
            if not force and self.has_orders(event_id):
                logger.error(
                    "Refusing to delete event %s — it has existing orders. "
                    "Use force=True to override.",
                    event_id,
                )
                return False
            self._request('DELETE', f'/events/v3/events/{event_id}')
            return True
        except Exception as exc:
            logger.error("Failed to delete event %s: %s", event_id, exc)
            return False

    def publish_event(self, event_id: str) -> Dict[str, Any]:
        """Publish a draft event (changes status from DRAFT to UPCOMING)"""
        response = self._request('POST', f'/events/v3/events/{event_id}/publish')
        return response.json().get('event', {})

    # Ticket/Registration Operations

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
        """Return up to ``limit`` ticket orders."""

        iterator = self.iter_orders(event_id=event_id, page_size=max(limit, 1))
        return list(islice(iterator, limit))

    def iter_orders(
        self,
        event_id: Optional[str] = None,
        *,
        page_size: int = 100,
    ) -> Iterator[Dict[str, Any]]:
        """Yield order records across all pages."""

        base_query: Dict[str, Any] = {}
        if event_id:
            base_query['filter'] = {'eventId': event_id}

        yield from self._paged_post(
            '/events/v3/orders/query',
            'orders',
            base_query,
            page_size,
        )

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
        raise WixApiError("Upload succeeded but no file descriptor returned")

    def create_ticket_definition(
        self,
        event_id: str,
        ticket_name: str,
        price: float,
        capacity: Optional[int] = None,
        limit_per_checkout: int = 4,
        currency: str = "CAD",
        fee_type: str = "FEE_ADDED_AT_CHECKOUT",
        sale_start: Optional[str] = None,
        sale_end: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Create a ticket definition for a TICKETING event."""
        definition: Dict[str, Any] = {
            "eventId": event_id,
            "name": ticket_name,
            "limitPerCheckout": limit_per_checkout,
            "pricingMethod": {
                "fixedPrice": {
                    "value": str(price),
                    "currency": currency,
                }
            },
            "feeType": fee_type,
        }

        if capacity is not None and capacity > 0:
            definition["initialLimit"] = capacity

        if sale_start or sale_end:
            sale_period: Dict[str, Any] = {}
            if sale_start:
                sale_period["startDate"] = sale_start
            if sale_end:
                sale_period["endDate"] = sale_end
            definition["salePeriod"] = sale_period

        response = self._request(
            'POST',
            '/events-ticket-definitions/v3/ticket-definitions',
            json={"ticketDefinition": definition},
        )
        result = response.json().get('ticketDefinition', {})

        actual_limit = result.get("initialLimit") or result.get("actualLimit")
        is_limited = result.get("limited", False)
        if capacity is not None and capacity > 0:
            if not is_limited or actual_limit != capacity:
                logger.warning(
                    "Ticket capacity mismatch — requested %d, got limited=%s actualLimit=%s",
                    capacity, is_limited, actual_limit,
                )

        return result

    def update_ticket_definition(
        self,
        ticket_def_id: str,
        revision: str,
        *,
        price: Optional[float] = None,
        capacity: Optional[int] = None,
        currency: str = "CAD",
    ) -> Dict[str, Any]:
        """Update an existing ticket definition's price and/or capacity."""
        update: Dict[str, Any] = {
            "id": ticket_def_id,
            "revision": revision,
        }
        if price is not None:
            update["pricingMethod"] = {
                "fixedPrice": {"value": str(price), "currency": currency}
            }
        if capacity is not None and capacity > 0:
            update["initialLimit"] = capacity

        response = self._request(
            'PATCH',
            f'/events-ticket-definitions/v3/ticket-definitions/{ticket_def_id}',
            json={"ticketDefinition": update},
        )
        return response.json().get('ticketDefinition', {})

    def get_ticket_definitions(
        self, event_id: str, include_sales: bool = False,
    ) -> List[Dict[str, Any]]:
        """Return all ticket definitions for an event."""
        try:
            body: Dict[str, Any] = {'query': {'filter': {'eventId': event_id}}}
            if include_sales:
                body['fields'] = ['SALES_DETAILS']
            response = self._request(
                'POST',
                '/events-ticket-definitions/v3/ticket-definitions/query',
                json=body,
            )
            return response.json().get('ticketDefinitions', [])
        except Exception as exc:
            logger.warning("Could not query ticket definitions for %s: %s", event_id, exc)
            return []

    # Category Operations

    def query_categories(self) -> List[Dict[str, Any]]:
        """Return all event categories on the site."""
        try:
            response = self._request(
                'POST',
                '/events/v1/categories/query',
                json={'query': {'paging': {'limit': 100}}},
            )
            return response.json().get('categories', [])
        except Exception as exc:
            logger.warning("Could not query categories: %s", exc)
            return []

    def create_category(self, name: str) -> Dict[str, Any]:
        """Create a single event category and return it."""
        response = self._request(
            'POST',
            '/events/v1/categories',
            json={'category': {'name': name, 'states': ['MANUAL']}},
        )
        return response.json().get('category', {})

    def assign_event_to_category(self, category_id: str, event_id: str) -> None:
        """Assign an event to a category."""
        self._request(
            'POST',
            f'/events/v1/categories/{category_id}/events',
            json={'eventId': [event_id]},
        )

    def unassign_event_from_category(self, category_id: str, event_id: str) -> None:
        """Remove an event from a category."""
        self._request(
            'DELETE',
            f'/events/v1/categories/{category_id}/events',
            params={'eventId': event_id},
        )

    # Utility Methods

    def search_events_by_title(self, title: str) -> List[Dict[str, Any]]:
        """Search for events by title"""
        return [
            event
            for event in self.iter_events(page_size=100)
            if title.lower() in event.get('title', '').lower()
        ]

    def get_event_by_title(self, title: str) -> Optional[Dict[str, Any]]:
        """Get the first event matching a title"""
        results = self.search_events_by_title(title)
        return results[0] if results else None
