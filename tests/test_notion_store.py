"""Unit tests for the Notion property mapping and hash-diff logic."""

from typing import Any, Dict, List, Optional

import pytest

from event_sync import notion_store
from event_sync.config import AppConfig
from event_sync.models import EventRecord
from event_sync.notion_store import (
    EventProps,
    NotionStore,
    event_page_to_row,
    event_properties_from_record,
    normalize_rate_string,
    p_date,
    row_to_event_record,
)


TZ = "America/Toronto"


def build_record(**overrides) -> EventRecord:
    payload = {
        "name": "Rope Lab",
        "category": "rope; class; suspension-lines",
        "start_date": "2026-08-12",
        "start_time": "19:00",
        "end_date": "2026-08-12",
        "end_time": "22:00",
        "location": "1233R Queen St W, Toronto",
        "ticket_price": 35.0,
        "capacity": 24,
        "registration_type": "TICKETS",
        "image_url": "https://drive.google.com/file/d/abc123/view",
        "teaser": "A teaser",
        "description": "Line one\n\nLine two",
        "ticket_name": "Regular; Student",
        "ticket_price_raw": "35; 25",
        "ticket_capacity": "20; 4",
        "tax_name": "HST",
        "tax_rate": "13",
        "tax_type": "ADDED_AT_CHECKOUT",
    }
    payload.update(overrides)
    return EventRecord(**payload)


def properties_to_page(props: Dict[str, Any], page_id: str = "page-1") -> Dict[str, Any]:
    """Convert a write-shape properties payload into a read-shape page."""
    read_props: Dict[str, Any] = {}
    for name, value in props.items():
        prop: Dict[str, Any] = {}
        if "title" in value:
            prop["type"] = "title"
            prop["title"] = [
                {"plain_text": t["text"]["content"]} for t in value["title"]
            ]
        elif "rich_text" in value:
            prop["type"] = "rich_text"
            prop["rich_text"] = [
                {"plain_text": t["text"]["content"]} for t in value["rich_text"]
            ]
        elif "select" in value:
            prop["type"] = "select"
            prop["select"] = value["select"]
        elif "multi_select" in value:
            prop["type"] = "multi_select"
            prop["multi_select"] = value["multi_select"]
        elif "number" in value:
            prop["type"] = "number"
            prop["number"] = value["number"]
        elif "url" in value:
            prop["type"] = "url"
            prop["url"] = value["url"]
        elif "date" in value:
            prop["type"] = "date"
            prop["date"] = value["date"]
        elif "relation" in value:
            prop["type"] = "relation"
            prop["relation"] = value["relation"]
        read_props[name] = prop
    return {"id": page_id, "properties": read_props}


