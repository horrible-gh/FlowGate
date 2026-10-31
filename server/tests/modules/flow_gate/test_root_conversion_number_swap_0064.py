"""flowgate.v02.0064 T#3 — R↔B number swap: file stage, identity fields, attachments, draft cards.

L0005 2.5~2.8 / P0006 §3.2·§3.4~§3.6 / DB0007 §2.5. A real SQLite DB with every migration applied and a
temp storage root: the judgement is a pure read, the coordinator stages the new body next to the original,
rewrites only the ``type`` / ``doc_number`` values, moves the attachment rows and files before the old row
goes (``attachments.doc_id`` cascades), moves the draft cards, and removes the old files only after the
outermost commit. Every failure leaves the original row, body and attachments exactly as they were.
"""
from __future__ import annotations

import asyncio
import hashlib
import os
import re
import sqlite3
import sys
import threading
import time
from contextlib import contextmanager
from pathlib import Path

import pytest

os.environ["TESTING"] = "1"
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing-only-32c")

_SERVER_DIR = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(_SERVER_DIR))

FLOW = "flowgate.v02.0091"
ROOT = f"{FLOW}.0001-R"
ROOT_B = f"{FLOW}.0001-B"
GROUP_DIR = "documents/flowgate/main/v02/0091"
BODY_REL = f"{GROUP_DIR}/0001-R_requirement.md"
USER = "u_kim"
OTHER_FLOW = "flowgate.v02.0092"
SOLO, SOLO_B = "flowgate.v02.solo.0001-R", "flowgate.v02.solo.0001-B"     # a root without a Flow
SOLO_REL = "documents/flowgate/main/v02/solo/0001-R_requirement.md"


@pytest.fixture()
def env(tmp_path, monkeypatch, migrated_sqlite_db):
    from modules.flow_gate.db import _SqliteDbAdapter, _SqliteTxnAdapter
    from modules.flow_gate.db import connection as conn_mod
    from modules.flow_gate.graph_workflow.services import canvas_events, card_document_ports

    path = migrated_sqlite_db("root-conversion.db")
    original = conn_mod.STORE
    store = conn_mod.FlowGateStore()
    store._db = _SqliteDbAdapter(path)
    conn_mod.STORE = store
    monkeypatch.setattr(_SqliteTxnAdapter, "cursor", property(lambda self: self._cur), raising=False)
    storage = tmp_path / "storage"
    monkeypatch.setenv("FLOWGATE_STORAGE_DIR", str(storage))
    state = {"db": path, "storage": storage, "events": []}
    monkeypatch.setattr(canvas_events, "broadcast_event_threadsafe", lambda ev: state["events"].append(ev) or 1)
    monkeypatch.setattr(card_document_ports, "_handlers", {})          # no card lines unless a test adds them
    _seed(path)
    try:
        yield state
    finally:
        conn_mod.STORE = original
        try:
            os.unlink(path)
        except OSError:
            pass


def _sql(db, sql, params=()):
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    try:
        rows = [dict(r) for r in conn.execute(sql, params).fetchall()]
        conn.commit()
        return rows
    finally:
        conn.close()


def _seed(db):
    from modules.flow_gate.db import groups as db_groups
    from modules.flow_gate.db import projects as db_projects

    db_projects.create({"project_id": "flowgate", "project_name": "flowgate"})
    _sql(db, "INSERT INTO users (user_id, username, email, password, created_at, updated_at) "
             "VALUES (?, ?, 'k@x', 'x', 't', 't')", (USER, USER))
    db_groups.create({"group_id": FLOW, "project_id": "flowgate", "module": "v02", "title": FLOW, "status": "OPEN"})


def make_doc(env, doc_id=ROOT, body=b"# root\nbody preserved\n", rel=BODY_REL, type_code=None, seq=1, **extra):
    from modules.flow_gate.db import documents as db_docs

    type_code = type_code or doc_id.rsplit("-", 1)[1]
    if body is not None and rel:
        target = env["storage"] / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(body)
    db_docs.create({"doc_id": doc_id, "project_id": "flowgate", "module": "v02", "group_id": FLOW,
                    "type_code": type_code, "seq": seq, "title": "요구사항", "status": "draft", "owner_id": USER,
                    "file_path": rel or "", "filename": (rel or "").rsplit("/", 1)[-1] or None, **extra})
    return doc_id


def row(env, doc_id):
    rows = _sql(env["db"], "SELECT * FROM documents WHERE doc_id = ?", (doc_id,))
    return rows[0] if rows else None


def attachment_rows(env, doc_id=None):
    if doc_id is None:
        return _sql(env["db"], "SELECT * FROM attachments ORDER BY filename")
    return _sql(env["db"], "SELECT * FROM attachments WHERE doc_id = ? ORDER BY filename", (doc_id,))


def counters(env):
    return _sql(env["db"], "SELECT * FROM id_counter")


def room_of(doc_id):
    from modules.flow_gate.db import documents as db_docs
    from modules.flow_gate.documents.attachments.locator import resolve_attach_dir

    return resolve_attach_dir(db_docs.get_by_id(doc_id))


def add_attachment(env, doc_id, name, data=b"payload", *, file_path=None, uploaded_at="2026-10-01T00:00:00Z"):
    """A registry row (``data=None``: a ghost row, its file missing) in ``doc_id``'s room."""
    from modules.flow_gate.documents.attachments.registry import registry_insert
    from modules.flow_gate.storage import paths as storage_paths

    room = room_of(doc_id)
    target = room / name
    if data is not None:
        room.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    registry_insert(doc_id=doc_id, original_filename=f"orig {name}", filename=name,
                    file_path=file_path or storage_paths.to_storage_relative(target, "flowgate"),
                    size=len(data or b""), content_type="text/plain",
                    content_sha256=hashlib.sha256(data or b"").hexdigest(), uploaded_by=USER,
                    uploaded_at=uploaded_at)
    return target


def orphan_attachment(env, doc_id, name, file_path):
    """A row on an id without a document (FK off: what an earlier swap left behind)."""
    _sql(env["db"], "INSERT INTO attachments (doc_id, original_filename, filename, file_path, size, content_type, "
                    "content_sha256, uploaded_by, uploaded_at, is_legacy_migrated, created_at) "
                    "VALUES (?, ?, ?, ?, 1, 'text/plain', ?, ?, '2026-10-01T00:00:00Z', 0, 't')",
         (doc_id, name, name, file_path, "0" * 64, USER))


def convert(doc_id=ROOT, new_type="B"):
    from modules.flow_gate.documents import root_conversion

    return root_conversion.execute_root_conversion(doc_id, new_type, USER)


def preview(doc_id=ROOT, new_type="B"):
    from modules.flow_gate.db import documents as db_docs
    from modules.flow_gate.documents import root_conversion

    doc = db_docs.get_by_id(doc_id)
    return root_conversion.judge_root_conversion(doc, new_type, root_conversion.read_context(doc_id, doc))


def refused(code, call):
    from modules.flow_gate.documents.conversion_errors import ConversionRefused

    with pytest.raises(ConversionRefused) as excinfo:
        call()
    assert excinfo.value.code == code, excinfo.value.body()
    return excinfo.value.body()


def files_under(path: Path):
    return sorted(str(p.relative_to(path)) for p in path.rglob("*")) if path.exists() else []


def snapshot(env):
    """Everything a refused / rolled-back conversion must leave as it was."""
    storage = env["storage"]
    return {
        "documents": _sql(env["db"], "SELECT * FROM documents ORDER BY doc_id"),
        "attachments": attachment_rows(env),
        "counters": counters(env),
        "events": _sql(env["db"], "SELECT * FROM workflow_events ORDER BY id"),
        "drafts": _sql(env["db"], "SELECT * FROM graph_drafts"),
        "draft_nodes": _sql(env["db"], "SELECT * FROM graph_draft_nodes ORDER BY node_key"),
        "files": {p: (storage / p).read_bytes() for p in files_under(storage) if (storage / p).is_file()},
    }


# ── identity fields: only the values change (V-B1b-2, V-B1b-5, V-B1b-9) ─────────

_FRONT = ("---{n}type: {t}{n}doc_number: {d}{n}title: '요구: 그대로'{n}# comment{n}---{n}"
          "본문 type: R doc_number: 0001-R{n}마지막")
_EOLS = {"lf": ("\n", "\n"), "crlf": ("\r\n", "\r\n"), "mixed": ("\r\n", "\n")}


def _body(eol: str, bom: bool, fields: bool, final_newline: bool, t: str = "R", d: str = "0001-R") -> bytes:
    first, other = _EOLS[eol]
    text = _FRONT if fields else "---{n}title: 메모{n}---{n}본문 type: R{n}마지막"
    text = text.replace("{t}", t).replace("{d}", d)
    parts = text.split("{n}")
    out = parts[0]
    for i, part in enumerate(parts[1:]):
        out += (first if i % 2 == 0 else other) + part
    if final_newline:
        out += other
    return (b"\xef\xbb\xbf" if bom else b"") + out.encode("utf-8")


