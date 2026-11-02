"""flowgate.v02.0113 T#5 — the result of one step / full command (0030-M §5, 0002-D 개정 §3.14 / §6.5).

The harness of ``test_fg_ai_roles_l2_assignment`` / ``test_fg_document_progress_0113`` (real routes, Graph engine, card
settings / documents / approval ports, a separate SQLite DB; every AI write start is the ``env["jobs"]`` recorder and every
review loop the ``starts`` recorder: nothing calls an AI). The role invocation records (§3.10), their launch outputs and the
AI runs' durable rows are written by the test as the launch journal would write them (mock records: no provider is called),
tied to the execution the engine requested for them (its ledger row) — except where a test says otherwise.

``GET /api/v1/runs/{run_id}/command-result``:
- a full command over a written manual Flow is ``completed`` with no AI call; branches that wait make it ``waiting``; a
  step that ends at a manual write wait is ``waiting``; one branch failing while another waits is ``failed`` with its
  one-line reason;
- the command's range (review rework #1): a Hold set / released after a step ended never grows it (its end, cards and
  messages stay); a full command waiting for a person goes on with that person's save to ``completed`` (or ``failed``)
  under the same ``command_seq`` and says it may still go on (``continues``) only while it can; the next command ends it;
- the messages of a command are its own (review rework #1): a record is the command's by the run event its request was
  made at — two commands and their records in one millisecond never mix (messages, cards, roles, failures); a record of
  no execution falls back to its time, given to one command only;
- each launch shows its own body and error (review rework #1): retries and review / rework launches of one AI run keep
  theirs; a launch recorded with no output says so and never shows another launch's; the launch journal stores each
  launch's own output;
- ``command_seq`` picks a command; an unknown one is 404, a malformed one 422.
"""
from __future__ import annotations

import hashlib
import json
import threading
import time

import pytest

from test_fg_ai_roles_l2_assignment import (  # noqa: F401 — fixtures used by name
    A, FA, X, Y, _h, _sql, ai_writes_result, command, db_path, decided, env, node_run, roles, save_instruction, start,
    starts, wired, write_line,
)
from test_fg_card_settings import setting
from test_fg_card_settings_review import WORK_RUN, _run_ended
from test_fg_document_progress_0113 import _write_wait, _written, all_roles, current, fork_join, graph  # noqa: F401

LONG_BODY = "# 실행 결과\n\n" + "\n".join(f"- {i}번째 줄: 긴 본문을 자르지 않고 그대로 돌려준다" for i in range(1, 3001)) + \
    "\n\n```python\nprint('code block kept')\n```\n"
ONE_MS = "2026-11-01T01:02:03.456+00:00"


# ── helpers ──────────────────────────────────────────────────────────────────

def result(env, run_id, seq=None, expect=200, raw=None):
    query = f"?command_seq={seq}" if seq is not None else (f"?command_seq={raw}" if raw is not None else "")
    res = env["client"].get(f"/api/v1/runs/{run_id}/command-result{query}", headers=_h())
    assert res.status_code == expect, res.text
    return res.json()["command_result"] if expect == 200 else res.json()


def outcomes(r):
    return {n["node_key"]: n["outcome"] for n in r["nodes"]}


def invocations(r):
    return [m for m in r["messages"] if m["kind"] == "invocation"]


def fixed(r):
    """What a command result must keep once it ended: its range, cards, failures and messages."""
    return {k: r[k] for k in ("command_seq", "end_seq", "ended_at", "ended", "state", "idle_reason", "nodes", "counts",
                              "failures", "roles", "message_count", "messages")}


def exec_of(env, key):
    """The ledger execution the engine requested for the card's latest write / loop (its ``exec_id``)."""
    rows = _sql(env["db"], "SELECT exec_id, request_json FROM graph_attempt_runs ORDER BY created_at, exec_id")
    found = [r["exec_id"] for r in rows
             if (json.loads(r["request_json"]).get("job") or {}).get("node_key") == key
             or json.loads(r["request_json"]).get("node_key") == key]
    assert found, rows
    return found[-1]


