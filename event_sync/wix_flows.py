"""Wix mutation flows shared by the sync pipeline: create/update events,
ticket definitions, categories, and the tax-by-location push.

Pure conversion helpers live in ``wix_mapping``; this module owns everything
that calls the Wix API to change state (plus the read-side event index and
credential checks the CLI exposes).
"""

from __future__ import annotations

import json
import time
from typing import Any, Dict, List, Optional, Tuple

from .config import AppConfig
from .images import upload_image_to_wix
from .logging_utils import get_logger
from .models import EventRecord, parse_tickets
from .runtime import SyncRuntime
from .wix_mapping import (
    build_wix_event_payload,
    diff_event_fields,
    log_event_diff,
    rates_equal,
    wix_event_match_key,
)


logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Credential / connectivity checks (CLI: validate, test, list)
# ---------------------------------------------------------------------------


def validate_credentials(config: AppConfig) -> bool:
    """Validate the settings the Notion pipeline actually requires.

    Hard requirements: Wix key + site id, Notion token + the four database
    ids. GOOGLE_CREDENTIALS is only needed to download Drive-hosted event
    images, so a missing value is a warning, not a failure.
    """

    logger.info("🔍 Validating credentials and configuration...\n")

    all_valid = True

    logger.info("Wix:")
    for name, value in (
        ("WIX_API_KEY", config.wix_api_key),
        ("WIX_SITE_ID", config.wix_site_id),
    ):
        if value:
            logger.info("✅ %s is set", name)
        else:
            logger.error("❌ %s is missing", name)
            all_valid = False
    if config.wix_account_id:
        logger.info("✅ WIX_ACCOUNT_ID is set")
    else:
        logger.info("ℹ️  WIX_ACCOUNT_ID not set (optional; needed for Site Media)")

    logger.info("")
    logger.info("Notion:")
    if config.notion_token:
        logger.info("✅ NOTION_ACCESS_TOKEN is set")
    else:
        logger.error("❌ NOTION_ACCESS_TOKEN is missing")
        all_valid = False

    notion_dbs = {
        "NOTION_EVENT_SCHEDULING_DB_ID": config.notion_event_scheduling_db_id,
        "NOTION_CATALOG_DB_ID": config.notion_catalog_db_id,
        "NOTION_SETTINGS_DB_ID": config.notion_settings_db_id,
        "NOTION_SITE_CONFIG_DB_ID": config.notion_site_config_db_id,
    }
    for name, value in notion_dbs.items():
        if value:
            logger.info("✅ %s is set", name)
        else:
            logger.error("❌ %s is missing (run setup-notion to create + print it)", name)
            all_valid = False

    logger.info("")
    logger.info("Google (Drive images):")
    if config.google_credentials_raw:
        creds = config.google_credentials
        if creds and "client_email" in creds:
            logger.info("✅ GOOGLE_CREDENTIALS is valid JSON")
            logger.info("   Service account: %s", creds["client_email"])
        else:
            logger.error("❌ GOOGLE_CREDENTIALS is invalid (missing client_email)")
            all_valid = False
    else:
        logger.warning(
            "⚠️  GOOGLE_CREDENTIALS not set — Drive-hosted event images "
            "cannot be downloaded"
        )

    logger.info("")
    if all_valid:
        logger.info("✅ All credentials are configured correctly!\n")
        logger.info("Next steps:")
        logger.info("  1. Run: python sync_events.py test")
        logger.info("  2. Run: python sync_events.py sync --dry-run")
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


# ---------------------------------------------------------------------------
# Live-event index (id-first matching, title|date|time fallback)
# ---------------------------------------------------------------------------


