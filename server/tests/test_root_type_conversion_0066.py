"""Root-type conversion R ↔ B (group 0066, NR0003 §5 → T0004).

R0066.0001 asked whether a workflow root's requirement(R)/bug(B) type can be
flipped before the workflow decision, and to make it so if absent. NR0066.0003
found no support path exists (type is part of the doc identity: doc_id, filename,
inbound references) and recommended a dedicated, atomic, pre-decision-only
converter. These tests exercise that converter against a REAL temp SQLite DB with
all migrations applied and foreign_keys ON, so the doc_id rename is validated for
referential integrity exactly as production would.
"""
from __future__ import annotations

import os
import sqlite3
import sys
import tempfile
from pathlib import Path

import pytest
from fastapi import HTTPException

os.environ["TESTING"] = "1"
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing-only-32c")

_SERVER_DIR = Path(__file__).resolve().parents[1]
_MIGRATIONS_DIR = _SERVER_DIR / "sql" / "migrations" / "sqlite"
sys.path.insert(0, str(_SERVER_DIR))


def _migrations() -> list[Path]:
    return sorted(_MIGRATIONS_DIR.glob("*.sql"))


@pytest.fixture()
def env(tmp_path):
    """Real FlowGateStore on a fresh migrated SQLite DB + a tmp storage root."""
    from modules.flow_gate.db import _SqliteDbAdapter
    from modules.flow_gate.db import connection as conn_mod

    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    conn = sqlite3.connect(db_path)
    for mig in _migrations():
        try:
            conn.executescript(mig.read_text(encoding="utf-8"))
        except sqlite3.OperationalError:
            pass
    conn.close()

    original = conn_mod.STORE
    store = conn_mod.FlowGateStore()
    store._db = _SqliteDbAdapter(db_path)
    conn_mod.STORE = store

    prev_storage = os.environ.get("FLOWGATE_STORAGE_DIR")
    os.environ["FLOWGATE_STORAGE_DIR"] = str(tmp_path)

    _seed_base()
    try:
        yield store, db_path, tmp_path
    finally:
        conn_mod.STORE = original
        if prev_storage is None:
            os.environ.pop("FLOWGATE_STORAGE_DIR", None)
        else:
            os.environ["FLOWGATE_STORAGE_DIR"] = prev_storage
        try:
            os.unlink(db_path)
        except OSError:
            pass


def _seed_base(project_id: str = "flowgate", group_id: str = "flowgate.default.0066") -> None:
    from modules.flow_gate.db import groups as db_groups
    from modules.flow_gate.db import projects as db_projects
    from modules.flow_gate.db import users as db_users

    db_projects.create({"project_id": project_id, "project_name": project_id})
    db_groups.create({
        "group_id": group_id,
        "project_id": project_id,
        "module": "default",
        "title": "Type change",
        "status": "OPEN",
    })
    db_users.create({
        "user_id": "usr_admin",
        "username": "admin",
        "email": "admin@test.com",
        "password": "x",
        "is_admin": 1,
    })


def _make_root(tmp_path: Path, type_code: str = "R", seq: int = 1) -> dict:
    """Create a pristine workflow root document with a real .md file on disk."""
    from modules.flow_gate.db import documents as db_docs

    code = f"{str(seq).zfill(4)}-{type_code}"
    doc_id = f"flowgate.default.0066.{code}"
    rel = f"documents/flowgate/main/default/0066/{code}_document.md"
    abs_path = tmp_path / rel
    abs_path.parent.mkdir(parents=True, exist_ok=True)
    abs_path.write_text(f"# root {type_code}\nbody preserved\n", encoding="utf-8")
    db_docs.create({
        "doc_id": doc_id,
        "project_id": "flowgate",
        "module": "default",
        "group_id": "flowgate.default.0066",
        "type_code": type_code,
        "seq": seq,
        "title": "Type change",
        "file_path": rel,
        "status": "open",
        "owner_id": "usr_admin",
    })
    return {"doc_id": doc_id, "code": code, "rel": rel, "abs": abs_path}


def _query(db_path: str, sql: str, params=None) -> list[dict]:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in conn.execute(sql, params or []).fetchall()]
    finally:
        conn.close()


# ── compute helper ────────────────────────────────────────────────────────────

def test_compute_converted_doc_id_swaps_only_the_suffix():
    from modules.flow_gate.documents import document_service as svc

    assert svc.compute_converted_doc_id("flowgate.default.0066.0001-R", "B") \
        == "flowgate.default.0066.0001-B"
    assert svc.compute_converted_doc_id("flowgate.default.0066.0012-B", "R") \
        == "flowgate.default.0066.0012-R"


# ── happy path ────────────────────────────────────────────────────────────────

def test_convert_r_to_b_rewrites_identity_and_renames_file(env):
    from modules.flow_gate.documents import document_service as svc

    _store, db_path, tmp_path = env
    root = _make_root(tmp_path, "R")

    result = svc.convert_root_document_type(root["doc_id"], "B", actor_user_id="usr_admin")

    new_id = "flowgate.default.0066.0001-B"
    assert result["doc_id"] == new_id
    assert result["type_code"] == "B"

    # Old identity is gone, new identity exists.
    assert _query(db_path, "SELECT 1 FROM documents WHERE doc_id=?", [root["doc_id"]]) == []
    rows = _query(db_path, "SELECT * FROM documents WHERE doc_id=?", [new_id])
    assert len(rows) == 1
    assert rows[0]["filename"] == "0001-B_document.md"

    # File renamed on disk, body preserved.
    old_abs = root["abs"]
    new_abs = old_abs.with_name("0001-B_document.md")
    assert not old_abs.exists()
    assert new_abs.is_file()
    assert "body preserved" in new_abs.read_text(encoding="utf-8")

    # A conversion event was recorded.
    ev = _query(db_path, "SELECT * FROM workflow_events WHERE event_type='doc_type_converted'")
    assert len(ev) == 1
    assert "R->B" in ev[0]["metadata"]


