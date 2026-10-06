"""TR2 lifecycle, proposal recovery, DB CAS and readiness (0565 T0030), connected.

Same real boundaries as ``test_tr2_connected_e2e_0565``: SQLite with every migration and a
connection per statement, a real Git worktree in scratch, the real HTTP routes. The
proposal-recovery scenarios damage the stored files exactly the way an operator or a
disk would, then go through the routes a screen uses.
"""
from __future__ import annotations

import json
import sqlite3
import subprocess
import sys

import pytest

from test_tr2_connected_e2e_0565 import (  # noqa: F401 — fixtures are used by name
    CREATED, GATE_OK, MODULE, PROJECT, USER, api, approve, auto_approved_t2, edit_spec, env,
    git, make_group, repo, store, submit_tr2, tr2_body, view,
)


def full_api():
    """``api()`` plus the generic documents router (the "create next document" path)."""
    from modules.flow_gate.documents.routers import documents as documents_routes
    client = api()
    client.app.include_router(documents_routes.router, prefix="/api/v1")
    return client


def doc_row(doc_id: str) -> dict:
    from modules.flow_gate.db import documents as db_docs
    return db_docs.get_by_id(doc_id)


def canonical(doc_id: str):
    from modules.flow_gate.documents import tr2_service
    return tr2_service.canonical_path_for_doc(doc_row(doc_id))


def snapshot(doc_id: str, revision: int):
    from modules.flow_gate.documents import tr2_service
    return tr2_service.snapshot_path(doc_row(doc_id), revision)


def recovery(client, doc_id: str) -> dict:
    resp = client.get(f"/api/v1/documents/{doc_id}/tr2/recovery")
    assert resp.status_code == 200, resp.text
    return resp.json()


def assert_no_host_path(resp, *roots) -> None:
    text = resp.text
    assert ":\\\\" not in text and ":/" not in text.replace("://", ""), text
    for root in roots:
        assert str(root) not in text and str(root).replace("\\", "/") not in text, text


def next_empty(client, group: dict, prev_doc_id: str, title: str = "반영안"):
    return client.post("/api/v1/documents/next-empty", json={
        "project_id": PROJECT, "group_id": group["group_id"], "prev_doc_id": prev_doc_id,
        "type_code": "TR2", "title": title, "module": MODULE})


# ── §4 one creation contract ─────────────────────────────────────────────────────

def test_human_next_empty_creates_the_canonical_proposal(env):
    """The route that produced ``0005-TR2_document.md`` now writes revision 1 canonically."""
    from modules.flow_gate.documents import tr2_service
    group = make_group("0101")
    client = full_api()
    t2 = auto_approved_t2(group)

    resp = next_empty(client, group, t2)
    assert resp.status_code == 201, resp.text
    doc_id = resp.json()["doc_id"]
    row = doc_row(doc_id)
    assert row["file_path"] == tr2_service.canonical_relative_path(row)
    assert row["file_path"].endswith("_document.json") and row["revision_no"] == 1
    assert row["doc_review_status"] == "pending_review"
    stored = canonical(doc_id).read_bytes()
    assert snapshot(doc_id, 1).read_bytes() == stored  # the recovery source of revision 1

    first = view(client, doc_id)
    assert first["body"]["source_t2_doc_id"] == t2
    assert first["body"]["edit_spec"]["termination"] == "needs_more_work"
    # an empty proposal is never "ready": the server says why
    assert first["readiness"] == {**first["readiness"], "ready": False, "reason": "needs_more_work"}

    # human CRUD on the created proposal reaches an authoritatively ready revision
    base = f"/api/v1/documents/{doc_id}/tr2"
    resp = client.put(base, json={"expected_revision": 1, "body": tr2_body(t2)})
    assert resp.status_code == 200 and resp.json()["new_revision"] == 2, resp.text
    assert view(client, doc_id)["readiness"]["ready"] is True

    # and the AI inbox path follows the same contract (row path = canonical file)
    group2 = make_group("0102")
    t2b = auto_approved_t2(group2)
    ai = submit_tr2(group2, t2b)
    ai_row = doc_row(ai)
    assert ai_row["file_path"] == tr2_service.canonical_relative_path(ai_row)
    assert snapshot(ai, 1).read_bytes() == canonical(ai).read_bytes()
    revs = recovery(client, ai)["revisions"]
    assert [(r["revision_no"], r["origin"], r["usable"]) for r in revs] == [(1, "ai", True)]


