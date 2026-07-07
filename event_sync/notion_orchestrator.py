"""Notion-backed orchestration: enrich, sync, pull, and site-config flows.

This module composes the Notion store (``notion_store``) with the existing Wix
call paths in ``orchestrator``. The Google-Sheets flows in ``orchestrator`` /
``generator`` are untouched and remain available under their ``*-sheet``
CLI aliases until the Notion path is proven out.
"""

from __future__ import annotations

import time
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
from .orchestrator import (
    _create_tickets_from_config,
    _ensure_ticket_definition,
    _index_events_by_id_and_key,
    _log_event_diff,
    apply_event_update_plan,
    compute_event_update_plan,
    create_wix_event,
    log_update_plan_dry_run,
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
        store.upsert_setting(key, value, notes=notes)
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
# import-classes (one-time: class_info sheet -> Catalog DB, defaults -> Settings)
# ---------------------------------------------------------------------------


def _slugify_category(raw: str) -> str:
    return "-".join(raw.strip().lower().split())


def import_classes(runtime: SyncRuntime) -> bool:
    """One-time import of the class_info sheet tab into the Catalog DB.

    Also copies the source sheet's ``defaults`` tab (e.g. ``default_img``)
    into the Settings DB so the sheet is no longer needed at enrich time.
    """
    from .generator import fetch_class_info, fetch_defaults

    store: NotionStore = runtime.get_notion_store()

    logger.info("📚 Importing class_info sheet into the Notion Catalog DB...\n")
    try:
        class_info = fetch_class_info(runtime)
    except Exception as exc:
        logger.error("❌ Failed to read class_info sheet: %s", exc)
        return False

    if not class_info:
        logger.warning("No class definitions found in the class_info tab.")
        return False

    existing = store.fetch_classes()

    created = 0
    updated = 0
    failed = 0
    for class_name, details in class_info.items():
        categories = [
            _slugify_category(c)
            for c in (details.get("categories", "") or "").split(";")
            if c.strip()
        ]
        existing_entry = existing.get(class_name.strip().lower())
        try:
            store.upsert_class(
                name=class_name,
                categories=categories,
                tagline=details.get("class_tagline", "") or "",
                description=details.get("description", "") or "",
                image_url=(details.get("image_link", "") or "").strip(),
                existing_page_id=(existing_entry or {}).get("page_id"),
            )
            if existing_entry:
                updated += 1
                logger.info("  ♻️  Updated class: %s", class_name)
            else:
                created += 1
                logger.info("  ➕ Created class: %s", class_name)
        except Exception as exc:
            failed += 1
            logger.warning("  ⚠️  Failed to import '%s': %s", class_name, exc)

    logger.info("\n⚙️  Copying defaults tab into the Settings DB...")
    try:
        defaults = fetch_defaults(runtime)
        for key, value in defaults.items():
            store.upsert_setting(key, value)
            logger.info("  ✅ Setting '%s' = %s", key, value)
        if not defaults:
            logger.info("  (no defaults found — set 'default_img' in Settings by hand)")
    except Exception as exc:
        logger.warning("  ⚠️  Could not import defaults: %s", exc)

    logger.info(
        "\n📊 Classes import: %d created, %d updated, %d failed",
        created, updated, failed,
    )
    return failed == 0


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
    from .generator import _wix_event_to_config_row

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
                key = f"{row['event_name']}|{row['start_date']}|{row['start_time']}"
                by_key.setdefault(key, row)

        results = {"created": [], "refreshed": [], "linked": [], "skipped": [], "failed": []}

        # Rows in these statuses are code-owned and safe to refresh from Wix.
        refreshable_statuses = {STATUS_PUBLISHED, STATUS_CANCELLED}

        for i, wix_event in enumerate(wix_events, 1):
            title = wix_event.get("title", "Untitled")
            wix_id = wix_event.get("id", "")
            logger.info("  %d/%d  %s", i, len(wix_events), title)

            # Wix CANCELED events land as Cancelled rows, everything else
            # (UPCOMING/STARTED/ENDED) as Published.
            target_status = (
                STATUS_CANCELLED
                if (wix_event.get("status") or "") == "CANCELED"
                else STATUS_PUBLISHED
            )

            ticket_defs = client.get_ticket_definitions(wix_id)
            config_row = _wix_event_to_config_row(wix_event, ticket_defs, tz_name=tz_name)

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
                match_key = f"{record.name}|{record.start_date}|{record.start_time}"
            else:
                match_key = (
                    f"{(config_row.get('event_name') or '').strip()}|"
                    f"{config_row.get('start_date', '')}|{config_row.get('start_time', '')}"
                )

            existing = by_wix_id.get(wix_id)
            matched_by_key = False
            if existing is None:
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
        logger.error("Fatal error during pull: %s", exc)
        return False


# ---------------------------------------------------------------------------
# enrich (fill blanks on Idea/Draft rows from Classes + Settings + pricing)
# ---------------------------------------------------------------------------


_BASELINE_TAGS = ["rope", "class"]


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
    from .generator import _parse_month_value

    if not month_filters:
        return None
    return {_parse_month_value(m) for m in month_filters}


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

    # Schedule: a template can carry default start/end times (HH:MM) so a
    # date-only Date property becomes a full schedule. Only blank time parts
    # are filled; a time someone picked in Notion stays as-is. Without a
    # start time (typed or defaulted) the Date is left alone — enrich will
    # flag the missing time as before.
    if klass and (row.get("start_date") or "").strip():
        tpl_start = _normalize_hhmm(klass.get("default_start_time") or "")
        tpl_end = _normalize_hhmm(klass.get("default_end_time") or "")
        if (row.get("start_time") or "").strip() or tpl_start:
            filled: List[str] = []
            if not (row.get("start_time") or "").strip():
                row["start_time"] = tpl_start
                filled.append(f"start time {tpl_start}")
            if not (row.get("end_time") or "").strip() and tpl_end:
                row["end_time"] = tpl_end
                filled.append(f"end time {tpl_end}")
            if filled:
                if not (row.get("end_date") or "").strip():
                    row["end_date"] = row["start_date"]
                end_time = (row.get("end_time") or "").strip()
                # Overnight events (Voyeur 21:00 -> 03:00): an end at or
                # before the start on the same day rolls to the next day.
                if (
                    end_time
                    and row["end_date"] == row["start_date"]
                    and end_time <= (row.get("start_time") or "")
                ):
                    try:
                        start_day = datetime.strptime(
                            row["start_date"], "%Y-%m-%d"
                        )
                        row["end_date"] = (
                            start_day + timedelta(days=1)
                        ).strftime("%Y-%m-%d")
                    except ValueError:
                        pass
                props[EventProps.DATE] = p_date(
                    row["start_date"],
                    row["start_time"],
                    row["end_date"] if end_time else None,
                    end_time or None,
                    tz_name=tz_name,
                )
                changes.extend(filled)

    # Staffing: template default instructor (before the description fill,
    # which prepends "Instructors: ..." from this field).
    if klass and not (row.get("instructor") or "").strip():
        tpl_instructor = (klass.get("default_instructor") or "").strip()
        if tpl_instructor:
            props[EventProps.INSTRUCTOR] = p_rich_text(tpl_instructor)
            changes.append("instructor")
            row["instructor"] = tpl_instructor

    default_image = (settings.get("default_img") or "").strip()
    default_location = (settings.get("default_location") or "").strip() or DEFAULT_LOCATION
    default_reg_type = (
        (settings.get("default_registration_type") or "").strip().upper() or "TICKETS"
    )
    default_tax_name = (settings.get("default_tax_name") or "").strip() or DEFAULT_TAX_NAME
    default_tax_rate = (settings.get("default_tax_rate") or "").strip() or DEFAULT_TAX_RATE
    default_tax_type = (settings.get("default_tax_type") or "").strip() or DEFAULT_TAX_TYPE
    default_fee_type = (settings.get("default_fee_type") or "").strip() or DEFAULT_FEE_TYPE
    try:
        default_capacity = int(float(settings.get("default_capacity", "")))
    except (TypeError, ValueError):
        default_capacity = DEFAULT_CAPACITY

    # Categories: merge row + template categories. The rope/class baseline
    # tags only apply to class templates — event templates (jams, parties,
    # shows) carry exactly their own tags.
    is_class_template = (
        klass is not None
        and (klass.get("type") or TEMPLATE_TYPE_CLASS) == TEMPLATE_TYPE_CLASS
    )
    row_cats = [
        c.strip() for c in (row.get("categories") or "").split(";") if c.strip()
    ]
    if klass:
        baseline = _BASELINE_TAGS if is_class_template else []
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
            row_cats = merged
            row["categories"] = "; ".join(merged)

    if not (row.get("location") or "").strip():
        props[EventProps.LOCATION] = p_rich_text(default_location)
        changes.append("location")
        row["location"] = default_location

    if not (row.get("registration_type") or "").strip():
        props[EventProps.REGISTRATION_TYPE] = p_select(default_reg_type)
        changes.append("registration type")
        row["registration_type"] = default_reg_type

    if not (row.get("capacity") or "").strip():
        capacity = None
        if klass and klass.get("default_capacity"):
            capacity = int(klass["default_capacity"])
        props[EventProps.CAPACITY] = p_number(capacity or default_capacity)
        changes.append("capacity")
        row["capacity"] = str(capacity or default_capacity)

    if not (row.get("ticket_price") or "").strip():
        price: Optional[float] = None
        # `is not None` (not truthiness) so a $0 override — free events like
        # Bound Together — is honored.
        if klass and klass.get("price_override") is not None:
            price = float(klass["price_override"])
        if price is None:
            for tag in row_cats:
                if tag in _BASELINE_TAGS:
                    continue
                price = _lookup_price_for_category(tag)
                if price is not None:
                    break
        if price is None and is_class_template:
            price = 30.0  # class rows always get a price (default $30)
        if price is not None:
            props[EventProps.TICKET_PRICE] = p_number(price)
            changes.append(f"price ${price:g}")
            row["ticket_price"] = f"{price:g}"

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

    if not (row.get("image_url") or "").strip():
        image = ""
        if klass:
            image = (klass.get("image_url") or "").strip()
        image = image or default_image
        if image:
            props[EventProps.IMAGE_URL] = p_url(image)
            changes.append("image")
            row["image_url"] = image

    is_ticketed = (row.get("registration_type") or "").strip().upper() in {
        "TICKETS", "TICKETING",
    }

    if (
        is_ticketed
        and not (row.get("tax_name") or "").strip()
        and not (row.get("tax_rate") or "").strip()
    ):
        props[EventProps.TAX_NAME] = p_rich_text(default_tax_name)
        try:
            props[EventProps.TAX_RATE] = p_number(float(default_tax_rate))
        except ValueError:
            props[EventProps.TAX_RATE] = p_number(float(DEFAULT_TAX_RATE))
            default_tax_rate = DEFAULT_TAX_RATE
        props[EventProps.TAX_TYPE] = p_rich_text(default_tax_type)
        changes.append("tax")
        row["tax_name"] = default_tax_name
        row["tax_rate"] = default_tax_rate
        row["tax_type"] = default_tax_type

    if is_ticketed and not (row.get("fee_type") or "").strip():
        props[EventProps.FEE_TYPE] = p_rich_text(default_fee_type)
        changes.append("fee type")
        row["fee_type"] = default_fee_type

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
        rows = store.fetch_event_rows(statuses=[STATUS_IDEA, STATUS_DRAFT])
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

        for row in rows:
            if not _row_in_months(row, allowed_months):
                continue

            klass = _resolve_class_for_row(row, classes, classes_by_page_id)

            # A linked Template is enough — fill a blank Name from the
            # template so the rest of the enrichment can run right away.
            name_props: Dict[str, Any] = {}
            if not (row.get("event_name") or "").strip():
                if klass is None:
                    logger.info(
                        "  ⏭️  Skipping unnamed row (add a Name or link a Template first)"
                    )
                    continue
                row["event_name"] = klass["class"]
                name_props[EventProps.NAME] = p_title(klass["class"])

            name = row["event_name"]
            page_id = row["page_id"]

            props, changes = _apply_row_defaults(
                row, klass, settings, tz_name=runtime.config.timezone
            )
            if name_props:
                props.update(name_props)
                changes.insert(0, "name from template")

            # Validate what the row would look like at sync time; surface
            # anything still missing in Sync Error so editors can see it.
            error_note: Optional[str] = None
            try:
                row_to_event_record(row)
            except ValidationError as exc:
                error_note = f"Not ready to sync: {parse_validation_error(exc)}"

            if error_note:
                props[EventProps.SYNC_ERROR] = p_rich_text(error_note)
                incomplete += 1
            elif row.get("status") == STATUS_IDEA:
                props[EventProps.STATUS] = p_select(STATUS_DRAFT)
                changes.append("Idea → Draft")
                props[EventProps.SYNC_ERROR] = p_rich_text("")
            else:
                props[EventProps.SYNC_ERROR] = p_rich_text("")

            if changes:
                logger.info("  ✨ %s: %s", name, ", ".join(changes))
                enriched += 1
            else:
                logger.info("  ⏭️  %s: nothing to fill", name)
                skipped += 1
            if error_note:
                logger.info("     ⚠️  %s", error_note)

            store.update_event_fields(page_id, props)

        logger.info(
            "\n📊 Enrich complete: %d enriched, %d unchanged, %d still missing fields",
            enriched, skipped, incomplete,
        )
        return True
    except Exception as exc:
        logger.error("Fatal error during enrich: %s", exc)
        return False


# ---------------------------------------------------------------------------
# sync (Notion -> Wix)
# ---------------------------------------------------------------------------


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


def notion_sync_events(
    runtime: SyncRuntime,
    auto_create_tickets: bool = True,
    draft: bool = False,
    dry_run: bool = False,
    month_filters: Optional[List[str]] = None,
    run_enrich: bool = True,
) -> bool:
    """Push Notion rows to Wix.

    Starts with an enrich pass (unless ``run_enrich`` is False or this is a
    dry run) so Idea/Draft rows get filled and annotated on the same run —
    drafts still need a human flip to Ready before anything is pushed.

    ``Ready`` rows are created (or, if they already match a Wix event,
    updated/published). ``Published`` rows are re-pushed only when their
    content hash differs from ``Synced Hash`` or a previous sync error is
    recorded. ``Cancel`` rows cancel the Wix event (row becomes ``Cancelled``);
    ``Delete`` rows delete it outright (row becomes ``Removed``). Results are
    written back onto each row.
    """
    logger.info("🚀 Starting Notion → Wix sync...\n")
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
            statuses=[STATUS_READY, STATUS_PUBLISHED, STATUS_CANCEL, STATUS_DELETE]
        )
        rows = [r for r in rows if _row_in_months(r, allowed_months)]
        if not rows:
            logger.info("No Ready/Published/Cancel/Delete rows to sync.")
            return True

        client = runtime.get_wix_client()
        # DETAILS is needed so existing mainImage/descriptions are present for
        # diffing and for skipping re-uploads of images already in Wix Media.
        by_id, by_key = _index_events_by_id_and_key(
            runtime, fieldsets=["DETAILS", "CATEGORIES", "REGISTRATION"],
        )

        results: Dict[str, List[str]] = {
            "created": [],
            "updated": [],
            "published": [],
            "cancelled": [],
            "removed": [],
            "skipped": [],
            "incomplete": [],
            "not_found": [],
            "failed": [],
        }

        def _match_raw_row(row: Dict[str, Any]) -> tuple:
            """Match a row to a live Wix event without needing a valid record."""
            wix_event: Optional[Dict[str, Any]] = None
            wix_id = (row.get("wix_event_id") or "").strip()
            if wix_id and wix_id in by_id:
                wix_event = by_id[wix_id]
            if wix_event is None:
                key = (
                    f"{(row.get('event_name') or '').strip()}|"
                    f"{row.get('start_date', '')}|{row.get('start_time', '')}"
                )
                wix_event = by_key.get(key)
                if wix_event is not None:
                    wix_id = wix_event.get("id") or ""
            return wix_event, wix_id

        # Classes/Settings are only needed to default-fill Ready rows that
        # skipped enrich; fetched lazily at most once.
        defaults_context: Optional[tuple] = None

        def _get_defaults_context() -> tuple:
            nonlocal defaults_context
            if defaults_context is None:
                classes = store.fetch_classes()
                classes_by_page_id = {
                    c["page_id"]: c for c in classes.values() if c.get("page_id")
                }
                settings = store.fetch_settings()
                defaults_context = (classes, classes_by_page_id, settings)
            return defaults_context

        for row in rows:
            name = row.get("event_name") or "(unnamed)"
            page_id = row["page_id"]
            row_status = row.get("status") or ""

            # ------------------------------------------- Cancel/Delete rows
            # Handled before validation: acting on an event only needs a Wix
            # match, not a fully valid row (a TBD-date row can still be
            # cancelled or deleted).
            if row_status == STATUS_CANCEL:
                wix_event, wix_id = _match_raw_row(row)
                if wix_event is None:
                    logger.warning(
                        "  ⚠️  %s: marked Cancel but not found in Wix — nothing to cancel",
                        name,
                    )
                    results["not_found"].append(name)
                    if not dry_run:
                        store.write_sync_result(
                            page_id,
                            error="Not found in Wix — nothing to cancel. "
                            "Set Status to Delete to just mark the row Removed.",
                        )
                    continue

                wix_status = wix_event.get("status") or ""
                if wix_status == "CANCELED":
                    logger.info("  ⏭️  %s (already cancelled in Wix)", name)
                    results["skipped"].append(name)
                    if not dry_run:
                        store.write_sync_result(
                            page_id, status=STATUS_CANCELLED,
                            wix_event_id=wix_id, error=None,
                        )
                    continue
                if wix_status == "DRAFT":
                    logger.warning(
                        "  ⚠️  %s: Wix drafts can't be cancelled — use Delete instead",
                        name,
                    )
                    results["failed"].append(name)
                    if not dry_run:
                        store.write_sync_result(
                            page_id, wix_event_id=wix_id,
                            error="Wix drafts can't be cancelled — set Status to Delete instead.",
                        )
                    continue

                if dry_run:
                    logger.info("  CANCEL: %s (Wix status %s)", name, wix_status)
                    results["cancelled"].append(name)
                    continue

                try:
                    client.cancel_event(wix_id)
                    logger.info("🚫 Cancelled: %s", name)
                    results["cancelled"].append(name)
                    store.write_sync_result(
                        page_id, status=STATUS_CANCELLED,
                        wix_event_id=wix_id, error=None,
                    )
                except Exception as exc:
                    logger.error("  ❌ Failed to cancel %s: %s", name, exc)
                    results["failed"].append(name)
                    store.write_sync_result(
                        page_id, status=STATUS_ERROR, wix_event_id=wix_id,
                        error=f"Cancel failed: {exc}",
                    )
                time.sleep(1)
                continue

            if row_status == STATUS_DELETE:
                wix_event, wix_id = _match_raw_row(row)
                if wix_event is None:
                    # Already gone from Wix (or never created) — intent is met.
                    logger.info(
                        "  🗑️  %s: not found in Wix — marking Removed", name,
                    )
                    results["removed"].append(name)
                    if not dry_run:
                        store.write_sync_result(
                            page_id, status=STATUS_REMOVED, error=None,
                        )
                    continue

                if dry_run:
                    logger.info(
                        "  DELETE: %s (Wix status %s)",
                        name, wix_event.get("status") or "?",
                    )
                    results["removed"].append(name)
                    continue

                if client.delete_event(wix_id, force=True):
                    logger.info("🗑️  Deleted from Wix: %s", name)
                    results["removed"].append(name)
                    store.write_sync_result(
                        page_id, status=STATUS_REMOVED,
                        wix_event_id=wix_id, error=None,
                    )
                else:
                    logger.error("  ❌ Failed to delete %s", name)
                    results["failed"].append(name)
                    store.write_sync_result(
                        page_id, status=STATUS_ERROR, wix_event_id=wix_id,
                        error="Delete failed — see sync logs",
                    )
                time.sleep(1)
                continue

            # Safety net for rows flipped straight to Ready without enrich:
            # fill blanks from the class catalog + Settings defaults so the
            # push uses (and the row shows) the same defaults enrich applies.
            if row_status == STATUS_READY and (row.get("event_name") or "").strip():
                classes, classes_by_page_id, settings = _get_defaults_context()
                klass = _resolve_class_for_row(row, classes, classes_by_page_id)
                fill_props, fill_changes = _apply_row_defaults(
                    row, klass, settings, tz_name=runtime.config.timezone
                )
                if fill_changes:
                    logger.info("  ✨ %s: defaulted %s", name, ", ".join(fill_changes))
                    if not dry_run:
                        store.update_event_fields(page_id, fill_props)

            try:
                record = row_to_event_record(row)
            except ValidationError as exc:
                message = parse_validation_error(exc)
                if row_status == STATUS_PUBLISHED:
                    # Row mirrors an event that is incomplete in Wix itself
                    # (e.g. TBD date). Flag it, don't fail the whole run.
                    logger.warning("  ⚠️  %s: incomplete row — %s", name, message)
                    results["incomplete"].append(name)
                    note = f"Incomplete: {message}"
                    if not dry_run and (row.get("sync_error") or "") != note:
                        store.write_sync_result(page_id, error=note)
                else:
                    logger.error("  ❌ %s: invalid row — %s", name, message)
                    results["failed"].append(name)
                    if not dry_run:
                        store.write_sync_result(
                            page_id, status=STATUS_ERROR, error=f"Invalid row: {message}",
                        )
                continue

            # Match to a live Wix event: id first, then title|date|time.
            wix_event: Optional[Dict[str, Any]] = None
            wix_id = (row.get("wix_event_id") or "").strip()
            if wix_id and wix_id in by_id:
                wix_event = by_id[wix_id]
            if wix_event is None:
                key = f"{record.name.strip()}|{record.start_date}|{record.start_time}"
                wix_event = by_key.get(key)
                if wix_event is not None:
                    wix_id = wix_event.get("id") or ""

            current_hash = record.content_hash()

            # Wix events cancelled outside this pipeline can't be updated or
            # recreated in place — reflect reality on the row and move on.
            if wix_event is not None and (wix_event.get("status") or "") == "CANCELED":
                logger.warning(
                    "  🚫 %s: event is cancelled in Wix — marking row Cancelled", name,
                )
                results["skipped"].append(name)
                if not dry_run:
                    store.write_sync_result(
                        page_id, status=STATUS_CANCELLED, wix_event_id=wix_id,
                        error="Cancelled in Wix. Set Status to Delete to remove it, "
                        "or duplicate the row without the Wix Event ID to recreate.",
                    )
                continue

            # ------------------------------------------------ Published rows
            if row_status == STATUS_PUBLISHED:
                if wix_event is None:
                    logger.warning(
                        "  ⚠️  %s: marked Published but not found in Wix — "
                        "flip Status to Ready to recreate it", name,
                    )
                    results["not_found"].append(name)
                    if not dry_run:
                        store.write_sync_result(
                            page_id,
                            error="Not found in Wix (deleted?). Set Status to Ready to recreate.",
                        )
                    continue

                had_error = bool((row.get("sync_error") or "").strip())
                if row.get("synced_hash") == current_hash and not had_error:
                    logger.info("  ⏭️  %s (no changes)", name)
                    results["skipped"].append(name)
                    continue

                plan = compute_event_update_plan(client, runtime, record, wix_id, wix_event)
                if not plan["any_changes"]:
                    logger.info("  ⏭️  %s (Wix already matches)", name)
                    results["skipped"].append(name)
                    if not dry_run:
                        store.write_sync_result(
                            page_id, wix_event_id=wix_id,
                            synced_hash=current_hash, error=None,
                        )
                    continue

                if dry_run:
                    log_update_plan_dry_run(record, plan)
                    results["updated"].append(name)
                    continue

                logger.info(
                    "♻️  Updating: %s on %s [%s]",
                    name, record.start_date, plan["change_desc"],
                )
                if plan["event_changed"]:
                    _log_event_diff(name, plan["event_diffs"])

                if apply_event_update_plan(client, runtime, record, wix_id, wix_event, plan):
                    results["updated"].append(name)
                    _converge_hosted_image(store, runtime, record, page_id)
                    store.write_sync_result(
                        page_id, status=STATUS_PUBLISHED, wix_event_id=wix_id,
                        synced_hash=record.content_hash(), error=None,
                    )
                else:
                    results["failed"].append(name)
                    store.write_sync_result(
                        page_id, status=STATUS_ERROR, wix_event_id=wix_id,
                        error="Update failed — see sync logs",
                    )
                time.sleep(1)
                continue

            # ----------------------------------------------------- Ready rows
            if wix_event is not None:
                wix_status = wix_event.get("status") or ""

                if wix_status == "DRAFT" and not draft:
                    if dry_run:
                        logger.info("  PUBLISH: %s (existing Wix draft)", name)
                        results["published"].append(name)
                        continue
                    try:
                        client.publish_event(wix_id)
                        logger.info("📢 Published draft: %s", name)
                    except Exception as exc:
                        logger.error("  ❌ Failed to publish draft %s: %s", name, exc)
                        results["failed"].append(name)
                        store.write_sync_result(
                            page_id, status=STATUS_ERROR, wix_event_id=wix_id,
                            error=f"Publish failed: {exc}",
                        )
                        continue
                    if auto_create_tickets and record.registration_type == "TICKETING":
                        if record.ticket_name:
                            _create_tickets_from_config(client, wix_id, record)
                        elif record.ticket_price > 0:
                            _ensure_ticket_definition(client, wix_id, record)
                    results["published"].append(name)
                    store.write_sync_result(
                        page_id, status=STATUS_PUBLISHED, wix_event_id=wix_id,
                        synced_hash=current_hash, error=None,
                    )
                    time.sleep(1)
                    continue

                # Already exists (live, or draft while in --draft mode): update.
                plan = compute_event_update_plan(client, runtime, record, wix_id, wix_event)
                if dry_run:
                    if plan["any_changes"]:
                        log_update_plan_dry_run(record, plan)
                        results["updated"].append(name)
                    else:
                        logger.info("  SKIP: %s (already in Wix, no changes)", name)
                        results["skipped"].append(name)
                    continue

                ok = True
                if plan["any_changes"]:
                    logger.info(
                        "♻️  Updating existing: %s [%s]", name, plan["change_desc"],
                    )
                    ok = apply_event_update_plan(client, runtime, record, wix_id, wix_event, plan)
                else:
                    logger.info("  🔗 %s already in Wix — linking row", name)

                if ok and auto_create_tickets and record.registration_type == "TICKETING" and wix_status != "DRAFT":
                    if record.ticket_name:
                        _create_tickets_from_config(client, wix_id, record)
                    elif record.ticket_price > 0:
                        _ensure_ticket_definition(client, wix_id, record)

                new_status = STATUS_READY if wix_status == "DRAFT" else STATUS_PUBLISHED
                if ok:
                    results["updated"].append(name)
                    _converge_hosted_image(store, runtime, record, page_id)
                    store.write_sync_result(
                        page_id, status=new_status, wix_event_id=wix_id,
                        synced_hash=record.content_hash(), error=None,
                    )
                else:
                    results["failed"].append(name)
                    store.write_sync_result(
                        page_id, status=STATUS_ERROR, wix_event_id=wix_id,
                        error="Update failed — see sync logs",
                    )
                time.sleep(1)
                continue

            # Brand new event.
            if dry_run:
                logger.info(
                    "  CREATE: %s on %s %s%s", name, record.start_date,
                    record.start_time, " (as draft)" if draft else "",
                )
                results["created"].append(name)
                continue

            logger.info("➕ Creating: %s on %s", name, record.start_date)
            new_id = create_wix_event(
                record, runtime=runtime,
                auto_create_tickets=auto_create_tickets, draft=draft,
            )
            if new_id:
                results["created"].append(name)
                _converge_hosted_image(store, runtime, record, page_id)
                store.write_sync_result(
                    page_id,
                    status=STATUS_READY if draft else STATUS_PUBLISHED,
                    wix_event_id=new_id,
                    synced_hash=record.content_hash(),
                    error="Created as Wix draft — run sync without --draft to publish" if draft else None,
                )
            else:
                results["failed"].append(name)
                store.write_sync_result(
                    page_id, status=STATUS_ERROR,
                    error="Create failed — see sync logs",
                )
            time.sleep(1)

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
            logger.info("\n♻️  Updated: %d", len(results["updated"]))
            for n in results["updated"]:
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
            logger.warning("\n⚠️  Published rows missing from Wix: %d", len(results["not_found"]))
            for n in results["not_found"]:
                logger.warning("  • %s", n)
        if results["failed"]:
            logger.error("\n❌ Failed: %d", len(results["failed"]))
            for n in results["failed"]:
                logger.error("  • %s", n)

        stats = runtime.cache_stats
        logger.info("\n🧮 Cache summary:")
        logger.info(
            "   Google Drive - hits: %s, misses: %s",
            stats["drive_hits"], stats["drive_misses"],
        )
        logger.info(
            "   Wix Media    - hits: %s, uploads: %s",
            stats["wix_hits"], stats["wix_uploads"],
        )

        return len(results["failed"]) == 0
    except Exception as exc:
        logger.error("Fatal error during sync: %s", exc)
        return False


# ---------------------------------------------------------------------------
# Site config (Notion-backed tax-by-location round-trip)
# ---------------------------------------------------------------------------


def pull_site_config_notion(runtime: SyncRuntime) -> bool:
    """Pull Wix tax regions/mappings into the Notion Site Config DB."""
    from .orchestrator import (
        _blank_region_site_row,
        _select_default_tax_group_id,
        _site_config_row_sort_key,
        _tax_mapping_to_site_row,
    )

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

        for row in rows:
            store.upsert_site_config_row(row)
            logger.info(
                "  ✅ %s — %s%%",
                row.get("jurisdiction") or row.get("region") or "(unknown)",
                row.get("tax_rate") or "unset",
            )

        logger.info("\n📊 Wrote %d site config row(s) to Notion", len(rows))
        return True
    except Exception as exc:
        logger.error("Failed to pull site config: %s", exc)
        return False


def push_site_config_notion(runtime: SyncRuntime, dry_run: bool = False) -> bool:
    """Push tax edits from the Notion Site Config DB back to Wix."""
    from .orchestrator import process_site_config_rows

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
        logger.error("Fatal error during site config push: %s", exc)
        return False
