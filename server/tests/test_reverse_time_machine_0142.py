from __future__ import annotations

import os
import sqlite3
import sys
from contextlib import contextmanager
from pathlib import Path

import pytest

os.environ["TESTING"] = "1"
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing-only-32c")

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

    def fetch_one(self, sql: str, params=None) -> dict | None:
        row = self._conn.execute(sql, params or []).fetchone()
        return dict(row) if row else None

    def fetch_all(self, sql: str, params=None) -> list[dict]:
        return [dict(r) for r in self._conn.execute(sql, params or []).fetchall()]

    @contextmanager
    def begin_transaction(self):
        self._conn.execute("BEGIN")
        txn = _MockTxn(self._conn)
        try:
            yield txn
            self._conn.commit()
        except Exception:
            self._conn.rollback()
            raise

    def close(self):
        self._conn.close()


class _MockTxn:
    def __init__(self, conn):
        self._conn = conn
        self._last_cursor = None

    def execute(self, sql: str, params=None):
        self._last_cursor = self._conn.execute(sql, params or [])

    def fetch_one(self) -> dict | None:
        if self._last_cursor is None:
            return None
        row = self._last_cursor.fetchone()
        return dict(row) if row else None

    def fetch_all(self) -> list[dict]:
        if self._last_cursor is None:
            return []
        return [dict(r) for r in self._last_cursor.fetchall()]


@pytest.fixture
def rt_store(tmp_path, monkeypatch):
    db_path = tmp_path / "rt.db"
    mock_db = _MockDB(str(db_path))
    for sql_file in sorted(_SCHEMA_DIR.glob("*.sql")):
        mock_db._conn.executescript(sql_file.read_text(encoding="utf-8"))
    mock_db._conn.commit()

    storage_root = tmp_path / "storage"
    storage_root.mkdir()
    monkeypatch.setenv("FLOWGATE_STORAGE_DIR", str(storage_root))

    from modules.flow_gate.db import connection as conn_mod

    original_store = conn_mod.STORE

    class _PatchedStore(conn_mod.FlowGateStore):
        def __init__(self):
            self._db = mock_db
            self._sq = None

    conn_mod.STORE = _PatchedStore()
    yield storage_root
    conn_mod.STORE = original_store
    mock_db.close()


PROJECT_ID = "rt0142"
GROUP_ID = "rt0142.default.0142"
USER_ID = "usr_rt0142"


def _seed_base_group(storage_root: Path, suffix: str = "") -> dict[str, str]:
    from modules.flow_gate.db import documents as db_docs
    from modules.flow_gate.db import groups as db_groups
    from modules.flow_gate.db import projects, users
    from modules.flow_gate.storage import paths as storage_paths

    project_id = PROJECT_ID + suffix
    group_id = GROUP_ID + suffix
    user_id = USER_ID + suffix
    projects.create({"project_id": project_id, "project_name": "Reverse TM"})
    users.create({
        "user_id": user_id,
        "username": "rtuser" + suffix,
        "email": f"rt{suffix or '0'}@test.com",
        "password": "hashed",
    })
    db_groups.create({
        "group_id": group_id,
        "project_id": project_id,
        "module": "default",
        "title": "Reverse TM Group",
    })

    created: dict[str, str] = {}
    for seq, type_code, title, review_status in (
        (1, "R", "Root", "wf_done"),
        (4, "D", "Design", "approved"),
        (5, "L", "Logic", "approved"),
        (6, "P", "Protocol", "approved"),
        (8, "T", "Task", "approved"),
        (9, "AC", "Final Approval", "approved"),
    ):
        doc_code = f"{seq:04d}-{type_code}"
        doc_id = f"{group_id}.{doc_code}"
        file_path = None
        if type_code != "AC":
            path = storage_paths.document_path(
                project_id=project_id,
                group_code=group_id,
                doc_code=doc_code,
                filename="document.md",
                module="default",
            )
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                f"---\ntitle: {title}\ndoc_review_status: {review_status}\nupdated_at: before\n---\n# {title}\n",
                encoding="utf-8",
            )
            file_path = storage_paths.to_storage_relative(path, project_id)
        db_docs.create({
            "doc_id": doc_id,
            "project_id": project_id,
            "type_code": type_code,
            "seq": seq,
            "title": title,
            "group_id": group_id,
            "module": "default",
            "owner_id": user_id,
            "file_path": file_path,
        })
        db_docs.update(doc_id, {"doc_review_status": review_status})
        created[type_code] = doc_id
    return {"project_id": project_id, "group_id": group_id, "user_id": user_id, **created}


