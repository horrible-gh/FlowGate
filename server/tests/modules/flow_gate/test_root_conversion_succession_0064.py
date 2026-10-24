"""flowgate.v02.0064 T#4 — R↔B succession conversion, the conversion preview and the live root.

L0005 2.5 / 2.9 / 2.10, P0006 §3.1 / §3.3~§3.5, DB0007 §2.3 / §2.4 / §3.3 / §4 Q8. The same real SQLite DB
(every migration) and temp storage root as the number swap suite (``test_root_conversion_number_swap_0064``,
whose fixture and helpers this file reuses): a root with child documents, or a member of a succession chain,
is succeeded by a new document of the other type. The original keeps its row, body, attachments, children,
history and only gets ``superseded_by`` / ``meta.converted_to`` / ``updated_at``; every failure leaves all of
it, the number counter included, exactly as it was. The judgement is a pure read and is what the console
document read carries with ``include=root_conversion``.
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from pathlib import Path

import pytest

os.environ.setdefault("ALLOWED_ORIGIN", "http://localhost")     # config.Settings, for the workflow routes
os.environ.setdefault("CONTEXT", "/flowgate")
os.environ.setdefault("DB_TYPE", "sqlite")

from test_root_conversion_number_swap_0064 import (  # noqa: F401 — fixtures (env, client) and helpers
    BODY_REL, FLOW, GROUP_DIR, ORIGINAL_BODY, ROOT, ROOT_B, USER, _body, _EOLS, _failing_commit, _sql,
    _unsafe_path, add_attachment, attachment_rows, client, convert, counters, env, files_under, make_doc,
    orphan_attachment, preview, refused, room_of, row, snapshot,
)

CHILDREN = [f"{FLOW}.0002-N", f"{FLOW}.0003-NR", f"{FLOW}.0004-D"]
B_REL = f"{GROUP_DIR}/0001-B_requirement.md"
NOTE_RB = f"R->B ({ROOT} -> {ROOT_B}) mode=succession children=3"


def child(env, doc_id, *, parent=ROOT):
    seq = int(doc_id.rsplit(".", 1)[1].split("-")[0])
    return make_doc(env, doc_id=doc_id, seq=seq, body=None, rel="", triggered_by=parent, target_id=parent)


def with_children(env, n=3, body=ORIGINAL_BODY, **extra):
    make_doc(env, body=body, **extra)
    for doc_id in CHILDREN[:n]:
        child(env, doc_id)


def counter_row(env, last_seq):
    _sql(env["db"], "INSERT INTO id_counter (project_id, module, group_seq, sub_group_seq, series, type_code, "
                    "last_seq, updated_at) VALUES ('flowgate', 'v02', ?, '', '', '__doc__', ?, 't')", (FLOW, last_seq))


def doc_counter(env):
    rows = _sql(env["db"], "SELECT last_seq FROM id_counter WHERE group_seq = ? AND type_code = '__doc__'", (FLOW,))
    return rows[0]["last_seq"] if rows else None


def meta_of(env, doc_id):
    return json.loads(row(env, doc_id)["meta"] or "{}")


def without(rec, *keys):
    return {k: v for k, v in rec.items() if k not in keys}


def events_of(env, event_type):
    return _sql(env["db"], "SELECT * FROM workflow_events WHERE event_type = ? ORDER BY id", (event_type,))


@pytest.fixture()
def counting(monkeypatch):
    """Calls of the number reservation, the two stages and the original-cleanup paths (V-B3-4c, V-B3-13)."""
    from modules.flow_gate.documents import root_conversion
    from modules.flow_gate.documents.attachments import conversion as attachment_conversion
    from modules.flow_gate.numbering import numbering_service

    calls = {"reserve": 0, "stage_body": 0, "stage_attachments": 0, "copy_files": 0, "finish_room": 0,
             "cleanup_room": 0, "finish_body": 0}

    def wrap(owner, name, key):
        real = getattr(owner, name)

        def counted(*a, **k):
            calls[key] += 1
            return real(*a, **k)
        monkeypatch.setattr(owner, name, counted)

    wrap(numbering_service, "reserve_document", "reserve")
    wrap(root_conversion, "stage_converted_file", "stage_body")
    wrap(attachment_conversion, "stage_attachment_room", "stage_attachments")
    wrap(attachment_conversion, "finish_attachment_room", "finish_room")
    wrap(attachment_conversion, "cleanup_old_attachment_room", "cleanup_room")
    wrap(root_conversion, "finish_converted_file", "finish_body")
    real_copy = attachment_conversion.copy_attachments

    def copy_without_files(*a, **k):                     # copy_attachments must not write a file itself
        real_write = attachment_conversion.write_new_file

        def write(*wa, **wk):
            calls["copy_files"] += 1
            return real_write(*wa, **wk)
        monkeypatch.setattr(attachment_conversion, "write_new_file", write)
        try:
            return real_copy(*a, **k)
        finally:
            monkeypatch.setattr(attachment_conversion, "write_new_file", real_write)
    monkeypatch.setattr(attachment_conversion, "copy_attachments", copy_without_files)
    return calls


def _seed_reference(env, table, column, value):
    """One row of ``table`` whose ``column`` holds ``value``; NOT NULL columns get placeholders (FK off)."""
    conn = sqlite3.connect(env["db"])
    try:
        info = conn.execute(f"PRAGMA table_info({table})").fetchall()
        if not info:
            return False
        values = {}
        for _cid, name, ctype, notnull, default, pk in info:
            if name == column:
                values[name] = value
            elif pk and "INT" in (ctype or "").upper():
                continue
            elif notnull and default is None:
                values[name] = 1 if "INT" in (ctype or "").upper() else f"x_{name}"
        cols = ", ".join(values)
        conn.execute(f"INSERT INTO {table} ({cols}) VALUES ({', '.join('?' * len(values))})", list(values.values()))
        conn.commit()
        return True
    except sqlite3.DatabaseError:
        return False
    finally:
        conn.close()


NEW_EVENT_TYPES = ("doc_type_succeeded", "doc_type_converted", "doc_review_inherited")


def references(env, doc_id):
    """Every row anywhere that stores ``doc_id`` (``_DOC_REFERENCE_COLUMNS``), the conversion's own events aside."""
    from modules.flow_gate.documents.document_service import _DOC_REFERENCE_COLUMNS

    out = {}
    for table, column in _DOC_REFERENCE_COLUMNS:
        try:
            rows = _sql(env["db"], f"SELECT * FROM {table} WHERE {column} = ?", (doc_id,))
        except sqlite3.DatabaseError:
            continue
        out[(table, column)] = [r for r in rows if r.get("event_type") not in NEW_EVENT_TYPES]
    return out


# ── succession: the original keeps everything (V-B3-1, V-B3-2, V-P-11, DB0007 §2.3 / §2.4) ───────────