def _ai_run_row(env, ai_run_id, last_message, stderr_tail=None, stop_code=None):
    """The AI run's durable row (``ai_invoke_runs``) its finalize writes: its last message and its stop."""
    now = "2026-11-01T00:00:00.000+00:00"
    _sql(env["db"],
         "INSERT INTO ai_invoke_runs (run_id, group_id, project_id, doc_ref, mode, status, outcome, started_at, "
         "finished_at, last_message, stop_code, stderr_tail, created_at, updated_at) "
         "VALUES (?, ?, 'flowgate', ?, 'single', 'finished', 'complete', ?, ?, ?, ?, ?, ?, ?)",
         (ai_run_id, FA, f"{FA}.0001-R", now, now, last_message, stop_code, stderr_tail, now, now))


def record(run_id, node_key, ai_run_id, launch_no, result_value="succeeded", detail=None, model="claude-opus-5-5",
           confirmed=("claude-opus-5-5",), role="execution", purpose="result_write", stage="result", exec_id=None,
           ai=X, body=None, stderr=None, attempt="att_t5"):
    """One role invocation record (DB-1 §2.6) as the launch journal writes it, its creation order fixed now, tied to
    the ledger execution ``exec_id`` (None: a record of no execution); with ``body`` / ``stderr`` also the launch's own
    output (migration 168) as the journal stores it when the launch ends."""
    from flowgate.ai_roles import record_store

    now = record_store._now()
    inv_id = f"{ai_run_id}#{launch_no}"
    rec = {
        "inv_id": inv_id, "create_seq": record_store.issue_create_seq(), "ai_run_id": ai_run_id, "launch_no": launch_no,
        "hop_no": None, "graph_exec_id": exec_id, "origin_kind": "card_assigned", "inv_role": role, "purpose": purpose,
        "target_group_id": FA, "target_graph_run_id": run_id, "target_node_key": node_key,
        "target_attempt_id": attempt, "target_stage": stage, "target_seq": 0, "target_doc_id": None,
        "target_doc_revision_no": None, "requested_ai_id": ai, "role_source": "card_explicit", "actual_ai_id": ai,
        "actual_ai_name": "AI " + ai, "provider_exec_type": "cli", "provider_kind": "claude",
        "requested_model_state": "value", "requested_model_value": model, "requested_model_reason": None,
        "confirmed_model_state": "confirmed", "confirmed_model_values_json": json.dumps(list(confirmed)),
        "confirmed_coverage": "full", "result": result_value, "result_detail": detail,
        "exit_code": 0 if result_value == "succeeded" else 1, "link_kind": "first", "unit_kind": "assignment",
        "unit_key_hash": hashlib.sha256(inv_id.encode()).hexdigest(), "chain_root_id": inv_id,
        "started_at": now, "ended_at": now, "created_at": now, "updated_at": now,
    }
    assert record_store.write_record(rec)
    if body is not None or stderr is not None:
        assert record_store.write_output({"inv_id": inv_id, "ai_run_id": ai_run_id, "launch_no": launch_no,
                                          "body": body, "stderr_tail": stderr, "stdout_tail": None,
                                          "register_errors_json": None, "detail": detail, "created_at": now})
    return rec


# ── state: completed / waiting / failed ──────────────────────────────────────

