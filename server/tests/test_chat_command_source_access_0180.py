"""flowgate.default.0180 T0004 -- CH fixed-token command capability regressions.

Real persisted tokens are issued through token_service.issue. Each token is issued for the
exact run id the command request runs under (ai_run_id), so the axis check passes for the
right reason and a refusal here can only come from the capability/liveness rules under test.
Only the process spawn is observed (spy) and commit/rollback/claim are counted by wrappers
that call the real functions.

Covers worker-loop continuation and approval/pre-spawn token replacement and revocation.
Actual start_run admission and losing claim coverage live in test_chat_command_admission_0180.py.
"""
from __future__ import annotations

import importlib
import itertools
import json
import os
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

os.environ.setdefault("TESTING", "1")
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing-only-32c")
os.environ.setdefault("DB_TYPE", "sqlite")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from modules.flow_gate.db import connection  # noqa: E402
from modules.flow_gate.services import chat_command_service as svc  # noqa: E402
from modules.flow_gate.services import chat_settings_service as css  # noqa: E402
from modules.flow_gate.services import git_service  # noqa: E402
from modules.flow_gate.services import token_service  # noqa: E402

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git binary not on PATH")

USER = "usr_0180"
PROJECT = "flowgate"
GROUP = "flowgate.default.0180"
DOC = "flowgate.default.0180.0009-CH"
POLICIES = ("always_approve", "user_approval", "reject")
CAP_CODE = "chat_command_capability_required"
FORBIDDEN_CODE = "chat_command_forbidden"

_STORE_MODULES = (
    "modules.flow_gate.db.connection",
    "modules.flow_gate.db.chat_command_requests",
    "modules.flow_gate.db.user_chat_command_policy",
    "modules.flow_gate.db.ai_run_source_changes",
    "modules.flow_gate.db.user_chat_settings",
    "modules.flow_gate.db.user_chat_source_access",
    "modules.flow_gate.db.tokens",
    "modules.flow_gate.services.chat_settings_service",
    "modules.flow_gate.services.token_service",
)
_RUN_IDS = itertools.count(1)


def _git(root: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True,
                          text=True, encoding="utf-8").stdout


@pytest.fixture
def repo(tmp_path) -> Path:
    root = tmp_path / "wt"
    root.mkdir()
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "t@example.com")
    _git(root, "config", "user.name", "tester")
    (root / "a.py").write_text("one\ntwo\n", encoding="utf-8")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "init")
    return root


@pytest.fixture
def store(migrated_sqlite_db, monkeypatch, tmp_path):
    """Real SQLite store from the migrations; every service reads the same instance."""
    from sqloader.sqlite3 import SQLiteWrapper

    path = migrated_sqlite_db("test_chat_command_source_access_0180.db")
    real = object.__new__(connection.FlowGateStore)
    real._db = SQLiteWrapper(path)
    real._sq = None
    for name in _STORE_MODULES:
        module = importlib.import_module(name)
        if hasattr(module, "get_store"):
            monkeypatch.setattr(module, "get_store", lambda real=real: real)
    monkeypatch.setattr(connection, "STORE", real, raising=False)
    monkeypatch.setenv("FLOWGATE_STORAGE_DIR", str(tmp_path / "storage"))
    monkeypatch.setenv("FLOWGATE_TOKEN_PEPPER_ACTIVE_ID", "v1")
    monkeypatch.setenv("FLOWGATE_TOKEN_PEPPER_v1", "test-pepper-0180")
    real._execute(
        "INSERT INTO users (user_id, username, email, password, is_active, created_at, updated_at) "
        "VALUES (?, ?, ?, 'x', 1, '2026-10-05T00:00:00+09:00', '2026-10-05T00:00:00+09:00')",
        [USER, USER, f"{USER}@example.com"],
    )
    # tokens.project and tokens.group_id are real FKs (migration 110): the project and the
    # group the token is issued for must exist as rows. tokens.doc_ref has no FK.
    real._execute(
        "INSERT OR IGNORE INTO projects (project_id, project_name, created_at, updated_at) "
        "VALUES (?, 'FlowGate', '2026-10-05T00:00:00+09:00', '2026-10-05T00:00:00+09:00')",
        [PROJECT],
    )
    real._execute(
        "INSERT INTO groups (group_id, project_id, module, title, status, created_at, updated_at) "
        "VALUES (?, ?, 'default', '0180 CH capability', 'OPEN', "
        "'2026-10-05T00:00:00+09:00', '2026-10-05T00:00:00+09:00')",
        [GROUP, PROJECT],
    )
    return real


