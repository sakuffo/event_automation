"""Global ticket policy blurb (Settings ``default_ticket_policy``).

The blurb is the ticket definition's ``policyText`` — the text printed on
every ticket a buyer receives (insurance requirement). It is global: one
Settings row, applied to every ticket the pipeline creates and diffed onto
existing tickets by the update plan. A blank setting means "not managed" —
nothing is sent and live policies are never touched, mirroring the
``ticket_limit_per_order`` semantics.

Also pins the read-only ``Ticket Policy Status`` column: the wording helper
and the refresh paths that write it (including drift-alone writes that the
hash fast-path would otherwise skip).
"""

from types import SimpleNamespace
from typing import Any, Dict, List

from event_sync import notion_orchestrator
from event_sync.config import AppConfig
from event_sync.models import EventRecord
from event_sync.notion_orchestrator import notion_sync_events
from event_sync.notion_store import row_to_event_record
from event_sync.runtime import MAX_TICKET_POLICY_CHARS, SyncRuntime
from event_sync.wix_flows import (
    apply_event_update_plan,
    compute_event_update_plan,
    create_tickets_from_config,
    ensure_ticket_definition,
)
from event_sync.wix_mapping import ticket_policy_status


TZ = "America/Toronto"
POLICY = "All sales final. Attendees participate at their own risk."


def make_runtime(policy: str = "") -> SimpleNamespace:
    return SimpleNamespace(
        config=SimpleNamespace(timezone=TZ),
        get_ticket_policy_text=lambda: policy,
    )


def make_record(**overrides) -> EventRecord:
    payload = dict(
        name="Rope Lab",
        start_date="2026-08-12",
        start_time="19:00",
        end_date="2026-08-12",
        end_time="22:00",
        location="Studio",
        ticket_price=35.0,
        registration_type="TICKETS",
    )
    payload.update(overrides)
    return EventRecord(**payload)


def make_wix_event() -> Dict[str, Any]:
    """Matches make_record() on every diffed field (19:00 EDT == 23:00 UTC)."""
    return {
        "id": "wix-1",
        "title": "Rope Lab",
        "status": "UPCOMING",
        "dateAndTimeSettings": {
            "startDate": "2026-08-12T23:00:00Z",
            "endDate": "2026-08-13T02:00:00Z",
            "timeZoneId": TZ,
        },
        "location": {"address": {"formattedAddress": "Studio"}},
        "registration": {"initialType": "TICKETING", "tickets": {}},
    }


class ClientStub:
    def __init__(self, ticket_defs=None):
        self.ticket_defs = ticket_defs or []
        self.created: List[Dict[str, Any]] = []
        self.ticket_updates: List[Dict[str, Any]] = []

    def get_ticket_definitions(self, event_id, include_sales=False):
        return self.ticket_defs

    def create_ticket_definition(self, **kwargs):
        self.created.append(kwargs)
        return {"initialLimit": kwargs.get("capacity"), "limited": True}

    def update_ticket_definition(self, ticket_def_id, revision, **kwargs):
        self.ticket_updates.append({"id": ticket_def_id, **kwargs})
        return {}

    def update_event(self, event_id, data):
        return {}


# ---------------------------------------------------------------------------
# Creation paths
# ---------------------------------------------------------------------------


def test_single_ticket_creation_carries_policy():
    client = ClientStub()
    ensure_ticket_definition(
        client, "wix-1", make_record(), existing_defs=[], policy_text=POLICY
    )
    assert client.created[0]["policy_text"] == POLICY


def test_multi_ticket_creation_carries_policy_on_every_ticket():
    client = ClientStub()
    record = make_record(
        ticket_name="GA; VIP", ticket_price_raw="25; 50", ticket_capacity="20; 4"
    )
    create_tickets_from_config(
        client, "wix-1", record, existing_defs=[], policy_text=POLICY
    )
    assert len(client.created) == 2
    assert all(c["policy_text"] == POLICY for c in client.created)


