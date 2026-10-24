"""0064 T-B1d — body diagnosis and recovery (L0005 2.11~2.13, P0006 §2.6~§2.8, DB0007 §2.4).

On the real SQLite DB and temp storage root of the card document suite (``test_fg_card_documents.env``): every
test builds its own fixture documents and files; no real environment's data is diagnosed or recovered (the
T-B1f reception case is out of scope). The console documents router and the worker token document read are
mounted as in ``test_document_body_state_0064``.
"""
from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from test_fg_card_documents import FA, FB, _doc, _h, _sql, db_path, env, node, put_draft  # noqa: F401 — fixtures
from modules.flow_gate.db import connection, documents
from modules.flow_gate.documents import body_recovery
from modules.flow_gate.documents.routers import documents as console
from modules.flow_gate.api.v1 import document_routes as token
from modules.flow_gate.services.mutation_policy import MutationPolicyError, mutation_error_response
from modules.flow_gate.storage import paths

_SERVER_DIR = Path(__file__).resolve().parents[3]
DISPOSED = "Modification not allowed: the group has been disposed."


@pytest.fixture(autouse=True)
def recovery_routes(env, monkeypatch):
    app = env["client"].app
    app.include_router(console.router, prefix="/api/v1")
    app.include_router(token.router, prefix="/flowgate")
    app.add_exception_handler(MutationPolicyError, lambda _request, exc: mutation_error_response(exc))
    app.dependency_overrides[console.get_current_user] = lambda: {"user_id": "u_kim"}
    monkeypatch.setattr(token, "verify_bearer", lambda request: {"_is_user_jwt": True})
    refreshed = []
    monkeypatch.setattr(console, "_broadcast_document_refresh", lambda doc: refreshed.append(doc["doc_id"]))
    env["refreshed"] = refreshed
    env["client"] = TestClient(app, raise_server_exceptions=False)


# ── helpers ──────────────────────────────────────────────────────────────────

def code_of(doc_id):
    return doc_id.rsplit(".", 1)[1]


def rel(doc_id, name="document.md"):
    return paths.to_storage_relative(paths.document_path("flowgate", FB, code_of(doc_id), name, module="v02"),
                                     "flowgate")


def absolute(stored):
    return next(c for c, inside in paths.storage_path_candidates(stored, "flowgate") if inside)


def front(doc_id, *, number=None, group=FB, kind=None, body="본문 그대로\n"):
    lines = ["---"]
    if group is not None:
        lines.append(f"group_id: {group}")
    if kind is not None:
        lines.append(f"type: {kind}")
    if number is not False:
        lines.append(f"doc_number: {number or code_of(doc_id)}")
    return ("\n".join(lines + ["---", ""]) + body).encode("utf-8")


