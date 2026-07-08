"""Unit tests for the row default-fill helper used by enrich and sync."""

from types import SimpleNamespace
from typing import Any, Dict

import pytest
from pydantic import ValidationError

from event_sync.models import EventRecord
from event_sync.notion_orchestrator import (
    DEFAULT_SETTINGS_SEED,
    _apply_row_defaults,
    enrich_events,
)
from event_sync.notion_store import EventProps


def bare_row(**overrides) -> Dict[str, Any]:
    row = {
        "page_id": "page-1",
        "event_name": "Improvised Suspensions 2",
        "status": "Ready",
        "categories": "",
        "start_date": "2026-09-01",
        "start_time": "19:00",
        "end_date": "2026-09-01",
        "end_time": "21:00",
        "location": "",
        "registration_type": "",
        "capacity": "",
        "ticket_price": "",
        "image_url": "",
        "short_description": "",
        "detailed_description": "",
        "ticket_name": "",
        "ticket_capacity": "",
        "ticket_limit_per_order": "",
        "checkout_form": "",
        "fee_type": "",
        "sale_start": "",
        "sale_end": "",
        "tax_name": "",
        "tax_rate": "",
        "tax_type": "",
        "instructor": "",
        "model": "",
        "wix_event_id": "",
        "synced_hash": "",
        "sync_error": "",
        "template_relation_ids": [],
    }
    row.update(overrides)
    return row


def sample_class(**overrides) -> Dict[str, Any]:
    klass = {
        "page_id": "class-1",
        "class": "Improvised Suspensions",
        "categories": ["The Body in Flight"],
        "tagline": "Suspend on harnesses built on the fly",
        "description": "Class body text",
        "image_url": "https://drive.google.com/file/d/abc/view",
        "price_override": None,
        "default_capacity": None,
        "default_ticket_names": "",
        "default_ticket_prices": "",
        "default_ticket_capacities": "",
    }
    klass.update(overrides)
    return klass


