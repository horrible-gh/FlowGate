"""Approval wait, run time budget and the no-progress watchdog (flowgate.v02.0032 T#1).

D0006 §3.5 (limits), §3.5.1 (``chat_run_budget_sec`` = hard-ceiling remainder - safety,
never ``_remaining_sec``), §3.5.2 rules 1-7, §3.6 (progress while waiting/running) and
completion conditions 5 and 7, including the boundary cases of a run that has been
working for more than its 30-minute hop budget.
"""
from __future__ import annotations

import sys
import threading
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _fg_conversation_support import (  # noqa: E402
    USER, FakeExecutor, bind_worktree, clean_commands_state, clean_runs, fg_store, init_worktree,  # noqa: F401
    make_run, seed, set_policy, use_executor, wait_until,
)

from flowgate.conversation import commands  # noqa: E402
from flowgate.conversation import contracts as c  # noqa: E402
from flowgate.conversation import legacy_adapter  # noqa: E402
from flowgate.conversation import repository as repo  # noqa: E402
from modules.flow_gate.services import ai_invoke_service as svc  # noqa: E402

PAST_HOP_BUDGET_SEC = 31 * 60  # the hop's timeout_sec (30 min) is already used up


@pytest.fixture()
def env(fg_store, tmp_path, monkeypatch, clean_commands_state, clean_runs):
    seed(fg_store)
    root = init_worktree(tmp_path / "wt")
    bind_worktree(monkeypatch, root)
    executor = FakeExecutor()
    use_executor(monkeypatch, executor)
    set_policy(monkeypatch, c.POLICY_USER_APPROVAL)
    return {"root": root, "executor": executor}


def _payload(**extra):
    body = {"program": "git", "args": ["status"], "timeout_seconds": 30}
    body.update(extra)
    return body


def _capture_windows(monkeypatch, result_why=commands.WAIT_DEADLINE):
    seen: list[float] = []

    def fake_wait(request_id, max_wait, run=None):
        seen.append(float(max_wait))
        return repo.get_command(request_id), result_why

    monkeypatch.setattr(commands, "wait_for", fake_wait)
    return seen


def _absolute_after(monkeypatch, first: float, then: float, switch_after: float):
    started = time.monotonic()
    monkeypatch.setattr(legacy_adapter, "absolute_remaining_sec",
                        lambda run: first if time.monotonic() - started < switch_after else then)


# ── the budget itself ────────────────────────────────────────────────────────

def test_budget_is_the_hard_ceiling_remainder_minus_safety(env):
    run = make_run(source_root=env["root"], elapsed_sec=PAST_HOP_BUDGET_SEC)
    assert svc._remaining_sec(run) < 0  # the hop budget is exhausted ...
    budget = commands.chat_run_budget_sec(run)
    expected = svc.RUN_TIMEOUT_CAP_SEC - PAST_HOP_BUDGET_SEC - c.RUN_SAFETY_SEC
    assert abs(budget - expected) < 5  # ... but the hard ceiling is not
    assert budget > c.APPROVAL_WAIT_MAX_SEC


def test_command_waiting_never_reads_the_hop_remaining_sec(env, monkeypatch):
    def forbidden(run):
        raise AssertionError("_remaining_sec must not bound a command wait")

    monkeypatch.setattr(svc, "_remaining_sec", forbidden)
    set_policy(monkeypatch, c.POLICY_ALWAYS_APPROVE)
    run = make_run(source_root=env["root"], elapsed_sec=PAST_HOP_BUDGET_SEC)
    status, result = commands.run_tool(run, _payload())
    assert status == 200 and result["status"] == c.SUCCEEDED


# ── rule 2: the approval window ──────────────────────────────────────────────

def test_approval_window_is_900s_on_a_run_past_its_hop_budget(env, monkeypatch):
    run = make_run(source_root=env["root"], elapsed_sec=PAST_HOP_BUDGET_SEC)
    seen = _capture_windows(monkeypatch)
    status, result = commands.run_tool(run, _payload())
    assert seen[0] == c.APPROVAL_WAIT_MAX_SEC == 900
    assert status == 200 and result["status"] == c.CANCELLED
    assert result["error_code"] == "chat_command_approval_wait_expired"
    assert "request it again" in result["message"]


