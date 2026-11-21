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

@pytest.mark.parametrize("wait", ["wait_setup", "wait_unavailable"])
def test_a_setup_or_unavailable_result_wait_keeps_its_attempt_until_its_role_changes_then_is_reinterpreted_once(
        env, decided, starts, wait):
    keys = graph(env, {"c": "T"}, [("r", "c")], omit=("c",))
    key = keys["c"]
    roles(key, "not_used", _write_wait(env, wait), "not_used")
    start(env)
    first = one(env, key, "result")
    assert (first["actor"], first["stage_state"]) == (wait, "waiting")
    tokens_before = _token_state(env, key)
    assert tokens_before and not any(t["revoked_at"] for t in tokens_before)

    for mode in ("step", "full", "step"):                               # unchanged: the wait stays as it is
        command(env, mode)
        assert _active_attempt(env, key) == first["attempt_id"]
        assert assignments(env, key) == [first] and _token_state(env, key) == tokens_before
    assert _sql(env["db"], "SELECT COUNT(*) AS n FROM graph_node_attempts WHERE node_key = ?", (key,))[0]["n"] == 1
    assert write_jobs(env) == [] and starts == []

    to_role(key, "execution", Y)
    assert write_jobs(env) == []                                         # the change alone calls nothing
    command(env, "step")                                                 # the first command after it reinterprets
    [job] = write_jobs(env)
    assert (job["stage"], job["provider_id"]) == ("result", Y)
    active = _active_attempt(env, key)
    assert active != first["attempt_id"]
    old = _sql(env["db"], "SELECT status, invalidated_reason FROM graph_node_attempts WHERE attempt_id = ?",
               (first["attempt_id"],))[0]
    assert (old["status"], old["invalidated_reason"]) == ("invalidated", "stage_reinterpret")
    revoked = {t["token_id"]: t["revoked_at"] for t in _token_state(env, key)}
    assert all(revoked[t["token_id"]] for t in tokens_before)            # the old assignment's permission withdrawn
    res = env["client"].post(f"/api/v1/flows/{FA}/runs", json={"revision_no": 1, "mode": "step"}, headers=_h())
    assert res.status_code == 409                                        # Y works: no second reinterpretation
    assert len(write_jobs(env)) == 1
@pytest.mark.parametrize("wait", ["wait_setup", "wait_unavailable"])
def test_a_setup_or_unavailable_result_wait_reinterpreted_keeps_the_instruction_and_the_report_baseline(
        env, decided, starts, monkeypatch, wait):
    keys = graph(env, {"c": "T"}, [("r", "c")])
    key = keys["c"]
    roles(key, X, _write_wait(env, wait), "not_used")
    report = prewrite(env, key, "report", REPORT_BODY)                   # row 6: the report of before
    start(env)
    instruction = _ai_writes_instruction(env, monkeypatch, provider=X)
    assert _review_status(instruction) == "approved"
    first = one(env, key, "result")
    assert first["actor"] == wait and write_jobs(env) == []
    assert current(env)["status"] == "idle"                              # the wait
    command(env, "step")                                                 # unchanged: kept
    assert _active_attempt(env, key) == first["attempt_id"] and write_jobs(env) == []

    to_role(key, "execution", Y)
    command(env, "step")
    assert [(j["stage"], j["provider_id"]) for j in write_jobs(env)] == [("result", Y)]   # no instruction AI again
    active = _active_attempt(env, key)
    ia = [a for a in assignments(env, key) if a["attempt_id"] == active and a["stage"] == "instruction"][0]
    assert ia["reused_doc_id"] == instruction
    assert [(r["doc_id"], r["carried_from"]) for r in baseline_rows(env, key)] == \
        [(report, None), (report, first["attempt_id"])]
    assert status(env, key) == "open" and node_run(env, key)["result_doc_id"] is None
    assert doc_row(env, report)["doc_review_status"] is None             # the report of before: kept, never taken
