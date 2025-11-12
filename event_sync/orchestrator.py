"""High-level orchestration helpers for syncing events."""

from __future__ import annotations

import json
import time
from datetime import datetime
from html import escape
from typing import Any, Dict, List, Optional, Set

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


def get_existing_event_keys(runtime: SyncRuntime) -> Set[str]:
    logger.info("🔍 Checking for existing events in Wix...")
    try:
        client = runtime.get_wix_client()
        existing_keys: Set[str] = set()
        total_events = 0
        for event in client.iter_events(page_size=200):
            title = event.get("title", "")
            start_datetime = event.get("dateAndTimeSettings", {}).get("startDate", "")
            if start_datetime:
                date_part = start_datetime.split("T")[0]
                time_part = start_datetime.split("T")[1][:5] if "T" in start_datetime else "00:00"
                existing_keys.add(f"{title}|{date_part}|{time_part}")
            total_events += 1

        logger.info("Found %d existing events (from %d Wix records)\n", len(existing_keys), total_events)
        return existing_keys
    except Exception as exc:
        logger.warning("Warning: Could not fetch existing events: %s", exc)
        return set()


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

    start_date_iso = convert_date_to_iso(event.start_date)
    end_date_iso = convert_date_to_iso(event.end_date)

    event_data: Dict[str, Any] = {
        "title": event.name,
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

    teaser = event.teaser.strip() if event.teaser else None
    if teaser:
        event_data["shortDescription"] = teaser

    if event.description:
        formatted_description = format_description_as_html(event.description)
        if formatted_description:
            event_data["detailedDescription"] = formatted_description

    if file_descriptor and "id" in file_descriptor:
        width = height = None
        if "media" in file_descriptor and "image" in file_descriptor["media"]:
            image_data = file_descriptor["media"]["image"].get("image", {})
            width = image_data.get("width")
            height = image_data.get("height")

        if width and height:
            event_data["mainImage"] = {
                "id": file_descriptor["id"],
                "width": width,
                "height": height,
            }

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


def sync_events(runtime: SyncRuntime, auto_create_tickets: bool = True) -> bool:
    logger.info("🚀 Starting Google Sheets → Wix Events sync...\n")
    if auto_create_tickets:
        logger.info("🎫 Auto-ticket creation: ENABLED")
    else:
        logger.info("🎫 Auto-ticket creation: DISABLED")
    logger.info("")

    try:
        events = fetch_events(runtime)
        existing_keys = get_existing_event_keys(runtime)

        results = {"success": [], "failed": [], "skipped": []}

        logger.info("📅 Creating new events in Wix...\n")

        for event in events:
            start_date_iso = convert_date_to_iso(event.start_date)
            event_key = f"{event.name}|{start_date_iso}|{event.start_time}"

            if event_key in existing_keys:
                logger.info(
                    "⏭️  Skipped: %s on %s (already exists)",
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

        if results["skipped"]:
            logger.info(
                "\n⏭️  Skipped (already exist): %d events",
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


