"""Unit tests for pydantic event models."""

import pytest

from event_sync.models import EventRecord, ValidationError


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
        "capacity": 25,
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


def test_event_record_capacity_must_be_positive():
    with pytest.raises(ValidationError):
        EventRecord(**build_event(capacity=0))


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