@pytest.fixture
def bound(repo, monkeypatch):
    monkeypatch.setattr(git_service, "effective_src_root_ex", lambda project, group: (repo, "worktree"))
    events: list[tuple[str, dict]] = []
    monkeypatch.setattr(git_service, "_emit", lambda kind, project, group, payload: events.append((kind, payload)))
    return events


@pytest.fixture
def spawns(monkeypatch):
    """Spawn spy: records every process creation. Zero-spawn assertions read this list."""
    calls: list[tuple] = []
    real_spawn = svc.executor.spawn

    def spy(*args, **kwargs):
        calls.append(args)
        return real_spawn(*args, **kwargs)

    monkeypatch.setattr(svc.executor, "spawn", spy)
    return calls


@pytest.fixture
def live(monkeypatch):
    registry: dict[str, dict] = {}
    from modules.flow_gate.services import ai_invoke_service

    monkeypatch.setattr(ai_invoke_service, "get_run_record", lambda run_id: registry.get(run_id))
    return registry


@pytest.fixture
def source_access_calls(monkeypatch):
    """Counts claim/commit/rollback on the real functions (wrappers still call through)."""
    from modules.flow_gate.db import user_chat_source_access as sa

    counts = {"claim": 0, "commit": 0, "rollback": 0}
    for name in counts:
        real = getattr(sa, name)

        def wrapper(*args, _n=name, _real=real, **kwargs):
            counts[_n] += 1
            return _real(*args, **kwargs)

        monkeypatch.setattr(sa, name, wrapper)
    return counts


def _set_policy(value: str) -> None:
    css.save_chat_settings(USER, {"command_policy": value})


def _set_mode(store, mode: str) -> None:
    """The user's UI source-access mode, stored through the real table."""
    store._execute(
        "INSERT INTO user_chat_source_access (user_id, source_access_mode, created_at, updated_at) "
        "VALUES (?, ?, '2026-10-05T00:00:00+09:00', '2026-10-05T00:00:00+09:00') "
        "ON CONFLICT(user_id) DO UPDATE SET source_access_mode = excluded.source_access_mode",
        [USER, mode],
    )


def _run(repo: Path, **extra) -> dict:
    run = {
        "run_id": f"aiv_0180_{next(_RUN_IDS)}", "project_id": PROJECT, "group_id": GROUP,
        "doc_ref": DOC, "action_scope": "chat", "status": "running", "issued_to": USER,
        "provider": {"name": "Test Model"}, "source_root": str(repo),
        "cancel_event": threading.Event(), "token_id": None,
    }
    run.update(extra)
    return run


def _bind(store, repo, live, source_mode: str, policy: str, **extra) -> dict:
    """Make a live run and a REAL token issued for that run id, then attach the token."""
    _set_mode(store, source_mode)
    _set_policy(policy)
    run = _run(repo, **extra)
    live[run["run_id"]] = run
    tok = token_service.issue(project=PROJECT, group_id=GROUP, action_scope="chat", doc_ref=DOC,
                              issued_to=USER, ai_run_id=run["run_id"])
    run["token_id"] = tok["token_id"]
    run["raw_token"] = tok["raw_token"]
    return run


def _stored(store, token_id: str) -> dict:
    return store._fetch_one("SELECT * FROM tokens WHERE token_id = ?", [token_id])


