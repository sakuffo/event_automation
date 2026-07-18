"""Unit tests for pydantic event models."""

import pytest

from event_sync.models import (
    EventRecord,
    ValidationError,
    parse_tickets,
    single_ticket_capacity,
)


def build_event(**overrides):
    payload = {
        "name": "Sample Event",
        "event_type": "Workshop",
        "start_date": "2026-01-25",
        "start_time": "12:00",
        "end_date": "2026-01-25",
        "end_time": "14:00",
        "location": "123 Fake St",
        "ticket_price": 10.5,
        "registration_type": "tickets",
        "image_url": "https://example.com/img.jpg",
    }
    payload.update(overrides)
    return payload


def test_event_record_normalizes_registration_type():
    record = EventRecord(**build_event())
    assert record.registration_type == "TICKETING"


def test_event_record_validates_dates_and_times():
    record = EventRecord(**build_event(start_date="01/25/2026", end_time="16:30"))
    assert record.start_date == "2026-01-25"
    assert record.end_time == "16:30"


def test_event_record_rejects_bad_time():
    with pytest.raises(ValidationError):
        EventRecord(**build_event(start_time="25:00"))


class TestTicketCapacityParsing:
    """Ticket Capacities is the sole inventory source (no event Capacity)."""

    def test_missing_tail_capacities_inherit_the_last_value(self):
        # A single value covers every ticket type.
        specs = parse_tickets("GA; VIP; Crew", "25; 50; 0", "100")
        assert [s.capacity for s in specs] == [100, 100, 100]

    def test_explicit_capacities_apply_positionally(self):
        specs = parse_tickets("GA; VIP", "25; 50", "100; 4")
        assert [s.capacity for s in specs] == [100, 4]

    def test_blank_capacities_fall_back_to_the_default(self):
        specs = parse_tickets("GA; VIP", "25; 50", None, default_capacity=24)
        assert [s.capacity for s in specs] == [24, 24]

    def test_unparseable_entry_gets_the_default_not_the_neighbor(self):
        specs = parse_tickets("GA; VIP", "25; 50", "1OO; 4", default_capacity=24)
        assert [s.capacity for s in specs] == [24, 4]

    def test_non_positive_capacities_fall_back_to_the_default(self):
        # Inventory must be a positive integer — 0/negatives would create
        # unlimited Wix tickets, which this pipeline never does. And because
        # missing tail entries inherit the last value, a rejected entry must
        # not spread: the default takes its place instead.
        specs = parse_tickets("GA; VIP; Crew", "25; 50; 10", "-5")
        assert [s.capacity for s in specs] == [24, 24, 24]
        specs = parse_tickets("GA; VIP", "25; 50", "0; 100", default_capacity=24)
        assert [s.capacity for s in specs] == [24, 100]

    def test_single_ticket_capacity_takes_the_first_value(self):
        assert single_ticket_capacity("60") == 60
        assert single_ticket_capacity("60; 10") == 60
        assert single_ticket_capacity("", default_capacity=24) == 24
        assert single_ticket_capacity(None, default_capacity=24) == 24
        assert single_ticket_capacity("lots", default_capacity=24) == 24
        assert single_ticket_capacity("0", default_capacity=24) == 24
        assert single_ticket_capacity("-5", default_capacity=24) == 24

    def test_float_formatted_capacities_round_to_whole_tickets(self):
        # "50.0" is a number, not a typo — the price column and the hash
        # canonicalizer accept float formatting, so capacities do too.
        specs = parse_tickets("GA", "25", "50.0")
        assert specs[0].capacity == 50
        assert single_ticket_capacity("50.0") == 50


def test_hash_keeps_empty_capacity_tokens_positionally():
    # "20; ; 4" (middle ticket unlimited on the live side) must not hash
    # like "20; 4" or "20; 4; " — otherwise the Published refresh would
    # permanently miss a dashboard edit that un-limits a middle ticket.
    def record_with(caps):
        return EventRecord(
            **build_event(
                ticket_name="GA; VIP; Crew",
                ticket_price_raw="10; 20; 30",
                ticket_capacity=caps,
            )
        )

    hashes = {
        record_with("20; 4").content_hash(),
        record_with("20; ; 4").content_hash(),
        record_with("20; 4; ").content_hash(),
    }
    assert len(hashes) == 3
    # Pure formatting drift still collapses.
    assert record_with("20;4").content_hash() == record_with("20; 4").content_hash()
    assert record_with("20.0; 4").content_hash() == record_with("20; 4").content_hash()


def test_ticket_limit_parses_strings_and_blank():
    assert EventRecord(**build_event(ticket_limit_per_order="4")).ticket_limit_per_order == 4
    assert EventRecord(**build_event(ticket_limit_per_order="")).ticket_limit_per_order is None
    assert EventRecord(**build_event()).ticket_limit_per_order is None


def test_ticket_limit_must_be_within_wix_bounds():
    with pytest.raises(ValidationError, match="between 1 and 50"):
        EventRecord(**build_event(ticket_limit_per_order=0))
    with pytest.raises(ValidationError, match="between 1 and 50"):
        EventRecord(**build_event(ticket_limit_per_order=51))
    with pytest.raises(ValidationError, match="must be a number"):
        EventRecord(**build_event(ticket_limit_per_order="lots"))


def test_ticket_limit_changes_content_hash():
    a = EventRecord(**build_event(ticket_limit_per_order=4))
    b = EventRecord(**build_event(ticket_limit_per_order=10))
    blank = EventRecord(**build_event())
    assert a.content_hash() != b.content_hash()
    assert a.content_hash() != blank.content_hash()


