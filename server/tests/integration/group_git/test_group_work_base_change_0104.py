"""Existing-Flow work-base change boundary (flowgate.v02.0104 D0004 §3.2–§3.4, §5.3–§5.5, §7.1).

Everything runs on the real migrated SQLite store (group_lock_stub.group_store): the
``groups`` row lock, ``operation_job`` and ``group_git_state`` are the production tables,
and provisioning runs the real ``worktree_provision`` job against a temporary repository
with a local bare origin. The concurrency cases (a)–(f) fix their interleaving with hooks
between calls; the one real race, (c), lets a provisioning thread register its job while
the change still holds the group row lock. A provision job that settles while the
change validates (after its group branch read) and the route's own body parsing are
fixed the same way (review rej_01M60FCNPMP9ZNXF).
"""
from __future__ import annotations

import sqlite3
import subprocess
import threading
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from modules.flow_gate import process_service
from modules.flow_gate.auth.middleware import get_current_user
from modules.flow_gate.db import groups as db_groups
from modules.flow_gate.db.connection import in_transaction
from modules.flow_gate.services import git_service
from modules.flow_gate.services.git import base_slot
from modules.flow_gate.services.git import group_work_base
from modules.flow_gate.services.git import job_store
from modules.flow_gate.services.git import lock_manager
from modules.flow_gate.services.git import worktree as worktree_service
from modules.flow_gate.services.git import worktree_provision
from modules.flow_gate.services.git.credentials import GitServiceError
from modules.flow_gate.workflow.routers import workflow

from group_lock_stub import group_store, hold_group_lock  # noqa: F401

pytestmark = pytest.mark.usefixtures("group_store")

PROJECT = "connected"
PROJECT_NAME = "Connected"
GID = "connected.default.0001"
OTHER_GID = "connected.default.0002"
BRANCH = "connected_default_0001"
NOW = "2026-10-28T00:00:00+09:00"
ADMIN = {"user_id": "admin", "is_admin": True}
WORKER = {"user_id": "worker", "is_admin": False}
ACTIVE_STATUSES = ("pending", "blocked", "retry_wait", "running", "freezing", "freeze_wait",
                   "recovery_required")


def _git(cwd: Path, *args: str) -> str:
    proc = subprocess.run(["git", *args], cwd=cwd, text=True, capture_output=True)
    assert proc.returncode == 0, (args, proc.stderr)
    return proc.stdout.strip()


class Env:
    def __init__(self, store, repo: Path, slots: Path, cfg: dict):
        self.store, self.repo, self.slots, self.cfg = store, repo, slots, cfg
        self.wt = slots / BRANCH

    def sha(self, ref: str) -> str:
        return _git(self.repo, "rev-parse", ref)

    def stored(self, group_id: str = GID):
        return db_groups.get_by_id(group_id)["work_base_ref"]

    def provision(self) -> str:
        return git_service.ensure_worktree(PROJECT, "default", GID)

    def register_job(self, status: str = "pending") -> dict:
        job = worktree_provision.create(self.cfg, PROJECT, PROJECT_NAME, GID, BRANCH, "test")
        assert job is not None
        if status != "pending":
            with self.store.transaction():
                self.store._execute(
                    "UPDATE operation_job SET status = ?, finished_at = ? WHERE job_id = ?",
                    [status, NOW if status in ("succeeded", "failed", "cancelled") else None,
                     job["job_id"]],
                )
        return job

    def jobs(self) -> list[dict]:
        return self.store._fetch_all(
            "SELECT * FROM operation_job WHERE group_id = ? ORDER BY created_at, job_id", [GID])

    def state(self, **cols) -> None:
        """A group_git_state row for GID (no ledger lock by default), then ``cols``."""
        with self.store.transaction():
            self.store._execute(
                "INSERT INTO group_git_state (group_id, project_id, branch, worktree_registered, "
                "status, created_at, updated_at) VALUES (?, ?, ?, 0, 'none', ?, ?)",
                [GID, PROJECT, BRANCH, NOW, NOW],
            )
            for column, value in cols.items():
                self.store._execute(
                    f"UPDATE group_git_state SET {column} = ? WHERE group_id = ?", [value, GID])

    def snapshot(self) -> dict:
        return {
            "refs": _git(self.repo, "for-each-ref", "--format=%(refname) %(objectname)"),
            "head": _git(self.repo, "symbolic-ref", "HEAD"),
            "status": _git(self.repo, "status", "--porcelain"),
            "worktrees": _git(self.repo, "worktree", "list", "--porcelain"),
        }