class TestApplyRowDefaults:
    def test_fills_everything_on_bare_ticketed_row(self):
        row = bare_row()
        props, changes = _apply_row_defaults(row, None, {})

        assert row["location"]  # constants default
        assert row["registration_type"] == "TICKETS"
        assert row["capacity"] == "24"
        assert row["ticket_price"] == "30"  # global default price
        assert row["tax_name"] == "HST"
        assert row["tax_rate"] == "13"
        assert row["tax_type"] == "ADDED_AT_CHECKOUT"
        assert row["fee_type"] == "FEE_ADDED_AT_CHECKOUT"
        assert row["ticket_limit_per_order"] == "4"
        assert "tax" in changes and "fee type" in changes
        assert EventProps.TAX_RATE in props
        assert props[EventProps.TAX_RATE]["number"] == 13.0
        assert props[EventProps.TICKET_PRICE]["number"] == 30.0
        assert EventProps.FEE_TYPE in props
        assert props[EventProps.TICKET_LIMIT_PER_ORDER]["number"] == 4

    def test_settings_values_win_over_constants(self):
        settings = {
            "default_location": "123 New Studio Ave",
            "default_capacity": "18",
            "default_tax_rate": "15",
            "default_fee_type": "NO_FEE",
            "default_ticket_limit_per_order": "6",
        }
        row = bare_row()
        _apply_row_defaults(row, None, settings)

        assert row["location"] == "123 New Studio Ave"
        assert row["capacity"] == "18"
        assert row["tax_rate"] == "15"
        assert row["fee_type"] == "NO_FEE"
        assert row["ticket_limit_per_order"] == "6"

    def test_existing_values_are_never_overwritten(self):
        row = bare_row(
            location="Custom Venue",
            registration_type="RSVP",
            capacity="10",
        )
        props, changes = _apply_row_defaults(row, None, {})

        assert row["location"] == "Custom Venue"
        assert row["registration_type"] == "RSVP"
        assert row["capacity"] == "10"
        assert EventProps.LOCATION not in props
        assert EventProps.REGISTRATION_TYPE not in props
        assert EventProps.CAPACITY not in props

    def test_no_tax_or_fee_for_rsvp_rows(self):
        row = bare_row(registration_type="RSVP")
        props, _ = _apply_row_defaults(row, None, {})

        assert row["tax_name"] == ""
        assert row["fee_type"] == ""
        assert row["ticket_limit_per_order"] == ""
        assert EventProps.TAX_NAME not in props
        assert EventProps.FEE_TYPE not in props
        assert EventProps.TICKET_LIMIT_PER_ORDER not in props

    def test_typed_ticket_limit_is_never_overwritten(self):
        row = bare_row(ticket_limit_per_order="2")
        props, _ = _apply_row_defaults(row, None, {})

        assert row["ticket_limit_per_order"] == "2"
        assert EventProps.TICKET_LIMIT_PER_ORDER not in props

    def test_out_of_range_limit_setting_falls_back_to_constant(self):
        row = bare_row()
        _apply_row_defaults(row, None, {"default_ticket_limit_per_order": "80"})
        assert row["ticket_limit_per_order"] == "4"

    def test_checkout_form_not_filled_when_setting_blank(self):
        # Blank default = not managed: rows stay blank, Wix keeps its own.
        row = bare_row()
        props, _ = _apply_row_defaults(row, None, {})
        assert row["checkout_form"] == ""
        assert EventProps.CHECKOUT_FORM not in props

    def test_checkout_form_filled_from_setting_on_ticketed_rows(self):
        row = bare_row()
        props, changes = _apply_row_defaults(
            row, None, {"default_checkout_form": "per_ticket"}
        )
        assert row["checkout_form"] == "PER_TICKET"
        assert props[EventProps.CHECKOUT_FORM]["select"]["name"] == "PER_TICKET"
        assert "checkout form PER_TICKET" in changes

    def test_checkout_form_not_filled_on_rsvp_rows(self):
        row = bare_row(registration_type="RSVP")
        props, _ = _apply_row_defaults(
            row, None, {"default_checkout_form": "PER_TICKET"}
        )
        assert row["checkout_form"] == ""
        assert EventProps.CHECKOUT_FORM not in props

    def test_typed_checkout_form_is_never_overwritten(self):
        row = bare_row(checkout_form="PER_ORDER")
        props, _ = _apply_row_defaults(
            row, None, {"default_checkout_form": "PER_TICKET"}
        )
        assert row["checkout_form"] == "PER_ORDER"
        assert EventProps.CHECKOUT_FORM not in props

    def test_invalid_checkout_form_setting_is_ignored(self):
        row = bare_row()
        props, _ = _apply_row_defaults(
            row, None, {"default_checkout_form": "SOMETIMES"}
        )
        assert row["checkout_form"] == ""
        assert EventProps.CHECKOUT_FORM not in props

    def test_class_fills_categories_price_and_content(self):
        row = bare_row()
        klass = sample_class()
        props, changes = _apply_row_defaults(row, klass, {"default_img": "https://x/img.png"})

        assert row["categories"] == "the-body-in-flight; rope; class"
        # "The Body in Flight" is a $40 category in CATEGORY_PRICING
        assert row["ticket_price"] == "40"
        assert row["short_description"] == klass["tagline"]
        assert row["detailed_description"] == klass["description"]
        assert row["image_url"] == klass["image_url"]
        assert "categories" in changes

    def test_class_price_override_wins(self):
        row = bare_row()
        klass = sample_class(price_override=55.0)
        _apply_row_defaults(row, klass, {})
        assert row["ticket_price"] == "55"

    def test_instructor_prepended_to_description(self):
        row = bare_row(instructor="Ben", model="Stitch")
        _apply_row_defaults(row, sample_class(), {})
        assert row["detailed_description"].startswith("Instructors: Ben & Stitch")

    def test_default_image_used_when_class_has_none(self):
        row = bare_row()
        klass = sample_class(image_url="")
        _apply_row_defaults(row, klass, {"default_img": "https://x/fallback.png"})
        assert row["image_url"] == "https://x/fallback.png"

    def test_unnamed_row_is_untouched(self):
        row = bare_row(event_name="")
        props, changes = _apply_row_defaults(row, None, {})
        assert props == {}
        assert changes == []
        assert row["location"] == ""

    def test_ticketed_row_without_template_gets_default_price(self):
        # A priceless TICKETING event would publish with no tickets at all,
        # so the global default price is the last-resort fallback.
        row = bare_row()
        _apply_row_defaults(row, None, {})
        assert row["ticket_price"] == "30"

    def test_default_price_setting_wins_over_constant(self):
        row = bare_row()
        _apply_row_defaults(row, None, {"default_ticket_price": "45"})
        assert row["ticket_price"] == "45"

    def test_invalid_default_price_setting_falls_back_to_constant(self):
        row = bare_row()
        _apply_row_defaults(row, None, {"default_ticket_price": "lots"})
        assert row["ticket_price"] == "30"

    def test_no_price_for_rsvp_row(self):
        row = bare_row(registration_type="RSVP")
        props, _ = _apply_row_defaults(row, None, {})
        assert row["ticket_price"] == ""
        assert EventProps.TICKET_PRICE not in props

    def test_typed_price_is_never_overwritten(self):
        row = bare_row(ticket_price="12")
        props, _ = _apply_row_defaults(row, None, {})
        assert row["ticket_price"] == "12"
        assert EventProps.TICKET_PRICE not in props

    def test_bad_settings_rate_falls_back_to_constant(self):
        row = bare_row()
        _apply_row_defaults(row, None, {"default_tax_rate": "not-a-number"})
        assert row["tax_rate"] == "13"

    def test_seed_covers_all_helper_settings_keys(self):
        seeded_keys = {key for key, _, _ in DEFAULT_SETTINGS_SEED}
        assert {
            "default_location",
            "default_capacity",
            "default_registration_type",
            "default_tax_name",
            "default_tax_rate",
            "default_tax_type",
            "default_fee_type",
            "default_ticket_limit_per_order",
            "default_ticket_price",
            "default_checkout_form",
            "default_ticket_policy",
        } <= seeded_keys


