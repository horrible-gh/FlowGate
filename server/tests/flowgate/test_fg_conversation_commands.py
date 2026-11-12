"""Chat command requests: states, policies, decisions, errors, AI result (flowgate.v02.0032 T#1).

D0006 §3.1-§3.3, §3.7, §5.2, §5.3 and completion conditions 3, 4, 6, 8, 10, 11, 26.
"""
from __future__ import annotations

import json
import sys
import threading
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _fg_conversation_support import (  # noqa: E402
    ADMIN, CHAT_DOC, GROUP, OTHER_USER, PROJECT, RUN_ID, USER, FakeExecutor, add_run_token,  # noqa: F401
    bind_worktree, clean_commands_state, clean_runs, fg_store, init_worktree, make_run, seed,  # noqa: F401
    set_policy, set_token, unregister_run, use_executor, wait_until,
)

from flowgate.conversation import commands  # noqa: E402
from flowgate.conversation import contracts as c  # noqa: E402
from flowgate.conversation import repository as repo  # noqa: E402


@pytest.fixture()
def env(fg_store, tmp_path, monkeypatch, clean_commands_state, clean_runs):
    seed(fg_store)
    root = init_worktree(tmp_path / "wt")
    bind_worktree(monkeypatch, root)
    executor = FakeExecutor()
    use_executor(monkeypatch, executor)
    set_policy(monkeypatch, c.POLICY_USER_APPROVAL)
    run = make_run(source_root=root)
    return {"root": root, "run": run, "executor": executor, "store": fg_store}


def _payload(**extra):
    body = {"program": "git", "args": ["status"], "cwd": ".", "timeout_seconds": 30}
    body.update(extra)
    return body


# ── contract constants ───────────────────────────────────────────────────────

def test_contract_names_match_the_design():
    assert c.STATUSES == ("pending_approval", "approved", "running", "succeeded", "failed",
                          "timed_out", "cancelled", "rejected")
    assert set(c.TERMINAL_STATUSES) == {"succeeded", "failed", "timed_out", "cancelled", "rejected"}
    assert set(c.OPEN_STATUSES) == {"pending_approval", "approved", "running"}
    assert c.DECISIONS == ("approve", "reject", "cancel")
    assert (c.APPROVAL_WAIT_MAX_SEC, c.RUN_SAFETY_SEC, c.CLI_WAIT_DEFAULT_SEC, c.CLI_WAIT_MAX_SEC) == (900, 5, 30, 120)
    assert (c.RESULT_STREAMS_TOTAL_CHARS, c.RESULT_META_FIELD_CHARS) == (12000, 1000)


def test_ai_result_cap_is_the_runtime_constant_not_a_copy():
    from modules.flow_gate.services.ai_invoke import runtime

    assert c.AI_RESULT_MAX_CHARS is runtime.API_TOOL_RESULT_MAX_CHARS or c.AI_RESULT_MAX_CHARS == runtime.API_TOOL_RESULT_MAX_CHARS
    source = (Path(c.__file__)).read_text(encoding="utf-8")
    assert "16000" not in source and "16_000" not in source


def test_command_policy_domain_matches_chat_settings():
    from modules.flow_gate.services import chat_settings_service

    assert tuple(chat_settings_service.COMMAND_POLICY_DOMAIN) == c.COMMAND_POLICIES
    assert chat_settings_service.COMMAND_POLICY_DEFAULT == c.POLICY_USER_APPROVAL


# ── policies ─────────────────────────────────────────────────────────────────

def test_user_approval_policy_creates_a_pending_request(env):
    row = commands.create_request(env["run"], _payload())
    assert row["status"] == c.PENDING_APPROVAL
    assert row["decision_source"] is None
    assert row["ai_run_id"] == RUN_ID and row["doc_id"] == CHAT_DOC
    assert row["category"] == "git_read"
    assert env["executor"].spawned == []


def test_always_approve_runs_immediately(env, monkeypatch):
    set_policy(monkeypatch, c.POLICY_ALWAYS_APPROVE)
    row = commands.create_request(env["run"], _payload())
    assert row["status"] == c.APPROVED and row["decision_source"] == c.DECIDED_BY_POLICY
    assert wait_until(lambda: repo.get_command(row["request_id"])["status"] == c.SUCCEEDED)
    final = repo.get_command(row["request_id"])
    assert final["exit_code"] == 0 and final["stdout_tail"] == "ok\n"
    assert env["executor"].spawned and Path(env["executor"].spawned[0][0]).name.lower().startswith("git")


