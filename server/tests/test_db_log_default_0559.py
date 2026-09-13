"""DB_LOG default direction + dead service.log key (flowgate.default.0559 T0007).

0559 NR0003 §8~10 권고2/권고3: server/config.py:43 declared `DB_LOG: bool = True`
while server/.env.sample:29 already documents `DB_LOG=false` as the intended
default — the code default pointed the wrong way, so any deployment whose
.env lacks a DB_LOG line (pydantic_settings falls back to the field default)
had full SQL/parameter echo on by default. Separately, the `"service": {"log":
True, ...}` key in all three DatabaseSetting._init_db() branches is never read
by sqloader (only "sqloder" is) and sat one line below the real engine
`"log": settings.DB_LOG` key, inviting confusion.

Uses the same assembly path as
test_pool_config_and_auth_load_0288.py::TestPoolSettingsForwarding:
DatabaseSetting.instance_init is patched to a no-op (no real DB connection),
then `_init_db()` is called on a bare instance to inspect `.config`.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

os.environ["TESTING"] = "1"
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing-only-32c")
os.environ.setdefault("ALLOWED_ORIGIN", "*")
os.environ.setdefault("CONTEXT", "/flowgate")
os.environ.setdefault("DB_TYPE", "sqlite3")

_SERVER_DIR = Path(__file__).resolve().parents[1]


class TestDBLogFieldDefault:
    def _settings(self, **overrides):
        from config import Settings

        base = {
            "ALLOWED_ORIGIN": "*",
            "SECRET_KEY": "x" * 32,
            "CONTEXT": "/flowgate",
            "DB_TYPE": "sqlite3",
        }
        base.update(overrides)
        return Settings(**base)

    def test_no_env_var_falls_back_to_false(self, monkeypatch):
        # Simulate a deployment .env with no DB_LOG line at all: pydantic_settings
        # only reads the environment it's given, so keep DB_LOG out of it.
        monkeypatch.delenv("DB_LOG", raising=False)
        s = self._settings()
        assert s.DB_LOG is False

    def test_field_default_itself_is_false(self):
        # Pin the declaration in config.py:43, independent of any environment.
        import config

        assert config.Settings.model_fields["DB_LOG"].default is False

    def test_explicit_opt_in_still_works(self):
        s = self._settings(DB_LOG=True)
        assert s.DB_LOG is True

    def test_explicit_opt_out_still_works(self):
        s = self._settings(DB_LOG=False)
        assert s.DB_LOG is False


class TestEnvSampleDirection:
    def test_env_sample_default_is_false_matching_the_code_default(self):
        # Read the actual shipped file rather than trusting the T's line number.
        env_sample = _SERVER_DIR / ".env.sample"
        lines = [
            line.strip()
            for line in env_sample.read_text(encoding="utf-8").splitlines()
            if line.strip().startswith("DB_LOG=")
        ]
        assert len(lines) == 1, "expected exactly one DB_LOG= line in .env.sample"
        assert lines[0] == "DB_LOG=false"

        import config

        # Same direction: both the sample and the field default say "off".
        assert config.Settings.model_fields["DB_LOG"].default is False


class TestServiceLogKeyRemovedAndEngineLogForwarded:
    """Mirrors TestPoolSettingsForwarding in test_pool_config_and_auth_load_0288.py."""

    @pytest.mark.parametrize(
        "db_type, engine_key",
        [
            ("mysql", "mysql"),
            ("sqlite3", "sqlite3"),
            ("postgres", "postgres"),
        ],
    )
    @pytest.mark.parametrize("db_log", [True, False])
    def test_engine_log_follows_db_log_and_service_log_key_is_gone(
        self, monkeypatch, db_type, engine_key, db_log
    ):
        import config

        monkeypatch.setattr(config.settings, "DB_TYPE", config.DBType(db_type))
        monkeypatch.setattr(config.settings, "DB_LOG", db_log)
        monkeypatch.setattr(config.DatabaseSetting, "instance_init", lambda self: None)

        setting = object.__new__(config.DatabaseSetting)
        setting._init_db()

        assert setting.config[engine_key]["log"] is db_log
        assert "log" not in setting.config["service"]
        assert setting.config["service"]["sqloder"] == config.SERVICE_SQLOADER
