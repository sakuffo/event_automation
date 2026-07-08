"""Notion-backed orchestration: enrich, sync, pull, and site-config flows.

This module composes the Notion store (``notion_store``) with the existing Wix
call paths in ``orchestrator``. The Google-Sheets flows in ``orchestrator`` /
``generator`` are untouched and remain available under their ``*-sheet``
CLI aliases until the Notion path is proven out.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Set

from .constants import (
    CATEGORY_PRICING,
    DEFAULT_CAPACITY,
    DEFAULT_FEE_TYPE,
    DEFAULT_LOCATION,
    DEFAULT_TAX_NAME,
    DEFAULT_TAX_RATE,
    DEFAULT_TAX_TYPE,
)
from .logging_utils import get_logger
from .models import EventRecord, ValidationError
from .notion_store import (
    EventProps,
    NotionStore,
    NotionStoreError,
    SOURCE_WIX,
    STATUS_CANCEL,
    STATUS_CANCELLED,
    STATUS_DELETE,
    STATUS_DRAFT,
    STATUS_ERROR,
    STATUS_IDEA,
    STATUS_PUBLISHED,
    STATUS_READY,
    STATUS_REMOVED,
    STATUS_UPDATE,
    TEMPLATE_TYPE_CLASS,
    TEMPLATE_TYPE_EVENT,
    event_page_to_row,
    p_date,
    p_multi_select,
    p_number,
    p_rich_text,
    p_select,
    p_title,
    p_url,
    parse_validation_error,
    row_to_event_record,
)
from .wix_flows import (
    apply_event_update_plan,
    compute_event_update_plan,
    create_tickets_from_config,
    create_wix_event,
    ensure_ticket_definition,
    index_events_by_id_and_key,
    log_update_plan_dry_run,
    process_site_config_rows,
)
from .wix_mapping import (
    blank_region_site_row,
    event_match_key,
    log_event_diff,
    parse_month_value,
    select_default_tax_group_id,
    site_config_row_sort_key,
    tax_mapping_to_site_row,
    wix_event_to_config_row,
)
from .runtime import SyncRuntime


logger = get_logger(__name__)

_UPCOMING_STATUSES = frozenset({"UPCOMING", "STARTED"})


# Settings-DB keys for pipeline defaults, seeded by setup-notion and consulted
# by the default-fill helper (constants remain the fallback).
DEFAULT_SETTINGS_SEED: List[tuple] = [
    ("default_location", DEFAULT_LOCATION, "Venue used when an event row has no Location"),
    ("default_capacity", str(DEFAULT_CAPACITY), "Capacity used when an event row has no Capacity"),
    ("default_registration_type", "TICKETS", "Registration Type for new event rows (TICKETS / RSVP / EXTERNAL / NO_REGISTRATION)"),
    ("default_tax_name", DEFAULT_TAX_NAME, "Ticket tax name for TICKETS events (e.g. HST)"),
    ("default_tax_rate", DEFAULT_TAX_RATE, "Ticket tax rate as a percent (13 = 13%)"),
    ("default_tax_type", DEFAULT_TAX_TYPE, "ADDED_AT_CHECKOUT or INCLUDED_IN_PRICE"),
    ("default_fee_type", DEFAULT_FEE_TYPE, "Wix service fee handling for tickets (FEE_ADDED_AT_CHECKOUT or NO_FEE)"),
    ("default_duration_hours", "2", "End time = start + this many hours when a row has no end time"),
]


def seed_default_settings(store: NotionStore) -> int:
    """Add any missing default_* rows to the Settings DB. Returns count added.

    Existing rows are never overwritten — the Settings DB is the editable
    source of truth once seeded.
    """
    existing = store.fetch_settings()
    added = 0
    for key, value, notes in DEFAULT_SETTINGS_SEED:
        if key in existing:
            continue
        # fetch_settings above confirmed the key is absent — skip the scan.
        store.upsert_setting(key, value, notes=notes, existing_page_id=None)
        logger.info("  ➕ Setting '%s' = %s", key, value)
        added += 1
    return added


# ---------------------------------------------------------------------------
# setup-notion
# ---------------------------------------------------------------------------


def setup_notion(runtime: SyncRuntime) -> bool:
    """Create the Notion databases and print the env vars to save."""
    config = runtime.config
    store: NotionStore = runtime.get_notion_store()

    parent_page_id = config.notion_parent_page_id
    if not parent_page_id:
        logger.error("NOTION_PARENT_PAGE_ID is missing.")
        pages = store.search_accessible_pages()
        if pages:
            logger.info("\nPages this integration token can access:")
            for page in pages:
                title = ""
                for prop in (page.get("properties") or {}).values():
                    if prop.get("type") == "title":
                        title = "".join(
                            t.get("plain_text", "") for t in prop.get("title") or []
                        )
                        break
                logger.info("  • %s  (%s)", title or "(untitled)", page.get("id", ""))
            logger.info(
                "\nSet NOTION_PARENT_PAGE_ID in .env to the page that should hold "
                "the event databases, then re-run setup-notion."
            )
        else:
            logger.info(
                "The integration token cannot see any pages. In Notion, share the "
                "target page with the integration (Connections menu), then re-run."
            )
        return False

    logger.info("🏗️  Creating Notion databases under page %s...\n", parent_page_id)
    try:
        results = store.setup_databases(parent_page_id)
    except Exception as exc:
        logger.error("❌ Failed to create databases: %s", exc)
        return False

    # Make the fresh ids usable this run (config may not have them yet).
    config.notion_catalog_db_id = results["NOTION_CATALOG_DB_ID"]
    config.notion_event_scheduling_db_id = results["NOTION_EVENT_SCHEDULING_DB_ID"]
    config.notion_settings_db_id = results["NOTION_SETTINGS_DB_ID"]
    config.notion_site_config_db_id = results["NOTION_SITE_CONFIG_DB_ID"]

    logger.info("\n⚙️  Seeding pipeline defaults into the Settings DB...")
    try:
        added = seed_default_settings(store)
        if added == 0:
            logger.info("  (all default_* settings already present)")
    except Exception as exc:
        logger.warning("  ⚠️  Could not seed default settings: %s", exc)

    try:
        added_options = store.ensure_event_status_options()
        if added_options:
            logger.info(
                "⚙️  Added %d new Status option(s) to the Events DB schema",
                added_options,
            )
    except Exception as exc:
        logger.warning("  ⚠️  Could not update Events Status options: %s", exc)

    try:
        added_props = store.ensure_catalog_properties()
        if added_props:
            logger.info(
                "⚙️  Added Catalog propert%s: %s",
                "y" if len(added_props) == 1 else "ies",
                ", ".join(added_props),
            )
    except Exception as exc:
        logger.warning("  ⚠️  Could not patch Catalog properties: %s", exc)

    try:
        added_types = store.ensure_template_type_options()
        if added_types:
            logger.info(
                "⚙️  Added %d Type option(s) to the Catalog DB schema "
                "(class/event templates)",
                added_types,
            )
    except Exception as exc:
        logger.warning("  ⚠️  Could not update Catalog Type options: %s", exc)

    try:
        renames = store.migrate_naming()
        for change in renames:
            logger.info("⚙️  Renamed: %s", change)
    except Exception as exc:
        logger.warning("  ⚠️  Could not migrate database naming: %s", exc)

    logger.info("\n✅ Databases ready. Add these to your .env (and GitHub secrets):\n")
    for env_name, db_id in results.items():
        logger.info("%s=%s", env_name, db_id)

    logger.info(
        "\nRecommended Notion views to add by hand (the API cannot create views):"
        "\n  • Events: Calendar view on the Date property"
        "\n  • Events: Board view grouped by Status"
        "\n  • Events: Table view 'Needs attention' filtered to Sync Error is not empty"
        "\n  • Events: Table view 'This month' filtered on Date"
    )
    return True


# ---------------------------------------------------------------------------
# category slugs
# ---------------------------------------------------------------------------


def _slugify_category(raw: str) -> str:
    return "-".join(raw.strip().lower().split())


# ---------------------------------------------------------------------------
# import-event-templates (recurring-events CSV -> catalog `event` rows)
# ---------------------------------------------------------------------------


# Per-family source-selection overrides. Tinker Tuesday instances alternate
# between a $25 base price (HST added at checkout — the correct baseline) and
# $28.25 (HST baked into the price), plus one "Tinker SUNDAY" special that
# shouldn't seed the template.
TEMPLATE_SOURCE_RULES: Dict[str, Dict[str, Any]] = {
    "tinker tuesday": {
        "exclude_title_substrings": ("sunday",),
        "require_base_price": 25.0,
    },
}


def _parse_price(text: Any) -> Optional[float]:
    value = str(text or "").strip()
    if not value:
        return None
    try:
        return float(value)
    except ValueError:
        return None


def select_template_sources(
    rows: List[Dict[str, Any]],
) -> Dict[str, Dict[str, Any]]:
    """Pick the baseline source row for each recurring-event family.

    Eligible rows carry a ``default_event`` family and ``include_in_feed``
    TRUE (canceled instances are flagged FALSE in the export). The latest
    instance by ``start_local_date`` wins. ``TEMPLATE_SOURCE_RULES`` adds
    per-family filters (Tinker Tuesday: skip Sunday specials and require the
    $25 base price).
    """
    best: Dict[str, tuple] = {}
    for row in rows:
        family = (row.get("default_event") or "").strip()
        if not family:
            continue
        if (row.get("include_in_feed") or "").strip().upper() != "TRUE":
            continue

        rules = TEMPLATE_SOURCE_RULES.get(family.lower(), {})
        title_lower = (row.get("title") or "").lower()
        if any(
            fragment in title_lower
            for fragment in rules.get("exclude_title_substrings", ())
        ):
            continue
        required_price = rules.get("require_base_price")
        if (
            required_price is not None
            and _parse_price(row.get("lowest_ticket_price")) != required_price
        ):
            continue

        sort_key = (row.get("start_local_date") or "", row.get("start_utc") or "")
        current = best.get(family)
        if current is None or sort_key > current[0]:
            best[family] = (sort_key, row)

    return {family: row for family, (_, row) in best.items()}


def import_event_templates(
    runtime: SyncRuntime,
    csv_path: str = "wix_events_export_de.csv",
    *,
    dry_run: bool = False,
    force: bool = False,
) -> bool:
    """Seed the catalog with ``event``-type templates from the events export.

    For each recurring-event family tagged in the export (``default_event``
    column), the latest posted instance becomes the template baseline: name,
    categories, teaser, description, image, and base price. Families already
    in the catalog are skipped unless ``force`` is set.
    """
    import csv as csv_module
    from pathlib import Path

    store: NotionStore = runtime.get_notion_store()

    path = Path(csv_path)
    if not path.exists():
        logger.error("❌ CSV not found: %s", path)
        return False

    with path.open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv_module.DictReader(handle))

    sources = select_template_sources(rows)
    if not sources:
        logger.warning(
            "No recurring-event families found in %s (missing default_event "
            "column or no feed-eligible rows).",
            path,
        )
        return False

    logger.info(
        "🎪 Importing %d event template(s) from %s into the catalog...\n",
        len(sources),
        path,
    )

    existing = store.fetch_classes()

    created = 0
    updated = 0
    skipped = 0
    failed = 0
    for family in sorted(sources):
        source = sources[family]
        price = _parse_price(source.get("lowest_ticket_price"))
        label = (
            f"{family}  ⟵  {source.get('start_local_date', '?')} "
            f"“{(source.get('title') or '').strip()}”"
            + (f" (${price:g})" if price is not None else "")
        )

        existing_entry = existing.get(family.strip().lower())
        if existing_entry and not force:
            skipped += 1
            logger.info(
                "  ⏭️  %s — already in the catalog as type '%s' "
                "(re-run with --force to overwrite)",
                family,
                existing_entry.get("type") or TEMPLATE_TYPE_CLASS,
            )
            continue

        if dry_run:
            logger.info(
                "  🔎 Would %s: %s",
                "update" if existing_entry else "create",
                label,
            )
            continue

        categories = [
            _slugify_category(c)
            for c in (source.get("categories") or "").split(";")
            if c.strip()
        ]
        try:
            store.upsert_class(
                name=family,
                categories=categories,
                tagline=(source.get("short_description") or "").strip(),
                description=(source.get("description") or "").strip(),
                image_url=(source.get("main_image_url") or "").strip(),
                template_type=TEMPLATE_TYPE_EVENT,
                price_override=price,
                default_start_time=(source.get("start_local_time") or "").strip(),
                default_end_time=(source.get("end_local_time") or "").strip(),
                existing_page_id=(existing_entry or {}).get("page_id"),
            )
            if existing_entry:
                updated += 1
                logger.info("  ♻️  Updated event template: %s", label)
            else:
                created += 1
                logger.info("  ➕ Created event template: %s", label)
        except Exception as exc:
            failed += 1
            logger.warning("  ⚠️  Failed to import '%s': %s", family, exc)

    logger.info(
        "\n📊 Event templates: %d created, %d updated, %d skipped, %d failed%s",
        created,
        updated,
        skipped,
        failed,
        " (dry run)" if dry_run else "",
    )
    return failed == 0


# ---------------------------------------------------------------------------
# pull (Wix -> Events DB)
# ---------------------------------------------------------------------------


def pull_events(runtime: SyncRuntime, scope: str = "upcoming") -> bool:
    """Backfill/refresh the Notion Events DB from live Wix events.

    Creates rows (with ``Wix Event ID`` + ``Synced Hash``) for Wix events
    missing from Notion — status ``Published``, or ``Cancelled`` for Wix
    events with status CANCELED — and refreshes rows already in one of those
    code-owned statuses. Rows in any other status (Idea/Draft/Ready/Cancel/
    Delete/Error/Skip) are never overwritten — those belong to humans or to
    the sync flow.
    """
    if scope not in {"upcoming", "all"}:
        logger.error("Invalid scope '%s' (expected 'upcoming' or 'all')", scope)
        return False

    logger.info("⬇️  Pulling Wix events into Notion (scope=%s)...\n", scope)

    try:
        store: NotionStore = runtime.get_notion_store()
        client = runtime.get_wix_client()
        tz_name = runtime.config.timezone

        wix_events = list(client.iter_events(
            page_size=100,
            include_drafts=False,
            fieldsets=["DETAILS", "REGISTRATION", "CATEGORIES"],
        ))
        if scope == "upcoming":
            wix_events = [
                e for e in wix_events if (e.get("status") or "") in _UPCOMING_STATUSES
            ]

        if not wix_events:
            logger.warning("No Wix events found for scope=%s.", scope)
            return False

        logger.info("Found %d Wix event(s) to pull\n", len(wix_events))

        # Index existing Notion rows by wix id and by (title|date|time).
        notion_rows = store.fetch_event_rows()
        by_wix_id: Dict[str, Dict[str, Any]] = {}
        by_key: Dict[str, Dict[str, Any]] = {}
        for row in notion_rows:
            if row.get("wix_event_id"):
                by_wix_id[row["wix_event_id"]] = row
            if row.get("event_name") and row.get("start_date") and row.get("start_time"):
                key = event_match_key(
                    row["event_name"], row["start_date"], row["start_time"]
                )
                by_key.setdefault(key, row)

        results = {"created": [], "refreshed": [], "linked": [], "skipped": [], "failed": []}

        # Rows in these statuses are code-owned and safe to refresh from Wix.
        refreshable_statuses = {STATUS_PUBLISHED, STATUS_CANCELLED}

        for i, wix_event in enumerate(wix_events, 1):
            title = wix_event.get("title", "Untitled")
            wix_id = wix_event.get("id", "")
            logger.info("  %d/%d  %s", i, len(wix_events), title)

            # Match by Wix id before any per-event Wix calls — id-matched
            # rows in human statuses skip the ticket-definitions fetch
            # entirely.
            existing = by_wix_id.get(wix_id)
            matched_by_key = False
            record_built = False
            record: Optional[EventRecord] = None
            config_row: Dict[str, Any] = {}
            target_status = STATUS_PUBLISHED
            invalid_note = ""

            if existing is None:
                # Key matching needs the localized date/time from the config
                # row, so the record is built here (one ticket-defs call).
                record, config_row, target_status, invalid_note = (
                    _wix_event_to_record(client, wix_event, tz_name)
                )
                record_built = True
                if record is not None:
                    match_key = event_match_key(
                        record.name, record.start_date, record.start_time
                    )
                else:
                    match_key = event_match_key(
                        config_row.get("event_name") or "",
                        config_row.get("start_date", ""),
                        config_row.get("start_time", ""),
                    )
                existing = by_key.get(match_key)
                matched_by_key = existing is not None

            try:
                if existing is None:
                    if record is not None:
                        store.upsert_event_from_record(
                            record, status=target_status, source=SOURCE_WIX,
                        )
                    else:
                        store.upsert_event_from_raw_row(
                            config_row,
                            status=target_status,
                            source=SOURCE_WIX,
                            wix_event_id=wix_id,
                            error=invalid_note,
                        )
                        logger.info("   ⚠️  %s", invalid_note)
                    results["created"].append(title)
                    logger.info("   ➕ Created Notion row (%s)", target_status)
                    continue

                row_status = existing.get("status") or ""
                if matched_by_key and row_status and row_status not in refreshable_statuses:
                    # Human-owned row that matches a live Wix event: link it
                    # (write the Wix id + hash) but leave their fields alone.
                    store.write_sync_result(
                        existing["page_id"],
                        wix_event_id=wix_id,
                        synced_hash=existing.get("synced_hash") or "",
                        error=None,
                    )
                    results["linked"].append(title)
                    logger.info("   🔗 Linked existing %s row (left fields untouched)", row_status)
                    continue

                if row_status and row_status not in refreshable_statuses:
                    results["skipped"].append(title)
                    logger.info("   ⏭️  Skipped (row status is %s)", row_status)
                    continue

                # Refresh path: build the record now if the id match let
                # us defer it.
                if not record_built:
                    record, config_row, target_status, invalid_note = (
                        _wix_event_to_record(client, wix_event, tz_name)
                    )

                # Refreshing a code-owned row: don't let an imageless Wix
                # event wipe a human-entered image link.
                _apply_image_preservation(record, config_row, existing)

                # Hash short-circuit mirroring the sync Published refresh —
                # an unchanged event costs no Notion write.
                if record is not None:
                    row_hash: Optional[str] = None
                    try:
                        row_hash = row_to_event_record(existing).content_hash()
                    except ValidationError:
                        row_hash = None
                    if row_hash == record.synced_hash and row_status == target_status:
                        stale_bookkeeping = (
                            (existing.get("synced_hash") or "") != record.synced_hash
                            or (existing.get("wix_event_id") or "").strip() != wix_id
                            or bool((existing.get("sync_error") or "").strip())
                        )
                        if stale_bookkeeping:
                            store.write_sync_result(
                                existing["page_id"], wix_event_id=wix_id,
                                synced_hash=record.synced_hash, error=None,
                            )
                        results["skipped"].append(title)
                        logger.info("   ⏭️  Unchanged (matches Wix)")
                        continue

                if record is not None:
                    store.upsert_event_from_record(
                        record,
                        status=target_status,
                        source=SOURCE_WIX,
                        page_id=existing["page_id"],
                    )
                else:
                    store.upsert_event_from_raw_row(
                        config_row,
                        status=target_status,
                        source=SOURCE_WIX,
                        wix_event_id=wix_id,
                        error=invalid_note,
                        page_id=existing["page_id"],
                    )
                    logger.info("   ⚠️  %s", invalid_note)
                results["refreshed"].append(title)
                logger.info("   ♻️  Refreshed Notion row (%s)", target_status)
            except Exception as exc:
                logger.warning("   ⚠️  Failed to write '%s' to Notion: %s", title, exc)
                results["failed"].append(title)

        logger.info("\n📈 Pull complete!")
        logger.info(
            "   ➕ %d created, ♻️ %d refreshed, 🔗 %d linked, ⏭️ %d skipped, ❌ %d failed",
            len(results["created"]),
            len(results["refreshed"]),
            len(results["linked"]),
            len(results["skipped"]),
            len(results["failed"]),
        )
        return len(results["failed"]) == 0
    except Exception as exc:
        logger.exception("Fatal error during pull: %s", exc)
        return False


# ---------------------------------------------------------------------------
# enrich (fill blanks on Idea/Draft rows from Classes + Settings + pricing)
# ---------------------------------------------------------------------------


_BASELINE_TAGS = ["rope", "class"]


def _default_duration_hours(settings: Dict[str, str]) -> float:
    """Event length assumed when a row has a start but no usable end."""
    try:
        hours = float((settings.get("default_duration_hours") or "").strip())
        return hours if 0 < hours <= 24 else 2.0
    except (TypeError, ValueError):
        return 2.0


def _normalize_hhmm(text: str) -> str:
    """Normalize a time string to zero-padded HH:MM (``"2:30"`` -> ``"02:30"``).

    Returns ``""`` for anything unparseable so callers can treat it as unset.
    """
    parts = (text or "").strip().split(":")
    if len(parts) != 2:
        return ""
    try:
        hour, minute = int(parts[0]), int(parts[1])
    except ValueError:
        return ""
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return ""
    return f"{hour:02d}:{minute:02d}"


def _lookup_price_for_category(category: str) -> Optional[float]:
    """Price for a category tag, tolerating slugged forms (case-insensitive)."""
    if not category:
        return None
    candidates = {category.strip().lower(), category.strip().lower().replace("-", " ")}
    for known, price in CATEGORY_PRICING.items():
        if known.lower() in candidates:
            return float(price)
    return None


def _month_numbers(month_filters: Optional[List[str]]) -> Optional[Set[int]]:
    if not month_filters:
        return None
    return {parse_month_value(m) for m in month_filters}


def _row_in_months(row: Dict[str, Any], allowed: Optional[Set[int]]) -> bool:
    if allowed is None:
        return True
    start_date = (row.get("start_date") or "").strip()
    if not start_date:
        return True  # keep undated rows visible to enrich so they get error notes
    try:
        month = int(start_date.split("-")[1])
    except (IndexError, ValueError):
        return True
    return month in allowed


def _resolve_class_for_row(
    row: Dict[str, Any],
    classes: Dict[str, Dict[str, Any]],
    classes_by_page_id: Dict[str, Dict[str, Any]],
) -> Optional[Dict[str, Any]]:
    """Resolve a row's class: the Class relation first, then exact title match."""
    for rel_id in row.get("template_relation_ids") or []:
        if rel_id in classes_by_page_id:
            return classes_by_page_id[rel_id]
    return classes.get((row.get("event_name") or "").strip().lower())


