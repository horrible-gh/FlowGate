"""Server access guard: member check, target-based check, list filters and session checks.

Seeds a SQLite database from the deploy SQL and checks, with real routers:

* a non-member is refused (403) on another project's document, conversation,
  work plan, TR2 and workflow endpoints when asking by ID;
* a role on ``__SYSTEM__`` alone does not make anyone a project member;
* a request pairing its own project with another project's document is refused;
* the removed legacy routes are gone and the kept storage routes need a login;
* project, module and group lists only hold the caller's projects;
* a disabled account or a revoked session stops a user JWT on the outbound path.
"""
from __future__ import annotations

import os
import sqlite3
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Optional

import pytest
from fastapi import APIRouter, Depends, FastAPI, HTTPException
from fastapi.testclient import TestClient
from pydantic import BaseModel
from starlette.requests import Request

os.environ.setdefault("SECRET_KEY", "test-secret-key-for-access-guard-0090")

_SERVER_DIR = Path(__file__).resolve().parents[3]
_SCHEMA_DIR = _SERVER_DIR / "sql" / "migrations" / "sqlite"
sys.path.insert(0, str(_SERVER_DIR))

NOW = "2026-11-25T00:00:00+00:00"

ADMIN = {"user_id": "u_admin", "username": "g0090_admin", "is_admin": 1, "is_active": 1}
MEMBER = {"user_id": "u_member", "username": "g0090_member", "is_admin": 0, "is_active": 1}
VIEWER = {"user_id": "u_viewer", "username": "g0090_viewer", "is_admin": 0, "is_active": 1}
SYSTEM_ONLY = {"user_id": "u_system", "username": "g0090_system", "is_admin": 0, "is_active": 1}
OUTSIDER = {"user_id": "u_out", "username": "g0090_outsider", "is_admin": 0, "is_active": 1}
DISABLED = {"user_id": "u_off", "username": "g0090_disabled", "is_admin": 0, "is_active": 0}
USERS = (ADMIN, MEMBER, VIEWER, SYSTEM_ONLY, OUTSIDER, DISABLED)


class _Txn:
    def __init__(self, conn):
        self._conn = conn
        self._cur = None

    def execute(self, sql, params=None):
        self._cur = self._conn.execute(sql, params or [])

    def fetchone(self):
        row = self._cur.fetchone() if self._cur else None
        return dict(row) if row else None

    def fetchall(self):
        return [dict(r) for r in self._cur.fetchall()] if self._cur else []


class _DB:
    def __init__(self, path: str):
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")

    def execute(self, sql, params=None):
        self._conn.execute(sql, params or [])
        self._conn.commit()

    def fetch_one(self, sql, params=None):
        row = self._conn.execute(sql, params or []).fetchone()
        return dict(row) if row else None

    def fetch_all(self, sql, params=None):
        return [dict(r) for r in self._conn.execute(sql, params or []).fetchall()]

    @contextmanager
    def begin_transaction(self):
        yield _Txn(self._conn)
        self._conn.commit()

    def close(self):
        self._conn.close()


@pytest.fixture(scope="module")
def db():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as handle:
        path = handle.name
    database = _DB(path)
    for sql_file in sorted(_SCHEMA_DIR.glob("*.sql")):
        try:
            database._conn.executescript(sql_file.read_text(encoding="utf-8"))
        except Exception:
            pass
    database._conn.commit()
    yield database
    database.close()
    os.unlink(path)


@pytest.fixture(scope="module", autouse=True)
def store(db):
    from modules.flow_gate.db import connection as conn_mod

    original = conn_mod.STORE

    class _Store(conn_mod.FlowGateStore):
        def __init__(self):
            self._db = db
            self._sq = None

    conn_mod.STORE = _Store()
    _seed(db)
    yield conn_mod.STORE
    conn_mod.STORE = original


@pytest.fixture(autouse=True)
def fresh_caches():
    from modules.flow_gate.auth import auth_cache
    from modules.flow_gate.rbac import permission_service

    permission_service.clear_all_cache()
    auth_cache.invalidate_user()
    yield
    permission_service.clear_all_cache()
    auth_cache.invalidate_user()


