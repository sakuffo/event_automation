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


def _extract_bullet_text(line: str) -> Optional[str]:
    stripped = line.lstrip()
    for marker in _BULLET_MARKERS:
        if stripped.startswith(marker):
            return stripped[len(marker) :].strip()
    return None


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
    """Escape HTML then apply inline markdown."""
    return _inline_markdown(escape(text.strip()))


def format_description_as_html(raw: str) -> str:
    """Convert text from Sheets into HTML for Wix.

    Supports paragraphs, bullet lists, **bold**, *italic*, and [links](url).
    """
    if not raw:
        return ""

    normalized = raw.replace("\r\n", "\n").replace("\r", "\n").strip()
    if not normalized:
        return ""

    lines = normalized.split("\n")
    paragraphs: List[List[str]] = []
    current: List[str] = []

    for line in lines:
        if line.strip() == "":
            if current:
                paragraphs.append(current)
                current = []
            continue
        current.append(line.rstrip())

    if current:
        paragraphs.append(current)

    html_blocks: List[str] = []

    for para in paragraphs:
        bullet_items: List[str] = []
        all_bullets = True

        for entry in para:
            bullet = _extract_bullet_text(entry)
            if bullet is None:
                all_bullets = False
                break
            bullet_items.append(_format_line(bullet))

        if all_bullets and bullet_items:
            items_html = "".join(f"<li>{item}</li>" for item in bullet_items)
            html_blocks.append(f"<ul>{items_html}</ul>")
            continue

        joined = "<br/>".join(_format_line(item) for item in para)
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
                    "type": DEFAULT_TAX_TYPE,
                    "name": DEFAULT_TAX_NAME,
                    "rate": DEFAULT_TAX_RATE,
                }
            }

    teaser = event.teaser.strip() if event.teaser else ""
    if teaser:
        event_data["shortDescription"] = teaser
    elif existing_event and existing_event.get("shortDescription"):
        event_data["shortDescription"] = ""

    raw_desc = event.description or ""
    if raw_desc.lstrip().startswith("<"):
        formatted_description = raw_desc
    else:
        formatted_description = format_description_as_html(raw_desc)
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