@dataclass
class _Defaults:
    """Settings-DB defaults with constants fallbacks, parsed once per fill."""

    image: str
    location: str
    registration_type: str
    tax_name: str
    tax_rate: str
    tax_type: str
    fee_type: str
    capacity: int

    @classmethod
    def from_settings(cls, settings: Dict[str, str]) -> "_Defaults":
        try:
            capacity = int(float(settings.get("default_capacity", "")))
        except (TypeError, ValueError):
            capacity = DEFAULT_CAPACITY
        tax_rate = (settings.get("default_tax_rate") or "").strip() or DEFAULT_TAX_RATE
        try:
            float(tax_rate)
        except ValueError:
            tax_rate = DEFAULT_TAX_RATE
        return cls(
            image=(settings.get("default_img") or "").strip(),
            location=(settings.get("default_location") or "").strip()
            or DEFAULT_LOCATION,
            registration_type=(
                (settings.get("default_registration_type") or "").strip().upper()
                or "TICKETS"
            ),
            tax_name=(settings.get("default_tax_name") or "").strip()
            or DEFAULT_TAX_NAME,
            tax_rate=tax_rate,
            tax_type=(settings.get("default_tax_type") or "").strip()
            or DEFAULT_TAX_TYPE,
            fee_type=(settings.get("default_fee_type") or "").strip()
            or DEFAULT_FEE_TYPE,
            capacity=capacity,
        )