def _seed(db: _DB) -> None:
    run = db._conn.execute
    for user in USERS:
        run(
            "INSERT OR IGNORE INTO users (user_id, username, email, password, is_admin, is_active,"
            " created_at, updated_at) VALUES (?,?,?,?,?,?,?,?)",
            [user["user_id"], user["username"], f"{user['username']}@example.test", "x",
             user["is_admin"], user["is_active"], NOW, NOW],
        )
    for project in ("pa", "pb"):
        run(
            "INSERT OR IGNORE INTO projects (project_id, project_name, is_active, created_at, updated_at)"
            " VALUES (?,?,1,?,?)",
            [project, project.upper(), NOW, NOW],
        )
        run(
            "INSERT OR IGNORE INTO groups (group_id, project_id, module, title, status, created_at, updated_at)"
            " VALUES (?,?,?,?,?,?,?)",
            [f"{project}.m.0001", project, "m", f"{project} flow", "OPEN", NOW, NOW],
        )
        run(
            "INSERT OR IGNORE INTO documents (doc_id, project_id, module, group_id, type_code, seq, title,"
            " status, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
            [f"{project}.m.0001.0001-R", project, "m", f"{project}.m.0001", "R", 1, "Root",
             "draft", NOW, NOW],
        )
        run(
            "INSERT OR IGNORE INTO project_modules (module_id, project_id, name, title, created_at, updated_at)"
            " VALUES (?,?,?,?,?,?)",
            [f"{project}:m", project, "m", f"{project} module", NOW, NOW],
        )
    grants = (
        (MEMBER["user_id"], "pa", "role_worker"),
        (VIEWER["user_id"], "pa", "role_viewer"),
        (DISABLED["user_id"], "pa", "role_worker"),
        (SYSTEM_ONLY["user_id"], "__SYSTEM__", "role_admin"),
    )
    for user_id, project, role in grants:
        run(
            "INSERT OR IGNORE INTO user_project_roles (user_id, project_id, role_id, granted_at)"
            " VALUES (?,?,?,?)",
            [user_id, project, role, NOW],
        )
    db._conn.commit()


DOC_A, DOC_B = "pa.m.0001.0001-R", "pb.m.0001.0001-R"
GROUP_A, GROUP_B = "pa.m.0001", "pb.m.0001"


def _status(call) -> Optional[int]:
    try:
        call()
    except HTTPException as exc:
        return exc.status_code
    return None


# ── The one judgment ─────────────────────────────────────────────────────────

class TestCan:
    def test_admin_may_do_everything(self):
        from modules.flow_gate.rbac import access

        assert access.can(ADMIN, "pb", "project.document.delete")

    def test_member_only_on_own_project(self):
        from modules.flow_gate.rbac import access

        assert access.can(MEMBER, "pa", "project.document.read")
        assert access.can(MEMBER, "pa", "project.document.update")
        assert not access.can(MEMBER, "pb", "project.document.read")

    def test_role_limits_the_member(self):
        from modules.flow_gate.rbac import access

        assert access.can(VIEWER, "pa", "project.document.read")
        assert not access.can(VIEWER, "pa", "project.document.update")

    def test_system_role_alone_is_not_membership(self):
        from modules.flow_gate.rbac import access

        assert not access.can(SYSTEM_ONLY, "pa", "project.document.read")
        assert not access.can(OUTSIDER, "pa", "project.document.read")

    def test_visible_projects_come_from_sql(self, store):
        from modules.flow_gate.rbac import access

        sql, params = access.visible_project_clause(MEMBER)
        rows = store._fetch_all("SELECT project_id FROM projects WHERE 1 = 1" + sql, params)
        assert {r["project_id"] for r in rows} == {"pa"}
        assert access.visible_project_clause(ADMIN) == ("", [])


# ── Real routers: a non-member asking by ID ─────────────────────────────────

