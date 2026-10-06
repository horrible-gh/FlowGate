"""TR2 lifecycle consistency (flowgate.default.0660 T0004), connected.

Same real boundaries as ``test_tr2_connected_e2e_0565``: SQLite with every migration, a
real Git worktree in scratch, the real HTTP routes. The scenarios are the ones 0660 NR0003
reproduced on the test server:

* RC1-a — git on, the group worktree is not provisioned yet (or its provisioning lost the
  project lock): creation provisions it synchronously, or answers a retryable error with a
  public reason and leaves nothing behind.
* RC1-b/c — git off and ``src/<project_name>/<branch>`` absent: a non-retryable error the
  client can tell apart from RC1-a, until the source is actually placed.
* RC2 — a refused workflow slot write is an identified 409, not a bare 500.
* RC3 — a Time Machine reopen whose commit cancel is blocked leaves the TR2 in an explicit
  ``revert_pending`` state: no save, restore or approval until a cancel retry succeeds.
* S2 — the reopen names the documents it deleted. S3 — a step after the reopened head
  cannot be approved first.
"""
from __future__ import annotations

import pytest

from group_lock_stub import hold_group_lock

from test_tr2_connected_e2e_0565 import (  # noqa: F401 — fixtures are used by name
    MODULE, PROJECT, USER, api, approve, auto_approved_t2, edit_spec, env, git, make_group,
    repo, store, submit_tr2, tr2_body, view,
)

USER_ROW = {"user_id": USER, "is_admin": 1, "username": "tr2reviewer"}


def full_api():
    from fastapi.responses import JSONResponse
    from modules.flow_gate.documents.routers import documents as documents_routes
    from modules.flow_gate.services.git.credentials import GitServiceError, git_error_envelope
    client = api()
    client.app.include_router(documents_routes.router, prefix="/api/v1")

    # The production app answers GitServiceError once, globally (server/routers/main.py).
    @client.app.exception_handler(GitServiceError)
    async def _git_error(_request, exc):  # noqa: ANN001
        return JSONResponse(status_code=exc.status, content=git_error_envelope(exc))

    return client


def next_empty(client, group: dict, prev_doc_id: str, type_code: str = "TR2", title: str = "반영안"):
    return client.post("/api/v1/documents/next-empty", json={
        "project_id": PROJECT, "group_id": group["group_id"], "prev_doc_id": prev_doc_id,
        "type_code": type_code, "title": title, "module": MODULE})


def doc_row(doc_id: str):
    from modules.flow_gate.db import documents as db_docs
    return db_docs.get_by_id(doc_id)


def group_doc_ids(group: dict) -> list[str]:
    from modules.flow_gate.db import documents as db_docs
    return sorted(d["doc_id"] for d in db_docs.list_documents(
        project_id=PROJECT, group_id=group["group_id"], limit=200))


def slot(group: dict, type_code: str) -> dict:
    from modules.flow_gate.db import workflow_sequences as db_wfseq
    seq = db_wfseq.get_sequence_by_doc_id(group["root"])
    return next(item for item in db_wfseq.get_sequence_items(seq["id"]) if item["type"] == type_code)


def revision_rows(group: dict) -> list[dict]:
    from modules.flow_gate.db.connection import get_store
    return get_store()._fetch_all(
        "SELECT doc_id, revision_no FROM document_revisions WHERE doc_id LIKE ?",
        [group["group_id"] + ".%"])


def group_storage_files(group: dict) -> list[str]:
    from modules.flow_gate.storage import paths as storage_paths
    root = storage_paths.get_storage_root(PROJECT)
    code = group["code"]
    return sorted(str(p.relative_to(root)) for p in root.rglob("*")
                  if p.is_file() and f"{MODULE}/{code}/" in p.as_posix())


def assert_nothing_left(group: dict, docs_before: list[str]) -> None:
    assert group_doc_ids(group) == docs_before
    assert slot(group, "TR2")["result_doc_id"] is None
    assert [r for r in revision_rows(group) if r["doc_id"].endswith("-TR2")] == []
    assert [f for f in group_storage_files(group) if "TR2" in f] == []


def assert_public(resp) -> dict:
    body = resp.json()
    text = resp.text
    assert ":\\\\" not in text and ":/" not in text.replace("://", ""), text
    assert "Traceback" not in text and "stderr" not in text, text
    return body


# ── T1 normal creation ────────────────────────────────────────────────────────────