def _is_class_template(klass: Optional[Dict[str, Any]]) -> bool:
    """Blank Type reads as ``class`` so pre-redesign catalog rows keep behaving."""
    return (
        klass is not None
        and (klass.get("type") or TEMPLATE_TYPE_CLASS) == TEMPLATE_TYPE_CLASS
    )


def _fill_schedule(
    row: Dict[str, Any],
    klass: Optional[Dict[str, Any]],
    settings: Dict[str, str],
    tz_name: str,
) -> tuple:
    """Template default times fill blank time parts; a start without a usable
    end gets the default duration; an end at/before the start on the same day
    is read as overnight and rolls to the next day. Everything is written back
    so Notion shows the schedule that will be pushed. Times someone picked
    stay as-is (a zero-duration end counts as unset — Wix rejects those)."""
    props: Dict[str, Any] = {}
    changes: List[str] = []

    start_date_iso = (row.get("start_date") or "").strip()
    if not start_date_iso:
        return props, changes

    tpl_start = _normalize_hhmm((klass or {}).get("default_start_time") or "")
    tpl_end = _normalize_hhmm((klass or {}).get("default_end_time") or "")

    if not (row.get("start_time") or "").strip() and tpl_start:
        row["start_time"] = tpl_start
        changes.append(f"start time {tpl_start}")

    start_time = (row.get("start_time") or "").strip()
    end_time = (row.get("end_time") or "").strip()
    end_date_iso = (row.get("end_date") or "").strip() or start_date_iso

    if start_time:
        zero_duration = end_time == start_time and end_date_iso == start_date_iso
        if not end_time or zero_duration:
            new_end = tpl_end if tpl_end and tpl_end != start_time else ""
            if new_end:
                changes.append(f"end time {new_end}")
            else:
                hours = _default_duration_hours(settings)
                try:
                    end_dt = datetime.strptime(
                        start_time, "%H:%M"
                    ) + timedelta(hours=hours)
                    new_end = end_dt.strftime("%H:%M")
                    changes.append(
                        f"end time {new_end} (start + {hours:g}h)"
                    )
                except ValueError:
                    new_end = ""
            if new_end:
                row["end_time"] = new_end
                row["end_date"] = start_date_iso
                end_time = new_end
                end_date_iso = start_date_iso

        # Overnight: an end at/before the start on the same day means it
        # runs past midnight (Voyeur 21:00 -> 03:00).
        if end_time and end_date_iso == start_date_iso and end_time <= start_time:
            try:
                next_day = datetime.strptime(
                    start_date_iso, "%Y-%m-%d"
                ) + timedelta(days=1)
                row["end_date"] = next_day.strftime("%Y-%m-%d")
                changes.append("end rolls past midnight")
            except ValueError:
                pass

    if changes:
        props[EventProps.DATE] = p_date(
            start_date_iso,
            (row.get("start_time") or "").strip() or None,
            (row.get("end_date") or "").strip() or None,
            (row.get("end_time") or "").strip() or None,
            tz_name=tz_name,
        )
    return props, changes


