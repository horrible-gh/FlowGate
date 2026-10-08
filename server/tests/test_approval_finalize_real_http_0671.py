"""Real HTTP approval/finalize with a real Git body and a real push (flowgate.default.0671
T0002 / 0671 TR0003's own last rejection, finding 1).

test_merge_timeout_recovery_0607.py already drives the real final-approval route and a real
Git body end to end, but it calls ``workflow.document_review_transition_rpc`` directly as a
Python coroutine -- never through the actual FastAPI/HTTP layer -- and every case there leaves
the worktree already committed before approving, so F2's absorb-commit path (an uncommitted
worker edit folded into one commit carrying ``FlowGate-Freeze: {job_id}``) never actually
runs. This file closes both gaps on the SAME real harness (real bare origin, real
``ensure_worktree``, real sqlite migrations):

* ``test_http_approve_real_merge_absorbs_uncommitted_edit_and_pushes`` -- a real
  ``TestClient`` POST to ``/api/v1/documents/review_transitions/approve`` (not a direct
  function call) approves a group whose worktree has an UNCOMMITTED edit. The real
  ``approval_freeze.run_freeze`` F1-F4 must absorb that edit into a commit tagged
  ``FlowGate-Freeze: <job_id>`` under G before the real Git merge body runs under B+R, and
  the merge must really reach the bare origin.
* ``test_http_approve_queues_behind_push_rejection_then_real_runner_reclaims_it`` -- the same
  real HTTP route, but the real push is rejected once (git_service._run_git wrapped to return
  the real "[rejected] ... (fetch first)" exit the way origin really answers a stale push).
  The request must come back 200 with the approval queued/deferred (not failed), the real job
  left in ``retry_wait``, and the real Job Runner's next claim (``_drive_jobs``) must finish
  the SAME approval and really reach origin -- the wait/blocked/reclaim contract T0002 asks
  for, observed through the HTTP response and the real job/lock state, not a mocked stand-in.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from test_git_integration_0115 import (  # noqa: F401 -- fixtures are used by name
    _git,
    needs_git,
    patch_store,
    tmp_db,
)

USER = {"user_id": "reviewer", "is_admin": True}
PROJECT = "httpfprj"
PROJECT_NAME = "HttpFPrj"


@pytest.fixture(scope="module")
def project(patch_store, tmp_db):
    from modules.flow_gate.db import projects

    projects.create({"project_id": PROJECT, "project_name": PROJECT_NAME})
    yield


@pytest.fixture(scope="module")
def origin_repo(project):
    import shutil
    import tempfile
    from modules.flow_gate.services import git_service as svc

    tmp = Path(tempfile.mkdtemp(prefix="fg-httpf-origin-"))
    bare = tmp / "origin.git"
    seedwt = tmp / "seedwt"
    _git(["init", "--bare", "-b", "main", str(bare)])
    _git(["init", "-b", "main", str(seedwt)])
    (seedwt / "README.md").write_text("hello\n", encoding="utf-8")
    _git(["add", "-A"], cwd=seedwt)
    _git(["commit", "-m", "init"], cwd=seedwt)
    _git(["remote", "add", "origin", str(bare)], cwd=seedwt)
    _git(["push", "origin", "main"], cwd=seedwt)

    svc.save_config(PROJECT, {
        "repo_url": bare.as_uri(),
        "provider": "generic",
        "base_branch": "main",
        "default_finalize_action": "merge",
        "enabled": True,
    })
    yield {"bare": bare, "seedwt": seedwt, "tmp": tmp}
    svc.delete_config(PROJECT)
    shutil.rmtree(tmp, ignore_errors=True)


def _seed_final_approval_group(group_id: str) -> tuple[str, str]:
    from modules.flow_gate.db import documents as db_docs
    from modules.flow_gate.db import groups as db_groups
    from modules.flow_gate.db import workflow_sequences as db_wfseq
    from modules.flow_gate.db.connection import get_store, now_iso

    store = get_store()
    if store._fetch_one("SELECT 1 AS ok FROM users WHERE user_id = ?", [USER["user_id"]]) is None:
        now = now_iso()
        store._execute(
            "INSERT INTO users (user_id, username, email, password, is_active, is_admin,"
            " first_login_required, created_at, updated_at)"
            " VALUES (?, ?, ?, 'x', 1, 1, 0, ?, ?)",
            [USER["user_id"], USER["user_id"], USER["user_id"] + "@test", now, now],
        )
    if db_groups.get_by_id(group_id) is None:
        db_groups.create({
            "group_id": group_id, "project_id": PROJECT,
            "module": "default", "title": "http real finalize",
        })
    root_id = f"{group_id}.0001-R"
    ac_id = f"{group_id}.0002-AC"
    if db_docs.get_by_id(root_id) is None:
        db_docs.create({
            "doc_id": root_id, "project_id": PROJECT, "module": "default",
            "group_id": group_id, "type_code": "R", "seq": 1, "title": "root",
            "file_path": f"documents/{group_id}/0001-R.md",
        })
    db_docs.update(root_id, {"doc_review_status": "wf_in_progress"})
    if db_wfseq.get_sequence_by_doc_id(root_id) is None:
        db_wfseq.insert_sequence(root_id)
    if db_docs.get_by_id(ac_id) is None:
        db_docs.create({
            "doc_id": ac_id, "project_id": PROJECT, "module": "default",
            "group_id": group_id, "type_code": "AC", "seq": 2,
            "title": "final approval", "target_id": root_id,
        })
    db_docs.update(ac_id, {"doc_review_status": "pending_review", "target_id": root_id})
    return root_id, ac_id


def _drive_jobs(group_id: str, limit: int = 6) -> int:
    from modules.flow_gate.db import operation_job as db_jobs
    from modules.flow_gate.db.connection import get_store
    from modules.flow_gate.services.git import job_runner, worktree_cleanup

    worktree_cleanup.install()
    ran = 0
    for _ in range(limit):
        progressed = False
        for kind in ("final_approval_publish", "worktree_cleanup"):
            for job in db_jobs.jobs_of_group(group_id, kind, ("pending", "blocked", "retry_wait")):
                with get_store().transaction():
                    get_store()._execute(
                        "UPDATE operation_job SET available_at = ? WHERE job_id = ?",
                        ["2000-01-01T00:00:00+00:00", job["job_id"]],
                    )
                claim = job_runner.try_claim(job["job_id"])
                if claim is not None:
                    job_runner.execute(claim)
                    progressed = ran = ran + 1
        if not progressed:
            break
    return ran


def _review_status(doc_id: str) -> str:
    from modules.flow_gate.db import documents as db_docs

    return (db_docs.get_by_id(doc_id) or {}).get("doc_review_status")


def _job(group_id: str) -> dict:
    from approval_job_fakes import job_of

    return job_of(group_id)


def _branch(group_id: str) -> str:
    return group_id.replace(".", "_")


def _base() -> Path:
    from modules.flow_gate.storage.paths import src_root

    return src_root(PROJECT_NAME, "main")


def _worktree(group_id: str) -> Path:
    from modules.flow_gate.storage.paths import src_root

    return src_root(PROJECT_NAME, _branch(group_id))


def _rev(repo: Path, rev: str) -> str:
    return _git(["rev-parse", rev], cwd=repo).strip()


def _origin_head(origin) -> str:
    return _git(["rev-parse", "main"], cwd=origin["bare"]).strip()


def _is_push_base(args) -> bool:
    return list(args[:3]) == ["push", "origin", "main"]


def _reject_push(match):
    from modules.flow_gate.services import git_service as svc

    original = svc._run_git

    def _wrapped(args, **kwargs):
        if match(args):
            return subprocess.CompletedProcess(args, 1, "", "! [rejected] main -> main (fetch first)")
        return original(args, **kwargs)

    return _wrapped


def api():
    """The real HTTP surface: the generic review-transition route, nothing stubbed."""
    from fastapi import FastAPI
    from starlette.testclient import TestClient
    from modules.flow_gate.auth.middleware import get_current_user
    from modules.flow_gate.workflow.routers import workflow as workflow_routes

    app = FastAPI()
    app.include_router(workflow_routes.router)
    app.dependency_overrides[get_current_user] = lambda: USER
    return TestClient(app, raise_server_exceptions=False)


def approve_http(client, doc_id: str, git_action: str = "merge"):
    response = client.post(
        "/api/v1/documents/review_transitions/approve",
        json={"doc_id": doc_id, "git_action": git_action},
    )
    return response.status_code, response.json()


@needs_git
class TestApprovalFinalizeRealHttp0671:
    def test_http_approve_real_merge_absorbs_uncommitted_edit_and_pushes(self, origin_repo):
        from modules.flow_gate.db import git_concurrency as gc
        from modules.flow_gate.services import git_service as svc

        group = f"{PROJECT}.default.0801"
        _root_id, ac_id = _seed_final_approval_group(group)
        assert svc.ensure_worktree(PROJECT, "default", group) == "ok"
        wt = _worktree(group)
        # Left UNCOMMITTED on purpose: this is exactly what F2 (run_freeze's real absorb
        # commit, under G, tagged "FlowGate-Freeze: {job_id}") must fold in before the real
        # merge body runs -- not a pre-committed change like 0607's _group_with_work uses.
        (wt / "uncommitted.py").write_text("worker_edit = 1\n", encoding="utf-8")

        base = _base()
        origin_before = _origin_head(origin_repo)

        client = api()
        status, payload = approve_http(client, ac_id)

        assert status == 200, payload
        result = payload["git"]["result"]
        assert result["status"] == "merged" and result["pushed"] is True
        assert payload["approval"]["approved"] is True
        assert _review_status(ac_id) == "approved"
        head = _rev(base, "HEAD")
        assert _origin_head(origin_repo) == head != origin_before
        assert head.startswith(result["merge_commit"])

        # the real F2 absorb commit really happened: it carries the freeze trailer and the
        # file the worker left uncommitted is really part of history now.
        log = _git(["log", "--all", "--grep=FlowGate-Freeze:", "--format=%H%n%b%n--"], cwd=base)
        assert "FlowGate-Freeze:" in log, log
        assert _git(["show", f"{head}:uncommitted.py"], cwd=base).strip() == "worker_edit = 1"

        # every lock the request took (G for the absorb, B+R for the merge/push) is gone.
        assert gc.list_locks_in_scope(PROJECT) == []

        _drive_jobs(group)
        assert not _worktree(group).exists()

    def test_http_approve_queues_behind_push_rejection_then_real_runner_reclaims_it(
        self, origin_repo, monkeypatch,
    ):
        from modules.flow_gate.services import git_service as svc

        group = f"{PROJECT}.default.0802"
        _root_id, ac_id = _seed_final_approval_group(group)
        assert svc.ensure_worktree(PROJECT, "default", group) == "ok"
        wt = _worktree(group)
        (wt / "e.py").write_text("e = 1\n", encoding="utf-8")
        _git(["add", "-A"], cwd=wt)
        _git(["commit", "-m", f"work {group}"], cwd=wt)

        base = _base()
        pre = _rev(base, "HEAD")
        origin_before = _origin_head(origin_repo)

        monkeypatch.setattr(svc, "_run_git", _reject_push(_is_push_base))
        client = api()
        status, payload = approve_http(client, ac_id)
        monkeypatch.undo()

        # a rejected push is a 5xx from origin, not a client error: the real route leaves the
        # approval queued (retry_wait), it does not fail the HTTP request or lose the intent.
        assert status == 200, payload
        assert payload["approval"]["approved"] is False
        assert payload["approval"]["stage"] == "queued" and payload["approval"]["deferred"] is True
        assert payload["block_reason"]["code"] == "push_rejected"
        assert _review_status(ac_id) == "pending_review"
        assert _origin_head(origin_repo) == origin_before
        assert _rev(base, "HEAD") == pre
        assert _job(group)["status"] == "retry_wait"

        # the real Job Runner's next claim (push works again) finishes the SAME approval --
        # the wait/blocked/reclaim contract, observed through real job state after the HTTP
        # request already returned.
        assert _drive_jobs(group) >= 1
        assert _job(group)["status"] == "succeeded"
        assert _review_status(ac_id) == "approved"
        assert _origin_head(origin_repo) == _rev(base, "HEAD") != origin_before
        assert not _worktree(group).exists()
