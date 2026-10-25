"""0061 T#13 regression specifications: the Graph Time Machine operation (Git step, DB step, journal, recovery).

TR Self-check only; formal PASS/gate belongs to TS/TSR. The Git step runs real ``git`` in a temporary repository (the
group branch with A / B / J merges); the cancel session that would take the group's G from the DB is replaced by a
session on that repository, or (``gated``) is the real ``open_cancel_session`` with only its DB-side gates and G stood
in, so its dirty-worktree gate (G10) and commits gate (G11) run on the repository. A crash is a ``BaseException`` raised
right after a real ``git revert --no-commit`` (before its commit, inside its conflict, before a compensation's commit).
The DB step runs on in-memory SQLite. No provider, server or DB file is used.
"""
from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import Mock
import json
import sqlite3
import subprocess

import pytest

from flowgate.graph_time_machine import git_revert, journal, operation, rewind
from modules.flow_gate.graph_workflow.api import graph_messages
from modules.flow_gate.graph_workflow.services.errors import GraphApiError
from modules.flow_gate.graph_workflow.store import run_store


# ── the Git step on a real repository ────────────────────────────────────────

def _run(repo, *args):
    return subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.com", *args], cwd=repo,
                          check=True, capture_output=True, text=True).stdout.strip()


@pytest.fixture
def branch(tmp_path, monkeypatch):
    """base -> merge A -> merge B -> merge J (J started from the group HEAD: the join of A and B)."""
    repo = tmp_path / "group"
    repo.mkdir()
    g = lambda *a: _run(repo, *a)  # noqa: E731
    g("init", "-q")
    g("config", "core.autocrlf", "false")
    g("checkout", "-q", "-b", "group")
    for name in ("a", "b", "j"):
        (repo / f"{name}.txt").write_bytes(b"base\n")
    g("add", ".")
    g("commit", "-qm", "base")
    base = g("rev-parse", "HEAD")

    def attempt(key, start):
        g("checkout", "-q", "-b", "attempt_" + key, start)
        (repo / f"{key.lower()}.txt").write_bytes(key.encode() + b"\n")
        g("commit", "-qam", "result " + key)
        result = g("rev-parse", "HEAD")
        g("checkout", "-q", "group")
        g("merge", "--no-ff", "-q", "-m", f"Merge branch 'attempt_{key}' into group", result)
        return {"attempt_id": key.lower(), "node_key": key, "result_commit": result, "commit_sha": g("rev-parse", "HEAD")}

    a = attempt("A", base)
    b = attempt("B", base)
    j = attempt("J", g("rev-parse", "HEAD"))
    session = {"wt_path": repo, "author_env": None}
    opened = Mock(return_value={"ok": True, "session": session})
    monkeypatch.setattr(git_revert.git, "open_cancel_session", opened)
    return SimpleNamespace(repo=repo, g=g, a=a, b=b, j=j, head=g("rev-parse", "HEAD"), opened=opened)


def text(branch, name):
    return (branch.repo / name).read_text().strip()


def first_parent(branch):
    return branch.g("rev-list", "--first-parent", "HEAD").split()


def test_reverts_each_target_merge_newest_first_without_moving_head_back(branch):
    out = git_revert.apply("flow", "gtm_1", [branch.a, branch.j])
    assert out["status"] == git_revert.OK and out["pre_head"] == branch.head
    assert [(r["node_key"], r["outcome"]) for r in out["reverts"]] == [("J", "reverted"), ("A", "reverted")]
    # A and its downstream J are gone from the tree, B's result stays.
    assert (text(branch, "a.txt"), text(branch, "b.txt"), text(branch, "j.txt")) == ("base", "B", "base")
    # The branch only grew: the old HEAD is an ancestor, two revert commits on top, nothing rewritten.
    line = first_parent(branch)
    assert line[2] == branch.head and line[:2] == [r["revert_commit"] for r in reversed(out["reverts"])]
    body = branch.g("log", "-1", "--format=%B", out["reverts"][0]["revert_commit"])
    assert "FlowGate-Time-Machine: gtm_1" in body and f"FlowGate-Reverts: {branch.j['commit_sha']}" in body
    assert len(branch.g("rev-list", "--parents", "-n", "1", line[0]).split()) == 2     # a plain revert commit
    assert branch.g("status", "--porcelain") == ""


def test_conflict_is_compensated_and_the_branch_reads_as_it_was(branch):
    (branch.repo / "a.txt").write_bytes(b"A2\n")                  # later work on the same lines, not a target
    branch.g("commit", "-qam", "later change of a")
    before = branch.g("rev-parse", "HEAD")
    out = git_revert.apply("flow", "gtm_2", [branch.a, branch.j])
    assert out["status"] == git_revert.BLOCKED and out["compensated"] is True
    assert out["reason"] == "revert_conflict" and out["commit_sha"] == branch.a["commit_sha"]
    assert branch.g("diff", before, "HEAD") == ""                # the tree of the start
    assert branch.g("merge-base", "--is-ancestor", before, "HEAD") == ""   # never moved back
    assert branch.g("status", "--porcelain") == ""
    assert not (branch.repo / ".git" / "REVERT_HEAD").exists()
    assert (text(branch, "b.txt"), text(branch, "j.txt")) == ("B", "J")      # nothing of another branch removed


def test_resume_never_reverts_twice_and_finishes_the_rest(branch):
    first = git_revert.apply("flow", "gtm_3", [branch.j])
    assert first["status"] == git_revert.OK
    out = git_revert.apply("flow", "gtm_3", [branch.a, branch.j], resume=True)
    assert out["status"] == git_revert.OK
    assert out["reverts"][0]["revert_commit"] == first["reverts"][0]["revert_commit"]
    assert out["pre_head"] == branch.head
    marked = [s for s in first_parent(branch)[:5]
              if "FlowGate-Time-Machine: gtm_3" in branch.g("log", "-1", "--format=%B", s)]
    assert len(marked) == 2
    again = git_revert.apply("flow", "gtm_3", [branch.a, branch.j], resume=True)   # everything already there
    assert again["status"] == git_revert.OK and len(first_parent(branch)) == len(marked) + len(
        branch.g("rev-list", "--first-parent", branch.head).split())