def test_creation_without_policy_sends_none():
    client = ClientStub()
    ensure_ticket_definition(client, "wix-1", make_record(), existing_defs=[])
    assert client.created[0]["policy_text"] is None


# ---------------------------------------------------------------------------
# Update plan (diff + patch of existing tickets)
# ---------------------------------------------------------------------------


def ticket_def(policy="", **overrides) -> Dict[str, Any]:
    td = {
        "id": "td-1",
        "revision": "3",
        "name": "Single Ticket",
        "policyText": policy,
        "pricingMethod": {"fixedPrice": {"value": "35.0", "currency": "CAD"}},
        "initialLimit": 24,
    }
    td.update(overrides)
    return td


def test_plan_flags_ticket_missing_the_policy():
    client = ClientStub(ticket_defs=[ticket_def(policy="")])
    plan = compute_event_update_plan(
        client, make_runtime(POLICY), make_record(), "wix-1", make_wix_event()
    )
    assert plan["tickets_changed"] is True
    assert plan["any_changes"] is True
    assert plan["ticket_updates"][0]["new_policy"] == POLICY

    assert apply_event_update_plan(
        client, make_runtime(POLICY), make_record(), "wix-1", make_wix_event(), plan
    ) is True
    assert client.ticket_updates == [
        {"id": "td-1", "price": None, "capacity": None, "policy_text": POLICY}
    ]


def test_plan_skips_ticket_already_carrying_the_policy():
    client = ClientStub(ticket_defs=[ticket_def(policy=POLICY)])
    plan = compute_event_update_plan(
        client, make_runtime(POLICY), make_record(), "wix-1", make_wix_event()
    )
    assert plan["tickets_changed"] is False
    assert plan["any_changes"] is False


def test_blank_setting_never_touches_live_policies():
    """No Settings value -> not managed: a hand-written policy in the
    dashboard must not be diffed away."""
    client = ClientStub(ticket_defs=[ticket_def(policy="Hand-written policy")])
    plan = compute_event_update_plan(
        client, make_runtime(""), make_record(), "wix-1", make_wix_event()
    )
    assert plan["tickets_changed"] is False
    assert plan["any_changes"] is False


def test_policy_merges_into_price_update_for_same_ticket():
    """One PATCH per ticket definition even when price and policy both drift."""
    client = ClientStub(ticket_defs=[ticket_def(policy="")])
    record = make_record(
        ticket_name="Single Ticket", ticket_price_raw="40", ticket_capacity="24"
    )
    plan = compute_event_update_plan(
        client, make_runtime(POLICY), record, "wix-1", make_wix_event()
    )
    assert len(plan["ticket_updates"]) == 1
    tu = plan["ticket_updates"][0]
    assert tu["new_price"] == 40.0
    assert tu["new_policy"] == POLICY

    apply_event_update_plan(
        client, make_runtime(POLICY), record, "wix-1", make_wix_event(), plan
    )
    assert len(client.ticket_updates) == 1
    assert client.ticket_updates[0]["price"] == 40.0
    assert client.ticket_updates[0]["policy_text"] == POLICY


# ---------------------------------------------------------------------------
# Runtime accessor (Settings lookup, caching, truncation, failure)
# ---------------------------------------------------------------------------


def make_sync_runtime(store) -> SyncRuntime:
    runtime = SyncRuntime(
        AppConfig(
            wix_api_key="k",
            wix_account_id=None,
            wix_site_id="s",
            google_credentials_raw=None,
            notion_token="t",
        )
    )
    runtime._notion_store = store
    return runtime


def test_runtime_reads_policy_from_settings_once():
    calls = []

    def fetch_settings():
        calls.append(1)
        return {"default_ticket_policy": f"  {POLICY}  "}

    runtime = make_sync_runtime(SimpleNamespace(fetch_settings=fetch_settings))
    assert runtime.get_ticket_policy_text() == POLICY
    assert runtime.get_ticket_policy_text() == POLICY
    assert len(calls) == 1


def test_runtime_blank_setting_means_off():
    runtime = make_sync_runtime(SimpleNamespace(fetch_settings=lambda: {}))
    assert runtime.get_ticket_policy_text() == ""


