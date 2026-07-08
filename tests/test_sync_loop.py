"""Characterization tests for the sync loop, pull, and enrich write behavior.

These pin the status-lifecycle semantics before the Phase 1/2 refactor:
Cancel/Delete/Published branches act before record validation, Ready rows
matching Wix drafts get published (never duplicated), dry runs write nothing
to Wix or Notion, and pull only refreshes code-owned rows.
"""

from types import SimpleNamespace
from typing import Any, Dict, List, Optional

import pytest

from event_sync import notion_orchestrator
from event_sync.notion_orchestrator import (
    enrich_events,
    notion_sync_events,
    pull_events,
)
from event_sync.notion_store import EventProps, row_to_event_record


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
        self.upserts: List[tuple] = []
        self.raw_upserts: List[tuple] = []
        self.sync_results: List[tuple] = []
        self.field_updates: List[tuple] = []
        self.classes: Dict[str, Dict[str, Any]] = {}
        self.settings: Dict[str, str] = {}

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
        return self.classes

    def fetch_settings(self):
        return self.settings

    def all_writes(self):
        return (
            self.upserts + self.raw_upserts + self.sync_results + self.field_updates
        )


class ClientStub:
    """Records every Wix mutation; raises if configured to refuse them."""

    def __init__(self, events: Optional[List[Dict[str, Any]]] = None, forbid_mutations=False):
        self.events = events or []
        self.forbid_mutations = forbid_mutations
        self.published: List[str] = []
        self.cancelled: List[str] = []
        self.deleted: List[tuple] = []
        self.delete_result = True

    def _mutate(self):
        if self.forbid_mutations:
            raise AssertionError("Wix mutation attempted during dry run")

    def iter_events(self, **kwargs):
        return iter(self.events)

    def get_ticket_definitions(self, wix_id, include_sales=False):
        self.ticket_def_calls = getattr(self, "ticket_def_calls", 0) + 1
        return []

    def publish_event(self, wix_id):
        self._mutate()
        self.published.append(wix_id)
        return {}

    def cancel_event(self, wix_id):
        self._mutate()
        self.cancelled.append(wix_id)
        return {}

    def delete_event(self, wix_id, force=False):
        self._mutate()
        self.deleted.append((wix_id, force))
        return self.delete_result


def make_runtime(store: StoreStub, client: Optional[ClientStub] = None) -> SimpleNamespace:
    return SimpleNamespace(
        config=SimpleNamespace(timezone=TZ),
        cache_stats={
            "drive_hits": 0, "drive_misses": 0, "wix_hits": 0, "wix_uploads": 0,
        },
        get_notion_store=lambda: store,
        get_wix_client=lambda: client or ClientStub(),
    )


def patch_index(monkeypatch, by_id=None, by_key=None):
    monkeypatch.setattr(
        notion_orchestrator,
        "index_events_by_id_and_key",
        lambda runtime, fieldsets=None: (by_id or {}, by_key or {}),
    )
    monkeypatch.setattr(notion_orchestrator.time, "sleep", lambda s: None)


def patch_config_row(monkeypatch, config_row):
    monkeypatch.setattr(
        notion_orchestrator,
        "wix_event_to_config_row",
        lambda event, ticket_defs, tz_name=TZ: config_row,
    )


def stub_plan(any_changes=True, event_changed=False):
    return {
        "any_changes": any_changes,
        "event_changed": event_changed,
        "event_diffs": [],
        "change_desc": "stub",
        "ticket_updates": [],
        "wix_ticket_defs": [],
    }


# ---------------------------------------------------------------------------
# Ready rows
# ---------------------------------------------------------------------------


