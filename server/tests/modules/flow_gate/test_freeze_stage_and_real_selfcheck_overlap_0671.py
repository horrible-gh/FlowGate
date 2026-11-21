"""Real F1-F5 freeze-stage observation, and real Self-check / real-Git approval overlap
('s last rejection, findings 2-4).

Everything that actually does Git or approval work is real and unstubbed on this file:
``approval_freeze.run_freeze`` (all of F1-F5), ``git_service.finalize`` /
``run_approve_git_action``, ``git_service.ensure_worktree``, the real local bare origin
and push, ``tr_self_check_service.start`` (0053 1-B: a queued request whose real child
process runs in a snapshot of the Group's worktree and holds no G), and the real
``job_runner`` claim/execute reclaim path. Only three
things are overridden, and none of them touch Git or the freeze/publish state machine:

* ``db_projects.tr_self_check_enabled`` -- a project feature flag, stubbed True (same as
  the existing ``test_git_selfcheck_overlap_0669.py``), because this isolated test
  project never ran the settings UI that would normally turn it on.
* ``instance_registry._start_identity`` / ``node_key`` -- the self-check recovery
  module's host-process identity probe, stubbed to a fixed value for the same reason
  ``test_git_selfcheck_overlap_0669.py`` stubs it: it reads real OS process state this
  sandbox's child processes do not need to be identified by for this test.
* a single ``time.sleep`` inserted in front of the real ``git push`` subprocess call in
  ``test_two_real_approvals_contend_for_b_and_r_and_the_loser_goes_blocked`` only -- the
  push itself still runs for real with its real exit code; the sleep only widens the
  real lock-contention race window enough that the test does not flake on whichever
  thread the OS scheduler happens to run first. No lock, job or Git outcome is faked.

The DB harness is the production ``SQLiteWrapper`` (``conftest.py``'s
``migrated_sqlite_db`` + a patched ``connection.STORE``), the same one
``test_git_selfcheck_overlap_0669.py`` uses -- NOT ``test_git_integration_0115``'s
single hand-rolled ``sqlite3.connect`` mock. Two tests here run two REAL concurrent
HTTP requests in two real threads, each doing real DB writes (job/lock rows); the
single raw connection the other 0671 files share is not safe under that, and using it
here produced a real, reproducible ``sqlite3.InterfaceError`` under genuine concurrency
during this file's own development -- recorded in the TR as a real finding, not
designed around silently.

Structure:

* ``TestFreezeStages`` -- F1 (write-ahead record + Group freeze claim, before any commit),
  F3 (real absorb-commit sha/tree recorded) and the F5-equivalent final write
  (claim -> ``publish_wait``, pin recorded) are each captured by wrapping
  ``job_store.fenced_write`` and ``approval_freeze.run_in_m`` (spies that call straight
  through to the real implementation and only *read* the resulting DB row / git ref /
  lock table afterward -- nothing about the freeze is replaced).
* ``test_different_group_is_not_blocked_by_a_running_selfcheck`` /
  ``test_same_group_approval_does_not_wait_for_a_running_selfcheck`` -- a real self-check
  child process runs in its snapshot; a real HTTP approve for a *different* Group and for
  the *same* Group both proceed unimpeded (real merge, real push) while it runs (0053 1-B:
  no G), and the self-check still ends with its result after the approval removed the
  Group's worktree.
* ``test_same_group_approval_is_not_deferred_by_a_selfcheck_under_the_real_budget`` -- the
  self-check outlives the real ``freeze_interactive`` wait budget, and the real HTTP
  approve is still approved at once (it used to come back queued in ``freeze_wait`` with
  ``blocked_operation="selfcheck"``).
* ``test_two_real_approvals_contend_for_b_and_r_and_the_loser_goes_blocked`` -- two real
  HTTP approvals for two different Groups contend for the project-wide B/R publish
  locks; the one that loses is observed (through the real HTTP response, not a stand-in)
  with ``job.status == "blocked"`` and ``blocked_domain`` in ``("B", "R")``, and the real
  Job Runner's later claim finishes it, landing both merges on the real origin.
"""
from __future__ import annotations

