"""Tests for the categories-only round-trip orchestrator + sheets helpers."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, Dict, Iterable, Iterator, List, Optional
from unittest.mock import MagicMock

import pytest

from event_sync import orchestrator
from event_sync.orchestrator import (
    CATEGORY_CONFIG_COLUMNS,
    _category_row_sort_key,
    _wix_event_to_category_row,
    pull_category_config,
    push_category_config,
)
from event_sync.sheets import (
    _normalize_category_header,
    fetch_category_config_rows,
)


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class FakeWixClient:
    """Hand-rolled stand-in for the subset of WixClient the new flow uses."""

    def __init__(self, events: Optional[List[Dict[str, Any]]] = None,
                 categories: Optional[List[Dict[str, Any]]] = None) -> None:
        self._events = list(events or [])
        self._categories = list(categories or [])

        self.update_event = MagicMock()
        self.create_event = MagicMock()
        self.delete_event = MagicMock()
        self.publish_event = MagicMock()
        self.get_ticket_definitions = MagicMock(return_value=[])
        self.update_ticket_definition = MagicMock()
        self.create_ticket_definition = MagicMock()

        self.assign_event_to_category = MagicMock()
        self.unassign_event_from_category = MagicMock()
        self.create_category = MagicMock(side_effect=self._create_category_side_effect)

    def iter_events(
        self,
        *,
        page_size: int = 100,
        include_drafts: bool = True,
        status_filter: Optional[str] = None,
        offset: int = 0,
        fieldsets: Optional[List[str]] = None,
    ) -> Iterator[Dict[str, Any]]:
        for event in self._events:
            if not include_drafts and event.get("status") == "DRAFT":
                continue
            yield event

    def query_categories(self) -> List[Dict[str, Any]]:
        return list(self._categories)

    def _create_category_side_effect(self, name: str) -> Dict[str, Any]:
        new_id = f"cat-{name.strip().lower().replace(' ', '-')}"
        cat = {"id": new_id, "name": name.strip()}
        self._categories.append(cat)
        return cat


def make_runtime(client: FakeWixClient, *, sheets_service: Any = None,
                 google_sheet_id: str = "sheet-123",
                 category_config_tab: str = "category_config",
                 timezone: str = "America/Toronto") -> Any:
    config = SimpleNamespace(
        timezone=timezone,
        google_sheet_id=google_sheet_id,
        category_config_tab=category_config_tab,
    )
    return SimpleNamespace(
        config=config,
        get_wix_client=lambda: client,
        get_sheets_service=lambda: sheets_service,
    )


def reset_category_cache():
    orchestrator._category_cache.clear()
    orchestrator._category_cache_loaded = False


@pytest.fixture(autouse=True)
def _isolate_category_cache():
    reset_category_cache()
    yield
    reset_category_cache()


# ---------------------------------------------------------------------------
# Sample payloads
# ---------------------------------------------------------------------------


def _wix_event(
    event_id: str,
    title: str,
    *,
    status: str = "UPCOMING",
    start_iso: str = "2026-05-10T23:00:00Z",  # 7pm ET
    short: str = "short",
    detailed: str = "<p>detailed</p>",
    categories: Optional[List[Dict[str, str]]] = None,
) -> Dict[str, Any]:
    return {
        "id": event_id,
        "title": title,
        "status": status,
        "shortDescription": short,
        "detailedDescription": detailed,
        "dateAndTimeSettings": {"startDate": start_iso},
        "categories": {"categories": list(categories or [])},
    }


# ---------------------------------------------------------------------------
# Row construction
# ---------------------------------------------------------------------------


def test_wix_event_to_category_row_produces_eight_columns_in_order():
    event = _wix_event(
        "evt-1",
        "Rope Lab",
        status="UPCOMING",
        start_iso="2026-05-10T23:00:00Z",
        short="hook",
        detailed="<p>body</p>",
        categories=[
            {"id": "c1", "name": "rope"},
            {"id": "c2", "name": "class"},
        ],
    )

    row = _wix_event_to_category_row(event, "America/Toronto")

    assert list(row.keys()) == CATEGORY_CONFIG_COLUMNS
    assert row["event_name"] == "Rope Lab"
    assert row["categories"] == "rope; class"
    assert row["short_description"] == "hook"
    assert row["detailed_description"] == "<p>body</p>"
    assert row["start_date"] == "05/10/2026"
    assert row["start_time"] == "19:00"
    assert row["status"] == "UPCOMING"
    assert row["event_id"] == "evt-1"


# ---------------------------------------------------------------------------
# Pull scope filtering + sort
# ---------------------------------------------------------------------------


def test_pull_scope_upcoming_filters_to_upcoming_and_started(monkeypatch):
    events = [
        _wix_event("u1", "Future A", status="UPCOMING", start_iso="2026-09-01T23:00:00Z"),
        _wix_event("s1", "Now Live",  status="STARTED",  start_iso="2026-04-18T23:00:00Z"),
        _wix_event("e1", "Past",      status="ENDED",    start_iso="2025-12-01T23:00:00Z"),
        _wix_event("c1", "Cancelled", status="CANCELED", start_iso="2026-06-01T23:00:00Z"),
    ]
    client = FakeWixClient(events=events)
    runtime = make_runtime(client)

    captured: List[List[Dict[str, Any]]] = []

    def fake_writer(rows, runtime, tab_name):
        captured.append((tab_name, list(rows)))
        return True

    monkeypatch.setattr(
        "event_sync.generator.write_category_config_to_sheet",
        fake_writer,
    )

    assert pull_category_config(runtime, scope="upcoming") is True

    # Last call writes the editable tab; all calls share the same row set.
    written_tabs = [tab for tab, _ in captured]
    assert written_tabs == ["category_config_last_pull", "category_config"]
    rows = captured[-1][1]
    titles = [r["event_name"] for r in rows]
    assert set(titles) == {"Future A", "Now Live"}


def test_pull_scope_all_keeps_all_non_drafts_sorted_desc(monkeypatch):
    events = [
        _wix_event("e1", "Past",      status="ENDED",    start_iso="2025-12-01T23:00:00Z"),
        _wix_event("u1", "Future A",  status="UPCOMING", start_iso="2026-09-01T23:00:00Z"),
        _wix_event("c1", "Cancelled", status="CANCELED", start_iso="2026-06-01T23:00:00Z"),
        _wix_event("s1", "Now Live",  status="STARTED",  start_iso="2026-04-18T23:00:00Z"),
    ]
    client = FakeWixClient(events=events)
    runtime = make_runtime(client)

    captured: List = []
    monkeypatch.setattr(
        "event_sync.generator.write_category_config_to_sheet",
        lambda rows, runtime, tab_name: captured.append(list(rows)) or True,
    )

    assert pull_category_config(runtime, scope="all") is True

    rows = captured[-1]
    titles = [r["event_name"] for r in rows]
    assert titles == ["Future A", "Cancelled", "Now Live", "Past"]


def test_category_row_sort_key_handles_blanks_and_iso():
    assert _category_row_sort_key({"start_date": "05/10/2026"}) == "2026-05-10"
    assert _category_row_sort_key({"start_date": "2026-05-10"}) == "2026-05-10"
    assert _category_row_sort_key({"start_date": ""}) == ""
    assert _category_row_sort_key({"start_date": "garbage"}) == ""


# ---------------------------------------------------------------------------
# Sheet header normalization
# ---------------------------------------------------------------------------


def test_normalize_category_header_strips_ro_marker_and_lowercases():
    assert _normalize_category_header("(ro) event_name") == "event_name"
    assert _normalize_category_header("(RO) Status") == "status"
    assert _normalize_category_header("categories") == "categories"
    assert _normalize_category_header("  Event_ID  ") == "event_id"


def _sheets_service_for_rows(rows):
    class _Values:
        def __init__(self, rows):
            self._rows = rows
        def get(self, spreadsheetId, range):
            return self
        def execute(self):
            return {"values": self._rows}

    class _Spreadsheets:
        def __init__(self, rows):
            self._rows = rows
        def values(self):
            return _Values(self._rows)

    return SimpleNamespace(spreadsheets=lambda: _Spreadsheets(rows))


def test_fetch_category_config_rows_normalizes_headers_and_skips_blanks():
    rows = [
        [
            "(ro) event_name", "categories", "(ro) short_description",
            "(ro) detailed_description", "(ro) start_date", "(ro) start_time",
            "(ro) status", "(ro) event_id",
        ],
        ["Rope Lab", "rope; class", "s", "d", "05/10/2026", "19:00", "UPCOMING", "evt-1"],
        ["", "", "", "", "", "", "", ""],
        ["Past Show", "rope", "", "", "01/01/2025", "20:00", "ENDED", "evt-2"],
    ]
    runtime = make_runtime(FakeWixClient(), sheets_service=_sheets_service_for_rows(rows))

    out = fetch_category_config_rows(runtime)

    assert len(out) == 2
    assert out[0]["event_name"] == "Rope Lab"
    assert out[0]["categories"] == "rope; class"
    assert out[0]["status"] == "UPCOMING"
    assert out[0]["event_id"] == "evt-1"
    assert out[1]["event_name"] == "Past Show"
    assert out[1]["status"] == "ENDED"


# ---------------------------------------------------------------------------
# Push: scope filtering, diffs, fallback matching, dry-run, regression
# ---------------------------------------------------------------------------


def _patch_fetch_rows(monkeypatch, rows: List[Dict[str, str]]):
    monkeypatch.setattr(
        "event_sync.sheets.fetch_category_config_rows",
        lambda runtime: rows,
    )


def _row(**overrides) -> Dict[str, str]:
    base = {col: "" for col in CATEGORY_CONFIG_COLUMNS}
    base.update(overrides)
    return base


def test_push_upcoming_scope_buckets_past_rows_as_out_of_scope(monkeypatch):
    live = [
        _wix_event("evt-up", "Live Class", status="UPCOMING",
                   start_iso="2026-05-10T23:00:00Z",
                   categories=[{"id": "c-rope", "name": "rope"}]),
        _wix_event("evt-past", "Old Class", status="ENDED",
                   start_iso="2025-01-10T23:00:00Z",
                   categories=[{"id": "c-rope", "name": "rope"}]),
    ]
    client = FakeWixClient(events=live)
    runtime = make_runtime(client)

    rows = [
        _row(event_name="Live Class", categories="rope; suspension",
             start_date="05/10/2026", start_time="19:00",
             status="UPCOMING", event_id="evt-up"),
        _row(event_name="Old Class", categories="rope; bondage",
             start_date="01/10/2025", start_time="19:00",
             status="ENDED", event_id="evt-past"),
    ]
    _patch_fetch_rows(monkeypatch, rows)

    assert push_category_config(runtime, scope="upcoming", dry_run=False) is True

    assign_calls = client.assign_event_to_category.call_args_list
    unassign_calls = client.unassign_event_from_category.call_args_list

    assert all(call.args[1] == "evt-up" for call in assign_calls)
    assert all(call.args[1] == "evt-up" for call in unassign_calls)
    client.update_event.assert_not_called()


def test_push_all_scope_acts_on_every_row(monkeypatch):
    live = [
        _wix_event("evt-up", "Live Class", status="UPCOMING",
                   start_iso="2026-05-10T23:00:00Z",
                   categories=[]),
        _wix_event("evt-past", "Old Class", status="ENDED",
                   start_iso="2025-01-10T23:00:00Z",
                   categories=[]),
    ]
    client = FakeWixClient(events=live)
    runtime = make_runtime(client)

    rows = [
        _row(event_name="Live Class", categories="alpha",
             status="UPCOMING", event_id="evt-up"),
        _row(event_name="Old Class", categories="beta",
             status="ENDED", event_id="evt-past"),
    ]
    _patch_fetch_rows(monkeypatch, rows)

    assert push_category_config(runtime, scope="all", dry_run=False) is True

    target_event_ids = {call.args[1] for call in client.assign_event_to_category.call_args_list}
    assert target_event_ids == {"evt-up", "evt-past"}


def test_push_diff_adds_and_removes_only_changed_categories(monkeypatch):
    live = [
        _wix_event(
            "evt-up", "Live", status="UPCOMING",
            start_iso="2026-05-10T23:00:00Z",
            categories=[
                {"id": "c-b", "name": "b"},
                {"id": "c-c", "name": "c"},
            ],
        ),
    ]
    client = FakeWixClient(
        events=live,
        categories=[
            {"id": "c-a", "name": "a"},
            {"id": "c-b", "name": "b"},
            {"id": "c-c", "name": "c"},
        ],
    )
    runtime = make_runtime(client)

    _patch_fetch_rows(monkeypatch, [
        _row(event_name="Live", categories="a; b",
             status="UPCOMING", event_id="evt-up"),
    ])

    assert push_category_config(runtime, scope="all", dry_run=False) is True

    assign_args = [call.args for call in client.assign_event_to_category.call_args_list]
    unassign_args = [call.args for call in client.unassign_event_from_category.call_args_list]

    assert assign_args == [("c-a", "evt-up")]
    assert unassign_args == [("c-c", "evt-up")]


def test_push_matches_by_event_id_then_falls_back_to_title_date_time(monkeypatch):
    live = [
        _wix_event(
            "evt-1", "Real Title", status="UPCOMING",
            start_iso="2026-05-10T23:00:00Z",
            categories=[{"id": "c-rope", "name": "rope"}],
        ),
        _wix_event(
            "evt-2", "Fallback Title", status="UPCOMING",
            start_iso="2026-06-15T23:00:00Z",
            categories=[],
        ),
    ]
    client = FakeWixClient(
        events=live,
        categories=[
            {"id": "c-rope", "name": "rope"},
            {"id": "c-extra", "name": "extra"},
        ],
    )
    runtime = make_runtime(client)

    rows = [
        # event_id wins even though the sheet title differs from Wix.
        _row(event_name="Stale Sheet Title", categories="rope; extra",
             status="UPCOMING", event_id="evt-1",
             start_date="05/10/2026", start_time="19:00"),
        # blank event_id → fallback by (title, start_date, start_time)
        _row(event_name="Fallback Title", categories="extra",
             status="UPCOMING", event_id="",
             start_date="06/15/2026", start_time="19:00"),
    ]
    _patch_fetch_rows(monkeypatch, rows)

    assert push_category_config(runtime, scope="all", dry_run=False) is True

    assigns = sorted(
        (call.args[0], call.args[1])
        for call in client.assign_event_to_category.call_args_list
    )
    assert assigns == [("c-extra", "evt-1"), ("c-extra", "evt-2")]


def test_push_ignores_description_edits_when_categories_unchanged(monkeypatch):
    live = [
        _wix_event(
            "evt-up", "Live", status="UPCOMING",
            start_iso="2026-05-10T23:00:00Z",
            categories=[{"id": "c-rope", "name": "rope"}],
        ),
    ]
    client = FakeWixClient(events=live)
    runtime = make_runtime(client)

    _patch_fetch_rows(monkeypatch, [
        _row(
            event_name="Live",
            categories="rope",  # unchanged from Wix
            short_description="EDITED IN SHEET",
            detailed_description="EDITED IN SHEET TOO",
            status="UPCOMING",
            event_id="evt-up",
        ),
    ])

    assert push_category_config(runtime, scope="all", dry_run=False) is True

    client.update_event.assert_not_called()
    client.assign_event_to_category.assert_not_called()
    client.unassign_event_from_category.assert_not_called()


def test_push_dry_run_makes_no_assign_or_unassign_calls(monkeypatch):
    live = [
        _wix_event(
            "evt-up", "Live", status="UPCOMING",
            start_iso="2026-05-10T23:00:00Z",
            categories=[{"id": "c-b", "name": "b"}],
        ),
    ]
    client = FakeWixClient(
        events=live,
        categories=[{"id": "c-a", "name": "a"}, {"id": "c-b", "name": "b"}],
    )
    runtime = make_runtime(client)

    _patch_fetch_rows(monkeypatch, [
        _row(event_name="Live", categories="a",
             status="UPCOMING", event_id="evt-up"),
    ])

    assert push_category_config(runtime, scope="all", dry_run=True) is True

    client.assign_event_to_category.assert_not_called()
    client.unassign_event_from_category.assert_not_called()
    client.update_event.assert_not_called()