class TestPropertyRoundTrip:
    def test_record_survives_notion_round_trip(self):
        record = build_record()
        props = event_properties_from_record(record, TZ, include_bookkeeping=False)
        page = properties_to_page(props)
        row = event_page_to_row(page, TZ)
        rebuilt = row_to_event_record(row)

        assert rebuilt.name == record.name
        assert rebuilt.start_date == record.start_date
        assert rebuilt.start_time == record.start_time
        assert rebuilt.end_date == record.end_date
        assert rebuilt.end_time == record.end_time
        assert rebuilt.location == record.location
        assert rebuilt.registration_type == "TICKETING"
        assert rebuilt.teaser == record.teaser
        assert rebuilt.description == record.description
        assert rebuilt.image_url == record.image_url
        assert rebuilt.ticket_name == record.ticket_name
        assert rebuilt.ticket_capacity == record.ticket_capacity
        assert rebuilt.tax_name == record.tax_name
        assert rebuilt.tax_rate == record.tax_rate
        assert rebuilt.tax_type == record.tax_type

    def test_round_trip_hash_is_stable(self):
        record = build_record()
        props = event_properties_from_record(record, TZ)
        row = event_page_to_row(properties_to_page(props), TZ)
        rebuilt = row_to_event_record(row)
        assert rebuilt.content_hash() == record.content_hash()

    def test_multi_ticket_prices_survive_round_trip(self):
        record = build_record()
        props = event_properties_from_record(record, TZ)
        row = event_page_to_row(properties_to_page(props), TZ)
        # Semicolon prices land in the Ticket Prices text property
        assert row["ticket_price"] == "35; 25"
        rebuilt = row_to_event_record(row)
        assert rebuilt.ticket_price == 0.0  # collapsed, raw preserved
        assert rebuilt.ticket_price_raw == "35; 25"

    def test_single_price_lands_in_number_property(self):
        record = build_record(ticket_name=None, ticket_price_raw="35", ticket_capacity=None)
        props = event_properties_from_record(record, TZ)
        assert props[EventProps.TICKET_PRICE]["number"] == 35.0
        row = event_page_to_row(properties_to_page(props), TZ)
        assert row["ticket_price"] == "35"
        rebuilt = row_to_event_record(row)
        assert rebuilt.ticket_price == 35.0

    def test_long_description_is_chunked_and_rejoined(self):
        long_text = "x" * 4500
        record = build_record(description=long_text)
        props = event_properties_from_record(record, TZ)
        chunks = props[EventProps.DESCRIPTION]["rich_text"]
        assert len(chunks) == 3
        assert all(len(c["text"]["content"]) <= 2000 for c in chunks)
        row = event_page_to_row(properties_to_page(props), TZ)
        assert row["detailed_description"] == long_text

    def test_registration_type_maps_tickets_to_select_and_back(self):
        record = build_record(registration_type="TICKETING")
        props = event_properties_from_record(record, TZ)
        assert props[EventProps.REGISTRATION_TYPE]["select"]["name"] == "TICKETS"
        row = event_page_to_row(properties_to_page(props), TZ)
        rebuilt = row_to_event_record(row)
        assert rebuilt.registration_type == "TICKETING"


class TestDateParsing:
    def test_naive_datetime_with_timezone_field(self):
        page = properties_to_page({
            EventProps.NAME: {"title": [{"type": "text", "text": {"content": "E"}}]},
            EventProps.DATE: p_date("2026-08-12", "19:00", "2026-08-12", "22:00", tz_name=TZ),
        })
        row = event_page_to_row(page, TZ)
        assert (row["start_date"], row["start_time"]) == ("2026-08-12", "19:00")
        assert (row["end_date"], row["end_time"]) == ("2026-08-12", "22:00")

    def test_utc_offset_datetime_converts_to_local(self):
        # Notion returns offsets once a page is saved: 23:00 UTC == 19:00 EDT
        page = properties_to_page({
            EventProps.DATE: {
                "date": {
                    "start": "2026-08-12T23:00:00.000+00:00",
                    "end": None,
                    "time_zone": None,
                }
            },
        })
        row = event_page_to_row(page, TZ)
        assert row["start_date"] == "2026-08-12"
        assert row["start_time"] == "19:00"

    def test_date_only_value_has_empty_time(self):
        page = properties_to_page({
            EventProps.DATE: {"date": {"start": "2026-08-12", "end": None}},
        })
        row = event_page_to_row(page, TZ)
        assert row["start_date"] == "2026-08-12"
        assert row["start_time"] == ""


class TestContentHash:
    def test_hash_ignores_formatting_drift(self):
        a = build_record(ticket_price_raw="35; 25", tax_rate="13")
        b = build_record(ticket_price_raw="35.0;25.00", tax_rate="13.0")
        assert a.content_hash() == b.content_hash()

    def test_hash_treats_none_and_empty_as_equal(self):
        a = build_record(teaser=None)
        b = build_record(teaser="")
        assert a.content_hash() == b.content_hash()

    def test_hash_changes_when_field_changes(self):
        a = build_record()
        b = build_record(description="Different")
        assert a.content_hash() != b.content_hash()

    def test_hash_ignores_bookkeeping_fields(self):
        a = build_record()
        b = build_record()
        b.notion_page_id = "some-page"
        b.wix_event_id = "some-wix-id"
        b.status = "Published"
        b.synced_hash = "deadbeef"
        assert a.content_hash() == b.content_hash()


