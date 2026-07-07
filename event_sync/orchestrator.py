"""High-level orchestration helpers for syncing events."""

from __future__ import annotations

import json
import re
import time
from datetime import datetime, timezone
from html import escape
from typing import Any, Dict, List, Optional, Tuple

try:  # pragma: no cover - standard library on Python 3.9+
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
except ImportError:  # pragma: no cover - fallback for older runtimes
    ZoneInfo = None  # type: ignore
    ZoneInfoNotFoundError = Exception  # type: ignore

try:  # pragma: no cover - optional dependency
    import pytz  # type: ignore
except ImportError:  # pragma: no cover - optional dependency
    pytz = None  # type: ignore

from .config import AppConfig
from .logging_utils import get_logger
from .images import upload_image_to_wix
from .models import EventRecord, parse_tickets
from .runtime import SyncRuntime
from .sheets import fetch_config_events, fetch_events
from .utils import convert_date_to_iso


logger = get_logger(__name__)


def _wix_timestamp(date_iso: str, time_24h: str, tz_name: str) -> str:
    """Return a UTC timestamp string for Wix while respecting the site timezone."""

    naive = datetime.strptime(f"{date_iso} {time_24h}", "%Y-%m-%d %H:%M")

    if ZoneInfo is not None:
        try:
            local_tz = ZoneInfo(tz_name)
            utc_tz = ZoneInfo("UTC")
            localized = naive.replace(tzinfo=local_tz)
            return localized.astimezone(utc_tz).strftime("%Y-%m-%dT%H:%M:%SZ")
        except ZoneInfoNotFoundError as exc:
            logger.warning("⚠️  Unknown timezone '%s' via zoneinfo: %s", tz_name, exc)
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning("⚠️  Failed zoneinfo conversion for %s: %s", tz_name, exc)

    if pytz is not None:
        try:
            local_tz = pytz.timezone(tz_name)
            localized = local_tz.localize(naive)
            return localized.astimezone(pytz.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning("⚠️  Failed pytz conversion for %s: %s", tz_name, exc)

    logger.warning(
        "⚠️  Falling back to naive UTC timestamp for timezone '%s'", tz_name
    )
    return f"{date_iso}T{time_24h}:00Z"


def _localize_wix_start(start_datetime: str, tz_name: str) -> Optional[Tuple[str, str]]:
    """Return (date_iso, time_24h) converted to the requested timezone."""

    if not start_datetime:
        return None

    try:
        normalized = start_datetime.replace("Z", "+00:00")
        dt_utc = datetime.fromisoformat(normalized)
    except ValueError:
        try:
            trimmed = start_datetime
            if "." in trimmed:
                trimmed = trimmed.split(".")[0]
            trimmed = trimmed.replace("Z", "")
            dt_utc = datetime.strptime(trimmed, "%Y-%m-%dT%H:%M:%S")
            dt_utc = dt_utc.replace(tzinfo=timezone.utc)
        except Exception:
            logger.warning("⚠️  Could not parse Wix startDate '%s'", start_datetime)
            return None
    else:
        if dt_utc.tzinfo is None:
            dt_utc = dt_utc.replace(tzinfo=timezone.utc)

    if ZoneInfo is not None:
        try:
            local_tz = ZoneInfo(tz_name)
            local_dt = dt_utc.astimezone(local_tz)
            return local_dt.strftime("%Y-%m-%d"), local_dt.strftime("%H:%M")
        except ZoneInfoNotFoundError as exc:
            logger.warning("⚠️  Unknown timezone '%s' via zoneinfo: %s", tz_name, exc)
        except Exception as exc:
            logger.warning("⚠️  Failed zoneinfo localization for %s: %s", tz_name, exc)

    if pytz is not None:
        try:
            local_tz = pytz.timezone(tz_name)
            local_dt = dt_utc.astimezone(local_tz)
            return local_dt.strftime("%Y-%m-%d"), local_dt.strftime("%H:%M")
        except Exception as exc:
            logger.warning("⚠️  Failed pytz localization for %s: %s", tz_name, exc)

    fallback = dt_utc.astimezone(timezone.utc)
    logger.warning(
        "⚠️  Falling back to UTC startDate for timezone '%s'", tz_name
    )
    return fallback.strftime("%Y-%m-%d"), fallback.strftime("%H:%M")


def _normalize_wix_timestamp(timestamp: str) -> Optional[str]:
    """Return a canonical UTC timestamp string for comparison against expected values."""

    if not timestamp:
        return None

    try:
        normalized = timestamp.replace("Z", "+00:00")
        dt_utc = datetime.fromisoformat(normalized)
    except ValueError:
        trimmed = timestamp
        if "." in trimmed:
            trimmed = trimmed.split(".")[0]
        trimmed = trimmed.replace("Z", "")
        try:
            dt_utc = datetime.strptime(trimmed, "%Y-%m-%dT%H:%M:%S")
            dt_utc = dt_utc.replace(tzinfo=timezone.utc)
        except Exception:
            logger.warning("⚠️  Could not normalize Wix timestamp '%s'", timestamp)
            return None
    else:
        if dt_utc.tzinfo is None:
            dt_utc = dt_utc.replace(tzinfo=timezone.utc)

    return dt_utc.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


_BULLET_MARKERS = ("- ", "* ", "\u2022 ", "\u2013 ", "\u2014 ")

# HTML tags that are preserved (not escaped) when found in description text.
SAFE_HTML_TAGS = frozenset({
    "b", "i", "u", "em", "strong", "a", "br", "p", "ul", "ol", "li",
    "h1", "h2", "h3", "h4", "h5", "h6", "span", "div", "blockquote",
})

_TAG_RE = re.compile(r'(<[^>]+>)')
_TAG_NAME_RE = re.compile(r'^</?([a-zA-Z][a-zA-Z0-9]*)')
_BLOCK_TAG_RE = re.compile(r'<(?:p|div|ul|ol|h[1-6]|blockquote)[\s>/]', re.IGNORECASE)


def _escape_preserving_html(text: str) -> str:
    """Escape plain text but preserve whitelisted HTML tags."""
    parts = _TAG_RE.split(text)
    result: List[str] = []
    for part in parts:
        m = _TAG_NAME_RE.match(part)
        if m and m.group(1).lower() in SAFE_HTML_TAGS:
            result.append(part)
        else:
            result.append(escape(part))
    return "".join(result)


def _is_complete_html(text: str) -> bool:
    """Return True if text is already fully-formed HTML with block tags."""
    stripped = text.strip()
    return (
        stripped.startswith("<")
        and stripped.endswith(">")
        and _BLOCK_TAG_RE.search(stripped) is not None
    )


def _extract_bullet_text(line: str) -> Optional[str]:
    stripped = line.lstrip()
    for marker in _BULLET_MARKERS:
        if stripped.startswith(marker):
            return stripped[len(marker) :].strip()
    return None


def _is_bullet(line: str) -> bool:
    return _extract_bullet_text(line) is not None


def _inline_markdown(text: str) -> str:
    """Apply inline markdown formatting to already-escaped HTML text.

    Supported: **bold**, *italic*, [text](url)
    """
    text = re.sub(
        r'\[([^\]]+)\]\(([^)]+)\)',
        r'<a href="\2" target="_blank">\1</a>',
        text,
    )
    text = re.sub(r'\*\*(.+?)\*\*', r'<b>\1</b>', text)
    text = re.sub(r'\*(.+?)\*', r'<i>\1</i>', text)
    return text


def _format_line(text: str) -> str:
    """Escape non-tag text, preserve safe HTML tags, apply inline markdown."""
    return _inline_markdown(_escape_preserving_html(text.strip()))


def format_description_as_html(raw: str) -> str:
    """Convert text from Sheets into HTML for Wix.

    Supports paragraphs, bullet lists, **bold**, *italic*, [links](url),
    and inline HTML tags (<b>, <i>, <a>, etc.).

    Paragraph breaks occur on blank lines AND at transitions between
    regular text and bullet-list lines.
    """
    if not raw:
        return ""

    normalized = raw.replace("\r\n", "\n").replace("\r", "\n").strip()
    if not normalized:
        return ""

    # Already fully-formed HTML — sanitize unsafe tags and return as-is.
    if _is_complete_html(normalized):
        return _escape_preserving_html(normalized)

    lines = normalized.split("\n")

    # Split lines into blocks, breaking on blank lines AND on transitions
    # between bullet and non-bullet lines.
    blocks: List[List[str]] = []
    current: List[str] = []
    current_is_bullet: Optional[bool] = None

    for line in lines:
        if line.strip() == "":
            if current:
                blocks.append(current)
                current = []
                current_is_bullet = None
            continue

        line_is_bullet = _is_bullet(line)

        # Transition between text and bullets (or vice versa) → new block
        if current and current_is_bullet is not None and line_is_bullet != current_is_bullet:
            blocks.append(current)
            current = []

        current.append(line.rstrip())
        current_is_bullet = line_is_bullet

    if current:
        blocks.append(current)

    html_blocks: List[str] = []

    for block in blocks:
        # Check if all lines in this block are bullets
        if all(_is_bullet(entry) for entry in block):
            items = [_format_line(_extract_bullet_text(entry)) for entry in block]
            items_html = "".join(f"<li>{item}</li>" for item in items)
            html_blocks.append(f"<ul>{items_html}</ul>")
        else:
            joined = "<br/>".join(_format_line(item) for item in block)
            html_blocks.append(f"<p>{joined}</p>")

    return "".join(html_blocks)


def validate_credentials(config: AppConfig) -> bool:
    """Validate that required environment/config values are present."""

    logger.info("🔍 Validating credentials and configuration...\n")

    checks = {
        "WIX_API_KEY": config.wix_api_key,
        "WIX_ACCOUNT_ID": config.wix_account_id,
        "WIX_SITE_ID": config.wix_site_id,
        "GOOGLE_SHEET_ID": config.google_sheet_id,
        "GOOGLE_CREDENTIALS": config.google_credentials_raw,
    }

    all_valid = True
    for name, value in checks.items():
        if not value:
            logger.error("❌ %s is missing", name)
            all_valid = False
        else:
            if name == "GOOGLE_CREDENTIALS":
                creds = config.google_credentials
                if creds and "client_email" in creds:
                    logger.info("✅ %s is valid JSON", name)
                    logger.info("   Service account: %s", creds["client_email"])
                else:
                    logger.error("❌ %s is invalid (missing client_email)", name)
                    all_valid = False
            else:
                logger.info("✅ %s is set", name)

    logger.info("")
    logger.info("Notion backend:")
    if config.notion_token:
        logger.info("✅ NOTION_ACCESS_TOKEN is set")
    else:
        logger.error("❌ NOTION_ACCESS_TOKEN is missing (required for the Notion pipeline)")
        all_valid = False

    notion_dbs = {
        "NOTION_EVENTS_DB_ID": config.notion_events_db_id,
        "NOTION_CATALOG_DB_ID": config.notion_catalog_db_id,
        "NOTION_SETTINGS_DB_ID": config.notion_settings_db_id,
        "NOTION_SITE_CONFIG_DB_ID": config.notion_site_config_db_id,
    }
    missing_dbs = [name for name, value in notion_dbs.items() if not value]
    for name, value in notion_dbs.items():
        if value:
            logger.info("✅ %s is set", name)
    if missing_dbs:
        logger.warning(
            "⚠️  Not set yet (run setup-notion to create + print them): %s",
            ", ".join(missing_dbs),
        )

    logger.info("")
    if all_valid:
        logger.info("✅ All credentials are configured correctly!\n")
        logger.info("Next steps:")
        logger.info("  1. Run: python sync_events.py test")
        logger.info("  2. Run: python sync_events.py sync")
    else:
        logger.error("❌ Some credentials are missing or invalid. Check .env file.\n")

    return all_valid


def test_wix_connection(runtime: SyncRuntime) -> bool:
    try:
        client = runtime.get_wix_client()
        client.list_events(limit=1)
        logger.info("✅ Wix API connection successful!")
        return True
    except Exception as exc:
        logger.error("❌ Wix API connection failed: %s", exc)
        return False


def list_wix_events(runtime: SyncRuntime) -> List[Dict[str, object]]:
    try:
        client = runtime.get_wix_client()
        events = list(client.iter_events(page_size=100))

        logger.info("\n📅 Existing Events in Wix:\n")
        for event in events[:50]:
            start_date = event.get("dateAndTimeSettings", {}).get("startDate", "No date")
            logger.info("  • %s - %s", event.get("title", "Untitled"), start_date)

        if len(events) > 50:
            logger.info("  • ...and %d more", len(events) - 50)

        return events
    except Exception as exc:
        logger.error("❌ Failed to list events: %s", exc)
        return []


def publish_all_drafts(runtime: SyncRuntime) -> bool:
    """Publish DRAFT events matching the generated sheet, then create their tickets."""
    logger.info("📢 Publishing draft events from generated sheet...\n")
    try:
        sheet_events = fetch_events(runtime)
        if not sheet_events:
            logger.warning("No events found in generated sheet.")
            return False

        sheet_lookup: Dict[str, EventRecord] = {}
        for event in sheet_events:
            start_date_iso = convert_date_to_iso(event.start_date)
            key = f"{event.name.strip()}|{start_date_iso}|{event.start_time}"
            sheet_lookup[key] = event

        logger.info("Found %d events in sheet, looking for matching drafts...\n", len(sheet_lookup))

        existing = get_existing_event_keys(runtime)
        client = runtime.get_wix_client()

        published = 0
        skipped = 0
        failed = 0
        for key, entry in existing.items():
            if key not in sheet_lookup:
                continue
            wix_event = entry.get("event") or {}
            event_id = entry.get("id")
            title = wix_event.get("title", "Untitled")
            sheet_event = sheet_lookup[key]

            if wix_event.get("status") != "DRAFT":
                logger.info("  ⏭️  Already published: %s", title)
                skipped += 1
                continue

            try:
                client.publish_event(event_id)
                logger.info("  ✅ Published: %s", title)
                published += 1
            except Exception as exc:
                logger.warning("  ⚠️  Failed to publish %s: %s", title, exc)
                failed += 1
                continue

            if (
                sheet_event.registration_type == "TICKETING"
                and sheet_event.ticket_price > 0
            ):
                _ensure_ticket_definition(client, event_id, sheet_event)

            time.sleep(0.3)

        if published == 0 and failed == 0:
            logger.info("No matching drafts to publish.")

        logger.info("\n📊 Results: %d published, %d already live, %d failed", published, skipped, failed)
        return failed == 0
    except Exception as exc:
        logger.error("❌ Failed to publish drafts: %s", exc)
        return False


def get_existing_event_keys(
    runtime: SyncRuntime,
    fieldsets: Optional[List[str]] = None,
) -> Dict[str, Dict[str, Any]]:
    logger.info("🔍 Checking for existing events in Wix...")
    try:
        client = runtime.get_wix_client()
        existing_events: Dict[str, Dict[str, Any]] = {}
        total_events = 0

        for event in client.iter_events(page_size=100, fieldsets=fieldsets):
            total_events += 1
            title = (event.get("title") or "").strip()
            start_settings = event.get("dateAndTimeSettings", {}) or {}
            start_datetime = start_settings.get("startDate", "")
            event_id = event.get("id")

            if not title or not start_datetime or not event_id:
                continue

            local_parts = _localize_wix_start(start_datetime, runtime.config.timezone)
            if local_parts is None:
                continue

            date_part, time_part = local_parts
            key = f"{title}|{date_part}|{time_part}"
            existing_events[key] = {"id": event_id, "event": event}

        logger.info(
            "Found %d existing events (from %d Wix records)\n",
            len(existing_events),
            total_events,
        )
        return existing_events
    except Exception as exc:
        logger.warning("Warning: Could not fetch existing events: %s", exc)
        return {}


def _build_wix_event_payload(
    event: EventRecord,
    runtime: SyncRuntime,
    *,
    file_descriptor: Optional[Dict[str, Any]] = None,
    existing_event: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    start_date_iso = convert_date_to_iso(event.start_date)
    end_date_iso = convert_date_to_iso(event.end_date)

    title = event.name.strip()

    event_data: Dict[str, Any] = {
        "title": title,
        "dateAndTimeSettings": {
            "dateAndTimeTbd": False,
            "startDate": _wix_timestamp(
                start_date_iso, event.start_time, runtime.config.timezone
            ),
            "endDate": _wix_timestamp(
                end_date_iso, event.end_time, runtime.config.timezone
            ),
            "timeZoneId": runtime.config.timezone,
        },
        "location": {
            "type": "VENUE",
            "address": {"formattedAddress": event.location},
        },
    }

    if existing_event is None:
        event_data["registration"] = {
            "initialType": event.registration_type,
        }
        if event.registration_type == "TICKETING":
            from .constants import DEFAULT_TAX_NAME, DEFAULT_TAX_RATE, DEFAULT_TAX_TYPE
            event_data["registration"]["tickets"] = {
                "taxSettings": {
                    "type": event.tax_type or DEFAULT_TAX_TYPE,
                    "name": event.tax_name or DEFAULT_TAX_NAME,
                    "rate": event.tax_rate or DEFAULT_TAX_RATE,
                }
            }

    teaser = event.teaser.strip() if event.teaser else ""
    if teaser:
        event_data["shortDescription"] = teaser
    elif existing_event and existing_event.get("shortDescription"):
        event_data["shortDescription"] = ""

    formatted_description = format_description_as_html(event.description or "")
    if formatted_description:
        event_data["detailedDescription"] = formatted_description
    elif existing_event and existing_event.get("detailedDescription"):
        event_data["detailedDescription"] = ""

    if file_descriptor and "id" in file_descriptor:
        width = height = None
        media = file_descriptor.get("media") or {}
        if isinstance(media, dict) and "image" in media:
            media_image = media.get("image") or {}
            if isinstance(media_image, dict):
                image_info = media_image.get("image") or {}
                if isinstance(image_info, dict):
                    width = image_info.get("width")
                    height = image_info.get("height")

        main_image: Dict[str, Any] = {"id": file_descriptor["id"]}
        if width and height:
            main_image["width"] = width
            main_image["height"] = height
        event_data["mainImage"] = main_image
    elif existing_event and existing_event.get("mainImage"):
        event_data["mainImage"] = existing_event["mainImage"]

    return event_data


EventDiff = Tuple[str, Any, Any]


def _diff_event_fields(
    event: EventRecord,
    existing_event: Dict[str, Any],
    runtime: SyncRuntime,
) -> List[EventDiff]:
    """Return ``(field, expected_from_sheet, actual_in_wix)`` for each field that differs.

    An empty list means the sheet and Wix agree on every field we sync.
    """
    diffs: List[EventDiff] = []

    expected_title = event.name.strip()
    actual_title = existing_event.get("title") or ""
    if expected_title != actual_title:
        diffs.append(("title", expected_title, actual_title))

    expected_start = _wix_timestamp(
        convert_date_to_iso(event.start_date),
        event.start_time,
        runtime.config.timezone,
    )
    expected_end = _wix_timestamp(
        convert_date_to_iso(event.end_date),
        event.end_time,
        runtime.config.timezone,
    )

    date_settings = existing_event.get("dateAndTimeSettings") or {}
    actual_start = _normalize_wix_timestamp(date_settings.get("startDate") or "")
    if actual_start != expected_start:
        diffs.append(("startDate", expected_start, actual_start))

    actual_end = _normalize_wix_timestamp(date_settings.get("endDate") or "")
    if actual_end != expected_end:
        diffs.append(("endDate", expected_end, actual_end))

    expected_tz = runtime.config.timezone
    actual_tz = date_settings.get("timeZoneId") or ""
    if expected_tz != actual_tz:
        diffs.append(("timezone", expected_tz, actual_tz))

    location_settings = existing_event.get("location") or {}
    actual_location = (
        (location_settings.get("address") or {}).get("formattedAddress") or ""
    )
    if event.location != actual_location:
        diffs.append(("location", event.location, actual_location))

    expected_teaser = event.teaser.strip() if event.teaser else ""
    actual_teaser = existing_event.get("shortDescription") or ""
    if expected_teaser != actual_teaser:
        diffs.append(("shortDescription", expected_teaser, actual_teaser))

    expected_description = format_description_as_html(event.description or "")
    actual_description = existing_event.get("detailedDescription") or ""
    if expected_description != actual_description:
        diffs.append(("detailedDescription", expected_description, actual_description))

    return diffs


def _log_event_diff(event_name: str, diffs: List[EventDiff]) -> None:
    """Log a per-field diff. Field names at INFO, full values at DEBUG."""
    if not diffs:
        return
    changed = ", ".join(name for name, _, _ in diffs)
    logger.info("   📝 Changed fields: %s", changed)
    for name, expected, actual in diffs:
        logger.debug(
            "      %s\n        sheet : %r\n        wix   : %r",
            name, expected, actual,
        )


def needs_update(event: EventRecord, existing_event: Dict[str, Any], runtime: SyncRuntime) -> bool:
    return bool(_diff_event_fields(event, existing_event, runtime))


_UPCOMING_STATUSES = frozenset({"UPCOMING", "STARTED"})

CATEGORY_CONFIG_COLUMNS = [
    "event_name",
    "categories",
    "short_description",
    "detailed_description",
    "start_date",
    "start_time",
    "status",
    "event_id",
]

_CATEGORY_READONLY_COLUMNS = frozenset(
    c for c in CATEGORY_CONFIG_COLUMNS if c != "categories"
)


_category_cache: Dict[str, str] = {}
_category_cache_loaded = False


def _resolve_category_id(client, category_name: str) -> Optional[str]:
    """Get or create a Wix category by name. Returns the category ID."""
    global _category_cache_loaded

    if not category_name:
        return None

    name_lower = category_name.strip().lower()
    if name_lower in _category_cache:
        return _category_cache[name_lower]

    if not _category_cache_loaded:
        _category_cache_loaded = True
        existing = client.query_categories()
        for cat in existing:
            _category_cache[cat.get("name", "").strip().lower()] = cat["id"]
        if name_lower in _category_cache:
            return _category_cache[name_lower]

    try:
        created = client.create_category(category_name.strip())
        cat_id = created.get("id")
        if cat_id:
            _category_cache[name_lower] = cat_id
            logger.info("   🏷️  Created category: %s", category_name.strip())
            return cat_id
    except Exception as exc:
        logger.warning("   ⚠️  Failed to create category '%s': %s", category_name, exc)

    return None


def _assign_categories(client, event_id: str, event: EventRecord) -> None:
    """Assign the event to all its categories, creating any that don't exist."""
    if not event.category:
        return

    tags = [t.strip() for t in event.category.split(";") if t.strip()]
    if not tags:
        return

    assigned = []
    for tag in tags:
        cat_id = _resolve_category_id(client, tag)
        if not cat_id:
            continue
        try:
            client.assign_event_to_category(cat_id, event_id)
            assigned.append(tag)
        except Exception as exc:
            logger.warning("   ⚠️  Failed to assign category '%s': %s", tag, exc)

    if assigned:
        logger.info("   🏷️  Categories: %s", ", ".join(assigned))


def _ensure_ticket_definition(
    client,
    event_id: str,
    event: EventRecord,
) -> bool:
    """Create a ticket definition if one doesn't already exist. Returns True on success."""
    existing = client.get_ticket_definitions(event_id)
    if existing:
        names = [d.get("name", "") for d in existing]
        logger.info("   ℹ️  Tickets already exist (%s) — skipping", ", ".join(names))
        return True

    try:
        logger.info("   🎫 Creating ticket definition...")
        result = client.create_ticket_definition(
            event_id=event_id,
            ticket_name="Single Ticket",
            price=event.ticket_price,
            capacity=event.capacity,
        )
        actual = result.get("initialLimit") or result.get("actualLimit")
        logger.info(
            "   ✅ Ticket created: $%.2f (capacity: %s, limitPerCheckout: %s)",
            event.ticket_price,
            actual if actual else "unlimited",
            result.get("limitPerCheckout", "?"),
        )
        return True
    except Exception as ticket_error:
        logger.warning(
            "   ⚠️  Failed to create ticket (event still exists): %s",
            ticket_error,
        )
        logger.info("   💡 You can add tickets manually via Wix Dashboard")
        return False


def create_wix_event(
    event: EventRecord,
    runtime: SyncRuntime,
    auto_create_tickets: bool = True,
    draft: bool = False,
) -> Optional[str]:
    """Create a Wix event. Returns the new event id on success, None on failure."""
    file_descriptor = None
    if event.image_url:
        file_descriptor = upload_image_to_wix(event.image_url, event.name, runtime)
        if file_descriptor:
            logger.info("   ✅ Image uploaded successfully")
        else:
            logger.warning("   ⚠️  Proceeding without image")

    event_data = _build_wix_event_payload(
        event,
        runtime,
        file_descriptor=file_descriptor,
    )

    try:
        logger.debug("Event payload for %s: %s", event.name, json.dumps(event_data))

        client = runtime.get_wix_client()
        created_event = client.create_event(event_data, draft=draft)
        event_id = created_event.get("id")
        status = created_event.get("status", "UNKNOWN")

        logger.info("✅ Created event: %s (%s)", event.name, status)

        if draft:
            logger.info("   ℹ️  Tickets deferred — run publish-drafts to publish and add tickets")
        elif event.registration_type == "TICKETING" and auto_create_tickets:
            if event.ticket_name:
                # Multi-ticket specs (semicolon-separated names/prices/capacities)
                _create_tickets_from_config(client, event_id, event)
            elif event.ticket_price > 0:
                _ensure_ticket_definition(client, event_id, event)
        elif event.registration_type == "TICKETING" and not auto_create_tickets:
            logger.info("   ℹ️  Ticket creation skipped (--no-tickets flag set)")

        _assign_categories(client, event_id, event)

        return event_id
    except Exception as exc:
        logger.error("❌ Failed to create event %s: %s", event.name, exc)
        return None


def _repair_missing_tickets(
    client,
    event_id: str,
    event: EventRecord,
) -> None:
    """Check for missing ticket definitions and create one if needed."""
    if event.registration_type != "TICKETING" or event.ticket_price <= 0:
        return

    existing_defs = client.get_ticket_definitions(event_id)
    if existing_defs:
        return

    logger.info("   🔧 No ticket definitions found — repairing...")
    _ensure_ticket_definition(client, event_id, event)


def update_wix_event(
    event: EventRecord,
    runtime: SyncRuntime,
    existing_event_id: str,
    existing_event: Dict[str, Any],
    auto_create_tickets: bool = True,
) -> bool:
    from .images import is_wix_media_url, normalize_wix_media_url

    existing_image_url = (existing_event.get("mainImage") or {}).get("url") or ""
    same_wix_image = (
        bool(event.image_url)
        and is_wix_media_url(event.image_url)
        and bool(existing_image_url)
        and normalize_wix_media_url(event.image_url)
        == normalize_wix_media_url(existing_image_url)
    )

    file_descriptor = None
    if same_wix_image:
        # Image already lives in Wix Media and matches the event's current
        # mainImage (row was pulled from Wix) — the payload builder preserves
        # it, so skip the pointless re-download/re-upload.
        pass
    elif event.image_url:
        file_descriptor = upload_image_to_wix(event.image_url, event.name, runtime)
        if file_descriptor:
            logger.info("   ✅ Image uploaded successfully")
        else:
            logger.info("   ℹ️  Keeping existing image")

    event_data = _build_wix_event_payload(
        event,
        runtime,
        file_descriptor=file_descriptor,
        existing_event=existing_event,
    )

    try:
        logger.debug("Update payload for %s: %s", event.name, json.dumps(event_data))

        client = runtime.get_wix_client()
        client.update_event(existing_event_id, event_data)
        logger.info("♻️  Updated event: %s", event.name)

        if auto_create_tickets:
            _repair_missing_tickets(client, existing_event_id, event)

        return True
    except Exception as exc:
        logger.error("❌ Failed to update event %s: %s", event.name, exc)
        return False


def sync_events(runtime: SyncRuntime, auto_create_tickets: bool = True, draft: bool = False) -> bool:
    logger.info("🚀 Starting Google Sheets → Wix Events sync...\n")
    if draft:
        logger.info("📋 Mode: DRAFT (events created as drafts, no tickets)")
        logger.info("   Run publish-drafts when ready to go live")
    elif auto_create_tickets:
        logger.info("🎫 Auto-ticket creation: ENABLED")
    else:
        logger.info("🎫 Auto-ticket creation: DISABLED")
    logger.info("")

    try:
        events = fetch_events(runtime)
        existing_events = get_existing_event_keys(runtime)

        results = {"success": [], "updated": [], "failed": [], "skipped": []}

        logger.info("📅 Creating new events in Wix...\n")

        for event in events:
            start_date_iso = convert_date_to_iso(event.start_date)
            event_name = event.name.strip()
            event_key = f"{event_name}|{start_date_iso}|{event.start_time}"

            existing_entry = existing_events.get(event_key)
            if existing_entry:
                wix_event = existing_entry.get("event") or {}
                event_id = existing_entry.get("id")

                if not event_id or not wix_event:
                    logger.warning(
                        "⚠️  Missing data for existing event %s - skipping update",
                        event.name,
                    )
                    results["skipped"].append(event.name)
                    continue

                diffs = _diff_event_fields(event, wix_event, runtime)
                if diffs:
                    logger.info(
                        "♻️  Updating: %s on %s",
                        event.name,
                        event.start_date,
                    )
                    _log_event_diff(event.name, diffs)
                    if update_wix_event(
                        event,
                        runtime=runtime,
                        existing_event_id=event_id,
                        existing_event=wix_event,
                        auto_create_tickets=auto_create_tickets,
                    ):
                        results["updated"].append(event.name)
                    else:
                        results["failed"].append(event.name)
                    time.sleep(1)
                else:
                    if auto_create_tickets:
                        client = runtime.get_wix_client()
                        _repair_missing_tickets(client, event_id, event)
                    logger.info(
                        "⏭️  Skipped: %s on %s (no changes)",
                        event.name,
                        event.start_date,
                    )
                    results["skipped"].append(event.name)
                continue

            if create_wix_event(event, runtime=runtime, auto_create_tickets=auto_create_tickets, draft=draft):
                results["success"].append(event.name)
            else:
                results["failed"].append(event.name)

            time.sleep(1)

        logger.info("\n📈 Sync Complete!\n")

        logger.info("✅ Successfully created: %d events", len(results["success"]))
        if results["success"]:
            for name in results["success"]:
                logger.info("  • %s", name)

        if results["updated"]:
            logger.info("\n♻️  Updated: %d events", len(results["updated"]))
            for name in results["updated"]:
                logger.info("  • %s", name)

        if results["skipped"]:
            logger.info(
                "\n⏭️  Skipped (already exist / unchanged): %d events",
                len(results["skipped"]),
            )
            for name in results["skipped"]:
                logger.info("  • %s", name)

        if results["failed"]:
            logger.error("\n❌ Failed: %d events", len(results["failed"]))
            for name in results["failed"]:
                logger.error("  • %s", name)

        stats = runtime.cache_stats
        logger.info("\n🧮 Cache summary:")
        logger.info(
            "   Google Drive - hits: %s, misses: %s",
            stats["drive_hits"],
            stats["drive_misses"],
        )
        logger.info(
            "   Wix Media    - hits: %s, uploads: %s",
            stats["wix_hits"],
            stats["wix_uploads"],
        )

        return len(results["failed"]) == 0
    except Exception as exc:
        logger.error("Fatal error during sync: %s", exc)
        return False


def clean_synced_events(runtime: SyncRuntime, dry_run: bool = False) -> bool:
    """Delete only synced rope-class events in the current generated_events window."""
    logger.info("🧹 Cleaning synced events...\n")
    if dry_run:
        logger.info("🔍 DRY RUN — no deletions will be made\n")

    try:
        sheet_events = fetch_events(runtime)
        if not sheet_events:
            logger.warning("No events in generated_events sheet.")
            return False

        sheet_keys: set = set()
        for event in sheet_events:
            start_date_iso = convert_date_to_iso(event.start_date)
            key = f"{event.name.strip()}|{start_date_iso}|{event.start_time}"
            sheet_keys.add(key)

        logger.info("Found %d events in sheet to match against\n", len(sheet_keys))

        client = runtime.get_wix_client()
        all_events = list(client.iter_events(
            page_size=100, fieldsets=["CATEGORIES"],
        ))

        to_delete = []
        for wix_event in all_events:
            event_id = wix_event.get("id")
            title = (wix_event.get("title") or "").strip()
            status = wix_event.get("status", "")

            if status not in ("UPCOMING", "STARTED", "DRAFT"):
                continue

            cat_data = wix_event.get("categories", {})
            cat_list = cat_data.get("categories", []) if isinstance(cat_data, dict) else []
            cat_names = {c.get("name", "") for c in cat_list}

            if "rope" not in cat_names or "class" not in cat_names:
                continue

            start_settings = wix_event.get("dateAndTimeSettings", {}) or {}
            start_datetime = start_settings.get("startDate", "")
            local_parts = _localize_wix_start(start_datetime, runtime.config.timezone)
            if local_parts is None:
                continue

            date_part, time_part = local_parts
            key = f"{title}|{date_part}|{time_part}"

            if key in sheet_keys:
                to_delete.append((event_id, title, start_datetime))

        if not to_delete:
            logger.info("No matching synced events found to clean.")
            return True

        logger.info("Found %d synced events to delete:\n", len(to_delete))
        deleted = 0
        failed = 0
        for event_id, title, start in to_delete:
            if dry_run:
                logger.info("  DELETE: %s (%s)", title, start[:10])
            else:
                ok = client.delete_event(event_id, force=True)
                if ok:
                    logger.info("  ✅ Deleted: %s", title)
                    deleted += 1
                else:
                    logger.warning("  ❌ Failed: %s", title)
                    failed += 1
                time.sleep(0.3)

        if dry_run:
            logger.info("\n📊 Would delete: %d events", len(to_delete))
        else:
            logger.info("\n📊 Results: %d deleted, %d failed", deleted, failed)

        return failed == 0
    except Exception as exc:
        logger.error("Failed to clean synced events: %s", exc)
        return False


def _create_tickets_from_config(client, event_id: str, event: EventRecord) -> bool:
    """Create ticket definitions from the config ticket fields.

    Skips creation if the event already has ticket definitions to avoid
    duplicates that could confuse customers.
    """
    from .constants import DEFAULT_FEE_TYPE

    existing_defs = client.get_ticket_definitions(event_id)
    if existing_defs:
        existing_names = [d.get("name", "") for d in existing_defs]
        logger.info(
            "   ℹ️  Tickets already exist (%s) — skipping creation",
            ", ".join(existing_names),
        )
        return True

    specs = parse_tickets(
        ticket_name=event.ticket_name,
        ticket_price=event.ticket_price_raw or event.ticket_price,
        ticket_capacity=event.ticket_capacity,
    )
    if not specs:
        return True

    fee = event.fee_type or DEFAULT_FEE_TYPE
    ok = True
    for spec in specs:
        try:
            result = client.create_ticket_definition(
                event_id=event_id,
                ticket_name=spec.name,
                price=spec.price,
                capacity=spec.capacity,
                limit_per_checkout=spec.limit_per_checkout,
                fee_type=fee,
                sale_start=event.sale_start,
                sale_end=event.sale_end,
            )
            actual = result.get("initialLimit") or result.get("actualLimit")
            logger.info(
                "   🎫 Ticket '%s': $%.2f (capacity: %s)",
                spec.name, spec.price, actual if actual else "unlimited",
            )
        except Exception as exc:
            logger.warning("   ⚠️  Failed to create ticket '%s': %s", spec.name, exc)
            ok = False
    return ok


def compute_event_update_plan(
    client,
    runtime: SyncRuntime,
    event: EventRecord,
    event_id: str,
    wix_event: Dict[str, Any],
) -> Dict[str, Any]:
    """Diff an ``EventRecord`` against a live Wix event.

    Covers event fields, categories, per-event ticket tax, and existing ticket
    definitions (price/capacity, guarded by sold counts). Returns a plan dict
    consumed by :func:`apply_event_update_plan`. Shared by the sheet-backed
    ``push-config`` and the Notion-backed ``sync`` update path.
    """
    event_diffs = _diff_event_fields(event, wix_event, runtime)
    event_changed = bool(event_diffs)

    # Categories
    wix_cats = wix_event.get("categories", {})
    wix_cat_list = wix_cats.get("categories", []) if isinstance(wix_cats, dict) else []
    wix_cat_names = sorted(c.get("name", "") for c in wix_cat_list)
    desired_cat_names = sorted(
        t.strip() for t in (event.category or "").split(";") if t.strip()
    )
    cats_changed = wix_cat_names != desired_cat_names

    # Tax settings (per-event ticket tax)
    wix_reg = wix_event.get("registration", {})
    wix_tax = (wix_reg.get("tickets") or {}).get("taxSettings", {})
    desired_tax_name = event.tax_name or ""
    desired_tax_rate = event.tax_rate or ""
    desired_tax_type = event.tax_type or ""
    wix_has_tax = bool(wix_tax.get("name") or wix_tax.get("rate"))
    desired_has_tax = bool(desired_tax_name or desired_tax_rate)
    wix_rate = wix_tax.get("rate") or ""
    if desired_tax_rate and wix_rate:
        rate_matches = _rates_equal(desired_tax_rate, wix_rate)
    else:
        rate_matches = desired_tax_rate == wix_rate
    tax_changed = (
        (wix_has_tax != desired_has_tax)
        or (desired_tax_name != (wix_tax.get("name") or ""))
        or not rate_matches
        or (desired_tax_type != (wix_tax.get("type") or ""))
    )

    # Ticket price/capacity (with sales data for safety)
    wix_ticket_defs = client.get_ticket_definitions(event_id, include_sales=True)
    desired_specs = parse_tickets(
        ticket_name=event.ticket_name,
        ticket_price=event.ticket_price_raw or event.ticket_price,
        ticket_capacity=event.ticket_capacity,
    )
    tickets_changed = False
    ticket_updates: List[Dict[str, Any]] = []
    for spec in desired_specs:
        for td in wix_ticket_defs:
            if td.get("name") == spec.name:
                wix_price = float(td.get("pricingMethod", {}).get("fixedPrice", {}).get("value", "0"))
                wix_cap = td.get("initialLimit") or td.get("actualLimit")
                sold = (td.get("salesDetails") or {}).get("soldCount", 0)

                new_price = spec.price if wix_price != spec.price else None
                new_capacity = spec.capacity if (wix_cap and wix_cap != spec.capacity) else None

                if new_capacity is not None and new_capacity < sold:
                    logger.warning(
                        "   ⚠️  Cannot reduce '%s' capacity to %d — %d tickets already sold",
                        spec.name, new_capacity, sold,
                    )
                    new_capacity = None

                if new_price is not None or new_capacity is not None:
                    tickets_changed = True
                    ticket_updates.append({
                        "id": td["id"],
                        "revision": td["revision"],
                        "name": spec.name,
                        "new_price": new_price,
                        "new_capacity": new_capacity,
                        "old_price": wix_price,
                        "old_capacity": wix_cap,
                        "sold": sold,
                    })
                break

    changes = []
    if event_changed:
        changes.append("event data")
    if cats_changed:
        changes.append("categories")
    if tax_changed:
        changes.append("tax")
    if tickets_changed:
        changes.append("tickets")

    return {
        "event_diffs": event_diffs,
        "event_changed": event_changed,
        "cats_changed": cats_changed,
        "wix_cat_names": wix_cat_names,
        "desired_cat_names": desired_cat_names,
        "wix_cat_list": wix_cat_list,
        "tax_changed": tax_changed,
        "wix_has_tax": wix_has_tax,
        "desired_has_tax": desired_has_tax,
        "desired_tax_name": desired_tax_name,
        "desired_tax_rate": desired_tax_rate,
        "desired_tax_type": desired_tax_type,
        "tickets_changed": tickets_changed,
        "ticket_updates": ticket_updates,
        "any_changes": bool(changes),
        "change_desc": " + ".join(changes),
    }


def log_update_plan_dry_run(event: EventRecord, plan: Dict[str, Any]) -> None:
    """Log a dry-run preview of an update plan (shared formatting)."""
    event_name = event.name.strip()
    logger.info(
        "  UPDATE: %s on %s [%s]", event_name, event.start_date, plan["change_desc"],
    )
    if plan["event_changed"]:
        _log_event_diff(event_name, plan["event_diffs"])
    for tu in plan["ticket_updates"]:
        parts = []
        if tu["new_price"] is not None:
            parts.append(f"price ${tu['old_price']:.2f} -> ${tu['new_price']:.2f}")
        if tu["new_capacity"] is not None:
            parts.append(f"capacity {tu['old_capacity']} -> {tu['new_capacity']}")
        logger.info("    🎫 %s: %s", tu["name"], ", ".join(parts))


def apply_event_update_plan(
    client,
    runtime: SyncRuntime,
    event: EventRecord,
    event_id: str,
    wix_event: Dict[str, Any],
    plan: Dict[str, Any],
) -> bool:
    """Apply a plan from :func:`compute_event_update_plan` to Wix."""
    event_name = event.name.strip()
    ok = True

    if plan["event_changed"]:
        if not update_wix_event(event, runtime=runtime, existing_event_id=event_id, existing_event=wix_event):
            ok = False

    if plan["tax_changed"]:
        if not plan["desired_has_tax"] and plan["wix_has_tax"]:
            logger.warning(
                "   ⚠️  Cannot remove tax via API (Wix limitation) — "
                "use Wix Dashboard to disable tax for %s", event_name,
            )
        elif plan["desired_has_tax"]:
            try:
                client.update_event(event_id, {
                    "registration": {
                        "tickets": {
                            "taxSettings": {
                                "type": plan["desired_tax_type"] or "ADDED_AT_CHECKOUT",
                                "name": plan["desired_tax_name"],
                                "rate": plan["desired_tax_rate"],
                            }
                        }
                    }
                })
                logger.info(
                    "   💰 Tax updated: %s %s%%",
                    plan["desired_tax_name"], plan["desired_tax_rate"],
                )
            except Exception as exc:
                logger.warning("   ⚠️  Failed to update tax: %s", exc)

    if plan["tickets_changed"]:
        for tu in plan["ticket_updates"]:
            try:
                client.update_ticket_definition(
                    tu["id"],
                    tu["revision"],
                    price=tu["new_price"],
                    capacity=tu["new_capacity"],
                )
                parts = []
                if tu["new_price"] is not None:
                    parts.append(f"price -> ${tu['new_price']:.2f}")
                if tu["new_capacity"] is not None:
                    parts.append(f"capacity -> {tu['new_capacity']}")
                logger.info("   🎫 Updated '%s': %s", tu["name"], ", ".join(parts))
            except Exception as exc:
                logger.warning("   ⚠️  Failed to update ticket '%s': %s", tu["name"], exc)

    if plan["cats_changed"]:
        wix_cat_set = set(plan["wix_cat_names"])
        desired_cat_set = set(plan["desired_cat_names"])
        to_add = desired_cat_set - wix_cat_set
        to_remove = wix_cat_set - desired_cat_set

        for tag in to_add:
            cat_id = _resolve_category_id(client, tag)
            if cat_id:
                try:
                    client.assign_event_to_category(cat_id, event_id)
                    logger.info("   🏷️  Added category: %s", tag)
                except Exception as exc:
                    logger.warning("   ⚠️  Failed to add category '%s': %s", tag, exc)

        wix_cat_id_map = {c.get("name", ""): c.get("id", "") for c in plan["wix_cat_list"]}
        for tag in to_remove:
            cat_id = wix_cat_id_map.get(tag)
            if cat_id:
                try:
                    client.unassign_event_from_category(cat_id, event_id)
                    logger.info("   🗑️  Removed category: %s", tag)
                except Exception as exc:
                    logger.warning("   ⚠️  Failed to remove category '%s': %s", tag, exc)

    return ok


def push_config_events(
    runtime: SyncRuntime,
    dry_run: bool = False,
) -> bool:
    """Push config updates to existing Wix events. Does not create new events."""
    logger.info("🚀 Push config updates to Wix...\n")
    if dry_run:
        logger.info("🔍 DRY RUN — no changes will be made\n")

    try:
        events = fetch_config_events(runtime)
        if not events:
            logger.warning("No events in config_events sheet.")
            return False

        existing_events = get_existing_event_keys(
            runtime, fieldsets=["CATEGORIES", "REGISTRATION"],
        )
        client = runtime.get_wix_client()
        results = {"updated": [], "skipped": [], "not_found": [], "failed": []}

        for event in events:
            start_date_iso = convert_date_to_iso(event.start_date)
            event_name = event.name.strip()
            event_key = f"{event_name}|{start_date_iso}|{event.start_time}"

            existing_entry = existing_events.get(event_key)

            if not existing_entry:
                logger.warning("  ⚠️  Not found in Wix: %s on %s — use sync to create it first", event_name, event.start_date)
                results["not_found"].append(event_name)
                continue

            wix_event = existing_entry.get("event") or {}
            event_id = existing_entry.get("id")

            if not event_id or not wix_event:
                results["skipped"].append(event_name)
                continue

            plan = compute_event_update_plan(client, runtime, event, event_id, wix_event)

            if not plan["any_changes"]:
                if dry_run:
                    logger.info("  SKIP: %s (no changes)", event_name)
                else:
                    logger.info("⏭️  Skipped: %s (no changes)", event_name)
                results["skipped"].append(event_name)
                continue

            if dry_run:
                log_update_plan_dry_run(event, plan)
                results["updated"].append(event_name)
                continue

            logger.info(
                "♻️  Updating: %s on %s [%s]",
                event_name, event.start_date, plan["change_desc"],
            )
            if plan["event_changed"]:
                _log_event_diff(event_name, plan["event_diffs"])

            if apply_event_update_plan(client, runtime, event, event_id, wix_event, plan):
                results["updated"].append(event_name)
            else:
                results["failed"].append(event_name)
            time.sleep(1)

        logger.info("\n📈 Push Complete!\n")

        if results["updated"]:
            label = "Would update" if dry_run else "Updated"
            logger.info("♻️  %s: %d events", label, len(results["updated"]))
            for n in results["updated"]:
                logger.info("  • %s", n)

        if results["skipped"]:
            logger.info("\n⏭️  Skipped (no changes): %d events", len(results["skipped"]))

        if results["not_found"]:
            logger.warning("\n⚠️  Not found in Wix (create with sync first): %d events", len(results["not_found"]))
            for n in results["not_found"]:
                logger.warning("  • %s", n)

        if results["failed"]:
            logger.error("\n❌ Failed: %d events", len(results["failed"]))
            for n in results["failed"]:
                logger.error("  • %s", n)

        return len(results["failed"]) == 0
    except Exception as exc:
        logger.error("Fatal error during push: %s", exc)
        return False


def _wix_event_to_category_row(
    wix_event: Dict[str, Any],
    tz_name: str,
) -> Dict[str, Any]:
    """Build a thin category-config row from a Wix event payload."""
    title = wix_event.get("title", "")
    event_id = wix_event.get("id", "")
    status = wix_event.get("status", "") or ""

    cat_data = wix_event.get("categories", {}) or {}
    cat_list = cat_data.get("categories", []) if isinstance(cat_data, dict) else []
    category_names = [c.get("name", "") for c in cat_list if c.get("name")]
    categories_str = "; ".join(category_names)

    date_settings = wix_event.get("dateAndTimeSettings", {}) or {}
    start_raw = date_settings.get("startDate", "")
    start_date = ""
    start_time = ""
    if start_raw:
        localized = _localize_wix_start(start_raw, tz_name)
        if localized:
            iso_date, time_part = localized
            try:
                d = datetime.strptime(iso_date, "%Y-%m-%d")
                start_date = d.strftime("%m/%d/%Y")
            except ValueError:
                start_date = iso_date
            start_time = time_part

    return {
        "event_name": title,
        "categories": categories_str,
        "short_description": wix_event.get("shortDescription", "") or "",
        "detailed_description": wix_event.get("detailedDescription", "") or "",
        "start_date": start_date,
        "start_time": start_time,
        "status": status,
        "event_id": event_id,
    }


def _category_row_sort_key(row: Dict[str, Any]) -> str:
    """Sort key that orders rows by ``start_date`` descending.

    Returns an ISO-style ``YYYY-MM-DD`` string so that natural string sort
    matches chronological order. Rows missing a parsable date sort last.
    """
    raw = (row.get("start_date") or "").strip()
    if not raw:
        return ""
    for fmt in ("%m/%d/%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(raw, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return ""


def pull_category_config(runtime: SyncRuntime, scope: str = "upcoming") -> bool:
    """Pull events into the ``category_config`` tab + ``_last_pull`` snapshot.

    ``scope='upcoming'`` (default) keeps only ``UPCOMING``/``STARTED`` events
    (matching ``pull_config_events``). ``scope='all'`` keeps every non-draft
    event ever published. Drafts are excluded by the underlying iterator.
    """
    from .generator import write_category_config_to_sheet

    if scope not in {"upcoming", "all"}:
        logger.error("Invalid scope '%s' (expected 'upcoming' or 'all')", scope)
        return False

    logger.info("Pulling category config from Wix (scope=%s)...\n", scope)

    try:
        client = runtime.get_wix_client()
        tz_name = runtime.config.timezone

        all_events = list(client.iter_events(
            page_size=100,
            include_drafts=False,
            fieldsets=["DETAILS", "CATEGORIES"],
        ))

        upcoming_count = sum(
            1 for e in all_events if (e.get("status") or "") in _UPCOMING_STATUSES
        )
        past_count = len(all_events) - upcoming_count

        if scope == "upcoming":
            filtered = [
                e for e in all_events
                if (e.get("status") or "") in _UPCOMING_STATUSES
            ]
        else:
            filtered = list(all_events)

        if not filtered:
            logger.warning(
                "No events found for scope=%s (Wix returned %d non-draft events).",
                scope, len(all_events),
            )
            return False

        rows: List[Dict[str, Any]] = [
            _wix_event_to_category_row(e, tz_name) for e in filtered
        ]
        rows.sort(key=_category_row_sort_key, reverse=True)

        logger.info(
            "Pulled %d events (scope=%s: %d upcoming, %d past)",
            len(filtered), scope, upcoming_count, past_count,
        )

        snapshot_tab = runtime.config.category_config_tab + "_last_pull"
        logger.info("Writing snapshot to '%s'...", snapshot_tab)
        write_category_config_to_sheet(rows, runtime, snapshot_tab)

        config_tab = runtime.config.category_config_tab
        logger.info("Writing editable config to '%s'...", config_tab)
        ok = write_category_config_to_sheet(rows, runtime, config_tab)

        return ok
    except Exception as exc:
        logger.error("Failed to pull category config: %s", exc)
        return False


def _split_categories_cell(value: str) -> List[str]:
    """Split a ``a; b; c`` cell into stripped, non-empty tokens."""
    if not value:
        return []
    return [tok.strip() for tok in value.split(";") if tok.strip()]


def _index_events_by_id_and_key(
    runtime: SyncRuntime,
    fieldsets: Optional[List[str]] = None,
) -> tuple:
    """Return ``(by_id, by_key)`` dicts of live Wix events.

    ``by_id`` maps Wix event id → event payload (with categories).
    ``by_key`` maps ``"title|YYYY-MM-DD|HH:MM"`` → event payload, mirroring the
    matching logic used by ``get_existing_event_keys``.
    """
    client = runtime.get_wix_client()
    tz_name = runtime.config.timezone

    by_id: Dict[str, Dict[str, Any]] = {}
    by_key: Dict[str, Dict[str, Any]] = {}

    for event in client.iter_events(
        page_size=100, fieldsets=fieldsets or ["CATEGORIES"],
    ):
        event_id = event.get("id")
        if not event_id:
            continue
        by_id[event_id] = event

        title = (event.get("title") or "").strip()
        start_settings = event.get("dateAndTimeSettings", {}) or {}
        start_raw = start_settings.get("startDate", "")
        if not title or not start_raw:
            continue
        localized = _localize_wix_start(start_raw, tz_name)
        if localized is None:
            continue
        date_part, time_part = localized
        by_key[f"{title}|{date_part}|{time_part}"] = event

    return by_id, by_key


def push_category_config(
    runtime: SyncRuntime,
    scope: str = "upcoming",
    dry_run: bool = False,
) -> bool:
    """Push category-only edits from the sheet back to Wix.

    The only Wix endpoints touched are ``iter_events``, ``query_categories``,
    ``create_category``, ``assign_event_to_category``, and
    ``unassign_event_from_category``. Description columns in the sheet are
    deliberately ignored — this push path never calls ``update_event``.
    """
    from .sheets import fetch_category_config_rows

    if scope not in {"upcoming", "all"}:
        logger.error("Invalid scope '%s' (expected 'upcoming' or 'all')", scope)
        return False

    logger.info("🚀 Push category config to Wix (scope=%s)...\n", scope)
    if dry_run:
        logger.info("🔍 DRY RUN — no changes will be made\n")

    try:
        rows = fetch_category_config_rows(runtime)
        if not rows:
            logger.warning(
                "No rows in category config tab '%s'.",
                runtime.config.category_config_tab,
            )
            return False

        client = runtime.get_wix_client()
        by_id, by_key = _index_events_by_id_and_key(runtime)

        results: Dict[str, List[str]] = {
            "updated": [],
            "skipped": [],
            "out_of_scope": [],
            "not_found": [],
            "failed": [],
        }

        for row in rows:
            event_name = (row.get("event_name") or "").strip() or "(unnamed)"
            row_status = (row.get("status") or "").strip().upper()

            if scope == "upcoming" and row_status not in _UPCOMING_STATUSES:
                logger.info(
                    "  ⏭️  Out of scope: %s (status=%s)", event_name, row_status or "?",
                )
                results["out_of_scope"].append(event_name)
                continue

            event_id = (row.get("event_id") or "").strip()
            wix_event: Optional[Dict[str, Any]] = None
            if event_id:
                wix_event = by_id.get(event_id)

            if wix_event is None:
                row_date_iso = ""
                if row.get("start_date"):
                    try:
                        row_date_iso = convert_date_to_iso(row["start_date"])
                    except Exception:
                        row_date_iso = ""
                fallback_key = f"{event_name}|{row_date_iso}|{row.get('start_time', '')}"
                wix_event = by_key.get(fallback_key)
                if wix_event is not None:
                    event_id = wix_event.get("id") or ""

            if wix_event is None or not event_id:
                logger.warning(
                    "  ⚠️  Not found in Wix: %s (event_id='%s')",
                    event_name, row.get("event_id", ""),
                )
                results["not_found"].append(event_name)
                continue

            wix_cats = wix_event.get("categories", {}) or {}
            wix_cat_list = (
                wix_cats.get("categories", []) if isinstance(wix_cats, dict) else []
            )
            wix_cat_names = {
                (c.get("name") or "").strip() for c in wix_cat_list if c.get("name")
            }
            wix_cat_id_map = {
                (c.get("name") or "").strip(): c.get("id", "")
                for c in wix_cat_list
                if c.get("name") and c.get("id")
            }

            sheet_cat_names = set(_split_categories_cell(row.get("categories", "")))

            to_add = sheet_cat_names - wix_cat_names
            to_remove = wix_cat_names - sheet_cat_names

            if not to_add and not to_remove:
                if dry_run:
                    logger.info("  SKIP: %s (no category changes)", event_name)
                else:
                    logger.info("⏭️  Skipped: %s (no category changes)", event_name)
                results["skipped"].append(event_name)
                continue

            change_parts: List[str] = []
            if to_add:
                change_parts.append(f"add: {', '.join(sorted(to_add))}")
            if to_remove:
                change_parts.append(f"remove: {', '.join(sorted(to_remove))}")
            change_desc = " | ".join(change_parts)

            if dry_run:
                logger.info("  UPDATE: %s [%s]", event_name, change_desc)
                results["updated"].append(event_name)
                continue

            logger.info("♻️  Updating: %s [%s]", event_name, change_desc)
            row_ok = True

            for tag in sorted(to_add):
                cat_id = _resolve_category_id(client, tag)
                if not cat_id:
                    row_ok = False
                    continue
                try:
                    client.assign_event_to_category(cat_id, event_id)
                    logger.info("   🏷️  Added category: %s", tag)
                except Exception as exc:
                    logger.warning(
                        "   ⚠️  Failed to add category '%s': %s", tag, exc,
                    )
                    row_ok = False

            for tag in sorted(to_remove):
                cat_id = wix_cat_id_map.get(tag)
                if not cat_id:
                    logger.warning(
                        "   ⚠️  No live category id for '%s' — skipping unassign",
                        tag,
                    )
                    continue
                try:
                    client.unassign_event_from_category(cat_id, event_id)
                    logger.info("   🗑️  Removed category: %s", tag)
                except Exception as exc:
                    logger.warning(
                        "   ⚠️  Failed to remove category '%s': %s", tag, exc,
                    )
                    row_ok = False

            if row_ok:
                results["updated"].append(event_name)
            else:
                results["failed"].append(event_name)
            time.sleep(0.3)

        logger.info("\n📈 Push Complete!\n")

        if results["updated"]:
            label = "Would update" if dry_run else "Updated"
            logger.info("♻️  %s: %d events", label, len(results["updated"]))
            for n in results["updated"]:
                logger.info("  • %s", n)

        if results["skipped"]:
            logger.info(
                "\n⏭️  Skipped (no category changes): %d events",
                len(results["skipped"]),
            )

        if results["out_of_scope"]:
            logger.info(
                "\n🚫 Out of scope (status not in {UPCOMING, STARTED}): %d events",
                len(results["out_of_scope"]),
            )

        if results["not_found"]:
            logger.warning(
                "\n⚠️  Not found in Wix: %d events",
                len(results["not_found"]),
            )
            for n in results["not_found"]:
                logger.warning("  • %s", n)

        if results["failed"]:
            logger.error("\n❌ Failed: %d events", len(results["failed"]))
            for n in results["failed"]:
                logger.error("  • %s", n)

        return len(results["failed"]) == 0
    except Exception as exc:
        logger.error("Fatal error during category push: %s", exc)
        return False


# ---------------------------------------------------------------------------
# Site config (eCommerce tax-by-location) round-trip
# ---------------------------------------------------------------------------


def _tax_region_label(region: Dict[str, Any]) -> str:
    """Human-friendly label for a tax region, e.g. ``CA / ON`` or ``US``."""
    country = (region.get("country") or "").strip()
    subdivision = (region.get("subdivision") or "").strip()
    if country and subdivision:
        return f"{country} / {subdivision}"
    return country or subdivision


def _rates_equal(a: Any, b: Any) -> bool:
    """Compare two tax rates numerically, tolerating scale differences."""
    try:
        return abs(float(a) - float(b)) < 1e-9
    except (TypeError, ValueError):
        return str(a).strip() == str(b).strip()


def _tax_mapping_to_site_row(
    mapping: Dict[str, Any],
    regions_by_id: Dict[str, Dict[str, Any]],
) -> Dict[str, Any]:
    """Build a site_config row from an existing manual tax mapping."""
    from .constants import TAX_LOCATION_SETTING, tax_rate_decimal_to_percent

    region = regions_by_id.get(mapping.get("taxRegionId", ""), {})
    region_label = _tax_region_label(region)
    return {
        "setting_type": TAX_LOCATION_SETTING,
        "jurisdiction": (mapping.get("jurisdiction") or "") or region_label,
        "region": region_label,
        "tax_name": mapping.get("taxName", "") or "",
        "tax_type": mapping.get("taxType", "") or "",
        "tax_rate": tax_rate_decimal_to_percent(mapping.get("taxRate", "")),
        "region_id": mapping.get("taxRegionId", "") or "",
        "group_id": mapping.get("taxGroupId", "") or "",
        "mapping_id": mapping.get("id", "") or "",
        "revision": str(mapping.get("revision", "") or ""),
    }


def _blank_region_site_row(
    region: Dict[str, Any],
    group_id: str,
) -> Dict[str, Any]:
    """Build a site_config row for a region that has no tax mapping yet.

    ``tax_rate`` is left blank so the operator can fill it in; pushing a blank
    rate is a no-op, while filling in (e.g.) ``13`` makes push create the
    mapping at that rate.
    """
    from .constants import DEFAULT_TAX_NAME, TAX_LOCATION_SETTING

    region_label = _tax_region_label(region)
    return {
        "setting_type": TAX_LOCATION_SETTING,
        "jurisdiction": region_label,
        "region": region_label,
        "tax_name": DEFAULT_TAX_NAME,
        "tax_type": "",
        "tax_rate": "",
        "region_id": region.get("id", "") or "",
        "group_id": group_id,
        "mapping_id": "",
        "revision": "",
    }


def _select_default_tax_group_id(groups: List[Dict[str, Any]]) -> str:
    """Pick a sensible default tax group id for regions with no mapping.

    Prefers a group named ``standard``/``default`` (case-insensitive), otherwise
    falls back to the first group. Returns ``""`` if there are no groups.
    """
    if not groups:
        return ""
    for preferred in ("standard", "default"):
        for group in groups:
            if (group.get("name") or "").strip().lower() == preferred:
                return group.get("id", "") or ""
    return groups[0].get("id", "") or ""


def _site_config_row_sort_key(row: Dict[str, Any]) -> Tuple[str, int]:
    """Sort site_config rows by region label, mapped rows before blank ones."""
    return (row.get("region", "") or "", 0 if row.get("mapping_id") else 1)


def pull_site_config(runtime: SyncRuntime) -> bool:
    """Pull eCommerce tax-by-location settings into the ``site_config`` tab.

    Joins tax regions, tax groups, and manual tax mappings into one row per
    mapping, plus a blank-rate row for any region that has no mapping yet, then
    writes both an editable tab and a ``_last_pull`` snapshot.
    """
    from .generator import write_site_config_to_sheet

    logger.info("Pulling site config (tax locations) from Wix...\n")

    try:
        client = runtime.get_wix_client()

        regions = client.query_tax_regions()
        groups = client.query_tax_groups()
        mappings = client.query_manual_tax_mappings()

        logger.info(
            "Found %d tax region(s), %d tax group(s), %d tax mapping(s)",
            len(regions), len(groups), len(mappings),
        )

        if not regions and not mappings:
            logger.warning(
                "No tax regions or mappings found. Your Wix site may have no "
                "tax regions configured yet, or the API key is missing the "
                "eCommerce 'Manage Orders' permission scope."
            )
            return False

        regions_by_id = {r.get("id", ""): r for r in regions if r.get("id")}
        default_group_id = _select_default_tax_group_id(groups)

        rows: List[Dict[str, Any]] = [
            _tax_mapping_to_site_row(m, regions_by_id) for m in mappings
        ]

        mapped_region_ids = {m.get("taxRegionId", "") for m in mappings}
        for region in regions:
            if region.get("id", "") in mapped_region_ids:
                continue
            rows.append(_blank_region_site_row(region, default_group_id))

        rows.sort(key=_site_config_row_sort_key)

        snapshot_tab = runtime.config.site_config_tab + "_last_pull"
        logger.info("Writing snapshot to '%s'...", snapshot_tab)
        write_site_config_to_sheet(rows, runtime, snapshot_tab)

        config_tab = runtime.config.site_config_tab
        logger.info("Writing editable config to '%s'...", config_tab)
        ok = write_site_config_to_sheet(rows, runtime, config_tab)

        return ok
    except Exception as exc:
        logger.error("Failed to pull site config: %s", exc)
        return False


def push_site_config(runtime: SyncRuntime, dry_run: bool = False) -> bool:
    """Push tax-location edits from ``site_config`` back to Wix.

    For each ``tax_location`` row with a non-blank ``tax_rate``: updates the
    matching manual tax mapping if its rate/name/type differ, or creates a new
    mapping (batched via bulk create) when the region+group has none. Blank
    rates are skipped and mappings are never deleted. The only Wix endpoints
    touched are ``query_manual_tax_mappings``, ``update_manual_tax_mapping``,
    and ``bulk_create_manual_tax_mappings``.
    """
    from .sheets import fetch_site_config_rows

    logger.info("🚀 Push site config (tax locations) to Wix...\n")
    if dry_run:
        logger.info("🔍 DRY RUN — no changes will be made\n")

    try:
        rows = fetch_site_config_rows(runtime)
        if not rows:
            logger.warning(
                "No rows in site config tab '%s'.",
                runtime.config.site_config_tab,
            )
            return False
        return process_site_config_rows(runtime, rows, dry_run=dry_run)
    except Exception as exc:
        logger.error("Fatal error during site config push: %s", exc)
        return False


def process_site_config_rows(
    runtime: SyncRuntime,
    rows: List[Dict[str, Any]],
    dry_run: bool = False,
) -> bool:
    """Apply tax-location rows to Wix (shared by sheet and Notion backends)."""
    from .constants import TAX_LOCATION_SETTING, tax_rate_percent_to_decimal

    try:
        client = runtime.get_wix_client()
        live_mappings = client.query_manual_tax_mappings()
        live_by_id = {m.get("id", ""): m for m in live_mappings if m.get("id")}
        live_by_region_group: Dict[Tuple[str, str], Dict[str, Any]] = {
            (m.get("taxRegionId", ""), m.get("taxGroupId", "")): m
            for m in live_mappings
        }

        results: Dict[str, List[str]] = {
            "updated": [],
            "created": [],
            "skipped": [],
            "invalid": [],
            "failed": [],
        }
        to_create: List[Dict[str, Any]] = []
        create_labels: List[str] = []

        for row in rows:
            label = (
                row.get("jurisdiction")
                or row.get("region")
                or row.get("region_id")
                or "(unknown region)"
            )

            if (row.get("setting_type") or "").strip().lower() != TAX_LOCATION_SETTING:
                logger.info("  ⏭️  Skipped: %s (setting_type not tax_location)", label)
                results["skipped"].append(label)
                continue

            desired_decimal = tax_rate_percent_to_decimal(row.get("tax_rate", ""))
            if not desired_decimal:
                logger.info("  ⏭️  Skipped: %s (no tax rate set)", label)
                results["skipped"].append(label)
                continue

            mapping_id = (row.get("mapping_id") or "").strip()
            region_id = (row.get("region_id") or "").strip()
            group_id = (row.get("group_id") or "").strip()
            sheet_name = (row.get("tax_name") or "").strip()
            sheet_type = (row.get("tax_type") or "").strip()

            live: Optional[Dict[str, Any]] = None
            if mapping_id and mapping_id in live_by_id:
                live = live_by_id[mapping_id]
            elif region_id and group_id and (region_id, group_id) in live_by_region_group:
                live = live_by_region_group[(region_id, group_id)]

            if live is not None:
                rate_changed = not _rates_equal(live.get("taxRate", ""), desired_decimal)
                name_changed = bool(sheet_name) and sheet_name != (live.get("taxName") or "")
                type_changed = bool(sheet_type) and sheet_type != (live.get("taxType") or "")

                if not (rate_changed or name_changed or type_changed):
                    logger.info("  ⏭️  Skipped: %s (no changes)", label)
                    results["skipped"].append(label)
                    continue

                change_parts: List[str] = []
                if rate_changed:
                    from .constants import tax_rate_decimal_to_percent
                    change_parts.append(
                        "rate %s%% -> %s%%" % (
                            tax_rate_decimal_to_percent(live.get("taxRate", "")) or "?",
                            row.get("tax_rate", ""),
                        )
                    )
                if name_changed:
                    change_parts.append(f"name -> {sheet_name}")
                if type_changed:
                    change_parts.append(f"type -> {sheet_type}")
                change_desc = ", ".join(change_parts)

                if dry_run:
                    logger.info("  UPDATE: %s [%s]", label, change_desc)
                    results["updated"].append(label)
                    continue

                try:
                    client.update_manual_tax_mapping(
                        live.get("id", ""),
                        str(live.get("revision", "")),
                        tax_rate=desired_decimal if rate_changed else None,
                        tax_name=sheet_name if name_changed else None,
                        tax_type=sheet_type if type_changed else None,
                    )
                    logger.info("♻️  Updated: %s [%s]", label, change_desc)
                    results["updated"].append(label)
                except Exception as exc:
                    logger.warning("   ⚠️  Failed to update mapping for %s: %s", label, exc)
                    results["failed"].append(label)
                time.sleep(0.3)
                continue

            # No live mapping — create one if we have both ids.
            if not region_id or not group_id:
                logger.warning(
                    "  ⚠️  Cannot create mapping for %s — missing region_id/group_id "
                    "(run pull-site-config to populate them)", label,
                )
                results["invalid"].append(label)
                continue

            mapping: Dict[str, Any] = {
                "taxGroupId": group_id,
                "taxRegionId": region_id,
                "taxRate": desired_decimal,
            }
            if sheet_name:
                mapping["taxName"] = sheet_name
            if sheet_type:
                mapping["taxType"] = sheet_type
            jurisdiction = (row.get("jurisdiction") or "").strip()
            if jurisdiction:
                mapping["jurisdiction"] = jurisdiction

            if dry_run:
                logger.info(
                    "  CREATE: %s [rate %s%%]", label, row.get("tax_rate", ""),
                )
                results["created"].append(label)
                continue

            to_create.append(mapping)
            create_labels.append(label)

        # Bulk-create any new mappings (max 100 per request).
        if to_create and not dry_run:
            for start in range(0, len(to_create), 100):
                batch = to_create[start:start + 100]
                batch_labels = create_labels[start:start + 100]
                try:
                    resp = client.bulk_create_manual_tax_mappings(batch)
                    meta = resp.get("bulkActionMetadata", {}) if isinstance(resp, dict) else {}
                    item_results = resp.get("results", []) if isinstance(resp, dict) else []
                    failed_idx = {
                        r.get("itemMetadata", {}).get("originalIndex")
                        for r in item_results
                        if not r.get("itemMetadata", {}).get("success", True)
                    }
                    for i, lbl in enumerate(batch_labels):
                        if i in failed_idx:
                            logger.warning("   ⚠️  Failed to create mapping for %s", lbl)
                            results["failed"].append(lbl)
                        else:
                            logger.info("   ➕ Created mapping: %s", lbl)
                            results["created"].append(lbl)
                    logger.debug(
                        "   Bulk create: %s succeeded, %s failed",
                        meta.get("totalSuccesses", "?"),
                        meta.get("totalFailures", "?"),
                    )
                except Exception as exc:
                    logger.warning("   ⚠️  Bulk create failed: %s", exc)
                    results["failed"].extend(batch_labels)
                time.sleep(0.3)
        elif to_create and dry_run:
            # Dry-run creates were already recorded per-row above.
            pass

        logger.info("\n📈 Push Complete!\n")

        if results["updated"]:
            label = "Would update" if dry_run else "Updated"
            logger.info("♻️  %s: %d location(s)", label, len(results["updated"]))
            for n in results["updated"]:
                logger.info("  • %s", n)

        if results["created"]:
            label = "Would create" if dry_run else "Created"
            logger.info("\n➕ %s: %d mapping(s)", label, len(results["created"]))
            for n in results["created"]:
                logger.info("  • %s", n)

        if results["skipped"]:
            logger.info("\n⏭️  Skipped (no rate / no change): %d", len(results["skipped"]))

        if results["invalid"]:
            logger.warning(
                "\n⚠️  Could not act (missing region_id/group_id): %d",
                len(results["invalid"]),
            )
            for n in results["invalid"]:
                logger.warning("  • %s", n)

        if results["failed"]:
            logger.error("\n❌ Failed: %d", len(results["failed"]))
            for n in results["failed"]:
                logger.error("  • %s", n)

        return len(results["failed"]) == 0
    except Exception as exc:
        logger.error("Fatal error during site config push: %s", exc)
        return False