def test_reject_policy_never_runs_and_says_policy(env, monkeypatch):
    set_policy(monkeypatch, c.POLICY_REJECT)
    row = commands.create_request(env["run"], _payload())
    assert row["status"] == c.REJECTED and row["decision_source"] == c.DECIDED_BY_POLICY
    result = commands.ai_result(row)
    assert result["rejected"] is True and result["rejected_by"] == "policy"
    assert "nothing was run" in result["message"]
    assert env["executor"].spawned == []


def test_policy_is_read_per_request(env, monkeypatch):
    set_policy(monkeypatch, c.POLICY_REJECT)
    first = commands.create_request(env["run"], _payload())
    set_policy(monkeypatch, c.POLICY_USER_APPROVAL)
    second = commands.create_request(env["run"], _payload())
    assert (first["status"], second["status"]) == (c.REJECTED, c.PENDING_APPROVAL)


# ── decisions ────────────────────────────────────────────────────────────────

def test_approve_runs_the_command_and_reject_does_not(env):
    a = commands.create_request(env["run"], _payload())
    b = commands.create_request(env["run"], _payload(args=["log", "-1"]))
    approved = commands.decide(a["request_id"], {"user_id": USER}, "approve")
    assert approved["status"] == c.APPROVED and approved["decided_by"] == USER
    rejected = commands.decide(b["request_id"], {"user_id": USER}, "reject")
    assert rejected["status"] == c.REJECTED and rejected["decision_source"] == c.DECIDED_BY_USER
    assert wait_until(lambda: repo.get_command(a["request_id"])["status"] == c.SUCCEEDED)
    assert commands.ai_result(repo.get_command(b["request_id"]))["rejected_by"] == "user"
    assert len(env["executor"].spawned) == 1


def test_admin_may_decide_another_users_request(env):
    row = commands.create_request(env["run"], _payload())
    assert commands.decide(row["request_id"], {"user_id": ADMIN, "is_admin": True}, "reject")["status"] == c.REJECTED


def test_decision_errors_follow_the_design_table(env):
    row = commands.create_request(env["run"], _payload())
    rid = row["request_id"]
    with pytest.raises(c.ConversationError) as missing:
        commands.decide("ccr_missing", {"user_id": USER}, "approve")
    assert (missing.value.status, missing.value.code) == (404, "chat_command_not_found")
    with pytest.raises(c.ConversationError) as invalid:
        commands.decide(rid, {"user_id": USER}, "maybe")
    assert (invalid.value.status, invalid.value.code) == (422, "chat_command_invalid_decision")
    with pytest.raises(c.ConversationError) as forbidden:
        commands.decide(rid, {"user_id": OTHER_USER}, "approve")
    assert (forbidden.value.status, forbidden.value.code) == (403, "chat_command_decision_forbidden")
    assert repo.get_command(rid)["status"] == c.PENDING_APPROVAL
    commands.decide(rid, {"user_id": USER}, "reject")
    with pytest.raises(c.ConversationError) as decided:
        commands.decide(rid, {"user_id": USER}, "approve")
    assert (decided.value.status, decided.value.code) == (409, "chat_command_already_decided")
    with pytest.raises(c.ConversationError) as finished:
        commands.decide(rid, {"user_id": USER}, "cancel")
    assert (finished.value.status, finished.value.code) == (409, "chat_command_already_finished")
    assert repo.get_command(rid)["status"] == c.REJECTED