@pytest.mark.parametrize("eol", sorted(_EOLS))
@pytest.mark.parametrize("bom", [False, True])
@pytest.mark.parametrize("fields", [True, False])
@pytest.mark.parametrize("final_newline", [True, False])
def test_number_swap_changes_only_the_identity_values(env, eol, bom, fields, final_newline):
    original = _body(eol, bom, fields, final_newline)
    make_doc(env, body=original)
    before_counters = counters(env)

    result = convert()

    data, conversion = result["data"], result["conversion"]
    assert (data["doc_id"], data["type_code"], data["filename"]) == (ROOT_B, "B", "0001-B_requirement.md")
    assert data["file_path"] == f"{GROUP_DIR}/0001-B_requirement.md"
    assert conversion == {"mode": "number_swap", "old_doc_id": ROOT, "new_doc_id": ROOT_B, "old_type": "R",
                          "new_type": "B", "child_count": 0, "attachments": {"count": 0, "ghosts": 0},
                          "draft_node_keys": []}
    new_bytes = (env["storage"] / data["file_path"]).read_bytes()
    # only the two values change; without identity fields every byte is copied
    assert new_bytes == (_body(eol, bom, fields, final_newline, "B", "0001-B") if fields else original)
    assert not (env["storage"] / BODY_REL).exists()                  # the old file went after the commit
    assert row(env, ROOT) is None and row(env, ROOT_B)["type_code"] == "B"
    assert counters(env) == before_counters                          # a number swap reserves nothing
    [event] = _sql(env["db"], "SELECT metadata FROM workflow_events WHERE event_type = 'doc_type_converted'")
    assert event["metadata"] == f"R->B ({ROOT} -> {ROOT_B}) mode=number_swap children=0 attachments=0/0 ghosts=0"


@pytest.mark.parametrize("line,expected", [
    (b"type: 'R'", b"type: 'B'"),
    (b'doc_number: "0001-R"', b'doc_number: "0001-B"'),
    (b"doc_number: R0001", b"doc_number: B0001"),
    (b"type: R  # \xeb\xa9\x94\xeb\xaa\xa8", b"type: B  # \xeb\xa9\x94\xeb\xaa\xa8"),
])
def test_quoted_compact_and_commented_values_keep_their_style(env, line, expected):
    body = b"---\n" + line + b"\ntitle: x\n---\nbody " + line + b"\n"
    make_doc(env, body=body)
    data = convert()["data"]
    assert (env["storage"] / data["file_path"]).read_bytes() == body.replace(line, expected, 1)


def test_round_trip_keeps_db_and_file_identity_in_step(env):
    from modules.flow_gate.documents import front_matter_identity as identity

    body = b"\xef\xbb\xbf---\r\ntype: 'R'\ndoc_number: R0001\r\nother: 1\n---\r\nbody\n"
    make_doc(env, body=body)
    b_data = convert(ROOT, "B")["data"]
    b_bytes = (env["storage"] / b_data["file_path"]).read_bytes()
    assert identity.check(b_bytes, "B", "0001-B").fields.keys() == {"type", "doc_number"}
    assert identity.check(b_bytes, "B", "0001-B").safe
    r_data = convert(ROOT_B, "R")["data"]
    assert (r_data["doc_id"], r_data["file_path"]) == (ROOT, BODY_REL)
    assert (env["storage"] / BODY_REL).read_bytes() == body                     # back to the very same bytes
    assert not (env["storage"] / b_data["file_path"]).exists()


@pytest.mark.parametrize("body,reason,field", [
    (b"---\ntype: R\ntype: R\n---\n", "duplicate_key", "type"),
    (b"---\ndoc_number: |\n  0001-R\n---\n", "unsupported_scalar", "doc_number"),
    (b"---\ndoc_number: 0009-R\n---\n", "identity_mismatch", "doc_number"),
    (b"---\ntype: 'R\n---\n", "unsupported_scalar", "type"),
    (b"---\n", "missing_end_delimiter", None),
    (b"---\ntype: R\ndoc_number: 0001-R\nbody without an end\n", "missing_end_delimiter", None),
    (b"\xef\xbb\xbf---\r\ntype: R\r\nbody\r\n", "missing_end_delimiter", None),
])
def test_unsafe_identity_fields_refuse_in_preview_and_execution(env, body, reason, field):
    make_doc(env, body=body)
    before = snapshot(env)
    judged = preview()
    assert judged.kind == "refused" and judged.refusal.code == "front_matter_identity_unsafe"
    assert snapshot(env) == before                                             # the preview is a pure read
    payload = refused("front_matter_identity_unsafe", convert)
    expected = {"error": "front_matter_identity_unsafe", "doc_id": ROOT, "reason": reason}
    if field:
        expected["field"] = field
    assert {k: v for k, v in payload.items() if k != "detail"} == expected and payload["detail"]
    assert snapshot(env) == before


@pytest.mark.parametrize("body", [b"---\ntype: R\n...\nbody\n", b"no front matter\n", b""])
def test_terminated_or_absent_front_matter_converts(env, body):
    make_doc(env, body=body)
    data = convert()["data"]
    assert (env["storage"] / data["file_path"]).read_bytes() == body.replace(b"type: R", b"type: B")


def test_a_body_that_cannot_be_read_refuses_both_ways_without_changes(env):
    make_doc(env, body=b"---\ntype: R\n---\n\xff\xfe broken utf-8\n")
    before = snapshot(env)
    judged = preview()
    assert judged.kind == "refused" and judged.refusal.body() == {
        "error": "root_body_unreadable", "detail": judged.refusal.body()["detail"], "doc_id": ROOT,
        "reason": "read_error", "mode": "number_swap"}
    assert "permissions or encoding" in judged.refusal.body()["detail"]
    refused("root_body_unreadable", convert)
    assert snapshot(env) == before


def test_a_root_without_its_body_file_keeps_the_path_only_behaviour(env):
    make_doc(env, body=None)                                                   # recorded path, no file
    data = convert()["data"]
    assert data["file_path"] == f"{GROUP_DIR}/0001-B_requirement.md"
    assert files_under(env["storage"]) == []                                   # nothing is created


# ── failures keep everything (V-B1b-1, V-B2-5, V-B1b-2c, V-B1b-2h) ─────────────

@contextmanager
def _failing_commit(self):
    from modules.flow_gate.db import _SqliteTxnAdapter

    conn = self._c()
    try:
        yield _SqliteTxnAdapter(conn)
        raise sqlite3.OperationalError("injected commit failure")
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


ORIGINAL_BODY = b"---\ntype: R\ndoc_number: 0001-R\n---\nbody\n"


def _with_attachments(env):
    make_doc(env, body=ORIGINAL_BODY)
    add_attachment(env, ROOT, "a.txt", b"alpha", uploaded_at="2026-10-01T00:00:01Z")
    add_attachment(env, ROOT, "ghost.txt", None, uploaded_at="2026-10-01T00:00:02Z")
    add_attachment(env, ROOT, "c.bin", b"\x00\x01", uploaded_at="2026-10-01T00:00:03Z")


@pytest.mark.parametrize("where", ["lines", "draft", "attachments_copied", "rows_reassigned", "commit"])
def test_an_injected_failure_rolls_back_rows_files_and_rooms(env, monkeypatch, where):
    from modules.flow_gate.db import _SqliteDbAdapter
    from modules.flow_gate.documents.attachments import conversion as attachment_conversion
    from modules.flow_gate.graph_workflow.services import card_document_ports, root_conversion_cards
    from modules.flow_gate.storage.body import body_read_state

    _with_attachments(env)
    before = snapshot(env)
    real_reassign = attachment_conversion.reassign_attachment_rows

    def boom(*_a, **_k):
        raise RuntimeError(f"injected at {where}")

    def after_rows(*a, **k):
        real_reassign(*a, **k)
        raise RuntimeError("injected after the rows moved")

    with monkeypatch.context() as m:
        if where == "lines":
            card_document_ports.register(card_document_ports.ON_ROOT_CONVERTED, boom)
        elif where == "draft":
            card_document_ports.register(card_document_ports.ON_ROOT_CONVERTED, lambda *a: ["n1"])
            m.setattr(root_conversion_cards, "sync_draft_cards", boom)
        elif where == "attachments_copied":
            m.setattr(attachment_conversion, "reassign_attachment_rows", boom)
        elif where == "rows_reassigned":
            m.setattr(attachment_conversion, "reassign_attachment_rows", after_rows)
        else:
            m.setattr(_SqliteDbAdapter, "begin_transaction", _failing_commit)
        with pytest.raises(Exception):
            convert()

    assert snapshot(env) == before                                   # rows, files, counters, events, draft
    assert not room_of(ROOT).with_name("0001-B").exists()            # no new room left behind
    assert not (env["storage"] / f"{GROUP_DIR}/0001-B_requirement.md").exists()
    from modules.flow_gate.db import documents as db_docs
    assert body_read_state(db_docs.get_by_id(ROOT))[0] == "readable"
    worker, console = bodies_through_apis(monkeypatch, ROOT)         # the work token and the console read it
    assert (worker["content_state"], worker["content"]) == ("readable", ORIGINAL_BODY.decode("utf-8"))
    assert console == ORIGINAL_BODY.decode("utf-8")


def guard_seq(env, flow=FLOW):
    rows = _sql(env["db"], "SELECT guard_seq FROM graph_flow_guards WHERE group_id = ?", (flow,))
    return rows[0]["guard_seq"] if rows else 0


