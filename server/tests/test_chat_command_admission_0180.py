"""flowgate.default.0180 T0004 -- actual edit_once admission -> repeated CH commands.

The real ``ai_invoke_service.start_run`` admission runs against a real SQLite store: the real
``token_service.issue`` (including the edit_once claim CAS), the real ``HandoffGate``
seal/commit/open sequence and the real chat command service guard. Only the external edges are
stood in for: the AI provider chain, the group worktree/source-sync checks, the group lease
rows and the worker thread body (it waits for the REAL gate to be OPEN, then asks for commands
through ``chat_command_service.run_tool`` exactly like the API tool loop does).

The command guard, token issuance and gate are never replaced. claim/commit/rollback are counted
by wrappers that call the real functions.
"""
from __future__ import annotations

import importlib
import os
import shutil
import subprocess
import sys
import threading
from pathlib import Path

import pytest

os.environ.setdefault("TESTING", "1")
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing-only-32c")
os.environ.setdefault("DB_TYPE", "sqlite")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from modules.flow_gate.db import connection  # noqa: E402
from modules.flow_gate.db import tokens as db_tokens  # noqa: E402
from modules.flow_gate.db import user_chat_source_access as db_source_access  # noqa: E402
from modules.flow_gate.services import ai_invoke_service  # noqa: E402
from modules.flow_gate.services import chat_command_service as commands  # noqa: E402
from modules.flow_gate.services import chat_settings_service as css  # noqa: E402
from modules.flow_gate.services import git_service  # noqa: E402
from modules.flow_gate.services import provider_capability_service  # noqa: E402
from modules.flow_gate.services import token_service  # noqa: E402
from modules.flow_gate.services.ai_invoke import admission  # noqa: E402
from modules.flow_gate.services.ai_invoke import runtime  # noqa: E402
from modules.flow_gate.settings import ai_settings_service  # noqa: E402
from modules.flow_gate.storage import paths as storage_paths  # noqa: E402

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git binary not on PATH")

USER = "usr_0180a"
PROJECT = "flowgate"
GROUP = "flowgate.default.0180"
DOC = "flowgate.default.0180.0010-CH"
CAP_CODE = "chat_command_capability_required"
COMMANDS = (
    {"program": "git", "args": ["status"]},
    {"program": "git", "args": ["rev-parse", "--show-toplevel"]},
    {"program": "git", "args": ["log", "-1", "--format=%s"]},
)
_STORE_MODULES = (
    "modules.flow_gate.db.connection",
    "modules.flow_gate.db.chat_command_requests",
    "modules.flow_gate.db.user_chat_command_policy",
    "modules.flow_gate.db.user_chat_settings",
    "modules.flow_gate.db.user_chat_source_access",
    "modules.flow_gate.db.tokens",
    "modules.flow_gate.services.chat_settings_service",
    "modules.flow_gate.services.token_service",
)


def _git(root: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True, text=True)


@pytest.fixture
def repo(tmp_path) -> Path:
    root = tmp_path / "wt"
    root.mkdir()
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "t@example.com")
    _git(root, "config", "user.name", "tester")
    (root / "a.py").write_text("one\n", encoding="utf-8")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "init")
    return root


@pytest.fixture
def store(migrated_sqlite_db, monkeypatch, tmp_path):
    from sqloader.sqlite3 import SQLiteWrapper

    real = object.__new__(connection.FlowGateStore)
    real._db = SQLiteWrapper(migrated_sqlite_db("test_chat_command_admission_0180.db"))
    real._sq = None
    for name in _STORE_MODULES:
        module = importlib.import_module(name)
        if hasattr(module, "get_store"):
            monkeypatch.setattr(module, "get_store", lambda real=real: real)
    monkeypatch.setattr(connection, "STORE", real, raising=False)
    monkeypatch.setenv("FLOWGATE_STORAGE_DIR", str(tmp_path / "storage"))
    monkeypatch.setenv("FLOWGATE_TOKEN_PEPPER_ACTIVE_ID", "v1")
    monkeypatch.setenv("FLOWGATE_TOKEN_PEPPER_v1", "test-pepper-0180-admission")
    stamp = "2026-10-05T00:00:00+09:00"
    real._execute(
        "INSERT INTO users (user_id, username, email, password, is_active, created_at, updated_at) "
        "VALUES (?, ?, ?, 'x', 1, ?, ?)", [USER, USER, f"{USER}@example.com", stamp, stamp])
    real._execute(
        "INSERT OR IGNORE INTO projects (project_id, project_name, created_at, updated_at) "
        "VALUES (?, 'FlowGate', ?, ?)", [PROJECT, stamp, stamp])
    real._execute(
        "INSERT INTO groups (group_id, project_id, module, title, status, created_at, updated_at) "
        "VALUES (?, ?, 'default', '0180 admission', 'OPEN', ?, ?)", [GROUP, PROJECT, stamp, stamp])
    return real


