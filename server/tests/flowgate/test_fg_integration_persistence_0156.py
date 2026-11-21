"""the code integration's persistence: an attempt left ``integrating`` / ``pending`` / a
hold left by a failure the drain could not record, reproduced condition by condition, and the runtime recovery that
reuses the existing paths (the drain, the Stop, the lock release / session close notices).

The harness (W1) runs the real stack of the card settings suites (``test_fg_card_settings``: every SQLite migration,
the Graph / run / card routes, ``graph_run.register()`` and so ``result_merge.install()``) with:

- a real Git repository: one common repository whose group worktree is a *linked* worktree per Flow (its own group
  branch) and whose attempt result is a real commit on the attempt's own branch in another linked worktree;
- the real Flow guard (``with_flow_guard``), the real lock manager (G/W/M rows in ``resource_lock``, the release
  listeners), the real ``result_merge`` / ``stop`` / ``conflict.abort_tr_conflict`` code;
- path resolution only is redirected to that repository (``result_merge._group_root``,
  ``git_service._session_context``), and the ended attempt's worktree cleanup (``workspace.cleanup_ended``) records
  instead of removing (it never raises by contract; D2-e makes it raise on purpose).

The AI execution is the only stand-in: a person's card (``LEE``) registers its result with the node's token and the
card's automatic completion approves it (``legacy_adapter.approve``), as the 0083 code-merge cases do.

Injection (W1): ``result_merge.with_flow_guard`` is wrapped so that one chosen guarded body (its ``__qualname__`` and,
for ``_set``, the status / reason it records) raises ``FlowLockTimeout(flow_id)`` — the real failure shape
(``flow_guard.with_flow_guard``) — once (transient) or until released (persistent). S-D2-8 makes the same failure
with a real contention (another thread holds the Flow lock, ``FLOW_LOCK_WAIT_SEC`` shortened).

Every reproduction case writes its state transition table (run / attempt / node / hold / working / merge commits /
MERGE_HEAD / sessions / run events / log lines) as JSON under pytest's basetemp (``fg0156_records``), so the same
cases record the state before the change and assert the state after it.
"""
from __future__ import annotations

import json
import logging
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from test_fg_card_settings import (  # noqa: F401 — fixtures used by name
    legacy_cards, passive_person_results, pre_feature_attempts,
    FA, _doc, _h, _sql, db_path, decided, edge, env, node, put_draft,
)
from test_fg_card_settings_review import _admin_and_reviewer
from test_fg_card_approval_0056 import LEE, card_outputs_port  # noqa: F401 — card_outputs_port: autouse

pytestmark = pytest.mark.usefixtures("legacy_cards", "passive_person_results", "pre_feature_attempts")


@pytest.fixture(autouse=True)
def conflict_kept_as_session_0042(monkeypatch):
    """these scenarios cover a conflict kept as the group's session. Since 0042 a merge keeps it
    only for a person's resolution request (``resolve_requested``; otherwise it is deferred with no session), so the
    scenarios' merges run as that request's merge. A clean merge is the same either way."""
    from flowgate.graph_run import result_merge
    real = result_merge._merge
    monkeypatch.setattr(result_merge, "_merge", lambda row, keep_session=False: real(row, True))

PROJECT = "flowgate"
INTEGRATING, PENDING, CONFLICT, FAILED, INTEGRATED = "integrating", "pending", "conflict", "failed", "integrated"
CAUSES = ("attempt_integration", "integration_hold", "integration_hold_end", "integration_hold_failed",
          "stop_pending", "user_stop", "work_resumed", "run_idle", "run_completed")
LOG_KEYS = ("Graph attempt integration failed", "Graph integration hold could not",
            "Graph retry after Git lock release failed", "Graph stop waits for", "Graph integration reclaim failed",
            "Graph integration after session")


# ── Git ──────────────────────────────────────────────────────────────────────

def _git(root, *args, check=True):
    proc = subprocess.run(["git", *args], cwd=str(root), capture_output=True, text=True, encoding="utf-8")
    if check and proc.returncode != 0:
        raise AssertionError(f"git {' '.join(args)} failed: {proc.stderr}")
    return proc.stdout.strip()


