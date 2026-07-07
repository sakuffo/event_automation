"""Unit tests for the row default-fill helper used by enrich and sync."""

from types import SimpleNamespace
from typing import Any, Dict, Optional

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
        assert row["tax_name"] == "HST"
        assert row["tax_rate"] == "13"
        assert row["tax_type"] == "ADDED_AT_CHECKOUT"
        assert row["fee_type"] == "FEE_ADDED_AT_CHECKOUT"
        assert "tax" in changes and "fee type" in changes
        assert EventProps.TAX_RATE in props
        assert props[EventProps.TAX_RATE]["number"] == 13.0
        assert EventProps.FEE_TYPE in props

    def test_settings_values_win_over_constants(self):
        settings = {
            "default_location": "123 New Studio Ave",
            "default_capacity": "18",
            "default_tax_rate": "15",
            "default_fee_type": "NO_FEE",
        }
        row = bare_row()
        _apply_row_defaults(row, None, settings)

        assert row["location"] == "123 New Studio Ave"
        assert row["capacity"] == "18"
        assert row["tax_rate"] == "15"
        assert row["fee_type"] == "NO_FEE"

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
        assert EventProps.TAX_NAME not in props
        assert EventProps.FEE_TYPE not in props

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

    def test_no_price_for_classless_uncategorized_row(self):
        row = bare_row()
        _apply_row_defaults(row, None, {})
        assert row["ticket_price"] == ""

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
        } <= seeded_keys


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


class TestEnrichNameFill:
    """Rows with a linked Template but no Name get the template's name."""

    def _run_enrich(self, rows, classes):
        updates: Dict[str, Dict[str, Any]] = {}
        store = SimpleNamespace(
            fetch_event_rows=lambda statuses=None: rows,
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
