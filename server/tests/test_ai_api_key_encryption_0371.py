"""flowgate.default.0371 T0010: AI provider API keys encrypted at rest (NR0007 §3).

`ai_providers.api_key` held the operator's provider secret verbatim, so any DB dump,
replica or backup carried a directly usable Anthropic/OpenAI key. These tests pin the
whole boundary:

  - what actually lands in the column (ciphertext with an explicit enc:v1: version tag)
  - that the L0004 §2.3 keep/replace/delete contract and the last-4 hint still work on
    the plaintext, i.e. the screen behaves exactly as before
  - the legacy plaintext backfill (idempotent, resumable)
  - key rotation via _PREV, and the fail-closed behaviour when no key can read a row —
    including the case that would otherwise lose data silently: "keep" on a row whose
    ciphertext is unreadable must NOT erase it

The DB fixtures mirror test_ai_settings_api.py (TESTING=1 + file-backed SQLite).
"""
from __future__ import annotations

import base64
import os
import sqlite3
import sys
from contextlib import contextmanager
from pathlib import Path

import pytest

os.environ["TESTING"] = "1"
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing-only-32c")

_SERVER_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_SERVER_DIR))

KEY_A = base64.b64encode(b"A" * 32).decode("ascii")
KEY_B = base64.b64encode(b"B" * 32).decode("ascii")

PLAIN_KEY = "sk-ant-api03-EXAMPLEKEY-J3zQ"


@pytest.fixture(autouse=True)
def ai_key(monkeypatch, tmp_path):
    """Every test runs with a known master key, and a storage root of its own so a
    key-file fallback can never touch the real one."""
    monkeypatch.setenv("FLOWGATE_AI_ENCRYPT_KEY", KEY_A)
    monkeypatch.delenv("FLOWGATE_AI_ENCRYPT_KEY_PREV", raising=False)
    monkeypatch.setenv("FLOWGATE_STORAGE_DIR", str(tmp_path / "storage"))
    yield


@pytest.fixture
def crypto():
    from modules.flow_gate.utils import api_key_crypto
    return api_key_crypto


# ── The crypto boundary itself ───────────────────────────────────────────────

class TestApiKeyCrypto:
    def test_roundtrip_carries_the_version_prefix(self, crypto):
        stored = crypto.encrypt_api_key(PLAIN_KEY)
        assert stored.startswith("enc:v1:")
        assert PLAIN_KEY not in stored
        assert crypto.decrypt_api_key(stored) == PLAIN_KEY

    def test_same_key_encrypts_differently_every_time(self, crypto):
        """A random nonce per write — otherwise equal ciphertexts would leak that two
        providers share one key."""
        first, second = crypto.encrypt_api_key(PLAIN_KEY), crypto.encrypt_api_key(PLAIN_KEY)
        assert first != second
        assert crypto.decrypt_api_key(first) == crypto.decrypt_api_key(second) == PLAIN_KEY

    def test_absent_values_pass_through(self, crypto):
        assert crypto.encrypt_api_key(None) is None
        assert crypto.encrypt_api_key("") == ""
        assert crypto.decrypt_api_key(None) is None
        assert crypto.decrypt_api_key("") == ""

    def test_a_base64_shaped_plaintext_is_not_mistaken_for_ciphertext(self, crypto):
        """NR0007 §3 권고 2: real API keys are often base64-shaped, so "looks like
        base64" can never be the encrypted/plaintext discriminator."""
        looks_like_b64 = base64.b64encode(b"a real key that happens to be base64").decode()
        assert crypto.is_encrypted(looks_like_b64) is False
        assert crypto.decrypt_api_key(looks_like_b64) == looks_like_b64

    def test_encrypting_an_encrypted_value_is_a_no_op(self, crypto):
        """Re-saving a row must never double-wrap: the second layer would be undecryptable
        by one pass and the value would come back as `enc:v1:...` text."""
        stored = crypto.encrypt_api_key(PLAIN_KEY)
        assert crypto.encrypt_api_key(stored) == stored

    def test_previous_key_still_decrypts_during_rotation(self, crypto, monkeypatch):
        stored = crypto.encrypt_api_key(PLAIN_KEY)
        monkeypatch.setenv("FLOWGATE_AI_ENCRYPT_KEY", KEY_B)
        monkeypatch.setenv("FLOWGATE_AI_ENCRYPT_KEY_PREV", KEY_A)
        assert crypto.decrypt_api_key(stored) == PLAIN_KEY

    def test_a_wrong_key_fails_closed(self, crypto, monkeypatch):
        """The failure is explicit. Returning the ciphertext (or None) would hide a lost
        master key until a run mysteriously authenticated with garbage."""
        stored = crypto.encrypt_api_key(PLAIN_KEY)
        monkeypatch.setenv("FLOWGATE_AI_ENCRYPT_KEY", KEY_B)
        with pytest.raises(crypto.ApiKeyCryptoError):
            crypto.decrypt_api_key(stored)

    def test_a_malformed_key_env_is_reported_not_ignored(self, crypto, monkeypatch):
        monkeypatch.setenv("FLOWGATE_AI_ENCRYPT_KEY", base64.b64encode(b"too short").decode())
        with pytest.raises(crypto.ApiKeyCryptoError):
            crypto.encrypt_api_key(PLAIN_KEY)

    def test_no_configured_key_provisions_one_under_the_storage_root(self, crypto, monkeypatch, tmp_path):
        """An install that predates this change has no FLOWGATE_AI_ENCRYPT_KEY. It must
        keep working (git_service's key-file precedent) — storing plaintext instead is
        never the fallback (NR0007 §3 권고 5)."""
        from config import settings as cfg

        monkeypatch.delenv("FLOWGATE_AI_ENCRYPT_KEY", raising=False)
        # "No key configured" means neither the environment NOR .env has one: pydantic
        # snapshots .env into `settings` at boot, and that snapshot is the second source
        # api_key_crypto consults (git_service's resolution order).
        monkeypatch.setattr(cfg, "FLOWGATE_AI_ENCRYPT_KEY", None, raising=False)
        root = tmp_path / "keyless-storage"
        monkeypatch.setenv("FLOWGATE_STORAGE_DIR", str(root))

        stored = crypto.encrypt_api_key(PLAIN_KEY)
        key_file = root / crypto.KEY_FILE_NAME
        assert key_file.is_file()
        assert len(base64.b64decode(key_file.read_text(encoding="ascii").strip())) == 32
        # The generated key persists, so a later read finds the same value.
        assert crypto.decrypt_api_key(stored) == PLAIN_KEY


