"""Ticket Capacities — the sole per-ticket inventory source.

The event-level Capacity column is gone: ticket inventory (Wix
``initialLimit``) comes only from the semicolon ``Ticket Capacities``
column. These tests pin the invariants of that model:

- create and Update parse the identical spec (a row publishes and updates to
  the same inventory — the old event-Capacity/24 mismatch can't recur),
- a fully blank column means "not managed" in the Update diff (dashboard
  capacities are left alone), and
- the single-ticket path (price, no names) caps with the first value.
"""

from types import SimpleNamespace
from typing import Any, Dict, List

from event_sync.models import EventRecord, parse_tickets
from event_sync.wix_flows import (
    apply_event_update_plan,
    compute_event_update_plan,
    create_tickets_from_config,
    ensure_event_tickets,
    ensure_ticket_definition,
)


TZ = "America/Toronto"


def make_runtime() -> SimpleNamespace:
    return SimpleNamespace(
        config=SimpleNamespace(timezone=TZ),
        get_ticket_policy_text=lambda: "",
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


def ticket_def(name: str, capacity: int, price: str = "35", sold: int = 0) -> Dict[str, Any]:
    return {
        "id": f"td-{name}",
        "revision": "1",
        "name": name,
        "pricingMethod": {"fixedPrice": {"value": price}},
        "initialLimit": capacity,
        "salesDetails": {"soldCount": sold},
    }


class ClientStub:
    def __init__(self, ticket_defs: List[Dict[str, Any]] = None):
        self.ticket_defs = ticket_defs or []
        self.created: List[Dict[str, Any]] = []
        self.ticket_updates: List[Dict[str, Any]] = []

    def get_ticket_definitions(self, event_id, include_sales=False):
        return self.ticket_defs

    def create_ticket_definition(self, **kwargs):
        self.created.append(kwargs)
        return {"initialLimit": kwargs.get("capacity"), "limited": True}

    def update_ticket_definition(self, td_id, revision, **kwargs):
        self.ticket_updates.append({"id": td_id, "revision": revision, **kwargs})
        return {}


# ---------------------------------------------------------------------------
# Create paths
# ---------------------------------------------------------------------------


def test_single_ticket_uses_first_capacity_value():
    client = ClientStub()
    record = make_record(ticket_capacity="60")
    assert ensure_ticket_definition(client, "wix-1", record) is True
    assert client.created[0]["capacity"] == 60


def test_single_ticket_blank_capacity_falls_back_to_default():
    client = ClientStub()
    record = make_record()
    assert ensure_ticket_definition(client, "wix-1", record) is True
    assert client.created[0]["capacity"] == 24


def test_settings_default_reaches_the_create_paths():
    # The default_capacity Setting (threaded through as default_capacity,
    # via runtime.get_default_ticket_capacity) fills blank/invalid entries
    # at ticket-creation time — not the module constant.
    client = ClientStub()
    record = make_record(
        ticket_name="GA; VIP", ticket_price_raw="25; 50", ticket_capacity="50; "
    )
    assert ensure_event_tickets(
        client, "wix-1", record, existing_defs=[], default_capacity=30
    ) is True
    assert [c["capacity"] for c in client.created] == [50, 30]

    single = ClientStub()
    assert ensure_event_tickets(
        single, "wix-1", make_record(), existing_defs=[], default_capacity=30
    ) is True
    assert single.created[0]["capacity"] == 30


def test_multi_ticket_single_value_covers_every_type():
    client = ClientStub()
    record = make_record(
        ticket_name="GA; VIP", ticket_price_raw="25; 50", ticket_capacity="100"
    )
    assert create_tickets_from_config(client, "wix-1", record) is True
    assert [c["capacity"] for c in client.created] == [100, 100]


def test_create_and_update_parse_identical_capacities():
    # The old bug: create used the event Capacity as the blank-entry default
    # while the update diff used the parser default — an Update flip could
    # silently shrink live inventory. Both paths now share one parse.
    record = make_record(
        ticket_name="GA; VIP", ticket_price_raw="25; 50", ticket_capacity="100"
    )
    create_specs = parse_tickets(
        ticket_name=record.ticket_name,
        ticket_price=record.ticket_price_raw or record.ticket_price,
        ticket_capacity=record.ticket_capacity,
    )
    client = ClientStub([ticket_def("GA", 100, "25"), ticket_def("VIP", 100, "50")])
    plan = compute_event_update_plan(
        client, make_runtime(), record, "wix-1", make_wix_event()
    )
    assert [s.capacity for s in create_specs] == [100, 100]
    assert plan["tickets_changed"] is False


# ---------------------------------------------------------------------------
# Update plan
# ---------------------------------------------------------------------------


def test_update_plan_patches_managed_capacity():
    client = ClientStub([ticket_def("GA", 24, "35")])
    record = make_record(
        ticket_name="GA", ticket_price_raw="35", ticket_capacity="60"
    )
    plan = compute_event_update_plan(
        client, make_runtime(), record, "wix-1", make_wix_event()
    )
    assert plan["tickets_changed"] is True
    (update,) = plan["ticket_updates"]
    assert update["new_capacity"] == 60
    assert update["old_capacity"] == 24


def test_apply_plan_patches_the_ticket_definition_capacity():
    client = ClientStub([ticket_def("GA", 24, "35")])
    record = make_record(
        ticket_name="GA", ticket_price_raw="35", ticket_capacity="60"
    )
    wix_event = make_wix_event()
    plan = compute_event_update_plan(
        client, make_runtime(), record, "wix-1", wix_event
    )
    assert apply_event_update_plan(
        client, make_runtime(), record, "wix-1", wix_event, plan
    ) is True
    (patch,) = client.ticket_updates
    assert patch["id"] == "td-GA"
    assert patch["capacity"] == 60


def test_blank_capacities_are_not_managed_in_update_plan():
    # A dashboard-set inventory (100) must not be dragged down to the
    # parser's fallback default just because the Notion column is blank.
    client = ClientStub([ticket_def("GA", 100, "35")])
    record = make_record(ticket_name="GA", ticket_price_raw="35")
    plan = compute_event_update_plan(
        client, make_runtime(), record, "wix-1", make_wix_event()
    )
    assert plan["tickets_changed"] is False


def test_blank_entry_leaves_that_live_ticket_alone():
    # "100; ; 50": VIP's slot is deliberately blank -> not managed for that
    # entry; the explicit entries diff normally. A blanked slot must never
    # drag live inventory to the parser's fallback default.
    client = ClientStub([
        ticket_def("GA", 100, "25"),
        ticket_def("VIP", 30, "50"),
        ticket_def("Crew", 40, "10"),
    ])
    record = make_record(
        ticket_name="GA; VIP; Crew",
        ticket_price_raw="25; 50; 10",
        ticket_capacity="100; ; 50",
    )
    plan = compute_event_update_plan(
        client, make_runtime(), record, "wix-1", make_wix_event()
    )
    updates = {u["name"]: u for u in plan["ticket_updates"]}
    assert "VIP" not in updates
    assert "GA" not in updates  # matches live, nothing to do
    assert updates["Crew"]["new_capacity"] == 50


def test_invalid_entry_is_not_managed_in_update_plan():
    # A typo ("1OO") must not rewrite the live inventory to the default.
    client = ClientStub([ticket_def("GA", 50, "35")])
    record = make_record(
        ticket_name="GA", ticket_price_raw="35", ticket_capacity="1OO"
    )
    plan = compute_event_update_plan(
        client, make_runtime(), record, "wix-1", make_wix_event()
    )
    assert plan["tickets_changed"] is False


def test_tail_inheritance_is_managed_in_update_plan():
    # The single-value-covers-all rule applies on update too: "80" manages
    # every ticket type, not just the first.
    client = ClientStub([ticket_def("GA", 80, "25"), ticket_def("VIP", 30, "50")])
    record = make_record(
        ticket_name="GA; VIP", ticket_price_raw="25; 50", ticket_capacity="80"
    )
    plan = compute_event_update_plan(
        client, make_runtime(), record, "wix-1", make_wix_event()
    )
    updates = {u["name"]: u for u in plan["ticket_updates"]}
    assert "GA" not in updates
    assert updates["VIP"]["new_capacity"] == 80


def test_capacity_reduction_below_sold_is_blocked():
    client = ClientStub([ticket_def("GA", 100, "35", sold=42)])
    record = make_record(
        ticket_name="GA", ticket_price_raw="35", ticket_capacity="30"
    )
    plan = compute_event_update_plan(
        client, make_runtime(), record, "wix-1", make_wix_event()
    )
    assert plan["tickets_changed"] is False
