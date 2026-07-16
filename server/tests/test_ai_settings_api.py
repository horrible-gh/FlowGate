"""flowgate.default.0164 T0006: AI provider settings (D0002/P0003/L0004/DB0005).

Covers the service layer (validation, key merge, tri-state resolution, default
fallback) and the router contract (shapes, 404, 422 error format).
Uses TESTING=1 mode with a file-backed SQLite DB, mirroring test_settings_api.py.
"""
from __future__ import annotations

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


@pytest.fixture(scope="module")
def test_db_path(tmp_path_factory):
    db_path = str(tmp_path_factory.mktemp("db") / "test_ai_settings.db")
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    _migrations_dir = _SERVER_DIR / "sql" / "migrations" / "sqlite"
    for _sql_file in sorted(_migrations_dir.glob("*.sql")):
        try:
            conn.executescript(_sql_file.read_text(encoding="utf-8"))
        except sqlite3.OperationalError:
            # Some migrations re-add existing tables/columns - safe to ignore in tests.
            pass
    conn.executescript(
        """
        INSERT OR IGNORE INTO projects(project_id,project_name,is_active,created_at,updated_at)
            VALUES('__SYSTEM__','[System]',1,datetime('now'),datetime('now')),
                  ('proj_001','TestProject',1,datetime('now'),datetime('now'));
        INSERT OR IGNORE INTO users(user_id,username,email,password,is_active,is_admin,first_login_required,created_at,updated_at)
            VALUES('usr_admin','admin','admin@test.com','hashed_pw',1,1,0,datetime('now'),datetime('now'));
    """
    )
    conn.commit()
    conn.close()
    return db_path


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
            cur = self._conn.execute(sql, params or [])
            row = cur.fetchone()
            return dict(row) if row else None

        def _fetch_all(self, sql, params=None):
            cur = self._conn.execute(sql, params or [])
            return [dict(r) for r in cur.fetchall()]

        @contextmanager
        def transaction(self):
            # Autocommit per statement is fine for tests; the context only has to
            # exist so db.ai_providers.replace_scope can run.
            yield self

    store = TestStore(test_db_path)
    import importlib

    import modules.flow_gate.db.connection as _conn
    _real_get_store = _conn.get_store
    _modules = [
        importlib.import_module(_name)
        for _name in (
            "modules.flow_gate.db.connection",
            "modules.flow_gate.db.system_settings",
            "modules.flow_gate.db.projects",
            "modules.flow_gate.db.ai_providers",
            "modules.flow_gate.rbac.decorators",
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
    mock_db._execute("DELETE FROM project_settings WHERE project_id = 'proj_001'")
    yield


def _cli(name="claude cli", command="claude -p", **kw):
    p = {
        "id": None, "name": name, "exec_type": "cli", "kind": "claude",
        "enabled": True, "cli_command": command,
        "api_base_url": None, "api_model": None, "api_key": None,
    }
    p.update(kw)
    return p


# The command verbatim from 0241 B0001 (524 chars): a sandboxed `claude` run whose inline
# settings JSON pushed it past the old 500-char cli_command cap.
_B0001_CLI_COMMAND = (
    'D="$(mktemp -d /tmp/claude-XXXXXX)"; F="$D/settings.json"; printf \'%s\' '
    '\'{"permissions":{"deny":["Write(//home/sjm/**)","Edit(//home/sjm/**)",'
    '"NotebookEdit(//home/sjm/**)"]},"sandbox":{"enabled":true,'
    '"failIfUnavailable":true,"enableWeakerNestedSandbox":true,'
    '"allowUnsandboxedCommands":false,"network":{"allowedDomains":["127.0.0.1",'
    '"localhost"]},"filesystem":{"allowWrite":["/tmp"]}}}\' > "$F"; cd "$D" && '
    'echo "질문" | claude --model claude-opus-4-8 --permission-mode '
    'bypassPermissions --settings "$F" --output-format json -p -'
)


def _api(name="claude api", key="sk-ant-api03-EXAMPLEKEY-J3zQ", **kw):
    p = {
        "id": None, "name": name, "exec_type": "api", "kind": "claude",
        "enabled": True, "cli_command": None,
        "api_base_url": "https://api.anthropic.com",
        "api_model": "claude-sonnet-5", "api_key": key,
    }
    p.update(kw)
    return p


class TestSystemScope:
    def test_initial_empty_state(self):
        from modules.flow_gate.settings.ai_settings_service import get_system_settings

        result = get_system_settings()
        assert result["ok"] is True
        assert result["providers"] == []
        assert result["default_provider_id"] is None
        assert result["updated_at"] is None
        assert result["catalog"]["exec_types"] == ["cli", "api"]
        assert result["catalog"]["kinds"]["cli"] == ["claude", "copilot", "codex", "custom"]
        assert result["catalog"]["kinds"]["api"] == ["claude", "openai", "custom"]

    def test_save_two_providers_issues_ids_and_default(self):
        from modules.flow_gate.settings.ai_settings_service import save_system_settings

        result = save_system_settings([_cli(), _api()], None, 0)
        assert result["ok"] is True
        ids = [p["id"] for p in result["providers"]]
        assert all(i.startswith("aip_") and len(i) == 10 for i in ids)
        assert len(set(ids)) == 2
        assert result["default_provider_id"] == ids[0]
        # api_key never serialized; hint carries the last 4 chars.
        assert all("api_key" not in p for p in result["providers"])
        assert result["providers"][0]["api_key_set"] is False
        assert result["providers"][1]["api_key_set"] is True
        assert result["providers"][1]["api_key_hint"] == "J3zQ"

    def test_key_kept_on_omit_and_reorder(self, mock_db):
        from modules.flow_gate.settings.ai_settings_service import (
            get_provider_secret, save_system_settings,
        )

        first = save_system_settings([_cli(), _api()], None, 0)
        cli_id = first["providers"][0]["id"]
        api_id = first["providers"][1]["id"]

        # Reorder, omit api_key (None = keep), pick the api row as default by id.
        second = save_system_settings(
            [_api(key=None, id=api_id), _cli(id=cli_id)], api_id, None
        )
        assert [p["id"] for p in second["providers"]] == [api_id, cli_id]
        assert second["default_provider_id"] == api_id
        assert second["providers"][0]["api_key_set"] is True
        assert get_provider_secret(None, api_id) == "sk-ant-api03-EXAMPLEKEY-J3zQ"

    def test_key_deleted_on_empty_string(self):
        from modules.flow_gate.settings.ai_settings_service import (
            get_provider_secret, save_system_settings,
        )

        first = save_system_settings([_api()], None, 0)
        api_id = first["providers"][0]["id"]
        second = save_system_settings([_api(key="", id=api_id)], api_id, None)
        assert second["providers"][0]["api_key_set"] is False
        assert second["providers"][0]["api_key_hint"] is None
        assert get_provider_secret(None, api_id) is None

    def test_dropped_row_is_deleted(self, mock_db):
        from modules.flow_gate.settings.ai_settings_service import save_system_settings

        first = save_system_settings([_cli(), _api()], None, 0)
        cli_id = first["providers"][0]["id"]
        second = save_system_settings([_cli(id=cli_id)], cli_id, None)
        assert [p["id"] for p in second["providers"]] == [cli_id]
        rows = mock_db._fetch_all(
            "SELECT provider_id FROM ai_providers WHERE project_id IS NULL"
        )
        assert [r["provider_id"] for r in rows] == [cli_id]

    def test_blank_api_base_url_falls_back_to_kind_default(self):
        from modules.flow_gate.settings.ai_settings_service import save_system_settings

        result = save_system_settings([_api(api_base_url=None)], None, 0)
        assert result["providers"][0]["api_base_url"] == "https://api.anthropic.com"

        result = save_system_settings(
            [_api(name="openai", kind="openai", api_base_url="",
                  api_model="gpt-5", id=None)], None, 0,
        )
        assert result["providers"][0]["api_base_url"] == "https://api.openai.com"


class TestValidation:
    def test_collects_all_errors(self):
        from modules.flow_gate.settings.ai_settings_service import validate_settings

        errors = validate_settings(
            [_api(name="", api_model=None)], None, 0, None
        )
        reasons = {(e.get("field"), e["reason"]) for e in errors}
        assert ("name", "required") in reasons
        assert ("api_model", "required_for_api") in reasons

    def test_duplicate_name_case_insensitive(self):
        from modules.flow_gate.settings.ai_settings_service import validate_settings

        errors = validate_settings([_cli(name="Claude"), _cli(name="claude")], None, 0, None)
        assert any(e["reason"] == "duplicate_name" for e in errors)

    def test_unknown_kind_and_exec_type(self):
        from modules.flow_gate.settings.ai_settings_service import validate_settings

        errors = validate_settings(
            [_cli(kind="openai"), _cli(exec_type="ssh")], None, 0, None
        )
        assert any(e["reason"] == "unknown_kind" for e in errors)
        assert any(e["reason"] == "unknown_exec_type" for e in errors)

    def test_cli_requires_command(self):
        from modules.flow_gate.settings.ai_settings_service import validate_settings

        errors = validate_settings([_cli(command="  ")], None, 0, None)
        assert any(e["reason"] == "required_for_cli" for e in errors)

    def test_bad_default_index_and_id(self):
        from modules.flow_gate.settings.ai_settings_service import validate_settings

        errors = validate_settings([_cli()], None, 5, None)
        assert any(e["reason"] == "bad_default" for e in errors)
        errors = validate_settings([_cli()], "aip_zzzzzz", None, None)
        assert any(e["reason"] == "bad_default" for e in errors)

    def test_cli_command_length_boundary(self):
        # 0241 B0001: the cap was 500, which a real `claude` one-liner carrying sandbox
        # settings already exceeded. Pin both sides of the raised limit.
        from modules.flow_gate.settings.ai_settings_service import (
            CLI_COMMAND_MAX,
            validate_settings,
        )

        assert CLI_COMMAND_MAX == 4000
        at_limit = validate_settings([_cli(command="c" * CLI_COMMAND_MAX)], None, 0, None)
        assert at_limit == []
        over = validate_settings([_cli(command="c" * (CLI_COMMAND_MAX + 1))], None, 0, None)
        assert over == [{"index": 0, "field": "cli_command", "reason": "too_long"}]

    def test_cli_command_accepts_sandboxed_claude_oneliner(self):
        from modules.flow_gate.settings.ai_settings_service import validate_settings

        assert len(_B0001_CLI_COMMAND) == 524  # the length that used to trip the cap
        assert validate_settings([_cli(command=_B0001_CLI_COMMAND)], None, 0, None) == []

    def test_too_many_providers(self):
        from modules.flow_gate.settings.ai_settings_service import validate_settings

        providers = [_cli(name=f"p{i}") for i in range(21)]
        errors = validate_settings(providers, None, None, None)
        assert any(e["reason"] == "too_many" for e in errors)

    def test_inherit_and_disabled_skip_provider_checks(self):
        from modules.flow_gate.settings.ai_settings_service import validate_settings

        assert validate_settings(None, None, None, "inherit") == []
        assert validate_settings(None, None, None, "disabled") == []

    def test_unknown_mode(self):
        from modules.flow_gate.settings.ai_settings_service import validate_settings

        errors = validate_settings([_cli()], None, 0, "sometimes")
        assert any(e["reason"] == "unknown_mode" for e in errors)


class TestProjectTriState:
    def _seed_global(self):
        from modules.flow_gate.settings.ai_settings_service import save_system_settings

        return save_system_settings([_cli(), _api()], None, 0)

    def test_default_is_inherit(self):
        from modules.flow_gate.settings.ai_settings_service import get_project_settings

        self._seed_global()
        result = get_project_settings("proj_001")
        assert result["mode"] == "inherit"
        assert result["providers"] is None
        assert result["effective"]["source"] == "system"
        assert len(result["effective"]["providers"]) == 2
        assert "catalog" in result

    def test_custom_mode_uses_project_list(self):
        from modules.flow_gate.settings.ai_settings_service import save_project_settings

        self._seed_global()
        result = save_project_settings(
            "proj_001", "custom",
            [_cli(name="project codex", kind="codex", command="codex exec")],
            None, 0,
        )
        assert result["mode"] == "custom"
        assert result["effective"]["source"] == "project"
        assert [p["name"] for p in result["effective"]["providers"]] == ["project codex"]
        assert result["effective"]["default_provider_id"] == result["providers"][0]["id"]

    def test_disabled_preserves_custom_list(self):
        from modules.flow_gate.settings.ai_settings_service import (
            get_project_settings, save_project_settings,
        )

        self._seed_global()
        saved = save_project_settings(
            "proj_001", "custom",
            [_cli(name="project codex", kind="codex", command="codex exec")],
            None, 0,
        )
        custom_id = saved["providers"][0]["id"]

        disabled = save_project_settings("proj_001", "disabled", None, None, None)
        assert disabled["mode"] == "disabled"
        assert disabled["effective"] == {
            "source": "disabled", "providers": [], "default_provider_id": None,
        }
        # The preserved list is still exposed for a later custom return (L0004 §3).
        assert disabled["providers"] is not None
        assert disabled["providers"][0]["id"] == custom_id

        back = save_project_settings(
            "proj_001", "custom",
            [{**_cli(name="project codex", kind="codex", command="codex exec"),
              "id": custom_id}],
            custom_id, None,
        )
        assert back["mode"] == "custom"
        assert back["providers"][0]["id"] == custom_id

    def test_inherit_transition_keeps_list(self, mock_db):
        from modules.flow_gate.settings.ai_settings_service import save_project_settings

        self._seed_global()
        save_project_settings(
            "proj_001", "custom",
            [_cli(name="project codex", kind="codex", command="codex exec")],
            None, 0,
        )
        result = save_project_settings("proj_001", "inherit", None, None, None)
        assert result["mode"] == "inherit"
        assert result["effective"]["source"] == "system"
        rows = mock_db._fetch_all(
            "SELECT * FROM ai_providers WHERE project_id = 'proj_001'"
        )
        assert len(rows) == 1  # list preserved

    def test_effective_excludes_disabled_rows_and_falls_back(self):
        from modules.flow_gate.settings.ai_settings_service import (
            resolve_effective, save_system_settings,
        )

        first = save_system_settings([_cli(), _api()], None, 0)
        cli_id = first["providers"][0]["id"]
        api_id = first["providers"][1]["id"]
        # Disable the default (cli) — chain must exclude it and fall back to the api row.
        save_system_settings(
            [_cli(id=cli_id, enabled=False), _api(key=None, id=api_id)], cli_id, None
        )
        effective = resolve_effective("proj_001")
        assert effective["source"] == "system"
        assert [p["id"] for p in effective["providers"]] == [api_id]
        assert effective["default_provider_id"] == api_id

    def test_empty_global_inherit_gives_empty_chain(self):
        from modules.flow_gate.settings.ai_settings_service import resolve_effective

        effective = resolve_effective("proj_001")
        assert effective["providers"] == []
        assert effective["default_provider_id"] is None

    def test_unknown_project_raises(self):
        from modules.flow_gate.settings.ai_settings_service import resolve_effective

        with pytest.raises(LookupError, match="Project not found: nope"):
            resolve_effective("nope")


class TestRouterContract:
    def _make_client(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from modules.flow_gate.auth.middleware import get_current_user
        from modules.flow_gate.settings.routers.ai_settings import router

        app = FastAPI()
        app.include_router(router, prefix="/api/v1")
        app.dependency_overrides[get_current_user] = lambda: {
            "user_id": "usr_admin", "is_admin": 1,
        }
        return TestClient(app)

    def test_system_roundtrip(self):
        client = self._make_client()
        resp = client.get("/api/v1/system/ai-settings")
        assert resp.status_code == 200
        assert resp.json()["providers"] == []
        assert "catalog" in resp.json()

        resp = client.put("/api/v1/system/ai-settings", json={
            "providers": [_cli(), _api()],
            "default_provider_id": None,
            "default_provider_index": 0,
        })
        assert resp.status_code == 200
        body = resp.json()
        assert body["default_provider_id"] == body["providers"][0]["id"]
        assert "api_key" not in body["providers"][1]
        assert body["providers"][1]["api_key_hint"] == "J3zQ"

    def test_b0001_sandboxed_claude_command_saves(self):
        # 0241 B0001 end to end: this exact PUT answered 422; it must now round-trip and
        # come back with the command stored whole (no silent truncation).
        client = self._make_client()
        command = _B0001_CLI_COMMAND
        resp = client.put("/api/v1/system/ai-settings", json={
            "providers": [_cli(name="claude sandbox", command=command)],
            "default_provider_id": None,
            "default_provider_index": 0,
        })
        assert resp.status_code == 200
        assert resp.json()["providers"][0]["cli_command"] == command

        resp = client.get("/api/v1/system/ai-settings")
        assert resp.json()["providers"][0]["cli_command"] == command

    def test_validation_failure_format(self):
        client = self._make_client()
        resp = client.put("/api/v1/system/ai-settings", json={
            "providers": [_api(name="", api_model=None)],
            "default_provider_id": None,
            "default_provider_index": 0,
        })
        assert resp.status_code == 422
        detail = resp.json()["detail"]
        assert detail["code"] == "validation_failed"
        reasons = {(e.get("field"), e["reason"]) for e in detail["errors"]}
        assert ("name", "required") in reasons
        assert ("api_model", "required_for_api") in reasons

    def test_project_roundtrip_and_effective(self):
        client = self._make_client()
        resp = client.get("/api/v1/projects/proj_001/ai-settings")
        assert resp.status_code == 200
        assert resp.json()["mode"] == "inherit"

        resp = client.put("/api/v1/projects/proj_001/ai-settings", json={
            "mode": "custom",
            "providers": [_cli(name="project codex", kind="codex", command="codex exec")],
            "default_provider_id": None,
            "default_provider_index": 0,
        })
        assert resp.status_code == 200
        assert resp.json()["mode"] == "custom"

        resp = client.get("/api/v1/projects/proj_001/ai-settings/effective")
        assert resp.status_code == 200
        body = resp.json()
        assert body["source"] == "project"
        assert [p["name"] for p in body["providers"]] == ["project codex"]

    def test_project_not_found(self):
        client = self._make_client()
        resp = client.get("/api/v1/projects/proj_999/ai-settings")
        assert resp.status_code == 404
        assert resp.json()["detail"] == "Project not found: proj_999"