def _fill_instructor(row: Dict[str, Any], klass: Optional[Dict[str, Any]]) -> tuple:
    """Template default instructor (before the description fill, which
    prepends "Instructors: ..." from this field)."""
    props: Dict[str, Any] = {}
    changes: List[str] = []
    if klass and not (row.get("instructor") or "").strip():
        tpl_instructor = (klass.get("default_instructor") or "").strip()
        if tpl_instructor:
            props[EventProps.INSTRUCTOR] = p_rich_text(tpl_instructor)
            changes.append("instructor")
            row["instructor"] = tpl_instructor
    return props, changes


def _fill_categories(row: Dict[str, Any], klass: Optional[Dict[str, Any]]) -> tuple:
    """Merge row + template categories. The rope/class baseline tags only
    apply to class templates — event templates (jams, parties, shows) carry
    exactly their own tags."""
    props: Dict[str, Any] = {}
    changes: List[str] = []
    if not klass:
        return props, changes

    row_cats = [
        c.strip() for c in (row.get("categories") or "").split(";") if c.strip()
    ]
    baseline = _BASELINE_TAGS if _is_class_template(klass) else []
    merged: List[str] = []
    seen: Set[str] = set()
    for raw in row_cats + (klass.get("categories") or []) + baseline:
        tag = _slugify_category(raw)
        if tag and tag not in seen:
            seen.add(tag)
            merged.append(tag)
    if merged != row_cats:
        props[EventProps.CATEGORIES] = p_multi_select(merged)
        changes.append("categories")
        row["categories"] = "; ".join(merged)
    return props, changes


def _fill_venue_and_registration(row: Dict[str, Any], defaults: _Defaults) -> tuple:
    props: Dict[str, Any] = {}
    changes: List[str] = []
    if not (row.get("location") or "").strip():
        props[EventProps.LOCATION] = p_rich_text(defaults.location)
        changes.append("location")
        row["location"] = defaults.location

    if not (row.get("registration_type") or "").strip():
        props[EventProps.REGISTRATION_TYPE] = p_select(defaults.registration_type)
        changes.append("registration type")
        row["registration_type"] = defaults.registration_type
    return props, changes


def _fill_capacity(
    row: Dict[str, Any], klass: Optional[Dict[str, Any]], defaults: _Defaults
) -> tuple:
    props: Dict[str, Any] = {}
    changes: List[str] = []
    if not (row.get("capacity") or "").strip():
        capacity = None
        if klass and klass.get("default_capacity"):
            capacity = int(klass["default_capacity"])
        props[EventProps.CAPACITY] = p_number(capacity or defaults.capacity)
        changes.append("capacity")
        row["capacity"] = str(capacity or defaults.capacity)
    return props, changes


def _fill_pricing(row: Dict[str, Any], klass: Optional[Dict[str, Any]]) -> tuple:
    """Price Override wins (a $0 override is honored — free events like Bound
    Together), then CATEGORY_PRICING by tag, then the $30 class floor."""
    props: Dict[str, Any] = {}
    changes: List[str] = []
    if (row.get("ticket_price") or "").strip():
        return props, changes

    price: Optional[float] = None
    if klass and klass.get("price_override") is not None:
        price = float(klass["price_override"])
    if price is None:
        row_cats = [
            c.strip() for c in (row.get("categories") or "").split(";") if c.strip()
        ]
        for tag in row_cats:
            if tag in _BASELINE_TAGS:
                continue
            price = _lookup_price_for_category(tag)
            if price is not None:
                break
    if price is None and _is_class_template(klass):
        price = 30.0  # class rows always get a price (default $30)
    if price is not None:
        props[EventProps.TICKET_PRICE] = p_number(price)
        changes.append(f"price ${price:g}")
        row["ticket_price"] = f"{price:g}"
    return props, changes


def _fill_descriptions(row: Dict[str, Any], klass: Optional[Dict[str, Any]]) -> tuple:
    props: Dict[str, Any] = {}
    changes: List[str] = []
    if not (row.get("short_description") or "").strip() and klass:
        tagline = (klass.get("tagline") or "").strip()
        if tagline:
            props[EventProps.TEASER] = p_rich_text(tagline)
            changes.append("teaser")
            row["short_description"] = tagline

    if not (row.get("detailed_description") or "").strip() and klass:
        description = (klass.get("description") or "").strip()
        instructor = (row.get("instructor") or "").strip()
        model = (row.get("model") or "").strip()
        team = " & ".join(p for p in [instructor, model] if p)
        if team and description:
            description = f"Instructors: {team}\n\n{description}"
        elif team:
            description = f"Instructors: {team}"
        if description:
            props[EventProps.DESCRIPTION] = p_rich_text(description)
            changes.append("description")
            row["detailed_description"] = description
    return props, changes


def _fill_image(
    row: Dict[str, Any], klass: Optional[Dict[str, Any]], defaults: _Defaults
) -> tuple:
    props: Dict[str, Any] = {}
    changes: List[str] = []
    if not (row.get("image_url") or "").strip():
        image = ""
        if klass:
            image = (klass.get("image_url") or "").strip()
        image = image or defaults.image
        if image:
            props[EventProps.IMAGE_URL] = p_url(image)
            changes.append("image")
            row["image_url"] = image
    return props, changes


def _fill_tax_and_fees(row: Dict[str, Any], defaults: _Defaults) -> tuple:
    props: Dict[str, Any] = {}
    changes: List[str] = []
    is_ticketed = (row.get("registration_type") or "").strip().upper() in {
        "TICKETS", "TICKETING",
    }
    if not is_ticketed:
        return props, changes

    if (
        not (row.get("tax_name") or "").strip()
        and not (row.get("tax_rate") or "").strip()
    ):
        props[EventProps.TAX_NAME] = p_rich_text(defaults.tax_name)
        props[EventProps.TAX_RATE] = p_number(float(defaults.tax_rate))
        props[EventProps.TAX_TYPE] = p_rich_text(defaults.tax_type)
        changes.append("tax")
        row["tax_name"] = defaults.tax_name
        row["tax_rate"] = defaults.tax_rate
        row["tax_type"] = defaults.tax_type

    if not (row.get("fee_type") or "").strip():
        props[EventProps.FEE_TYPE] = p_rich_text(defaults.fee_type)
        changes.append("fee type")
        row["fee_type"] = defaults.fee_type
    return props, changes


def _apply_row_defaults(
    row: Dict[str, Any],
    klass: Optional[Dict[str, Any]],
    settings: Dict[str, str],
    tz_name: str = "America/Toronto",
) -> tuple:
    """Fill blank fields on an Events row from its template and the Settings DB.

    Only empty fields are touched — anything a human typed stays as-is.
    Settings ``default_*`` rows win over the constants fallbacks. Mutates
    ``row`` in place and returns ``(props, changes)``: the Notion property
    payload to write and human-readable change labels. Rows without a name
    are ignored (returns empty).
    """
    props: Dict[str, Any] = {}
    changes: List[str] = []

    if not (row.get("event_name") or "").strip():
        return props, changes

    defaults = _Defaults.from_settings(settings)

    # Fill order matters: instructor before descriptions (which prepend the
    # "Instructors: ..." line), categories before pricing (tags drive the
    # CATEGORY_PRICING lookup), registration before tax/fees (only ticketed
    # rows get them).
    for frag_props, frag_changes in (
        _fill_schedule(row, klass, settings, tz_name),
        _fill_instructor(row, klass),
        _fill_categories(row, klass),
        _fill_venue_and_registration(row, defaults),
        _fill_capacity(row, klass, defaults),
        _fill_pricing(row, klass),
        _fill_descriptions(row, klass),
        _fill_image(row, klass, defaults),
        _fill_tax_and_fees(row, defaults),
    ):
        props.update(frag_props)
        changes.extend(frag_changes)

    return props, changes