class TestNormalizers:
    def test_normalize_rate_string(self):
        assert normalize_rate_string("13.0") == "13"
        assert normalize_rate_string("13") == "13"
        assert normalize_rate_string("13.5") == "13.5"
        assert normalize_rate_string("") == ""
        assert normalize_rate_string(None) == ""
        assert normalize_rate_string("junk") == "junk"


# ---------------------------------------------------------------------------
# NotionStore with a mocked notion_client.Client
# ---------------------------------------------------------------------------


class FakeDataSources:
    def __init__(
        self,
        pages_by_call: List[Dict[str, Any]],
        schema: Optional[Dict[str, Any]] = None,
    ):
        self.pages_by_call = pages_by_call
        self.calls: List[Dict[str, Any]] = []
        self.schema = schema or {}
        self.update_calls: List[Dict[str, Any]] = []

    def query(self, **kwargs):
        self.calls.append(kwargs)
        return self.pages_by_call[len(self.calls) - 1]

    def retrieve(self, **kwargs):
        return self.schema

    def update(self, **kwargs):
        self.update_calls.append(kwargs)
        return self.schema


class FakeDatabases:
    def __init__(self):
        self.retrieve_calls: List[str] = []

    def retrieve(self, database_id: str):
        self.retrieve_calls.append(database_id)
        return {"id": database_id, "data_sources": [{"id": f"ds-{database_id}", "name": "x"}]}


class FakeClient:
    def __init__(
        self,
        pages_by_call: List[Dict[str, Any]],
        schema: Optional[Dict[str, Any]] = None,
    ):
        self.data_sources = FakeDataSources(pages_by_call, schema=schema)
        self.databases = FakeDatabases()


def make_config(**overrides) -> AppConfig:
    defaults = dict(
        wix_api_key=None,
        wix_account_id=None,
        wix_site_id=None,
        google_credentials_raw=None,
        notion_token="secret",
        notion_event_scheduling_db_id="db-events",
        notion_catalog_db_id="db-catalog",
        notion_settings_db_id="db-settings",
        notion_site_config_db_id="db-site",
    )
    defaults.update(overrides)
    return AppConfig(**defaults)


@pytest.fixture
def fake_store(monkeypatch):
    def _build(pages_by_call: List[Dict[str, Any]]) -> NotionStore:
        fake = FakeClient(pages_by_call)
        monkeypatch.setattr(notion_store, "Client", lambda **kwargs: fake)
        store = NotionStore(make_config())
        store._fake = fake  # type: ignore[attr-defined]
        return store

    return _build


def _page(name: str, status: str = "Ready") -> Dict[str, Any]:
    return properties_to_page(
        {
            EventProps.NAME: {"title": [{"type": "text", "text": {"content": name}}]},
            EventProps.STATUS: {"select": {"name": status}},
            EventProps.DATE: p_date("2026-08-12", "19:00", "2026-08-12", "22:00", tz_name=TZ),
            EventProps.LOCATION: {
                "rich_text": [{"type": "text", "text": {"content": "Studio"}}]
            },
        },
        page_id=f"page-{name}",
    )