def test_runtime_truncates_to_wix_limit():
    long_text = "x" * (MAX_TICKET_POLICY_CHARS + 50)
    runtime = make_sync_runtime(
        SimpleNamespace(fetch_settings=lambda: {"default_ticket_policy": long_text})
    )
    assert runtime.get_ticket_policy_text() == "x" * MAX_TICKET_POLICY_CHARS


def test_runtime_settings_failure_disables_policy_instead_of_crashing():
    def boom():
        raise RuntimeError("Notion down")

    runtime = make_sync_runtime(SimpleNamespace(fetch_settings=boom))
    assert runtime.get_ticket_policy_text() == ""


# ---------------------------------------------------------------------------
# Ticket Policy Status wording (single owner: wix_mapping.ticket_policy_status)
# ---------------------------------------------------------------------------


def defs_with_policies(*policies) -> List[Dict[str, Any]]:
    return [
        {"id": f"td-{i}", "name": f"T{i}", "policyText": p}
        for i, p in enumerate(policies)
    ]


def test_status_blank_when_policy_setting_is_off():
    assert ticket_policy_status(defs_with_policies("", POLICY), "") == ""


def test_status_blank_for_events_without_tickets():
    assert ticket_policy_status([], POLICY) == ""


def test_status_ok_counts_tickets():
    assert ticket_policy_status(defs_with_policies(POLICY), POLICY) == "OK (1 ticket)"
    assert (
        ticket_policy_status(defs_with_policies(POLICY, POLICY, POLICY), POLICY)
        == "OK (3 tickets)"
    )


def test_status_flags_missing_policy():
    assert (
        ticket_policy_status(defs_with_policies("", "", POLICY), POLICY)
        == "2 of 3 tickets missing policy"
    )


def test_status_flags_different_policy():
    assert (
        ticket_policy_status(defs_with_policies("Old text", POLICY), POLICY)
        == "1 of 2 tickets different policy"
    )


def test_status_flags_mixed_drift():
    assert (
        ticket_policy_status(defs_with_policies("", "Old text", POLICY), POLICY)
        == "2 of 3 tickets missing/different policy"
    )


# ---------------------------------------------------------------------------
# Ticket Policy Status writes during the sync Published refresh
# ---------------------------------------------------------------------------