def test_ready_row_matching_wix_draft_is_published_with_tickets(monkeypatch):
    store = StoreStub([make_row("Ready")])
    client = ClientStub()
    patch_index(monkeypatch, by_id={"wix-1": {"id": "wix-1", "status": "DRAFT"}})
    ensured: List[tuple] = []
    monkeypatch.setattr(
        notion_orchestrator,
        "ensure_ticket_definition",
        lambda c, wix_id, record, **kw: ensured.append((wix_id, record.ticket_price)),
    )

    assert notion_sync_events(make_runtime(store, client), run_enrich=False) is True
    assert client.published == ["wix-1"]
    # Single-price TICKETING row without ticket_name -> _ensure_ticket_definition.
    assert ensured == [("wix-1", 35.0)]
    assert len(store.sync_results) == 1
    page_id, kwargs = store.sync_results[0]
    assert kwargs["status"] == "Published"
    assert kwargs["wix_event_id"] == "wix-1"
    assert kwargs["synced_hash"]
    assert kwargs["error"] is None


def test_ready_row_with_named_tickets_uses_config_ticket_creation(monkeypatch):
    store = StoreStub(
        [make_row("Ready", ticket_name="GA; VIP", ticket_price="25; 50")]
    )
    client = ClientStub()
    patch_index(monkeypatch, by_id={"wix-1": {"id": "wix-1", "status": "DRAFT"}})
    created: List[str] = []
    monkeypatch.setattr(
        notion_orchestrator,
        "create_tickets_from_config",
        lambda c, wix_id, record, **kw: created.append(wix_id) or True,
    )

    assert notion_sync_events(make_runtime(store, client), run_enrich=False) is True
    assert client.published == ["wix-1"]
    assert created == ["wix-1"]


def test_ready_row_matching_live_event_updates_never_creates(monkeypatch):
    store = StoreStub([make_row("Ready")])
    patch_index(monkeypatch, by_id={"wix-1": {"id": "wix-1", "status": "UPCOMING"}})
    monkeypatch.setattr(
        notion_orchestrator,
        "compute_event_update_plan",
        lambda *a: stub_plan(any_changes=False),
    )
    monkeypatch.setattr(
        notion_orchestrator,
        "create_wix_event",
        lambda *a, **k: pytest.fail("create_wix_event must not run for matched rows"),
    )
    ensured: List[str] = []
    monkeypatch.setattr(
        notion_orchestrator,
        "ensure_ticket_definition",
        lambda c, wix_id, record, **kw: ensured.append(wix_id),
    )

    assert notion_sync_events(make_runtime(store), run_enrich=False) is True
    # No changes -> row simply linked back to Published with a fresh hash.
    assert len(store.sync_results) == 1
    _, kwargs = store.sync_results[0]
    assert kwargs["status"] == "Published"
    assert kwargs["wix_event_id"] == "wix-1"
    # Ticket safety net still runs for live TICKETING events.
    assert ensured == ["wix-1"]


def test_ready_row_matches_by_title_date_time_when_id_missing(monkeypatch):
    store = StoreStub([make_row("Ready", wix_event_id="")])
    key = "Rope Lab|2026-08-12|19:00"
    patch_index(
        monkeypatch, by_key={key: {"id": "wix-9", "status": "UPCOMING"}}
    )
    monkeypatch.setattr(
        notion_orchestrator,
        "compute_event_update_plan",
        lambda *a: stub_plan(any_changes=False),
    )
    monkeypatch.setattr(
        notion_orchestrator,
        "create_wix_event",
        lambda *a, **k: pytest.fail("matched by key; must not create"),
    )
    monkeypatch.setattr(
        notion_orchestrator, "ensure_ticket_definition", lambda *a, **kw: None
    )

    assert notion_sync_events(make_runtime(store), run_enrich=False) is True
    _, kwargs = store.sync_results[0]
    assert kwargs["wix_event_id"] == "wix-9"


def test_ready_row_without_match_creates_event(monkeypatch):
    store = StoreStub([make_row("Ready", wix_event_id="")])
    patch_index(monkeypatch)
    monkeypatch.setattr(
        notion_orchestrator, "create_wix_event", lambda record, **k: "new-wix-id"
    )

    assert notion_sync_events(make_runtime(store), run_enrich=False) is True
    assert len(store.sync_results) == 1
    _, kwargs = store.sync_results[0]
    assert kwargs["status"] == "Published"
    assert kwargs["wix_event_id"] == "new-wix-id"
    assert kwargs["error"] is None