def test_full_on_a_written_manual_flow_is_completed_with_no_ai_call(env, decided, starts):
    keys = graph(env, {"c1": "T", "c2": "T", "c3": "T"}, [("r", "c1"), ("c1", "c2"), ("c2", "c3")])
    all_roles(keys)
    for key in keys.values():
        _written(env, key)
    run = start(env)                                                      # [전체 실행]
    r = result(env, run["run_id"])
    assert (r["state"], r["scope"], r["ended"], r["next_state"]) == ("completed", "full", True, "completed")
    assert r["continues"] is False
    assert r["counts"] == {"done": 3, "failed": 0, "waiting": 0}
    assert outcomes(r) == {key: "done" for key in keys.values()}          # the root input is no work of the command
    assert r["failure"] is None and r["failures"] == []
    assert invocations(r) == [] and starts == [] and env["jobs"] == []    # AI 0건, still 완료
    assert r["message_count"] == len(r["messages"]) > 0                  # its node events
    assert {m["node_key"] for m in r["messages"]} == set(keys.values())
    assert all(m["title"] for m in r["messages"])


def test_full_with_one_branch_waiting_is_waiting_never_completed(env, fork_join, starts):
    keys = graph(env, {"c1": "T", "c2": "T"}, [("r", "c1"), ("r", "c2")])
    all_roles(keys)
    _written(env, keys["c1"])
    run = start(env)
    r = result(env, run["run_id"])
    assert (r["state"], r["idle_reason"], r["continues"]) == ("waiting", "needs_intervention", True)
    assert outcomes(r) == {keys["c1"]: "done", keys["c2"]: "waiting"}
    assert r["counts"] == {"done": 1, "failed": 0, "waiting": 1}
    assert next(n for n in r["nodes"] if n["node_key"] == keys["c2"])["reason"] == "manual_write_wait"
    assert r["failure"] is None


def test_one_branch_failed_while_another_waits_is_failed_with_its_reason(env, fork_join, starts):
    keys = graph(env, {"c1": "T", "c2": "T"}, [("r", "c1"), ("r", "c2")], omit=("c1",))
    all_roles(keys)
    roles(keys["c1"], "not_used", _write_wait(env, "wait_unavailable"), "not_used")   # 사용 불가 AI 지정
    run = start(env)
    r = result(env, run["run_id"])
    assert r["idle_reason"] == "needs_intervention"                        # the run waits for c2's person ...
    assert (r["state"], r["continues"]) == ("failed", False)              # ... and the command failed (c1)
    assert outcomes(r) == {keys["c1"]: "failed", keys["c2"]: "waiting"}
    assert r["counts"] == {"done": 0, "failed": 1, "waiting": 1}
    assert (r["failure"]["node_key"], r["failure"]["code"], r["failure"]["detail"]) == (keys["c1"], "ai_unavailable",
                                                                                        "inactive")
    failed = [m for m in r["messages"] if m["error"]]
    assert [(m["code"], m["node_key"]) for m in failed] == [("ai_unavailable", keys["c1"])]
    assert r["roles"]["execution"] == [{"kind": "wait_unavailable", "ai_id": "aip_a", "ai_name": "AI aip_a"}]
    assert env["jobs"] == [] and starts == []                            # nothing called


def test_a_step_that_stops_at_a_manual_write_wait_is_waiting(env, decided, starts):
    keys = graph(env, {"c": "T"}, [("r", "c")])
    all_roles(keys)
    run = command(env, "step")                                            # [다음 단계]
    r = result(env, run["run_id"])
    assert (r["state"], r["scope"], r["continues"]) == ("waiting", "step", True)
    assert outcomes(r) == {keys["c"]: "waiting"}
    assert r["nodes"][0]["reason"] == "manual_write_wait"
    assert r["failure"] is None and invocations(r) == []


def test_a_command_still_running_is_running(env, decided, starts):
    keys = graph(env, {"c": "T"}, [("r", "c")], omit=("c",))
    roles(keys["c"], "not_used", X, "not_used")
    run = command(env, "full")
    assert run["status"] == "running" and len(env["jobs"]) == 1          # the AI works (a recorder)
    r = result(env, run["run_id"])
    assert (r["state"], r["ended"], r["ended_at"], r["continues"]) == ("running", False, None, False)
    assert outcomes(r) == {keys["c"]: "running"}


