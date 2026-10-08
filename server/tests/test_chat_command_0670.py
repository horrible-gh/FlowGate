"""flowgate.default.0670 T0004 -- chat (CH) command execution and run change summaries.

Covers NR0003 §15:
  15.1 the command path never touches the 1회 편집 claim/commit/rollback;
  15.2 policy (default user_approval / always_approve / reject), approve/reject flow,
       results returned to the same run, exit code / stdout / stderr / timeout, cwd and
       worktree binding;
  15.3 FlowGate-computed run changes (clean edit, re-edit of an already dirty file,
       new/deleted/binary files, a commit during the run, zero changes -> nothing);
  15.4 the command policy setting is additive and never creates the legacy row.
Real git and real child processes are used where the contract is about them.
"""
from __future__ import annotations

import importlib
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
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from modules.flow_gate.db import connection  # noqa: E402
from modules.flow_gate.services import chat_command_policy as policy  # noqa: E402
from modules.flow_gate.services import chat_command_service as svc  # noqa: E402
from modules.flow_gate.services import chat_run_changes_service as changes  # noqa: E402
from modules.flow_gate.services import chat_settings_service as css  # noqa: E402
from modules.flow_gate.services import git_service  # noqa: E402
from modules.flow_gate.services import invoke_mention_service  # noqa: E402

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git binary not on PATH")

USER = "usr_0670"
OTHER = "usr_other"


# ── fixtures ────────────────────────────────────────────────────────────────

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
    (root / "a.py").write_text("one\ntwo\nthree\n", encoding="utf-8")
    (root / "keep.txt").write_text("keep\n", encoding="utf-8")
    (root / ".gitignore").write_text("ignored.log\n", encoding="utf-8")
    (root / "sub").mkdir()
    (root / "sub" / "b.txt").write_text("b\n", encoding="utf-8")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "init")
    return root


def _real_store(db_path: str) -> connection.FlowGateStore:
    from sqloader.sqlite3 import SQLiteWrapper

    store = object.__new__(connection.FlowGateStore)
    store._db = SQLiteWrapper(db_path)
    store._sq = None
    return store


@pytest.fixture
def store(migrated_sqlite_db, monkeypatch):
    path = migrated_sqlite_db("test_chat_command_0670.db")
    real = _real_store(path)
    for name in ("modules.flow_gate.db.connection", "modules.flow_gate.db.chat_command_requests",
                 "modules.flow_gate.db.user_chat_command_policy", "modules.flow_gate.db.ai_run_source_changes",
                 "modules.flow_gate.db.user_chat_settings", "modules.flow_gate.db.user_chat_source_access",
                 "modules.flow_gate.services.chat_settings_service"):
        monkeypatch.setattr(importlib.import_module(name), "get_store", lambda real=real: real)
    for user in (USER, OTHER):
        real._execute(
            "INSERT INTO users (user_id, username, email, password, is_active, created_at, updated_at) "
            "VALUES (?, ?, ?, 'x', 1, '2026-10-05T00:00:00+09:00', '2026-10-05T00:00:00+09:00')",
            [user, user, f"{user}@example.com"],
        )
    return real


@pytest.fixture
def bound(repo, monkeypatch):
    """The group resolves to the temp repo as its managed worktree; SSE is silenced."""
    monkeypatch.setattr(git_service, "effective_src_root_ex", lambda project, group: (repo, "worktree"))
    events: list[tuple[str, dict]] = []
    monkeypatch.setattr(git_service, "_emit", lambda kind, project, group, payload: events.append((kind, payload)))
    return events


@pytest.fixture
def no_source_access(monkeypatch):
    """15.1: any 1회 편집 claim/commit/rollback from the command path is a failure."""
    from modules.flow_gate.db import user_chat_source_access as sa

    for name in ("claim", "commit", "rollback"):
        monkeypatch.setattr(sa, name, lambda *a, _n=name, **k: pytest.fail(f"source access {_n} touched"))


def _run(repo: Path, **extra) -> dict:
    run = {
        "run_id": f"aiv_{time.monotonic_ns()}", "project_id": "flowgate", "group_id": "flowgate.default.0670",
        "doc_ref": "flowgate.default.0670.0009-CH", "action_scope": "chat", "status": "running",
        "issued_to": USER, "provider": {"name": "Test Model"}, "source_root": str(repo),
        "cancel_event": threading.Event(), "token_id": "tok_1",
    }
    run.update(extra)
    return run