# ── DB fixtures (test_ai_settings_api.py precedent) ──────────────────────────

@pytest.fixture(scope="module")
def test_db_path(migrated_sqlite_db):
    """flowgate.default.0394 T0010 (NR0003 §13-6 / §7.2): built by the shared
    conftest.py factory instead of a private migration loop, so this suite's schema
    can never drift from the others the way test_auth.py's hand-picked subset did."""
    return migrated_sqlite_db(
        "test_ai_key_encryption.db",
        seed_sql="""
        INSERT OR IGNORE INTO projects(project_id,project_name,is_active,created_at,updated_at)
            VALUES('__SYSTEM__','[System]',1,datetime('now'),datetime('now'));
        """,
    )


@pytest.fixture(autouse=True)
def mock_db(test_db_path):
    class TestStore:
        def __init__(self, db_path):
            self._conn = sqlite3.connect(db_path, check_same_thread=False)
            self._conn.row_factory = sqlite3.Row
            self._conn.execute("PRAGMA foreign_keys = ON")

        def _execute(self, sql, params=None):
            self._conn.execute(sql, params or [])
            self._conn.commit()

        def _fetch_one(self, sql, params=None):
            row = self._conn.execute(sql, params or []).fetchone()
            return dict(row) if row else None

        def _fetch_all(self, sql, params=None):
            return [dict(r) for r in self._conn.execute(sql, params or []).fetchall()]

        @contextmanager
        def transaction(self):
            yield self

    store = TestStore(test_db_path)
    import importlib

    import modules.flow_gate.db.connection as _conn
    _real_get_store = _conn.get_store
    _modules = [
        importlib.import_module(name)
        for name in (
            "modules.flow_gate.db.connection",
            "modules.flow_gate.db.system_settings",
            "modules.flow_gate.db.projects",
            "modules.flow_gate.db.ai_providers",
        )
    ]
    for _m in _modules:
        _m.get_store = lambda store=store: store
    try:
        yield store
    finally:
        for _m in _modules:
            _m.get_store = _real_get_store


@pytest.fixture(autouse=True)
def clean_tables(mock_db):
    mock_db._execute("DELETE FROM ai_providers")
    mock_db._execute("DELETE FROM system_settings WHERE setting_key = 'ai_default_provider_id'")
    yield


@pytest.fixture
def svc():
    from modules.flow_gate.settings import ai_settings_service
    return ai_settings_service