def test_a_root_with_children_is_succeeded_and_keeps_everything(env):
    from modules.flow_gate.db import events as db_doc_events
    from modules.flow_gate.documents.document_service import _DOC_REFERENCE_COLUMNS

    body = _body("crlf", True, True, False)
    with_children(env, body=body, status="open")
    _sql(env["db"], "UPDATE documents SET doc_review_status = 'approved', workflow_steps = '[\"N\"]', "
                    "rejection_history = '[{\"reason\": \"r\"}]', revision_no = 2, "
                    "updated_at = '2026-01-01T00:00:00+09:00' WHERE doc_id = ?", (ROOT,))
    db_doc_events.insert_event(ROOT, "created", memo_file="m.md")
    skip = ("documents", "events", "workflow_sequences", "workflow_sequence_items")   # a sequence = decided
    seeded = [(t, c) for t, c in _DOC_REFERENCE_COLUMNS if t not in skip and _seed_reference(env, t, c, ROOT)]
    assert len(seeded) >= 5, seeded                               # reviews, questions, revisions, tokens, runs...
    original = row(env, ROOT)
    kids = {d: row(env, d) for d in CHILDREN}
    refs = references(env, ROOT)
    before_counters = counters(env)

    result = convert()

    assert result["conversion"] == {
        "mode": "succession", "old_doc_id": ROOT, "new_doc_id": ROOT_B, "old_type": "R", "new_type": "B",
        "child_count": 3, "attachments": {"count": 0, "ghosts": 0}, "draft_node_keys": []}
    after = row(env, ROOT)
    assert without(after, "superseded_by", "meta", "updated_at") == without(original, "superseded_by", "meta",
                                                                            "updated_at")
    assert after["superseded_by"] == ROOT_B and after["updated_at"] != original["updated_at"]
    mark = meta_of(env, ROOT)["converted_to"]
    assert (mark["doc_id"], mark["mode"], mark["by"]) == (ROOT_B, "succession", USER) and mark["at"]
    assert {d: row(env, d) for d in CHILDREN} == kids                  # children still point at the original
    assert references(env, ROOT) == refs                               # reviews, events, revisions, tokens, Q&A, runs
    assert (env["storage"] / BODY_REL).read_bytes() == body            # the original body, byte for byte
    assert (env["storage"] / B_REL).read_bytes() == _body("crlf", True, True, False, "B", "0001-B")   # V-B1b-3
    assert counters(env) == before_counters                            # the same seq: no reservation

    new = row(env, ROOT_B)
    for col in ("title", "owner_id", "status", "doc_review_status", "workflow_steps", "project_id", "module",
                "group_id", "branch"):
        assert new[col] == original[col], col
    assert (new["type_code"], new["seq"], new["revision_no"], new["superseded_by"]) == ("B", 1, 0, None)
    assert (new["file_path"], new["filename"]) == (B_REL, "0001-B_requirement.md")
    assert new["rejection_history"] in ("[]", None) and new["rejection_reason"] is None
    came = json.loads(new["meta"])["converted_from"]
    assert (came["doc_id"], came["mode"], came["by"], came["children"]) == (ROOT, "succession", USER, 3)

    [succeeded] = events_of(env, "doc_type_succeeded")
    [converted] = events_of(env, "doc_type_converted")
    assert (succeeded["document_id"], succeeded["metadata"]) == (original["id"], NOTE_RB)
    assert (converted["document_id"], converted["metadata"]) == (new["id"], NOTE_RB)
    [inherited] = _sql(env["db"], "SELECT * FROM events WHERE event_type = 'doc_review_inherited'")
    assert (inherited["doc_id"], inherited["related_doc_id"]) == (ROOT_B, ROOT)
    assert json.loads(inherited["note"]) == {"from": ROOT, "status": "open", "doc_review_status": "approved"}


def test_the_new_card_document_is_not_an_empty_draft(env):
    """L0005 2.9.2 / 1.4: the copied body is no empty draft — ``graph_card.created_empty`` is false on the new row."""
    meta = {"graph_card": {"node_key": "n_req", "slot": "requirement", "created_empty": True}, "keep": [1, {"a": 2}]}
    with_children(env, n=1, meta=json.dumps(meta))
    convert()
    new_meta = meta_of(env, ROOT_B)
    assert new_meta["graph_card"] == {"node_key": "n_req", "slot": "requirement", "created_empty": False}
    assert new_meta["keep"] == [1, {"a": 2}]
    old_meta = meta_of(env, ROOT)
    assert old_meta["graph_card"]["created_empty"] is True and "converted_from" not in old_meta


@pytest.mark.parametrize("eol", sorted(_EOLS))
@pytest.mark.parametrize("bom", [False, True])
@pytest.mark.parametrize("fields", [True, False])
@pytest.mark.parametrize("final_newline", [True, False])
def test_succession_changes_only_the_identity_values(env, eol, bom, fields, final_newline):
    """V-B1b-3: the original bytes stay; the copy differs in the ``type`` / ``doc_number`` values only."""
    original = _body(eol, bom, fields, final_newline)
    with_children(env, n=1, body=original)
    convert()
    assert (env["storage"] / BODY_REL).read_bytes() == original
    expected = _body(eol, bom, fields, final_newline, "B", "0001-B") if fields else original
    assert (env["storage"] / B_REL).read_bytes() == expected


@pytest.mark.parametrize("line,expected", [
    (b"type: 'R'", b"type: 'B'"),
    (b'doc_number: "0001-R"', b'doc_number: "0001-B"'),
    (b"doc_number: R0001", b"doc_number: B0001"),
    (b"type: R  # \xeb\xa9\x94\xeb\xaa\xa8", b"type: B  # \xeb\xa9\x94\xeb\xaa\xa8"),
])
def test_succession_keeps_quoted_compact_and_commented_styles(env, line, expected):
    """V-B1b-5 (succession side)."""
    body = b"---\n" + line + b"\ntitle: x\n---\nbody " + line + b"\n"
    with_children(env, n=1, body=body)
    convert()
    assert (env["storage"] / B_REL).read_bytes() == body.replace(line, expected, 1)
    assert (env["storage"] / BODY_REL).read_bytes() == body


@pytest.mark.parametrize("body,reason,field", [
    (b"---\ntype: R\ntype: R\n---\n", "duplicate_key", "type"),
    (b"---\ndoc_number: |\n  0001-R\n---\n", "unsupported_scalar", "doc_number"),
    (b"---\ndoc_number: 0009-R\n---\n", "identity_mismatch", "doc_number"),
    (b"---\ntype: 'R\n---\n", "unsupported_scalar", "type"),
    (b"---\n", "missing_end_delimiter", None),
    (b"---\ntype: R\ndoc_number: 0001-R\nbody without an end\n", "missing_end_delimiter", None),
    (b"\xef\xbb\xbf---\r\ntype: R\r\nbody\r\n", "missing_end_delimiter", None),
])
def test_succession_refuses_unsafe_identity_fields_in_preview_and_execution(env, body, reason, field):
    """V-B1b-6 / V-B1b-8 (succession side): refused, nothing changes, the counter neither."""
    with_children(env, n=2, body=body)
    before = snapshot(env)
    judged = preview()
    assert (judged.kind, judged.code, judged.extra) == ("refused", "front_matter_identity_unsafe",
                                                        {"reason": reason, "field": field})
    payload = refused("front_matter_identity_unsafe", convert)
    assert without(payload, "detail") == {"error": "front_matter_identity_unsafe", "doc_id": ROOT, "reason": reason,
                                          **({"field": field} if field else {})}
    assert snapshot(env) == before


@pytest.mark.parametrize("body", [b"---\ntype: R\n...\nbody\n", b"no front matter\n", b""])
def test_succession_of_terminated_or_absent_front_matter(env, body):
    """V-B1b-8: a terminated front matter changes its values only; without one every byte is copied."""
    with_children(env, n=1, body=body)
    convert()
    assert (env["storage"] / B_REL).read_bytes() == body.replace(b"type: R", b"type: B")


# ── attachments: copied, ghosts kept, originals untouched (V-B3-4, 4a~4f) ───────────────────────────

