"""Archive Notion rows for secondary occurrences of Wix recurring series.

Pull keeps one Notion row per Wix-native recurring series (the occurrence Wix
marks ``RECURRING_UPCOMING``), but rows created before that rule — one per
occurrence of series like Fringe or Tinker Tuesday — are still cluttering the
Event Scheduling DB. This one-off archives them: rows in a code-owned status
(``Published``/``Cancelled``) whose ``Wix Event ID`` points at a secondary
recurring occurrence are moved to Notion's trash (restorable from the UI).
Rows in human statuses are reported but never touched.

Usage:
  python scripts/archive_recurring_rows.py           # dry run (default)
  python scripts/archive_recurring_rows.py --apply   # actually archive
"""

import argparse
import sys
from pathlib import Path
from typing import Any, Dict

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from event_sync.config import load_config
from event_sync.logging_utils import configure_logging, get_logger
from event_sync.notion_store import STATUS_CANCELLED, STATUS_PUBLISHED
from event_sync.runtime import SyncRuntime
from event_sync.wix_mapping import is_secondary_recurring_occurrence


logger = get_logger(__name__)

CODE_OWNED_STATUSES = {STATUS_PUBLISHED, STATUS_CANCELLED}


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Archive Notion rows pointing at secondary occurrences of Wix "
            "recurring series (one row per series stays: the next upcoming "
            "occurrence)"
        )
    )
    parser.add_argument(
        "--apply", action="store_true",
        help="archive the rows (default is a dry run)",
    )
    args = parser.parse_args()

    configure_logging("INFO")
    config = load_config()
    config.ensure_notion_valid()
    config.ensure_wix_valid()
    runtime = SyncRuntime(config)

    if not args.apply:
        logger.info("🔍 DRY RUN — pass --apply to archive rows\n")

    client = runtime.get_wix_client()
    store = runtime.get_notion_store()

    # Index every Wix event (all statuses — old clutter rows point at ended
    # occurrences too) by id, keeping only the recurrence verdict.
    secondary_by_id: Dict[str, bool] = {}
    for event in client.iter_events(page_size=100, include_drafts=False):
        event_id = event.get("id") or ""
        if event_id:
            secondary_by_id[event_id] = is_secondary_recurring_occurrence(event)
    logger.info("Indexed %d Wix event(s)\n", len(secondary_by_id))

    rows = store.fetch_event_rows()
    counts = {"archived": 0, "kept": 0, "human": 0, "failed": 0}

    for row in rows:
        wix_id = (row.get("wix_event_id") or "").strip()
        if not wix_id or not secondary_by_id.get(wix_id, False):
            counts["kept"] += 1
            continue

        name = row.get("event_name") or "(unnamed)"
        label = f"{name} ({row.get('start_date') or 'no date'})"
        status = (row.get("status") or "").strip()
        if status not in CODE_OWNED_STATUSES:
            logger.warning(
                "  ✋ %s: recurring occurrence but row status is %s — "
                "left for a human to archive", label, status or "(blank)",
            )
            counts["human"] += 1
            continue

        if not args.apply:
            logger.info("  ARCHIVE: %s [%s]", label, status)
            counts["archived"] += 1
            continue
        try:
            store.archive_page(row["page_id"])
            logger.info("  🗄️  Archived: %s [%s]", label, status)
            counts["archived"] += 1
        except Exception as exc:
            logger.error("  ❌ Failed: %s — %s", label, exc)
            counts["failed"] += 1

    label = "Would archive" if not args.apply else "Archived"
    logger.info(
        "\n📈 Done: %s %d row(s), %d kept, %d left to humans, %d failed",
        label, counts["archived"], counts["kept"], counts["human"],
        counts["failed"],
    )
    return 0 if counts["failed"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