@pytest.fixture
def live(monkeypatch):
    """Runs the service can look up as live (ai_invoke_service.get_run_record)."""
    registry: dict[str, dict] = {}
    from modules.flow_gate.services import ai_invoke_service

    monkeypatch.setattr(ai_invoke_service, "get_run_record", lambda run_id: registry.get(run_id))
    return registry


def _set_policy(value: str) -> None:
    css.save_chat_settings(USER, {"command_policy": value})


# ── 1. policy (no DB, no process) ───────────────────────────────────────────

class TestPolicy:
    def test_structured_argv_only(self):
        cmd = policy.validate_request({"program": "pytest", "args": ["-q", "tests/x.py"]})
        assert (cmd.program, cmd.args, cmd.cwd, cmd.timeout_seconds) == ("pytest", ("-q", "tests/x.py"), ".", 300)
        assert cmd.category == "test_build"

    @pytest.mark.parametrize("payload, code", [
        ({"program": "bash", "args": ["-c", "ls"]}, "chat_command_program_denied"),
        ({"program": "powershell"}, "chat_command_program_denied"),
        ({"program": "curl", "args": ["http://x"]}, "chat_command_program_denied"),
        ({"program": "git", "args": ["log", "|", "head"]}, "chat_command_shell_operator"),
        ({"program": "python", "args": ["-c", "print(1)"]}, "chat_command_inline_execution"),
        ({"program": "node", "args": ["-e", "1"]}, "chat_command_inline_execution"),
        ({"program": "/bin/ls"}, "chat_command_invalid_program"),
        ({"program": "git", "args": ["-c", "alias.x=!sh", "x"]}, "chat_command_inline_execution"),
        ({"program": "git", "args": ["-C", "/elsewhere", "status"]}, "chat_command_invalid_cwd"),
        ({"program": "git", "timeout_seconds": 0}, "chat_command_invalid_timeout"),
        ({"program": "git", "timeout_seconds": 99999}, "chat_command_invalid_timeout"),
        ({"program": "git", "shell": True}, "chat_command_invalid_request"),
    ])
    def test_refusals(self, payload, code):
        with pytest.raises(policy.ChatCommandPolicyError) as info:
            policy.validate_request(payload)
        assert info.value.code == code

    def test_git_is_allowed_unlike_self_check(self):
        assert policy.validate_request({"program": "git", "args": ["status"]}).category == "git_read"
        commit = policy.validate_request({"program": "git", "args": ["-c", "user.name=x", "commit", "-m", "m"]})
        assert (commit.category, commit.high_impact) == ("git_write", False)
        push = policy.validate_request({"program": "git", "args": ["push"]})
        assert (push.category, push.high_impact) == ("git_write", True)

    def test_self_check_policy_is_unchanged(self, repo):
        from modules.flow_gate.services import tr_self_check_policy as base

        path_value, _ = base.controlled_path(repo)
        with pytest.raises(base.PolicyError) as info:
            base.resolve_command("git", ["status"], repo, path_value)
        assert info.value.code == "selfcheck_program_denied"

    def test_cwd_must_stay_inside_the_worktree(self, repo):
        for cwd in ("..", "../..", str(repo.parent)):
            with pytest.raises(policy.ChatCommandPolicyError) as info:
                policy.resolve(policy.validate_request({"program": "git", "args": ["status"], "cwd": cwd}), repo)
            assert info.value.code == "chat_command_invalid_cwd"
        _resolved, cwd, _path = policy.resolve(
            policy.validate_request({"program": "git", "args": ["status"], "cwd": "sub"}), repo)
        assert cwd == (repo / "sub").resolve()

    def test_display_is_the_full_command(self):
        assert policy.display("git", ["commit", "-m", "two words"]) == "git commit -m 'two words'"


# ── 2. settings (15.4) ──────────────────────────────────────────────────────

class TestSetting:
    def test_default_is_user_approval(self, store):
        settings, is_default = css.resolve_chat_settings(USER)
        assert settings["command_policy"] == "user_approval" and is_default is True
        assert css.settings_response(USER)["domain"]["command_policy"] == ["always_approve", "user_approval", "reject"]

    def test_policy_only_patch_never_creates_the_legacy_row(self, store):
        body = css.save_chat_settings(USER, {"command_policy": "always_approve"})
        assert body["settings"]["command_policy"] == "always_approve"
        assert body["is_default"] is True  # the [on send] hand-over still sees "never saved"
        assert store._fetch_one("SELECT * FROM user_chat_settings WHERE user_id = ?", [USER]) is None

    def test_mixed_patch_keeps_existing_fields(self, store):
        css.save_chat_settings(USER, {"send_action": "invoke_ai", "context_mode": "all", "command_policy": "reject"})
        settings, is_default = css.resolve_chat_settings(USER)
        assert (settings["send_action"], settings["context_mode"], settings["command_policy"]) == ("invoke_ai", "all", "reject")
        assert is_default is False

    def test_unknown_value_is_refused(self, store):
        with pytest.raises(css.ChatSettingsError) as info:
            css.save_chat_settings(USER, {"command_policy": "sometimes"})
        assert info.value.field == "command_policy"


