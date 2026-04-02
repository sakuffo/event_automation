"""Generator module for merging rolling_schedule and class_info data."""

from __future__ import annotations

import csv
import sys
from datetime import datetime
from typing import Any, Dict, List, Optional

from googleapiclient.errors import HttpError

from .constants import (
    CATEGORY_PRICING,
    CONFIG_COLUMNS,
    DEFAULT_CAPACITY,
    DEFAULT_FEE_TYPE,
    DEFAULT_LOCATION,
    DEFAULT_REGISTRATION_TYPE,
    DEFAULT_TAX_NAME,
    DEFAULT_TAX_RATE,
    DEFAULT_TAX_TYPE,
)
from .logging_utils import get_logger
from .runtime import SyncRuntime


logger = get_logger(__name__)

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


def _parse_month_value(value: str) -> int:
    """Parse month value from names/abbreviations into month number."""
    token = value.strip().lower()
    if not token:
        raise ValueError("Month filter cannot be empty")

    month_map = {
        "jan": 1,
        "january": 1,
        "feb": 2,
        "february": 2,
        "mar": 3,
        "march": 3,
        "apr": 4,
        "april": 4,
        "may": 5,
        "jun": 6,
        "june": 6,
        "jul": 7,
        "july": 7,
        "aug": 8,
        "august": 8,
        "sep": 9,
        "sept": 9,
        "september": 9,
        "oct": 10,
        "october": 10,
        "nov": 11,
        "november": 11,
        "dec": 12,
        "december": 12,
    }
    if token in month_map:
        return month_map[token]

    raise ValueError(
        "Invalid month filter. Use values like 'mar', 'MAR', or 'March'."
    )


def _record_month_number(record: Dict[str, str]) -> Optional[int]:
    """Resolve month number from full_date first, then month column."""
    full_date = (record.get("full_date") or "").strip()
    if full_date:
        for fmt in ("%m/%d/%Y", "%Y-%m-%d", "%m/%d/%y"):
            try:
                return datetime.strptime(full_date, fmt).month
            except ValueError:
                continue

    month_text = (record.get("month") or "").strip()
    if month_text:
        try:
            return _parse_month_value(month_text)
        except ValueError:
            return None
    return None


def _resolve_month_filters(
    month_args: Optional[List[str]],
) -> Optional[set]:
    """Convert month name arguments into a set of month numbers.

    Returns ``None`` when no filter should be applied (show all months).
    """
    if not month_args:
        return None

    month_numbers: set = set()
    for arg in month_args:
        month_numbers.add(_parse_month_value(arg))
    return month_numbers


def fetch_rolling_schedule(
    runtime: SyncRuntime,
    month_filter: Optional[str] = None,
    month_filters: Optional[List[str]] = None,
) -> List[Dict[str, str]]:
    """Fetch events from the rolling_schedule tab.

    Accepts either a single ``month_filter`` (legacy) or a list of
    ``month_filters`` (e.g. ``["apr", "may"]``).
    """
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

    # Build the set of allowed month numbers
    if month_filters:
        allowed_months = _resolve_month_filters(month_filters)
    elif month_filter:
        allowed_months = _resolve_month_filters([month_filter])
    else:
        allowed_months = None

    events: List[Dict[str, str]] = []
    skipped_count = 0
    skipped_month = 0
    for row in data_rows:
        if not row or not any(row):
            continue

        while len(row) < len(headers):
            row.append("")

        record = {}
        for i, header in enumerate(headers):
            record[header] = row[i].strip() if row[i] else ""

        if not record.get("class") or not record.get("full_date"):
            continue

        if record.get("holiday"):
            continue

        if _should_skip_event(record.get("class", "")):
            skipped_count += 1
            continue

        if allowed_months is not None:
            row_month = _record_month_number(record)
            if row_month not in allowed_months:
                skipped_month += 1
                continue

        events.append(record)

    if allowed_months is not None:
        month_labels = month_filters or ([month_filter] if month_filter else [])
        logger.info(
            "   Found %d scheduled events for months=%s "
            "(skipped %d placeholders, %d outside month filter)",
            len(events),
            ", ".join(month_labels),
            skipped_count,
            skipped_month,
        )
    else:
        logger.info(
            "   Found %d scheduled events (skipped %d placeholders)",
            len(events),
            skipped_count,
        )
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