class TestDirectIdRequests:
    def test_document_detail(self):
        from modules.flow_gate.documents.routers import documents as routes

        assert _status(lambda: routes.get_document(doc_id=DOC_B, include=None, current_user=OUTSIDER)) == 403
        assert _status(lambda: routes.get_document(doc_id=DOC_B, include=None, current_user=SYSTEM_ONLY)) == 403
        assert _status(lambda: routes.get_document_rpc(doc_id=DOC_B, current_user=MEMBER)) == 403

    def test_document_list_of_another_project(self):
        from modules.flow_gate.documents.routers import documents as routes

        assert _status(lambda: routes.list_documents(
            project_id="pb", module=None, group_id=None, type_code=None, status=None,
            limit=10, offset=0, current_user=MEMBER)) == 403

    def test_conversation(self):
        from modules.flow_gate.documents.routers import conversation_turns as routes

        request = Request({"type": "http", "headers": []})
        assert _status(lambda: routes.list_conversation_turns(
            doc_id=DOC_B, request=request, after_seq=None, before_seq=None, limit=None,
            include_head=None, current_user=OUTSIDER)) == 403
        assert _status(lambda: routes.get_conversation_settings(doc_id=DOC_B, current_user=MEMBER)) == 403

    def test_work_plan(self):
        from modules.flow_gate.documents.routers import work_plan as routes

        request = Request({"type": "http", "headers": []})
        assert _status(lambda: routes.get_work_plan(request=request, doc_id=DOC_B, current_user=OUTSIDER)) == 403

    def test_tr2(self):
        from modules.flow_gate.documents.routers import tr2 as routes

        assert _status(lambda: routes.get_tr2(doc_id=DOC_B, current_user=OUTSIDER)) == 403

    def test_workflow(self):
        from modules.flow_gate.workflow.routers import workflow as wf

        assert _status(lambda: wf.document_prompt_endpoint(DOC_B, OUTSIDER)) == 403
        assert _status(lambda: wf.group_timeline_endpoint(GROUP_B, limit=10, current_user=MEMBER)) == 403
        assert _status(lambda: wf.list_groups_endpoint(
            project_id="pb", module=None, status=None, current_user=MEMBER)) == 403
        assert _status(lambda: wf.document_transition_endpoint(
            DOC_B, "submit", wf.DocumentTransitionRequest(), OUTSIDER)) == 403

    def test_workflow_permissions_follow_the_project(self):
        from modules.flow_gate.workflow.routers import workflow as wf

        assert "document.read" in wf._project_permissions(MEMBER, "pa")
        assert "document.approve" not in wf._project_permissions(MEMBER, "pa")
        assert wf._project_permissions(MEMBER, "pb") == set()
        assert wf._project_permissions(SYSTEM_ONLY, "pa") == set()
        assert wf._project_permissions(VIEWER, "pa") == {"document.read"}
        assert wf._project_permissions(MEMBER, None) == set()
        assert "document.approve" in wf._project_permissions(ADMIN, None)

    def test_member_passes_the_check(self):
        from modules.flow_gate.workflow.routers import workflow as wf

        result = wf.group_timeline_endpoint(GROUP_A, limit=10, current_user=MEMBER)
        assert result["group_id"] == GROUP_A


# ── Decorator through FastAPI: signature, body targets, mismatched IDs ───────

class _Body(BaseModel):
    project_id: str
    prev_doc_id: Optional[str] = None


def _guarded_app(user: dict) -> TestClient:
    from modules.flow_gate.auth.middleware import get_current_user
    from modules.flow_gate.rbac.access import require_target_permission

    router = APIRouter()

    @router.post("/next")
    @require_target_permission("project.document.create")
    def create_next(body: _Body, current_user: dict = Depends(get_current_user)):
        return {"ok": True, "project_id": body.project_id}

    @router.get("/doc/{doc_id}")
    @require_target_permission("project.document.read")
    async def read_doc(doc_id: str, current_user: dict = Depends(get_current_user)):
        return {"doc_id": doc_id}

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_current_user] = lambda: user
    return TestClient(app)


class TestTargetDecorator:
    def test_own_project_and_own_document(self):
        client = _guarded_app(MEMBER)
        assert client.post("/next", json={"project_id": "pa", "prev_doc_id": DOC_A}).status_code == 200
        assert client.get(f"/doc/{DOC_A}").status_code == 200

    def test_own_project_with_another_projects_document_is_refused(self):
        client = _guarded_app(MEMBER)
        assert client.post("/next", json={"project_id": "pa", "prev_doc_id": DOC_B}).status_code == 403
        assert client.post("/next", json={"project_id": "pb"}).status_code == 403
        assert client.get(f"/doc/{DOC_B}").status_code == 403

    def test_viewer_cannot_create(self):
        client = _guarded_app(VIEWER)
        assert client.post("/next", json={"project_id": "pa"}).status_code == 403
        assert client.get(f"/doc/{DOC_A}").status_code == 200


