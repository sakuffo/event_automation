"""Google Sheets data access helpers."""

from __future__ import annotations

from typing import Any, Dict, List

from .constants import COLUMN_MAPPING, DEFAULT_CAPACITY, REQUIRED_FIELDS
from .logging_utils import get_logger
from .models import EventRecord, ValidationError
from .runtime import SyncRuntime
from .utils import build_column_map


logger = get_logger(__name__)


def fetch_events(runtime: SyncRuntime) -> List[EventRecord]:
    """Fetch events from Google Sheets using the flexible header mapping."""

    logger.info("📊 Fetching events from Google Sheets...")

    service = runtime.get_sheets_service()
    sheet_id = runtime.config.google_sheet_id
    if not sheet_id:
        raise ValueError("GOOGLE_SHEET_ID is not configured")

    result = (
        service.spreadsheets()
        .values()
        .get(spreadsheetId=sheet_id, range=runtime.config.sheet_range)
        .execute()
    )

    rows = result.get("values", [])
    if not rows:
        logger.warning("No data found in spreadsheet.")
        return []

    headers = rows[0]
    data_rows = rows[1:]
    column_map = build_column_map(headers, COLUMN_MAPPING)

    missing = [field for field in REQUIRED_FIELDS if field not in column_map]
    if missing:
        raise ValueError(f"Missing required columns: {', '.join(missing)}")

    logger.info("   ✓ Found %d recognized columns", len(column_map))

    events: List[EventRecord] = []
    for row in data_rows:
        if not row or not any(row):
            continue

        while len(row) < len(headers):
            row.append("")

        def get_col(field_name: str, default: str = "") -> str:
            idx = column_map.get(field_name)
            if idx is None or idx >= len(row):
                return default
            return row[idx].strip() if row[idx] else default

        event_name = get_col("event_name")
        if not event_name:
            continue

        reg_type = get_col("registration_type", "RSVP")
        if reg_type.upper() == "TICKETS":
            logger.info(
                '   📋 Note: "%s" uses TICKETS - creating TICKETING event',
                event_name,
            )
            reg_type = "TICKETING"

        ticket_price_str = get_col("ticket_price", "0")
        try:
            ticket_price = float(ticket_price_str) if ticket_price_str else 0.0
        except ValueError:
            ticket_price = 0.0

        capacity_str = get_col("capacity", str(DEFAULT_CAPACITY))
        try:
            capacity = int(capacity_str) if capacity_str else DEFAULT_CAPACITY
        except ValueError:
            capacity = DEFAULT_CAPACITY

        event_kwargs = {
            "name": event_name,
            "category": get_col("category"),
            "event_type": get_col("event_type"),
            "start_date": get_col("start_date"),
            "start_time": get_col("start_time"),
            "end_date": get_col("end_date") or get_col("start_date"),
            "end_time": get_col("end_time") or get_col("start_time"),
            "location": get_col("location"),
            "ticket_price": ticket_price,
            "capacity": capacity,
            "registration_type": reg_type,
            "image_url": get_col("image_url"),
            "teaser": get_col("teaser"),
            "description": get_col("description"),
        }

        try:
            record = EventRecord(**event_kwargs)
        except ValidationError as exc:
            logger.error("❌ Skipping '%s': %s", event_name, exc)
            continue

        events.append(record)

    logger.info("Found %d events in spreadsheet", len(events))
    logger.info("")
    return events


def fetch_config_events(runtime: SyncRuntime) -> List[EventRecord]:
    """Fetch events from the config_events tab with all extended fields."""

    tab_name = runtime.config.config_events_tab
    logger.info("📊 Fetching config events from '%s'...", tab_name)

    service = runtime.get_sheets_service()
    sheet_id = runtime.config.google_sheet_id
    if not sheet_id:
        raise ValueError("GOOGLE_SHEET_ID is not configured")

    result = (
        service.spreadsheets()
        .values()
        .get(spreadsheetId=sheet_id, range=f"{tab_name}!A1:Z500")
        .execute()
    )

    rows = result.get("values", [])
    if not rows:
        logger.warning("No data found in '%s' tab.", tab_name)
        return []

    headers = rows[0]
    data_rows = rows[1:]
    column_map = build_column_map(headers, COLUMN_MAPPING)

    missing = [field for field in REQUIRED_FIELDS if field not in column_map]
    if missing:
        raise ValueError(f"Missing required columns in {tab_name}: {', '.join(missing)}")

    logger.info("   ✓ Found %d recognized columns", len(column_map))

    events: List[EventRecord] = []
    for row in data_rows:
        if not row or not any(row):
            continue

        while len(row) < len(headers):
            row.append("")

        def get_col(field_name: str, default: str = "") -> str:
            idx = column_map.get(field_name)
            if idx is None or idx >= len(row):
                return default
            return row[idx].strip() if row[idx] else default

        event_name = get_col("event_name")
        if not event_name:
            continue

        reg_type = get_col("registration_type", "TICKETING")
        if reg_type.upper() == "TICKETS":
            reg_type = "TICKETING"

        raw_price = get_col("ticket_price", "0")
        if ";" in raw_price:
            ticket_price = 0.0
        else:
            try:
                ticket_price = float(raw_price) if raw_price else 0.0
            except ValueError:
                ticket_price = 0.0

        capacity_str = get_col("capacity", str(DEFAULT_CAPACITY))
        try:
            capacity = int(capacity_str) if capacity_str else DEFAULT_CAPACITY
        except ValueError:
            capacity = DEFAULT_CAPACITY

        event_kwargs = {
            "name": event_name,
            "category": get_col("category"),
            "event_type": get_col("event_type"),
            "start_date": get_col("start_date"),
            "start_time": get_col("start_time"),
            "end_date": get_col("end_date") or get_col("start_date"),
            "end_time": get_col("end_time") or get_col("start_time"),
            "location": get_col("location"),
            "ticket_price": ticket_price,
            "capacity": capacity,
            "registration_type": reg_type,
            "image_url": get_col("image_url"),
            "teaser": get_col("teaser"),
            "description": get_col("description"),
            "ticket_name": get_col("ticket_name"),
            "ticket_price_raw": raw_price,
            "ticket_capacity": get_col("ticket_capacity"),
            "fee_type": get_col("fee_type"),
            "sale_start": get_col("sale_start"),
            "sale_end": get_col("sale_end"),
            "tax_name": get_col("tax_name"),
            "tax_rate": get_col("tax_rate"),
            "tax_type": get_col("tax_type"),
        }

        try:
            record = EventRecord(**event_kwargs)
        except ValidationError as exc:
            logger.error("❌ Skipping '%s': %s", event_name, exc)
            continue

        events.append(record)

    logger.info("Found %d config events", len(events))
    logger.info("")
    return events


