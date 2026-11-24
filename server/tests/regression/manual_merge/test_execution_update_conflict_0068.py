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


# ── flowgate.v02test.0068: one side rewrote the function the other side added lines to ─────────────
#
# The shape of F80's conflict: the base (F82) added "why it was skipped" log lines inside a function,
# the Flow rewrote that function so those lines have no place any more and already logs the same
# thing in one parameterised line. The right resolution is the Flow's side as it is.

NAMING_BASE = (
    "def name_target(flow, kind):\n"
    "    record = ledger_load(flow)\n"
    "    if record is None:\n"
    "        return {'status': 'unchanged'}\n"
    "    if record.get('busy'):\n"
    "        return {'status': 'unchanged', 'busy': True}\n"
    "    return save_candidate(flow, record)\n"
)
NAMING_THEIRS = (
    "def name_target(flow, kind):\n"
    "    record = ledger_load(flow)\n"
    "    if record is None:\n"
    "        logger.info('flow naming skipped reason=empty_message flow=%s target=%s', flow, 'chat')\n"
    "        return {'status': 'unchanged'}\n"
    "    if record.get('busy'):\n"
    "        logger.info('flow naming skipped reason=busy flow=%s target=%s', flow, 'chat')\n"
    "        return {'status': 'unchanged', 'busy': True}\n"
    "    return save_candidate(flow, record)\n"
)
NAMING_OURS = (
    "def name_target(flow, kind):\n"
    "    group, target, step = guard(flow, claim)\n"
    "    if step in ('missing', 'done'):\n"
    "        return step\n"
    "    if step in ('busy', 'unchanged'):\n"
    "        logger.info('flow naming skipped reason=%s flow=%s target=%s', step, flow, kind)\n"
    "        return 'unchanged'\n"
    "    return apply_candidate(flow, target)\n"
)
KEEP_OURS = {"side": "ours", "reason": "the rewritten function logs every skip reason in one line"}


def waiting_on_rewrite(proj, ours=NAMING_OURS):
    """A run parked on an update conflict whose chunk both sides changed over a common base."""
    gid = FA
    remote(proj, "naming.py", NAMING_BASE)
    work(proj, gid, "note.txt", "first\n")
    first = start(gid)
    assert drive(gid, first["jobId"], lambda v: v["status"] != "running")["status"] == "complete"
    assert (proj.wt(gid) / "naming.py").read_text(encoding="utf-8") == NAMING_BASE
    work(proj, gid, "naming.py", ours)
    _git(["pull", "origin", "main"], cwd=proj.seed)      # the first run pushed the Flow's merge
    remote(proj, "naming.py", NAMING_THEIRS)
    run = start(gid)
    view = drive(gid, run["jobId"], lambda v: v["mergeId"] is not None)
    return gid, run["jobId"], view


@needs_git
def test_rewritten_side_is_refused_bare_recorded_as_ai_failure_then_accepted_declared(proj):
    gid, job_id, view = waiting_on_rewrite(proj)
    merge_id = view["mergeId"]
    assert view["conflictAi"] is None
    files = [{"path": "naming.py", "content": NAMING_OURS}]
    # The update merge writes the base block too, so "both sides changed" can be judged here as well.
    assert "\n||||||| " in (proj.wt(gid) / "naming.py").read_text(encoding="utf-8")
    # 1) Keeping one side without saying why is still refused, and nothing is written.
    with pytest.raises(conflict.GitServiceError) as refused:
        conflict.resolve_conflicts(gid, merge_id, files, True, resolver_run_id="run-ai-1")
    assert refused.value.code == "conflict_side_dropped"
    # 2) The refusal is on the session; when that AI run ends with the conflict left, it FAILED and
    #    both the conflicts answer and the waiting run say so (no more silent 「충돌 대기」).
    attempt = conflict.list_conflicts(gid, merge_id)["resolver_attempt"]
    assert (attempt["run_id"], attempt["state"], attempt["code"]) == ("run-ai-1", "rejected", "conflict_side_dropped")
    assert conflict.note_resolver_finished(merge_id, {"run_id": "run-ai-1", "stop_code": "turn_limit"}) is True
    waiting = ex.view(tick(job_id))
    assert waiting["status"] == "running" and waiting["mergeId"] == merge_id
    assert waiting["conflictAi"]["state"] == "failed"
    assert waiting["conflictAi"]["code"] == "conflict_side_dropped" and waiting["conflictAi"]["path"] == "naming.py"
    # 3) The same text with the declaration passes: every line the base added is still carried by
    #    the rewritten function (reworded), so no side was dropped. The same run goes on to push.
    resolved = conflict.resolve_conflicts(
        gid, merge_id, [{**files[0], "supersede": KEEP_OURS}], True, resolver_run_id="run-ai-2")
    assert resolved["result"]["status"] == "updated"
    done = drive(gid, job_id, lambda v: v["status"] != "running")
    assert done["status"] == "complete" and done["conflictAi"] is None, done
    assert proj.show("main", "naming.py") == NAMING_OURS


