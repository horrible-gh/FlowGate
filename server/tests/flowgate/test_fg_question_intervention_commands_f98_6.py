"""F98-6: real assignments/commands, with a controlled question reader."""
import os
import pytest

@pytest.fixture(scope="module", autouse=True)
def restore_agent_pepper_environment():
    key = "FLOWGATE_AGENT_PEPPER_ID"
    before = os.environ.get(key)
    yield
    if before is None:
        os.environ.pop(key, None)
    else:
        os.environ[key] = before

from test_fg_command_rerequest_0124 import (  # noqa: F401
    FA, SECOND_LOOP, _spent, _loop_row, _loop_end, _read, _wire, _loops, _sql,
    assignments, b1, command, db_path, decided, env, events, fork_join, one,
    record_starts, starts, wired, node_run, _review_status,
)

@pytest.mark.parametrize("mode", ["step", "full"])
def test_second_loop_question_blocks_rerequest_then_exhaustion_completes(
        env, decided, monkeypatch, starts, fork_join, mode):
    from flowgate.card_settings import legacy_adapter, assigned_review
    from modules.flow_gate.db import ai_invoke_document_review_loops as db_loops
    a, _, _, result, first = _spent(env, monkeypatch, starts)
    assert _wire(env, a)["intervention"]["reason"] == "review_not_passed:retry_exhausted"
    command(env, mode)
    second = one(env, a, "result_review", 2)
    _loop_row(result, SECOND_LOOP, "rework")
    _sql(env["db"], "UPDATE ai_invoke_document_review_loops SET started_at = 'z' WHERE run_id = ?", (SECOND_LOOP,))
    questions = [{"id": 1, "anchor_doc_id": result, "asker_ai_run_id": SECOND_LOOP}]
    original = legacy_adapter.open_questions_of_ai_run
    monkeypatch.setattr(legacy_adapter, "open_questions_of_ai_run",
                        lambda f, r: list(questions) if r == SECOND_LOOP else original(f, r))
    _loop_end(env, second["intent_exec_id"], result, SECOND_LOOP, "review_count_exhausted")
    assert one(env, a, "result_review", 2)["verdict"] is None
    assert _wire(env, a)["intervention"] is None
    assert _read(env)["idle_reason"] in ("no_targets", "step_done")
    before = len(_loops(starts))
    command(env, mode)
    assert len(_loops(starts)) == before
    assert one(env, a, "result_review", 3) is None
    assert one(env, a, "result_review", 1) == first
    questions.clear()
    assigned_review.consume_loop_verdict(FA, one(env, a, "result_review", 2), db_loops.get(SECOND_LOOP),
                                         assigned_review.HELD, [])
    assert node_run(env, a)["status"] == "done"
    assert _review_status(result) == "approved"
    assert one(env, a, "result_review", 2)["verdict"] == "auto_approved"