def test_legacy_markdown_tr2_is_reported_then_converted_explicitly(env):
    """A TR2 registered as a Markdown skeleton (pre-T0030 next-empty) is not guessed at."""
    from modules.flow_gate.db import documents as db_docs
    from modules.flow_gate.db.connection import now_iso
    from modules.flow_gate.storage import paths as storage_paths
    from modules.flow_gate.workflow.pipeline_service import (
        register_workflow_result, transition_document_review)

    group = make_group("0103")
    client = full_api()
    t2 = auto_approved_t2(group)
    doc_id = f"{group['group_id']}.0003-TR2"
    legacy = storage_paths.document_path(PROJECT, group["group_id"], "0003-TR2", "document.md",
                                         module=MODULE, branch="main")
    legacy.parent.mkdir(parents=True, exist_ok=True)
    legacy.write_bytes("---\ntype: TR2\ntitle: 변경제안\n---\n".encode("utf-8"))
    rel = storage_paths.to_storage_relative(legacy, PROJECT)
    db_docs.create({"doc_id": doc_id, "project_id": PROJECT, "module": MODULE,
                    "group_id": group["group_id"], "type_code": "TR2", "seq": 3,
                    "title": "변경제안", "status": "draft", "target_id": t2,
                    "triggered_by": t2, "owner_id": USER, "file_path": rel})
    head = [i for i in group["items"] if i["type"] == "TR2"][0]
    register_workflow_result(item_id=head["id"], registered_path=rel, registered_doc_id=doc_id,
                             registered_at=now_iso(), actor_user_id=USER)
    transition_document_review(doc_id=doc_id, action="submit", actor_user_id=USER,
                               user_permissions={"document.update"})

    resp = client.get(f"/api/v1/documents/{doc_id}/tr2")
    assert resp.status_code == 409 and resp.json()["code"] == "tr2_storage_mismatch", resp.text
    assert resp.json()["recovery"] is True
    assert_no_host_path(resp, env["repo"])
    state = recovery(client, doc_id)
    assert state["state"] == "tr2_storage_mismatch" and state["recoverable"] is False
    assert state["approval_blocked"] is True and state["raw"]["available"] is True
    raw = client.get(f"/api/v1/documents/{doc_id}/tr2/raw").json()
    assert raw["content"].startswith("---\ntype: TR2") and raw["filename"] == legacy.name
    blocked = approve(client, doc_id, 0, request_key="legacy:0103")
    assert blocked.status_code == 409 and blocked.json()["code"] == "tr2_storage_mismatch"

    # explicit conversion: a new, empty (never approvable) canonical proposal
    resp = client.post(f"/api/v1/documents/{doc_id}/tr2/recovery/new", json={"expected_revision": 0})
    assert resp.status_code == 200 and resp.json()["new_revision"] == 1, resp.text
    converted = view(client, doc_id)
    assert converted["body"]["source_t2_doc_id"] == t2
    assert doc_row(doc_id)["file_path"].endswith("0003-TR2_document.json")
    assert legacy.is_file()  # the old skeleton is kept, never deleted


# ── §6 proposal revision recovery ────────────────────────────────────────────────

def _proposal_with_history(client, group: dict):
    t2 = auto_approved_t2(group)
    doc_id = submit_tr2(group, t2)                                       # r1 (AI)
    base = f"/api/v1/documents/{doc_id}/tr2"
    assert client.post(f"{base}/items", json={"expected_revision": 1, "collection": "edits",
                                              "item": CREATED}).status_code == 200   # r2
    assert client.delete(f"{base}/items/d1", params={"expected_revision": 2}).status_code == 200  # r3
    return t2, doc_id, base


