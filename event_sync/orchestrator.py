"""High-level orchestration helpers for syncing events."""

from __future__ import annotations

import json
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
from .models import EventRecord
from .runtime import SyncRuntime
from .sheets import fetch_events
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


def format_description_as_html(raw: str) -> str:
    """Convert plain text from Sheets into minimal HTML for Wix."""

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
            bullet_items.append(escape(bullet))

        if all_bullets and bullet_items:
            items_html = "".join(f"<li>{item}</li>" for item in bullet_items)
            html_blocks.append(f"<ul>{items_html}</ul>")
            continue

        joined = "<br/>".join(escape(item.strip()) for item in para)
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


def get_existing_event_keys(runtime: SyncRuntime) -> Dict[str, Dict[str, Any]]:
    logger.info("🔍 Checking for existing events in Wix...")
    try:
        client = runtime.get_wix_client()
        existing_events: Dict[str, Dict[str, Any]] = {}
        total_events = 0

        for event in client.iter_events(page_size=200):
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
        "registration": {"initialType": event.registration_type},
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

    registration_settings = existing_event.get("registration") or {}
    if event.registration_type != registration_settings.get("initialType"):
        return True

    expected_teaser = event.teaser.strip() if event.teaser else ""
    if expected_teaser != (existing_event.get("shortDescription") or ""):
        return True

    expected_description = format_description_as_html(event.description or "")
    if expected_description != (existing_event.get("detailedDescription") or ""):
        return True

    return False


def create_wix_event(
    event: EventRecord,
    runtime: SyncRuntime,
    auto_create_tickets: bool = True,
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
        created_event = client.create_event(event_data)
        event_id = created_event.get("id")

        logger.info("✅ Created event: %s", event.name)

        should_create_ticket = (
            auto_create_tickets
            and event.registration_type == "TICKETING"
            and event.ticket_price > 0
        )

        if should_create_ticket:
            try:
                logger.info("   🎫 Creating ticket definition...")
                client.create_ticket_definition(
                    event_id=event_id,
                    ticket_name="General Admission",
                    price=event.ticket_price,
                    capacity=event.capacity,
                )
                logger.info(
                    "   ✅ Ticket created: $%.2f (capacity: %d)",
                    event.ticket_price,
                    event.capacity,
                )
            except Exception as ticket_error:
                logger.warning(
                    "   ⚠️  Failed to create ticket (event still exists): %s",
                    ticket_error,
                )
                logger.info("   💡 You can add tickets manually via Wix Dashboard")
        elif event.registration_type == "TICKETING" and not auto_create_tickets:
            logger.info("   ℹ️  Ticket creation skipped (--no-tickets flag set)")
            logger.info("   💡 Re-run without --no-tickets to enable automatic tickets or add them manually via Wix Dashboard")

        return True
    except Exception as exc:
        logger.error("❌ Failed to create event %s: %s", event.name, exc)
        return False


def update_wix_event(
    event: EventRecord,
    runtime: SyncRuntime,
    existing_event_id: str,
    existing_event: Dict[str, Any],
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

        return True
    except Exception as exc:
        logger.error("❌ Failed to update event %s: %s", event.name, exc)
        return False


def sync_events(runtime: SyncRuntime, auto_create_tickets: bool = True) -> bool:
    logger.info("🚀 Starting Google Sheets → Wix Events sync...\n")
    if auto_create_tickets:
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
                    ):
                        results["updated"].append(event.name)
                    else:
                        results["failed"].append(event.name)
                    time.sleep(1)
                else:
                    logger.info(
                        "⏭️  Skipped: %s on %s (no changes)",
                        event.name,
                        event.start_date,
                    )
                    results["skipped"].append(event.name)
                continue

            if create_wix_event(event, runtime=runtime, auto_create_tickets=auto_create_tickets):
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