@pytest.fixture
def edges(store, repo, monkeypatch):
    """Provider chain, worktree/source-sync checks and lease rows -- nothing else."""
    monkeypatch.setattr(git_service, "effective_src_root_ex", lambda project, group: (repo, "worktree"))
    monkeypatch.setattr(git_service, "_emit", lambda *args, **kwargs: None)
    monkeypatch.setattr(git_service, "get_branch_merge_group_claim", lambda group_id: None)
    provider = {"id": "p1", "name": "fake-api", "exec_type": "api", "kind": "openai",
                "api_base_url": "https://api.example", "api_model": "test-model"}
    monkeypatch.setattr(ai_settings_service, "resolve_effective",
                        lambda project: {"ok": True, "source": "test", "providers": [provider],
                                         "registered_count": 1})
    monkeypatch.setattr(provider_capability_service, "capability_finding", lambda *args: None)
    monkeypatch.setattr(admission.db_docs, "get_by_id", lambda doc_id: {"type": "CH", "branch": "main"})
    monkeypatch.setattr(admission.db_docs, "get_group_max_seq", lambda group_id: 0)
    monkeypatch.setattr(admission, "_refuse_frozen_group", lambda group_id: None)
    monkeypatch.setattr(ai_invoke_service, "_require_group_worktree", lambda *a, **k: None)
    monkeypatch.setattr(ai_invoke_service, "_ensure_initial_source_sync", lambda *a, **k: None)
    monkeypatch.setattr(storage_paths, "resolve_project_src_root", lambda project, branch, *, group_id: repo)
    leases = admission.db_group_ai_leases
    monkeypatch.setattr(leases, "get_active", lambda group_id: None)
    monkeypatch.setattr(leases, "acquire", lambda **kw: {"run_id": kw["run_id"], "state": "acquiring"})
    monkeypatch.setattr(leases, "activate", lambda *a, **k: {"state": "active"})
    monkeypatch.setattr(leases, "release", lambda *a, **k: None)
    monkeypatch.setattr(ai_invoke_service, "_runs", {})


class Counts:
    def __init__(self, monkeypatch):
        self.calls = {"claim": 0, "commit": 0, "rollback": 0}
        for name in self.calls:
            real = getattr(db_source_access, name)
            monkeypatch.setattr(db_source_access, name, self._wrap(name, real))

    def _wrap(self, name, real):
        def counted(*args, **kwargs):
            self.calls[name] += 1
            return real(*args, **kwargs)
        return counted


class Worker:
    """Stands in for worker._worker: waits for the REAL gate, then asks for commands."""

    def __init__(self, monkeypatch, count: int = len(COMMANDS)):
        self.done = threading.Event()
        self.gate_state = None
        self.outcomes: list[tuple[int, dict]] = []
        self.errors: list[Exception] = []
        self.count = count
        monkeypatch.setattr(ai_invoke_service, "_worker", self._body)

    def _body(self, run, chain, mention):
        try:
            gate = run["handoff_gate"]
            state = gate.wait_pending(30)
            if state == runtime.SEALED:
                state = gate.wait_sealed(30)
            self.gate_state = state
            if state != runtime.OPEN:
                return
            run["current_token_id"] = run.get("current_token_id") or run.get("token_id")
            for payload in COMMANDS[: self.count]:
                self.outcomes.append(commands.run_tool(run, dict(payload), 60))
        except Exception as exc:
            self.errors.append(exc)
        finally:
            self.done.set()


def _approve_when_pending(run_id: str, stop: threading.Event) -> None:
    while not stop.is_set():
        for row in commands.db.list_open_for_run(run_id):
            if row["status"] == "pending_approval":
                commands.decide(row["request_id"], {"user_id": USER}, "approve")
        stop.wait(0.05)


def _start(**extra) -> dict:
    return ai_invoke_service.start_run(
        project_id=PROJECT, module="default", group_id=GROUP, doc_ref=DOC, action_scope="chat",
        mode="single", continuation_target_seq=None, continuation_review_mode=False,
        continuation_instruction_mode=None, continuation_locale=None, issued_to=USER,
        api_base_url="http://127.0.0.1:1/flowgate/api/v1",
        mention_builder=lambda raw, scratch: "## prompt\nrun the tests\n",
        completion_oracle=lambda: True, **extra,
    )