def test_missing_current_file_recovers_from_an_exact_snapshot(env):
    from modules.flow_gate.db import tr2_approval_attempts as db_attempts
    group = make_group("0104")
    client = full_api()
    _t2, doc_id, base = _proposal_with_history(client, group)
    r2 = json.loads(snapshot(doc_id, 2).read_bytes())
    assert [r["revision_no"] for r in recovery(client, doc_id)["revisions"]] == [3, 2, 1]

    canonical(doc_id).unlink()
    resp = client.get(base)
    assert resp.status_code == 409 and resp.json()["code"] == "tr2_body_missing", resp.text
    assert_no_host_path(resp, env["repo"], canonical(doc_id).parent)
    state = recovery(client, doc_id)
    assert state["state"] == "tr2_body_missing" and state["recoverable"] is True
    assert state["recommended_revision_no"] == 3 and state["raw"]["available"] is False
    assert all(r["usable"] for r in state["revisions"])
    # nothing moves while the proposal is unreadable: no approval, no partial edit
    blocked = approve(client, doc_id, 3, request_key="missing:0104")
    assert blocked.status_code == 409 and blocked.json()["code"] == "tr2_body_missing"
    assert db_attempts.list_by_doc(doc_id) == []
    edit = client.post(f"{base}/items", json={"expected_revision": 3, "collection": "edits",
                                              "item": dict(CREATED, id="c9")})
    assert edit.status_code == 409 and edit.json()["code"] == "tr2_body_missing"

    # restore r2: a NEW revision (4); history is not rewritten
    stale = client.post(f"{base}/recovery/restore", json={"expected_revision": 2, "revision_no": 2})
    assert stale.status_code == 409 and stale.json()["code"] == "tr2_spec_changed"
    resp = client.post(f"{base}/recovery/restore", json={"expected_revision": 3, "revision_no": 2})
    assert resp.status_code == 200, resp.text
    assert resp.json()["new_revision"] == 4 and resp.json()["restored_from_revision"] == 2
    restored = view(client, doc_id)
    assert restored["document"]["revision_no"] == 4
    assert restored["body"]["edit_spec"] == r2["edit_spec"]
    assert restored["body"]["baseline_fingerprint"] == r2["baseline_fingerprint"]  # recomputed, same source
    assert restored["readiness"]["ready"] is True
    assert snapshot(doc_id, 4).read_bytes() == canonical(doc_id).read_bytes()
    assert [r["revision_no"] for r in recovery(client, doc_id)["revisions"]] == [4, 3, 2, 1]
    missing = client.post(f"{base}/recovery/restore", json={"expected_revision": 4, "revision_no": 99})
    assert missing.status_code == 404 and missing.json()["code"] == "tr2_revision_not_found"


def test_damaged_and_schema_invalid_bodies_are_distinct_and_recoverable(env):
    group = make_group("0105")
    client = full_api()
    _t2, doc_id, base = _proposal_with_history(client, group)

    canonical(doc_id).write_bytes(b'{"tr2_version": 1, "edit_spec": ')
    resp = client.get(base)
    assert resp.status_code == 409 and resp.json()["code"] == "tr2_body_corrupt", resp.text
    raw = client.get(f"{base}/raw").json()
    assert raw["content"] == '{"tr2_version": 1, "edit_spec": '  # inspectable, downloadable
    assert recovery(client, doc_id)["raw"]["available"] is True

    canonical(doc_id).write_bytes(json.dumps({"tr2_version": 1}).encode("utf-8"))
    resp = client.get(base)
    assert resp.status_code == 409 and resp.json()["code"] == "tr2_body_schema_invalid", resp.text
    assert resp.json()["details"]["loc"] == "body"

    # a snapshot whose bytes are not what was saved is refused, not "repaired"
    snapshot(doc_id, 1).write_bytes(snapshot(doc_id, 1).read_bytes().replace(b'"high"', b'"low" '))
    info = {r["revision_no"]: r for r in recovery(client, doc_id)["revisions"]}
    assert info[1]["usable"] is False and info[1]["problem"] == "tr2_body_corrupt"
    refused = client.post(f"{base}/recovery/restore", json={"expected_revision": 3, "revision_no": 1})
    assert refused.status_code == 409 and refused.json()["code"] == "tr2_revision_unusable"

    resp = client.post(f"{base}/recovery/restore", json={"expected_revision": 3, "revision_no": 3})
    assert resp.status_code == 200 and resp.json()["new_revision"] == 4, resp.text
    assert view(client, doc_id)["document"]["revision_no"] == 4


