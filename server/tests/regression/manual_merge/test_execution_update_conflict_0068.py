"""flowgate.v02test.0068: a run that stops on a 받아오기 (group update) conflict.

Real Git + SQLite jobs. The update conflict session is shown as the group's conflict row
(git/status), so the ordinary conflict card works on it; resolving it lets the same run go on
to merge and push, aborting it stops the run with ``merge_aborted``.
"""
import uuid
from types import SimpleNamespace

import pytest

from manual_merge_0066_support import work, rev, _git, held_locks, needs_git
from test_fg_gate_intake_0085 import product, env, db_path, FA  # noqa: F401
from test_fg_gate_publish_0085 import git_env, PROJECT  # noqa: F401
from modules.flow_gate.db import operation_job as jobs
from modules.flow_gate.db.connection import get_store
from modules.flow_gate.services import git_service as gs
from modules.flow_gate.services.git import execution as ex, job_store, job_runner, conflict


@pytest.fixture
def proj(git_env):
    r = git_env
    return SimpleNamespace(pid=PROJECT, base=r["base"], bare=r["bare"], seed=r["seed"],
        wt=lambda gid: r["wt"],
        show=lambda branch, path: _git(["show", f"{branch}:{path}"], cwd=r["bare"]))


def start(gid, scope="push"):
    return ex.start(gid, scope=scope, all=True, message="fix(test): update conflict",
                    request_id=str(uuid.uuid4()))


def tick(job_id):
    with get_store().transaction():
        get_store()._execute("UPDATE operation_job SET available_at = ? WHERE job_id = ?",
                            ["2000-01-01T00:00:00+00:00", job_id])
    claim = job_runner.try_claim(job_id)
    assert claim is not None
    job_runner.execute(claim)
    return job_store.get_job(job_id)


def drive(gid, job_id, until):
    """Ticks the run and its child jobs until ``until(view)`` holds (a bounded number of rounds)."""
    for _ in range(12):
        for kind in (ex.KIND, "base_publish"):
            for row in jobs.jobs_of_group(gid, kind, ("pending", "blocked", "retry_wait")):
                tick(row["job_id"])
        view = ex.view(job_store.get_job(job_id))
        if until(view):
            return view
    pytest.fail(str(view))


def remote(proj, name, content):
    (proj.seed / name).write_text(content, encoding="utf-8")
    _git(["add", name], cwd=proj.seed)
    _git(["commit", "-m", "base change"], cwd=proj.seed)
    _git(["push", "origin", "main"], cwd=proj.seed)


def conflict_row(gid):
    rows = gs.project_git_status(PROJECT)["status"]["pending"]
    return next((r for r in rows if r["group_id"] == gid and r["status"] == "conflict"), None)


def waiting_on_update(proj):
    gid = FA
    work(proj, gid, "same.txt", "work\n")
    remote(proj, "same.txt", "base\n")
    run = start(gid)
    view = drive(gid, run["jobId"], lambda v: v["mergeId"] is not None)
    return gid, run["jobId"], view


@needs_git
def test_update_conflict_is_a_conflict_row_and_resolving_it_pushes(proj):
    gid, job_id, view = waiting_on_update(proj)
    # The run waits on the update session: still running, parked at 받아오기, nothing skipped.
    assert view["status"] == "running" and view["completed"] == ["commit"], view
    assert view["steps"][view["cursor"]] == "update" and not view["skipped"]
    session = gs.db_git.get_session(view["mergeId"])
    assert gs.db_git.session_kind(session) == gs.db_git.SESSION_KIND_GROUP_UPDATE
    # git/status names it as the group's conflict, the row the conflict card is drawn from.
    row = conflict_row(gid)
    assert row is not None and row["merge_id"] == view["mergeId"] and row["review_state"] is None
    assert held_locks(proj.pid) == []
    # Waiting is no failure: another round keeps the same wait.
    assert ex.view(tick(job_id))["mergeId"] == view["mergeId"]
    resolved = conflict.resolve_conflicts(gid, view["mergeId"],
                                          [{"path": "same.txt", "content": "base\nwork\n"}], True)
    assert resolved["result"]["status"] == "updated"
    assert conflict_row(gid) is None
    done = drive(gid, job_id, lambda v: v["status"] != "running")
    assert done["status"] == "complete", done
    assert done["completed"] == ["commit", "update", "merge", "push"]
    assert proj.show("main", "same.txt") == "base\nwork\n"
    assert held_locks(proj.pid) == []


@needs_git
def test_update_conflict_abort_stops_the_run(proj):
    gid, job_id, view = waiting_on_update(proj)
    before = rev(proj.wt(gid), "HEAD")
    conflict.abort_merge(gid, view["mergeId"])
    stopped = drive(gid, job_id, lambda v: v["status"] != "running")
    assert stopped["status"] == "stopped" and stopped["error"] == "merge_aborted", stopped
    assert stopped["completed"] == ["commit"]
    assert conflict_row(gid) is None
    assert rev(proj.wt(gid), "HEAD") == before
    # Nothing of the Flow reached the target: it still has the base side only.
    assert proj.show("main", "same.txt") == "base\n"


def test_update_conflict_sessions_picks_open_group_updates_of_listed_slots(monkeypatch):
    from modules.flow_gate.services.git import status
    open_sessions = [
        {"merge_id": 3, "group_id": "g1", "kind": gs.db_git.SESSION_KIND_GROUP_UPDATE},
        {"merge_id": 4, "group_id": "g2", "kind": gs.db_git.SESSION_KIND_MERGE},
        {"merge_id": 5, "group_id": "other", "kind": gs.db_git.SESSION_KIND_GROUP_UPDATE},
    ]
    monkeypatch.setattr(gs.db_git, "list_open_sessions", lambda: open_sessions)
    monkeypatch.setattr(gs.db_git, "session_kind", lambda s: s["kind"])
    found = status.update_conflict_sessions([{"group_id": "g1"}, {"group_id": "g2"}])
    assert list(found) == ["g1"] and found["g1"]["merge_id"] == 3
    monkeypatch.setattr(gs.db_git, "list_open_sessions", lambda: (_ for _ in ()).throw(RuntimeError("db")))
    assert status.update_conflict_sessions([{"group_id": "g1"}]) == {}
