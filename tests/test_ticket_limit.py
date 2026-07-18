"""Ticket Limit Per Order — the explicit max-tickets-per-checkout column.

Wix has two limits that are easy to confuse:

- the per-ticket-definition ``limitPerCheckout`` is **read-only** (Wix derives
  it from remaining stock), and
- the event-level ``registration.tickets.ticketLimitPerOrder`` is the writable
  knob, defaulting to 20 when never set.

These tests pin the full path of the explicit column: the create payload, the
pull/refresh read-back, and the Update-row diff/patch.
"""

from types import SimpleNamespace
from typing import Any, Dict, List, Tuple

from event_sync.models import EventRecord
from event_sync.wix_flows import apply_event_update_plan, compute_event_update_plan
from event_sync.wix_mapping import build_wix_event_payload, wix_event_to_config_row


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
        ticket_limit_per_order=4,
    )
    payload.update(overrides)
    return EventRecord(**payload)


def make_wix_event(ticket_limit=20) -> Dict[str, Any]:
    """A live Wix event that matches make_record() on every diffed field
    except (potentially) the ticket limit. 19:00/22:00 EDT == 23:00/02:00 UTC.
    """
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
        "registration": {
            "initialType": "TICKETING",
            "tickets": {"ticketLimitPerOrder": ticket_limit},
        },
    }


class ClientStub:
    def __init__(self):
        self.update_calls: List[Tuple[str, Dict[str, Any]]] = []

    def get_ticket_definitions(self, event_id, include_sales=False):
        return []

    def update_event(self, event_id, data):
        self.update_calls.append((event_id, data))
        return {}


# ---------------------------------------------------------------------------
# Create payload
# ---------------------------------------------------------------------------


def test_create_payload_sets_event_level_ticket_limit():
    payload = build_wix_event_payload(make_record(), make_runtime())
    tickets = payload["registration"]["tickets"]
    assert tickets["ticketLimitPerOrder"] == 4


def test_create_payload_omits_limit_when_blank():
    """A blank column defers to the Wix default (20) rather than sending 0."""
    payload = build_wix_event_payload(
        make_record(ticket_limit_per_order=None), make_runtime()
    )
    assert "ticketLimitPerOrder" not in payload["registration"]["tickets"]


# ---------------------------------------------------------------------------
# Read side (pull / Published refresh)
# ---------------------------------------------------------------------------


def test_config_row_reads_limit_from_wix_event():
    row = wix_event_to_config_row(make_wix_event(ticket_limit=20), [], tz_name=TZ)
    assert row["ticket_limit_per_order"] == "20"


def test_config_row_blank_for_events_without_tickets():
    event = make_wix_event()
    event["registration"] = {"initialType": "RSVP"}
    row = wix_event_to_config_row(event, [], tz_name=TZ)
    assert row["ticket_limit_per_order"] == ""


# ---------------------------------------------------------------------------
# Update plan (diff + patch)
# ---------------------------------------------------------------------------


def test_update_plan_diffs_and_patches_ticket_limit():
    client = ClientStub()
    record = make_record(ticket_limit_per_order=4)
    wix_event = make_wix_event(ticket_limit=20)

    plan = compute_event_update_plan(client, make_runtime(), record, "wix-1", wix_event)
    assert plan["limit_changed"] is True
    assert plan["any_changes"] is True
    assert "ticket limit per order" in plan["change_desc"]
    # Only the limit differs — nothing else should be flagged.
    assert plan["event_changed"] is False
    assert plan["tax_changed"] is False

    assert apply_event_update_plan(
        client, make_runtime(), record, "wix-1", wix_event, plan
    ) is True
    assert client.update_calls == [
        ("wix-1", {"registration": {"tickets": {"ticketLimitPerOrder": 4}}})
    ]


def test_update_plan_skips_matching_limit():
    client = ClientStub()
    record = make_record(ticket_limit_per_order=4)
    plan = compute_event_update_plan(
        client, make_runtime(), record, "wix-1", make_wix_event(ticket_limit=4)
    )
    assert plan["limit_changed"] is False
    assert plan["any_changes"] is False


def test_blank_notion_limit_is_not_managed():
    """No value in Notion -> never diff, so dashboard-configured events are
    left alone instead of being reset."""
    client = ClientStub()
    record = make_record(ticket_limit_per_order=None)
    plan = compute_event_update_plan(
        client, make_runtime(), record, "wix-1", make_wix_event(ticket_limit=20)
    )
    assert plan["limit_changed"] is False
    assert plan["any_changes"] is False