@pytest.mark.parametrize("case", ["unverified", "off_branch"])
def test_a_merge_that_is_not_the_attempts_is_refused_untouched(branch, case):
    target = dict(branch.j, result_commit=branch.a["result_commit"]) if case == "unverified" else \
        dict(branch.a, commit_sha=branch.a["result_commit"])     # A's result itself: on a side line only
    out = git_revert.apply("flow", "gtm_4", [target])
    assert out["status"] == git_revert.BLOCKED and out["compensated"] is False
    assert out["reason"] == ("integration_commit_unverified" if case == "unverified" else "integration_commit_off_branch")
    assert branch.g("rev-parse", "HEAD") == branch.head


# ── rework: interruption, restore proof, history reads (rej_01M4W2J62CDJRF6G) ──

@pytest.fixture
def gated(branch, monkeypatch):
    """The real cancel session on the repository: G10 (dirty worktree) and G11 run; the DB gates and G are stood in."""
    from modules.flow_gate.services import git_service
    from modules.flow_gate.services.git import commit
    monkeypatch.setattr(commit, "_cancel_prelock_gate", lambda gid: {
        "blocked_reason": None, "block_sub": None, "project_id": "p", "cfg": {}, "state": {"branch": "group"}})
    monkeypatch.setattr(commit, "_acquire_group_lock", lambda *a, **k: ("lock",))
    monkeypatch.setattr(commit, "_release_group_lock", lambda lock: None)
    monkeypatch.setattr(git_service, "get_branch_merge_group_claim", lambda gid: None)
    monkeypatch.setattr(git_service, "_project_name", lambda pid: "p")
    monkeypatch.setattr(git_service, "src_root", lambda name, b: branch.repo)
    monkeypatch.setattr(git_revert.git, "open_cancel_session", commit.open_cancel_session)
    return branch


class Crash(BaseException):
    """The server dies: nothing of the step after it runs (no restore, no commit)."""


def crash_at(monkeypatch, branch, when):
    """The revert whose body ``when`` picks dies right after its real ``git revert --no-commit``."""
    real = git_revert.git.revert_tr_commit

    def revert(session, *, commit_sha, subject, body, mainline=None):
        if when(body):
            subprocess.run(["git", "revert", "--no-commit", "--no-edit", *(["-m", str(mainline)] if mainline else []),
                            commit_sha], cwd=branch.repo, capture_output=True)
            raise Crash()
        return real(session, commit_sha=commit_sha, subject=subject, body=body, mainline=mainline)
    monkeypatch.setattr(git_revert.git, "revert_tr_commit", revert)
    return lambda: monkeypatch.setattr(git_revert.git, "revert_tr_commit", real)


def reverting(target):
    return lambda body: f"FlowGate-Reverts: {target['commit_sha']}" in body


def tracked_status(branch):
    return branch.g("status", "--porcelain", "--untracked-files=no")


def marker(branch, operation_id):
    return git_revert._marker_path(branch.repo, operation_id).exists()


def ours(branch, operation_id):
    return [s for s in first_parent(branch)
            if f"FlowGate-Time-Machine: {operation_id}" in branch.g("log", "-1", "--format=%B", s)]


def later_change_of_a(branch):
    (branch.repo / "a.txt").write_bytes(b"A2\n")
    branch.g("commit", "-qam", "later change of a")
    return branch.g("rev-parse", "HEAD")


def in_progress(branch):
    from modules.flow_gate.services import git_service
    return git_service.check_git_operation_in_progress(branch.repo)


def test_a_step_interrupted_before_its_commit_is_put_back_and_resumed(gated, monkeypatch):
    restore = crash_at(monkeypatch, gated, reverting(gated.a))
    with pytest.raises(Crash):
        git_revert.apply("flow", "gtm_i1", [gated.a, gated.j])
    assert tracked_status(gated) and marker(gated, "gtm_i1")          # A's revert staged, never committed
    restore()
    # The general gate stays: a first run of anything else is refused on this tree.
    other = git_revert.apply("flow", "gtm_other", [gated.a])
    assert (other["status"], other["reason"]) == (git_revert.BLOCKED, "dirty_worktree")
    out = git_revert.apply("flow", "gtm_i1", [gated.a, gated.j], resume=True)
    assert out["status"] == git_revert.OK
    assert [(r["node_key"], r["outcome"]) for r in out["reverts"]] == [("J", "reverted"), ("A", "reverted")]
    assert len(ours(gated, "gtm_i1")) == 2                              # J once, A once
    assert (text(gated, "a.txt"), text(gated, "b.txt"), text(gated, "j.txt")) == ("base", "B", "base")
    assert gated.g("status", "--porcelain") == "" and not marker(gated, "gtm_i1")
    assert gated.g("merge-base", "--is-ancestor", gated.head, "HEAD") == ""


def test_dirt_that_is_not_the_steps_keeps_the_gate_closed(gated, monkeypatch):
    restore = crash_at(monkeypatch, gated, reverting(gated.a))
    with pytest.raises(Crash):
        git_revert.apply("flow", "gtm_i2", [gated.a, gated.j])
    restore()
    (gated.repo / "b.txt").write_bytes(b"someone else\n")              # not a path of A's merge
    out = git_revert.apply("flow", "gtm_i2", [gated.a, gated.j], resume=True)
    assert (out["status"], out["reason"]) == (git_revert.DEFERRED, "dirty_worktree")
    assert text(gated, "b.txt") == "someone else" and marker(gated, "gtm_i2")   # nothing removed


