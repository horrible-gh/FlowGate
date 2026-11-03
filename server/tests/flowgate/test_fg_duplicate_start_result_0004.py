"""flowgate.v02test.0004 T#3 / T#4 — the command result of a card whose execution was started twice.

B flowgate.v02test.0004.0001-R: a duplicate start's refusal stored before T#1 (``admission_refused`` /
``run_in_progress`` of the same execution while its launch ran) made the command result show
"오류 전문: admission_refused (run_in_progress)" and fail the command, though the card's AI ran once and succeeded.

Now the command result leaves such a stored record out — told by its execution and its launch's own times
(``command_result.duplicate_starts``), never by the message — and the stored row stays as it is. A refusal no launch of
its execution covered stays the command's failure, as before (no blanket hiding of ``admission_refused``).

The harness of ``test_fg_command_result_0113`` (real routes, Graph engine, SQLite DB; records written as the launch
journal writes them). Nothing calls an AI.
"""
from __future__ import annotations

import pytest
from fastapi import HTTPException

from test_fg_ai_roles_l2_assignment import (  # noqa: F401 — fixtures used by name
    FA, X, _sql, command, db_path, decided, env, roles, starts, wired,
)
from test_fg_command_result_0113 import exec_of, invocations, record, result
from test_fg_document_progress_0113 import graph

from flowgate.ai_roles import invocations as inv
from flowgate.graph_run import command_result

T_START, T_REFUSED, T_END, T_LATE = ("2026-11-03T02:31:06.167+00:00", "2026-11-03T02:31:06.300+00:00",
                                     "2026-11-03T02:45:29.000+00:00", "2026-11-03T02:46:00.000+00:00")
BUSY = {"code": "run_in_progress", "message": "An AI run is already in progress."}


def _stored_refusal(env, run_id, key, exec_id, at):
    """A start refusal as the launch journal stored it before T#1 (``invocations.refused``), made at ``at``."""
    origin = {"origin_kind": "card_assigned", "purpose": "result_write_retry", "requested_ai_id": X,
              "role_source": "card_explicit",
              "target": {"flow_id": FA, "graph_run_id": run_id, "node_key": key, "attempt_id": "att_t5",
                         "stage": "result", "seq": 0, "doc_id": None, "doc_revision_no": None}}
    inv.refused(origin, HTTPException(409, BUSY), exec_id)
    [row] = _sql(env["db"], "SELECT inv_id FROM ai_role_invocations WHERE graph_exec_id = ? "
                            "AND result = 'admission_refused' ORDER BY create_seq DESC LIMIT 1", (exec_id,))
    _sql(env["db"], "UPDATE ai_role_invocations SET created_at = ?, ended_at = ? WHERE inv_id = ?",
         (at, at, row["inv_id"]))
    return row["inv_id"]


def _one_card(env):
    keys = graph(env, {"c": "T"}, [("r", "c")], omit=("c",))
    roles(keys["c"], "not_used", X, "not_used")
    run_id = command(env, "step")["run_id"]
    return keys["c"], run_id, exec_of(env, keys["c"])


def _launched(env, run_id, key, exec_id, ai_run_id="aiv_20261103_003400", result_value="succeeded", end=T_END):
    rec = record(run_id, key, ai_run_id, 1, result_value, None if result_value == "succeeded" else "exit_nonzero",
                 exec_id=exec_id, purpose="result_write_retry", body="조사 결과")
    _sql(env["db"], "UPDATE ai_role_invocations SET started_at = ?, ended_at = ? WHERE inv_id = ?",
         (T_START, end, rec["inv_id"]))
    return rec["inv_id"]