def test_duplicate_concurrent_approvals_run_the_command_once(env):
    row = commands.create_request(env["run"], _payload())
    results, errors = [], []

    def click():
        try:
            results.append(commands.decide(row["request_id"], {"user_id": USER}, "approve"))
        except c.ConversationError as exc:
            errors.append(exc.code)

    threads = [threading.Thread(target=click) for _ in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(results) == 1
    assert errors and set(errors) == {"chat_command_already_decided"}
    assert wait_until(lambda: repo.get_command(row["request_id"])["status"] == c.SUCCEEDED)
    assert len(env["executor"].spawned) == 1


def test_approving_after_the_run_ended_closes_the_request(env):
    row = commands.create_request(env["run"], _payload())
    env["run"]["status"] = "finished"
    with pytest.raises(c.ConversationError) as exc:
        commands.decide(row["request_id"], {"user_id": USER}, "approve")
    assert (exc.value.status, exc.value.code) == (409, "chat_command_run_finished")
    stored = repo.get_command(row["request_id"])
    assert stored["status"] == c.CANCELLED and stored["error_code"] == "chat_command_run_finished"
    assert env["executor"].spawned == []


def test_approving_while_the_run_is_cancelling_closes_the_request(env):
    row = commands.create_request(env["run"], _payload())
    env["run"]["cancel_event"].set()
    with pytest.raises(c.ConversationError) as exc:
        commands.decide(row["request_id"], {"user_id": USER}, "approve")
    assert (exc.value.status, exc.value.code) == (409, "chat_command_run_cancelled")
    assert repo.get_command(row["request_id"])["error_code"] == "chat_command_run_cancelled"


def test_user_cancel_of_pending_and_of_running(env, monkeypatch):
    pending = commands.create_request(env["run"], _payload())
    cancelled = commands.decide(pending["request_id"], {"user_id": USER}, "cancel")
    assert cancelled["status"] == c.CANCELLED and cancelled["error_code"] == "chat_command_cancelled_by_user"

    slow = FakeExecutor(auto_release=False)
    use_executor(monkeypatch, slow)
    running = commands.create_request(env["run"], _payload())
    commands.decide(running["request_id"], {"user_id": USER}, "approve")
    assert slow.spawned_event.wait(5)
    assert wait_until(lambda: repo.get_command(running["request_id"])["status"] == c.RUNNING)
    stopped = commands.decide(running["request_id"], {"user_id": USER}, "cancel")
    assert stopped["status"] == c.CANCELLED and stopped["error_code"] == "chat_command_cancelled_by_user"
    result = commands.ai_result(stopped)
    assert result["cancelled"] is True and result["error_code"] == "chat_command_cancelled_by_user"


# ── request creation errors (§5.3) ───────────────────────────────────────────

def test_create_errors_follow_the_design_table(env, monkeypatch):
    run = env["run"]
    no_cap = make_run(run_id="aiv_nocap", capability=False, source_root=env["root"])
    with pytest.raises(c.ConversationError) as cap:
        commands.create_request(no_cap, _payload())
    assert (cap.value.status, cap.value.code) == (403, "chat_command_capability_required")

    with pytest.raises(c.ConversationError) as invalid:
        commands.create_request(run, {"program": "bash", "args": ["-c", "x"]})
    assert invalid.value.status == 422 and invalid.value.code == "chat_command_program_denied"
    with pytest.raises(c.ConversationError) as shell:
        commands.create_request(run, {"program": "git", "args": ["status", "|", "x"]})
    assert shell.value.status == 422 and shell.value.code == "chat_command_shell_operator"
    with pytest.raises(c.ConversationError) as unknown:
        commands.create_request(run, {"program": "git", "group_id": "x"})
    assert unknown.value.status == 422 and unknown.value.code == "chat_command_invalid_request"
    with pytest.raises(c.ConversationError) as timeout:
        commands.create_request(run, {"program": "git", "timeout_seconds": 0})
    assert timeout.value.code == "chat_command_invalid_timeout"
    with pytest.raises(c.ConversationError) as cwd:
        commands.create_request(run, {"program": "git", "cwd": "../outside"})
    assert cwd.value.status == 422 and cwd.value.code == "chat_command_invalid_cwd"

    other = make_run(run_id="aiv_other_root", source_root=env["root"].parent)
    with pytest.raises(c.ConversationError) as mismatch:
        commands.create_request(other, _payload())
    assert (mismatch.value.status, mismatch.value.code) == (409, "chat_command_worktree_mismatch")

    bind_worktree(monkeypatch, None)
    with pytest.raises(c.ConversationError) as missing:
        commands.create_request(run, _payload())
    assert (missing.value.status, missing.value.code) == (409, "chat_command_worktree_unavailable")


def test_create_on_finished_or_cancelling_run(env):
    finished = make_run(run_id="aiv_done", source_root=env["root"])
    finished["status"] = "finished"
    with pytest.raises(c.ConversationError) as done:
        commands.create_request(finished, _payload())
    assert (done.value.status, done.value.code) == (409, "chat_command_run_finished")
    cancelling = make_run(run_id="aiv_cancel", source_root=env["root"])
    cancelling["cancel_event"].set()
    with pytest.raises(c.ConversationError) as cancel:
        commands.create_request(cancelling, _payload())
    assert (cancel.value.status, cancel.value.code) == (409, "chat_command_run_cancelled")


def test_a_request_stored_while_the_run_closes_is_closed_at_once(env, monkeypatch):
    real_insert = repo.insert_command

    def insert_then_close(**kwargs):
        row = real_insert(**kwargs)
        commands._close_run(kwargs["ai_run_id"], c.REASON_RUN_FINISHED)
        return row

    monkeypatch.setattr(repo, "insert_command", insert_then_close)
    with pytest.raises(c.ConversationError) as exc:
        commands.create_request(env["run"], _payload())
    assert exc.value.code == "chat_command_run_finished"
    rows = repo.list_commands_for_doc(CHAT_DOC)
    assert rows and rows[-1]["status"] == c.CANCELLED and rows[-1]["error_code"] == "chat_command_run_finished"


# ── run end / restart cleanup (§3.7) ─────────────────────────────────────────

def test_finalize_leaves_no_open_command(env, monkeypatch):
    pending = commands.create_request(env["run"], _payload())
    slow = FakeExecutor(auto_release=False)
    use_executor(monkeypatch, slow)
    running = commands.create_request(env["run"], _payload())
    commands.decide(running["request_id"], {"user_id": USER}, "approve")
    assert slow.spawned_event.wait(5)
    commands.cancel_for_run(RUN_ID, c.REASON_RUN_FINISHED)
    assert repo.list_open_commands_for_run(RUN_ID) == []
    for rid in (pending["request_id"], running["request_id"]):
        row = repo.get_command(rid)
        assert row["status"] == c.CANCELLED and row["error_code"] == "chat_command_run_finished"
    with pytest.raises(c.ConversationError) as late:
        commands.create_request(env["run"], _payload())
    assert late.value.code == "chat_command_run_finished"


def test_run_cancel_closes_pending_with_run_cancelled_and_announces_under_the_gate(env):
    pending = commands.create_request(env["run"], _payload())
    announced = []
    commands.stop_run(RUN_ID, on_closed=lambda: announced.append(commands._run_closed(RUN_ID)))
    assert announced == [c.REASON_RUN_CANCELLED]
    row = repo.get_command(pending["request_id"])
    assert row["status"] == c.CANCELLED and row["error_code"] == "chat_command_run_cancelled"


def test_startup_recovery_cancels_open_rows_without_resuming(env):
    rows = [commands.create_request(env["run"], _payload()) for _ in range(2)]
    repo.transition_command(rows[1]["request_id"], (c.PENDING_APPROVAL,), c.RUNNING)
    assert commands.recover() == 2
    for row in rows:
        stored = repo.get_command(row["request_id"])
        assert stored["status"] == c.CANCELLED and stored["error_code"] == "chat_command_server_restarted"
    assert env["executor"].spawned == []


# ── public view ──────────────────────────────────────────────────────────────

def test_public_view_carries_status_reason_rejector_and_anchor(env, monkeypatch):
    set_policy(monkeypatch, c.POLICY_REJECT)
    row = commands.create_request(env["run"], _payload())
    view = commands.public(row)
    for key in ("request_id", "ai_run_id", "doc_id", "status", "error_code", "rejected_by", "decision_source",
                "command", "program", "args", "cwd", "stdout_tail", "stderr_tail", "anchor_seq",
                "anchor_position", "anchor_state", "high_impact", "category", "policy"):
        assert key in view
    assert view["rejected_by"] == "policy" and view["command"] == "git status"


# ── AI result builder (§3.5.3 / §5.2 / completion condition 26) ──────────────

def _row(**over):
    row = {"request_id": "ccr_x", "program": "pytest", "args": ["-q"], "cwd_relative": ".",
           "status": c.SUCCEEDED, "exit_code": 0, "timed_out": False, "duration_ms": 10,
           "stdout_tail": "", "stderr_tail": "", "error_code": None, "decision_source": "user",
           "stdout_truncated": False, "stderr_truncated": False}
    row.update(over)
    return row


def test_result_item_order_puts_judgement_first_and_streams_last():
    keys = list(commands.ai_result(_row(status=c.REJECTED, decision_source="user")).keys())
    assert keys == ["ok", "request_id", "status", "error_code", "rejected", "rejected_by", "cancelled",
                    "exit_code", "timed_out", "duration_ms", "message", "stdout_truncated",
                    "stderr_truncated", "meta_truncated", "program", "command", "args", "cwd",
                    "stdout", "stderr"]
    assert "rejected_by" not in commands.ai_result(_row())


@pytest.mark.parametrize("out_len,err_len", [(30000, 30000), (50000, 10), (10, 50000), (3000, 3000)])
def test_streams_share_one_budget_and_keep_their_ends(out_len, err_len):
    out = ("o" * (out_len - 3)) + "END"
    err = ("e" * (err_len - 3)) + "FIN" if err_len >= 3 else "e" * err_len
    result = commands.ai_result(_row(stdout_tail=out, stderr_tail=err))
    assert len(result["stdout"]) + len(result["stderr"]) <= c.RESULT_STREAMS_TOTAL_CHARS
    assert result["stdout"].endswith(out[-3:]) and result["stderr"].endswith(err[-3:])
    assert result["stdout_truncated"] == (len(result["stdout"]) < out_len)
    assert result["stderr_truncated"] == (len(result["stderr"]) < err_len)
    if out_len <= 6000 and err_len <= 6000:
        assert (result["stdout"], result["stderr"]) == (out, err)
    elif out_len <= 6000:
        assert result["stdout"] == out
    elif err_len <= 6000:
        assert result["stderr"] == err
        assert len(result["stdout"]) == c.RESULT_STREAMS_TOTAL_CHARS - err_len
    else:
        assert len(result["stdout"]) == len(result["stderr"]) == c.RESULT_STREAMS_TOTAL_CHARS // 2


@pytest.mark.parametrize("unit", ['"', "\n", "\\", "\t", '"\n', "\x01"])
def test_escape_heavy_output_still_fits_and_parses(unit):
    noisy = unit * 40000
    result = commands.ai_result(_row(status=c.FAILED, exit_code=1, stdout_tail=noisy, stderr_tail=noisy))
    text = json.dumps(result, ensure_ascii=False)
    assert len(text) <= c.AI_RESULT_MAX_CHARS
    parsed = json.loads(text)
    assert parsed["status"] == c.FAILED and parsed["error_code"] is None
    assert parsed["stdout_truncated"] is True and parsed["stderr_truncated"] is True
    assert "Only the end of the output is included" in parsed["message"]
    # The worker's own safety cut is a no-op for this result.
    assert text[:c.AI_RESULT_MAX_CHARS] == text


def test_oversized_meta_fields_are_clipped_and_flagged():
    long_args = ["x" * 600 for _ in range(10)]
    result = commands.ai_result(_row(args=long_args, cwd_relative="d/" * 900, stdout_tail="\n" * 50000))
    assert result["meta_truncated"] is True
    assert len(json.dumps(result["args"], ensure_ascii=False)) <= c.RESULT_META_FIELD_CHARS
    assert len(result["cwd"]) <= c.RESULT_META_FIELD_CHARS
    assert len(result["command"]) <= c.RESULT_META_FIELD_CHARS
    assert len(json.dumps(result, ensure_ascii=False)) <= c.AI_RESULT_MAX_CHARS


@pytest.mark.parametrize("field", ["f" * 20000, '"' * 20000, "\n" * 9000 + "x" * 9000])
def test_input_error_result_fits_and_parses(env, field):
    status, result = commands.run_tool(env["run"], {"program": "git", field: "x"})
    text = json.dumps(result, ensure_ascii=False)
    assert status == 422 and len(text) <= c.AI_RESULT_MAX_CHARS
    parsed = json.loads(text)
    assert parsed["ok"] is False and parsed["op"] == "run_command"
    assert parsed["error"]["code"] == "chat_command_invalid_request"
    assert parsed["meta_truncated"] is True
    assert len(json.dumps(parsed["error"]["message"], ensure_ascii=False)) - 2 <= c.RESULT_META_FIELD_CHARS
    assert parsed["error"]["message"].startswith("unknown field(s): ")
    # The worker's own safety cut is a no-op for this result.
    assert text[:c.AI_RESULT_MAX_CHARS] == text
    assert repo.list_open_commands_for_run(env["run"]["run_id"]) == []


def test_echoed_argument_error_fits_and_parses(env):
    # One schema-valid argument (<= 8192 chars) doubles under JSON escaping.
    status, result = commands.run_tool(env["run"], {"program": "git", "args": ["--git-dir=" + '"' * 8000]})
    text = json.dumps(result, ensure_ascii=False)
    assert status == 422 and len(text) <= c.AI_RESULT_MAX_CHARS
    parsed = json.loads(text)
    assert parsed["error"]["code"] == "chat_command_invalid_cwd" and parsed["meta_truncated"] is True


def test_any_oversized_error_message_is_held_to_the_cap(env, monkeypatch):
    def failing(run, payload, token_id=None):
        raise c.ConversationError(422, "chat_command_program_denied", "\\" * 30000)

    monkeypatch.setattr(commands, "create_request", failing)
    status, result = commands.run_tool(env["run"], {"program": "x"})
    text = json.dumps(result, ensure_ascii=False)
    assert status == 422 and len(text) <= c.AI_RESULT_MAX_CHARS
    assert json.loads(text)["error"]["code"] == "chat_command_program_denied"


def test_short_input_error_is_returned_whole(env):
    status, result = commands.run_tool(env["run"], {"program": "git", "group_id": "x"})
    assert status == 422
    assert result == {"ok": False, "op": "run_command", "meta_truncated": False,
                      "error": {"code": "chat_command_invalid_request", "message": "unknown field(s): group_id"}}


def test_stored_tail_cut_is_reported_even_when_it_fits():
    result = commands.ai_result(_row(stdout_tail="short", stdout_truncated=True))
    assert result["stdout"] == "short" and result["stdout_truncated"] is True
    assert "full output is shown to the user" in result["message"]


@pytest.mark.parametrize("reason,needle", [
    ("chat_command_approval_wait_expired", "request it again"),
    ("chat_command_run_budget_exhausted", "remaining time"),
    ("chat_command_cancelled_by_user", "user cancelled"),
    ("chat_command_server_restarted", "server restarted"),
])
def test_cancel_reason_messages(reason, needle):
    result = commands.ai_result(_row(status=c.CANCELLED, error_code=reason, exit_code=None))
    assert result["cancelled"] is True and result["error_code"] == reason
    assert needle in result["message"]


def test_secrets_are_redacted_in_the_result():
    result = commands.ai_result(_row(stdout_tail="Authorization: Bearer abc.def.ghi\n"))
    assert "abc.def.ghi" not in result["stdout"]


# ── CLI transport ────────────────────────────────────────────────────────────

@pytest.mark.parametrize("value,expected", [
    (None, 30.0), ("abc", 30.0), (-5, 30.0), ("-1", 30.0), (True, 30.0), (10, 10.0), ("7.5", 7.5),
    (500, 120.0), ("9999", 120.0), (0, 0.0),
])
def test_cli_wait_parsing(value, expected):
    assert commands.cli_wait_seconds(value) == expected


def test_cli_token_must_be_the_runs_current_token(env):
    run = env["run"]
    token = {"ai_run_id": RUN_ID, "project": run["project_id"], "group_id": run["group_id"],
             "token_id": run["current_token_id"]}
    assert commands.authorize_token(token, run) is run
    for bad in ({**token, "group_id": "other"}, {**token, "token_id": "tok_old"}, {**token, "ai_run_id": None}):
        with pytest.raises(c.ConversationError) as exc:
            commands.authorize_token(bad, run)
        assert (exc.value.status, exc.value.code) == (403, "chat_command_forbidden")


def test_cli_create_and_read_return_terminal_results(env, monkeypatch):
    run = env["run"]
    token = {"token_id": run["current_token_id"]}
    pending = commands.cli_create(run, token, {**_payload(), "wait_seconds": 0})
    assert pending["terminal"] is False and pending["result"] is None
    rid = pending["request"]["request_id"]
    commands.decide(rid, {"user_id": USER}, "reject")
    done = commands.cli_read(run, rid, 5)
    assert done["terminal"] is True and done["result"]["rejected_by"] == "user"
    with pytest.raises(c.ConversationError) as missing:
        commands.cli_read(make_run(run_id="aiv_other", source_root=env["root"]), rid, 0)
    assert (missing.value.status, missing.value.code) == (404, "chat_command_not_found")


# ── issued token capability: read-only chats never run commands (0180 policy) ──
#
# The authority is the persisted row of the run's current token -- never the run's own
# ``chat_command_capability`` flag nor the user's current source-access setting. Judged at
# creation, approval and right before spawn; API and CLI share it.

TOKEN = f"tok_{RUN_ID}"
PAST = "2000-01-01T00:00:00+00:00"


def _read_only_run(env, run_id="aiv_read", source_access="read"):
    return make_run(run_id=run_id, source_root=env["root"], source_access=source_access)


@pytest.mark.parametrize("source_access", ["read", None])
def test_read_only_or_legacy_token_cannot_create_a_command(env, monkeypatch, source_access):
    set_policy(monkeypatch, c.POLICY_ALWAYS_APPROVE)
    run = _read_only_run(env, source_access=source_access)
    assert run["chat_command_capability"] is True  # the run flag alone never grants it
    with pytest.raises(c.ConversationError) as exc:
        commands.create_request(run, _payload())
    assert (exc.value.status, exc.value.code) == (403, "chat_command_capability_required")
    assert repo.list_open_commands_for_run(run["run_id"]) == []
    assert env["executor"].spawned == []


def test_read_only_token_gets_the_refusal_as_an_api_tool_result(env, monkeypatch):
    set_policy(monkeypatch, c.POLICY_ALWAYS_APPROVE)
    status, result = commands.run_tool(_read_only_run(env), _payload())
    assert status == 403 and result["ok"] is False
    assert result["error"]["code"] == "chat_command_capability_required"
    assert env["executor"].spawned == []


def test_read_only_token_is_refused_on_the_cli_transport(env, monkeypatch):
    set_policy(monkeypatch, c.POLICY_ALWAYS_APPROVE)
    run = _read_only_run(env)
    token = {"ai_run_id": run["run_id"], "project": PROJECT, "group_id": GROUP,
             "token_id": run["current_token_id"]}
    with pytest.raises(c.ConversationError) as exc:
        commands.authorize_token(token, run)
    assert (exc.value.status, exc.value.code) == (403, "chat_command_capability_required")
    with pytest.raises(c.ConversationError) as created:
        commands.cli_create(run, token, {**_payload(), "wait_seconds": 0})
    assert created.value.code == "chat_command_capability_required"
    assert env["executor"].spawned == []


def test_missing_or_mismatched_token_rows_fail_closed(env):
    run = env["run"]
    # The token row names another run / is not a chat token ...
    for over in ({"ai_run_id": "aiv_someone_else"}, {"action_scope": "edit"}):
        set_token(TOKEN, **over)
        with pytest.raises(c.ConversationError) as exc:
            commands.create_request(run, _payload())
        assert (exc.value.status, exc.value.code) == (403, "chat_command_forbidden"), over
        set_token(TOKEN, ai_run_id=RUN_ID, action_scope="chat")
    # ... or the run names another document / group than its token.
    for key, value in (("doc_ref", "fgp.v02.0001.0099-CH"), ("group_id", "fgp.v02.0002")):
        original = run[key]
        run[key] = value
        try:
            with pytest.raises(c.ConversationError) as exc:
                commands._authorize_run(run)
            assert (exc.value.status, exc.value.code) == (403, "chat_command_forbidden"), key
        finally:
            run[key] = original
    ghost = make_run(run_id="aiv_ghost", source_root=env["root"])
    ghost["current_token_id"] = "tok_never_issued"
    with pytest.raises(c.ConversationError) as missing:
        commands.create_request(ghost, _payload())
    assert (missing.value.status, missing.value.code) == (403, "chat_command_forbidden")
    assert env["executor"].spawned == []


def test_edit_once_token_keeps_running_after_the_setting_fell_back_to_read_only(env, monkeypatch):
    """A won edit_once claim fixed read_write on the token at issue; admission then left the
    stored mode read_only. Commands follow the token, not the setting."""
    from modules.flow_gate.db import user_chat_source_access
    from modules.flow_gate.db.connection import now_iso

    user_chat_source_access.upsert(USER, PROJECT, GROUP, "read_only", now_iso())
    assert user_chat_source_access.resolve_source_access_mode(USER, PROJECT, GROUP) == "read_only"
    set_policy(monkeypatch, c.POLICY_ALWAYS_APPROVE)
    row = commands.create_request(env["run"], _payload())
    assert wait_until(lambda: repo.get_command(row["request_id"])["status"] == c.SUCCEEDED)
    assert len(env["executor"].spawned) == 1


@pytest.mark.parametrize("change", [
    {"revoked_at": PAST}, {"consumed_at": PAST}, {"expires_at": PAST}, {"source_access": "read"},
])
def test_approval_re_judges_the_requests_token(env, change):
    row = commands.create_request(env["run"], _payload())
    set_token(TOKEN, **change)
    with pytest.raises(c.ConversationError) as exc:
        commands.decide(row["request_id"], {"user_id": USER}, "approve")
    expected = "chat_command_capability_required" if "source_access" in change else "chat_command_forbidden"
    assert (exc.value.status, exc.value.code) == (403, expected)
    stored = repo.get_command(row["request_id"])
    assert stored["status"] == c.CANCELLED and stored["error_code"] == expected
    assert env["executor"].spawned == []


def test_a_request_of_a_replaced_token_is_not_approved(env):
    row = commands.create_request(env["run"], _payload())
    assert row["token_id"] == TOKEN
    add_run_token(RUN_ID, token_id="tok_next_hop")
    env["run"]["current_token_id"] = "tok_next_hop"  # a live read_write token, but not the request's
    with pytest.raises(c.ConversationError) as exc:
        commands.decide(row["request_id"], {"user_id": USER}, "approve")
    assert (exc.value.status, exc.value.code) == (403, "chat_command_forbidden")
    assert repo.get_command(row["request_id"])["status"] == c.CANCELLED
    # The new current token itself may still ask.
    assert commands.create_request(env["run"], _payload())["token_id"] == "tok_next_hop"
    assert env["executor"].spawned == []


def test_the_spawn_gate_re_judges_the_token_after_approval(env, monkeypatch):
    started = []
    monkeypatch.setattr(commands, "_start", lambda row: started.append(row))
    row = commands.create_request(env["run"], _payload())
    commands.decide(row["request_id"], {"user_id": USER}, "approve")
    assert [r["request_id"] for r in started] == [row["request_id"]]
    set_token(TOKEN, source_access="read")
    commands._execute(row["request_id"], env["run"]["source_root"])
    stored = repo.get_command(row["request_id"])
    assert stored["status"] == c.CANCELLED and stored["error_code"] == "chat_command_capability_required"
    assert env["executor"].spawned == []


def test_consuming_the_token_never_cuts_a_command_already_running(env, monkeypatch):
    """The turn submit consumes the token. A command started before it runs to its end
    (only finalize closes it); only new requests / approvals / spawns need a live token."""
    slow = FakeExecutor(auto_release=False)
    use_executor(monkeypatch, slow)
    set_policy(monkeypatch, c.POLICY_ALWAYS_APPROVE)
    row = commands.create_request(env["run"], _payload())
    assert slow.spawned_event.wait(5)
    assert wait_until(lambda: repo.get_command(row["request_id"])["status"] == c.RUNNING)
    set_token(TOKEN, consumed_at=PAST)
    slow.release.set()
    assert wait_until(lambda: repo.get_command(row["request_id"])["status"] == c.SUCCEEDED)
    with pytest.raises(c.ConversationError) as late:
        commands.create_request(env["run"], _payload())
    assert (late.value.status, late.value.code) == (403, "chat_command_forbidden")


def test_run_state_refusals_keep_their_codes_before_the_token_check(env):
    row = commands.create_request(env["run"], _payload())
    set_token(TOKEN, source_access="read")
    env["run"]["status"] = "finished"
    with pytest.raises(c.ConversationError) as exc:
        commands.decide(row["request_id"], {"user_id": USER}, "approve")
    assert exc.value.code == "chat_command_run_finished"


def test_token_allows_commands_is_the_offer_not_the_gate(env):
    assert commands.token_allows_commands(env["run"]) is True
    assert commands.token_allows_commands(_read_only_run(env)) is False
    assert commands.token_allows_commands(_read_only_run(env, run_id="aiv_legacy", source_access=None)) is False
    assert commands.token_allows_commands({"action_scope": "edit", "run_id": RUN_ID}) is False
    assert commands.token_allows_commands(None) is False