@pytest.mark.parametrize("absolute,expected", [(904.0, 899.0), (300.0, 295.0), (905.0, 900.0), (6.0, 1.0)])
def test_approval_window_shrinks_to_the_remaining_budget(env, monkeypatch, absolute, expected):
    monkeypatch.setattr(legacy_adapter, "absolute_remaining_sec", lambda run: absolute)
    seen = _capture_windows(monkeypatch)
    commands.run_tool(make_run(source_root=env["root"]), _payload())
    assert seen[0] == pytest.approx(min(900.0, expected))


@pytest.mark.parametrize("policy", [c.POLICY_USER_APPROVAL, c.POLICY_ALWAYS_APPROVE])
@pytest.mark.parametrize("absolute", [5.0, 1.0, -30.0])
def test_no_budget_left_closes_the_request_at_creation(env, monkeypatch, policy, absolute):
    set_policy(monkeypatch, policy)
    monkeypatch.setattr(legacy_adapter, "absolute_remaining_sec", lambda run: absolute)
    status, result = commands.run_tool(make_run(source_root=env["root"]), _payload())
    assert status == 200
    assert result["status"] == c.CANCELLED and result["error_code"] == "chat_command_run_budget_exhausted"
    assert env["executor"].spawned == []
    assert repo.list_open_commands() == []


def test_the_approval_wait_really_expires(env, monkeypatch):
    monkeypatch.setattr(c, "APPROVAL_WAIT_MAX_SEC", 0.6)
    run = make_run(source_root=env["root"])
    started = time.monotonic()
    status, result = commands.run_tool(run, _payload())
    assert time.monotonic() - started < 5
    assert result["status"] == c.CANCELLED and result["error_code"] == "chat_command_approval_wait_expired"
    stored = repo.get_command(result["request_id"])
    assert stored["status"] == c.CANCELLED
    with pytest.raises(c.ConversationError) as late:
        commands.decide(result["request_id"], {"user_id": USER}, "approve")
    assert late.value.code == "chat_command_already_decided"


def test_budget_running_out_during_the_approval_wait(env, monkeypatch):
    _absolute_after(monkeypatch, first=4000.0, then=4.0, switch_after=0.4)
    status, result = commands.run_tool(make_run(source_root=env["root"]), _payload())
    assert result["status"] == c.CANCELLED and result["error_code"] == "chat_command_run_budget_exhausted"


def test_a_decision_during_the_wait_returns_to_the_same_call(env):
    run = make_run(source_root=env["root"])
    out = {}
    worker = threading.Thread(target=lambda: out.update(result=commands.run_tool(run, _payload())))
    worker.start()
    assert wait_until(lambda: bool(repo.list_open_commands_for_run(run["run_id"])))
    rid = repo.list_open_commands_for_run(run["run_id"])[0]["request_id"]
    commands.decide(rid, {"user_id": USER}, "reject")
    worker.join(10)
    status, result = out["result"]
    assert result["request_id"] == rid and result["rejected_by"] == "user"


# ── §3.6: waiting is progress ────────────────────────────────────────────────

def test_pending_wait_keeps_the_no_progress_watchdog_fed(env):
    run = make_run(source_root=env["root"], elapsed_sec=PAST_HOP_BUDGET_SEC)
    out = {}
    worker = threading.Thread(target=lambda: out.update(result=commands.run_tool(run, _payload())))
    worker.start()
    assert wait_until(lambda: bool(repo.list_open_commands_for_run(run["run_id"])))
    first = run["progress_observations"]
    time.sleep(2.5)
    assert run["progress_observations"] >= first + 2  # at least once per second
    assert run["last_progress_signal"] == "command"
    # The engine's no-progress clock is full again although the hop started 31 min ago.
    assert svc._stall_remaining_sec(run) > run["timeout_sec"] - 5
    rid = repo.list_open_commands_for_run(run["run_id"])[0]["request_id"]
    commands.decide(rid, {"user_id": USER}, "approve")
    worker.join(10)
    assert out["result"][1]["status"] == c.SUCCEEDED


def test_progress_failure_never_fails_the_command(env):
    run = make_run(source_root=env["root"])

    class Hostile(dict):
        def __setitem__(self, key, value):
            if key == "stall_anchor_mono":
                raise RuntimeError("boom")
            super().__setitem__(key, value)

    hostile = Hostile(run)
    commands.note_progress(hostile)  # never raises


# ── rule 4 / completion 7: running commands ──────────────────────────────────