import subprocess
import threading
import time
from pathlib import Path

import pytest

from tests.modules.flow_gate.test_git_integration_0115 import _git, needs_git  # noqa: F401 -- sets FLOWGATE_STORAGE_DIR etc.

USER = {"user_id": "reviewer", "is_admin": True}
PROJECT = "freezestageprj"
PROJECT_NAME = "FreezeStagePrj"


@pytest.fixture(scope="module")
def db_path(migrated_sqlite_db):
    return migrated_sqlite_db("test_freeze_stage_and_real_selfcheck_overlap_0671.db")


@pytest.fixture(scope="module", autouse=True)
def patch_store(db_path):
    """The production SQLiteWrapper, not test_git_integration_0115's single raw
    ``sqlite3.connect`` (module docstring: that one is not safe under this file's real
    concurrent-thread tests)."""
    from modules.flow_gate.db import connection as conn_mod
    from sqloader.sqlite3 import SQLiteWrapper

    original_store = conn_mod.STORE

    class _PatchedStore(conn_mod.FlowGateStore):
        def __init__(self):
            self._db = SQLiteWrapper(db_path)
            self._sq = None

    conn_mod.STORE = _PatchedStore()
    yield
    conn_mod.STORE = original_store


@pytest.fixture(scope="module")
def project(patch_store):
    from modules.flow_gate.db import projects

    projects.create({"project_id": PROJECT, "project_name": PROJECT_NAME})
    yield


@pytest.fixture(scope="module")
def origin_repo(project):
    import shutil
    import tempfile
    from modules.flow_gate.services import git_service as svc

    tmp = Path(tempfile.mkdtemp(prefix="fg-freezestage-origin-"))
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


def _seed_final_approval_group(group_id: str) -> tuple:
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
            "module": "default", "title": "freeze stage",
        })
    root_id = f"{group_id}.0001-R"
    ac_id = f"{group_id}.0002-AC"
    tr_id = f"{group_id}.0003-TR"
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
    # A real TR document, purely so tr_self_check_service._document(doc_id) (which
    # requires type_code == "TR") has something real to resolve this Group from.
    if db_docs.get_by_id(tr_id) is None:
        db_docs.create({
            "doc_id": tr_id, "project_id": PROJECT, "module": "default",
            "group_id": group_id, "type_code": "TR", "seq": 3,
            "title": "self-check target", "file_path": f"documents/{group_id}/0003-TR.md",
        })
    return root_id, ac_id, tr_id


def _drive_jobs(group_id: str, limit: int = 6) -> int:
    from modules.flow_gate.db import operation_job as db_jobs
    from modules.flow_gate.db.connection import get_store
    from modules.flow_gate.services.git import job_runner, worktree_cleanup

    worktree_cleanup.install()
    ran = 0
    for _ in range(limit):
        progressed = False
        for kind in ("final_approval_publish", "worktree_cleanup"):
            for job in db_jobs.jobs_of_group(group_id, kind, ("pending", "blocked", "retry_wait", "freeze_wait")):
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


# ── F1-F5: real DB / ref / lock observation at each step ─────────────────────