# ── the command's range: a separate step after it never grows it; a person's save goes on with it ─────────

def test_a_hold_set_or_released_after_a_step_ended_never_grows_its_result(env, decided, starts):
    keys = graph(env, {"c1": "T", "c2": "T"}, [("r", "c1"), ("c1", "c2")])
    all_roles(keys)
    for key in keys.values():
        _written(env, key)
    run = command(env, "step")                                            # c1 done (its commit's approval), c2 pending
    first = result(env, run["run_id"])
    assert (first["state"], first["idle_reason"]) == ("waiting", "step_done")
    assert outcomes(first) == {keys["c1"]: "done"}                        # the post-commit approval is the step's own
    before = fixed(first)

    setting(env, FA, keys["c2"], "hold", True)                            # a person's Hold on the next card
    held = result(env, run["run_id"])
    assert fixed(held) == before                                          # end_seq, ended_at, cards, messages: the same
    assert keys["c2"] not in {m["node_key"] for m in held["messages"]}
    assert not any(m.get("code") in ("hold_set", "hold_released") for m in held["messages"])
    setting(env, FA, keys["c2"], "hold", False)
    assert fixed(result(env, run["run_id"])) == before
    assert fixed(result(env, run["run_id"], first["command_seq"])) == before

    command(env, "step")                                                  # the next command takes c2
    assert fixed(result(env, run["run_id"], first["command_seq"])) == before
    assert result(env, run["run_id"], first["command_seq"])["continues"] is False


def test_a_full_waiting_for_a_person_goes_on_with_the_save_and_completes_under_the_same_command(env, decided, starts):
    keys = graph(env, {"c1": "T", "c2": "T"}, [("r", "c1"), ("c1", "c2")])
    all_roles(keys)
    _written(env, keys["c2"])
    run = start(env)                                                      # [전체 실행]: c1 waits for its person
    waiting = result(env, run["run_id"])
    assert (waiting["state"], waiting["continues"], waiting["ended"]) == ("waiting", True, True)
    assert outcomes(waiting) == {keys["c1"]: "waiting"}
    seq = waiting["command_seq"]

    setting(env, FA, keys["c2"], "hold", True)                            # a Hold toggled while it waits: not its own
    setting(env, FA, keys["c2"], "hold", False)
    assert fixed(result(env, run["run_id"])) == fixed(waiting)

    save_instruction(env, keys["c1"])                                     # the person writes c1: the command goes on
    write_line(env, keys["c1"], "report", "## 결과\n\n사람이 쓴 레포트\n")
    current(env)                                                          # the screen reads the run
    done = result(env, run["run_id"])
    assert done["command_seq"] == seq and done["latest"] is True
    assert (done["state"], done["continues"], done["next_state"]) == ("completed", False, "completed")
    assert outcomes(done) == {keys["c1"]: "done", keys["c2"]: "done"}
    assert not any(m.get("code") in ("hold_set", "hold_released") for m in done["messages"])
    assert env["jobs"] == [] and starts == []


def test_a_full_waiting_for_a_person_that_fails_after_the_save_is_failed_under_the_same_command(env, decided, starts):
    keys = graph(env, {"c": "T"}, [("r", "c")], omit=("c",))
    roles(keys["c"], "not_used", "not_used", _write_wait(env, "wait_unavailable"))   # its review AI cannot run
    run = start(env)
    waiting = result(env, run["run_id"])
    assert (waiting["state"], waiting["continues"]) == ("waiting", True)
    write_line(env, keys["c"], "report", "## 결과\n\n사람이 쓴 레포트\n")  # the person writes: its review cannot start
    current(env)
    failed = result(env, run["run_id"])
    assert failed["command_seq"] == waiting["command_seq"]
    assert (failed["state"], failed["continues"]) == ("failed", False)
    assert (failed["failure"]["node_key"], failed["failure"]["code"]) == (keys["c"], "ai_unavailable")
    assert env["jobs"] == [] and starts == []


