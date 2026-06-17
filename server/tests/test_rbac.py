"""T058: pytest tests for RBAC middleware/services.



Environment: TESTING=1 (unit tests that run without a DB + TestClient integration tests)



Test coverage:

  - Permission check positive/negative cases (TestPermission)

  - own/all branching (TestPolicies)

  - Cache TTL behavior (TestPermissionCache)

  - Router permission gate — FastAPI TestClient + JWT mock (TestRbacRouter)

"""

from __future__ import annotations



import os

import sqlite3

import sys

import time

from pathlib import Path

from typing import Optional

from unittest.mock import MagicMock, patch



import pytest



# Set TESTING=1 (prevent DB initialization)

os.environ["TESTING"] = "1"

os.environ.setdefault("SECRET_KEY", "test-secret-key-for-rbac-testing-32c")



_SERVER_DIR = Path(__file__).resolve().parents[1]

_SCHEMA_001 = _SERVER_DIR / "sql" / "migrations" / "sqlite" / "001_flowgate_schema.sql"

_SCHEMA_002 = _SERVER_DIR / "sql" / "migrations" / "sqlite" / "002_auth_columns.sql"



sys.path.insert(0, str(_SERVER_DIR))





# ─────────────────────────────────────────────────────────────────────────────

# In-memory SQLite + FlowGateStore fixture

# ─────────────────────────────────────────────────────────────────────────────



@pytest.fixture(scope="module")

def db_conn():

    """In-memory SQLite loaded with schema + seed data."""

    conn = sqlite3.connect(":memory:", check_same_thread=False)

    conn.row_factory = sqlite3.Row

    conn.execute("PRAGMA foreign_keys = ON")

    conn.executescript(_SCHEMA_001.read_text(encoding="utf-8"))

    try:

        conn.executescript(_SCHEMA_002.read_text(encoding="utf-8"))

    except sqlite3.OperationalError:

        pass



    # Insert the __SYSTEM__ project (D011 r1 §4-1 method A)

    try:

        conn.execute(

            "INSERT OR IGNORE INTO projects (project_id, project_name, is_active, created_at, updated_at) "

            "VALUES ('__SYSTEM__', '[System]', 1, datetime('now'), datetime('now'))"

        )

    except sqlite3.OperationalError:

        pass



    # Test project

    conn.execute(

        "INSERT OR IGNORE INTO projects (project_id, project_name, is_active, created_at, updated_at) "

        "VALUES ('proj_alpha', 'Alpha', 1, datetime('now'), datetime('now'))"

    )



    # Four users for testing

    for uid, uname, is_admin in [

        ("user_admin", "admin_user", 1),

        ("user_manager", "manager_user", 0),

        ("user_worker", "worker_user", 0),

        ("user_viewer", "viewer_user", 0),

    ]:

        conn.execute(

            "INSERT OR IGNORE INTO users (user_id, username, email, password, is_active, is_admin, created_at, updated_at) "

            "VALUES (?, ?, ?, 'hashed', 1, ?, datetime('now'), datetime('now'))",

            [uid, uname, f"{uname}@test.com", is_admin],

        )



    # Assign roles

    now = "2026-05-12T00:00:00+09:00"

    assignments = [

        ("user_admin",   "__SYSTEM__",  "role_admin"),

        ("user_manager", "proj_alpha",  "role_manager"),

        ("user_worker",  "proj_alpha",  "role_worker"),

        ("user_viewer",  "proj_alpha",  "role_viewer"),

    ]

    for uid, pid, rid in assignments:

        conn.execute(

            "INSERT OR IGNORE INTO user_project_roles (user_id, project_id, role_id, granted_at) "

            "VALUES (?, ?, ?, ?)",

            [uid, pid, rid, now],

        )



    conn.commit()

    return conn





