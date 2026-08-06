"""Pull-only sales columns and the generated Events Dashboard.

Pins the sales-column invariants: Tickets Sold / Tickets Sold By Type /
Revenue are written only by the sync/pull read path (never hashed, never in
any Wix payload — pull-only by construction), sales drift alone triggers a
bookkeeping write on an otherwise-unchanged Published row, and dry runs
write nothing. Also covers the pure converters and the dashboard block
builder.
"""

from types import SimpleNamespace
from typing import Any, Dict, List, Optional

from event_sync import notion_orchestrator
from event_sync.models import EventRecord
from event_sync.notion_dashboard import build_dashboard_blocks
from event_sync.notion_orchestrator import notion_sync_events
from event_sync.notion_store import (
    EventProps,
    event_page_to_row,
    event_properties_from_record,
    row_to_event_record,
)
from event_sync.wix_mapping import (
    build_wix_event_payload,
    order_summary_revenue,
    ticket_sales_summary,
)

from datetime import datetime


TZ = "America/Toronto"


# ---------------------------------------------------------------------------
# Pure converters
# ---------------------------------------------------------------------------


def ticket_def(sold: Optional[int], name: str = "GA") -> Dict[str, Any]:
    td: Dict[str, Any] = {"name": name}
    if sold is not None:
        td["salesDetails"] = {"soldCount": sold}
    return td


def test_ticket_sales_summary_empty_defs_is_unknown():
    assert ticket_sales_summary([]) == (None, None)


def test_ticket_sales_summary_totals_and_positional_breakdown():
    defs = [ticket_def(12, "GA"), ticket_def(3, "VIP")]
    assert ticket_sales_summary(defs) == (15, "12; 3")


def test_ticket_sales_summary_missing_sales_details_counts_zero():
    defs = [ticket_def(None, "GA"), ticket_def(5, "VIP")]
    assert ticket_sales_summary(defs) == (5, "0; 5")


def test_order_summary_revenue_none_stays_unknown():
    assert order_summary_revenue(None) is None


def test_order_summary_revenue_no_orders_is_zero():
    assert order_summary_revenue({"sales": []}) == 0.0


def test_order_summary_revenue_sums_currency_entries():
    summary = {
        "sales": [
            {"revenue": {"value": "420.50", "currency": "CAD"}},
            {"revenue": {"value": "10", "currency": "USD"}},
            {"revenue": {}},
        ]
    }
    assert order_summary_revenue(summary) == 430.5


# ---------------------------------------------------------------------------
# Pull-only guarantees: never hashed, never in a Wix payload
# ---------------------------------------------------------------------------


def build_record(**overrides) -> EventRecord:
    payload = {
        "name": "Rope Lab",
        "start_date": "2026-08-12",
        "start_time": "19:00",
        "end_date": "2026-08-12",
        "end_time": "22:00",
        "location": "Studio",
        "ticket_price": 35.0,
        "registration_type": "TICKETS",
    }
    payload.update(overrides)
    return EventRecord(**payload)


def test_sales_fields_never_change_content_hash():
    plain = build_record()
    with_sales = build_record(
        tickets_sold=42, tickets_sold_by_type="40; 2", revenue=1234.5,
        hidden_from_schedule=True,
    )
    assert plain.content_hash() == with_sales.content_hash()
    for field in ("tickets_sold", "tickets_sold_by_type", "revenue"):
        assert field not in EventRecord.HASHED_FIELDS


def test_sales_fields_never_reach_the_wix_payload():
    runtime = SimpleNamespace(config=SimpleNamespace(timezone=TZ))
    plain = build_wix_event_payload(build_record(), runtime)
    with_sales = build_wix_event_payload(
        build_record(
            tickets_sold=42, tickets_sold_by_type="40; 2", revenue=1234.5,
        ),
        runtime,
    )
    assert plain == with_sales