# ── 3. request lifecycle (15.1 / 15.2) ──────────────────────────────────────

class TestLifecycle:
    def test_default_waits_for_the_user_and_creates_no_process(self, store, bound, live, repo, monkeypatch, no_source_access):
        monkeypatch.setattr(svc.executor, "spawn", lambda *a, **k: pytest.fail("no process before approval"))
        run = _run(repo)
        live[run["run_id"]] = run
        row = svc.create_request(run, {"program": "git", "args": ["status"]})
        assert row["status"] == "pending_approval" and row["policy"] == "user_approval"
        assert row["provider_name"] == "Test Model"
        assert [kind for kind, _ in bound] == ["chat_command_updated"]
        assert svc.wait_terminal(row["request_id"], 0.2)["status"] == "pending_approval"

    def test_approve_runs_in_the_worktree_and_returns_exit_code_and_output(self, store, bound, live, repo, no_source_access):
        run = _run(repo)
        live[run["run_id"]] = run
        row = svc.create_request(run, {"program": "git", "args": ["rev-parse", "--show-toplevel"]})
        svc.decide(row["request_id"], {"user_id": USER}, "approve")
        done = svc.wait_terminal(row["request_id"], 60)
        result = svc.ai_result(done)
        assert result["status"] == "succeeded" and result["exit_code"] == 0
        assert Path(result["stdout"].strip()).resolve() == repo.resolve()
        assert result["rejected"] is False and result["timed_out"] is False
        assert done["decision_source"] == "user" and done["decided_by"] == USER

    def test_failed_command_keeps_stderr_and_exit_code(self, store, bound, live, repo):
        _set_policy("always_approve")
        run = _run(repo)
        live[run["run_id"]] = run
        row = svc.create_request(run, {"program": "git", "args": ["rev-parse", "--verify", "no-such-ref"]})
        done = svc.wait_terminal(row["request_id"], 60)
        assert done["status"] == "failed" and done["exit_code"] not in (None, 0)
        assert done["decision_source"] == "policy"
        assert "no-such-ref" in svc.ai_result(done)["stderr"] or done["stderr_tail"]

    def test_policy_reject_never_spawns_and_tells_the_model(self, store, bound, live, repo, monkeypatch):
        monkeypatch.setattr(svc.executor, "spawn", lambda *a, **k: pytest.fail("reject must not spawn"))
        _set_policy("reject")
        run = _run(repo)
        live[run["run_id"]] = run
        status, result = svc.run_tool(run, {"program": "git", "args": ["status"]}, 60)
        assert status == 200
        assert (result["status"], result["rejected"], result["rejected_by"]) == ("rejected", True, "policy")

    def test_user_reject_is_returned_to_the_same_waiting_call(self, store, bound, live, repo, monkeypatch):
        monkeypatch.setattr(svc.executor, "spawn", lambda *a, **k: pytest.fail("rejected must not spawn"))
        run = _run(repo)
        live[run["run_id"]] = run

        def reject_soon():
            for _ in range(100):
                rows = svc.db.list_open_for_run(run["run_id"])
                if rows:
                    svc.decide(rows[0]["request_id"], {"user_id": USER}, "reject")
                    return
                time.sleep(0.05)

        threading.Thread(target=reject_soon, daemon=True).start()
        status, result = svc.run_tool(run, {"program": "git", "args": ["status"]}, 60)
        assert status == 200
        assert (result["status"], result["rejected_by"]) == ("rejected", "user")

    def test_only_the_invoking_user_or_an_admin_decides(self, store, bound, live, repo):
        run = _run(repo)
        live[run["run_id"]] = run
        row = svc.create_request(run, {"program": "git", "args": ["status"]})
        with pytest.raises(svc.ChatCommandError) as info:
            svc.decide(row["request_id"], {"user_id": OTHER}, "approve")
        assert info.value.status == 403
        svc.decide(row["request_id"], {"user_id": OTHER, "is_admin": 1}, "reject")
        with pytest.raises(svc.ChatCommandError) as info:
            svc.decide(row["request_id"], {"user_id": USER}, "approve")
        assert info.value.code == "chat_command_already_decided"

    def test_timeout_is_reported(self, store, bound, live, repo):
        python = shutil.which("python") or shutil.which("python3")
        if not python or "WindowsApps" in python:
            pytest.skip("a real python executable is needed on PATH")
        (repo / "sleep.py").write_text("import time\ntime.sleep(20)\n", encoding="utf-8")
        _set_policy("always_approve")
        run = _run(repo)
        live[run["run_id"]] = run
        name = Path(python).stem
        row = svc.create_request(run, {"program": name, "args": ["sleep.py"], "timeout_seconds": 1})
        done = svc.wait_terminal(row["request_id"], 30)
        assert done["status"] == "timed_out" and svc.ai_result(done)["timed_out"] is True

    def test_worktree_binding_is_checked(self, store, bound, live, repo, tmp_path, monkeypatch):
        other = tmp_path / "other"
        other.mkdir()
        with pytest.raises(svc.ChatCommandError) as info:
            svc.create_request(_run(repo, source_root=str(other)), {"program": "git", "args": ["status"]})
        assert info.value.code == "chat_command_worktree_mismatch"
        monkeypatch.setattr(git_service, "effective_src_root_ex", lambda p, g: (repo, "base_fallback"))
        with pytest.raises(svc.ChatCommandError) as info:
            svc.create_request(_run(repo), {"program": "git", "args": ["status"]})
        assert info.value.code == "chat_command_worktree_unavailable"

    def test_only_a_live_chat_run_may_ask(self, store, bound, repo):
        for run in (_run(repo, action_scope="edit"), _run(repo, status="finished", finished_at="x")):
            with pytest.raises(svc.ChatCommandError):
                svc.create_request(run, {"program": "git", "args": ["status"]})

    def test_finalize_closes_what_the_run_left_open(self, store, bound, live, repo):
        run = _run(repo)
        live[run["run_id"]] = run
        row = svc.create_request(run, {"program": "git", "args": ["status"]})
        svc.cancel_for_run(run["run_id"])
        assert svc.db.get(row["request_id"])["status"] == "cancelled"
        with pytest.raises(svc.ChatCommandError) as info:  # a finalized run takes no new request
            svc.create_request(run, {"program": "git", "args": ["status"]})
        assert info.value.code == "chat_command_run_finished"

    @pytest.mark.parametrize("stop, code", [
        (lambda run: run["cancel_event"].set(), "chat_command_run_cancelled"),
        (lambda run: run.update(status="finished"), "chat_command_run_finished"),
        (lambda run: svc.cancel_for_run(run["run_id"]), "chat_command_run_finished"),
    ])
    def test_approve_is_refused_once_the_run_is_stopping(self, store, bound, live, repo, monkeypatch, stop, code):
        monkeypatch.setattr(svc.executor, "spawn", lambda *a, **k: pytest.fail("a stopping run must not spawn"))
        run = _run(repo)
        live[run["run_id"]] = run
        row = svc.create_request(run, {"program": "git", "args": ["status"]})
        stop(run)
        with pytest.raises(svc.ChatCommandError) as info:
            svc.decide(row["request_id"], {"user_id": USER}, "approve")
        assert info.value.code == code
        assert svc.db.get(row["request_id"])["status"] == "cancelled"

    @staticmethod
    def _gate_pre_spawn(monkeypatch):
        """Hold the executor thread after it moved the row to running, before any process."""
        entered, release, spawned = threading.Event(), threading.Event(), []
        real_root = svc.worktree_root

        def gated(*args, **kwargs):
            if threading.current_thread().name.startswith("chat-command-"):
                entered.set()
                assert release.wait(30)
            return real_root(*args, **kwargs)

        def spawn(*args, **kwargs):
            spawned.append(args)
            raise RuntimeError("no process may be spawned for a stopped run")

        monkeypatch.setattr(svc, "worktree_root", gated)
        monkeypatch.setattr(svc.executor, "spawn", spawn)
        return entered, release, spawned

    def test_finalize_waits_out_the_pre_spawn_window(self, store, bound, live, repo, monkeypatch):
        entered, release, spawned = self._gate_pre_spawn(monkeypatch)
        _set_policy("always_approve")
        run = _run(repo)
        live[run["run_id"]] = run
        row = svc.create_request(run, {"program": "git", "args": ["status"]})
        assert entered.wait(30)
        assert svc.db.get(row["request_id"])["status"] == "running"
        assert row["request_id"] not in svc._controls  # running, but no process control yet
        returned = threading.Event()
        threading.Thread(target=lambda: (svc.cancel_for_run(run["run_id"]), returned.set()), daemon=True).start()
        assert not returned.wait(0.5)  # finalization does not return while the thread may still spawn
        release.set()
        assert returned.wait(30)
        done = svc.db.get(row["request_id"])
        assert (done["status"], done["error_code"]) == ("cancelled", "chat_command_run_finished")
        assert spawned == [] and row["request_id"] not in svc._executions

    def test_run_cancellation_after_approval_stops_before_spawn(self, store, bound, live, repo, monkeypatch):
        entered, release, spawned = self._gate_pre_spawn(monkeypatch)
        run = _run(repo)
        live[run["run_id"]] = run
        row = svc.create_request(run, {"program": "git", "args": ["status"]})
        svc.decide(row["request_id"], {"user_id": USER}, "approve")
        assert entered.wait(30)
        run["cancel_event"].set()  # the user stopped the run while the command was starting
        release.set()
        done = svc.wait_terminal(row["request_id"], 30)
        assert (done["status"], done["error_code"]) == ("cancelled", "chat_command_run_cancelled")
        assert spawned == []

    def test_end_snapshot_is_taken_only_after_the_running_command_is_gone(self, store, bound, live, repo, monkeypatch):
        python = shutil.which("python") or shutil.which("python3")
        if not python or "WindowsApps" in python:
            pytest.skip("a real python executable is needed on PATH")
        from modules.flow_gate.services.ai_invoke import finalize

        (repo / "late.py").write_text(
            "import pathlib, time\ntime.sleep(15)\npathlib.Path('late.txt').write_text('late')\n", encoding="utf-8")
        _set_policy("always_approve")
        run = _run(repo)
        live[run["run_id"]] = run
        row = svc.create_request(run, {"program": Path(python).stem, "args": ["late.py"], "timeout_seconds": 60})
        deadline = time.monotonic() + 30
        while row["request_id"] not in svc._controls:  # the process exists
            assert time.monotonic() < deadline
            time.sleep(0.02)
        seen = {}

        def snapshot(finished_run):
            seen["status"] = svc.db.get(row["request_id"])["status"]
            seen["executing"] = row["request_id"] in svc._executions or row["request_id"] in svc._controls

        monkeypatch.setattr(changes, "finalize_run", snapshot)
        started = time.monotonic()
        finalize._finalize_chat_run(run)
        assert seen == {"status": "cancelled", "executing": False}
        assert time.monotonic() - started < 15  # killed, not waited out
        time.sleep(1)
        assert not (repo / "late.txt").exists()

    @staticmethod
    def _pause_between_live_check_and_spawn(monkeypatch):
        """Hold the executor thread right after its last live check passed, before the spawn.

        ``order`` gets "spawn"/"kill"; ``at_spawn`` records the run's cancellation state at
        the instant the process would be created.
        """
        checked, release, order, at_spawn = threading.Event(), threading.Event(), [], []
        real_refusal = svc._live_refusal

        def paused(run_id):
            refusal = real_refusal(run_id)
            if refusal is None and threading.current_thread().name.startswith("chat-command-"):
                checked.set()
                assert release.wait(30)
            return refusal

        class Control:
            def cancel(self):
                order.append("kill")

            def close(self):
                pass

        def spawn(*args, **kwargs):
            run_id = svc.db.get(args[-1])["ai_run_id"]
            run = svc._live_run(run_id) or {}
            at_spawn.append({"status": run.get("status"), "cancel_event": run["cancel_event"].is_set(),
                             "closed": svc._run_closed(run_id)})
            order.append("spawn")
            return Control(), {}

        def wait(control, timeout_seconds, cancelled):
            assert cancelled.wait(30)
            return svc.executor.ExecutionResult(None, False, True, "", "")

        monkeypatch.setattr(svc, "_live_refusal", paused)
        monkeypatch.setattr(svc.executor, "spawn", spawn)
        monkeypatch.setattr(svc.executor, "wait", wait)
        return checked, release, order, at_spawn

    def test_run_cancel_cannot_begin_between_the_live_check_and_the_spawn(self, store, bound, live, repo, monkeypatch):
        from modules.flow_gate.services.ai_invoke import chain

        checked, release, order, at_spawn = self._pause_between_live_check_and_spawn(monkeypatch)
        run = _run(repo)
        live[run["run_id"]] = run
        row = svc.create_request(run, {"program": "git", "args": ["status"]})
        svc.decide(row["request_id"], {"user_id": USER}, "approve")
        assert checked.wait(30)  # the live check passed; the spawn has not happened yet
        returned = threading.Event()
        threading.Thread(target=lambda: (chain.cancel_run(run["run_id"]), returned.set()), daemon=True).start()
        assert not returned.wait(0.5)
        # The stop waits for the launch gate: the cancellation has not begun inside the
        # window -- the run is not announced cancelling, not closed, cancel_event not set.
        assert run["status"] == "running"
        assert not run["cancel_event"].is_set() and not svc._run_closed(run["run_id"])
        assert order == []
        release.set()
        assert returned.wait(30) and run["cancel_event"].is_set() and run["status"] == "cancelling"
        done = svc.wait_terminal(row["request_id"], 30)
        assert done["status"] == "cancelled"
        # The process was created strictly before the cancellation began, and the stop killed it.
        assert at_spawn == [{"status": "running", "cancel_event": False, "closed": False}]
        assert order[0] == "spawn" and "kill" in order[1:]

    def test_no_process_starts_once_the_run_is_cancelling(self, store, bound, live, repo, monkeypatch):
        """Every launch that reaches its live check after cancel_run began spawns nothing."""
        from modules.flow_gate.services.ai_invoke import chain

        checked, release, order, at_spawn = self._pause_between_live_check_and_spawn(monkeypatch)
        release.set()  # do not hold launches; only record what the spawn would see
        run = _run(repo)
        live[run["run_id"]] = run
        states = []
        real_close = svc._close_run

        def close_and_observe(run_id, on_closed=None):
            def observed():
                if on_closed is not None:
                    on_closed()
                states.append(run["status"])
            real_close(run_id, observed)

        monkeypatch.setattr(svc, "_close_run", close_and_observe)
        assert chain.cancel_run(run["run_id"])["status"] == "cancelling"
        # Announced only under the gate, after the run was closed.
        assert states == ["cancelling"] and svc._run_closed(run["run_id"])
        with pytest.raises(svc.ChatCommandError):
            svc.create_request(run, {"program": "git", "args": ["status"]})
        assert at_spawn == [] and order == []

    def test_finalize_cannot_close_between_the_live_check_and_the_spawn(self, store, bound, live, repo, monkeypatch):
        checked, release, order, _at_spawn = self._pause_between_live_check_and_spawn(monkeypatch)
        _set_policy("always_approve")
        run = _run(repo)
        live[run["run_id"]] = run
        row = svc.create_request(run, {"program": "git", "args": ["status"]})
        assert checked.wait(30)
        returned = threading.Event()
        threading.Thread(target=lambda: (svc.cancel_for_run(run["run_id"]), returned.set()), daemon=True).start()
        assert not returned.wait(0.5)
        assert not svc._run_closed(run["run_id"]) and order == []
        release.set()
        assert returned.wait(30)
        done = svc.db.get(row["request_id"])
        assert done["status"] == "cancelled"
        assert order[0] == "spawn" and "kill" in order[1:]
        assert row["request_id"] not in svc._executions and row["request_id"] not in svc._controls

    def test_run_cancel_before_the_live_check_spawns_nothing(self, store, bound, live, repo, monkeypatch):
        from modules.flow_gate.services.ai_invoke import chain

        entered, release, spawned = self._gate_pre_spawn(monkeypatch)
        run = _run(repo)
        live[run["run_id"]] = run
        row = svc.create_request(run, {"program": "git", "args": ["status"]})
        svc.decide(row["request_id"], {"user_id": USER}, "approve")
        assert entered.wait(30)
        assert chain.cancel_run(run["run_id"])["status"] == "cancelling"  # no launch holds the gate
        release.set()
        done = svc.wait_terminal(row["request_id"], 30)
        assert done["status"] == "cancelled" and done["error_code"] in (
            "chat_command_run_finished", "chat_command_run_cancelled")
        assert spawned == []

    def test_cli_token_must_match_the_run(self, repo):
        run = _run(repo)
        good = {"project_id": "flowgate", "group_id": run["group_id"], "ai_run_id": run["run_id"], "token_id": "tok_1"}
        assert svc.authorize_token(good, run) is run
        for bad in ({**good, "group_id": "flowgate.default.0001"}, {**good, "token_id": "tok_2"}, {**good, "ai_run_id": None}):
            with pytest.raises(svc.ChatCommandError):
                svc.authorize_token(bad, run)