def _make_mock_store(conn: sqlite3.Connection) -> MagicMock:

    """Mock that wraps FlowGateStore with an in-memory SQLite connection."""

    store = MagicMock()



    def _fetch_all(sql: str, params=None):

        cur = conn.execute(sql, params or [])

        rows = cur.fetchall()

        return [dict(r) for r in rows]



    def _fetch_one(sql: str, params=None):

        cur = conn.execute(sql, params or [])

        row = cur.fetchone()

        return dict(row) if row else None



    def _execute(sql: str, params=None):

        conn.execute(sql, params or [])

        conn.commit()



    store._fetch_all.side_effect = _fetch_all

    store._fetch_one.side_effect = _fetch_one

    store._execute.side_effect = _execute

    return store





# ─────────────────────────────────────────────────────────────────────────────

# 1. Permission check positive/negative cases

# ─────────────────────────────────────────────────────────────────────────────



class TestPermission:

    def test_manager_has_document_create(self, db_conn):

        from modules.flow_gate.rbac.permission_service import _fetch_permissions

        store = _make_mock_store(db_conn)

        with patch("modules.flow_gate.rbac.permission_service.get_store", return_value=store):

            perms = _fetch_permissions("user_manager", "proj_alpha")

        assert "perm_document_create" in perms



    def test_viewer_has_only_read_perms(self, db_conn):

        from modules.flow_gate.rbac.permission_service import _fetch_permissions

        store = _make_mock_store(db_conn)

        with patch("modules.flow_gate.rbac.permission_service.get_store", return_value=store):

            perms = _fetch_permissions("user_viewer", "proj_alpha")

        assert "perm_document_read" in perms

        assert "perm_document_create" not in perms

        assert "perm_document_delete" not in perms



    def test_worker_cannot_delete(self, db_conn):

        from modules.flow_gate.rbac.permission_service import _fetch_permissions

        store = _make_mock_store(db_conn)

        with patch("modules.flow_gate.rbac.permission_service.get_store", return_value=store):

            perms = _fetch_permissions("user_worker", "proj_alpha")

        assert "perm_document_delete" not in perms



    def test_admin_has_system_role_perms(self, db_conn):

        from modules.flow_gate.rbac.permission_service import _fetch_permissions

        store = _make_mock_store(db_conn)

        # admin has __SYSTEM__ role which maps to role_admin (all perms)

        with patch("modules.flow_gate.rbac.permission_service.get_store", return_value=store):

            perms = _fetch_permissions("user_admin", "proj_alpha")

        # __SYSTEM__ is included in IN clause so admin perms propagate

        assert "perm_document_delete" in perms

        assert "perm_role_assign" in perms



    def test_wrong_project_returns_empty(self, db_conn):

        from modules.flow_gate.rbac.permission_service import _fetch_permissions

        store = _make_mock_store(db_conn)

        with patch("modules.flow_gate.rbac.permission_service.get_store", return_value=store):

            perms = _fetch_permissions("user_worker", "proj_nonexistent")

        # worker has no role in proj_nonexistent; __SYSTEM__ also has none for worker

        assert "perm_document_create" not in perms



    def test_has_permission_true(self, db_conn):

        from modules.flow_gate.rbac import has_permission

        from modules.flow_gate.rbac.permission_service import clear_all_cache

        clear_all_cache()

        store = _make_mock_store(db_conn)

        with patch("modules.flow_gate.rbac.permission_service.get_store", return_value=store):

            result = has_permission("user_manager", "proj_alpha", "perm_document_approve")

        assert result is True



    def test_has_permission_false(self, db_conn):

        from modules.flow_gate.rbac import has_permission

        from modules.flow_gate.rbac.permission_service import clear_all_cache

        clear_all_cache()

        store = _make_mock_store(db_conn)

        with patch("modules.flow_gate.rbac.permission_service.get_store", return_value=store):

            result = has_permission("user_viewer", "proj_alpha", "perm_document_approve")

        assert result is False





# ─────────────────────────────────────────────────────────────────────────────

