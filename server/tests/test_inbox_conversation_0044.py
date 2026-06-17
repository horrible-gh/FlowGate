"""T0044.0009 — conversation (CH) inbox behaviour (HTTP integration).

Exercises the real inbox new/edit handlers (TestClient + all-migrations sqlite) for:
  - L0044.0008 §3.3 (L-AUTO): a standalone-opened CH is auto-approved (not NULL,
    not pending_review) even with no matching workflow head.
  - L0044.0008 §8 (I-SSE): a CH edit by a non-owner subject still delivers a
    DOCUMENT_EXPLORER_REFRESH to the owner's audience.
  - L0044.0008 §7: at the carry-over threshold the edit opens a successor CH doc.

Scaffolding mirrors test_inbox_step9_wiring.py.
"""
from __future__ import annotations

import os
import sqlite3
import sys
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient

os.environ["TESTING"] = "1"
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing-only-32c")
os.environ["FLOWGATE_TOKEN_PEPPER_ACTIVE_ID"] = "test1"
os.environ["FLOWGATE_TOKEN_PEPPER_test1"] = "test-pepper-value-123"

_SERVER_DIR = Path(__file__).resolve().parents[1]
_SCHEMA_DIR = _SERVER_DIR / "sql" / "migrations" / "sqlite"
sys.path.insert(0, str(_SERVER_DIR))


class _MockDB:
    def __init__(self, db_path: str):
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")

    def execute(self, sql: str, params=None):
        self._conn.execute(sql, params or [])
        self._conn.commit()

    def fetch_one(self, sql: str, params=None):
        row = self._conn.execute(sql, params or []).fetchone()
        return dict(row) if row else None

    def fetch_all(self, sql: str, params=None):
        return [dict(r) for r in self._conn.execute(sql, params or []).fetchall()]

    @contextmanager
    def begin_transaction(self):
        yield _MockTxn(self._conn)

    def close(self):
        self._conn.close()


class _MockTxn:
    def __init__(self, conn):
        self._conn = conn
        self._last_cursor = None

    def execute(self, sql: str, params=None):
        self._last_cursor = self._conn.execute(sql, params or [])
        self._conn.commit()

    def fetch_one(self):
        if self._last_cursor is None:
            return None
        row = self._last_cursor.fetchone()
        return dict(row) if row else None

    def fetch_all(self):
        if self._last_cursor is None:
            return []
        return [dict(r) for r in self._last_cursor.fetchall()]


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
            raise NotImplementedError

    conn_mod.STORE = _PatchedStore()
    yield
    conn_mod.STORE = original_store


@pytest.fixture(scope="module")
def seed_data(tmp_db):
    from modules.flow_gate.db import documents as db_docs
    from modules.flow_gate.db import groups as db_groups
    from modules.flow_gate.db import projects, users
    from modules.flow_gate.db.connection import get_store, now_iso

    projects.create({"project_id": "testprj", "project_name": "Test Project"})
    for uid, uname in [("usr_test_001", "owner"), ("usr_ai_bot", "aibot")]:
        users.create({
            "user_id": uid, "username": uname,
            "email": f"{uname}@example.com", "password": "hashed_pw",
        })

    store = get_store()
    now = now_iso()
    store._execute(
        "INSERT OR IGNORE INTO roles (role_id, role_name, created_at, updated_at) VALUES (?,?,?,?)",
        ["role_worker", "Worker", now, now],
    )
    for perm in ["document.create", "document.read", "document.update"]:
        store._execute(
            "INSERT OR IGNORE INTO permissions (permission_id, permission_name, created_at) VALUES (?,?,?)",
            [perm, perm, now],
        )
        store._execute(
            "INSERT OR IGNORE INTO role_permissions (role_id, permission_id) VALUES (?,?)",
            ["role_worker", perm],
        )
    for uid in ["usr_test_001", "usr_ai_bot"]:
        store._execute(
            "INSERT OR IGNORE INTO user_project_roles (user_id, project_id, role_id, granted_at) VALUES (?,?,?,?)",
            [uid, "testprj", "role_worker", now],
        )
    db_groups.create({
        "group_id": "testprj-__ALL__-0001",
        "project_id": "testprj",
        "module": "__ALL__",
        "title": "Test Group",
    })
    store._execute(
        "INSERT OR IGNORE INTO document_types (project_id, type_code, type_name, series, is_system, is_active, sort_order, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?)",
        [None, "R", "Requirement", "general", 1, 1, 1, now, now],
    )
    # CH type comes from migration 047, but ensure presence regardless of mock apply.
    store._execute(
        "INSERT OR IGNORE INTO document_types (project_id, type_code, type_name, series, is_system, is_active, sort_order, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?)",
        [None, "CH", "대화", "general", 1, 1, 25, now, now],
    )
    db_docs.create({
        "doc_id": "testprj-__ALL__-0001-R0001",
        "project_id": "testprj",
        "type_code": "R",
        "seq": 1,
        "title": "Root Requirement",
        "group_id": "testprj-__ALL__-0001",
        "module": "__ALL__",
        "owner_id": "usr_test_001",
    })
    yield


def _build_client():
    from modules.flow_gate.api import inbox_routes

    app = FastAPI()
    app.include_router(inbox_routes.router)
    return TestClient(app)


def _issue_token(tmp_path, action_scope, doc_ref, issued_to="usr_test_001"):
    from modules.flow_gate.services import token_service

    with patch.object(token_service, "_scratch_dir", return_value=tmp_path / "scratch"):
        issued = token_service.issue(
            project="testprj",
            group_id="testprj-__ALL__-0001",
            action_scope=action_scope,
            doc_ref=doc_ref,
            issued_to=issued_to,
        )
    return issued["raw_token"]