def test_t1_tr2_next_empty_with_a_ready_worktree_creates_row_file_r1_and_slot(env):
    from modules.flow_gate.documents import tr2_service
    group = make_group("0601")
    client = full_api()
    t2 = auto_approved_t2(group)

    resp = next_empty(client, group, t2)
    assert resp.status_code == 201, resp.text
    doc_id = resp.json()["doc_id"]
    row = doc_row(doc_id)
    assert row is not None and row["revision_no"] == 1
    assert tr2_service.canonical_path_for_doc(row).is_file()
    assert tr2_service.snapshot_path(row, 1).is_file()
    assert (doc_id, 1) in {(r["doc_id"], r["revision_no"]) for r in revision_rows(group)}
    assert slot(group, "TR2")["result_doc_id"] == doc_id


# ── T2 git on, worktree not ready ─────────────────────────────────────────────────

def _unprovision(group: dict, error: str = "git_busy") -> None:
    from modules.flow_gate.db import git_integration as db_git
    db_git.unregister_worktree(group["group_id"])
    db_git.upsert_provision_failure(group["group_id"], PROJECT, "work", error)


def test_t2_unprovisioned_worktree_is_ensured_synchronously_on_create(env, monkeypatch):
    from modules.flow_gate.db import git_integration as db_git
    from modules.flow_gate.services import git_service
    group = make_group("0602")
    client = full_api()
    t2 = auto_approved_t2(group)
    _unprovision(group)
    calls = []

    def provisioned(project_id, module, group_id, trigger="remote_access", start_point=None):
        calls.append((project_id, module, group_id, trigger))
        db_git.register_worktree(group_id, project_id, "work")
        db_git.clear_provision_failure(group_id)
        return "ok"

    monkeypatch.setattr(git_service, "ensure_worktree", provisioned)
    resp = next_empty(client, group, t2)
    assert resp.status_code == 201, resp.text
    assert calls == [(PROJECT, MODULE, group["group_id"], "tr2_create")]
    assert slot(group, "TR2")["result_doc_id"] == resp.json()["doc_id"]


def test_t2_lock_held_through_ensure_is_retryable_leaves_nothing_then_succeeds(env, monkeypatch):
    """The real ensure_worktree finds the Group's G taken: 503 retryable + git_busy."""
    from modules.flow_gate.db import git_integration as db_git
    from modules.flow_gate.services import git_service
    from modules.flow_gate.services.git import lock_manager as lm
    group = make_group("0603")
    client = full_api()
    t2 = auto_approved_t2(group)
    _unprovision(group)
    docs_before = group_doc_ids(group)
    monkeypatch.setattr(git_service, "git_available", lambda: True)
    monkeypatch.setattr(lm, "wait_budget", lambda _domain, _mode: 0.0)
    # The group has no base checkout here, so ensure goes base-first (B); a Group slot
    # that already has its base would meet the Group's G. Hold both.
    ctx = lm.new_context()
    held = []
    try:
        for domain, gid, kind in (("G", group["group_id"], "source_mutation"),
                                  ("B", None, "base_mutation")):
            out = lm.acquire(domain, PROJECT, group_id=gid, holder_kind=kind,
                             mode=lm.NO_WAIT, ctx=ctx)
            assert out.ok, out
            held.append(out.lock_key)
        if True:
            for _attempt in range(2):  # same UI action twice: same answer, still nothing left
                resp = next_empty(client, group, t2)
                assert resp.status_code == 503, resp.text
                body = assert_public(resp)
                assert body["code"] == "tr2_git_unavailable"
                assert body["retryable"] is True
                assert body["details"]["reason"] == "git_busy"
                assert_nothing_left(group, docs_before)
    finally:
        for key in reversed(held):
            lm.release(ctx, key)

    def provisioned(project_id, module, group_id, trigger="remote_access", start_point=None):
        db_git.register_worktree(group_id, project_id, "work")
        db_git.clear_provision_failure(group_id)
        return "ok"

    monkeypatch.setattr(git_service, "ensure_worktree", provisioned)
    resp = next_empty(client, group, t2)
    assert resp.status_code == 201, resp.text
    # ensure runs before the number is reserved: the refused attempts consumed none.
    assert resp.json()["doc_id"] == f"{group['group_id']}.0003-TR2"


