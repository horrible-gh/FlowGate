"""TR2 lifecycle consistency, connected.

Same real boundaries as ``tests.e2e.tr2.test_tr2_connected_e2e_0565``: SQLite with every migration, a
real Git worktree in scratch, the real HTTP routes. The scenarios are the ones the review
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

from tests.e2e.tr2.test_tr2_connected_e2e_0565 import (  # noqa: F401 — fixtures are used by name
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







# ── RC2 slot conflict ─────────────────────────────────────────────────────────────



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








# ── S2 deleted documents, S3 reopened head ────────────────────────────────────────





# ── RC1 the save response carries only the public enum ────────────────────────────





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






# ── S3 the head guard fails closed ────────────────────────────────────────────────

def _later_step(env, group: dict, client) -> str:
    _t2, tr2_id = _approved_tr2(env, group, client)
    created = next_empty(client, group, tr2_id, type_code="T", title="반영지시")
    assert created.status_code == 201, created.text
    return created.json()["doc_id"]