def _wait_for_asyncmock(mock, minimum):
    for _ in range(40):
        if mock.await_count >= minimum:
            return
        time.sleep(0.05)


def _make_ch_doc(doc_id, tmp_path, content, owner="usr_test_001", seq=2):
    from modules.flow_gate.db import documents as db_docs
    target = tmp_path / "docs" / f"{doc_id}.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    db_docs.create({
        "doc_id": doc_id, "project_id": "testprj", "type_code": "CH", "seq": seq,
        "title": doc_id, "group_id": "testprj-__ALL__-0001", "module": "__ALL__",
        "owner_id": owner, "file_path": str(target), "revision_no": 0,
    })
    db_docs.update(doc_id, {"doc_review_status": "approved"})
    return target


# ── §3.3 L-AUTO ────────────────────────────────────────────────────────────────
def test_ch_new_standalone_is_auto_approved(seed_data, tmp_path):
    from modules.flow_gate.db import documents as db_docs

    client = _build_client()
    raw = _issue_token(tmp_path, "new", "testprj-__ALL__-0001-R0001")

    with patch(
        "modules.flow_gate.api.inbox_routes.numbering_service.reserve_document",
        return_value="0002-CH",
    ), patch(
        "modules.flow_gate.api.inbox_routes.document_path",
        return_value=tmp_path / "docs" / "0002-CH_document.md",
    ):
        resp = client.post(
            "/api/v1/inbox",
            json={
                "project": "testprj", "module": "__ALL__",
                "group_name": "testprj-__ALL__-0001",
                "action": "new", "prev_doc_id": "testprj-__ALL__-0001-R0001",
                "doc_type": "CH", "content": "---\ntype: CH\n---\nhello",
            },
            headers={"Authorization": f"Bearer {raw}"},
        )

    assert resp.status_code == 201, resp.text
    new_doc_id = resp.json()["doc_id"]
    rec = db_docs.get_by_id(new_doc_id)
    # L-AUTO: auto-approved on creation, not left NULL and not gated to pending_review.
    assert rec["doc_review_status"] == "approved"


# ── §8 owner-targeted SSE on edit by a non-owner subject ───────────────────────
def test_ch_edit_by_non_owner_broadcasts_to_owner(seed_data, tmp_path):
    # Legacy (hyphenated) harness uses the {TYPE}{seq4} doc-id form, like NR0001.
    doc_id = "testprj-__ALL__-0001-CH0003"
    _make_ch_doc(doc_id, tmp_path, "---\ntype: CH\n---\nturn 0", owner="usr_test_001", seq=3)

    client = _build_client()
    # Edit token issued to the AI bot — a DIFFERENT subject than the owner.
    raw = _issue_token(tmp_path, "edit", doc_id, issued_to="usr_ai_bot")
    publish_event = AsyncMock()

    with patch("modules.flow_gate.api.v1.events.publisher.publish_event", publish_event):
        resp = client.post(
            "/api/v1/inbox",
            json={
                "project": "testprj", "module": "__ALL__",
                "group_name": "testprj-__ALL__-0001",
                "action": "edit", "doc_id": doc_id,
                "edit_reason": "user_comment",
                "content": "---\ntype: CH\n---\nturn 0\n\nturn 1",
            },
            headers={"Authorization": f"Bearer {raw}"},
        )

    assert resp.status_code == 200, resp.text
    _wait_for_asyncmock(publish_event, 4)
    # An explorer refresh must be addressed to the OWNER audience (not just the actor).
    owner_refreshes = [
        c.args[0] for c in publish_event.await_args_list
        if c.args[0].event_type == "document_explorer_refresh"
        and c.args[0].audience == "usr_test_001"
    ]
    assert owner_refreshes, "expected an owner-targeted document_explorer_refresh"
    # And none of the actor-audience publishes should be the only delivery path.
    assert any(c.args[0].audience == "usr_ai_bot" for c in publish_event.await_args_list)


# ── §7 carry-over at the content threshold ─────────────────────────────────────
def test_ch_edit_carries_over_at_threshold(seed_data, tmp_path, monkeypatch):
    from modules.flow_gate.db import documents as db_docs

    doc_id = "testprj-__ALL__-0001-CH0004"
    _make_ch_doc(doc_id, tmp_path, "---\ntype: CH\n---\nstart", owner="usr_test_001", seq=4)

    # Tiny cap so a normal edit crosses the 80% carry-over line (but stays < 100%).
    monkeypatch.setenv("FLOWGATE_INBOX_CONTENT_MAX", "200")
    big_body = "---\ntype: CH\n---\n" + ("x" * 170)  # ~187 bytes: ≥80% of 200, <200
    assert 160 <= len(big_body.encode("utf-8")) < 200

    client = _build_client()
    raw = _issue_token(tmp_path, "edit", doc_id, issued_to="usr_test_001")

    with patch(
        "modules.flow_gate.api.inbox_routes.numbering_service.reserve_document",
        return_value="0005-CH",
    ), patch(
        "modules.flow_gate.api.inbox_routes.document_path",
        return_value=tmp_path / "docs" / "0005-CH_document.md",
    ):
        resp = client.post(
            "/api/v1/inbox",
            json={
                "project": "testprj", "module": "__ALL__",
                "group_name": "testprj-__ALL__-0001",
                "action": "edit", "doc_id": doc_id,
                "edit_reason": "user_comment", "content": big_body,
            },
            headers={"Authorization": f"Bearer {raw}"},
        )

    assert resp.status_code == 200, resp.text
    carried = resp.json().get("carried_over_doc_id")
    assert carried, "expected a successor conversation doc id in the response"
    successor = db_docs.get_by_id(carried)
    assert successor is not None
    assert successor["type_code"] == "CH"
    assert successor["doc_review_status"] == "approved"  # L-AUTO on successor too