@pytest.fixture
def env(group_store, tmp_path, monkeypatch) -> Env:
    repo = tmp_path / "base"
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.name", "Test")
    _git(repo, "config", "user.email", "test@example.com")
    (repo / "README.md").write_text("base\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "base")
    for name in ("feature-a", "feature-b"):
        _git(repo, "switch", "-c", name, "main")
        (repo / f"{name}.txt").write_text(f"{name}\n", encoding="utf-8")
        _git(repo, "add", ".")
        _git(repo, "commit", "-m", name)
    _git(repo, "switch", "main")
    origin = tmp_path / "origin.git"
    _git(tmp_path, "init", "--bare", "-b", "main", str(origin))
    _git(repo, "remote", "add", "origin", str(origin))
    _git(repo, "push", "origin", "--all")
    slots = tmp_path / "slots"
    slots.mkdir()

    cfg = {"enabled": 1, "base_branch": "main"}
    monkeypatch.setattr(git_service.db_git, "get_config", lambda _pid: cfg)
    monkeypatch.setattr(git_service, "_project_name", lambda _pid: PROJECT_NAME)
    monkeypatch.setattr(git_service, "git_available", lambda: True)
    monkeypatch.setattr(git_service, "_load_secret_for", lambda _cfg: "")
    monkeypatch.setattr(git_service, "src_root",
                        lambda _name, branch: repo if branch == "main" else slots / branch)
    monkeypatch.setattr(git_service, "_emit", lambda *_a, **_k: None)
    monkeypatch.setattr(git_service, "_emit_worktree_ready", lambda *_a, **_k: None)
    monkeypatch.setattr(worktree_service, "_emit_worktree_failed", lambda *_a, **_k: None)
    db_groups.insert_group(GID, PROJECT, "default", "Flow", None, "feature-a")
    db_groups.insert_group(OTHER_GID, PROJECT, "default", "Other", None, None)
    assert git_service.worktree_branch_name(PROJECT, "default", GID) == BRANCH
    return Env(group_store, repo, slots, cfg)


@pytest.fixture(autouse=True)
def job_creates(monkeypatch):
    """D 8.4: create_or_get_job is never called inside a transaction."""
    calls = []
    real = job_store.create_or_get_job

    def spy(*args, **kwargs):
        calls.append(in_transaction())
        return real(*args, **kwargs)

    monkeypatch.setattr(job_store, "create_or_get_job", spy)
    yield calls
    assert True not in calls, "job_store.create_or_get_job ran inside a transaction"


def _client(user: dict = ADMIN) -> TestClient:
    app = FastAPI()
    app.include_router(workflow.router)
    app.dependency_overrides[get_current_user] = lambda: user
    return TestClient(app, raise_server_exceptions=False)


def _get(user: dict = ADMIN, group_id: str = GID):
    return _client(user).get(f"/api/v1/groups/{group_id}/work-base")


def _patch(body: dict, user: dict = ADMIN, group_id: str = GID):
    return _client(user).patch(f"/api/v1/groups/{group_id}/work-base", json=body)


def _error(response) -> dict:
    detail = response.json()["detail"]
    assert set(detail) == {"code", "message", "details"}, detail
    return detail


def _stub_outbox_io(monkeypatch, tmp_path) -> None:
    """The v0.1 root document's file/numbering side, as test_group_work_base_0613 stubs it.
    The existing group itself is the real row."""
    monkeypatch.setattr(process_service.db, "get_allowed_projects", lambda: [])
    monkeypatch.setattr(process_service.db, "get_documents_by_group_id", lambda _gid: [])
    monkeypatch.setattr(process_service.numbering_service, "reserve_document",
                        lambda _gid, _type, module: "0001-R")
    monkeypatch.setattr(process_service.db, "outbox_dir", lambda _p: str(tmp_path / "outbox"))
    monkeypatch.setattr(process_service.storage_paths, "document_path",
                        lambda **_k: tmp_path / "documents" / "0001-requirement.md")
    monkeypatch.setattr(process_service.db, "insert_document", lambda **_k: None)
    monkeypatch.setattr(process_service.db, "insert_event", lambda *_a, **_k: None)


def _v01(ref: str = "feature-b") -> dict:
    return process_service.create_requirement(
        project=PROJECT, module="default", title="Requirement", slug="", priority="medium",
        body="body", group_id=GID, work_base_ref=ref,
    )


def _v01_codes(result: dict) -> list:
    assert result["status"] == "error", result
    return [e["code"] if isinstance(e, dict) else e for e in result["errors"]]


# ── GET /groups/{id}/work-base (§5.3) ─────────────────────────────────────────

def test_get_reports_an_editable_unstarted_group(env):
    response = _get()
    assert response.status_code == 200
    assert response.json() == {
        "group_id": GID, "project_id": PROJECT, "git_enabled": True,
        "work_base_ref": "feature-a", "effective_work_base_ref": "feature-a",
        "work_base_locked": False, "lock_evidence": [], "work_base_editable": True,
        "reason": None, "can_edit": True,
    }
    worker = _get(WORKER)
    assert worker.status_code == 200
    assert worker.json()["can_edit"] is False
    assert worker.json()["work_base_editable"] is True


def test_get_reason_priority_takes_the_first_of_five(env, monkeypatch):
    env.register_job("pending")                        # provision_busy
    env.state(worktree_registered=1)                   # history_locked
    disposed = {"value": True}
    monkeypatch.setattr(process_service, "is_group_disposed", lambda _gid: disposed["value"])
    checkout = {"root": env.repo}
    monkeypatch.setattr(git_service, "_base_root_of", lambda _pid: checkout["root"])

    def reason():
        response = _get()
        assert response.status_code == 200
        return response.json()["reason"]

    env.cfg["enabled"] = 0
    checkout["root"] = env.repo.parent / "no-checkout"
    assert reason() == "disposed"
    disposed["value"] = False
    assert reason() == "git_disabled"
    env.cfg["enabled"] = 1
    assert reason() == "checkout_unavailable"
    checkout["root"] = env.repo
    assert reason() == "history_locked"
    with env.store.transaction():
        env.store._execute("DELETE FROM group_git_state WHERE group_id = ?", [GID])
    assert reason() == "provision_busy"
    with env.store.transaction():
        env.store._execute("UPDATE operation_job SET status = 'failed' WHERE group_id = ?", [GID])
    assert reason() is None
    assert _get().json()["work_base_editable"] is True


def test_get_unknown_group_is_404(env):
    response = _get(group_id="connected.default.9999")
    assert response.status_code == 404
    assert _error(response)["code"] == "group_not_found"


def test_get_read_permission_is_document_read(env, monkeypatch):
    monkeypatch.setattr(workflow, "_get_user_permissions", lambda _user: set())
    response = _get()
    assert response.status_code == 403
    assert isinstance(response.json()["detail"], str)


# ── extended lock evidence (§3.2.2) ───────────────────────────────────────────

@pytest.mark.parametrize("state, evidence", [
    ({"worktree_registered": 1}, ["ledger"]),
    ({"initial_source_sync_at": NOW}, ["ledger"]),
    ({"status": "merged"}, ["ledger"]),
    ({"work_base_sha": "0" * 40, "work_base_state": "verified"}, ["work_base_record"]),
])
def test_ledger_and_floor_record_lock(env, state, evidence):
    env.state(**state)
    view = _get().json()
    assert (view["work_base_locked"], view["lock_evidence"], view["reason"]) == (
        True, evidence, "history_locked")
    response = _patch({"work_base_ref": "feature-b", "expected_work_base_ref": "feature-a"})
    assert response.status_code == 409
    assert _error(response)["code"] == "group_work_base_locked"
    assert _error(response)["details"] == {"lock_evidence": evidence}
    assert env.stored() == "feature-a"










# ── PATCH /groups/{id}/work-base (§5.4) ───────────────────────────────────────

















_STORE_FAULTS = ("group_row", "disposed", "config", "lock_evidence", "settled_jobs", "validate",
                 "row_lock")


def _arm_store_fault(env, monkeypatch, stage) -> None:
    """A repository error at ``stage`` of the change path only: step 1 reads (group row,
    disposed, config, DB lock evidence, settled jobs, validation) or the row lock (step 2)."""
    armed = {"on": False}

    def faulty(real):
        def call(*args, **kwargs):
            if armed["on"]:
                raise sqlite3.OperationalError("database is locked")
            return real(*args, **kwargs)
        return call

    owner, name = {
        "group_row": (db_groups, "get_by_id"),
        "disposed": (process_service, "is_group_disposed"),
        "config": (git_service.db_git, "get_config"),
        "lock_evidence": (git_service.db_git, "get_state"),
        "settled_jobs": (group_work_base.db_jobs, "jobs_of_group"),
        "validate": (git_service.db_git, "get_state_by_branch"),
        "row_lock": (group_work_base, "lock_group_row"),
    }[stage]
    monkeypatch.setattr(owner, name, faulty(getattr(owner, name)))
    real_change = git_service.change_group_work_base_ref

    def change(*args, **kwargs):
        armed["on"] = True
        try:
            return real_change(*args, **kwargs)
        finally:
            armed["on"] = False

    monkeypatch.setattr(git_service, "change_group_work_base_ref", change)














# ── concurrency, deterministic (§7.1 (a)–(f)) ─────────────────────────────────

def _race(monkeypatch, change):
    """(c): the change checks for jobs; right then another thread provisions -- it reads
    the payload ref while the change is not committed yet and registers its job, which
    the row lock (SQLite: the write lock) lets through only after the change commits."""
    seen: dict = {}
    payload_read = threading.Event()
    real_resolve = git_service.resolve_group_work_base_ref

    def resolve(*args, **kwargs):
        value = real_resolve(*args, **kwargs)
        if threading.current_thread().name == "provision-b" and not payload_read.is_set():
            seen["payload_read_as"] = value
            payload_read.set()
        return value

    real_jobs = group_work_base._active_provision_jobs

    def jobs_then_provision(group_id):
        jobs = real_jobs(group_id)
        assert in_transaction() and jobs == []
        if "thread" not in seen:
            thread = threading.Thread(
                name="provision-b",
                target=lambda: seen.update(result=git_service.ensure_worktree(
                    PROJECT, "default", GID)),
            )
            seen["thread"] = thread
            thread.start()
            assert payload_read.wait(20), "provisioning never read its payload ref"
        return jobs

    monkeypatch.setattr(git_service, "resolve_group_work_base_ref", resolve)
    monkeypatch.setattr(group_work_base, "_active_provision_jobs", jobs_then_provision)
    outcome = change()
    seen["thread"].join(60)
    assert not seen["thread"].is_alive()
    monkeypatch.setattr(git_service, "resolve_group_work_base_ref", real_resolve)
    monkeypatch.setattr(group_work_base, "_active_provision_jobs", real_jobs)
    return outcome, seen


def _assert_race_lost_without_git_effect(env, seen, before):
    assert seen["payload_read_as"] == "feature-a"
    assert seen["result"] == "failed"
    (job,) = env.jobs()
    assert (job["status"], job["last_error_code"], job["phase"]) == (
        "failed", "work_base_changed", "start")
    assert worktree_provision._payload(job)["work_base_ref"] == "feature-a"
    assert env.snapshot() == before                    # base checkout and branches untouched
    assert not env.wt.exists()
    assert git_service.db_git.get_state(GID)["provision_error"] == "work_base_changed"
    assert group_work_base.group_work_base_locked(PROJECT, GID) is False
    view = _get().json()
    assert (view["lock_evidence"], view["reason"]) == ([], None)


def _assert_next_provision_uses(env, ref):
    assert env.provision() == "ok"
    assert _git(env.wt, "rev-parse", "HEAD") == env.sha(ref)
    assert worktree_provision._payload(env.jobs()[-1])["work_base_ref"] == ref
    assert env.jobs()[-1]["status"] == "succeeded"




















def _job_settles_during_validation(env, monkeypatch, *, branch_created: bool) -> list:
    """Review rej_01M60FCNPMP9ZNXF: the old ref's provision job is running when the change
    reads the group branch (none yet); while the change validates the new ref, the job
    creates the group branch from ``feature-a`` -- or fails before that -- and settles
    ``failed`` (retries exhausted). Only then does the change take the row lock."""
    job = env.register_job("running")
    assert worktree_provision._payload(job)["work_base_ref"] == "feature-a"
    real = group_work_base.validate_group_work_base_ref
    calls = []

    def validate_while_the_job_settles(project_id, value):
        calls.append(value)
        if len(calls) == 1:
            if branch_created:
                _git(env.repo, "branch", BRANCH, "feature-a")
            with env.store.transaction():
                env.store._execute(
                    "UPDATE operation_job SET status = 'failed', finished_at = ?, "
                    "touch_seq = touch_seq + 1 WHERE job_id = ?", [NOW, job["job_id"]])
        return real(project_id, value)

    monkeypatch.setattr(group_work_base, "validate_group_work_base_ref",
                        validate_while_the_job_settles)
    return calls
















# ── job registration and the base-less provision path ────────────────────────