@needs_git
def test_declared_side_that_really_lost_a_line_is_still_refused_with_that_line(proj):
    # The rewritten function WITHOUT its log line: the base's two log lines are gone for real.
    lost = NAMING_OURS.replace(
        "        logger.info('flow naming skipped reason=%s flow=%s target=%s', step, flow, kind)\n", "")
    gid, job_id, view = waiting_on_rewrite(proj, ours=lost)
    merge_id = view["mergeId"]
    with pytest.raises(conflict.GitServiceError) as refused:
        conflict.resolve_conflicts(gid, merge_id, [{"path": "naming.py", "content": lost, "supersede": KEEP_OURS}],
                                   True, resolver_run_id="run-ai-3")
    assert refused.value.code == "conflict_supersede_invalid"
    problem = refused.value.details["files"][0]
    assert problem["condition"] == "dropped_lines_removed" and len(problem["removed_lines"]) == 2
    attempt = conflict.list_conflicts(gid, merge_id)["resolver_attempt"]
    assert attempt["code"] == "conflict_supersede_invalid" and attempt["line_count"] == 2
    assert "reason=empty_message" in attempt["lines"][0]
    assert ex.view(tick(job_id))["mergeId"] == merge_id      # still waiting, nothing committed


def test_resolver_that_resolved_everything_is_no_failure(monkeypatch):
    monkeypatch.setattr(gs.db_git, "get_session", lambda mid: {"merge_id": mid, "status": "open", "group_id": "g"})
    monkeypatch.setattr(gs.db_git, "remaining_conflicts", lambda mid: [])
    assert conflict.note_resolver_finished(7, {"run_id": "r"}) is False
    monkeypatch.setattr(gs.db_git, "get_session", lambda mid: None)
    assert conflict.note_resolver_finished(7, {"run_id": "r"}) is False


# ── a closed 받아오기 session is announced, whatever closed it ─────────────────────────────────────
# An update conflict never writes the group status, so nothing told an open screen that it had
# ended: the Flow tab kept its conflict mark after 세션 중단. Every close now sends
# git_status_changed (reason update_session_closed) for the group.

def closed_notices(monkeypatch):
    seen = []
    monkeypatch.setattr(gs, "_emit", lambda event, project, group, payload: seen.append((event, project, group, payload)))
    return lambda: [(group, payload["merge_id"], payload["session_status"]) for event, _project, group, payload in seen
                    if event == "git_status_changed" and payload.get("reason") == "update_session_closed"]


@needs_git
def test_update_conflict_abort_announces_the_closed_session(proj, monkeypatch):
    gid, _job_id, view = waiting_on_update(proj)
    closed = closed_notices(monkeypatch)
    conflict.abort_merge(gid, view["mergeId"])
    assert closed() == [(gid, view["mergeId"], "aborted")]
    assert conflict_row(gid) is None


@needs_git
def test_update_conflict_resolve_announces_the_closed_session(proj, monkeypatch):
    gid, _job_id, view = waiting_on_update(proj)
    closed = closed_notices(monkeypatch)
    conflict.resolve_conflicts(gid, view["mergeId"], [{"path": "same.txt", "content": "base\nwork\n"}], True)
    assert closed() == [(gid, view["mergeId"], "done")]


@needs_git
def test_closed_session_notice_waits_for_the_commit(proj, monkeypatch):
    gid, _job_id, view = waiting_on_update(proj)
    closed = closed_notices(monkeypatch)
    with get_store().transaction():
        gs._announce_update_session_closed(view["mergeId"], "done")
        # A reader woken now would still find the session open.
        assert closed() == []
    assert closed() == [(gid, view["mergeId"], "done")]


def test_closed_session_notice_is_for_update_sessions_only(monkeypatch):
    sessions = {
        3: {"merge_id": 3, "group_id": "g1", "kind": gs.db_git.SESSION_KIND_GROUP_UPDATE},
        4: {"merge_id": 4, "group_id": "g1", "kind": gs.db_git.SESSION_KIND_MERGE},
        5: {"merge_id": 5, "group_id": None, "kind": gs.db_git.SESSION_KIND_GROUP_UPDATE},
    }
    monkeypatch.setattr(gs.db_git, "get_session", lambda merge_id: sessions.get(merge_id))
    monkeypatch.setattr(gs.db_git, "session_kind", lambda s: s["kind"])
    monkeypatch.setattr(gs, "_project_of_group", lambda group_id: "proj")
    closed = closed_notices(monkeypatch)
    for merge_id in (4, 5, 99):
        gs._announce_update_session_closed(merge_id, "aborted")
    assert closed() == []
    gs._announce_update_session_closed(3, "aborted")
    assert closed() == [("g1", 3, "aborted")]
    # Registered once on the session close notice.
    assert [cb.__name__ for cb in gs.db_git._close_listeners].count("_announce_update_session_closed") == 1
