"""Real Git + SQLite jobs: a browser is not involved after submission."""
import json
import uuid
from types import SimpleNamespace

import pytest

from manual_merge_0066_support import work, rev, _git, held_locks, needs_git
from test_fg_gate_intake_0085 import product, env, db_path, FA
from test_fg_gate_publish_0085 import git_env, PROJECT
from modules.flow_gate.db import operation_job as jobs
from modules.flow_gate.db.connection import get_store
from modules.flow_gate.services.git import execution as ex, job_store, job_runner
from modules.flow_gate.services.git.credentials import GitServiceError


@pytest.fixture
def proj(git_env):
    r = git_env
    return SimpleNamespace(pid=PROJECT, base=r["base"], bare=r["bare"],
        wt=lambda gid: r["wt"],
        origin_files=lambda branch: _git(["ls-tree", "-r", "--name-only", branch], cwd=r["bare"]).splitlines())


def group(proj, seq):
    return FA


def start(gid, scope="push", **kw):
    return ex.start(gid, scope=scope, all=True, message="fix(test): durable sequence",
                    request_id=kw.pop("request_id", str(uuid.uuid4())), **kw)


def tick(job_id):
    with get_store().transaction():
        get_store()._execute("UPDATE operation_job SET available_at = ? WHERE job_id = ?",
                            ["2000-01-01T00:00:00+00:00", job_id])
    claim = job_runner.try_claim(job_id)
    assert claim is not None
    job_runner.execute(claim)
    return job_store.get_job(job_id)


def finish(gid, job_id):
    for _ in range(12):
        for kind in (ex.KIND, "base_publish"):
            for row in jobs.jobs_of_group(gid, kind, ("pending", "blocked", "retry_wait")):
                tick(row["job_id"])
        row = job_store.get_job(job_id)
        if row["status"] in jobs.TERMINAL_STATUSES:
            return ex.view(row)
    pytest.fail(str(row))


@needs_git
def test_commit_merge_push_completes_without_browser_and_reloads(proj):
    gid = group(proj, 1)
    work(proj, gid, "file.txt", "before\n")
    (proj.wt(gid) / "file.txt").write_text("after\n")
    run = start(gid)
    done = finish(gid, run["jobId"])
    assert done["status"] == "complete", done
    assert done["completed"] == ["commit", "update", "merge", "push"]
    assert "file.txt" in proj.origin_files("main")
    assert ex.latest(gid) == done
    assert held_locks(proj.pid) == []


@needs_git
def test_duplicate_click_and_same_request_after_completion(proj):
    gid = group(proj, 1)
    key = str(uuid.uuid4())
    first = start(gid, "commit", request_id=key)
    assert start(gid, "commit")["jobId"] == first["jobId"]
    assert start(gid, "commit", request_id=key)["jobId"] == first["jobId"]
    finish(gid, first["jobId"])
    assert start(gid, "commit", request_id=key)["jobId"] == first["jobId"]


@needs_git
def test_child_wait_releases_locks_and_reuses_child(proj):
    gid = group(proj, 1)
    work(proj, gid, "file.txt", "content\n")
    run = start(gid)
    row = tick(run["jobId"])
    assert row["status"] == "retry_wait", row
    first_child = ex.view(row)["childJobId"]
    assert first_child
    assert held_locks(proj.pid) == []
    row = tick(run["jobId"])
    assert ex.view(row)["childJobId"] == first_child
    assert finish(gid, run["jobId"])["status"] == "complete"


@needs_git
def test_commit_crash_evidence_does_not_commit_twice(proj, monkeypatch):
    gid = group(proj, 1)
    work(proj, gid, "file.txt", "before\n")
    (proj.wt(gid) / "file.txt").write_text("after\n")
    before = rev(proj.wt(gid), "HEAD")
    run = start(gid, "commit")
    advance = ex._advance
    monkeypatch.setattr(ex, "_advance", lambda *a: (_ for _ in ()).throw(RuntimeError("crash")))
    row = tick(run["jobId"])
    after = rev(proj.wt(gid), "HEAD")
    assert after != before and row["phase"] == "commit_apply"
    decision = ex.decide(row)
    assert decision.verdict == job_runner.APPLIED_NEXT
    assert decision.evidence["completed"] == ["commit"]
    # Reconciler's persisted transition, then a fresh worker claim.
    evidence = json.loads(row["evidence"])
    evidence.update(decision.evidence)
    with get_store().transaction():
        get_store()._execute("UPDATE operation_job SET status='pending', phase=?, evidence=?, "
                            "lease_owner=NULL, lease_token=NULL, lease_until=NULL WHERE job_id=?",
                            [decision.next_phase, json.dumps(evidence), run["jobId"]])
    monkeypatch.setattr(ex, "_advance", advance)
    assert finish(gid, run["jobId"])["status"] == "complete"
    assert rev(proj.wt(gid), "HEAD") == after