@pytest.fixture
def ai_db():
    from modules.flow_gate.db import ai_providers
    return ai_providers


def _api_provider(**kw):
    p = {
        "id": None, "name": "openai api", "exec_type": "api", "kind": "openai",
        "enabled": True, "cli_command": None, "api_base_url": None,
        "api_model": "gpt-5.6-sol", "api_key": PLAIN_KEY,
    }
    p.update(kw)
    return p


def _raw_key(store, provider_id):
    row = store._fetch_one(
        "SELECT api_key FROM ai_providers WHERE provider_id = ?", [provider_id],
    )
    return row["api_key"] if row else None


def _echo(view):
    """The provider view sent straight back on the next save (the editor's own round
    trip): no api_key field at all, i.e. "keep"."""
    return {
        "id": view["id"], "name": view["name"], "exec_type": view["exec_type"],
        "kind": view["kind"], "enabled": view["enabled"],
        "cli_command": view["cli_command"], "api_base_url": view["api_base_url"],
        "api_model": view["api_model"],
    }


# ── What lands in the column ─────────────────────────────────────────────────

class TestAtRestStorage:
    def test_the_column_never_holds_the_plaintext_key(self, svc, mock_db):
        result = svc.save_system_settings([_api_provider()], None, None)
        provider_id = result["providers"][0]["id"]

        stored = _raw_key(mock_db, provider_id)
        assert stored.startswith("enc:v1:")
        assert PLAIN_KEY not in stored

    def test_the_view_contract_is_unchanged(self, svc):
        """L0004 §2.3: the response still carries only api_key_set + the last-4 hint,
        and the hint is computed from the decrypted value — not from ciphertext."""
        result = svc.save_system_settings([_api_provider()], None, None)
        view = result["providers"][0]
        assert "api_key" not in view
        assert view["api_key_set"] is True
        assert view["api_key_hint"] == PLAIN_KEY[-4:]
        assert "api_key_unreadable" not in view

    def test_execution_reads_the_plaintext_back(self, svc):
        result = svc.save_system_settings([_api_provider()], None, None)
        provider_id = result["providers"][0]["id"]
        assert svc.get_provider_secret(None, provider_id) == PLAIN_KEY

    def test_keep_replace_delete_still_hold(self, svc, mock_db):
        first = svc.save_system_settings([_api_provider()], None, None)
        provider_id = first["providers"][0]["id"]

        # keep (api_key absent)
        kept = svc.save_system_settings([_echo(first["providers"][0])], None, None)
        assert kept["providers"][0]["api_key_set"] is True
        assert svc.get_provider_secret(None, provider_id) == PLAIN_KEY
        assert _raw_key(mock_db, provider_id).startswith("enc:v1:")

        # replace
        replaced = svc.save_system_settings(
            [dict(_echo(kept["providers"][0]), api_key="sk-openai-REPLACED-9xYz")], None, None,
        )
        assert replaced["providers"][0]["api_key_hint"] == "9xYz"
        assert svc.get_provider_secret(None, provider_id) == "sk-openai-REPLACED-9xYz"
        assert "REPLACED" not in _raw_key(mock_db, provider_id)

        # delete ("")
        deleted = svc.save_system_settings(
            [dict(_echo(replaced["providers"][0]), api_key="")], None, None,
        )
        assert deleted["providers"][0]["api_key_set"] is False
        assert _raw_key(mock_db, provider_id) is None
        assert svc.get_provider_secret(None, provider_id) is None

    def test_a_cli_provider_without_a_key_stores_null(self, svc, mock_db):
        result = svc.save_system_settings(
            [{"id": None, "name": "claude cli", "exec_type": "cli", "kind": "claude",
              "enabled": True, "cli_command": "claude -p", "api_base_url": None,
              "api_model": None, "api_key": None}], None, None,
        )
        assert _raw_key(mock_db, result["providers"][0]["id"]) is None


# ── Legacy plaintext backfill ────────────────────────────────────────────────