def make_notion_row(**overrides) -> Dict[str, Any]:
    row = {
        "page_id": "page-1",
        "event_name": "Rope Lab",
        "status": "Published",
        "categories": "rope; class",
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
        "tax_name": "HST",
        "tax_rate": "13",
        "tax_type": "ADDED_AT_CHECKOUT",
        "instructor": "",
        "model": "",
        "wix_event_id": "wix-1",
        "synced_hash": "",
        "sync_error": "",
        "ticket_policy_status": "",
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


class RefreshStoreStub:
    def __init__(self, rows: List[Dict[str, Any]]):
        self.rows = rows
        self.upserts: List[tuple] = []
        self.sync_results: List[tuple] = []

    def fetch_event_rows(self, statuses=None, include_missing_status=False):
        return self.rows

    def upsert_event_from_record(self, record, *, status, source=None, page_id=None):
        self.upserts.append((record, status, source, page_id))

    def upsert_event_from_raw_row(self, row, **kwargs):
        pass

    def write_sync_result(self, page_id, **kwargs):
        self.sync_results.append((page_id, kwargs))

    def update_event_fields(self, page_id, props):
        pass

    def fetch_classes(self):
        return {}

    def fetch_settings(self):
        return {}


def make_refresh_runtime(
    store: RefreshStoreStub, client: ClientStub, policy: str = POLICY
) -> SimpleNamespace:
    return SimpleNamespace(
        config=SimpleNamespace(timezone=TZ),
        cache_stats={
            "drive_hits": 0, "drive_misses": 0, "wix_hits": 0, "wix_uploads": 0,
        },
        get_notion_store=lambda: store,
        get_wix_client=lambda: client,
        get_ticket_policy_text=lambda: policy,
    )


def patch_refresh(monkeypatch, config_row):
    monkeypatch.setattr(
        notion_orchestrator,
        "index_events_by_id_and_key",
        lambda runtime, fieldsets=None: (
            {"wix-1": {"id": "wix-1", "status": "UPCOMING"}}, {}
        ),
    )
    monkeypatch.setattr(
        notion_orchestrator,
        "wix_event_to_config_row",
        lambda event, ticket_defs, tz_name=TZ: config_row,
    )
    monkeypatch.setattr(notion_orchestrator.time, "sleep", lambda s: None)


def test_published_refresh_writes_policy_status_column(monkeypatch):
    # Full refresh (content changed in Wix): the record carries the status.
    store = RefreshStoreStub([make_notion_row()])
    client = ClientStub(ticket_defs=[ticket_def(policy="")])
    patch_refresh(
        monkeypatch, make_wix_config_row(short_description="Edited in Wix")
    )

    assert notion_sync_events(
        make_refresh_runtime(store, client), run_enrich=False
    ) is True
    assert len(store.upserts) == 1
    record, status, _, _ = store.upserts[0]
    assert status == "Published"
    assert record.ticket_policy_status == "1 of 1 ticket missing policy"


def test_policy_drift_alone_still_triggers_bookkeeping_write(monkeypatch):
    # Content hash matches Wix, so the fast-path would skip — but the policy
    # column is stale (dashboard-side policy edit) and must be rewritten.
    config_row = make_wix_config_row()
    wix_hash = row_to_event_record(config_row).content_hash()
    store = RefreshStoreStub([
        make_notion_row(synced_hash=wix_hash, ticket_policy_status="OK (1 ticket)")
    ])
    client = ClientStub(ticket_defs=[ticket_def(policy="Old text")])
    patch_refresh(monkeypatch, config_row)

    assert notion_sync_events(
        make_refresh_runtime(store, client), run_enrich=False
    ) is True
    assert store.upserts == []  # no full rewrite
    assert len(store.sync_results) == 1
    _, kwargs = store.sync_results[0]
    assert kwargs["ticket_policy_status"] == "1 of 1 ticket different policy"


def test_matching_policy_status_costs_no_write(monkeypatch):
    config_row = make_wix_config_row()
    wix_hash = row_to_event_record(config_row).content_hash()
    store = RefreshStoreStub([
        make_notion_row(synced_hash=wix_hash, ticket_policy_status="OK (1 ticket)")
    ])
    client = ClientStub(ticket_defs=[ticket_def(policy=POLICY)])
    patch_refresh(monkeypatch, config_row)

    assert notion_sync_events(
        make_refresh_runtime(store, client), run_enrich=False
    ) is True
    assert store.upserts == []
    assert store.sync_results == []


def test_update_push_stamps_ok_status(monkeypatch):
    # A successful Update push converges the policy, so the row is stamped
    # OK directly instead of waiting for the next refresh.
    store = RefreshStoreStub([make_notion_row(status="Update")])
    client = ClientStub()
    patch_refresh(monkeypatch, make_wix_config_row())
    plan = {
        "any_changes": True,
        "event_changed": False,
        "event_diffs": [],
        "change_desc": "tickets",
        "ticket_updates": [],
        "wix_ticket_defs": [ticket_def(policy=""), ticket_def(policy="")],
    }
    monkeypatch.setattr(
        notion_orchestrator,
        "compute_event_update_plan",
        lambda client, runtime, record, wix_id, wix_event: plan,
    )
    monkeypatch.setattr(
        notion_orchestrator,
        "apply_event_update_plan",
        lambda client, runtime, record, wix_id, wix_event, p: True,
    )

    assert notion_sync_events(
        make_refresh_runtime(store, client), run_enrich=False
    ) is True
    assert len(store.sync_results) == 1
    _, kwargs = store.sync_results[0]
    assert kwargs["status"] == "Published"
    assert kwargs["ticket_policy_status"] == "OK (2 tickets)"
