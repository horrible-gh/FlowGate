"""Independently selectable scenarios; shared fixtures remain in test_workspaces_settings_0020.py."""
from __future__ import annotations

from tests.modules.flow_gate.test_workspaces_settings_0020 import (
    ADMIN,
    Path,
    _MIGRATIONS,
    _SEED,
    _SERVER_DIR,
    _SqliteStore,
    _build_db,
    _client,
    _rbac_client,
    _role,
    _user,
    contextmanager,
    datetime,
    os,
    pytest,
    sqlite3,
    store,
    sys,
    timedelta,
    timezone,
)

class TestAiPolicy:
    def _seed_ai(self, store):
        store._execute(
            "INSERT INTO ai_providers (provider_id, project_id, name, exec_type, kind, enabled, sort_order, "
            "cli_command, owner_scope, created_at, updated_at) "
            "VALUES ('aip_a', NULL, 'OpenAI', 'cli', 'codex', 1, 0, 'codex', 'system', datetime('now'), datetime('now')),"
            "       ('aip_b', NULL, 'Claude', 'cli', 'claude', 1, 1, 'claude', 'system', datetime('now'), datetime('now'))"
        )

    def test_allowlist_limits_the_run_chain_and_revoke_blocks_new_runs(self, store, monkeypatch):
        from modules.flow_gate.settings import ai_settings_service
        from modules.flow_gate.settings import workspace_service as ws

        self._seed_ai(store)
        chain = {"ok": True, "source": "system", "default_provider_id": "aip_a",
                 "providers": [{"id": "aip_a", "name": "OpenAI"}, {"id": "aip_b", "name": "Claude"}]}
        monkeypatch.setattr(ai_settings_service, "_require_project", lambda pid: None)
        monkeypatch.setattr(ai_settings_service, "resolve_global", lambda user_id=None: dict(chain))

        assert ws.allowed_resource_ids("alpha") is None  # policy off
        assert [p["id"] for p in ai_settings_service.resolve_effective("alpha")["providers"]] == ["aip_a", "aip_b"]

        ws.set_policy(ADMIN, "ws_default", ai_mode="allow")
        assert ai_settings_service.resolve_effective("alpha")["providers"] == []
        detail = ws.allow_resource(ADMIN, "ws_default", "provider", "aip_b")
        assert detail["policy"]["allowlist"][0]["name"] == "Claude"
        effective = ai_settings_service.resolve_effective("alpha")
        assert [p["id"] for p in effective["providers"]] == ["aip_b"]
        assert effective["default_provider_id"] == "aip_b"

        ws.revoke_resource(ADMIN, "ws_default", "provider", "aip_b")
        assert ai_settings_service.resolve_effective("alpha")["providers"] == []
        row = store._fetch_one("SELECT status FROM workspace_ai_allowlist WHERE resource_id='aip_b'")
        assert row["status"] == "revoked"  # kept as history
        with pytest.raises(ws.WorkspaceError):
            ws.allow_resource(ADMIN, "ws_default", "provider", "aip_missing")

    def test_resources_list_system_ai_and_agents(self, store):
        from modules.flow_gate.settings import workspace_service as ws

        self._seed_ai(store)
        store._execute("INSERT INTO agents(agent_id,name,enabled,location,created_at,updated_at) "
                       "VALUES ('ag_1','Local Codex Worker',1,'local',datetime('now'),datetime('now'))")
        result = ws.list_policy_resources(ADMIN, "ws_default")
        assert [p["name"] for p in result["providers"]] == ["OpenAI", "Claude"]
        assert result["agents"][0]["name"] == "Local Codex Worker"
        ws.set_policy(ADMIN, "ws_default", ai_mode="allow")
        ws.allow_resource(ADMIN, "ws_default", "agent", "ag_1")
        assert ws.is_agent_allowed("alpha", "ag_1") and not ws.is_agent_allowed("alpha", "ag_2")
class TestSystemUsers:
    def test_list_summary(self, store):
        from modules.flow_gate.settings import system_user_service as su

        result = su.list_users()
        assert result["summary"]["total"] == 5 and result["summary"]["admins"] == 2
        assert result["summary"]["no_workspace"] == 1  # u_free
        assert result["summary"]["tfa_missing"] == 5
        free = next(u for u in result["users"] if u["user_id"] == "u_free")
        assert free["workspaces"] == [] and free["state"] == "active"

    def test_invite_create_accept_and_login_state(self, store):
        from modules.flow_gate.settings import system_user_service as su

        created = su.create_user(ADMIN, "Han Seojun", "han@x.io", "invite", False, "ws_default")
        assert created["user"]["state"] == "invited" and created["invite_path"].startswith("/login?invite=")
        assert created["user"]["workspaces"][0]["workspace_id"] == "ws_default"
        token = created["invite_path"].split("=", 1)[1]
        assert su.find_invited(token)["username"] == "Han Seojun"
        with pytest.raises(su.SystemUserError) as weak:
            su.accept_invite(token, "short")
        assert weak.value.code == "invalid_password_policy"
        assert su.accept_invite(token, "Str0ng-Passw0rd!")["email"] == "han@x.io"
        assert su.find_invited(token) is None
        with pytest.raises(su.SystemUserError) as dup:
            su.create_user(ADMIN, "Other", "han@x.io", "password", False)
        assert dup.value.code == "email_already_exists"

    def test_temporary_password_user_is_admin_when_asked(self, store):
        from modules.flow_gate.settings import system_user_service as su

        created = su.create_user(ADMIN, "Choi Yuna", "yuna@x.io", "password", True)
        assert created["temp_password"] and created["user"]["is_admin"] is True
        row = store._fetch_one("SELECT first_login_required FROM users WHERE username='Choi Yuna'")
        assert row["first_login_required"] == 1

    def test_last_admin_guard_and_disable_ends_sessions(self, store):
        from modules.flow_gate.auth.session_store import create_session, get_session
        from modules.flow_gate.settings import system_user_service as su

        su.set_admin(ADMIN, "u_admin2", False)
        with pytest.raises(su.SystemUserError) as last:
            su.set_admin(ADMIN, "u_admin", False)
        assert last.value.code == "last_admin"
        with pytest.raises(su.SystemUserError):
            su.disable_user({"user_id": "u_admin2", "is_admin": 1}, "u_admin")
        sid = create_session("u_dev")
        assert su.disable_user(ADMIN, "u_dev")["state"] == "disabled"
        assert get_session(sid)["revoked_at"] is not None
        assert su.enable_user(ADMIN, "u_dev")["state"] == "active"