class Repo:
    """One common repository (``main``); each Flow's group worktree is a linked worktree on its own group branch, each
    attempt's tree a linked worktree on the attempt's own branch (``workspace.location``)."""

    def __init__(self, tmp: Path):
        self.tmp = tmp
        tmp.mkdir(parents=True, exist_ok=True)
        self.common = tmp / "common"
        self.common.mkdir()
        _git(self.common, "init", "-b", "main")
        _git(self.common, "config", "user.name", "Persistence Test")
        _git(self.common, "config", "user.email", "persistence@example.invalid")
        _git(self.common, "config", "core.autocrlf", "false")
        for name in ("a.txt", "b.txt"):
            (self.common / name).write_text("base\n", encoding="utf-8")
        _git(self.common, "add", ".")
        _git(self.common, "commit", "-m", "chore: base")
        self.base = _git(self.common, "rev-parse", "HEAD")
        self.flows: dict[str, SimpleNamespace] = {}
        self.cleaned: list[str] = []
        self.attempts = 0

    def group(self, flow: str) -> SimpleNamespace:
        n = len(self.flows) + 1
        wt, branch = self.tmp / f"group{n}", f"flow{n}"
        _git(self.common, "worktree", "add", "-b", branch, str(wt), self.base)
        self.flows[flow] = SimpleNamespace(wt=wt, branch=branch)
        return self.flows[flow]

    def wt(self, flow: str) -> Path:
        return self.flows[flow].wt

    def merges(self, flow: str) -> int:
        return int(_git(self.wt(flow), "rev-list", "--merges", "--count", "HEAD"))

    def merge_head(self, flow: str) -> bool:
        return subprocess.run(["git", "rev-parse", "-q", "--verify", "MERGE_HEAD"], cwd=str(self.wt(flow)),
                              capture_output=True).returncode == 0

    def holds(self, flow: str, commit: str) -> bool:
        return subprocess.run(["git", "merge-base", "--is-ancestor", commit, "HEAD"], cwd=str(self.wt(flow)),
                              capture_output=True).returncode == 0

    def dirty(self, flow: str) -> bool:
        return bool(_git(self.wt(flow), "status", "--porcelain", "--untracked-files=no"))

    def commit_on_group(self, flow: str, path: str, text: str) -> str:
        (self.wt(flow) / path).write_text(text, encoding="utf-8")
        _git(self.wt(flow), "add", path)
        _git(self.wt(flow), "commit", "-m", f"chore: group side {path}")
        return _git(self.wt(flow), "rev-parse", "HEAD")

    def make_dirty(self, flow: str) -> None:
        (self.wt(flow) / "b.txt").write_text("dirty by a person\n", encoding="utf-8")

    def make_clean(self, flow: str) -> None:
        _git(self.wt(flow), "checkout", "--", "b.txt")

    def attempt_tree(self, branch: str, *, conflict: bool) -> tuple[Path, str]:
        self.attempts += 1
        linked = self.tmp / f"attempt{self.attempts}"
        _git(self.common, "worktree", "add", "-b", branch, str(linked), self.base)
        if conflict:
            (linked / "a.txt").write_text("attempt side\n", encoding="utf-8")
        else:
            (linked / f"result{self.attempts}.txt").write_text("the card's result\n", encoding="utf-8")
        _git(linked, "add", ".")
        _git(linked, "commit", "-m", "feat: the card's result")
        return linked, _git(linked, "rev-parse", "HEAD")

    def attempt_tree_uncommitted(self, branch: str) -> Path:
        """An attempt's tree whose result is not committed yet (its result commit is the earlier implementation's ``commit_result``, in
        the integration): a tracked change and a new file, on the attempt's own branch."""
        self.attempts += 1
        linked = self.tmp / f"attempt{self.attempts}"
        _git(self.common, "worktree", "add", "-b", branch, str(linked), self.base)
        (linked / "b.txt").write_text("the card's change\n", encoding="utf-8")
        (linked / "result_l2.txt").write_text("the card's result\n", encoding="utf-8")
        return linked

    def git_path(self, root: Path, name: str) -> Path:
        out = Path(_git(root, "rev-parse", "--git-path", name))
        return out if out.is_absolute() else (root / out)


