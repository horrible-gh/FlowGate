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


def test_cleanup_leaving_only_a_succeeded_job_locks(env):
    env.register_job("succeeded")
    assert _get().json()["lock_evidence"] == ["provision_succeeded"]
    assert _patch({"work_base_ref": "feature-b",
                   "expected_work_base_ref": "feature-a"}).status_code == 409


@pytest.mark.parametrize("where", ["local", "origin"])
def test_existing_group_branch_locks(env, where):
    if where == "local":
        _git(env.repo, "branch", BRANCH, "main")
    else:
        _git(env.repo, "update-ref", f"refs/remotes/origin/{BRANCH}", env.sha("main"))
    assert _get().json()["lock_evidence"] == ["group_branch_exists"]
    response = _patch({"work_base_ref": "feature-b", "expected_work_base_ref": "feature-a"})
    assert _error(response)["code"] == "group_work_base_locked"


@pytest.mark.parametrize("trace", ["failed_job", "cancelled_job", "failure_ledger", "backfill"])
def test_traces_without_git_work_stay_editable(env, trace):
    if trace in ("failed_job", "cancelled_job"):
        env.register_job(trace.split("_")[0])
    elif trace == "failure_ledger":
        git_service.db_git.upsert_provision_failure(GID, PROJECT, BRANCH, "fetch_failed")
    else:
        env.state(work_base_state="unverified")
    view = _get().json()
    assert (view["lock_evidence"], view["reason"], view["work_base_editable"]) == ([], None, True)
    response = _patch({"work_base_ref": "feature-b", "expected_work_base_ref": "feature-a"})
    assert response.status_code == 200, response.json()
    assert env.stored() == "feature-b"


def test_groups_list_lock_flag_keeps_the_ledger_only_judgment(env):
    env.state(work_base_sha="0" * 40, work_base_state="verified")
    listed = _client().get(f"/api/v1/groups?project_id={PROJECT}")
    assert listed.status_code == 200
    row = next(g for g in listed.json()["groups"] if g["group_id"] == GID)
    assert row["work_base_locked"] is False
    assert git_service.locked_group_ids(PROJECT) == set()
    assert _get().json()["work_base_locked"] is True
    with env.store.transaction():
        env.store._execute("UPDATE group_git_state SET worktree_registered = 1 WHERE group_id = ?",
                           [GID])
    row = next(g for g in _client().get(f"/api/v1/groups?project_id={PROJECT}").json()["groups"]
               if g["group_id"] == GID)
    assert row["work_base_locked"] is True


# ── PATCH /groups/{id}/work-base (§5.4) ───────────────────────────────────────

def test_patch_changes_an_unstarted_group_and_reads_back(env):
    before = env.snapshot()
    response = _patch({"work_base_ref": "feature-b", "expected_work_base_ref": "feature-a"})
    assert response.status_code == 200
    body = response.json()
    assert {k: body[k] for k in ("group_id", "work_base_ref", "effective_work_base_ref",
                                 "work_base_locked", "work_base_editable", "reason")} == {
        "group_id": GID, "work_base_ref": "feature-b", "effective_work_base_ref": "feature-b",
        "work_base_locked": False, "work_base_editable": True, "reason": None}
    assert body["updated_at"] == db_groups.get_by_id(GID)["updated_at"]
    view = _get().json()
    assert (view["work_base_ref"], view["effective_work_base_ref"]) == ("feature-b", "feature-b")
    assert env.snapshot() == before                      # no checkout switch, no Git write


def test_patch_compares_a_legacy_null_with_null(env):
    assert env.stored(OTHER_GID) is None
    assert _get(group_id=OTHER_GID).json()["effective_work_base_ref"] == "main"
    mismatch = _patch({"work_base_ref": "feature-b", "expected_work_base_ref": "main"},
                      group_id=OTHER_GID)
    assert _error(mismatch)["code"] == "group_work_base_changed"
    assert env.stored(OTHER_GID) is None
    ok = _patch({"work_base_ref": "feature-b", "expected_work_base_ref": None}, group_id=OTHER_GID)
    assert ok.status_code == 200
    assert env.stored(OTHER_GID) == "feature-b"