class TestNotionStoreQueries:
    def test_iter_pages_paginates_and_resolves_data_source_once(self, fake_store):
        store = fake_store([
            {"results": [_page("A")], "has_more": True, "next_cursor": "cur-2"},
            {"results": [_page("B")], "has_more": False, "next_cursor": None},
        ])
        rows = store.fetch_event_rows()
        assert [r["event_name"] for r in rows] == ["A", "B"]

        fake = store._fake
        assert fake.databases.retrieve_calls == ["db-events"]
        assert fake.data_sources.calls[0]["data_source_id"] == "ds-db-events"
        assert "start_cursor" not in fake.data_sources.calls[0]
        assert fake.data_sources.calls[1]["start_cursor"] == "cur-2"

    def test_fetch_event_rows_builds_single_status_filter(self, fake_store):
        store = fake_store([
            {"results": [], "has_more": False, "next_cursor": None},
        ])
        store.fetch_event_rows(statuses=["Ready"])
        filter_ = store._fake.data_sources.calls[0]["filter"]
        assert filter_ == {"property": EventProps.STATUS, "select": {"equals": "Ready"}}

    def test_fetch_event_rows_builds_or_filter_for_multiple_statuses(self, fake_store):
        store = fake_store([
            {"results": [], "has_more": False, "next_cursor": None},
        ])
        store.fetch_event_rows(statuses=["Ready", "Published"])
        filter_ = store._fake.data_sources.calls[0]["filter"]
        assert "or" in filter_ and len(filter_["or"]) == 2

    def test_fetch_event_rows_can_include_missing_status(self, fake_store):
        store = fake_store([
            {"results": [], "has_more": False, "next_cursor": None},
        ])
        store.fetch_event_rows(statuses=["Idea", "Draft"], include_missing_status=True)
        filter_ = store._fake.data_sources.calls[0]["filter"]
        assert len(filter_["or"]) == 3
        assert {
            "property": EventProps.STATUS,
            "select": {"is_empty": True},
        } in filter_["or"]


class TestStatusLifecycle:
    def test_every_status_has_a_select_option(self):
        option_names = {o["name"] for o in notion_store.STATUS_SELECT_OPTIONS}
        assert set(notion_store.ALL_STATUSES) == option_names

    def test_events_schema_includes_cancel_and_delete_states(self):
        props = notion_store._events_db_properties(None)
        option_names = {
            o["name"] for o in props[EventProps.STATUS]["select"]["options"]
        }
        assert {"Cancel", "Cancelled", "Delete", "Removed"} <= option_names

    def _schema_with_options(self, names: List[str]) -> Dict[str, Any]:
        return {
            "properties": {
                EventProps.STATUS: {
                    "select": {
                        "options": [
                            {"id": f"opt-{i}", "name": n, "color": "gray"}
                            for i, n in enumerate(names)
                        ]
                    }
                }
            }
        }

    def test_ensure_status_options_adds_missing(self, monkeypatch):
        schema = self._schema_with_options(
            ["Idea", "Draft", "Ready", "Published", "Error", "Skip"]
        )
        fake = FakeClient([], schema=schema)
        monkeypatch.setattr(notion_store, "Client", lambda **kwargs: fake)
        store = NotionStore(make_config())

        added = store.ensure_event_status_options()

        assert added == 5
        sent = fake.data_sources.update_calls[0]["properties"]
        sent_names = [o["name"] for o in sent[EventProps.STATUS]["select"]["options"]]
        # Existing options (with their ids) come first, new ones appended.
        assert sent_names[:6] == ["Idea", "Draft", "Ready", "Published", "Error", "Skip"]
        assert {"Update", "Cancel", "Cancelled", "Delete", "Removed"} <= set(sent_names)

    def test_ensure_status_options_noop_when_complete(self, monkeypatch):
        schema = self._schema_with_options(list(notion_store.ALL_STATUSES))
        fake = FakeClient([], schema=schema)
        monkeypatch.setattr(notion_store, "Client", lambda **kwargs: fake)
        store = NotionStore(make_config())

        assert store.ensure_event_status_options() == 0
        assert fake.data_sources.update_calls == []