def test_attachments_are_copied_and_ghosts_stay_ghosts(env, counting):
    from modules.flow_gate.db import documents as db_docs
    from modules.flow_gate.documents.attachments import AttachmentError, delete_attachment
    from modules.flow_gate.documents.attachments.locator import resolve_registered_attachment

    with_children(env)
    add_attachment(env, ROOT, "c.txt", b"gamma", uploaded_at="2026-10-01T00:00:01Z")     # listed out of name order
    add_attachment(env, ROOT, "b.txt", None, uploaded_at="2026-10-01T00:00:02Z")         # ghost
    add_attachment(env, ROOT, "a.txt", b"alpha", uploaded_at="2026-10-01T00:00:03Z")
    old_rows = attachment_rows(env, ROOT)
    old_room = room_of(ROOT)
    old_files = {p: (old_room / p).read_bytes() for p in files_under(old_room)}

    result = convert()

    assert result["conversion"]["attachments"] == {"count": 3, "ghosts": 1}
    assert attachment_rows(env, ROOT) == old_rows                                 # original rows unchanged
    assert {p: (old_room / p).read_bytes() for p in files_under(old_room)} == old_files
    new_room = room_of(ROOT_B)
    assert new_room.name == "0001-B" and files_under(new_room) == ["a.txt", "c.txt"]
    new_doc = db_docs.get_by_id(ROOT_B)
    new_rows = {r["filename"]: r for r in attachment_rows(env, ROOT_B)}
    assert sorted(new_rows) == ["a.txt", "b.txt", "c.txt"]
    for old in old_rows:
        new = new_rows[old["filename"]]
        for col in ("original_filename", "size", "content_type", "content_sha256", "uploaded_by", "uploaded_at",
                    "is_legacy_migrated"):
            assert new[col] == old[col], col
        assert new["id"] != old["id"] and new["file_path"] == f"{GROUP_DIR}/0001-B/{old['filename']}"
    for name in ("a.txt", "c.txt"):                                                # J1~J7 pass in the new room
        _row, path = resolve_registered_attachment(new_doc, name)
        assert path.parent == new_room.resolve()
        assert hashlib.sha256(path.read_bytes()).hexdigest() == new_rows[name]["content_sha256"]
    with pytest.raises(AttachmentError) as ghost:
        resolve_registered_attachment(new_doc, "b.txt")
    assert (ghost.value.status_code, ghost.value.code) == (404, "ATTACHMENT_NOT_FOUND")
    assert delete_attachment(ROOT_B, "b.txt", {"user_id": USER})["metadata_deleted"] is True
    assert sorted(r["filename"] for r in attachment_rows(env, ROOT_B)) == ["a.txt", "c.txt"]
    assert attachment_rows(env, ROOT) == old_rows                                 # the original ghost stays
    assert (counting["stage_attachments"], counting["copy_files"]) == (1, 0)
    assert (counting["finish_room"], counting["cleanup_room"]) == (0, 0)          # no original cleanup queued
    [converted] = events_of(env, "doc_type_converted")
    assert converted["metadata"] == NOTE_RB                                       # no attachment counts (P0006 §3.4)


def test_a_reserved_ghost_of_the_new_document_keeps_the_delete_refusal(env):
    from modules.flow_gate.documents.attachments import AttachmentError, delete_attachment

    with_children(env, n=1)
    add_attachment(env, ROOT, "__wp_pre_instruction__T-1__x.txt", None)
    convert()
    with pytest.raises(AttachmentError) as exc:
        delete_attachment(ROOT_B, "__wp_pre_instruction__T-1__x.txt", {"user_id": USER})
    assert exc.value.code == "DOCUMENT_NOT_MUTABLE"
    assert len(attachment_rows(env, ROOT_B)) == 1 and len(attachment_rows(env, ROOT)) == 1


@pytest.mark.parametrize("kind", ["dotdot", "absolute", "other_room", "symlink"])
@pytest.mark.parametrize("position", [0, 1, 2])
def test_one_unsafe_row_refuses_the_whole_succession(env, tmp_path, counting, kind, position):
    """V-B3-4a: every row is checked before anything is copied; nothing changes."""
    with_children(env)
    stamps = ["2026-10-01T00:00:01Z", "2026-10-01T00:00:02Z", "2026-10-01T00:00:03Z"]
    unsafe_at = stamps.pop(position)
    add_attachment(env, ROOT, "a.txt", b"alpha", uploaded_at=stamps[0])
    add_attachment(env, ROOT, "ghost.txt", None, uploaded_at=stamps[1])
    add_attachment(env, ROOT, "x.txt", None, file_path=_unsafe_path(env, kind, tmp_path), uploaded_at=unsafe_at)
    before = snapshot(env)
    payload = refused("attachment_path_unsafe", convert)
    assert payload["filename"] == "x.txt" and payload["doc_id"] == ROOT and payload["reason"]
    assert snapshot(env) == before
    assert not room_of(ROOT).with_name("0001-B").exists() and row(env, ROOT_B) is None
    assert (counting["finish_room"], counting["cleanup_room"]) == (0, 0)


@pytest.mark.parametrize("change", ["add", "delete", "same_name_path", "same_name_sha", "same_name_size",
                                    "lost", "first_of_empty"])
def test_rows_changed_after_staging_refuse_the_succession(env, monkeypatch, change):
    """V-B3-4b: any change of the source rows between staging and the first INSERT rolls everything back."""
    from modules.flow_gate.db.connection import get_store
    from modules.flow_gate.documents.attachments import conversion as attachment_conversion

    with_children(env)
    if change != "first_of_empty":
        add_attachment(env, ROOT, "a.txt", b"alpha", uploaded_at="2026-10-01T00:00:01Z")
        add_attachment(env, ROOT, "ghost.txt", None, uploaded_at="2026-10-01T00:00:02Z")
    before = snapshot(env)
    real = attachment_conversion.copy_attachments
    columns = {"same_name_path": "file_path = 'documents/x/a.txt'", "same_name_sha": "content_sha256 = '" + "f" * 64 + "'",
               "same_name_size": "size = 99"}

    def changed(doc, new_id, stage, mode):
        store = get_store()
        if change in ("add", "first_of_empty"):
            add_attachment(env, ROOT, "late.txt", b"late")
        elif change in ("delete", "lost"):
            store._execute("DELETE FROM attachments WHERE doc_id = ? AND filename = ?",
                           [ROOT, "a.txt" if change == "delete" else "ghost.txt"])
        else:
            store._execute(f"UPDATE attachments SET {columns[change]} WHERE doc_id = ? AND filename = 'a.txt'", [ROOT])
        return real(doc, new_id, stage, mode)

    monkeypatch.setattr(attachment_conversion, "copy_attachments", changed)
    refused("attachments_changed", convert)
    after = snapshot(env)
    for path in [p for p in after["files"] if p.endswith("late.txt")]:   # written outside the transaction
        after["files"].pop(path)
    assert after == before
    assert not (env["storage"] / GROUP_DIR / "0001-B").exists()


def test_an_order_only_difference_lets_the_succession_through(env, monkeypatch):
    from modules.flow_gate.documents.attachments import conversion as attachment_conversion

    with_children(env)
    add_attachment(env, ROOT, "a.txt", b"alpha", uploaded_at="2026-10-01T00:00:01Z")
    add_attachment(env, ROOT, "ghost.txt", None, uploaded_at="2026-10-01T00:00:02Z")
    real = attachment_conversion.registry_list
    calls = {"n": 0}

    def shuffled(doc_id):
        calls["n"] += 1
        rows = list(real(doc_id))
        return rows[::-1] if calls["n"] % 2 == 0 else rows

    monkeypatch.setattr(attachment_conversion, "registry_list", shuffled)
    assert convert()["conversion"]["attachments"] == {"count": 2, "ghosts": 1}


@pytest.mark.parametrize("what", ["file", "ghost", "symlink"])
def test_something_in_the_new_room_refuses_the_succession(env, tmp_path, what):
    """V-B3-4b: a file or a link already at a copy's target is ``attachment_target_exists``."""
    with_children(env)
    add_attachment(env, ROOT, "a.txt", None if what == "ghost" else b"alpha")
    squatter = room_of(ROOT).with_name("0001-B") / "a.txt"
    squatter.parent.mkdir(parents=True)
    if what == "symlink":
        target = tmp_path / "elsewhere.txt"
        target.write_bytes(b"x")
        try:
            squatter.symlink_to(target)
        except (OSError, NotImplementedError):
            pytest.skip("symlinks unavailable")
    else:
        squatter.write_bytes(b"someone else")
    before = snapshot(env)
    assert refused("attachment_target_exists", convert)["filename"] == "a.txt"
    assert snapshot(env) == before