def test_convert_rewrites_inbound_references(env):
    """A token's doc_ref and an event row pointing at the root follow the rename."""
    from modules.flow_gate.documents import document_service as svc

    _store, db_path, tmp_path = env
    root = _make_root(tmp_path, "R")
    old_id = root["doc_id"]

    # An events row (FK → documents.doc_id) and a tokens.doc_ref both target the root.
    _store._execute(
        "INSERT INTO events (doc_id, event_type, created_at) "
        "VALUES (?, 'doc_created', datetime('now'))",
        [old_id],
    )
    _store._execute(
        "INSERT INTO tokens (token_id, hash, pepper_id, project, group_id, doc_ref, "
        "action_scope, issued_to, created_at, expires_at) "
        "VALUES ('tk1','h1','p1','flowgate','flowgate.default.0066',?, 'edit','usr_admin',"
        "datetime('now'), datetime('now','+1 day'))",
        [old_id],
    )

    svc.convert_root_document_type(old_id, "B", actor_user_id="usr_admin")
    new_id = "flowgate.default.0066.0001-B"

    assert _query(db_path, "SELECT doc_id FROM events WHERE doc_id=?", [new_id])
    assert _query(db_path, "SELECT doc_id FROM events WHERE doc_id=?", [old_id]) == []
    assert _query(db_path, "SELECT doc_ref FROM tokens WHERE doc_ref=?", [new_id])
    assert _query(db_path, "SELECT doc_ref FROM tokens WHERE doc_ref=?", [old_id]) == []


def test_convert_is_idempotent_for_same_type(env):
    from modules.flow_gate.documents import document_service as svc

    _store, _db_path, tmp_path = env
    root = _make_root(tmp_path, "R")
    result = svc.convert_root_document_type(root["doc_id"], "R", actor_user_id="usr_admin")
    assert result["doc_id"] == root["doc_id"]
    assert result["type_code"] == "R"


# ── rejection gates ───────────────────────────────────────────────────────────

def test_reject_when_child_document_exists(env):
    from modules.flow_gate.db import documents as db_docs
    from modules.flow_gate.documents import document_service as svc

    _store, _db_path, tmp_path = env
    root = _make_root(tmp_path, "R")
    # A child N references the root (workflow decision was taken).
    db_docs.create({
        "doc_id": "flowgate.default.0066.0002-N",
        "project_id": "flowgate",
        "module": "default",
        "group_id": "flowgate.default.0066",
        "type_code": "N",
        "seq": 2,
        "title": "child",
        "status": "open",
        "owner_id": "usr_admin",
        "triggered_by": root["doc_id"],
        "target_id": root["doc_id"],
    })

    with pytest.raises(HTTPException) as exc:
        svc.convert_root_document_type(root["doc_id"], "B", actor_user_id="usr_admin")
    assert exc.value.status_code == 409
    # The root was NOT mutated.
    assert db_docs.get_by_id(root["doc_id"]) is not None


def test_reject_when_workflow_sequence_exists(env):
    from modules.flow_gate.db import workflow_sequences as db_seq
    from modules.flow_gate.documents import document_service as svc

    _store, _db_path, tmp_path = env
    root = _make_root(tmp_path, "R")
    db_seq.insert_sequence(root["doc_id"])

    with pytest.raises(HTTPException) as exc:
        svc.convert_root_document_type(root["doc_id"], "B", actor_user_id="usr_admin")
    assert exc.value.status_code == 409


def test_reject_non_root_type(env):
    from modules.flow_gate.db import documents as db_docs
    from modules.flow_gate.documents import document_service as svc

    _store, _db_path, _tmp = env
    db_docs.create({
        "doc_id": "flowgate.default.0066.0002-N",
        "project_id": "flowgate",
        "module": "default",
        "group_id": "flowgate.default.0066",
        "type_code": "N",
        "seq": 2,
        "title": "memo",
        "status": "open",
        "owner_id": "usr_admin",
    })
    with pytest.raises(HTTPException) as exc:
        svc.convert_root_document_type("flowgate.default.0066.0002-N", "R", actor_user_id="usr_admin")
    assert exc.value.status_code == 422


def test_reject_invalid_target_type(env):
    from modules.flow_gate.documents import document_service as svc

    _store, _db_path, tmp_path = env
    root = _make_root(tmp_path, "R")
    with pytest.raises(HTTPException) as exc:
        svc.convert_root_document_type(root["doc_id"], "X", actor_user_id="usr_admin")
    assert exc.value.status_code == 422


def test_missing_document_404(env):
    from modules.flow_gate.documents import document_service as svc

    with pytest.raises(HTTPException) as exc:
        svc.convert_root_document_type("flowgate.default.0066.9999-R", "B", actor_user_id="usr_admin")
    assert exc.value.status_code == 404