def test_an_approved_command_runs_to_completion_on_a_run_past_30_minutes(env, monkeypatch):
    set_policy(monkeypatch, c.POLICY_ALWAYS_APPROVE)
    slow = FakeExecutor(auto_release=False)
    use_executor(monkeypatch, slow)
    run = make_run(source_root=env["root"], elapsed_sec=PAST_HOP_BUDGET_SEC)
    threading.Timer(1.5, slow.release.set).start()
    status, result = commands.run_tool(run, _payload(timeout_seconds=60))
    assert result["status"] == c.SUCCEEDED and result["error_code"] is None
    assert run["last_progress_signal"] == "command"


def test_a_running_command_stops_when_the_budget_runs_out(env, monkeypatch):
    set_policy(monkeypatch, c.POLICY_ALWAYS_APPROVE)
    slow = FakeExecutor(auto_release=False)
    use_executor(monkeypatch, slow)
    _absolute_after(monkeypatch, first=4000.0, then=3.0, switch_after=0.8)
    status, result = commands.run_tool(make_run(source_root=env["root"]), _payload(timeout_seconds=600))
    assert result["status"] == c.CANCELLED and result["error_code"] == "chat_command_run_budget_exhausted"
    assert repo.list_open_commands() == []


def test_a_running_command_is_waited_for_its_timeout_plus_grace_only(env, monkeypatch):
    set_policy(monkeypatch, c.POLICY_ALWAYS_APPROVE)
    monkeypatch.setattr(c, "RUNNING_GRACE_SEC", 0.3)
    slow = FakeExecutor(auto_release=False)
    use_executor(monkeypatch, slow)
    started = time.monotonic()
    status, result = commands.run_tool(make_run(source_root=env["root"]), _payload(timeout_seconds=1))
    assert time.monotonic() - started < 6
    assert result["status"] == c.CANCELLED and result["error_code"] == "chat_command_run_budget_exhausted"


def test_command_timeout_is_timed_out_not_cancelled(env, monkeypatch):
    set_policy(monkeypatch, c.POLICY_ALWAYS_APPROVE)
    use_executor(monkeypatch, FakeExecutor(exit_code=None, timed_out=True))
    status, result = commands.run_tool(make_run(source_root=env["root"]), _payload(timeout_seconds=1))
    assert result["status"] == c.TIMED_OUT and result["timed_out"] is True


# ── rule 5: the CLI long-poll ────────────────────────────────────────────────

def test_cli_long_poll_is_capped_by_the_budget(env, monkeypatch):
    monkeypatch.setattr(legacy_adapter, "absolute_remaining_sec", lambda run: c.RUN_SAFETY_SEC + 0.5)
    run = make_run(source_root=env["root"])
    started = time.monotonic()
    payload = commands.cli_create(run, {"token_id": run["current_token_id"]}, {**_payload(), "wait_seconds": 60})
    assert time.monotonic() - started < 3
    assert payload["terminal"] is False


def test_cli_long_poll_answers_at_once_without_budget(env, monkeypatch):
    run = make_run(source_root=env["root"])
    payload = commands.cli_create(run, {"token_id": run["current_token_id"]}, {**_payload(), "wait_seconds": 0})
    rid = payload["request"]["request_id"]
    monkeypatch.setattr(legacy_adapter, "absolute_remaining_sec", lambda run: c.RUN_SAFETY_SEC - 1)
    started = time.monotonic()
    again = commands.cli_read(run, rid, 60)
    assert time.monotonic() - started < 1
    assert again["terminal"] is False and again["request"]["status"] == c.PENDING_APPROVAL


def test_cli_pending_has_no_approval_cap_and_feeds_the_watchdog(env, monkeypatch):
    monkeypatch.setattr(c, "APPROVAL_WAIT_MAX_SEC", 0.2)  # API-only cap: must not apply here
    run = make_run(source_root=env["root"])
    payload = commands.cli_create(run, {"token_id": run["current_token_id"]}, {**_payload(), "wait_seconds": 0})
    rid = payload["request"]["request_id"]
    before = run["progress_observations"]
    again = commands.cli_read(run, rid, "1.5")
    assert again["terminal"] is False and again["request"]["status"] == c.PENDING_APPROVAL
    assert run["progress_observations"] > before and run["last_progress_signal"] == "command"
    commands.cancel_for_run(run["run_id"], c.REASON_RUN_FINISHED)
    done = repo.get_command(rid)
    assert done["status"] == c.CANCELLED and done["error_code"] == "chat_command_run_finished"