def test_the_next_command_ends_a_waiting_command(env, fork_join, starts):
    keys = graph(env, {"c1": "T", "c2": "T"}, [("r", "c1"), ("r", "c2")])
    all_roles(keys)
    run = start(env)
    first = result(env, run["run_id"])
    assert (first["state"], first["continues"]) == ("waiting", True)
    command(env, "full")                                                  # the next command: the first one is over
    after = result(env, run["run_id"], first["command_seq"])
    assert (after["continues"], after["latest"]) == (False, False)
    assert (after["end_seq"], after["nodes"], after["messages"]) == (first["end_seq"], first["nodes"], first["messages"])
    assert result(env, run["run_id"])["command_seq"] != first["command_seq"]


# ── messages: the command's own, whole ───────────────────────────────────────

def test_the_messages_are_the_command_s_own_whole_and_never_another_command_s(env, decided, starts, monkeypatch):
    keys = graph(env, {"c1": "T", "c2": "T"}, [("r", "c1"), ("c1", "c2")], omit=("c1",))
    roles(keys["c1"], "not_used", X, "not_used")
    roles(keys["c2"], "not_used", "not_used", "not_used")
    _written(env, keys["c2"])
    run = command(env, "step")                                            # command 1: c1's AI
    run_id = run["run_id"]
    assert run["status"] == "running"
    [job] = env["jobs"]
    ex = exec_of(env, keys["c1"])

    # The launch journal of command 1 (mock records): a failed launch and its restart, then 24 more AI runs.
    record(run_id, keys["c1"], "air_t5_a", 1, "call_failed", "exit_nonzero", exec_id=ex,
           stderr="Traceback: provider exited 1\nboom")
    record(run_id, keys["c1"], "air_t5_a", 2, model="claude-opus-5-5", confirmed=("claude-sonnet-5-5",), exec_id=ex,
           body=LONG_BODY)
    for i in range(24):
        record(run_id, keys["c1"], f"air_t5_m{i:02d}", 1, role="review", purpose="result_review_round",
               stage="result_review", exec_id=ex, body=f"## 검수 {i}\n\n" + "\n".join(f"{i}-{n}" for n in range(400)))
    ai_writes_result(env, monkeypatch, keys["c1"], job)
    _run_ended(WORK_RUN, provider_id=X)                                   # c1 approved (review not used): done
    assert node_run(env, keys["c1"])["status"] == "done"

    first = result(env, run_id)
    assert (first["state"], first["scope"], first["idle_reason"]) == ("waiting", "step", "step_done")
    assert outcomes(first) == {keys["c1"]: "done"}
    assert first["counts"] == {"done": 1, "failed": 0, "waiting": 0}     # the failed launch was restarted
    assert first["failure"] is None
    msgs = invocations(first)
    assert len(msgs) == 26 and {m["attribution"] for m in msgs} == {"event"}
    failed, restarted = msgs[0], msgs[1]
    assert (failed["result"], failed["body"], failed["error"], failed["body_source"]) == ("call_failed", "", True,
                                                                                          "launch")
    assert "exit_nonzero" in failed["error_text"] and "Traceback: provider exited 1\nboom" in failed["error_text"]
    assert restarted["body"] == LONG_BODY and restarted["error_text"] is None   # 3,000 lines, nothing cut
    assert restarted["requested"]["model"] == "claude-opus-5-5"
    assert restarted["confirmed"]["models"] == ["claude-sonnet-5-5"]     # requested and confirmed apart
    assert (restarted["role"], restarted["node_key"], restarted["title"]) == ("execution", keys["c1"], "T")
    assert all(m["body"].startswith(f"## 검수 {i}") and m["body"].count("\n") == 401
               for i, m in enumerate(msgs[2:]))
    assert first["roles"]["execution"] and first["roles"]["execution"][0]["ai_id"] == X
    seq1 = first["command_seq"]

    run2 = command(env, "step")                                           # command 2: c2, written
    second = result(env, run2["run_id"])
    assert second["command_seq"] != seq1 and second["latest"] is True
    assert (second["state"], second["scope"]) == ("completed", "step")
    assert outcomes(second) == {keys["c2"]: "done"}
    assert invocations(second) == []                                      # none of command 1's

    time.sleep(0.01)
    record(run_id, keys["c2"], "air_t5_late", 1, attempt="att_none")      # no execution, made after command 2 ended
    assert invocations(result(env, run_id)) == []
    again = result(env, run_id, seq1)
    assert again["latest"] is False and len(invocations(again)) == 26
    assert [m["inv_id"] for m in invocations(again)] == [m["inv_id"] for m in msgs]