def write(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def plain(seq, kind="D", *, file_path=None, **extra):
    """A non-card document; its recorded file (``file_path`` default: the standard name) is absent."""
    doc_id = f"{FB}.{seq:04d}-{kind}"
    stored = rel(doc_id) if file_path is None else file_path
    return _doc(FB, seq, kind, status="draft", file_path=stored, **extra)


def recovered_events(doc_id):
    return connection.get_store()._fetch_all(
        "SELECT * FROM events WHERE doc_id = ? AND event_type = 'doc_body_recovered'", [doc_id])


def all_recovered_events():
    return connection.get_store()._fetch_all("SELECT * FROM events WHERE event_type = 'doc_body_recovered'")


def diagnose(env, **query):
    response = env["client"].get("/api/v1/documents/body-diagnosis", params=query)
    assert response.status_code == 200, response.text
    return response.json()


def items_by_id(result):
    return {item["doc_id"]: item for item in result["items"]}


def regenerate(env, doc_id, method=None, *, raw=None):
    url = f"/api/v1/documents/{doc_id}/regenerate"
    if raw is not None:
        return env["client"].post(url, json=raw)
    return env["client"].post(url, json={"expected_method": method} if method else None)


def bulk(env, selections):
    return env["client"].post("/api/v1/documents/body-recovery", json={"selections": selections})


def stable(row):
    """The row without what a recovery may change (the path, its derived name and the update time)."""
    return {k: v for k, v in row.items() if k not in ("file_path", "filename", "updated_at")}


def storage_files(env):
    root = env["storage"]
    return {str(p.relative_to(root)): p.read_bytes() for p in root.rglob("*") if p.is_file()} if root.exists() else {}


def snapshot(env):
    db = env["db"]
    return {table: _sql(db, f"SELECT * FROM {table}") for table in
            ("documents", "events", "id_counter", "document_revisions", "group_events")} | {"files": storage_files(env)}


def add_revision(env, doc_id, text, revision_no=1):
    backup = write(env["storage"] / "revisions" / f"{code_of(doc_id)}.r{revision_no}.md", text.encode("utf-8"))
    _sql(env["db"], "INSERT INTO document_revisions (doc_id, revision_no, backup_path, edit_reason, created_by, "
                    "created_at) VALUES (?, ?, ?, 'user_comment', 'u_kim', 't')",
         (doc_id, revision_no, paths.to_storage_relative(backup, "flowgate")))
    return backup


def card_docs(env, kind="N", label=None, version=0, ref="tmp_c"):
    draft = put_draft(env, FB, version, [node(ref, kind, label)])
    key = draft["key_map"][ref]
    return key, [line["doc_id"] for line in draft["card_documents"][key]]


def unlink_body(doc_id):
    path = paths.resolve_storage_path(documents.get_by_id(doc_id)["file_path"], "flowgate")
    path.unlink()
    return path


def set_meta(doc_id, **changes):
    meta = json.loads(documents.get_by_id(doc_id)["meta"] or "{}")
    for key, value in changes.items():
        if value is None:
            meta.pop(key, None)
        else:
            meta[key] = value
    documents.update(doc_id, {"meta": json.dumps(meta, ensure_ascii=False)})


def card_without_created_empty(doc_id):
    meta = json.loads(documents.get_by_id(doc_id)["meta"])
    meta["graph_card"].pop("created_empty")
    documents.update(doc_id, {"meta": json.dumps(meta)})


def other_project_row(stored):
    """A row of another project (``other``) whose recorded file is ``stored``."""
    from modules.flow_gate.db import groups as db_groups
    from modules.flow_gate.db import projects as db_projects

    db_projects.create({"project_id": "other", "project_name": "other"})
    db_groups.create({"group_id": "other.m.0001", "project_id": "other", "module": "m", "title": "o",
                      "status": "OPEN"})
    documents.create({"doc_id": "other.m.0001.0001-D", "project_id": "other", "module": "m",
                      "group_id": "other.m.0001", "type_code": "D", "seq": 1, "title": "o", "status": "draft",
                      "file_path": stored})


def token_reads(env, doc_id):
    base = f"/flowgate/api/v1/document/{doc_id}"
    content = env["client"].get(base).json()
    return content.get("content_state"), content.get("content"), env["client"].get(base + "/outline").status_code


# ── V-B1d-1: the method and body loss of each case ───────────────────────────

def test_diagnosis_method_and_body_loss_of_every_case(env, monkeypatch, tmp_path):
    """V-B1d-1 / V-P-6: 4.3 order and body loss; one diagnosis covers them all and succeeds."""
    old_root = tmp_path / "storage_old"
    monkeypatch.setattr(paths, "_system_storage_root_value", lambda: str(old_root))
    draft = put_draft(env, FB, 0, [node("tmp_n", "N"), node("tmp_t", "T")])
    key = draft["key_map"]["tmp_n"]
    instruction, report = [line["doc_id"] for line in draft["card_documents"][key]]   # instructed / RUN lines
    for doc_id in (instruction, report):                                 # new card drafts
        unlink_body(doc_id)
    old_instruction, old_report = [line["doc_id"] for line in draft["card_documents"][draft["key_map"]["tmp_t"]]]
    for doc_id in (old_instruction, old_report):                         # old card drafts (no marker)
        card_without_created_empty(doc_id)
        unlink_body(doc_id)
    moved = plain(21)                                                    # other storage root
    write(old_root / rel(moved), front(moved))
    swapped = _doc(FB, 22, "R", status="draft", file_path=rel(f"{FB}.0022-R", "requirement.md"))
    write(absolute(rel(f"{FB}.0022-B", "requirement.md")), front(swapped, kind="R"))   # opposite number file
    unrecorded = plain(23, file_path="")
    write(absolute(rel(unrecorded)), front(unrecorded))                 # standard location file
    backed = plain(24)
    add_revision(env, backed, "# last saved\n")                          # revision backup
    lost = plain(29)                                                     # no evidence
    broken = plain(30)
    write(absolute(rel(broken)), b"\xff\xfe not utf-8")                  # read error
    before = snapshot(env)

    result = diagnose(env, group_id=FB)

    assert snapshot(env) == before                                       # V-P-6: nothing written
    found = items_by_id(result)
    expected = {
        moved: ("file_not_found", "fix_storage_root", False, None),
        swapped: ("file_not_found", "relink_opposite", True, "none"),
        unrecorded: ("file_path_missing", "relink_standard", True, "none"),
        backed: ("file_not_found", "restore_revision", True, "none"),
        instruction: ("file_not_found", "regenerate_card_draft", True, "none"),
        report: ("file_not_found", "regenerate_card_draft", True, "none"),
        old_instruction: ("file_not_found", "regenerate_card_draft", True, "possible"),
        old_report: ("file_not_found", "regenerate_card_draft", True, "possible"),
        lost: ("file_not_found", "regenerate_stub", True, "lost"),
        broken: ("read_error", "read_error_manual", False, None),
    }
    assert {d: (i["reason"], i["method"], i["recoverable"], i["body_loss"]) for d, i in found.items()} == expected
    assert found[moved]["evidence"] == {"root": str(old_root), "source": "system_setting"}
    assert found[swapped]["candidate"]["relative"] == rel(f"{FB}.0022-B", "requirement.md")
    assert found[unrecorded]["candidate"]["relative"] == rel(unrecorded)
    assert found[instruction]["origin"] == {"card": True, "node_key": key, "slot": "instruction",
                                            "unsubmitted": True, "untouched_proven": True, "inherited_body": False}
    assert found[old_report]["origin"]["untouched_proven"] is False
    assert found[lost]["origin"] == {"card": False}
    assert result["summary"] == {"checked": len(_sql(env["db"], "SELECT 1 FROM documents WHERE group_id = ?", (FB,))),
                                 "unreadable": 10, "recoverable": 8, "storage_root_suspect": 1,
                                 "suspect_roots": [str(old_root)]}
    assert result["scope"] == {"kind": "group", "id": FB, "project_id": "flowgate"}
    assert result["storage_root"]["effective"] == str(env["storage"])
    assert result["storage_root"]["source"] == "env"
    assert result["storage_root"]["configured"]["system"] == str(old_root)
    assert len(result["fingerprint"]) == 64
    assert diagnose(env, group_id=FB)["fingerprint"] == result["fingerprint"]


def test_diagnosis_requests_and_an_all_readable_flow(env):
    """V-P-6: exactly one scope; an unknown Flow is 404; nothing unreadable is an empty list."""
    client = env["client"]
    for query in ({}, {"group_id": FB, "project_id": "flowgate"}):
        response = client.get("/api/v1/documents/body-diagnosis", params=query)
        assert response.status_code == 422
        assert response.json() == {"detail": "Exactly one of group_id or project_id is required."}
    response = client.get("/api/v1/documents/body-diagnosis", params={"group_id": f"{FB}9"})
    assert response.status_code == 404 and response.json() == {"detail": f"Flow not found: {FB}9"}
    card_docs(env, "R")
    result = diagnose(env, group_id=FB)
    assert result["items"] == [] and result["summary"] == {
        "checked": 1, "unreadable": 0, "recoverable": 0, "storage_root_suspect": 0, "suspect_roots": []}


def test_project_diagnosis_reads_every_page(env, monkeypatch):
    """L0005 2.11.1: DIAG_SCAN_PAGE rows at a time, to the end; cancelled documents are skipped."""
    monkeypatch.setattr(body_recovery, "DIAG_SCAN_PAGE", 2)
    made = [plain(seq) for seq in range(1, 6)]
    documents.update(made[2], {"status": "cancelled"})
    result = diagnose(env, project_id="flowgate")
    assert result["scope"] == {"kind": "project", "id": "flowgate", "project_id": "flowgate"}
    unreadable = [d for d in made if d != made[2]] + [f"{FA}.0001-R"]      # the seeded root has no file either
    assert [item["doc_id"] for item in result["items"]] == sorted(unreadable)
    assert result["summary"]["checked"] == len(_sql(env["db"], "SELECT 1 FROM documents WHERE status <> 'cancelled'"))


# ── V-B1d-2: opposite number candidates that must not relink ─────────────────

OPPOSITE = rel(f"{FB}.0002-B", "requirement.md")


@pytest.mark.parametrize("case", ["two", "no_number", "number_mismatch", "group_mismatch", "type_mismatch",
                                  "used_elsewhere", "no_front_matter", "match", "match_without_group"])
def test_opposite_number_candidate_needs_one_matching_unused_file(env, case):
    """V-B1d-2: only a matching number (+ the same group or none) relinks; the candidate never changes."""
    doc_id = _doc(FB, 2, "R", status="draft", file_path=rel(f"{FB}.0002-R", "requirement.md"))
    candidate = absolute(OPPOSITE)
    data = {
        "two": front(doc_id), "no_number": front(doc_id, number=False), "number_mismatch": front(doc_id, number="0003-R"),
        "group_mismatch": front(doc_id, group=f"{FB}9"), "type_mismatch": front(doc_id, kind="B"),
        "used_elsewhere": front(doc_id), "no_front_matter": b"# no front matter\n", "match": front(doc_id, number="R0002"),
        "match_without_group": front(doc_id, group=None),
    }[case]
    write(candidate, data)
    if case == "two":
        write(candidate.with_name("0002-B_other.md"), front(doc_id))
    if case == "used_elsewhere":
        _doc(FB, 7, "D", status="draft", file_path=str(candidate))      # another stored spelling, same file
    item = items_by_id(diagnose(env, group_id=FB))[doc_id]
    relinks = case in ("match", "match_without_group")
    assert item["method"] == ("relink_opposite" if relinks else "regenerate_stub")
    assert candidate.read_bytes() == data


# ── V-B1d-3: every method recovers to a readable body ────────────────────────

def _case(env, method, tmp_path):
    if method == "relink_opposite":
        doc_id = _doc(FB, 2, "R", status="draft", file_path=rel(f"{FB}.0002-R", "requirement.md"))
        write(absolute(OPPOSITE), front(doc_id, kind="R"))
        return doc_id, OPPOSITE
    if method == "relink_standard":
        doc_id = plain(3, file_path="")
        write(absolute(rel(doc_id)), front(doc_id))
        return doc_id, rel(doc_id)
    if method == "restore_revision":
        doc_id = plain(4)
        add_revision(env, doc_id, "# 마지막 저장본\n")
        return doc_id, rel(doc_id)
    if method == "regenerate_card_draft":
        _key, (doc_id, _report) = card_docs(env, "N", "조사")
        path = unlink_body(doc_id)
        return doc_id, paths.to_storage_relative(path, "flowgate")
    doc_id = plain(9, file_path="")
    return doc_id, rel(doc_id)


@pytest.mark.parametrize("method", ["relink_opposite", "relink_standard", "restore_revision",
                                    "regenerate_card_draft", "regenerate_stub"])
def test_each_method_recovers_a_readable_body_and_changes_only_the_path(env, tmp_path, method):
    """V-B1d-3 / V-P-7: token read and outline work again; one event; number, title, status unchanged."""
    doc_id, after = _case(env, method, tmp_path)
    before = documents.get_by_id(doc_id)
    env["events"].clear()
    response = regenerate(env, doc_id, method)
    assert response.status_code == 200, response.text
    out = response.json()
    loss = "none" if method != "regenerate_stub" else "lost"
    assert {k: out[k] for k in ("method", "body_loss", "body_lost", "before_file_path", "after_file_path")} == {
        "method": method, "body_loss": loss, "body_lost": loss != "none",
        "before_file_path": before["file_path"], "after_file_path": after}
    assert out["restored_from"] == body_recovery.RESTORED_FROM[method]
    row = documents.get_by_id(doc_id)
    assert stable(row) == stable(before) and row["file_path"] == after and out["data"]["file_path"] == after
    state, content, outline = token_reads(env, doc_id)
    assert (state, outline) == ("readable", 200) and content == out["content"]
    [event] = recovered_events(doc_id)
    assert event["reason"] == ("file_path_missing" if not before["file_path"] else "file_not_found")
    assert json.loads(event["note"]) == {"method": method, "before": before["file_path"], "after": after,
                                         "body_lost": {"none": "false", "lost": "true"}[loss],
                                         "reason": event["reason"]}
    assert env["refreshed"] == [doc_id]
    notices = [e.payload for e in env["events"] if e.payload.get("reason") == "documents"]
    assert len(notices) == 1 and notices[0]["flow_id"] == FB
    again = regenerate(env, doc_id)
    assert again.status_code == 409 and again.json() == {
        "detail": "Document file already exists; nothing to regenerate.", "error": "file_exists"}
    if method == "restore_revision":
        assert content == "# 마지막 저장본\n"
    if method == "regenerate_card_draft":
        assert content.startswith(f"---\nproject: flowgate\nmodule: v02\ngroup: 0082\ngroup_id: {FB}\ntype: N\n")
        assert f"title: {before['title']}\n---\n" in content


def test_a_final_approved_closed_document_recovers(env):
    """L0005 2.12: recovery applies no editability check; the old call without a body still works."""
    doc_id = plain(4, review="wf_done")
    documents.update(doc_id, {"status": "closed"})
    response = env["client"].post(f"/api/v1/documents/{doc_id}/regenerate")
    assert response.status_code == 200, response.text
    assert response.json()["restored_from"] == "metadata" and response.json()["body_lost"] is True
    assert documents.get_by_id(doc_id)["doc_review_status"] == "wf_done"


# ── V-B1d-4 / V-B1d-13 / V-P-17: a target another row uses ───────────────────

@pytest.mark.parametrize("reason", ["file_path_missing", "path_unresolved"])
@pytest.mark.parametrize("same_project", [True, False])
def test_a_target_another_row_uses_is_refused_without_change(env, tmp_path, reason, same_project):
    doc_id = plain(4, file_path="" if reason == "file_path_missing" else str(tmp_path / "outside" / "x.md"))
    target = absolute(rel(doc_id))
    write(target, b"# the other document's body\n")
    if same_project:
        user = _doc(FB, 8, "D", status="draft", file_path=str(target))     # absolute spelling, same file
    else:                                                               # another project's row: no id shown
        other_project_row(str(target))
        user = None
    before, files = documents.get_by_id(doc_id), storage_files(env)
    assert items_by_id(diagnose(env, group_id=FB))[doc_id]["method"] == "regenerate_stub"
    response = regenerate(env, doc_id, "regenerate_stub")
    assert response.status_code == 409, response.text
    assert response.json() == {
        "detail": "The recovery target file is used by another document; nothing was changed.",
        "error": "target_in_use",
        "evidence": {"target_file_path": rel(doc_id), "used_by_doc_id": user}}
    assert documents.get_by_id(doc_id) == before and storage_files(env) == files
    assert recovered_events(doc_id) == [] and env["refreshed"] == []


@pytest.mark.parametrize("landed", ["file", "row_only"])
def test_a_target_taken_after_the_check_is_still_refused(env, monkeypatch, landed):
    """The write's own same-file judgement (``write_new_file``) answers the same 409, never the 503 envelope;
    a row that took the path before its file exists takes it too."""
    from modules.flow_gate.storage import files

    doc_id = plain(4, file_path="")
    target = absolute(rel(doc_id))
    real = files.write_new_file

    def racing(path, data, **kwargs):
        if landed == "file":
            write(path, b"# landed meanwhile\n")
        _doc(FB, 8, "D", status="draft", file_path=str(path))
        return real(path, data, **kwargs)
    monkeypatch.setattr(files, "write_new_file", racing)
    response = regenerate(env, doc_id)
    assert response.status_code == 409 and response.json()["error"] == "target_in_use"
    assert response.json()["evidence"] == {"target_file_path": rel(doc_id), "used_by_doc_id": f"{FB}.0008-D"}
    assert documents.get_by_id(doc_id)["file_path"] == ""
    if landed == "file":
        assert target.read_bytes() == b"# landed meanwhile\n"
    else:
        assert not target.exists()
    assert recovered_events(doc_id) == []


@pytest.mark.parametrize("spelling", ["absolute", "relative", "backslash", "other_project"])
@pytest.mark.parametrize("route", ["single", "bulk"])
def test_a_missing_target_another_row_points_at_is_refused_without_change(env, spelling, route):
    """V-B1d-4 / V-B1d-13 / V-P-17: the other row's file is missing as well, yet its path is in use in any stored
    spelling; nothing is written (no stub the other row would read as its own), single 409 / bulk refused."""
    doc_id = plain(4, file_path="")
    target = absolute(rel(doc_id))
    stored = {"absolute": str(target), "relative": rel(doc_id), "backslash": rel(doc_id).replace("/", "\\"),
              "other_project": str(target)}[spelling]
    if spelling == "other_project":
        other_project_row(stored)
        user = None
    else:
        user = _doc(FB, 8, "D", status="draft", file_path=stored)
    assert not target.exists()
    assert items_by_id(diagnose(env, group_id=FB))[doc_id]["method"] == "regenerate_stub"
    evidence = {"target_file_path": rel(doc_id), "used_by_doc_id": user}
    before, snap = documents.get_by_id(doc_id), snapshot(env)
    if route == "single":
        response = regenerate(env, doc_id, "regenerate_stub")
        assert response.status_code == 409, response.text
        assert response.json() == {"detail": body_recovery.DETAILS["target_in_use"], "error": "target_in_use",
                                   "evidence": evidence}
        assert snapshot(env) == snap and env["refreshed"] == []
    else:
        other = plain(5)
        response = bulk(env, [{"doc_id": doc_id, "expected_method": "regenerate_stub"},
                              {"doc_id": other, "expected_method": "regenerate_stub"}])
        assert response.status_code == 200, response.text
        out = response.json()
        assert [r["status"] for r in out["results"]] == ["refused", "recovered"]
        assert out["results"][0]["refusal"] == {"error": "target_in_use",
                                                "detail": body_recovery.DETAILS["target_in_use"],
                                                "evidence": evidence}
        assert env["refreshed"] == [other]
    assert documents.get_by_id(doc_id) == before and not target.exists()
    assert recovered_events(doc_id) == []


def test_an_unused_file_at_the_target_is_moved_aside_not_overwritten(env):
    """L0005 2.12: nobody uses it, so it is kept as ``.orphan-*`` and the recovery writes next to it."""
    doc_id = plain(4, file_path="")
    target = write(absolute(rel(doc_id)), b"# precious, no front matter\n")
    response = regenerate(env, doc_id)
    assert response.status_code == 200, response.text
    [orphan] = [p for p in target.parent.iterdir() if p.name.startswith(target.name + ".orphan-")]
    assert orphan.read_bytes() == b"# precious, no front matter\n"
    assert target.read_text(encoding="utf-8") == response.json()["content"]


# ── relink candidates and revision backups that do not decode ────────────────

CORRUPT = b"\n\xff not UTF-8 after the front matter\n"


@pytest.mark.parametrize("method", ["relink_opposite", "relink_standard"])
def test_a_relink_candidate_that_is_not_utf8_is_never_relinked(env, tmp_path, method):
    """V-B1d-2 / V-B1d-3: a matching front matter over bytes that do not decode is no relink. The diagnosis goes on
    to the next method and to the other documents; no recovery reports a relink without loss; nothing changes."""
    doc_id, after = _case(env, method, tmp_path)
    candidate = absolute(after)
    data = candidate.read_bytes() + CORRUPT
    candidate.write_bytes(data)
    other = plain(6)
    found = items_by_id(diagnose(env, group_id=FB))
    assert (found[doc_id]["method"], found[doc_id]["body_loss"], found[doc_id]["candidate"]) == (
        "regenerate_stub", "lost", None)
    assert found[other]["method"] == "regenerate_stub"
    before = documents.get_by_id(doc_id)
    response = regenerate(env, doc_id, method)
    assert response.status_code == 409, response.text
    assert response.json()["error"] == "diagnosis_changed"
    assert response.json()["diagnosis"]["method"] == "regenerate_stub"
    out = bulk(env, [{"doc_id": doc_id, "expected_method": method},
                     {"doc_id": other, "expected_method": "regenerate_stub"}]).json()
    assert [r["status"] for r in out["results"]] == ["refused", "recovered"]
    assert out["results"][0]["refusal"]["error"] == "diagnosis_changed"
    assert documents.get_by_id(doc_id) == before and candidate.read_bytes() == data
    assert recovered_events(doc_id) == []


def test_a_relink_candidate_broken_after_the_diagnosis_is_refused(env, tmp_path, monkeypatch):
    """The relink reads the candidate strictly when it applies: a decode error is a refusal, never a success."""
    doc_id, after = _case(env, "relink_opposite", tmp_path)
    candidate = absolute(after)
    real, seen = body_recovery.diagnose_doc, []

    def breaking(doc, reason, root):
        found = real(doc, reason, root)
        if not seen:                                                    # broken right after the guard's diagnosis
            seen.append(found["method"])
            candidate.write_bytes(candidate.read_bytes() + CORRUPT)
        return found
    monkeypatch.setattr(body_recovery, "diagnose_doc", breaking)
    before = documents.get_by_id(doc_id)
    response = regenerate(env, doc_id, "relink_opposite")
    assert seen == ["relink_opposite"]
    assert response.status_code == 409, response.text
    assert response.json() == {
        "detail": body_recovery.DETAILS["diagnosis_changed"], "error": "diagnosis_changed",
        "diagnosis": {"reason": "file_not_found", "method": "regenerate_stub", "recoverable": True,
                      "body_loss": "lost"}}
    assert documents.get_by_id(doc_id) == before
    assert recovered_events(doc_id) == [] and env["refreshed"] == []


def test_a_broken_revision_backup_is_skipped_for_the_next_one(env):
    """A backup that is not UTF-8 never stops the diagnosis (no 500): the next older readable backup restores, with
    none readable the next method shows; the other documents of the Flow and the project are diagnosed too."""
    older_ok = plain(1)
    add_revision(env, older_ok, "# 이전 저장본\n", revision_no=1)
    add_revision(env, older_ok, "x", revision_no=2).write_bytes(b"\xff\xfe broken newest")
    all_broken = plain(2)
    for revision_no in (1, 2):
        add_revision(env, all_broken, "x", revision_no=revision_no).write_bytes(b"\xff broken")
    other = plain(3)
    for query in ({"group_id": FB}, {"project_id": "flowgate"}):
        found = items_by_id(diagnose(env, **query))
        assert {d: (found[d]["method"], found[d]["body_loss"]) for d in (older_ok, all_broken, other)} == {
            older_ok: ("restore_revision", "none"), all_broken: ("regenerate_stub", "lost"),
            other: ("regenerate_stub", "lost")}
    response = regenerate(env, older_ok, "restore_revision")
    assert response.status_code == 200, response.text
    assert response.json()["content"] == "# 이전 저장본\n"
    assert token_reads(env, older_ok) == ("readable", "# 이전 저장본\n", 200)
    out = bulk(env, [{"doc_id": all_broken, "expected_method": "regenerate_stub"},
                     {"doc_id": other, "expected_method": "regenerate_stub"}]).json()
    assert (out["recovered"], out["refused"], out["failed"]) == (2, 0, 0)


# ── V-B1d-5 / V-B1d-7 / V-P-8 / V-P-18: bulk ──────────────────────────────────

def test_bulk_skips_only_the_document_whose_diagnosis_changed(env):
    """V-B1d-5 / V-P-8: three selections, one changed since the diagnosis; the others recover."""
    first, changed, last = plain(1), plain(2), plain(3)
    shown = items_by_id(diagnose(env, group_id=FB))
    add_revision(env, changed, "# saved meanwhile\n")
    response = bulk(env, [{"doc_id": d, "expected_method": shown[d]["method"]} for d in (first, changed, last)])
    assert response.status_code == 200, response.text
    out = response.json()
    assert (out["ok"], out["recovered"], out["refused"], out["failed"]) == (True, 2, 1, 0)
    by_id = {r["doc_id"]: r for r in out["results"]}
    assert [r["doc_id"] for r in out["results"]] == [first, changed, last]
    assert by_id[changed] == {
        "doc_id": changed, "status": "refused", "method": None, "restored_from": None, "body_lost": None,
        "body_loss": None, "before_file_path": None, "after_file_path": None,
        "refusal": {"error": "diagnosis_changed",
                    "detail": "The diagnosis changed since it was shown; run the diagnosis again.",
                    "evidence": {"reason": "file_not_found", "method": "restore_revision", "recoverable": True,
                                 "body_loss": "none"}}}
    assert by_id[first]["status"] == by_id[last]["status"] == "recovered"
    assert by_id[first]["refusal"] is None and by_id[first]["body_loss"] == "lost"
    assert recovered_events(changed) == [] and documents.get_by_id(changed)["file_path"] == rel(changed)
    assert not absolute(rel(changed)).exists()
    assert [item["doc_id"] for item in diagnose(env, group_id=FB)["items"]] == [changed]


@pytest.mark.parametrize("count", [0, 51])
def test_bulk_selection_count(env, count):
    """V-B1d-7: 0 and 51 selections are 422 ``selection_count_invalid`` and change nothing."""
    plain(1)
    before = snapshot(env)
    response = bulk(env, [{"doc_id": f"{FB}.0001-D", "expected_method": "regenerate_stub"}] * count)
    assert response.status_code == 422
    assert response.json() == {"detail": "selections must contain between 1 and 50 items.",
                               "error": "selection_count_invalid"}
    assert snapshot(env) == before


def test_bulk_target_in_use_and_tr2_are_refusals_not_failures(env):
    """V-P-18 / P0006 §2.8 table: TR2's single answer stays the old 409 without ``error``."""
    in_use = plain(1, file_path="")
    target = write(absolute(rel(in_use)), b"used\n")
    _doc(FB, 8, "D", status="draft", file_path=str(target))
    tr2 = _doc(FB, 2, "TR2", status="draft", file_path="")
    normal = plain(3)
    response = bulk(env, [{"doc_id": in_use, "expected_method": "regenerate_stub"},
                          {"doc_id": tr2, "expected_method": "regenerate_stub"},
                          {"doc_id": normal, "expected_method": "regenerate_stub"}])
    assert response.status_code == 200, response.text
    out = response.json()
    assert (out["recovered"], out["refused"], out["failed"]) == (1, 2, 0)
    refusals = [r["refusal"] for r in out["results"]]
    assert refusals[0] == {"error": "target_in_use",
                           "detail": "The recovery target file is used by another document; nothing was changed.",
                           "evidence": {"target_file_path": rel(in_use), "used_by_doc_id": f"{FB}.0008-D"}}
    assert refusals[1] == {"error": "tr2_not_supported", "detail": "TR2 recovery requires its canonical JSON document.",
                           "evidence": {"type_code": "TR2"}}
    assert out["results"][2]["status"] == "recovered" and refusals[2] is None
    single = regenerate(env, tr2)
    assert single.status_code == 409 and single.json() == {"detail": "TR2 recovery requires its canonical JSON document."}


def test_bulk_refusal_table_matches_the_single_answers(env, monkeypatch, tmp_path):
    """P0006 §2.8: each single refusal becomes the same bulk code; an unexpected error is ``failed``."""
    old_root = tmp_path / "storage_old"
    monkeypatch.setattr(paths, "_system_storage_root_value", lambda: str(old_root))
    missing = f"{FB}.0099-D"
    readable = plain(1)
    write(absolute(rel(readable)), b"# here\n")
    broken = plain(2)
    write(absolute(rel(broken)), b"\xff\xfe")
    moved = plain(3)
    write(old_root / rel(moved), b"# in the old root\n")
    exploding = plain(4)
    real = body_recovery._apply

    def apply(doc, *args):
        if doc["doc_id"] == exploding:
            raise RuntimeError("injected")
        return real(doc, *args)
    monkeypatch.setattr(body_recovery, "_apply", apply)
    singles = {d: regenerate(env, d) for d in (missing, readable, broken, moved)}
    assert singles[missing].status_code == 404 and singles[missing].json() == {"detail": f"Document not found: {missing}"}
    assert singles[readable].json()["error"] == "file_exists"
    assert singles[broken].status_code == 409 and singles[broken].json() == {
        "detail": "The document file exists but cannot be read; fix permissions or encoding first.", "error": "read_error"}
    assert singles[moved].status_code == 409 and singles[moved].json() == {
        "detail": ("The storage root appears to have changed; correct the storage root setting instead of "
                   "recovering this document."),
        "error": "storage_root_mismatch", "evidence": {"root": str(old_root), "source": "system_setting"}}
    out = bulk(env, [{"doc_id": d} for d in (missing, readable, broken, moved, exploding)]).json()
    assert [(r["status"], (r["refusal"] or {}).get("error")) for r in out["results"]] == [
        ("refused", "not_found"), ("refused", "file_exists"), ("refused", "read_error"),
        ("refused", "storage_root_mismatch"), ("failed", "internal_error")]
    assert out["results"][3]["refusal"]["evidence"] == singles[moved].json()["evidence"]
    assert out["results"][4]["refusal"] == {"error": "internal_error", "detail": None, "evidence": None}
    for r in out["results"][:4]:
        assert r["refusal"]["detail"] == singles[r["doc_id"]].json()["detail"]
    assert all_recovered_events() == []


# ── V-B1d-6 / V-B1d-11: refusals that change nothing ─────────────────────────

def test_fix_storage_root_is_refused_and_changes_nothing(env, monkeypatch, tmp_path):
    """V-B1d-6."""
    old_root = tmp_path / "storage_old"
    monkeypatch.setattr(paths, "_system_storage_root_value", lambda: str(old_root))
    doc_id = plain(1)
    write(old_root / rel(doc_id), b"# old root\n")
    before = snapshot(env)
    for response in (regenerate(env, doc_id, "fix_storage_root"),
                     bulk(env, [{"doc_id": doc_id, "expected_method": "fix_storage_root"}])):
        assert response.status_code in (200, 409)
    assert snapshot(env) == before and env["refreshed"] == []


def test_a_read_error_is_refused_single_and_bulk_keeps_going(env):
    """V-B1d-11: file bytes unchanged; the other selection recovers."""
    broken = plain(1)
    path = write(absolute(rel(broken)), b"\xff\xfe broken")
    other = plain(2)
    assert regenerate(env, broken, "read_error_manual").json()["error"] == "read_error"
    out = bulk(env, [{"doc_id": broken, "expected_method": "read_error_manual"},
                     {"doc_id": other, "expected_method": "regenerate_stub"}]).json()
    assert [r["status"] for r in out["results"]] == ["refused", "recovered"]
    assert out["results"][0]["refusal"]["error"] == "read_error"
    assert path.read_bytes() == b"\xff\xfe broken" and recovered_events(broken) == []


def test_diagnosis_changed_single_and_unknown_method(env):
    """V-P-7: a stale ``expected_method`` is 409 with the current diagnosis; an unknown one is 422."""
    doc_id = plain(1)
    before = snapshot(env)
    response = regenerate(env, doc_id, "restore_revision")
    assert response.status_code == 409 and response.json() == {
        "detail": "The diagnosis changed since it was shown; run the diagnosis again.", "error": "diagnosis_changed",
        "diagnosis": {"reason": "file_not_found", "method": "regenerate_stub", "recoverable": True, "body_loss": "lost"}}
    for call in (lambda: regenerate(env, doc_id, raw={"expected_method": "guess"}),
                 lambda: bulk(env, [{"doc_id": doc_id, "expected_method": "guess"}])):
        response = call()
        assert response.status_code == 422 and response.json() == {"detail": "Unknown expected_method."}
    assert snapshot(env) == before


# ── V-B1d-8 / V-B1d-8a: the untouched evidence ───────────────────────────────

def test_a_saved_card_draft_is_a_stub_and_an_unsaved_one_a_draft(env):
    """V-B1d-8 ①: a person's save is a body write whatever the line's author."""
    _key, (instruction, report) = card_docs(env, "N")
    response = env["client"].patch("/api/v1/documents/content", json={"doc_id": report, "content": ""})
    assert response.status_code == 200, response.text
    for doc_id in (instruction, report):
        unlink_body(doc_id)
    found = items_by_id(diagnose(env, group_id=FB))
    assert (found[report]["method"], found[report]["body_loss"]) == ("regenerate_stub", "lost")
    assert (found[instruction]["method"], found[instruction]["body_loss"]) == ("regenerate_card_draft", "none")


def _succeed_saved_root(env, version=0):
    """A card root R with a person's body (``doc_body_saved`` on R only), succeeded by B (it has a child)."""
    key, [root] = card_docs(env, "R", "요구", version=version, ref="tmp_r")
    response = env["client"].patch("/api/v1/documents/content", json={"doc_id": root, "content": "# 사람이 쓴 본문\n"})
    assert response.status_code == 200, response.text
    _doc(FB, 50, "N", status="draft", triggered_by=root, target_id=root)
    response = env["client"].patch(f"/api/v1/documents/{root}/root-type", json={"new_type": "B"})
    assert response.status_code == 200, response.text
    return key, root, response.json()["data"]["doc_id"]


def test_a_succeeded_card_root_is_never_proven_untouched(env):
    """V-B1d-8 ②: the successor has no save event of its own, still ``possible``; saved itself: stub."""
    _key, root, successor = _succeed_saved_root(env)
    original = paths.resolve_storage_path(documents.get_by_id(root)["file_path"], "flowgate").read_bytes()
    root_events = _sql(env["db"], "SELECT * FROM events WHERE doc_id = ?", (root,))
    meta = json.loads(documents.get_by_id(successor)["meta"])
    assert meta["graph_card"]["created_empty"] is False and meta["converted_from"]["doc_id"] == root
    assert _sql(env["db"], "SELECT 1 FROM events WHERE doc_id = ? AND event_type = 'doc_body_saved'", (successor,)) == []
    unlink_body(successor)
    item = items_by_id(diagnose(env, group_id=FB))[successor]
    assert (item["method"], item["body_loss"]) == ("regenerate_card_draft", "possible")
    assert item["origin"]["untouched_proven"] is False and item["origin"]["inherited_body"] is True
    out = regenerate(env, successor, "regenerate_card_draft").json()
    assert (out["body_loss"], out["body_lost"]) == ("possible", True)
    assert json.loads(recovered_events(successor)[0]["note"])["body_lost"] == "possible"
    assert paths.resolve_storage_path(documents.get_by_id(root)["file_path"], "flowgate").read_bytes() == original
    assert _sql(env["db"], "SELECT * FROM events WHERE doc_id = ?", (root,)) == root_events
    response = env["client"].patch("/api/v1/documents/content", json={"doc_id": successor, "content": "# B 본문\n"})
    assert response.status_code == 200, response.text
    unlink_body(successor)
    item = items_by_id(diagnose(env, group_id=FB))[successor]
    assert (item["method"], item["body_loss"]) == ("regenerate_stub", "lost")


def test_succession_evidence_survives_old_marks_repeats_and_draft_recoveries(env):
    """V-B1d-8a: a leftover ``created_empty: true``, a second succession and a second loss stay ``possible``."""
    _key, root, successor = _succeed_saved_root(env)
    meta = json.loads(documents.get_by_id(successor)["meta"])
    meta["graph_card"]["created_empty"] = True                          # the past defect's leftover
    documents.update(successor, {"meta": json.dumps(meta)})
    unlink_body(successor)
    assert items_by_id(diagnose(env, group_id=FB))[successor]["body_loss"] == "possible"
    assert regenerate(env, successor, "regenerate_card_draft").status_code == 200
    unlink_body(successor)                                              # lost again after a draft recovery
    assert items_by_id(diagnose(env, group_id=FB))[successor]["body_loss"] == "possible"
    assert regenerate(env, successor, "regenerate_card_draft").status_code == 200
    _doc(FB, 51, "N", status="draft", triggered_by=successor, target_id=successor)
    response = env["client"].patch(f"/api/v1/documents/{successor}/root-type", json={"new_type": "R"})
    assert response.status_code == 200, response.text
    third = response.json()["data"]["doc_id"]
    unlink_body(third)
    item = items_by_id(diagnose(env, group_id=FB))[third]
    assert (item["method"], item["body_loss"], item["origin"]["inherited_body"]) == (
        "regenerate_card_draft", "possible", True)
    assert [json.loads(e["note"])["body_lost"] for e in all_recovered_events()] == ["possible", "possible"]
    assert all(e["doc_id"] == successor for e in all_recovered_events())


# ── V-B1d-10: a card work plan ───────────────────────────────────────────────

def test_a_card_work_plan_recovers_as_an_empty_plan(env):
    """V-B1d-10: JSON, no front matter; the plan editor opens it; title and status unchanged."""
    from modules.flow_gate.documents.routers import work_plan as plan_routes
    from modules.flow_gate.services import work_plan_service as wp

    env["client"].app.include_router(plan_routes.router, prefix="/api/v1")
    key, lines_made = card_docs(env, "WP", ref="tmp_wp")
    plan = lines_made[0]                                                 # the plan line; the deploy line follows
    before = documents.get_by_id(plan)
    path = unlink_body(plan)
    assert items_by_id(diagnose(env, group_id=FB))[plan]["method"] == "regenerate_card_draft"
    out = regenerate(env, plan, "regenerate_card_draft").json()
    assert out["method"] == "regenerate_card_draft" and out["after_file_path"] == before["file_path"]
    assert not path.read_text(encoding="utf-8").startswith("---")
    assert json.loads(path.read_text(encoding="utf-8")) == json.loads(wp.dumps(wp.empty_recovery_body("flowgate")))
    assert env["client"].get(f"/api/v1/documents/{plan}/work-plan").status_code == 200
    row = documents.get_by_id(plan)
    assert (row["title"], row["status"]) == (before["title"], before["status"])
    lines = env["client"].get(f"/api/v1/flows/{FB}/canvas", headers=_h()).json()
    shown = next(n["card_documents"] for n in lines["nodes"] if n.get("node_key") == key)
    assert shown[0]["body_state"] == "readable"


# ── V-B1d-12: the guard re-checks ────────────────────────────────────────────

@pytest.mark.parametrize("change", ["lease", "disposed"])
@pytest.mark.parametrize("route", ["single", "bulk"])
def test_a_lease_or_disposal_while_waiting_for_the_guard_refuses(env, monkeypatch, change, route):
    """V-B1d-12: the check inside the guard refuses; no file, no row change, no event."""
    from modules.flow_gate.graph_workflow.services import flow_guard

    doc_id = plain(1, file_path="")
    target = absolute(rel(doc_id))
    original = flow_guard.with_flow_guard
    waiting = {"inside": False}

    def guard(flow, callback):
        waiting["inside"] = True
        return original(flow, callback)
    monkeypatch.setattr(flow_guard, "with_flow_guard", guard)

    def reject(doc):
        if waiting["inside"]:
            if change == "lease":
                raise MutationPolicyError(423, "GROUP_AI_RUN_LOCKED", "group AI run is active")
            raise HTTPException(status_code=409, detail=DISPOSED)
    monkeypatch.setattr(console, "_reject_if_group_ai_running" if change == "lease" else "_reject_if_group_disposed",
                        reject)
    before = documents.get_by_id(doc_id)
    if route == "single":
        response = regenerate(env, doc_id, "regenerate_stub")
        if change == "lease":
            assert response.status_code == 423 and response.json()["error"]["code"] == "GROUP_AI_RUN_LOCKED"
        else:
            assert response.status_code == 409 and response.json() == {"detail": DISPOSED}
    else:
        out = bulk(env, [{"doc_id": doc_id, "expected_method": "regenerate_stub"}]).json()
        expected = ({"error": "group_ai_running", "detail": "The group has a running AI run; try again after it ends.",
                     "evidence": None} if change == "lease" else
                    {"error": "group_disposed", "detail": DISPOSED, "evidence": None})
        assert out["results"][0]["refusal"] == expected and out["refused"] == 1
    assert waiting["inside"] and not target.exists()
    assert documents.get_by_id(doc_id) == before and recovered_events(doc_id) == []


def test_a_rollback_after_the_write_removes_the_new_file(env, monkeypatch):
    """L0005 2.12: the file is written last with ``write_new_file``; a later failure takes it away again."""
    from modules.flow_gate.db import events as db_events

    doc_id = plain(1, file_path="")
    target = absolute(rel(doc_id))

    def fail(*args, **kwargs):
        raise RuntimeError("injected event failure")
    monkeypatch.setattr(db_events, "insert_event", fail)
    response = regenerate(env, doc_id)
    assert response.status_code == 500
    assert not target.exists() and documents.get_by_id(doc_id)["file_path"] == "" and env["refreshed"] == []


# ── V-B1d-9: every write of an existing document's body is accounted for ─────

# Body file writers: the storage primitives and the canonical JSON writers of a work plan / TR2.
_WRITERS = {"write_new_file", "overwrite_file", "write_body_atomically"}
# Raw writes are counted too in the modules that own existing documents' bodies.
_RAW = {"write_text", "write_bytes"}
_OWNERS = {"modules/flow_gate/documents/routers/documents.py", "modules/flow_gate/documents/routers/work_plan.py",
           "modules/flow_gate/documents/tr2_service.py", "modules/flow_gate/documents/body_recovery.py",
           "modules/flow_gate/documents/document_service.py"}
# (file, function) -> what writes there. "save:*" must record ``doc_body_saved`` in the same function (2.4.1).
EXPECTED_WRITERS = {
    ("modules/flow_gate/documents/routers/documents.py", "update_document_content.save"): "save:console_save",
    ("modules/flow_gate/documents/routers/work_plan.py", "save_work_plan"): "save:work_plan_save",
    ("modules/flow_gate/documents/routers/work_plan.py", "restore_work_plan_revision"): "save:work_plan_save",
    ("modules/flow_gate/documents/tr2_service.py", "_save"): "save:tr2_save",
    ("modules/flow_gate/api/inbox_routes.py", "_handle_new"): "inbox",
    ("modules/flow_gate/api/inbox_routes.py", "_handle_edit"): "inbox",
    ("flowgate/card_documents/legacy_adapter.py", "_write_file"): "card document creation",
    ("modules/flow_gate/documents/root_conversion.py", "stage_converted_file"): "conversion",
    ("modules/flow_gate/documents/attachments/conversion.py", "stage_attachment_room"): "conversion",
    ("modules/flow_gate/documents/body_recovery.py", "_apply"): "recovery",
    # a new document's first file (not an existing document's body)
    ("modules/flow_gate/documents/routers/documents.py", "create_document"): "new document",
    ("modules/flow_gate/documents/routers/documents.py", "create_related_document"): "new document",
    ("modules/flow_gate/documents/routers/documents.py", "create_next_empty_document"): "new document",
    ("modules/flow_gate/documents/routers/documents.py", "create_next_approved_core"): "new document",
    ("modules/flow_gate/documents/routers/documents.py", "_create_next_empty_document_for_auto_draft"): "new document",
    ("modules/flow_gate/documents/routers/work_plan.py", "create_work_plan"): "new document",
    # a plan file that never held a plan revived next to it (no person's content; the old file stays)
    ("modules/flow_gate/documents/routers/work_plan.py", "_heal_unwritten_plan"): "plan revival",
}


class _Calls(ast.NodeVisitor):
    def __init__(self, names):
        self.names, self.stack, self.found = names, [], {}

    def visit_FunctionDef(self, fn):
        self.stack.append(fn.name)
        self.generic_visit(fn)
        self.stack.pop()

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_Call(self, call):
        name = call.func.attr if isinstance(call.func, ast.Attribute) else getattr(call.func, "id", None)
        if name in self.names and self.stack:
            self.found.setdefault(".".join(self.stack), set()).add(name)
        self.generic_visit(call)


def test_every_body_write_is_a_recorded_save_or_a_known_writer():
    """V-B1d-9: a new body write point fails here until it records ``doc_body_saved`` or is classified."""
    found = {}
    for path in sorted(_SERVER_DIR.rglob("*.py")):
        relative = path.relative_to(_SERVER_DIR).as_posix()
        parts = relative.split("/")
        if parts[0] in ("tests", "scripts") or relative.startswith("modules/flow_gate/storage/") or any(
                part.startswith(".") or part in ("venv", "node_modules", "__pycache__", "site-packages")
                for part in parts):
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        names = _WRITERS | (_RAW if relative in _OWNERS else set())
        if not any(name in text for name in names):
            continue
        visitor = _Calls(names | {"record_body_saved"})
        visitor.visit(ast.parse(text))
        for function, calls in visitor.found.items():
            if calls & names and not function.endswith("write_body_atomically"):
                found[(relative, function)] = calls
    assert set(found) == set(EXPECTED_WRITERS)
    for key, kind in EXPECTED_WRITERS.items():
        assert ("record_body_saved" in found[key]) == kind.startswith("save:"), key


# ── shared pieces ────────────────────────────────────────────────────────────

def test_front_matter_scalars_read_like_the_identity_check():
    from modules.flow_gate.documents.front_matter_identity import scalars

    keys = ("doc_number", "group_id", "type")
    assert scalars(b"---\ndoc_number: '0001-R'\r\ngroup_id: g # c\n---\nbody", keys) == {
        "doc_number": "0001-R", "group_id": "g"}
    assert scalars(b"# none\n", keys) == {}
    assert scalars(b"---\ndoc_number: 1\n", keys) is None
    assert scalars(b"---\ntype: R\ntype: B\n---\n", keys) is None
    assert scalars(b"---\ntype: [R]\n---\n", keys) is None


def test_the_card_generator_and_its_recovery_share_the_front_matter():
    from flowgate.card_documents import legacy_adapter

    args = ("flowgate", "v02", FB, "N", "0002-N", "조사 · 지시서")
    assert legacy_adapter._front_matter(*args) == body_recovery.card_front_matter(*args) == (
        f"---\nproject: flowgate\nmodule: v02\ngroup: 0082\ngroup_id: {FB}\ntype: N\ndoc_number: 0002-N\n"
        "title: 조사 · 지시서\n---\n")