def fetch_defaults(runtime: SyncRuntime) -> Dict[str, str]:
    """Fetch defaults from the defaults tab.

    Expected format: a standard table with `key` and `value` headers.
    """
    logger.info("Fetching defaults tab...")

    service = runtime.get_sheets_service()
    source_sheet_id = runtime.config.generator_sheet_id
    destination_sheet_id = runtime.config.google_sheet_id
    if not source_sheet_id and not destination_sheet_id:
        raise ValueError("SOURCE_SHEET_ID or GOOGLE_SHEET_ID is not configured")

    tab_name = runtime.config.defaults_tab
    # Quote sheet names in A1 notation to support spaces/special chars safely.
    safe_tab_name = tab_name.replace("'", "''")
    range_name = f"'{safe_tab_name}'!A1:Z50"

    candidate_sheet_ids: List[str] = []
    if source_sheet_id:
        candidate_sheet_ids.append(source_sheet_id)
    if destination_sheet_id and destination_sheet_id not in candidate_sheet_ids:
        candidate_sheet_ids.append(destination_sheet_id)

    result = None
    for candidate_sheet_id in candidate_sheet_ids:
        try:
            result = (
                service.spreadsheets()
                .values()
                .get(spreadsheetId=candidate_sheet_id, range=range_name)
                .execute()
            )
            logger.info(
                "   Loaded defaults from spreadsheet: %s",
                candidate_sheet_id,
            )
            break
        except HttpError as exc:
            logger.warning(
                "Could not read defaults tab '%s' from spreadsheet %s (%s)",
                tab_name,
                candidate_sheet_id,
                exc,
            )

    if result is None:
        logger.warning("Continuing without defaults.")
        return {}

    rows = result.get("values", [])
    if not rows:
        logger.info("   No defaults found in tab '%s'", tab_name)
        return {}

    headers = [h.strip().lower() for h in rows[0]]
    defaults: Dict[str, str] = {}

    if "key" not in headers or "value" not in headers:
        logger.warning(
            "Defaults tab '%s' is missing required headers: key, value",
            tab_name,
        )
        return {}

    key_idx = headers.index("key")
    value_idx = headers.index("value")
    for row in rows[1:]:
        key = row[key_idx].strip().lower() if len(row) > key_idx and row[key_idx] else ""
        value = row[value_idx].strip() if len(row) > value_idx and row[value_idx] else ""
        if key and value:
            defaults[key] = value

    logger.info("   Loaded %d defaults", len(defaults))
    return defaults


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


def _normalize_category_slug(category: str) -> str:
    """Normalize category text to a lowercase, hyphen-joined slug."""
    collapsed = " ".join(category.strip().split())
    return collapsed.lower().replace(" ", "-")