def test_two_commands_and_their_records_in_one_millisecond_never_mix(env, decided, starts, monkeypatch):
    keys = graph(env, {"c1": "T", "c2": "T"}, [("r", "c1"), ("c1", "c2")], omit=("c1", "c2"))
    roles(keys["c1"], "not_used", X, "not_used")
    roles(keys["c2"], "not_used", Y, "not_used")
    run = command(env, "step")                                            # command 1: c1's AI X
    run_id = run["run_id"]
    [job1] = env["jobs"]
    ex1 = exec_of(env, keys["c1"])
    record(run_id, keys["c1"], "air_ms_1", 1, exec_id=ex1, ai=X, body="command 1 body")
    ai_writes_result(env, monkeypatch, keys["c1"], job1)
    _run_ended(WORK_RUN, provider_id=X)
    assert node_run(env, keys["c1"])["status"] == "done"
    command(env, "step")                                                  # command 2: c2's AI Y
    ex2 = exec_of(env, keys["c2"])
    assert ex2 != ex1
    record(run_id, keys["c2"], "air_ms_2", 1, "call_failed", "exit_nonzero", exec_id=ex2, ai=Y,
           stderr="command 2 boom")
    record(run_id, keys["c2"], "air_ms_free", 1, attempt="att_none", ai=Y, role="review",   # a record of no execution
           purpose="result_review_round", stage="result_review")

    # Every event and every record in one and the same millisecond (and one creation order value).
    _sql(env["db"], "UPDATE graph_run_events SET created_at = ?", (ONE_MS,))
    _sql(env["db"], "UPDATE ai_role_invocations SET created_at = ?, started_at = ?, ended_at = ?, create_seq = 1",
         (ONE_MS, ONE_MS, ONE_MS))

    one = result(env, run_id, None)
    seqs = sorted({one["command_seq"]})
    starts_ = [e["event_seq"] for e in _sql(env["db"], "SELECT event_seq FROM graph_run_events WHERE event_type LIKE "
                                                       "'%run_status%' AND cause IN ('user_start', 'user_command') "
                                                       "ORDER BY event_seq")]
    assert len(starts_) == 2 and seqs == [starts_[1]]
    first, second = result(env, run_id, starts_[0]), result(env, run_id, starts_[1])

    assert [m["inv_id"] for m in invocations(first)] == ["air_ms_1#1"]
    assert invocations(first)[0]["body"] == "command 1 body"
    assert outcomes(first) == {keys["c1"]: "done"}
    assert [u["ai_id"] for u in first["roles"]["execution"]] == [X]
    assert first["failure"] is None and first["failures"] == [] and first["state"] == "waiting"

    assert [m["inv_id"] for m in invocations(second) if m["attribution"] == "event"] == ["air_ms_2#1"]
    assert "air_ms_1#1" not in {m["inv_id"] for m in invocations(second)}
    assert keys["c1"] not in outcomes(second) and keys["c2"] in outcomes(second)
    assert [u["ai_id"] for u in second["roles"]["execution"]] == [Y]
    assert [(f["node_key"], f["code"]) for f in second["failures"]] == [(keys["c2"], "call_failed")]
    assert "command 2 boom" in invocations(second)[0]["error_text"]
    free = [m for m in invocations(second) if m["inv_id"] == "air_ms_free#1"]   # by its time: one command only
    assert [m["attribution"] for m in free] == ["time"]
    assert "air_ms_free#1" not in {m["inv_id"] for m in invocations(first)}


