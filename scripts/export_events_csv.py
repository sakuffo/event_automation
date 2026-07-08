"""Export every Wix event (all statuses, incl. drafts) to a CSV for analysis.

Read-only: uses only the Events V3 query endpoint. Each row includes dates,
location, registration type, categories, descriptions (rich text flattened to
plain text), and the dashboard sales summary (tickets sold, orders, revenue,
RSVP counts).

Usage:
    python scripts/export_events_csv.py                         # site from .env
    python scripts/export_events_csv.py --site-id <SITE_ID>     # e.g. production
    python scripts/export_events_csv.py -o my_export.csv

API shape confirmed against:
https://dev.wix.com/docs/api-reference/business-solutions/events/event-management/events-v3/query-events
"""

import argparse
import csv
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional
from zoneinfo import ZoneInfo

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from event_sync.wix_client import WixClient

QUERY_FIELDS = ["DETAILS", "TEXTS", "REGISTRATION", "URLS", "DASHBOARD", "CATEGORIES"]

CSV_COLUMNS = [
    "event_id",
    "title",
    "slug",
    "status",
    "start_utc",
    "end_utc",
    "timezone",
    "start_local_date",
    "start_local_time",
    "end_local_time",
    "recurrence",
    "location_name",
    "location_address",
    "registration_type",
    "registration_status",
    "categories",
    "short_description",
    "description",
    "currency",
    "lowest_ticket_price",
    "highest_ticket_price",
    "sold_out",
    "tickets_sold",
    "total_orders",
    "revenue",
    "total_sales",
    "rsvp_total",
    "rsvp_yes",
    "rsvp_no",
    "rsvp_waitlist",
    "event_page_url",
    "main_image_url",
    "created_date",
    "published_date",
]


def ricos_to_text(rich_content: Optional[Dict[str, Any]]) -> str:
    """Flatten a Ricos rich-content document to readable plain text."""
    if not rich_content or not rich_content.get("nodes"):
        return ""

    def flatten(node: Dict[str, Any]) -> str:
        parts: List[str] = []

        def walk(n: Dict[str, Any]) -> None:
            if n.get("type") == "TEXT":
                text = (n.get("textData") or {}).get("text", "")
                if text:
                    parts.append(text)
            for child in n.get("nodes") or []:
                walk(child)

        walk(node)
        return "".join(parts)

    blocks: List[str] = []
    for block in rich_content["nodes"]:
        if block.get("type") in ("BULLETED_LIST", "ORDERED_LIST"):
            items = ["- " + flatten(item) for item in block.get("nodes") or []]
            blocks.append("\n".join(i for i in items if i != "- "))
        else:
            blocks.append(flatten(block))
    return "\n".join(b for b in blocks if b.strip())


def to_local(iso_utc: str, tz_id: str) -> tuple:
    """Convert a UTC ISO timestamp to (date, time) strings in the event's timezone."""
    if not iso_utc:
        return "", ""
    try:
        dt = datetime.fromisoformat(iso_utc.replace("Z", "+00:00"))
        local = dt.astimezone(ZoneInfo(tz_id or "America/Toronto"))
        return local.strftime("%Y-%m-%d"), local.strftime("%H:%M")
    except (ValueError, KeyError):
        return iso_utc, ""