def test_a_step_interrupted_in_its_conflict_is_put_back_and_blocked(gated, monkeypatch):
    before = later_change_of_a(gated)
    restore = crash_at(monkeypatch, gated, reverting(gated.a))
    with pytest.raises(Crash):
        git_revert.apply("flow", "gtm_i3", [gated.a, gated.j])
    assert "UU a.txt" in tracked_status(gated)                          # J reverted, A in its conflict
    restore()
    out = git_revert.apply("flow", "gtm_i3", [gated.a, gated.j], resume=True)
    assert out["status"] == git_revert.BLOCKED and out["compensated"] is True
    assert gated.g("diff", before, "HEAD") == "" and gated.g("status", "--porcelain") == ""
    assert gated.g("merge-base", "--is-ancestor", before, "HEAD") == ""
    assert in_progress(gated) is None and not marker(gated, "gtm_i3")


def test_a_compensation_interrupted_before_its_commit_is_finished(gated, monkeypatch):
    before = later_change_of_a(gated)
    restore = crash_at(monkeypatch, gated, lambda body: "FlowGate-Compensates" in body)
    with pytest.raises(Crash):
        git_revert.apply("flow", "gtm_i4", [gated.a, gated.j])          # J reverted, A conflicts, J's undo dies
    assert tracked_status(gated) and marker(gated, "gtm_i4")
    restore()
    out = git_revert.apply("flow", "gtm_i4", [gated.a, gated.j], resume=True)
    assert out["status"] == git_revert.BLOCKED and out["compensated"] is True
    assert gated.g("diff", before, "HEAD") == "" and gated.g("status", "--porcelain") == ""
    assert in_progress(gated) is None and not marker(gated, "gtm_i4")


@pytest.mark.parametrize("targets", ["a", "aj"])
def test_a_restore_that_fails_is_a_partial_failure_never_blocked(gated, monkeypatch, targets):
    before = later_change_of_a(gated)
    real = git_revert.git.restore_after_failed_revert
    monkeypatch.setattr(git_revert.git, "restore_after_failed_revert", lambda session: None)  # --quit / reset failed
    chosen = [gated.a] + ([gated.j] if targets == "aj" else [])
    out = git_revert.apply("flow", "gtm_r1", chosen)
    assert (out["status"], out["reason"]) == (git_revert.FAILED, "restore_failed")
    assert "UU a.txt" in tracked_status(gated) and marker(gated, "gtm_r1")
    assert len(out["reverts"]) == (1 if targets == "aj" else 0)          # J's revert is reported, never dropped
    monkeypatch.setattr(git_revert.git, "restore_after_failed_revert", real)
    again = git_revert.apply("flow", "gtm_r1", chosen, resume=True)      # the next start: put back, then proven
    assert again["status"] == git_revert.BLOCKED and again["compensated"] is (targets == "aj")
    assert gated.g("diff", before, "HEAD") == "" and gated.g("status", "--porcelain") == ""
    assert in_progress(gated) is None and not marker(gated, "gtm_r1")


@pytest.mark.parametrize("failing", [("rev-list", "--first-parent"), ("log", "--first-parent"),
                                     ("rev-list", "--parents")])
def test_a_failed_history_read_is_a_partial_failure_never_blocked(branch, monkeypatch, failing):
    assert git_revert.apply("flow", "gtm_h1", [branch.j])["status"] == git_revert.OK
    real_run = git_revert.git._run_git

    def run(args, **kwargs):
        if tuple(args[:2]) == failing:
            return SimpleNamespace(returncode=128, stdout="", stderr="fatal: injected")
        return real_run(args, **kwargs)
    monkeypatch.setattr(git_revert.git, "_run_git", run)
    out = git_revert.apply("flow", "gtm_h1", [branch.a, branch.j], resume=True)
    assert (out["status"], out["reason"]) == (git_revert.FAILED, "git_read_failed")
    assert text(branch, "j.txt") == "base" and len(ours(branch, "gtm_h1")) == 1   # the revert stays, not "blocked"


def test_the_recorded_reverts_must_be_on_the_branch(branch):
    first = git_revert.apply("flow", "gtm_h2", [branch.j])
    wrong = [dict(first["reverts"][0], revert_commit="0" * 40)]
    out = git_revert.apply("flow", "gtm_h2", [branch.a, branch.j], resume=True, recorded=wrong)
    assert (out["status"], out["reason"]) == (git_revert.FAILED, "journal_mismatch")
    assert len(out["reverts"]) == 1
    good = git_revert.apply("flow", "gtm_h2", [branch.a, branch.j], resume=True, recorded=first["reverts"])
    assert good["status"] == git_revert.OK and len(ours(branch, "gtm_h2")) == 2


def test_the_journal_is_read_past_any_depth(branch):
    first = git_revert.apply("flow", "gtm_h3", [branch.j])
    tip = branch.g("rev-parse", "HEAD")
    stream = []
    for i in range(2100):                                               # later commits on top of the revert
        message = f"later {i}\n"
        stream.append(f"commit refs/heads/group\ncommitter t <t@example.com> {1700000000 + i} +0000\n"
                      f"data {len(message)}\n{message}" + (f"from {tip}\n" if i == 0 else "") + "\n")
    subprocess.run(["git", "fast-import", "--quiet"], cwd=branch.repo, input="".join(stream).encode(), check=True)
    assert branch.g("status", "--porcelain") == ""
    out = git_revert.apply("flow", "gtm_h3", [branch.a, branch.j], resume=True)
    assert out["status"] == git_revert.OK
    assert out["reverts"][0]["revert_commit"] == first["reverts"][0]["revert_commit"]
    assert len(ours(branch, "gtm_h3")) == 2                             # J never reverted twice