@pytest.mark.parametrize("body, status, code", [
    ({"expected_work_base_ref": "feature-a"}, 422, "group_work_base_invalid"),
    ({"work_base_ref": "", "expected_work_base_ref": "feature-a"}, 422, "group_work_base_invalid"),
    ({"work_base_ref": "   ", "expected_work_base_ref": "feature-a"}, 422, "group_work_base_invalid"),
    ({"work_base_ref": " feature-b", "expected_work_base_ref": "feature-a"}, 422,
     "group_work_base_invalid"),
    ({"work_base_ref": "bad..name", "expected_work_base_ref": "feature-a"}, 422,
     "group_work_base_invalid"),
    ({"work_base_ref": 7, "expected_work_base_ref": "feature-a"}, 422, "group_work_base_invalid"),
    ({"work_base_ref": "feature-b"}, 422, "group_work_base_expected_required"),
    ({"work_base_ref": "feature-b", "expected_work_base_ref": "feature-b"}, 409,
     "group_work_base_changed"),
    ({"work_base_ref": "missing", "expected_work_base_ref": "feature-a"}, 404,
     "group_work_base_not_found"),
    ({"work_base_ref": "remote-only", "expected_work_base_ref": "feature-a"}, 409,
     "group_work_base_remote_only"),
    ({"work_base_ref": "owned", "expected_work_base_ref": "feature-a"}, 409,
     "group_work_base_internal"),
    ({"work_base_ref": "blob-ref", "expected_work_base_ref": "feature-a"}, 422,
     "group_work_base_not_commit"),
])
def test_patch_input_and_validation_codes(env, body, status, code):
    _git(env.repo, "update-ref", "refs/remotes/origin/remote-only", env.sha("main"))
    with env.store.transaction():
        env.store._execute(
            "INSERT INTO group_git_state (group_id, project_id, branch, worktree_registered, "
            "status, created_at, updated_at) VALUES (?, ?, 'owned', 1, 'none', ?, ?)",
            [OTHER_GID, PROJECT, NOW, NOW])
    blob = subprocess.run(["git", "hash-object", "-w", "--stdin"], cwd=env.repo, input="blob\n",
                          text=True, capture_output=True, check=True).stdout.strip()
    (env.repo / ".git" / "refs" / "heads" / "blob-ref").write_text(blob + "\n", encoding="utf-8")
    response = _patch(body)
    assert response.status_code == status, response.json()
    assert _error(response)["code"] == code
    assert env.stored() == "feature-a"


def test_patch_state_codes(env, monkeypatch):
    def code():
        response = _patch({"work_base_ref": "feature-b", "expected_work_base_ref": "feature-a"})
        return response.status_code, _error(response)["code"]

    assert _patch({"work_base_ref": "feature-b", "expected_work_base_ref": None},
                  group_id="connected.default.9999").status_code == 404
    monkeypatch.setattr(process_service, "is_group_disposed", lambda _gid: True)
    assert code() == (409, "group_disposed")
    monkeypatch.setattr(process_service, "is_group_disposed", lambda _gid: False)
    env.cfg["enabled"] = 0
    assert code() == (409, "group_work_base_git_disabled")
    env.cfg["enabled"] = 1
    monkeypatch.setattr(git_service, "_base_root_of", lambda _pid: env.repo.parent / "absent")
    assert code() == (409, "group_work_base_checkout_unavailable")
    assert env.stored() == "feature-a"


def test_patch_403_is_the_plain_string_of_the_group_apis(env):
    response = _patch({"work_base_ref": "feature-b", "expected_work_base_ref": "feature-a"}, WORKER)
    assert response.status_code == 403
    assert response.json()["detail"] == "project.group.manage permission required."
    assert env.stored() == "feature-a"


@pytest.mark.parametrize("content, field", [
    (None, "work_base_ref"),            # no body at all: the new ref is missing
    (b"", "work_base_ref"),
    (b"   ", "work_base_ref"),
    (b"null", "body"),
    (b"[]", "body"),
    (b'["feature-b", "feature-a"]', "body"),
    (b'"feature-b"', "body"),
    (b"7", "body"),
    (b"{not json", "body"),
    (b"\xff\xfe{", "body"),
])
def test_patch_body_that_is_not_an_object_is_the_structured_422(env, content, field):
    kwargs = {} if content is None else {
        "content": content, "headers": {"content-type": "application/json"}}
    response = _client().patch(f"/api/v1/groups/{GID}/work-base", **kwargs)
    assert response.status_code == 422
    detail = _error(response)
    assert (detail["code"], detail["details"]) == ("group_work_base_invalid", {"field": field})
    assert env.stored() == "feature-a"