def test_sales_validators_normalize_notion_values():
    record = build_record(
        tickets_sold=12.0, tickets_sold_by_type="  ", revenue="35.5",
    )
    assert record.tickets_sold == 12
    assert record.tickets_sold_by_type is None
    assert record.revenue == 35.5


# ---------------------------------------------------------------------------
# Notion property mapping round trip
# ---------------------------------------------------------------------------


def test_sales_columns_round_trip_through_notion_properties():
    from tests.test_notion_store import properties_to_page

    record = build_record(
        tickets_sold=7, tickets_sold_by_type="5; 2", revenue=245.0,
        wix_event_id="w1", synced_hash="abc", hidden_from_schedule=True,
    )
    props = event_properties_from_record(record, TZ, include_bookkeeping=True)
    assert props[EventProps.TICKETS_SOLD]["number"] == 7
    assert props[EventProps.REVENUE]["number"] == 245.0

    row = event_page_to_row(properties_to_page(props), TZ)
    rebuilt = row_to_event_record(row)
    assert rebuilt.tickets_sold == 7
    assert rebuilt.tickets_sold_by_type == "5; 2"
    assert rebuilt.revenue == 245.0
    assert rebuilt.hidden_from_schedule is True


def test_unknown_sales_values_are_not_written():
    record = build_record(wix_event_id="w1", synced_hash="abc")
    props = event_properties_from_record(record, TZ, include_bookkeeping=True)
    assert EventProps.TICKETS_SOLD not in props
    assert EventProps.TICKETS_SOLD_BY_TYPE not in props
    assert EventProps.REVENUE not in props


# ---------------------------------------------------------------------------
# Sync refresh behavior (stubs modeled on test_sync_loop)
# ---------------------------------------------------------------------------


def make_row(status: str, **overrides) -> Dict[str, Any]:
    row = {
        "page_id": "page-1",
        "event_name": "Rope Lab",
        "status": status,
        "categories": "",
        "start_date": "2026-08-12",
        "start_time": "19:00",
        "end_date": "2026-08-12",
        "end_time": "22:00",
        "location": "Studio",
        "registration_type": "TICKETS",
        "ticket_price": "35",
        "image_url": "",
        "short_description": "",
        "detailed_description": "",
        "ticket_name": "",
        "ticket_capacity": "",
        "fee_type": "",
        "sale_start": "",
        "sale_end": "",
        "tax_name": "",
        "tax_rate": "",
        "tax_type": "",
        "instructor": "",
        "model": "",
        "wix_event_id": "wix-1",
        "synced_hash": "",
        "sync_error": "",
        "ticket_policy_status": "",
        "tickets_sold": None,
        "tickets_sold_by_type": "",
        "revenue": None,
        "template_relation_ids": [],
    }
    row.update(overrides)
    return row


def make_wix_config_row(**overrides) -> Dict[str, Any]:
    row = {
        "event_name": "Rope Lab",
        "categories": "",
        "start_date": "08/12/2026",
        "start_time": "19:00",
        "end_date": "08/12/2026",
        "end_time": "22:00",
        "location": "Studio",
        "registration_type": "TICKETING",
        "short_description": "",
        "detailed_description": "",
        "image_url": "",
        "ticket_name": "",
        "ticket_price": "35",
        "ticket_capacity": "",
        "fee_type": "",
        "sale_start": "",
        "sale_end": "",
        "tax_name": "",
        "tax_rate": "",
        "tax_type": "",
    }
    row.update(overrides)
    return row


class StoreStub:
    def __init__(self, rows: List[Dict[str, Any]]):
        self.rows = rows
        self.upserts: List[tuple] = []
        self.raw_upserts: List[tuple] = []
        self.sync_results: List[tuple] = []
        self.field_updates: List[tuple] = []

    def fetch_event_rows(self, statuses=None, include_missing_status=False):
        return self.rows

    def upsert_event_from_record(self, record, *, status, source=None, page_id=None):
        self.upserts.append((record, status, source, page_id))

    def upsert_event_from_raw_row(self, row, **kwargs):
        self.raw_upserts.append((row, kwargs))

    def write_sync_result(self, page_id, **kwargs):
        self.sync_results.append((page_id, kwargs))

    def update_event_fields(self, page_id, props):
        self.field_updates.append((page_id, props))

    def fetch_classes(self):
        return {}

    def fetch_settings(self):
        return {}

    def all_writes(self):
        return (
            self.upserts + self.raw_upserts + self.sync_results + self.field_updates
        )