def merge_event_data(
    schedule: List[Dict[str, str]],
    class_info: Dict[str, Dict[str, str]],
    defaults: Optional[Dict[str, str]] = None,
) -> List[Dict[str, Any]]:
    """Merge rolling_schedule with class_info and apply pricing."""
    merged_events: List[Dict[str, Any]] = []
    missing_categories: set = set()
    unmatched_classes: List[str] = []
    defaults = defaults or {}
    default_image = defaults.get("default_img", "").strip()

    for sched in schedule:
        class_name = sched.get("class", "")
        primary_category = (
            sched.get("categories", "") or sched.get("catagories", "")
        ).strip()

        # Look up class details — skip if no match in class_info
        details = class_info.get(class_name, {})
        if not details:
            date_str = sched.get("full_date", "?")
            unmatched_classes.append(f"{class_name} (scheduled {date_str})")
            continue

        # Build merged category list: primary from rolling_schedule first,
        # then additional from class_info (semicolon-separated).
        # "rope" and "class" are always included as baseline tags.
        # All values are lowercased, stripped, and deduplicated.
        seen: set = set()
        merged_categories: List[str] = []
        for raw in [primary_category] + (details.get("categories", "") or "").split(";"):
            tag = "-".join(raw.strip().lower().split())
            if tag and tag not in seen:
                seen.add(tag)
                merged_categories.append(tag)
        for default_tag in ["rope", "class"]:
            if default_tag not in seen:
                seen.add(default_tag)
                merged_categories.append(default_tag)

        # Look up base price from the primary category (case-insensitive).
        # Tax (HST) is handled by Wix at checkout — ticket_price is the base price.
        base_price, matched = _lookup_category_price(primary_category)
        if not matched and primary_category:
            missing_categories.add(primary_category)
        ticket_price = base_price

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

        image_url = (details.get("image_link") or "").strip() or default_image

        event = {
            "event_name": class_name,
            "catagories": "; ".join(merged_categories),
            "event_type": "TICKETING",
            "start_date": sched.get("full_date", ""),
            "start_time": sched.get("time_start", ""),
            "end_date": sched.get("full_date", ""),
            "end_time": sched.get("time_end", ""),
            "location": DEFAULT_LOCATION,
            "base_price": base_price,
            "ticket_price": ticket_price,
            "capacity": DEFAULT_CAPACITY,
            "registration_type": DEFAULT_REGISTRATION_TYPE,
            "image_url": image_url,
            "short_description": details.get("class_tagline", ""),
            "detailed_description": description,
        }

        merged_events.append(event)

    if unmatched_classes:
        logger.warning(
            "⚠️  %d class(es) in rolling_schedule have no match in class_info (skipped):",
            len(unmatched_classes),
        )
        for entry in unmatched_classes:
            logger.warning("   - %s", entry)
        logger.warning(
            "   Fix these in the source sheet so they match a class_info title exactly."
        )

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
    rows: List[List[Any]] = [OUTPUT_COLUMNS]
    for event in events:
        # Preserve native types so numeric/date/time-looking values are not forced
        # into text cells when writing to Google Sheets.
        row = [event.get(col, "") for col in OUTPUT_COLUMNS]
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
            valueInputOption="USER_ENTERED",
            body={"values": rows},
        ).execute()

        logger.info("   Successfully wrote %d events to '%s'", len(events), tab_name)
        return True
    except Exception as exc:
        logger.error("Failed to write to sheet: %s", exc)
        return False