def test_no_recovery_source_fails_closed_until_a_new_proposal_is_written(env):
    from modules.flow_gate.db import tr2_approval_attempts as db_attempts
    group = make_group("0106")
    client = full_api()
    t2, doc_id, base = _proposal_with_history(client, group)
    canonical(doc_id).unlink()
    for revision in (1, 2, 3):
        snapshot(doc_id, revision).unlink()

    state = recovery(client, doc_id)
    assert state["recoverable"] is False and state["recommended_revision_no"] is None
    assert state["approval_blocked"] is True
    assert client.get(f"{base}/raw").json()["code"] == "tr2_body_missing"
    assert approve(client, doc_id, 3, request_key="gone:0106").json()["code"] == "tr2_body_missing"
    assert db_attempts.list_by_doc(doc_id) == []

    resp = client.post(f"{base}/recovery/new", json={"expected_revision": 3})
    assert resp.status_code == 200 and resp.json()["new_revision"] == 4, resp.text
    fresh = view(client, doc_id)
    assert fresh["body"]["edit_spec"]["edits"] == [] and fresh["readiness"]["ready"] is False
    # still not approvable: an empty proposal is needs_more_work
    refused = approve(client, doc_id, 4, request_key="gone:0106:new")
    assert refused.status_code != 200
    assert doc_row(doc_id)["doc_review_status"] == "pending_review"


# ── §8 DB-level CAS across connections and processes ────────────────────────────

def _db_path(env) -> str:
    return env["store"]._db._path


def test_revision_cas_is_decided_by_the_database_not_the_process_lock(env, monkeypatch):
    from modules.flow_gate.documents import tr2_service as tr2
    group = make_group("0107")
    client = full_api()
    t2 = auto_approved_t2(group)
    doc_id = submit_tr2(group, t2)
    before = canonical(doc_id).read_bytes()
    real_root = tr2.resolve_source_root

    def other_process_writes_first(*args):
        # Another server process (its own connection, its own process lock) takes
        # revision 2 after this request passed its early check.
        script = ("import sqlite3,sys; c=sqlite3.connect(sys.argv[1]); "
                  "c.execute('UPDATE documents SET revision_no = revision_no + 1 "
                  "WHERE doc_id = ? AND revision_no = 1', [sys.argv[2]]); c.commit()")
        subprocess.run([sys.executable, "-c", script, _db_path(env), doc_id], check=True)
        return real_root(*args)

    monkeypatch.setattr(tr2, "resolve_source_root", other_process_writes_first)
    with pytest.raises(tr2.Tr2ValidationError) as error:
        tr2.save(doc_id, tr2_body(t2, edit_spec(edits=[CREATED])), actor=USER,
                 expected_revision=1)
    assert error.value.code == "tr2_spec_changed"
    assert error.value.details["current_revision_no"] == 2
    assert doc_row(doc_id)["revision_no"] == 2          # the other writer's N+1, not ours
    assert canonical(doc_id).read_bytes() == before    # our body never landed
    assert not snapshot(doc_id, 2).exists()

    # a second connection inside this process: same outcome, over HTTP
    monkeypatch.setattr(tr2, "resolve_source_root", real_root)

    def racing_connection(*args):
        conn = sqlite3.connect(_db_path(env))
        conn.execute("UPDATE documents SET revision_no = revision_no + 1 WHERE doc_id = ?", [doc_id])
        conn.commit()
        conn.close()
        return real_root(*args)

    monkeypatch.setattr(tr2, "resolve_source_root", racing_connection)
    resp = client.post(f"/api/v1/documents/{doc_id}/tr2/items",
                       json={"expected_revision": 2, "collection": "edits", "item": CREATED})
    assert resp.status_code == 409 and resp.json()["code"] == "tr2_spec_changed", resp.text
    assert canonical(doc_id).read_bytes() == before


def test_cas_matching_more_than_one_row_is_an_invariant_failure(env, monkeypatch):
    from modules.flow_gate.documents import tr2_service as tr2
    group = make_group("0108")
    t2 = auto_approved_t2(group)
    doc_id = submit_tr2(group, t2)
    before = canonical(doc_id).read_bytes()
    store = env["store"]
    monkeypatch.setattr(type(store), "_execute_affected", lambda self, sql, params=None: 2)
    with pytest.raises(tr2.Tr2ValidationError) as error:
        tr2.save(doc_id, tr2_body(t2), actor=USER, expected_revision=1)
    assert error.value.code == "tr2_history_invariant_error"
    assert canonical(doc_id).read_bytes() == before


# ── §7 authoritative readiness ───────────────────────────────────────────────────