# 2. own/all branching (policies)

# ─────────────────────────────────────────────────────────────────────────────



class TestPolicies:

    def _perms_with_delete(self):

        return {"perm_document_delete", "perm_document_read"}



    def _perms_without_delete(self):

        return {"perm_document_read", "perm_document_update"}



    def test_can_delete_all_with_perm(self):

        """With perm_document_delete, documents owned by others can also be deleted."""

        from modules.flow_gate.rbac.policies import can_delete_document

        doc = {"owner_id": "other_user", "status": "approved"}

        with patch(

            "modules.flow_gate.rbac.policies.get_user_permissions",

            return_value=self._perms_with_delete(),

        ):

            result = can_delete_document("user_manager", "proj_alpha", doc)

        assert result.allowed is True

        assert result.reason == "all"



    def test_can_delete_own_draft(self):

        """Even without perm_document_delete, your own draft can be deleted."""

        from modules.flow_gate.rbac.policies import can_delete_document

        doc = {"owner_id": "user_worker", "status": "draft"}

        with patch(

            "modules.flow_gate.rbac.policies.get_user_permissions",

            return_value=self._perms_without_delete(),

        ):

            result = can_delete_document("user_worker", "proj_alpha", doc)

        assert result.allowed is True

        assert result.reason == "own.draft"



    def test_cannot_delete_own_non_draft(self):

        """Even if owned by the user, it cannot be deleted unless it is a draft."""

        from modules.flow_gate.rbac.policies import can_delete_document

        doc = {"owner_id": "user_worker", "status": "in_review"}

        with patch(

            "modules.flow_gate.rbac.policies.get_user_permissions",

            return_value=self._perms_without_delete(),

        ):

            result = can_delete_document("user_worker", "proj_alpha", doc)

        assert result.allowed is False



    def test_cannot_delete_other_draft(self):

        """A draft owned by someone else cannot be deleted without perm_document_delete."""

        from modules.flow_gate.rbac.policies import can_delete_document

        doc = {"owner_id": "other_user", "status": "draft"}

        with patch(

            "modules.flow_gate.rbac.policies.get_user_permissions",

            return_value=self._perms_without_delete(),

        ):

            result = can_delete_document("user_worker", "proj_alpha", doc)

        assert result.allowed is False



    def test_can_update_editable_status(self):

        """Allowed when perm_document_update is present and the status is editable."""

        from modules.flow_gate.rbac.policies import can_update_document

        doc = {"status": "draft"}

        with patch(

            "modules.flow_gate.rbac.policies.get_user_permissions",

            return_value={"perm_document_update"},

        ):

            result = can_update_document("user_worker", "proj_alpha", doc)

        assert result.allowed is True



    def test_can_update_approved_before_final_approval(self):

        """Intermediate approval does not lock the document."""

        from modules.flow_gate.rbac.policies import can_update_document

        doc = {"status": "approved", "doc_review_status": "approved"}

        with patch(

            "modules.flow_gate.rbac.policies.get_user_permissions",

            return_value={"perm_document_update"},

        ):

            result = can_update_document("user_worker", "proj_alpha", doc)

        assert result.allowed is True



    def test_cannot_update_after_final_approval(self):

        """Final workflow approval locks the document."""

        from modules.flow_gate.rbac.policies import can_update_document

        doc = {"status": "closed", "doc_review_status": "wf_done"}

        with patch(

            "modules.flow_gate.rbac.policies.get_user_permissions",

            return_value={"perm_document_update"},

        ):

            result = can_update_document("user_worker", "proj_alpha", doc)

        assert result.allowed is False



    def test_evaluate_simple_permission(self):

        """evaluate() — simple permission presence check."""

        from modules.flow_gate.rbac.policies import evaluate

        with patch(

            "modules.flow_gate.rbac.policies.get_user_permissions",

            return_value={"perm_document_read"},

        ):

            assert evaluate("uid", "pid", "perm_document_read").allowed is True

            assert evaluate("uid", "pid", "perm_document_create").allowed is False



    def test_evaluate_context_dependent(self):

        """evaluate() — context-dependent policy."""

        from modules.flow_gate.rbac.policies import evaluate

        doc = {"owner_id": "uid", "status": "draft"}

        with patch(

            "modules.flow_gate.rbac.policies.get_user_permissions",

            return_value=set(),

        ):

            result = evaluate("uid", "pid", "perm_document_delete", context=doc)

        assert result.allowed is True

        assert result.reason == "own.draft"