class TestTemplateTicketDefaults:
    """Template Default Ticket Names/Prices/Capacities land on ticketed rows."""

    def _klass(self, **overrides):
        defaults = {
            "default_ticket_names": "GA; VIP",
            "default_ticket_prices": "25; 50",
            "default_ticket_capacities": "20; 4",
        }
        defaults.update(overrides)
        return sample_class(**defaults)

    def test_fills_ticket_trio_from_template(self):
        row = bare_row()
        props, changes = _apply_row_defaults(row, self._klass(), {})

        assert row["ticket_name"] == "GA; VIP"
        assert row["ticket_price"] == "25; 50"
        assert row["ticket_capacity"] == "20; 4"
        assert (
            props[EventProps.TICKET_NAMES]["rich_text"][0]["text"]["content"]
            == "GA; VIP"
        )
        # A semicolon price list routes to the Ticket Prices text property,
        # not the single-price number.
        assert (
            props[EventProps.TICKET_PRICES]["rich_text"][0]["text"]["content"]
            == "25; 50"
        )
        assert EventProps.TICKET_PRICE not in props
        assert "ticket names" in changes
        assert "ticket prices" in changes
        assert "ticket capacities" in changes

    def test_template_trio_beats_global_default_price(self):
        row = bare_row()
        _apply_row_defaults(row, self._klass(), {"default_ticket_price": "45"})
        assert row["ticket_price"] == "25; 50"

    def test_typed_ticket_fields_are_never_overwritten(self):
        row = bare_row(
            ticket_name="Members", ticket_price="15", ticket_capacity="10"
        )
        props, _ = _apply_row_defaults(row, self._klass(), {})

        assert row["ticket_name"] == "Members"
        assert row["ticket_price"] == "15"
        assert row["ticket_capacity"] == "10"
        assert EventProps.TICKET_NAMES not in props
        assert EventProps.TICKET_PRICES not in props
        assert EventProps.TICKET_CAPACITIES not in props

    def test_typed_names_still_get_template_prices(self):
        row = bare_row(ticket_name="Members")
        _apply_row_defaults(row, self._klass(), {})
        assert row["ticket_name"] == "Members"
        assert row["ticket_price"] == "25; 50"
        assert row["ticket_capacity"] == "20; 4"

    def test_price_list_without_names_is_not_applied(self):
        # Prices/capacities are keyed by names — without names they would
        # produce no tickets, so the normal pricing chain runs instead.
        row = bare_row()
        klass = self._klass(default_ticket_names="")
        _apply_row_defaults(row, klass, {})

        assert row["ticket_name"] == ""
        assert row["ticket_capacity"] == ""
        # "The Body in Flight" category price from the template's tags.
        assert row["ticket_price"] == "40"

    def test_rsvp_row_gets_no_ticket_trio(self):
        row = bare_row(registration_type="RSVP")
        props, _ = _apply_row_defaults(row, self._klass(), {})

        assert row["ticket_name"] == ""
        assert row["ticket_capacity"] == ""
        assert EventProps.TICKET_NAMES not in props
        assert EventProps.TICKET_CAPACITIES not in props
        # The template price list is skipped too; the price comes from the
        # pre-existing category chain ("The Body in Flight" = $40), which
        # has never been gated on registration type.
        assert row["ticket_price"] == "40"