def test_patch_body_errors_keep_403_first_and_the_other_group_apis_unchanged(env):
    denied = _client(WORKER).patch(f"/api/v1/groups/{GID}/work-base")
    assert denied.status_code == 403
    assert denied.json()["detail"] == "project.group.manage permission required."
    # PUT /groups/{id} keeps its pydantic validation (the app's RequestValidationError list).
    put = _client().put(f"/api/v1/groups/{GID}", json={"graph_execution_limit": "x"})
    assert put.status_code == 422
    assert isinstance(put.json()["detail"], list)
    assert env.stored() == "feature-a"


def test_patch_store_failure_is_500_and_changes_nothing(env, monkeypatch):
    def boom(*_args):
        raise RuntimeError("disk full")

    monkeypatch.setattr(group_work_base, "_save_work_base_ref", boom)
    response = _patch({"work_base_ref": "feature-b", "expected_work_base_ref": "feature-a"})
    assert response.status_code == 500
    assert _error(response)["code"] == "group_work_base_update_failed"
    assert env.stored() == "feature-a"


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


@pytest.mark.parametrize("stage", _STORE_FAULTS)
def test_patch_store_error_in_either_step_is_the_structured_500(env, monkeypatch, stage):
    _arm_store_fault(env, monkeypatch, stage)
    response = _patch({"work_base_ref": "feature-b", "expected_work_base_ref": "feature-a"})
    assert response.status_code == 500
    assert response.headers["content-type"].startswith("application/json")
    assert _error(response)["code"] == "group_work_base_update_failed"
    assert env.stored() == "feature-a"
    assert env.jobs() == []


@pytest.mark.parametrize("stage", _STORE_FAULTS)
def test_v01_store_error_in_either_step_is_an_errors_entry(env, monkeypatch, tmp_path, stage):
    _stub_outbox_io(monkeypatch, tmp_path)
    _arm_store_fault(env, monkeypatch, stage)
    result = _v01("feature-b")
    assert _v01_codes(result) == ["group_work_base_update_failed"]
    assert env.stored() == "feature-a"


def test_store_error_conversion_keeps_the_git_service_error_codes_and_order(env, monkeypatch):
    """A GitServiceError raised in step 1 still wins over a later repository error."""
    _arm_store_fault(env, monkeypatch, "row_lock")
    monkeypatch.setattr(process_service, "is_group_disposed", lambda _gid: True)
    response = _patch({"work_base_ref": "feature-b", "expected_work_base_ref": "feature-a"})
    assert (response.status_code, _error(response)["code"]) == (409, "group_disposed")
    monkeypatch.setattr(process_service, "is_group_disposed", lambda _gid: False)
    response = _patch({"work_base_ref": "missing", "expected_work_base_ref": "feature-a"})
    assert (response.status_code, _error(response)["code"]) == (404, "group_work_base_not_found")
    assert env.stored() == "feature-a"


def test_patch_conditional_save_missing_its_row_is_changed(env, monkeypatch):
    monkeypatch.setattr(group_work_base, "_save_work_base_ref", lambda *_a: 0)
    response = _patch({"work_base_ref": "feature-b", "expected_work_base_ref": "feature-a"})
    assert (response.status_code, _error(response)["code"]) == (409, "group_work_base_changed")
    assert env.stored() == "feature-a"


def test_patch_trusts_the_group_row_not_the_body_project(env):
    response = _patch({"work_base_ref": "feature-b", "expected_work_base_ref": "feature-a",
                       "project_id": "p"})
    assert response.status_code == 200
    assert response.json()["project_id"] == PROJECT


def test_group_row_lock_keeps_updated_at_and_reads_the_row(env):
    before = db_groups.get_by_id(GID)
    with env.store.transaction():
        row = group_work_base.lock_group_row(GID)
        assert group_work_base.lock_group_row("connected.default.9999") is None
    assert row["work_base_ref"] == "feature-a" and row["project_id"] == PROJECT
    assert db_groups.get_by_id(GID)["updated_at"] == before["updated_at"]
    with pytest.raises(RuntimeError):
        group_work_base.lock_group_row(GID)


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


def test_a_change_committed_before_registration_reaches_the_payload(env, job_creates):
    assert _patch({"work_base_ref": "feature-b",
                   "expected_work_base_ref": "feature-a"}).status_code == 200
    _assert_next_provision_uses(env, "feature-b")
    assert (env.wt / "feature-b.txt").exists() and not (env.wt / "feature-a.txt").exists()
    assert job_creates and True not in job_creates
    view = _get().json()
    assert view["reason"] == "history_locked"
    assert {"ledger", "provision_succeeded", "group_branch_exists"} <= set(view["lock_evidence"])