@needs_git
def test_changed_files_after_enqueue_are_not_silently_committed(proj):
    gid = group(proj, 1)
    work(proj, gid, "file.txt", "before\n")
    (proj.wt(gid) / "file.txt").write_text("selected\n")
    before = rev(proj.wt(gid), "HEAD")
    run = start(gid, "commit")
    (proj.wt(gid) / "file.txt").write_text("later edit\n")
    done = finish(gid, run["jobId"])
    assert done["status"] == "stopped" and done["error"] == "worktree_changed"
    assert rev(proj.wt(gid), "HEAD") == before


@needs_git
def test_selected_files_leave_other_work_remaining(proj):
    gid = group(proj, 1)
    work(proj, gid, "one.txt", "before\n")
    work(proj, gid, "two.txt", "before\n")
    for name in ("one.txt", "two.txt"):
        (proj.wt(gid) / name).write_text("after\n")
    _git(["add", "two.txt"], cwd=proj.wt(gid))
    run = ex.start(gid, scope="commit", paths=["one.txt"], message="fix(test): one file",
                   request_id=str(uuid.uuid4()))
    done = finish(gid, run["jobId"])
    assert done["status"] == "remaining", done
    assert _git(["show", "HEAD:two.txt"], cwd=proj.wt(gid)) == "before\n"


def test_conflict_wait_has_no_retry_exhaustion(monkeypatch):
    job = {"job_id": "parent", "group_id": "group", "phase": "conflict",
           "payload": json.dumps({"steps": ["merge"], "completed": []}),
           "evidence": json.dumps({"merge_id": 7, "completed": []})}
    ctx = SimpleNamespace(job=job)
    writes = []
    monkeypatch.setattr(ex.gs.db_git, "get_session", lambda _id: {"status": "open"})
    monkeypatch.setattr(ex.store, "job_write", lambda _ctx, **kw: writes.append(kw))
    assert ex.run(ctx, job_store.PUBLISH) is None
    assert writes[-1]["status"] == "retry_wait"
    assert "attempt_count" not in writes[-1]
    assert writes[-1]["lease_owner"] is None


def resume_decision(row, decision):
    evidence = json.loads(row["evidence"])
    evidence.update(decision.evidence or {})
    with get_store().transaction():
        get_store()._execute("UPDATE operation_job SET status='pending', phase=?, evidence=?, "
                            "lease_owner=NULL, lease_token=NULL, lease_until=NULL WHERE job_id=?",
                            [decision.next_phase, json.dumps(evidence), row["job_id"]])


@needs_git
def test_update_crash_after_merge_repairs_floor_without_merging_twice(proj, git_env, monkeypatch):
    gid = FA
    work(proj, gid, "local.txt", "local\n")
    seed = git_env["seed"]
    (seed / "remote.txt").write_text("remote\n")
    _git(["add", "remote.txt"], cwd=seed)
    _git(["commit", "-m", "remote"], cwd=seed)
    _git(["push", "origin", "main"], cwd=seed)
    run = start(gid, "merge")
    class Crash(BaseException):
        pass
    with monkeypatch.context() as m:
        m.setattr(ex.scope_base, "record_update", lambda *a, **kw: (_ for _ in ()).throw(Crash()))
        with pytest.raises(Crash):
            tick(run["jobId"])
    head = rev(proj.wt(gid), "HEAD")
    row = job_store.get_job(run["jobId"])
    decision = ex.decide(row)
    assert decision.verdict == job_runner.APPLIED_NEXT and decision.next_phase == "update_record"
    resume_decision(row, decision)
    done = finish(gid, run["jobId"])
    assert done["status"] == "complete", done
    assert rev(proj.wt(gid), "HEAD") == head
    assert ex.gs.db_git.get_state(gid)["work_base_sync_sha"] == rev(seed, "HEAD")


