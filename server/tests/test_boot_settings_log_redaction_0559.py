"""Boot-time settings log stops leaking secrets (flowgate.default.0559 NR0010 §5 / T0011).

NR0010 found that ``DatabaseSetting._init_db()`` unconditionally logged the whole
``Settings`` object (``logger.debug("settings", settings)``), independent of
``DB_LOG``. LogAssist's ``debug(tag, msg)`` builds the record with an f-string
(``f'[{tag}] {msg}'``), which stringifies the pydantic ``Settings`` instance and
puts ``SECRET_KEY``, ``DB_PASSWORD`` and the ``FLOWGATE_*_ENCRYPT_KEY`` values into
``logs/default.log`` in plain text on every boot. T0011 scopes the fix to exactly
this line: replace the raw object with an explicit allow-list of non-secret
fields. This locks that in.
"""
from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

import pytest

os.environ["TESTING"] = "1"
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing-only-32c")
os.environ.setdefault("ALLOWED_ORIGIN", "*")
os.environ.setdefault("CONTEXT", "/flowgate")
os.environ.setdefault("DB_TYPE", "sqlite3")

_SERVER_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_SERVER_DIR))

_SECRET_MARKERS = {
    "SECRET_KEY": "s3cr3t-jwt-signing-marker",
    "DB_PASSWORD": "s3cr3t-db-password-marker",
    "FLOWGATE_GIT_ENCRYPT_KEY": "s3cr3t-git-key-marker",
    "FLOWGATE_TOTP_ENCRYPT_KEY": "s3cr3t-totp-key-marker",
}


@pytest.fixture
def boot_with_secrets(monkeypatch):
    """A DatabaseSetting._init_db() run with distinctive, non-guessable secret values.

    Mirrors test_pool_config_and_auth_load_0288.py::TestPoolSettingsForwarding: build
    the config dict only (instance_init is a no-op) so no real DB connection happens.
    """
    import config

    monkeypatch.setattr(config.DatabaseSetting, "instance_init", lambda self: None)
    monkeypatch.setattr(config.settings, "DB_TYPE", config.DBType.SQLITE3)
    for field, value in _SECRET_MARKERS.items():
        monkeypatch.setattr(config.settings, field, value)
    return config


def test_boot_log_never_contains_a_secret_value(boot_with_secrets, caplog):
    caplog.set_level(logging.DEBUG, logger="LogAssist.log")

    setting = object.__new__(boot_with_secrets.DatabaseSetting)
    setting._init_db()

    text = caplog.text
    for marker in _SECRET_MARKERS.values():
        assert marker not in text


def test_boot_log_still_reports_the_intended_fields(boot_with_secrets, caplog):
    caplog.set_level(logging.DEBUG, logger="LogAssist.log")

    setting = object.__new__(boot_with_secrets.DatabaseSetting)
    setting._init_db()

    text = caplog.text
    assert "sqlite3" in text
    assert "DB_LOG" in text
    assert "AUTO_MIGRATION" in text