# ─────────────────────────────────────────────────────────────────────────────

# 3. Cache TTL behavior

# ─────────────────────────────────────────────────────────────────────────────



class TestPermissionCache:

    def test_cache_hit(self, db_conn):

        """After the first call, a repeated call with the same key does not re-query the DB."""

        from modules.flow_gate.rbac.permission_service import (

            get_user_permissions,

            clear_all_cache,

        )

        clear_all_cache()

        store = _make_mock_store(db_conn)

        with patch("modules.flow_gate.rbac.permission_service.get_store", return_value=store):

            p1 = get_user_permissions("user_manager", "proj_alpha")

            p2 = get_user_permissions("user_manager", "proj_alpha")



        # _fetch_all should be called only once (the second call is a cache hit)

        assert store._fetch_all.call_count == 1

        assert p1 == p2



    def test_cache_miss_after_invalidate(self, db_conn):

        """After invalidate_cache, a repeated call re-queries the DB."""

        from modules.flow_gate.rbac.permission_service import (

            get_user_permissions,

            invalidate_cache,

            clear_all_cache,

        )

        clear_all_cache()

        store = _make_mock_store(db_conn)

        with patch("modules.flow_gate.rbac.permission_service.get_store", return_value=store):

            get_user_permissions("user_worker", "proj_alpha")

            invalidate_cache("user_worker", "proj_alpha")

            get_user_permissions("user_worker", "proj_alpha")



        assert store._fetch_all.call_count == 2



    def test_cache_ttl_expiry(self, db_conn):

        """Verify cache expiration after the TTL elapses."""

        from modules.flow_gate import rbac as rbac_module

        from modules.flow_gate.rbac.permission_service import (

            get_user_permissions,

            clear_all_cache,

            _cache,

            _cache_lock,

        )

        clear_all_cache()

        store = _make_mock_store(db_conn)

        with patch("modules.flow_gate.rbac.permission_service.get_store", return_value=store):

            get_user_permissions("user_viewer", "proj_alpha")



        # Access the cache directly and move the timestamp to TTL+1 seconds in the past

        key = ("user_viewer", "proj_alpha")

        with _cache_lock:

            old_perms, _ = _cache[key]

            _cache[key] = (old_perms, time.monotonic() - rbac_module.CACHE_TTL - 1)



        with patch("modules.flow_gate.rbac.permission_service.get_store", return_value=store):

            get_user_permissions("user_viewer", "proj_alpha")



        # DB is queried again after TTL expiration

        assert store._fetch_all.call_count == 2



    def test_invalidate_all_for_user(self, db_conn):

        """When invalidating with project_id=None, clear all cached entries for that user."""

        from modules.flow_gate.rbac.permission_service import (

            get_user_permissions,

            invalidate_cache,

            clear_all_cache,

            _cache,

        )

        clear_all_cache()

        store = _make_mock_store(db_conn)

        with patch("modules.flow_gate.rbac.permission_service.get_store", return_value=store):

            get_user_permissions("user_manager", "proj_alpha")

            get_user_permissions("user_manager", "__SYSTEM__")



        # There should be 2 cache entries

        cached_keys = [k for k in _cache if k[0] == "user_manager"]

        assert len(cached_keys) == 2



        invalidate_cache("user_manager")  # project_id=None -> invalidate everything



        cached_keys_after = [k for k in _cache if k[0] == "user_manager"]

        assert len(cached_keys_after) == 0





