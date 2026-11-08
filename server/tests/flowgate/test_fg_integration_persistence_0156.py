"""flowgate.v02.0156 T#2 (T0007) — the code integration's persistence: an attempt left ``integrating`` / ``pending`` / a
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
        """An attempt's tree whose result is not committed yet (its result commit is T#8's ``commit_result``, in
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
    token's consume — the result's authorship by the attempt, which T#8's ``commit_result`` checks)."""
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
    uncommitted change of its tree (no ``result_commit``: the integration's T#8 step commits it, W5 L2)."""
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


@pytest.mark.parametrize("mode", ["transient", "persistent"])
@pytest.mark.parametrize("hold", ["h1", "h2", "h3"])
@pytest.mark.parametrize("point", ["a", "b", "c", "d", "e"])
def test_s_d2_matrix(env, decided, repo, tap, record, monkeypatch, point, hold, mode):
    """S-D2-1 (a, transient), S-D2-2 (a, persistent), S-D2-3 (b), S-D2-4 (c), D2-d, S-D2-9 (e): the failure at the
    point, then F1 (the failure's own dispatch; for c: the blocking G's release), [release], F2 (an unrelated G
    release), F3 (a command), F4 (Stop, Stop again, a G release), F5 (the server start's recovery, the control)."""
    sc = scene(env, repo, conflict=point in ("b", "d"))
    rec = record(f"D2-{point}_{hold}_{mode}", sc)
    rec.step("scene")
    if hold == "h2":
        prefail(sc)
        rec.step("prefail")
    blocker = HoldG(sc.flow) if point == "c" else None
    injection = arm(point, monkeypatch, sc, persistent=mode == "persistent", on_fire=lambda: rec.step("inject"))
    drive(sc, hold, monkeypatch)
    rec.step("drive")
    if blocker is not None:
        blocker.release()                                   # c: the release of the G that made it busy
    rec.step("F1")
    if mode == "persistent":
        injection.release()
        rec.step("release")
    lock_release(sc.flow)
    rec.step("F2")
    rec.step("F3", code=command(sc))
    codes = [stop(sc)]
    rec.step("F4-stop", code=codes[-1])
    codes.append(stop(sc))
    lock_release(sc.flow)
    rec.step("F4", codes=codes)
    from flowgate.graph_run import result_merge, stop as stop_module
    result_merge.recover()
    stop_module.recover()
    rec.step("F5")

    assert rec.at("inject")["integration"] in (INTEGRATING, CONFLICT, INTEGRATED), rec.at("inject")
    expect_recovered(point, rec.at("F1" if mode == "transient" else "F2"), sc)
    if point == "d" and hold == "h3":
        settled = rec.at("F1" if mode == "transient" else "F2")
        assert (settled["run"], settled["idle_reason"]) == ("idle", "merge_conflict"), settled
    runtime = rec.at("F4")                                  # no restart: the runtime triggers alone
    assert runtime["integration"] != INTEGRATING and runtime["run"] != "stopping", runtime
    for row in rec.rows:                                    # S-SAFE-1 / S-SAFE-2 / S-SAFE-3
        assert row["merges"] <= 1 and len([s for s in row["sessions"] if s[1] == "open"]) <= 1, row
        assert not row["merge_head"] or any(s[1] == "open" for s in row["sessions"]), row
    for row in rec.rows[:[r["step"] for r in rec.rows].index("F3")]:
        assert len(row["sessions"]) <= 1, row              # no trigger but a command merges a failed one again
    if point in ("a", "c", "e"):
        assert runtime["node"] == "done" and runtime["result_in_branch"], runtime   # done stays done


# ── S-D2-5: a person aborts the session an integration left integrating ─────

def test_s_d2_5_an_abort_of_the_left_session_fails_the_attempt_and_never_merges_again(env, decided, repo, tap,
                                                                                         record, monkeypatch):
    sc = scene(env, repo, conflict=True)
    rec = record("S-D2-5", sc)
    injection = Inject(monkeypatch, TARGETS["b"], persistent=True, on_fire=lambda: rec.step("inject"))
    approve(sc)
    rec.step("F1")
    merge_id = person_aborts(sc)                           # the person's abort (its session closes)
    rec.step("abort", merge_id=merge_id)
    injection.release()
    lock_release(sc.flow)
    rec.step("F2")
    from flowgate.graph_run import result_merge, stop as stop_module
    result_merge.recover()                                 # (a failed one waits for its command, by design)
    stop_module.recover()
    rec.step("F5")
    for label in ("abort", "F2", "F5"):
        row = rec.at(label)
        assert (row["integration"], row["reason"]) == (FAILED, "conflict_aborted"), row
        assert row["sessions"] == [(merge_id, "aborted")] and not row["merge_head"] and row["merges"] == 0, row
    assert (rec.at("F2")["run"], rec.at("F2")["idle_reason"]) == ("idle", "merge_conflict"), rec.at("F2")


def test_s_d2_5_an_abort_in_the_failed_drain_s_window_still_fails_the_attempt(env, decided, repo, tap, record,
                                                                              monkeypatch):
    """S-D2-5, the order of TR0008 rev 0's review: D2-b's conflict record raises; ``integrate``'s finally has dropped
    ``_opened`` while the drain still owns the Flow (its ``except`` not reached yet) when a person aborts the session
    on another thread. That close cannot own the Flow (``_as_drain_owner`` False) and its ``retry`` only marks
    ``_again``, which the failed drain's cleanup drops. The close is still the attempt's: ``failed``
    (``conflict_aborted``), no second session, no merge — and the integration in progress is never reclaimed while
    the drain owns the Flow (``_recover_one`` never called in the window)."""
    from flowgate.graph_run import result_merge, stop as stop_module
    sc = scene(env, repo, conflict=True)
    rec = record("S-D2-5_window", sc)
    Inject(monkeypatch, TARGETS["b"], on_fire=lambda: rec.step("inject"))    # transient: later records work
    calls = _recover_spy(monkeypatch)
    seen: dict = {}
    real = result_merge.integrate

    def integrate(attempt_id):
        try:
            return real(attempt_id)
        except Exception:
            if seen:
                raise
            seen["owned"] = sc.flow in result_merge._draining
            seen["opened"] = attempt_id in result_merge._opened
            rec.step("window")
            box: dict = {}

            def person():
                try:
                    box["merge_id"] = person_aborts(sc)        # its notice: session_closed + the G release's retry
                except BaseException as exc:  # noqa: BLE001
                    box["error"] = exc

            t = threading.Thread(target=person, daemon=True)
            t.start()
            t.join(60)
            seen["alive"] = t.is_alive()
            seen["error"] = repr(box.get("error"))
            seen["merge_id"] = box.get("merge_id")
            seen["again"] = result_merge._again.get(sc.flow)
            seen["calls_in_window"] = list(calls)
            rec.step("abort_in_window", merge_id=box.get("merge_id"))
            raise

    monkeypatch.setattr(result_merge, "integrate", integrate)
    approve(sc)
    rec.step("F1")
    lock_release(sc.flow)
    rec.step("F2")                                       # (no command: a person's step retries a failed one)
    result_merge.recover()
    stop_module.recover()
    rec.step("F5")
    assert seen.get("owned") is True and seen.get("opened") is False, seen          # the window was reached
    assert seen["alive"] is False and seen["error"] == "None" and seen["merge_id"] is not None, seen
    assert seen["calls_in_window"] == [], seen                                      # the owned drain kept
    assert rec.at("window")["integration"] == INTEGRATING, rec.at("window")
    merge_id = seen["merge_id"]
    for label in ("F1", "F2", "F5"):
        row = rec.at(label)
        assert (row["integration"], row["reason"]) == (FAILED, "conflict_aborted"), row
        assert row["sessions"] == [(merge_id, "aborted")], row                      # never opened again
        assert row["merges"] == 0 and not row["merge_head"], row
    assert (rec.at("F2")["run"], rec.at("F2")["idle_reason"]) == ("idle", "merge_conflict"), rec.at("F2")
    assert sc.attempt not in calls, calls                   # judged as the close, never the recovery's re-merge


def test_s_d2_5_an_abort_between_the_reclaim_s_session_read_and_its_recovery_still_fails_the_attempt(
        env, decided, repo, tap, record, monkeypatch):
    """S-D2-5, the order of TR0008 rev 1's review: D2-b leaves the attempt ``integrating`` with its own session open
    (``_left_open``). The failed drain's own dispatch reclaims it as the drain owner: the reclaim reads that session
    open (so ``_recover_one``, not ``_settle_closed``) and, before ``_recover_one`` reads the Flow's open session again,
    a person aborts it on another thread and that close's notice ends - it cannot own the Flow (only ``_again``) and
    leaves ``_left_open``. ``_recover_one`` then finds no session and records ``pending`` (``recovered``). The close is
    still the attempt's: ``failed`` (``conflict_aborted``) before the pass retries anything - no second session, no
    merge - and ``_recover_one`` runs once, by the drain owner (never by the close: no merge in progress taken)."""
    from flowgate.graph_run import result_merge, stop as stop_module
    sc = scene(env, repo, conflict=True)
    rec = record("S-D2-5_reclaim_window", sc)
    Inject(monkeypatch, TARGETS["b"], on_fire=lambda: rec.step("inject"))    # transient: later records work
    calls: list = []
    seen: dict = {}
    real = result_merge._recover_one

    def recover_one(row):
        calls.append(row["attempt_id"])
        if row["attempt_id"] == sc.attempt and not seen:
            with result_merge._opened_lock:
                left = result_merge._left_open.get(sc.attempt)
            seen["left"] = left
            seen["open_before"] = left is not None and result_merge._session_open(left[1])
            seen["owned"] = sc.flow in result_merge._draining
            seen["opened"] = sc.attempt in result_merge._opened
            rec.step("reclaim_window")
            box: dict = {}

            def person():
                try:
                    box["merge_id"] = person_aborts(sc)        # its notice: session_closed + the G release's retry
                except BaseException as exc:  # noqa: BLE001
                    box["error"] = exc

            t = threading.Thread(target=person, daemon=True)
            t.start()
            t.join(60)
            seen["alive"] = t.is_alive()
            seen["error"] = repr(box.get("error"))
            seen["merge_id"] = box.get("merge_id")
            seen["again"] = result_merge._again.get(sc.flow)
            with result_merge._opened_lock:
                seen["left_after_close"] = result_merge._left_open.get(sc.attempt)
            seen["calls_after_close"] = list(calls)
            rec.step("abort_in_reclaim_window", merge_id=box.get("merge_id"))
        return real(row)

    monkeypatch.setattr(result_merge, "_recover_one", recover_one)
    approve(sc)
    rec.step("F1")
    lock_release(sc.flow)
    rec.step("F2")                                       # (no command: a person's step retries a failed one)
    result_merge.recover()
    stop_module.recover()
    rec.step("F5")
    merge_id = seen.get("merge_id")
    assert seen.get("open_before") is True and seen.get("left") == (sc.flow, merge_id), seen   # the window reached
    assert seen["owned"] is True and seen["opened"] is False, seen
    assert seen["alive"] is False and seen["error"] == "None" and merge_id is not None, seen
    assert seen["again"] is not None and seen["left_after_close"] == (sc.flow, merge_id), seen  # the close left it
    assert seen["calls_after_close"] == [sc.attempt], seen          # the close reclaimed nothing (the owner's only)
    assert rec.at("reclaim_window")["integration"] == INTEGRATING, rec.at("reclaim_window")
    assert rec.at("reclaim_window")["sessions"] == [(merge_id, "open")], rec.at("reclaim_window")
    for label in ("F1", "F2", "F5"):
        row = rec.at(label)
        assert (row["integration"], row["reason"]) == (FAILED, "conflict_aborted"), row
        assert row["sessions"] == [(merge_id, "aborted")], row                      # never opened again
        assert row["merges"] == 0 and not row["merge_head"], row
    assert (rec.at("F2")["run"], rec.at("F2")["idle_reason"]) == ("idle", "merge_conflict"), rec.at("F2")
    assert calls == [sc.attempt], calls                     # one recovery, by the drain owner; never again


# ── S-D2-6: a Stop on an integrating attempt ────────────────────────────────

@pytest.mark.parametrize("then", ["stop_again", "lock_release", "server_start"])
def test_s_d2_6_a_stop_reclaims_the_integrating_attempt_done_stays_done(env, decided, repo, tap, record, monkeypatch,
                                                                          then):
    from flowgate.graph_run import result_merge, stop as stop_module
    sc = scene(env, repo)
    rec = record(f"S-D2-6_{then}", sc)
    injection = Inject(monkeypatch, TARGETS["a"], persistent=True, on_fire=lambda: rec.step("inject"))
    approve(sc)
    rec.step("F1")
    rec.step("stop_while_failing", code=stop(sc))
    assert rec.at("stop_while_failing")["run"] == "stopping"          # the merge's end is not recorded yet
    injection.release()
    if then == "stop_again":
        rec.step("stop", code=stop(sc))
    elif then == "lock_release":
        lock_release(sc.flow)
        rec.step("lock_release")
    else:
        stop_module.recover()
        rec.step("server_start")
    row = rec.rows[-1]
    assert (row["run"], row["idle_reason"]) == ("idle", "stopped"), row
    assert (row["attempt"], row["integration"], row["node"]) == ("ended", INTEGRATED, "done"), row
    assert row["merges"] == 1 and row["result_in_branch"], row
    assert stop(sc) == 409                                             # idempotent: nothing left to stop
    lock_release(sc.flow)
    result_merge.recover()
    stop_module.recover()
    again = rec.step("again")
    assert (again["run"], again["integration"], again["node"], again["merges"]) == ("idle", INTEGRATED, "done", 1)


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


@pytest.mark.parametrize("ending", ["merges", "finish_fails"])
@pytest.mark.parametrize("with_stop", [False, True])
def test_s_d2_7_no_trigger_reclaims_an_integration_in_progress(env, decided, repo, tap, record, monkeypatch,
                                                                with_stop, ending):
    from flowgate.graph_run import result_merge, stop as stop_module
    sc = scene(env, repo)
    rec = record(f"S-D2-7_{'stop' if with_stop else 'run'}_{ending}", sc)
    gate, entered = _held_merge(monkeypatch)
    calls = _recover_spy(monkeypatch)
    if ending == "finish_fails":
        Inject(monkeypatch, TARGETS["a"], on_fire=lambda: rec.step("inject"))
    worker = threading.Thread(target=approve, args=(sc,), daemon=True)
    worker.start()
    assert entered.wait(60)                                # thread A: inside _merge, under G, before the merge
    rec.step("held")
    result_merge.request(sc.flow)                          # thread B: every trigger
    result_merge._retry_released(PROJECT, sc.flow, set())
    result_merge.on_lock_released(SimpleNamespace(domain="W", project_id=PROJECT, group_id=None))
    if with_stop:
        rec.step("stop", code=stop(sc))
        assert (rec.at("stop")["run"], rec.at("stop")["integration"]) == ("stopping", INTEGRATING), rec.at("stop")
        stop_module.advance(sc.flow, sc.run_id)
    rec.step("triggered")
    assert calls == [] and rec.at("triggered")["integration"] == INTEGRATING
    gate.set()
    worker.join(120)
    assert not worker.is_alive()
    row = rec.step("end")
    assert (row["integration"], row["node"], row["merges"]) == (INTEGRATED, "done", 1), row
    assert row["run"] != "stopping" and len(row["sessions"]) == 0, row
    if with_stop:
        assert (row["run"], row["idle_reason"]) == ("idle", "stopped"), row
    if ending == "finish_fails":
        assert calls == [sc.attempt], calls                # reclaimed once A left the drain (its dispatch)
    else:
        assert calls == [], calls


def test_s_d2_7_a_session_just_opened_is_never_reclaimed(env, decided, repo, tap, record, monkeypatch):
    from flowgate.graph_run import result_merge
    from modules.flow_gate.services.git import conflict
    sc = scene(env, repo, conflict=True)
    rec = record("S-D2-7_session", sc)
    real = conflict.open_tr_conflict_session
    gate, entered = threading.Event(), threading.Event()

    def opened(*args, **kwargs):
        out = real(*args, **kwargs)
        entered.set()
        assert gate.wait(120)
        return out

    monkeypatch.setattr(conflict, "open_tr_conflict_session", opened)
    calls = _recover_spy(monkeypatch)
    worker = threading.Thread(target=approve, args=(sc,), daemon=True)
    worker.start()
    assert entered.wait(60)
    result_merge.request(sc.flow)
    result_merge._retry_released(PROJECT, sc.flow, set())
    rec.step("triggered")
    assert calls == [] and rec.at("triggered")["integration"] == INTEGRATING
    gate.set()
    worker.join(120)
    row = rec.step("end")
    merge_id = open_session(sc)
    assert (row["integration"], row["reason"]) == (CONFLICT, f"merge_session:{merge_id}") and len(row["sessions"]) == 1


# ── S-D2-8: a real Flow lock contention ─────────────────────────────────────

def test_s_d2_8_a_real_flow_lock_timeout_after_the_merge_is_reclaimed_once_the_lock_is_free(env, decided, repo, tap,
                                                                                            record, monkeypatch):
    from modules.flow_gate.graph_workflow.services import flow_guard
    sc = scene(env, repo)
    rec = record("S-D2-8", sc)
    grab, grabbed, let_go = threading.Event(), threading.Event(), threading.Event()

    def holder():
        assert grab.wait(120)
        lock = flow_guard._flow_lock(sc.flow)
        lock.acquire()
        grabbed.set()
        let_go.wait(120)
        lock.release()

    contender = threading.Thread(target=holder, daemon=True)
    contender.start()
    gate, _entered = _held_merge(monkeypatch, after_merge=lambda: (grab.set(), grabbed.wait(60)))
    gate.set()
    monkeypatch.setattr(flow_guard, "FLOW_LOCK_WAIT_SEC", 1)
    try:                                                   # the merge commits, then (e) times out on the Flow lock
        approved = approve(sc, check=False)
    except Exception as exc:  # noqa: BLE001 — recorded: the approval's own guards may time out too
        approved = type(exc).__name__
    rec.step("F1", approved=str(approved))
    let_go.set()
    contender.join(30)
    monkeypatch.setattr(flow_guard, "FLOW_LOCK_WAIT_SEC", 30)
    lock_release(sc.flow)
    row = rec.step("F2")
    assert rec.at("F1")["merges"] == 1 and "Graph attempt integration failed" in rec.at("F1")["logs"]
    expect_recovered("a", row, sc)


# ── W4 ⓐ: the gate snapshot reservation ─────────────────────────────────────

def reserve(flow, *, run_id="run_gate_other"):
    from flowgate.gate_decisions import repository as repo
    return repo.insert_decision({
        "group_id": flow, "run_id": run_id, "node_key": "gn_gate_other", "attempt_id": f"ga_gate_{flow}",
        "revision_no": 1, "actor_user_id": "u_kim", "request_id": "req_gate_0156", "request_fingerprint": "0" * 64,
        "decision": repo.CONFIRM, "job_id": "job_gate_0156"})


def test_s_d3a_1_the_snapshot_pin_s_lock_release_retries_the_reserved_integration(env, decided, repo, tap, record):
    from flowgate.gate_decisions import repository as gate_repo
    from modules.flow_gate.services.git import lock_manager
    sc = scene(env, repo)
    rec = record("S-D3a-1", sc)
    decision = reserve(sc.flow)
    approve(sc)
    row = rec.step("reserved")
    assert (row["integration"], row["reason"]) == (PENDING, "gate_snapshot_reserved"), row
    pinned = threading.Event()

    def snapshot():                                        # the publish job's _snapshot: G and M, frozen_sha, release
        ctx = lock_manager.new_context("job", "job_gate_0156")
        held = []
        for domain, extra in (("G", {"group_id": sc.flow}), ("M", {})):
            outcome = lock_manager.acquire(domain, PROJECT, holder_kind="publish", ctx=ctx, **extra)
            assert outcome.ok, outcome
            held.append(outcome.lock_key)
        assert gate_repo.move(decision["decision_id"], decision["decision_version"],
                              frozen_sha=_git(repo.wt(sc.flow), "rev-parse", "HEAD"))
        for key in reversed(held):
            lock_manager.release(ctx, key)
        pinned.set()

    t = threading.Thread(target=snapshot, daemon=True)
    t.start()
    assert pinned.wait(60)
    t.join(30)
    row = rec.step("pinned")
    expect_recovered("a", row, sc)


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


@pytest.mark.parametrize("path", ["job_failed", "cancel", "resolve_confirmed", "resolve_abandon"])
def test_s_d3a_2_a_reservation_that_ends_without_a_pin(env, decided, repo, tap, record, path):
    sc = scene(env, repo)
    rec = record(f"S-D3a-2_{path}", sc)
    decision = reserve(sc.flow)
    approve(sc)
    rec.step("reserved")
    _gate_end(sc, decision, path)
    row = rec.step("ended")
    if path == "job_failed":                               # still reserved (failed): a person's retry / resolve
        assert (row["integration"], row["reason"]) == (PENDING, "gate_snapshot_reserved"), row
    else:
        expect_recovered("a", row, sc)


# ── W4 ⓑ: the branch merge claim ────────────────────────────────────────────

def _claim(flow, branch):
    from modules.flow_gate.db import git_integration as db_git
    return db_git.create_branch_merge_session(PROJECT, context={
        "merge_target": {"target_group_id": flow, "branch": branch}, "attempt_state": "in_progress"})


def test_s_d3b_1_close_attempt_retries_the_claimed_group(env, decided, repo, tap, record):
    from modules.flow_gate.services.git import merge_target
    sc = scene(env, repo)
    rec = record("S-D3b-1", sc)
    merge_id = _claim(sc.flow, repo.flows[sc.flow].branch)
    approve(sc)
    row = rec.step("claimed")
    assert (row["integration"], row["reason"]) == (PENDING, "git_busy"), row
    merge_target.close_attempt(merge_id, merge_target.ATTEMPT_COMPLETED)
    expect_recovered("a", rec.step("closed"), sc)


def test_s_d3b_2_a_claim_ended_with_its_session_open_is_retried_by_the_lock_release_that_follows(env, decided, repo,
                                                                                                    tap, record):
    """``complete_reviewed`` / ``_complete_merge_review`` write ``review_state=completed`` and close the attempt in the
    same call under the caller's Git lock; the claim ends with the session still open only in between."""
    from modules.flow_gate.db import git_integration as db_git
    sc = scene(env, repo)
    rec = record("S-D3b-2", sc)
    merge_id = _claim(sc.flow, repo.flows[sc.flow].branch)
    approve(sc)
    row = rec.step("claimed")
    assert (row["integration"], row["reason"]) == (PENDING, "git_busy"), row
    holder = HoldG(sc.flow, "branch_merge")             # the review's caller holds the target's lock
    context = db_git.session_context(db_git.get_session(merge_id))
    db_git.set_session_context(merge_id, {**context, "review_state": "completed"})
    row = rec.step("claim_ended")
    assert (row["integration"], row["reason"]) == (PENDING, "git_busy"), row
    holder.release()
    expect_recovered("a", rec.step("released"), sc)


# ── W4 ⓒ: notices taken while a lock or a transaction is held ───────────────

def test_s_d3c_1_a_g_release_under_m_is_taken_at_the_m_release(env, decided, repo, tap, record, monkeypatch):
    from flowgate.graph_run import result_merge
    from modules.flow_gate.services.git import lock_manager
    sc = scene(env, repo)
    rec = record("S-D3c-1", sc)
    steps = {name: threading.Event() for name in ("held", "g", "m", "go_g", "go_m")}
    taken = []
    real = result_merge._retry_released
    monkeypatch.setattr(result_merge, "_retry_released",
                        lambda p, g, seen: taken.append((p, g)) or real(p, g, seen))

    def job():
        ctx = lock_manager.new_context("req")
        g = lock_manager.acquire("G", PROJECT, group_id=sc.flow, holder_kind="review_action", ctx=ctx)
        m = lock_manager.acquire("M", PROJECT, holder_kind="publish", ctx=ctx)
        assert g.ok and m.ok
        steps["held"].set()
        steps["go_g"].wait(60)
        lock_manager.release(ctx, g.lock_key)
        steps["g"].set()
        steps["go_m"].wait(60)
        lock_manager.release(ctx, m.lock_key)
        steps["m"].set()

    t = threading.Thread(target=job, daemon=True)
    t.start()
    assert steps["held"].wait(30)
    approve(sc)
    rec.step("busy")
    steps["go_g"].set()
    assert steps["g"].wait(30)
    row = rec.step("g_released")
    assert taken == [] and row["integration"] == PENDING, (taken, row)
    steps["go_m"].set()
    assert steps["m"].wait(60)
    t.join(30)
    expect_recovered("a", rec.step("m_released"), sc)
    assert (PROJECT, sc.flow) in taken


def test_s_d3c_2_a_release_inside_a_transaction_waits_for_the_thread_s_next_dispatch(env, decided, repo, tap, record):
    from modules.flow_gate.db.connection import get_store
    from modules.flow_gate.services.git import lock_manager
    sc = scene(env, repo)
    rec = record("S-D3c-2", sc)
    steps = {name: threading.Event() for name in ("held", "go", "released", "go2", "done")}

    def job():
        ctx = lock_manager.new_context("req")
        g = lock_manager.acquire("G", PROJECT, group_id=sc.flow, holder_kind="review_action", ctx=ctx)
        assert g.ok
        steps["held"].set()
        steps["go"].wait(60)
        with get_store().transaction():
            lock_manager.release(ctx, g.lock_key)
        steps["released"].set()
        steps["go2"].wait(60)
        other = lock_manager.acquire("W", PROJECT, target_key="unrelated", holder_kind="review_action", ctx=ctx)
        lock_manager.release(ctx, other.lock_key)        # the thread's next release dispatches what waited
        steps["done"].set()

    t = threading.Thread(target=job, daemon=True)
    t.start()
    assert steps["held"].wait(30)
    approve(sc)
    rec.step("busy")
    steps["go"].set()
    assert steps["released"].wait(30)
    row = rec.step("released_in_transaction")
    assert row["integration"] == PENDING, row
    steps["go2"].set()
    assert steps["done"].wait(60)
    t.join(30)
    expect_recovered("a", rec.step("next_dispatch"), sc)


def test_s_d3c_3_a_session_closed_inside_a_transaction(env, decided, repo, tap, record):
    from flowgate.graph_run import result_merge
    from modules.flow_gate.db import git_integration as db_git
    from modules.flow_gate.db.connection import get_store
    sc = scene(env, repo, conflict=True)
    rec = record("S-D3c-3", sc)
    approve(sc)
    merge_id = open_session(sc)
    assert merge_id is not None and rec.step("conflict")["integration"] == CONFLICT
    _git(repo.wt(sc.flow), "merge", "--abort")
    done = threading.Event()

    def closer():                                          # its own thread: nothing it holds is released after
        with get_store().transaction():
            db_git.close_session(merge_id, "aborted")
        done.set()

    t = threading.Thread(target=closer, daemon=True)
    t.start()
    assert done.wait(30)
    t.join(10)
    row = rec.step("closed_in_transaction")
    assert row["integration"] == CONFLICT and row["sessions"] == [(merge_id, "aborted")], row
    lock_release(sc.flow)
    row = rec.step("lock_release")
    # 0156 T#3 Q3: the first G release settles the lost close; no restart or new merge.
    assert (row["integration"], row["reason"]) == (FAILED, "conflict_aborted"), row
    assert row["merges"] == 0 and not row["merge_head"], row
    assert not [session for session in row["sessions"] if session[1] == "open"], row


def test_s_d3c_4_a_release_retried_by_its_timer_dispatches_on_the_timer_thread(env, decided, repo, tap, record,
                                                                              monkeypatch):
    from modules.flow_gate.services.git import lock_manager
    sc = scene(env, repo)
    rec = record("S-D3c-4", sc)
    blocker = HoldG(sc.flow)
    approve(sc)
    rec.step("busy")
    real = lock_manager.db.delete_lock
    failed = {"n": 0}

    def delete_lock(*args, **kwargs):
        if failed["n"] == 0:
            failed["n"] += 1
            raise RuntimeError("store unavailable")
        return real(*args, **kwargs)

    monkeypatch.setattr(lock_manager.db, "delete_lock", delete_lock)
    blocker.release()
    row = rec.step("store_error")
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline and attempt_of(env, sc.attempt)["integration_status"] != INTEGRATED:
        time.sleep(0.2)
    assert failed["n"] == 1 and row["integration"] == PENDING
    expect_recovered("a", rec.step("timer"), sc)


def test_s_d3c_5_a_stale_lock_reclaimed_by_the_sweeper(env, decided, repo, tap, record):
    from modules.flow_gate.db.connection import get_store
    from modules.flow_gate.services.git import job_sweeper, lock_manager
    sc = scene(env, repo)
    rec = record("S-D3c-5", sc)
    blocker = HoldG(sc.flow, "run_prepare")              # a long hold (heartbeated)
    held = blocker.lock[0].find_held(blocker.lock[1])
    lock_manager._heartbeats.remove(held)                  # its holder stopped beating
    approve(sc)
    rec.step("busy")
    get_store()._execute("UPDATE resource_lock SET heartbeat_until = ? WHERE lock_key = ?",
                         ["2000-01-01T00:00:00+00:00", blocker.lock[1]])
    reclaimed = job_sweeper.step_stale_locks(full=True)
    row = rec.step("reclaimed", reclaimed=reclaimed)
    assert reclaimed == 1
    blocker.release()                                      # lock_lost_before_release: no notice
    rec.step("holder_release")
    expect_recovered("a", row, sc)


# ── W4 ⓓ: the hold whose release failed ─────────────────────────────────────

def test_s_d3d_1_a_failed_drain_and_a_failed_hold_release(env, decided, repo, tap, record, monkeypatch):
    sc = scene(env, repo)
    rec = record("S-D3d-1", sc)
    prefail(sc)
    Inject(monkeypatch, [TARGETS["a"][0], ("_release_hold.<locals>.body", None)],
           on_fire=lambda: rec.step("inject"))
    drive(sc, "h2", monkeypatch)
    rec.step("F1")
    lock_release(sc.flow)
    row = rec.step("F2")
    expect_recovered("a", row, sc)
    assert row["hold"] is None, row


def test_s_d3d_2_a_hold_left_with_no_waiting_integration_is_taken_by_the_next_lock_release(env, decided, repo, tap,
                                                                                         record, monkeypatch):
    from modules.flow_gate.graph_workflow.services import run_service
    sc = scene(env, repo)
    rec = record("S-D3d-2", sc)
    prefail(sc)
    real = run_service.go_on_command
    fails = {"n": 0}

    def go_on(ctx, command_, policies):
        if fails["n"] == 0:
            fails["n"] += 1
            raise RuntimeError("root_binding_lost")
        return real(ctx, command_, policies)

    monkeypatch.setattr(run_service, "go_on_command", go_on)
    Inject(monkeypatch, [("_release_hold.<locals>.body", None)], on_fire=lambda: rec.step("inject"))
    drive(sc, "h2", monkeypatch)
    row = rec.step("F1")                                   # F1: the drain's own G release, at its end
    assert row["integration"] == INTEGRATED and fails["n"] == 1, row
    lock_release(sc.flow)
    row = rec.step("F2")
    assert row["hold"] is None, row
    attempts = _sql(env["db"], "SELECT COUNT(*) AS n FROM graph_node_attempts WHERE run_id = ? AND node_key = ?",
                    (sc.run_id, sc.next))[0]["n"]
    assert attempts <= 1                                   # no second dispatch of the next card
    lock_release(sc.flow)
    assert rec.step("F2_again")["hold"] is None


# ── W5: an outside index.lock ───────────────────────────────────────────────

def _lock_file(repo, sc, where) -> Path:
    if where == "L1":
        return repo.git_path(repo.wt(sc.flow), "index.lock")
    if where == "L3":
        return repo.common / ".git" / "refs" / "heads" / f"{repo.flows[sc.flow].branch}.lock"
    return repo.common / ".git" / "index.lock"           # L4


@pytest.mark.parametrize("hold", ["h1", "h2"])
@pytest.mark.parametrize("where", ["L1", "L3", "L4"])
def test_s_il_an_outside_git_lock_file(env, decided, repo, tap, record, monkeypatch, where, hold):
    sc = scene(env, repo)
    rec = record(f"S-IL_{where}_{hold}", sc)
    if hold == "h2":
        prefail(sc)
    path = _lock_file(repo, sc, where)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("", encoding="utf-8")
    drive(sc, hold, monkeypatch)
    row = rec.step("locked", lock=str(path), dirty=repo.dirty(sc.flow))
    lock_release(sc.flow)
    rec.step("lock_release")
    path.unlink()
    rec.step("command", code=command(sc), dirty=repo.dirty(sc.flow))
    final = rec.step("final", dirty=repo.dirty(sc.flow))
    if where == "L4":                                      # the common index: a linked group worktree is not touched
        assert row["integration"] == INTEGRATED, row
    else:
        assert row["integration"] in (FAILED, INTEGRATED), row          # never pending with no trigger
        assert row["hold"] is None, row
    assert final["integration"] == INTEGRATED and final["merges"] == 1 and final["result_in_branch"], final
    assert not final["merge_head"], final


def test_s_il_l2_the_attempt_worktree_lock_is_a_failed_commit_never_pending(tmp_path, monkeypatch):
    """L2: a result commit already made (the integration reuses it) never touches the attempt worktree; the result
    commit step (T#8, ``create_attempt_commit``) meeting an attempt worktree ``index.lock`` answers ``commit_failed``,
    which ``integrate`` records as ``failed`` (only ``git_busy`` — an app lock with a release notice — is pending)."""
    if not shutil.which("git"):
        pytest.skip("Git is not installed")
    from modules.flow_gate.services import git_service as gs
    from modules.flow_gate.services.git import commit as git_commit
    r = Repo(tmp_path / "git")
    linked = tmp_path / "git" / "attempt"
    _git(r.common, "worktree", "add", "-b", "graph_attempt_x", str(linked), r.base)
    (linked / "a.txt").write_text("changed\n", encoding="utf-8")
    r.git_path(linked, "index.lock").write_text("", encoding="utf-8")
    monkeypatch.setattr(gs.db_git, "get_config", lambda project_id: {"enabled": True})
    monkeypatch.setattr(gs, "_worktree_link_ok", lambda path: True)
    from modules.flow_gate.services.git import lock_manager
    monkeypatch.setattr(lock_manager, "acquire", lambda *a, **k: SimpleNamespace(ok=True, lock_key="W:x"))
    monkeypatch.setattr(lock_manager, "release", lambda ctx, key: None)
    outcome = git_commit.create_attempt_commit(PROJECT, linked, "graph_attempt_x", r.base, "ga_x", "doc_x", "feat: x")
    assert outcome["committed"] is False and outcome["skipped_reason"] == "commit_failed", outcome


def _result_commit_of(env, attempt_id):
    return _sql(env["db"], "SELECT result_commit FROM graph_node_attempts WHERE attempt_id = ?",
                (attempt_id,))[0]["result_commit"]


@pytest.mark.parametrize("hold", ["h1", "h2"])
def test_s_il_l2_an_attempt_worktree_lock_fails_the_integration_and_a_command_recovers_it(env, decided, repo, tap,
                                                                                           record, monkeypatch, hold):
    """S-IL-2 (L2) on the real stack: t1's result is not committed yet, so the integration's T#8 step
    (``commit_result`` -> ``create_attempt_commit``, under the real W lock) meets an ``index.lock`` left in the attempt
    worktree. Under the lock: ``failed`` (``result_commit:commit_failed``), no hold, idle (``merge_conflict``), no
    result commit recorded, no merge, no MERGE_HEAD, the card's change kept in its tree. An unrelated G release does
    not retry it (a failed one waits for its command, by design). The lock removed, a command (step) commits it once
    and merges it once: integrated, done, one merge commit, the result in the group branch. (h1) the idle hold of an
    approval no command wakes; (h2) the step command's hold after the first failure. Only path resolution is
    redirected (``workspace.source_root`` -> the attempt's linked worktree; the project's Git config read as enabled)."""
    from flowgate.graph_run import workspace
    from modules.flow_gate.services import git_service as gs
    from modules.flow_gate.services.git import commit as git_commit
    sc = scene(env, repo, uncommitted=True)
    rec = record(f"S-IL_L2_{hold}", sc)
    real_root = workspace.source_root
    monkeypatch.setattr(workspace, "source_root", lambda attempt_id, **kw:
                        sc.linked.resolve() if attempt_id == sc.attempt else real_root(attempt_id, **kw))
    real_cfg = gs.db_git.get_config
    monkeypatch.setattr(gs.db_git, "get_config", lambda project_id:
                        {**(real_cfg(project_id) or {}), "enabled": True} if project_id == PROJECT
                        else real_cfg(project_id))
    commits: list = []
    real_commit = git_commit.create_attempt_commit

    def create_attempt_commit(*args, **kwargs):
        out = real_commit(*args, **kwargs)
        commits.append("committed" if out.get("committed") else out.get("skipped_reason"))
        return out

    monkeypatch.setattr(git_commit, "create_attempt_commit", create_attempt_commit)
    lock = repo.git_path(sc.linked, "index.lock")
    lock.parent.mkdir(parents=True, exist_ok=True)
    lock.write_text("", encoding="utf-8")
    assert _result_commit_of(env, sc.attempt) is None                 # nothing made in advance to reuse
    if hold == "h1":
        approve_unwoken(sc, monkeypatch)
    else:
        approve(sc)                                                   # the first integration meets the lock
        first = rec.step("first", commits=list(commits), result_commit=_result_commit_of(env, sc.attempt))
        assert (first["integration"], first["reason"]) == (FAILED, "result_commit:commit_failed"), first
        assert command(sc, "step") == 200                             # (h2) the command's hold, the lock still there
    locked = rec.step("locked", commits=list(commits), result_commit=_result_commit_of(env, sc.attempt),
                      dirty=repo.dirty(sc.flow), kept=(sc.linked / "result_l2.txt").exists())
    lock_release(sc.flow)                                             # F2: no retry of a failed one
    released = rec.step("lock_release", commits=list(commits), result_commit=_result_commit_of(env, sc.attempt))
    lock.unlink()
    code = command(sc, "step")
    sc.result = _result_commit_of(env, sc.attempt)
    final = rec.step("command", code=code, commits=list(commits), result_commit=sc.result, dirty=repo.dirty(sc.flow))
    final_after = rec.step("final", dirty=repo.dirty(sc.flow))

    for row in (locked, released):
        assert (row["integration"], row["reason"]) == (FAILED, "result_commit:commit_failed"), row
        assert row["hold"] is None and not row["working"], row
        assert (row["run"], row["idle_reason"]) == ("idle", "merge_conflict"), row
        assert row["result_commit"] is None and row["merges"] == 0 and not row["merge_head"], row
        assert row["sessions"] == [], row
    assert locked["commits"] and set(locked["commits"]) == {"commit_failed"}, locked
    assert len(locked["commits"]) == (1 if hold == "h1" else 2), locked             # each integration met the lock
    assert released["commits"] == locked["commits"], released                      # the G release asked nothing
    assert not locked["dirty"] and locked["kept"], locked                          # no partial merge, change kept
    assert code == 200, final
    assert final["commits"][len(locked["commits"]):] == ["committed"], final       # committed once, after the lock
    assert sc.result and sc.result != repo.base, final
    expect_recovered("a", final, sc)                       # integrated, ended, done, one merge, result in the branch
    assert not final["merge_head"] and not final["dirty"], final
    assert (repo.wt(sc.flow) / "result_l2.txt").read_text(encoding="utf-8") == "the card's result\n"
    assert final_after["merges"] == 1, final_after


# ── S-REC-1: the server start's recovery is unchanged ───────────────────────

@pytest.mark.parametrize("case", ["merged", "not_merged", "hold"])
def test_s_rec_1_the_server_start_recovery(env, decided, repo, tap, record, monkeypatch, case):
    from flowgate.graph_run import result_merge
    sc = scene(env, repo)
    rec = record(f"S-REC-1_{case}", sc)
    blocker = None
    if case == "merged":
        injection = Inject(monkeypatch, TARGETS["a"], persistent=True)
        approve(sc)
        injection.release()
    elif case == "not_merged":
        blocker = HoldG(sc.flow)
        injection = Inject(monkeypatch, TARGETS["c"], persistent=True)
        approve(sc)
        injection.release()
    else:
        prefail(sc)
        fails = Inject(monkeypatch, [("_end_hold.<locals>.body", None), ("_release_hold.<locals>.body", None)],
                       persistent=True)
        drive(sc, "h2", monkeypatch)
        fails.release()
    before = rec.step("before")
    result_merge.recover()
    row = rec.step("server_start")
    if case == "hold":
        assert before["hold"] is not None and row["hold"] is None, (before, row)
    if case == "not_merged":                               # pending again (recovered), its retry finds G busy
        assert before["integration"] == INTEGRATING and (row["integration"], row["reason"]) == (PENDING, "git_busy")
        blocker.release()
        row = rec.step("blocker_released")
    else:
        assert before["integration"] == (INTEGRATING if case == "merged" else INTEGRATED), before
    expect_recovered("a", row, sc)


# ── S-SAFE-4: the source rules ──────────────────────────────────────────────

_SERVER = Path(__file__).resolve().parents[2]


def _source(rel):
    return (_SERVER / rel).read_text(encoding="utf-8")


def test_s_safe_4_no_new_thread_timer_sweep_and_the_recovery_rule_is_called_not_copied():
    merge = _source("flowgate/graph_run/result_merge.py")
    stop_src = _source("flowgate/graph_run/stop.py")
    for src in (merge, stop_src):
        assert "Thread(" not in src and "Timer(" not in src and "time.sleep" not in merge
    assert merge.count("def _recover_one(") == 1
    reclaim = merge[merge.index("def reclaim_integrating("):]
    reclaim = reclaim[:reclaim.index("\ndef ", 1)]
    assert "_recover_one(row)" in reclaim and "merge --abort" not in reclaim and "_merge_commit_of" not in reclaim
    retry = merge[merge.index("def retry("):]
    retry = retry[:retry.index("\ndef ", 1)]
    assert retry.index("reclaim_integrating(") < retry.index("_closed_conflicts(")
    advance = stop_src[stop_src.index("def advance("):]
    advance = advance[:advance.index("\ndef ", 1)]
    assert advance.index("reclaim_integrating(") < advance.index("abort_for_stop(")
    for rel in ("flowgate/graph_run/settle.py", "modules/flow_gate/graph_workflow/api/run_routes.py",
                "modules/flow_gate/graph_workflow/services/run_service.py"):
        assert "reclaim_integrating" not in _source(rel), rel
    assert "integrate(attempt_id)" in retry                 # integrate runs only inside the drain