@pytest.fixture
def spawns(monkeypatch):
    calls: list[tuple] = []
    real_spawn = commands.executor.spawn

    def spy(*args, **kwargs):
        calls.append(args)
        return real_spawn(*args, **kwargs)

    monkeypatch.setattr(commands.executor, "spawn", spy)
    return calls


def _finish(worker: Worker, run_id: str) -> None:
    assert worker.done.wait(120), "worker body did not finish"
    commands.cancel_for_run(run_id)
    assert worker.errors == []


@pytest.mark.parametrize("command_policy, statuses", [
    ("always_approve", ("succeeded",) * 3),
    ("user_approval", ("succeeded",) * 3),
    ("reject", ("rejected",) * 3),
])
def test_edit_once_run_repeats_commands_under_every_policy_with_no_extra_claim(
        store, edges, monkeypatch, spawns, command_policy, statuses):
    # Given: a stored edit_once mode whose claim this admission wins, and a command policy.
    db_source_access.upsert(USER, "edit_once", "2026-10-05T00:00:00+09:00")
    css.save_chat_settings(USER, {"command_policy": command_policy})
    counts, worker = Counts(monkeypatch), Worker(monkeypatch)
    stop = threading.Event()

    # When: the real admission seals, commits the claim and opens the gate.
    started = _start()
    run_id = started["run_id"]
    after_admission = dict(counts.calls)
    token_id = ai_invoke_service.get_run_record(run_id)["token_id"]
    threading.Thread(target=_approve_when_pending, args=(run_id, stop), daemon=True).start()
    try:
        _finish(worker, run_id)
    finally:
        stop.set()

    # Then: admission claimed+committed exactly once, the worker saw an OPEN gate, the user's
    # stored mode is back to read_only with the marker cleared, and the commands added nothing.
    assert after_admission == {"claim": 1, "commit": 1, "rollback": 0}
    assert counts.calls == after_admission
    assert worker.gate_state == runtime.OPEN
    mode_row = db_source_access.get(USER)
    assert mode_row["source_access_mode"] == "read_only" and mode_row["one_shot_token_id"] is None
    assert db_tokens.get_by_id(token_id)["source_access"] == "read_write"
    assert [status for status, _ in worker.outcomes] == [200] * 3
    assert tuple(result["status"] for _, result in worker.outcomes) == statuses
    assert {row["token_id"] for row in commands.db.list_for_doc(DOC)} == {token_id}
    assert len(spawns) == (0 if command_policy == "reject" else 3)


def test_edit_once_claim_loser_run_gets_read_and_every_command_is_refused(
        store, edges, monkeypatch, spawns):
    # Given: another invocation already won the one-shot claim and has not committed yet.
    db_source_access.upsert(USER, "edit_once", "2026-10-05T00:00:00+09:00")
    css.save_chat_settings(USER, {"command_policy": "always_approve"})
    counts, worker = Counts(monkeypatch), Worker(monkeypatch, count=2)
    winner = {}
    token_ids = iter(("tok_20261005_000180", "tok_20261005_000181"))
    monkeypatch.setattr(token_service, "_next_token_id", lambda: next(token_ids))
    real_resolve = token_service._resolve_chat_source_access

    def resolve_with_competing_issue(issued_to, token_id, created_at):
        resolved = real_resolve(issued_to, token_id, created_at)
        if resolved[1] and not winner:
            winner["started"] = True
            winner.update(token_service.issue(
                project=PROJECT, group_id=GROUP, action_scope="chat", doc_ref=DOC,
                issued_to=USER, ai_run_id="aiv_0180_winner"))
        return resolved

    # The competitor wins after this invocation reads edit_once, before its real CAS.
    monkeypatch.setattr(token_service, "_resolve_chat_source_access", resolve_with_competing_issue)

    # When: this run is admitted for real, loses the claim and gets a read token.
    started = _start()
    run_id = started["run_id"]
    token_id = ai_invoke_service.get_run_record(run_id)["token_id"]
    _finish(worker, run_id)

    # Then: the gate opened directly, the token is read, nothing was spawned or stored for it,
    # and the winner's pending claim marker was neither committed nor rolled back by this run.
    assert worker.gate_state == runtime.OPEN
    assert db_tokens.get_by_id(token_id)["source_access"] == "read"
    assert [(status, result["error"]["code"]) for status, result in worker.outcomes] == [(403, CAP_CODE)] * 2
    assert spawns == [] and commands.db.list_for_doc(DOC) == []
    assert winner["one_shot_claimed"] is True
    assert counts.calls == {"claim": 2, "commit": 0, "rollback": 0}
    assert db_source_access.get(USER)["one_shot_token_id"] == winner["token_id"]