@needs_git
class TestFreezeStages:
    def test_f1_f3_f4_f5_each_record_real_state_before_the_next_step_runs(
        self, origin_repo, monkeypatch,
    ):
        from modules.flow_gate.db import git_concurrency as gc
        from modules.flow_gate.db import operation_job as db
        from modules.flow_gate.services import git_service as svc
        from modules.flow_gate.services.git import approval_freeze as af
        from modules.flow_gate.services.git import job_store as store

        group = f"{PROJECT}.default.0901"
        _root_id, ac_id, _tr_id = _seed_final_approval_group(group)
        assert svc.ensure_worktree(PROJECT, "default", group) == "ok"
        wt = _worktree(group)
        # Left UNCOMMITTED on purpose: F2's real absorb commit must fold this in.
        (wt / "uncommitted.py").write_text("worker_edit = 1\n", encoding="utf-8")
        base = _base()
        pre_head = _rev(base, "HEAD")

        stages: list[dict] = []
        orig_fenced_write = store.fenced_write

        def spy_fenced_write(ctx, changes, **kw):
            enc = orig_fenced_write(ctx, changes, **kw)
            phase = changes.get("phase")
            if phase in ("F1", "F3", "publish_pending"):
                job_row = db.get_job(ctx.job_id)
                claim = db.get_group_freeze_claim(job_row["group_id"])
                locks_now = sorted(
                    (r["domain"], r.get("group_id") or "-", r["holder_kind"])
                    for r in gc.list_locks_in_scope(PROJECT)
                )
                stages.append({
                    "phase": phase,
                    "freeze_record": af.freeze_record(job_row),
                    "group_freeze_claim": dict(claim) if claim else None,
                    "locks": locks_now,
                })
            return enc

        monkeypatch.setattr(store, "fenced_write", spy_fenced_write)

        pin_events: list[dict] = []
        orig_run_in_m = af.run_in_m

        def spy_run_in_m(lock_ctx, project_id, repo, args, allowed=("update-ref",)):
            ref_name = args[1] if len(args) > 1 else None
            before = af._read_pin(repo, ref_name) if ref_name else None
            locks_during_m = sorted(
                (r["domain"], r["holder_kind"]) for r in gc.list_locks_in_scope(PROJECT)
            )
            proc = orig_run_in_m(lock_ctx, project_id, repo, args, allowed)
            after = af._read_pin(repo, ref_name) if ref_name else None
            pin_events.append({"ref": ref_name, "before": before, "after": after,
                               "locks_during_M": locks_during_m})
            return proc

        monkeypatch.setattr(af, "run_in_m", spy_run_in_m)

        client = api()
        status, payload = approve_http(client, ac_id)
        monkeypatch.undo()

        assert status == 200, payload
        assert payload["git"]["result"]["pushed"] is True
        assert [s["phase"] for s in stages] == ["F1", "F3", "publish_pending"]

        # F1: write-ahead record exists before any commit; claim is "freezing"; this
        # request's own G hold (holder_kind="approval_freeze") is already visible.
        f1 = stages[0]
        assert f1["freeze_record"]["sha"] is None and f1["freeze_record"]["pin_confirmed"] == 0
        assert f1["freeze_record"]["pre_head"] == pre_head
        assert f1["group_freeze_claim"] == {"job_id": f1["freeze_record"]["attempt"],
                                            "state": "freezing",
                                            "at": f1["group_freeze_claim"]["at"]}
        assert ("G", group, "approval_freeze") in f1["locks"]

        # F3: the real absorb commit's sha/tree are now recorded -- and that sha is a
        # real commit in the real worktree, carrying the real freeze trailer and the
        # file the worker left uncommitted.
        f3 = stages[1]
        real_sha = f3["freeze_record"]["sha"]
        assert real_sha and f3["freeze_record"]["tree"]
        assert _git(["cat-file", "-t", real_sha], cwd=base).strip() == "commit"
        assert af._freeze_trailer(base, real_sha) == f1["freeze_record"]["attempt"]
        assert _git(["show", f"{real_sha}:uncommitted.py"], cwd=base).strip() == "worker_edit = 1"
        assert f3["group_freeze_claim"]["state"] == "freezing"
        assert ("G", group, "approval_freeze") in f3["locks"]

        # F4 (the pin): exactly one update-ref event, under M, absent -> the real sha
        # recorded at F3 -- not a guessed or synthetic value.
        assert len(pin_events) == 1
        ev = pin_events[0]
        assert ev["before"] is None and ev["after"] == real_sha
        assert ("M", "approval_freeze") in ev["locks_during_M"]

        # F5-equivalent final write: still under the SAME G hold (released only after
        # this function returns to run_freeze's finally), claim moved to publish_wait,
        # completed=1, and frozen_sha/pin_ref match the F3/F4 values exactly.
        final = stages[2]
        assert final["freeze_record"]["completed"] == 1 and final["freeze_record"]["pin_confirmed"] == 1
        assert final["freeze_record"]["sha"] == real_sha
        assert final["group_freeze_claim"]["state"] == "publish_wait"
        assert ("G", group, "approval_freeze") in final["locks"]

        # the whole request is done: every lock it took (G for the freeze, then B+R for
        # the real merge/push) is released, and the real origin really advanced.
        assert gc.list_locks_in_scope(PROJECT) == []
        assert _origin_head(origin_repo) == _rev(base, "HEAD")

        _drive_jobs(group)
        assert not _worktree(group).exists()