# ── Legacy routes ────────────────────────────────────────────────────────────

class TestLegacyRoutes:
    def test_unused_routes_are_removed(self):
        from modules.flow_gate.api.v1 import legacy_misc_routes

        paths = {route.path for route in legacy_misc_routes.router.routes}
        assert paths == {"/api/v1/storage/folder", "/api/v1/storage/file"}

    def test_storage_needs_a_login(self):
        from modules.flow_gate.api.v1 import legacy_misc_routes

        app = FastAPI()
        app.include_router(legacy_misc_routes.router)
        response = TestClient(app).post("/api/v1/storage/folder", json={"project_id": "pa"})
        assert response.status_code == 401

    def test_storage_checks_the_project(self, monkeypatch):
        from modules.flow_gate.api.v1 import legacy_misc_routes
        from modules.flow_gate.auth.middleware import get_current_user

        created = []
        monkeypatch.setattr(legacy_misc_routes.process_service, "create_storage_folder",
                            lambda **kwargs: created.append(kwargs) or {"status": "success"})
        app = FastAPI()
        app.include_router(legacy_misc_routes.router)
        app.dependency_overrides[get_current_user] = lambda: MEMBER
        client = TestClient(app)
        other = {"project_id": "pb", "parent_path": "", "name": "x"}
        mixed = {"project_id": "pa", "parent_path": "", "name": "x", "group_id": GROUP_B}
        own = {"project_id": "pa", "parent_path": "", "name": "x", "group_id": GROUP_A}
        assert client.post("/api/v1/storage/folder", json=other).status_code == 403
        assert client.post("/api/v1/storage/folder", json=mixed).status_code == 403
        assert client.post("/api/v1/storage/folder", json=own).status_code == 200
        assert len(created) == 1


# ── Lists ────────────────────────────────────────────────────────────────────

class TestLists:
    def _projects(self, user):
        from modules.flow_gate.settings.routers import project_settings

        rows = project_settings.list_projects_endpoint(status="all", user=user)["projects"]
        return {row["project_id"] for row in rows} - {"__SYSTEM__"}

    def test_project_list(self):
        assert self._projects(MEMBER) == {"pa"}
        assert self._projects(OUTSIDER) == set()
        assert self._projects(SYSTEM_ONLY) == set()
        assert {"pa", "pb"} <= self._projects(ADMIN)

    def test_module_and_group_lists(self, monkeypatch):
        import json

        from modules.flow_gate.api.v1 import module_routes

        request = Request({"type": "http", "headers": []})
        auth = {"issued_to": MEMBER["user_id"], "_is_user_jwt": True}
        monkeypatch.setattr(module_routes, "verify_bearer", lambda _request: auth)

        modules = json.loads(module_routes.list_modules(request, project_id=None).body)
        assert {item["title"] for item in modules["items"]} == {"pa module", "All"}
        assert module_routes.list_modules(request, project_id="pb").status_code == 403

        groups = json.loads(module_routes.list_groups_for_module(request, "m").body)
        assert {item["project"] for item in groups["items"]} == {"pa"}
        assert module_routes.list_documents_for_group(request, "m", GROUP_B).status_code == 403
        assert module_routes.list_predecessor_docs(request, DOC_B).status_code == 403


# ── Account and session checks on the outbound JWT path ─────────────────────

