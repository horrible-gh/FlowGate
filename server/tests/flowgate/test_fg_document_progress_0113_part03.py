"""Independently selectable scenarios; shared fixtures remain in test_fg_document_progress_0113.py."""
from __future__ import annotations

from test_fg_document_progress_0113 import (
    A,
    B,
    EDITED_REPORT,
    FA,
    INSTRUCTION_BODY,
    NEW_REPORT,
    REPORT_BODY,
    ROWS,
    WORK_RUN,
    X,
    Y,
    _BrokenBaselineStore,
    _active_attempt,
    _ai_writes_instruction,
    _approved_report_card,
    _break_baseline,
    _events,
    _h,
    _review_end,
    _review_status,
    _row6_person_card,
    _row_card,
    _run_ended,
    _saved,
    _sql,
    _token_state,
    _waits_for_its_report,
    _write_wait,
    _write_written,
    _written,
    ai_writes_result,
    all_roles,
    assignments,
    baseline_rows,
    command,
    current,
    db_path,
    decided,
    doc_row,
    edge,
    env,
    fork_join,
    graph,
    line_doc,
    loops,
    node,
    node_run,
    one,
    prewrite,
    put_draft,
    pytest,
    record_starts,
    roles,
    run_row,
    save_instruction,
    start,
    starts,
    status,
    stored,
    taken_judged_events,
    to_role,
    tokens,
    wire,
    wired,
    write_jobs,
    write_line,
)

def test_a_review_ai_with_a_stored_zero_is_one_round(env, decided, starts, monkeypatch):
    keys = graph(env, {"c": "T"}, [("r", "c")], omit=("c",), settings=(("c", "review_count", 0),))
    key = keys["c"]
    roles(key, "not_used", X, B)
    start(env)
    [job] = write_jobs(env)
    ai_writes_result(env, monkeypatch, key, job)
    record_starts(monkeypatch, starts)
    _run_ended(WORK_RUN, provider_id=X)
    rr = one(env, key, "result_review", 1)
    assert (rr["actor"], rr["provider_id"], rr["author"], rr["review_rounds"], rr["rework_provider_id"]) == \
        ("ai", B, "ai", 1, X)
    assert len(loops(starts)) == 1
def test_full_on_a_linear_flow_stops_at_its_first_unwritten_card_and_never_opens_the_next(env, decided, starts):
    keys = graph(env, {"c1": "T", "c2": "T", "c3": "T", "c4": "T"},
                 [("r", "c1"), ("c1", "c2"), ("c2", "c3"), ("c3", "c4")])
    all_roles(keys)
    for ref in ("c1", "c2", "c4"):
        _written(env, keys[ref])
    start(env)                                                           # [전체 실행]
    assert [status(env, keys[r]) for r in ("c1", "c2", "c3", "c4")] == ["done", "done", "open", "pending"]
    run = current(env)
    assert wire(run, keys["c3"])["wait_reason"]["code"] == "manual_write_wait"
    assert (run["status"], run["idle_reason"]) == ("idle", "needs_intervention")
    assert write_jobs(env) == [] and starts == []
def test_full_on_parallel_branches_waits_at_every_unwritten_card_at_once_and_the_join_waits(env, fork_join, starts):
    cards = {r: "T" for r in ("c1", "c21", "c22", "c23", "c31", "c32", "c33", "c4")}
    links = [("r", "c1"), ("c1", "c21"), ("c1", "c22"), ("c1", "c23"), ("c21", "c31"), ("c22", "c32"),
             ("c23", "c33"), ("c31", "c4"), ("c32", "c4"), ("c33", "c4")]
    keys = graph(env, cards, links)
    all_roles(keys)
    for ref in ("c1", "c21", "c22", "c23", "c32", "c4"):
        _written(env, keys[ref])
    start(env)
    got = {ref: status(env, keys[ref]) for ref in cards}
    assert got == {"c1": "done", "c21": "done", "c22": "done", "c23": "done", "c31": "open", "c32": "done",
                   "c33": "open", "c4": "pending"}
    run = current(env)
    assert {wire(run, keys[r])["wait_reason"]["code"] for r in ("c31", "c33")} == {"manual_write_wait"}
    assert run["idle_reason"] == "needs_intervention"                     # not complete: two branches wait
    assert write_jobs(env) == [] and starts == []

    save_instruction(env, keys["c31"])                                   # one branch written: the join still waits
    write_line(env, keys["c31"], "report", NEW_REPORT)
    assert status(env, keys["c31"]) == "done" and status(env, keys["c4"]) == "pending"