class SalesClientStub:
    """Wix client stub serving ticket definitions with sales details."""

    def __init__(self, ticket_defs, order_summary):
        self.ticket_defs = ticket_defs
        self.order_summary = order_summary
        self.order_summary_calls = 0

    def iter_events(self, **kwargs):
        return iter([])

    def get_ticket_definitions(self, wix_id, include_sales=False):
        assert include_sales, "sales refresh must request SALES_DETAILS"
        return self.ticket_defs

    def get_order_summary(self, wix_id):
        self.order_summary_calls += 1
        return self.order_summary


def make_runtime(store, client) -> SimpleNamespace:
    return SimpleNamespace(
        config=SimpleNamespace(timezone=TZ),
        cache_stats={
            "drive_hits": 0, "drive_misses": 0, "wix_hits": 0, "wix_uploads": 0,
        },
        get_notion_store=lambda: store,
        get_wix_client=lambda: client,
        get_ticket_policy_text=lambda: "",
        get_default_ticket_capacity=lambda: 24,
    )


def patch_sync(monkeypatch, config_row, wix_event):
    monkeypatch.setattr(
        notion_orchestrator,
        "index_events_by_id_and_key",
        lambda runtime, fieldsets=None: ({wix_event["id"]: wix_event}, {}),
    )
    monkeypatch.setattr(notion_orchestrator.time, "sleep", lambda s: None)
    monkeypatch.setattr(
        notion_orchestrator,
        "wix_event_to_config_row",
        lambda event, ticket_defs, tz_name=TZ: dict(config_row),
    )


ORDER_SUMMARY = {"sales": [{"revenue": {"value": "420.00", "currency": "CAD"}}]}


def run_sync(store, client, dry_run=False):
    return notion_sync_events(
        make_runtime(store, client),
        dry_run=dry_run, run_enrich=False, run_pull=False,
    )


def test_sales_drift_alone_triggers_bookkeeping_write(monkeypatch):
    # Content hash matches Wix; only ticket sales moved. The refresh must
    # write the pull-only columns without a full row rewrite or any Wix
    # mutation.
    config_row = make_wix_config_row()
    matching_hash = row_to_event_record(config_row).content_hash()
    row = make_row("Published", synced_hash=matching_hash, tickets_sold=10.0)
    store = StoreStub([row])
    client = SalesClientStub([ticket_def(12)], ORDER_SUMMARY)
    patch_sync(monkeypatch, config_row, {"id": "wix-1", "status": "UPCOMING"})

    assert run_sync(store, client) is True
    assert store.upserts == []
    assert len(store.sync_results) == 1
    _, kwargs = store.sync_results[0]
    assert kwargs["tickets_sold"] == 12
    assert kwargs["tickets_sold_by_type"] == "12"
    assert kwargs["revenue"] == 420.0


def test_unchanged_sales_cost_no_write(monkeypatch):
    config_row = make_wix_config_row()
    matching_hash = row_to_event_record(config_row).content_hash()
    row = make_row(
        "Published", synced_hash=matching_hash,
        tickets_sold=12.0, tickets_sold_by_type="12", revenue=420.0,
    )
    store = StoreStub([row])
    client = SalesClientStub([ticket_def(12)], ORDER_SUMMARY)
    patch_sync(monkeypatch, config_row, {"id": "wix-1", "status": "UPCOMING"})

    assert run_sync(store, client) is True
    assert store.all_writes() == []


