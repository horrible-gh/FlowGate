"""project_messages — db module (DB0008 §7) + router (P0006 §3-5) full-stack test.

Covers:
  - CRUD round-trip via db.messages (real queries.json `messages` namespace + transaction id recovery)
  - dialog union: requested type + [All]('*') returned together (P0006 §4.5)
  - empty project -> [] (L0007 fallback trigger)
  - router list/create/patch/delete + 404/422 + project->project_id reflection (created_at hidden)
Environment: TESTING=1 with temporary SQLite + real queries.json, mirroring test_q_doc_response_answers.py.
"""
from __future__ import annotations

import os
import sys
import sqlite3
import tempfile
from contextlib import contextmanager
from pathlib import Path

import pytest

os.environ["TESTING"] = "1"
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing-only-32c")

_SERVER_DIR = Path(__file__).resolve().parents[1]
_SCHEMA_DIR = _SERVER_DIR / "sql" / "migrations" / "sqlite"
_QUERIES_JSON = _SERVER_DIR / "sql" / "queries" / "queries.json"
sys.path.insert(0, str(_SERVER_DIR))

import json as _json

_QUERIES: dict = {}
if _QUERIES_JSON.exists():
    raw = _json.loads(_QUERIES_JSON.read_text(encoding="utf-8"))
    for section, entries in raw.items():
        if isinstance(entries, dict):
            for key, sql in entries.items():
                if isinstance(sql, str):
                    _QUERIES[f"{section}.{key}"] = sql.replace("%s", "?")


class _MockTxn:
    def __init__(self, conn):
        self._conn = conn
        self._cur = None

    def execute(self, sql, params=None):
        self._cur = self._conn.execute(sql, params or [])
        self._conn.commit()

    def fetchone(self):
        row = self._cur.fetchone() if self._cur else None
        return dict(row) if row else None

    def fetchall(self):
        return [dict(r) for r in self._cur.fetchall()] if self._cur else []


class _MockDB:
    def __init__(self, db_path: str):
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
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
        yield _MockTxn(self._conn)

    def close(self):
        self._conn.close()


@pytest.fixture(scope="module")
def tmp_db():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    mock_db = _MockDB(db_path)
    for sql_file in sorted(_SCHEMA_DIR.glob("*.sql")):
        try:
            mock_db._conn.executescript(sql_file.read_text(encoding="utf-8"))
        except Exception:
            pass
    mock_db._conn.commit()
    yield mock_db, db_path
    mock_db.close()
    os.unlink(db_path)


@pytest.fixture(scope="module", autouse=True)
def patch_store(tmp_db):
    mock_db, _ = tmp_db
    from modules.flow_gate.db import connection as conn_mod
    original_store = conn_mod.STORE

    class _PatchedStore(conn_mod.FlowGateStore):
        def __init__(self):
            self._db = mock_db
            self._sq = None

        def _sql(self, key: str) -> str:
            if key in _QUERIES:
                return _QUERIES[key]
            raise KeyError(f"Query not found: {key}")

    conn_mod.STORE = _PatchedStore()
    yield
    conn_mod.STORE = original_store


@pytest.fixture(scope="module")
def seed(tmp_db):
    from modules.flow_gate.db import projects
    projects.create({"project_id": "msgprj", "project_name": "Message Project"})
    projects.create({"project_id": "otherprj", "project_name": "Other Project"})
    yield


# ── db module ───────────────────────────────────────────────────────────────