@pytest.fixture
def repo(tmp_path, monkeypatch):
    if not shutil.which("git"):
        pytest.skip("Git is not installed")
    from flowgate.graph_run import result_merge, workspace
    from modules.flow_gate.db import git_integration as db_git
    from modules.flow_gate.services import git_service
    from modules.flow_gate.services.git.credentials import GitServiceError

    r = Repo(tmp_path / "git")

    def group_root(row):
        f = r.flows.get(row["group_id"])
        return (PROJECT, f.wt, f.branch) if f else (PROJECT, None, None)

    real_context = git_service._session_context

    def session_context(group_id, merge_id):
        f = r.flows.get(group_id)
        if f is None:
            return real_context(group_id, merge_id)
        session = db_git.get_session(int(merge_id))
        if session is None or session.get("group_id") != group_id:
            raise GitServiceError(404, "not_found", f"merge session {merge_id} not found")
        return session, {}, PROJECT, f.wt

    monkeypatch.setattr(result_merge, "_group_root", group_root)
    monkeypatch.setattr(git_service, "_session_context", session_context)
    monkeypatch.setattr(workspace, "cleanup_ended", lambda attempt_id: r.cleaned.append(attempt_id) or True)
    return r


@pytest.fixture(autouse=True)
def no_document_gates(monkeypatch):
    """The automatic completion's read-only document gates pass: the person's results here have no file gate."""
    from flowgate.card_settings import legacy_adapter

    monkeypatch.setattr(legacy_adapter, "completion_gates", lambda doc: None)


@pytest.fixture(autouse=True)
def fresh_drain_state():
    """The drain's process-wide registries start empty (a case that failed mid-drain must not leak into the next)."""
    from flowgate.graph_run import result_merge
    yield
    with result_merge._drain_lock:
        result_merge._draining.clear()
        result_merge._again.clear()
        result_merge._skips.clear()
    with result_merge._opened_lock:
        result_merge._opened.clear()
        getattr(result_merge, "_left_open", {}).clear()
    for name in ("closed", "released", "dispatching", "retry_depth", "quiet"):
        result_merge._local.__dict__.pop(name, None)


class LogTap(logging.Handler):
    def __init__(self):
        super().__init__(logging.INFO)
        self.seen: list[str] = []

    def emit(self, record):
        try:
            msg = record.getMessage()
        except Exception:  # noqa: BLE001
            return
        for key in LOG_KEYS:
            if msg.startswith(key):
                self.seen.append(key)


@pytest.fixture
def tap():
    handler = LogTap()
    logger = logging.getLogger("flowgate.graph_run")
    logger.addHandler(handler)
    old = logger.level
    logger.setLevel(logging.INFO)
    yield handler
    logger.removeHandler(handler)
    logger.setLevel(old)


# ── the Flow, its run and its card ───────────────────────────────────────────

def _group(flow: str) -> None:
    from modules.flow_gate.db import groups as db_groups
    if db_groups.get_by_id(flow) is None:
        db_groups.create({"group_id": flow, "project_id": PROJECT, "module": "v02", "title": flow, "status": "OPEN"})
        _doc(flow, 1, "R", "approved")


def chain(env, flow, cards):
    nodes = [node("tmp_r", "R")]
    for i, (kind, assignee) in enumerate(cards):
        nodes.append(node(f"tmp_{i}", kind, assignee=assignee))
    refs = ["tmp_r"] + [f"tmp_{i}" for i in range(len(cards))]
    body = put_draft(env, flow, 0, nodes, [edge(f"tmp_e{i}", a, b) for i, (a, b) in enumerate(zip(refs, refs[1:]))])
    assert env["client"].post(f"/api/v1/flows/{flow}/graph/revisions", json={"draft_version": 1},
                              headers=_h()).status_code == 201
    return [body["key_map"][f"tmp_{i}"] for i in range(len(cards))]


def run_of(env, flow) -> dict:
    return _sql(env["db"], "SELECT run_id, status, idle_reason, last_event_seq FROM graph_runs WHERE group_id = ?",
                (flow,))[0]


def node_of(env, key) -> tuple:
    row = _sql(env["db"], "SELECT status, result_doc_id FROM graph_node_runs WHERE node_key = ?", (key,))[0]
    return row["status"], row["result_doc_id"]


def attempt_of(env, attempt_id) -> dict:
    return _sql(env["db"], "SELECT status, integration_status, integration_reason, integrated_commit "
                           "FROM graph_node_attempts WHERE attempt_id = ?", (attempt_id,))[0]