# ── 4. API loop continuation (15.2 "같은 run에서 결과 반환 후 AI loop 계속") ─────

def test_api_chat_runs_command_then_keeps_going_in_the_same_run(monkeypatch, store, bound, live, repo):
    from modules.flow_gate.services import ai_invoke_service as ai
    from modules.flow_gate.services.ai_invoke import worker  # noqa: F401

    _set_policy("always_approve")
    run = _run(repo, docs_target=0, raw_token="raw", mode="single", timed_out=False,
               api_base_url="http://127.0.0.1/flowgate/api/v1", started_mono=time.monotonic())
    live[run["run_id"]] = run
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
    assert ai._api_execute({"id": "p", "kind": "openai", "api_base_url": "https://x", "api_model": "m"},
                           "prompt", run) == ("started_ok", None)
    assert calls["n"] == 2
    tool_results = [m for m in calls["conversations"][1] if m.get("role") == "tool"]
    assert any('"status": "succeeded"' in str(m.get("content")) and '"exit_code": 0' in str(m.get("content"))
               for m in tool_results)


# ── 5. run changes (15.3) ───────────────────────────────────────────────────

def _index_bytes(repo: Path) -> bytes:
    return (repo / ".git" / "index").read_bytes()


class TestRunChanges:
    def _finish(self, run: dict):
        return changes.finalize_run(run)

    def test_clean_tree_one_file_edit(self, store, bound, repo):
        before = _index_bytes(repo)
        run = _run(repo, source_start_tree=changes.capture_tree(repo))
        (repo / "a.py").write_text("one\nTWO\nthree\nfour\n", encoding="utf-8")
        row = self._finish(run)
        assert _index_bytes(repo) == before  # the real index was never written
        assert row["files"] == [{"path": "a.py", "status": "M", "insertions": 2, "deletions": 1}]
        assert (row["insertions"], row["deletions"], row["files_changed"]) == (2, 1, 1)
        assert any(kind == "chat_run_changes" for kind, _ in bound)

    def test_already_dirty_file_edited_again_is_counted(self, store, bound, repo):
        (repo / "a.py").write_text("one\ntwo\nthree\ndirty\n", encoding="utf-8")
        run = _run(repo, source_start_tree=changes.capture_tree(repo))
        (repo / "a.py").write_text("one\ntwo\nthree\ndirty\nmore\nlines\n", encoding="utf-8")
        row = self._finish(run)
        assert row["files"] == [{"path": "a.py", "status": "M", "insertions": 2, "deletions": 0}]

    def test_added_deleted_binary_and_ignored(self, store, bound, repo):
        (repo / "a.py").write_text("one\ntwo\nthree\npre-dirty\n", encoding="utf-8")
        run = _run(repo, source_start_tree=changes.capture_tree(repo))
        (repo / "new.txt").write_text("x\ny\n", encoding="utf-8")
        (repo / "keep.txt").unlink()
        (repo / "blob.bin").write_bytes(b"\x00\x01\x02")
        (repo / "ignored.log").write_text("noise\n", encoding="utf-8")
        row = self._finish(run)
        by_path = {f["path"]: f for f in row["files"]}
        assert set(by_path) == {"new.txt", "keep.txt", "blob.bin"}  # a.py was dirty before and untouched
        assert by_path["new.txt"] == {"path": "new.txt", "status": "A", "insertions": 2, "deletions": 0}
        assert by_path["keep.txt"]["status"] == "D"
        assert (by_path["blob.bin"]["insertions"], by_path["blob.bin"]["deletions"]) == (None, None)

    def test_commit_during_the_run_does_not_hide_the_change(self, store, bound, repo):
        run = _run(repo, source_start_tree=changes.capture_tree(repo))
        (repo / "sub" / "b.txt").write_text("b\nc\n", encoding="utf-8")
        _git(repo, "add", "-A")
        _git(repo, "commit", "-q", "-m", "during run")
        row = self._finish(run)
        assert row["files"] == [{"path": "sub/b.txt", "status": "M", "insertions": 1, "deletions": 0}]

    def test_no_change_stores_and_announces_nothing(self, store, bound, repo):
        (repo / "a.py").write_text("dirty before\n", encoding="utf-8")
        run = _run(repo, source_start_tree=changes.capture_tree(repo))
        assert self._finish(run) is None
        assert changes.list_for_doc(run["doc_ref"]) == []
        assert not any(kind == "chat_run_changes" for kind, _ in bound)

    def test_other_scopes_take_no_snapshot(self, repo):
        assert changes.start_snapshot("edit", repo) is None
        assert changes.start_snapshot("chat", repo)

    def test_file_diff_reads_both_snapshots(self, store, bound, repo):
        run = _run(repo, source_start_tree=changes.capture_tree(repo))
        (repo / "a.py").write_text("one\nTWO\nthree\n", encoding="utf-8")
        self._finish(run)
        (repo / "a.py").write_text("changed again after the run\n", encoding="utf-8")
        data = changes.file_diff(run["doc_ref"], run["run_id"], "a.py")["data"]
        assert data["status"] == "M"
        assert data["old"]["content"] == "one\ntwo\nthree\n"
        assert data["new"]["content"] == "one\nTWO\nthree\n"  # the run's end, not the live file
        with pytest.raises(git_service.GitServiceError):
            changes.file_diff(run["doc_ref"], run["run_id"], "keep.txt")


