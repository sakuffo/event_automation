from types import SimpleNamespace

import pytest

from event_sync.sheets import fetch_events

# These tests target a categories-as-list EventRecord feature that lives in an
# unmerged worktree branch (.claude/worktrees/*) and has never existed on
# mainline, where `category` is a semicolon-separated string and categories is
# not a required column. Skipped until that feature is merged.
pytestmark = pytest.mark.skip(
    reason="categories-as-list feature not merged to mainline"
)


class _FakeValuesApi:
    def __init__(self, rows):
        self._rows = rows

    def get(self, spreadsheetId, range):
        return self

    def execute(self):
        return {"values": self._rows}


class _FakeSpreadsheetsApi:
    def __init__(self, rows):
        self._rows = rows

    def values(self):
        return _FakeValuesApi(self._rows)


class _FakeSheetsService:
    def __init__(self, rows):
        self._rows = rows

    def spreadsheets(self):
        return _FakeSpreadsheetsApi(self._rows)


def _runtime_for_rows(rows):
    config = SimpleNamespace(google_sheet_id="sheet-123", sheet_range="Sheet1!A1:Z100")
    return SimpleNamespace(config=config, get_sheets_service=lambda: _FakeSheetsService(rows))


def test_fetch_events_parses_categories_column():
    rows = [
        [
            "event_name",
            "categories",
            "start_date",
            "start_time",
            "end_date",
            "end_time",
            "location",
            "registration_type",
        ],
        [
            "Rope Lab",
            "rope, suspension",
            "2026-03-01",
            "19:00",
            "2026-03-01",
            "21:00",
            "Studio",
            "RSVP",
        ],
    ]

    events = fetch_events(_runtime_for_rows(rows))

    assert len(events) == 1
    assert events[0].categories == ["rope", "suspension"]


def test_fetch_events_requires_categories_column():
    rows = [
        [
            "event_name",
            "start_date",
            "start_time",
            "end_date",
            "end_time",
            "location",
        ],
        ["Rope Lab", "2026-03-01", "19:00", "2026-03-01", "21:00", "Studio"],
    ]

    with pytest.raises(ValueError, match="Missing required columns: categories"):
        fetch_events(_runtime_for_rows(rows))