def test_readiness_names_what_approval_would_refuse(env):
    group = make_group("0109")
    client = full_api()
    repo = env["repo"]
    t2 = auto_approved_t2(group)
    doc_id = submit_tr2(group, t2)
    base = f"/api/v1/documents/{doc_id}/tr2"
    assert view(client, doc_id)["readiness"]["ready"] is True

    def put(spec, revision):
        resp = client.put(base, json={"expected_revision": revision, "body": tr2_body(t2, spec)})
        assert resp.status_code == 200, resp.text
        return view(client, doc_id)

    # no drift, termination ready_to_apply — the old screen called these green
    missing = dict(edit_spec()["edits"][0], anchor_old="not in the file")
    state = put(edit_spec(edits=[missing]), 1)
    assert state["derived"]["live_precheck"]["drift"] is False
    assert state["readiness"]["ready"] is False
    assert (state["readiness"]["code"], state["readiness"]["reason"]) == (
        "tr2_edit_not_applicable", "anchor_missing")

    (repo / "src" / "new_module.txt").write_bytes(b"already here\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-m", "collision")
    state = put(edit_spec(edits=[CREATED]), 2)
    assert (state["readiness"]["code"], state["readiness"]["reason"]) == (
        "tr2_edit_not_applicable", "file_exists")

    state = put(edit_spec(gate={"commands": ["python -c \"print(1)\""], "apply": False}), 3)
    assert state["readiness"]["ready"] is True
    assert state["gate_admission"]["candidate_count"] == 1

    state = put(edit_spec(), 4)
    assert state["readiness"]["ready"] is True
    (repo / "src" / "stray.txt").write_bytes(b"uncommitted\n")
    state = view(client, doc_id)
    assert state["readiness"]["code"] == "tr2_worktree_dirty"
    assert approve(client, doc_id, 5, request_key="dirty:0109").json()["code"] == "tr2_worktree_dirty"


def test_readiness_after_a_reopen_requires_a_new_revision(env):
    """A Time Machine reopen puts an applied revision back in review; approval would refuse
    it (tr2_history_revision_required), so the read model must not call it ready."""
    from modules.flow_gate.services import workflow_rework_service
    from modules.flow_gate.services.mutation_policy import human_principal
    group = make_group("0110")
    client = full_api()
    (env["repo"] / "gate.ok").write_bytes(b"ok")
    t2 = auto_approved_t2(group)
    doc_id = submit_tr2(group, t2)
    assert approve(client, doc_id, 1, request_key="reopen:0110").status_code == 200
    # 0660 T0004 §3: a real reopen (status AND commit cancel). Flipping only the status
    # is the RC3 state, which test_tr2_lifecycle_0660 covers as revert_pending.
    user = {"user_id": USER, "is_admin": 1, "username": "tr2reviewer"}
    workflow_rework_service.reopen_to_target(
        doc_id=doc_id, target_seq=doc_row(doc_id)["seq"], actor=user,
        mutation_context=human_principal(user))
    state = view(client, doc_id)["readiness"]
    assert (state["ready"], state["code"]) == (False, "tr2_history_revision_required")
    again = approve(client, doc_id, 1, request_key="reopen:0110:again")
    assert again.json()["code"] == "tr2_history_revision_required"


def test_time_machine_reopen_reaches_open_tr2_screens(env, monkeypatch):
    """A rewind changes a 반영안's approval and source history outside the TR2 routes; open
    screens must hear about it (T0030 §11-6) and must not call it ready afterwards."""
    import modules.flow_gate.api.v1.events.publisher as publisher
    from modules.flow_gate.api.v1.events.event_types import EventType
    from modules.flow_gate.db import tr_commit_ledger as db_ledger
    from modules.flow_gate.services import workflow_rework_service
    from modules.flow_gate.services.mutation_policy import human_principal
    group = make_group("0111")
    client = full_api()
    (env["repo"] / "gate.ok").write_bytes(b"ok")
    t2 = auto_approved_t2(group)
    doc_id = submit_tr2(group, t2)
    assert approve(client, doc_id, 1, request_key="tm:0111").status_code == 200
    events = []
    monkeypatch.setattr(publisher, "broadcast_event_threadsafe", lambda event: events.append(event) or 1)

    user = {"user_id": USER, "is_admin": 1, "username": "tr2reviewer"}
    workflow_rework_service.reopen_to_target(
        doc_id=doc_id, target_seq=doc_row(doc_id)["seq"], actor=user,
        mutation_context=human_principal(user))

    assert doc_row(doc_id)["doc_review_status"] == "pending_review"
    row = [r for r in db_ledger.list_by_group(group["group_id"]) if r["doc_id"] == doc_id][0]
    assert row["state"] == "canceled"
    notified = [e for e in events if e.event_type == EventType.GROUP_VIEW_REFRESH
                and e.payload.get("reason") == "tr2_history_changed"]
    assert [(e.doc_id, e.audience) for e in notified] == [(doc_id, "*")]
    assert view(client, doc_id)["readiness"]["code"] == "tr2_history_revision_required"


