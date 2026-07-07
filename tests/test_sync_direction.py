"""Tests for the sync direction semantics of Published vs Update rows.

Published rows treat Wix as authoritative (Wix -> Notion refresh); Update
rows push local Notion changes to Wix and land back on Published.
"""

from types import SimpleNamespace
from typing import Any, Dict, List, Optional

from event_sync import generator, notion_orchestrator
from event_sync.notion_orchestrator import notion_sync_events
from event_sync.notion_store import row_to_event_record


TZ = "America/Toronto"


def make_row(status: str, **overrides) -> Dict[str, Any]:
    row = {
        "page_id": "page-1",
        "event_name": "Rope Lab",
        "status": status,
        "categories": "rope; class",
        "start_date": "2026-08-12",
        "start_time": "19:00",
        "end_date": "2026-08-12",
        "end_time": "22:00",
        "location": "Studio",
        "registration_type": "TICKETS",
        "capacity": "24",
        "ticket_price": "35",
        "image_url": "",
        "short_description": "",
        "detailed_description": "",
        "ticket_name": "",
        "ticket_capacity": "",
        "fee_type": "",
        "sale_start": "",
        "sale_end": "",
        "tax_name": "HST",
        "tax_rate": "13",
        "tax_type": "ADDED_AT_CHECKOUT",
        "instructor": "",
        "model": "",
        "wix_event_id": "wix-1",
        "synced_hash": "",
        "sync_error": "",
        "template_relation_ids": [],
    }
    row.update(overrides)
    return row


def make_wix_config_row(**overrides) -> Dict[str, Any]:
    """The row `_wix_event_to_config_row` would derive from the live event."""
    row = {
        "event_name": "Rope Lab",
        "categories": "rope; class",
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
        "tax_name": "HST",
        "tax_rate": "13",
        "tax_type": "ADDED_AT_CHECKOUT",
    }
    row.update(overrides)
    return row


class StoreStub:
    def __init__(self, rows: List[Dict[str, Any]]):
        self.rows = rows
        self.fetch_calls: List[Optional[List[str]]] = []
        self.upserts: List[tuple] = []
        self.raw_upserts: List[tuple] = []
        self.sync_results: List[tuple] = []
        self.field_updates: List[tuple] = []

    def fetch_event_rows(self, statuses=None, include_missing_status=False):
        self.fetch_calls.append(statuses)
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


class ClientStub:
    def get_ticket_definitions(self, wix_id):
        return []


def make_runtime(store: StoreStub) -> SimpleNamespace:
    return SimpleNamespace(
        config=SimpleNamespace(timezone=TZ),
        cache_stats={
            "drive_hits": 0, "drive_misses": 0, "wix_hits": 0, "wix_uploads": 0,
        },
        get_notion_store=lambda: store,
        get_wix_client=lambda: ClientStub(),
    )


def patch_wix_side(monkeypatch, wix_event, config_row):
    monkeypatch.setattr(
        notion_orchestrator,
        "_index_events_by_id_and_key",
        lambda runtime, fieldsets=None: ({wix_event["id"]: wix_event}, {}),
    )
    monkeypatch.setattr(
        generator,
        "_wix_event_to_config_row",
        lambda event, ticket_defs, tz_name=TZ: config_row,
    )
    monkeypatch.setattr(notion_orchestrator.time, "sleep", lambda s: None)


def test_sync_fetches_update_rows(monkeypatch):
    store = StoreStub([])
    assert notion_sync_events(make_runtime(store), run_enrich=False) is True
    assert store.fetch_calls == [
        ["Ready", "Published", "Update", "Cancel", "Delete"]
    ]


def test_published_row_matching_wix_is_skipped(monkeypatch):
    config_row = make_wix_config_row()
    wix_hash = row_to_event_record(config_row).content_hash()
    store = StoreStub([make_row("Published", synced_hash=wix_hash)])
    patch_wix_side(
        monkeypatch, {"id": "wix-1", "status": "UPCOMING"}, config_row
    )

    assert notion_sync_events(make_runtime(store), run_enrich=False) is True
    assert store.upserts == []
    assert store.raw_upserts == []
    assert store.sync_results == []