def _refused(run: dict, payload: dict, code: str) -> None:
    with pytest.raises(svc.ChatCommandError) as info:
        svc.create_request(run, payload, token_id=run["token_id"])
    assert info.value.code == code and info.value.status == 403


# ---- 1. read capability: refused for every policy, no row, no process ----

@pytest.mark.parametrize("policy", POLICIES)
def test_read_token_is_refused_for_every_policy(store, bound, live, repo, spawns, policy):
    run = _bind(store, repo, live, "read_only", policy, source_access="read_write")
    assert _stored(store, run["token_id"])["source_access"] == "read"
    _refused(run, {"program": "git", "args": ["status"]}, CAP_CODE)
    assert svc.db.list_open_for_run(run["run_id"]) == [] and spawns == []


@pytest.mark.parametrize("policy", POLICIES)
def test_read_token_run_tool_is_refused_for_every_policy(store, bound, live, repo, spawns, policy):
    run = _bind(store, repo, live, "read_only", policy)
    status, result = svc.run_tool(run, {"program": "git", "args": ["status"]}, 60)
    assert status == 403 and result["ok"] is False and result["error"]["code"] == CAP_CODE
    assert svc.db.list_open_for_run(run["run_id"]) == [] and spawns == []


@pytest.mark.parametrize("policy", POLICIES)
def test_read_token_refused_over_http_and_run_stays_live(store, bound, live, repo, spawns, policy):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from modules.flow_gate.api.v1 import chat_command_routes

    run = _bind(store, repo, live, "read_only", policy)
    app = FastAPI()
    app.include_router(chat_command_routes.router, prefix="/api/v1")
    response = TestClient(app).post(
        "/api/v1/chat-commands", json={"program": "git", "args": ["status"]},
        headers={"Authorization": f"Bearer {run['raw_token']}"})
    assert response.status_code == 403
    assert response.json()["error"]["code"] == CAP_CODE
    assert svc.db.list_open_for_run(run["run_id"]) == [] and spawns == []
    assert run["status"] == "running" and not run["cancel_event"].is_set()


# ---- 2. read_write capability (edit / edit_once) x 3 policies ----

@pytest.mark.parametrize("mode", ("edit", "edit_once"))
@pytest.mark.parametrize("policy", POLICIES)
def test_read_write_policy_matrix(store, bound, live, repo, spawns, source_access_calls, mode, policy):
    run = _bind(store, repo, live, mode, policy)
    assert _stored(store, run["token_id"])["source_access"] == "read_write"
    payload = {"program": "git", "args": ["rev-parse", "--show-toplevel"]}
    if policy == "reject":
        status, result = svc.run_tool(run, payload, 60)
        assert (status, result["rejected_by"]) == (200, "policy") and spawns == []
        return
    if policy == "always_approve":
        status, result = svc.run_tool(run, payload, 60)
        assert status == 200 and result["status"] == "succeeded" and result["exit_code"] == 0
        assert len(spawns) == 1
        return
    row = svc.create_request(run, payload, token_id=run["token_id"])
    assert row["status"] == "pending_approval" and spawns == []  # waits, no process
    svc.decide(row["request_id"], {"user_id": USER}, "approve")
    assert svc.wait_terminal(row["request_id"], 60)["status"] == "succeeded" and len(spawns) == 1


@pytest.mark.parametrize("mode", ("edit", "edit_once"))
def test_user_reject_never_spawns(store, bound, live, repo, spawns, mode):
    run = _bind(store, repo, live, mode, "user_approval")
    row = svc.create_request(run, {"program": "git", "args": ["status"]}, token_id=run["token_id"])
    svc.decide(row["request_id"], {"user_id": USER}, "reject")
    assert svc.wait_terminal(row["request_id"], 30)["status"] == "rejected" and spawns == []


# ---- 3. edit_once: one claim at issue, commands add zero claim/commit/rollback ----

