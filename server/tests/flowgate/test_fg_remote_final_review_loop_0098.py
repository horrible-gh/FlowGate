"""Remote terminal failure closes the loop before an explicit full command retries it."""
from __future__ import annotations

import os

import pytest


@pytest.fixture(scope="module", autouse=True)
def restore_agent_pepper_environment():
    # The shared Graph harness initializes this lazily; keep other suites' value.
    key = "FLOWGATE_AGENT_PEPPER_ID"
    before = os.environ.get(key)
    yield
    if before is None:
        os.environ.pop(key, None)
    else:
        os.environ[key] = before

from test_fg_command_rerequest_0124 import (  # noqa: F401 - shared fixtures
    REVIEW_RUN, SPENT, _chain, _loop_row, _loops, _read, _reviewed_ai_result,
    _run_ended, _sql, _wire, assignments, b1, command, db_path, decided, env,
    events, fork_join, one, record_starts, starts, wired,
)
from modules.flow_gate.services.ai_invoke import review


@pytest.mark.parametrize("maximum,used,stage", [(2, 1, "review"), (-1, 9, "review"), (0, 1, "stopped"), (2, 3, "stopped")])
def test_ordinary_failure_retains_retry_policy(maximum, used, stage):
    state = review.resolve_document_review_loop_gate({
        "current_stage": "review", "last_hop_outcome": "failed",
        "attempts_used": used, "failure_restart_max_attempts": maximum,
        "failure_detail": "ordinary failure",
    })
    assert state["current_stage"] == stage
    assert state["stop_reason"] == ("retry_exhausted" if stage == "stopped" else None)


@pytest.mark.parametrize("failure,detail", [
    ({"cause": "model_not_found", "reason": "job_failed", "code": "remote_failed"}, "model_not_found"),
    ({"reason": "agent_disconnected", "code": "remote_failed"}, "agent_disconnected"),
    ({"code": "remote_spawn_failed"}, "remote_spawn_failed"),
    (True, "all_providers_failed"),
])
def test_remote_failure_preserves_specific_cause(failure, detail):
    state = review.resolve_document_review_loop_gate({
        "current_stage": "review", "last_hop_outcome": "failed",
        "attempts_used": 1, "failure_restart_max_attempts": -1,
        "remote_final_failure": failure, "failure_detail": "all_providers_failed",
    })
    assert state["current_stage"] == "stopped"
    assert state["stop_reason"] == "retry_exhausted"
    assert state["stop_detail"] == "remote final failure: " + detail
    assert state["attempts_used"] == 1


@pytest.mark.parametrize("maximum", [2, -1])
def test_remote_failure_checkpoints_stop_and_full_command_requests_review_again(
        env, decided, monkeypatch, starts, fork_join, maximum):
    from modules.flow_gate.db import ai_invoke_document_review_loops as db_loops
    from flowgate.card_intervention import reasons

    a, _a2, _a3 = _chain(env)
    command(env, "full")
    _key, result, assignment = _reviewed_ai_result(env, monkeypatch, starts, a)
    _loop_row(result, REVIEW_RUN, "review")
    _sql(env["db"], "UPDATE ai_invoke_document_review_loops SET deadline_at = ?, "
         "failure_restart_max_attempts = ? WHERE run_id = ?",
         ("2099-01-01T00:00:00+00:00", maximum, REVIEW_RUN))
    run = {
        "run_id": REVIEW_RUN, "doc_ref": result, "outcome": "none",
        "end_reason": "all_providers_failed", "last_message": "provider failed",
        "remote_final_failure": {"cause": "model_not_found", "reason": "job_failed"},
        "document_review_loop": db_loops.get(REVIEW_RUN),
    }
    latest = review._checkpoint_document_review_loop(run)
    saved = db_loops.get(REVIEW_RUN)
    assert latest == saved == run["document_review_loop"]
    assert saved["current_stage"] == "stopped"
    assert saved["stop_reason"] == "retry_exhausted"
    assert "model_not_found" in saved["stop_detail"]
    assert saved["attempts_used"] == 1
    assert saved["last_hop_outcome"] == "failed"

    # Finish the execution using the checkpoint's real stored result, without
    # an orphan sweep or replacing the loop row with a manufactured stop.
    _sql(env["db"], "UPDATE graph_attempt_runs SET ai_run_id = ?, status = 'ended', "
         "ended_at = 't' WHERE exec_id = ?", (REVIEW_RUN, assignment["intent_exec_id"]))
    _run_ended(REVIEW_RUN, action_scope="review", doc_ref=result,
               graph_exec_id=assignment["intent_exec_id"], document_review_loop=saved)
    before = len(_loops(starts))
    _read(env)
    assert len(_loops(starts)) == before
    intervention = _wire(env, a)["intervention"]
    assert intervention == {
        "kind": "needs_intervention", "reason": SPENT,
        "stop_detail": saved["stop_detail"],
    }
    assert _read(env)["idle_reason"] == "needs_intervention"
    from flowgate.graph_read import service as graph_read
    assert graph_read.node_run(_wire(env, a), set())["intervention"] == intervention
    assert reasons.command_rerequest_due(SPENT)
    assert not reasons.retry_due([SPENT])
    first = one(env, a, "result_review", 1)
    assert first["verdict"] == "not_passed:retry_exhausted"
    command(env, "full")
    second = one(env, a, "result_review", 2)
    assert second is not None and second["stage_state"] == "active"
    assert second["basis_doc_id"] == result
    assert len(_loops(starts)) == before + 1
    assert one(env, a, "result_review", 1) == first
    assert db_loops.get(REVIEW_RUN) == saved