def test_t2_persistent_provision_failure_is_not_retryable_and_hides_stderr(env, monkeypatch):
    from modules.flow_gate.services import git_service
    group = make_group("0604")
    client = full_api()
    t2 = auto_approved_t2(group)
    _unprovision(group, "fatal: 'C:/secret/base' is not a git repository")
    docs_before = group_doc_ids(group)
    monkeypatch.setattr(git_service, "ensure_worktree", lambda *a, **k: "failed")
    resp = next_empty(client, group, t2)
    assert resp.status_code == 503, resp.text
    body = assert_public(resp)
    assert (body["code"], body["retryable"]) == ("tr2_worktree_provision_failed", False)
    assert body["details"] == {"loc": "source_root", "reason": "worktree_provision_failed"}
    assert "secret" not in resp.text
    assert_nothing_left(group, docs_before)


# ── T3 git off, source root missing ───────────────────────────────────────────────

def test_t3_git_off_missing_source_is_not_retryable_until_the_source_exists(env):
    from modules.flow_gate.db import git_integration as db_git
    from modules.flow_gate.storage import paths as storage_paths
    group = make_group("0605")
    client = full_api()
    t2 = auto_approved_t2(group)
    db_git.upsert_config(PROJECT, {"repo_url": "https://example.invalid/tr2.git",
                                   "enabled": False, "base_branch": "main", "secret_enc": None})
    docs_before = group_doc_ids(group)

    pre = client.get("/api/v1/documents/next-empty/preflight",
                     params={"prev_doc_id": t2, "type_code": "TR2"})
    assert pre.status_code == 200, pre.text
    assert pre.json()["ok"] is False
    assert pre.json()["error"]["code"] == "tr2_source_root_missing"
    assert pre.json()["error"]["retryable"] is False

    for _attempt in range(3):  # retrying without placing the source never helps
        resp = next_empty(client, group, t2)
        assert resp.status_code == 409, resp.text
        body = assert_public(resp)
        assert (body["code"], body["retryable"]) == ("tr2_source_root_missing", False)
        assert body["details"]["reason"] == "project_source_missing"
        assert_nothing_left(group, docs_before)

    source = storage_paths.src_root("TR2 E2E", "main")
    source.mkdir(parents=True, exist_ok=True)
    assert client.get("/api/v1/documents/next-empty/preflight",
                      params={"prev_doc_id": t2, "type_code": "TR2"}).json() == {"ok": True}
    resp = next_empty(client, group, t2)
    assert resp.status_code == 201, resp.text


def test_t3_git_on_unready_worktree_preflight_defers_to_create(env):
    group = make_group("0606")
    client = full_api()
    t2 = auto_approved_t2(group)
    _unprovision(group)
    pre = client.get("/api/v1/documents/next-empty/preflight",
                     params={"prev_doc_id": t2, "type_code": "TR2"})
    assert pre.json() == {"ok": True, "provisioning": True}


def test_public_reason_is_a_closed_enum():
    from modules.flow_gate.documents.tr2_errors import error_payload
    leaked = error_payload("tr2_git_unavailable", details={"reason": "fatal: C:/x stderr"})
    assert "reason" not in leaked["details"]
    kept = error_payload("tr2_git_unavailable", details={"reason": "git_busy"})
    assert (kept["retryable"], kept["details"]["reason"]) == (True, "git_busy")
    missing = error_payload("tr2_source_root_missing", details={"reason": "project_source_missing"})
    assert (missing["retryable"], missing["details"]["reason"]) == (False, "project_source_missing")


# ── RC2 slot conflict ─────────────────────────────────────────────────────────────

def test_t4_workflow_slot_conflict_is_an_identified_409(env, monkeypatch):
    from modules.flow_gate.workflow import pipeline_service
    group = make_group("0607")
    client = full_api()
    t2 = auto_approved_t2(group)
    docs_before = group_doc_ids(group)

    def occupied(*, item_id, registered_doc_id, **_kw):
        raise pipeline_service.WorkflowSlotConflictError(
            item_id=item_id, existing_doc_id=f"{group['group_id']}.0009-TR2",
            requested_doc_id=registered_doc_id)

    monkeypatch.setattr(pipeline_service, "register_workflow_result", occupied)
    resp = next_empty(client, group, t2)
    assert resp.status_code == 409, resp.text
    assert resp.json()["error"]["code"] == "workflow_slot_occupied"
    assert_nothing_left(group, docs_before)


# ── RC3 blocked commit cancel ─────────────────────────────────────────────────────