def test_cleanup_waits_for_the_outermost_commit(env):
    """The caller owns the outermost frame as the Flow guard of the same Flow: the conversion joins it (one guard
    bump), and its cleanup waits for that guard's commit and is dropped by its rollback."""
    from modules.flow_gate.graph_workflow.services.flow_guard import with_flow_guard

    _with_attachments(env)
    old_room = room_of(ROOT)
    seq = guard_seq(env)

    def owner():
        convert()                                                    # joins: an inner frame ends here
        assert (env["storage"] / BODY_REL).is_file()                 # nothing removed before the outermost commit
        assert (old_room / "a.txt").is_file()

    with_flow_guard(FLOW, owner)
    assert guard_seq(env) == seq + 1                                 # the guard was entered once, not twice
    assert not (env["storage"] / BODY_REL).exists() and not old_room.exists()

    from modules.flow_gate.db import documents as db_docs
    make_doc(env, doc_id=f"{FLOW}.0002-R", seq=2, rel=f"{GROUP_DIR}/0002-R_requirement.md")

    def owner_fails():
        convert(f"{FLOW}.0002-R")
        raise RuntimeError("the caller owning the outer guard fails later")

    with pytest.raises(RuntimeError):
        with_flow_guard(FLOW, owner_fails)
    assert db_docs.get_by_id(f"{FLOW}.0002-R") is not None
    assert (env["storage"] / f"{GROUP_DIR}/0002-R_requirement.md").is_file()
    assert not (env["storage"] / f"{GROUP_DIR}/0002-B_requirement.md").exists()


def test_a_plain_or_another_flows_transaction_is_refused_before_anything_moves(env):
    """An open transaction that is not this document's guard never lets the conversion or an attachment write in:
    no Flow lock, no guard bump, nothing staged (``transaction_required`` / the attachment 500 envelope)."""
    from modules.flow_gate.db import groups as db_groups
    from modules.flow_gate.db.connection import get_store
    from modules.flow_gate.documents.attachments import AttachmentError, delete_attachment, service
    from modules.flow_gate.graph_workflow.services.flow_guard import with_flow_guard

    _with_attachments(env)
    db_groups.create({"group_id": OTHER_FLOW, "project_id": "flowgate", "module": "v02", "title": OTHER_FLOW,
                      "status": "OPEN"})
    before, seq = snapshot(env), guard_seq(env)

    def plain(body):
        with get_store().transaction():
            return body()

    def other_flow(body):
        return with_flow_guard(OTHER_FLOW, body)

    item = {"original_filename": "x.txt", "filename": "x.txt", "file_path": f"{GROUP_DIR}/0001-R/x.txt", "size": 1,
            "content_type": "text/plain", "content_sha256": "0" * 64}
    for opener in (plain, other_flow):
        opener(lambda: refused("transaction_required", convert))
        with pytest.raises(AttachmentError) as deleted:
            opener(lambda: delete_attachment(ROOT, "a.txt", {"user_id": USER}))
        assert deleted.value.status_code == 500
        with pytest.raises(AttachmentError) as uploaded:
            opener(lambda: service._commit_registry(ROOT, [item], USER, {"user_id": USER}, room_of(ROOT)))
        assert uploaded.value.status_code == 500
    assert snapshot(env) == before and guard_seq(env) == seq


def test_a_cleanup_queue_that_cannot_be_used_refuses_without_inline_deletion(env, monkeypatch):
    from modules.flow_gate.documents.attachments import conversion as attachment_conversion

    _with_attachments(env)
    before = snapshot(env)
    with monkeypatch.context() as m:
        m.setattr(attachment_conversion, "after_commit", lambda cb: False)
        refused("transaction_required", convert)
    assert snapshot(env) == before


def test_a_failed_cleanup_after_the_commit_keeps_the_conversion(env, monkeypatch):
    from modules.flow_gate.documents import root_conversion
    from modules.flow_gate.documents.attachments import conversion as attachment_conversion

    _with_attachments(env)
    old_room = room_of(ROOT)

    def fail(*_a, **_k):
        raise OSError("injected cleanup failure")

    monkeypatch.setattr(root_conversion, "referenced_by_any_document", fail)
    monkeypatch.setattr(attachment_conversion, "referenced_by_any_attachment", fail)
    data = convert()["data"]
    assert row(env, ROOT_B) is not None
    assert (env["storage"] / BODY_REL).is_file() and (old_room / "a.txt").is_file()   # only the old files stay
    assert (env["storage"] / data["file_path"]).is_file()
    assert (room_of(ROOT_B) / "a.txt").read_bytes() == b"alpha"


# ── attachments follow the number (V-B1b-2a, 2b, 2f, 2g, 2i, 2j) ───────────────

@pytest.mark.parametrize("with_body", [True, False])
def test_attachments_follow_every_swap(env, with_body):
    from modules.flow_gate.db import documents as db_docs
    from modules.flow_gate.documents.attachments.locator import resolve_registered_attachment

    make_doc(env, body=b"# body\n" if with_body else None, rel=BODY_REL if with_body else "")
    add_attachment(env, ROOT, "a.txt", b"alpha", uploaded_at="2026-10-01T00:00:01Z")
    add_attachment(env, ROOT, "b.pdf", b"%PDF", uploaded_at="2026-10-01T00:00:02Z")
    add_attachment(env, ROOT, "c.bin", b"\x00\x01", uploaded_at="2026-10-01T00:00:03Z")
    before = {r["filename"]: r for r in attachment_rows(env, ROOT)}
    r_room = room_of(ROOT)

    result = convert()
    assert result["conversion"]["attachments"] == {"count": 3, "ghosts": 0}
    b_room = room_of(ROOT_B)
    assert b_room.name == "0001-B" and not r_room.exists()
    assert attachment_rows(env, ROOT) == []
    moved = {r["filename"]: r for r in attachment_rows(env, ROOT_B)}
    assert moved.keys() == before.keys()
    b_doc = db_docs.get_by_id(ROOT_B)
    for name, old in before.items():
        new = moved[name]
        for col in ("id", "original_filename", "size", "content_type", "content_sha256", "uploaded_by",
                    "uploaded_at", "created_at", "is_legacy_migrated"):
            assert new[col] == old[col], col
        _row, path = resolve_registered_attachment(b_doc, name)      # J1~J7 pass for the new id
        assert path.parent == b_room.resolve() and hashlib.sha256(path.read_bytes()).hexdigest() == old["content_sha256"]
    [event] = _sql(env["db"], "SELECT metadata FROM workflow_events WHERE event_type = 'doc_type_converted'")
    assert event["metadata"].endswith(" attachments=3/0 ghosts=0")

    convert(ROOT_B, "R")                                              # and back again
    assert [r["filename"] for r in attachment_rows(env, ROOT)] == sorted(before)
    assert attachment_rows(env, ROOT_B) == [] and not b_room.exists() and room_of(ROOT).is_dir()


def test_a_ghost_moves_as_a_row_and_can_then_be_deleted(env):
    from modules.flow_gate.documents.attachments import AttachmentError, delete_attachment, list_attachments
    from modules.flow_gate.documents.attachments.locator import resolve_registered_attachment
    from modules.flow_gate.db import documents as db_docs

    make_doc(env)
    add_attachment(env, ROOT, "ghost.txt", None)
    result = convert()
    assert result["conversion"]["attachments"] == {"count": 1, "ghosts": 1}
    assert not room_of(ROOT_B).exists()                               # a ghost creates no file and no room
    assert list_attachments(ROOT_B)["count"] == 1
    with pytest.raises(AttachmentError) as read:
        resolve_registered_attachment(db_docs.get_by_id(ROOT_B), "ghost.txt")
    assert (read.value.status_code, read.value.code) == (404, "ATTACHMENT_NOT_FOUND")
    assert delete_attachment(ROOT_B, "ghost.txt", {"user_id": USER})["metadata_deleted"] is True
    assert attachment_rows(env) == []
    with pytest.raises(AttachmentError) as again:
        delete_attachment(ROOT_B, "ghost.txt", {"user_id": USER})
    assert (again.value.status_code, again.value.code) == (404, "ATTACHMENT_NOT_FOUND")


def test_a_reserved_ghost_keeps_the_existing_delete_refusal(env):
    from modules.flow_gate.documents.attachments import AttachmentError, delete_attachment

    make_doc(env)
    add_attachment(env, ROOT, "__wp_pre_instruction__T-1__x.txt", None)
    convert()
    with pytest.raises(AttachmentError) as exc:
        delete_attachment(ROOT_B, "__wp_pre_instruction__T-1__x.txt", {"user_id": USER})
    assert exc.value.code == "DOCUMENT_NOT_MUTABLE"
    assert len(attachment_rows(env, ROOT_B)) == 1


@pytest.mark.parametrize("state", ["group_disposed", "group_ai_running"])
def test_a_ghost_of_a_document_that_cannot_change_keeps_its_row(env, monkeypatch, state):
    """V-B1b-2j: a moved ghost is not deletable just because its file is missing — the existing refusals stand."""
    from modules.flow_gate import process_service
    from modules.flow_gate.documents.attachments import AttachmentError, delete_attachment
    from modules.flow_gate.services import mutation_policy

    make_doc(env)
    add_attachment(env, ROOT, "ghost.txt", None)
    convert()
    [ghost] = attachment_rows(env, ROOT_B)
    if state == "group_disposed":
        monkeypatch.setattr(process_service, "is_group_disposed", lambda group_id: group_id == FLOW)
    else:
        lease = {"group_id": FLOW, "run_id": "run_live_1", "token_id": "tok_1", "action_scope": "edit"}
        monkeypatch.setattr(mutation_policy.db_leases, "get_active", lambda group_id: lease if group_id == FLOW else None)
        monkeypatch.setattr(mutation_policy, "_lease_run_live", lambda run_id: True)
    with pytest.raises(AttachmentError) as exc:
        delete_attachment(ROOT_B, "ghost.txt", {"user_id": USER})
    assert (exc.value.status_code, exc.value.code, exc.value.details["reason"]) == (409, "DOCUMENT_NOT_MUTABLE", state)
    assert attachment_rows(env, ROOT_B) == [ghost]


