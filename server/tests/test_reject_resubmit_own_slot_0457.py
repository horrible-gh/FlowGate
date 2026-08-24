"""0457 B0001 — a rejected resubmit goes back to its OWN slot, and no registration
may evict the document already sitting in a slot.

NR0003 §1: `inbox_routes.py` Step 7.5 asked `get_in_progress_head_by_group()` which slot
to re-register a rejected document into. That selector is the only one ordered by
`updated_at DESC` instead of `sort_order ASC`, so it answered "the slot touched most
recently" — and when a work plan pours two sets, a sequence holds two TR slots, so the
`head.type == doc_type` guard passed on a *stranger's* slot. `0005-TR`'s resubmit took
slot 6 away from `0007-TR`, which became a document belonging to no slot at all. The
failure left no event, and the registration sat inside `except Exception: logger.warning`,
so nothing surfaced anywhere.

The four regressions NR0003 §8 asks for:

* **A** — two TR slots, the earlier document is rejected and resubmitted. Its own slot is
  re-registered; the later slot is untouched, down to its `updated_at`; nothing is
  orphaned. The precondition pins the in-progress head onto the *later* slot, which is
  what the pre-fix code would have written to.
* **B** — the B0046 misaligned-head shape. With an own slot, only that slot is
  re-registered; with no own slot, nothing is registered at all and the document still
  goes `rejected → revised` with its `DOC_REVIEW_STATUS_CHANGED` broadcast.
* **G** — registering into a slot another document holds raises
  `WorkflowSlotConflictError`, leaves slot, linkage and ledger untouched, and records a
  `workflow_slot_conflict` event. The `item_seq` recovery route answers 409. Re-registering
  the *same* document stays allowed.
* **H** — two documents racing for one empty slot. Run both as a deterministic
  interleaving *and* as real threads on separate connections; exactly one wins either way,
  and the loser leaves neither a slot value nor a ledger row. A control test runs the
  pre-fix read-then-write shape through the same interleaving to show the race window is
  real and not an artefact of the harness.

Harness: the self-contained SQLite store from `test_inbox_timemachine_reject_0046.py`
(no sqloader; SQL comes from the real `queries.json`), plus a connection-per-call backend
for the threaded case, because the live SQLite backend also opens a connection per call
and a single shared connection cannot race.
"""
from __future__ import annotations

import json
import os
import shutil
import sqlite3
import sys
import tempfile
import threading
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