@needs_git
def test_rejected_push_resumes_with_remote_sync(proj, git_env, monkeypatch):
    from manual_merge_0066_support import reject_push
    gid = FA
    work(proj, gid, "local.txt", "local\n")
    run = start(gid)
    with monkeypatch.context() as m:
        reject_push(m)
        stopped = finish(gid, run["jobId"])
    assert stopped["error"] == "push_rejected", stopped
    seed = git_env["seed"]
    (seed / "remote.txt").write_text("remote\n")
    _git(["add", "remote.txt"], cwd=seed)
    _git(["commit", "-m", "remote"], cwd=seed)
    _git(["push", "origin", "main"], cwd=seed)
    retry = start(gid, resume_from=run["jobId"])
    done = finish(gid, retry["jobId"])
    # Remote synchronization can leave new work to receive in the Flow, but push is verified.
    assert done["status"] in ("complete", "remaining"), done
    assert done["completed"] == ["commit", "update", "merge", "push"]
    assert {"local.txt", "remote.txt"} <= set(proj.origin_files("main"))


def test_resolved_conflict_continues_to_push(monkeypatch):
    p = {"steps": ["merge", "push"], "completed": [], "scope": "push"}
    job = {"job_id": "parent", "group_id": "group", "phase": "conflict",
           "payload": json.dumps(p), "evidence": json.dumps({"merge_id": 7, "completed": []})}
    ctx = SimpleNamespace(job=job, job_id="parent")
    monkeypatch.setattr(ex.gs.db_git, "get_session", lambda _id: {"status": "done"})
    monkeypatch.setattr(ex.store, "ensure_lease", lambda _ctx: None)
    monkeypatch.setattr(ex.store, "job_write", lambda _ctx, **kw: job.update(kw))
    steps = []
    monkeypatch.setattr(ex, "_child", lambda ctx, p, ev, step: steps.append(step) or {})
    monkeypatch.setattr(ex, "_remaining", lambda *a: False)
    result = ex.run(ctx, job_store.PUBLISH)
    assert steps == ["push"]
    assert result.cls == job_store.SUCCESS
    assert job["evidence"]["completed"] == ["merge", "push"]


@needs_git
def test_push_scope_without_changes_completes(proj):
    # 0068 Sol: a Flow with nothing to commit or merge stopped at push with status_unavailable.
    gid = group(proj, 1)
    run = start(gid)
    done = finish(gid, run["jobId"])
    assert done["status"] == "complete", done
    assert done["completed"] == ["commit", "update", "merge", "push"]
    assert held_locks(proj.pid) == []


def _push_ctx(monkeypatch, ahead, results=()):
    monkeypatch.setattr(ex.remote_refresh, "fetch_refs", lambda *a: None)
    monkeypatch.setattr(ex.manual_merge, "merge_results_view", lambda _gid: {"results": list(results)})
    monkeypatch.setattr(ex.manual_merge, "merge_view", lambda _gid, _target=None: {"ahead_of_target": ahead})
    return SimpleNamespace(job={"group_id": "group", "project_id": "project"}, job_id="parent")


def test_push_without_merge_result_and_nothing_ahead_is_skipped(monkeypatch):
    ctx = _push_ctx(monkeypatch, ahead=0)
    assert ex._child(ctx, {"target": "main"}, {}, "push") == {}


def test_push_without_merge_result_but_commits_ahead_says_so(monkeypatch):
    ctx = _push_ctx(monkeypatch, ahead=2)
    with pytest.raises(GitServiceError) as exc:
        ex._child(ctx, {"target": "main"}, {}, "push")
    assert exc.value.code == "merge_result_missing"


def test_push_with_unmeasured_remote_stays_status_unavailable(monkeypatch):
    row = {"target": {"target_branch": "main"}, "remote": {"measured": False}}
    ctx = _push_ctx(monkeypatch, ahead=0, results=[row])
    with pytest.raises(GitServiceError) as exc:
        ex._child(ctx, {"target": "main"}, {}, "push")
    assert exc.value.code == "status_unavailable"