class TestOutboundJwt:
    def _token(self, user: dict, sid: Optional[str]) -> str:
        from modules.flow_gate.auth.jwt_service import create_access_token

        token, _ = create_access_token(user["user_id"], user["username"], [], False, sid=sid)
        return token

    def test_active_session_passes(self):
        from modules.flow_gate.auth.session_store import create_session
        from modules.flow_gate.services import auth_outbound

        sid = create_session(MEMBER["user_id"])
        result = auth_outbound._verify_user_jwt(self._token(MEMBER, sid))
        assert isinstance(result, dict) and result["issued_to"] == MEMBER["user_id"]

    def test_revoked_session_is_refused(self):
        from modules.flow_gate.auth import auth_cache
        from modules.flow_gate.auth.session_store import create_session, revoke_session
        from modules.flow_gate.services import auth_outbound

        sid = create_session(MEMBER["user_id"])
        token = self._token(MEMBER, sid)
        revoke_session(sid, MEMBER["user_id"], "logout")
        auth_cache.invalidate_session(sid)
        result = auth_outbound._verify_user_jwt(token)
        assert getattr(result, "status_code", None) == 401

    def test_disabled_account_is_refused(self):
        from modules.flow_gate.services import auth_outbound

        result = auth_outbound._verify_user_jwt(self._token(DISABLED, None))
        assert getattr(result, "status_code", None) == 401

    def test_disabled_account_is_refused_on_the_session_path(self):
        from modules.flow_gate.auth import middleware

        assert _status(lambda: middleware.get_current_user({"sub": DISABLED["user_id"]})) == 401
        assert middleware.get_current_user({"sub": MEMBER["user_id"]})["user_id"] == MEMBER["user_id"]


# ── Review rework: same target in check and read, attachments, outbound membership ──

def _jwt(user: dict) -> dict:
    return {"issued_to": user["user_id"], "_is_user_jwt": True}


class TestMemberPermissionClause:
    def _ids(self, store, user, permission="project.document.read"):
        from modules.flow_gate.rbac import access

        sql, params = access.member_permission_clause(user, permission, "p.project_id")
        rows = store._fetch_all("SELECT p.project_id FROM projects p WHERE 1 = 1" + sql, params)
        return {r["project_id"] for r in rows}

    def test_membership_and_permission_in_sql(self, store):
        from modules.flow_gate.rbac import access

        assert self._ids(store, MEMBER) == {"pa"}
        assert self._ids(store, VIEWER) == {"pa"}
        assert self._ids(store, VIEWER, "project.document.update") == set()
        # role_admin on __SYSTEM__ holds project.document.read but is not membership.
        assert self._ids(store, SYSTEM_ONLY) == set()
        assert self._ids(store, OUTSIDER) == set()
        assert access.member_permission_clause(ADMIN, "project.document.read", "p.project_id") == ("", [])


class TestBodyDiagnosisScope:
    def _calls(self, monkeypatch):
        from modules.flow_gate.documents import body_recovery

        calls = []
        monkeypatch.setattr(body_recovery, "diagnose_scope",
                            lambda group_id=None, project_id=None: calls.append((group_id, project_id)) or {"ok": True})
        return calls

    def test_padded_group_id_is_judged_as_the_stored_group(self, monkeypatch):
        from modules.flow_gate.documents.routers import documents as routes

        calls = self._calls(monkeypatch)
        padded = f"  {GROUP_B} "
        # Through the decorator and through the endpoint's own scope check.
        assert _status(lambda: routes.diagnose_document_bodies(
            group_id=padded, project_id=None, current_user=MEMBER)) == 403
        assert _status(lambda: routes.diagnose_document_bodies.__wrapped__(
            group_id=padded, project_id=None, current_user=MEMBER)) == 403
        assert _status(lambda: routes.diagnose_document_bodies.__wrapped__(
            group_id=None, project_id=" pb ", current_user=MEMBER)) == 403
        assert calls == []

    def test_system_role_alone_and_unknown_scope_are_refused(self, monkeypatch):
        from modules.flow_gate.documents.routers import documents as routes

        calls = self._calls(monkeypatch)
        assert _status(lambda: routes.diagnose_document_bodies.__wrapped__(
            group_id=GROUP_A, project_id=None, current_user=SYSTEM_ONLY)) == 403
        assert _status(lambda: routes.diagnose_document_bodies.__wrapped__(
            group_id="pa.m.9999", project_id=None, current_user=MEMBER)) == 404
        assert calls == []

    def test_member_runs_the_diagnosis_on_the_checked_ids(self, monkeypatch):
        from modules.flow_gate.documents.routers import documents as routes

        calls = self._calls(monkeypatch)
        routes.diagnose_document_bodies(group_id=f" {GROUP_A} ", project_id=None, current_user=MEMBER)
        routes.diagnose_document_bodies(group_id=None, project_id=" pa", current_user=MEMBER)
        assert calls == [(GROUP_A, None), (None, "pa")]


