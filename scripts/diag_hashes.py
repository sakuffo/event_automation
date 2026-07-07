"""Dev diagnostic: compare stored synced_hash vs recomputed hash per row."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from event_sync.config import load_config
from event_sync.logging_utils import configure_logging
from event_sync.models import ValidationError
from event_sync.notion_store import EventProps, NotionStore, row_to_event_record, v_date_raw


def main() -> int:
    configure_logging("WARNING")
    config = load_config()
    store = NotionStore(config)

    db_id = config.notion_events_db_id
    for page in store.iter_pages(db_id):
        from event_sync.notion_store import event_page_to_row

        row = event_page_to_row(page, config.timezone)
        raw_start, raw_end, raw_tz = v_date_raw(page, EventProps.DATE)
        try:
            record = row_to_event_record(row)
            fresh = record.content_hash()
        except ValidationError:
            fresh = "(invalid)"
        stored = row.get("synced_hash") or "(none)"
        marker = "OK " if fresh == stored else "DIFF"
        print(
            f"{marker} {row['event_name'][:38]:38} stored={stored} fresh={fresh}\n"
            f"     raw_date start={raw_start!r} end={raw_end!r} tz={raw_tz!r}\n"
            f"     parsed  {row['start_date']} {row['start_time']} -> {row['end_date']} {row['end_time']}"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