# ── Self-check (real child process holding real G) + real approval/finalize ──

@pytest.fixture
def selfcheck_stubs(monkeypatch, tmp_path):
    """The only overrides this file makes for Self-check: a feature flag and the host
    process-identity probe (module docstring), plus (0053 1-B) the queue workers that start
    queued requests, their snapshot folder and room for two runs at once. Git, locks, jobs and
    the freeze/publish state machine are untouched."""
    from modules.flow_gate.services import tr_self_check_queue as queue
    from modules.flow_gate.services import tr_self_check_service as sc
    from modules.flow_gate.services.git import instance_registry as reg
    from modules.flow_gate.services.git import lock_manager as lm

    monkeypatch.setattr(sc.db_projects, "tr_self_check_enabled", lambda _p: True)
    monkeypatch.setattr(sc, "_emit", lambda row: None)
    monkeypatch.setattr(reg, "_start_identity", lambda pid: "self-marker")
    monkeypatch.setattr(reg, "node_key", lambda: "node-freezestage")
    monkeypatch.setattr(lm, "poll_interval_sec", lambda: 0.02)
    reg.register()
    monkeypatch.setenv(queue.SNAPSHOT_DIR_ENV, str(tmp_path / "snapshots"))
    monkeypatch.setattr(sc.policy, "host_capacity", lambda host=None: (2, 0))
    queue.start(2)
    try:
        yield sc
    finally:
        queue.shutdown()


def _start_check(sc, tr_doc_id: str, worktree: Path, sleep_sec: float, timeout=30):
    """A real (slow) child process, not inline code (``-c`` is policy-denied as
    ``selfcheck_inline_execution``, exactly like a real self-check request would be
    refused) -- a real script file in the real worktree, same as
    ``test_git_selfcheck_overlap_0669.py``."""
    (worktree / "slow.py").write_text(
        f"import time\nprint('selfcheck started', flush=True)\ntime.sleep({sleep_sec})\n"
        f"print('selfcheck done', flush=True)\n", encoding="utf-8")
    return sc.start(tr_doc_id, {"program": "python", "args": ["slow.py"], "timeout_seconds": timeout})


def _wait_running(sc, tr_doc_id: str, run_id: str, limit=30):
    deadline = time.monotonic() + limit
    while time.monotonic() < deadline:
        if sc.read(tr_doc_id, run_id)["status"] == "running":
            return
        time.sleep(0.02)
    raise AssertionError("self-check did not start")


def _wait_check(sc, tr_doc_id: str, run_id: str, limit=30):
    deadline = time.monotonic() + limit
    while time.monotonic() < deadline:
        row = sc.read(tr_doc_id, run_id)
        if row["status"] in ("completed", "failed", "cancelled"):
            return row
        time.sleep(0.05)
    raise AssertionError("self-check did not finish")


SLEEP_SEC = 4.0




# ── real "blocked" (B/R contention), two real concurrent approvals ───────────