def test_edit_once_issue_claims_once_and_two_commands_add_no_source_access_calls(
        store, bound, live, repo, spawns, source_access_calls):
    run = _bind(store, repo, live, "edit_once", "always_approve")
    assert source_access_calls["claim"] == 1  # the single issue-time claim
    base = dict(source_access_calls)
    assert _stored(store, run["token_id"])["source_access"] == "read_write"
    for _ in range(2):
        status, result = svc.run_tool(run, {"program": "git", "args": ["status"]}, 60)
        assert status == 200 and result["status"] == "succeeded"
    assert len(spawns) == 2
    assert dict(source_access_calls) == base  # zero extra claim/commit/rollback


def test_ui_upgrade_does_not_promote_an_issued_read_token(store, bound, live, repo, spawns):
    run = _bind(store, repo, live, "read_only", "always_approve")
    _set_mode(store, "edit")  # UI now says read_write; the issued token stays read
    _refused(run, {"program": "git", "args": ["status"]}, CAP_CODE)
    assert _stored(store, run["token_id"])["source_access"] == "read" and spawns == []


def test_claim_loser_token_is_blocked(store, bound, live, repo, spawns):
    winner = _bind(store, repo, live, "edit_once", "always_approve")
    loser = _run(repo)
    live[loser["run_id"]] = loser
    loser["token_id"] = token_service.issue(project=PROJECT, group_id=GROUP, action_scope="chat", doc_ref=DOC, issued_to=USER, ai_run_id=loser["run_id"])["token_id"]
    assert winner["token_id"] != loser["token_id"]
    assert _stored(store, loser["token_id"])["source_access"] == "read"
    _refused(loser, {"program": "git", "args": ["status"]}, CAP_CODE)
    assert spawns == []


# ---- 4. NULL / legacy / corrupt capability and liveness ----

@pytest.mark.parametrize("bad_value", [None, "legacy", "corrupt"])
def test_null_legacy_or_corrupt_capability_is_blocked(store, bound, live, repo, spawns, bad_value):
    run = _bind(store, repo, live, "edit", "always_approve")
    # tokens.source_access has a CHECK domain; the legacy/corrupt rows are written past it
    # on purpose, the same way a pre-domain row would look to the reader.
    with store.transaction():
        store._execute("PRAGMA ignore_check_constraints = ON")
        try:
            store._execute("UPDATE tokens SET source_access = ? WHERE token_id = ?", [bad_value, run["token_id"]])
        finally:
            store._execute("PRAGMA ignore_check_constraints = OFF")
    _refused(run, {"program": "git", "args": ["status"]}, CAP_CODE)
    assert spawns == []


def test_revoked_token_is_refused(store, bound, live, repo, spawns):
    run = _bind(store, repo, live, "edit", "always_approve")
    token_service.revoke(run["token_id"])
    _refused(run, {"program": "git", "args": ["status"]}, FORBIDDEN_CODE)
    assert svc.db.list_open_for_run(run["run_id"]) == [] and spawns == []


def test_expired_token_is_refused(store, bound, live, repo, spawns):
    run = _bind(store, repo, live, "edit", "always_approve")
    store._execute("UPDATE tokens SET expires_at = '2020-01-01T00:00:00+09:00' WHERE token_id = ?",
                   [run["token_id"]])
    _refused(run, {"program": "git", "args": ["status"]}, FORBIDDEN_CODE)
    assert spawns == []


def test_legacy_pending_read_is_terminal_on_approval(store, bound, live, repo, spawns):
    """A pending row stored by the old service under a read token cannot be approved."""
    run = _bind(store, repo, live, "read_only", "user_approval")
    row = svc.db.create(
        request_id=f"ccr_legacy_{run['run_id']}", ai_run_id=run["run_id"], doc_id=DOC,
        project_id=PROJECT, group_id=GROUP, token_id=run["token_id"], issued_to=USER,
        provider_name="Test Model", program="git", args=["status"], cwd_relative=".",
        timeout_seconds=60, category="git_read", policy="user_approval", status="pending_approval")
    with pytest.raises(svc.ChatCommandError) as info:
        svc.decide(row["request_id"], {"user_id": USER}, "approve")
    assert info.value.code == CAP_CODE
    done = svc.db.get(row["request_id"])
    assert done["status"] == "cancelled" and done["error_code"] == CAP_CODE
    assert spawns == []