def result_doc(flow, seq, typ="T"):
    from modules.flow_gate.db import documents as db_documents
    from modules.flow_gate.storage.paths import get_storage_root

    doc_id = _doc(flow, seq, typ)
    path = get_storage_root(PROJECT, create=True) / "docs" / f"{doc_id}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"# {typ} {seq}\n\nbody\n", encoding="utf-8")
    db_documents.update(doc_id, {"file_path": f"docs/{doc_id}.md", "doc_review_status": "pending_review"})
    return doc_id


def registers(env, flow, key, seq=81, typ="T"):
    """A person's card registers its result with the node's token (the inbox's order: the Graph contact, then the
    token's consume — the result's authorship by the attempt, which the earlier implementation's ``commit_result`` checks)."""
    from modules.flow_gate.graph_workflow.api import v01_contact
    from modules.flow_gate.services import token_service

    token = _sql(env["db"], "SELECT token_id FROM graph_run_token_map WHERE node_key = ? ORDER BY rowid",
                 (key,))[0]["token_id"]
    doc = result_doc(flow, seq, typ)
    v01_contact.after_inbox_registered(flow, doc, typ, token)
    assert token_service.consume(token, PROJECT, doc)
    assert node_of(env, key) == ("review", doc)
    return doc


def scene(env, repo, flow=FA, *, conflict=False, seq=81, uncommitted=False) -> SimpleNamespace:
    """R -> t1 (a person's card) -> t2 (a person's card) on ``flow``, started (full): the run idle, t1 open. t1's
    attempt is a prepared worktree attempt (``input_commit`` the run base, its mode record) whose result is a real
    commit on its own branch; ``conflict``: that result and a later group branch commit change the same line. t1's
    result is registered: t1 in review, waiting for its approval. ``uncommitted``: t1's result is still an
    uncommitted change of its tree (no ``result_commit``: the integration's the earlier implementation step commits it, W5 L2)."""
    from flowgate.graph_run import result_merge, workspace
    from modules.flow_gate.graph_workflow.store import run_store

    from modules.flow_gate.db import git_integration as db_git

    _admin_and_reviewer(env)
    if flow != FA:
        _group(flow)
    db_git.register_worktree(flow, PROJECT, repo.group(flow).branch)     # the group's Git state (session FK)
    t1, t2 = chain(env, flow, [("T", LEE), ("T", LEE)])
    started = env["client"].post(f"/api/v1/flows/{flow}/runs", json={"revision_no": 1}, headers=_h())
    assert started.status_code == 201, started.text
    run = run_of(env, flow)
    assert (run["status"], run["idle_reason"]) == ("idle", "needs_intervention")
    attempt = run_store.active_attempt_id(run["run_id"], t1)
    _directory, _root, branch = workspace.location(workspace.attempt(attempt), PROJECT)
    if uncommitted:
        linked, result = repo.attempt_tree_uncommitted(branch), None
    else:
        linked, result = repo.attempt_tree(branch, conflict=conflict)
    _sql(env["db"], "UPDATE graph_node_attempts SET input_commit = ?, input_source = 'run_base', "
                    "input_attempt_ids = '[]' WHERE attempt_id = ?", (repo.base, attempt))
    workspace._event(workspace.attempt(attempt), "worktree", input_commit=repo.base, input_source="run_base",
                     input_attempt_ids=[])
    if result is not None:
        _sql(env["db"], "UPDATE graph_node_attempts SET result_commit = ? WHERE attempt_id = ?", (result, attempt))
    assert result_merge.needs_integration(workspace.attempt(attempt))
    if conflict:
        repo.commit_on_group(flow, "a.txt", "group side\n")
    doc = registers(env, flow, t1, seq)
    return SimpleNamespace(env=env, repo=repo, flow=flow, key=t1, next=t2, attempt=attempt, result=result, doc=doc,
                           linked=linked,
                           run_id=run["run_id"], seq=int(run_of(env, flow)["last_event_seq"]))


def approve(sc, *, check=True):
    """t1's automatic completion (0119 T-1) — the approval the run holds for the merge (``defer_done``)."""
    from flowgate.card_settings import legacy_adapter
    done = legacy_adapter.approve(sc.flow, sc.doc, "u_kim", legacy_adapter.AUTOMATION, node_key=sc.key)
    if check:
        assert done is True
    return done


