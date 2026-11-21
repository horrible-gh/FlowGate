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

class TestWorkspaces:



    def test_removing_a_member_leaves_every_project_and_owner_is_locked(self, store):
        from modules.flow_gate.settings import workspace_service as ws

        ws.remove_member(ADMIN, "ws_default", "u_dev")
        assert _role(store, "u_dev", "alpha") is None
        assert store._fetch_all("SELECT * FROM workspace_project_access WHERE user_id='u_dev'") == []
        with pytest.raises(ws.WorkspaceError) as locked:
            ws.remove_member(ADMIN, "ws_default", "u_admin")
        assert locked.value.code == "owner_locked"

    def test_member_management_needs_owner_or_admin(self, store):
        from modules.flow_gate.settings import workspace_service as ws

        with pytest.raises(ws.WorkspaceError):
            ws.add_member(_user("u_dev"), "ws_default", "u_free", "member")
        ws.add_member(_user("u_admin2"), "ws_default", "u_free", "member")  # workspace admin
        with pytest.raises(ws.WorkspaceError) as denied:
            ws.update_workspace(_user("u_admin2"), "ws_default", name="Renamed")
        assert denied.value.code == "forbidden"  # rename is the owner's

    def test_disable_revokes_and_enable_restores_access(self, store):
        from modules.flow_gate.settings import workspace_service as ws

        lab = ws.create_workspace(ADMIN, "Archive Team", "u_admin")
        store._execute("UPDATE projects SET workspace_id=? WHERE project_id='beta'", [lab["workspace_id"]])
        ws.add_member(ADMIN, lab["workspace_id"], "u_view", "admin")
        assert _role(store, "u_view", "beta") == "role_worker"
        ws.update_workspace(ADMIN, lab["workspace_id"], status="disabled")
        assert _role(store, "u_view", "beta") is None
        ws.update_workspace(ADMIN, lab["workspace_id"], status="active")
        assert _role(store, "u_view", "beta") == "role_worker"
        with pytest.raises(ws.WorkspaceError) as locked:
            ws.update_workspace(ADMIN, "ws_default", status="disabled")
        assert locked.value.code == "default_workspace_locked"

    def test_delete_needs_an_empty_non_default_workspace(self, store):
        from modules.flow_gate.settings import workspace_service as ws

        lab = ws.create_workspace(ADMIN, "Data Platform", "u_admin")
        ws.add_member(ADMIN, lab["workspace_id"], "u_dev", "member")
        with pytest.raises(ws.WorkspaceError) as busy:
            ws.delete_workspace(ADMIN, lab["workspace_id"])
        assert busy.value.code == "workspace_not_empty"
        ws.remove_member(ADMIN, lab["workspace_id"], "u_dev")
        ws.delete_workspace(ADMIN, lab["workspace_id"])
        assert store._fetch_one("SELECT 1 FROM workspaces WHERE workspace_id=?", [lab["workspace_id"]]) is None
        with pytest.raises(ws.WorkspaceError):
            ws.delete_workspace(ADMIN, "ws_default")

    def test_project_create_policy(self, store):
        from modules.flow_gate.settings import workspace_service as ws

        assert ws.resolve_create_workspace(_user("u_dev"), None) == "ws_default"  # migrated: all
        ws.set_policy(ADMIN, "ws_default", project_create="owner_admin")
        with pytest.raises(ws.WorkspaceError) as denied:
            ws.resolve_create_workspace(_user("u_dev"), None)
        assert denied.value.code == "project_create_forbidden"
        assert ws.resolve_create_workspace(_user("u_admin2"), "ws_default") == "ws_default"
        ws.set_policy(ADMIN, "ws_default", project_create="owner")
        assert not ws.can_create_project(_user("u_admin2"), "ws_default")
        with pytest.raises(ws.WorkspaceError):
            ws.resolve_create_workspace(_user("u_free"), None)  # not a member

    def test_new_project_goes_to_its_workspace_and_creator_can_open_it(self, store):
        from modules.flow_gate.settings import workspace_service as ws

        store._execute("INSERT INTO projects(project_id,project_name,is_active,created_at,updated_at) "
                       "VALUES ('gamma','Gamma',1,datetime('now'),datetime('now'))")
        ws.on_project_created(_user("u_dev"), "gamma", "ws_default")
        assert store._fetch_one("SELECT workspace_id FROM projects WHERE project_id='gamma'")["workspace_id"] == "ws_default"
        assert _role(store, "u_dev", "gamma") == "role_manager"
        assert _role(store, "u_admin2", "gamma") == "role_worker"  # admin default: dev
