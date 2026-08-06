"""Auto-generated Events Dashboard page in Notion.

``sync`` rewrites one Notion page per run with a read-only snapshot of the
Published rows: an upcoming-events table (sold / capacity / revenue, with
the old GitHub Pages dashboard's 70%/100% color thresholds) and a by-month
sales summary. The page is display-only — nothing on it feeds back into the
sync or push flows, and building it never talks to Wix.

Page identity lives in the Settings DB (``dashboard_page_id``, auto-managed):
blank means "create the page under ``NOTION_PARENT_PAGE_ID`` on the next
sync and remember it". Clearing the setting makes sync create a fresh page.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from .logging_utils import get_logger
from .models import managed_ticket_capacities
from .notion_store import STATUS_PUBLISHED, NotionStore, NotionStoreError

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover - Python < 3.9
    ZoneInfo = None


logger = get_logger(__name__)

DASHBOARD_TITLE = "Events Dashboard"
DASHBOARD_PAGE_SETTING = "dashboard_page_id"

# Nested table rows count toward Notion's 100-blocks-per-append limit, so
# both tables are capped (with a truncation note) to keep appends safe.
MAX_UPCOMING_ROWS = 60
MAX_MONTH_ROWS = 24

# Same thresholds as the old GitHub Pages dashboard's capacity colors.
PCT_AMBER = 70
PCT_RED = 100


# ---------------------------------------------------------------------------
# Pure block builders (no I/O — unit-testable)
# ---------------------------------------------------------------------------


def _rt(text: str, color: str = "default", bold: bool = False) -> Dict[str, Any]:
    """One Notion rich-text item."""
    item: Dict[str, Any] = {"type": "text", "text": {"content": text}}
    if color != "default" or bold:
        item["annotations"] = {"color": color, "bold": bold}
    return item


def _paragraph(text: str, color: str = "default") -> Dict[str, Any]:
    return {
        "object": "block",
        "type": "paragraph",
        "paragraph": {"rich_text": [_rt(text, color=color)] if text else []},
    }


def _heading(text: str, level: int = 2) -> Dict[str, Any]:
    kind = f"heading_{level}"
    return {
        "object": "block",
        "type": kind,
        kind: {"rich_text": [_rt(text)]},
    }


def _table_row(cells: List[List[Dict[str, Any]]]) -> Dict[str, Any]:
    return {
        "object": "block",
        "type": "table_row",
        "table_row": {"cells": cells},
    }


def _table(
    header: List[str], rows: List[List[List[Dict[str, Any]]]]
) -> Dict[str, Any]:
    children = [_table_row([[_rt(h, bold=True)] for h in header])]
    children.extend(_table_row(cells) for cells in rows)
    return {
        "object": "block",
        "type": "table",
        "table": {
            "table_width": len(header),
            "has_column_header": True,
            "has_row_header": False,
            "children": children,
        },
    }


def _money(value: Optional[float]) -> str:
    if value is None:
        return "—"
    return f"${value:,.2f}"


def _row_capacity_total(row: Dict[str, Any]) -> Optional[int]:
    """Total sellable inventory across a row's ticket types, if known.

    Uses the managed-capacity semantics (explicit positive tokens only,
    single-value-covers-all tail rule); any unmanaged slot makes the total
    unknown rather than guessing.
    """
    names = [
        n.strip() for n in (row.get("ticket_name") or "").split(";") if n.strip()
    ]
    count = len(names) if names else 1
    values = managed_ticket_capacities(row.get("ticket_capacity"), count)
    if any(v is None for v in values):
        return None
    return sum(values)


def _sold_count(row: Dict[str, Any]) -> Optional[int]:
    value = row.get("tickets_sold")
    if value is None:
        return None
    return int(round(float(value)))


def _percent_cell(
    sold: Optional[int], capacity: Optional[int]
) -> List[Dict[str, Any]]:
    if sold is None or not capacity:
        return [_rt("—")]
    pct = round(100 * sold / capacity)
    if pct >= PCT_RED:
        color = "red"
    elif pct >= PCT_AMBER:
        color = "orange"
    else:
        color = "green"
    return [_rt(f"{pct}%", color=color, bold=pct >= PCT_AMBER)]


def _row_start_date(row: Dict[str, Any]) -> str:
    return (row.get("start_date") or "").strip()


def _month_key(iso_date: str) -> str:
    return iso_date[:7] if len(iso_date) >= 7 else ""


def _month_label(month_key: str) -> str:
    try:
        return datetime.strptime(month_key, "%Y-%m").strftime("%B %Y")
    except ValueError:
        return month_key


def build_dashboard_blocks(
    rows: List[Dict[str, Any]],
    now: Optional[datetime] = None,
) -> List[Dict[str, Any]]:
    """Build the dashboard page's block payloads from Published event rows.

    Pure function: rows in (as returned by ``fetch_event_rows``), Notion
    block dicts out.
    """
    now = now or datetime.now()
    today = now.strftime("%Y-%m-%d")

    dated = [r for r in rows if _row_start_date(r)]
    upcoming = sorted(
        (r for r in dated if _row_start_date(r) >= today),
        key=_row_start_date,
    )

    blocks: List[Dict[str, Any]] = [
        _paragraph(
            f"Auto-generated by sync — do not edit. "
            f"Last updated {now.strftime('%Y-%m-%d %H:%M')}."
        ),
        _heading(f"Upcoming events ({len(upcoming)})"),
    ]

    if upcoming:
        table_rows = []
        total_sold = 0
        total_revenue = 0.0
        for row in upcoming[:MAX_UPCOMING_ROWS]:
            sold = _sold_count(row)
            capacity = _row_capacity_total(row)
            time_part = (row.get("start_time") or "").strip()
            date_label = _row_start_date(row) + (
                f" {time_part}" if time_part else ""
            )
            by_type = (row.get("tickets_sold_by_type") or "").strip()
            multi_type = ";" in by_type
            sold_label = "—" if sold is None else (
                f"{sold} ({by_type})" if multi_type else str(sold)
            )
            revenue = row.get("revenue")
            table_rows.append([
                [_rt(date_label)],
                [_rt((row.get("event_name") or "(unnamed)").strip())],
                [_rt(sold_label)],
                [_rt("—" if capacity is None else str(capacity))],
                _percent_cell(sold, capacity),
                [_rt(_money(revenue))],
            ])
            total_sold += sold or 0
            total_revenue += float(revenue or 0.0)
        blocks.append(
            _table(["Date", "Event", "Sold", "Capacity", "% Sold", "Revenue"],
                   table_rows)
        )
        if len(upcoming) > MAX_UPCOMING_ROWS:
            blocks.append(_paragraph(
                f"… and {len(upcoming) - MAX_UPCOMING_ROWS} more events "
                f"(showing the first {MAX_UPCOMING_ROWS})."
            ))
        blocks.append(_paragraph(
            f"Upcoming totals: {total_sold} tickets sold · "
            f"{_money(total_revenue)} revenue",
        ))
    else:
        blocks.append(_paragraph("No upcoming published events."))

    # By-month summary over every dated Published row (past months included
    # while their rows remain in the DB).
    by_month: Dict[str, Dict[str, float]] = {}
    for row in dated:
        month = _month_key(_row_start_date(row))
        if not month:
            continue
        bucket = by_month.setdefault(
            month, {"events": 0, "sold": 0, "revenue": 0.0}
        )
        bucket["events"] += 1
        bucket["sold"] += _sold_count(row) or 0
        bucket["revenue"] += float(row.get("revenue") or 0.0)

    if by_month:
        blocks.append(_heading("By month"))
        months = sorted(by_month)[-MAX_MONTH_ROWS:]
        month_rows = [
            [
                [_rt(_month_label(month))],
                [_rt(str(int(by_month[month]["events"])))],
                [_rt(str(int(by_month[month]["sold"])))],
                [_rt(_money(by_month[month]["revenue"]))],
            ]
            for month in months
        ]
        blocks.append(
            _table(["Month", "Events", "Tickets Sold", "Revenue"], month_rows)
        )

    return blocks


# ---------------------------------------------------------------------------
# Page refresh (Notion I/O)
# ---------------------------------------------------------------------------


def refresh_dashboard(runtime) -> bool:
    """Rewrite the Events Dashboard page from the current Published rows.

    Contained: failures are logged and return False without raising, so a
    dashboard hiccup can never fail the sync that feeds it. Never touches
    Wix.
    """
    store: NotionStore = runtime.get_notion_store()
    try:
        settings = store.fetch_settings()
        page_id = (settings.get(DASHBOARD_PAGE_SETTING) or "").strip()
        rows = store.fetch_event_rows(statuses=[STATUS_PUBLISHED])
        blocks = build_dashboard_blocks(rows, now=_local_now(runtime))

        created_this_run = False
        if not page_id:
            page_id = _create_dashboard_page(runtime, store)
            if not page_id:
                return False
            created_this_run = True

        try:
            store.replace_page_blocks(page_id, blocks)
        except NotionStoreError:
            if created_this_run:
                raise
            # The remembered page was deleted/archived — recreate once.
            logger.info(
                "Dashboard page %s is gone — creating a fresh one", page_id,
            )
            page_id = _create_dashboard_page(runtime, store)
            if not page_id:
                return False
            store.replace_page_blocks(page_id, blocks)

        logger.info("📊 Events Dashboard refreshed (%s)", page_id)
        return True
    except Exception as exc:
        logger.warning("⚠️  Could not refresh the Events Dashboard: %s", exc)
        return False


def _local_now(runtime) -> datetime:
    tz_name = getattr(runtime.config, "timezone", None)
    if tz_name and ZoneInfo is not None:
        try:
            return datetime.now(ZoneInfo(tz_name)).replace(tzinfo=None)
        except Exception:  # pragma: no cover - missing tzdata
            pass
    return datetime.now()


def _create_dashboard_page(runtime, store: NotionStore) -> str:
    """Create the dashboard page and remember its id in Settings."""
    parent = (runtime.config.notion_parent_page_id or "").strip()
    if not parent:
        logger.warning(
            "⚠️  Cannot create the Events Dashboard: NOTION_PARENT_PAGE_ID "
            "is not set (add it to .env, or set the dashboard_page_id "
            "Setting to an existing page id)."
        )
        return ""
    page_id = store.create_child_page(parent, DASHBOARD_TITLE)
    store.upsert_setting(
        DASHBOARD_PAGE_SETTING,
        page_id,
        notes=(
            "Auto-managed by sync: page id of the generated Events "
            "Dashboard. Clear to have the next sync create a fresh page."
        ),
    )
    return page_id


__all__ = [
    "DASHBOARD_PAGE_SETTING",
    "DASHBOARD_TITLE",
    "build_dashboard_blocks",
    "refresh_dashboard",
]
