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
        "GOOGLE_SHEET_ID",
        "GOOGLE_CREDENTIALS",
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