def test_ready_row_create_in_draft_mode_stays_ready_with_note(monkeypatch):
    store = StoreStub([make_row("Ready", wix_event_id="")])
    patch_index(monkeypatch)
    monkeypatch.setattr(
        notion_orchestrator, "create_wix_event", lambda record, **k: "new-wix-id"
    )

    assert (
        notion_sync_events(make_runtime(store), run_enrich=False, draft=True) is True
    )
    _, kwargs = store.sync_results[0]
    assert kwargs["status"] == "Ready"
    assert "draft" in kwargs["error"].lower()


def test_ready_row_create_failure_lands_error(monkeypatch):
    store = StoreStub([make_row("Ready", wix_event_id="")])
    patch_index(monkeypatch)
    monkeypatch.setattr(
        notion_orchestrator, "create_wix_event", lambda record, **k: None
    )

    assert notion_sync_events(make_runtime(store), run_enrich=False) is False
    _, kwargs = store.sync_results[0]
    assert kwargs["status"] == "Error"


def test_invalid_ready_row_gets_error_status(monkeypatch):
    store = StoreStub([make_row("Ready", start_date="", wix_event_id="")])
    patch_index(monkeypatch)

    assert notion_sync_events(make_runtime(store), run_enrich=False) is False
    _, kwargs = store.sync_results[0]
    assert kwargs["status"] == "Error"
    assert "Invalid row" in kwargs["error"]


def test_ready_row_matching_canceled_event_flips_to_cancelled(monkeypatch):
    store = StoreStub([make_row("Ready")])
    patch_index(monkeypatch, by_id={"wix-1": {"id": "wix-1", "status": "CANCELED"}})

    assert notion_sync_events(make_runtime(store), run_enrich=False) is True
    _, kwargs = store.sync_results[0]
    assert kwargs["status"] == "Cancelled"
    assert "Cancelled in Wix" in kwargs["error"]


# ---------------------------------------------------------------------------
# Cancel / Delete rows (must act BEFORE record validation)
# ---------------------------------------------------------------------------


def test_cancel_row_cancels_live_event(monkeypatch):
    store = StoreStub([make_row("Cancel")])
    client = ClientStub()
    patch_index(monkeypatch, by_id={"wix-1": {"id": "wix-1", "status": "UPCOMING"}})

    assert notion_sync_events(make_runtime(store, client), run_enrich=False) is True
    assert client.cancelled == ["wix-1"]
    _, kwargs = store.sync_results[0]
    assert kwargs["status"] == "Cancelled"


def test_cancel_acts_on_incomplete_row_before_validation(monkeypatch):
    # No date, no location: row_to_event_record would raise, but Cancel
    # only needs the Wix match.
    store = StoreStub(
        [make_row("Cancel", start_date="", start_time="", location="")]
    )
    client = ClientStub()
    patch_index(monkeypatch, by_id={"wix-1": {"id": "wix-1", "status": "UPCOMING"}})

    assert notion_sync_events(make_runtime(store, client), run_enrich=False) is True
    assert client.cancelled == ["wix-1"]


def test_cancel_row_missing_from_wix_writes_note_without_status(monkeypatch):
    store = StoreStub([make_row("Cancel")])
    patch_index(monkeypatch)

    assert notion_sync_events(make_runtime(store), run_enrich=False) is True
    _, kwargs = store.sync_results[0]
    assert "nothing to cancel" in kwargs["error"]
    assert "status" not in kwargs