def test_reopen_records_return_point_and_full_restore_clears_it(rt_store):
    from modules.flow_gate.db import documents as db_docs
    from modules.flow_gate.db import workflow_return_points as db_rp
    from modules.flow_gate.documents.routers import documents as doc_routes

    ids = _seed_base_group(rt_store)
    reopened = doc_routes.reopen_workflow(
        doc_routes._ReopenBody(doc_id=ids["T"], target_seq=4),
        {"user_id": ids["user_id"]},
    )

    assert reopened["ok"] is True
    assert reopened["return_point"]["exists"] is True
    assert reopened["return_point"]["front_seq"] == 8
    assert reopened["return_point"]["restorable_count"] == 4
    assert db_docs.get_by_id(ids["AC"]) is None
    assert db_docs.get_by_id(ids["R"])["doc_review_status"] == "wf_in_progress"
    assert db_docs.get_by_id(ids["D"])["doc_review_status"] == "pending_review"

    restored = doc_routes.restore_workflow(
        doc_routes._RestoreBody(doc_id=ids["R"], destination_seq=None),
        {"user_id": ids["user_id"]},
    )

    assert restored["ok"] is True
    assert restored["restored"] == [ids["D"], ids["L"], ids["P"], ids["T"]]
    assert restored["stopped_at"] is None
    assert restored["reached_front"] is True
    assert restored["return_point_cleared"] is True
    assert restored["root_status"] == "wf_done"
    assert db_rp.get_by_group(ids["group_id"]) is None


def test_restore_stops_at_first_changed_document_and_keeps_return_point(rt_store):
    from modules.flow_gate.db import documents as db_docs
    from modules.flow_gate.db import workflow_return_points as db_rp
    from modules.flow_gate.documents.routers import documents as doc_routes
    from modules.flow_gate.storage import paths as storage_paths

    ids = _seed_base_group(rt_store, "b")
    doc_routes.reopen_workflow(
        doc_routes._ReopenBody(doc_id=ids["T"], target_seq=4),
        {"user_id": ids["user_id"]},
    )

    p_doc = db_docs.get_by_id(ids["P"])
    p_path = storage_paths.resolve_storage_path(p_doc["file_path"], ids["project_id"])
    assert p_path is not None
    p_path.write_text("---\ntitle: Protocol\n---\n# Protocol changed\n", encoding="utf-8")

    restored = doc_routes.restore_workflow(
        doc_routes._RestoreBody(doc_id=ids["R"], destination_seq=None),
        {"user_id": ids["user_id"]},
    )

    assert restored["restored"] == [ids["D"], ids["L"]]
    assert restored["stopped_at"] == 6
    assert restored["stopped_doc_id"] == ids["P"]
    assert restored["reached_front"] is False
    assert restored["return_point_cleared"] is False
    assert db_docs.get_by_id(ids["R"])["doc_review_status"] == "wf_in_progress"
    assert db_docs.get_by_id(ids["P"])["doc_review_status"] == "pending_review"
    assert db_rp.get_by_group(ids["group_id"]) is not None


