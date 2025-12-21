"""Generator module for merging rolling_schedule and class_info data."""

from __future__ import annotations

import csv
import sys
from typing import Any, Dict, List, Optional

from .constants import (
    CATEGORY_PRICING,
    DEFAULT_CAPACITY,
    DEFAULT_LOCATION,
    DEFAULT_REGISTRATION_TYPE,
    HST_MULTIPLIER,
)
from .logging_utils import get_logger
from .runtime import SyncRuntime


logger = get_logger(__name__)

# Column indices for rolling_schedule tab
ROLLING_SCHEDULE_COLUMNS = [
    "month",
    "date",
    "full_date",
    "time_start",
    "time_end",
    "catagories",
    "class",
    "instructor",
    "model",
    "notes",
    "event",
    "unavailability_notice",
    "holiday",
]

# Column indices for class_info tab
CLASS_INFO_COLUMNS = [
    "class",
    "catagories",
    "image_link",
    "image_notes",
    "class_tagline",
    "description",
    "instructor_naive",
    "instructor_specific",
]

# Output columns for generated events
OUTPUT_COLUMNS = [
    "event_name",
    "catagories",
    "event_type",
    "start_date",
    "start_time",
    "end_date",
    "end_time",
    "location",
    "base_price",
    "ticket_price",
    "capacity",
    "registration_type",
    "image_url",
    "short_description",
    "detailed_description",
]


# Patterns to skip when filtering events
SKIP_PATTERNS = ["[no class]", "[tbd]", "n/a", "tbd"]


def _should_skip_event(class_name: str) -> bool:
    """Check if an event should be skipped based on its class name."""
    lower_name = class_name.lower().strip()
    for pattern in SKIP_PATTERNS:
        if pattern in lower_name:
            return True
    return False


def fetch_rolling_schedule(runtime: SyncRuntime) -> List[Dict[str, str]]:
    """Fetch events from the rolling_schedule tab."""
    logger.info("Fetching rolling_schedule tab...")

    service = runtime.get_sheets_service()
    sheet_id = runtime.config.generator_sheet_id
    if not sheet_id:
        raise ValueError("SOURCE_SHEET_ID or GOOGLE_SHEET_ID is not configured")

    tab_name = runtime.config.rolling_schedule_tab
    result = (
        service.spreadsheets()
        .values()
        .get(spreadsheetId=sheet_id, range=f"{tab_name}!A1:Z500")
        .execute()
    )

    rows = result.get("values", [])
    if not rows:
        logger.warning("No data found in rolling_schedule tab.")
        return []

    headers = [h.strip().lower() for h in rows[0]]
    data_rows = rows[1:]

    events: List[Dict[str, str]] = []
    skipped_count = 0
    for row in data_rows:
        if not row or not any(row):
            continue

        # Pad row to match headers
        while len(row) < len(headers):
            row.append("")

        record = {}
        for i, header in enumerate(headers):
            record[header] = row[i].strip() if row[i] else ""

        # Skip rows without a class name or full_date
        if not record.get("class") or not record.get("full_date"):
            continue

        # Skip holidays or unavailable dates
        if record.get("holiday") or record.get("unavailability_notice"):
            continue

        # Skip placeholder events like [No Class], [TBD], N/A
        if _should_skip_event(record.get("class", "")):
            skipped_count += 1
            continue

        events.append(record)

    logger.info("   Found %d scheduled events (skipped %d placeholders)", len(events), skipped_count)
    return events


def fetch_class_info(runtime: SyncRuntime) -> Dict[str, Dict[str, str]]:
    """Fetch class details from the class_info tab, indexed by class name."""
    logger.info("Fetching class_info tab...")

    service = runtime.get_sheets_service()
    sheet_id = runtime.config.generator_sheet_id
    if not sheet_id:
        raise ValueError("SOURCE_SHEET_ID or GOOGLE_SHEET_ID is not configured")

    tab_name = runtime.config.class_info_tab
    result = (
        service.spreadsheets()
        .values()
        .get(spreadsheetId=sheet_id, range=f"{tab_name}!A1:Z200")
        .execute()
    )

    rows = result.get("values", [])
    if not rows:
        logger.warning("No data found in class_info tab.")
        return {}

    headers = [h.strip().lower() for h in rows[0]]
    data_rows = rows[1:]

    class_info: Dict[str, Dict[str, str]] = {}
    for row in data_rows:
        if not row or not any(row):
            continue

        # Pad row to match headers
        while len(row) < len(headers):
            row.append("")

        record = {}
        for i, header in enumerate(headers):
            record[header] = row[i].strip() if row[i] else ""

        class_name = record.get("class", "").strip()
        if class_name:
            class_info[class_name] = record

    logger.info("   Found %d class definitions", len(class_info))
    return class_info


def _lookup_category_price(category: str) -> tuple:
    """Look up price for a category, case-insensitive. Returns (price, matched)."""
    # Try exact match first
    if category in CATEGORY_PRICING:
        return CATEGORY_PRICING[category], True

    # Try case-insensitive match
    category_lower = category.lower().strip()
    for known_cat, price in CATEGORY_PRICING.items():
        if known_cat.lower() == category_lower:
            return price, True

    # No match found
    return 30, False  # Default to $30