def test_session_refusal_blocks_a_first_run_and_defers_a_resume(branch):
    branch.opened.return_value = {"ok": False, "blocked_reason": "git_busy", "block_sub": "lock_timeout"}
    assert git_revert.apply("flow", "gtm_5", [branch.a])["status"] == git_revert.BLOCKED
    assert git_revert.apply("flow", "gtm_5", [branch.a], resume=True)["status"] == git_revert.DEFERRED
    assert git_revert.apply("flow", "gtm_5", [])["status"] == git_revert.OK     # no Git work: no session at all


# ── the DB step on SQLite ────────────────────────────────────────────────────

SCHEMA = """
CREATE TABLE graph_runs(run_id TEXT, group_id TEXT, revision_no INTEGER, status TEXT, active_group_id TEXT,
    started_at INTEGER, started_by TEXT, stopped_at INTEGER, finished_at INTEGER, last_event_seq INTEGER,
    policy_source TEXT, policy_set_id TEXT, policy_set_version TEXT, policy_digest TEXT, updated_at INTEGER,
    run_mode TEXT, idle_reason TEXT, step_node_keys TEXT);
CREATE TABLE graph_node_runs(run_id TEXT, node_key TEXT, status TEXT, result_doc_id TEXT, root_bound INTEGER,
    opened_at INTEGER, finished_at INTEGER, dispatch_token_issued INTEGER, dispatch_issued_to TEXT,
    dispatch_mention_id TEXT, dispatch_error_code TEXT, stage TEXT, instruction_doc_id TEXT,
    result_event_floor INTEGER, updated_at INTEGER);
CREATE TABLE graph_node_attempts(attempt_id TEXT, run_id TEXT, node_key TEXT, revision_no INTEGER, status TEXT,
    active_node_key TEXT, created_at TEXT, ended_at TEXT, invalidated_reason TEXT, result_commit TEXT,
    integration_status TEXT, integrated_commit TEXT, input_commit TEXT);
CREATE TABLE graph_run_token_map(token_id TEXT, run_id TEXT, node_key TEXT, stage TEXT, attempt_id TEXT,
    mapped_at INTEGER);
CREATE TABLE graph_attempt_runs(exec_id TEXT, attempt_id TEXT, run_id TEXT, status TEXT);
CREATE TABLE graph_propagations(run_id TEXT, source_event_seq INTEGER, node_key TEXT, outcome TEXT, status TEXT,
    created_at INTEGER, done_at INTEGER);
"""


@pytest.fixture
def db(monkeypatch):
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    connection.executescript(SCHEMA)
    connection.execute("INSERT INTO graph_runs (run_id, group_id, revision_no, status, active_group_id, "
                       "last_event_seq, run_mode, idle_reason) VALUES ('run','flow',1,'idle','flow',9,'full','completed')")
    nodes = {"R": "done", "A": "done", "B": "done", "J": "done", "C": "review", "D": "pending"}
    for key, status in nodes.items():
        connection.execute("INSERT INTO graph_node_runs (run_id, node_key, status, result_doc_id, root_bound) "
                           "VALUES ('run',?,?,?,?)", [key, status, None if status == "pending" else "doc-" + key,
                                                      1 if key == "R" else 0])
    for key in ("A", "B", "J", "C"):
        status = "active" if key == "C" else "ended"
        merged = "merge-" + key if key != "C" else None
        connection.execute("INSERT INTO graph_node_attempts VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                           [key.lower(), "run", key, 1, status, key if status == "active" else None, key, None, None,
                            "result-" + key, "integrated" if merged else "none", merged, "input-" + key])
        connection.execute("INSERT INTO graph_run_token_map (token_id, run_id, node_key, stage, attempt_id, mapped_at) "
                           "VALUES (?,?,?,?,?,?)",
                           ["tok-" + key, "run", key, "result", key.lower(), 1])
        if key != "C":
            connection.execute("INSERT INTO graph_propagations VALUES ('run',?,?,'approved','pending',1,NULL)",
                               [ord(key), key])
    def affected(sql, params):
        return connection.execute(sql, params).rowcount
    monkeypatch.setattr(run_store, "available", lambda: True)
    monkeypatch.setattr(run_store, "_all", lambda sql, p: [dict(r) for r in connection.execute(sql, p).fetchall()])
    monkeypatch.setattr(run_store, "_one", lambda sql, p: (lambda r: dict(r) if r else None)(
        connection.execute(sql, p).fetchone()))
    monkeypatch.setattr(run_store, "_affected", affected)
    from modules.flow_gate.services import ai_invoke_service
    revoked = []
    monkeypatch.setattr(ai_invoke_service, "_reclaim_orphan_lease_token",
                        lambda row, reason: revoked.append((row["token_id"], reason)))
    docs = {"doc-A": "approved", "doc-J": "approved", "doc-C": "pending_review", "doc-B": "approved"}

    def cas(doc_id, expected, nxt):
        if docs.get(doc_id) != expected:
            return None
        docs[doc_id] = nxt
        return {"doc_id": doc_id}
    monkeypatch.setattr(rewind.db_docs, "update_review_status_cas", cas)
    ledger = {k.lower(): {"id": i, "state": "live", "commit_sha": "result-" + k} for i, k in enumerate("ABJ", 1)}
    canceled = {}

    def mark(row_id, *, cancel_commit, reason=None):
        canceled[row_id] = (cancel_commit, reason)
        return True
    monkeypatch.setattr(rewind.tr_commit_ledger, "by_attempt", lambda aid: ledger.get(aid))
    monkeypatch.setattr(rewind.tr_commit_ledger, "mark_canceled", mark)
    emitted = []
    monkeypatch.setattr(rewind.run_apply, "emit_node_state",
                        lambda ctx, key, frm, to, cause, doc: emitted.append((key, frm, to, cause)))
    monkeypatch.setattr(rewind.run_apply, "read_run_state", lambda ctx: None)
    monkeypatch.setattr(rewind.settle, "idle_reason", lambda ctx, rows, state: "no_targets")
    yield SimpleNamespace(connection=connection, revoked=revoked, docs=docs, canceled=canceled, emitted=emitted)
    connection.close()


