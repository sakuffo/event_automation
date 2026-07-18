"""Notion data access layer.

All Notion I/O for the event pipeline lives here: database schemas, the
property-mapping between Notion pages and plain row dicts / ``EventRecord``,
and query/upsert helpers. Nothing in this module talks to Wix or Google —
callers in ``notion_orchestrator`` compose the two sides.

Uses the official ``notion-client`` SDK pinned to Notion API version
``2025-09-03`` (multi-source databases): database IDs are resolved to data
source IDs once and cached for the lifetime of the store.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any, Dict, Iterator, List, Optional, Tuple

try:  # pragma: no cover - standard library on Python 3.9+
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover
    ZoneInfo = None  # type: ignore

from notion_client import Client

from .config import AppConfig, ConfigError
from .logging_utils import get_logger
from .models import EventRecord, ValidationError

logger = get_logger(__name__)

NOTION_VERSION = "2025-09-03"

# Max characters per rich_text object; longer values are chunked.
_RICH_TEXT_CHUNK = 2000

# Delay between successive page writes to stay friendly with Notion's ~3 rps
# rate limit (the SDK also retries 429s automatically).
WRITE_DELAY_SECONDS = 0.35


# ---------------------------------------------------------------------------
# Status lifecycle
# ---------------------------------------------------------------------------

STATUS_IDEA = "Idea"
STATUS_DRAFT = "Draft"
STATUS_READY = "Ready"
STATUS_PUBLISHED = "Published"
STATUS_ERROR = "Error"
STATUS_SKIP = "Skip"
# Action statuses (humans set them, sync performs the Wix call)...
STATUS_UPDATE = "Update"
STATUS_CANCEL = "Cancel"
STATUS_DELETE = "Delete"
# ...and the terminal states sync writes back afterwards.
STATUS_CANCELLED = "Cancelled"
STATUS_REMOVED = "Removed"

ALL_STATUSES = [
    STATUS_IDEA,
    STATUS_DRAFT,
    STATUS_READY,
    STATUS_PUBLISHED,
    STATUS_UPDATE,
    STATUS_CANCEL,
    STATUS_CANCELLED,
    STATUS_DELETE,
    STATUS_REMOVED,
    STATUS_ERROR,
    STATUS_SKIP,
]

# Status select options with colors, shared by DB creation and the live-schema
# patch that adds newly introduced options to an existing Events DB.
STATUS_SELECT_OPTIONS = [
    {"name": STATUS_IDEA, "color": "gray"},
    {"name": STATUS_DRAFT, "color": "yellow"},
    {"name": STATUS_READY, "color": "blue"},
    {"name": STATUS_PUBLISHED, "color": "green"},
    {"name": STATUS_UPDATE, "color": "pink"},
    {"name": STATUS_CANCEL, "color": "orange"},
    {"name": STATUS_CANCELLED, "color": "brown"},
    {"name": STATUS_DELETE, "color": "red"},
    {"name": STATUS_REMOVED, "color": "purple"},
    {"name": STATUS_ERROR, "color": "red"},
    {"name": STATUS_SKIP, "color": "default"},
]

# Source values for Events rows (who created / last refreshed the row).
SOURCE_MANUAL = "manual"
SOURCE_WIX = "wix"
SOURCE_GCAL = "gcal"


# ---------------------------------------------------------------------------
# Catalog template types
# ---------------------------------------------------------------------------

# The Catalog DB (formerly "Classes") holds standardized templates: `class`
# rows (the original class catalog) and `event` rows (recurring non-class
# events like jams/parties/shows). Blank Type reads as `class` so rows created
# before the Type property existed keep their behavior.
TEMPLATE_TYPE_CLASS = "class"
TEMPLATE_TYPE_EVENT = "event"

TEMPLATE_TYPE_SELECT_OPTIONS = [
    {"name": TEMPLATE_TYPE_CLASS, "color": "blue"},
    {"name": TEMPLATE_TYPE_EVENT, "color": "purple"},
]


# ---------------------------------------------------------------------------
# Property names (single source of truth for every DB schema)
# ---------------------------------------------------------------------------

class EventProps:
    NAME = "Name"
    STATUS = "Status"
    DATE = "Date"
    CATEGORIES = "Categories"
    LOCATION = "Location"
    REGISTRATION_TYPE = "Registration Type"
    TICKET_PRICE = "Ticket Price"
    TICKET_NAMES = "Ticket Names"
    TICKET_PRICES = "Ticket Prices"
    TICKET_CAPACITIES = "Ticket Capacities"
    # Event-level max tickets per checkout (Wix ticketLimitPerOrder, 1-50).
    TICKET_LIMIT_PER_ORDER = "Ticket Limit Per Order"
    # PER_TICKET / PER_ORDER (Wix guestsAssignedSeparately); blank = not
    # managed, the Wix dashboard setting is left alone.
    CHECKOUT_FORM = "Checkout Form"
    FEE_TYPE = "Fee Type"
    SALE_START = "Sale Start"
    SALE_END = "Sale End"
    TAX_NAME = "Tax Name"
    TAX_RATE = "Tax Rate"
    TAX_TYPE = "Tax Type"
    INSTRUCTOR = "Instructor"
    MODEL = "Model"
    TEASER = "Teaser"
    DESCRIPTION = "Description"
    IMAGE_URL = "Image URL"
    TEMPLATE = "Template"
    WIX_EVENT_ID = "Wix Event ID"
    LAST_SYNCED = "Last Synced"
    SYNCED_HASH = "Synced Hash"
    SYNC_ERROR = "Sync Error"
    # Read-only: does the live event's tickets carry the Settings
    # default_ticket_policy? Written by sync/pull, never by humans.
    TICKET_POLICY_STATUS = "Ticket Policy Status"
    SOURCE = "Source"
    EXTERNAL_REF = "External Ref"


class TemplateProps:
    """Properties of the Catalog DB (class + recurring-event templates)."""

    NAME = "Template"
    TYPE = "Type"
    CATEGORIES = "Categories"
    TAGLINE = "Tagline"
    DESCRIPTION = "Description"
    IMAGE_URL = "Image URL"
    PRICE_OVERRIDE = "Price Override"
    DEFAULT_START_TIME = "Default Start Time"
    DEFAULT_END_TIME = "Default End Time"
    DEFAULT_INSTRUCTOR = "Default Instructor"
    # Semicolon-separated ticket defaults mirroring the Event Scheduling
    # Ticket Names/Prices/Capacities columns; filled onto blank ticketed rows.
    DEFAULT_TICKET_NAMES = "Default Ticket Names"
    DEFAULT_TICKET_PRICES = "Default Ticket Prices"
    DEFAULT_TICKET_CAPACITIES = "Default Ticket Capacities"


class SettingProps:
    KEY = "Key"
    VALUE = "Value"
    NOTES = "Notes"


class SiteConfigProps:
    NAME = "Name"
    SETTING_TYPE = "Setting Type"
    REGION = "Region"
    TAX_NAME = "Tax Name"
    TAX_TYPE = "Tax Type"
    TAX_RATE = "Tax Rate"
    REGION_ID = "Region ID"
    GROUP_ID = "Group ID"
    MAPPING_ID = "Mapping ID"
    REVISION = "Revision"


# Human-facing select value; EventRecord normalizes TICKETS -> TICKETING.
REGISTRATION_TYPE_OPTIONS = ["TICKETS", "RSVP", "EXTERNAL", "NO_REGISTRATION"]

# Checkout Form select values (Wix guestsAssignedSeparately: true/false).
CHECKOUT_FORM_OPTIONS = ["PER_TICKET", "PER_ORDER"]


def _events_db_properties(catalog_data_source_id: Optional[str]) -> Dict[str, Any]:
    """Schema for the Events database."""
    props: Dict[str, Any] = {
        EventProps.NAME: {"title": {}},
        EventProps.STATUS: {"select": {"options": list(STATUS_SELECT_OPTIONS)}},
        EventProps.DATE: {"date": {}},
        EventProps.CATEGORIES: {"multi_select": {}},
        EventProps.LOCATION: {"rich_text": {}},
        EventProps.REGISTRATION_TYPE: {
            "select": {
                "options": [{"name": name} for name in REGISTRATION_TYPE_OPTIONS]
            }
        },
        EventProps.TICKET_PRICE: {"number": {"format": "canadian_dollar"}},
        EventProps.TICKET_NAMES: {"rich_text": {}},
        EventProps.TICKET_PRICES: {"rich_text": {}},
        EventProps.TICKET_CAPACITIES: {"rich_text": {}},
        EventProps.TICKET_LIMIT_PER_ORDER: {"number": {"format": "number"}},
        EventProps.CHECKOUT_FORM: {
            "select": {
                "options": [{"name": name} for name in CHECKOUT_FORM_OPTIONS]
            }
        },
        EventProps.FEE_TYPE: {"rich_text": {}},
        EventProps.SALE_START: {"rich_text": {}},
        EventProps.SALE_END: {"rich_text": {}},
        EventProps.TAX_NAME: {"rich_text": {}},
        EventProps.TAX_RATE: {"number": {"format": "number"}},
        EventProps.TAX_TYPE: {"rich_text": {}},
        EventProps.INSTRUCTOR: {"rich_text": {}},
        EventProps.MODEL: {"rich_text": {}},
        EventProps.TEASER: {"rich_text": {}},
        EventProps.DESCRIPTION: {"rich_text": {}},
        EventProps.IMAGE_URL: {"url": {}},
        EventProps.WIX_EVENT_ID: {"rich_text": {}},
        EventProps.LAST_SYNCED: {"date": {}},
        EventProps.SYNCED_HASH: {"rich_text": {}},
        EventProps.SYNC_ERROR: {"rich_text": {}},
        EventProps.TICKET_POLICY_STATUS: {"rich_text": {}},
        EventProps.SOURCE: {
            "select": {
                "options": [
                    {"name": SOURCE_MANUAL, "color": "default"},
                    {"name": SOURCE_WIX, "color": "blue"},
                    {"name": SOURCE_GCAL, "color": "orange"},
                ]
            }
        },
        EventProps.EXTERNAL_REF: {"rich_text": {}},
    }
    if catalog_data_source_id:
        props[EventProps.TEMPLATE] = {
            "relation": {
                "data_source_id": catalog_data_source_id,
                "single_property": {},
            }
        }
    return props


def _catalog_db_properties() -> Dict[str, Any]:
    return {
        TemplateProps.NAME: {"title": {}},
        TemplateProps.TYPE: {
            "select": {"options": list(TEMPLATE_TYPE_SELECT_OPTIONS)}
        },
        TemplateProps.CATEGORIES: {"multi_select": {}},
        TemplateProps.TAGLINE: {"rich_text": {}},
        TemplateProps.DESCRIPTION: {"rich_text": {}},
        TemplateProps.IMAGE_URL: {"url": {}},
        TemplateProps.PRICE_OVERRIDE: {"number": {"format": "canadian_dollar"}},
        # Default schedule/staffing for rows created from this template:
        # times are HH:MM strings applied when the row's Date lacks a time.
        TemplateProps.DEFAULT_START_TIME: {"rich_text": {}},
        TemplateProps.DEFAULT_END_TIME: {"rich_text": {}},
        TemplateProps.DEFAULT_INSTRUCTOR: {"rich_text": {}},
        TemplateProps.DEFAULT_TICKET_NAMES: {"rich_text": {}},
        TemplateProps.DEFAULT_TICKET_PRICES: {"rich_text": {}},
        TemplateProps.DEFAULT_TICKET_CAPACITIES: {"rich_text": {}},
    }


def _settings_db_properties() -> Dict[str, Any]:
    return {
        SettingProps.KEY: {"title": {}},
        SettingProps.VALUE: {"rich_text": {}},
        SettingProps.NOTES: {"rich_text": {}},
    }


def _site_config_db_properties() -> Dict[str, Any]:
    return {
        SiteConfigProps.NAME: {"title": {}},
        SiteConfigProps.SETTING_TYPE: {
            "select": {"options": [{"name": "tax_location"}]}
        },
        SiteConfigProps.REGION: {"rich_text": {}},
        SiteConfigProps.TAX_NAME: {"rich_text": {}},
        SiteConfigProps.TAX_TYPE: {"rich_text": {}},
        SiteConfigProps.TAX_RATE: {"number": {"format": "number"}},
        SiteConfigProps.REGION_ID: {"rich_text": {}},
        SiteConfigProps.GROUP_ID: {"rich_text": {}},
        SiteConfigProps.MAPPING_ID: {"rich_text": {}},
        SiteConfigProps.REVISION: {"rich_text": {}},
    }


# ---------------------------------------------------------------------------
# Property value builders (python -> Notion JSON)
# ---------------------------------------------------------------------------


def _chunk_text(text: str) -> List[Dict[str, Any]]:
    if not text:
        return []
    return [
        {"type": "text", "text": {"content": text[i : i + _RICH_TEXT_CHUNK]}}
        for i in range(0, len(text), _RICH_TEXT_CHUNK)
    ]


def p_title(text: str) -> Dict[str, Any]:
    return {"title": _chunk_text(text or "")}


def p_rich_text(text: Optional[str]) -> Dict[str, Any]:
    return {"rich_text": _chunk_text(text or "")}


def p_select(name: Optional[str]) -> Dict[str, Any]:
    if not name:
        return {"select": None}
    return {"select": {"name": _sanitize_option(name)}}


def p_multi_select(names: List[str]) -> Dict[str, Any]:
    return {
        "multi_select": [
            {"name": _sanitize_option(n)} for n in names if n and n.strip()
        ]
    }


def p_number(value: Optional[float]) -> Dict[str, Any]:
    return {"number": value}


def p_url(url: Optional[str]) -> Dict[str, Any]:
    return {"url": url or None}


def p_relation(page_ids: List[str]) -> Dict[str, Any]:
    return {"relation": [{"id": pid} for pid in page_ids]}


def p_date(
    start_date_iso: Optional[str],
    start_time: Optional[str] = None,
    end_date_iso: Optional[str] = None,
    end_time: Optional[str] = None,
    tz_name: Optional[str] = None,
) -> Dict[str, Any]:
    """Build a Notion date property value from date/time strings.

    When a time is present the value is written as a local datetime with the
    site timezone attached (no UTC offset in the string, per the Notion API).
    """
    if not start_date_iso:
        return {"date": None}

    value: Dict[str, Any] = {}
    if start_time:
        value["start"] = f"{start_date_iso}T{start_time}:00"
        if tz_name:
            value["time_zone"] = tz_name
    else:
        value["start"] = start_date_iso

    if end_date_iso:
        if end_time:
            value["end"] = f"{end_date_iso}T{end_time}:00"
        else:
            value["end"] = end_date_iso
    return {"date": value}


def _sanitize_option(name: str) -> str:
    # Notion select/multi_select option names cannot contain commas.
    return name.replace(",", " ").strip()[:100]


# ---------------------------------------------------------------------------
# Property value parsers (Notion JSON -> python)
# ---------------------------------------------------------------------------


def _prop(page: Dict[str, Any], name: str) -> Dict[str, Any]:
    return (page.get("properties") or {}).get(name) or {}


def v_plain_text(page: Dict[str, Any], name: str) -> str:
    prop = _prop(page, name)
    parts = prop.get("title") if prop.get("type") == "title" else prop.get("rich_text")
    if not parts:
        return ""
    return "".join(p.get("plain_text", "") for p in parts)


def v_select(page: Dict[str, Any], name: str) -> str:
    sel = _prop(page, name).get("select")
    return (sel or {}).get("name", "") or ""


def v_multi_select(page: Dict[str, Any], name: str) -> List[str]:
    values = _prop(page, name).get("multi_select") or []
    return [v.get("name", "") for v in values if v.get("name")]


def v_number(page: Dict[str, Any], name: str) -> Optional[float]:
    return _prop(page, name).get("number")


def v_url(page: Dict[str, Any], name: str) -> str:
    return _prop(page, name).get("url") or ""


def v_relation_ids(page: Dict[str, Any], name: str) -> List[str]:
    values = _prop(page, name).get("relation") or []
    return [v.get("id", "") for v in values if v.get("id")]


def v_date_raw(page: Dict[str, Any], name: str) -> Tuple[str, str, str]:
    """Return (start, end, time_zone) raw strings from a date property."""
    value = _prop(page, name).get("date") or {}
    return value.get("start") or "", value.get("end") or "", value.get("time_zone") or ""


def _parse_notion_datetime(raw: str, tz_name: str) -> Tuple[str, str]:
    """Split a Notion date/datetime string into (YYYY-MM-DD, HH:MM).

    Datetimes with a UTC offset are converted into ``tz_name``; naive
    datetimes (written with an explicit ``time_zone``) are taken as-is.
    Date-only values return an empty time part.
    """
    if not raw:
        return "", ""
    if "T" not in raw:
        return raw[:10], ""

    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return raw[:10], ""

    if dt.tzinfo is not None and ZoneInfo is not None:
        try:
            dt = dt.astimezone(ZoneInfo(tz_name))
        except Exception:  # pragma: no cover - unknown tz name
            pass
    return dt.strftime("%Y-%m-%d"), dt.strftime("%H:%M")


def _format_number(value: Optional[float]) -> str:
    """Format a Notion number as a clean string (no trailing ``.0``)."""
    if value is None:
        return ""
    if float(value) == int(value):
        return str(int(value))
    return str(value)


def normalize_rate_string(value: Optional[str]) -> str:
    """Normalize a percent-rate string: ``"13.0"`` -> ``"13"``; junk passes through."""
    text = (value or "").strip()
    if not text:
        return ""
    try:
        return _format_number(float(text))
    except ValueError:
        return text


def reg_type_to_select(reg_type: Optional[str]) -> str:
    """Wix registration type -> the human-facing select value (TICKETING -> TICKETS)."""
    normalized = (reg_type or "").strip().upper()
    return "TICKETS" if normalized == "TICKETING" else normalized


def _float_or_none(value: Any) -> Optional[float]:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def split_price(raw: Any) -> Tuple[Optional[float], str]:
    """Split a raw price value into ``(single_price_or_None, multi_price_text)``.

    Semicolon-separated multi-ticket prices stay text (routed to the Ticket
    Prices property); a single value parses to a number or None.
    """
    text = str(raw or "").strip()
    if ";" in text:
        return None, text
    return _float_or_none(text), ""


# ---------------------------------------------------------------------------
# Row-field -> property mapping (the write-side reverse of event_page_to_row)
# ---------------------------------------------------------------------------


_FIELD_TEXT_PROPS = {
    "location": "Location",
    "instructor": "Instructor",
    "short_description": "Teaser",
    "detailed_description": "Description",
    "tax_name": "Tax Name",
    "tax_type": "Tax Type",
    "fee_type": "Fee Type",
    "sync_error": "Sync Error",
}

_DATE_PART_FIELDS = frozenset({"start_date", "start_time", "end_date", "end_time"})


def event_property_for_field(
    row: Dict[str, Any], field: str, tz_name: str
) -> Tuple[str, Dict[str, Any]]:
    """Return ``(property name, payload)`` for one row field's current value.

    Fill helpers set a plain row field and delegate the Notion payload here,
    so field->property knowledge lives in this module alongside the read-side
    ``event_page_to_row``. Any date-part field rebuilds the composite Date
    property from all four parts of the row.
    """
    if field in _DATE_PART_FIELDS:
        return EventProps.DATE, p_date(
            (row.get("start_date") or "").strip(),
            (row.get("start_time") or "").strip() or None,
            (row.get("end_date") or "").strip() or None,
            (row.get("end_time") or "").strip() or None,
            tz_name=tz_name,
        )
    if field in _FIELD_TEXT_PROPS:
        return _FIELD_TEXT_PROPS[field], p_rich_text(row.get(field) or "")
    if field == "event_name":
        return EventProps.NAME, p_title(row.get("event_name") or "")
    if field == "status":
        return EventProps.STATUS, p_select(row.get("status") or None)
    if field == "registration_type":
        return EventProps.REGISTRATION_TYPE, p_select(
            row.get("registration_type") or None
        )
    if field == "categories":
        return EventProps.CATEGORIES, p_multi_select(
            [c.strip() for c in (row.get("categories") or "").split(";") if c.strip()]
        )
    if field == "ticket_limit_per_order":
        number = _float_or_none(row.get("ticket_limit_per_order"))
        if number is not None and number == int(number):
            number = int(number)
        return EventProps.TICKET_LIMIT_PER_ORDER, p_number(number)
    if field == "checkout_form":
        return EventProps.CHECKOUT_FORM, p_select(
            row.get("checkout_form") or None
        )
    if field == "ticket_name":
        return EventProps.TICKET_NAMES, p_rich_text(row.get("ticket_name") or "")
    if field == "ticket_capacity":
        return EventProps.TICKET_CAPACITIES, p_rich_text(
            row.get("ticket_capacity") or ""
        )
    if field == "ticket_price":
        # The row field merges the Ticket Price number and the Ticket Prices
        # text (see event_page_to_row): a semicolon list routes back to the
        # rich-text property, a single value to the number property.
        price_number, multi_price = split_price(row.get("ticket_price"))
        if multi_price:
            return EventProps.TICKET_PRICES, p_rich_text(multi_price)
        return EventProps.TICKET_PRICE, p_number(price_number)
    if field == "tax_rate":
        return EventProps.TAX_RATE, p_number(_float_or_none(row.get("tax_rate")))
    if field == "image_url":
        return EventProps.IMAGE_URL, p_url((row.get("image_url") or "").strip() or None)
    raise KeyError(f"No Events property mapping for row field '{field}'")


# ---------------------------------------------------------------------------
# Row <-> EventRecord adapters
# ---------------------------------------------------------------------------


def event_page_to_row(page: Dict[str, Any], tz_name: str) -> Dict[str, str]:
    """Flatten an Events page into a plain string-keyed row dict.

    Keys mirror the canonical column names used by the sheet readers so the
    row -> ``EventRecord`` conversion is shared between backends.
    """
    start_raw, end_raw, _ = v_date_raw(page, EventProps.DATE)
    start_date, start_time = _parse_notion_datetime(start_raw, tz_name)
    end_date, end_time = _parse_notion_datetime(end_raw, tz_name)

    price_number = v_number(page, EventProps.TICKET_PRICE)
    ticket_prices_text = v_plain_text(page, EventProps.TICKET_PRICES).strip()

    row = {
        "page_id": page.get("id", ""),
        "event_name": v_plain_text(page, EventProps.NAME).strip(),
        "status": v_select(page, EventProps.STATUS),
        "categories": "; ".join(v_multi_select(page, EventProps.CATEGORIES)),
        "start_date": start_date,
        "start_time": start_time,
        "end_date": end_date,
        "end_time": end_time,
        "location": v_plain_text(page, EventProps.LOCATION).strip(),
        "registration_type": v_select(page, EventProps.REGISTRATION_TYPE),
        "ticket_limit_per_order": _format_number(
            v_number(page, EventProps.TICKET_LIMIT_PER_ORDER)
        ),
        "checkout_form": v_select(page, EventProps.CHECKOUT_FORM),
        "ticket_price": ticket_prices_text or _format_number(price_number),
        "image_url": v_url(page, EventProps.IMAGE_URL),
        "short_description": v_plain_text(page, EventProps.TEASER),
        "detailed_description": v_plain_text(page, EventProps.DESCRIPTION),
        "ticket_name": v_plain_text(page, EventProps.TICKET_NAMES).strip(),
        "ticket_capacity": v_plain_text(page, EventProps.TICKET_CAPACITIES).strip(),
        "fee_type": v_plain_text(page, EventProps.FEE_TYPE).strip(),
        "sale_start": v_plain_text(page, EventProps.SALE_START).strip(),
        "sale_end": v_plain_text(page, EventProps.SALE_END).strip(),
        "tax_name": v_plain_text(page, EventProps.TAX_NAME).strip(),
        "tax_rate": _format_number(v_number(page, EventProps.TAX_RATE)),
        "tax_type": v_plain_text(page, EventProps.TAX_TYPE).strip(),
        "instructor": v_plain_text(page, EventProps.INSTRUCTOR).strip(),
        "model": v_plain_text(page, EventProps.MODEL).strip(),
        "wix_event_id": v_plain_text(page, EventProps.WIX_EVENT_ID).strip(),
        "synced_hash": v_plain_text(page, EventProps.SYNCED_HASH).strip(),
        "sync_error": v_plain_text(page, EventProps.SYNC_ERROR).strip(),
        "ticket_policy_status": v_plain_text(
            page, EventProps.TICKET_POLICY_STATUS
        ).strip(),
        "template_relation_ids": v_relation_ids(page, EventProps.TEMPLATE),
    }
    return row


def row_to_event_record(row: Dict[str, Any]) -> EventRecord:
    """Build an :class:`EventRecord` from a flattened row dict.

    Mirrors ``sheets.fetch_config_events`` semantics: semicolon multi-ticket
    prices collapse ``ticket_price`` to ``0.0`` with the raw string preserved,
    blank end date/time fall back to the start values, and registration type
    defaults to ``TICKETING``.
    """
    raw_price = str(row.get("ticket_price") or "").strip()
    price_number, multi_price = split_price(raw_price)
    ticket_price = 0.0 if multi_price else (price_number or 0.0)

    reg_type = (row.get("registration_type") or "").strip() or "TICKETING"

    return EventRecord(
        name=row.get("event_name") or "",
        category=row.get("categories") or "",
        event_type=row.get("event_type") or "",
        start_date=row.get("start_date") or "",
        start_time=row.get("start_time") or "",
        end_date=row.get("end_date") or row.get("start_date") or "",
        end_time=row.get("end_time") or row.get("start_time") or "",
        location=row.get("location") or "",
        ticket_price=ticket_price,
        ticket_limit_per_order=row.get("ticket_limit_per_order") or None,
        checkout_form=row.get("checkout_form") or None,
        registration_type=reg_type,
        image_url=row.get("image_url") or "",
        teaser=row.get("short_description") or "",
        description=row.get("detailed_description") or "",
        ticket_name=row.get("ticket_name") or "",
        ticket_price_raw=raw_price,
        ticket_capacity=row.get("ticket_capacity") or "",
        fee_type=row.get("fee_type") or "",
        sale_start=row.get("sale_start") or "",
        sale_end=row.get("sale_end") or "",
        tax_name=row.get("tax_name") or "",
        tax_rate=normalize_rate_string(row.get("tax_rate")),
        tax_type=row.get("tax_type") or "",
        notion_page_id=row.get("page_id") or None,
        wix_event_id=row.get("wix_event_id") or None,
        status=row.get("status") or None,
        synced_hash=row.get("synced_hash") or None,
    )


def _event_content_props(
    *,
    name: str,
    date_prop: Dict[str, Any],
    categories: str,
    location: str,
    reg_select: str,
    price_number: Optional[float],
    multi_price_text: str,
    ticket_name: Optional[str],
    ticket_capacity: Optional[str],
    ticket_limit_per_order: Optional[float],
    checkout_form: Optional[str],
    fee_type: Optional[str],
    sale_start: Optional[str],
    sale_end: Optional[str],
    tax_name: Optional[str],
    tax_rate_number: Optional[float],
    tax_type: Optional[str],
    teaser: Optional[str],
    description: Optional[str],
    image_url: Optional[str],
) -> Dict[str, Any]:
    """The single owner of the Events content-property shape.

    Both builders (validated record and raw row) feed their normalized values
    through here, so adding a property is a one-place change.
    """
    props: Dict[str, Any] = {
        EventProps.NAME: p_title(name),
        EventProps.DATE: date_prop,
        EventProps.CATEGORIES: p_multi_select(
            [c.strip() for c in (categories or "").split(";") if c.strip()]
        ),
        EventProps.LOCATION: p_rich_text(location),
        EventProps.REGISTRATION_TYPE: p_select(reg_select or None),
        EventProps.TICKET_PRICE: p_number(price_number),
        EventProps.TICKET_NAMES: p_rich_text(ticket_name),
        EventProps.TICKET_PRICES: p_rich_text(multi_price_text),
        EventProps.TICKET_CAPACITIES: p_rich_text(ticket_capacity),
        EventProps.TICKET_LIMIT_PER_ORDER: p_number(ticket_limit_per_order),
        EventProps.CHECKOUT_FORM: p_select(checkout_form or None),
        EventProps.FEE_TYPE: p_rich_text(fee_type),
        EventProps.SALE_START: p_rich_text(sale_start),
        EventProps.SALE_END: p_rich_text(sale_end),
        EventProps.TAX_NAME: p_rich_text(tax_name),
        EventProps.TAX_RATE: p_number(tax_rate_number),
        EventProps.TAX_TYPE: p_rich_text(tax_type),
        EventProps.TEASER: p_rich_text(teaser),
        EventProps.DESCRIPTION: p_rich_text(description),
        EventProps.IMAGE_URL: p_url(image_url or None),
    }
    return props


def event_properties_from_raw_row(row: Dict[str, Any], tz_name: str) -> Dict[str, Any]:
    """Build Events properties from a raw string row without validation.

    Used by ``pull`` for Wix events too incomplete to form an ``EventRecord``
    (missing dates/location) so they still show up in Notion, flagged with a
    Sync Error, instead of silently disappearing.
    """
    from .utils import convert_date_to_iso

    def _iso_or_blank(value: str) -> str:
        text = (value or "").strip()
        if not text:
            return ""
        try:
            return convert_date_to_iso(text)
        except ValueError:
            return ""

    start_iso = _iso_or_blank(row.get("start_date", ""))
    end_iso = _iso_or_blank(row.get("end_date", ""))
    price_number, multi_price = split_price(row.get("ticket_price"))

    return _event_content_props(
        name=row.get("event_name") or "(untitled)",
        date_prop=p_date(
            start_iso,
            (row.get("start_time") or "").strip() or None,
            end_iso or None,
            (row.get("end_time") or "").strip() or None,
            tz_name=tz_name,
        ),
        categories=row.get("categories") or "",
        location=row.get("location") or "",
        reg_select=reg_type_to_select(row.get("registration_type")),
        price_number=price_number,
        multi_price_text=multi_price,
        ticket_name=row.get("ticket_name") or "",
        ticket_capacity=row.get("ticket_capacity") or "",
        ticket_limit_per_order=_float_or_none(row.get("ticket_limit_per_order")),
        checkout_form=(row.get("checkout_form") or "").strip(),
        fee_type=row.get("fee_type") or "",
        sale_start=row.get("sale_start") or "",
        sale_end=row.get("sale_end") or "",
        tax_name=row.get("tax_name") or "",
        tax_rate_number=_float_or_none(row.get("tax_rate")),
        tax_type=row.get("tax_type") or "",
        teaser=row.get("short_description") or "",
        description=row.get("detailed_description") or "",
        image_url=(row.get("image_url") or "").strip(),
    )


def event_properties_from_record(
    record: EventRecord,
    tz_name: str,
    *,
    include_bookkeeping: bool = False,
) -> Dict[str, Any]:
    """Build the Notion properties payload for an Events page from a record."""
    price_number, multi_price = split_price(record.ticket_price_raw or "")
    if not multi_price and price_number is None:
        # Absent or unparseable single price: fall back to the derived number.
        price_number = record.ticket_price or None

    props = _event_content_props(
        name=record.name,
        date_prop=p_date(
            record.start_date,
            record.start_time,
            record.end_date,
            record.end_time,
            tz_name=tz_name,
        ),
        categories=record.category or "",
        location=record.location,
        reg_select=reg_type_to_select(record.registration_type or "TICKETING"),
        price_number=price_number,
        multi_price_text=multi_price,
        ticket_name=record.ticket_name,
        ticket_capacity=record.ticket_capacity,
        ticket_limit_per_order=record.ticket_limit_per_order,
        checkout_form=record.checkout_form,
        fee_type=record.fee_type,
        sale_start=record.sale_start,
        sale_end=record.sale_end,
        tax_name=record.tax_name,
        tax_rate_number=_float_or_none(record.tax_rate),
        tax_type=record.tax_type,
        teaser=record.teaser,
        description=record.description,
        image_url=record.image_url,
    )

    if include_bookkeeping:
        props[EventProps.WIX_EVENT_ID] = p_rich_text(record.wix_event_id)
        props[EventProps.SYNCED_HASH] = p_rich_text(record.synced_hash)
        props[EventProps.TICKET_POLICY_STATUS] = p_rich_text(
            record.ticket_policy_status
        )
        if record.status:
            props[EventProps.STATUS] = p_select(record.status)

    return props


# ---------------------------------------------------------------------------
# The store
# ---------------------------------------------------------------------------


def _last_synced_prop() -> Dict[str, Any]:
    return {"date": {"start": datetime.now(timezone.utc).isoformat()}}


class NotionStoreError(RuntimeError):
    """Raised when a Notion operation fails in a way callers should surface."""


def _is_transient_notion_error(exc: Exception) -> bool:
    """Gateway hiccups and connection drops — worth one more try."""
    status = getattr(exc, "status", None)
    if status in (502, 503, 504):
        return True
    return type(exc).__name__ in {
        "ConnectError",
        "ConnectTimeout",
        "ReadTimeout",
        "ReadError",
        "RemoteProtocolError",
    }


class NotionStore:
    """Thin, synchronous wrapper over the Notion API for our four databases."""

    RETRY_ATTEMPTS = 3

    def __init__(self, config: AppConfig):
        if not config.notion_token:
            raise ConfigError("NOTION_ACCESS_TOKEN is missing")
        self.config = config
        self.client = Client(
            auth=config.notion_token,
            notion_version=NOTION_VERSION,
        )
        self._data_source_ids: Dict[str, str] = {}

    # -- plumbing ----------------------------------------------------------

    @staticmethod
    def _require_db(db_id: Optional[str], env_name: str) -> str:
        if not db_id:
            raise ConfigError(f"{env_name} is missing")
        return db_id

    def _api(self, description: str, call, *, retry: bool = True):
        """Run one SDK call with transient-error retry and error context.

        The SDK already retries 429s; this covers gateway 5xx and dropped
        connections. ``retry=False`` for page creation — retrying a create
        that actually landed server-side would duplicate the row. Failures
        surface as :class:`NotionStoreError` so per-row callers can contain
        them without catching bare ``Exception``.
        """
        attempts = self.RETRY_ATTEMPTS if retry else 1
        for attempt in range(attempts):
            try:
                return call()
            except Exception as exc:
                if attempt < attempts - 1 and _is_transient_notion_error(exc):
                    wait = 1.5 * (attempt + 1)
                    logger.warning(
                        "Notion %s failed transiently (%s) — retrying in %.1fs",
                        description, exc, wait,
                    )
                    time.sleep(wait)
                    continue
                raise NotionStoreError(f"Notion {description} failed: {exc}") from exc

    def data_source_id(self, database_id: str) -> str:
        """Resolve (and cache) the data source id for a database id."""
        if not database_id:
            raise NotionStoreError("Database id is not configured")
        cached = self._data_source_ids.get(database_id)
        if cached:
            return cached

        database = self._api(
            f"database retrieve {database_id}",
            lambda: self.client.databases.retrieve(database_id=database_id),
        )
        sources = database.get("data_sources") or []
        if not sources:
            raise NotionStoreError(
                f"Database {database_id} has no data sources (unexpected)"
            )
        ds_id = sources[0]["id"]
        self._data_source_ids[database_id] = ds_id
        return ds_id

    def iter_pages(
        self,
        database_id: str,
        filter_: Optional[Dict[str, Any]] = None,
    ) -> Iterator[Dict[str, Any]]:
        """Yield every page of a database (first data source), paginated."""
        ds_id = self.data_source_id(database_id)
        cursor: Optional[str] = None
        while True:
            kwargs: Dict[str, Any] = {"data_source_id": ds_id, "page_size": 100}
            if filter_ is not None:
                kwargs["filter"] = filter_
            if cursor:
                kwargs["start_cursor"] = cursor
            response = self._api(
                "data source query",
                lambda: self.client.data_sources.query(**kwargs),
            )
            for page in response.get("results", []):
                yield page
            if not response.get("has_more"):
                break
            cursor = response.get("next_cursor")

    def create_page(
        self, database_id: str, properties: Dict[str, Any]
    ) -> Dict[str, Any]:
        ds_id = self.data_source_id(database_id)
        page = self._api(
            "page create",
            lambda: self.client.pages.create(
                parent={"type": "data_source_id", "data_source_id": ds_id},
                properties=properties,
            ),
            retry=False,
        )
        time.sleep(WRITE_DELAY_SECONDS)
        return page

    def update_page(self, page_id: str, properties: Dict[str, Any]) -> Dict[str, Any]:
        page = self._api(
            f"page update {page_id}",
            lambda: self.client.pages.update(page_id=page_id, properties=properties),
        )
        time.sleep(WRITE_DELAY_SECONDS)
        return page

    # -- database creation (setup-notion) -----------------------------------

    def _ensure_select_options(
        self, db_id: str, prop_name: str, wanted: List[Dict[str, str]]
    ) -> int:
        """Append any missing options to a live select property.

        Existing options (and their ids/colors) are preserved untouched —
        patching is additive only. Returns how many options were added.
        """
        ds_id = self.data_source_id(db_id)
        data_source = self._api(
            f"data source retrieve {ds_id}",
            lambda: self.client.data_sources.retrieve(data_source_id=ds_id),
        )
        prop = (data_source.get("properties") or {}).get(prop_name) or {}
        existing_options = (prop.get("select") or {}).get("options") or []
        existing_names = {opt.get("name") for opt in existing_options}

        missing = [opt for opt in wanted if opt["name"] not in existing_names]
        if not missing:
            return 0

        self._api(
            f"schema update {prop_name}",
            lambda: self.client.data_sources.update(
                data_source_id=ds_id,
                properties={
                    prop_name: {"select": {"options": existing_options + missing}}
                },
            ),
        )
        return len(missing)

    def _ensure_properties(self, db_id: str, schema: Dict[str, Any]) -> List[str]:
        """Add schema properties wholly missing from a live database.

        Existing properties (and their values) are never touched. Returns
        the sorted names added.
        """
        ds_id = self.data_source_id(db_id)
        data_source = self._api(
            f"data source retrieve {ds_id}",
            lambda: self.client.data_sources.retrieve(data_source_id=ds_id),
        )
        live = data_source.get("properties") or {}

        missing = {
            name: definition
            for name, definition in schema.items()
            if name not in live
        }
        if not missing:
            return []

        self._api(
            "schema update (missing properties)",
            lambda: self.client.data_sources.update(
                data_source_id=ds_id, properties=missing
            ),
        )
        return sorted(missing)

    def ensure_event_status_options(self) -> int:
        """Add any missing Status select options to the live Events DB schema."""
        db_id = self._require_db(
            self.config.notion_event_scheduling_db_id, "NOTION_EVENT_SCHEDULING_DB_ID"
        )
        return self._ensure_select_options(
            db_id, EventProps.STATUS, STATUS_SELECT_OPTIONS
        )

    def ensure_template_type_options(self) -> int:
        """Add the Type select options (class/event) to the live Catalog DB schema."""
        db_id = self._require_db(
            self.config.notion_catalog_db_id, "NOTION_CATALOG_DB_ID"
        )
        return self._ensure_select_options(
            db_id, TemplateProps.TYPE, TEMPLATE_TYPE_SELECT_OPTIONS
        )

    def ensure_catalog_properties(self) -> List[str]:
        """Add any Catalog schema properties missing from the live database."""
        db_id = self._require_db(
            self.config.notion_catalog_db_id, "NOTION_CATALOG_DB_ID"
        )
        return self._ensure_properties(db_id, _catalog_db_properties())

    def ensure_event_properties(self) -> List[str]:
        """Add any Events schema properties missing from the live database.

        The Events DB previously had no property patcher at all — a property
        added to ``_events_db_properties`` after a database was created would
        silently never exist there (reads blank, writes 400). The Template
        relation needs the Catalog data source id; when the catalog is not
        configured or resolvable, that one property is skipped.
        """
        db_id = self._require_db(
            self.config.notion_event_scheduling_db_id, "NOTION_EVENT_SCHEDULING_DB_ID"
        )
        catalog_ds_id: Optional[str] = None
        if self.config.notion_catalog_db_id:
            try:
                catalog_ds_id = self.data_source_id(self.config.notion_catalog_db_id)
            except Exception as exc:
                logger.warning(
                    "Could not resolve the Catalog data source (%s) — "
                    "skipping the Template relation in the schema patch", exc,
                )
        return self._ensure_properties(db_id, _events_db_properties(catalog_ds_id))

    def _rename_property(
        self, database_id: str, old_name: str, new_name: str
    ) -> bool:
        """Rename a property on a live database if the old name is present.

        No-op when the property was already renamed (or never existed).
        Returns True when a rename was performed.
        """
        ds_id = self.data_source_id(database_id)
        data_source = self.client.data_sources.retrieve(data_source_id=ds_id)
        properties = data_source.get("properties") or {}
        if old_name not in properties or new_name in properties:
            return False
        self.client.data_sources.update(
            data_source_id=ds_id,
            properties={old_name: {"name": new_name}},
        )
        return True

    def _rename_database_title(
        self, database_id: str, old_title: str, new_title: str
    ) -> bool:
        """Rename a database title if it currently matches ``old_title``."""
        database = self._api(
            f"database retrieve {database_id}",
            lambda: self.client.databases.retrieve(database_id=database_id),
        )
        title_text = "".join(
            t.get("plain_text", "") for t in database.get("title") or []
        ).strip()
        if title_text != old_title:
            return False
        self.client.databases.update(
            database_id=database_id,
            title=[{"type": "text", "text": {"content": new_title}}],
        )
        return True

    def migrate_naming(self) -> List[str]:
        """One-time in-place renames from the catalog/scheduling redesigns.

        - Catalog DB title property ``Class`` -> ``Template``
        - Events DB relation property ``Class`` -> ``Template``
        - Catalog database title ``Classes`` -> ``Catalog``
        - Events database title ``Events`` -> ``Event Scheduling``

        Idempotent: each step is skipped once the new name is in place.
        Returns human-readable labels of the changes performed.
        """
        changes: List[str] = []

        catalog_db = self.config.notion_catalog_db_id
        if catalog_db:
            if self._rename_property(catalog_db, "Class", TemplateProps.NAME):
                changes.append("Catalog title property: Class -> Template")
            if self._rename_database_title(catalog_db, "Classes", "Catalog"):
                changes.append("Database title: Classes -> Catalog")

        scheduling_db = self.config.notion_event_scheduling_db_id
        if scheduling_db:
            if self._rename_property(scheduling_db, "Class", EventProps.TEMPLATE):
                changes.append("Event Scheduling relation property: Class -> Template")
            if self._rename_database_title(
                scheduling_db, "Events", "Event Scheduling"
            ):
                changes.append("Database title: Events -> Event Scheduling")

        return changes

    def create_database(
        self,
        parent_page_id: str,
        title: str,
        properties: Dict[str, Any],
    ) -> Tuple[str, str]:
        """Create a database under a page; return ``(database_id, data_source_id)``."""
        response = self.client.databases.create(
            parent={"type": "page_id", "page_id": parent_page_id},
            title=[{"type": "text", "text": {"content": title}}],
            initial_data_source={"properties": properties},
        )
        db_id = response["id"]
        sources = response.get("data_sources") or []
        if sources:
            ds_id = sources[0]["id"]
        else:  # pragma: no cover - defensive re-fetch
            ds_id = self.data_source_id(db_id)
        self._data_source_ids[db_id] = ds_id
        return db_id, ds_id

    def setup_databases(self, parent_page_id: str) -> Dict[str, str]:
        """Create any missing databases; return env-var name -> database id."""
        results: Dict[str, str] = {}

        catalog_db = self.config.notion_catalog_db_id
        if catalog_db:
            logger.info("Catalog DB already configured (%s)", catalog_db)
        else:
            catalog_db, _ = self.create_database(
                parent_page_id, "Catalog", _catalog_db_properties()
            )
            logger.info("Created Catalog DB: %s", catalog_db)
        results["NOTION_CATALOG_DB_ID"] = catalog_db

        scheduling_db = self.config.notion_event_scheduling_db_id
        if scheduling_db:
            logger.info("Event Scheduling DB already configured (%s)", scheduling_db)
        else:
            catalog_ds_id = self.data_source_id(catalog_db)
            scheduling_db, _ = self.create_database(
                parent_page_id,
                "Event Scheduling",
                _events_db_properties(catalog_ds_id),
            )
            logger.info("Created Event Scheduling DB: %s", scheduling_db)
        results["NOTION_EVENT_SCHEDULING_DB_ID"] = scheduling_db

        settings_db = self.config.notion_settings_db_id
        if settings_db:
            logger.info("Settings DB already configured (%s)", settings_db)
        else:
            settings_db, _ = self.create_database(
                parent_page_id, "Settings", _settings_db_properties()
            )
            logger.info("Created Settings DB: %s", settings_db)
        results["NOTION_SETTINGS_DB_ID"] = settings_db

        site_db = self.config.notion_site_config_db_id
        if site_db:
            logger.info("Site Config DB already configured (%s)", site_db)
        else:
            site_db, _ = self.create_database(
                parent_page_id, "Site Config", _site_config_db_properties()
            )
            logger.info("Created Site Config DB: %s", site_db)
        results["NOTION_SITE_CONFIG_DB_ID"] = site_db

        return results

    # -- events --------------------------------------------------------------

    def fetch_event_rows(
        self,
        statuses: Optional[List[str]] = None,
        include_missing_status: bool = False,
    ) -> List[Dict[str, Any]]:
        """Fetch Events rows, optionally filtered to a set of Status values.

        ``include_missing_status`` also matches rows whose Status is empty —
        used by enrich so freshly created rows without a status are picked up
        and bootstrapped to Idea.
        """
        db_id = self._require_db(self.config.notion_event_scheduling_db_id, "NOTION_EVENT_SCHEDULING_DB_ID")

        filter_: Optional[Dict[str, Any]] = None
        if statuses:
            clauses = [
                {"property": EventProps.STATUS, "select": {"equals": s}}
                for s in statuses
            ]
            if include_missing_status:
                clauses.append(
                    {"property": EventProps.STATUS, "select": {"is_empty": True}}
                )
            filter_ = clauses[0] if len(clauses) == 1 else {"or": clauses}

        tz_name = self.config.timezone
        rows = [event_page_to_row(page, tz_name) for page in self.iter_pages(db_id, filter_)]
        logger.info(
            "Fetched %d event row(s) from Notion%s",
            len(rows),
            f" (status in {statuses})" if statuses else "",
        )
        return rows

    def write_sync_result(
        self,
        page_id: str,
        *,
        status: Optional[str] = None,
        wix_event_id: Optional[str] = None,
        synced_hash: Optional[str] = None,
        error: Optional[str] = None,
        source: Optional[str] = None,
        ticket_policy_status: Optional[str] = None,
    ) -> None:
        """Write sync bookkeeping back onto an Events row.

        ``error=None`` clears the Sync Error property; pass a message to set it.
        ``ticket_policy_status=None`` leaves the column untouched; pass ``""``
        to clear it.
        """
        props: Dict[str, Any] = {
            EventProps.LAST_SYNCED: _last_synced_prop(),
            EventProps.SYNC_ERROR: p_rich_text(error or ""),
        }
        if status:
            props[EventProps.STATUS] = p_select(status)
        if wix_event_id is not None:
            props[EventProps.WIX_EVENT_ID] = p_rich_text(wix_event_id)
        if synced_hash is not None:
            props[EventProps.SYNCED_HASH] = p_rich_text(synced_hash)
        if source:
            props[EventProps.SOURCE] = p_select(source)
        if ticket_policy_status is not None:
            props[EventProps.TICKET_POLICY_STATUS] = p_rich_text(
                ticket_policy_status
            )
        self.update_page(page_id, props)

    def update_event_fields(self, page_id: str, props: Dict[str, Any]) -> None:
        """Update arbitrary Events properties (used by enrich write-backs)."""
        if props:
            self.update_page(page_id, props)

    def upsert_event_from_record(
        self,
        record: EventRecord,
        *,
        status: str,
        source: str = SOURCE_MANUAL,
        page_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Create or fully refresh an Events row from a record (used by pull)."""
        props = event_properties_from_record(
            record, self.config.timezone, include_bookkeeping=True
        )
        return self._stamp_and_write_event(
            props, status=status, source=source, error="", page_id=page_id
        )

    def upsert_event_from_raw_row(
        self,
        row: Dict[str, Any],
        *,
        status: str,
        source: str = SOURCE_MANUAL,
        wix_event_id: str = "",
        error: str = "",
        page_id: Optional[str] = None,
        ticket_policy_status: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Create/refresh an Events row from raw strings, skipping validation.

        Lets ``pull`` land incomplete Wix events (no date, no location) in
        Notion with a Sync Error note instead of dropping them.
        ``ticket_policy_status=None`` leaves that column untouched.
        """
        props = event_properties_from_raw_row(row, self.config.timezone)
        props[EventProps.WIX_EVENT_ID] = p_rich_text(wix_event_id)
        if ticket_policy_status is not None:
            props[EventProps.TICKET_POLICY_STATUS] = p_rich_text(
                ticket_policy_status
            )
        return self._stamp_and_write_event(
            props, status=status, source=source, error=error, page_id=page_id
        )

    def _stamp_and_write_event(
        self,
        props: Dict[str, Any],
        *,
        status: str,
        source: str,
        error: str,
        page_id: Optional[str],
    ) -> Dict[str, Any]:
        """Shared bookkeeping stamp + create-or-update tail for Events upserts."""
        props[EventProps.STATUS] = p_select(status)
        props[EventProps.SOURCE] = p_select(source)
        props[EventProps.SYNC_ERROR] = p_rich_text(error)
        props[EventProps.LAST_SYNCED] = _last_synced_prop()
        db_id = self._require_db(
            self.config.notion_event_scheduling_db_id,
            "NOTION_EVENT_SCHEDULING_DB_ID",
        )
        if page_id:
            return self.update_page(page_id, props)
        return self.create_page(db_id, props)

    # -- classes --------------------------------------------------------------

    def fetch_classes(self) -> Dict[str, Dict[str, Any]]:
        """Return Classes rows indexed by lowercased class title."""
        db_id = self._require_db(self.config.notion_catalog_db_id, "NOTION_CATALOG_DB_ID")

        classes: Dict[str, Dict[str, Any]] = {}
        for page in self.iter_pages(db_id):
            title = v_plain_text(page, TemplateProps.NAME).strip()
            if not title:
                continue
            classes[title.lower()] = {
                "page_id": page.get("id", ""),
                "class": title,
                "type": v_select(page, TemplateProps.TYPE) or TEMPLATE_TYPE_CLASS,
                "categories": v_multi_select(page, TemplateProps.CATEGORIES),
                "tagline": v_plain_text(page, TemplateProps.TAGLINE),
                "description": v_plain_text(page, TemplateProps.DESCRIPTION),
                "image_url": v_url(page, TemplateProps.IMAGE_URL),
                "price_override": v_number(page, TemplateProps.PRICE_OVERRIDE),
                "default_start_time": v_plain_text(
                    page, TemplateProps.DEFAULT_START_TIME
                ).strip(),
                "default_end_time": v_plain_text(
                    page, TemplateProps.DEFAULT_END_TIME
                ).strip(),
                "default_instructor": v_plain_text(
                    page, TemplateProps.DEFAULT_INSTRUCTOR
                ).strip(),
                "default_ticket_names": v_plain_text(
                    page, TemplateProps.DEFAULT_TICKET_NAMES
                ).strip(),
                "default_ticket_prices": v_plain_text(
                    page, TemplateProps.DEFAULT_TICKET_PRICES
                ).strip(),
                "default_ticket_capacities": v_plain_text(
                    page, TemplateProps.DEFAULT_TICKET_CAPACITIES
                ).strip(),
            }
        logger.info("Fetched %d class definition(s) from Notion", len(classes))
        return classes

    def upsert_class(
        self,
        *,
        name: str,
        categories: List[str],
        tagline: str,
        description: str,
        image_url: str,
        template_type: Optional[str] = None,
        price_override: Optional[float] = None,
        default_start_time: Optional[str] = None,
        default_end_time: Optional[str] = None,
        default_instructor: Optional[str] = None,
        default_ticket_names: Optional[str] = None,
        default_ticket_prices: Optional[str] = None,
        default_ticket_capacities: Optional[str] = None,
        existing_page_id: Optional[str] = None,
    ) -> str:
        """Create or update a catalog row (class or event template).

        The optional keyword fields are only written when provided, so the
        original import-classes path leaves them untouched.
        """
        props = {
            TemplateProps.NAME: p_title(name),
            TemplateProps.CATEGORIES: p_multi_select(categories),
            TemplateProps.TAGLINE: p_rich_text(tagline),
            TemplateProps.DESCRIPTION: p_rich_text(description),
            TemplateProps.IMAGE_URL: p_url(image_url),
        }
        if template_type is not None:
            props[TemplateProps.TYPE] = p_select(template_type)
        if price_override is not None:
            props[TemplateProps.PRICE_OVERRIDE] = p_number(price_override)
        if default_start_time is not None:
            props[TemplateProps.DEFAULT_START_TIME] = p_rich_text(default_start_time)
        if default_end_time is not None:
            props[TemplateProps.DEFAULT_END_TIME] = p_rich_text(default_end_time)
        if default_instructor is not None:
            props[TemplateProps.DEFAULT_INSTRUCTOR] = p_rich_text(default_instructor)
        if default_ticket_names is not None:
            props[TemplateProps.DEFAULT_TICKET_NAMES] = p_rich_text(default_ticket_names)
        if default_ticket_prices is not None:
            props[TemplateProps.DEFAULT_TICKET_PRICES] = p_rich_text(default_ticket_prices)
        if default_ticket_capacities is not None:
            props[TemplateProps.DEFAULT_TICKET_CAPACITIES] = p_rich_text(
                default_ticket_capacities
            )
        if existing_page_id:
            self.update_page(existing_page_id, props)
            return existing_page_id
        db_id = self._require_db(self.config.notion_catalog_db_id, "NOTION_CATALOG_DB_ID")
        page = self.create_page(db_id, props)
        return page.get("id", "")

    # -- settings --------------------------------------------------------------

    def fetch_settings(self) -> Dict[str, str]:
        """Return Settings rows as a lowercase key -> value dict.

        Rows with a blank Value are kept (as ``""``) — callers treat blank
        and missing the same, and dropping them would make the setup-notion
        seeder re-create deliberately-blank rows (e.g. an unset
        ``default_ticket_policy``) on every run.
        """
        db_id = self._require_db(self.config.notion_settings_db_id, "NOTION_SETTINGS_DB_ID")

        settings: Dict[str, str] = {}
        for page in self.iter_pages(db_id):
            key = v_plain_text(page, SettingProps.KEY).strip().lower()
            value = v_plain_text(page, SettingProps.VALUE).strip()
            if key:
                settings[key] = value
        logger.info("Fetched %d setting(s) from Notion", len(settings))
        return settings

    _SCAN_FOR_EXISTING = object()

    def upsert_setting(
        self,
        key: str,
        value: str,
        notes: str = "",
        existing_page_id: Any = _SCAN_FOR_EXISTING,
    ) -> None:
        """Create or update a Settings row.

        Callers that already know the key is absent (e.g. the seeder, which
        checks ``fetch_settings`` first) pass ``existing_page_id=None`` to
        skip the per-key DB scan.
        """
        db_id = self._require_db(self.config.notion_settings_db_id, "NOTION_SETTINGS_DB_ID")

        if existing_page_id is self._SCAN_FOR_EXISTING:
            existing_page_id = None
            for page in self.iter_pages(db_id):
                if v_plain_text(page, SettingProps.KEY).strip().lower() == key.lower():
                    existing_page_id = page.get("id")
                    break

        props = {
            SettingProps.KEY: p_title(key),
            SettingProps.VALUE: p_rich_text(value),
        }
        if notes:
            props[SettingProps.NOTES] = p_rich_text(notes)

        if existing_page_id:
            self.update_page(existing_page_id, props)
        else:
            self.create_page(db_id, props)

    # -- site config -------------------------------------------------------------

    def fetch_site_config_rows(self) -> List[Dict[str, str]]:
        """Return Site Config rows shaped like the sheet reader's dicts."""
        db_id = self._require_db(self.config.notion_site_config_db_id, "NOTION_SITE_CONFIG_DB_ID")

        rows: List[Dict[str, str]] = []
        for page in self.iter_pages(db_id):
            row = {
                "page_id": page.get("id", ""),
                "setting_type": v_select(page, SiteConfigProps.SETTING_TYPE),
                "jurisdiction": v_plain_text(page, SiteConfigProps.NAME).strip(),
                "region": v_plain_text(page, SiteConfigProps.REGION).strip(),
                "tax_name": v_plain_text(page, SiteConfigProps.TAX_NAME).strip(),
                "tax_type": v_plain_text(page, SiteConfigProps.TAX_TYPE).strip(),
                "tax_rate": _format_number(v_number(page, SiteConfigProps.TAX_RATE)),
                "region_id": v_plain_text(page, SiteConfigProps.REGION_ID).strip(),
                "group_id": v_plain_text(page, SiteConfigProps.GROUP_ID).strip(),
                "mapping_id": v_plain_text(page, SiteConfigProps.MAPPING_ID).strip(),
                "revision": v_plain_text(page, SiteConfigProps.REVISION).strip(),
            }
            if not row["region_id"] and not row["mapping_id"]:
                continue
            rows.append(row)
        logger.info("Fetched %d site config row(s) from Notion", len(rows))
        return rows

    def index_site_config_pages(self) -> Tuple[Dict[str, Any], Dict[Tuple[str, str], Any]]:
        """One paginated pass over the Site Config DB, indexed for upserts.

        Returns ``(by_mapping_id, by_region_group)``; pages that carry a
        mapping id are excluded from the region+group index (mapping id wins),
        and duplicate keys keep the first page — the same precedence the
        per-row scan used.
        """
        db_id = self._require_db(self.config.notion_site_config_db_id, "NOTION_SITE_CONFIG_DB_ID")

        by_mapping: Dict[str, Any] = {}
        by_region_group: Dict[Tuple[str, str], Any] = {}
        for page in self.iter_pages(db_id):
            mapping_id = v_plain_text(page, SiteConfigProps.MAPPING_ID).strip()
            if mapping_id:
                by_mapping.setdefault(mapping_id, page)
                continue
            region_group = (
                v_plain_text(page, SiteConfigProps.REGION_ID).strip(),
                v_plain_text(page, SiteConfigProps.GROUP_ID).strip(),
            )
            if region_group != ("", ""):
                by_region_group.setdefault(region_group, page)
        return by_mapping, by_region_group

    @staticmethod
    def _site_config_rate_number(row: Dict[str, Any]) -> Optional[float]:
        return _float_or_none(row.get("tax_rate"))

    @classmethod
    def _site_config_page_matches(cls, page: Dict[str, Any], row: Dict[str, Any]) -> bool:
        """True when writing ``row`` to ``page`` would change nothing.

        Strict equality on every written field — any doubt means write.
        """
        return (
            v_plain_text(page, SiteConfigProps.NAME)
            == (row.get("jurisdiction") or row.get("region") or "(unknown region)")
            and v_select(page, SiteConfigProps.SETTING_TYPE)
            == _sanitize_option(row.get("setting_type") or "tax_location")
            and v_plain_text(page, SiteConfigProps.REGION) == (row.get("region") or "")
            and v_plain_text(page, SiteConfigProps.TAX_NAME) == (row.get("tax_name") or "")
            and v_plain_text(page, SiteConfigProps.TAX_TYPE) == (row.get("tax_type") or "")
            and v_number(page, SiteConfigProps.TAX_RATE) == cls._site_config_rate_number(row)
            and v_plain_text(page, SiteConfigProps.REGION_ID) == (row.get("region_id") or "")
            and v_plain_text(page, SiteConfigProps.GROUP_ID) == (row.get("group_id") or "")
            and v_plain_text(page, SiteConfigProps.MAPPING_ID) == (row.get("mapping_id") or "")
            and v_plain_text(page, SiteConfigProps.REVISION) == str(row.get("revision") or "")
        )

    def upsert_site_config_row(
        self,
        row: Dict[str, Any],
        page_index: Optional[Tuple[Dict[str, Any], Dict[Tuple[str, str], Any]]] = None,
    ) -> str:
        """Create or update a Site Config row, keyed by mapping id then region+group.

        ``page_index`` (from :meth:`index_site_config_pages`) lets bulk
        callers avoid a full DB scan per row. Unchanged rows are not
        rewritten. Returns ``"created"``, ``"updated"``, or ``"unchanged"``.
        """
        db_id = self._require_db(self.config.notion_site_config_db_id, "NOTION_SITE_CONFIG_DB_ID")

        if page_index is None:
            page_index = self.index_site_config_pages()
        by_mapping, by_region_group = page_index

        mapping_id = (row.get("mapping_id") or "").strip()
        region_group = (
            (row.get("region_id") or "").strip(),
            (row.get("group_id") or "").strip(),
        )
        existing_page: Optional[Dict[str, Any]] = None
        if mapping_id and mapping_id in by_mapping:
            existing_page = by_mapping[mapping_id]
        elif region_group != ("", ""):
            existing_page = by_region_group.get(region_group)

        if existing_page is not None and self._site_config_page_matches(existing_page, row):
            return "unchanged"

        props = {
            SiteConfigProps.NAME: p_title(
                row.get("jurisdiction") or row.get("region") or "(unknown region)"
            ),
            SiteConfigProps.SETTING_TYPE: p_select(row.get("setting_type") or "tax_location"),
            SiteConfigProps.REGION: p_rich_text(row.get("region") or ""),
            SiteConfigProps.TAX_NAME: p_rich_text(row.get("tax_name") or ""),
            SiteConfigProps.TAX_TYPE: p_rich_text(row.get("tax_type") or ""),
            SiteConfigProps.TAX_RATE: p_number(self._site_config_rate_number(row)),
            SiteConfigProps.REGION_ID: p_rich_text(row.get("region_id") or ""),
            SiteConfigProps.GROUP_ID: p_rich_text(row.get("group_id") or ""),
            SiteConfigProps.MAPPING_ID: p_rich_text(row.get("mapping_id") or ""),
            SiteConfigProps.REVISION: p_rich_text(str(row.get("revision") or "")),
        }

        if existing_page is not None:
            self.update_page(existing_page.get("id", ""), props)
            return "updated"
        self.create_page(db_id, props)
        return "created"

    # -- workspace discovery (bootstrap helper) -----------------------------------

    def search_accessible_pages(self) -> List[Dict[str, Any]]:
        """Return pages the integration token can see (for setup guidance)."""
        try:
            response = self.client.search(
                filter={"property": "object", "value": "page"},
                page_size=25,
            )
            return response.get("results", [])
        except Exception as exc:  # pragma: no cover - network/permission errors
            logger.warning("Notion search failed: %s", exc)
            return []


def parse_validation_error(exc: ValidationError) -> str:
    """Compact one-line summary of a pydantic validation error for Sync Error."""
    try:
        problems = [
            f"{'.'.join(str(loc) for loc in err.get('loc', []))}: {err.get('msg', '')}"
            for err in exc.errors()
        ]
        return "; ".join(problems)[:1900]
    except Exception:  # pragma: no cover - defensive
        return str(exc)[:1900]