def merge_event_data(
    schedule: List[Dict[str, str]],
    class_info: Dict[str, Dict[str, str]],
) -> List[Dict[str, Any]]:
    """Merge rolling_schedule with class_info and apply pricing."""
    merged_events: List[Dict[str, Any]] = []
    missing_categories: set = set()

    for sched in schedule:
        class_name = sched.get("class", "")
        category = sched.get("catagories", "")

        # Look up class details
        details = class_info.get(class_name, {})

        # Look up base price from category (case-insensitive)
        base_price, matched = _lookup_category_price(category)
        if not matched and category:
            missing_categories.add(category)
        ticket_price = round(base_price * HST_MULTIPLIER, 2)

        # Build instructor team from instructor + model columns
        instructor = sched.get("instructor", "").strip()
        model = sched.get("model", "").strip()
        team_parts = [p for p in [instructor, model] if p]
        team = " & ".join(team_parts) if team_parts else ""

        # Prepend instructors to description if we have a team
        description = details.get("description", "")
        if team and description:
            description = f"Instructors: {team}\n\n{description}"
        elif team:
            description = f"Instructors: {team}"

        event = {
            "event_name": class_name,
            "catagories": category,
            "event_type": "TICKETS",
            "start_date": sched.get("full_date", ""),
            "start_time": sched.get("time_start", ""),
            "end_date": sched.get("full_date", ""),  # Same as start for single-day
            "end_time": sched.get("time_end", ""),
            "location": DEFAULT_LOCATION,
            "base_price": base_price,
            "ticket_price": ticket_price,
            "capacity": DEFAULT_CAPACITY,
            "registration_type": DEFAULT_REGISTRATION_TYPE,
            "image_url": details.get("image_link", ""),
            "short_description": details.get("class_tagline", ""),
            "detailed_description": description,
        }

        merged_events.append(event)

    # Log missing categories
    if missing_categories:
        logger.warning("⚠️  Categories not in pricing table (defaulting to $30):")
        for cat in sorted(missing_categories):
            logger.warning("   - %s", cat)

    return merged_events


def output_csv(events: List[Dict[str, Any]], file=None) -> None:
    """Write events to CSV format (to stdout or specified file)."""
    if file is None:
        file = sys.stdout

    writer = csv.DictWriter(file, fieldnames=OUTPUT_COLUMNS, extrasaction="ignore")
    writer.writeheader()
    for event in events:
        writer.writerow(event)


def write_to_sheet(
    events: List[Dict[str, Any]],
    runtime: SyncRuntime,
    tab_name: str,
) -> bool:
    """Write merged events to a new tab in the Google Sheet."""
    logger.info("Writing %d events to sheet tab '%s'...", len(events), tab_name)

    service = runtime.get_sheets_service()
    sheet_id = runtime.config.google_sheet_id
    if not sheet_id:
        raise ValueError("GOOGLE_SHEET_ID is not configured")

    # Prepare data rows (header + data)
    rows: List[List[str]] = [OUTPUT_COLUMNS]
    for event in events:
        row = [str(event.get(col, "")) for col in OUTPUT_COLUMNS]
        rows.append(row)

    # Check if tab exists; if not, create it
    try:
        spreadsheet = service.spreadsheets().get(spreadsheetId=sheet_id).execute()
        sheets = spreadsheet.get("sheets", [])
        tab_exists = any(
            s.get("properties", {}).get("title") == tab_name for s in sheets
        )

        if not tab_exists:
            logger.info("   Creating new tab '%s'...", tab_name)
            service.spreadsheets().batchUpdate(
                spreadsheetId=sheet_id,
                body={
                    "requests": [
                        {"addSheet": {"properties": {"title": tab_name}}}
                    ]
                },
            ).execute()
    except Exception as exc:
        logger.error("Failed to check/create tab: %s", exc)
        return False

    # Clear existing data and write new data
    try:
        range_name = f"{tab_name}!A1:Z{len(rows) + 10}"

        # Clear the range first
        service.spreadsheets().values().clear(
            spreadsheetId=sheet_id,
            range=range_name,
            body={},
        ).execute()

        # Write the data
        service.spreadsheets().values().update(
            spreadsheetId=sheet_id,
            range=f"{tab_name}!A1",
            valueInputOption="RAW",
            body={"values": rows},
        ).execute()

        logger.info("   Successfully wrote %d events to '%s'", len(events), tab_name)
        return True
    except Exception as exc:
        logger.error("Failed to write to sheet: %s", exc)
        return False


def generate_events(
    runtime: SyncRuntime,
    output_sheet: Optional[str] = None,
) -> bool:
    """Main entry point for generating merged event data."""
    # When outputting CSV to stdout, send all logs to stderr
    import sys
    if output_sheet is None:
        # Temporarily redirect logger output to stderr for clean CSV piping
        import logging
        for handler in logging.getLogger().handlers:
            handler.stream = sys.stderr

    logger.info("Generating event data from rolling_schedule + class_info...\n")

    try:
        schedule = fetch_rolling_schedule(runtime)
        class_info = fetch_class_info(runtime)

        if not schedule:
            logger.warning("No scheduled events found.")
            return False

        merged = merge_event_data(schedule, class_info)
        logger.info("\nMerged %d events with class info\n", len(merged))

        if output_sheet:
            return write_to_sheet(merged, runtime, output_sheet)
        else:
            output_csv(merged)
            return True

    except Exception as exc:
        logger.error("Failed to generate events: %s", exc)
        return False