class TestAttachmentGuard:
    def _refusal(self, user, doc_id=DOC_A, permission="project.document.read"):
        import anyio

        from modules.flow_gate.documents.routers import documents as routes

        return anyio.run(routes._attachment_permission_refusal, doc_id, user, permission)

    def test_route_guard_uses_membership(self):
        assert self._refusal(MEMBER) is None
        assert self._refusal(ADMIN, DOC_B, "project.document.update") is None
        assert self._refusal(SYSTEM_ONLY).status_code == 403
        assert self._refusal(OUTSIDER).status_code == 403
        assert self._refusal(VIEWER, permission="project.document.update").status_code == 403

    def test_attachment_request_judgment_uses_membership(self):
        from modules.flow_gate.documents.attachments import requests as att_requests

        assert att_requests.has_permission(MEMBER, "project.document.read", "pa")
        assert not att_requests.has_permission(SYSTEM_ONLY, "project.document.read", "pa")
        assert not att_requests.has_permission(MEMBER, "project.document.read", "pb")


class TestOutboundMembership:
    def test_can_read_project_needs_membership_for_a_user_jwt(self):
        from modules.flow_gate.services import auth_outbound

        assert auth_outbound.can_read_project(_jwt(MEMBER), "pa")
        assert not auth_outbound.can_read_project(_jwt(MEMBER), "pb")
        assert not auth_outbound.can_read_project(_jwt(SYSTEM_ONLY), "pa")
        assert auth_outbound.can_read_project(_jwt(ADMIN), "pb")

    def test_readable_projects_come_from_sql(self, monkeypatch):
        from modules.flow_gate.services import auth_outbound

        # The visibility set is selected by the database: judging rows in Python
        # (can_read_project) is never consulted.
        monkeypatch.setattr(auth_outbound, "can_read_project",
                            lambda *_a, **_k: pytest.fail("python-side project filter used"))
        assert auth_outbound.readable_projects(_jwt(MEMBER)) == ["pa"]
        assert auth_outbound.readable_projects(_jwt(SYSTEM_ONLY)) == []
        assert auth_outbound.readable_projects(_jwt(OUTSIDER)) == []
        assert {"pa", "pb"} <= set(auth_outbound.readable_projects(_jwt(ADMIN)))

    def test_worker_token_rule_is_unchanged(self):
        from modules.flow_gate.services import auth_outbound

        worker = {"issued_to": MEMBER["user_id"]}
        assert auth_outbound.readable_projects(worker) == ["pa"]
        assert auth_outbound.can_read_project(worker, "pa")
        assert not auth_outbound.can_read_project(worker, "pb")

    def test_module_group_and_document_lists_for_a_system_only_user(self, monkeypatch):
        import json

        from modules.flow_gate.api.v1 import module_routes

        request = Request({"type": "http", "headers": []})
        monkeypatch.setattr(module_routes, "verify_bearer", lambda _request: _jwt(SYSTEM_ONLY))

        modules = json.loads(module_routes.list_modules(request, project_id=None).body)
        titles = {item["title"] for item in modules["items"]}
        assert "pa module" not in titles and "pb module" not in titles
        assert module_routes.list_modules(request, project_id="pa").status_code == 403

        groups = json.loads(module_routes.list_groups_for_module(request, "m").body)
        assert groups["items"] == []
        assert module_routes.list_documents_for_group(request, "m", GROUP_A).status_code == 403
        assert module_routes.list_predecessor_docs(request, DOC_A).status_code == 403

    def test_outbound_project_list(self, monkeypatch):
        import json

        from modules.flow_gate.api.v1 import list_routes

        request = Request({"type": "http", "headers": []})

        def listed(user, **kwargs):
            monkeypatch.setattr(list_routes, "verify_bearer", lambda _request: _jwt(user))
            params = {"limit": 50, "offset": 0, "status": "all", "is_active": None, **kwargs}
            return json.loads(list_routes.list_projects(request, **params).body)

        member = listed(MEMBER)
        assert [item["project_id"] for item in member["items"]] == ["pa"] and member["total"] == 1
        assert listed(SYSTEM_ONLY)["items"] == [] and listed(SYSTEM_ONLY)["total"] == 0
        admin = listed(ADMIN, limit=1)
        assert len(admin["items"]) == 1 and admin["total"] >= 2
        assert list_routes.list_projects(request, limit=10, offset=-1, status=None, is_active=None).status_code == 400