def approve_unwoken(sc, monkeypatch) -> None:
    """(h1) The approval no command / approval wakes (a step's, a paused-then-idle run's, the server start's drain):
    ``approval_wakes`` answers False for this one decision, so the held approval's drain takes the idle run's
    integration hold (``result_merge._wake``)."""
    from flowgate.graph_run import command, execution
    real_action, real_command = execution._ACTIONS["approval_wakes"], command.approval_wakes
    monkeypatch.setitem(execution._ACTIONS, "approval_wakes", lambda ctx: False)
    monkeypatch.setattr(command, "approval_wakes", lambda ctx: False)
    try:
        approve(sc)
    finally:
        monkeypatch.setitem(execution._ACTIONS, "approval_wakes", real_action)
        monkeypatch.setattr(command, "approval_wakes", real_command)


def prefail(sc) -> None:
    """(h2) precondition: t1's first integration fails (a person's uncommitted change in the group worktree,
    ``group_worktree_dirty``) and the run is idle (``merge_conflict``); then the worktree is clean again."""
    sc.repo.make_dirty(sc.flow)
    approve(sc)
    row = attempt_of(sc.env, sc.attempt)
    assert (row["integration_status"], row["integration_reason"]) == (FAILED, "group_worktree_dirty"), row
    run = run_of(sc.env, sc.flow)
    assert (run["status"], run["idle_reason"]) == ("idle", "merge_conflict"), run
    sc.repo.make_clean(sc.flow)


def command(sc, mode="step"):
    """A command on the run: paused -> resume (the same command), idle -> ``mode`` (step by default); running /
    stopping take none (None)."""
    run = run_of(sc.env, sc.flow)
    if run["status"] == "paused":
        body = {}
    elif run["status"] == "idle":
        body = {"mode": mode}
    else:
        return None
    res = sc.env["client"].post(f"/api/v1/runs/{sc.run_id}/resume", json=body, headers=_h())
    return res.status_code


def stop(sc):
    res = sc.env["client"].post(f"/api/v1/runs/{sc.run_id}/stop", json={}, headers=_h())
    return res.status_code


def drive(sc, hold, monkeypatch):
    """The approval / command that asks t1's integration under the hold kind ``hold``."""
    if hold == "h1":
        approve_unwoken(sc, monkeypatch)
    elif hold == "h2":
        assert command(sc, "step") == 200
    else:
        approve(sc)


# ── locks ────────────────────────────────────────────────────────────────────

class HoldG:
    """Another job holds the Flow's group lock G on its own thread (the real lock manager); ``release`` lets it go
    (the release listeners run on that thread, as a real job's would)."""

    def __init__(self, flow, kind="review_action", *, domain="G"):
        from modules.flow_gate.services.git import commit as git_commit, lock_manager
        self.go, self.held, self.done = threading.Event(), threading.Event(), threading.Event()
        self.error = None

        def body():
            try:
                if domain == "G":
                    self.lock = git_commit._acquire_group_lock(PROJECT, flow, kind)
                    assert self.lock is not None, "G busy"
                else:
                    ctx = lock_manager.new_context("req")
                    outcome = lock_manager.acquire(domain, PROJECT, holder_kind=kind, ctx=ctx)
                    assert outcome.ok, outcome
                    self.lock = (ctx, outcome.lock_key)
                self.held.set()
                self.go.wait(120)
                git_commit._release_group_lock(self.lock)
            except BaseException as exc:  # noqa: BLE001
                self.error = exc
                self.held.set()
            finally:
                self.done.set()

        self.thread = threading.Thread(target=body, daemon=True)
        self.thread.start()
        assert self.held.wait(30) and self.error is None, self.error

    def release(self):
        self.go.set()
        assert self.done.wait(120)
        self.thread.join(5)
        assert self.error is None, self.error


def lock_release(flow) -> None:
    """F2: an unrelated job takes the Flow's G and lets it go."""
    HoldG(flow).release()


# ── injection ────────────────────────────────────────────────────────────────