def ctx_of():
    run = run_store.run_by_id("run")
    return SimpleNamespace(run=run, run_id="run", flow_id="flow", project_id="p", actor="u")


PLAN = {"node_key": "A", "node_keys": ["A", "C", "D", "J"], "attempt_ids": ["a", "c", "j"],
        "documents": [{"doc_id": d, "attempt_id": d[-1].lower(), "node_key": d[-1], "stage": "result"}
                      for d in ("doc-A", "doc-J", "doc-C")],
        "commits": [{"attempt_id": k.lower(), "node_key": k, "commit_sha": "merge-" + k, "result_commit": "result-" + k}
                    for k in ("A", "J")]}
REVERTS = [{"attempt_id": "j", "node_key": "J", "commit_sha": "merge-J", "revert_commit": "rev-J", "outcome": "reverted"},
           {"attempt_id": "a", "node_key": "A", "commit_sha": "merge-A", "revert_commit": None, "outcome": "empty"}]


def rows(db, sql):
    return [dict(r) for r in db.connection.execute(sql).fetchall()]


def test_db_step_rewinds_the_selected_branch_and_keeps_the_other(db):
    result = rewind.rewind(ctx_of(), PLAN, REVERTS)
    attempts = {r["attempt_id"]: r for r in rows(db, "SELECT * FROM graph_node_attempts")}
    assert {k: (a["status"], a["invalidated_reason"]) for k, a in attempts.items()} == {
        "a": ("invalidated", "time_machine"), "j": ("invalidated", "time_machine"),
        "c": ("invalidated", "time_machine"), "b": ("ended", None)}
    assert attempts["c"]["active_node_key"] is None and attempts["c"]["ended_at"]
    nodes = {r["node_key"]: (r["status"], r["result_doc_id"]) for r in rows(db, "SELECT * FROM graph_node_runs")}
    assert nodes == {"R": ("done", "doc-R"), "A": ("pending", None), "B": ("done", "doc-B"),
                     "J": ("pending", None), "C": ("pending", None), "D": ("pending", None)}
    assert sorted(result["reset_node_keys"]) == ["A", "C", "J"]
    assert {(k, t) for k, _f, t, c in db.emitted if c == "graph_time_machine"} == {("A", "pending"), ("J", "pending"),
                                                                                  ("C", "pending")}
    assert sorted(t for t, r in db.revoked) == ["tok-A", "tok-C", "tok-J"] and {r for _t, r in db.revoked} == {
        "time_machine"}
    # Old tokens are no evidence any more: reconciliation / the result binder take nothing of them.
    assert run_store.token_ids("run", "A") == [] and run_store.token_ids("run", "B") == ["tok-B"]
    assert run_store.invalidated_token_ids("run", "J") == ["tok-J"]
    props = {r["node_key"]: r["status"] for r in rows(db, "SELECT * FROM graph_propagations")}
    assert props == {"A": "done", "J": "done", "B": "pending"}
    # Approved documents go back to review, kept; B's stays approved; a not-approved one is left.
    assert db.docs == {"doc-A": "pending_review", "doc-J": "pending_review", "doc-C": "pending_review",
                       "doc-B": "approved"}
    assert result["reopened_document_ids"] == ["doc-A", "doc-J"]
    assert db.canceled == {3: ("rev-J", None), 1: (None, "empty_revert")}
    run = run_store.run_by_id("run")
    assert (run["status"], run["idle_reason"]) == ("idle", "no_targets")       # completed released, still idle
    assert run_store.active_attempt_id("run", "A") is None                   # the next command opens a new attempt


def test_old_tokens_of_no_attempt_are_cut_off_with_their_node(db):
    # Tokens mapped before migration 151 (attempt_id NULL): A's goes with its node, B's stays B's.
    columns = "(token_id, run_id, node_key, stage, attempt_id, mapped_at)"           # legacy rows: map_kind NULL
    db.connection.execute(f"INSERT INTO graph_run_token_map {columns} VALUES ('old-A','run','A','result',NULL,0)")
    db.connection.execute(f"INSERT INTO graph_run_token_map {columns} VALUES ('old-B','run','B','result',NULL,0)")
    assert run_store.token_ids("run", "A") == ["old-A", "tok-A"]
    result = rewind.rewind(ctx_of(), PLAN, REVERTS)
    [legacy] = result["detached_legacy_attempt_ids"]
    row = rows(db, f"SELECT * FROM graph_node_attempts WHERE attempt_id = '{legacy}'")[0]
    assert (row["node_key"], row["status"], row["invalidated_reason"], row["active_node_key"]) == (
        "A", "invalidated", "time_machine", None)
    assert legacy not in result["invalidated_attempt_ids"]                 # no worktree of its own to clean
    # No evidence any more (reconciliation, the binder's token reads), an old output of the card (stale lines).
    assert run_store.token_ids("run", "A") == []
    assert run_store.invalidated_token_ids("run", "A") == ["old-A", "tok-A"]
    assert run_store.token_ids("run", "B") == ["old-B", "tok-B"]
    assert ("old-A", "time_machine") in db.revoked and all(t != "old-B" for t, _r in db.revoked)
    mapped = rows(db, "SELECT token_id, attempt_id FROM graph_run_token_map WHERE token_id LIKE 'old-%'")
    assert {r["token_id"]: r["attempt_id"] for r in mapped} == {"old-A": legacy, "old-B": None}   # nothing deleted


