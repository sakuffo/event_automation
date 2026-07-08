"""Tests for the recurring-event template import and type-aware enrichment."""

from typing import Any, Dict

from event_sync.notion_orchestrator import (
    _apply_row_defaults,
    select_template_sources,
)
from event_sync.notion_store import EventProps


# ---------------------------------------------------------------------------
# select_template_sources
# ---------------------------------------------------------------------------


def csv_row(**overrides) -> Dict[str, Any]:
    row = {
        "default_event": "Voyeur",
        "include_in_feed": "TRUE",
        "title": "Voyeur",
        "start_local_date": "2026-05-02",
        "start_utc": "2026-05-02T23:00:00Z",
        "lowest_ticket_price": "",
        "categories": "play; show; party",
        "short_description": "teaser",
        "description": "body",
        "main_image_url": "https://static.wixstatic.com/media/x.png",
    }
    row.update(overrides)
    return row


def test_latest_feed_eligible_instance_wins():
    rows = [
        csv_row(start_local_date="2025-10-11", title="Voyeur (old)"),
        csv_row(start_local_date="2026-05-02", title="Voyeur (new)"),
        csv_row(start_local_date="2026-01-24", title="Voyeur (mid)"),
    ]
    chosen = select_template_sources(rows)
    assert chosen["Voyeur"]["title"] == "Voyeur (new)"


def test_feed_false_and_familyless_rows_are_ignored():
    rows = [
        csv_row(start_local_date="2026-06-01", include_in_feed="FALSE"),
        csv_row(default_event="", start_local_date="2026-07-01"),
        csv_row(start_local_date="2026-01-24"),
    ]
    chosen = select_template_sources(rows)
    assert list(chosen) == ["Voyeur"]
    assert chosen["Voyeur"]["start_local_date"] == "2026-01-24"


def test_families_are_independent():
    rows = [
        csv_row(),
        csv_row(default_event="Sweat", title="SWEAT", start_local_date="2026-07-18"),
    ]
    chosen = select_template_sources(rows)
    assert set(chosen) == {"Voyeur", "Sweat"}


def tt_row(**overrides) -> Dict[str, Any]:
    base = csv_row(
        default_event="Tinker Tuesday",
        title="Tinker Tuesday",
        lowest_ticket_price="25",
    )
    base.update(overrides)
    return base


def test_tinker_tuesday_requires_25_base_price_and_skips_sunday():
    rows = [
        # Latest overall, but HST baked into the price -> skipped.
        tt_row(start_local_date="2026-12-15", lowest_ticket_price="28.25"),
        # Sunday special -> skipped even with the right price.
        tt_row(
            start_local_date="2026-12-20",
            title="Tinker SUNDAY Afternoon Holiday Special",
        ),
        # Latest $25 weekday instance -> the baseline.
        tt_row(start_local_date="2026-12-08"),
        tt_row(start_local_date="2026-11-10"),
        # Blank price -> skipped.
        tt_row(start_local_date="2026-12-22", lowest_ticket_price=""),
    ]
    chosen = select_template_sources(rows)
    pick = chosen["Tinker Tuesday"]
    assert pick["start_local_date"] == "2026-12-08"
    assert pick["lowest_ticket_price"] == "25"


# ---------------------------------------------------------------------------
# _apply_row_defaults type-awareness
# ---------------------------------------------------------------------------


def event_row(**overrides) -> Dict[str, Any]:
    row = {
        "event_name": "Tinker Tuesday",
        "categories": "",
        "location": "somewhere",
        "registration_type": "TICKETS",
        "capacity": "24",
        "ticket_price": "",
        "short_description": "",
        "detailed_description": "",
        "image_url": "https://example.com/img.png",
        "tax_name": "HST",
        "tax_rate": "13",
        "tax_type": "ADDED_AT_CHECKOUT",
        "fee_type": "FEE_ADDED_AT_CHECKOUT",
        "instructor": "",
        "model": "",
    }
    row.update(overrides)
    return row


def template(**overrides) -> Dict[str, Any]:
    tpl = {
        "page_id": "tpl-1",
        "class": "Tinker Tuesday",
        "type": "event",
        "categories": ["jam", "culture", "social"],
        "tagline": "Calling all rope nerds",
        "description": "Bring a project.",
        "image_url": "https://static.wixstatic.com/media/t.png",
        "price_override": None,
        "default_capacity": None,
    }
    tpl.update(overrides)
    return tpl


def test_event_template_gets_no_baseline_tags():
    row = event_row()
    _apply_row_defaults(row, template(), {})
    assert row["categories"] == "jam; culture; social"


def test_class_template_still_gets_baseline_tags():
    row = event_row(event_name="Rope Lab")
    klass = template(type="class", categories=["suspension-lines"])
    _apply_row_defaults(row, klass, {})
    tags = [t.strip() for t in row["categories"].split(";")]
    assert "rope" in tags and "class" in tags and "suspension-lines" in tags


def test_blank_type_behaves_as_class():
    row = event_row(event_name="Rope Lab")
    klass = template(categories=["suspension-lines"])
    klass.pop("type")
    klass["type"] = ""
    # fetch_classes defaults blank to "class", but be safe against raw dicts.
    klass["type"] = klass["type"] or "class"
    _apply_row_defaults(row, klass, {})
    tags = [t.strip() for t in row["categories"].split(";")]
    assert "class" in tags


def test_event_template_price_comes_only_from_override():
    row = event_row()
    props, _ = _apply_row_defaults(row, template(price_override=25.0), {})
    assert row["ticket_price"] == "25"
    assert props[EventProps.TICKET_PRICE] == {"number": 25.0}


def test_event_template_zero_override_is_honored():
    row = event_row()
    _apply_row_defaults(row, template(price_override=0.0), {})
    assert row["ticket_price"] == "0"


def test_event_template_without_override_falls_back_to_default_price():
    # Before the guaranteed-ticket-defaults change this stayed blank and the
    # event published with no tickets at all; ticketed rows now land on the
    # global default_ticket_price instead.
    row = event_row()
    props, _ = _apply_row_defaults(row, template(), {})
    assert row["ticket_price"] == "30"
    assert props[EventProps.TICKET_PRICE] == {"number": 30.0}


def test_class_template_keeps_30_dollar_fallback():
    row = event_row(event_name="Rope Lab")
    klass = template(type="class", categories=["unpriced-category"])
    _apply_row_defaults(row, klass, {})
    assert row["ticket_price"] == "30"