def _approved_tr2(env, group: dict, client) -> tuple[str, str]:
    (env["repo"] / "gate.ok").write_bytes(b"ok")
    t2 = auto_approved_t2(group)
    doc_id = submit_tr2(group, t2)
    assert approve(client, doc_id, 1, request_key=f"0660:{group['code']}").status_code == 200
    return t2, doc_id


def _reopen(doc_id: str) -> dict:
    from modules.flow_gate.services import workflow_rework_service
    from modules.flow_gate.services.mutation_policy import human_principal
    return workflow_rework_service.reopen_to_target(
        doc_id=doc_id, target_seq=doc_row(doc_id)["seq"], actor=USER_ROW,
        mutation_context=human_principal(USER_ROW))


def test_t6_blocked_cancel_keeps_tr2_revert_pending_until_the_retry_succeeds(env, monkeypatch):
    from modules.flow_gate.db import tr_commit_ledger as db_ledger
    from modules.flow_gate.services import git_service
    group = make_group("0608")
    client = full_api()
    repo = env["repo"]
    t2, doc_id = _approved_tr2(env, group, client)
    approved_head = git(repo, "rev-parse", "HEAD").strip()
    source = repo / "src" / "app.txt"
    assert source.read_bytes() == b"greeting = 'hi'\ncount = 1\n"
    monkeypatch.setattr(git_service, "CANCEL_LOCK_WAIT_SEC", 0)
    monkeypatch.setattr("modules.flow_gate.services.git.lock.LOCK_WAIT_SEC", 0)

    with hold_group_lock(PROJECT, group["group_id"]):
        result = _reopen(doc_id)

    # The rewind stands (D0005 K8) but the TR2 is not reopened for editing.
    assert result["tr_commit_cancel"]["blocked_reason"] == "git_busy"
    assert result["tr_commit_cancel"]["retryable"] is True
    assert result["revert_pending"] == [doc_id]
    assert doc_row(doc_id)["doc_review_status"] == "pending_review"
    assert [r["state"] for r in db_ledger.live_rows(group["group_id"], [doc_id])] == ["live"]
    assert git(repo, "rev-parse", "HEAD").strip() == approved_head

    state = view(client, doc_id)
    assert state["mutation"] == {"allowed": False, "reason": "revert_pending"}
    assert (state["readiness"]["ready"], state["readiness"]["code"]) == (False, "tr2_revert_pending")
    saved = client.put(f"/api/v1/documents/{doc_id}/tr2", json={
        "expected_revision": 1, "body": tr2_body(t2)})
    assert saved.status_code == 409 and saved.json()["code"] == "tr2_revert_pending", saved.text
    restored = client.post(f"/api/v1/documents/{doc_id}/tr2/recovery/restore",
                           json={"revision_no": 1, "expected_revision": 1})
    assert restored.status_code == 409 and restored.json()["code"] == "tr2_revert_pending", restored.text
    refused = approve(client, doc_id, 1, request_key="0660:0608:blocked")
    assert refused.status_code == 409 and refused.json()["code"] == "tr2_revert_pending"
    assert doc_row(doc_id)["revision_no"] == 1
    assert git(repo, "rev-parse", "HEAD").strip() == approved_head
    assert git(repo, "status", "--porcelain") == ""

    # Lock released: the product's own retry path cancels the commit, then editing works.
    retry = client.post(f"/api/v1/documents/workflow/{doc_id}/return-point/cancel-commits")
    assert retry.status_code == 200, retry.text
    assert [c["doc_id"] for c in retry.json()["tr_commit_cancel"]["canceled"]] == [doc_id]
    assert db_ledger.live_rows(group["group_id"], [doc_id]) == []
    assert source.read_bytes() == b"greeting = 'hello'\ncount = 1\n"
    assert git(repo, "rev-parse", "HEAD").strip() != approved_head
    state = view(client, doc_id)
    assert state["mutation"] == {"allowed": True, "reason": None}
    assert state["readiness"]["code"] == "tr2_history_revision_required"

    saved = client.put(f"/api/v1/documents/{doc_id}/tr2", json={
        "expected_revision": 1, "body": tr2_body(t2)})
    assert saved.status_code == 200 and saved.json()["new_revision"] == 2, saved.text
    assert view(client, doc_id)["readiness"]["ready"] is True
    assert approve(client, doc_id, 2, request_key="0660:0608:after").status_code == 200
    assert source.read_bytes() == b"greeting = 'hi'\ncount = 1\n"
    assert git(repo, "status", "--porcelain") == ""
    rows = [r for r in db_ledger.list_by_group(group["group_id"]) if r["doc_id"] == doc_id]
    assert sorted(r["state"] for r in rows) == ["canceled", "live"]