@pytest.mark.parametrize("where", ["copy", "rows", "lines", "draft", "commit", "events"])
def test_an_injected_failure_rolls_the_succession_back(env, monkeypatch, counting, where):
    """V-B3-4c: every failure keeps the original row, body, attachments (ghost too) and counter; the new files and
    room go; the original attachments are never unlinked nor queued for cleanup."""
    from modules.flow_gate.db import _SqliteDbAdapter
    from modules.flow_gate.db import workflow_events
    from modules.flow_gate.documents.attachments import conversion as attachment_conversion
    from modules.flow_gate.graph_workflow.services import card_document_ports, root_conversion_cards

    with_children(env)
    add_attachment(env, ROOT, "a.txt", b"alpha", uploaded_at="2026-10-01T00:00:01Z")
    add_attachment(env, ROOT, "ghost.txt", None, uploaded_at="2026-10-01T00:00:02Z")
    before = snapshot(env)
    real_copy = attachment_conversion.copy_attachments

    def boom(*_a, **_k):
        raise RuntimeError(f"injected at {where}")

    def after_rows(*a, **k):
        real_copy(*a, **k)
        raise RuntimeError("injected after the rows were copied")

    with monkeypatch.context() as m:
        if where == "copy":
            m.setattr(attachment_conversion, "copy_attachments", boom)
        elif where == "rows":
            m.setattr(attachment_conversion, "copy_attachments", after_rows)
        elif where == "lines":
            card_document_ports.register(card_document_ports.ON_ROOT_CONVERTED, boom)
        elif where == "draft":
            card_document_ports.register(card_document_ports.ON_ROOT_CONVERTED, lambda *a: ["n1"])
            m.setattr(root_conversion_cards, "sync_draft_cards", boom)
        elif where == "events":
            m.setattr(workflow_events, "create", boom)
        else:
            m.setattr(_SqliteDbAdapter, "begin_transaction", _failing_commit)
        with pytest.raises(Exception):
            convert()
    assert snapshot(env) == before                                   # rows, files, counter, events, draft
    assert not (env["storage"] / GROUP_DIR / "0001-B").exists() and not (env["storage"] / B_REL).exists()
    assert (counting["finish_room"], counting["cleanup_room"]) == (0, 0)


@pytest.mark.parametrize("source", ["same_name", "other_name", "none"])
@pytest.mark.parametrize("target", ["file", "ghost", "unsafe"])
def test_rows_on_the_same_seq_target_refuse_the_succession(env, counting, source, target):
    """V-B3-4d: ``0001-B`` with children succeeds to ``0001-R`` (no document), which holds an orphan row."""
    make_doc(env, doc_id=ROOT_B, body=b"---\ntype: B\n---\n", rel=B_REL)
    child(env, CHILDREN[0], parent=ROOT_B)
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
    assert (judged.kind, judged.code, judged.extra) == ("refused", "attachment_target_rows_exist",
                                                        {"target_doc_id": ROOT, "mode": "succession"})
    payload = refused("attachment_target_rows_exist", lambda: convert(ROOT_B, "R"))
    assert without(payload, "detail") == {"error": "attachment_target_rows_exist", "doc_id": ROOT_B,
                                          "target_doc_id": ROOT, "mode": "succession"}
    assert snapshot(env) == before and row(env, ROOT) is None
    assert (counting["reserve"], counting["stage_body"], counting["stage_attachments"]) == (0, 0, 0)


def _first_succession(env):
    """``0001-R`` (children 0002, 0003) -> ``0001-B``: ``0001-B`` is a chain member whose same seq is taken."""
    with_children(env, n=2)
    convert()
    return ROOT_B


@pytest.mark.parametrize("with_source", [True, False])
def test_rows_on_the_reserved_target_refuse_and_restore_the_reservation(env, counting, with_source):
    """V-B3-4e: the preview cannot know the number (exact false, nothing checked, counter unchanged); the execution
    reserves once, finds the orphan row on that id and rolls the reservation back."""
    _first_succession(env)
    if with_source:
        add_attachment(env, ROOT_B, "a.txt", b"alpha")
    expected = f"{FLOW}.0004-R"
    orphan_attachment(env, expected, "x.txt", f"{GROUP_DIR}/0004-R/x.txt")
    before = snapshot(env)
    for _ in range(10):
        judged = preview(ROOT_B, "R")
        assert (judged.kind, judged.new_id, judged.exact) == ("succession", expected, False)
    assert snapshot(env) == before
    counting["reserve"] = 0
    payload = refused("attachment_target_rows_exist", lambda: convert(ROOT_B, "R"))
    assert (payload["target_doc_id"], payload["mode"]) == (expected, "succession")
    assert counting["reserve"] == 1                                   # reserved once, never again
    assert snapshot(env) == before                                    # the counter is back as well
    assert row(env, expected) is None


@pytest.mark.parametrize("with_source", [True, False])
@pytest.mark.parametrize("inject", [True, False])
def test_target_rows_appearing_before_the_first_insert_are_caught(env, monkeypatch, with_source, inject):
    """V-B3-4f: the target is checked again right before the copied rows are registered (empty moves too)."""
    from modules.flow_gate.db.connection import get_store
    from modules.flow_gate.documents.attachments import conversion as attachment_conversion

    with_children(env)
    if with_source:
        add_attachment(env, ROOT, "a.txt", b"alpha", uploaded_at="2026-10-01T00:00:01Z")
        add_attachment(env, ROOT, "ghost.txt", None, uploaded_at="2026-10-01T00:00:02Z")
    before = snapshot(env)
    real = attachment_conversion.copy_attachments

    def injected(doc, new_id, stage, mode):                   # the new row exists here, so the FK holds
        get_store()._execute(
            "INSERT INTO attachments (doc_id, original_filename, filename, file_path, size, content_type, "
            "content_sha256, uploaded_by, uploaded_at, is_legacy_migrated, created_at) "
            "VALUES (?, 'x', 'other.txt', ?, 1, 'text/plain', ?, ?, 't', 0, 't')",
            [new_id, f"{GROUP_DIR}/0001-B/other.txt", "1" * 64, USER])
        return real(doc, new_id, stage, mode)

    if inject:
        monkeypatch.setattr(attachment_conversion, "copy_attachments", injected)
        refused("attachment_target_rows_exist", convert)
        assert snapshot(env) == before
        assert not (env["storage"] / GROUP_DIR / "0001-B").exists()
        return
    result = convert()
    expected = ["a.txt", "ghost.txt"] if with_source else []
    assert sorted(r["filename"] for r in attachment_rows(env, ROOT_B)) == expected
    assert result["conversion"]["attachments"]["count"] == len(expected)
    assert attachment_rows(env, ROOT) == before["attachments"]


# ── numbers and the chain (V-B3-5, V-B3-12, V-B3-13, V-P-10) ─────────────────────────────────────────

@pytest.mark.parametrize("seeded", [True, False])
def test_round_trips_extend_the_chain_with_the_expected_numbers(env, counting, seeded):
    """V-B3-5 (L0005 2.9.1 table): R->B->R->B->R from 0001-R with children 0002 / 0003."""
    with_children(env, n=2)
    if seeded:
        counter_row(env, 3)
    steps = [(ROOT, "B", f"{FLOW}.0001-B", 0, 3), (f"{FLOW}.0001-B", "R", f"{FLOW}.0004-R", 1, 4),
             (f"{FLOW}.0004-R", "B", f"{FLOW}.0004-B", 0, 4), (f"{FLOW}.0004-B", "R", f"{FLOW}.0005-R", 1, 5)]
    frozen = {}
    for i, (doc_id, to, expected, reservations, last_seq) in enumerate(steps):
        counting["reserve"] = 0
        judged = preview(doc_id, to)
        assert (judged.kind, judged.chained) == ("succession", i > 0)
        assert judged.children == (2 if i == 0 else 0)
        assert (judged.new_id, judged.exact) == (expected, reservations == 0)    # 2·4: the expected number
        result = convert(doc_id, to)
        assert (result["conversion"]["mode"], result["conversion"]["new_doc_id"]) == ("succession", expected)
        assert counting["reserve"] == reservations
        assert doc_counter(env) == (last_seq if seeded or i > 0 else None)        # no row until a reservation
        frozen[doc_id] = without(row(env, doc_id), "updated_at")
        assert meta_of(env, expected)["converted_from"]["doc_id"] == doc_id
    chain = [ROOT] + [s[2] for s in steps]
    for prev, nxt in zip(chain, chain[1:]):
        assert row(env, prev)["superseded_by"] == nxt
    for doc_id, rec in frozen.items():                                            # untouched after their step
        assert without(row(env, doc_id), "updated_at") == rec
    from modules.flow_gate.documents import live_root
    from modules.flow_gate.db import documents as db_docs
    assert [d["doc_id"] for d in live_root.live_roots(db_docs.get_documents_by_group_id(FLOW))] == [chain[-1]]
    assert all(row(env, c)["triggered_by"] == ROOT for c in CHILDREN[:2])
    for middle in chain[:-1]:
        payload = refused("root_superseded", lambda m=middle: convert(m, "B" if m.endswith("R") else "R"))
        assert payload["live_root_doc_id"] == chain[-1]
    assert _sql(env["db"], "SELECT COUNT(*) AS n FROM workflow_events WHERE event_type = 'doc_type_converted'")[0][
        "n"] == 4