# ─────────────────────────────────────────────────────────────────────────────

# 4. Router permission gate (FastAPI TestClient + JWT mock)

# ─────────────────────────────────────────────────────────────────────────────



def _make_jwt_token(user_id: str, is_admin: bool = False) -> str:

    """Create a JWT access token for tests."""

    from modules.flow_gate.auth.jwt_service import create_access_token

    roles = ["role_admin"] if is_admin else ["role_worker"]

    token, _ = create_access_token(user_id, f"{user_id}_username", roles)

    return token





def _make_test_app(db_conn):

    """FastAPI app for tests — includes the RBAC router."""

    from fastapi import FastAPI

    from modules.flow_gate.rbac.routers import router as rbac_router



    app = FastAPI()

    app.include_router(rbac_router, prefix="/rbac")

    return app





class TestRbacRouter:

    @pytest.fixture(autouse=True)

    def _setup(self, db_conn):

        """Set up the DB mock + JWT blacklist mock."""

        self._db_conn = db_conn

        self._store = _make_mock_store(db_conn)



    def _get_client(self):

        from fastapi.testclient import TestClient

        app = _make_test_app(self._db_conn)

        return TestClient(app)



    def _patch_all(self):

        """Context that patches the DB store + blacklist mock at the same time."""

        import contextlib

        patches = [

            patch("modules.flow_gate.rbac.permission_service.get_store", return_value=self._store),

            patch("modules.flow_gate.rbac.role_service.get_store", return_value=self._store),

            patch("modules.flow_gate.db.roles.get_store", return_value=self._store),

            patch("modules.flow_gate.db.permissions.get_store", return_value=self._store),

            patch("modules.flow_gate.db.users.get_store", return_value=self._store),

            patch("modules.flow_gate.auth.token_store.is_blacklisted", return_value=False),

            patch("modules.flow_gate.rbac.permission_service.clear_all_cache"),

        ]

        return contextlib.ExitStack(), patches



    def test_get_roles_requires_auth(self):

        """Returns 401 when /rbac/roles is accessed without authentication."""

        from modules.flow_gate.rbac.permission_service import clear_all_cache

        clear_all_cache()

        client = self._get_client()

        # no Authorization header → FastAPI OAuth2 scheme returns 401

        resp = client.get("/rbac/roles")

        assert resp.status_code == 401



    def test_get_roles_with_valid_token(self, db_conn):

        """Returns 200 when /rbac/roles is accessed with a valid token."""

        from modules.flow_gate.rbac.permission_service import clear_all_cache

        clear_all_cache()

        token = _make_jwt_token("user_manager")

        client = self._get_client()



        # middleware.py uses 'from .token_store import is_blacklisted'

        # It keeps a local reference, so the name inside the middleware module must be patched.

        with (

            patch("modules.flow_gate.rbac.permission_service.get_store", return_value=self._store),

            patch("modules.flow_gate.db.roles.get_store", return_value=self._store),

            patch("modules.flow_gate.db.users.get_store", return_value=self._store),

            patch("modules.flow_gate.auth.middleware.is_blacklisted", return_value=False),

        ):

            resp = client.get("/rbac/roles", headers={"Authorization": f"Bearer {token}"})

        assert resp.status_code == 200

        assert isinstance(resp.json(), list)



    def test_my_permissions_admin_gets_all(self, db_conn):

        """An admin user receives all permissions at /my-permissions."""

        from modules.flow_gate.rbac.permission_service import clear_all_cache

        clear_all_cache()

        token = _make_jwt_token("user_admin", is_admin=True)



        admin_row = {

            "user_id": "user_admin", "username": "admin_user",

            "email": "admin@test.com", "is_active": 1, "is_admin": 1,

            "first_login_required": 0,

        }



        store = _make_mock_store(db_conn)

        # Return admin_row for user lookup, while the permission list is read from the actual DB

        original_side = store._fetch_one.side_effect



        def _fetch_one_admin(sql, params=None):

            if "FROM users" in sql:

                return admin_row

            return original_side(sql, params) if original_side else None



        store._fetch_one.side_effect = _fetch_one_admin



        client = self._get_client()

        with (

            patch("modules.flow_gate.rbac.permission_service.get_store", return_value=store),

            patch("modules.flow_gate.db.permissions.get_store", return_value=store),

            patch("modules.flow_gate.db.users.get_store", return_value=store),

            patch("modules.flow_gate.auth.middleware.is_blacklisted", return_value=False),

        ):

            resp = client.get(

                "/rbac/projects/proj_alpha/my-permissions",

                headers={"Authorization": f"Bearer {token}"},

            )

        assert resp.status_code == 200

        data = resp.json()

        assert data["is_admin"] is True



    def test_assign_role_requires_permission(self, db_conn):

        """A viewer without perm_role_assign gets 403 when attempting role assignment."""

        from modules.flow_gate.rbac.permission_service import clear_all_cache

        clear_all_cache()

        token = _make_jwt_token("user_viewer")



        viewer_row = {

            "user_id": "user_viewer", "username": "viewer_user",

            "email": "viewer@test.com", "is_active": 1, "is_admin": 0,

            "first_login_required": 0,

        }



        store = _make_mock_store(db_conn)

        original_side = store._fetch_one.side_effect



        def _fetch_one_viewer(sql, params=None):

            if "FROM users" in sql:

                return viewer_row

            return original_side(sql, params) if original_side else None



        store._fetch_one.side_effect = _fetch_one_viewer



        client = self._get_client()

        with (

            patch("modules.flow_gate.rbac.permission_service.get_store", return_value=store),

            patch("modules.flow_gate.rbac.role_service.get_store", return_value=store),

            patch("modules.flow_gate.db.users.get_store", return_value=store),

            patch("modules.flow_gate.auth.middleware.is_blacklisted", return_value=False),

        ):

            resp = client.post(

                "/rbac/users/user_worker/roles",

                json={"project_id": "proj_alpha", "role_id": "role_admin"},

                headers={"Authorization": f"Bearer {token}"},

            )



        assert resp.status_code == 403