class Inject:
    """``result_merge.with_flow_guard`` raising ``FlowLockTimeout`` at the chosen guarded bodies: ``targets`` is a list
    of ``(qualname, when(locals of the caller) or None)``; once (transient) or until ``release`` (persistent)."""

    def __init__(self, monkeypatch, targets, *, persistent=False, on_fire=None, flow=None):
        from flowgate.graph_run import result_merge
        from modules.flow_gate.graph_workflow.services.errors import FlowLockTimeout
        self.armed = [True] * len(targets)
        self.fired = [0] * len(targets)
        real = result_merge.with_flow_guard

        def guard(flow_id, body):
            name = getattr(body, "__qualname__", "")
            for i, (qualname, when) in enumerate(targets):
                if self.armed[i] and name == qualname and (flow is None or flow == flow_id) and                         (when is None or when(sys._getframe(1).f_locals)):
                    self.fired[i] += 1
                    if not persistent:
                        self.armed[i] = False
                    if on_fire is not None and sum(self.fired) == 1:
                        on_fire()
                    raise FlowLockTimeout(flow_id)
            return real(flow_id, body)

        monkeypatch.setattr(result_merge, "with_flow_guard", guard)

    def release(self):
        self.armed = [False] * len(self.armed)


def _status_is(status, reason=None):
    return lambda loc: loc.get("status") == status and (reason is None or loc.get("reason") == reason)


TARGETS = {
    "a": [("_finish.<locals>.body", None)],                                  # (e) after the merge commit
    "b": [("_set.<locals>.body", _status_is(CONFLICT))],                     # the conflict session just opened
    "c": [("_set.<locals>.body", _status_is(PENDING, "git_busy"))],          # the merge found G busy
    "d": [("_set.<locals>.body", _status_is(FAILED, "conflict_aborted"))],   # _settle_closed of a session closed
}


class CleanupFails:
    """D2-e: ``workspace.cleanup_ended`` raises (it never does by contract) after the integration committed."""

    def __init__(self, monkeypatch, repo, *, persistent=False, on_fire=None):
        from flowgate.graph_run import workspace
        self.armed, self.fired = True, 0

        def cleanup(attempt_id):
            if self.armed:
                self.fired += 1
                if not persistent:
                    self.armed = False
                if on_fire is not None and self.fired == 1:
                    on_fire()
                raise RuntimeError("cleanup_ended_injected")
            repo.cleaned.append(attempt_id)
            return True

        monkeypatch.setattr(workspace, "cleanup_ended", cleanup)

    def release(self):
        self.armed = False


def abort_before_conflict(monkeypatch, sc) -> dict:
    """D2-d: a person aborts the conflict session the integration just opened, before its ``conflict`` is recorded
    (the real abort under G, ``conflict.abort_tr_conflict``)."""
    from flowgate.graph_run import result_merge
    from modules.flow_gate.services.git import conflict
    real = result_merge._set
    seen = {"merge_id": None}

    def set_(attempt_id, status, reason, expect):
        if status == CONFLICT and seen["merge_id"] is None:
            seen["merge_id"] = int(str(reason)[len(result_merge.SESSION_REASON):])
            conflict.abort_tr_conflict(sc.flow, seen["merge_id"])
        return real(attempt_id, status, reason, expect)

    monkeypatch.setattr(result_merge, "_set", set_)
    return seen


def person_aborts(sc) -> int:
    from modules.flow_gate.db import git_integration as db_git
    from modules.flow_gate.services.git import conflict
    session = db_git.get_open_session_by_group(sc.flow)
    assert session is not None
    conflict.abort_tr_conflict(sc.flow, int(session["merge_id"]))
    return int(session["merge_id"])


# ── the record ───────────────────────────────────────────────────────────────

def state(sc, tap=None) -> dict:
    from flowgate.graph_run import result_merge, settle
    from modules.flow_gate.db import git_integration as db_git
    db = sc.env["db"]
    run = run_of(sc.env, sc.flow)
    att = attempt_of(sc.env, sc.attempt)
    nodes = {r["node_key"]: r["status"] for r in _sql(db, "SELECT node_key, status FROM graph_node_runs "
                                                          "WHERE run_id = ?", (run["run_id"],))}
    ctx = SimpleNamespace(run_id=run["run_id"], run={"status": run["status"]}, flow_id=sc.flow)
    events = [(r["cause"], r["reason"]) for r in _sql(
        db, "SELECT cause, reason FROM graph_run_events WHERE run_id = ? AND node_key IS NULL AND event_seq > ? "
            "ORDER BY event_seq", (run["run_id"], sc.seq)) if r["cause"] in CAUSES]
    sessions = [(int(s["merge_id"]), s["status"]) for s in db_git.sessions_by_group(sc.flow)]
    return {
        "run": run["status"], "idle_reason": run["idle_reason"],
        "attempt": att["status"], "integration": att["integration_status"], "reason": att["integration_reason"],
        "integrated_commit": bool(att["integrated_commit"]),
        "node": nodes.get(sc.key), "next": nodes.get(sc.next),
        "hold": result_merge.hold_of(run["run_id"]), "working": bool(settle.working(ctx, [])),
        "merges": sc.repo.merges(sc.flow), "merge_head": sc.repo.merge_head(sc.flow),
        "result_in_branch": bool(sc.result) and sc.repo.holds(sc.flow, sc.result), "sessions": sessions,
        "draining": sc.flow in result_merge._draining,
        "events": events, "logs": list(tap.seen) if tap is not None else [],
    }