def _unsafe_path(env, kind, tmp_path):
    base = f"{GROUP_DIR}/0001-R"
    if kind == "dotdot":
        return f"{base}/../../x.txt"
    if kind == "absolute":
        return "C:/outside/x.txt"
    if kind == "other_room":
        return f"{GROUP_DIR}/0002-N/x.txt"
    target = tmp_path / "outside.txt"
    target.write_bytes(b"secret")
    link = room_of(ROOT) / "x.txt"
    link.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.symlink(target, link)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks unavailable")
    return f"{base}/x.txt"


@pytest.mark.parametrize("kind", ["dotdot", "absolute", "other_room", "symlink"])
@pytest.mark.parametrize("position", [0, 1, 2])
def test_one_unsafe_row_refuses_the_whole_conversion(env, tmp_path, kind, position):
    make_doc(env)
    stamps = ["2026-10-01T00:00:01Z", "2026-10-01T00:00:02Z", "2026-10-01T00:00:03Z"]
    unsafe_at = stamps.pop(position)
    add_attachment(env, ROOT, "a.txt", b"alpha", uploaded_at=stamps[0])
    add_attachment(env, ROOT, "ghost.txt", None, uploaded_at=stamps[1])
    path = _unsafe_path(env, kind, tmp_path)
    add_attachment(env, ROOT, "x.txt", None, file_path=path, uploaded_at=unsafe_at)
    before = snapshot(env)
    payload = refused("attachment_path_unsafe", convert)
    assert payload["filename"] == "x.txt" and payload["doc_id"] == ROOT and payload["reason"]
    assert snapshot(env) == before
    assert not room_of(ROOT).with_name("0001-B").exists()


@pytest.mark.parametrize("ghost", [False, True])
def test_a_file_already_in_the_new_room_refuses(env, ghost):
    make_doc(env)
    add_attachment(env, ROOT, "a.txt", None if ghost else b"alpha")
    squatter = room_of(ROOT).with_name("0001-B") / "a.txt"
    squatter.parent.mkdir(parents=True)
    squatter.write_bytes(b"someone else")
    before = snapshot(env)
    assert refused("attachment_target_exists", convert)["filename"] == "a.txt"
    assert snapshot(env) == before


@pytest.mark.parametrize("change", ["add", "delete", "same_name_value"])
def test_rows_changed_after_staging_refuse(env, monkeypatch, change):
    from modules.flow_gate.db.connection import get_store
    from modules.flow_gate.documents.attachments import conversion as attachment_conversion

    _with_attachments(env)
    before = snapshot(env)
    real = attachment_conversion.reassign_attachment_rows

    def changed(doc, new_id, stage, mode):
        store = get_store()
        if change == "add":
            add_attachment(env, ROOT, "late.txt", b"late")
        elif change == "delete":
            store._execute("DELETE FROM attachments WHERE doc_id = ? AND filename = 'c.bin'", [ROOT])
        else:
            store._execute("UPDATE attachments SET size = 99 WHERE doc_id = ? AND filename = 'a.txt'", [ROOT])
        return real(doc, new_id, stage, mode)

    with monkeypatch.context() as m:
        m.setattr(attachment_conversion, "reassign_attachment_rows", changed)
        refused("attachments_changed", convert)
    after = snapshot(env)
    if change == "add":                                    # the late file was written outside the transaction
        after["files"].pop(next(p for p in after["files"] if p.endswith("late.txt")))
    assert after == before


def test_an_order_only_difference_is_not_a_change(env, monkeypatch):
    from modules.flow_gate.documents.attachments import conversion as attachment_conversion

    _with_attachments(env)
    real = attachment_conversion.registry_list
    calls = {"n": 0}

    def shuffled(doc_id):
        calls["n"] += 1
        rows = list(real(doc_id))
        return rows[::-1] if calls["n"] % 2 == 0 else rows

    monkeypatch.setattr(attachment_conversion, "registry_list", shuffled)
    assert convert()["conversion"]["attachments"] == {"count": 3, "ghosts": 1}
    assert sorted(r["filename"] for r in attachment_rows(env, ROOT_B)) == ["a.txt", "c.bin", "ghost.txt"]


def test_files_other_rows_still_use_are_kept(env):
    from modules.flow_gate.documents.attachments.registry import registry_insert

    make_doc(env)
    old_room = room_of(ROOT)
    add_attachment(env, ROOT, "a.txt", b"alpha")
    make_doc(env, doc_id=f"{FLOW}.0002-N", seq=2, body=b"n", rel=f"{GROUP_DIR}/0002-N_note.md")
    registry_insert(doc_id=f"{FLOW}.0002-N", original_filename="a.txt", filename="a.txt",
                    file_path=f"{GROUP_DIR}/./0001-R/a.txt", size=5, content_type="text/plain",
                    content_sha256=hashlib.sha256(b"alpha").hexdigest(), uploaded_by=USER)
    (old_room / "loose.txt").write_bytes(b"no row")
    absolute = str((env["storage"] / BODY_REL).resolve())
    make_doc(env, doc_id=f"{FLOW}.0003-M", seq=3, body=None, rel=absolute)    # same body, another spelling

    convert()
    assert (old_room / "a.txt").read_bytes() == b"alpha"                        # another row's file
    assert (old_room / "loose.txt").is_file() and old_room.is_dir()             # a non-empty room stays
    assert (env["storage"] / BODY_REL).is_file()                                 # another row's body
    from modules.flow_gate.db import documents as db_docs
    from modules.flow_gate.storage.body import body_read_state
    assert body_read_state(db_docs.get_by_id(f"{FLOW}.0003-M"))[0] == "readable"


# ── target id rows (V-B1b-2k, 2l) ───────────────────────────────────────────────

@pytest.mark.parametrize("source", ["same_name", "other_name", "none"])
@pytest.mark.parametrize("target", ["file", "ghost", "unsafe"])
def test_rows_left_on_the_target_id_refuse_everything(env, source, target):
    make_doc(env, doc_id=ROOT_B, body=b"---\ntype: B\n---\n", rel=f"{GROUP_DIR}/0001-B_requirement.md")
    if source != "none":
        add_attachment(env, ROOT_B, "a.txt" if source == "same_name" else "b.txt", b"mine")
    room_r = env["storage"] / GROUP_DIR / "0001-R"
    path = {"file": f"{GROUP_DIR}/0001-R/a.txt", "ghost": f"{GROUP_DIR}/0001-R/gone.txt",
            "unsafe": "C:/elsewhere/a.txt"}[target]
    if target == "file":
        room_r.mkdir(parents=True)
        (room_r / "a.txt").write_bytes(b"orphan")
    orphan_attachment(env, ROOT, "a.txt", path)
    before = snapshot(env)
    judged = preview(ROOT_B, "R")
    assert judged.kind == "refused"
    assert judged.refusal.body()["error"] == "attachment_target_rows_exist"
    payload = refused("attachment_target_rows_exist", lambda: convert(ROOT_B, "R"))
    assert {k: v for k, v in payload.items() if k != "detail"} == {
        "error": "attachment_target_rows_exist", "doc_id": ROOT_B, "target_doc_id": ROOT, "mode": "number_swap"}
    assert snapshot(env) == before
    assert row(env, ROOT) is None


@pytest.mark.parametrize("with_source", [True, False])
def test_target_rows_appearing_later_are_caught_inside(env, monkeypatch, with_source):
    from modules.flow_gate.db.connection import get_store
    from modules.flow_gate.documents.attachments import conversion as attachment_conversion

    make_doc(env)
    if with_source:
        add_attachment(env, ROOT, "a.txt", b"alpha")
    assert preview().kind == "number_swap"
    orphan_attachment(env, ROOT_B, "early.txt", f"{GROUP_DIR}/0001-B/early.txt")    # after the preview
    before = snapshot(env)
    refused("attachment_target_rows_exist", convert)
    assert snapshot(env) == before
    _sql(env["db"], "DELETE FROM attachments WHERE doc_id = ?", (ROOT_B,))

    real = attachment_conversion.reassign_attachment_rows

    def injected(doc, new_id, stage, mode):                     # the new row exists here, so the FK holds
        get_store()._execute(
            "INSERT INTO attachments (doc_id, original_filename, filename, file_path, size, content_type, "
            "content_sha256, uploaded_by, uploaded_at, is_legacy_migrated, created_at) "
            "VALUES (?, 'x', 'injected.txt', ?, 1, 'text/plain', ?, ?, 't', 0, 't')",
            [new_id, f"{GROUP_DIR}/0001-B/injected.txt", "1" * 64, USER])
        return real(doc, new_id, stage, mode)

    before = snapshot(env)
    with monkeypatch.context() as m:
        m.setattr(attachment_conversion, "reassign_attachment_rows", injected)
        refused("attachment_target_rows_exist", convert)
    assert snapshot(env) == before
    assert not (env["storage"] / GROUP_DIR / "0001-B").exists()


# ── draft cards (V-B2-1~4, V-P-13) ──────────────────────────────────────────────

def _draft(nodes):
    from modules.flow_gate.db.connection import get_store
    from modules.flow_gate.graph_workflow.store import definition_store

    with get_store().transaction():
        definition_store.create_draft(FLOW, None, USER)
        definition_store.replace_draft_content(FLOW, nodes, [])


