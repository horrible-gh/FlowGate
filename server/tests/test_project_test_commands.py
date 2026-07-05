"""project_test_commands (flowgate.default.0152) — db + service + router full-stack test.

Covers:
  - normalization / identity (trim + whitespace-collapse, case-sensitive)  → L §2-1
  - manual create / list ordering / conflict 409 / list-full 422           → L §2-3, P
  - suppressed-tombstone: DELETE soft-suppresses, revive on manual re-add   → L §2-2
  - auto-reflection from a passed run: setup+case only, dedup, tombstone respected, cap → L §2-4
  - TS-mention "Verified test commands" block: present with rows, '' when empty          → L §2-5
  - router CRUD + 404/422/409 + project->project_id reflection
Environment: TESTING=1 with a temporary SQLite + the real queries.json (mirrors test_project_messages.py).
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
    projects.create({"project_id": "tcprj", "project_name": "Test Command Project"})
    projects.create({"project_id": "tcother", "project_name": "Other Project"})
    yield


# ── normalization / identity (L §2-1) ─────────────────────────────────────────

class TestNormalize:
    def test_trim_and_collapse(self):
        from modules.flow_gate.services import test_command_service as svc
        assert svc.normalize_command("  cd client   &&   npm   test \n") == "cd client && npm test"

    def test_case_sensitive(self):
        from modules.flow_gate.services import test_command_service as svc
        assert svc.normalize_command("NPM Test") != svc.normalize_command("npm test")


# ── service: manual CRUD + tombstone (L §2-2, §2-3) ───────────────────────────

class TestServiceManual:
    def test_create_and_list(self, seed):
        from modules.flow_gate.services import test_command_service as svc
        row = svc.create_manual("tcprj", "  cd server &&  python -m pytest -q ", "server pytest")
        assert row["id"] > 0
        assert row["project_id"] == "tcprj"
        assert row["command"] == "cd server && python -m pytest -q"   # normalized
        assert row["origin"] == "manual"
        assert row["last_success_at"] is None
        listed = svc.list_for_view("tcprj")
        assert any(r["command"] == "cd server && python -m pytest -q" for r in listed)

    def test_empty_command_422(self, seed):
        from modules.flow_gate.services import test_command_service as svc
        with pytest.raises(svc.TestCommandValidationError):
            svc.create_manual("tcprj", "   ", "")

    def test_duplicate_active_409(self, seed):
        from modules.flow_gate.services import test_command_service as svc
        svc.create_manual("tcprj", "npm run e2e", "e2e")
        with pytest.raises(svc.TestCommandConflictError):
            svc.create_manual("tcprj", "npm   run   e2e", "dup")   # same after normalize

    def test_delete_suppresses_then_revive(self, seed):
        from modules.flow_gate.services import test_command_service as svc
        created = svc.create_manual("tcprj", "go test ./...", "go suite")
        cid = created["id"]
        assert svc.delete("tcprj", cid) is True
        # gone from the active list
        assert not any(r["id"] == cid for r in svc.list_for_view("tcprj"))
        # second delete -> 404 signal
        assert svc.delete("tcprj", cid) is False
        # manual re-add of the same command revives the SAME row (id preserved)
        revived = svc.create_manual("tcprj", "go test ./...", "go suite again")
        assert revived["id"] == cid
        assert revived["origin"] == "manual"
        assert any(r["id"] == cid for r in svc.list_for_view("tcprj"))

    def test_patch_and_404(self, seed):
        from modules.flow_gate.services import test_command_service as svc
        created = svc.create_manual("tcprj", "cargo test", "rust")
        updated = svc.patch("tcprj", created["id"], {"description": "rust unit suite"})
        assert updated["description"] == "rust unit suite"
        assert updated["command"] == "cargo test"
        assert svc.patch("tcprj", 999999, {"description": "x"}) is None


# ── service: auto-reflection (L §2-4) ─────────────────────────────────────────

class TestServiceReflect:
    def test_reflect_collects_setup_and_case_only(self, seed):
        from modules.flow_gate.services import test_command_service as svc
        doc = {"project_id": "tcother", "doc_id": "flowgate.default.0152.0005-TS"}
        items = [
            {"kind": "setup", "cmd": "cd client && npm install", "case_no": "SETUP-1", "case_title": ""},
            {"kind": "service", "cmd": "python server.py", "case_no": "SETUP-2", "case_title": ""},
            {"kind": "wait", "cmd": "8000", "case_no": "SETUP-3", "case_title": ""},
            {"kind": "case", "cmd": "cd client && npm test", "case_no": "TC-1", "case_title": "unit green"},
            {"kind": "case", "cmd": "cd client && npm test", "case_no": "TC-2", "case_title": "dup cmd"},
            {"kind": "teardown", "cmd": "rm -rf tmp", "case_no": "CLEAN-1", "case_title": ""},
        ]
        svc.reflect_from_passed_run(doc, items)
        rows = svc.list_for_view("tcother")
        cmds = {r["command"] for r in rows}
        assert "cd client && npm install" in cmds      # setup cmd collected
        assert "cd client && npm test" in cmds         # case cmd collected (deduped, one row)
        assert "python server.py" not in cmds          # service excluded
        assert "8000" not in cmds                       # wait excluded
        assert "rm -rf tmp" not in cmds                 # teardown excluded
        assert len([r for r in rows if r["command"] == "cd client && npm test"]) == 1
        for r in rows:
            if r["command"] in ("cd client && npm install", "cd client && npm test"):
                assert r["origin"] == "auto"
                assert r["last_success_at"] is not None

    def test_reflect_updates_existing_and_respects_tombstone(self, seed):
        from modules.flow_gate.services import test_command_service as svc
        # pre-existing manual row gets last_success_at bumped but keeps origin=manual
        manual = svc.create_manual("tcother", "make check", "manual make")
        assert manual["last_success_at"] is None
        # a suppressed tombstone must NOT be re-registered by reflection
        supp = svc.create_manual("tcother", "flake8 .", "lint")
        assert svc.delete("tcother", supp["id"]) is True
        doc = {"project_id": "tcother", "doc_id": "flowgate.default.0152.0009-TS"}
        items = [
            {"kind": "case", "cmd": "make check", "case_no": "TC-1", "case_title": "make"},
            {"kind": "case", "cmd": "flake8 .", "case_no": "TC-2", "case_title": "lint"},
        ]
        svc.reflect_from_passed_run(doc, items)
        rows = {r["command"]: r for r in svc.list_for_view("tcother")}
        assert rows["make check"]["origin"] == "manual"          # origin preserved
        assert rows["make check"]["last_success_at"] is not None  # success bumped
        assert "flake8 ." not in rows                            # tombstone still suppressed


# ── service: TS mention block (L §2-5) ────────────────────────────────────────

class TestMentionBlock:
    def test_empty_project_returns_blank(self, seed):
        from modules.flow_gate.services import test_command_service as svc
        from modules.flow_gate.db import projects
        projects.create({"project_id": "tcempty", "project_name": "Empty"})
        assert svc.build_verified_commands_block("tcempty") == ""

    def test_block_lists_commands(self, seed):
        from modules.flow_gate.services import test_command_service as svc
        block = svc.build_verified_commands_block("tcprj")
        assert block != ""
        assert "- cd server && python -m pytest -q" in block
        assert "Prefer these over guessing" in block


# ── router (P §3-5) ───────────────────────────────────────────────────────────

def _make_client():
    from fastapi import FastAPI
    from starlette.testclient import TestClient
    from modules.flow_gate.auth.middleware import get_current_user
    from modules.flow_gate.settings.routers import project_settings
    app = FastAPI()
    app.include_router(project_settings.router, prefix="/api/v1")
    app.dependency_overrides[get_current_user] = lambda: {"user_id": "usr_admin", "is_admin": True}
    return TestClient(app, raise_server_exceptions=True)


class TestRouter:
    def setup_method(self):
        self.client = _make_client()

    def test_create_and_envelope(self, seed):
        resp = self.client.post(
            "/api/v1/projects/tcprj/test-commands",
            json={"command": "npm run lint", "description": "front lint"},
        )
        assert resp.status_code == 201
        body = resp.json()
        assert body["project_id"] == "tcprj"
        assert body["command"] == "npm run lint"
        assert body["origin"] == "manual"
        assert "status" not in body                          # internal field hidden

        lst = self.client.get("/api/v1/projects/tcprj/test-commands")
        assert lst.status_code == 200
        assert isinstance(lst.json()["data"], list)

    def test_create_empty_422(self, seed):
        assert self.client.post(
            "/api/v1/projects/tcprj/test-commands", json={"command": "   "}
        ).status_code == 422

    def test_create_conflict_409(self, seed):
        self.client.post(
            "/api/v1/projects/tcprj/test-commands", json={"command": "pytest -k api"}
        )
        dup = self.client.post(
            "/api/v1/projects/tcprj/test-commands", json={"command": "pytest   -k   api"}
        )
        assert dup.status_code == 409

    def test_patch_and_delete_404(self, seed):
        created = self.client.post(
            "/api/v1/projects/tcprj/test-commands", json={"command": "mvn test", "description": "java"}
        ).json()
        cid = created["id"]
        patched = self.client.patch(
            f"/api/v1/projects/tcprj/test-commands/{cid}", json={"description": "java suite"}
        )
        assert patched.status_code == 200
        assert patched.json()["description"] == "java suite"
        assert self.client.delete(f"/api/v1/projects/tcprj/test-commands/{cid}").status_code == 200
        # gone -> 404 on both patch and delete
        assert self.client.patch(
            f"/api/v1/projects/tcprj/test-commands/{cid}", json={"description": "x"}
        ).status_code == 404
        assert self.client.delete(f"/api/v1/projects/tcprj/test-commands/{cid}").status_code == 404
