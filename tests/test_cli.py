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
        "WIX_PROD_SITE_ID",
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


# ---------------------------------------------------------------------------
# Production-site guard (--production). All handlers are stubbed — these
# tests never touch any live API.
# ---------------------------------------------------------------------------

DEV_SITE_ID = "fake-dev-site-id"
PROD_SITE_ID = "fake-prod-site-id"


@pytest.fixture
def guarded_env(monkeypatch):
    """Complete fake config with WIX_SITE_ID on the dev site."""
    env = {
        "WIX_API_KEY": "fake-key",
        "WIX_SITE_ID": DEV_SITE_ID,
        "NOTION_ACCESS_TOKEN": "fake-token",
        "NOTION_EVENT_SCHEDULING_DB_ID": "db-events",
        "NOTION_CATALOG_DB_ID": "db-catalog",
        "NOTION_SETTINGS_DB_ID": "db-settings",
        "NOTION_SITE_CONFIG_DB_ID": "db-site-config",
    }
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    monkeypatch.delenv("WIX_PROD_SITE_ID", raising=False)


@pytest.fixture
def sync_stub(monkeypatch):
    """Replace the sync handler; records the config each call received."""
    calls = []

    def stub(args, config, runtime):
        calls.append(config)
        return True

    monkeypatch.setitem(cli.COMMANDS, "sync", stub)
    return calls


def test_sync_refuses_prod_site_without_production_flag(
    guarded_env, sync_stub, monkeypatch
):
    monkeypatch.setenv("WIX_SITE_ID", PROD_SITE_ID)
    monkeypatch.setenv("WIX_PROD_SITE_ID", PROD_SITE_ID)
    exit_code = cli.main(["--log-level", "CRITICAL", "sync"])
    assert exit_code == 1
    assert sync_stub == []


def test_production_flag_retargets_onto_prod_site(
    guarded_env, sync_stub, monkeypatch
):
    monkeypatch.setenv("WIX_PROD_SITE_ID", PROD_SITE_ID)
    exit_code = cli.main(["--log-level", "CRITICAL", "sync", "--production"])
    assert exit_code == 0
    assert len(sync_stub) == 1
    assert sync_stub[0].wix_site_id == PROD_SITE_ID


def test_production_flag_requires_declared_prod_site(guarded_env, sync_stub):
    # No WIX_PROD_SITE_ID in the env — the flag must refuse, not guess.
    exit_code = cli.main(["--log-level", "CRITICAL", "sync", "--production"])
    assert exit_code == 1
    assert sync_stub == []


def test_dev_sync_unaffected_by_guard(guarded_env, sync_stub, monkeypatch):
    monkeypatch.setenv("WIX_PROD_SITE_ID", PROD_SITE_ID)
    exit_code = cli.main(["--log-level", "CRITICAL", "sync"])
    assert exit_code == 0
    assert len(sync_stub) == 1
    assert sync_stub[0].wix_site_id == DEV_SITE_ID


def test_notion_only_commands_have_no_production_flag(guarded_env):
    # enrich never talks to Wix, so it must not accept --production.
    with pytest.raises(SystemExit) as excinfo:
        cli.main(["--log-level", "CRITICAL", "enrich", "--production"])
    assert excinfo.value.code != 0