def test_db_step_takes_back_a_flow_its_legacy_completed_run_gave_up(db):
    db.connection.execute("UPDATE graph_runs SET active_group_id = NULL")
    rewind.rewind(ctx_of(), PLAN, REVERTS)
    assert run_store.active_run("flow")["run_id"] == "run"


@pytest.mark.parametrize("change,code", [
    ("UPDATE graph_runs SET status = 'running'", "run_not_idle"),
    ("INSERT INTO graph_attempt_runs VALUES ('x','b','run','requested')", "executions_in_progress"),
    ("UPDATE graph_node_attempts SET integration_status = 'integrating' WHERE attempt_id = 'c'",
     "integrations_in_progress"),
    ("INSERT INTO graph_node_attempts VALUES ('j2','run','J',1,'ended',NULL,'Z',NULL,NULL,'r2','integrated','m2','i2')",
     "unplanned_integration"),
])
def test_db_step_refuses_before_writing(db, change, code):
    db.connection.execute(change)
    before = rows(db, "SELECT * FROM graph_node_attempts ORDER BY attempt_id")
    with pytest.raises(rewind.RewindRefused) as raised:
        rewind.rewind(ctx_of(), PLAN, REVERTS)
    assert raised.value.code == code
    assert rows(db, "SELECT * FROM graph_node_attempts ORDER BY attempt_id") == before and not db.revoked


def test_db_step_refuses_a_missing_revert(db):
    with pytest.raises(rewind.RewindRefused) as raised:
        rewind.rewind(ctx_of(), PLAN, REVERTS[:1])
    assert raised.value.code == "revert_missing"


# ── the operation: journal order, outcomes and recovery ──────────────────────

ASSESSMENT = {"flow_id": "flow", "run_id": "run", "node_key": "A", "allowed": True, "reasons": [], "impact": {
    "node_keys": ["A", "C", "D", "J"], "revision_nos": [1], "ledger_rows": [],
    "attempts": [{"attempt_id": "a", "node_key": "A", "result_commit": "result-A", "input_commit": "in-A"},
                 {"attempt_id": "j", "node_key": "J", "result_commit": "in-J", "input_commit": "in-J"},
                 {"attempt_id": "c", "node_key": "C", "result_commit": None, "input_commit": None}],
    "documents": [{"doc_id": "doc-A", "attempt_id": "a", "node_key": "A", "stage": "result"}],
    "commits": [{"kind": "result", "commit_sha": "result-A", "attempt_id": "a", "node_key": "A", "ledger_row_id": 1},
                {"kind": "integration", "commit_sha": "merge-A", "attempt_id": "a", "node_key": "A"},
                {"kind": "integration", "commit_sha": "in-J", "attempt_id": "j", "node_key": "J"}]}}


@pytest.fixture
def op(monkeypatch):
    entries = []
    monkeypatch.setattr(operation, "with_flow_guard", lambda flow, body: body())
    monkeypatch.setattr(operation.fencing, "exclusive", lambda flow, **kw: nullcontext())
    monkeypatch.setattr(operation, "require_writable", lambda flow: None)
    monkeypatch.setattr(operation, "read_ownership", lambda flow: SimpleNamespace(state="graph"))
    monkeypatch.setattr(operation.service, "require_safe", lambda flow, key, locale: json.loads(json.dumps(ASSESSMENT)))
    monkeypatch.setattr(operation.run_store, "run_by_id", lambda run_id: {"run_id": run_id, "group_id": "flow"})
    monkeypatch.setattr(operation.run_store, "_all", lambda sql, params: [])
    monkeypatch.setattr(operation.run_apply, "load_context", lambda run, project, actor, *a: SimpleNamespace(run=run))
    monkeypatch.setattr(operation, "_project", lambda flow: "p")
    monkeypatch.setattr(operation.journal, "emit",
                        lambda ctx, op_id, status, **extra: entries.append({"operation_id": op_id,
                                                                            "operation_status": status, **extra}))
    git = Mock(return_value={"status": "ok", "pre_head": "h0", "reverts": [
        {"attempt_id": "a", "node_key": "A", "commit_sha": "merge-A", "revert_commit": "rev-A", "outcome": "reverted"}]})
    monkeypatch.setattr(operation.git_revert, "apply", git)
    db_step = Mock(return_value={"invalidated_attempt_ids": ["a", "j", "c"], "active_attempt_ids": ["c"],
                                 "reset_node_keys": ["A", "J"], "reopened_document_ids": ["doc-A"],
                                 "canceled_ledger_row_ids": [1], "revoked_token_count": 3, "idle_reason": "no_targets"})
    monkeypatch.setattr(operation.rewind, "rewind", db_step)
    cleanup = Mock(return_value=True)
    monkeypatch.setattr(operation.workspace, "cleanup", cleanup)
    from modules.flow_gate.graph_workflow.services import run_service
    monkeypatch.setattr(run_service, "run_payload", lambda run_id, with_nodes: {"run_id": run_id})
    return SimpleNamespace(entries=entries, git=git, db_step=db_step, cleanup=cleanup)


def statuses(op):
    return [e["operation_status"] for e in op.entries]


def test_operation_records_each_phase_and_answers_scope_and_result(op):
    out = operation.execute({"group_id": "flow", "project_id": "p"}, "run", "A", "u", "en")
    assert statuses(op) == ["started", "git_applied", "completed"]
    plan = op.entries[0]["plan"]
    # Only A's own merge is a Git target: J had no source change (no merge), C is document-only, result commits never.
    assert plan["commits"] == [{"attempt_id": "a", "node_key": "A", "commit_sha": "merge-A", "result_commit": "result-A"}]
    assert op.git.call_args.args[2] == plan["commits"]
    assert op.git.call_args.kwargs == {"resume": False, "recorded": []}
    assert op.db_step.call_args.args[1:] == (plan, op.git.return_value["reverts"])
    tm = out["time_machine"]
    assert tm["operation_status"] == "completed" and tm["impact"]["node_keys"] == ["A", "C", "D", "J"]
    assert tm["reverts"][0]["revert_commit"] == "rev-A" and out["run"] == {"run_id": "run"}
    assert [c.args[0] for c in op.cleanup.call_args_list] == ["a", "j", "c"]
    assert len({e["operation_id"] for e in op.entries}) == 1