# ── Review rework 2: per-project judgment of the module/group lists ──────────

MULTI = {"user_id": "u_multi", "username": "g0090_multi", "is_admin": 0, "is_active": 1}
NO_READ_ROLE = "role_g0090_noread"


@pytest.fixture(scope="class")
def multi_project_seed(db):
    """A third project ``pc`` and a user whose permission differs per project.

    ``u_multi`` is a worker on ``pa`` (may read) and a member of ``pc`` through a
    role without any permission (may not read); ``pb`` is not theirs at all.
    """
    run = db._conn.execute
    run(
        "INSERT OR IGNORE INTO users (user_id, username, email, password, is_admin, is_active,"
        " created_at, updated_at) VALUES (?,?,?,?,?,?,?,?)",
        [MULTI["user_id"], MULTI["username"], f"{MULTI['username']}@example.test", "x", 0, 1, NOW, NOW],
    )
    run(
        "INSERT OR IGNORE INTO roles (role_id, role_name, description, is_system, created_at, updated_at)"
        " VALUES (?,?,?,0,?,?)",
        [NO_READ_ROLE, NO_READ_ROLE, "member role without permissions", NOW, NOW],
    )
    run(
        "INSERT OR IGNORE INTO projects (project_id, project_name, is_active, created_at, updated_at)"
        " VALUES ('pc','PC',1,?,?)",
        [NOW, NOW],
    )
    run(
        "INSERT OR IGNORE INTO groups (group_id, project_id, module, title, status, created_at, updated_at)"
        " VALUES ('pc.m.0001','pc','m','pc flow','OPEN',?,?)",
        [NOW, NOW],
    )
    run(
        "INSERT OR IGNORE INTO project_modules (module_id, project_id, name, title, created_at, updated_at)"
        " VALUES ('pc:m','pc','m','pc module',?,?)",
        [NOW, NOW],
    )
    for project, role in (("pa", "role_worker"), ("pc", NO_READ_ROLE)):
        run(
            "INSERT OR IGNORE INTO user_project_roles (user_id, project_id, role_id, granted_at)"
            " VALUES (?,?,?,?)",
            [MULTI["user_id"], project, role, NOW],
        )
    db._conn.commit()
    yield
    for sql in (
        "DELETE FROM user_project_roles WHERE user_id = 'u_multi'",
        "DELETE FROM project_modules WHERE module_id = 'pc:m'",
        "DELETE FROM groups WHERE group_id = 'pc.m.0001'",
        "DELETE FROM projects WHERE project_id = 'pc'",
        "DELETE FROM roles WHERE role_id = 'role_g0090_noread'",
        "DELETE FROM users WHERE user_id = 'u_multi'",
    ):
        run(sql)
    db._conn.commit()


def _worker(user: dict) -> dict:
    return {"issued_to": user["user_id"]}


_CALLERS = [
    (f"{kind}:{user['username']}", maker(user))
    for user in (ADMIN, MEMBER, VIEWER, SYSTEM_ONLY, OUTSIDER, MULTI)
    for kind, maker in (("jwt", _jwt), ("worker", _worker))
]