def needs_update(event: EventRecord, existing_event: Dict[str, Any], runtime: SyncRuntime) -> bool:
    expected_title = event.name.strip()
    if expected_title != existing_event.get("title"):
        return True

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
    if actual_start is None or actual_start != expected_start:
        return True
    actual_end = _normalize_wix_timestamp(date_settings.get("endDate") or "")
    if actual_end is None or actual_end != expected_end:
        return True
    if runtime.config.timezone != date_settings.get("timeZoneId"):
        return True

    location_settings = existing_event.get("location") or {}
    formatted_address = (
        (location_settings.get("address") or {}).get("formattedAddress") or ""
    )
    if event.location != formatted_address:
        return True

    expected_teaser = event.teaser.strip() if event.teaser else ""
    if expected_teaser != (existing_event.get("shortDescription") or ""):
        return True

    raw_desc = event.description or ""
    if raw_desc.lstrip().startswith("<"):
        expected_description = raw_desc
    else:
        expected_description = format_description_as_html(raw_desc)
    if expected_description != (existing_event.get("detailedDescription") or ""):
        return True

    return False


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
) -> bool:
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
        else:
            should_create_ticket = (
                auto_create_tickets
                and event.registration_type == "TICKETING"
                and event.ticket_price > 0
            )

            if should_create_ticket:
                _ensure_ticket_definition(client, event_id, event)
            elif event.registration_type == "TICKETING" and not auto_create_tickets:
                logger.info("   ℹ️  Ticket creation skipped (--no-tickets flag set)")

        _assign_categories(client, event_id, event)

        return True
    except Exception as exc:
        logger.error("❌ Failed to create event %s: %s", event.name, exc)
        return False


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
    file_descriptor = None
    if event.image_url:
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

                if needs_update(event, wix_event, runtime):
                    logger.info(
                        "♻️  Updating: %s on %s",
                        event.name,
                        event.start_date,
                    )
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
        ticket_price=event.ticket_price,
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

            event_changed = needs_update(event, wix_event, runtime)

            # Check categories
            wix_cats = wix_event.get("categories", {})
            wix_cat_list = wix_cats.get("categories", []) if isinstance(wix_cats, dict) else []
            wix_cat_names = sorted(c.get("name", "") for c in wix_cat_list)
            sheet_cat_names = sorted(t.strip() for t in (event.category or "").split(";") if t.strip())
            cats_changed = wix_cat_names != sheet_cat_names

            # Check tax settings
            wix_reg = wix_event.get("registration", {})
            wix_tax = (wix_reg.get("tickets") or {}).get("taxSettings", {})
            sheet_tax_name = event.tax_name or ""
            sheet_tax_rate = event.tax_rate or ""
            sheet_tax_type = event.tax_type or ""
            wix_has_tax = bool(wix_tax.get("name") or wix_tax.get("rate"))
            sheet_has_tax = bool(sheet_tax_name or sheet_tax_rate)
            tax_changed = (
                (wix_has_tax != sheet_has_tax)
                or (sheet_tax_name != (wix_tax.get("name") or ""))
                or (sheet_tax_rate != (wix_tax.get("rate") or ""))
                or (sheet_tax_type != (wix_tax.get("type") or ""))
            )

            # Check ticket price/capacity (with sales data for safety)
            wix_ticket_defs = client.get_ticket_definitions(event_id, include_sales=True)
            sheet_specs = parse_tickets(
                ticket_name=event.ticket_name,
                ticket_price=event.ticket_price,
                ticket_capacity=event.ticket_capacity,
            )
            tickets_changed = False
            ticket_updates: List[Dict[str, Any]] = []
            for spec in sheet_specs:
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

            if not event_changed and not cats_changed and not tax_changed and not tickets_changed:
                if dry_run:
                    logger.info("  SKIP: %s (no changes)", event_name)
                else:
                    logger.info("⏭️  Skipped: %s (no changes)", event_name)
                results["skipped"].append(event_name)
                continue

            changes = []
            if event_changed:
                changes.append("event data")
            if cats_changed:
                changes.append("categories")
            if tax_changed:
                changes.append("tax")
            if tickets_changed:
                changes.append("tickets")
            change_desc = " + ".join(changes)

            if dry_run:
                logger.info("  UPDATE: %s on %s [%s]", event_name, event.start_date, change_desc)
                for tu in ticket_updates:
                    parts = []
                    if tu["new_price"] is not None:
                        parts.append(f"price ${tu['old_price']:.2f} -> ${tu['new_price']:.2f}")
                    if tu["new_capacity"] is not None:
                        parts.append(f"capacity {tu['old_capacity']} -> {tu['new_capacity']}")
                    logger.info("    🎫 %s: %s", tu["name"], ", ".join(parts))
                results["updated"].append(event_name)
                continue

            logger.info("♻️  Updating: %s on %s [%s]", event_name, event.start_date, change_desc)
            ok = True

            if event_changed:
                if not update_wix_event(event, runtime=runtime, existing_event_id=event_id, existing_event=wix_event):
                    ok = False

            if tax_changed:
                if not sheet_has_tax and wix_has_tax:
                    logger.warning(
                        "   ⚠️  Cannot remove tax via API (Wix limitation) — "
                        "use Wix Dashboard to disable tax for %s", event_name,
                    )
                elif sheet_has_tax:
                    try:
                        client.update_event(event_id, {
                            "registration": {
                                "tickets": {
                                    "taxSettings": {
                                        "type": sheet_tax_type or "ADDED_AT_CHECKOUT",
                                        "name": sheet_tax_name,
                                        "rate": sheet_tax_rate,
                                    }
                                }
                            }
                        })
                        logger.info("   💰 Tax updated: %s %s%%", sheet_tax_name, sheet_tax_rate)
                    except Exception as exc:
                        logger.warning("   ⚠️  Failed to update tax: %s", exc)

            if tickets_changed:
                for tu in ticket_updates:
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

            if cats_changed:
                wix_cat_set = set(wix_cat_names)
                sheet_cat_set = set(sheet_cat_names)
                to_add = sheet_cat_set - wix_cat_set
                to_remove = wix_cat_set - sheet_cat_set

                for tag in to_add:
                    cat_id = _resolve_category_id(client, tag)
                    if cat_id:
                        try:
                            client.assign_event_to_category(cat_id, event_id)
                            logger.info("   🏷️  Added category: %s", tag)
                        except Exception as exc:
                            logger.warning("   ⚠️  Failed to add category '%s': %s", tag, exc)

                wix_cat_id_map = {c.get("name", ""): c.get("id", "") for c in wix_cat_list}
                for tag in to_remove:
                    cat_id = wix_cat_id_map.get(tag)
                    if cat_id:
                        try:
                            client.unassign_event_from_category(cat_id, event_id)
                            logger.info("   🗑️  Removed category: %s", tag)
                        except Exception as exc:
                            logger.warning("   ⚠️  Failed to remove category '%s': %s", tag, exc)

            if ok:
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