class TestTemplateScheduleDefaults:
    """Template default times/instructor land on rows that lack them."""

    def _klass(self, **overrides):
        defaults = {
            "default_start_time": "19:00",
            "default_end_time": "22:00",
            "default_instructor": "Miranda",
        }
        defaults.update(overrides)
        return sample_class(**defaults)

    def test_fills_blank_times_and_rewrites_date(self):
        row = bare_row(start_time="", end_time="")
        props, changes = _apply_row_defaults(row, self._klass(), {}, tz_name="America/Toronto")

        assert row["start_time"] == "19:00"
        assert row["end_time"] == "22:00"
        assert "start time 19:00" in changes and "end time 22:00" in changes
        date_value = props[EventProps.DATE]["date"]
        assert date_value["start"] == "2026-09-01T19:00:00"
        assert date_value["end"] == "2026-09-01T22:00:00"
        assert date_value["time_zone"] == "America/Toronto"

    def test_typed_times_are_never_overwritten(self):
        row = bare_row(start_time="18:00", end_time="20:30")
        props, changes = _apply_row_defaults(row, self._klass(), {})

        assert row["start_time"] == "18:00"
        assert row["end_time"] == "20:30"
        assert EventProps.DATE not in props

    def test_fills_only_end_when_start_is_typed(self):
        row = bare_row(start_time="18:00", end_time="")
        props, _ = _apply_row_defaults(row, self._klass(), {})

        assert row["start_time"] == "18:00"
        assert row["end_time"] == "22:00"
        assert props[EventProps.DATE]["date"]["start"] == "2026-09-01T18:00:00"
        assert props[EventProps.DATE]["date"]["end"] == "2026-09-01T22:00:00"

    def test_no_start_time_available_leaves_date_alone(self):
        row = bare_row(start_time="", end_time="")
        klass = self._klass(default_start_time="")
        props, _ = _apply_row_defaults(row, klass, {})

        assert EventProps.DATE not in props
        assert row["start_time"] == ""

    def test_no_date_means_no_time_fill(self):
        row = bare_row(start_date="", start_time="", end_date="", end_time="")
        props, _ = _apply_row_defaults(row, self._klass(), {})
        assert EventProps.DATE not in props

    def test_default_instructor_fills_and_reaches_description(self):
        row = bare_row(start_time="", end_time="")
        props, changes = _apply_row_defaults(row, self._klass(), {})

        assert row["instructor"] == "Miranda"
        assert "instructor" in changes
        assert row["detailed_description"].startswith("Instructors: Miranda")
        assert EventProps.INSTRUCTOR in props

    def test_typed_instructor_wins(self):
        row = bare_row(instructor="Ben")
        props, _ = _apply_row_defaults(row, self._klass(), {})
        assert row["instructor"] == "Ben"
        assert EventProps.INSTRUCTOR not in props

    def test_overnight_end_rolls_to_next_day(self):
        row = bare_row(start_time="", end_date="", end_time="")
        klass = self._klass(default_start_time="21:00", default_end_time="3:00")
        props, _ = _apply_row_defaults(row, klass, {}, tz_name="America/Toronto")

        date_value = props[EventProps.DATE]["date"]
        assert date_value["start"] == "2026-09-01T21:00:00"
        assert date_value["end"] == "2026-09-02T03:00:00"
        assert row["end_date"] == "2026-09-02"

    def test_unpadded_template_times_are_normalized(self):
        row = bare_row(start_time="", end_time="")
        klass = self._klass(default_start_time="9:00", default_end_time="11:30")
        _apply_row_defaults(row, klass, {})
        assert row["start_time"] == "09:00"
        assert row["end_time"] == "11:30"