def test_cancel_row_already_cancelled_in_wix_just_records_status(monkeypatch):
    store = StoreStub([make_row("Cancel")])
    client = ClientStub()
    patch_index(monkeypatch, by_id={"wix-1": {"id": "wix-1", "status": "CANCELED"}})

    assert notion_sync_events(make_runtime(store, client), run_enrich=False) is True
    assert client.cancelled == []
    _, kwargs = store.sync_results[0]
    assert kwargs["status"] == "Cancelled"


def test_cancel_row_on_wix_draft_fails_with_guidance(monkeypatch):
    store = StoreStub([make_row("Cancel")])
    client = ClientStub()
    patch_index(monkeypatch, by_id={"wix-1": {"id": "wix-1", "status": "DRAFT"}})

    assert notion_sync_events(make_runtime(store, client), run_enrich=False) is False
    assert client.cancelled == []
    _, kwargs = store.sync_results[0]
    assert "Delete" in kwargs["error"]


def test_delete_row_deletes_with_force(monkeypatch):
    store = StoreStub([make_row("Delete")])
    client = ClientStub()
    patch_index(monkeypatch, by_id={"wix-1": {"id": "wix-1", "status": "UPCOMING"}})

    assert notion_sync_events(make_runtime(store, client), run_enrich=False) is True
    assert client.deleted == [("wix-1", True)]
    _, kwargs = store.sync_results[0]
    assert kwargs["status"] == "Removed"


def test_delete_row_missing_from_wix_marks_removed(monkeypatch):
    store = StoreStub([make_row("Delete", wix_event_id="")])
    patch_index(monkeypatch)

    assert notion_sync_events(make_runtime(store), run_enrich=False) is True
    _, kwargs = store.sync_results[0]
    assert kwargs["status"] == "Removed"


def test_delete_failure_lands_error(monkeypatch):
    store = StoreStub([make_row("Delete")])
    client = ClientStub()
    client.delete_result = False
    patch_index(monkeypatch, by_id={"wix-1": {"id": "wix-1", "status": "UPCOMING"}})

    assert notion_sync_events(make_runtime(store, client), run_enrich=False) is False
    _, kwargs = store.sync_results[0]
    assert kwargs["status"] == "Error"


# ---------------------------------------------------------------------------
# Published rows (refresh runs before validation too)
# ---------------------------------------------------------------------------


def test_published_row_missing_from_wix_writes_note(monkeypatch):
    store = StoreStub([make_row("Published")])
    patch_index(monkeypatch)

    assert notion_sync_events(make_runtime(store), run_enrich=False) is True
    _, kwargs = store.sync_results[0]
    assert "Not found in Wix" in kwargs["error"]
    assert "status" not in kwargs


def test_published_refresh_with_invalid_wix_event_lands_raw_row(monkeypatch):
    # The Wix event is too incomplete to validate (blank date) but must
    # still land in Notion with a Sync Error note.
    store = StoreStub([make_row("Published")])
    patch_index(monkeypatch, by_id={"wix-1": {"id": "wix-1", "status": "UPCOMING"}})
    patch_config_row(
        monkeypatch, make_wix_config_row(start_date="", end_date="")
    )

    assert notion_sync_events(make_runtime(store), run_enrich=False) is True
    assert len(store.raw_upserts) == 1
    _, kwargs = store.raw_upserts[0]
    assert kwargs["page_id"] == "page-1"
    assert "missing fields" in kwargs["error"]


# ---------------------------------------------------------------------------
# Dry run writes nothing anywhere
# ---------------------------------------------------------------------------