def test_b_registered_job_makes_the_change_busy(env):
    job = env.register_job()
    response = _patch({"work_base_ref": "feature-b", "expected_work_base_ref": "feature-a"})
    assert (response.status_code, _error(response)["code"]) == (409, "group_work_base_busy")
    assert env.stored() == "feature-a"
    assert worktree_provision.attempt(job) == "ok"
    assert _git(env.wt, "rev-parse", "HEAD") == env.sha("feature-a")


def test_c_registration_after_the_job_check_fails_at_the_start_recheck(env, monkeypatch):
    before = env.snapshot()
    response, seen = _race(monkeypatch, lambda: _patch(
        {"work_base_ref": "feature-b", "expected_work_base_ref": "feature-a"}))
    assert response.status_code == 200, response.json()
    assert env.stored() == "feature-b"
    _assert_race_lost_without_git_effect(env, seen, before)
    _assert_next_provision_uses(env, "feature-b")


@pytest.mark.parametrize("status", ACTIVE_STATUSES)
def test_d_every_active_job_status_is_busy(env, status):
    env.register_job(status)
    assert _get().json()["reason"] == "provision_busy"
    response = _patch({"work_base_ref": "feature-b", "expected_work_base_ref": "feature-a"})
    assert (response.status_code, _error(response)["code"]) == (409, "group_work_base_busy")
    assert env.stored() == "feature-a"


def test_e_change_after_the_recheck_passed_is_busy(env, monkeypatch):
    real = worktree_provision._recheck_work_base
    refused = []

    def recheck_then_change(slot):
        real(slot)
        try:
            git_service.change_group_work_base_ref(GID, "feature-b", expected="feature-a")
        except GitServiceError as exc:
            refused.append(exc.code)

    monkeypatch.setattr(worktree_provision, "_recheck_work_base", recheck_then_change)
    assert env.provision() == "ok"
    assert refused == ["group_work_base_busy"]
    assert env.stored() == "feature-a"
    assert _git(env.wt, "rev-parse", "HEAD") == env.sha("feature-a")
    monkeypatch.setattr(worktree_provision, "_recheck_work_base", real)
    response = _patch({"work_base_ref": "feature-b", "expected_work_base_ref": "feature-a"})
    assert _error(response)["code"] == "group_work_base_locked"


def test_recheck_runs_on_every_start_attempt_and_not_after_intent(env, monkeypatch):
    real = worktree_provision._recheck_work_base
    rechecks = []
    monkeypatch.setattr(worktree_provision, "_recheck_work_base",
                        lambda slot: (rechecks.append(slot.work_base_ref), real(slot)))
    monkeypatch.setattr(lock_manager, "wait_budget", lambda *_a, **_k: 0.0)
    with hold_group_lock(PROJECT, GID):
        assert env.provision() == "failed"               # blocked on G, still at start
    assert env.jobs()[-1]["phase"] == "start" and rechecks == ["feature-a"]

    real_run_git = git_service._run_git
    fetches = []

    def run_git(args, **kwargs):
        if list(args[:2]) == ["fetch", "origin"]:
            fetches.append(1)
            if len(fetches) == 1:
                return subprocess.CompletedProcess(args, 1, "", "injected")
        return real_run_git(args, **kwargs)

    monkeypatch.setattr(git_service, "_run_git", run_git)
    assert env.provision() == "failed"                   # start ran again, then fetch failed
    assert rechecks == ["feature-a", "feature-a"]
    assert env.jobs()[-1]["phase"] == "intent_recorded"
    with env.store.transaction():
        env.store._execute("UPDATE operation_job SET available_at = ? WHERE group_id = ?",
                           ["2000-01-01T00:00:00+00:00", GID])
    assert env.provision() == "ok"                       # resumed after start: no recheck
    assert rechecks == ["feature-a", "feature-a"]


def test_recheck_catches_a_project_base_change_for_a_legacy_group(env, monkeypatch):
    """§3.3.4: the project base is not serialized with the row lock, but a NULL group's
    effective ref that moved under a queued job still fails the recheck."""
    with env.store.transaction():
        env.store._execute("UPDATE groups SET work_base_ref = NULL WHERE group_id = ?", [GID])
    monkeypatch.setattr(git_service, "src_root", lambda _name, branch: (
        env.repo if branch in ("main", "feature-b") else env.slots / branch))
    job = env.register_job()
    assert worktree_provision._payload(job)["work_base_ref"] == "main"
    env.cfg["base_branch"] = "feature-b"
    before = env.snapshot()
    assert worktree_provision.attempt(job) == "failed"
    assert env.jobs()[-1]["last_error_code"] == "work_base_changed"
    assert env.snapshot() == before and not env.wt.exists()