@pytest.mark.parametrize("breakage", ["unregistered", "dir_missing"])
def test_read_model_loads_the_proposal_when_the_worktree_is_unavailable(env, monkeypatch, breakage):
    """Loading a stored 반영안 does not depend on the source (T0030 §7): with Git integration
    on but the group worktree released or gone, GET still returns the proposal, the
    source-derived facts are unknown, and readiness names the approval block."""
    from modules.flow_gate.db import git_integration as db_git
    from modules.flow_gate.services import git_service
    group = make_group("0112" if breakage == "unregistered" else "0113")
    client = full_api()
    t2 = auto_approved_t2(group)
    doc_id = submit_tr2(group, t2)
    assert view(client, doc_id)["readiness"]["ready"] is True

    if breakage == "unregistered":
        db_git.unregister_worktree(group["group_id"])
    else:
        monkeypatch.setattr(git_service, "src_root", lambda _name, _branch: env["repo"] / "gone")
    resp = client.get(f"/api/v1/documents/{doc_id}/tr2")
    assert resp.status_code == 200, resp.text
    assert_no_host_path(resp, env["repo"])
    state = resp.json()
    assert state["document"]["revision_no"] == 1
    assert [edit["id"] for edit in state["body"]["edit_spec"]["edits"]] == ["e1"]
    assert (state["readiness"]["ready"], state["readiness"]["code"]) == (
        False, "tr2_git_unavailable")
    live = state["derived"]["live_precheck"]
    assert (live["source_available"], live["live_fingerprint"], live["drift"]) == (
        False, None, None)
    assert live["baseline_fingerprint"] == state["body"]["baseline_fingerprint"]
    assert live["anchors"] is None
    assert [(f["path"], f["exists"], f["edit_ids"]) for f in state["derived"]["files"]] == [
        ("src/app.txt", None, ["e1"])]
    refused = approve(client, doc_id, 1, request_key=f"unavailable:{breakage}")
    assert refused.json()["code"] == "tr2_git_unavailable"


def test_time_machine_new_revision_reapproves_from_reverted_source(env):
    """Cancel releases ownership (0641 file policy: a canceled lineage is inactive) and the
    new approval takes it back; a newly saved edit uses the reverted source baseline."""
    from modules.flow_gate.db import tr_commit_ledger as db_ledger
    from modules.flow_gate.db import tr2_approval_attempts as db_attempts
    from modules.flow_gate.services import tr2_file_policy, workflow_rework_service
    from modules.flow_gate.services.mutation_policy import human_principal

    group = make_group("0141")
    client = full_api()
    source = env["repo"] / "src" / "app.txt"
    (env["repo"] / "gate.ok").write_bytes(b"ok")
    t2 = auto_approved_t2(group)
    doc_id = submit_tr2(group, t2)
    assert approve(client, doc_id, 1, request_key="tm:0141:first").status_code == 200
    first_head = git(env["repo"], "rev-parse", "HEAD").strip()
    assert source.read_bytes() == b"greeting = 'hi'\ncount = 1\n"

    user = {"user_id": USER, "is_admin": 1, "username": "tr2reviewer"}
    workflow_rework_service.reopen_to_target(
        doc_id=doc_id, target_seq=doc_row(doc_id)["seq"], actor=user,
        mutation_context=human_principal(user))
    canceled_head = git(env["repo"], "rev-parse", "HEAD").strip()
    assert canceled_head != first_head
    assert source.read_bytes() == b"greeting = 'hello'\ncount = 1\n"
    assert git(env["repo"], "status", "--porcelain") == ""
    assert tr2_file_policy.managed_paths(group["group_id"]) == set()
    unchanged = approve(client, doc_id, 1, request_key="tm:0141:same")
    assert unchanged.status_code == 409
    assert unchanged.json()["code"] == "tr2_history_revision_required"
    assert git(env["repo"], "rev-parse", "HEAD").strip() == canceled_head

    changed = edit_spec()
    changed["edits"] = [dict(changed["edits"][0], replacement_new="greeting = 'welcome'")]
    saved = client.put(f"/api/v1/documents/{doc_id}/tr2", json={
        "expected_revision": 1, "body": tr2_body(t2, changed)})
    assert saved.status_code == 200 and saved.json()["new_revision"] == 2, saved.text
    state = view(client, doc_id)
    assert state["readiness"]["ready"] is True
    assert state["body"]["baseline_fingerprint"] == state["derived"]["live_precheck"]["live_fingerprint"]
    assert state["body"]["baseline_fingerprint"] == db_attempts.latest_success(doc_id)["baseline_fingerprint"]
    assert approve(client, doc_id, 2, request_key="tm:0141:new").status_code == 200
    second_head = git(env["repo"], "rev-parse", "HEAD").strip()
    assert second_head != canceled_head
    assert git(env["repo"], "rev-parse", "HEAD^").strip() == canceled_head
    assert source.read_bytes() == b"greeting = 'welcome'\ncount = 1\n"
    assert git(env["repo"], "status", "--porcelain") == ""
    assert [(row["document_revision"], row["state"]) for row in db_attempts.list_by_doc(doc_id)] == [
        (2, "succeeded"), (1, "succeeded")]
    rows = [row for row in db_ledger.list_by_group(group["group_id"]) if row["doc_id"] == doc_id]
    assert len(rows) == 2
    assert sorted(row["state"] for row in rows) == ["canceled", "live"]
    assert tr2_file_policy.managed_paths(group["group_id"]) == {"src/app.txt"}