# ── 6. CLI mention ──────────────────────────────────────────────────────────

def test_command_section_names_the_token_bound_endpoint():
    text = invoke_mention_service._command_section(base="http://h/flowgate/api/v1", raw_token="RAW")
    assert "POST http://h/flowgate/api/v1/chat-commands" in text
    assert "GET http://h/flowgate/api/v1/chat-commands/<request_id>?wait=60" in text
    assert "Authorization: Bearer RAW" in text


def test_only_the_invoke_mention_offers_commands(monkeypatch):
    monkeypatch.setattr(invoke_mention_service.chat_settings_service, "resolve_chat_settings_safe",
                        lambda _u: {"context_mode": "all", "context_turns": 20})
    monkeypatch.setattr(invoke_mention_service, "_chat_lookup_sections", lambda **_: [])
    kwargs = dict(doc_id="flowgate.default.0670.0009-CH", project="flowgate", module="default",
                  group_name="flowgate.default.0670", raw_token="RAW", token_id="tok",
                  api_base_url="http://h/flowgate/api/v1")
    assert "## Command execution" not in invoke_mention_service.build_conversation_mention(**kwargs)
    assert "## Command execution" in invoke_mention_service.build_conversation_mention(**kwargs, command_execution=True)


# ── 7. HTTP layer ───────────────────────────────────────────────────────────