class TestDurationFallback:
    """Rows with a start but no usable end get a default duration."""

    def test_missing_end_gets_default_duration(self):
        row = bare_row(start_time="19:00", end_date="", end_time="")
        props, changes = _apply_row_defaults(row, None, {})

        assert row["end_time"] == "21:00"
        assert row["end_date"] == "2026-09-01"
        assert props[EventProps.DATE]["date"]["end"] == "2026-09-01T21:00:00"
        assert any("start + 2h" in c for c in changes)

    def test_zero_duration_end_is_replaced(self):
        # This is the "New Event" failure mode: end == start -> Wix 400.
        row = bare_row(start_time="23:00", end_date="2026-09-01", end_time="23:00")
        props, _ = _apply_row_defaults(row, None, {})

        assert row["end_time"] == "01:00"
        assert row["end_date"] == "2026-09-02"  # rolled past midnight
        date_value = props[EventProps.DATE]["date"]
        assert date_value["start"] == "2026-09-01T23:00:00"
        assert date_value["end"] == "2026-09-02T01:00:00"

    def test_duration_setting_is_honored(self):
        row = bare_row(start_time="19:00", end_date="", end_time="")
        _apply_row_defaults(row, None, {"default_duration_hours": "3.5"})
        assert row["end_time"] == "22:30"

    def test_template_end_beats_duration_fallback(self):
        row = bare_row(start_time="19:00", end_date="", end_time="")
        klass = sample_class(default_end_time="23:00")
        _apply_row_defaults(row, klass, {})
        assert row["end_time"] == "23:00"

    def test_typed_overnight_end_rolls_forward(self):
        row = bare_row(start_time="21:00", end_date="2026-09-01", end_time="01:00")
        props, changes = _apply_row_defaults(row, None, {})

        assert row["end_date"] == "2026-09-02"
        assert "end rolls past midnight" in changes
        assert props[EventProps.DATE]["date"]["end"] == "2026-09-02T01:00:00"

    def test_valid_typed_times_are_untouched(self):
        row = bare_row(start_time="19:00", end_time="21:00")
        props, _ = _apply_row_defaults(row, None, {})
        assert EventProps.DATE not in props


