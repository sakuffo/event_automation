"""Dev helper: set the Status of a Notion Events row by page id.

Usage: python scripts/set_event_status.py <page_id> <status>
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from event_sync.config import load_config
from event_sync.logging_utils import configure_logging
from event_sync.notion_store import EventProps, NotionStore, p_select


def main() -> int:
    configure_logging("INFO")
    if len(sys.argv) < 3:
        print("Usage: python scripts/set_event_status.py <page_id> <status>")
        return 1
    page_id, status = sys.argv[1], sys.argv[2]
    store = NotionStore(load_config())
    store.update_page(page_id, {EventProps.STATUS: p_select(status)})
    print(f"Set {page_id} status to {status}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