def test_dry_run_writes_nothing_across_all_branches(monkeypatch):
    rows = [
        make_row("Cancel", page_id="p1", event_name="A", wix_event_id="w1"),
        make_row("Delete", page_id="p2", event_name="B", wix_event_id="w2"),
        make_row("Published", page_id="p3", event_name="C", wix_event_id="w3",
                 short_description="stale"),
        make_row("Update", page_id="p4", event_name="D", wix_event_id="w4"),
        make_row("Ready", page_id="p5", event_name="E", wix_event_id=""),
        make_row("Ready", page_id="p6", event_name="F", wix_event_id="w6"),
    ]
    store = StoreStub(rows)
    client = ClientStub(forbid_mutations=True)
    by_id = {
        "w1": {"id": "w1", "status": "UPCOMING"},
        "w2": {"id": "w2", "status": "UPCOMING"},
        "w3": {"id": "w3", "status": "UPCOMING"},
        "w4": {"id": "w4", "status": "UPCOMING"},
        "w6": {"id": "w6", "status": "DRAFT"},
    }
    patch_index(monkeypatch, by_id=by_id)
    patch_config_row(monkeypatch, make_wix_config_row(event_name="C"))
    monkeypatch.setattr(
        notion_orchestrator, "compute_event_update_plan", lambda *a: stub_plan()
    )
    monkeypatch.setattr(
        notion_orchestrator,
        "apply_event_update_plan",
        lambda *a, **k: pytest.fail("apply_event_update_plan ran during dry run"),
    )
    monkeypatch.setattr(
        notion_orchestrator,
        "create_wix_event",
        lambda *a, **k: pytest.fail("create_wix_event ran during dry run"),
    )

    assert (
        notion_sync_events(make_runtime(store, client), dry_run=True) is True
    )
    assert store.all_writes() == []
    assert client.published == client.cancelled == []
    assert client.deleted == []


# ---------------------------------------------------------------------------
# pull_events buckets
# ---------------------------------------------------------------------------


def wix_event(id_, title, status="UPCOMING"):
    return {"id": id_, "title": title, "status": status}


def config_row_for(event):
    return make_wix_config_row(event_name=event["title"])


def make_pull_runtime(store, events):
    client = ClientStub(events=events)
    return make_runtime(store, client)


def patch_pull(monkeypatch):
    monkeypatch.setattr(
        notion_orchestrator,
        "wix_event_to_config_row",
        lambda event, ticket_defs, tz_name=TZ: config_row_for(event),
    )


def test_pull_creates_published_row_for_new_wix_event(monkeypatch):
    store = StoreStub([])
    patch_pull(monkeypatch)

    assert pull_events(make_pull_runtime(store, [wix_event("w1", "New Jam")])) is True
    assert len(store.upserts) == 1
    record, status, source, page_id = store.upserts[0]
    assert (status, source, page_id) == ("Published", "wix", None)
    assert record.wix_event_id == "w1"
    assert record.synced_hash == record.content_hash()


def test_pull_maps_wix_canceled_to_cancelled_row(monkeypatch):
    store = StoreStub([])
    patch_pull(monkeypatch)

    events = [wix_event("w1", "Dead Event", status="CANCELED")]
    assert pull_events(make_pull_runtime(store, events), scope="all") is True
    _, status, _, _ = store.upserts[0]
    assert status == "Cancelled"


def test_pull_scope_upcoming_skips_ended_events(monkeypatch):
    store = StoreStub([])
    patch_pull(monkeypatch)

    events = [
        wix_event("w1", "Upcoming"),
        wix_event("w2", "Old", status="ENDED"),
    ]
    assert pull_events(make_pull_runtime(store, events)) is True
    assert len(store.upserts) == 1
    assert store.upserts[0][0].wix_event_id == "w1"


def test_pull_refreshes_existing_published_row(monkeypatch):
    existing = make_row("Published", event_name="Rope Lab", wix_event_id="w1")
    store = StoreStub([existing])
    monkeypatch.setattr(
        notion_orchestrator,
        "wix_event_to_config_row",
        lambda event, ticket_defs, tz_name=TZ: make_wix_config_row(
            event_name=event["title"], short_description="edited on the website"
        ),
    )

    assert pull_events(make_pull_runtime(store, [wix_event("w1", "Rope Lab")])) is True
    assert len(store.upserts) == 1
    _, status, _, page_id = store.upserts[0]
    assert (status, page_id) == ("Published", "page-1")