def test_failed_order_summary_leaves_revenue_alone(monkeypatch):
    # The order-summary call failing (None) must not blank the existing
    # Revenue value — only the changed sold count is written.
    config_row = make_wix_config_row()
    matching_hash = row_to_event_record(config_row).content_hash()
    row = make_row(
        "Published", synced_hash=matching_hash,
        tickets_sold=10.0, tickets_sold_by_type="10", revenue=350.0,
    )
    store = StoreStub([row])
    client = SalesClientStub([ticket_def(12)], order_summary=None)
    patch_sync(monkeypatch, config_row, {"id": "wix-1", "status": "UPCOMING"})

    assert run_sync(store, client) is True
    assert len(store.sync_results) == 1
    _, kwargs = store.sync_results[0]
    assert kwargs["tickets_sold"] == 12
    assert kwargs["revenue"] is None  # None = leave the column untouched


def test_event_without_tickets_skips_order_summary(monkeypatch):
    config_row = make_wix_config_row(registration_type="RSVP", ticket_price="")
    matching_hash = row_to_event_record(config_row).content_hash()
    row = make_row(
        "Published", synced_hash=matching_hash,
        registration_type="RSVP", ticket_price="",
    )
    store = StoreStub([row])
    client = SalesClientStub([], ORDER_SUMMARY)
    patch_sync(monkeypatch, config_row, {"id": "wix-1", "status": "UPCOMING"})

    assert run_sync(store, client) is True
    assert client.order_summary_calls == 0
    assert store.all_writes() == []


def test_full_refresh_carries_sales_fields(monkeypatch):
    # Content changed in Wix: the full rewrite happens and the record
    # carries the sales fields into the upsert.
    config_row = make_wix_config_row(short_description="new blurb")
    row = make_row("Published", synced_hash="stale")
    store = StoreStub([row])
    client = SalesClientStub([ticket_def(12)], ORDER_SUMMARY)
    patch_sync(monkeypatch, config_row, {"id": "wix-1", "status": "UPCOMING"})

    assert run_sync(store, client) is True
    assert len(store.upserts) == 1
    record, status, _, _ = store.upserts[0]
    assert status == "Published"
    assert record.tickets_sold == 12
    assert record.revenue == 420.0


def test_dry_run_with_sales_drift_writes_nothing(monkeypatch):
    config_row = make_wix_config_row()
    matching_hash = row_to_event_record(config_row).content_hash()
    row = make_row("Published", synced_hash=matching_hash, tickets_sold=1.0)
    store = StoreStub([row])
    client = SalesClientStub([ticket_def(12)], ORDER_SUMMARY)
    patch_sync(monkeypatch, config_row, {"id": "wix-1", "status": "UPCOMING"})

    assert run_sync(store, client, dry_run=True) is True
    assert store.all_writes() == []


# ---------------------------------------------------------------------------
# Dashboard block builder (pure — no Notion I/O)
# ---------------------------------------------------------------------------


NOW = datetime(2026, 8, 6, 12, 0)


def dash_row(**overrides) -> Dict[str, Any]:
    row = make_row("Published")
    row.update({
        "tickets_sold": 12.0,
        "tickets_sold_by_type": "12",
        "revenue": 420.0,
        "ticket_name": "GA",
        "ticket_capacity": "20",
    })
    row.update(overrides)
    return row


def block_texts(blocks) -> List[str]:
    out = []
    for b in blocks:
        payload = b.get(b["type"]) or {}
        for rt in payload.get("rich_text", []):
            out.append(rt["text"]["content"])
    return out


def find_tables(blocks):
    return [b for b in blocks if b["type"] == "table"]


