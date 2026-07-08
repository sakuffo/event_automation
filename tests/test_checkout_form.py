"""Checkout Form — one registration form per ticket vs per order.

Maps to the Wix event-level ``registration.tickets.guestsAssignedSeparately``
boolean (PER_TICKET = true, PER_ORDER = false). Modeled exactly like the
Ticket Limit Per Order column: a blank Notion value means "not managed" —
nothing sent on create, never diffed on update — so events configured by hand
in the Wix dashboard are left alone.

These tests pin the full path: the model validator, the create payload, the
pull/refresh read-back, and the Update-row diff/patch.
"""

from types import SimpleNamespace
from typing import Any, Dict, List, Tuple

import pytest
from pydantic import ValidationError

from event_sync.models import EventRecord
from event_sync.wix_flows import apply_event_update_plan, compute_event_update_plan
from event_sync.wix_mapping import (
    build_wix_event_payload,
    checkout_form_to_guests_assigned,
    guests_assigned_to_checkout_form,
    wix_event_to_config_row,
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
        capacity=24,
        registration_type="TICKETS",
        checkout_form="PER_TICKET",
    )
    payload.update(overrides)
    return EventRecord(**payload)


def make_wix_event(guests_assigned=False) -> Dict[str, Any]:
    """A live Wix event matching make_record() on every diffed field
    except (potentially) the checkout form. 19:00/22:00 EDT == 23:00/02:00 UTC.
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
            "tickets": {
                "ticketLimitPerOrder": 20,
                "guestsAssignedSeparately": guests_assigned,
            },
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
# Model validator
# ---------------------------------------------------------------------------


def test_validator_normalizes_case_and_separators():
    assert make_record(checkout_form="per ticket").checkout_form == "PER_TICKET"
    assert make_record(checkout_form="per-order").checkout_form == "PER_ORDER"


def test_validator_blank_means_not_managed():
    assert make_record(checkout_form="").checkout_form is None
    assert make_record(checkout_form=None).checkout_form is None
    assert make_record(checkout_form="   ").checkout_form is None


def test_validator_rejects_unknown_values():
    with pytest.raises(ValidationError, match="PER_TICKET or PER_ORDER"):
        make_record(checkout_form="EVERY_OTHER_TICKET")


def test_converters_round_trip():
    assert checkout_form_to_guests_assigned("PER_TICKET") is True
    assert checkout_form_to_guests_assigned("PER_ORDER") is False
    assert checkout_form_to_guests_assigned("") is None
    assert checkout_form_to_guests_assigned(None) is None
    assert guests_assigned_to_checkout_form(True) == "PER_TICKET"
    assert guests_assigned_to_checkout_form(False) == "PER_ORDER"
    assert guests_assigned_to_checkout_form(None) == ""


# ---------------------------------------------------------------------------
# Create payload
# ---------------------------------------------------------------------------


def test_create_payload_sets_guests_assigned_separately():
    payload = build_wix_event_payload(make_record(), make_runtime())
    tickets = payload["registration"]["tickets"]
    assert tickets["guestsAssignedSeparately"] is True


def test_create_payload_sets_false_for_per_order():
    payload = build_wix_event_payload(
        make_record(checkout_form="PER_ORDER"), make_runtime()
    )
    assert payload["registration"]["tickets"]["guestsAssignedSeparately"] is False


def test_create_payload_omits_field_when_blank():
    """A blank column defers to the Wix default rather than sending false."""
    payload = build_wix_event_payload(
        make_record(checkout_form=None), make_runtime()
    )
    assert "guestsAssignedSeparately" not in payload["registration"]["tickets"]


# ---------------------------------------------------------------------------
# Read side (pull / Published refresh)
# ---------------------------------------------------------------------------


def test_config_row_reads_per_ticket_from_wix_event():
    row = wix_event_to_config_row(make_wix_event(guests_assigned=True), [], tz_name=TZ)
    assert row["checkout_form"] == "PER_TICKET"


def test_config_row_reads_per_order_when_flag_absent():
    # Wix omits false booleans: a ticketed event without the field is
    # one-form-per-order.
    event = make_wix_event()
    del event["registration"]["tickets"]["guestsAssignedSeparately"]
    row = wix_event_to_config_row(event, [], tz_name=TZ)
    assert row["checkout_form"] == "PER_ORDER"


def test_config_row_blank_for_events_without_tickets():
    event = make_wix_event()
    event["registration"] = {"initialType": "RSVP"}
    row = wix_event_to_config_row(event, [], tz_name=TZ)
    assert row["checkout_form"] == ""


# ---------------------------------------------------------------------------
# Update plan (diff + patch)
# ---------------------------------------------------------------------------


def test_update_plan_diffs_and_patches_checkout_form():
    client = ClientStub()
    record = make_record(checkout_form="PER_TICKET")
    wix_event = make_wix_event(guests_assigned=False)

    plan = compute_event_update_plan(client, make_runtime(), record, "wix-1", wix_event)
    assert plan["form_changed"] is True
    assert plan["any_changes"] is True
    assert "checkout form" in plan["change_desc"]
    # Only the checkout form differs — nothing else should be flagged.
    assert plan["event_changed"] is False
    assert plan["tax_changed"] is False
    assert plan["limit_changed"] is False

    assert apply_event_update_plan(
        client, make_runtime(), record, "wix-1", wix_event, plan
    ) is True
    assert client.update_calls == [
        ("wix-1", {"registration": {"tickets": {"guestsAssignedSeparately": True}}})
    ]


def test_update_plan_skips_matching_form():
    client = ClientStub()
    record = make_record(checkout_form="PER_TICKET")
    plan = compute_event_update_plan(
        client, make_runtime(), record, "wix-1", make_wix_event(guests_assigned=True)
    )
    assert plan["form_changed"] is False
    assert plan["any_changes"] is False


def test_blank_notion_form_is_not_managed():
    """No value in Notion -> never diff, so dashboard-configured events are
    left alone instead of being reset."""
    client = ClientStub()
    record = make_record(checkout_form=None)
    plan = compute_event_update_plan(
        client, make_runtime(), record, "wix-1", make_wix_event(guests_assigned=True)
    )
    assert plan["form_changed"] is False
    assert plan["any_changes"] is False