class Record:
    def __init__(self, name, sc, tap, base: Path):
        self.name, self.sc, self.tap, self.base = name, sc, tap, base
        self.rows: list[dict] = []
        self._logs = 0
        self._t0 = time.monotonic()

    def step(self, label, **extra) -> dict:
        row = state(self.sc, self.tap)
        row["logs"] = row["logs"][self._logs:]
        self._logs += len(row["logs"])
        row = {"step": label, "t": round(time.monotonic() - self._t0, 2), **row, **extra}
        self.rows.append(row)
        return row

    def at(self, label) -> dict:
        return next(r for r in reversed(self.rows) if r["step"] == label)

    def save(self) -> None:
        out = self.base / "fg0156_records"
        out.mkdir(parents=True, exist_ok=True)
        (out / f"{self.name}.json").write_text(json.dumps(self.rows, ensure_ascii=False, indent=1), encoding="utf-8")


@pytest.fixture
def record(tmp_path_factory, tap):
    made: list[Record] = []

    def make(name, sc):
        rec = Record(name, sc, tap, tmp_path_factory.getbasetemp())
        made.append(rec)
        return rec

    yield make
    for rec in made:                     # saved even when the case fails (the state before the change)
        rec.save()


def open_session(sc):
    from modules.flow_gate.db import git_integration as db_git
    session = db_git.get_open_session_by_group(sc.flow)
    return int(session["merge_id"]) if session else None


# ── W2 / W3: D2 — the matrix (point × hold × transient / persistent × F1..F5) ───

def arm(point, monkeypatch, sc, *, persistent, on_fire):
    if point == "e":
        return CleanupFails(monkeypatch, sc.repo, persistent=persistent, on_fire=on_fire)
    if point == "d":
        abort_before_conflict(monkeypatch, sc)
    return Inject(monkeypatch, TARGETS[point], persistent=persistent, on_fire=on_fire)


def expect_recovered(point, row, sc):
    """The state the first runtime trigger after the failure leaves (after the change)."""
    if point in ("a", "c", "e"):
        assert (row["attempt"], row["integration"], row["node"]) == ("ended", INTEGRATED, "done"), row
        assert row["merges"] == 1 and row["result_in_branch"] and row["integrated_commit"], row
    elif point == "b":
        merge_id = open_session(sc)
        assert merge_id is not None and (row["integration"], row["reason"]) == (CONFLICT, f"merge_session:{merge_id}"), row
        assert row["sessions"] == [(merge_id, "open")] and row["merges"] == 0, row
    elif point == "d":
        assert (row["integration"], row["reason"]) == (FAILED, "conflict_aborted"), row
        assert [s for s in row["sessions"] if s[1] == "open"] == [] and row["merges"] == 0, row




# ── S-D2-5: a person aborts the session an integration left integrating ─────







# ── S-D2-6: a Stop on an integrating attempt ────────────────────────────────



# ── S-D2-7: a merge in progress is never reclaimed ──────────────────────────

def _held_merge(monkeypatch, after_merge=None):
    from modules.flow_gate.services.git import branches
    real = branches.merge_no_ff
    gate, entered = threading.Event(), threading.Event()

    def merge(*args, **kwargs):
        entered.set()
        assert gate.wait(120)
        out = real(*args, **kwargs)
        if after_merge is not None:
            after_merge()
        return out

    monkeypatch.setattr(branches, "merge_no_ff", merge)
    return gate, entered