class _ConnPerCallDB:
    """A backend that opens a fresh connection for every statement.

    Regression H needs two threads to contend for the same row *in the database*. The
    shared-connection `_MockDB` above cannot express that — one connection serialises
    everything through a single transaction context, so a lost update is structurally
    impossible there for reasons that have nothing to do with the code under test. The
    live SQLite backend opens a connection per call (see the note in
    `db/connection.py:transaction`), so this mirrors production and lets the two writers
    actually collide.
    """

    def __init__(self, db_path: str):
        self._path = db_path

    def _connect(self):
        conn = sqlite3.connect(self._path, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout = 30000")
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def execute(self, sql: str, params=None):
        conn = self._connect()
        try:
            conn.execute(sql, params or [])
            conn.commit()
        finally:
            conn.close()

    def fetch_one(self, sql: str, params=None) -> dict | None:
        conn = self._connect()
        try:
            row = conn.execute(sql, params or []).fetchone()
            return dict(row) if row else None
        finally:
            conn.close()

    def fetch_all(self, sql: str, params=None) -> list[dict]:
        conn = self._connect()
        try:
            return [dict(r) for r in conn.execute(sql, params or []).fetchall()]
        finally:
            conn.close()

    @contextmanager
    def begin_transaction(self):
        conn = self._connect()
        conn.execute("BEGIN IMMEDIATE")
        try:
            yield _MockTxn(conn)
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()


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


PROJECT_ID = "sl0457"
MODULE = "__ALL__"
USER_ID = "usr_sl0457"


@pytest.fixture(scope="module")
def slot_db():
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
def slot_store(slot_db):
    mock_db, _ = slot_db
    from modules.flow_gate.db import connection as conn_mod

    original_store = conn_mod.STORE
    conn_mod.STORE = _patched_store_class(mock_db)()
    yield
    conn_mod.STORE = original_store


@pytest.fixture(scope="module", autouse=True)
def slot_seed(slot_db, slot_store):
    from modules.flow_gate.db import projects, users
    from modules.flow_gate.db.connection import get_store, now_iso

    now = now_iso()
    projects.create({"project_id": PROJECT_ID, "project_name": "0457 slot ownership"})
    users.create({
        "user_id": USER_ID,
        "username": "slotworker",
        "email": "slot@test.com",
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
    for code, name in (
        ("B", "Bug"), ("R", "Requirement"), ("T", "Task"), ("TR", "Task Report"),
        ("N", "Notice"), ("NR", "Notice Result"), ("TS", "Test Scenario"),
    ):
        store._execute(
            "INSERT OR IGNORE INTO document_types "
            "(project_id, type_code, type_name, series, is_system, is_active, sort_order, created_at, updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            [None, code, name, "work", 1, 1, 0, now, now],
        )
    yield


# ── seeding helpers ───────────────────────────────────────────────────────────

def _group_id(code: str) -> str:
    return f"{PROJECT_ID}-{MODULE}-{code}"


def _make_group(code: str, title: str) -> str:
    from modules.flow_gate.db import groups as db_groups

    gid = _group_id(code)
    db_groups.create({
        "group_id": gid, "project_id": PROJECT_ID, "module": MODULE, "title": title,
    })
    return gid


def _make_doc(gid: str, code: str, type_code: str, seq: int, review_status: str,
              tmp_path: Path | None = None) -> str:
    from modules.flow_gate.db import documents as db_docs

    doc_id = f"{gid}-{code}"
    file_path = None
    if tmp_path is not None:
        stored = tmp_path / gid / f"{code}_document.md"
        stored.parent.mkdir(parents=True, exist_ok=True)
        stored.write_text(f"# {code}\n\nOriginal body.\n", encoding="utf-8")
        file_path = str(stored)
    db_docs.create({
        "doc_id": doc_id, "project_id": PROJECT_ID, "type_code": type_code, "seq": seq,
        "title": code, "group_id": gid, "module": MODULE, "owner_id": USER_ID,
        "file_path": file_path, "revision_no": 0,
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


def _stamp(item_id: int, updated_at: str) -> None:
    """Pin a slot's updated_at so the in-progress head resolves deterministically.

    `get_in_progress_head_by_group` orders by `updated_at DESC` at millisecond
    resolution, and two seeding writes can land in the same millisecond.
    """
    from modules.flow_gate.db.connection import get_store

    get_store()._execute(
        "UPDATE workflow_sequence_items SET updated_at = ? WHERE id = ?", [updated_at, item_id]
    )


def _item(item_id: int) -> dict:
    from modules.flow_gate.db import workflow_sequences as db_wfseq

    return db_wfseq.get_item_by_id(item_id)


def _ledger(item_id: int) -> list[dict]:
    from modules.flow_gate.db.connection import get_store

    return get_store()._fetch_all(
        "SELECT * FROM workflow_item_results WHERE item_id = ? ORDER BY id ASC", [item_id]
    )


def _slot_conflict_events(gid: str) -> list[dict]:
    from modules.flow_gate.db.connection import get_store

    return get_store()._fetch_all(
        "SELECT * FROM workflow_events WHERE group_id = ? AND event_type = ? ORDER BY id ASC",
        [gid, "workflow_slot_conflict"],
    )


def _issue_edit_token(tmp_path: Path, gid: str, doc_id: str) -> str:
    from modules.flow_gate.services import token_service

    with patch.object(token_service, "_scratch_dir", return_value=tmp_path / "scratch"):
        result = token_service.issue(
            project=PROJECT_ID, group_id=gid, action_scope="edit",
            doc_ref=doc_id, issued_to=USER_ID,
        )
    return result["raw_token"]


def _post_resubmit(raw: str, doc_id: str, group_code: str, body: str = "# Reworked\n"):
    from fastapi import FastAPI
    from starlette.testclient import TestClient
    from modules.flow_gate.api import inbox_routes

    app = FastAPI()
    app.include_router(inbox_routes.router)
    with patch(
        "modules.flow_gate.rbac.permission_service.has_permission", return_value=True
    ):
        return TestClient(app).post(
            "/api/v1/inbox",
            json={
                "project": PROJECT_ID, "module": MODULE, "group": group_code,
                "action": "edit", "doc_id": doc_id, "edit_reason": "rejected",
                "content": body,
            },
            headers={"Authorization": f"Bearer {raw}"},
        )


@contextmanager
def _capture_review_sse(monkeypatch):
    """Collect DOC_REVIEW_STATUS_CHANGED broadcasts fired by inbox Step 9."""
    import modules.flow_gate.api.v1.events.publisher as pub_mod
    from modules.flow_gate.api.v1.events.event_types import EventType

    captured: list = []

    def _capture(event):
        if event.event_type == EventType.DOC_REVIEW_STATUS_CHANGED:
            captured.append(event)

    monkeypatch.setattr(pub_mod, "broadcast_event_threadsafe", _capture)
    yield captured


# ══════════════════════════════════════════════════════════════════════════════
# Regression A — two TR slots; the rejected TR returns to its own slot
# ══════════════════════════════════════════════════════════════════════════════

def test_A_rejected_resubmit_returns_to_own_slot_and_spares_the_later_tr(tmp_path):
    """The exact 0454 shape: a two-set plan, so the sequence holds two TR slots.

    The in-progress head is pinned onto the *later* TR slot, which is what
    `get_in_progress_head_by_group` returned in the incident and what the pre-fix code
    therefore wrote to. The earlier TR is rejected and resubmitted.
    """
    from modules.flow_gate.db import documents as db_docs
    from modules.flow_gate.db import workflow_sequences as db_wfseq

    gid = _make_group("0501", "0457 A — two TR slots")
    root = _make_doc(gid, "B0001", "B", 1, "wf_in_progress")
    t1 = _make_doc(gid, "T0002", "T", 2, "approved")
    tr1 = _make_doc(gid, "TR0003", "TR", 3, "rejected", tmp_path)
    t2 = _make_doc(gid, "T0004", "T", 4, "approved")
    tr2 = _make_doc(gid, "TR0005", "TR", 5, "pending_review")

    items = _make_sequence(root, [(1, "T"), (2, "TR"), (3, "T"), (4, "TR")])
    slot_t1, slot_tr1, slot_t2, slot_tr2 = (i["id"] for i in items)
    for item_id, doc in ((slot_t1, t1), (slot_tr1, tr1), (slot_t2, t2), (slot_tr2, tr2)):
        _register(item_id, doc)
    for offset, item_id in enumerate((slot_t1, slot_tr1, slot_t2, slot_tr2), start=1):
        _stamp(item_id, f"2026-08-24T00:00:0{offset}.000Z")

    # ── Precondition: the state that made the old code pick the wrong slot ──
    # Both TR slots hold a non-approved document, so both are candidates; the later one
    # was touched most recently, so `updated_at DESC` puts it first. Its type matches the
    # rejected document's type, so the old `head.type == doc_type` guard passed on it.
    head = db_wfseq.get_in_progress_head_by_group(gid, PROJECT_ID)
    assert head is not None and head["id"] == slot_tr2, head
    assert head["type"] == "TR" == db_docs.get_by_id(tr1)["type_code"]
    assert head["result_doc_id"] == tr2
    tr2_before = _item(slot_tr2)
    tr2_ledger_before = _ledger(slot_tr2)

    raw = _issue_edit_token(tmp_path, gid, tr1)
    resp = _post_resubmit(raw, tr1, "0501")
    assert resp.status_code == 200, resp.text

    # The rejected TR went back into *its own* slot …
    own = _item(slot_tr1)
    assert own["result_doc_id"] == tr1
    assert len(_ledger(slot_tr1)) == 2, "the resubmit should add a registration row of its own"

    # … and the later TR slot is untouched, down to the timestamp.
    after = _item(slot_tr2)
    assert after["result_doc_id"] == tr2, (
        f"the resubmit evicted {tr2} from item_seq=4 (0457 B0001 regression)"
    )
    assert after["updated_at"] == tr2_before["updated_at"], "the later slot was written to"
    assert _ledger(slot_tr2) == tr2_ledger_before

    # The other two slots did not move either.
    assert _item(slot_t1)["result_doc_id"] == t1
    assert _item(slot_t2)["result_doc_id"] == t2

    # No document was pushed out of the sequence, and none holds two slots.
    for doc in (t1, tr1, t2, tr2):
        assert db_wfseq.get_item_by_result_doc_id(doc) is not None
        assert db_wfseq.is_orphaned_workflow_member(doc) is False, f"{doc} became orphaned"
    from modules.flow_gate.db.connection import get_store
    dupes = get_store()._fetch_all(
        "SELECT result_doc_id, COUNT(*) AS n FROM workflow_sequence_items "
        "WHERE result_doc_id IS NOT NULL GROUP BY result_doc_id HAVING COUNT(*) > 1"
    )
    assert dupes == [], f"a document occupies more than one slot: {dupes}"

    assert db_docs.get_by_id(tr1)["doc_review_status"] == "revised"


# ══════════════════════════════════════════════════════════════════════════════
# Regression B — B0046 misaligned head: own slot, or no slot at all
# ══════════════════════════════════════════════════════════════════════════════

def test_B_misaligned_head_registers_only_the_documents_own_slot(tmp_path, monkeypatch):
    """Own slot exists while the head points elsewhere → only the own slot is written."""
    from modules.flow_gate.db import documents as db_docs
    from modules.flow_gate.db import workflow_sequences as db_wfseq

    gid = _make_group("0502", "0457 B — misaligned head, own slot present")
    root = _make_doc(gid, "B0001", "B", 1, "wf_in_progress")
    n_doc = _make_doc(gid, "N0002", "N", 2, "rejected", tmp_path)
    nr_doc = _make_doc(gid, "NR0003", "NR", 3, "pending_review")

    items = _make_sequence(root, [(1, "N"), (2, "NR")])
    slot_n, slot_nr = (i["id"] for i in items)
    _register(slot_n, n_doc)
    _register(slot_nr, nr_doc)
    _stamp(slot_n, "2026-08-24T00:00:01.000Z")
    _stamp(slot_nr, "2026-08-24T00:00:02.000Z")

    head = db_wfseq.get_in_progress_head_by_group(gid, PROJECT_ID)
    assert head is not None and head["id"] == slot_nr, head
    nr_before = _item(slot_nr)

    raw = _issue_edit_token(tmp_path, gid, n_doc)
    with _capture_review_sse(monkeypatch) as sse:
        resp = _post_resubmit(raw, n_doc, "0502")
    assert resp.status_code == 200, resp.text

    assert _item(slot_n)["result_doc_id"] == n_doc
    assert len(_ledger(slot_n)) == 2
    assert _item(slot_nr)["result_doc_id"] == nr_doc
    assert _item(slot_nr)["updated_at"] == nr_before["updated_at"]
    assert len(_ledger(slot_nr)) == 1

    assert db_docs.get_by_id(n_doc)["doc_review_status"] == "revised"
    assert len(sse) == 1, "DOC_REVIEW_STATUS_CHANGED was not broadcast (B0046 regression)"
    assert sse[0].payload["next_status"] == "revised"


def test_B_no_own_slot_skips_registration_but_still_revises_and_broadcasts(tmp_path, monkeypatch):
    """No own slot → nothing is registered, and the transition + SSE still happen.

    The head here holds a *different* document of the same type, which is exactly the
    configuration in which the old code registered anyway and evicted it.
    """
    from modules.flow_gate.db import documents as db_docs
    from modules.flow_gate.db import workflow_sequences as db_wfseq

    gid = _make_group("0503", "0457 B — no own slot")
    root = _make_doc(gid, "B0001", "B", 1, "wf_in_progress")
    n_held = _make_doc(gid, "N0002", "N", 2, "pending_review")
    n_loose = _make_doc(gid, "N0003", "N", 3, "rejected", tmp_path)

    items = _make_sequence(root, [(1, "N"), (2, "N")])
    slot_first, slot_second = (i["id"] for i in items)
    _register(slot_first, n_held)
    _stamp(slot_first, "2026-08-24T00:00:01.000Z")

    head = db_wfseq.get_in_progress_head_by_group(gid, PROJECT_ID)
    assert head is not None and head["id"] == slot_first, head
    assert head["type"] == "N" == db_docs.get_by_id(n_loose)["type_code"], (
        "the head must be type-compatible, or this would not reproduce the old path"
    )
    assert db_wfseq.get_item_by_result_doc_id(n_loose) is None
    first_before, second_before = _item(slot_first), _item(slot_second)

    raw = _issue_edit_token(tmp_path, gid, n_loose)
    with _capture_review_sse(monkeypatch) as sse:
        resp = _post_resubmit(raw, n_loose, "0503")
    assert resp.status_code == 200, resp.text

    assert _item(slot_first) == first_before, "the occupied head slot was written to"
    assert _item(slot_second) == second_before, "an unrelated empty slot was written to"
    assert len(_ledger(slot_first)) == 1
    assert _ledger(slot_second) == []

    assert db_docs.get_by_id(n_loose)["doc_review_status"] == "revised"
    assert len(sse) == 1, "DOC_REVIEW_STATUS_CHANGED was not broadcast (B0046 regression)"


# ══════════════════════════════════════════════════════════════════════════════
# Regression G — an occupied slot is never overwritten
# ══════════════════════════════════════════════════════════════════════════════

_G_GROUPS = iter(range(1, 100))


@pytest.fixture
def occupied_slot(tmp_path):
    """A sequence whose second slot holds `held`, with `intruder` looking for a home.

    Function-scoped and freshly numbered: every G case gets its own group, so the order
    they run in cannot make one case's writes look like another's.
    """
    gid = _make_group(f"054{next(_G_GROUPS):02d}", "0457 G — occupied slot")
    root = _make_doc(gid, "B0001", "B", 1, "wf_in_progress")
    held = _make_doc(gid, "TR0002", "TR", 2, "pending_review", tmp_path)
    intruder = _make_doc(gid, "TR0003", "TR", 3, "pending_review", tmp_path)

    items = _make_sequence(root, [(1, "T"), (2, "TR"), (3, "TS")])
    slots = [i["id"] for i in items]
    _register(slots[1], held)
    return {
        "gid": gid, "held": held, "intruder": intruder,
        "slot": slots[1], "items": items,
    }


def test_G_direct_registration_into_an_occupied_slot_raises_and_changes_nothing(occupied_slot):
    from modules.flow_gate.db import workflow_sequences as db_wfseq
    from modules.flow_gate.workflow.pipeline_service import WorkflowSlotConflictError

    slot = occupied_slot["slot"]
    before, ledger_before = _item(slot), _ledger(slot)
    events_before = len(_slot_conflict_events(occupied_slot["gid"]))

    with pytest.raises(WorkflowSlotConflictError) as excinfo:
        _register(slot, occupied_slot["intruder"])

    exc = excinfo.value
    assert exc.item_id == slot
    assert exc.existing_doc_id == occupied_slot["held"]
    assert exc.requested_doc_id == occupied_slot["intruder"]
    assert exc.error["code"] == "workflow_slot_occupied"

    # The slot, its timestamp, the ledger and the linkage are all exactly as they were.
    assert _item(slot) == before
    assert _ledger(slot) == ledger_before
    assert db_wfseq.get_item_by_result_doc_id(occupied_slot["held"])["id"] == slot
    assert db_wfseq.get_item_by_result_doc_id(occupied_slot["intruder"]) is None

    events = _slot_conflict_events(occupied_slot["gid"])
    assert len(events) == events_before + 1, "the refused eviction left no event (NR0003 §7-4)"
    meta = json.loads(events[-1]["metadata"])
    assert meta["item_id"] == slot
    assert meta["existing_doc_id"] == occupied_slot["held"]
    assert meta["requested_doc_id"] == occupied_slot["intruder"]


def test_G_same_document_may_re_register_into_its_own_slot(occupied_slot):
    """The compatibility half: every rejected-resubmit revision does exactly this."""
    slot = occupied_slot["slot"]
    before = _item(slot)

    result = _register(slot, occupied_slot["held"])  # must not raise

    assert result is not None and result["doc_id"] == occupied_slot["held"]
    assert _item(slot)["result_doc_id"] == occupied_slot["held"]
    assert len(_ledger(slot)) == 2, "a re-registration should still be recorded"
    assert _item(slot)["id"] == before["id"]


def test_G_recover_with_item_seq_on_an_occupied_slot_answers_409(occupied_slot):
    """The `item_seq` recovery route refuses the same eviction, with data unchanged."""
    from fastapi import HTTPException
    from modules.flow_gate.workflow.routers import workflow as wf_router

    slot = occupied_slot["slot"]
    before, ledger_before = _item(slot), _ledger(slot)

    with patch.object(wf_router.db_wfseq, "is_orphaned_workflow_member", return_value=True), \
            patch.object(wf_router.storage_paths, "to_storage_relative", side_effect=lambda p, _: p):
        with pytest.raises(HTTPException) as excinfo:
            wf_router.recover_orphaned_workflow_document_endpoint(
                occupied_slot["intruder"],
                wf_router.OrphanRecoveryRequest(item_seq=2),
                {"user_id": USER_ID, "is_admin": True},
            )

    assert excinfo.value.status_code == 409
    assert "already filled by" in excinfo.value.detail
    assert _item(slot) == before
    assert _ledger(slot) == ledger_before


def test_G_recover_maps_a_late_conflict_to_409_instead_of_500(occupied_slot, monkeypatch):
    """The backstop: the pre-check reads the slot, the claim writes it, and the slot can
    change hands in between. Simulate that by feeding the route a stale (empty-looking)
    slot row — the refusal must still surface as 409, not as a server fault."""
    from fastapi import HTTPException
    from modules.flow_gate.workflow.routers import workflow as wf_router

    slot = occupied_slot["slot"]
    before, ledger_before = _item(slot), _ledger(slot)
    stale = [dict(i, result_doc_id=None) for i in occupied_slot["items"]]

    with patch.object(wf_router.db_wfseq, "is_orphaned_workflow_member", return_value=True), \
            patch.object(wf_router.db_wfseq, "get_sequence_items", return_value=stale), \
            patch.object(wf_router.storage_paths, "to_storage_relative", side_effect=lambda p, _: p):
        with pytest.raises(HTTPException) as excinfo:
            wf_router.recover_orphaned_workflow_document_endpoint(
                occupied_slot["intruder"],
                wf_router.OrphanRecoveryRequest(item_seq=2),
                {"user_id": USER_ID, "is_admin": True},
            )

    assert excinfo.value.status_code == 409, excinfo.value.detail
    assert occupied_slot["held"] in excinfo.value.detail
    assert _item(slot) == before
    assert _ledger(slot) == ledger_before


# ══════════════════════════════════════════════════════════════════════════════
# Regression H — two documents racing for one empty slot
# ══════════════════════════════════════════════════════════════════════════════

def _race_group(tmp_path, code: str, title: str) -> dict:
    gid = _make_group(code, title)
    root = _make_doc(gid, "B0001", "B", 1, "wf_in_progress")
    first = _make_doc(gid, "TR0002", "TR", 2, "pending_review", tmp_path)
    second = _make_doc(gid, "TR0003", "TR", 3, "pending_review", tmp_path)
    items = _make_sequence(root, [(1, "TR")])
    return {"gid": gid, "slot": items[0]["id"], "first": first, "second": second}


def test_H_interleaved_claims_let_exactly_one_document_win(tmp_path, monkeypatch):
    """Deterministic interleaving: the competitor's whole registration is injected into
    the window between "the slot looks empty" and "write it", which is where a
    read-then-write guard loses. Only one document may end up in the slot."""
    from modules.flow_gate.db import workflow_sequences as db_wfseq
    from modules.flow_gate.workflow.pipeline_service import WorkflowSlotConflictError

    race = _race_group(tmp_path, "0505", "0457 H — interleaved claims")
    slot, first, second = race["slot"], race["first"], race["second"]
    assert _item(slot)["result_doc_id"] is None

    real_claim = db_wfseq.claim_item_result_doc_id
    fired: list[str] = []

    def _racing_claim(item_id, result_doc_id):
        if result_doc_id == first and not fired:
            fired.append(result_doc_id)
            _register(item_id, second)  # the competitor completes, right here
        return real_claim(item_id, result_doc_id)

    monkeypatch.setattr(db_wfseq, "claim_item_result_doc_id", _racing_claim)

    with pytest.raises(WorkflowSlotConflictError) as excinfo:
        _register(slot, first)

    assert fired == [first], "the harness never injected the competitor"
    assert excinfo.value.existing_doc_id == second
    assert excinfo.value.requested_doc_id == first

    assert _item(slot)["result_doc_id"] == second
    ledger = _ledger(slot)
    assert len(ledger) == 1, f"the losing claim left a registration row: {ledger}"
    assert ledger[0]["registered_doc_id"] == second
    assert db_wfseq.get_item_by_result_doc_id(first) is None


def test_H_control_read_then_write_loses_the_same_race(tmp_path):
    """Control — the pre-0457 shape under the *same* interleaving.

    Without this, a green test above would not distinguish "the CAS closed the window"
    from "the harness never opened one". Here the guard is a Python read followed by an
    unconditional write, exactly what `set_item_result_doc_id` gave the old code: it sees
    an empty slot, the competitor takes it, and the write goes through anyway — the
    competitor is evicted and its ledger row is stranded. That is 0454's slot 6.
    """
    from modules.flow_gate.db import workflow_item_results as db_wir
    from modules.flow_gate.db import workflow_sequences as db_wfseq

    race = _race_group(tmp_path, "0506", "0457 H — control")
    slot, first, second = race["slot"], race["first"], race["second"]

    def _naive_register(item_id: int, doc_id: str, competitor: str | None) -> None:
        current = db_wfseq.get_item_by_id(item_id)
        occupant = (current or {}).get("result_doc_id")
        assert occupant in (None, doc_id), "read-then-write guard tripped"
        if competitor is not None:
            _naive_register(item_id, competitor, None)  # the same injected interleaving
        db_wir.insert_result(
            item_id=item_id, registered_path=f"documents/{doc_id}.md",
            registered_doc_id=doc_id, registered_at="2026-08-24T00:00:00Z",
        )
        db_wfseq.set_item_result_doc_id(item_id, doc_id)

    _naive_register(slot, first, competitor=second)

    assert _item(slot)["result_doc_id"] == first, (
        "the control did not reproduce the eviction — the interleaving is not a race window"
    )
    assert db_wfseq.get_item_by_result_doc_id(second) is None, "the competitor was evicted"
    assert [r["registered_doc_id"] for r in _ledger(slot)] == [second, first], (
        "the evicted document's registration row is the only trace it left — NR0003 §3"
    )


def test_H_two_threads_racing_for_one_slot_leave_exactly_one_winner(tmp_path, slot_db):
    """Real threads on real separate connections, arriving at the claim together."""
    from modules.flow_gate.db import connection as conn_mod
    from modules.flow_gate.db import workflow_sequences as db_wfseq
    from modules.flow_gate.workflow.pipeline_service import (
        WorkflowSlotConflictError,
        register_workflow_result,
    )

    race = _race_group(tmp_path, "0507", "0457 H — threads")
    slot, first, second = race["slot"], race["first"], race["second"]

    # Take the seeded database somewhere the threads can open it independently.
    _, module_db_path = slot_db
    race_path = str(tmp_path / "race.db")
    shutil.copy(module_db_path, race_path)
    prep = sqlite3.connect(race_path)
    prep.execute("PRAGMA journal_mode = WAL")
    prep.commit()
    prep.close()

    original_store = conn_mod.STORE
    conn_mod.STORE = _patched_store_class(_ConnPerCallDB(race_path))()
    try:
        assert _item(slot)["result_doc_id"] is None
        gate = threading.Barrier(2)
        outcome: dict[str, object] = {}
        lock = threading.Lock()

        def _attempt(doc_id: str) -> None:
            gate.wait()
            try:
                register_workflow_result(
                    item_id=slot, registered_path=f"documents/{doc_id}.md",
                    registered_doc_id=doc_id, registered_at="2026-08-24T00:00:00Z",
                    actor_user_id=USER_ID,
                )
                result: object = "won"
            except WorkflowSlotConflictError as exc:
                result = exc
            except Exception as exc:  # surfaced below rather than swallowed
                result = exc
            with lock:
                outcome[doc_id] = result

        threads = [threading.Thread(target=_attempt, args=(d,)) for d in (first, second)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=60)
        assert not any(t.is_alive() for t in threads), "a racing thread never finished"

        winners = [d for d, r in outcome.items() if r == "won"]
        conflicts = [d for d, r in outcome.items() if isinstance(r, WorkflowSlotConflictError)]
        others = [(d, r) for d, r in outcome.items()
                  if r != "won" and not isinstance(r, WorkflowSlotConflictError)]
        assert others == [], f"a racing registration failed for an unrelated reason: {others}"
        assert len(winners) == 1, f"both registrations succeeded: {outcome}"
        assert len(conflicts) == 1, f"nobody was refused: {outcome}"

        winner, loser = winners[0], conflicts[0]
        assert outcome[loser].existing_doc_id == winner
        assert _item(slot)["result_doc_id"] == winner
        ledger = _ledger(slot)
        assert [r["registered_doc_id"] for r in ledger] == [winner], (
            f"the loser left a registration row: {ledger}"
        )
        assert db_wfseq.get_item_by_result_doc_id(loser) is None
    finally:
        conn_mod.STORE = original_store