def test_previewing_an_expected_number_ten_times_changes_nothing(env, client):
    """V-B3-12 / V-P-10: the judgement and the console read are pure reads (``id_counter`` included)."""
    _first_succession(env)
    counter_row(env, 3)
    before = snapshot(env)
    for _ in range(10):
        judged = preview(ROOT_B, "R")
        assert (judged.new_id, judged.exact) == (f"{FLOW}.0004-R", False)
    for _ in range(5):
        res = client.get(f"/api/v1/documents/{ROOT_B}", params={"include": "root_conversion"})
        assert res.status_code == 200
        assert (res.json()["root_conversion"]["new_doc_id"], res.json()["root_conversion"]["new_doc_exact"]) == (
            f"{FLOW}.0004-R", False)
    assert snapshot(env) == before


@pytest.mark.parametrize("case", ["succession_taken", "succession_free", "number_swap"])
def test_one_conversion_reserves_and_stages_once(env, counting, case):
    """V-B3-13: reservations ①1 ②0 ③0, each stage once, the answer's number is the reserved one."""
    if case == "succession_taken":
        _first_succession(env)
        add_attachment(env, ROOT_B, "a.txt", b"alpha")
        doc_id, to, expected = ROOT_B, "R", f"{FLOW}.0004-R"
    elif case == "succession_free":
        with_children(env)
        add_attachment(env, ROOT, "a.txt", b"alpha")
        doc_id, to, expected = ROOT, "B", ROOT_B
    else:
        make_doc(env, body=ORIGINAL_BODY)
        add_attachment(env, ROOT, "a.txt", b"alpha")
        doc_id, to, expected = ROOT, "B", ROOT_B
    for key in counting:
        counting[key] = 0
    result = convert(doc_id, to)
    assert result["conversion"]["new_doc_id"] == expected
    assert counting["reserve"] == (1 if case == "succession_taken" else 0)
    assert (counting["stage_body"], counting["stage_attachments"], counting["copy_files"]) == (1, 1, 0)
    if case == "succession_taken":
        assert doc_counter(env) == 4


def test_a_failed_succession_restores_the_counter(env, monkeypatch):
    from modules.flow_gate.documents.attachments import conversion as attachment_conversion

    _first_succession(env)
    counter_row(env, 3)
    before = snapshot(env)
    monkeypatch.setattr(attachment_conversion, "copy_attachments",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("injected")))
    with pytest.raises(Exception):
        convert(ROOT_B, "R")
    assert snapshot(env) == before and doc_counter(env) == 3


# ── the judgement (V-B3-6~10b, V-B3-14, DB0007 Q8) ────────────────────────────────────────────────

def test_a_succeeded_root_is_refused_with_its_live_root(env, client):
    """V-B3-6 / P0006 §3.5."""
    with_children(env)
    convert()
    res = client.patch(f"/api/v1/documents/{ROOT}/root-type", json={"new_type": "B"})
    assert res.status_code == 409
    assert res.json() == {"error": "root_superseded", "detail": res.json()["detail"], "doc_id": ROOT,
                          "live_root_doc_id": ROOT_B}
    assert res.json()["detail"] == "This root was already succeeded by a later document; convert the live root instead."
    again = convert(ROOT_B, "R")                                       # a second conversion of the same root is
    assert again["conversion"]["new_doc_id"] == f"{FLOW}.0005-R"        # ... the live root's own, a new seq
    assert refused("root_superseded", convert)["live_root_doc_id"] == f"{FLOW}.0005-R"


def test_without_children_outside_a_chain_it_stays_a_number_swap(env):
    """V-B3-7."""
    make_doc(env, body=ORIGINAL_BODY)
    assert preview().kind == "number_swap"
    result = convert()
    assert result["conversion"]["mode"] == "number_swap" and row(env, ROOT) is None


def test_a_predecessor_by_card_line_replacement_is_no_chain(env):
    """V-B3-14: ``superseded_by`` alone (a card line replacement, ``meta`` ``{}``) keeps the number swap."""
    make_doc(env, body=ORIGINAL_BODY)
    make_doc(env, doc_id=f"{FLOW}.0009-R", seq=9, body=None, rel="", superseded_by=ROOT, meta="{}")
    judged = preview()
    assert (judged.kind, judged.chained) == ("number_swap", False)
    assert convert()["conversion"]["mode"] == "number_swap"


@pytest.mark.parametrize("case,expected", [
    ("a_empty_meta", False), ("b_predecessor_mark", True), ("c_own_mark", True), ("d_other_mode", False),
    ("d_broken_json", False), ("e_one_of_many", True), ("no_predecessor", False)])
def test_the_chain_is_read_from_the_meta_marks(env, case, expected):
    """DB0007 §4 Q8 (a)~(e): the candidates' ``meta`` is parsed in the application; no row-only test."""
    from modules.flow_gate.db import documents as db_docs
    from modules.flow_gate.documents import live_root

    succession = json.dumps({"converted_to": {"doc_id": ROOT, "mode": "succession"}})
    own = json.dumps({"converted_from": {"doc_id": f"{FLOW}.0009-B", "mode": "succession"}})
    make_doc(env, body=None, rel="", meta=own if case == "c_own_mark" else "{}")
    metas = {"a_empty_meta": ["{}"], "b_predecessor_mark": [succession],
             "d_other_mode": [json.dumps({"converted_to": {"doc_id": ROOT, "mode": "number_swap"}})],
             "d_broken_json": ["{oops"], "e_one_of_many": ["{}", "not json", succession]}.get(case, [])
    for i, meta in enumerate(metas):
        make_doc(env, doc_id=f"{FLOW}.00{20 + i}-B", seq=20 + i, body=None, rel="", superseded_by=ROOT, meta=meta)
    assert live_root.in_succession_chain(db_docs.get_by_id(ROOT)) is expected


@pytest.mark.parametrize("ownership,expected", [("graph", "flow_owned_by_graph"),
                                                ("conflict", "flow_ownership_conflict"),
                                                ("unassigned", None), ("v01", "decision_taken")])
def test_each_ownership_takes_its_own_path(env, monkeypatch, ownership, expected):
    """V-B3-8a: graph / conflict refuse with their own code; unassigned / v01 go on to the sequence check."""
    from modules.flow_gate.db import workflow_sequences as ws
    from modules.flow_gate.graph_workflow.services import engine_router
    from modules.flow_gate.graph_workflow.services.engine_router import Ownership

    with_children(env)
    if ownership == "v01":
        ws.insert_sequence(ROOT)
    monkeypatch.setattr(engine_router, "read_ownership", lambda flow: Ownership(ownership, None, None))
    before = snapshot(env)
    judged = preview()
    if expected is None:
        assert judged.kind == "succession"
        assert convert()["conversion"]["mode"] == "succession"
        return
    assert (judged.kind, judged.code, judged.extra) == ("refused", expected, {})
    with pytest.raises(Exception) as exc:
        convert()
    assert getattr(exc.value, "code", None) == expected or exc.value.status_code == 409
    assert snapshot(env) == before


@pytest.mark.parametrize("state", ["disposed", "ai_running"])
def test_a_disposed_or_running_flow_is_refused_as_before(env, monkeypatch, state):
    """V-B3-8: the existing refusals stand for a root with children."""
    from modules.flow_gate import process_service
    from modules.flow_gate.services import mutation_policy

    with_children(env)
    if state == "disposed":
        monkeypatch.setattr(process_service, "is_group_disposed", lambda group_id: group_id == FLOW)
    else:
        lease = {"group_id": FLOW, "run_id": "run_live_1", "token_id": "tok_1", "action_scope": "edit"}
        monkeypatch.setattr(mutation_policy.db_leases, "get_active", lambda group_id: lease if group_id == FLOW else None)
        monkeypatch.setattr(mutation_policy, "_lease_run_live", lambda run_id: True)
    before = snapshot(env)
    judged = preview()
    assert (judged.kind, judged.code) == ("refused", "group_disposed" if state == "disposed" else "group_ai_running")
    with pytest.raises(Exception):
        convert()
    assert snapshot(env) == before