# ── each launch: its own body and error ──────────────────────────────────────

def test_each_launch_of_one_ai_run_shows_its_own_body_and_error_and_copy_keeps_them_apart(env, decided, starts,
                                                                                            monkeypatch):
    keys = graph(env, {"c": "T"}, [("r", "c")], omit=("c",))
    roles(keys["c"], "not_used", X, "not_used")
    run = command(env, "full")
    run_id = run["run_id"]
    ex = exec_of(env, keys["c"])
    # One AI run: two failed launches with different errors, then the success (retries of one request).
    record(run_id, keys["c"], "air_l", 1, "call_failed", "exit_nonzero", exec_id=ex, body="first partial",
           stderr="first boom")
    record(run_id, keys["c"], "air_l", 2, "call_failed", "timeout", exec_id=ex, stderr="second boom")
    record(run_id, keys["c"], "air_l", 3, exec_id=ex, body="## 세 번째\n\n성공 본문")
    # One review loop run: a review round and its rework, each with its own message.
    record(run_id, keys["c"], "air_rv", 1, role="review", purpose="result_review_round", stage="result_review",
           exec_id=ex, body="검수 1: 반려")
    record(run_id, keys["c"], "air_rv", 2, role="rework", purpose="result_rework", stage="result_review", exec_id=ex,
           body="재작업 본문")
    # A run recorded before the launch output store: two launches, no output rows, only the run's final row.
    record(run_id, keys["c"], "air_old", 1, "call_failed", "exit_nonzero", exec_id=ex)
    record(run_id, keys["c"], "air_old", 2, exec_id=ex)
    _ai_run_row(env, "air_old", "OLD FINAL MESSAGE", stderr_tail="old run stderr", stop_code="exited")

    r = result(env, run_id)
    by = {m["inv_id"]: m for m in invocations(r)}
    assert len(by) == 7
    assert (by["air_l#1"]["body"], by["air_l#1"]["body_source"]) == ("first partial", "launch")
    assert "first boom" in by["air_l#1"]["error_text"] and "second boom" not in by["air_l#1"]["error_text"]
    assert by["air_l#2"]["body"] == "" and "second boom" in by["air_l#2"]["error_text"]
    assert "first boom" not in by["air_l#2"]["error_text"] and "timeout" in by["air_l#2"]["error_text"]
    assert (by["air_l#3"]["body"], by["air_l#3"]["error_text"]) == ("## 세 번째\n\n성공 본문", None)
    assert (by["air_rv#1"]["body"], by["air_rv#2"]["body"]) == ("검수 1: 반려", "재작업 본문")
    assert (by["air_rv#1"]["role"], by["air_rv#2"]["role"]) == ("review", "rework")
    old1, old2 = by["air_old#1"], by["air_old#2"]
    assert (old1["body"], old1["body_source"]) == ("", "not_preserved")   # never the run's final output
    assert "launch_output: not_preserved" in old1["error_text"] and "old run stderr" not in old1["error_text"]
    assert (old2["body"], old2["body_source"]) == ("OLD FINAL MESSAGE", "run")   # the run's last launch: its final row
    bodies = [m["body"] for m in invocations(r)]
    assert bodies.count("OLD FINAL MESSAGE") == 1 and bodies.count("## 세 번째\n\n성공 본문") == 1