def _route_client(user: dict):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from modules.flow_gate.api.v1 import chat_command_routes
    from modules.flow_gate.auth.middleware import get_current_user

    app = FastAPI()
    app.include_router(chat_command_routes.router, prefix="/api/v1")
    app.dependency_overrides[get_current_user] = lambda: user
    return TestClient(app)


@pytest.fixture
def readable_doc(monkeypatch):
    from modules.flow_gate.api.v1 import chat_command_routes

    monkeypatch.setattr(chat_command_routes.db_documents, "get_by_id",
                        lambda doc_id: {"doc_id": doc_id, "type_code": "CH", "project_id": "flowgate"})
    monkeypatch.setattr(chat_command_routes, "_has_permission", lambda user, perm, project: True)


def test_routes_stay_synchronous():
    import inspect

    from modules.flow_gate.api.v1 import chat_command_routes as routes

    for handler in (routes.create_chat_command, routes.read_chat_command, routes.decide_chat_command,
                    routes.chat_activity, routes.chat_run_file_diff):
        assert not inspect.iscoroutinefunction(handler)


def test_decision_route_and_activity(store, bound, live, repo, readable_doc):
    run = _run(repo)
    live[run["run_id"]] = run
    row = svc.create_request(run, {"program": "git", "args": ["status"]})
    other = _route_client({"user_id": OTHER})
    denied = other.post(f"/api/v1/chat-commands/{row['request_id']}/decision", json={"decision": "approve"})
    assert denied.status_code == 403 and denied.json()["error"]["code"] == "chat_command_decision_forbidden"
    owner = _route_client({"user_id": USER})
    activity = owner.get(f"/api/v1/chat-activity/{run['doc_ref']}").json()
    assert [c["status"] for c in activity["commands"]] == ["pending_approval"]
    assert activity["commands"][0]["command"] == "git status" and activity["changes"] == []
    rejected = owner.post(f"/api/v1/chat-commands/{row['request_id']}/decision", json={"decision": "reject"})
    assert rejected.status_code == 200 and rejected.json()["request"]["status"] == "rejected"


def test_worker_routes_require_a_live_chat_run_token(store):
    client = _route_client({"user_id": USER})
    response = client.post("/api/v1/chat-commands", json={"program": "git", "args": ["status"]},
                           headers={"Authorization": "Bearer not-a-token"})
    assert response.status_code == 403 and response.json()["error"]["code"] == "chat_command_forbidden"
