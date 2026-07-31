"""Tests for the pull and enrich passes that sync runs before its refresh."""

from types import SimpleNamespace

from event_sync import cli, notion_orchestrator
from event_sync.notion_orchestrator import notion_sync_events


def make_runtime():
    """Runtime stub whose store has no syncable rows (sync exits early)."""
    store = SimpleNamespace(fetch_event_rows=lambda statuses=None: [])
    return SimpleNamespace(get_notion_store=lambda: store)


def patch_pull(monkeypatch, calls=None, result=True):
    monkeypatch.setattr(
        notion_orchestrator,
        "pull_events",
        lambda runtime, scope="upcoming": (
            calls.append(scope) if calls is not None else None
        ) or result,
    )


def patch_enrich(monkeypatch, calls=None, result=True):
    monkeypatch.setattr(
        notion_orchestrator,
        "enrich_events",
        lambda runtime, month_filters=None: (
            calls.append(month_filters) if calls is not None else None
        ) or result,
    )


# ---------------------------------------------------------------------------
# Pull pass
# ---------------------------------------------------------------------------


def test_sync_runs_pull_pass_by_default(monkeypatch):
    calls = []
    patch_pull(monkeypatch, calls)
    patch_enrich(monkeypatch)
    assert notion_sync_events(make_runtime()) is True
    assert calls == ["upcoming"]


def test_sync_skips_pull_when_disabled(monkeypatch):
    calls = []
    patch_pull(monkeypatch, calls)
    patch_enrich(monkeypatch)
    assert notion_sync_events(make_runtime(), run_pull=False) is True
    assert calls == []


def test_sync_skips_pull_on_dry_run(monkeypatch):
    calls = []
    patch_pull(monkeypatch, calls)
    assert notion_sync_events(make_runtime(), dry_run=True) is True
    assert calls == []


def test_sync_continues_when_pull_fails(monkeypatch):
    patch_pull(monkeypatch, result=False)
    patch_enrich(monkeypatch)
    assert notion_sync_events(make_runtime()) is True


def test_sync_runs_pull_before_enrich(monkeypatch):
    order = []
    monkeypatch.setattr(
        notion_orchestrator,
        "pull_events",
        lambda runtime, scope="upcoming": order.append("pull") or True,
    )
    monkeypatch.setattr(
        notion_orchestrator,
        "enrich_events",
        lambda runtime, month_filters=None: order.append("enrich") or True,
    )
    assert notion_sync_events(make_runtime()) is True
    assert order == ["pull", "enrich"]


# ---------------------------------------------------------------------------
# Enrich pass
# ---------------------------------------------------------------------------


def test_sync_runs_enrich_pass_by_default(monkeypatch):
    calls = []
    patch_pull(monkeypatch)
    patch_enrich(monkeypatch, calls)
    assert notion_sync_events(make_runtime(), month_filters=["aug"]) is True
    assert calls == [["aug"]]


def test_sync_skips_enrich_when_disabled(monkeypatch):
    calls = []
    patch_pull(monkeypatch)
    patch_enrich(monkeypatch, calls)
    assert notion_sync_events(make_runtime(), run_enrich=False) is True
    assert calls == []


def test_sync_skips_enrich_on_dry_run(monkeypatch):
    calls = []
    patch_enrich(monkeypatch, calls)
    assert notion_sync_events(make_runtime(), dry_run=True) is True
    assert calls == []


def test_sync_continues_when_enrich_fails(monkeypatch):
    patch_pull(monkeypatch)
    patch_enrich(monkeypatch, result=False)
    assert notion_sync_events(make_runtime()) is True


# ---------------------------------------------------------------------------
# CLI flags
# ---------------------------------------------------------------------------


def test_cli_exposes_no_enrich_flag():
    parser = cli.build_parser()
    args = parser.parse_args(["sync", "--no-enrich"])
    assert args.no_enrich is True
    args = parser.parse_args(["sync"])
    assert args.no_enrich is False


def test_cli_exposes_no_pull_flag():
    parser = cli.build_parser()
    args = parser.parse_args(["sync", "--no-pull"])
    assert args.no_pull is True
    args = parser.parse_args(["sync"])
    assert args.no_pull is False