def test_revoke_before_spawn_guard_spawns_nothing(store, bound, live, repo, spawns, monkeypatch):
    run = _bind(store, repo, live, "edit", "always_approve")
    entered, release = threading.Event(), threading.Event()
    real_root = svc.worktree_root

    def gated(*args, **kwargs):
        if threading.current_thread().name.startswith("chat-command-"):
            entered.set()
            assert release.wait(30)
        return real_root(*args, **kwargs)

    monkeypatch.setattr(svc, "worktree_root", gated)
    row = svc.create_request(run, {"program": "git", "args": ["status"]}, token_id=run["token_id"])
    assert entered.wait(30)
    token_service.revoke(run["token_id"])
    release.set()
    done = svc.wait_terminal(row["request_id"], 30)
    assert done["status"] == "cancelled" and done["error_code"] == FORBIDDEN_CODE
    assert spawns == []


def test_token_replaced_before_approval_is_refused(store, bound, live, repo, spawns):
    """A pending row under token A; token A is revoked and the run carries token B."""
    run = _bind(store, repo, live, "edit", "user_approval")
    row = svc.create_request(run, {"program": "git", "args": ["status"]}, token_id=run["token_id"])
    token_service.revoke(run["token_id"])
    replacement = token_service.issue(project=PROJECT, group_id=GROUP, action_scope="chat", doc_ref=DOC,
                                      issued_to=USER, ai_run_id=run["run_id"])
    run["token_id"] = replacement["token_id"]
    with pytest.raises(svc.ChatCommandError) as info:
        svc.decide(row["request_id"], {"user_id": USER}, "approve")
    assert info.value.status == 403 and spawns == []


def test_api_read_refusal_keeps_same_conversation(monkeypatch, store, bound, live, repo, spawns):
    from modules.flow_gate.services import ai_invoke_service as ai
    from modules.flow_gate.services.ai_invoke import worker  # noqa: F401

    run = _bind(store, repo, live, "read_only", "always_approve", docs_target=0, mode="single",
                timed_out=False, api_base_url="http://127.0.0.1/flowgate/api/v1",
                started_mono=time.monotonic())
    monkeypatch.setattr(ai.ai_settings_service, "get_provider_secret", lambda *_: "key")
    monkeypatch.setattr(ai, "_remaining_sec", lambda _run: 120)
    monkeypatch.setattr(ai, "_conversation_context", lambda *_: (200, {"head_seq": 1, "turns": []}))
    calls = {"n": 0, "conversations": []}

    def fake_call(*args):
        calls["n"] += 1
        calls["conversations"].append(list(args[3]))
        if calls["n"] == 1:
            return None, {"id": "c1", "name": "run_command",
                          "input": {"program": "git", "args": ["status", "--porcelain"]}}, {"role": "assistant"}
        return "done", {"id": "c2", "name": "send_chat_reply", "input": {"body": "done"}}, {"role": "assistant"}

    monkeypatch.setattr(ai, "_call_openai", fake_call)
    monkeypatch.setattr(ai, "_conversation_turn_register", lambda *_: (201, {"ok": True}))
    run_id = run["run_id"]
    assert ai._api_execute({"id": "p", "kind": "openai", "api_base_url": "https://x", "api_model": "m"},
                           "prompt", run) == ("started_ok", None)
    assert calls["n"] == 2 and run["run_id"] == run_id
    tool_results = [m for m in calls["conversations"][1] if m.get("role") == "tool"]
    refusal = [json.loads(m["content"]) for m in tool_results if CAP_CODE in str(m.get("content"))]
    assert refusal and refusal[0]["ok"] is False and refusal[0]["error"]["code"] == CAP_CODE
    assert svc.db.list_open_for_run(run_id) == [] and spawns == []