# ─────────────────────────────────────────────────────────────────────────────

# T242 regression test: permission ID consistency for the token issuance endpoint (perm_document_read)

# ─────────────────────────────────────────────────────────────────────────────



class TestTokenIssueRbac:

    """T242: verify that permission checks on the token/issue endpoint work correctly.

    

    Regression test:

    - Verify use of the correct permission ID (perm_document_read)

    - role_viewer + perm_document_read → 200 OK

    - User without permission -> 403

    """



    @pytest.fixture(autouse=True)

    def _setup(self, db_conn):

        """Set up the DB mock."""

        self._db_conn = db_conn

        self._store = _make_mock_store(db_conn)



    def _make_token_app(self):

        """Test app that includes token_routes."""

        from fastapi import FastAPI

        from modules.flow_gate.api.token_routes import router as token_router

        

        app = FastAPI()

        app.include_router(token_router)

        return app



    def test_token_issue_with_perm_document_read(self, db_conn):

        """user_viewer (has perm_document_read) -> /token/issue returns 200 OK."""

        from fastapi.testclient import TestClient

        from modules.flow_gate.rbac.permission_service import clear_all_cache

        

        clear_all_cache()

        token = _make_jwt_token("user_viewer")

        

        # Mock: project/group existence checks and token issuance result

        app = self._make_token_app()

        client = TestClient(app)

        

        viewer_row = {

            "user_id": "user_viewer", "username": "viewer_user",

            "email": "viewer@test.com", "is_active": 1, "is_admin": 0,

            "first_login_required": 0,

        }

        

        project_row = {

            "project_id": "proj_alpha", "project_name": "Alpha",

            "is_active": 1,

        }

        

        group_row = {

            "group_id": "proj_alpha-__ALL__-0001", "project_id": "proj_alpha",

            "title": "Test Group", "module": "__ALL__",

        }

        

        token_result = {

            "raw_token": "test_token_value",

            "token_id": "tok_test_001",

            "expires_at": "2026-05-17T15:00:00+09:00",

            "scratch_dir": "/tmp/scratch/tok_test_001",

        }

        

        store = _make_mock_store(db_conn)

        original_fetch_one = store._fetch_one.side_effect

        

        def _fetch_one_custom(sql, params=None):

            if "FROM users" in sql:

                return viewer_row

            if "FROM projects" in sql:

                return project_row

            if "FROM groups" in sql:

                return group_row

            return original_fetch_one(sql, params) if original_fetch_one else None

        

        store._fetch_one.side_effect = _fetch_one_custom

        

        # Mock the token service

        with (

            patch("modules.flow_gate.rbac.permission_service.get_store", return_value=store),

            patch("modules.flow_gate.db.projects.get_store", return_value=store),

            patch("modules.flow_gate.db.groups.get_store", return_value=store),

            patch("modules.flow_gate.db.users.get_store", return_value=store),

            patch("modules.flow_gate.auth.middleware.is_blacklisted", return_value=False),

            patch("modules.flow_gate.services.token_service.issue", return_value=token_result),

        ):

            resp = client.post(

                "/api/v1/token/issue",

                json={

                    "project": "proj_alpha",

                    "group": "0001",

                    "action_scope": "new",

                },

                headers={"Authorization": f"Bearer {token}"},

            )

        

        # role_viewer has perm_document_read, so 200 OK is expected

        assert resp.status_code == 200

        data = resp.json()

        assert data["ok"] is True

        assert data["token_id"] == "tok_test_001"



    def test_token_issue_permission_denied(self, db_conn):

        """A user without permission gets /token/issue 403 (verifies permission ID consistency)."""

        from fastapi.testclient import TestClient

        from modules.flow_gate.rbac.permission_service import clear_all_cache

        from modules.flow_gate.api.token_routes import issue_token, TokenIssueRequest

        from fastapi import HTTPException

        

        # Directly test the permission check

        clear_all_cache()

        

        # Mock has_permission to return False

        def mock_has_permission(user_id, project_id, permission_id):

            # T242 check: verify that permission_id is correct

            assert permission_id == "perm_document_read", f"Expected 'perm_document_read' but got '{permission_id}'"

            return False

        

        # Test: validate permission_id when has_permission is called

        with patch("modules.flow_gate.rbac.has_permission", side_effect=mock_has_permission):

            try:

                # Mock current_user

                current_user = {"user_id": "user_test"}

                # Mock request body

                body = TokenIssueRequest(

                    project="proj_alpha",

                    group="0001",

                    action_scope="new"

                )

                

                # Mock dependencies

                with (

                    patch("modules.flow_gate.db.projects.get_by_id", return_value={"project_id": "proj_alpha"}),

                    patch("modules.flow_gate.api.token_routes._resolve_group", return_value="group_id_123"),

                    patch("modules.flow_gate.rbac.permission_service.get_store"),

                    patch("modules.flow_gate.db.groups.get_store"),

                ):

                    # Call -> has_permission is called -> permission_id is validated.
                    # issue_token signature is (body, request, current_user=Depends(...)),
                    # so current_user must be passed by keyword; request is unused here.
                    issue_token(body, request=None, current_user=current_user)

            except HTTPException as e:

                # Permission denied 403 -> expected

                assert e.status_code == 403

                # Verify that the error message contains the correct permission ID

                assert "perm_document_read" in e.detail

                assert "document.read" not in e.detail


