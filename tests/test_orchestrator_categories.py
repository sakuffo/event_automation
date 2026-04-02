from types import SimpleNamespace

from event_sync.models import EventRecord
from event_sync.orchestrator import _wix_timestamp, needs_update


def _build_runtime(timezone: str = "America/Toronto"):
    return SimpleNamespace(config=SimpleNamespace(timezone=timezone))


def _build_existing_event(event: EventRecord, timezone: str, category_ids):
    return {
        "title": event.name,
        "dateAndTimeSettings": {
            "startDate": _wix_timestamp(event.start_date, event.start_time, timezone),
            "endDate": _wix_timestamp(event.end_date, event.end_time, timezone),
            "timeZoneId": timezone,
        },
        "location": {"address": {"formattedAddress": event.location}},
        "registration": {"initialType": event.registration_type},
        "shortDescription": "",
        "detailedDescription": "",
        "categoryIds": category_ids,
    }


def test_needs_update_detects_category_changes(monkeypatch):
    event = EventRecord(
        name="Rope Lab",
        categories=["rope"],
        start_date="2026-03-01",
        start_time="19:00",
        end_date="2026-03-01",
        end_time="21:00",
        location="Studio",
        registration_type="RSVP",
    )
    runtime = _build_runtime()

    monkeypatch.setattr(
        "event_sync.orchestrator._resolve_wix_category_ids",
        lambda event, runtime: ["cat-expected"],
    )

    existing = _build_existing_event(event, runtime.config.timezone, ["cat-other"])
    assert needs_update(event, existing, runtime) is True


def test_needs_update_skips_when_category_ids_match(monkeypatch):
    event = EventRecord(
        name="Rope Lab",
        categories=["rope"],
        start_date="2026-03-01",
        start_time="19:00",
        end_date="2026-03-01",
        end_time="21:00",
        location="Studio",
        registration_type="RSVP",
    )
    runtime = _build_runtime()

    monkeypatch.setattr(
        "event_sync.orchestrator._resolve_wix_category_ids",
        lambda event, runtime: ["cat-expected"],
    )

    existing = _build_existing_event(event, runtime.config.timezone, ["cat-expected"])
    assert needs_update(event, existing, runtime) is False