def test_dashboard_blocks_structure_and_totals():
    rows = [
        dash_row(start_date="2026-08-12"),
        dash_row(
            page_id="page-2", event_name="Past Jam", start_date="2026-07-01",
            tickets_sold=30.0, revenue=900.0,
        ),
    ]
    blocks = build_dashboard_blocks(rows, now=NOW)
    texts = block_texts(blocks)

    assert any("Last updated 2026-08-06" in t for t in texts)
    assert any("Upcoming events (1)" in t for t in texts)
    # Upcoming totals only count upcoming rows.
    assert any("12 tickets sold" in t and "$420.00" in t for t in texts)

    upcoming_table, month_table = find_tables(blocks)
    assert upcoming_table["table"]["table_width"] == 6
    header, data = upcoming_table["table"]["children"]
    date_cell = data["table_row"]["cells"][0][0]["text"]["content"]
    assert date_cell == "2026-08-12 19:00"

    # Month summary includes the past month too.
    month_rows = month_table["table"]["children"][1:]
    labels = [r["table_row"]["cells"][0][0]["text"]["content"] for r in month_rows]
    assert labels == ["July 2026", "August 2026"]
    july_revenue = month_rows[0]["table_row"]["cells"][3][0]["text"]["content"]
    assert july_revenue == "$900.00"


def test_dashboard_percent_colors_match_thresholds():
    rows = [
        dash_row(tickets_sold=3.0, ticket_capacity="10", start_date="2026-08-10"),
        dash_row(page_id="p2", tickets_sold=7.0, ticket_capacity="10",
                 start_date="2026-08-11"),
        dash_row(page_id="p3", tickets_sold=10.0, ticket_capacity="10",
                 start_date="2026-08-12"),
    ]
    blocks = build_dashboard_blocks(rows, now=NOW)
    table = find_tables(blocks)[0]
    pct_cells = [
        r["table_row"]["cells"][4][0]
        for r in table["table"]["children"][1:]
    ]
    colors = [c.get("annotations", {}).get("color", "default") for c in pct_cells]
    assert colors == ["green", "orange", "red"]


def test_dashboard_hides_windowed_row_but_keeps_month_history():
    rows = [
        dash_row(start_date="2026-08-12", event_name="Visible"),
        dash_row(
            page_id="p2",
            start_date="2026-08-19",
            event_name="Hidden Tinker",
            hidden_from_schedule=True,
        ),
    ]
    blocks = build_dashboard_blocks(rows, now=NOW)
    assert any("Upcoming events (1)" in t for t in block_texts(blocks))

    upcoming_table, month_table = find_tables(blocks)
    upcoming_names = [
        row["table_row"]["cells"][1][0]["text"]["content"]
        for row in upcoming_table["table"]["children"][1:]
    ]
    assert upcoming_names == ["Visible"]
    august = month_table["table"]["children"][1]
    assert august["table_row"]["cells"][1][0]["text"]["content"] == "2"


def test_dashboard_unknown_sales_and_capacity_show_dashes():
    rows = [dash_row(
        tickets_sold=None, tickets_sold_by_type="", revenue=None,
        ticket_capacity="", ticket_name="",
    )]
    blocks = build_dashboard_blocks(rows, now=NOW)
    table = find_tables(blocks)[0]
    cells = table["table"]["children"][1]["table_row"]["cells"]
    sold, capacity, pct, revenue = cells[2], cells[3], cells[4], cells[5]
    assert sold[0]["text"]["content"] == "—"
    assert capacity[0]["text"]["content"] == "—"
    assert pct[0]["text"]["content"] == "—"
    assert revenue[0]["text"]["content"] == "—"


def test_dashboard_truncates_long_upcoming_table():
    rows = [
        dash_row(page_id=f"p{i}", start_date=f"2026-09-{(i % 28) + 1:02d}")
        for i in range(70)
    ]
    blocks = build_dashboard_blocks(rows, now=NOW)
    table = find_tables(blocks)[0]
    assert len(table["table"]["children"]) == 61  # header + 60 rows
    assert any("and 10 more events" in t for t in block_texts(blocks))


def test_dashboard_no_upcoming_events_message():
    rows = [dash_row(start_date="2026-07-01")]
    blocks = build_dashboard_blocks(rows, now=NOW)
    assert any(
        "No upcoming published events." in t for t in block_texts(blocks)
    )
    assert len(find_tables(blocks)) == 1  # only the month summary