def test_the_guard_judgement_decides_the_mode(env):
    """V-B3-9: a child created after the preview makes the execution a succession."""
    make_doc(env, body=ORIGINAL_BODY)
    assert preview().kind == "number_swap"
    child(env, CHILDREN[0])
    result = convert()
    assert (result["conversion"]["mode"], result["conversion"]["child_count"]) == ("succession", 1)
    assert row(env, ROOT)["superseded_by"] == ROOT_B


def _chain_root(env):
    _first_succession(env)
    return ROOT_B, "R", B_REL


@pytest.mark.parametrize("case", ["number_swap", "children", "chain"])
@pytest.mark.parametrize("point", ["state", "bytes", "utf8"])
def test_a_body_read_error_refuses_both_ways_without_changes(env, monkeypatch, case, point):
    """V-B3-10: the error stays inside the judgement — ``root_body_unreadable(reason=read_error, mode)``."""
    from modules.flow_gate.documents import root_conversion

    if case == "chain":
        doc_id, to, rel = _chain_root(env)
    else:
        with_children(env, n=0 if case == "number_swap" else 2)
        doc_id, to, rel = ROOT, "B", BODY_REL
    mode = "number_swap" if case == "number_swap" else "succession"
    target = (env["storage"] / rel).resolve()
    if point == "utf8":
        target.write_bytes(b"---\ntype: R\n---\n\xff\xfe broken utf-8\n")
    before = snapshot(env)
    real = Path.read_bytes
    with monkeypatch.context() as m:
        if point == "state":
            m.setattr(root_conversion, "body_read_state", lambda doc: (_ for _ in ()).throw(PermissionError("denied")))
        elif point == "bytes":
            m.setattr(Path, "read_bytes", lambda self: (_ for _ in ()).throw(PermissionError("denied"))
                      if self.resolve() == target else real(self))
        for _ in range(3):
            judged = preview(doc_id, to)
            assert (judged.kind, judged.code, judged.extra) == ("refused", "root_body_unreadable",
                                                                {"reason": "read_error", "mode": mode})
        payload = refused("root_body_unreadable", lambda: convert(doc_id, to))
    assert (payload["reason"], payload["mode"]) == ("read_error", mode)
    assert "permissions or encoding" in payload["detail"]
    assert snapshot(env) == before


@pytest.mark.parametrize("case", ["number_swap", "succession_taken"])
def test_a_read_error_while_staging_rolls_back(env, monkeypatch, counting, case):
    """V-B3-10a: the judgement read the body, the staging re-read fails: the same refusal, all rolled back (the
    reservation too)."""
    if case == "succession_taken":
        doc_id, to, rel = _chain_root(env)
    else:
        make_doc(env, body=ORIGINAL_BODY)
        doc_id, to, rel = ROOT, "B", BODY_REL
    target = (env["storage"] / rel).resolve()
    before = snapshot(env)
    real = Path.read_bytes
    reads = {"n": 0}

    def second_read_fails(self):
        if self.resolve() == target:
            reads["n"] += 1
            if reads["n"] == 2:                                   # 1: the judgement in the guard, 2: the stage
                raise PermissionError("denied")
        return real(self)

    counting["reserve"] = 0
    with monkeypatch.context() as m:
        m.setattr(Path, "read_bytes", second_read_fails)
        payload = refused("root_body_unreadable", lambda: convert(doc_id, to))
    assert (payload["reason"], payload["mode"]) == ("read_error", "number_swap" if case == "number_swap"
                                                    else "succession")
    assert counting["reserve"] == (1 if case == "succession_taken" else 0)
    assert snapshot(env) == before


@pytest.mark.parametrize("state", ["file_path_missing", "file_not_found", "path_unresolved"])
@pytest.mark.parametrize("children", [0, 2])
def test_a_missing_body_keeps_the_number_swap_and_refuses_the_succession(env, state, children):
    """V-B3-10b: the number swap keeps the path-only behaviour; a succession needs a readable body."""
    rel = {"file_path_missing": "", "file_not_found": BODY_REL,
           "path_unresolved": "C:/outside/flowgate/0001-R_requirement.md"}[state]
    make_doc(env, body=None, rel=rel)
    for doc_id in CHILDREN[:children]:
        child(env, doc_id)
    before = snapshot(env)
    judged = preview()
    if children == 0:
        assert judged.kind == "number_swap"
        convert()
        assert files_under(env["storage"]) == [] and row(env, ROOT_B) is not None
        return
    assert (judged.kind, judged.code, judged.extra) == ("refused", "root_body_unreadable",
                                                        {"reason": state, "mode": "succession"})
    payload = refused("root_body_unreadable", convert)
    assert (payload["reason"], payload["mode"]) == (state, "succession")
    assert payload["detail"].endswith("recover the document first.")
    assert snapshot(env) == before


# ── card lines, draft cards, Canvas and the live root (V-B3-3, V-B3-11, L0005 2.10 P1~P10) ───────────

def _draft(nodes):
    from modules.flow_gate.db.connection import get_store
    from modules.flow_gate.graph_workflow.store import definition_store

    with get_store().transaction():
        definition_store.create_draft(FLOW, None, USER)
        definition_store.replace_draft_content(FLOW, nodes, [])


def test_lines_draft_cards_and_the_canvas_follow_the_new_document(env):
    from modules.flow_gate.db import documents as db_docs
    from modules.flow_gate.graph_workflow.domain import root_binding, supersede
    from modules.flow_gate.graph_workflow.domain.canvas_mapping import is_alive
    from modules.flow_gate.graph_workflow.services import card_document_ports

    with_children(env)
    _draft([{"node_key": "n_req", "node_type": "document", "doc_type": "R", "palette_item_id": "requirement",
             "label": "요구사항"}])
    moved = []
    card_document_ports.register(card_document_ports.ON_ROOT_CONVERTED,
                                 lambda flow, old, new: moved.append((flow, old, new["doc_id"])) or ["n_req"])
    env["events"].clear()

    result = convert()

    assert moved == [(FLOW, ROOT, ROOT_B)]                                   # the card lines move to the new id
    assert result["conversion"]["draft_node_keys"] == ["n_req"]
    node = _sql(env["db"], "SELECT doc_type, palette_item_id, label FROM graph_draft_nodes WHERE node_key = 'n_req'")[0]
    assert (node["doc_type"], node["palette_item_id"], node["label"]) == ("B", "bug", "버그")
    assert [e.payload["reason"] for e in env["events"] if e.event_type == "graph_canvas_changed"] == ["draft"]
    docs = {d["doc_id"]: d for d in db_docs.get_documents_by_group_id(FLOW)}
    handoffs = supersede.pending_handoffs(FLOW, docs, {root_binding.doc_key(ROOT)}, set())
    assert handoffs == [{"from_key": root_binding.doc_key(ROOT), "to_key": root_binding.doc_key(ROOT_B), "hops": 1}]
    state, roots = root_binding.resolve_root([d for d in docs.values() if is_alive(d, FLOW)])      # P2
    assert (state, [d["doc_id"] for d in roots]) == ("one", [ROOT_B])


def _set_review(env, doc_id, value):
    _sql(env["db"], "UPDATE documents SET doc_review_status = ? WHERE doc_id = ?", (value, doc_id))