def _nodes(env):
    return {r["node_key"]: (r["node_type"], r["doc_type"], r["palette_item_id"], r["label"])
            for r in _sql(env["db"], "SELECT * FROM graph_draft_nodes WHERE group_id = ?", (FLOW,))}


def _version(env):
    return _sql(env["db"], "SELECT draft_version FROM graph_drafts WHERE group_id = ?", (FLOW,))[0]["draft_version"]


def test_draft_cards_holding_the_root_take_the_new_type(env):
    from modules.flow_gate.graph_workflow.services import card_document_ports

    make_doc(env)
    doc = lambda key, label, palette=None, t="R": {"node_key": key, "node_type": "document", "doc_type": t,  # noqa: E731
                                                   "palette_item_id": palette, "label": label}
    _draft([doc("n_ko", "요구사항", "requirement"), doc("n_en", "  requirement ", None), doc("n_ja", "要件", "other"),
            doc("n_person", "내 요구", "requirement"), doc("n_other", "요구사항", "requirement"),
            {"node_key": "n_custom", "node_type": "custom", "doc_type": None, "palette_item_id": "custom_x",
             "label": "요구사항"},
            {"node_key": "n_gate", "node_type": "gate", "doc_type": None, "palette_item_id": None, "label": "확인"}])
    holders = ["n_ko", "n_en", "n_ja", "n_person", "n_custom", "n_gate"]
    card_document_ports.register(card_document_ports.ON_ROOT_CONVERTED, lambda flow, old, new: holders)
    env["events"].clear()

    result = convert()

    assert result["conversion"]["draft_node_keys"] == ["n_ko", "n_en", "n_ja", "n_person"]
    assert _nodes(env) == {
        "n_ko": ("document", "B", "bug", "버그"),
        "n_en": ("document", "B", None, "Bug"),
        "n_ja": ("document", "B", "other", "バグ"),
        "n_person": ("document", "B", "bug", "내 요구"),
        "n_other": ("document", "R", "requirement", "요구사항"),          # not holding this root
        "n_custom": ("custom", None, "custom_x", "요구사항"),
        "n_gate": ("gate", None, None, "확인"),
    }
    assert _version(env) == 2
    events = [e.payload for e in env["events"] if e.event_type == "graph_canvas_changed"]
    assert [(p["reason"], p["draft_version"], p["flow_id"], p["actor_user_id"]) for p in events] == [
        ("draft", 2, FLOW, USER)]


def test_no_changed_card_means_no_version_and_no_event(env):
    from modules.flow_gate.graph_workflow.services import card_document_ports

    make_doc(env)
    _draft([{"node_key": "n_r", "node_type": "document", "doc_type": "R", "palette_item_id": None, "label": "요구"}])
    card_document_ports.register(card_document_ports.ON_ROOT_CONVERTED, lambda flow, old, new: [])
    env["events"].clear()
    assert convert()["conversion"]["draft_node_keys"] == []
    assert _version(env) == 1 and _nodes(env)["n_r"][1] == "R"
    assert [e for e in env["events"] if e.event_type == "graph_canvas_changed"] == []


def test_a_draft_version_race_inside_the_guard_rolls_everything_back(env, monkeypatch):
    from modules.flow_gate.graph_workflow.services import card_document_ports
    from modules.flow_gate.graph_workflow.store import definition_store

    make_doc(env)
    _draft([{"node_key": "n_r", "node_type": "document", "doc_type": "R", "palette_item_id": None, "label": "요구"}])
    card_document_ports.register(card_document_ports.ON_ROOT_CONVERTED, lambda flow, old, new: ["n_r"])
    before = snapshot(env)
    with monkeypatch.context() as m:
        m.setattr(definition_store, "bump_draft", lambda *a: 0)
        assert refused("draft_version_conflict", convert)["doc_id"] == ROOT
    assert snapshot(env) == before


def test_root_names_match_the_client_i18n():
    from modules.flow_gate.graph_workflow.services.root_conversion_cards import ROOT_NAME_KEYS, ROOT_NAMES

    i18n = Path(__file__).resolve().parents[4] / "client_gui" / "src" / "app" / "i18n"
    assert ROOT_NAME_KEYS == {"R": "gui.designer.center.node_kinds.requirement",
                              "B": "gui.designer.inspector.header.kind_bug"}
    for locale in ("ko", "ja", "en"):
        text = (i18n / f"{locale}.ts").read_text(encoding="utf-8")
        kinds = re.search(r"node_kinds:\s*\{[^}]*?requirement:\s*'([^']*)'", text)
        bug = re.search(r"header:\s*\{[^}]*?kind_bug:\s*'([^']*)'", text, re.S)
        assert kinds and kinds.group(1) == ROOT_NAMES["R"][locale], locale
        assert bug and bug.group(1) == ROOT_NAMES["B"][locale], locale


# ── the route (V-P-11, V-P-12) ──────────────────────────────────────────────────

@pytest.fixture()
def client(env):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from modules.flow_gate.auth.middleware import get_current_user
    from modules.flow_gate.documents.routers import documents as routes

    app = FastAPI()
    app.include_router(routes.router, prefix="/api/v1")
    app.dependency_overrides[get_current_user] = lambda: {"user_id": USER, "is_admin": True}
    return TestClient(app)