def test_full_on_a_written_manual_flow_completes_with_no_ai_call(env, decided, starts):
    keys = graph(env, {"c1": "T", "c2": "T", "c3": "T"}, [("r", "c1"), ("c1", "c2"), ("c2", "c3")])
    all_roles(keys)
    for key in keys.values():
        _written(env, key)
    start(env)
    assert [status(env, keys[r]) for r in ("c1", "c2", "c3")] == ["done", "done", "done"]
    assert (run_row(env)["status"], run_row(env)["idle_reason"]) == ("idle", "completed")
    assert write_jobs(env) == [] and starts == [] and _sql(env["db"], "SELECT * FROM graph_attempt_runs") == []
def test_full_on_a_written_flow_with_a_review_ai_reviews_each_card_then_completes(env, decided, starts, monkeypatch):
    from flowgate.card_settings import legacy_adapter

    keys = graph(env, {"c1": "T", "c2": "T", "c3": "T"}, [("r", "c1"), ("c1", "c2"), ("c2", "c3")])
    all_roles(keys, review=B)
    for key in keys.values():
        _written(env, key)
    monkeypatch.setattr(legacy_adapter, "submission_passed", lambda doc_id: True)
    start(env)
    for i, ref in enumerate(("c1", "c2", "c3"), 1):
        rr = one(env, keys[ref], "result_review", 1)
        assert (rr["actor"], rr["provider_id"]) == ("ai", B) and len(loops(starts)) == i
        assert status(env, keys[ref]) == "review"
        _review_end(env, rr, "review_passed", f"air_review_{ref}")
        assert status(env, keys[ref]) == "done"
    assert run_row(env)["idle_reason"] == "completed" and write_jobs(env) == []
def test_step_keeps_its_targets_on_a_written_flow(env, decided, starts):
    """step's contract is kept (no wider ``in_command``): its written target is done with no AI, the successor waits
    pending for the next command, which takes it the same way."""
    keys = graph(env, {"c1": "T", "c2": "T"}, [("r", "c1"), ("c1", "c2")])
    all_roles(keys)
    for key in keys.values():
        _written(env, key)
    command(env, "step")                                                 # the run starts with [다음 단계]
    assert (status(env, keys["c1"]), status(env, keys["c2"])) == ("done", "pending")
    assert run_row(env)["idle_reason"] != "completed"
    command(env, "step")
    assert (status(env, keys["c1"]), status(env, keys["c2"])) == ("done", "done")
    assert run_row(env)["idle_reason"] == "completed" and write_jobs(env) == [] and starts == []
def test_a_manual_result_wait_whose_execution_ai_changed_is_the_ai_s_at_the_next_command(env, decided, starts):
    keys = graph(env, {"c": "T"}, [("r", "c")], omit=("c",))
    key = keys["c"]
    roles(key, "not_used", "not_used", "not_used")
    start(env)
    first = one(env, key, "result")
    assert (first["actor"], status(env, key)) == ("person", "open")
    old_tokens = [t["token_id"] for t in tokens(env, key)]

    command(env, "step")                                                 # unchanged: the wait stays
    assert one(env, key, "result") == first and write_jobs(env) == []
    assert [a["attempt_id"] for a in assignments(env, key)] == [first["attempt_id"]]

    to_role(key, "execution", X)
    assert write_jobs(env) == [] and starts == []                        # the change alone calls nothing
    run = command(env, "step")                                           # the next command reinterprets it
    [job] = write_jobs(env)
    assert (job["stage"], job["provider_id"]) == ("result", X)
    active = _sql(env["db"], "SELECT attempt_id FROM graph_node_attempts WHERE node_key = ? AND status = 'active'",
                  (key,))[0]["attempt_id"]
    assert active != first["attempt_id"]
    ra = [a for a in assignments(env, key) if a["attempt_id"] == active and a["stage"] == "result"][0]
    assert (ra["actor"], ra["provider_id"]) == ("ai", X)
    old = _sql(env["db"], "SELECT status, invalidated_reason FROM graph_node_attempts WHERE attempt_id = ?",
               (first["attempt_id"],))[0]
    assert (old["status"], old["invalidated_reason"]) == ("invalidated", "stage_reinterpret")
    revoked = _sql(env["db"], "SELECT token_id, revoked_at FROM tokens WHERE token_id IN (%s)"
                   % ",".join("?" * len(old_tokens)), tuple(old_tokens))
    assert old_tokens and all(r["revoked_at"] for r in revoked)          # the person's write permission withdrawn
    assert run["status"] == "running"                                    # the AI's work: the run runs
    res = env["client"].post(f"/api/v1/flows/{FA}/runs", json={"revision_no": 1, "mode": "step"}, headers=_h())
    assert res.status_code == 409 and res.json()["error"]["code"] == "run_already_active"
    assert len(write_jobs(env)) == 1