def _wix_event_to_config_row(
    wix_event: Dict[str, Any],
    ticket_defs: List[Dict[str, Any]],
    tz_name: str = "America/Toronto",
) -> Dict[str, Any]:
    """Convert a Wix event + its ticket definitions into a config_events row."""
    from .orchestrator import _localize_wix_start

    date_settings = wix_event.get("dateAndTimeSettings", {})
    location = wix_event.get("location", {})
    address = (location.get("address") or {}).get("formattedAddress", "")
    registration = wix_event.get("registration", {})
    reg_type = registration.get("initialType") or registration.get("type", "")
    tax = (registration.get("tickets") or {}).get("taxSettings", {})

    # Extract category names from the CATEGORIES fieldset
    cat_data = wix_event.get("categories", {})
    cat_list = cat_data.get("categories", []) if isinstance(cat_data, dict) else []
    category_names = [c.get("name", "") for c in cat_list if c.get("name")]
    categories_str = "; ".join(category_names)

    start_raw = date_settings.get("startDate", "")
    end_raw = date_settings.get("endDate", "")

    def _localize(iso_str):
        if not iso_str:
            return "", ""
        result = _localize_wix_start(iso_str, tz_name)
        if result:
            date_part, time_part = result
            try:
                from datetime import datetime as dt
                d = dt.strptime(date_part, "%Y-%m-%d")
                return d.strftime("%m/%d/%Y"), time_part
            except Exception:
                return date_part, time_part
        return "", ""

    start_date, start_time = _localize(start_raw)
    end_date, end_time = _localize(end_raw)

    # Build image URL from Wix media
    image_url = ""
    main_image = wix_event.get("mainImage") or {}
    if main_image.get("url"):
        image_url = main_image["url"]

    # Build ticket spec string: Name:Price:Capacity
    ticket_parts = []
    fee_type = DEFAULT_FEE_TYPE
    sale_start = ""
    sale_end = ""
    for td in ticket_defs:
        name = td.get("name", "Ticket")
        pricing = td.get("pricingMethod", {})
        fixed = pricing.get("fixedPrice", {})
        price = fixed.get("value", "0")
        capacity = td.get("initialLimit") or td.get("actualLimit") or ""
        parts = [name, str(price)]
        if capacity:
            parts.append(str(capacity))
        ticket_parts.append(":".join(parts))
        fee_type = td.get("feeType", fee_type)
        sp = td.get("salePeriod") or {}
        if sp.get("startDate"):
            sale_start = sp["startDate"]
        if sp.get("endDate"):
            sale_end = sp["endDate"]

    return {
        "event_name": wix_event.get("title", ""),
        "categories": categories_str,
        "start_date": start_date,
        "start_time": start_time,
        "end_date": end_date,
        "end_time": end_time,
        "location": address,
        "registration_type": reg_type,
        "short_description": wix_event.get("shortDescription", ""),
        "detailed_description": wix_event.get("detailedDescription", ""),
        "image_url": image_url,
        "tickets": "; ".join(ticket_parts) if ticket_parts else "",
        "fee_type": fee_type,
        "sale_start": sale_start,
        "sale_end": sale_end,
        "tax_name": tax.get("name", DEFAULT_TAX_NAME),
        "tax_rate": tax.get("rate", DEFAULT_TAX_RATE),
        "tax_type": tax.get("type", DEFAULT_TAX_TYPE),
    }


def write_config_to_sheet(
    config_rows: List[Dict[str, Any]],
    runtime: SyncRuntime,
    tab_name: str,
) -> bool:
    """Write config rows to a sheet tab using CONFIG_COLUMNS."""
    logger.info("Writing %d events to config tab '%s'...", len(config_rows), tab_name)

    service = runtime.get_sheets_service()
    sheet_id = runtime.config.google_sheet_id
    if not sheet_id:
        raise ValueError("GOOGLE_SHEET_ID is not configured")

    rows: List[List[Any]] = [CONFIG_COLUMNS]
    for row_data in config_rows:
        rows.append([row_data.get(col, "") for col in CONFIG_COLUMNS])

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
                body={"requests": [{"addSheet": {"properties": {"title": tab_name}}}]},
            ).execute()
    except Exception as exc:
        logger.error("Failed to check/create tab: %s", exc)
        return False

    try:
        range_name = f"{tab_name}!A1:Z{len(rows) + 10}"
        service.spreadsheets().values().clear(
            spreadsheetId=sheet_id, range=range_name, body={},
        ).execute()
        service.spreadsheets().values().update(
            spreadsheetId=sheet_id,
            range=f"{tab_name}!A1",
            valueInputOption="USER_ENTERED",
            body={"values": rows},
        ).execute()

        # Right-align date, time, and number columns
        right_align_cols = {"start_date", "start_time", "end_date", "end_time", "tax_rate"}
        col_indices = [i for i, c in enumerate(CONFIG_COLUMNS) if c in right_align_cols]

        # Get the sheet's internal gid for formatting requests
        spreadsheet_meta = service.spreadsheets().get(spreadsheetId=sheet_id).execute()
        sheet_gid = None
        for s in spreadsheet_meta.get("sheets", []):
            if s.get("properties", {}).get("title") == tab_name:
                sheet_gid = s["properties"]["sheetId"]
                break

        if sheet_gid is not None and col_indices:
            format_requests = []
            for col_idx in col_indices:
                format_requests.append({
                    "repeatCell": {
                        "range": {
                            "sheetId": sheet_gid,
                            "startRowIndex": 1,
                            "endRowIndex": len(rows),
                            "startColumnIndex": col_idx,
                            "endColumnIndex": col_idx + 1,
                        },
                        "cell": {
                            "userEnteredFormat": {
                                "horizontalAlignment": "RIGHT",
                            }
                        },
                        "fields": "userEnteredFormat.horizontalAlignment",
                    }
                })
            service.spreadsheets().batchUpdate(
                spreadsheetId=sheet_id,
                body={"requests": format_requests},
            ).execute()

        logger.info("   Successfully wrote %d events to '%s'", len(config_rows), tab_name)
        return True
    except Exception as exc:
        logger.error("Failed to write to config sheet: %s", exc)
        return False