@pytest.mark.parametrize("replacement_mode", ("read_only", "edit"))
@pytest.mark.parametrize("phase", ("pending", "pre_spawn"))
def test_replaced_current_token_stops_pending_and_pre_spawn(store, bound, live, repo, spawns, monkeypatch,
                                                           replacement_mode, phase):
    policy = "user_approval" if phase == "pending" else "always_approve"
    run = _bind(store, repo, live, "edit", policy)
    entered, release = threading.Event(), threading.Event()
    real_root = svc.worktree_root

    def gated(*args, **kwargs):
        if phase == "pre_spawn" and threading.current_thread().name.startswith("chat-command-"):
            entered.set()
            assert release.wait(30)
        return real_root(*args, **kwargs)

    monkeypatch.setattr(svc, "worktree_root", gated)
    row = svc.create_request(run, {"program": "git", "args": ["status"]}, token_id=run["token_id"])
    _set_mode(store, replacement_mode)
    replacement = token_service.issue(project=PROJECT, group_id=GROUP, action_scope="chat", doc_ref=DOC,
                                      issued_to=USER, ai_run_id=run["run_id"])
    assert _stored(store, replacement["token_id"])["source_access"] == (
        "read" if replacement_mode == "read_only" else "read_write")
    run["current_token_id"] = replacement["token_id"]
    try:
        if phase == "pending":
            with pytest.raises(svc.ChatCommandError) as info:
                svc.decide(row["request_id"], {"user_id": USER}, "approve")
            assert info.value.code == FORBIDDEN_CODE
        else:
            assert entered.wait(30)
    finally:
        release.set()
    done = svc.wait_terminal(row["request_id"], 30)
    assert done["status"] == "cancelled" and done["error_code"] == FORBIDDEN_CODE
    assert spawns == [] and svc.db.list_open_for_run(run["run_id"]) == []

@pytest.mark.parametrize("mode", ("edit", "edit_once"))
@pytest.mark.parametrize("policy", POLICIES)
def test_cli_write_token_preserves_policy(store, bound, live, repo, spawns, mode, policy):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from modules.flow_gate.api.v1 import chat_command_routes

    run = _bind(store, repo, live, mode, policy)
    app = FastAPI()
    app.include_router(chat_command_routes.router, prefix="/api/v1")
    response = TestClient(app).post(
        "/api/v1/chat-commands",
        json={"program": "git", "args": ["status"],
              "wait_seconds": 0 if policy == "user_approval" else 60},
        headers={"Authorization": f"Bearer {run['raw_token']}"})
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    request_id = body["request"]["request_id"]
    if policy == "user_approval":
        assert body["terminal"] is False and body["request"]["status"] == "pending_approval"
        assert spawns == []
        svc.decide(request_id, {"user_id": USER}, "approve")
    done = svc.wait_terminal(request_id, 60)
    assert done["status"] == ("rejected" if policy == "reject" else "succeeded")
    assert len(spawns) == (0 if policy == "reject" else 1)


@pytest.mark.parametrize("damage", ("missing_id", "missing_row", "consumed", "bad_expiry", "doc_mismatch"))
def test_missing_or_invalid_token_binding_is_fail_closed(store, bound, live, repo, spawns, damage):
    run = _bind(store, repo, live, "edit", "always_approve")
    if damage == "missing_id":
        run["token_id"] = None
    elif damage == "missing_row":
        run["token_id"] = "tok_absent"
    elif damage == "consumed":
        token_service.db_tokens.consume(run["token_id"])
    elif damage == "bad_expiry":
        store._execute("UPDATE tokens SET expires_at = 'corrupted' WHERE token_id = ?", [run["token_id"]])
    else:
        run["doc_ref"] = "flowgate.default.0180.0011-CH"
    status, result = svc.run_tool(run, {"program": "git", "args": ["status"]}, 60)
    assert status == 403 and result["error"]["code"] == FORBIDDEN_CODE
    assert svc.db.list_open_for_run(run["run_id"]) == [] and spawns == []