def test_every_live_root_point_counts_the_live_root_only(env, client):
    """L0005 2.10 P4~P10 after a succession: the original root is history, the new document is the root."""
    from modules.flow_gate import process_service, service
    from modules.flow_gate.db import documents as db_docs
    from modules.flow_gate.db import workflow_sequences as ws
    from modules.flow_gate.documents import document_service

    with_children(env)
    convert()
    kid = CHILDREN[0]

    # P4 — the parent root of a child, and the narrow fallback query
    assert client.get(f"/api/v1/documents/{kid}").json()["parent_root_doc_id"] == ROOT_B
    assert db_docs.list_live_roots("flowgate", FLOW, "R") == []
    assert [d["doc_id"] for d in db_docs.list_live_roots("flowgate", FLOW, "B")] == [ROOT_B]

    # P5 / P6 / P8 — final approval follows the live root only
    _set_review(env, ROOT, "wf_done")
    assert client.get(f"/api/v1/documents/{kid}").json()["is_final_approved"] is False
    assert document_service.is_final_approved(db_docs.get_by_id(kid)) is False
    group = next(n for n in process_service.get_group_tree("flowgate")["nodes"] if n.get("id") == FLOW)
    assert group["is_final_approved"] is False
    _set_review(env, ROOT, None)
    _set_review(env, ROOT_B, "wf_done")
    assert client.get(f"/api/v1/documents/{kid}").json()["is_final_approved"] is True
    assert document_service.is_final_approved(db_docs.get_by_id(kid)) is True
    group = next(n for n in process_service.get_group_tree("flowgate")["nodes"] if n.get("id") == FLOW)
    assert group["is_final_approved"] is True
    _set_review(env, ROOT_B, None)

    # P5 — a succeeded root reads the workflow of its live root
    ws.insert_sequence(ROOT_B)
    seq_id = ws.get_sequence_by_doc_id(ROOT_B)["id"]
    ws.insert_sequence_item(sequence_id=seq_id, item_seq=1, type_="T", label="작업", doc_class="B", sort_order=0)
    original = client.get(f"/api/v1/documents/{ROOT}").json()
    assert original["workflow_steps"] == ["T"] and original["live_root_doc_id"] == ROOT_B

    # P9 — one chain root, the new document under the original (V-B3-11)
    nodes, warnings = service.build_chain_tree(db_docs.get_documents_by_group_id(FLOW), kid)
    assert "Multiple roots detected" not in warnings
    by_id = {n["doc_id"]: n for n in nodes}
    assert nodes[0]["doc_id"] == ROOT and by_id[ROOT_B]["parent_id"] == ROOT
    assert all(by_id[c]["parent_id"] == ROOT for c in CHILDREN)

    # P7 — the discard anchors to the live root
    result = process_service.dispose_group(FLOW, "other", "test")
    assert result["status"] == "success", result
    assert db_docs.get_by_id(result["doc_id"])["target_id"] == ROOT_B


class _Request:
    headers: dict = {}


def test_a_succeeded_root_takes_no_decision_and_no_sequence(env, monkeypatch):
    """P10 / V-B3-6: decide and the sequence save refuse the original root with the live root's id."""
    from modules.flow_gate.api.v1 import workflow_decision_routes as wdr
    from modules.flow_gate.db import documents as db_docs

    with_children(env)
    convert()
    monkeypatch.setattr(wdr, "verify_bearer", lambda request: {"issued_to": USER})
    decide = wdr.post_workflow_decide(ROOT, wdr.DecideRequest(
        doc_class="R", sequence=[wdr.SequenceItem(id=1, type="T", label="작업")]), _Request())
    edit = wdr.patch_workflow_sequence_endpoint(wdr.EditSequenceBodyRequest.model_construct(
        doc_id=ROOT, items=[], force_encoding_reason=None, expected_workflow_tag=None, expected_plan=None,
        acknowledged_codes=None), _Request())
    for res in (decide, edit):
        assert res.status_code == 409
        assert json.loads(res.body) == {
            "error": "root_superseded", "doc_id": ROOT, "live_root_doc_id": ROOT_B,
            "detail": "This root was already succeeded by a later document; use the live root instead."}
    assert wdr._superseded_root_response(ROOT_B, db_docs.get_by_id(ROOT_B)) is None
    replaced = make_doc(env, doc_id=f"{FLOW}.0009-R", seq=9, body=None, rel="", superseded_by=ROOT_B, meta="{}")
    assert wdr._superseded_root_response(replaced, db_docs.get_by_id(replaced)) is None   # not a succession


@pytest.mark.parametrize("route", ["decide", "sequence"])
def test_a_succession_landing_before_the_guard_still_refuses_the_old_root(env, monkeypatch, route):
    """P10 / V-B3-6 (rework): the route reads the original before it is succeeded and passes its own check; the
    succession commits between that read and the Flow guard. The row read again inside the guard refuses — the
    original gets no sequence and keeps the row the succession left."""
    from modules.flow_gate.api.v1 import workflow_decision_routes as wdr
    from modules.flow_gate.db import workflow_sequences as ws
    from modules.flow_gate.graph_workflow.services import flow_guard, v01_contact

    with_children(env)
    seen: dict = {}
    real_read = v01_contact.read_ownership

    def read_after_a_succession(flow_id):                  # the contact's read before it takes the guard
        if not seen:
            seen["busy"] = True
            seen["mode"] = convert()["conversion"]["mode"]
            seen["row"] = row(env, ROOT)
        return real_read(flow_id)

    checks = []
    real_check = wdr._superseded_root_response
    monkeypatch.setattr(v01_contact, "read_ownership", read_after_a_succession)
    monkeypatch.setattr(wdr, "_superseded_root_response",
                        lambda doc_id, doc: checks.append(flow_guard.held_guard()) or real_check(doc_id, doc))
    monkeypatch.setattr(wdr, "verify_bearer", lambda request: {"issued_to": USER})
    if route == "decide":
        res = wdr.post_workflow_decide(ROOT, wdr.DecideRequest(
            doc_class="R", sequence=[wdr.SequenceItem(id=1, type="T", label="작업")]), _Request())
    else:
        res = wdr.patch_workflow_sequence_endpoint(wdr.EditSequenceBodyRequest.model_construct(
            doc_id=ROOT, items=[wdr.EditSequenceItem(type="T", label="작업")], force_encoding_reason=None,
            expected_workflow_tag=None, expected_plan=None, acknowledged_codes=None), _Request())
    assert seen["mode"] == "succession"
    assert checks == [None, ("flow", FLOW)]                # before the guard (passed), inside it (refused)
    assert res.status_code == 409
    assert json.loads(res.body) == {
        "error": "root_superseded", "doc_id": ROOT, "live_root_doc_id": ROOT_B,
        "detail": "This root was already succeeded by a later document; use the live root instead."}
    assert ws.get_sequence_by_doc_id(ROOT) is None and ws.get_sequence_by_doc_id(ROOT_B) is None
    assert row(env, ROOT) == seen["row"]


def test_a_succeeded_root_reads_and_writes_by_its_live_root_final_approval(env, client):
    """P5 / P6 (rework): once the live successor is finally approved, the original root reads as finally approved
    and not editable, and its title and body writes are refused the same way (no change)."""
    from fastapi import HTTPException

    from modules.flow_gate.db import documents as db_docs
    from modules.flow_gate.documents import document_service

    with_children(env)
    convert()
    original = db_docs.get_by_id(ROOT)
    assert document_service.is_final_approved(original) is False
    assert document_service.is_document_editable(original) is True
    read = client.get(f"/api/v1/documents/{ROOT}").json()
    assert (read["is_final_approved"], read["is_editable"]) == (False, True)

    _set_review(env, ROOT_B, "wf_done")
    original = db_docs.get_by_id(ROOT)
    assert original["doc_review_status"] != "wf_done"      # the original keeps its own state
    assert document_service.is_final_approved(original) is True
    assert document_service.is_document_editable(original) is False
    read = client.get(f"/api/v1/documents/{ROOT}").json()
    assert (read["is_final_approved"], read["is_editable"]) == (True, False)

    before, body = row(env, ROOT), (env["storage"] / BODY_REL).read_bytes()
    with pytest.raises(HTTPException) as excinfo:
        document_service.update_document(ROOT, {"title": "바뀐 제목"}, USER)
    assert (excinfo.value.status_code, excinfo.value.detail) == (422, "Modification not allowed after final approval.")
    saved = client.patch(f"/api/v1/documents/{ROOT}/content", json={"content": "바뀐 본문"})
    assert saved.status_code == 422, saved.text
    assert saved.json()["detail"] == "Modification not allowed after final approval."
    assert row(env, ROOT) == before and (env["storage"] / BODY_REL).read_bytes() == body

    # the live root, and a root that was never succeeded, still answer for themselves
    assert document_service.is_final_approved(db_docs.get_by_id(ROOT_B)) is True
    other = make_doc(env, doc_id=f"{FLOW}.0009-R", seq=9, body=None, rel="")
    assert document_service.is_final_approved(db_docs.get_by_id(other)) is False