class TestMessagesDb:
    def test_empty_project_returns_empty(self, seed):
        from modules.flow_gate.db import messages as db
        assert db.list_by_project("msgprj") == []
        # L0007 fallback signal: dialog query on an empty project -> []
        assert db.list_for_dialog("msgprj", "D") == []

    def test_create_returns_row_with_id(self, seed):
        from modules.flow_gate.db import messages as db
        row = db.create("msgprj", "*", "검토 부탁드립니다.")
        assert row["id"] > 0                       # last_insert_rowid recovered in the txn
        assert row["project"] == "msgprj"
        assert row["doc_type"] == "*"
        assert row["message"] == "검토 부탁드립니다."
        assert row["updated_at"]                    # now_iso() set explicitly
        # round-trips via get_by_id
        again = db.get_by_id(row["id"])
        assert again["message"] == "검토 부탁드립니다."

    def test_dialog_union_includes_wildcard(self, seed):
        from modules.flow_gate.db import messages as db
        db.create("msgprj", "D", "설계 검토 후 회신 바랍니다.")
        db.create("msgprj", "P", "프로토콜 확인 바랍니다.")
        rows = db.list_for_dialog("msgprj", "D")
        types = sorted(r["doc_type"] for r in rows)
        # D-specific + the wildcard '*'; P is excluded
        assert types == ["*", "D"]
        assert all(r["project"] == "msgprj" for r in rows)

    def test_list_by_project_is_scoped(self, seed):
        from modules.flow_gate.db import messages as db
        db.create("otherprj", "D", "다른 프로젝트 메세지")
        msg_rows = db.list_by_project("msgprj")
        assert all(r["project"] == "msgprj" for r in msg_rows)
        assert len(msg_rows) == 3                   # *, D, P from above

    def test_update_partial_and_404(self, seed):
        from modules.flow_gate.db import messages as db
        row = db.create("msgprj", "L", "원본 문구")
        # message-only patch keeps doc_type
        updated = db.update(row["id"], {"message": "수정된 문구"})
        assert updated["doc_type"] == "L"
        assert updated["message"] == "수정된 문구"
        # doc_type-only patch keeps message
        updated2 = db.update(row["id"], {"doc_type": "*"})
        assert updated2["doc_type"] == "*"
        assert updated2["message"] == "수정된 문구"
        # missing id -> None
        assert db.update(999999, {"message": "x"}) is None

    def test_delete_and_404(self, seed):
        from modules.flow_gate.db import messages as db
        row = db.create("msgprj", "DB", "삭제 대상")
        assert db.delete(row["id"]) is True
        assert db.get_by_id(row["id"]) is None
        assert db.delete(row["id"]) is False        # already gone -> 404 signal


# ── router (P0006 §3-5) ───────────────────────────────────────────────────────

def _make_client():
    from fastapi import FastAPI
    from starlette.testclient import TestClient
    from modules.flow_gate.auth.middleware import get_current_user
    from modules.flow_gate.settings.routers import project_settings
    app = FastAPI()
    app.include_router(project_settings.router, prefix="/api/v1")
    # admin bypasses _has_permission (rbac/decorators._has_permission)
    app.dependency_overrides[get_current_user] = lambda: {"user_id": "usr_admin", "is_admin": True}
    return TestClient(app, raise_server_exceptions=True)


class TestMessagesRouter:
    def setup_method(self):
        self.client = _make_client()

    def test_create_reflects_project_id_and_hides_created_at(self, seed):
        resp = self.client.post(
            "/api/v1/projects/msgprj/messages",
            json={"doc_type": "T", "message": "라우터 생성"},
        )
        assert resp.status_code == 201
        body = resp.json()
        assert body["project_id"] == "msgprj"       # project -> project_id reflection
        assert "project" not in body
        assert "created_at" not in body             # internal audit only
        assert set(body.keys()) == {"id", "project_id", "doc_type", "message", "updated_at"}

    def test_list_management_envelope(self, seed):
        resp = self.client.get("/api/v1/projects/msgprj/messages")
        assert resp.status_code == 200
        assert "data" in resp.json()
        assert isinstance(resp.json()["data"], list)

    def test_dialog_query_union(self, seed):
        resp = self.client.get("/api/v1/projects/msgprj/messages", params={"doc_type": "T"})
        assert resp.status_code == 200
        types = {m["doc_type"] for m in resp.json()["data"]}
        assert "T" in types and "*" in types        # union with [All]

    def test_create_validation_422(self, seed):
        assert self.client.post(
            "/api/v1/projects/msgprj/messages", json={"doc_type": "T", "message": "   "}
        ).status_code == 422
        assert self.client.post(
            "/api/v1/projects/msgprj/messages", json={"message": "no type"}
        ).status_code == 422

    def test_patch_and_delete_404(self, seed):
        created = self.client.post(
            "/api/v1/projects/msgprj/messages", json={"doc_type": "N", "message": "patch me"}
        ).json()
        mid = created["id"]
        patched = self.client.patch(
            f"/api/v1/projects/msgprj/messages/{mid}", json={"message": "patched"}
        )
        assert patched.status_code == 200
        assert patched.json()["message"] == "patched"
        assert self.client.delete(f"/api/v1/projects/msgprj/messages/{mid}").status_code == 200
        # gone -> 404 on patch and delete
        assert self.client.patch(
            f"/api/v1/projects/msgprj/messages/{mid}", json={"message": "x"}
        ).status_code == 404
        assert self.client.delete(f"/api/v1/projects/msgprj/messages/{mid}").status_code == 404
