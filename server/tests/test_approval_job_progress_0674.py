"""Approval job progress, conflict hand-off meaning and provider failure reasons
(flowgate.default.0674 T0004 §2-2, §2-4, §2-5, §2-6 and §4 "서버").

0674 NR0003 measured what the screen could not see: a conflict AI run that ended
``finished / exited / outcome=none`` with ``stop_code``/``stop_reason`` NULL while its own
output said ``Selected model is at capacity`` (D1's server half), a final approval job whose
waiting states (freeze_wait / blocked / retry_wait / recovery_required) reached no screen
(C1/C2), and a conflict hand-off job that reads ``succeeded`` and nothing else (C3).

* Unit level — ``job_outcome`` / ``job_view`` (C3), ``notify_progress`` and the Runner's
  observer hook (C2), and the canonical ``provider_failed`` stop (§2-2), with the
  precedence of every older stop code kept.
* Real Git + the production SQLite store (the 0671 harness) —
  ``TestQueuedApprovalIsVisible``: G held -> the request answers queued/freeze_wait, the
  finalize state carries ``approval_job`` with its blocker and the request path emits
  ``git_approval_job_changed``; the Runner's re-freeze then blocks on R and emits the same
  event from the Runner path; the job finishes and ``approval_job`` is gone.
  ``TestConflictHandOffWithFailedResolveRun``: a real conflict hand-off (``outcome =
  handed_off`` beside ``status = succeeded``), then a resolve_conflict run that ended on
  a provider failure is judged and recorded with its reason while the merge session
  stays in conflict and the AC stays pending.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from test_git_integration_0115 import _git, needs_git  # noqa: F401 -- sets FLOWGATE_STORAGE_DIR etc.

USER = {"user_id": "reviewer", "is_admin": True}
PROJECT = "apjobprj"
PROJECT_NAME = "ApJobPrj"

CAPACITY = "Selected model is at capacity. Please try a different model."
CODEX_TAIL = (
    '{"type":"item.completed","item":{"type":"command_execution","command":"resolve_190.ps1",'
    '"exit_code":1}}\n'
    '{"type":"turn.failed","error":{"message":"' + CAPACITY + '"}}\n'
)


# ── unit: job outcome / view (C3) ────────────────────────────────────────────

class TestJobOutcome:
    def _ap(self):
        from modules.flow_gate.services.git import approval_publish as ap
        return ap

    def test_handed_off_job_is_succeeded_with_a_handed_off_outcome(self):
        ap = self._ap()
        job = {"job_id": "opj_1", "status": "succeeded", "result": ap._HANDOFF_RESULT,
               "payload": json.dumps({"doc_id": "p.default.0001.0002-AC"})}
        view = ap.job_view(job)
        assert view["status"] == "succeeded"          # the Runner's terminal contract stays
        assert view["outcome"] == "handed_off"
        assert view["stage"] == "terminal"
        assert view["doc_id"] == "p.default.0001.0002-AC"

    def test_executed_and_approved_elsewhere_are_told_apart(self):
        ap = self._ap()
        executed = {"status": "succeeded",
                    "result": json.dumps({"git": {"status": "merged"}, "approved": True})}
        elsewhere = {"status": "succeeded",
                     "result": json.dumps({"approved_elsewhere": True, "git": {}})}
        assert ap.job_outcome(executed) == "executed"
        assert ap.job_outcome(elsewhere) == "approved_elsewhere"
        assert ap.job_outcome({"status": "failed"}) == "failed"
        assert ap.job_outcome({"status": "cancelled"}) == "cancelled"

    @pytest.mark.parametrize("status", ["freezing", "freeze_wait", "pending", "blocked",
                                        "running", "retry_wait", "recovery_required"])
    def test_a_live_job_has_no_outcome_and_its_status_is_its_stage(self, status):
        ap = self._ap()
        view = ap.job_view({"job_id": "opj_2", "status": status})
        assert view["outcome"] is None
        assert view["stage"] == status

    def test_describe_still_reads_the_hand_off(self):
        ap = self._ap()
        out = ap.describe({"status": "succeeded", "result": ap._HANDOFF_RESULT})
        assert out.state == ap.HANDED_OFF


# ── unit: progress events (C2) ───────────────────────────────────────────────

class TestNotifyProgress:
    @pytest.fixture
    def emitted(self, monkeypatch):
        from modules.flow_gate.services import git_service as svc
        events = []
        monkeypatch.setattr(svc, "_emit", lambda kind, project, group, payload:
                            events.append((kind, project, group, payload)))
        return events

    @pytest.mark.parametrize("status", ["freeze_wait", "blocked", "retry_wait", "recovery_required"])
    def test_every_waiting_transition_is_announced(self, emitted, status):
        from modules.flow_gate.services.git import approval_publish as ap
        job = {"job_id": "opj_3", "kind": ap.KIND, "status": status, "project_id": "p",
               "group_id": "p.default.0001", "blocked_domain": "R", "blocked_lock_key": "R:p",
               "blocked_operation": "publish",
               "payload": json.dumps({"doc_id": "p.default.0001.0002-AC"})}
        ap.notify_progress(job)
        assert len(emitted) == 1
        kind, project, group, payload = emitted[0]
        assert (kind, project, group) == ("git_approval_job_changed", "p", "p.default.0001")
        assert payload["status"] == status and payload["stage"] == status
        assert payload["doc_id"] == "p.default.0001.0002-AC"
        assert payload["job"]["job_id"] == "opj_3"
        assert payload["job"]["blocker"]["domain"] == "R"

    @pytest.mark.parametrize("status", ["freezing", "pending", "running", "succeeded", "failed"])
    def test_other_statuses_say_nothing_here(self, emitted, status):
        from modules.flow_gate.services.git import approval_publish as ap
        ap.notify_progress({"job_id": "opj_4", "status": status, "project_id": "p",
                            "group_id": "p.default.0001"})
        assert emitted == []

    def test_the_runner_observer_is_installed_and_only_speaks_on_a_change(self, monkeypatch):
        from modules.flow_gate.services.git import approval_publish as ap
        from modules.flow_gate.services.git import job_runner as runner

        ap.install()
        with runner._reg_guard:
            assert runner._observers.get(ap.KIND) is ap.notify_progress
        seen = []
        monkeypatch.setitem(runner._observers, ap.KIND, seen.append)
        job = {"job_id": "opj_5", "kind": ap.KIND, "status": "blocked",
               "blocked_lock_key": "R:p", "last_error_code": "busy"}
        runner._observe(job, runner._progress_key(job, "blocked"))       # unchanged
        assert seen == []
        runner._observe(job, runner._progress_key({}, "pending"))        # changed
        assert seen == [job]

    def test_an_observer_failure_never_escapes(self, monkeypatch):
        from modules.flow_gate.services.git import job_runner as runner

        def boom(_job):
            raise RuntimeError("observer down")

        monkeypatch.setitem(runner._observers, "final_approval_publish", boom)
        runner._observe({"job_id": "x", "kind": "final_approval_publish", "status": "blocked"},
                        ("pending", None, None))


# ── unit: provider failure stop (§2-2) ───────────────────────────────────────

def _run(**overrides) -> dict:
    run = {"run_id": "aiv_0674", "mode": "single", "action_scope": "resolve_conflict",
           "outcome": "none", "end_reason": "exited", "exit_code": 1,
           "docs_reached": 0, "docs_target": 0,
           "stdout_tail": CODEX_TAIL, "stderr_tail": "",
           "provider": {"name": "Codex2 GPT 6-Sol"}, "provider_id": "aip_c2",
           "attempt_no": 1}
    run.update(overrides)
    return run


class TestProviderFailedStop:
    def _fin(self):
        from modules.flow_gate.services.ai_invoke import finalize as fin
        return fin

    def test_the_0668_run_gets_a_canonical_code_and_the_providers_own_reason(self):
        fin = self._fin()
        run = _run()
        assert fin._resolve_stop_code(run, False) == "provider_failed"
        fin._finalize_stop(run, False)
        assert run["stop_code"] == "provider_failed"
        assert run["resumable"] is False
        assert CAPACITY in run["stop_reason"]
        assert "Codex2 GPT 6-Sol" in run["stop_reason"]
        assert "exit code 1" in run["stop_reason"]

    def test_an_error_event_without_a_failed_exit_is_enough(self):
        fin = self._fin()
        run = _run(exit_code=0, stdout_tail='{"type":"error","message":"stream disconnected"}\n')
        assert fin.provider_failure_of(run) == {"exit_code": 0, "message": "stream disconnected"}
        assert "stream disconnected" in fin._stop_reason_text("provider_failed", run)

    def test_a_failed_exit_without_any_event_still_names_the_exit(self):
        fin = self._fin()
        run = _run(stdout_tail="plain text\n", exit_code=2)
        assert fin._resolve_stop_code(run, False) == "provider_failed"
        text = fin._stop_reason_text("provider_failed", run)
        assert "exit code 2" in text and "exited" in text

    def test_stderr_carries_the_event_too(self):
        fin = self._fin()
        run = _run(stdout_tail="", stderr_tail=CODEX_TAIL, exit_code=None)
        assert fin.provider_failure_of(run)["message"] == CAPACITY

    def test_a_long_message_is_clipped(self):
        fin = self._fin()
        long = "x" * 900
        run = _run(stdout_tail='{"type":"turn.failed","error":{"message":"' + long + '"}}\n')
        assert len(fin.provider_failure_of(run)["message"]) <= 300

    @pytest.mark.parametrize("overrides", [
        {"outcome": "complete"},                               # it did its work
        {"exit_code": 0, "stdout_tail": "done\n"},             # clean exit, nothing reported
        {"exit_code": None, "stdout_tail": ""},                # API provider: no exit code
        {"exit_code": True, "stdout_tail": ""},                # a bool is not an exit code
    ])
    def test_no_failure_means_no_code(self, overrides):
        fin = self._fin()
        assert fin._resolve_stop_code(_run(**overrides), False) is None

    @pytest.mark.parametrize("end_reason, code", [
        ("cancelled", "cancelled"), ("timeout", "timeout"),
        ("user_paused", "user_paused"), ("all_providers_failed", "providers_exhausted"),
    ])
    def test_older_codes_keep_their_precedence(self, end_reason, code):
        fin = self._fin()
        assert fin._resolve_stop_code(_run(end_reason=end_reason), False) == code

    def test_a_continuous_no_output_hop_is_still_no_output_exhausted(self):
        fin = self._fin()
        run = _run(mode="continuous", docs_target=1, action_scope="work")
        assert fin._resolve_stop_code(run, False) == "no_output_exhausted"

    def test_a_handoff_and_an_inbox_stop_are_untouched(self):
        fin = self._fin()
        assert fin._resolve_stop_code(_run(), True) == "hop_handoff"
        assert fin._resolve_stop_code(_run(inbox_stop_code="head_slot_mismatch"), False) == \
            "head_slot_mismatch"


# ── real Git + production SQLite store (the 0671 harness) ────────────────────

@pytest.fixture(scope="module")
def db_path(migrated_sqlite_db):
    return migrated_sqlite_db("test_approval_job_progress_0674.db")


@pytest.fixture(scope="module")
def patch_store(db_path):
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

    tmp = Path(tempfile.mkdtemp(prefix="fg-apjob-origin-"))
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
        "repo_url": bare.as_uri(), "provider": "generic", "base_branch": "main",
        "default_finalize_action": "merge", "enabled": True,
    })
    yield {"bare": bare, "seedwt": seedwt, "tmp": tmp}
    svc.delete_config(PROJECT)
    shutil.rmtree(tmp, ignore_errors=True)


@pytest.fixture
def lock_env(monkeypatch):
    """Host identity probe and poll interval only (the 0671 harness's own overrides);
    the short interactive budgets keep the queued paths fast."""
    from modules.flow_gate.services.git import instance_registry as reg
    from modules.flow_gate.services.git import lock_manager as lm

    monkeypatch.setattr(reg, "_start_identity", lambda pid: "self-marker")
    monkeypatch.setattr(reg, "node_key", lambda: "node-apjob")
    monkeypatch.setattr(lm, "poll_interval_sec", lambda: 0.02)
    monkeypatch.setenv("FLOWGATE_FREEZE_INTERACTIVE_WAIT_SEC", "0.2")
    monkeypatch.setenv("FLOWGATE_INTERACTIVE_WAIT_SEC", "0.2")
    monkeypatch.setenv("FLOWGATE_R_INTERACTIVE_WAIT_SEC", "0.2")
    reg.register()
    yield


@pytest.fixture
def emitted(monkeypatch):
    from modules.flow_gate.services import git_service as svc
    events = []
    original = svc._emit

    def record(kind, project_id, group_id, payload):
        events.append((kind, group_id, payload))
        return original(kind, project_id, group_id, payload)

    monkeypatch.setattr(svc, "_emit", record)
    return events


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
        db_groups.create({"group_id": group_id, "project_id": PROJECT,
                          "module": "default", "title": "approval job progress"})
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


def _worktree(group_id: str) -> Path:
    from modules.flow_gate.storage.paths import src_root
    return src_root(PROJECT_NAME, group_id.replace(".", "_"))


def _commit_work(group_id: str, name: str) -> None:
    from modules.flow_gate.services import git_service as svc
    assert svc.ensure_worktree(PROJECT, "default", group_id) == "ok"
    wt = _worktree(group_id)
    (wt / name).write_text(f"{name} = 1\n", encoding="utf-8")
    _git(["add", "-A"], cwd=wt)
    _git(["commit", "-m", f"work {name}"], cwd=wt)


def _claim_all(group_id: str) -> int:
    """One Runner claim + execute for every claimable job of the Group (0671 _drive_jobs)."""
    from modules.flow_gate.db import operation_job as db_jobs
    from modules.flow_gate.db.connection import get_store
    from modules.flow_gate.services.git import job_runner

    ran = 0
    for kind in ("final_approval_publish", "worktree_cleanup"):
        for job in db_jobs.jobs_of_group(group_id, kind, ("pending", "blocked", "retry_wait", "freeze_wait")):
            with get_store().transaction():
                get_store()._execute("UPDATE operation_job SET available_at = ? WHERE job_id = ?",
                                     ["2000-01-01T00:00:00+00:00", job["job_id"]])
            claim = job_runner.try_claim(job["job_id"])
            if claim is not None:
                job_runner.execute(claim)
                ran += 1
    return ran


def _drive_jobs(group_id: str, limit: int = 6) -> None:
    from modules.flow_gate.services.git import worktree_cleanup
    worktree_cleanup.install()
    for _ in range(limit):
        if not _claim_all(group_id):
            break


def _approval_job(group_id: str) -> dict:
    from approval_job_fakes import job_of
    return job_of(group_id)


def _review_status(doc_id: str) -> str:
    from modules.flow_gate.db import documents as db_docs
    return (db_docs.get_by_id(doc_id) or {}).get("doc_review_status")


def api():
    from fastapi import FastAPI
    from starlette.testclient import TestClient
    from modules.flow_gate.auth.middleware import get_current_user
    from modules.flow_gate.workflow.routers import workflow as workflow_routes

    app = FastAPI()
    app.include_router(workflow_routes.router)
    app.dependency_overrides[get_current_user] = lambda: USER
    return TestClient(app, raise_server_exceptions=False)


def approve_http(doc_id: str, git_action: str = "merge"):
    response = api().post("/api/v1/documents/review_transitions/approve",
                          json={"doc_id": doc_id, "git_action": git_action})
    return response.status_code, response.json()


def _job_events(events, group_id: str) -> list:
    return [p for kind, g, p in events if kind == "git_approval_job_changed" and g == group_id]


@needs_git
class TestQueuedApprovalIsVisible:
    def test_queued_then_blocked_then_done_is_followed_by_state_and_events(
        self, origin_repo, lock_env, emitted,
    ):
        from modules.flow_gate.services import git_service as svc
        from modules.flow_gate.services.git import approval_publish as ap
        from modules.flow_gate.services.git import job_store as store
        from modules.flow_gate.services.git import lock_manager as locks

        group = f"{PROJECT}.default.0911"
        _root_id, ac_id = _seed_final_approval_group(group)
        _commit_work(group, "g911.py")

        # ── request path: G is held elsewhere -> freeze_wait, answered as queued ──
        g_out, g_ctx = locks.acquire_group(PROJECT, group, holder_kind="selfcheck")
        assert g_out.ok
        try:
            status, payload = approve_http(ac_id)
        finally:
            locks.release(g_ctx, g_out.lock_key)
        assert status == 200, payload
        assert payload["approval"]["stage"] == "queued" and payload["approval"]["approved"] is False
        assert payload["approval"]["blocker"]["domain"] == "G"
        job = payload["job"]
        assert job["status"] == "freeze_wait" and job["stage"] == "freeze_wait"
        assert job["outcome"] is None and job["doc_id"] == ac_id
        assert _review_status(ac_id) == "pending_review"

        state = svc.get_finalize_state(group)["state"]
        assert state["approval_in_flight"] is True
        assert state["approval_job"]["job_id"] == job["job_id"]
        assert state["approval_job"]["stage"] == "freeze_wait"
        assert state["approval_job"]["blocker"]["domain"] == "G"

        events = _job_events(emitted, group)
        assert [e["status"] for e in events] == ["freeze_wait"]       # the request path
        assert events[0]["job"]["job_id"] == job["job_id"]
        assert events[0]["doc_id"] == ac_id

        # ── Runner path: the re-freeze lands, then publish blocks on R ──────────
        r_ctx = locks.new_context("req")
        r_out = locks.acquire("R", PROJECT, holder_kind="publish", mode="interactive", ctx=r_ctx)
        assert r_out.ok
        try:
            assert _claim_all(group) == 1                   # freeze_wait -> pending (frozen)
            assert _approval_job(group)["status"] == "pending"
            assert [e["status"] for e in _job_events(emitted, group)] == ["freeze_wait"]
            assert _claim_all(group) == 1                   # pending -> blocked on R
            blocked = _approval_job(group)
            assert blocked["status"] == "blocked" and blocked["blocked_domain"] == "R"
            statuses = [e["status"] for e in _job_events(emitted, group)]
            assert statuses == ["freeze_wait", "blocked"]   # the Runner path speaks too
            assert _job_events(emitted, group)[-1]["job"]["blocker"]["domain"] == "R"
            state = svc.get_finalize_state(group)["state"]
            assert state["approval_job"]["stage"] == "blocked"
            assert state["approval_job"]["blocker"]["domain"] == "R"
            # a re-claim that changes nothing says nothing again
            _claim_all(group)
            assert [e["status"] for e in _job_events(emitted, group)] == ["freeze_wait", "blocked"]
        finally:
            locks.release(r_ctx, r_out.lock_key)
            locks.unbind(r_ctx)

        # ── terminal: the Runner finishes the same approval ─────────────────────
        _drive_jobs(group)
        done = _approval_job(group)
        assert done["status"] == "succeeded"
        assert ap.job_view(store.get_job(done["job_id"]))["outcome"] == "executed"
        assert _review_status(ac_id) == "approved"
        assert svc.get_finalize_state(group)["state"]["approval_job"] is None
        assert any(kind == "git_finalize_done" and g == group for kind, g, _p in emitted)


@needs_git
class TestConflictHandOffWithFailedResolveRun:
    PATH = "shared.txt"

    def test_failed_resolve_run_is_recorded_and_the_conflict_and_approval_stay_open(
        self, origin_repo, lock_env, monkeypatch,
    ):
        from modules.flow_gate.db import ai_invoke_runs as db_runs
        from modules.flow_gate.db import git_integration as db_git
        from modules.flow_gate.services import git_service as svc
        from modules.flow_gate.services.ai_invoke import finalize as fin
        from modules.flow_gate.services.ai_invoke.runtime import _svc
        from modules.flow_gate.services.git import approval_publish as ap
        from modules.flow_gate.services.git import job_store as store

        group = f"{PROJECT}.default.0912"
        _root_id, ac_id = _seed_final_approval_group(group)
        assert svc.ensure_worktree(PROJECT, "default", group) == "ok"
        (_worktree(group) / self.PATH).write_text("ours\n", encoding="utf-8")
        seedwt = origin_repo["seedwt"]
        _git(["pull", "origin", "main"], cwd=seedwt)
        (seedwt / self.PATH).write_text("theirs\n", encoding="utf-8")
        _git(["add", "-A"], cwd=seedwt)
        _git(["commit", "-m", "mainline shared"], cwd=seedwt)
        _git(["push", "origin", "main"], cwd=seedwt)

        status, payload = approve_http(ac_id)
        assert status == 200, payload
        assert payload["git"]["result"]["status"] == "conflict"
        assert payload["approval"]["approved"] is False
        assert payload["approval"]["stage"] == "git_finalize"
        # C3: terminal for the Runner, but not "done" for the approval
        assert payload["job"]["status"] == "succeeded"
        assert payload["job"]["outcome"] == "handed_off"
        job = store.get_job(payload["job"]["job_id"])
        assert ap.job_view(job)["outcome"] == "handed_off"
        merge_id = int(payload["git"]["result"]["merge_id"])

        # The 0668 run: resolve_conflict, provider at capacity, exit 1, nothing resolved.
        monkeypatch.setattr(_svc(), "ORACLE_SETTLE_SEC", 0)
        run = _run(run_id="aiv_20261005_0674a", group_id=group, project_id=PROJECT,
                   doc_ref=ac_id, merge_id=merge_id, outcome=None,
                   started_at="2026-10-05T17:05:07+09:00",
                   finished_at="2026-10-05T17:07:04+09:00")
        fin._judge_hop(run)
        assert run["outcome"] == "none"                  # the conflict really is still there
        fin._finalize_stop(run, False)
        assert fin._persist_run_record(run) is True

        row = db_runs.get(run["run_id"])
        assert row["outcome"] == "none" and row["end_reason"] == "exited"
        assert row["exit_code"] == 1
        assert row["stop_code"] == "provider_failed"
        assert CAPACITY in row["stop_reason"]

        # nothing else moved: the session is still the conflict, the AC still pending
        assert db_git.get_state(group)["status"] == "conflict"
        state = svc.get_finalize_state(group)["state"]
        assert state["status"] == "conflict" and state["merge_id"] == merge_id
        assert state["approval_job"] is None            # the hand-off job is terminal
        assert _review_status(ac_id) == "pending_review"
        conflicts = svc.list_conflicts(group, merge_id)
        assert sum(int(f.get("conflict_count") or 0) for f in conflicts["files"]) >= 1

        svc.abort_merge(group, merge_id)