@pytest.mark.usefixtures("multi_project_seed")
class TestPerProjectListJudgment:
    """Every listed row is judged against its own project, like ``can_read_project``."""

    def test_permission_really_differs_per_project(self):
        from modules.flow_gate.services import auth_outbound

        assert auth_outbound.can_read_project(_jwt(MULTI), "pa")
        assert not auth_outbound.can_read_project(_jwt(MULTI), "pc")
        assert not auth_outbound.can_read_project(_jwt(MULTI), "pb")
        assert auth_outbound.can_read_project(_worker(MULTI), "pa")
        assert not auth_outbound.can_read_project(_worker(MULTI), "pc")
        assert not auth_outbound.can_read_project(_worker(MULTI), "pb")

    @pytest.mark.parametrize("label,auth", _CALLERS, ids=[c[0] for c in _CALLERS])
    def test_group_list_matches_the_individual_judgment(self, monkeypatch, store, label, auth):
        import json

        from modules.flow_gate.api.v1 import module_routes
        from modules.flow_gate.services import auth_outbound

        groups = store._fetch_all("SELECT group_id, project_id FROM groups WHERE module = 'm'")
        expected = {g["group_id"] for g in groups if auth_outbound.can_read_project(auth, g["project_id"])}

        request = Request({"type": "http", "headers": []})
        monkeypatch.setattr(module_routes, "verify_bearer", lambda _request: auth)
        body = json.loads(module_routes.list_groups_for_module(request, "m", limit=200).body)
        listed = {item["group_id"] for item in body["items"]}
        assert listed == expected, label
        assert body["total"] == len(expected), label

    @pytest.mark.parametrize("label,auth", _CALLERS, ids=[c[0] for c in _CALLERS])
    def test_module_list_matches_the_individual_judgment(self, monkeypatch, store, label, auth):
        import json

        from modules.flow_gate.api.v1 import module_routes
        from modules.flow_gate.services import auth_outbound

        modules = store._fetch_all("SELECT project_id, name, title FROM project_modules")
        readable = [m for m in modules if auth_outbound.can_read_project(auth, m["project_id"])]
        expected = {m["title"] or m["name"] for m in readable}

        request = Request({"type": "http", "headers": []})
        monkeypatch.setattr(module_routes, "verify_bearer", lambda _request: auth)
        body = json.loads(module_routes.list_modules(request, project_id=None, limit=200).body)
        # The synthetic "All" entry the endpoint prepends is not a stored module.
        listed = {item["title"] for item in body["items"] if (item["name"], item["title"]) != ("none", "All")}
        assert listed == expected, label
        assert body["total"] == len(readable), label

    @pytest.mark.parametrize("label,auth", _CALLERS, ids=[c[0] for c in _CALLERS])
    def test_readable_projects_match_the_individual_judgment(self, store, label, auth):
        from modules.flow_gate.services import auth_outbound

        projects = [r["project_id"] for r in store._fetch_all("SELECT project_id FROM projects")]
        expected = sorted(p for p in projects if auth_outbound.can_read_project(auth, p))
        assert auth_outbound.readable_projects(auth) == expected, label

    def test_multi_project_member_sees_only_the_readable_project(self, monkeypatch):
        import json

        from modules.flow_gate.api.v1 import module_routes

        request = Request({"type": "http", "headers": []})
        for auth in (_jwt(MULTI), _worker(MULTI)):
            monkeypatch.setattr(module_routes, "verify_bearer", lambda _request, auth=auth: auth)
            body = json.loads(module_routes.list_groups_for_module(request, "m").body)
            assert [item["group_id"] for item in body["items"]] == [GROUP_A]
            assert body["total"] == 1

    def test_unqualified_project_column_is_refused(self):
        from modules.flow_gate.rbac import access
        from modules.flow_gate.services import auth_outbound

        for column in ("project_id", "acl_role.project_id", "g.project_id OR 1=1"):
            with pytest.raises(ValueError):
                auth_outbound.readable_project_clause(_jwt(MULTI), column)
            with pytest.raises(ValueError):
                auth_outbound.readable_project_clause(_worker(MULTI), column)
            with pytest.raises(ValueError):
                access.member_permission_clause(MULTI, "project.document.read", column)


class TestProjectListQuery:
    """The SQL the session project list (GET /projects) runs, without importing the router.

    ``TestLists.test_project_list`` drives the endpoint itself; this pins the same
    query where the router's unrelated dependencies are not installed.
    """

    def _ids(self, user, is_active=None):
        from modules.flow_gate.db import projects as db_projects
        from modules.flow_gate.rbac.access import visible_project_clause

        rows = db_projects.list_projects(is_active=is_active, visible_clause=visible_project_clause(user))
        return {row["project_id"] for row in rows} - {"__SYSTEM__"}

    def test_members_see_their_projects_and_admins_see_all(self):
        assert self._ids(MEMBER) == {"pa"}
        assert self._ids(MEMBER, is_active=1) == {"pa"}
        assert self._ids(OUTSIDER) == set()
        assert self._ids(SYSTEM_ONLY) == set()
        assert {"pa", "pb"} <= self._ids(ADMIN)