def _seed_group_with_phantom(storage_root: Path, suffix: str) -> dict:
    """Replica of live group flowgate.default.0094: a T/TR continuous-work chain wired into
    a workflow sequence, PLUS a phantom TR (an abandoned/superseded revision) that shares the
    group's seq space but never filled a sequence slot. The reverse time-machine must ignore
    the phantom entirely (0142 rework — it previously got swept into the rewind, inflating the
    restorable count and getting reset to pending_review spuriously)."""
    from modules.flow_gate.db import documents as db_docs
    from modules.flow_gate.db import groups as db_groups
    from modules.flow_gate.db import projects, users
    from modules.flow_gate.db import workflow_sequences as db_wfseq
    from modules.flow_gate.storage import paths as storage_paths

    project_id = "rtp" + suffix
    group_id = "rtp.default.0094" + suffix
    user_id = "usrp" + suffix
    projects.create({"project_id": project_id, "project_name": "RT phantom"})
    users.create({"user_id": user_id, "username": "up" + suffix,
                  "email": f"up{suffix}@t.com", "password": "h"})
    db_groups.create({"group_id": group_id, "project_id": project_id,
                      "module": "default", "title": "0094"})

    created: dict[int, str] = {}
    # (seq, type, wired_into_sequence?) — seq 3 TR is the phantom (NOT wired).
    rows = [(1, "R", False), (2, "T", True), (3, "TR", False),
            (5, "TR", True), (6, "T", True), (7, "TR", True),
            (8, "T", True), (9, "TR", True), (10, "T", True), (11, "TR", True)]
    for seq, tc, _ in rows:
        doc_code = f"{seq:04d}-{tc}"
        doc_id = f"{group_id}.{doc_code}"
        review = "wf_done" if tc == "R" else "approved"
        path = storage_paths.document_path(
            project_id=project_id, group_code=group_id, doc_code=doc_code,
            filename="document.md", module="default")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"---\ntitle: {tc}{seq}\n---\n# {tc}{seq}\n", encoding="utf-8")
        db_docs.create({"doc_id": doc_id, "project_id": project_id, "type_code": tc,
                        "seq": seq, "title": f"{tc}{seq}", "group_id": group_id,
                        "module": "default", "owner_id": user_id,
                        "file_path": storage_paths.to_storage_relative(path, project_id)})
        db_docs.update(doc_id, {"doc_review_status": review})
        created[seq] = doc_id

    root_id = created[1]
    db_wfseq.insert_sequence(root_id)
    seq_hdr = db_wfseq.get_sequence_by_doc_id(root_id)
    step_seqs = [2, 5, 6, 7, 8, 9, 10, 11]
    for sort_order, s in enumerate(step_seqs):
        tc = "T" if created[s].endswith("-T") else "TR"
        db_wfseq.insert_sequence_item(seq_hdr["id"], sort_order + 1, tc, f"{tc}{s}", "R", sort_order)
    for item in db_wfseq.get_sequence_items(seq_hdr["id"]):
        db_wfseq.set_item_result_doc_id(item["id"], created[step_seqs[item["sort_order"]]])
    return {"project_id": project_id, "group_id": group_id, "user_id": user_id, "docs": created}


def test_phantom_non_sequence_doc_is_excluded_from_rewind_and_restore(rt_store):
    """0142 rework — the group-0094 boundary bug: a phantom doc (0003-TR) that is not part of
    the workflow sequence must not be rewound, snapshotted, or counted. The restore count must
    match the 8 real steps, never 9."""
    from modules.flow_gate.db import documents as db_docs
    from modules.flow_gate.documents.routers import documents as doc_routes

    ids = _seed_group_with_phantom(rt_store, "x")
    docs = ids["docs"]
    phantom = docs[3]  # 0003-TR

    reopened = doc_routes.reopen_workflow(
        doc_routes._ReopenBody(doc_id=docs[2], target_seq=2),
        {"user_id": ids["user_id"]})

    # 8 real steps, not 9 — the phantom is excluded from the snapshot.
    assert reopened["return_point"]["restorable_count"] == 8
    assert phantom not in reopened["reopened"]
    # The phantom keeps its approval; it was never a rewindable step.
    assert db_docs.get_by_id(phantom)["doc_review_status"] == "approved"

    # A full restore walks exactly the 8 real steps in seq order (phantom absent).
    restored = doc_routes.restore_workflow(
        doc_routes._RestoreBody(doc_id=docs[1], destination_seq=None),
        {"user_id": ids["user_id"]})
    assert restored["reached_front"] is True
    assert restored["return_point_cleared"] is True
    assert phantom not in restored["restored"]
    assert restored["restored"] == [docs[s] for s in (2, 5, 6, 7, 8, 9, 10, 11)]