def enrich_events(
    runtime: SyncRuntime,
    month_filters: Optional[List[str]] = None,
) -> bool:
    """Fill blanks on Idea/Draft rows from the Catalog and Settings.

    Only empty fields are filled — anything a human typed stays as-is. Rows
    with a linked Template but a blank Name get the template's name written
    in first, so picking a template from the catalog is enough to start a
    row. Rows that match a template get the merged category tags (plus the
    ``rope``/``class`` baseline for class templates), pricing from the
    template Price Override (or ``CATEGORY_PRICING``), and the template
    tagline/description/image. Idea rows that were successfully enriched are
    promoted to Draft.
    """
    logger.info("✨ Enriching Idea/Draft rows in Notion...\n")

    try:
        allowed_months = _month_numbers(month_filters)
    except ValueError as exc:
        logger.error("%s", exc)
        return False

    try:
        store: NotionStore = runtime.get_notion_store()
        rows = store.fetch_event_rows(
            statuses=[STATUS_IDEA, STATUS_DRAFT], include_missing_status=True
        )
        if not rows:
            logger.info("No Idea/Draft rows found — nothing to enrich.")
            return True

        classes = store.fetch_classes()
        classes_by_page_id = {
            c["page_id"]: c for c in classes.values() if c.get("page_id")
        }
        settings = store.fetch_settings()

        enriched = 0
        skipped = 0
        incomplete = 0
        write_failures = 0

        for row in rows:
            if not _row_in_months(row, allowed_months):
                continue

            klass = _resolve_class_for_row(row, classes, classes_by_page_id)

            # Bootstrap fills: a linked Template is enough — a blank Name
            # comes from the template, and a blank Status starts as Idea (so
            # the normal Idea→Draft promotion applies in the same pass).
            bootstrap_props: Dict[str, Any] = {}
            bootstrap_changes: List[str] = []
            if not (row.get("event_name") or "").strip():
                if klass is None:
                    logger.info(
                        "  ⏭️  Skipping unnamed row (add a Name or link a Template first)"
                    )
                    continue
                row["event_name"] = klass["class"]
                bootstrap_props[EventProps.NAME] = p_title(klass["class"])
                bootstrap_changes.append("name from template")

            if not (row.get("status") or "").strip():
                row["status"] = STATUS_IDEA
                bootstrap_props[EventProps.STATUS] = p_select(STATUS_IDEA)
                bootstrap_changes.append("status Idea")

            name = row["event_name"]
            page_id = row["page_id"]

            props, changes = _apply_row_defaults(
                row, klass, settings, tz_name=runtime.config.timezone
            )
            if bootstrap_props:
                # Merge first so the later Idea→Draft promotion can still
                # override the bootstrapped Status in the same update.
                props.update(bootstrap_props)
                changes[:0] = bootstrap_changes

            # Validate what the row would look like at sync time; surface
            # anything still missing in Sync Error so editors can see it.
            error_note: Optional[str] = None
            try:
                row_to_event_record(row)
            except ValidationError as exc:
                error_note = f"Not ready to sync: {parse_validation_error(exc)}"

            if error_note:
                incomplete += 1
            elif row.get("status") == STATUS_IDEA:
                props[EventProps.STATUS] = p_select(STATUS_DRAFT)
                changes.append("Idea → Draft")

            # Write Sync Error only when it actually changes — combined with
            # the empty-props guard below, rows with nothing to fill skip the
            # Notion PATCH entirely (sync runs enrich daily, so this is N
            # saved writes per steady-state run). Clearing a stale note after
            # a human fixed the row still writes.
            if (error_note or "") != (row.get("sync_error") or ""):
                props[EventProps.SYNC_ERROR] = p_rich_text(error_note or "")

            if changes:
                logger.info("  ✨ %s: %s", name, ", ".join(changes))
                enriched += 1
            else:
                logger.info("  ⏭️  %s: nothing to fill", name)
                skipped += 1
            if error_note:
                logger.info("     ⚠️  %s", error_note)

            if props:
                try:
                    store.update_event_fields(page_id, props)
                except NotionStoreError as exc:
                    logger.error("  ❌ %s: Notion write failed — %s", name, exc)
                    write_failures += 1

        logger.info(
            "\n📊 Enrich complete: %d enriched, %d unchanged, %d still missing fields",
            enriched, skipped, incomplete,
        )
        if write_failures:
            logger.error("❌ %d enrich write(s) failed — see above", write_failures)
        return write_failures == 0
    except Exception as exc:
        logger.exception("Fatal error during enrich: %s", exc)
        return False


# ---------------------------------------------------------------------------
# sync (Notion -> Wix)
# ---------------------------------------------------------------------------


def _preserved_image_url(
    existing_row: Optional[Dict[str, Any]], wix_image_url: str
) -> str:
    """Keep a human-entered Image URL when the Wix event has none.

    A transient upload failure leaves the Wix event imageless; blindly
    refreshing the Notion row from it would wipe the human-entered Drive
    link permanently. Wixstatic URLs are code-written, so an image removed
    on the website stays removed.
    """
    from .images import is_wix_media_url

    if wix_image_url:
        return wix_image_url
    current = ((existing_row or {}).get("image_url") or "").strip()
    if current and not is_wix_media_url(current):
        return current
    return ""


def _converge_hosted_image(
    store: NotionStore,
    runtime: SyncRuntime,
    record: EventRecord,
    page_id: str,
) -> None:
    """Point the row at the Wix-hosted copy of an image uploaded this run.

    Every upload to Wix Media creates a new file, so a row whose Image URL is
    a wixstatic link that differs from the event's mainImage would re-upload
    on every edit. After a successful push, rewrite the row's Image URL to the
    freshly hosted file so future syncs can skip the upload. Google Drive
    links are left alone — those are the human-managed source of truth.
    """
    from .images import is_wix_media_url, normalize_wix_media_url

    if not record.image_url or not is_wix_media_url(record.image_url):
        return
    cache_key = normalize_wix_media_url(record.image_url)
    descriptor = runtime.get_cached_wix_media(cache_key)
    if not descriptor or not descriptor.get("id"):
        return
    hosted = f"https://static.wixstatic.com/media/{descriptor['id']}"
    if normalize_wix_media_url(hosted) == cache_key:
        return
    record.image_url = hosted
    try:
        store.update_event_fields(
            page_id, {EventProps.IMAGE_URL: p_url(hosted)}
        )
        logger.info("   🖼️  Row image now points at Wix media: %s", hosted)
    except Exception as exc:  # pragma: no cover - non-fatal write-back
        logger.warning("   ⚠️  Could not update row image URL: %s", exc)


# Pacing between Wix mutations to stay friendly with rate limits.
WIX_MUTATION_PACING_SECONDS = 1.0


@dataclass
class _SyncContext:
    """Everything the per-status sync handlers need, threaded as one object."""

    runtime: SyncRuntime
    store: NotionStore
    client: Any
    by_id: Dict[str, Dict[str, Any]]
    by_key: Dict[str, Dict[str, Any]]
    results: Dict[str, List[str]]
    dry_run: bool
    draft: bool
    auto_create_tickets: bool
    # Lazily fetched Catalog/Settings for the Ready-row default-fill safety net.
    _defaults_context: Optional[tuple] = None


def _write_row_result(ctx: _SyncContext, page_id: str, **kwargs: Any) -> None:
    """Row bookkeeping write-back — the single dry-run gate for sync writes."""
    if not ctx.dry_run:
        ctx.store.write_sync_result(page_id, **kwargs)


def _pace_wix(ctx: _SyncContext) -> None:
    """Pause after a Wix mutation (skipped on dry runs, which mutate nothing)."""
    if not ctx.dry_run:
        time.sleep(WIX_MUTATION_PACING_SECONDS)


def _match_wix_event(
    row: Dict[str, Any],
    by_id: Dict[str, Dict[str, Any]],
    by_key: Dict[str, Dict[str, Any]],
) -> tuple:
    """Match a Notion row to a live Wix event: id first, then title|date|time.

    Works on raw rows — no valid record needed, so Cancel/Delete/Published
    can act on incomplete rows. Returns ``(wix_event_or_None, wix_id)``.
    """
    wix_event: Optional[Dict[str, Any]] = None
    wix_id = (row.get("wix_event_id") or "").strip()
    if wix_id and wix_id in by_id:
        wix_event = by_id[wix_id]
    if wix_event is None:
        key = event_match_key(
            row.get("event_name") or "",
            row.get("start_date", ""),
            row.get("start_time", ""),
        )
        wix_event = by_key.get(key)
        if wix_event is not None:
            wix_id = wix_event.get("id") or ""
    return wix_event, wix_id


def _wix_event_to_record(client, wix_event: Dict[str, Any], tz_name: str) -> tuple:
    """Build the Notion-side view of a live Wix event.

    The shared read path of "Wix is authoritative", used by both ``pull`` and
    the sync Published refresh. Returns ``(record_or_None, config_row,
    target_status, invalid_note)`` — ``record`` is None when the Wix event is
    too incomplete to validate (the raw ``config_row`` still lands in Notion,
    flagged with the note).
    """
    wix_id = wix_event.get("id", "")
    # Wix CANCELED events land as Cancelled rows, everything else
    # (UPCOMING/STARTED/ENDED) as Published.
    target_status = (
        STATUS_CANCELLED
        if (wix_event.get("status") or "") == "CANCELED"
        else STATUS_PUBLISHED
    )
    ticket_defs = client.get_ticket_definitions(wix_id)
    config_row = wix_event_to_config_row(wix_event, ticket_defs, tz_name=tz_name)

    record: Optional[EventRecord] = None
    invalid_note = ""
    try:
        record = row_to_event_record(config_row)
    except ValidationError as exc:
        invalid_note = (
            "Pulled from Wix with missing fields — "
            f"{parse_validation_error(exc)}"
        )
    if record is not None:
        record.wix_event_id = wix_id
        record.synced_hash = record.content_hash()
    return record, config_row, target_status, invalid_note