def test_f_v01_existing_group_follows_the_same_boundary(env, monkeypatch, tmp_path):
    _stub_outbox_io(monkeypatch, tmp_path)
    # success path: common change without the expected compare
    assert _v01("feature-b")["status"] == "success"
    assert env.stored() == "feature-b"
    # (b) a registered job
    job = env.register_job()
    assert _v01_codes(_v01("feature-a")) == ["group_work_base_busy"]
    assert env.stored() == "feature-b"
    with env.store.transaction():
        env.store._execute("UPDATE operation_job SET status = 'cancelled' WHERE job_id = ?",
                           [job["job_id"]])
    # (d) every active status
    for status in ACTIVE_STATUSES:
        active = env.register_job(status)
        assert _v01_codes(_v01("feature-a")) == ["group_work_base_busy"], status
        with env.store.transaction():
            env.store._execute("UPDATE operation_job SET status = 'cancelled' WHERE job_id = ?",
                               [active["job_id"]])
    assert env.stored() == "feature-b"


def test_f_v01_race_fails_the_provision_at_the_recheck(env, monkeypatch, tmp_path):
    _stub_outbox_io(monkeypatch, tmp_path)
    before = env.snapshot()
    result, seen = _race(monkeypatch, lambda: _v01("feature-b"))
    assert result["status"] == "success", result
    assert env.stored() == "feature-b"
    _assert_race_lost_without_git_effect(env, seen, before)
    _assert_next_provision_uses(env, "feature-b")


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


def test_g_job_settling_after_creating_the_branch_locks_the_patch(env, monkeypatch):
    calls = _job_settles_during_validation(env, monkeypatch, branch_created=True)
    response = _patch({"work_base_ref": "feature-b", "expected_work_base_ref": "feature-a"})
    assert (response.status_code, _error(response)["code"]) == (409, "group_work_base_locked")
    assert _error(response)["details"] == {"lock_evidence": ["group_branch_exists"]}
    assert calls == ["feature-b"]                      # the second run stopped at the lock
    assert env.stored() == "feature-a"
    assert _git(env.repo, "rev-parse", BRANCH) == env.sha("feature-a")
    view = _get().json()
    assert (view["work_base_ref"], view["reason"]) == ("feature-a", "history_locked")


def test_g_job_failing_before_the_branch_still_lets_the_patch_change(env, monkeypatch):
    calls = _job_settles_during_validation(env, monkeypatch, branch_created=False)
    response = _patch({"work_base_ref": "feature-b", "expected_work_base_ref": "feature-a"})
    assert response.status_code == 200, response.json()
    assert calls == ["feature-b", "feature-b"]         # rolled back once, read again
    assert env.stored() == "feature-b"
    _assert_next_provision_uses(env, "feature-b")


def test_g_v01_job_settling_after_creating_the_branch_is_locked(env, monkeypatch, tmp_path):
    _stub_outbox_io(monkeypatch, tmp_path)
    calls = _job_settles_during_validation(env, monkeypatch, branch_created=True)
    result = _v01("feature-b")
    assert _v01_codes(result) == ["group_work_base_locked"]
    assert result["errors"][0]["details"] == {"lock_evidence": ["group_branch_exists"]}
    assert calls == ["feature-b"]
    assert env.stored() == "feature-a"


def test_g_v01_job_failing_before_the_branch_still_changes(env, monkeypatch, tmp_path):
    _stub_outbox_io(monkeypatch, tmp_path)
    calls = _job_settles_during_validation(env, monkeypatch, branch_created=False)
    assert _v01("feature-b")["status"] == "success"
    assert calls == ["feature-b", "feature-b"]
    assert env.stored() == "feature-b"


def test_g_jobs_that_keep_settling_end_busy_without_a_save(env, monkeypatch):
    reads = []

    def always_new(_group_id):
        reads.append(1)
        return frozenset({len(reads)})

    monkeypatch.setattr(group_work_base, "_settled_provision_jobs", always_new)
    response = _patch({"work_base_ref": "feature-b", "expected_work_base_ref": "feature-a"})
    assert (response.status_code, _error(response)["code"]) == (409, "group_work_base_busy")
    assert len(reads) == 2 * group_work_base._CHANGE_ATTEMPTS
    assert env.stored() == "feature-a"