def test_pull_skips_unchanged_published_row(monkeypatch):
    # Row content and bookkeeping already match the live event: zero writes.
    config_row = make_wix_config_row()
    matching_hash = row_to_event_record(config_row).content_hash()
    existing = make_row(
        "Published", event_name="Rope Lab", wix_event_id="w1",
        synced_hash=matching_hash,
    )
    store = StoreStub([existing])
    patch_pull(monkeypatch)

    assert pull_events(make_pull_runtime(store, [wix_event("w1", "Rope Lab")])) is True
    assert store.all_writes() == []


def test_pull_refreshes_stale_bookkeeping_without_full_rewrite(monkeypatch):
    # Content matches but the stored hash is stale: one bookkeeping write,
    # no full page rewrite.
    existing = make_row(
        "Published", event_name="Rope Lab", wix_event_id="w1", synced_hash="",
    )
    store = StoreStub([existing])
    patch_pull(monkeypatch)

    assert pull_events(make_pull_runtime(store, [wix_event("w1", "Rope Lab")])) is True
    assert store.upserts == []
    assert len(store.sync_results) == 1
    _, kwargs = store.sync_results[0]
    assert kwargs["wix_event_id"] == "w1"
    assert kwargs["synced_hash"]


def test_pull_links_human_row_matched_by_key_without_touching_fields(monkeypatch):
    # A Draft row matching a live Wix event by title|date|time gets the Wix id
    # written but its fields stay untouched.
    existing = make_row("Draft", event_name="Rope Lab", wix_event_id="",
                        synced_hash="keep-me")
    store = StoreStub([existing])
    patch_pull(monkeypatch)

    assert pull_events(make_pull_runtime(store, [wix_event("w1", "Rope Lab")])) is True
    assert store.upserts == []
    assert store.raw_upserts == []
    assert len(store.sync_results) == 1
    _, kwargs = store.sync_results[0]
    assert kwargs["wix_event_id"] == "w1"
    assert kwargs["synced_hash"] == "keep-me"


def test_pull_skips_human_row_matched_by_wix_id(monkeypatch):
    existing = make_row("Update", event_name="Rope Lab", wix_event_id="w1")
    store = StoreStub([existing])
    patch_pull(monkeypatch)
    client = ClientStub(events=[wix_event("w1", "Rope Lab")])

    assert pull_events(make_runtime(store, client)) is True
    assert store.all_writes() == []
    # Id-matched human rows must not pay the ticket-definitions fetch.
    assert getattr(client, "ticket_def_calls", 0) == 0


def test_pull_returns_false_when_no_events(monkeypatch):
    store = StoreStub([])
    patch_pull(monkeypatch)
    assert pull_events(make_pull_runtime(store, [])) is False


# ---------------------------------------------------------------------------
# Enrich write behavior (pins the current always-write shape; Phase 3
# deliberately changes this — update these tests alongside that change)
# ---------------------------------------------------------------------------


def make_enrich_runtime(store):
    return make_runtime(store)


def test_enrich_promotes_complete_idea_row_to_draft():
    store = StoreStub([make_row("Idea")])
    assert enrich_events(make_enrich_runtime(store)) is True
    assert len(store.field_updates) == 1
    _, props = store.field_updates[0]
    assert props[EventProps.STATUS] == {"select": {"name": "Draft"}}


def test_enrich_writes_error_note_and_keeps_idea_status():
    store = StoreStub([make_row("Idea", start_date="", start_time="")])
    assert enrich_events(make_enrich_runtime(store)) is True
    _, props = store.field_updates[0]
    assert EventProps.STATUS not in props
    note = props[EventProps.SYNC_ERROR]["rich_text"][0]["text"]["content"]
    assert note.startswith("Not ready to sync")


def test_enrich_skips_noop_draft_rows_entirely():
    # A complete Draft row with nothing to fill and no stale error costs
    # zero Notion writes.
    store = StoreStub([make_row("Draft", fee_type="FEE_ADDED_AT_CHECKOUT")])
    assert enrich_events(make_enrich_runtime(store)) is True
    assert store.field_updates == []