def _apply_image_preservation(
    record: Optional[EventRecord],
    config_row: Dict[str, Any],
    existing_row: Optional[Dict[str, Any]],
) -> None:
    """Keep a human-entered image link when the Wix event has none.

    Applied to whichever shape will be written — the validated record (with
    its hash recomputed so the short-circuit stays consistent) or the raw
    config row.
    """
    if record is not None:
        preserved = _preserved_image_url(existing_row, record.image_url or "")
        if preserved and not record.image_url:
            record.image_url = preserved
            record.synced_hash = record.content_hash()
    else:
        config_row["image_url"] = _preserved_image_url(
            existing_row, (config_row.get("image_url") or "").strip()
        )


def _get_defaults_context(ctx: _SyncContext) -> tuple:
    """Catalog + Settings for the Ready-row default fill, fetched at most once."""
    if ctx._defaults_context is None:
        classes = ctx.store.fetch_classes()
        classes_by_page_id = {
            c["page_id"]: c for c in classes.values() if c.get("page_id")
        }
        settings = ctx.store.fetch_settings()
        ctx._defaults_context = (classes, classes_by_page_id, settings)
    return ctx._defaults_context


# --- per-status handlers ----------------------------------------------------
#
# notion_sync_events dispatches to these in a fixed order that is load-bearing:
# Cancel/Delete/Published run before record validation (incomplete rows can
# still be acted on); Update and Ready share the validated-record match
# prelude and the CANCELED guard.


def _handle_cancel_row(
    ctx: _SyncContext, row: Dict[str, Any], name: str, page_id: str
) -> None:
    """Cancel the matching Wix event; the row becomes Cancelled."""
    wix_event, wix_id = _match_wix_event(row, ctx.by_id, ctx.by_key)
    if wix_event is None:
        logger.warning(
            "  ⚠️  %s: marked Cancel but not found in Wix — nothing to cancel",
            name,
        )
        ctx.results["not_found"].append(name)
        _write_row_result(
            ctx, page_id,
            error="Not found in Wix — nothing to cancel. "
            "Set Status to Delete to just mark the row Removed.",
        )
        return

    wix_status = wix_event.get("status") or ""
    if wix_status == "CANCELED":
        logger.info("  ⏭️  %s (already cancelled in Wix)", name)
        ctx.results["skipped"].append(name)
        _write_row_result(
            ctx, page_id, status=STATUS_CANCELLED,
            wix_event_id=wix_id, error=None,
        )
        return
    if wix_status == "DRAFT":
        logger.warning(
            "  ⚠️  %s: Wix drafts can't be cancelled — use Delete instead",
            name,
        )
        ctx.results["failed"].append(name)
        _write_row_result(
            ctx, page_id, wix_event_id=wix_id,
            error="Wix drafts can't be cancelled — set Status to Delete instead.",
        )
        return

    if ctx.dry_run:
        logger.info("  CANCEL: %s (Wix status %s)", name, wix_status)
        ctx.results["cancelled"].append(name)
        return

    try:
        ctx.client.cancel_event(wix_id)
        logger.info("🚫 Cancelled: %s", name)
        ctx.results["cancelled"].append(name)
        _write_row_result(
            ctx, page_id, status=STATUS_CANCELLED,
            wix_event_id=wix_id, error=None,
        )
    except Exception as exc:
        logger.error("  ❌ Failed to cancel %s: %s", name, exc)
        ctx.results["failed"].append(name)
        _write_row_result(
            ctx, page_id, status=STATUS_ERROR, wix_event_id=wix_id,
            error=f"Cancel failed: {exc}",
        )
    _pace_wix(ctx)


def _handle_delete_row(
    ctx: _SyncContext, row: Dict[str, Any], name: str, page_id: str
) -> None:
    """Delete the matching Wix event outright; the row becomes Removed."""
    wix_event, wix_id = _match_wix_event(row, ctx.by_id, ctx.by_key)
    if wix_event is None:
        # Already gone from Wix (or never created) — intent is met.
        logger.info(
            "  🗑️  %s: not found in Wix — marking Removed", name,
        )
        ctx.results["removed"].append(name)
        _write_row_result(ctx, page_id, status=STATUS_REMOVED, error=None)
        return

    if ctx.dry_run:
        logger.info(
            "  DELETE: %s (Wix status %s)",
            name, wix_event.get("status") or "?",
        )
        ctx.results["removed"].append(name)
        return

    if ctx.client.delete_event(wix_id, force=True):
        logger.info("🗑️  Deleted from Wix: %s", name)
        ctx.results["removed"].append(name)
        _write_row_result(
            ctx, page_id, status=STATUS_REMOVED,
            wix_event_id=wix_id, error=None,
        )
    else:
        logger.error("  ❌ Failed to delete %s", name)
        ctx.results["failed"].append(name)
        _write_row_result(
            ctx, page_id, status=STATUS_ERROR, wix_event_id=wix_id,
            error="Delete failed — see sync logs",
        )
    _pace_wix(ctx)


def _refresh_published_row(
    ctx: _SyncContext, row: Dict[str, Any], name: str, page_id: str
) -> None:
    """Refresh a Published row from its live Wix event (Wix is authoritative)."""
    wix_event, wix_id = _match_wix_event(row, ctx.by_id, ctx.by_key)
    if wix_event is None:
        logger.warning(
            "  ⚠️  %s: marked Published but not found in Wix — "
            "flip Status to Ready to recreate it", name,
        )
        ctx.results["not_found"].append(name)
        _write_row_result(
            ctx, page_id,
            error="Not found in Wix (deleted?). Set Status to Ready to recreate.",
        )
        return

    wix_record, config_row, target_status, invalid_note = _wix_event_to_record(
        ctx.client, wix_event, ctx.runtime.config.timezone
    )
    _apply_image_preservation(wix_record, config_row, row)

    if wix_record is None:
        # Wix event too incomplete to validate (e.g. TBD date): land it
        # anyway with a Sync Error note, like pull does.
        if ctx.dry_run:
            logger.info("  REFRESH: %s — %s", name, invalid_note)
            ctx.results["incomplete"].append(name)
            return
        ctx.store.upsert_event_from_raw_row(
            config_row, status=target_status, source=SOURCE_WIX,
            wix_event_id=wix_id, error=invalid_note, page_id=page_id,
        )
        logger.warning("  ⚠️  %s: %s", name, invalid_note)
        ctx.results["incomplete"].append(name)
        return

    row_hash: Optional[str] = None
    try:
        row_hash = row_to_event_record(row).content_hash()
    except ValidationError:
        row_hash = None

    row_status = row.get("status") or ""
    if row_hash == wix_record.synced_hash and row_status == target_status:
        stale_bookkeeping = (
            (row.get("synced_hash") or "") != wix_record.synced_hash
            or (row.get("wix_event_id") or "").strip() != wix_id
            or bool((row.get("sync_error") or "").strip())
        )
        if stale_bookkeeping:
            _write_row_result(
                ctx, page_id, wix_event_id=wix_id,
                synced_hash=wix_record.synced_hash, error=None,
            )
        logger.info("  ⏭️  %s (matches Wix)", name)
        ctx.results["skipped"].append(name)
        return

    if ctx.dry_run:
        logger.info(
            "  REFRESH: %s (Notion row updated from Wix%s)",
            name,
            " — becomes Cancelled"
            if target_status == STATUS_CANCELLED else "",
        )
        ctx.results["refreshed"].append(name)
        return

    ctx.store.upsert_event_from_record(
        wix_record, status=target_status, source=SOURCE_WIX,
        page_id=page_id,
    )
    logger.info("  ⬇️  %s: refreshed from Wix (%s)", name, target_status)
    ctx.results["refreshed"].append(name)


def _default_fill_ready_row(
    ctx: _SyncContext, row: Dict[str, Any], name: str, page_id: str
) -> None:
    """Safety net for rows flipped straight to Ready without an enrich pass:
    fill blanks from the class catalog + Settings defaults so the push uses
    (and the row shows) the same defaults enrich applies."""
    classes, classes_by_page_id, settings = _get_defaults_context(ctx)
    klass = _resolve_class_for_row(row, classes, classes_by_page_id)
    fill_props, fill_changes = _apply_row_defaults(
        row, klass, settings, tz_name=ctx.runtime.config.timezone
    )
    if fill_changes:
        logger.info("  ✨ %s: defaulted %s", name, ", ".join(fill_changes))
        if not ctx.dry_run:
            ctx.store.update_event_fields(page_id, fill_props)