def test_git_refusal_ends_blocked_with_the_reason_and_no_db_step(op):
    op.git.return_value = {"status": "blocked", "reason": "revert_conflict", "commit_sha": "merge-A",
                           "compensated": True, "reverts": []}
    with pytest.raises(GraphApiError) as raised:
        operation.execute({"group_id": "flow"}, "run", "A", "u", "ja")
    error = raised.value
    assert (error.status, error.code) == (409, "graph_time_machine_blocked")
    assert error.params["reasons"][0]["code"] == "git_revert_blocked" and error.params["reasons"][0]["compensated"]
    assert error.params["reasons"][0]["message"] == graph_messages.TIME_MACHINE_REASONS["git_revert_blocked"][2]
    assert error.params["operation_status"] == "blocked" and error.params["impact"] == ASSESSMENT["impact"]
    assert statuses(op) == ["started", "blocked"] and not op.db_step.called


@pytest.mark.parametrize("failure", [rewind.RewindRefused("run_not_idle", status="running"), RuntimeError("db down")])
def test_db_failure_after_git_is_a_partial_failure(op, failure):
    op.db_step.side_effect = failure
    with pytest.raises(GraphApiError) as raised:
        operation.execute({"group_id": "flow"}, "run", "A", "u", "ko")
    assert (raised.value.status, raised.value.code) == (409, "graph_time_machine_partial_failed")
    assert raised.value.params["phase"] == "db"
    assert raised.value.params["error"]["code"] == ("run_not_idle" if isinstance(failure, rewind.RewindRefused)
                                                    else "db_error")
    assert statuses(op) == ["started", "git_applied", "partial_failed"]
    assert op.entries[-1]["reverts"] == op.git.return_value["reverts"] and not op.cleanup.called


def test_unknown_git_outcome_is_a_partial_failure(op):
    op.git.return_value = {"status": "failed", "reason": "compensation_failed", "reverts": []}
    with pytest.raises(GraphApiError) as raised:
        operation.execute({"group_id": "flow"}, "run", "A", "u", "ko")
    assert raised.value.code == "graph_time_machine_partial_failed" and raised.value.params["phase"] == "git"
    assert statuses(op) == ["started", "partial_failed"] and not op.db_step.called


def test_shared_merge_and_wrong_run_block_before_anything_is_recorded(op, monkeypatch):
    monkeypatch.setattr(operation.run_store, "_all", lambda sql, params: [
        {"attempt_id": "x", "node_key": "B", "integrated_commit": "merge-A"}])
    with pytest.raises(GraphApiError) as raised:
        operation.execute({"group_id": "flow"}, "run", "A", "u", "ko")
    assert raised.value.params["reasons"][0]["code"] == "integration_commit_shared"
    with pytest.raises(GraphApiError) as raised:
        operation.execute({"group_id": "flow"}, "other-run", "A", "u", "ko")
    assert raised.value.code == "graph_time_machine_blocked" and raised.value.params["reasons"][0]["code"] == "run_missing"
    assert op.entries == [] and not op.git.called


def test_server_start_resumes_an_unfinished_operation_from_its_journal(op, monkeypatch):
    plan = {"node_key": "A", "node_keys": ["A"], "attempt_ids": ["a"], "documents": [],
            "commits": [{"attempt_id": "a", "node_key": "A", "commit_sha": "merge-A", "result_commit": "result-A"}]}
    monkeypatch.setattr(operation.journal, "unfinished", lambda: [
        ("flow", {"operation_id": "gtm_9", "run_id": "run", "status": "partial_failed"})])
    monkeypatch.setattr(operation.journal, "entries", lambda run_id, op_id: [
        {"operation_id": op_id, "operation_status": "started", "plan": plan},
        {"operation_id": op_id, "operation_status": "git_applied"},
        {"operation_id": op_id, "operation_status": "partial_failed", "phase": "db"}])
    operation.recover()
    assert op.git.call_args.args == ("flow", "gtm_9", plan["commits"])
    assert op.git.call_args.kwargs == {"resume": True, "recorded": []}
    assert statuses(op) == ["git_applied", "completed"] and {e["operation_id"] for e in op.entries} == {"gtm_9"}
    op.entries.clear()
    op.git.return_value = {"status": "deferred", "reason": "git_busy", "reverts": []}
    operation.recover()                                        # the session cannot be opened: left for the next start
    assert op.entries == []


def test_server_start_checks_the_recorded_reverts_and_a_failed_git_step_stays_unfinished(op, monkeypatch):
    plan = {"node_key": "A", "node_keys": ["A"], "attempt_ids": ["a"], "documents": [],
            "commits": [{"attempt_id": "a", "node_key": "A", "commit_sha": "merge-A", "result_commit": "result-A"}]}
    applied = [{"attempt_id": "a", "node_key": "A", "commit_sha": "merge-A", "revert_commit": "rev-A",
                "outcome": "reverted"}]
    monkeypatch.setattr(operation.journal, "unfinished", lambda: [
        ("flow", {"operation_id": "gtm_8", "run_id": "run", "status": "partial_failed"})])
    monkeypatch.setattr(operation.journal, "entries", lambda run_id, op_id: [
        {"operation_id": op_id, "operation_status": "started", "plan": plan},
        {"operation_id": op_id, "operation_status": "git_applied", "reverts": applied},
        {"operation_id": op_id, "operation_status": "partial_failed", "phase": "db"}])
    op.git.return_value = {"status": "failed", "reason": "git_read_failed", "reverts": []}
    operation.recover()
    assert op.git.call_args.kwargs == {"resume": True, "recorded": applied}
    # Never "blocked" (which would free the Flow) on an unproven Git state: partially failed again.
    assert statuses(op) == ["partial_failed"] and not op.db_step.called