@pytest.mark.parametrize("wait", ["wait_setup", "wait_unavailable"])
def test_a_setup_or_unavailable_instruction_wait_keeps_its_attempt_until_its_role_changes(env, decided, starts, wait):
    keys = graph(env, {"c": "T"}, [("r", "c")])
    key = keys["c"]
    roles(key, _write_wait(env, wait), "not_used", "not_used")
    start(env)
    first = one(env, key, "instruction")
    assert first["actor"] == wait
    tokens_before = _token_state(env, key)
    to_role(key, "execution", Y)                                         # another role than the waiting stage's
    for mode in ("step", "full"):
        command(env, mode)
        assert _active_attempt(env, key) == first["attempt_id"] and _token_state(env, key) == tokens_before
    to_role(key, "instruction", X)
    command(env, "full")
    [job] = write_jobs(env)
    assert (job["stage"], job["provider_id"]) == ("instruction", X)
    assert _active_attempt(env, key) != first["attempt_id"]
@pytest.mark.parametrize("order", ["report_first", "instruction_first"])
@pytest.mark.parametrize("wait", ["wait_setup", "wait_unavailable"])
def test_a_wait_reinterpreted_with_its_instruction_written_takes_only_a_report_saved_after_the_instruction(
        env, decided, starts, wait, order):
    """0045-TR rework: the instruction stage waits for its setup / an unavailable AI (no person's stage takes a save);
    the person saves the report and the instruction meanwhile, then the role changes and the next command opens a new
    attempt that finds the instruction written. A report saved before the instruction is carried as the baseline (the
    card waits for a report of after it); a report saved after the instruction is the result."""
    keys = graph(env, {"c": "T"}, [("r", "c")])
    key = keys["c"]
    roles(key, _write_wait(env, wait), "not_used", "not_used")
    report = prewrite(env, key, "report", REPORT_BODY)
    start(env)
    first = one(env, key, "instruction")
    assert first["actor"] == wait
    instruction = line_doc(env, key, "instruction")
    saves = [(report, EDITED_REPORT), (instruction, INSTRUCTION_BODY)]
    for doc_id, body in (saves if order == "report_first" else saves[::-1]):
        _saved(doc_id, body)
    to_role(key, "instruction", "not_used")
    command(env, "step")                                               # reinterpreted: a new attempt, instruction written
    assert _sql(env["db"], "SELECT COUNT(*) AS n FROM graph_node_attempts WHERE node_key = ?", (key,))[0]["n"] == 2
    if order == "instruction_first":                                   # saved after the instruction: the result
        current(env)
        assert status(env, key) == "done" and _review_status(report) == "approved"
        assert write_jobs(env) == [] and starts == []
        return
    _waits_for_its_report(env, key, report)
    assert [(r["doc_id"], r["carried_from"]) for r in baseline_rows(env, key)] == \
        [(report, None), (report, first["attempt_id"])]
    _saved(report, NEW_REPORT)                                         # saved after the instruction
    current(env)
    assert status(env, key) == "done" and _review_status(report) == "approved"
    assert write_jobs(env) == [] and starts == []