# ── the console read (P0006 §3.1, V-P-10) and the refusals (§3.5, V-P-12) ─────────────────────────

def _read(client, doc_id, include=True):
    res = client.get(f"/api/v1/documents/{doc_id}", params={"include": "root_conversion"} if include else None)
    assert res.status_code == 200, res.text
    return res.json()


def _judgement(body):
    out = dict(body["root_conversion"])
    assert out.pop("read_at")
    return out


def test_the_document_read_carries_the_judgement_on_request(env, client):
    with_children(env)
    plain = _read(client, ROOT, include=False)
    assert "root_conversion" not in plain
    assert (plain["converted_from"], plain["converted_to"], plain["live_root_doc_id"]) == (None, None, ROOT)
    body = _read(client, ROOT)
    assert _judgement(body) == {
        "target_type": "B", "decision": "succession", "mode": "succession", "refusal": None, "new_doc_id": ROOT_B,
        "new_doc_exact": True, "child_count": 3, "chained": False, "live_root_doc_id": None}
    kid = _read(client, CHILDREN[0])
    assert (kid["root_conversion"], kid["live_root_doc_id"]) == (None, None)

    convert()
    original = _read(client, ROOT)
    assert original["superseded_by"] == ROOT_B and original["live_root_doc_id"] == ROOT_B
    assert original["converted_from"] is None
    assert without(original["converted_to"], "at") == {"doc_id": ROOT_B, "mode": "succession", "by": USER}
    assert _judgement(original) == {
        "target_type": "B", "decision": "refused", "mode": None,
        "refusal": {"code": "root_superseded", "live_root_doc_id": ROOT_B}, "new_doc_id": None,
        "new_doc_exact": False, "child_count": 3, "chained": False, "live_root_doc_id": ROOT_B}
    new = _read(client, ROOT_B)
    assert without(new["converted_from"], "at") == {"doc_id": ROOT, "mode": "succession", "by": USER, "children": 3}
    assert (new["converted_to"], new["live_root_doc_id"]) == (None, ROOT_B)
    assert _judgement(new) == {
        "target_type": "R", "decision": "succession", "mode": "succession", "refusal": None,
        "new_doc_id": f"{FLOW}.0005-R", "new_doc_exact": False, "child_count": 0, "chained": True,
        "live_root_doc_id": None}


@pytest.mark.parametrize("case,expected", [
    ("swap", {"decision": "number_swap", "mode": "number_swap", "refusal": None, "new_doc_id": ROOT_B,
              "new_doc_exact": True, "child_count": 0}),
    ("identity", {"decision": "refused", "mode": None, "new_doc_id": None, "new_doc_exact": False, "child_count": 0,
                  "refusal": {"code": "front_matter_identity_unsafe", "reason": "missing_end_delimiter",
                              "field": None}}),
    ("missing", {"decision": "refused", "mode": "succession", "new_doc_id": None, "new_doc_exact": False,
                 "child_count": 3, "refusal": {"code": "root_body_unreadable", "reason": "file_not_found",
                                               "mode": "succession"}}),
    ("read_error", {"decision": "refused", "mode": "number_swap", "new_doc_id": None, "new_doc_exact": False,
                    "child_count": 0, "refusal": {"code": "root_body_unreadable", "reason": "read_error",
                                                  "mode": "number_swap"}}),
    ("rows", {"decision": "refused", "mode": "number_swap", "new_doc_id": None, "new_doc_exact": False,
              "child_count": 0, "refusal": {"code": "attachment_target_rows_exist", "target_doc_id": ROOT_B,
                                            "mode": "number_swap"}}),
    ("collision", {"decision": "refused", "mode": None, "new_doc_id": None, "new_doc_exact": False, "child_count": 0,
                   "refusal": {"code": "target_id_exists", "target_doc_id": ROOT_B}}),
])
def test_each_judgement_reads_as_the_protocol_says(env, client, case, expected):
    body = {"identity": b"---\n", "read_error": b"---\ntype: R\n---\n\xff\xfe\n"}.get(case, ORIGINAL_BODY)
    make_doc(env, body=None if case == "missing" else body)
    if case == "missing":
        for doc_id in CHILDREN:
            child(env, doc_id)
    if case == "rows":
        orphan_attachment(env, ROOT_B, "a.txt", f"{GROUP_DIR}/0001-B/a.txt")
    if case == "collision":
        make_doc(env, doc_id=ROOT_B, seq=1, body=None, rel="")
    before = snapshot(env)
    judged = _judgement(_read(client, ROOT))
    assert {k: judged[k] for k in expected} == expected
    assert (judged["target_type"], judged["chained"], judged["live_root_doc_id"]) == ("B", False, None)
    assert snapshot(env) == before


def test_a_failed_judgement_read_answers_null_not_a_guess(env, client, monkeypatch):
    from modules.flow_gate.graph_workflow.services import engine_router

    with_children(env)
    monkeypatch.setattr(engine_router, "read_ownership", lambda flow: (_ for _ in ()).throw(RuntimeError("db")))
    body = _read(client, ROOT)
    assert body["root_conversion"] is None and body["doc_id"] == ROOT


def test_the_route_refusals_keep_their_flat_shapes(env, client):
    """V-P-12: new codes are flat 409 bodies with ``error`` / ``detail``; the existing ones are unchanged."""
    from modules.flow_gate.db import workflow_sequences as ws

    make_doc(env, body=None)
    for doc_id in CHILDREN:
        child(env, doc_id)
    res = client.patch(f"/api/v1/documents/{ROOT}/root-type", json={"new_type": "B"})
    assert (res.status_code, res.json()) == (409, {
        "error": "root_body_unreadable", "doc_id": ROOT, "reason": "file_not_found", "mode": "succession",
        "detail": "The root body cannot be read, so it cannot be converted; recover the document first."})
    ws.insert_sequence(ROOT)
    decided = client.patch(f"/api/v1/documents/{ROOT}/root-type", json={"new_type": "B"})
    assert (decided.status_code, decided.json()) == (
        409, {"detail": "Cannot convert root type after the workflow decision was taken."})


def test_the_route_answers_a_succession_with_its_conversion(env, client):
    """V-P-11: ``child_count`` 3, the children keep ``triggered_by``, the original gets only the marks."""
    with_children(env)
    res = client.patch(f"/api/v1/documents/{ROOT}/root-type", json={"new_type": "B"})
    assert res.status_code == 200, res.text
    assert res.json()["data"]["doc_id"] == ROOT_B and res.json()["conversion"]["child_count"] == 3
    assert json.loads(res.json()["data"]["meta"])["converted_from"]["doc_id"] == ROOT
    assert all(row(env, c)["triggered_by"] == ROOT for c in CHILDREN)


# ── migration 156 (DB0007 §3.3) ──────────────────────────────────────────────────────────────────

def test_the_superseded_by_index_exists_in_every_dialect(env):
    names = [r["name"] for r in _sql(env["db"], "PRAGMA index_list(documents)")]
    assert "idx_documents_superseded_by" in names
    [info] = [r for r in _sql(env["db"], "PRAGMA index_info(idx_documents_superseded_by)")]
    assert info["name"] == "superseded_by"
    root = Path(__file__).resolve().parents[3] / "sql" / "migrations"
    sql = {d: (root / d / "156_documents_superseded_by_index.sql").read_text(encoding="utf-8")
           for d in ("mysql", "postgres", "sqlite")}
    statements = {d: [ln for ln in text.splitlines() if ln.strip() and not ln.startswith("--")]
                  for d, text in sql.items()}
    assert statements["mysql"] == ["CREATE INDEX idx_documents_superseded_by ON documents (superseded_by(191));"]
    for d in ("postgres", "sqlite"):
        assert statements[d] == ["CREATE INDEX IF NOT EXISTS idx_documents_superseded_by ON documents (superseded_by);"]