class TestSiteConfigUpsertPrecedence:
    """upsert_site_config_row matching: mapping_id wins; region+group only
    matches pages that have no mapping_id; no match -> create."""

    @staticmethod
    def _site_page(page_id, mapping_id="", region_id="", group_id=""):
        SC = notion_store.SiteConfigProps
        return properties_to_page(
            {
                SC.NAME: notion_store.p_title("Ontario"),
                SC.MAPPING_ID: notion_store.p_rich_text(mapping_id),
                SC.REGION_ID: notion_store.p_rich_text(region_id),
                SC.GROUP_ID: notion_store.p_rich_text(group_id),
            },
            page_id=page_id,
        )

    def _store_with_pages(self, fake_store, monkeypatch, pages):
        store = fake_store([])
        monkeypatch.setattr(store, "iter_pages", lambda db_id, filter_=None: iter(pages))
        writes = {"updated": [], "created": []}
        monkeypatch.setattr(
            store, "update_page",
            lambda page_id, props: writes["updated"].append(page_id),
        )
        monkeypatch.setattr(
            store, "create_page",
            lambda db_id, props: writes["created"].append(db_id),
        )
        return store, writes

    def test_mapping_id_match_wins(self, fake_store, monkeypatch):
        pages = [
            self._site_page("p-map", mapping_id="m1", region_id="r1", group_id="g1"),
            self._site_page("p-plain", region_id="r1", group_id="g1"),
        ]
        store, writes = self._store_with_pages(fake_store, monkeypatch, pages)
        store.upsert_site_config_row(
            {"mapping_id": "m1", "region_id": "r1", "group_id": "g1", "tax_rate": "13"}
        )
        assert writes["updated"] == ["p-map"]
        assert writes["created"] == []

    def test_region_group_only_matches_pages_without_mapping_id(
        self, fake_store, monkeypatch
    ):
        pages = [
            self._site_page("p-map", mapping_id="m1", region_id="r1", group_id="g1"),
            self._site_page("p-plain", region_id="r1", group_id="g1"),
        ]
        store, writes = self._store_with_pages(fake_store, monkeypatch, pages)
        store.upsert_site_config_row(
            {"mapping_id": "", "region_id": "r1", "group_id": "g1", "tax_rate": "13"}
        )
        assert writes["updated"] == ["p-plain"]

    def test_unmatched_row_creates_page(self, fake_store, monkeypatch):
        pages = [self._site_page("p-map", mapping_id="m1")]
        store, writes = self._store_with_pages(fake_store, monkeypatch, pages)
        store.upsert_site_config_row(
            {"mapping_id": "m2", "region_id": "r9", "group_id": "g9", "tax_rate": "5"}
        )
        assert writes["updated"] == []
        assert writes["created"] == ["db-site"]


class TestSiteConfigUpsertEfficiency:
    @staticmethod
    def _full_page(page_id="p-1"):
        SC = notion_store.SiteConfigProps
        return properties_to_page(
            {
                SC.NAME: notion_store.p_title("Ontario"),
                SC.SETTING_TYPE: notion_store.p_select("tax_location"),
                SC.REGION: notion_store.p_rich_text("CA / ON"),
                SC.TAX_NAME: notion_store.p_rich_text("HST"),
                SC.TAX_TYPE: notion_store.p_rich_text(""),
                SC.TAX_RATE: notion_store.p_number(13.0),
                SC.REGION_ID: notion_store.p_rich_text("r1"),
                SC.GROUP_ID: notion_store.p_rich_text("g1"),
                SC.MAPPING_ID: notion_store.p_rich_text("m1"),
                SC.REVISION: notion_store.p_rich_text("3"),
            },
            page_id=page_id,
        )

    @staticmethod
    def _matching_row():
        return {
            "setting_type": "tax_location",
            "jurisdiction": "Ontario",
            "region": "CA / ON",
            "tax_name": "HST",
            "tax_type": "",
            "tax_rate": "13",
            "region_id": "r1",
            "group_id": "g1",
            "mapping_id": "m1",
            "revision": "3",
        }

    def _store(self, fake_store, monkeypatch, pages):
        store = fake_store([])
        monkeypatch.setattr(store, "iter_pages", lambda db_id, filter_=None: iter(pages))
        writes = {"updated": [], "created": []}
        monkeypatch.setattr(
            store, "update_page", lambda page_id, props: writes["updated"].append(page_id)
        )
        monkeypatch.setattr(
            store, "create_page", lambda db_id, props: writes["created"].append(db_id)
        )
        return store, writes

    def test_unchanged_row_is_not_rewritten(self, fake_store, monkeypatch):
        store, writes = self._store(fake_store, monkeypatch, [self._full_page()])
        outcome = store.upsert_site_config_row(self._matching_row())
        assert outcome == "unchanged"
        assert writes == {"updated": [], "created": []}

    def test_changed_rate_still_updates(self, fake_store, monkeypatch):
        store, writes = self._store(fake_store, monkeypatch, [self._full_page()])
        row = dict(self._matching_row(), tax_rate="15")
        assert store.upsert_site_config_row(row) == "updated"
        assert writes["updated"] == ["p-1"]

    def test_prebuilt_index_avoids_per_row_scans(self, fake_store, monkeypatch):
        store, writes = self._store(fake_store, monkeypatch, [self._full_page()])
        index = store.index_site_config_pages()

        def boom(db_id, filter_=None):
            raise AssertionError("iter_pages must not run per row")

        monkeypatch.setattr(store, "iter_pages", boom)
        row = dict(self._matching_row(), tax_rate="15")
        assert store.upsert_site_config_row(row, page_index=index) == "updated"
        assert store.upsert_site_config_row(self._matching_row(), page_index=index) == "unchanged"
        assert writes["updated"] == ["p-1"]