def test_journal_entries_and_unfinished_read_the_run_event_log(monkeypatch):
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    connection.executescript("""
        CREATE TABLE graph_runs(run_id TEXT, group_id TEXT);
        CREATE TABLE graph_run_events(run_id TEXT, event_seq INTEGER, event_type TEXT, cause TEXT,
            payload_json TEXT, created_at TEXT);
        INSERT INTO graph_runs VALUES ('run','flow'), ('run2','flow2');
    """)
    for seq, (run, op_id, status) in enumerate([("run", "x", "started"), ("run", "y", "started"),
                                                 ("run", "y", "completed"), ("run2", "z", "blocked")], 1):
        connection.execute("INSERT INTO graph_run_events VALUES (?,?,?,?,?,?)",
                           (run, seq, "graph_run_status_changed", "graph_time_machine",
                            json.dumps({"operation_id": op_id, "operation_status": status}), str(seq)))
    monkeypatch.setattr(run_store, "available", lambda: True)
    monkeypatch.setattr(run_store, "_all", lambda sql, p: [dict(r) for r in connection.execute(sql, p).fetchall()])
    assert [e["operation_status"] for e in journal.entries("run", "y")] == ["started", "completed"]
    assert journal.unfinished() == [("flow", {"operation_id": "x", "run_id": "run", "status": "started"})]


# ── what an unfinished operation refuses ─────────────────────────────────────

UNFINISHED = [{"operation_id": "gtm_1", "run_id": "run", "status": "partial_failed"}]


def test_commands_edits_and_integrations_are_refused_while_unfinished(monkeypatch):
    from flowgate.graph_run import editing, execution, result_merge
    monkeypatch.setattr(operation, "incomplete_operations", lambda flow: UNFINISHED)
    monkeypatch.setattr(run_store, "get_store", lambda: SimpleNamespace(table_exists=lambda name: True))
    for refuse in (lambda: operation.require_no_operation("flow"),
                   lambda: execution._ACTIONS["time_machine_refusal"]("flow"),
                   lambda: editing.require_editable("flow")):
        with pytest.raises(GraphApiError) as raised:
            refuse()
        assert (raised.value.status, raised.value.code) == (409, "graph_time_machine_in_progress")
        assert raised.value.params["operations"] == UNFINISHED
    row = {"attempt_id": "b", "status": "active", "integration_status": "pending", "group_id": "flow", "run_id": "run"}
    run_status = {"status": "idle"}
    monkeypatch.setattr(result_merge.workspace, "attempt", lambda attempt_id: dict(row))
    monkeypatch.setattr(result_merge.run_store, "run_by_id", lambda run_id: {"run_id": run_id, "group_id": "flow",
                                                                             **run_status})
    monkeypatch.setattr(result_merge, "with_flow_guard", lambda flow, body: body())
    woken = Mock()
    monkeypatch.setattr(result_merge.run_store, "set_run_status", woken)
    for status in ("idle", "running", "paused"):                # whatever the run's status (rework)
        run_status["status"] = status
        assert result_merge._runnable("b") is False
    run_status["status"] = "idle"
    result_merge._wake("b")
    assert not woken.called                                   # an idle run is not woken for an integration
    monkeypatch.setattr(operation, "incomplete_operations", lambda flow: [])
    assert result_merge._runnable("b") is True
    operation.require_no_operation("flow")
    monkeypatch.setattr(operation, "incomplete_operations", lambda flow: UNFINISHED)
    monkeypatch.setattr(run_store, "get_store", lambda: SimpleNamespace(table_exists=lambda name: False))
    assert not operation.busy("flow")                         # before migration 144 there is no operation


def test_server_start_finishes_time_machine_operations_before_any_execution_replay():
    from pathlib import Path
    text_of = (Path(__file__).resolve().parents[3] / "startup.py").read_text(encoding="utf-8")
    source = text_of[text_of.index("def run_all("):]
    assert source.index("recover_graph_time_machine()") < source.index("recover_graph_executions()")


def test_run_commands_ask_the_time_machine_hook(monkeypatch):
    from modules.flow_gate.graph_workflow.services import run_service
    from modules.flow_gate.services import graph_execution_hooks
    asked = Mock()
    monkeypatch.setattr(graph_execution_hooks, "call", asked)
    run_service._refuse_time_machine("flow")
    asked.assert_called_once_with("time_machine_refusal", "flow")


@pytest.mark.parametrize("locale", ["ko", "en", "ja"])
def test_new_codes_are_localized(locale):
    for code in ("graph_time_machine_in_progress", "graph_time_machine_partial_failed"):
        assert graph_messages.RUN_EDIT_MESSAGES[code][locale]
    rendered = graph_messages.time_machine_reasons(
        [{"code": "git_revert_blocked"}, {"code": "integration_commit_shared"}], locale)
    assert all(r["message"] for r in rendered)


def test_route_takes_no_body_fields():
    from flowgate.graph_time_machine import routes
    assert routes.parse_body(None) == [] and routes.parse_body({}) == []
    assert routes.parse_body({"x": 1}) == [{"field": "x", "reason": "unknown_field"}]
    assert routes.parse_body([]) == [{"field": "body", "reason": "not_an_object"}]
    assert any(getattr(r, "path", "") == "/api/v1/runs/{run_id}/nodes/{node_key}/time-machine"
               for r in routes.router.routes)