class TestBackfill:
    def _insert_plaintext_row(self, store, provider_id="aip_legacy", key=PLAIN_KEY):
        store._execute(
            "INSERT INTO ai_providers (provider_id, project_id, name, exec_type, kind, "
            "enabled, cli_command, api_base_url, api_model, api_key, sort_order, "
            "created_at, updated_at) VALUES (?, NULL, ?, 'api', 'openai', 1, NULL, NULL, "
            "'gpt-5.6-sol', ?, 0, '2026-01-01T00:00:00+09:00', '2026-01-01T00:00:00+09:00')",
            [provider_id, "legacy " + provider_id, key],
        )
        return provider_id

    def test_plaintext_rows_are_migrated_and_stay_readable(self, ai_db, svc, mock_db):
        provider_id = self._insert_plaintext_row(mock_db)

        assert ai_db.encrypt_plaintext_api_keys() == 1

        stored = _raw_key(mock_db, provider_id)
        assert stored.startswith("enc:v1:")
        assert PLAIN_KEY not in stored
        assert svc.get_provider_secret(None, provider_id) == PLAIN_KEY

    def test_the_backfill_is_idempotent(self, ai_db, mock_db):
        """A second boot — or a pass that died halfway — must be a no-op, not a second
        layer of encryption."""
        provider_id = self._insert_plaintext_row(mock_db)
        ai_db.encrypt_plaintext_api_keys()
        after_first = _raw_key(mock_db, provider_id)

        assert ai_db.encrypt_plaintext_api_keys() == 0
        assert _raw_key(mock_db, provider_id) == after_first

    def test_a_mixed_table_migrates_only_the_plaintext_rows(self, ai_db, svc, mock_db):
        encrypted = svc.save_system_settings([_api_provider()], None, None)["providers"][0]["id"]
        untouched = _raw_key(mock_db, encrypted)
        legacy = self._insert_plaintext_row(mock_db, "aip_legacy2", "sk-legacy-KEY-abCd")

        assert ai_db.encrypt_plaintext_api_keys() == 1
        assert _raw_key(mock_db, encrypted) == untouched
        assert svc.get_provider_secret(None, legacy) == "sk-legacy-KEY-abCd"

    def test_rows_without_a_key_are_left_alone(self, ai_db, svc, mock_db):
        svc.save_system_settings(
            [{"id": None, "name": "claude cli", "exec_type": "cli", "kind": "claude",
              "enabled": True, "cli_command": "claude -p", "api_base_url": None,
              "api_model": None, "api_key": None}], None, None,
        )
        assert ai_db.encrypt_plaintext_api_keys() == 0

    def test_a_legacy_row_reads_back_before_the_backfill_runs(self, svc, mock_db):
        """The migration is a boot step, so both states exist in the wild for a moment:
        an un-migrated row must still serve its key rather than blow up."""
        provider_id = self._insert_plaintext_row(mock_db, "aip_legacy3")
        assert svc.get_provider_secret(None, provider_id) == PLAIN_KEY
        assert svc.get_system_settings()["providers"][0]["api_key_hint"] == PLAIN_KEY[-4:]


# ── Rotation and the unreadable-row case ─────────────────────────────────────