class _FakeTransientError(Exception):
    def __init__(self, status=503):
        super().__init__(f"HTTP {status}")
        self.status = status


class TestNotionApiRetry:
    def test_transient_query_error_is_retried(self, fake_store, monkeypatch):
        store = fake_store([{"results": [], "has_more": False, "next_cursor": None}])
        monkeypatch.setattr(notion_store.time, "sleep", lambda s: None)
        attempts = {"n": 0}

        def flaky():
            attempts["n"] += 1
            if attempts["n"] == 1:
                raise _FakeTransientError()
            return {"ok": True}

        assert store._api("test call", flaky) == {"ok": True}
        assert attempts["n"] == 2

    def test_page_create_is_never_retried(self, fake_store, monkeypatch):
        store = fake_store([])
        monkeypatch.setattr(notion_store.time, "sleep", lambda s: None)
        attempts = {"n": 0}

        def flaky():
            attempts["n"] += 1
            raise _FakeTransientError()

        with pytest.raises(notion_store.NotionStoreError):
            store._api("page create", flaky, retry=False)
        assert attempts["n"] == 1

    def test_non_transient_error_is_not_retried(self, fake_store, monkeypatch):
        store = fake_store([])
        monkeypatch.setattr(notion_store.time, "sleep", lambda s: None)
        attempts = {"n": 0}

        def bad_request():
            attempts["n"] += 1
            raise ValueError("400 validation error")

        with pytest.raises(notion_store.NotionStoreError):
            store._api("page update", bad_request)
        assert attempts["n"] == 1


class TestEnsureEventProperties:
    def _store_with_live_props(self, monkeypatch, live_props):
        fake = FakeClient([], schema={"properties": live_props})
        monkeypatch.setattr(notion_store, "Client", lambda **kwargs: fake)
        return NotionStore(make_config()), fake

    def test_adds_missing_events_properties(self, monkeypatch):
        # A database created before Instructor/Template existed.
        live = {
            name: {"id": f"prop-{i}"}
            for i, name in enumerate(
                notion_store._events_db_properties("ds-db-catalog")
            )
            if name not in (EventProps.INSTRUCTOR, EventProps.TEMPLATE)
        }
        store, fake = self._store_with_live_props(monkeypatch, live)

        added = store.ensure_event_properties()

        assert added == sorted([EventProps.INSTRUCTOR, EventProps.TEMPLATE])
        sent = fake.data_sources.update_calls[0]["properties"]
        assert sent[EventProps.TEMPLATE]["relation"]["data_source_id"] == "ds-db-catalog"

    def test_noop_when_schema_complete(self, monkeypatch):
        live = {
            name: {"id": f"prop-{i}"}
            for i, name in enumerate(
                notion_store._events_db_properties("ds-db-catalog")
            )
        }
        store, fake = self._store_with_live_props(monkeypatch, live)
        assert store.ensure_event_properties() == []
        assert fake.data_sources.update_calls == []
