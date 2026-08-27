"""B0046 — Inbox Step 7.5: a rejected→revised transition must NOT depend on the
in-progress workflow head matching the resubmitted doc's type.

Root cause (NR0046.0003, candidate 2): the old Step 7.5 nested
`transition_document_review(action="submit")` inside the head-match guard
(`head_item.type == doc_type_code`). A time-machine reopen restores a doc's
`doc_review_status` to 'rejected' but does NOT realign the sequence head, so the
in-progress head can resolve to a *trailing* slot (or None). When it did, the AI's
rejected-doc resubmit skipped the transition, the doc stayed 'rejected', and — because
the Step 9 SSE broadcast (DOC_REVIEW_STATUS_CHANGED) is gated on
`doc_review_status == 'revised'` — no event was emitted, so the reviewer's action bar
never flipped back from the [Revision complete] rework toolbar to [Approve]/[Reject].

These tests pin the relocated transition: it runs on every rejected resubmit,
independent of the head.

0457 T0005 update: the registration is no longer head-gated either. B0046 left it
behind on the head because "register_workflow_result still needs the head item id";
0457 B0001 showed that was the wrong slot to hand it — see
tests/test_reject_resubmit_own_slot_0457.py. The resubmit now re-registers into the
slot the document itself is already in (get_item_by_result_doc_id), or into none. Both
cases below still hold, and the second one is now true for a stronger reason: the N doc
lands in the N slot because it is *its own*, not because the head happened to match.

Mirrors the T820 self-contained SQLite harness (no sqloader).
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

import pytest

os.environ["TESTING"] = "1"
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing-only-32c")
os.environ["FLOWGATE_TOKEN_PEPPER_ACTIVE_ID"] = "test1"
os.environ["FLOWGATE_TOKEN_PEPPER_test1"] = "test-pepper-value-123"

_SERVER_DIR = Path(__file__).resolve().parents[1]
_SCHEMA_DIR = _SERVER_DIR / "sql" / "migrations" / "sqlite"
_QUERIES_JSON = _SERVER_DIR / "sql" / "queries" / "queries.json"

sys.path.insert(0, str(_SERVER_DIR))

_QUERIES: dict[str, str] = {}
if _QUERIES_JSON.exists():
    raw = json.loads(_QUERIES_JSON.read_text(encoding="utf-8"))
    for section, entries in raw.items():
        if isinstance(entries, dict):
            for key, sql in entries.items():
                if isinstance(sql, str):
                    _QUERIES[f"{section}.{key}"] = sql.replace("%s", "?")


class _MockDB:
    def __init__(self, db_path: str):
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")

    def execute(self, sql: str, params=None):
        self._conn.execute(sql, params or [])
        self._conn.commit()

    def fetch_one(self, sql: str, params=None) -> dict | None:
        row = self._conn.execute(sql, params or []).fetchone()
        return dict(row) if row else None

    def fetch_all(self, sql: str, params=None) -> list[dict]:
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

    def fetch_one(self) -> dict | None:
        if self._last_cursor is None:
            return None
        row = self._last_cursor.fetchone()
        return dict(row) if row else None

    def fetch_all(self) -> list[dict]:
        if self._last_cursor is None:
            return []
        return [dict(r) for r in self._last_cursor.fetchall()]


@pytest.fixture(scope="module")
def tm_db():
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
def tm_store(tm_db):
    mock_db, _ = tm_db
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


PROJECT_ID = "tm0046"
GROUP_ID = "tm0046-__ALL__-0046"
USER_ID = "usr_tm0046"


@pytest.fixture(scope="module", autouse=True)
def tm_seed(tm_db, tm_store):
    from modules.flow_gate.db import documents as db_docs
    from modules.flow_gate.db import groups as db_groups
    from modules.flow_gate.db import projects, users
    from modules.flow_gate.db.connection import get_store, now_iso

    now = now_iso()
    projects.create({"project_id": PROJECT_ID, "project_name": "B0046 TM"})
    users.create({
        "user_id": USER_ID,
        "username": "tmworker",
        "email": "tm@test.com",
        "password": "hashed",
    })
    store = get_store()
    store._execute(
        "INSERT OR IGNORE INTO roles (role_id, role_name, created_at, updated_at) VALUES (?,?,?,?)",
        ["role_worker", "Worker", now, now],
    )
    for perm in ("document.create", "document.read", "document.update", "document.reject"):
        store._execute(
            "INSERT OR IGNORE INTO permissions (permission_id, permission_name, created_at) VALUES (?,?,?)",
            [perm, perm, now],
        )
        store._execute(
            "INSERT OR IGNORE INTO role_permissions (role_id, permission_id) VALUES (?,?)",
            ["role_worker", perm],
        )
    store._execute(
        "INSERT OR IGNORE INTO user_project_roles (user_id, project_id, role_id, granted_at) VALUES (?,?,?,?)",
        [USER_ID, PROJECT_ID, "role_worker", now],
    )
    db_groups.create({
        "group_id": GROUP_ID,
        "project_id": PROJECT_ID,
        "module": "__ALL__",
        "title": "B0046 TM Group",
    })
    for code, name in (("R", "Requirement"), ("N", "Notice"), ("NR", "Notice Result"), ("T", "Task")):
        store._execute(
            "INSERT OR IGNORE INTO document_types "
            "(project_id, type_code, type_name, series, is_system, is_active, sort_order, created_at, updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            [None, code, name, "work", 1, 1, 0, now, now],
        )
    yield


def _make_edit_token(tmp_path, doc_id: str) -> str:
    from modules.flow_gate.services import token_service
    with patch.object(token_service, "_scratch_dir", return_value=tmp_path / "s_edit"):
        result = token_service.issue(
            project=PROJECT_ID,
            group_id=GROUP_ID,
            action_scope="edit",
            doc_ref=doc_id,
            issued_to=USER_ID,
        )
    return result["raw_token"]


def _create_rejected_doc(doc_id: str, type_code: str, seq: int, stored_path: Path):
    from modules.flow_gate.db import documents as db_docs
    stored_path.parent.mkdir(parents=True, exist_ok=True)
    stored_path.write_text("# Original (rejected)")
    db_docs.create({
        "doc_id": doc_id,
        "project_id": PROJECT_ID,
        "type_code": type_code,
        "seq": seq,
        "title": doc_id,
        "group_id": GROUP_ID,
        "module": "__ALL__",
        "owner_id": USER_ID,
        "file_path": str(stored_path),
        "revision_no": 0,
    })
    # create() does not persist doc_review_status — set the rejected state explicitly.
    db_docs.update(doc_id, {
        "doc_review_status": "rejected",
        "rejection_history": json.dumps([
            {"rejection_id": f"rej-{doc_id}", "reason": "needs rework"}
        ]),
    })


def _post_edit_reject(raw: str, doc_id: str, group_code: str):
    from fastapi import FastAPI
    from starlette.testclient import TestClient
    from modules.flow_gate.api import inbox_routes

    app = FastAPI()
    app.include_router(inbox_routes.router)
    with patch(
        "modules.flow_gate.rbac.permission_service.has_permission",
        return_value=True,
    ):
        return TestClient(app).post(
            "/api/v1/inbox",
            json={
                "project": PROJECT_ID,
                "module": "__ALL__",
                "group": group_code,
                "action": "edit",
                "doc_id": doc_id,
                "edit_reason": "rejected",
                "content": "# Reworked content",
                "rejection_response": "addressed review feedback",
            },
            headers={"Authorization": f"Bearer {raw}"},
        )


def test_timemachine_head_mismatch_still_transitions_to_revised(tmp_path):
    """★ Core: head resolves to a trailing (NR) slot, NOT the rejected N — the
    rejected N resubmit must still transition rejected→revised so the reviewer's
    action bar flips back to [Approve]/[Reject]. (Old head-gated code left it 'rejected'.)"""
    from modules.flow_gate.db import documents as db_docs
    from modules.flow_gate.db import workflow_sequences as db_wfseq

    r_doc = f"{GROUP_ID}-R0046"
    db_docs.create({
        "doc_id": r_doc, "project_id": PROJECT_ID, "type_code": "R", "seq": 1,
        "title": "Root", "group_id": GROUP_ID, "module": "__ALL__", "owner_id": USER_ID,
    })
    db_docs.update(r_doc, {"doc_review_status": "wf_in_progress"})

    n_doc = f"{GROUP_ID}-N0046"
    n_stored = tmp_path / "docs" / f"{n_doc}_document.md"
    _create_rejected_doc(n_doc, "N", 2, n_stored)

    # A trailing NR step whose result doc is pending (not approved); it is set LAST so
    # its wsi.updated_at is the most recent → the in-progress head resolves to NR, not N.
    nr_doc = f"{GROUP_ID}-NR0046"
    db_docs.create({
        "doc_id": nr_doc, "project_id": PROJECT_ID, "type_code": "NR", "seq": 3,
        "title": "Trailing report", "group_id": GROUP_ID, "module": "__ALL__", "owner_id": USER_ID,
    })
    db_docs.update(nr_doc, {"doc_review_status": "pending_review"})

    db_wfseq.insert_sequence(r_doc)
    seq = db_wfseq.get_sequence_by_doc_id(r_doc)
    db_wfseq.insert_sequence_item(seq["id"], 1, "N", "Notice", "R", 0)
    db_wfseq.insert_sequence_item(seq["id"], 2, "NR", "Notice Result", "R", 1)
    items = db_wfseq.get_sequence_items(seq["id"])
    n_item = next(i for i in items if i["type"] == "N")
    nr_item = next(i for i in items if i["type"] == "NR")
    db_wfseq.set_item_result_doc_id(n_item["id"], n_doc)
    db_wfseq.set_item_result_doc_id(nr_item["id"], nr_doc)  # set last → newest updated_at

    # Precondition: the in-progress head does NOT match the rejected N doc's type.
    head = db_wfseq.get_in_progress_head_by_group(GROUP_ID, PROJECT_ID)
    assert head is not None and head["type"] == "NR", head

    raw = _make_edit_token(tmp_path, n_doc)
    resp = _post_edit_reject(raw, n_doc, "0046")
    assert resp.status_code == 200, resp.text

    doc = db_docs.get_by_id(n_doc)
    assert doc["doc_review_status"] == "revised", (
        f"rejected resubmit did not transition to 'revised' (head was NR, not N): "
        f"doc_review_status={doc['doc_review_status']!r}"
    )


def test_normal_reject_rework_transitions_and_registers(tmp_path):
    """Regression: in the normal flow (one slot, holding the rejected doc), the resubmit
    still transitions rejected→revised AND registers the workflow result."""
    from modules.flow_gate.db import documents as db_docs
    from modules.flow_gate.db import workflow_sequences as db_wfseq

    r_doc = f"{GROUP_ID}-R0047"
    db_docs.create({
        "doc_id": r_doc, "project_id": PROJECT_ID, "type_code": "R", "seq": 1,
        "title": "Root2", "group_id": GROUP_ID, "module": "__ALL__", "owner_id": USER_ID,
    })
    db_docs.update(r_doc, {"doc_review_status": "wf_in_progress"})

    n_doc = f"{GROUP_ID}-N0047"
    n_stored = tmp_path / "docs2" / f"{n_doc}_document.md"
    _create_rejected_doc(n_doc, "N", 2, n_stored)

    db_wfseq.insert_sequence(r_doc)
    seq = db_wfseq.get_sequence_by_doc_id(r_doc)
    db_wfseq.insert_sequence_item(seq["id"], 1, "N", "Notice", "R", 0)
    items = db_wfseq.get_sequence_items(seq["id"])
    n_item = next(i for i in items if i["type"] == "N")
    db_wfseq.set_item_result_doc_id(n_item["id"], n_doc)

    head = db_wfseq.get_in_progress_head_by_group(GROUP_ID, PROJECT_ID)
    assert head is not None and head["type"] == "N", head

    raw = _make_edit_token(tmp_path, n_doc)
    resp = _post_edit_reject(raw, n_doc, "0046")
    assert resp.status_code == 200, resp.text

    doc = db_docs.get_by_id(n_doc)
    assert doc["doc_review_status"] == "revised", doc["doc_review_status"]
    # register_workflow_result ran (the doc's own slot was found) → the N slot still
    # points at the resubmit, now re-registered rather than merely left in place.
    refreshed = db_wfseq.get_item_by_result_doc_id(n_doc)
    assert refreshed is not None and refreshed["type"] == "N"


def test_reopen_deletes_ac_after_releasing_workflow_event_fk():
    """AC rejection reopens the workflow and deletes the file-less AC document.

    PostgreSQL enforces workflow_events.document_id -> documents.id, so the low-level
    delete path must release that FK first. SQLite catches the same shape here with
    foreign_keys=ON.
    """
    from modules.flow_gate.db import documents as db_docs
    from modules.flow_gate.db import groups as db_groups
    from modules.flow_gate.db.connection import get_store, now_iso
    from modules.flow_gate.documents.routers import documents as doc_routes

    store = get_store()
    group_id = f"{PROJECT_ID}-__ALL__-0048"
    db_groups.create({
        "group_id": group_id,
        "project_id": PROJECT_ID,
        "module": "__ALL__",
        "title": "B0046 AC reopen FK",
    })
    r_doc = f"{group_id}-R0048"
    n_doc = f"{group_id}-N0048"
    ac_doc = f"{group_id}-AC0048"

    db_docs.create({
        "doc_id": r_doc, "project_id": PROJECT_ID, "type_code": "R", "seq": 1,
        "title": "Root3", "group_id": group_id, "module": "__ALL__", "owner_id": USER_ID,
    })
    db_docs.update(r_doc, {"doc_review_status": "wf_done"})
    db_docs.create({
        "doc_id": n_doc, "project_id": PROJECT_ID, "type_code": "N", "seq": 2,
        "title": "Rollback target", "group_id": group_id, "module": "__ALL__", "owner_id": USER_ID,
    })
    db_docs.update(n_doc, {"doc_review_status": "approved"})
    db_docs.create({
        "doc_id": ac_doc, "project_id": PROJECT_ID, "type_code": "AC", "seq": 3,
        "title": "Final Approval", "group_id": group_id, "module": "__ALL__", "owner_id": USER_ID,
    })
    ac = db_docs.get_by_id(ac_doc)
    store._execute(
        "INSERT INTO workflow_events "
        "(event_type, project_id, group_id, document_id, actor_user_id, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        ["ac_reopen_fk_test", PROJECT_ID, group_id, ac["id"], USER_ID, now_iso()],
    )

    result = doc_routes.reopen_workflow(
        doc_routes._ReopenBody(doc_id=ac_doc, target_seq=2),
        {"user_id": USER_ID},
    )

    assert result["ok"] is True
    assert n_doc in result["reopened"]
    assert db_docs.get_by_id(ac_doc) is None
    assert db_docs.get_by_id(r_doc)["doc_review_status"] == "wf_in_progress"
    assert db_docs.get_by_id(n_doc)["doc_review_status"] == "pending_review"
    event = store._fetch_one(
        "SELECT document_id FROM workflow_events WHERE event_type = ?",
        ["ac_reopen_fk_test"],
    )
    assert event["document_id"] is None