def _recover_spy(monkeypatch):
    from flowgate.graph_run import result_merge
    calls = []
    real = result_merge._recover_one
    monkeypatch.setattr(result_merge, "_recover_one", lambda row: calls.append(row["attempt_id"]) or real(row))
    return calls






# ── S-D2-8: a real Flow lock contention ─────────────────────────────────────



# ── W4 ⓐ: the gate snapshot reservation ─────────────────────────────────────

def reserve(flow, *, run_id="run_gate_other"):
    from flowgate.gate_decisions import repository as repo
    return repo.insert_decision({
        "group_id": flow, "run_id": run_id, "node_key": "gn_gate_other", "attempt_id": f"ga_gate_{flow}",
        "revision_no": 1, "actor_user_id": "u_kim", "request_id": "req_gate_0156", "request_fingerprint": "0" * 64,
        "decision": repo.CONFIRM, "job_id": "job_gate_0156"})




def _gate_end(sc, decision, path):
    """How a reservation ends without a pin (W4 ⓐ-2), each as its path writes it: the decision moves in the run's
    Flow guard (``legacy.guarded_run``), then the post-commit job ``publish.after_terminal``."""
    from flowgate.gate_decisions import publish, repository as gate_repo
    from modules.flow_gate.graph_workflow.services.flow_guard import with_flow_guard
    from modules.flow_gate.services.git import lock_manager
    did = decision["decision_id"]

    def move(*args, **changes):
        current = gate_repo.get(did)
        assert gate_repo.move(did, current["decision_version"], *args, **changes)

    if path in ("resolve_confirmed", "resolve_abandon"):
        with_flow_guard(sc.flow, lambda: move(gate_repo.RECOVERY_REQUIRED, last_error_code="gate_job_failed"))
    if path == "job_failed":
        with_flow_guard(sc.flow, lambda: move(gate_repo.FAILED, last_error_code="gate_git_unavailable"))
    elif path == "cancel":
        with_flow_guard(sc.flow, lambda: move(gate_repo.CANCELED))
    elif path == "resolve_confirmed":
        with_flow_guard(sc.flow, lambda: move(gate_repo.SUCCEEDED, close_kind=gate_repo.CLOSE_EFFECTS_CONFIRMED,
                                              closed_by="u_kim", close_reason="checked", closed_at="2026-11-08"))
    elif path == "resolve_abandon":
        ctx = lock_manager.new_context("req")            # _keep_recovery_refs: M before the guard
        outcome = lock_manager.acquire("M", PROJECT, holder_kind="publish", ctx=ctx)
        assert outcome.ok
        lock_manager.release(ctx, outcome.lock_key)
        with_flow_guard(sc.flow, lambda: move(gate_repo.ABANDONED, close_kind=gate_repo.CLOSE_ABANDON,
                                              closed_by="u_kim", close_reason="given up", closed_at="2026-11-08",
                                              recovery_reset_required=True))
    publish.after_terminal(sc.flow, decision["run_id"])




# ── W4 ⓑ: the branch merge claim ────────────────────────────────────────────

def _claim(flow, branch):
    from modules.flow_gate.db import git_integration as db_git
    return db_git.create_branch_merge_session(PROJECT, context={
        "merge_target": {"target_group_id": flow, "branch": branch}, "attempt_state": "in_progress"})






# ── W4 ⓒ: notices taken while a lock or a transaction is held ───────────────











# ── W4 ⓓ: the hold whose release failed ─────────────────────────────────────





# ── W5: an outside index.lock ───────────────────────────────────────────────

def _lock_file(repo, sc, where) -> Path:
    if where == "L1":
        return repo.git_path(repo.wt(sc.flow), "index.lock")
    if where == "L3":
        return repo.common / ".git" / "refs" / "heads" / f"{repo.flows[sc.flow].branch}.lock"
    return repo.common / ".git" / "index.lock"           # L4






def _result_commit_of(env, attempt_id):
    return _sql(env["db"], "SELECT result_commit FROM graph_node_attempts WHERE attempt_id = ?",
                (attempt_id,))[0]["result_commit"]




# ── S-REC-1: the server start's recovery is unchanged ───────────────────────



# ── S-SAFE-4: the source rules ──────────────────────────────────────────────

_SERVER = Path(__file__).resolve().parents[2]


def _source(rel):
    return (_SERVER / rel).read_text(encoding="utf-8")
