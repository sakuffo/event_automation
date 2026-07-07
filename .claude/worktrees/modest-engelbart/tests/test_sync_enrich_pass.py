"""Tests for the enrich pass that sync runs before pushing to Wix."""

from types import SimpleNamespace

from event_sync import cli, notion_orchestrator
from event_sync.notion_orchestrator import notion_sync_events


def make_runtime():
    """Runtime stub whose store has no syncable rows (sync exits early)."""
    store = SimpleNamespace(fetch_event_rows=lambda statuses=None: [])
    return SimpleNamespace(get_notion_store=lambda: store)


def test_sync_runs_enrich_pass_by_default(monkeypatch):
    calls = []
    monkeypatch.setattr(
        notion_orchestrator,
        "enrich_events",
        lambda runtime, month_filters=None: calls.append(month_filters) or True,
    )
    assert notion_sync_events(make_runtime(), month_filters=["aug"]) is True
    assert calls == [["aug"]]


def test_sync_skips_enrich_when_disabled(monkeypatch):
    calls = []
    monkeypatch.setattr(
        notion_orchestrator,
        "enrich_events",
        lambda runtime, month_filters=None: calls.append(1) or True,
    )
    assert notion_sync_events(make_runtime(), run_enrich=False) is True
    assert calls == []


def test_sync_skips_enrich_on_dry_run(monkeypatch):
    calls = []
    monkeypatch.setattr(
        notion_orchestrator,
        "enrich_events",
        lambda runtime, month_filters=None: calls.append(1) or True,
    )
    assert notion_sync_events(make_runtime(), dry_run=True) is True
    assert calls == []


def test_sync_continues_when_enrich_fails(monkeypatch):
    monkeypatch.setattr(
        notion_orchestrator,
        "enrich_events",
        lambda runtime, month_filters=None: False,
    )
    assert notion_sync_events(make_runtime()) is True


def test_cli_exposes_no_enrich_flag():
    parser = cli.build_parser()
    args = parser.parse_args(["sync", "--no-enrich"])
    assert args.no_enrich is True
    args = parser.parse_args(["sync"])
    assert args.no_enrich is False
