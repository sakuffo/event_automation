"""Dev helper: create a bare Idea row in the Notion Event Scheduling DB.

Simulates a teammate dropping in a placeholder (just a class name + date) so
enrich/sync can be exercised. Not part of the production pipeline.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from event_sync.config import load_config
from event_sync.logging_utils import configure_logging
from event_sync.notion_store import (
    EventProps,
    NotionStore,
    STATUS_IDEA,
    p_date,
    p_select,
    p_title,
)


def main() -> int:
    configure_logging("INFO")
    config = load_config()
    store = NotionStore(config)

    name = sys.argv[1] if len(sys.argv) > 1 else "Your First Rope Class"
    date = sys.argv[2] if len(sys.argv) > 2 else "2026-08-12"
    start = sys.argv[3] if len(sys.argv) > 3 else "19:00"
    end = sys.argv[4] if len(sys.argv) > 4 else "22:00"

    props = {
        EventProps.NAME: p_title(name),
        EventProps.STATUS: p_select(STATUS_IDEA),
        EventProps.DATE: p_date(date, start, date, end, tz_name=config.timezone),
    }
    page = store.create_page(config.notion_event_scheduling_db_id, props)
    print(f"Created Idea row '{name}' on {date} {start}-{end}: {page.get('id')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