def test_t6_contaminated_state_without_cancel_is_guarded(env):
    """The chat.0006 shape: reopened status, commit never canceled. Guarded, not editable."""
    from modules.flow_gate.db import documents as db_docs
    group = make_group("0609")
    client = full_api()
    t2, doc_id = _approved_tr2(env, group, client)
    db_docs.update(doc_id, {"doc_review_status": "pending_review"})
    state = view(client, doc_id)
    assert state["mutation"]["reason"] == "revert_pending"
    assert state["readiness"]["code"] == "tr2_revert_pending"
    saved = client.put(f"/api/v1/documents/{doc_id}/tr2", json={
        "expected_revision": 1, "body": tr2_body(t2)})
    assert saved.status_code == 409 and saved.json()["code"] == "tr2_revert_pending"


def test_t5_unblocked_reopen_reports_nothing_pending(env):
    group = make_group("0610")
    client = full_api()
    _t2, doc_id = _approved_tr2(env, group, client)
    result = _reopen(doc_id)
    assert result["revert_pending"] == []
    assert [c["doc_id"] for c in result["tr_commit_cancel"]["canceled"]] == [doc_id]
    again = approve(client, doc_id, 1, request_key="0660:0610:same")
    assert again.status_code == 409 and again.json()["code"] == "tr2_history_revision_required"


# ── S2 deleted documents, S3 reopened head ────────────────────────────────────────

def test_s2_reopen_names_the_documents_it_deleted(env):
    from modules.flow_gate.db import documents as db_docs
    group = make_group("0611")
    client = full_api()
    _t2, doc_id = _approved_tr2(env, group, client)
    ac_id = f"{group['group_id']}.0099-AC"
    db_docs.create({"doc_id": ac_id, "project_id": PROJECT, "type_code": "AC", "seq": 99,
                    "title": "Final Approval", "group_id": group["group_id"], "module": MODULE,
                    "owner_id": USER, "revision_no": 0})
    result = _reopen(doc_id)
    assert result["deleted"] == [ac_id]
    assert doc_row(ac_id) is None


def test_s3_step_after_the_reopened_head_cannot_be_approved(env):
    from modules.flow_gate.documents import tr2_service
    group = make_group("0612")
    client = full_api()
    _t2, tr2_id = _approved_tr2(env, group, client)
    created = next_empty(client, group, tr2_id, type_code="T", title="반영지시")
    assert created.status_code == 201, created.text
    later = created.json()["doc_id"]

    _reopen(tr2_id)
    head_before = tr2_service.effective_head_for(tr2_id)
    assert head_before["result_doc_id"] == tr2_id
    refused = approve(client, later, int(doc_row(later)["revision_no"] or 0))
    assert refused.status_code == 409, refused.text
    assert "헤드" in refused.json()["detail"]
    assert doc_row(later)["doc_review_status"] == "pending_review"
    assert tr2_service.effective_head_for(tr2_id)["id"] == head_before["id"]


# ── RC1 the save response carries only the public enum ────────────────────────────

def test_rc1_save_with_unready_worktree_answers_only_public_detail_keys(env):
    """An existing TR2 saved while its worktree is unregistered: the internal SRC_ROOT
    name (``worktree_unregistered``) stays in the log, the response names the public enum."""
    from modules.flow_gate.db import git_integration as db_git
    group = make_group("0613")
    client = full_api()
    t2 = auto_approved_t2(group)
    doc_id = submit_tr2(group, t2)
    db_git.unregister_worktree(group["group_id"])

    saved = client.put(f"/api/v1/documents/{doc_id}/tr2", json={
        "expected_revision": 1, "body": tr2_body(t2)})
    assert saved.status_code == 503, saved.text
    body = assert_public(saved)
    assert (body["code"], body["retryable"]) == ("tr2_git_unavailable", True)
    assert body["details"] == {"loc": "source_root", "reason": "worktree_provisioning"}
    assert "worktree_unregistered" not in saved.text
    assert doc_row(doc_id)["revision_no"] == 1