@pytest.mark.parametrize("fault", ["insert", "read", "absent"])
def test_a_baseline_that_cannot_be_recorded_fails_the_opening_and_the_old_report_is_never_taken(env, decided, starts,
                                                                                              monkeypatch, fault):
    """A-2: the opening that needs a baseline (instruction not written, a report there) fails as a whole when the
    baseline cannot be recorded (an insert or read failure, migration 167 missing): the report is kept as it was,
    unregistered, the card not done. Once the store works the next command opens it with its baseline, and only a
    report saved after it completes the card."""
    key, report = _row6_person_card(env)
    before = doc_row(env, report)
    faults = _break_baseline(monkeypatch, fault)
    res = env["client"].post(f"/api/v1/flows/{FA}/runs", json={"revision_no": 1}, headers=_h())
    assert (res.status_code, res.json()["error"]["code"]) == (500, "internal_error")   # rolled back as a whole
    assert _sql(env["db"], "SELECT * FROM graph_node_attempts WHERE node_key = ?", (key,)) == []   # nothing opened
    assert _sql(env["db"], "SELECT * FROM graph_attempt_report_baselines") == []
    assert doc_row(env, report) == before and assignments(env, key) == []

    faults["fault"] = None                                               # the store works again
    command(env, "full")
    assert baseline_rows(env, key)[0]["doc_id"] == report
    instruction = save_instruction(env, key)
    assert _review_status(instruction) == "approved"
    current(env)
    assert (status(env, key), node_run(env, key)["stage"], node_run(env, key)["result_doc_id"]) == \
        ("open", "result", None)
    assert doc_row(env, report) == before                                # still the report of before: not taken
    _write_written(report, NEW_REPORT)                                   # saved after the baseline
    current(env)
    assert status(env, key) == "done" and _review_status(report) == "approved"
    assert write_jobs(env) == [] and starts == []
@pytest.mark.parametrize("fault", ["read", "absent"])
def test_a_baseline_that_cannot_be_read_takes_no_report_until_it_can(env, decided, starts, monkeypatch, fault):
    """A-2: after the opening, a baseline that cannot be read (a read failure, the table gone) is no "no baseline":
    the result stage takes no report — neither the old one nor one saved meanwhile — and nothing is completed; once
    it can be read again only a report saved after the baseline completes the card."""
    key, report = _row6_person_card(env)
    start(env)
    before = doc_row(env, report)
    instruction = save_instruction(env, key)
    assert _review_status(instruction) == "approved"
    assert (status(env, key), node_run(env, key)["stage"]) == ("open", "result")

    faults = _break_baseline(monkeypatch, fault)
    for _ in range(2):                                                   # run reads (RC-8) and a command take nothing
        current(env)
    command(env, "step")
    assert status(env, key) == "open" and node_run(env, key)["result_doc_id"] is None
    assert doc_row(env, report) == before
    _write_written(report, NEW_REPORT)                                   # even a new save waits while it is unknown
    current(env)
    assert status(env, key) == "open" and node_run(env, key)["result_doc_id"] is None

    faults["fault"] = None
    current(env)                                                         # known again: the report saved after it
    assert status(env, key) == "done" and _review_status(report) == "approved"
    assert write_jobs(env) == [] and starts == []
def test_migration_167_is_the_same_table_in_three_dialects_and_runs_twice():
    import sqlite3
    from pathlib import Path

    root = Path(__file__).resolve().parents[2] / "sql" / "migrations"
    for dialect in ("sqlite", "postgres", "mysql"):
        sql = (root / dialect / "167_attempt_report_baselines.sql").read_text(encoding="utf-8")
        assert "CREATE TABLE IF NOT EXISTS graph_attempt_report_baselines" in sql
        for column in ("attempt_id", "run_id", "group_id", "node_key", "doc_id", "revision_no", "body_sha256",
                       "body_event_id", "carried_from", "recorded_at"):
            assert column in sql, (dialect, column)
        ddl = sql[sql.index("CREATE TABLE"):].split("-- ── DOWN")[0]
        assert "REFERENCES" not in ddl and "FOREIGN KEY" not in ddl          # a history row
    conn = sqlite3.connect(":memory:")
    script = (root / "sqlite" / "167_attempt_report_baselines.sql").read_text(encoding="utf-8")
    conn.executescript(script)
    conn.execute("INSERT INTO graph_attempt_report_baselines (attempt_id, run_id, group_id, node_key, recorded_at) "
                 "VALUES ('a', 'r', 'g', 'n', 't')")
    conn.executescript(script)                                           # again: the row stays
    assert conn.execute("SELECT COUNT(*) FROM graph_attempt_report_baselines").fetchone()[0] == 1