def _push_update_row(
    ctx: _SyncContext,
    record: EventRecord,
    wix_event: Optional[Dict[str, Any]],
    wix_id: str,
    name: str,
    page_id: str,
) -> None:
    """Push local Notion edits to Wix (the reverse of the Published refresh),
    then land the row back on Published. No hash fast-path — an explicit
    Update always diffs."""
    if wix_event is None:
        logger.warning(
            "  ⚠️  %s: marked Update but not found in Wix — "
            "flip Status to Ready to create it", name,
        )
        ctx.results["not_found"].append(name)
        _write_row_result(
            ctx, page_id,
            error="Not found in Wix (deleted?). Set Status to Ready to create it.",
        )
        return

    plan = compute_event_update_plan(ctx.client, ctx.runtime, record, wix_id, wix_event)
    if not plan["any_changes"]:
        logger.info("  ⏭️  %s (Wix already matches) — back to Published", name)
        ctx.results["skipped"].append(name)
        _write_row_result(
            ctx, page_id, status=STATUS_PUBLISHED, wix_event_id=wix_id,
            synced_hash=record.content_hash(), error=None,
        )
        return

    if ctx.dry_run:
        log_update_plan_dry_run(record, plan)
        ctx.results["updated"].append(name)
        return

    logger.info(
        "♻️  Pushing local changes: %s on %s [%s]",
        name, record.start_date, plan["change_desc"],
    )
    if plan["event_changed"]:
        log_event_diff(name, plan["event_diffs"])

    if apply_event_update_plan(ctx.client, ctx.runtime, record, wix_id, wix_event, plan):
        ctx.results["updated"].append(name)
        _converge_hosted_image(ctx.store, ctx.runtime, record, page_id)
        _write_row_result(
            ctx, page_id, status=STATUS_PUBLISHED, wix_event_id=wix_id,
            synced_hash=record.content_hash(), error=None,
        )
    else:
        ctx.results["failed"].append(name)
        _write_row_result(
            ctx, page_id, status=STATUS_ERROR, wix_event_id=wix_id,
            error="Update failed — see sync logs",
        )
    _pace_wix(ctx)


def _push_matched_ready_row(
    ctx: _SyncContext,
    record: EventRecord,
    wix_event: Dict[str, Any],
    wix_id: str,
    name: str,
    page_id: str,
) -> None:
    """A Ready row matching an existing Wix event: publish the draft or
    update the live event — never create a duplicate."""
    wix_status = wix_event.get("status") or ""

    if wix_status == "DRAFT" and not ctx.draft:
        if ctx.dry_run:
            logger.info("  PUBLISH: %s (existing Wix draft)", name)
            ctx.results["published"].append(name)
            return
        try:
            ctx.client.publish_event(wix_id)
            logger.info("📢 Published draft: %s", name)
        except Exception as exc:
            logger.error("  ❌ Failed to publish draft %s: %s", name, exc)
            ctx.results["failed"].append(name)
            _write_row_result(
                ctx, page_id, status=STATUS_ERROR, wix_event_id=wix_id,
                error=f"Publish failed: {exc}",
            )
            return
        if ctx.auto_create_tickets and record.registration_type == "TICKETING":
            if record.ticket_name:
                create_tickets_from_config(ctx.client, wix_id, record)
            elif record.ticket_price > 0:
                ensure_ticket_definition(ctx.client, wix_id, record)
        ctx.results["published"].append(name)
        _write_row_result(
            ctx, page_id, status=STATUS_PUBLISHED, wix_event_id=wix_id,
            synced_hash=record.content_hash(), error=None,
        )
        _pace_wix(ctx)
        return

    # Already exists (live, or draft while in --draft mode): update.
    plan = compute_event_update_plan(ctx.client, ctx.runtime, record, wix_id, wix_event)
    if ctx.dry_run:
        if plan["any_changes"]:
            log_update_plan_dry_run(record, plan)
            ctx.results["updated"].append(name)
        else:
            logger.info("  SKIP: %s (already in Wix, no changes)", name)
            ctx.results["skipped"].append(name)
        return

    ok = True
    if plan["any_changes"]:
        logger.info(
            "♻️  Updating existing: %s [%s]", name, plan["change_desc"],
        )
        ok = apply_event_update_plan(ctx.client, ctx.runtime, record, wix_id, wix_event, plan)
    else:
        logger.info("  🔗 %s already in Wix — linking row", name)

    if ok and ctx.auto_create_tickets and record.registration_type == "TICKETING" and wix_status != "DRAFT":
        # The plan already fetched this event's ticket definitions.
        plan_defs = plan.get("wix_ticket_defs")
        if record.ticket_name:
            create_tickets_from_config(
                ctx.client, wix_id, record, existing_defs=plan_defs
            )
        elif record.ticket_price > 0:
            ensure_ticket_definition(
                ctx.client, wix_id, record, existing_defs=plan_defs
            )

    new_status = STATUS_READY if wix_status == "DRAFT" else STATUS_PUBLISHED
    if ok:
        ctx.results["updated"].append(name)
        _converge_hosted_image(ctx.store, ctx.runtime, record, page_id)
        _write_row_result(
            ctx, page_id, status=new_status, wix_event_id=wix_id,
            synced_hash=record.content_hash(), error=None,
        )
    else:
        ctx.results["failed"].append(name)
        _write_row_result(
            ctx, page_id, status=STATUS_ERROR, wix_event_id=wix_id,
            error="Update failed — see sync logs",
        )
    _pace_wix(ctx)


def _create_new_event(
    ctx: _SyncContext, record: EventRecord, name: str, page_id: str
) -> None:
    """A Ready row with no Wix match: create the event (optionally as draft)."""
    if ctx.dry_run:
        logger.info(
            "  CREATE: %s on %s %s%s", name, record.start_date,
            record.start_time, " (as draft)" if ctx.draft else "",
        )
        ctx.results["created"].append(name)
        return

    logger.info("➕ Creating: %s on %s", name, record.start_date)
    new_id = create_wix_event(
        record, runtime=ctx.runtime,
        auto_create_tickets=ctx.auto_create_tickets, draft=ctx.draft,
    )
    if new_id:
        ctx.results["created"].append(name)
        _converge_hosted_image(ctx.store, ctx.runtime, record, page_id)
        failed_image = getattr(ctx.runtime, "last_image_failure", None)
        if ctx.draft:
            note = "Created as Wix draft — run sync without --draft to publish"
        elif failed_image:
            note = (
                "Created without image — upload failed for "
                f"{failed_image}. Fix the link and set Status to "
                "Update to retry."
            )
        else:
            note = None
        _write_row_result(
            ctx, page_id,
            status=STATUS_READY if ctx.draft else STATUS_PUBLISHED,
            wix_event_id=new_id,
            synced_hash=record.content_hash(),
            error=note,
        )
    else:
        ctx.results["failed"].append(name)
        _write_row_result(
            ctx, page_id, status=STATUS_ERROR,
            error="Create failed — see sync logs",
        )
    _pace_wix(ctx)


def _sync_row(ctx: _SyncContext, row: Dict[str, Any], name: str) -> None:
    """Dispatch one row to its status handler.

    The dispatch order is load-bearing — NOT a status->handler map.
    Cancel/Delete/Published act before record validation so incomplete rows
    can still be cancelled, deleted, or refreshed.
    """
    page_id = row["page_id"]
    row_status = row.get("status") or ""

    if row_status == STATUS_CANCEL:
        _handle_cancel_row(ctx, row, name, page_id)
        return
    if row_status == STATUS_DELETE:
        _handle_delete_row(ctx, row, name, page_id)
        return
    if row_status == STATUS_PUBLISHED:
        _refresh_published_row(ctx, row, name, page_id)
        return

    if row_status == STATUS_READY and (row.get("event_name") or "").strip():
        _default_fill_ready_row(ctx, row, name, page_id)

    try:
        record = row_to_event_record(row)
    except ValidationError as exc:
        message = parse_validation_error(exc)
        logger.error("  ❌ %s: invalid row — %s", name, message)
        ctx.results["failed"].append(name)
        _write_row_result(
            ctx, page_id, status=STATUS_ERROR,
            error=f"Invalid row: {message}",
        )
        return

    wix_event, wix_id = _match_wix_event(row, ctx.by_id, ctx.by_key)

    # Wix events cancelled outside this pipeline can't be updated or
    # recreated in place — reflect reality on the row and move on.
    if wix_event is not None and (wix_event.get("status") or "") == "CANCELED":
        logger.warning(
            "  🚫 %s: event is cancelled in Wix — marking row Cancelled", name,
        )
        ctx.results["skipped"].append(name)
        _write_row_result(
            ctx, page_id, status=STATUS_CANCELLED, wix_event_id=wix_id,
            error="Cancelled in Wix. Set Status to Delete to remove it, "
            "or duplicate the row without the Wix Event ID to recreate.",
        )
        return

    if row_status == STATUS_UPDATE:
        _push_update_row(ctx, record, wix_event, wix_id, name, page_id)
        return

    if wix_event is not None:
        _push_matched_ready_row(ctx, record, wix_event, wix_id, name, page_id)
        return

    _create_new_event(ctx, record, name, page_id)