def test_rc1_public_details_drop_every_undocumented_key():
    from modules.flow_gate.documents.tr2_errors import error_payload
    for code, reason in (("tr2_git_unavailable", "worktree_provisioning"),
                         ("tr2_source_root_missing", "project_source_missing"),
                         ("tr2_worktree_provision_failed", "worktree_provision_failed"),
                         ("tr2_revert_pending", "commit_cancel_blocked")):
        payload = error_payload(code, details={
            "loc": "source_root", "reason": reason, "source_root": "resolution_error",
            "provision_error": "fatal: x", "group_id": "g"})
        assert payload["details"] == {"loc": "source_root", "reason": reason}, code


# ── RC3 the pending state holds the whole group, not only the TR2 ─────────────────

def _blocked_reopen(group: dict, tr2_id: str, monkeypatch) -> dict:
    from modules.flow_gate.services import git_service
    monkeypatch.setattr(git_service, "CANCEL_LOCK_WAIT_SEC", 0)
    monkeypatch.setattr("modules.flow_gate.services.git.lock.LOCK_WAIT_SEC", 0)
    with hold_group_lock(PROJECT, group["group_id"]):
        result = _reopen(tr2_id)
    assert result["tr_commit_cancel"]["blocked_reason"] == "git_busy"
    assert result["revert_pending"] == [tr2_id]
    return result


def test_rc3_second_reopen_while_cancel_pending_is_refused_until_the_retry(env, monkeypatch):
    """Blocked cancel, then a rewind to an EARLIER step: refused, and nothing in the group
    advances (approve / next step / final approval / forward restore / workflow edit /
    reapply) until the cancel retry succeeds. Only then does the earlier rewind run."""
    from modules.flow_gate.db import tr_commit_ledger as db_ledger
    from modules.flow_gate.db import workflow_return_points as db_rp
    from modules.flow_gate.documents import tr2_service
    group = make_group("0614")
    client = full_api()
    repo = env["repo"]
    t2, tr2_id = _approved_tr2(env, group, client)
    created = next_empty(client, group, tr2_id, type_code="T", title="반영지시")
    assert created.status_code == 201, created.text
    later = created.json()["doc_id"]
    approved_head = git(repo, "rev-parse", "HEAD").strip()

    _blocked_reopen(group, tr2_id, monkeypatch)
    head = tr2_service.effective_head_for(tr2_id)
    rp_before = db_rp.get_by_group(group["group_id"])
    steps_before = doc_row(group["root"]).get("workflow_steps")
    assert tr2_service.group_revert_pending(group["group_id"]) == [tr2_id]
    t2_seq = doc_row(t2)["seq"]

    # A second rewind to the earlier T2 step over HTTP: identified 409, nothing moved.
    again = client.post("/api/v1/documents/workflow/reopen",
                        json={"doc_id": tr2_id, "target_seq": t2_seq})
    assert again.status_code == 409, again.text
    assert again.json()["error"]["code"] == "workflow_revert_pending"
    assert again.json()["error"]["details"] == {"doc_ids": [tr2_id]}
    assert doc_row(t2)["doc_review_status"] == "approved"
    assert doc_row(tr2_id)["doc_review_status"] == "pending_review"
    assert tr2_service.effective_head_for(tr2_id)["id"] == head["id"]
    assert db_rp.get_by_group(group["group_id"]) == rp_before
    assert [r["state"] for r in db_ledger.live_rows(group["group_id"], [tr2_id])] == ["live"]
    assert git(repo, "rev-parse", "HEAD").strip() == approved_head

    # No other group workflow mutation gets through either.
    refused = approve(client, later, int(doc_row(later)["revision_no"] or 0))
    assert refused.status_code == 409, refused.text
    assert "커밋 취소" in refused.json()["detail"]
    assert doc_row(later)["doc_review_status"] == "pending_review"
    own = approve(client, tr2_id, 1, request_key="0660:0614:pending")
    assert own.status_code == 409 and own.json()["code"] == "tr2_revert_pending", own.text
    nxt = next_empty(client, group, tr2_id, type_code="T", title="또 다른 지시")
    assert nxt.status_code == 409, nxt.text
    assert nxt.json()["error"]["code"] == "workflow_revert_pending"
    for path, payload in (
        ("/api/v1/documents/workflow/final-approval", {"doc_id": group["root"]}),
        ("/api/v1/documents/workflow/restore", {"doc_id": tr2_id, "destination_seq": 99}),
        (f"/api/v1/documents/workflow/{tr2_id}/return-point/reapply-commits", {}),
    ):
        resp = client.post(path, json=payload)
        assert resp.status_code == 409, (path, resp.text)
        assert resp.json()["error"]["code"] == "workflow_revert_pending", path
    edit = client.patch(f"/api/v1/documents/{group['root']}/workflow", json={"workflow_steps": []})
    assert edit.status_code == 409, edit.text
    assert edit.json()["error"]["code"] == "workflow_revert_pending"
    assert doc_row(group["root"]).get("workflow_steps") == steps_before
    assert tr2_service.effective_head_for(tr2_id)["id"] == head["id"]
    assert db_rp.get_by_group(group["group_id"]) == rp_before

    # The one path left open: the cancel retry. It clears the group, and the earlier
    # rewind that was refused above now runs normally.
    retry = client.post(f"/api/v1/documents/workflow/{tr2_id}/return-point/cancel-commits")
    assert retry.status_code == 200, retry.text
    assert [c["doc_id"] for c in retry.json()["tr_commit_cancel"]["canceled"]] == [tr2_id]
    assert tr2_service.group_revert_pending(group["group_id"]) == []
    assert git(repo, "status", "--porcelain") == ""
    again = client.post("/api/v1/documents/workflow/reopen",
                        json={"doc_id": tr2_id, "target_seq": t2_seq})
    assert again.status_code == 200, again.text
    assert again.json()["revert_pending"] == []
    assert doc_row(t2)["doc_review_status"] == "pending_review"