class TestRotationAndFailClosed:
    def test_the_previous_key_keeps_stored_keys_readable(self, svc, monkeypatch):
        provider_id = svc.save_system_settings(
            [_api_provider()], None, None)["providers"][0]["id"]

        monkeypatch.setenv("FLOWGATE_AI_ENCRYPT_KEY", KEY_B)
        monkeypatch.setenv("FLOWGATE_AI_ENCRYPT_KEY_PREV", KEY_A)
        assert svc.get_provider_secret(None, provider_id) == PLAIN_KEY

    def test_a_lost_key_is_reported_not_silently_empty(self, svc, crypto, monkeypatch):
        provider_id = svc.save_system_settings(
            [_api_provider()], None, None)["providers"][0]["id"]
        monkeypatch.setenv("FLOWGATE_AI_ENCRYPT_KEY", KEY_B)

        with pytest.raises(crypto.ApiKeyCryptoError):
            svc.get_provider_secret(None, provider_id)

    def test_the_settings_screen_still_renders_an_unreadable_row(self, svc, monkeypatch):
        """One unreadable row must not take the whole settings page down — that is the
        page the operator needs in order to type a replacement key."""
        svc.save_system_settings([_api_provider()], None, None)
        monkeypatch.setenv("FLOWGATE_AI_ENCRYPT_KEY", KEY_B)

        view = svc.get_system_settings()["providers"][0]
        assert view["api_key_set"] is True      # a key IS stored...
        assert view["api_key_hint"] is None     # ...but its plaintext is gone
        assert view["api_key_unreadable"] is True

    def test_keeping_an_unreadable_key_does_not_erase_it(self, svc, mock_db, monkeypatch):
        """The data-loss trap: with no plaintext to merge, a plain "keep" would write
        NULL and destroy a key the request never mentioned — after which restoring the
        master key would recover nothing."""
        first = svc.save_system_settings([_api_provider()], None, None)
        provider_id = first["providers"][0]["id"]
        stored_before = _raw_key(mock_db, provider_id)

        monkeypatch.setenv("FLOWGATE_AI_ENCRYPT_KEY", KEY_B)
        svc.save_system_settings(
            [dict(_echo(first["providers"][0]), name="renamed while unreadable")], None, None,
        )
        assert _raw_key(mock_db, provider_id) == stored_before

        monkeypatch.setenv("FLOWGATE_AI_ENCRYPT_KEY", KEY_A)
        assert svc.get_provider_secret(None, provider_id) == PLAIN_KEY

    def test_an_unreadable_key_can_still_be_replaced(self, svc, mock_db, monkeypatch):
        first = svc.save_system_settings([_api_provider()], None, None)
        provider_id = first["providers"][0]["id"]

        monkeypatch.setenv("FLOWGATE_AI_ENCRYPT_KEY", KEY_B)
        replaced = svc.save_system_settings(
            [dict(_echo(first["providers"][0]), api_key="sk-openai-FRESH-1234")], None, None,
        )
        assert replaced["providers"][0]["api_key_hint"] == "1234"
        assert "api_key_unreadable" not in replaced["providers"][0]
        assert svc.get_provider_secret(None, provider_id) == "sk-openai-FRESH-1234"
        assert _raw_key(mock_db, provider_id).startswith("enc:v1:")

    def test_an_unreadable_key_can_still_be_deleted(self, svc, mock_db, monkeypatch):
        first = svc.save_system_settings([_api_provider()], None, None)
        provider_id = first["providers"][0]["id"]

        monkeypatch.setenv("FLOWGATE_AI_ENCRYPT_KEY", KEY_B)
        deleted = svc.save_system_settings(
            [dict(_echo(first["providers"][0]), api_key="")], None, None,
        )
        assert deleted["providers"][0]["api_key_set"] is False
        assert _raw_key(mock_db, provider_id) is None


# ── The run path ─────────────────────────────────────────────────────────────

class TestInvokePath:
    def test_an_unreadable_key_fails_the_spawn_with_its_own_reason(self, monkeypatch):
        """`api_key_not_set` would send the operator looking for a key nobody removed."""
        from modules.flow_gate.services import ai_invoke_service as svc_invoke
        from modules.flow_gate.utils.api_key_crypto import ApiKeyCryptoError

        def _boom(scope, provider_id):
            raise ApiKeyCryptoError("master key changed")

        monkeypatch.setattr(svc_invoke.ai_settings_service, "get_provider_secret", _boom)
        run = {"project_id": None, "chain_source": "system", "run_id": "run_x",
               "docs_target": 1}
        status, detail = svc_invoke._api_execute(
            {"id": "aip_x", "kind": "openai", "api_model": "gpt-5.6-sol"}, "prompt", run,
        )
        assert (status, detail) == ("spawn_failed", "api_key_unreadable")


# ── Provisioning (the key has to exist wherever FlowGate is installed) ───────

class TestKeyProvisioning:
    """A key that only some install paths create is the failure mode the git key already
    had (0273 P2-2): the container generated one, host installs quietly fell back."""

    _REPO_ROOT = _SERVER_DIR.parent

    @pytest.mark.parametrize("path", [
        "server/.env.sample", "docker-compose.yml", "deploy/docker-entrypoint.sh",
        "setup.sh", "setup.ps1",
    ])
    def test_every_install_path_knows_the_key(self, path):
        body = (self._REPO_ROOT / path).read_text(encoding="utf-8")
        assert "FLOWGATE_AI_ENCRYPT_KEY" in body, f"{path} does not provision the AI key"

    def test_config_declares_the_key_so_a_dotenv_line_does_not_break_boot(self):
        """pydantic forbids extra keys: an undeclared FLOWGATE_AI_ENCRYPT_KEY line in
        .env would fail the boot outright (the TOTP-key lesson, config.py §)."""
        from config import Settings

        assert "FLOWGATE_AI_ENCRYPT_KEY" in Settings.model_fields
        assert "FLOWGATE_AI_ENCRYPT_KEY_PREV" in Settings.model_fields

    def test_startup_runs_the_backfill(self):
        import startup

        assert hasattr(startup, "encrypt_ai_provider_keys")
        source = (_SERVER_DIR / "startup.py").read_text(encoding="utf-8")
        assert "encrypt_ai_provider_keys()" in source.split("def run_all")[1]