def test_the_launch_journal_stores_each_launch_s_own_output(env):
    """The journal (``invocations``) stores per launch only what that launch left: a later launch that wrote no message
    keeps none of the earlier one's, and each launch keeps its own stderr."""
    from flowgate.ai_roles import invocations, record_store

    run = {"run_id": "air_journal", "cancel_event": threading.Event(),
           "invocation_origin": {"origin_kind": "card_assigned", "purpose": "result_write", "role_source": "card_explicit",
                                 "requested_ai_id": X,
                                 "target": {"flow_id": FA, "graph_run_id": "run_journal", "node_key": "gn_journal",
                                            "attempt_id": "att_journal", "stage": "result", "seq": 0}}}
    provider = {"id": X, "name": "AI " + X, "exec_type": "cli", "kind": "claude", "cli_command": "claude"}
    invocations.begin(run, provider)
    run.update(last_message="launch 1 message", stderr_tail="launch 1 stderr", register_errors=["e1"])
    invocations.cli_outcome(run, stdout_full="", exit_code=1, timed_out=False, process_exited_itself=True)
    invocations.finish(run, "started_ok")
    invocations.begin(run, provider)
    run["stderr_tail"] = "launch 2 stderr"                                # no new message: launch 1's stays in the run
    invocations.cli_outcome(run, stdout_full="", exit_code=0, timed_out=False, process_exited_itself=True)
    invocations.finish(run, "started_ok")
    invocations.finalize(run)

    rows = record_store.by_run("air_journal")
    assert [(r["launch_no"], r["result"]) for r in rows] == [(1, "call_failed"), (2, "succeeded")]
    out = record_store.outputs_for(["air_journal#1", "air_journal#2"])
    assert (out["air_journal#1"]["body"], out["air_journal#1"]["stderr_tail"]) == ("launch 1 message", "launch 1 stderr")
    assert json.loads(out["air_journal#1"]["register_errors_json"]) == ["e1"]
    assert (out["air_journal#2"]["body"], out["air_journal#2"]["stderr_tail"]) == (None, "launch 2 stderr")
    assert out["air_journal#2"]["register_errors_json"] is None


def test_an_unknown_command_is_404_and_a_malformed_one_422(env, decided, starts):
    keys = graph(env, {"c": "T"}, [("r", "c")])
    all_roles(keys)
    run = start(env)
    body = result(env, run["run_id"], 99999, expect=404)
    assert body["error"]["code"] == "command_not_found"
    body = result(env, run["run_id"], raw="abc", expect=422)
    assert body["error"]["code"] == "invalid_request"
    res = env["client"].get("/api/v1/runs/run_missing/command-result", headers=_h())
    assert res.status_code == 404 and res.json()["error"]["code"] == "run_not_found"


# ── migration 168 ─────────────────────────────────────────────────────────────

def test_migration_168_is_the_same_table_in_three_dialects_and_runs_twice():
    import sqlite3
    from pathlib import Path

    root = Path(__file__).resolve().parents[2] / "sql" / "migrations"
    for dialect in ("sqlite", "postgres", "mysql"):
        sql = (root / dialect / "168_ai_role_invocation_outputs.sql").read_text(encoding="utf-8")
        assert "CREATE TABLE IF NOT EXISTS ai_role_invocation_outputs" in sql
        for column in ("inv_id", "ai_run_id", "launch_no", "body", "stderr_tail", "stdout_tail", "register_errors_json",
                       "detail", "created_at"):
            assert column in sql, (dialect, column)
        ddl = sql[sql.index("CREATE TABLE"):].split("-- ── DOWN")[0]
        assert "REFERENCES" not in ddl and "FOREIGN KEY" not in ddl and "DELETE" not in ddl
    conn = sqlite3.connect(":memory:")
    script = (root / "sqlite" / "168_ai_role_invocation_outputs.sql").read_text(encoding="utf-8")
    conn.executescript(script)
    conn.execute("INSERT INTO ai_role_invocation_outputs (inv_id, created_at) VALUES ('a#1', 't')")
    conn.executescript(script)                                           # again: the row stays
    assert conn.execute("SELECT COUNT(*) FROM ai_role_invocation_outputs").fetchone()[0] == 1