def event_to_row(event: Dict[str, Any]) -> Dict[str, Any]:
    dt = event.get("dateAndTimeSettings") or {}
    tz_id = dt.get("timeZoneId") or ""
    start_date, start_time = to_local(dt.get("startDate", ""), tz_id)
    _, end_time = to_local(dt.get("endDate", ""), tz_id)

    location = event.get("location") or {}
    address = (location.get("address") or {}).get("formattedAddress", "")

    registration = event.get("registration") or {}
    tickets_reg = registration.get("tickets") or {}

    categories = (event.get("categories") or {}).get("categories") or []

    summaries = event.get("summaries") or {}
    ticket_summary = summaries.get("tickets") or {}
    rsvp_summary = summaries.get("rsvps") or {}

    page_url = event.get("eventPageUrl") or {}
    url = (page_url.get("base") or "") + (page_url.get("path") or "")

    return {
        "event_id": event.get("id", ""),
        "title": (event.get("title") or "").strip(),
        "slug": event.get("slug", ""),
        "status": event.get("status", ""),
        "start_utc": dt.get("startDate", ""),
        "end_utc": dt.get("endDate", ""),
        "timezone": tz_id,
        "start_local_date": start_date,
        "start_local_time": start_time,
        "end_local_time": end_time,
        "recurrence": dt.get("recurrenceStatus", ""),
        "location_name": location.get("name", ""),
        "location_address": address,
        "registration_type": registration.get("type", ""),
        "registration_status": registration.get("status", ""),
        "categories": "; ".join(c.get("name", "") for c in categories),
        "short_description": event.get("shortDescription", ""),
        "description": ricos_to_text(event.get("description")),
        "currency": tickets_reg.get("currency", ""),
        "lowest_ticket_price": (tickets_reg.get("lowestPrice") or {}).get("value", ""),
        "highest_ticket_price": (tickets_reg.get("highestPrice") or {}).get("value", ""),
        "sold_out": tickets_reg.get("soldOut", ""),
        "tickets_sold": ticket_summary.get("ticketsSold", ""),
        "total_orders": ticket_summary.get("totalOrders", ""),
        "revenue": (ticket_summary.get("revenue") or {}).get("value", ""),
        "total_sales": (ticket_summary.get("totalSales") or {}).get("value", ""),
        "rsvp_total": rsvp_summary.get("totalCount", ""),
        "rsvp_yes": rsvp_summary.get("yesCount", ""),
        "rsvp_no": rsvp_summary.get("noCount", ""),
        "rsvp_waitlist": rsvp_summary.get("waitlistCount", ""),
        "event_page_url": url,
        "main_image_url": (event.get("mainImage") or {}).get("url", ""),
        "created_date": event.get("createdDate", ""),
        "published_date": event.get("publishedDate", ""),
    }


def iter_all_events(client: WixClient, page_size: int = 50) -> Iterator[Dict[str, Any]]:
    """Yield every event sorted by start date, including drafts when permitted.

    ``includeDrafts`` needs the WIX_EVENTS.READ_DRAFT_EVENTS permission; if the
    API key doesn't have it, retry without drafts (published/ended/canceled only).
    """
    base_query = {"sort": [{"fieldName": "dateAndTimeSettings.startDate", "order": "ASC"}]}
    for include_drafts in (True, False):
        extra: Dict[str, Any] = {"fields": QUERY_FIELDS}
        if include_drafts:
            extra["includeDrafts"] = True
        try:
            yield from client._paged_post(
                "/events/v3/events/query",
                "events",
                base_query,
                page_size,
                extra_body=extra,
            )
            return
        except requests.exceptions.HTTPError as exc:
            if include_drafts and exc.response is not None and exc.response.status_code == 403:
                print("No READ_DRAFT_EVENTS permission - exporting without drafts.")
                continue
            raise


def main() -> int:
    parser = argparse.ArgumentParser(description="Export all Wix events to CSV.")
    parser.add_argument(
        "--site-id",
        default=None,
        help="Wix site ID to export from (defaults to WIX_SITE_ID in .env). "
        "Pass the production site ID to export real sales history.",
    )
    parser.add_argument(
        "-o", "--output", default="wix_events_export.csv", help="Output CSV path."
    )
    args = parser.parse_args()

    client = WixClient(site_id=args.site_id)
    print(f"Exporting events from site {client.site_id} ...")

    rows = [event_to_row(e) for e in iter_all_events(client)]

    output = Path(args.output)
    with output.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)

    print(f"Wrote {len(rows)} events to {output.resolve()}\n")

    statuses = Counter(r["status"] for r in rows)
    print("By status: " + ", ".join(f"{k}={v}" for k, v in statuses.most_common()))

    with_sales = [r for r in rows if r["tickets_sold"] not in ("", 0)]
    revenue = sum(float(r["revenue"]) for r in with_sales if r["revenue"])
    tickets = sum(int(r["tickets_sold"]) for r in with_sales)
    print(f"Events with ticket sales: {len(with_sales)} "
          f"({tickets} tickets, {revenue:,.2f} total revenue)")

    category_counts = Counter(
        cat.strip()
        for r in rows
        for cat in r["categories"].split(";")
        if cat.strip()
    )
    print("Top categories: " + ", ".join(f"{k}({v})" for k, v in category_counts.most_common(15)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
