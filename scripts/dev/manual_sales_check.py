#!/usr/bin/env python3
"""Manual check: do Published rows carry the pull-only sales columns?

Read-only against the Notion DBs in .env (run against dev). Prints each
Published row's Tickets Sold / By Type / Revenue plus the dashboard page id.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")

from event_sync.config import load_config
from event_sync.notion_store import STATUS_PUBLISHED, NotionStore


def main() -> int:
    store = NotionStore(load_config())
    rows = store.fetch_event_rows(statuses=[STATUS_PUBLISHED])
    with_sales = [r for r in rows if r.get("tickets_sold") is not None]
    print(f"{len(with_sales)}/{len(rows)} Published rows have Tickets Sold")
    for row in with_sales[:10]:
        print(
            f"  {row['event_name'][:42]:42} "
            f"sold={row['tickets_sold']} "
            f"by_type={row['tickets_sold_by_type']!r} "
            f"revenue={row['revenue']}"
        )
    settings = store.fetch_settings()
    print("dashboard_page_id =", settings.get("dashboard_page_id"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