def test_published_row_is_refreshed_from_wix(monkeypatch):
    config_row = make_wix_config_row(short_description="New teaser from Wix")
    store = StoreStub([make_row("Published")])
    patch_wix_side(
        monkeypatch, {"id": "wix-1", "status": "UPCOMING"}, config_row
    )

    assert notion_sync_events(make_runtime(store), run_enrich=False) is True
    assert len(store.upserts) == 1
    record, status, source, page_id = store.upserts[0]
    assert status == "Published"
    assert source == "wix"
    assert page_id == "page-1"
    assert record.teaser == "New teaser from Wix"
    assert record.wix_event_id == "wix-1"
    assert record.synced_hash == record.content_hash()
    # The old push path must not run for Published rows.
    assert store.sync_results == []


def test_published_row_cancelled_in_wix_becomes_cancelled(monkeypatch):
    config_row = make_wix_config_row()
    store = StoreStub([make_row("Published")])
    patch_wix_side(
        monkeypatch, {"id": "wix-1", "status": "CANCELED"}, config_row
    )

    assert notion_sync_events(make_runtime(store), run_enrich=False) is True
    assert len(store.upserts) == 1
    _, status, _, _ = store.upserts[0]
    assert status == "Cancelled"


def test_update_row_pushes_local_changes_and_returns_to_published(monkeypatch):
    store = StoreStub([make_row("Update")])
    patch_wix_side(
        monkeypatch, {"id": "wix-1", "status": "UPCOMING"}, make_wix_config_row()
    )
    plan = {
        "any_changes": True,
        "event_changed": True,
        "event_diffs": [],
        "change_desc": "event fields",
    }
    applied: List[str] = []
    monkeypatch.setattr(
        notion_orchestrator,
        "compute_event_update_plan",
        lambda client, runtime, record, wix_id, wix_event: plan,
    )
    monkeypatch.setattr(
        notion_orchestrator,
        "apply_event_update_plan",
        lambda client, runtime, record, wix_id, wix_event, p: applied.append(wix_id) or True,
    )

    assert notion_sync_events(make_runtime(store), run_enrich=False) is True
    assert applied == ["wix-1"]
    assert store.upserts == []  # no Wix -> Notion refresh for Update rows
    assert len(store.sync_results) == 1
    page_id, kwargs = store.sync_results[0]
    assert page_id == "page-1"
    assert kwargs["status"] == "Published"
    assert kwargs["error"] is None
    assert kwargs["synced_hash"]


def test_update_row_without_changes_flips_back_to_published(monkeypatch):
    store = StoreStub([make_row("Update")])
    patch_wix_side(
        monkeypatch, {"id": "wix-1", "status": "UPCOMING"}, make_wix_config_row()
    )
    monkeypatch.setattr(
        notion_orchestrator,
        "compute_event_update_plan",
        lambda client, runtime, record, wix_id, wix_event: {
            "any_changes": False,
            "event_changed": False,
            "event_diffs": [],
            "change_desc": "",
        },
    )

    assert notion_sync_events(make_runtime(store), run_enrich=False) is True
    assert len(store.sync_results) == 1
    page_id, kwargs = store.sync_results[0]
    assert page_id == "page-1"
    assert kwargs["status"] == "Published"


def test_update_row_missing_from_wix_gets_error_note(monkeypatch):
    store = StoreStub([make_row("Update")])
    monkeypatch.setattr(
        notion_orchestrator,
        "_index_events_by_id_and_key",
        lambda runtime, fieldsets=None: ({}, {}),
    )
    monkeypatch.setattr(notion_orchestrator.time, "sleep", lambda s: None)

    assert notion_sync_events(make_runtime(store), run_enrich=False) is True
    assert len(store.sync_results) == 1
    page_id, kwargs = store.sync_results[0]
    assert page_id == "page-1"
    assert "Not found in Wix" in kwargs["error"]
    assert "status" not in kwargs