def test_a_stored_duplicate_start_refusal_is_no_failure_nor_message_of_the_command(env, decided, starts):
    key, run_id, exec_id = _one_card(env)
    launched = _launched(env, run_id, key, exec_id)
    refusal = _stored_refusal(env, run_id, key, exec_id, T_REFUSED)            # 133 ms after the launch began

    r = result(env, run_id)
    assert [m["inv_id"] for m in invocations(r)] == [launched]
    assert r["failures"] == [] and r["failure"] is None and r["state"] != "failed"
    assert not any("admission_refused" in (m.get("error_text") or "") for m in r["messages"])
    # the stored row is kept as it was
    [row] = _sql(env["db"], "SELECT result, result_detail, graph_exec_id FROM ai_role_invocations WHERE inv_id = ?",
                 (refusal,))
    assert (row["result"], row["result_detail"], row["graph_exec_id"]) == ("admission_refused", "run_in_progress",
                                                                           exec_id)


def test_a_refusal_no_launch_of_its_execution_covered_stays_the_command_s_failure(env, decided, starts):
    key, run_id, exec_id = _one_card(env)
    _launched(env, run_id, key, exec_id)
    refusal = _stored_refusal(env, run_id, key, exec_id, T_LATE)               # after the launch ended

    r = result(env, run_id)
    assert refusal in [m["inv_id"] for m in invocations(r)]
    assert [(f["node_key"], f["code"]) for f in r["failures"]] == [(key, "admission_refused")]
    shown = [m for m in invocations(r) if m["inv_id"] == refusal][0]
    assert shown["error"] is True and "admission_refused (run_in_progress)" in shown["error_text"]


def test_a_refusal_of_an_execution_never_launched_stays_the_command_s_failure(env, decided, starts):
    key, run_id, exec_id = _one_card(env)
    refusal = _stored_refusal(env, run_id, key, exec_id, T_REFUSED)

    r = result(env, run_id)
    assert [m["inv_id"] for m in invocations(r)] == [refusal]
    assert [(f["node_key"], f["code"]) for f in r["failures"]] == [(key, "admission_refused")]


def _rec(inv_id, exec_id, result_value, *, created=None, started=None, ended=None, ai_run_id=None, launch_no=None,
         detail=None):
    return {"inv_id": inv_id, "graph_exec_id": exec_id, "result": result_value, "result_detail": detail,
            "created_at": created or started, "started_at": started, "ended_at": ended, "ai_run_id": ai_run_id,
            "launch_no": launch_no}


@pytest.mark.parametrize("refusal,expected", [
    (_rec("dup", "e1", "admission_refused", created=T_REFUSED, detail="run_in_progress"), {"dup"}),
    (_rec("late", "e1", "admission_refused", created=T_LATE, detail="run_in_progress"), set()),
    (_rec("early", "e1", "admission_refused", created="2026-11-03T02:31:06.000+00:00", detail="run_in_progress"),
     set()),
    (_rec("other_exec", "e2", "admission_refused", created=T_REFUSED, detail="run_in_progress"), set()),
    (_rec("no_exec", None, "admission_refused", created=T_REFUSED, detail="run_in_progress"), set()),
    (_rec("other_code", "e1", "admission_refused", created=T_REFUSED, detail="graph_worktree_unavailable"), set()),
])
def test_duplicate_starts_are_told_by_their_execution_and_its_launch_times(refusal, expected):
    launch = _rec("aiv#1", "e1", "succeeded", started=T_START, ended=T_END, ai_run_id="aiv", launch_no=1)
    assert command_result.duplicate_starts([launch, refusal]) == expected


def test_a_launch_still_running_covers_a_refusal_made_after_it_began():
    running = _rec("aiv#1", "e1", "running", started=T_START, ended=None, ai_run_id="aiv", launch_no=1)
    refusal = _rec("dup", "e1", "admission_refused", created=T_LATE, detail="run_in_progress")
    assert command_result.duplicate_starts([running, refusal]) == {"dup"}
    failed = _rec("aiv#1", "e1", "call_failed", started=T_START, ended=T_END, ai_run_id="aiv", launch_no=1)
    covered = _rec("dup", "e1", "admission_refused", created=T_REFUSED, detail="run_in_progress")
    assert command_result.duplicate_starts([failed, covered]) == {"dup"}     # covered by a launch that failed too
