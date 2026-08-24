"""0457 T0009 — recover 409 error.code, /relations candidate_slots, and the client's
recover-button/toast contract (NR0003 §9 recommendations 5/6/7, §7 invariants 5/6).

NR0003 §8 asks for regressions C/D/E/F, matching the style of A/B/G/H in
test_reject_resubmit_own_slot_0457.py:

* **C** — an orphaned document with a type-matching empty slot recovers (200), lands in
  that slot, and `/relations` reports the slot `empty: false` afterward.
* **D** — the first empty slot's type does not match, but a later one does. `/relations`
  reports the later slot `empty: true`; targeting it by `item_seq` recovers (200);
  omitting `item_seq` (or targeting the first, wrong-type slot) answers
  `slot_type_mismatch` 409.
* **E** — a document already attached to a slot (not orphaned) answers `not_orphaned`
  409 and leaves the data untouched.
* **F** — a sequence with no empty slot at all answers `no_available_slot` 409, data is
  untouched, and `/relations` candidate_slots has no `empty: true` entry.

Harness: the same self-contained SQLite store as test_reject_resubmit_own_slot_0457.py
(no sqloader; SQL comes from the real queries.json), trimmed to what these four cases
need — no threading, no inbox/token plumbing.
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
    projects.create({"project_id": PROJECT_ID, "project_name": "0457 T0009 candidate slots"})
    users.create({
        "user_id": USER_ID,
        "username": "csworker",
        "email": "cs@test.com",
        "password": "hashed",
    })
    store = get_store()
    for code, name in (
        ("B", "Bug"), ("T", "Task"), ("TR", "Task Report"), ("N", "Notice"),
    ):
        store._execute(
            "INSERT OR IGNORE INTO document_types "
            "(project_id, type_code, type_name, series, is_system, is_active, sort_order, created_at, updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            [None, code, name, "work", 1, 1, 0, now, now],
        )
    yield


_GROUP_COUNTER = iter(range(1, 100))


def _group_id() -> str:
    return f"{PROJECT_ID}-{MODULE}-{next(_GROUP_COUNTER):04d}"


def _make_group(title: str) -> str:
    from modules.flow_gate.db import groups as db_groups

    gid = _group_id()
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


def _ledger(item_id: int) -> list[dict]:
    from modules.flow_gate.db.connection import get_store

    return get_store()._fetch_all(
        "SELECT * FROM workflow_item_results WHERE item_id = ? ORDER BY id ASC", [item_id]
    )


def _relations(doc_id: str) -> dict:
    from modules.flow_gate.api.v1 import document_routes
    from modules.flow_gate.db import documents as db_docs

    return document_routes._relations_workflow(doc_id, db_docs.get_by_id(doc_id))


def _recover(doc_id: str, item_seq: int | None = None):
    from modules.flow_gate.workflow.routers import workflow as wf_router

    return wf_router.recover_orphaned_workflow_document_endpoint(
        doc_id,
        wf_router.OrphanRecoveryRequest(item_seq=item_seq),
        {"user_id": USER_ID, "is_admin": True},
    )


# ══════════════════════════════════════════════════════════════════════════════
# Regression C — a type-matching empty slot recovers, and /relations updates
# ══════════════════════════════════════════════════════════════════════════════

def test_C_orphan_with_matching_empty_slot_recovers_and_relations_updates():
    from modules.flow_gate.db import workflow_sequences as db_wfseq

    root = _make_group("0457 T0009 C")
    b_doc = _make_doc(root, "B0001", "B", 1, "wf_in_progress")
    t_doc = _make_doc(root, "T0002", "T", 2, "approved")
    orphan_tr = _make_doc(root, "TR0099", "TR", 99, "pending_review")

    items = _make_sequence(b_doc, [(1, "T"), (2, "TR")])
    slot_t, slot_tr = (i["id"] for i in items)
    _register(slot_t, t_doc)

    assert db_wfseq.is_orphaned_workflow_member(orphan_tr) is True
    before = _relations(orphan_tr)
    assert before["orphan"] is True
    # decided=True: the fallback resolves the sequence through the group's B root even
    # though this orphan is not registered as any slot's result yet (0457 T0009).
    assert before["decided"] is True
    assert before["item_seq"] is None
    slot2_before = next(s for s in before["candidate_slots"] if s["item_seq"] == 2)
    assert slot2_before == {"item_seq": 2, "type": "TR", "empty": True}

    result = _recover(orphan_tr, item_seq=2)

    assert result["recovered"] is True
    assert result["item_seq"] == 2
    assert _item(slot_tr)["result_doc_id"] == orphan_tr
    assert db_wfseq.is_orphaned_workflow_member(orphan_tr) is False

    after = _relations(orphan_tr)
    assert after["orphan"] is False
    assert after["decided"] is True
    assert after["item_seq"] == 2
    slot2_after = next(s for s in after["candidate_slots"] if s["item_seq"] == 2)
    assert slot2_after == {"item_seq": 2, "type": "TR", "empty": False}


# ══════════════════════════════════════════════════════════════════════════════
# Regression D — first empty slot type mismatches; a later one matches
# ══════════════════════════════════════════════════════════════════════════════

def test_D_relations_surfaces_the_later_matching_slot_and_recovery_targets_it():
    from modules.flow_gate.services.mutation_policy import MutationPolicyError

    root = _make_group("0457 T0009 D")
    b_doc = _make_doc(root, "B0001", "B", 1, "wf_in_progress")
    t1_doc = _make_doc(root, "T0002", "T", 2, "approved")
    t2_doc = _make_doc(root, "T0004", "T", 4, "approved")
    orphan_tr = _make_doc(root, "TR0099", "TR", 99, "pending_review")

    # item_seq=2 is an empty N slot (wrong type); item_seq=4 is the empty TR slot.
    items = _make_sequence(b_doc, [(1, "T"), (2, "N"), (3, "T"), (4, "TR")])
    slot_t1, slot_n, slot_t2, slot_tr = (i["id"] for i in items)
    _register(slot_t1, t1_doc)
    _register(slot_t2, t2_doc)

    relations = _relations(orphan_tr)
    assert relations["orphan"] is True
    slots_by_seq = {s["item_seq"]: s for s in relations["candidate_slots"]}
    assert slots_by_seq[2] == {"item_seq": 2, "type": "N", "empty": True}
    assert slots_by_seq[4] == {"item_seq": 4, "type": "TR", "empty": True}

    # Omitting item_seq targets the effective head, which is the first empty slot (2, N).
    with pytest.raises(MutationPolicyError) as exc_default:
        _recover(orphan_tr)
    assert exc_default.value.status_code == 409
    assert exc_default.value.error["code"] == "slot_type_mismatch"
    assert _item(slot_n)["result_doc_id"] is None
    assert _item(slot_tr)["result_doc_id"] is None

    # Explicitly targeting the first (wrong-type) slot answers the same refusal.
    with pytest.raises(MutationPolicyError) as exc_first:
        _recover(orphan_tr, item_seq=2)
    assert exc_first.value.error["code"] == "slot_type_mismatch"

    # Targeting the later, type-matching slot succeeds.
    result = _recover(orphan_tr, item_seq=4)
    assert result["recovered"] is True
    assert result["item_seq"] == 4
    assert _item(slot_tr)["result_doc_id"] == orphan_tr
    assert _item(slot_n)["result_doc_id"] is None


# ══════════════════════════════════════════════════════════════════════════════
# Regression E — a document already in a slot is not orphaned
# ══════════════════════════════════════════════════════════════════════════════

def test_E_already_attached_document_answers_not_orphaned_and_data_is_unchanged():
    from modules.flow_gate.services.mutation_policy import MutationPolicyError

    root = _make_group("0457 T0009 E")
    b_doc = _make_doc(root, "B0001", "B", 1, "wf_in_progress")
    tr_doc = _make_doc(root, "TR0002", "TR", 2, "pending_review")

    items = _make_sequence(b_doc, [(1, "TR")])
    slot = items[0]["id"]
    _register(slot, tr_doc)
    before, ledger_before = _item(slot), _ledger(slot)

    with pytest.raises(MutationPolicyError) as exc:
        _recover(tr_doc)

    assert exc.value.status_code == 409
    assert exc.value.error["code"] == "not_orphaned"
    assert _item(slot) == before
    assert _ledger(slot) == ledger_before


# ══════════════════════════════════════════════════════════════════════════════
# Regression F — no empty slot anywhere in the sequence
# ══════════════════════════════════════════════════════════════════════════════

def test_F_fully_occupied_sequence_answers_no_available_slot():
    from modules.flow_gate.db import workflow_sequences as db_wfseq
    from modules.flow_gate.services.mutation_policy import MutationPolicyError

    root = _make_group("0457 T0009 F")
    b_doc = _make_doc(root, "B0001", "B", 1, "wf_in_progress")
    t_doc = _make_doc(root, "T0002", "T", 2, "approved")
    tr_doc = _make_doc(root, "TR0003", "TR", 3, "approved")
    orphan_tr = _make_doc(root, "TR0099", "TR", 99, "pending_review")

    items = _make_sequence(b_doc, [(1, "T"), (2, "TR")])
    slot_t, slot_tr = (i["id"] for i in items)
    _register(slot_t, t_doc)
    _register(slot_tr, tr_doc)
    before_t, before_tr = _item(slot_t), _item(slot_tr)
    ledger_t_before, ledger_tr_before = _ledger(slot_t), _ledger(slot_tr)

    assert db_wfseq.is_orphaned_workflow_member(orphan_tr) is True
    relations = _relations(orphan_tr)
    assert relations["candidate_slots"], "the fallback lookup found no sequence at all"
    assert all(not s["empty"] for s in relations["candidate_slots"]), (
        "a fully-occupied sequence reported an empty slot"
    )

    with pytest.raises(MutationPolicyError) as exc:
        _recover(orphan_tr)

    assert exc.value.status_code == 409
    assert exc.value.error["code"] == "no_available_slot"
    assert _item(slot_t) == before_t
    assert _item(slot_tr) == before_tr
    assert _ledger(slot_t) == ledger_t_before
    assert _ledger(slot_tr) == ledger_tr_before
    assert db_wfseq.get_item_by_result_doc_id(orphan_tr) is None