def test_time_machine_new_revision_reports_real_precheck_failure(env):
    """An incomplete or ungrounded new revision never writes source or a commit."""
    from modules.flow_gate.db import tr2_approval_attempts as db_attempts
    from modules.flow_gate.services import workflow_rework_service
    from modules.flow_gate.services.mutation_policy import human_principal

    group = make_group("0142")
    client = full_api()
    (env["repo"] / "gate.ok").write_bytes(b"ok")
    t2 = auto_approved_t2(group)
    doc_id = submit_tr2(group, t2)
    assert approve(client, doc_id, 1, request_key="tm:0142:first").status_code == 200
    user = {"user_id": USER, "is_admin": 1, "username": "tr2reviewer"}
    workflow_rework_service.reopen_to_target(
        doc_id=doc_id, target_seq=doc_row(doc_id)["seq"], actor=user,
        mutation_context=human_principal(user))
    canceled_head = git(env["repo"], "rev-parse", "HEAD").strip()
    source = env["repo"] / "src" / "app.txt"
    baseline_source = source.read_bytes()

    empty = edit_spec(termination="needs_more_work", edits=[])
    saved = client.put(f"/api/v1/documents/{doc_id}/tr2", json={
        "expected_revision": 1, "body": tr2_body(t2, empty)})
    assert saved.status_code == 200 and saved.json()["new_revision"] == 2, saved.text
    state = view(client, doc_id)
    assert state["readiness"]["code"] == "tr2_edit_not_applicable"
    assert state["readiness"]["reason"] == "needs_more_work"
    refused = approve(client, doc_id, 2, request_key="tm:0142:empty")
    assert refused.status_code == 422 and refused.json()["code"] == "tr2_edit_not_applicable"
    assert refused.json()["details"]["loc"] == "edit_spec.edits"
    assert db_attempts.latest_by_doc(doc_id)["error_code"] == "tr2_edit_not_applicable"
    assert source.read_bytes() == baseline_source
    assert git(env["repo"], "rev-parse", "HEAD").strip() == canceled_head

    wrong = edit_spec()
    wrong["edits"] = [dict(wrong["edits"][0], anchor_old="not present in source")]
    saved = client.put(f"/api/v1/documents/{doc_id}/tr2", json={
        "expected_revision": 2, "body": tr2_body(t2, wrong)})
    assert saved.status_code == 200 and saved.json()["new_revision"] == 3, saved.text
    assert view(client, doc_id)["readiness"]["reason"] == "anchor_missing"
    refused = approve(client, doc_id, 3, request_key="tm:0142:wrong")
    assert refused.status_code == 422 and refused.json()["code"] == "tr2_edit_not_applicable"
    assert db_attempts.latest_by_doc(doc_id)["error_code"] == "tr2_edit_not_applicable"
    assert source.read_bytes() == baseline_source
    assert git(env["repo"], "rev-parse", "HEAD").strip() == canceled_head
    assert git(env["repo"], "status", "--porcelain") == ""