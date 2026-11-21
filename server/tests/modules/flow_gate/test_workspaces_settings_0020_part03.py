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









    def test_legacy_role_api_keeps_the_workspace_in_step(self, store):
        from modules.flow_gate.settings import user_admin_service
        from modules.flow_gate.settings import workspace_service as ws

        user_admin_service.assign_project_role("u_free", "beta", "role_worker", granted_by="u_admin")
        assert ws.member_role("ws_default", "u_free") == "member"
        ws.sync("ws_default")
        assert _role(store, "u_free", "beta") == "role_worker"
        user_admin_service.revoke_project_role("u_free", "beta")
        ws.sync("ws_default")
        assert _role(store, "u_free", "beta") is None
class TestLegacyRbacRoleRoutes:
    """POST /rbac/users/{id}/roles and DELETE /rbac/users/{id}/roles/{project_id} go through the
    workspace: no project access without workspace membership (R0001 §3.1)."""

    def _workspace(self, store, default_member="none"):
        from modules.flow_gate.settings import workspace_service as ws

        wid = ws.create_workspace(ADMIN, "Data Platform", "u_admin")["workspace_id"]
        for pid in ("gamma", "delta"):
            store._execute("INSERT INTO projects(project_id,project_name,is_active,created_at,updated_at) "
                           "VALUES (?,?,1,datetime('now'),datetime('now'))", [pid, pid.title()])
            ws.on_project_created(ADMIN, pid, wid)
        ws.set_default_access(ADMIN, wid, {"member": default_member})
        return wid

    def test_grant_to_an_outsider_makes_a_member_with_access_to_that_project_only(self, store):
        from modules.flow_gate.settings import workspace_service as ws

        wid = self._workspace(store, default_member="dev")
        assert ws.member_role(wid, "u_free") is None
        resp = _rbac_client(ADMIN).post("/rbac/users/u_free/roles",
                                        json={"project_id": "gamma", "role_id": "role_viewer"})
        assert resp.status_code == 201
        assert resp.json()["role_id"] == "role_viewer"
        assert ws.member_role(wid, "u_free") == "member"
        assert _role(store, "u_free", "gamma") == "role_viewer"
        assert _role(store, "u_free", "delta") is None  # the member default does not leak
        ws.sync(wid)
        assert (_role(store, "u_free", "gamma"), _role(store, "u_free", "delta")) == ("role_viewer", None)

    def test_revoke_route_records_the_exception_so_a_sync_keeps_it_revoked(self, store):
        from modules.flow_gate.settings import workspace_service as ws

        wid = self._workspace(store, default_member="dev")
        ws.add_member(ADMIN, wid, "u_free", "member")
        assert _role(store, "u_free", "gamma") == "role_worker"
        resp = _rbac_client(ADMIN).delete("/rbac/users/u_free/roles/gamma")
        assert resp.status_code == 204
        assert _role(store, "u_free", "gamma") is None
        ws.sync(wid)
        assert _role(store, "u_free", "gamma") is None
        assert _role(store, "u_free", "delta") == "role_worker"

    def test_a_project_manager_cannot_pull_an_outsider_into_the_workspace(self, store, monkeypatch):
        from modules.flow_gate.rbac.routers import rbac_admin
        from modules.flow_gate.settings import workspace_service as ws

        wid = self._workspace(store)
        monkeypatch.setattr(rbac_admin, "get_user_permissions", lambda uid, pid: {"perm_role_assign"})
        client = _rbac_client(_user("u_dev"))
        resp = client.post("/rbac/users/u_free/roles", json={"project_id": "gamma", "role_id": "role_worker"})
        assert resp.status_code == 403
        assert resp.json()["detail"]["code"] == "not_workspace_member"
        assert _role(store, "u_free", "gamma") is None and ws.member_role(wid, "u_free") is None
        ws.add_member(ADMIN, wid, "u_free", "member")
        resp = client.post("/rbac/users/u_free/roles", json={"project_id": "gamma", "role_id": "role_worker"})
        assert resp.status_code == 201  # an existing member: the project-level grant stands
        assert _role(store, "u_free", "gamma") == "role_worker"

    def test_disabled_workspace_and_custom_roles_take_no_grant(self, store):
        from modules.flow_gate.settings import workspace_service as ws

        wid = self._workspace(store)
        store._execute("INSERT INTO roles(role_id,role_name,is_system,created_at,updated_at) "
                       "VALUES ('role_custom','Custom',0,datetime('now'),datetime('now'))")
        client = _rbac_client(ADMIN)
        resp = client.post("/rbac/users/u_free/roles", json={"project_id": "gamma", "role_id": "role_custom"})
        assert (resp.status_code, resp.json()["detail"]["code"]) == (422, "role_invalid")
        ws.update_workspace(ADMIN, wid, status="disabled")
        resp = client.post("/rbac/users/u_free/roles", json={"project_id": "gamma", "role_id": "role_worker"})
        assert (resp.status_code, resp.json()["detail"]["code"]) == (409, "workspace_disabled")
        assert _role(store, "u_free", "gamma") is None and ws.member_role(wid, "u_free") is None

    def test_system_roles_are_not_workspace_bound(self, store):
        from modules.flow_gate.settings import workspace_service as ws

        resp = _rbac_client(ADMIN).post("/rbac/users/u_free/roles",
                                        json={"project_id": "__SYSTEM__", "role_id": "role_manager"})
        assert resp.status_code == 201
        assert _role(store, "u_free", "__SYSTEM__") == "role_manager"
        assert ws.member_role("ws_default", "u_free") is None

    def test_settings_user_create_with_project_roles_goes_through_the_workspace(self, store):
        from modules.flow_gate.settings import user_admin_service
        from modules.flow_gate.settings import workspace_service as ws

        wid = self._workspace(store, default_member="review")
        created = user_admin_service.create_user({
            "username": "newbie", "email": "newbie@x.io", "password": "Pass1234!Pass",
            "project_roles": [{"project_id": "gamma", "role_id": "role_worker"}],
        }, created_by="u_admin")
        uid = created["user_id"]
        assert ws.member_role(wid, uid) == "member"
        assert (_role(store, uid, "gamma"), _role(store, uid, "delta")) == ("role_worker", None)
