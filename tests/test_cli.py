"""CLI integration smoke tests for argument handling and config validation."""

import os

import pytest  # type: ignore[import-unresolved]

from event_sync import cli


@pytest.fixture
def clear_env(monkeypatch):
    keys = [
        "WIX_API_KEY",
        "WIX_ACCOUNT_ID",
        "WIX_SITE_ID",
        "GOOGLE_CREDENTIALS",
        "NOTION_ACCESS_TOKEN",
        "NOTION_TOKEN",
        "NOTION_EVENT_SCHEDULING_DB_ID",
        "NOTION_EVENTS_DB_ID",
        "NOTION_CATALOG_DB_ID",
        "NOTION_CLASSES_DB_ID",
        "NOTION_SETTINGS_DB_ID",
        "NOTION_SITE_CONFIG_DB_ID",
    ]
    originals = {key: os.getenv(key) for key in keys}
    for key in keys:
        monkeypatch.delenv(key, raising=False)
    yield
    for key, value in originals.items():
        if value is None:
            monkeypatch.delenv(key, raising=False)
        else:
            monkeypatch.setenv(key, value)


def test_cli_validate_fails_when_env_missing(clear_env):
    exit_code = cli.main(["--log-level", "ERROR", "validate"])
    assert exit_code == 1


def test_cli_sync_exits_when_config_invalid(clear_env):
    exit_code = cli.main(["--log-level", "ERROR", "sync", "--no-tickets"])
    assert exit_code == 1


def test_cli_pull_exits_when_config_invalid(clear_env):
    exit_code = cli.main(["--log-level", "ERROR", "pull"])
    assert exit_code == 1


def test_cli_enrich_exits_when_config_invalid(clear_env):
    exit_code = cli.main(["--log-level", "ERROR", "enrich"])
    assert exit_code == 1


def test_cli_pull_rejects_invalid_scope(clear_env):
    with pytest.raises(SystemExit) as excinfo:
        cli.main(["--log-level", "ERROR", "pull", "--scope", "bogus"])
    assert excinfo.value.code != 0


def test_cli_rejects_removed_legacy_commands(clear_env):
    # The Sheets pipeline is gone; its commands must fail loudly, not silently.
    with pytest.raises(SystemExit) as excinfo:
        cli.main(["--log-level", "ERROR", "sync-sheet"])
    assert excinfo.value.code != 0
