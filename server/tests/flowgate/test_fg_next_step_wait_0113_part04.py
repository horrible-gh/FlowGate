"""Independently selectable scenarios; shared fixtures remain in test_fg_next_step_wait_0113.py."""
from __future__ import annotations

from test_fg_next_step_wait_0113 import (
    A,
    AI,
    B,
    BODY,
    FA,
    INSTRUCTION_RUN,
    NS,
    QUESTION,
    REVIEW_MODEL,
    REVIEW_RUN,
    ROOT,
    SAVED_REPORT,
    X,
    _admin_and_reviewer,
    _again,
    _ai_instruction,
    _ai_run_finished,
    _current,
    _ended,
    _executions,
    _h,
    _lost_instruction,
    _lost_result,
    _review_status,
    _run_one_card,
    _run_row,
    _sql,
    _start,
    _start_ai,
    _step,
    _submitted,
    _wire,
    _write_written,
    assignments,
    b1,
    db_path,
    decided,
    env,
    events,
    flow,
    launches,
    legacy_cards,
    legacy_setting,
    line_doc,
    node_run,
    one,
    pre_feature_attempts,
    pytest,
    questions,
    roles,
    save_instruction,
    start,
    starts,
    stored,
    threading,
    time,
    tokens,
    traceback,
    wired,
    write_line,
)

@pytest.mark.parametrize("stage", ["instruction", "result"])
def test_a_submission_without_its_review_assignment_is_reviewed_once_by_its_review_ai_and_its_model(
        env, decided, monkeypatch, launches, stage):
    if stage == "instruction":
        key, doc_id = _lost_instruction(env, B)
    else:
        key, doc_id = _lost_result(env, monkeypatch, B)
    review = "instruction_review" if stage == "instruction" else "result_review"

    run = _current(env)                                          # the run read's reconciliation (RC-6 / RC-3)
    a = one(env, key, review, 1)
    assert (a["actor"], a["provider_id"], a["author"]) == ("ai", B, "person")
    assert a["basis_doc_id"] == doc_id and a["intent_kind"] == "execution"
    [request] = launches["requests"]                             # one request, to the assigned provider
    assert (request["document_review_loop"]["reviewer_provider_id"], request["doc_ref"]) == (B, doc_id)
    [spawn] = launches["spawns"]                                 # one launch, down to the (spy) process spawn
    assert spawn["provider_id"] == B and f"--model {REVIEW_MODEL}" in spawn["command"]
    [record] = launches["runs"][0]["invocations"]                # the launch's own evidence of what it asked for
    assert (record["requested_ai_id"], record["actual_ai_id"]) == (B, B)
    assert (record["requested_model_state"], record["requested_model_value"]) == ("value", REVIEW_MODEL)
    assert record["purpose"] == ("instruction_review_round" if stage == "instruction" else "result_review_round")
    assert (record["target_stage"], record["target_seq"], record["target_doc_id"]) == (review, 1, doc_id)
    assert _review_status(doc_id) == "pending_review" and run["idle_reason"] != "no_targets"

    # an existing assignment wins: the card's review AI changed since asks nobody else
    if stage == "instruction":
        stored(key, "instruction_review_ai", A)
    else:
        roles(key, "not_used", "not_used", A)
    before = assignments(env, key)
    run = _again(env, key, stage, doc_id)
    assert assignments(env, key) == before
    assert len(launches["requests"]) == 1 and len(launches["spawns"]) == 1
    assert _review_status(doc_id) == "pending_review" and run["idle_reason"] != "no_targets"
@pytest.mark.parametrize("stage", ["instruction", "result"])
def test_a_submission_without_its_review_assignment_whose_review_is_not_used_goes_on_without_a_person(
        env, decided, monkeypatch, launches, stage):
    if stage == "instruction":
        key, doc_id = _lost_instruction(env, "not_used")
    else:
        key, doc_id = _lost_result(env, monkeypatch, "not_used")
    review = "instruction_review" if stage == "instruction" else "result_review"

    _current(env)
    a = one(env, key, review, 1)
    assert (a["actor"], a["verdict"]) == ("no_review", "auto_approved")
    assert _review_status(doc_id) == "approved"
    row = node_run(env, key)
    if stage == "instruction":
        assert (row["status"], row["stage"]) == ("open", "result")   # the result stage, waiting for its report
    else:
        assert row["status"] == "done"

    def state():
        return (assignments(env, key), _sql(env["db"], "SELECT doc_id, doc_review_status FROM documents ORDER BY doc_id"),
                _sql(env["db"], "SELECT node_key, status, stage FROM graph_node_runs ORDER BY node_key"))

    before = state()
    _again(env, key, stage, doc_id)
    assert state() == before                                     # no second approval, assignment or node change
    assert launches["requests"] == [] and launches["spawns"] == [] and env["jobs"] == []
@pytest.mark.parametrize("stage", ["instruction", "result"])
def test_a_submission_without_its_review_assignment_whose_setting_is_unreadable_waits_with_its_cause(
        env, decided, monkeypatch, launches, stage):
    if stage == "instruction":
        key, doc_id = _lost_instruction(env, "unreadable")
    else:
        key, doc_id = _lost_result(env, monkeypatch, "unreadable")
    review = "instruction_review" if stage == "instruction" else "result_review"

    run = _current(env)
    a = one(env, key, review, 1)
    if stage == "instruction":
        assert (a["actor"], a["actor_reason"]) == ("wait_setup", "setting_unreadable")
    else:
        assert (a["actor"], a["source"]) == ("wait_setup", "store_error")
    # a wait with its cause, never "nothing to run"; the earlier change the earlier implementation (T-2 rework): the instruction's
    # ``review_result_unknown`` is a system error (the error family), the result's store error a setup wait
    assert run["idle_reason"] == ("error" if stage == "instruction" else "needs_intervention")
    wait = _wire(run, key)["wait_reason"]
    assert (wait["code"], wait["role"]) == ("ai_setup_required", "review")
    assert stage == "instruction" or wait["detail"] == "store_error"
    before = assignments(env, key)
    run = _again(env, key, stage, doc_id)
    if stage == "instruction":                                   # read again up to the limit, then never
        reviews = [a for a in assignments(env, key) if a["stage"] == review]
        assert [(a["seq"], a["actor"], a["actor_reason"], a["stage_state"]) for a in reviews] == \
            [(1, "wait_setup", "setting_unreadable", "failed"), (2, "wait_setup", "setting_unreadable", "failed"),
             (3, "wait_setup", "setting_unreadable", "waiting")]
        assert run["idle_reason"] == "error"
        assert _wire(run, key)["intervention"] == {"kind": "system_error", "reason": "review_result_unknown"}
        after = assignments(env, key)
        _current(env)
        assert assignments(env, key) == after
    else:
        assert assignments(env, key) == before and run["idle_reason"] == "needs_intervention"
        assert _wire(run, key)["intervention"] == {"kind": "needs_intervention", "reason": "completion_missing"}
    assert _review_status(doc_id) == "pending_review"            # nothing approved it, no person's approval added
    assert launches["requests"] == [] and launches["spawns"] == [] and env["jobs"] == []