def test_enrich_clears_stale_sync_error_on_clean_row():
    # A human fixed the row but the old note is still there: one write,
    # clearing Sync Error.
    store = StoreStub([
        make_row("Draft", fee_type="FEE_ADDED_AT_CHECKOUT",
                 sync_error="Not ready to sync: old"),
    ])
    assert enrich_events(make_enrich_runtime(store)) is True
    assert len(store.field_updates) == 1
    _, props = store.field_updates[0]
    assert props[EventProps.SYNC_ERROR] == {"rich_text": []}


def test_enrich_skips_rewrite_of_unchanged_error_note():
    # An incomplete row whose note is already up to date is not rewritten.
    store = StoreStub([
        make_row("Draft", start_date="", start_time="",
                 fee_type="FEE_ADDED_AT_CHECKOUT"),
    ])
    assert enrich_events(make_enrich_runtime(store)) is True
    assert len(store.field_updates) == 1
    _, props = store.field_updates[0]
    note = props[EventProps.SYNC_ERROR]["rich_text"][0]["text"]["content"]

    # Second pass with the note already stored on the row: no write.
    row = make_row("Draft", start_date="", start_time="",
                   fee_type="FEE_ADDED_AT_CHECKOUT", sync_error=note)
    store2 = StoreStub([row])
    assert enrich_events(make_enrich_runtime(store2)) is True
    assert store2.field_updates == []


def test_enrich_skips_unnamed_row_without_template():
    store = StoreStub([make_row("Idea", event_name="")])
    assert enrich_events(make_enrich_runtime(store)) is True
    assert store.field_updates == []


def test_validation_record_round_trip_matches_row():
    # Sanity: the row fixtures used across this module build a valid record.
    record = row_to_event_record(make_row("Ready"))
    assert record.name == "Rope Lab"
    assert record.registration_type == "TICKETING"


# ---------------------------------------------------------------------------
# Image preservation (the silent-image-loss fix)
# ---------------------------------------------------------------------------

DRIVE_URL = "https://drive.google.com/file/d/abc123/view"
WIXSTATIC_URL = "https://static.wixstatic.com/media/xyz.jpg"


def test_published_refresh_preserves_drive_image_when_wix_has_none(monkeypatch):
    row = make_row("Published", image_url=DRIVE_URL, short_description="stale")
    store = StoreStub([row])
    patch_index(monkeypatch, by_id={"wix-1": {"id": "wix-1", "status": "UPCOMING"}})
    patch_config_row(monkeypatch, make_wix_config_row(image_url=""))

    assert notion_sync_events(make_runtime(store), run_enrich=False) is True
    assert len(store.upserts) == 1
    record, _, _, _ = store.upserts[0]
    assert record.image_url == DRIVE_URL
    assert record.synced_hash == record.content_hash()


def test_published_refresh_skips_when_only_difference_was_missing_wix_image(monkeypatch):
    # With preservation, a row identical to Wix except for its Drive image
    # hash-matches and is skipped instead of being rewritten imageless.
    row = make_row("Published", image_url=DRIVE_URL)
    config_row = make_wix_config_row(image_url="")
    preserved = dict(config_row, image_url=DRIVE_URL)
    from event_sync.notion_store import row_to_event_record as to_record
    row["synced_hash"] = to_record(preserved).content_hash()
    store = StoreStub([row])
    patch_index(monkeypatch, by_id={"wix-1": {"id": "wix-1", "status": "UPCOMING"}})
    patch_config_row(monkeypatch, config_row)

    assert notion_sync_events(make_runtime(store), run_enrich=False) is True
    assert store.upserts == []