def _log_sync_summary(
    results: Dict[str, List[str]], dry_run: bool, cache_stats: Dict[str, int]
) -> None:
    """Human-facing end-of-run report (kept verbatim from the monolith)."""
    logger.info("\n📈 Sync Complete!\n")
    label = "Would create" if dry_run else "Created"
    logger.info("➕ %s: %d", label, len(results["created"]))
    for n in results["created"]:
        logger.info("  • %s", n)
    if results["published"]:
        logger.info("\n📢 Published drafts: %d", len(results["published"]))
        for n in results["published"]:
            logger.info("  • %s", n)
    if results["updated"]:
        label = "Would push to Wix" if dry_run else "Pushed to Wix"
        logger.info("\n♻️  %s: %d", label, len(results["updated"]))
        for n in results["updated"]:
            logger.info("  • %s", n)
    if results["refreshed"]:
        label = (
            "Would refresh from Wix" if dry_run else "Refreshed from Wix"
        )
        logger.info("\n⬇️  %s: %d", label, len(results["refreshed"]))
        for n in results["refreshed"]:
            logger.info("  • %s", n)
    if results["cancelled"]:
        label = "Would cancel" if dry_run else "Cancelled"
        logger.info("\n🚫 %s: %d", label, len(results["cancelled"]))
        for n in results["cancelled"]:
            logger.info("  • %s", n)
    if results["removed"]:
        label = "Would delete" if dry_run else "Removed from Wix"
        logger.info("\n🗑️  %s: %d", label, len(results["removed"]))
        for n in results["removed"]:
            logger.info("  • %s", n)
    if results["skipped"]:
        logger.info("\n⏭️  Skipped (no changes): %d", len(results["skipped"]))
    if results["incomplete"]:
        logger.warning(
            "\n⚠️  Incomplete rows (fix fields in Notion or Wix): %d",
            len(results["incomplete"]),
        )
        for n in results["incomplete"]:
            logger.warning("  • %s", n)
    if results["not_found"]:
        logger.warning(
            "\n⚠️  Published/Update rows missing from Wix: %d",
            len(results["not_found"]),
        )
        for n in results["not_found"]:
            logger.warning("  • %s", n)
    if results["failed"]:
        logger.error("\n❌ Failed: %d", len(results["failed"]))
        for n in results["failed"]:
            logger.error("  • %s", n)

    logger.info("\n🧮 Cache summary:")
    logger.info(
        "   Google Drive - hits: %s, misses: %s",
        cache_stats["drive_hits"], cache_stats["drive_misses"],
    )
    logger.info(
        "   Wix Media    - hits: %s, uploads: %s",
        cache_stats["wix_hits"], cache_stats["wix_uploads"],
    )


def notion_sync_events(
    runtime: SyncRuntime,
    auto_create_tickets: bool = True,
    draft: bool = False,
    dry_run: bool = False,
    month_filters: Optional[List[str]] = None,
    run_enrich: bool = True,
) -> bool:
    """Sync Notion rows with Wix.

    Starts with an enrich pass (unless ``run_enrich`` is False or this is a
    dry run) so Idea/Draft rows get filled and annotated on the same run —
    drafts still need a human flip to Ready before anything is pushed.

    ``Ready`` rows are created (or, if they already match a Wix event,
    updated/published). ``Published`` rows treat Wix as authoritative: the
    Notion row is refreshed from the live event, so edits made on the website
    flow back into Notion. To push local Notion edits the other way, flip the
    row to ``Update`` — it is pushed to Wix and lands back on ``Published``.
    ``Cancel`` rows cancel the Wix event (row becomes ``Cancelled``);
    ``Delete`` rows delete it outright (row becomes ``Removed``). Results are
    written back onto each row.
    """
    logger.info("🚀 Starting Notion ⇄ Wix sync...\n")
    if dry_run:
        logger.info("🔍 DRY RUN — nothing will be written to Wix or Notion\n")
    if draft:
        logger.info("📋 Mode: DRAFT (new events created as Wix drafts, no tickets)")

    try:
        allowed_months = _month_numbers(month_filters)
    except ValueError as exc:
        logger.error("%s", exc)
        return False

    if run_enrich:
        if dry_run:
            logger.info("⏭️  Skipping enrich pass (dry run writes nothing to Notion)\n")
        else:
            if not enrich_events(runtime, month_filters=month_filters):
                logger.warning("⚠️  Enrich pass had errors — continuing with sync")
            logger.info("")

    try:
        store: NotionStore = runtime.get_notion_store()
        rows = store.fetch_event_rows(
            statuses=[
                STATUS_READY,
                STATUS_PUBLISHED,
                STATUS_UPDATE,
                STATUS_CANCEL,
                STATUS_DELETE,
            ]
        )
        rows = [r for r in rows if _row_in_months(r, allowed_months)]
        if not rows:
            logger.info("No Ready/Published/Update/Cancel/Delete rows to sync.")
            return True

        client = runtime.get_wix_client()
        # DETAILS is needed so existing mainImage/descriptions are present for
        # diffing and for skipping re-uploads of images already in Wix Media.
        by_id, by_key = index_events_by_id_and_key(
            runtime, fieldsets=["DETAILS", "CATEGORIES", "REGISTRATION"],
        )

        ctx = _SyncContext(
            runtime=runtime,
            store=store,
            client=client,
            by_id=by_id,
            by_key=by_key,
            results={
                "created": [],
                "updated": [],
                "published": [],
                "refreshed": [],
                "cancelled": [],
                "removed": [],
                "skipped": [],
                "incomplete": [],
                "not_found": [],
                "failed": [],
            },
            dry_run=dry_run,
            draft=draft,
            auto_create_tickets=auto_create_tickets,
        )

        for row in rows:
            name = row.get("event_name") or "(unnamed)"
            try:
                _sync_row(ctx, row, name)
            except NotionStoreError as exc:
                # One row's failed write-back must not abort the batch — the
                # row keeps its status and is retried on the next run.
                logger.error("  ❌ %s: Notion write failed — %s", name, exc)
                ctx.results["failed"].append(name)

        _log_sync_summary(ctx.results, dry_run, runtime.cache_stats)
        return len(ctx.results["failed"]) == 0
    except Exception as exc:
        logger.exception("Fatal error during sync: %s", exc)
        return False


# ---------------------------------------------------------------------------
# Site config (Notion-backed tax-by-location round-trip)
# ---------------------------------------------------------------------------


def pull_site_config_notion(runtime: SyncRuntime) -> bool:
    """Pull Wix tax regions/mappings into the Notion Site Config DB."""
    logger.info("⬇️  Pulling site config (tax locations) from Wix into Notion...\n")

    try:
        store: NotionStore = runtime.get_notion_store()
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
        default_group_id = select_default_tax_group_id(groups)

        rows: List[Dict[str, Any]] = [
            tax_mapping_to_site_row(m, regions_by_id) for m in mappings
        ]
        mapped_region_ids = {m.get("taxRegionId", "") for m in mappings}
        for region in regions:
            if region.get("id", "") in mapped_region_ids:
                continue
            rows.append(blank_region_site_row(region, default_group_id))

        rows.sort(key=site_config_row_sort_key)

        # One scan of the Site Config DB serves every upsert below.
        page_index = store.index_site_config_pages()
        outcomes = {"created": 0, "updated": 0, "unchanged": 0}
        for row in rows:
            outcome = store.upsert_site_config_row(row, page_index=page_index)
            outcomes[outcome] += 1
            marker = "✅" if outcome != "unchanged" else "⏭️"
            logger.info(
                "  %s %s — %s%%%s",
                marker,
                row.get("jurisdiction") or row.get("region") or "(unknown)",
                row.get("tax_rate") or "unset",
                " (unchanged)" if outcome == "unchanged" else "",
            )

        logger.info(
            "\n📊 Site config: %d created, %d updated, %d unchanged",
            outcomes["created"], outcomes["updated"], outcomes["unchanged"],
        )
        return True
    except Exception as exc:
        logger.exception("Failed to pull site config: %s", exc)
        return False


def push_site_config_notion(runtime: SyncRuntime, dry_run: bool = False) -> bool:
    """Push tax edits from the Notion Site Config DB back to Wix."""
    logger.info("🚀 Push site config (tax locations) from Notion to Wix...\n")
    if dry_run:
        logger.info("🔍 DRY RUN — no changes will be made\n")

    try:
        store: NotionStore = runtime.get_notion_store()
        rows = store.fetch_site_config_rows()
        if not rows:
            logger.warning(
                "No site config rows in Notion. Run pull-site-config first."
            )
            return False
        return process_site_config_rows(runtime, rows, dry_run=dry_run)
    except Exception as exc:
        logger.exception("Fatal error during site config push: %s", exc)
        return False