def bodies_through_apis(monkeypatch, doc_id):
    """(work-token document read, console body) of ``doc_id`` through their real routes (V-B1b-1)."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from modules.flow_gate.api.v1 import document_routes as token
    from modules.flow_gate.auth.middleware import get_current_user
    from modules.flow_gate.documents.routers import documents as routes

    app = FastAPI()
    app.include_router(routes.router, prefix="/api/v1")
    app.include_router(token.router, prefix="/flowgate")
    app.dependency_overrides[get_current_user] = lambda: {"user_id": USER, "is_admin": True}
    monkeypatch.setattr(token, "verify_bearer", lambda request: {"_is_user_jwt": True})
    client = TestClient(app)
    worker = client.get(f"/flowgate/api/v1/document/{doc_id}")
    console = client.get(f"/api/v1/documents/{doc_id}/content")
    assert (worker.status_code, console.status_code) == (200, 200), (worker.text, console.text)
    return worker.json(), console.json()["content"]


def test_the_route_answers_with_conversion_and_flat_refusals(env, client):
    make_doc(env, body=b"---\ntype: R\ntype: R\n---\n")
    bad = client.patch(f"/api/v1/documents/{ROOT}/root-type", json={"new_type": "B"})
    assert bad.status_code == 409
    assert bad.json() == {"error": "front_matter_identity_unsafe", "detail": bad.json()["detail"], "doc_id": ROOT,
                          "reason": "duplicate_key", "field": "type"}

    (env["storage"] / BODY_REL).write_bytes(b"---\ntype: R\n---\nbody\n")
    ok = client.patch(f"/api/v1/documents/{ROOT}/root-type", json={"new_type": "B"})
    assert ok.status_code == 200, ok.text
    assert ok.json()["data"]["doc_id"] == ROOT_B and ok.json()["conversion"]["mode"] == "number_swap"
    same = client.patch(f"/api/v1/documents/{ROOT_B}/root-type", json={"new_type": "B"})
    assert same.status_code == 200 and "conversion" not in same.json()

    make_doc(env, doc_id=f"{FLOW}.0002-R", seq=2, rel=f"{GROUP_DIR}/0002-R_requirement.md")
    make_doc(env, doc_id=f"{FLOW}.0003-N", seq=3, body=None, rel="", triggered_by=f"{FLOW}.0002-R")
    child = client.patch(f"/api/v1/documents/{FLOW}.0002-R/root-type", json={"new_type": "B"})
    # 0064 T#4: a root with child documents is succeeded (P0006 §3.3), the child refusal is gone (§3.5).
    assert child.status_code == 200, child.text
    assert (child.json()["conversion"]["mode"], child.json()["conversion"]["child_count"]) == ("succession", 1)


# ── one guard: the contact's, the coordinator's own, attachment writes (L0005 2.6 / 2.7.1) ─────

DECIDED = "Cannot convert root type after the workflow decision was taken."


def test_the_route_joins_the_contact_guard_or_takes_its_own(env, client, monkeypatch):
    from modules.flow_gate.db import workflow_sequences as ws
    from modules.flow_gate.graph_workflow.services import flow_guard, v01_contact

    make_doc(env)
    calls = []
    real = flow_guard.with_flow_guard
    monkeypatch.setattr(v01_contact, "with_flow_guard", lambda f, b: calls.append(("contact", f)) or real(f, b))
    monkeypatch.setattr(flow_guard, "with_flow_guard", lambda f, b: calls.append(("own", f)) or real(f, b))
    ok = client.patch(f"/api/v1/documents/{ROOT}/root-type", json={"new_type": "B"})
    assert ok.status_code == 200 and calls == [("contact", FLOW)]          # unassigned: joined, one guard

    calls.clear()
    ws.insert_sequence(ROOT_B)                                             # a v0.1 Flow: the contact passes through
    decided = client.patch(f"/api/v1/documents/{ROOT_B}/root-type", json={"new_type": "R"})
    assert (decided.status_code, decided.json()) == (409, {"detail": DECIDED})
    assert calls == [("own", FLOW)]


def test_ownership_and_ai_run_are_judged_again_inside_the_guard(env, monkeypatch):
    from modules.flow_gate.graph_workflow.services import engine_router
    from modules.flow_gate.graph_workflow.services.engine_router import Ownership
    from modules.flow_gate.services import mutation_policy

    make_doc(env)
    before = snapshot(env)
    for state, code in (("graph", "flow_owned_by_graph"), ("conflict", "flow_ownership_conflict")):
        with monkeypatch.context() as m:
            m.setattr(engine_router, "read_ownership", lambda flow, s=state: Ownership(s, None, None))
            assert refused(code, convert) == {"error": code, "doc_id": ROOT, "group_id": FLOW,
                                              "graph_revision_no": None}
    lease = {"group_id": FLOW, "run_id": "run_live_1", "token_id": "tok_1", "action_scope": "edit"}
    with monkeypatch.context() as m:
        m.setattr(mutation_policy.db_leases, "get_active", lambda group_id: lease if group_id == FLOW else None)
        m.setattr(mutation_policy, "_lease_run_live", lambda run_id: True)
        with pytest.raises(mutation_policy.MutationPolicyError):
            convert()
    assert snapshot(env) == before


class _Part:
    def __init__(self, filename, data: bytes) -> None:
        self.filename, self._data, self._pos = filename, data, 0

    async def read(self, size: int = -1) -> bytes:
        if size is None or size < 0:
            chunk, self._pos = self._data[self._pos:], len(self._data)
            return chunk
        chunk = self._data[self._pos:self._pos + size]
        self._pos += len(chunk)
        return chunk


def test_upload_and_delete_commit_under_the_flow_guard(env, monkeypatch):
    from modules.flow_gate.documents.attachments import delete_attachment, service
    from modules.flow_gate.graph_workflow.services import flow_guard

    make_doc(env)
    guarded = []
    real = flow_guard.with_flow_guard
    monkeypatch.setattr(flow_guard, "with_flow_guard", lambda f, b: guarded.append(f) or real(f, b))
    out = asyncio.run(service.upload_attachments(ROOT, [_Part("a.txt", b"alpha")], {"user_id": USER}))
    assert out["count"] == 1 and guarded == [FLOW]
    delete_attachment(ROOT, "a.txt", {"user_id": USER})
    assert guarded == [FLOW, FLOW] and attachment_rows(env) == []


def test_an_upload_committing_after_the_conversion_is_refused_and_cleaned(env, monkeypatch):
    from modules.flow_gate.documents.attachments import AttachmentError, service

    make_doc(env)
    real = service._commit_registry

    def conversion_first(*args):
        convert()                                   # the conversion got the guard before this commit
        return real(*args)

    monkeypatch.setattr(service, "_commit_registry", conversion_first)
    with pytest.raises(AttachmentError) as exc:
        asyncio.run(service.upload_attachments(ROOT, [_Part("late.txt", b"late")], {"user_id": USER}))
    assert (exc.value.status_code, exc.value.code) == (404, "DOCUMENT_NOT_FOUND")
    assert attachment_rows(env) == [] and row(env, ROOT_B) is not None
    assert not (env["storage"] / GROUP_DIR / "0001-R" / "late.txt").exists()


# ── one guard, real contention: Flow guard or the document row lock (L0005 2.7.1, V-B1b-2e ③) ─────────────

class _Pause:
    """Holds the wrapped call (inside its guard) until released."""

    def __init__(self) -> None:
        self.inside, self.release = threading.Event(), threading.Event()

    def wrap(self, real):
        def paused(*args, **kwargs):
            self.inside.set()
            assert self.release.wait(30), "never released"
            return real(*args, **kwargs)
        return paused


def _thread(results, key, call):
    def target():
        try:
            results[key] = ("ok", call())
        except BaseException as exc:  # noqa: BLE001 — judged by the test
            results[key] = ("error", exc)
    worker = threading.Thread(target=target, daemon=True)
    worker.start()
    return worker


def _joined(*workers):
    for worker in workers:
        worker.join(60)
        assert not worker.is_alive()


def make_root(env, flow: bool):
    """(old id, new id) of a root in the Flow, or of a root without a Flow (its own row lock)."""
    if flow:
        make_doc(env, body=b"---\ntype: R\n---\nbody\n")
        return ROOT, ROOT_B
    make_doc(env, doc_id=SOLO, rel=SOLO_REL, body=b"---\ntype: R\n---\nbody\n", group_id=None)
    return SOLO, SOLO_B


def _upload(doc_id, name, data):
    from modules.flow_gate.documents.attachments import service

    return asyncio.run(service.upload_attachments(doc_id, [_Part(name, data)], {"user_id": USER}))


@pytest.mark.parametrize("flow", [True, False], ids=["flow_guard", "row_lock"])
def test_writes_wait_for_a_conversion_holding_the_guard(env, monkeypatch, flow):
    """While a conversion holds the guard, an upload's registration, a delete, a legacy registration and a second
    conversion of the same root wait; then each finds the document moved and is refused. Nothing is left on the old
    id, the moved attachment keeps its bytes, the refused upload / legacy copy are removed and the legacy original
    stays where it was."""
    from fastapi import HTTPException

    from modules.flow_gate.documents.attachments import AttachmentError, delete_attachment, legacy
    from modules.flow_gate.documents.attachments import conversion as attachment_conversion

    old_id, new_id = make_root(env, flow)
    add_attachment(env, old_id, "a.txt", b"alpha")
    old_room = room_of(old_id)
    original = legacy.legacy_root("flowgate") / old_id / "old.txt"
    original.parent.mkdir(parents=True)
    original.write_bytes(b"legacy")
    pause = _Pause()
    monkeypatch.setattr(attachment_conversion, "reassign_attachment_rows",
                        pause.wrap(attachment_conversion.reassign_attachment_rows))
    results: dict = {}
    converting = _thread(results, "convert", lambda: convert(old_id))
    assert pause.inside.wait(30)
    writers = [
        _thread(results, "upload", lambda: _upload(old_id, "late.txt", b"late")),
        _thread(results, "delete", lambda: delete_attachment(old_id, "a.txt", {"user_id": USER})),
        _thread(results, "legacy", lambda: legacy.migrate_legacy_attachments("flowgate", dry_run=False)),
        _thread(results, "again", lambda: convert(old_id)),
    ]
    time.sleep(1.5)
    assert all(w.is_alive() for w in writers), results                # every writer waits on the guard
    assert (old_room / "a.txt").read_bytes() == b"alpha" and row(env, old_id) is not None
    pause.release.set()
    _joined(converting, *writers)

    assert results["convert"][0] == "ok", results["convert"]
    assert row(env, old_id) is None and row(env, new_id) is not None
    for key in ("upload", "delete"):
        kind, exc = results[key]
        assert kind == "error" and isinstance(exc, AttachmentError), results[key]
        assert (exc.status_code, exc.code) == (404, "DOCUMENT_NOT_FOUND")
    kind, exc = results["again"]
    assert kind == "error" and isinstance(exc, HTTPException) and exc.status_code == 404, results["again"]
    report = results["legacy"][1]
    assert report["moved"] == [] and report["refused"] == [
        {"doc_id": old_id, "filename": "old.txt", "code": "DOCUMENT_NOT_FOUND", "reason": None}]
    assert original.read_bytes() == b"legacy"                          # the legacy original is kept, unmoved
    assert not (old_room / "late.txt").exists() and not (old_room / "old.txt").exists()
    assert attachment_rows(env, old_id) == []
    assert [r["filename"] for r in attachment_rows(env, new_id)] == ["a.txt"]
    assert (room_of(new_id) / "a.txt").read_bytes() == b"alpha"         # the committed attachment is not lost


@pytest.mark.parametrize("writer", ["upload", "delete"])
@pytest.mark.parametrize("flow", [True, False], ids=["flow_guard", "row_lock"])
def test_a_conversion_waiting_for_an_attachment_write_moves_what_it_committed(env, monkeypatch, flow, writer):
    """The other order: the attachment write holds the guard, the conversion waits, then snapshots and moves exactly
    what that write committed (an upload's new row and file move with it; a deleted row stays deleted)."""
    from modules.flow_gate.documents.attachments import delete_attachment, service

    old_id, new_id = make_root(env, flow)
    add_attachment(env, old_id, "a.txt", b"alpha")
    add_attachment(env, old_id, "c.txt", b"charlie")
    pause = _Pause()
    results: dict = {}
    if writer == "upload":
        monkeypatch.setattr(service, "registry_insert", pause.wrap(service.registry_insert))
        writing = _thread(results, "write", lambda: _upload(old_id, "b.txt", b"bravo"))
        expected = {"a.txt": b"alpha", "b.txt": b"bravo", "c.txt": b"charlie"}
    else:
        monkeypatch.setattr(service, "registry_delete", pause.wrap(service.registry_delete))
        writing = _thread(results, "write", lambda: delete_attachment(old_id, "a.txt", {"user_id": USER}))
        expected = {"c.txt": b"charlie"}
    assert pause.inside.wait(30)
    converting = _thread(results, "convert", lambda: convert(old_id))
    time.sleep(1.5)
    assert converting.is_alive() and row(env, old_id) is not None       # the conversion waits on the guard
    pause.release.set()
    _joined(writing, converting)

    assert results["write"][0] == "ok", results["write"]
    assert results["convert"][0] == "ok", results["convert"]
    assert results["convert"][1]["conversion"]["attachments"] == {"count": len(expected), "ghosts": 0}
    assert attachment_rows(env, old_id) == [] and not room_of(new_id).with_name(old_id.rsplit(".", 1)[1]).exists()
    assert {r["filename"]: (room_of(new_id) / r["filename"]).read_bytes()
            for r in attachment_rows(env, new_id)} == expected


def test_the_row_lock_holds_off_a_writer_that_skips_the_process_lock(env):
    """A second server process shares no in-process lock: the durable half (the document row lock, here SQLite's
    write lock taken as the transaction's first statement) still makes its transaction wait."""
    from modules.flow_gate.db.connection import get_store
    from modules.flow_gate.graph_workflow.services.flow_guard import lock_document_row, with_document_lock

    make_root(env, flow=False)
    pause = _Pause()
    results: dict = {}
    holder = _thread(results, "holder", lambda: with_document_lock(SOLO, pause.wrap(lambda: "held")))
    assert pause.inside.wait(30)

    def other_process():
        with get_store().transaction():
            lock_document_row(SOLO)
            return time.monotonic()

    waiter = _thread(results, "waiter", other_process)
    time.sleep(1.5)
    assert waiter.is_alive()
    released_at = time.monotonic()
    pause.release.set()
    _joined(holder, waiter)
    assert results["holder"] == ("ok", "held")
    assert results["waiter"][0] == "ok" and results["waiter"][1] >= released_at


@pytest.mark.parametrize("dialect,statement", [
    (1, "UPDATE documents SET updated_at = updated_at WHERE doc_id = ?"),
    (2, "SELECT doc_id FROM documents WHERE doc_id = ? FOR UPDATE"),
    (3, "SELECT doc_id FROM documents WHERE doc_id = ? FOR UPDATE"),
], ids=["sqlite", "mysql", "postgresql"])
def test_the_row_lock_statement_of_each_database(monkeypatch, dialect, statement):
    """PostgreSQL / MySQL lock the row (``FOR UPDATE``); SQLite has no row lock and takes its write lock."""
    from modules.flow_gate.db import connection as conn_mod
    from modules.flow_gate.graph_workflow.services import flow_guard

    seen = []

    class _Store:
        def __init__(self) -> None:
            self.dialect = dialect

        def _execute_affected(self, sql, params=None):
            seen.append((sql, params))
            return 1

        def _fetch_one(self, sql, params=None):
            seen.append((sql, params))
            return {"doc_id": params[0]}

    monkeypatch.setattr(conn_mod, "STORE", _Store())
    flow_guard.lock_document_row(SOLO)
    assert seen == [(statement, [SOLO])]


def test_a_root_without_a_flow_converts_under_its_row_lock(env, monkeypatch):
    """No Flow: the conversion and the attachment writes take the document's own guard, never a Flow guard."""
    from modules.flow_gate.documents.attachments import delete_attachment
    from modules.flow_gate.graph_workflow.services import flow_guard

    old_id, new_id = make_root(env, flow=False)
    add_attachment(env, old_id, "a.txt", b"alpha")
    taken = []
    real_flow, real_doc = flow_guard.with_flow_guard, flow_guard.with_document_lock
    monkeypatch.setattr(flow_guard, "with_flow_guard", lambda f, b: taken.append(("flow", f)) or real_flow(f, b))
    monkeypatch.setattr(flow_guard, "with_document_lock", lambda d, b: taken.append(("document", d)) or real_doc(d, b))
    assert _upload(old_id, "b.txt", b"bravo")["count"] == 1
    delete_attachment(old_id, "b.txt", {"user_id": USER})
    result = convert(old_id)
    assert result["conversion"]["new_doc_id"] == new_id and result["conversion"]["attachments"]["count"] == 1
    assert taken == [("document", old_id)] * 3
    assert (room_of(new_id) / "a.txt").read_bytes() == b"alpha" and attachment_rows(env, old_id) == []


# ── legacy registration under the same guard (L0005 2.7.1) ─────────────────────────────────────────────────

def _legacy_original(doc_id, name="note.txt", data=b"note"):
    from modules.flow_gate.documents.attachments import legacy

    original = legacy.legacy_root("flowgate") / doc_id / name
    original.parent.mkdir(parents=True, exist_ok=True)
    original.write_bytes(data)
    return original


@pytest.mark.parametrize("flow", [True, False], ids=["flow_guard", "row_lock"])
def test_legacy_registration_commits_under_the_documents_guard(env, monkeypatch, flow):
    from modules.flow_gate.documents.attachments import legacy
    from modules.flow_gate.graph_workflow.services import flow_guard

    old_id, _new_id = make_root(env, flow)
    original = _legacy_original(old_id)
    taken = []
    real_flow, real_doc = flow_guard.with_flow_guard, flow_guard.with_document_lock
    monkeypatch.setattr(flow_guard, "with_flow_guard", lambda f, b: taken.append(("flow", f)) or real_flow(f, b))
    monkeypatch.setattr(flow_guard, "with_document_lock", lambda d, b: taken.append(("document", d)) or real_doc(d, b))
    report = legacy.migrate_legacy_attachments("flowgate", dry_run=False)
    assert [m["filename"] for m in report["moved"]] == ["note.txt"] and report["refused"] == []
    assert taken == ([("flow", FLOW)] if flow else [("document", old_id)])
    [registered] = attachment_rows(env, old_id)
    assert registered["is_legacy_migrated"] == 1 and registered["uploaded_by"] is None
    assert (room_of(old_id) / registered["filename"]).read_bytes() == b"note"
    assert not original.exists()
    assert (legacy.legacy_root("flowgate") / legacy.MIGRATED_DIR_NAME / old_id / "note.txt").read_bytes() == b"note"


@pytest.mark.parametrize("change", ["converted", "room_moved"])
def test_legacy_registration_rechecks_the_document_under_the_guard(env, monkeypatch, change):
    """The copy is written first; under the guard the document must still exist and own that room. Otherwise only
    this run's copy (and the room it made) goes, no row is written and the original stays where it was."""
    from modules.flow_gate.documents.attachments import legacy

    make_doc(env)
    original = _legacy_original(ROOT)
    copy_room = env["storage"] / GROUP_DIR / "0001-R"
    real = legacy.resolve_content_type

    def meanwhile(name):
        assert (copy_room / "note.txt").is_file()                       # the copy is on disk, not registered
        if change == "converted":
            convert()
        else:
            moved = f"{GROUP_DIR}/moved/0001-R_requirement.md"
            (env["storage"] / moved).parent.mkdir(parents=True)
            (env["storage"] / BODY_REL).rename(env["storage"] / moved)
            _sql(env["db"], "UPDATE documents SET file_path = ? WHERE doc_id = ?", (moved, ROOT))
        return real(name)

    monkeypatch.setattr(legacy, "resolve_content_type", meanwhile)
    report = legacy.migrate_legacy_attachments("flowgate", dry_run=False)
    code, reason = (("DOCUMENT_NOT_FOUND", None) if change == "converted"
                    else ("DOCUMENT_NOT_MUTABLE", "attachment_room_changed"))
    assert report["moved"] == [] and report["refused"] == [
        {"doc_id": ROOT, "filename": "note.txt", "code": code, "reason": reason}]
    assert original.read_bytes() == b"note"                             # neither moved nor deleted
    assert attachment_rows(env) == []
    assert not copy_room.exists()                                       # the copy and the room this run made


# ── cleanup after the durable lock is gone: another process stages at the same paths (0064 T#3) ─────────────

class _Processes:
    """Worker threads that act as separate server processes: none shares the in-process Flow / document lock of
    another (each has its own lock tables), so only the durable half of a guard — the DB lock — orders them."""

    def __init__(self, monkeypatch) -> None:
        from modules.flow_gate.graph_workflow.services import flow_guard

        self._local = threading.local()
        real = flow_guard._named_lock

        def named(table, key):
            own = getattr(self._local, "tables", None)
            if own is None:
                return real(table, key)
            return own.setdefault((id(table), key), threading.Lock())

        monkeypatch.setattr(flow_guard, "_named_lock", named)

    def name(self):
        return getattr(self._local, "name", None)

    def start(self, results, name, call):
        def as_process():
            self._local.tables, self._local.name = {}, name
            return call()
        return _thread(results, name, as_process)


def _pause_cleanup(monkeypatch, procs, process, ending, inside=False):
    """Hold ``process``'s cleanup whose label ends with ``ending``: after its commit / rollback and before it takes
    the durable lock again (``inside=False``), or holding that lock before it checks and removes (``inside=True``)."""
    from modules.flow_gate.graph_workflow.services import flow_guard

    pause, real = _Pause(), flow_guard.run_cleanup

    def run_cleanup(key, body, what="cleanup"):
        if procs.name() == process and what.endswith(ending):
            if inside:
                return real(key, pause.wrap(body), what)
            return pause.wrap(real)(key, body, what)
        return real(key, body, what)

    monkeypatch.setattr(flow_guard, "run_cleanup", run_cleanup)
    return pause


def _in_guard(monkeypatch, procs, pauses=(), fails=()):
    """Inside the conversion's guard, after the body and the attachments are staged (the row reassignment): hold
    the processes in ``pauses`` there, and make those in ``fails`` fail right after it (the whole transaction rolls
    back)."""
    from modules.flow_gate.documents.attachments import conversion as attachment_conversion

    held, real = {name: _Pause() for name in pauses}, attachment_conversion.reassign_attachment_rows

    def reassign(*args, **kwargs):
        name = procs.name()
        if name in held:
            return held[name].wrap(real)(*args, **kwargs)
        real(*args, **kwargs)
        if name in fails:
            raise RuntimeError(f"process {name} fails after staging")

    monkeypatch.setattr(attachment_conversion, "reassign_attachment_rows", reassign)
    return held


def _orphans(path: Path):
    return sorted(path.parent.glob(path.name + ".orphan-*"))


def _readable(doc_id):
    from modules.flow_gate.db import documents as db_docs
    from modules.flow_gate.storage.body import body_read_state

    return body_read_state(db_docs.get_by_id(doc_id))[0] == "readable"


def _attachments_read(env, doc_id):
    return {r["filename"]: (room_of(doc_id) / r["filename"]).read_bytes() for r in attachment_rows(env, doc_id)}


def test_a_cleanup_removes_only_the_file_it_owns(tmp_path):
    """The identity is the file, not the path: a file moved aside keeps it, a file staged at the path has another."""
    from modules.flow_gate.db import connection as conn_mod
    from modules.flow_gate.storage.files import file_identity, is_same_file_as, remove_owned_file

    path = tmp_path / "0001-R_requirement.md"
    path.write_bytes(b"mine")
    mine = file_identity(path)
    aside = path.with_name(path.name + ".orphan-1")
    path.rename(aside)
    path.write_bytes(b"mine")                                           # same bytes, another file
    assert not is_same_file_as(path, mine) and is_same_file_as(aside, mine)
    assert not remove_owned_file(path, mine) and path.read_bytes() == b"mine"
    assert remove_owned_file(aside, mine) and not aside.exists()
    assert not remove_owned_file(tmp_path / "missing.md", mine)

    queued: list = []
    conn_mod._tx_local.after_rollback = queued                           # what an open transaction would hold
    conn_mod._tx_local.txn = object()
    try:
        conn_mod.track_new_file(path, still_ours=lambda p: is_same_file_as(p, mine))
    finally:
        conn_mod._tx_local.txn = None
        conn_mod._tx_local.after_rollback = []
    [remove] = queued
    remove()
    assert path.read_bytes() == b"mine"                                 # the path holds someone else's file


@pytest.mark.parametrize("dialect,statement", [
    (1, "UPDATE graph_flow_guards SET guard_seq = guard_seq WHERE group_id = ?"),
    (2, "SELECT group_id FROM graph_flow_guards WHERE group_id = ? FOR UPDATE"),
    (3, "SELECT group_id FROM graph_flow_guards WHERE group_id = ? FOR UPDATE"),
], ids=["sqlite", "mysql", "postgresql"])
def test_a_cleanup_takes_the_durable_lock_without_a_bump(monkeypatch, dialect, statement):
    """A cleanup re-takes the Flow guard row (no ``guard_seq`` change) or the document row, as its first statement."""
    from modules.flow_gate.db import connection as conn_mod
    from modules.flow_gate.graph_workflow.services import flow_guard

    seen: list = []

    class _Store:
        def __init__(self) -> None:
            self.dialect = dialect

        @contextmanager
        def transaction(self):
            seen.append("begin")
            yield self
            seen.append("commit")

        def _execute_affected(self, sql, params=None):
            seen.append((sql, params))
            return 1

        def _fetch_one(self, sql, params=None):
            seen.append((sql, params))
            return {}

    monkeypatch.setattr(conn_mod, "STORE", _Store())
    assert flow_guard.run_cleanup(("flow", FLOW), lambda: seen.append("body") or "done") == "done"
    assert seen == ["begin", (statement, [FLOW]), "body", "commit"]
    seen.clear()
    flow_guard.run_cleanup(("document", SOLO_B), lambda: seen.append("body"))
    row_lock = ("UPDATE documents SET updated_at = updated_at WHERE doc_id = ?" if dialect == 1
                else "SELECT doc_id FROM documents WHERE doc_id = ? FOR UPDATE")
    assert seen == ["begin", (row_lock, [SOLO_B]), "body", "commit"]


@pytest.mark.parametrize("flow", [True, False], ids=["flow_guard", "row_lock"])
def test_a_commit_cleanup_never_removes_what_another_process_staged_next(env, monkeypatch, flow):
    """Process A's R→B has committed and its cleanup of the old R body has not run yet when process B (sharing no
    in-process lock) converts B→R and stages a new R body at that very path. A's cleanup waits on the durable lock
    while B is inside its guard, then keeps B's file; B's row reads its body and its attachment, and A removed only
    the old attachment it owned."""
    from modules.flow_gate.documents.attachments import delete_attachment

    procs = _Processes(monkeypatch)
    old_id, new_id = make_root(env, flow)
    body_path = env["storage"] / row(env, old_id)["file_path"]
    original = body_path.read_bytes()
    add_attachment(env, old_id, "a.txt", b"alpha")
    old_room = room_of(old_id)
    a_cleanup = _pause_cleanup(monkeypatch, procs, "A", body_path.name)
    held = _in_guard(monkeypatch, procs, pauses=["B"])
    results: dict = {}

    a = procs.start(results, "A", lambda: convert(old_id))
    assert a_cleanup.inside.wait(30)
    assert row(env, old_id) is None and row(env, new_id) is not None and body_path.is_file()   # committed, not cleaned
    # process C: the B document drops the moved a.txt and gets b.txt, so B's B→R stages into the old room as well
    c = procs.start(results, "C", lambda: (delete_attachment(new_id, "a.txt", {"user_id": USER}),
                                         _upload(new_id, "b.txt", b"bravo")))
    _joined(c)
    assert results["C"][0] == "ok", results["C"]
    b = procs.start(results, "B", lambda: convert(new_id, "R"))
    assert held["B"].inside.wait(30)
    assert [p.read_bytes() for p in _orphans(body_path)] == [original]   # B moved A's old body aside, staged its own
    a_cleanup.release.set()
    time.sleep(1.5)
    assert a.is_alive() and body_path.is_file()                      # A's cleanup waits for B's guard
    held["B"].release.set()
    _joined(a, b)

    assert results["A"][0] == "ok" and results["B"][0] == "ok", results
    assert row(env, new_id) is None and row(env, old_id) is not None
    assert body_path.read_bytes() == original and _readable(old_id)  # B's row points at a readable body
    assert _attachments_read(env, old_id) == {"b.txt": b"bravo"}
    assert not (old_room / "a.txt").exists()                         # A removed the old file it owned, nothing else


@pytest.mark.parametrize("flow", [True, False], ids=["flow_guard", "row_lock"])
def test_a_rollback_cleanup_never_removes_what_a_retry_of_another_process_staged(env, monkeypatch, flow):
    """Process A's R→B rolls back after staging; before its cleanup removes the staged B body, process B retries the
    same conversion and stages a new B body at that path. A's cleanup waits on the durable lock while B is inside
    its guard, then keeps B's file; B's row reads its body and its attachment."""
    procs = _Processes(monkeypatch)
    old_id, new_id = make_root(env, flow)
    new_body = (env["storage"] / row(env, old_id)["file_path"]).with_name("0001-B_requirement.md")
    add_attachment(env, old_id, "a.txt", b"alpha")
    a_cleanup = _pause_cleanup(monkeypatch, procs, "A", new_body.name)
    held = _in_guard(monkeypatch, procs, pauses=["B"], fails=["A"])
    results: dict = {}

    a = procs.start(results, "A", lambda: convert(old_id))
    assert a_cleanup.inside.wait(30)
    assert row(env, new_id) is None and row(env, old_id) is not None and new_body.is_file()    # rolled back, not cleaned
    b = procs.start(results, "B", lambda: convert(old_id))
    assert held["B"].inside.wait(30)
    assert len(_orphans(new_body)) == 1                              # B moved A's staged body aside, staged its own
    a_cleanup.release.set()
    time.sleep(1.5)
    assert a.is_alive() and new_body.is_file()                       # A's cleanup waits for B's guard
    held["B"].release.set()
    _joined(a, b)

    assert results["A"][0] == "error" and results["B"][0] == "ok", results
    assert row(env, old_id) is None and row(env, new_id)["file_path"].endswith(new_body.name)
    assert new_body.read_bytes() == b"---\ntype: B\n---\nbody\n" and _readable(new_id)
    assert _attachments_read(env, new_id) == {"a.txt": b"alpha"}


@pytest.mark.parametrize("flow", [True, False], ids=["flow_guard", "row_lock"])
def test_a_conversion_of_another_process_waits_for_a_cleanup_holding_the_durable_lock(env, monkeypatch, flow):
    """The other order: A's cleanup holds the durable lock (between its check and its removal) when process B starts
    B→R. B stages nothing until A has removed its old body, so no check of A's ever sees a file of B's."""
    procs = _Processes(monkeypatch)
    old_id, new_id = make_root(env, flow)
    body_path = env["storage"] / row(env, old_id)["file_path"]
    original = body_path.read_bytes()
    a_cleanup = _pause_cleanup(monkeypatch, procs, "A", body_path.name, inside=True)
    results: dict = {}

    a = procs.start(results, "A", lambda: convert(old_id))
    assert a_cleanup.inside.wait(30)
    b = procs.start(results, "B", lambda: convert(new_id, "R"))
    time.sleep(1.5)
    assert b.is_alive() and row(env, new_id) is not None             # B waits on the durable lock
    assert body_path.read_bytes() == original and _orphans(body_path) == []
    a_cleanup.release.set()
    _joined(a, b)

    assert results["A"][0] == "ok" and results["B"][0] == "ok", results
    assert row(env, new_id) is None and body_path.read_bytes() == original and _readable(old_id)
    assert _orphans(body_path) == []                                 # A removed its old body before B staged