def pull_config_events(runtime: SyncRuntime) -> bool:
    """Pull all published events from Wix into config_events and config_events_last_pull."""
    logger.info("Pulling all events from Wix...\n")

    try:
        client = runtime.get_wix_client()
        tz_name = runtime.config.timezone

        logger.info("Fetching events from Wix...")
        all_events = list(client.iter_events(
            page_size=100,
            include_drafts=False,
            fieldsets=["DETAILS", "REGISTRATION", "CATEGORIES"],
        ))
        upcoming = [e for e in all_events if e.get("status") in ("UPCOMING", "STARTED")]
        logger.info("   Found %d published events (%d total)\n", len(upcoming), len(all_events))

        if not upcoming:
            logger.warning("No published events found on Wix.")
            return False

        config_rows: List[Dict[str, Any]] = []
        for i, wix_event in enumerate(upcoming, 1):
            title = wix_event.get("title", "Untitled")
            event_id = wix_event.get("id", "")
            logger.info("  %d/%d  %s", i, len(upcoming), title)

            ticket_defs = client.get_ticket_definitions(event_id)
            row = _wix_event_to_config_row(wix_event, ticket_defs, tz_name=tz_name)
            config_rows.append(row)

        logger.info("")

        snapshot_tab = runtime.config.config_events_tab + "_last_pull"
        logger.info("Writing snapshot to '%s'...", snapshot_tab)
        write_config_to_sheet(config_rows, runtime, snapshot_tab)

        config_tab = runtime.config.config_events_tab
        logger.info("Writing editable config to '%s'...", config_tab)
        ok = write_config_to_sheet(config_rows, runtime, config_tab)

        return ok

    except Exception as exc:
        logger.error("Failed to pull config events: %s", exc)
        return False


def _default_rolling_months() -> List[str]:
    """Return month names for this month and next month."""
    today = datetime.today()
    this_month = today.month
    next_month = this_month % 12 + 1
    month_names = [
        "", "jan", "feb", "mar", "apr", "may", "jun",
        "jul", "aug", "sep", "oct", "nov", "dec",
    ]
    return [month_names[this_month], month_names[next_month]]


def generate_events(
    runtime: SyncRuntime,
    output_sheet: Optional[str] = None,
    month_filter: Optional[str] = None,
    month_filters: Optional[List[str]] = None,
) -> bool:
    """Main entry point for generating merged event data."""
    import sys
    if output_sheet is None:
        import logging
        for handler in logging.getLogger().handlers:
            handler.stream = sys.stderr

    logger.info("Generating event data from rolling_schedule + class_info...\n")

    try:
        schedule = fetch_rolling_schedule(
            runtime,
            month_filter=month_filter,
            month_filters=month_filters,
        )
        class_info = fetch_class_info(runtime)
        defaults = fetch_defaults(runtime)

        if not schedule:
            logger.warning("No scheduled events found.")
            return False

        merged = merge_event_data(schedule, class_info, defaults=defaults)
        logger.info("\nMerged %d events with class info\n", len(merged))

        if output_sheet:
            return write_to_sheet(merged, runtime, output_sheet)
        else:
            output_csv(merged)
            return True

    except Exception as exc:
        logger.error("Failed to generate events: %s", exc)
        return False