class TestDurationValidation:
    def _record_kwargs(self, **overrides):
        kwargs = dict(
            name="Test",
            start_date="2026-09-01",
            start_time="19:00",
            end_date="2026-09-01",
            end_time="21:00",
            location="Studio",
        )
        kwargs.update(overrides)
        return kwargs

    def test_zero_duration_is_rejected_with_clear_message(self):
        with pytest.raises(ValidationError, match="End must be after start"):
            EventRecord(**self._record_kwargs(end_time="19:00"))

    def test_negative_duration_is_rejected(self):
        with pytest.raises(ValidationError, match="End must be after start"):
            EventRecord(**self._record_kwargs(end_time="18:00"))

    def test_overnight_with_next_day_end_date_is_valid(self):
        record = EventRecord(
            **self._record_kwargs(
                start_time="21:00", end_date="2026-09-02", end_time="01:00"
            )
        )
        assert record.end_date == "2026-09-02"


class TestEnrichNameFill:
    """Rows with a linked Template but no Name get the template's name."""

    def _run_enrich(self, rows, classes):
        updates: Dict[str, Dict[str, Any]] = {}
        store = SimpleNamespace(
            fetch_event_rows=lambda statuses=None, include_missing_status=False: rows,
            fetch_classes=lambda: classes,
            fetch_settings=lambda: {},
            update_event_fields=lambda page_id, props: updates.__setitem__(
                page_id, props
            ),
        )
        runtime = SimpleNamespace(
            get_notion_store=lambda: store,
            config=SimpleNamespace(timezone="America/Toronto"),
        )
        assert enrich_events(runtime) is True
        return updates

    def _catalog(self):
        klass = sample_class(page_id="tpl-1", **{"class": "Improvised Suspensions"})
        return {"improvised suspensions": klass}

    def test_unnamed_row_with_template_gets_named_and_enriched(self):
        row = bare_row(
            event_name="",
            status="Idea",
            template_relation_ids=["tpl-1"],
        )
        updates = self._run_enrich([row], self._catalog())

        props = updates["page-1"]
        assert props[EventProps.NAME]["title"][0]["text"]["content"] == (
            "Improvised Suspensions"
        )
        assert row["event_name"] == "Improvised Suspensions"
        # Fully enriched in the same pass: promoted and content filled.
        assert props[EventProps.STATUS] == {"select": {"name": "Draft"}}
        assert EventProps.DESCRIPTION in props

    def test_unnamed_row_without_template_is_skipped(self):
        row = bare_row(event_name="", status="Idea")
        updates = self._run_enrich([row], self._catalog())
        assert updates == {}

    def test_named_row_keeps_its_name(self):
        row = bare_row(
            event_name="Custom Title",
            status="Idea",
            template_relation_ids=["tpl-1"],
        )
        updates = self._run_enrich([row], self._catalog())
        assert EventProps.NAME not in updates["page-1"]
        assert row["event_name"] == "Custom Title"

    def test_blank_status_bootstraps_to_idea_then_promotes(self):
        # A complete row with no status behaves like a fresh Idea row: it
        # gets Idea, then the normal promotion lands it at Draft.
        row = bare_row(
            event_name="",
            status="",
            template_relation_ids=["tpl-1"],
        )
        updates = self._run_enrich([row], self._catalog())

        props = updates["page-1"]
        # The promotion only fires for rows sitting on Idea, so landing at
        # Draft proves the bootstrap happened; the row now mirrors the
        # written state.
        assert row["status"] == "Draft"
        assert props[EventProps.STATUS] == {"select": {"name": "Draft"}}

    def test_blank_status_on_incomplete_row_stays_idea(self):
        row = bare_row(
            event_name="",
            status="",
            start_date="",
            start_time="",
            end_date="",
            end_time="",
            template_relation_ids=["tpl-1"],
        )
        updates = self._run_enrich([row], self._catalog())

        props = updates["page-1"]
        assert props[EventProps.STATUS] == {"select": {"name": "Idea"}}
        assert props[EventProps.SYNC_ERROR]["rich_text"]  # not-ready note