def test_rc3_service_level_reopen_is_refused_too(env, monkeypatch):
    """Automatic callers (failed-TS auto reopen) go through the same service boundary."""
    from modules.flow_gate.services.git.credentials import GitServiceError
    group = make_group("0615")
    client = full_api()
    t2, tr2_id = _approved_tr2(env, group, client)
    _blocked_reopen(group, tr2_id, monkeypatch)
    with pytest.raises(GitServiceError) as caught:
        _reopen(t2)
    assert (caught.value.status, caught.value.code) == (409, "workflow_revert_pending")
    assert doc_row(t2)["doc_review_status"] == "approved"


# ── S3 the head guard fails closed ────────────────────────────────────────────────

def _later_step(env, group: dict, client) -> str:
    _t2, tr2_id = _approved_tr2(env, group, client)
    created = next_empty(client, group, tr2_id, type_code="T", title="반영지시")
    assert created.status_code == 201, created.text
    return created.json()["doc_id"]


def test_s3_head_lookup_failure_refuses_the_approval(env, monkeypatch):
    from modules.flow_gate.workflow import pipeline_service
    group = make_group("0616")
    client = full_api()
    later = _later_step(env, group, client)

    def broken(_doc_id):
        raise RuntimeError("database is locked")

    monkeypatch.setattr(pipeline_service.db_wfseq, "get_item_by_result_doc_id", broken)
    refused = approve(client, later, int(doc_row(later)["revision_no"] or 0))
    assert refused.status_code == 409, refused.text
    assert "확인할 수 없어" in refused.json()["detail"]
    assert "database is locked" not in refused.text
    assert doc_row(later)["doc_review_status"] == "pending_review"


def test_s3_missing_head_refuses_the_approval(env, monkeypatch):
    from modules.flow_gate.workflow import pipeline_service
    group = make_group("0617")
    client = full_api()
    later = _later_step(env, group, client)
    monkeypatch.setattr(pipeline_service.db_wfseq, "get_effective_head", lambda _sid: None)
    refused = approve(client, later, int(doc_row(later)["revision_no"] or 0))
    assert refused.status_code == 409, refused.text
    assert "확인할 수 없어" in refused.json()["detail"]
    assert doc_row(later)["doc_review_status"] == "pending_review"


def test_s3_readable_head_passes_the_guard_to_the_next_rule(env):
    """Control: with a readable head the guard lets the in-order step through; the empty
    next-empty T is then refused by the ordinary empty-body rule, not by the head guard."""
    group = make_group("0618")
    client = full_api()
    later = _later_step(env, group, client)
    resp = approve(client, later, int(doc_row(later)["revision_no"] or 0))
    assert resp.status_code == 409, resp.text
    assert "본문이 비어" in resp.json()["detail"]
    assert "헤드" not in resp.json()["detail"]