@pytest.mark.parametrize("evidence", ["work_base_record", "provision_succeeded",
                                      "group_branch_exists", "ledger"])
def test_f_v01_extended_lock_refuses_what_groups_list_shows_editable(
    env, monkeypatch, tmp_path, evidence,
):
    _stub_outbox_io(monkeypatch, tmp_path)
    if evidence == "work_base_record":
        env.state(work_base_sha="0" * 40, work_base_state="verified")
    elif evidence == "provision_succeeded":
        env.register_job("succeeded")
    elif evidence == "group_branch_exists":
        _git(env.repo, "branch", BRANCH, "main")
    else:
        env.state(status="waiting")
    listed = next(g for g in _client().get(f"/api/v1/groups?project_id={PROJECT}").json()["groups"]
                  if g["group_id"] == GID)
    assert listed["work_base_locked"] is (evidence == "ledger")
    result = _v01("feature-b")
    assert _v01_codes(result) == ["group_work_base_locked"]
    assert result["errors"][0]["details"] == {"lock_evidence": [evidence]}
    assert env.stored() == "feature-a"


def test_v01_and_patch_share_the_one_change_path(env, monkeypatch, tmp_path):
    _stub_outbox_io(monkeypatch, tmp_path)
    calls = []
    real = group_work_base.change_group_work_base_ref

    def spy(group_id, value, **kwargs):
        calls.append(("expected" in kwargs, kwargs.get("project_id")))
        return real(group_id, value, **kwargs)

    monkeypatch.setattr(git_service, "change_group_work_base_ref", spy)
    assert _v01("feature-b")["status"] == "success"
    assert _patch({"work_base_ref": "feature-a",
                   "expected_work_base_ref": "feature-b"}).status_code == 200
    assert calls == [(False, PROJECT), (True, None)]
    assert git_service.apply_group_work_base_ref(PROJECT, GID, "feature-b") == "feature-b"
    with pytest.raises(GitServiceError) as caught:
        git_service.apply_group_work_base_ref("p", GID, "feature-a")
    assert caught.value.code == "group_not_found"


# ── job registration and the base-less provision path ────────────────────────

def test_job_registration_inside_a_transaction_is_still_refused(env, job_creates):
    with env.store.transaction():
        with pytest.raises(job_store.JobProgramError) as caught:
            job_store.create_or_get_job(
                worktree_provision.KIND, PROJECT, {"group_id": GID, "group_generation": "x"},
                group_id=GID, payload={}, wake=False)
    assert caught.value.code == "job_create_inside_transaction"
    assert job_creates == [True]      # the spy sees it; this deliberate call is not a finding
    job_creates.clear()
    assert env.jobs() == []


def test_missing_base_checkout_provisions_the_base_then_takes_the_job_path(monkeypatch):
    order = []
    slot = {"state": "missing"}
    cfg = {"enabled": 1, "base_branch": "main"}
    monkeypatch.setattr(git_service.db_git, "get_config", lambda _pid: cfg)
    monkeypatch.setattr(git_service, "_project_name", lambda _pid: PROJECT_NAME)
    monkeypatch.setattr(git_service, "git_available", lambda: True)
    monkeypatch.setattr(git_service, "get_branch_merge_group_claim", lambda _gid: None)
    monkeypatch.setattr(git_service.db_git, "get_state", lambda _gid: None)
    monkeypatch.setattr(base_slot, "_judge_base_slot", lambda *_a: (
        order.append(("judge", slot["state"])) or slot["state"]))

    def provision_base(project_id, trigger):
        order.append(("provision_base", project_id))
        slot["state"] = "checkout"
        return {"status": "ready", "reason": None}

    monkeypatch.setattr(git_service, "provision_base", provision_base)
    monkeypatch.setattr(worktree_provision, "provision",
                        lambda *args: order.append(("job", args[3])) or "ok")
    for owner in (worktree_service, git_service):
        monkeypatch.setattr(owner, "_ensure_worktree_locked",
                            lambda *_a, **_k: pytest.fail("the old mutex path was taken"))
    assert git_service.ensure_worktree(PROJECT, "default", GID) == "ok"
    assert order == [("judge", "missing"), ("provision_base", PROJECT),
                     ("judge", "checkout"), ("job", GID)]
