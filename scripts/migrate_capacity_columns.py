"""Migrate event-level capacity into the semicolon Ticket Capacities columns.

The pipeline is dropping the Events ``Capacity`` number column and the Catalog
``Default Capacity`` number column: per-ticket inventory lives solely in
``Ticket Capacities`` / ``Default Ticket Capacities`` (semicolon rich text; a
single value applies to every ticket type). This one-off moves the numbers
across before the columns are deleted:

  1. Catalog: ``Default Capacity`` -> ``Default Ticket Capacities`` where the
     target is blank.
  2. Event Scheduling: ``Capacity`` -> ``Ticket Capacities`` on human-status
     rows (not Published/Cancelled/Removed — those are code-owned and refresh
     from Wix) where the target is blank.
  3. Settings: reword the ``default_capacity`` Notes to describe its new job
     (fallback ticket capacity when Ticket Capacities is blank).
  4. ``--drop-columns``: delete the two number properties from the database
     schemas (run only after the new code is deployed).

Usage:
  python scripts/migrate_capacity_columns.py                  # dry run (default)
  python scripts/migrate_capacity_columns.py --apply          # write steps 1-3
  python scripts/migrate_capacity_columns.py --apply --drop-columns
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from event_sync.config import load_config
from event_sync.logging_utils import configure_logging, get_logger
from event_sync.notion_store import (
    STATUS_CANCELLED,
    STATUS_PUBLISHED,
    STATUS_REMOVED,
    EventProps,
    NotionStore,
    TemplateProps,
    p_rich_text,
    v_number,
    v_plain_text,
    v_select,
)

logger = get_logger(__name__)

# The retired columns, referenced by literal name: the pipeline code no longer
# knows about them (that's the point of this migration), so the mappers won't
# surface their values.
RETIRED_EVENT_CAPACITY = "Capacity"
RETIRED_TEMPLATE_CAPACITY = "Default Capacity"

# Rows in these statuses are code-owned (refreshed from Wix); their Capacity
# column is stale bookkeeping, not human intent — never copied.
CODE_OWNED_STATUSES = {STATUS_PUBLISHED, STATUS_CANCELLED, STATUS_REMOVED}

NEW_DEFAULT_CAPACITY_NOTES = (
    "Fallback ticket capacity (per ticket type) used when a ticketed row's "
    "Ticket Capacities is blank"
)


def _capacity_text(value) -> str:
    """Render a Notion number as the positive-int string the parser expects.

    Returns "" for non-positive values — the old Capacity validator rejected
    those at sync time, so copying one across would just smuggle bad data
    into the new column (the enrich default fills the gap instead).
    """
    number = int(round(float(value)))
    return str(number) if number > 0 else ""


def migrate_templates(store: NotionStore, apply: bool) -> int:
    """Default Capacity -> first slot of Default Ticket Capacities."""
    copied = 0
    db_id = store.config.notion_catalog_db_id
    pages = sorted(
        store.iter_pages(db_id),
        key=lambda p: v_plain_text(p, TemplateProps.NAME).lower(),
    )
    for page in pages:
        name = v_plain_text(page, TemplateProps.NAME).strip() or "(untitled)"
        cap = v_number(page, RETIRED_TEMPLATE_CAPACITY)
        if cap is None:
            continue
        existing = v_plain_text(page, TemplateProps.DEFAULT_TICKET_CAPACITIES).strip()
        if existing:
            logger.info(
                "  ⏭️  %s — Default Ticket Capacities already set (%r), leaving it",
                name, existing,
            )
            continue
        value = _capacity_text(cap)
        if not value:
            logger.warning(
                "  ⚠️  %s — skipping non-positive Default Capacity %s", name, cap,
            )
            continue
        copied += 1
        if not apply:
            logger.info("  COPY: %s — Default Capacity %s -> Default Ticket Capacities %r", name, value, value)
            continue
        store.update_page(
            page["id"],
            {TemplateProps.DEFAULT_TICKET_CAPACITIES: p_rich_text(value)},
        )
        logger.info("  ✅ %s — Default Ticket Capacities = %r", name, value)
    return copied


def migrate_event_rows(store: NotionStore, apply: bool) -> int:
    """Capacity -> Ticket Capacities on human-status rows with a blank target."""
    copied = 0
    db_id = store.config.notion_event_scheduling_db_id
    for page in store.iter_pages(db_id):
        status = v_select(page, EventProps.STATUS).strip()
        if status in CODE_OWNED_STATUSES:
            continue
        cap = v_number(page, RETIRED_EVENT_CAPACITY)
        if cap is None:
            continue
        if v_plain_text(page, EventProps.TICKET_CAPACITIES).strip():
            continue
        value = _capacity_text(cap)
        label = v_plain_text(page, EventProps.NAME).strip() or "(untitled)"
        if not value:
            logger.warning(
                "  ⚠️  %s — skipping non-positive Capacity %s", label, cap,
            )
            continue
        copied += 1
        if not apply:
            logger.info(
                "  COPY: %s [%s] — Capacity %s -> Ticket Capacities %r",
                label, status or "no status", value, value,
            )
            continue
        store.update_page(
            page["id"],
            {EventProps.TICKET_CAPACITIES: p_rich_text(value)},
        )
        logger.info("  ✅ %s — Ticket Capacities = %r", label, value)
    return copied


def update_settings_notes(store: NotionStore, apply: bool) -> bool:
    """Reword the default_capacity Notes for its new role (value unchanged)."""
    settings = store.fetch_settings()
    if "default_capacity" not in settings:
        logger.info("  ⏭️  No default_capacity Settings row — nothing to reword")
        return False
    value = settings["default_capacity"]
    if not apply:
        logger.info(
            "  UPDATE NOTES: default_capacity (value stays %r) -> %r",
            value, NEW_DEFAULT_CAPACITY_NOTES,
        )
        return True
    store.upsert_setting("default_capacity", value, notes=NEW_DEFAULT_CAPACITY_NOTES)
    logger.info("  ✅ default_capacity Notes reworded (value stays %r)", value)
    return True


def drop_columns(store: NotionStore, apply: bool) -> None:
    """Delete Events.Capacity and Catalog.Default Capacity from the schemas."""
    targets = [
        (store.config.notion_event_scheduling_db_id, RETIRED_EVENT_CAPACITY),
        (store.config.notion_catalog_db_id, RETIRED_TEMPLATE_CAPACITY),
    ]
    for db_id, prop_name in targets:
        ds_id = store.data_source_id(db_id)
        current = store.client.data_sources.retrieve(data_source_id=ds_id)
        if prop_name not in current.get("properties", {}):
            logger.info("  ⏭️  %r already absent from %s", prop_name, db_id)
            continue
        if not apply:
            logger.info("  DROP: property %r from %s", prop_name, db_id)
            continue
        store.client.data_sources.update(
            data_source_id=ds_id, properties={prop_name: None}
        )
        after = store.client.data_sources.retrieve(data_source_id=ds_id)
        if prop_name in after.get("properties", {}):
            raise RuntimeError(f"{prop_name!r} still present after delete on {db_id}")
        logger.info("  🗑️  Dropped %r from %s", prop_name, db_id)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Move Capacity/Default Capacity into the Ticket Capacities columns"
    )
    parser.add_argument(
        "--apply", action="store_true",
        help="write changes to Notion (default is a dry run)",
    )
    parser.add_argument(
        "--drop-columns", action="store_true",
        help="also delete the Capacity/Default Capacity properties — run only "
        "after the code that stops using them is deployed",
    )
    args = parser.parse_args()

    configure_logging("INFO")
    config = load_config()
    # Only three databases are touched — don't demand NOTION_SITE_CONFIG_DB_ID.
    config.ensure_notion_valid(require_databases=False)
    missing = [
        name for name, value in (
            ("NOTION_EVENT_SCHEDULING_DB_ID", config.notion_event_scheduling_db_id),
            ("NOTION_CATALOG_DB_ID", config.notion_catalog_db_id),
            ("NOTION_SETTINGS_DB_ID", config.notion_settings_db_id),
        ) if not value
    ]
    if missing:
        logger.error("Missing in .env: %s", ", ".join(missing))
        return 1
    store = NotionStore(config)

    if not args.apply:
        logger.info("🔍 DRY RUN — pass --apply to write changes\n")

    logger.info("1️⃣  Catalog: Default Capacity -> Default Ticket Capacities")
    templates = migrate_templates(store, args.apply)

    logger.info("\n2️⃣  Event Scheduling: Capacity -> Ticket Capacities (human rows)")
    rows = migrate_event_rows(store, args.apply)

    logger.info("\n3️⃣  Settings: reword default_capacity Notes")
    update_settings_notes(store, args.apply)

    if args.drop_columns:
        logger.info("\n4️⃣  Dropping retired columns")
        drop_columns(store, args.apply)

    verb = "Copied" if args.apply else "Would copy"
    logger.info(
        "\n📈 Done: %s %d template value(s) and %d event row value(s)%s",
        verb, templates, rows, "" if args.apply else " (dry run)",
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