def test_published_refresh_respects_deliberate_wix_image_removal(monkeypatch):
    # A wixstatic row URL is code-written; if the image is gone from Wix,
    # the refresh clears it rather than resurrecting it.
    row = make_row("Published", image_url=WIXSTATIC_URL, short_description="stale")
    store = StoreStub([row])
    patch_index(monkeypatch, by_id={"wix-1": {"id": "wix-1", "status": "UPCOMING"}})
    patch_config_row(monkeypatch, make_wix_config_row(image_url=""))

    assert notion_sync_events(make_runtime(store), run_enrich=False) is True
    record, _, _, _ = store.upserts[0]
    assert record.image_url is None


def test_pull_refresh_preserves_drive_image(monkeypatch):
    existing = make_row(
        "Published", event_name="Rope Lab", wix_event_id="w1", image_url=DRIVE_URL
    )
    store = StoreStub([existing])
    monkeypatch.setattr(
        notion_orchestrator,
        "wix_event_to_config_row",
        lambda event, ticket_defs, tz_name=TZ: make_wix_config_row(
            event_name=event["title"], image_url="", short_description="new"
        ),
    )

    assert pull_events(make_pull_runtime(store, [wix_event("w1", "Rope Lab")])) is True
    record, _, _, page_id = store.upserts[0]
    assert page_id == "page-1"
    assert record.image_url == DRIVE_URL


def test_create_with_failed_image_upload_gets_sync_error_note(monkeypatch):
    store = StoreStub([make_row("Ready", wix_event_id="", image_url=DRIVE_URL)])
    patch_index(monkeypatch)

    def fake_create(record, *, runtime, **kwargs):
        runtime.last_image_failure = record.image_url
        return "new-wix-id"

    monkeypatch.setattr(notion_orchestrator, "create_wix_event", fake_create)

    runtime = make_runtime(store)
    assert notion_sync_events(runtime, run_enrich=False) is True
    _, kwargs = store.sync_results[0]
    assert kwargs["status"] == "Published"
    assert "upload failed" in kwargs["error"]
    assert DRIVE_URL in kwargs["error"]


# ---------------------------------------------------------------------------
# Match-key normalization (one owner: wix_mapping.event_match_key)
# ---------------------------------------------------------------------------


def test_match_key_strips_title_whitespace():
    from event_sync.wix_mapping import event_match_key

    assert (
        event_match_key("  Rope Lab ", "2026-08-12", "19:00")
        == event_match_key("Rope Lab", "2026-08-12", "19:00")
        == "Rope Lab|2026-08-12|19:00"
    )


def test_row_with_padded_name_still_matches_by_key(monkeypatch):
    from event_sync.notion_orchestrator import _match_wix_event

    wix = {"id": "w-1", "status": "UPCOMING"}
    by_key = {"Rope Lab|2026-08-12|19:00": wix}
    row = make_row("Ready", event_name="  Rope Lab ", wix_event_id="")

    matched, wix_id = _match_wix_event(row, {}, by_key)
    assert matched is wix
    assert wix_id == "w-1"


# ---------------------------------------------------------------------------
# One row's failed Notion write-back must not abort the batch
# ---------------------------------------------------------------------------


def test_failed_notion_write_does_not_abort_remaining_rows(monkeypatch):
    from event_sync.notion_store import NotionStoreError

    rows = [
        make_row("Delete", page_id="p1", event_name="A", wix_event_id=""),
        make_row("Delete", page_id="p2", event_name="B", wix_event_id=""),
    ]
    store = StoreStub(rows)
    patch_index(monkeypatch)

    original = store.write_sync_result
    def flaky(page_id, **kwargs):
        if page_id == "p1":
            raise NotionStoreError("Notion page update p1 failed: 503")
        original(page_id, **kwargs)
    store.write_sync_result = flaky

    assert notion_sync_events(make_runtime(store), run_enrich=False) is False
    # Row B was still processed and marked Removed.
    assert len(store.sync_results) == 1
    page_id, kwargs = store.sync_results[0]
    assert page_id == "p2"
    assert kwargs["status"] == "Removed"
