"""0457 T0009 — recover error.code envelope and /relations candidate_slots.

NR0003 §9 recommendations 5/6/7: T0007 (TR0008) closed the data-repair half of the
orphan-slot incident; the 409 response shape, the /relations candidate-slot listing,
and the client's slot-aware recover button were explicitly left for this T. This suite
covers the server half — regressions **C**, **D**, **E**, **F** of NR0003 §8 — against a
real SQLite store built from the actual migrations, the same harness shape as
`test_reject_resubmit_own_slot_0457.py` (regressions A/B/G/H) and
`test_workflow_duplicate_slot_repair_0457.py` (regression I).

* **C** — an orphaned document with a compatible empty slot recovers into it (200);
  `/relations` reports that slot as `empty: false` afterward.
* **D** — the first empty slot's type does not match, but a later slot does:
  `/relations` still lists the later slot as `empty: true`; recovering with that
  slot's `item_seq` succeeds, while the default (first-empty-slot) path answers
  `slot_type_mismatch`.
* **E** — a document already linked to a slot is not orphaned; recovering it answers
  `not_orphaned` and leaves the slot untouched.
* **F** — a sequence with no empty slot left answers `no_available_slot`, and
  `/relations` candidate_slots has no `empty: true` entry.

Every scenario asserts the response's `error.code` against the T0009 §1 table and that
the slot row, its workflow_item_results ledger, and the target document's own row are
all unchanged on failure (TR0010 rev2: D/E/F previously only compared the slot and/or
ledger, not the document).
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path

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


def _patched_store_class(backend):
    from modules.flow_gate.db import connection as conn_mod

    class _PatchedStore(conn_mod.FlowGateStore):
        def __init__(self):
            self._db = backend
            self._sq = None

        def _sql(self, key: str) -> str:
            if key in _QUERIES:
                return _QUERIES[key]
            raise KeyError(f"Query not found: {key}")

    return _PatchedStore


PROJECT_ID = "cs0457"
MODULE = "__ALL__"
USER_ID = "usr_cs0457"


@pytest.fixture(scope="module")
def cs_db():
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
def cs_store(cs_db):
    mock_db, _ = cs_db
    from modules.flow_gate.db import connection as conn_mod

    original_store = conn_mod.STORE
    conn_mod.STORE = _patched_store_class(mock_db)()
    yield
    conn_mod.STORE = original_store


@pytest.fixture(scope="module", autouse=True)
def cs_seed(cs_db, cs_store):
    from modules.flow_gate.db import projects, users
    from modules.flow_gate.db.connection import get_store, now_iso

    now = now_iso()
    projects.create({"project_id": PROJECT_ID, "project_name": "0457 candidate slots"})
    users.create({
        "user_id": USER_ID,
        "username": "csworker",
        "email": "cs@test.com",
        "password": "hashed",
    })
    store = get_store()
    store._execute(
        "INSERT OR IGNORE INTO roles (role_id, role_name, created_at, updated_at) VALUES (?,?,?,?)",
        ["role_worker", "Worker", now, now],
    )
    for perm in ("document.create", "document.read", "document.update", "document.approve"):
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
    for code, name in (("B", "Bug"), ("R", "Requirement"), ("T", "Task"), ("TR", "Task Report")):
        store._execute(
            "INSERT OR IGNORE INTO document_types "
            "(project_id, type_code, type_name, series, is_system, is_active, sort_order, created_at, updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            [None, code, name, "work", 1, 1, 0, now, now],
        )
    yield


# ── seeding helpers (same shape as test_reject_resubmit_own_slot_0457.py) ────────────

def _make_group(code: str, title: str) -> str:
    from modules.flow_gate.db import groups as db_groups

    gid = f"{PROJECT_ID}-{MODULE}-{code}"
    db_groups.create({
        "group_id": gid, "project_id": PROJECT_ID, "module": MODULE, "title": title,
    })
    return gid


def _make_doc(gid: str, code: str, type_code: str, seq: int, review_status: str) -> str:
    from modules.flow_gate.db import documents as db_docs

    doc_id = f"{gid}-{code}"
    db_docs.create({
        "doc_id": doc_id, "project_id": PROJECT_ID, "type_code": type_code, "seq": seq,
        "title": code, "group_id": gid, "module": MODULE, "owner_id": USER_ID,
        "file_path": f"documents/{doc_id}.md", "revision_no": 0,
    })
    db_docs.update(doc_id, {"doc_review_status": review_status})
    return doc_id


def _make_sequence(root_doc_id: str, slots: list[tuple[int, str]]) -> list[dict]:
    from modules.flow_gate.db import workflow_sequences as db_wfseq

    db_wfseq.insert_sequence(root_doc_id)
    seq = db_wfseq.get_sequence_by_doc_id(root_doc_id)
    for order, (item_seq, type_code) in enumerate(slots):
        db_wfseq.insert_sequence_item(seq["id"], item_seq, type_code, type_code, "doc", order)
    return db_wfseq.get_sequence_items(seq["id"])


def _register(item_id: int, doc_id: str):
    from modules.flow_gate.workflow.pipeline_service import register_workflow_result

    return register_workflow_result(
        item_id=item_id,
        registered_path=f"documents/{doc_id}.md",
        registered_doc_id=doc_id,
        registered_at="2026-08-24T00:00:00Z",
        actor_user_id=USER_ID,
    )


def _item(item_id: int) -> dict:
    from modules.flow_gate.db import workflow_sequences as db_wfseq

    return db_wfseq.get_item_by_id(item_id)


def _doc(doc_id: str) -> dict:
    from modules.flow_gate.db import documents as db_docs

    return db_docs.get_by_id(doc_id)


def _ledger(item_id: int) -> list[dict]:
    from modules.flow_gate.db.connection import get_store

    return get_store()._fetch_all(
        "SELECT * FROM workflow_item_results WHERE item_id = ? ORDER BY id ASC", [item_id]
    )


def _relations(doc_id: str, group_id: str) -> dict:
    from modules.flow_gate.api.v1 import document_routes

    return document_routes._relations_workflow(doc_id, group_id)


def _admin() -> dict:
    return {"user_id": USER_ID, "is_admin": True}


def _recover(doc_id: str, item_seq: int | None):
    from modules.flow_gate.workflow.routers import workflow as wf_router

    return wf_router.recover_orphaned_workflow_document_endpoint(
        doc_id, wf_router.OrphanRecoveryRequest(item_seq=item_seq), _admin(),
    )


# ══════════════════════════════════════════════════════════════════════════════
# Regression C — compatible empty slot recovers; candidate_slots reflects it
# ══════════════════════════════════════════════════════════════════════════════

def test_C_orphan_with_matching_empty_slot_recovers_and_relations_updates():
    gid = _make_group("c001", "0457 C — matching empty slot")
    root = _make_doc(gid, "B0001", "B", 1, "wf_in_progress")
    orphan_doc = _make_doc(gid, "TR0002", "TR", 2, "pending_review")
    items = _make_sequence(root, [(1, "TR")])
    slot_id = items[0]["id"]

    before = _relations(orphan_doc, gid)
    assert before["orphan"] is True
    assert before["candidate_slots"] == [{"item_seq": 1, "type": "TR", "empty": True}]

    result = _recover(orphan_doc, 1)
    assert result["recovered"] is True
    assert result["item_seq"] == 1

    assert _item(slot_id)["result_doc_id"] == orphan_doc
    after = _relations(orphan_doc, gid)
    assert after["orphan"] is False
    assert after["candidate_slots"] == [{"item_seq": 1, "type": "TR", "empty": False}]


# ══════════════════════════════════════════════════════════════════════════════
# Regression D — first empty slot type differs, a later slot matches
# ══════════════════════════════════════════════════════════════════════════════

def test_D_later_matching_slot_is_targetable_but_default_head_mismatches():
    from modules.flow_gate.services.mutation_policy import MutationPolicyError

    gid = _make_group("d001", "0457 D — later matching slot")
    root = _make_doc(gid, "B0001", "B", 1, "wf_in_progress")
    orphan_doc = _make_doc(gid, "TR0003", "TR", 3, "pending_review")
    items = _make_sequence(root, [(1, "T"), (2, "TR")])
    t_slot_id, tr_slot_id = items[0]["id"], items[1]["id"]

    relations = _relations(orphan_doc, gid)
    assert relations["orphan"] is True
    assert relations["candidate_slots"] == [
        {"item_seq": 1, "type": "T", "empty": True},
        {"item_seq": 2, "type": "TR", "empty": True},
    ]

    t_ledger_before, tr_ledger_before = _ledger(t_slot_id), _ledger(tr_slot_id)
    doc_before = _doc(orphan_doc)

    # Omitting item_seq targets the earliest empty slot (item_seq=1, type T) — mismatch.
    with pytest.raises(MutationPolicyError) as excinfo:
        _recover(orphan_doc, None)
    assert excinfo.value.status_code == 409
    assert excinfo.value.error["code"] == "slot_type_mismatch"
    assert _item(t_slot_id)["result_doc_id"] is None
    assert _item(tr_slot_id)["result_doc_id"] is None
    # The failed default-path attempt must not touch either slot's ledger or the
    # orphan document's own status — a 409 on the mismatched candidate is not a
    # partial write.
    assert _ledger(t_slot_id) == t_ledger_before
    assert _ledger(tr_slot_id) == tr_ledger_before
    assert _doc(orphan_doc) == doc_before

    # Naming the later, type-matching slot explicitly succeeds.
    result = _recover(orphan_doc, 2)
    assert result["recovered"] is True
    assert result["item_seq"] == 2
    assert _item(tr_slot_id)["result_doc_id"] == orphan_doc
    assert _item(t_slot_id)["result_doc_id"] is None

    after = _relations(orphan_doc, gid)
    assert after["candidate_slots"] == [
        {"item_seq": 1, "type": "T", "empty": True},
        {"item_seq": 2, "type": "TR", "empty": False},
    ]


# ══════════════════════════════════════════════════════════════════════════════
# Regression E — an already-attached document is not orphaned
# ══════════════════════════════════════════════════════════════════════════════

def test_E_already_attached_document_is_not_orphaned():
    from modules.flow_gate.services.mutation_policy import MutationPolicyError

    gid = _make_group("e001", "0457 E — already attached")
    root = _make_doc(gid, "B0001", "B", 1, "wf_in_progress")
    attached_doc = _make_doc(gid, "TR0002", "TR", 2, "pending_review")
    items = _make_sequence(root, [(1, "TR")])
    slot_id = items[0]["id"]
    _register(slot_id, attached_doc)
    before, ledger_before = _item(slot_id), _ledger(slot_id)
    doc_before = _doc(attached_doc)

    with pytest.raises(MutationPolicyError) as excinfo:
        _recover(attached_doc, 1)
    assert excinfo.value.status_code == 409
    assert excinfo.value.error["code"] == "not_orphaned"

    assert _item(slot_id) == before
    assert _ledger(slot_id) == ledger_before
    # The already-linked document's own status must be untouched by the refused call too,
    # not just the slot/ledger it is already attached to.
    assert _doc(attached_doc) == doc_before

    relations = _relations(attached_doc, gid)
    assert relations["orphan"] is False


# ══════════════════════════════════════════════════════════════════════════════
# Regression F — no empty slot left
# ══════════════════════════════════════════════════════════════════════════════

def test_F_sequence_fully_consumed_has_no_available_slot():
    from modules.flow_gate.services.mutation_policy import MutationPolicyError

    gid = _make_group("f001", "0457 F — fully consumed")
    root = _make_doc(gid, "B0001", "B", 1, "wf_in_progress")
    filler_doc = _make_doc(gid, "TR0002", "TR", 2, "pending_review")
    orphan_doc = _make_doc(gid, "TR0003", "TR", 3, "pending_review")
    items = _make_sequence(root, [(1, "TR")])
    slot_id = items[0]["id"]
    _register(slot_id, filler_doc)
    before, ledger_before = _item(slot_id), _ledger(slot_id)
    doc_before = _doc(orphan_doc)

    relations = _relations(orphan_doc, gid)
    assert relations["orphan"] is True
    assert relations["candidate_slots"] == [{"item_seq": 1, "type": "TR", "empty": False}]
    assert not any(slot["empty"] for slot in relations["candidate_slots"])

    with pytest.raises(MutationPolicyError) as excinfo:
        _recover(orphan_doc, None)
    assert excinfo.value.status_code == 409
    assert excinfo.value.error["code"] == "no_available_slot"

    assert _item(slot_id) == before
    assert _ledger(slot_id) == ledger_before
    assert _item(slot_id)["result_doc_id"] == filler_doc
    # The refused orphan document's own status is untouched too — it stays orphaned,
    # not silently marked as something else.
    assert _doc(orphan_doc) == doc_before