def index_events_by_id_and_key(
    runtime: SyncRuntime,
    fieldsets: Optional[List[str]] = None,
) -> tuple:
    """Return ``(by_id, by_key)`` dicts of live Wix events.

    ``by_id`` maps Wix event id → event payload (with categories).
    ``by_key`` maps ``wix_mapping.event_match_key`` output → event payload.
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

        key = wix_event_match_key(event, tz_name)
        if key is not None:
            by_key[key] = event

    return by_id, by_key


# ---------------------------------------------------------------------------
# Categories
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# Ticket definitions
# ---------------------------------------------------------------------------


def ensure_ticket_definition(
    client,
    event_id: str,
    event: EventRecord,
    existing_defs: Optional[List[Dict[str, Any]]] = None,
) -> bool:
    """Create a ticket definition if one doesn't already exist. Returns True on success.

    ``existing_defs`` lets callers that already fetched the event's ticket
    definitions (e.g. via an update plan) skip the re-query.
    """
    existing = (
        existing_defs if existing_defs is not None
        else client.get_ticket_definitions(event_id)
    )
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


def create_tickets_from_config(
    client,
    event_id: str,
    event: EventRecord,
    existing_defs: Optional[List[Dict[str, Any]]] = None,
) -> bool:
    """Create ticket definitions from the multi-ticket fields.

    Skips creation if the event already has ticket definitions to avoid
    duplicates that could confuse customers. ``existing_defs`` lets callers
    that already fetched them skip the re-query.
    """
    from .constants import DEFAULT_FEE_TYPE

    if existing_defs is None:
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


def ensure_event_tickets(
    client,
    event_id: str,
    record: EventRecord,
    existing_defs: Optional[List[Dict[str, Any]]] = None,
) -> bool:
    """Create whatever tickets the record calls for.

    Named multi-ticket specs win over the single-price definition; a record
    with neither is a no-op. Call-site guards (TICKETING registration,
    auto-create flags, draft status) stay with the callers.
    """
    if record.ticket_name:
        return create_tickets_from_config(
            client, event_id, record, existing_defs=existing_defs
        )
    if record.ticket_price > 0:
        return ensure_ticket_definition(
            client, event_id, record, existing_defs=existing_defs
        )
    return True


def _repair_missing_tickets(
    client,
    event_id: str,
    event: EventRecord,
    existing_defs: Optional[List[Dict[str, Any]]] = None,
) -> None:
    """Check for missing ticket definitions and create one if needed."""
    if event.registration_type != "TICKETING" or event.ticket_price <= 0:
        return

    if existing_defs is None:
        existing_defs = client.get_ticket_definitions(event_id)
    if existing_defs:
        return

    logger.info("   🔧 No ticket definitions found — repairing...")
    ensure_ticket_definition(client, event_id, event, existing_defs=[])


# ---------------------------------------------------------------------------
# Create / update events
# ---------------------------------------------------------------------------


def create_wix_event(
    event: EventRecord,
    runtime: SyncRuntime,
    auto_create_tickets: bool = True,
    draft: bool = False,
) -> Optional[str]:
    """Create a Wix event. Returns the new event id on success, None on failure."""
    runtime.last_image_failure = None
    file_descriptor = None
    if event.image_url:
        file_descriptor = upload_image_to_wix(event.image_url, event.name, runtime)
        if file_descriptor:
            logger.info("   ✅ Image uploaded successfully")
        else:
            logger.warning("   ⚠️  Proceeding without image")
            runtime.last_image_failure = event.image_url

    event_data = build_wix_event_payload(
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
            logger.info("   ℹ️  Tickets deferred until the draft is published")
        elif event.registration_type == "TICKETING" and auto_create_tickets:
            # A just-created event has no ticket definitions — skip the check.
            ensure_event_tickets(client, event_id, event, existing_defs=[])
        elif event.registration_type == "TICKETING" and not auto_create_tickets:
            logger.info("   ℹ️  Ticket creation skipped (--no-tickets flag set)")

        _assign_categories(client, event_id, event)

        return event_id
    except Exception as exc:
        logger.error("❌ Failed to create event %s: %s", event.name, exc)
        return None


def update_wix_event(
    event: EventRecord,
    runtime: SyncRuntime,
    existing_event_id: str,
    existing_event: Dict[str, Any],
    auto_create_tickets: bool = True,
    existing_ticket_defs: Optional[List[Dict[str, Any]]] = None,
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

    event_data = build_wix_event_payload(
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
            _repair_missing_tickets(
                client, existing_event_id, event,
                existing_defs=existing_ticket_defs,
            )

        return True
    except Exception as exc:
        logger.error("❌ Failed to update event %s: %s", event.name, exc)
        return False


# ---------------------------------------------------------------------------
# Update plans (diff a record against a live event, then apply)
# ---------------------------------------------------------------------------


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
    consumed by :func:`apply_event_update_plan`.
    """
    event_diffs = diff_event_fields(event, wix_event, runtime)
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
        rate_matches = rates_equal(desired_tax_rate, wix_rate)
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
        "wix_ticket_defs": wix_ticket_defs,
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
        log_event_diff(event_name, plan["event_diffs"])
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
        if not update_wix_event(
            event, runtime=runtime, existing_event_id=event_id,
            existing_event=wix_event,
            existing_ticket_defs=plan.get("wix_ticket_defs"),
        ):
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


# ---------------------------------------------------------------------------
# Site config (tax-by-location) push core
# ---------------------------------------------------------------------------


def process_site_config_rows(
    runtime: SyncRuntime,
    rows: List[Dict[str, Any]],
    dry_run: bool = False,
) -> bool:
    """Apply tax-location rows to Wix."""
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
                rate_changed = not rates_equal(live.get("taxRate", ""), desired_decimal)
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
        logger.exception("Fatal error during site config push: %s", exc)
        return False
