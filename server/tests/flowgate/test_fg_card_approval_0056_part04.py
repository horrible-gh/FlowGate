"""Independently selectable scenarios; shared fixtures remain in test_fg_card_approval_0056.py."""
from __future__ import annotations

from test_fg_card_approval_0056 import (
    AI,
    FA,
    LEE,
    Path,
    REVIEW_RUN,
    TS_AUTOMATED,
    TS_INVALID,
    WORK_RUN,
    _GatePolicy,
    _SERVER_DIR,
    _STOPPED_AI_RUNS,
    _admin_and_reviewer,
    _approval_route_gone,
    _causes_after,
    _chain,
    _current,
    _doc,
    _doc_events,
    _events,
    _h,
    _human_result,
    _imports,
    _in_review,
    _lease,
    _line_doc,
    _loop_row,
    _node,
    _node_run,
    _recorded_start_run,
    _reopened,
    _result,
    _resume,
    _review_status,
    _run_id,
    _run_one_card,
    _sql,
    _start,
    _stop,
    _submit_written,
    _ts_instruction,
    _ts_runs,
    _v01_approve,
    _work_run_ended,
    _write_body,
    ast,
    card_outputs_port,
    db_path,
    decided,
    edge,
    env,
    gate_decided,
    legacy_cards,
    no_v01_path,
    node,
    pre_feature_attempts,
    put_draft,
    pytest,
    pytestmark,
    setting,
    ts_admission,
)

def test_the_card_approval_package_port_and_route_are_removed():
    """T-3 ③ / ④: nothing imports ``flowgate.card_approval`` or ``card_approval_ports`` (the server's sources and
    its tests); the package, the port and the route mount are gone, and ``startup`` no longer registers it."""
    removed = ("flowgate.card_approval", "modules.flow_gate.graph_workflow.services.card_approval_ports")
    offenders = []
    for path in _SERVER_DIR.rglob("*.py"):
        if "__pycache__" in path.parts or ".venv" in path.parts:
            continue
        found = [m for m in _imports(path) if m.startswith(removed) or m.endswith(".card_approval_ports")]
        if found:
            offenders.append((str(path.relative_to(_SERVER_DIR)), found))
    assert offenders == []
    assert not (_SERVER_DIR / "flowgate" / "card_approval").joinpath("__init__.py").exists()
    assert not list((_SERVER_DIR / "flowgate" / "card_approval").glob("*.py"))
    assert not (_SERVER_DIR / "modules" / "flow_gate" / "graph_workflow" / "services" / "card_approval_ports.py").exists()
    main = (_SERVER_DIR / "routers" / "main.py").read_text(encoding="utf-8")
    assert "card_approval" not in main
    startup_src = (_SERVER_DIR / "startup.py").read_text(encoding="utf-8")
    assert "def start_card_approval_integration" not in startup_src
    assert "start_card_approval_integration()" not in startup_src
def test_startup_registers_the_pending_outputs_port_and_the_port_is_stdlib_only():
    import startup
    from modules.flow_gate.graph_workflow.services import card_completion_ports as ports

    from flowgate.card_completion import integration

    integration.unregister()
    assert not ports.registered(ports.PENDING_OUTPUTS)
    assert ports.pending_outputs(FA, {"node_key": "gn_x"}, "d") == []    # unregistered: the result alone
    startup.start_card_completion_integration()
    try:
        assert ports.registered(ports.PENDING_OUTPUTS)
    finally:
        integration.unregister()
    path = _SERVER_DIR / "modules" / "flow_gate" / "graph_workflow" / "services" / "card_completion_ports.py"
    imported = {m.split(".")[0] for m in _imports(path)}
    assert imported <= {"__future__", "logging", "threading", "typing"}
def test_a_ts_instruction_s_approval_admits_its_test_run_once_with_its_approved_display(
        env, decided, monkeypatch, no_v01_path, ts_admission):
    """rej_01M6DQW6YF0SCQ5D P1: a test card's TS instruction approved by the v0.2 (``card_completion.instruction``) runs the
    TS's own effect the v0.1 approval carried — its specification validated, its automated Case's asset checked, its
    run queued and its TSR opened (``admit_on_approval``) with the admission event — in the approval's transaction, by
    the system with the run's issuer as ``requested_by`` / the run's runner. Told again it admits nothing more."""
    from flowgate.card_completion import instruction as card_instruction

    _in_review(env, monkeypatch, (("instruction", "omit"),), "TR")       # a run with its issuer
    ts = _ts_instruction(95, TS_AUTOMATED, ts_admission["root"])
    assert card_instruction.approve(ts, requested_by="u_kim") == card_instruction.WRITTEN
    assert _review_status(ts) == "approved"
    [run] = _ts_runs(env, ts)                                            # one run, queued for the issuer
    assert run["runner_id"] == "u_kim" and ts_admission["opened"] == [run["run_id"]]
    [admitted] = _doc_events(env, ts, "test_spec_execution_admitted")
    assert '"actor": "system"' in admitted and '"requested_by": "u_kim"' in admitted and run["run_id"] in admitted
    assert len(_doc_events(env, ts, "card_instruction_approved")) == 1
    assert ts_admission["told"] == [run["run_id"]]                       # told after the commit
    assert card_instruction.approve(ts, requested_by="u_kim") == card_instruction.ALREADY     # told again
    assert len(_ts_runs(env, ts)) == 1 and len(_doc_events(env, ts, "test_spec_execution_admitted")) == 1
    assert ts_admission["opened"] == [run["run_id"]]
@pytest.mark.parametrize("case", ["invalid_specification", "missing_test_asset", "run_in_progress"])
def test_a_ts_instruction_that_does_not_pass_its_gate_is_not_approved_and_starts_no_result_stage(
        env, decided, monkeypatch, no_v01_path, ts_admission, case):
    """rej_01M6DQW6YF0SCQ5D P1: a TS whose specification does not validate, whose automated Case's test file is missing,
    or whose earlier run is still in progress is refused — not approved, no run, no admission event, no approval event
    — and the instruction stage's caller (``review_stage._reconciled_result_stage``, as every caller of
    ``approve_instruction``) starts no result stage: a system error recorded (``approval_refused``), retried."""
    from contextlib import contextmanager

    from flowgate.card_completion import instruction as card_instruction
    from flowgate.card_settings import legacy_adapter, review_stage
    from modules.flow_gate.services import spec_execution_service

    key, _result_doc, _token = _in_review(env, monkeypatch, (("instruction", "omit"),), "TR")
    body = TS_INVALID if case == "invalid_specification" else TS_AUTOMATED
    ts = _ts_instruction(96, body, None if case == "missing_test_asset" else ts_admission["root"])
    if case == "run_in_progress":
        monkeypatch.setattr(spec_execution_service, "admission_blocker",
                            lambda doc_id: {"error": "run_in_progress", "run_id": "tr_running"})
    with pytest.raises(card_instruction.InstructionRefused) as refused:
        card_instruction.approve(ts, requested_by="u_kim")
    assert refused.value.code == {"invalid_specification": "test_gate_refused", "missing_test_asset": "ts_automation_assets",
                                  "run_in_progress": "ts_run_blocked"}[case]
    started = []
    monkeypatch.setattr(legacy_adapter, "start_result_stage", lambda *a, **k: started.append(a))

    @contextmanager
    def owned():
        yield True

    run_id = _run_id(env)
    node_row = {"node_key": key, "node_type": "document", "doc_type": "T"}
    review_stage._reconciled_result_stage(FA, run_id, key, node_row, ts, "u_kim", None, hold=owned())
    assert started == []                                                 # no result stage
    assert _review_status(ts) == "pending_review" and _ts_runs(env, ts) == []
    assert _doc_events(env, ts, "test_spec_execution_admitted") == [] == _doc_events(env, ts, "card_instruction_approved")
    assert ts_admission["opened"] == [] and "approval_refused" in legacy_adapter.approval_waits(run_id, key, ts)
def test_a_valid_ts_instruction_s_approval_starts_its_result_stage_once(env, decided, monkeypatch, no_v01_path,
                                                                       ts_admission):
    from contextlib import contextmanager

    from flowgate.card_settings import legacy_adapter, review_stage

    key, _result_doc, _token = _in_review(env, monkeypatch, (("instruction", "omit"),), "TR")
    ts = _ts_instruction(97, TS_AUTOMATED, ts_admission["root"])
    started = []
    monkeypatch.setattr(legacy_adapter, "start_result_stage", lambda *a, **k: started.append(a))

    @contextmanager
    def owned():
        yield True

    node_row = {"node_key": key, "node_type": "document", "doc_type": "T"}
    review_stage._reconciled_result_stage(FA, _run_id(env), key, node_row, ts, "u_kim", None, hold=owned())
    assert [a[3] for a in started] == [ts] and _review_status(ts) == "approved"     # approved, then its result stage
    assert review_stage.approve_instruction(FA, node_row, ts, "u_kim") is True       # told again: approved already
    assert len(_ts_runs(env, ts)) == 1 and len(_doc_events(env, ts, "test_spec_execution_admitted")) == 1
def test_the_required_instruction_judgement_names_a_missing_empty_or_stale_instruction(monkeypatch):
    """rej_01M6DQW6YF0SCQ5D P2 (``outputs.pending_outputs``): with its instruction used, a card whose instruction has no live
    document — no line document, its untouched empty draft, an ended attempt's document — is not complete
    (``missing:<type>``); a live one not approved is named; approved, the card's outputs are complete. Exempt only: no
    instruction used, a one-stage node (its result of the instruction's type, a node run with no stage)."""
    from flowgate.card_completion import legacy_adapter, outputs
    from flowgate.card_documents import decision_repository

    # the output judgement also reads the human-decision ledger; this stubbed test has no store, so the
    # projection sees no decisions and the document's own review status decides (the ledger: test_fg_human_*_0126).
    monkeypatch.setattr(decision_repository, "available", lambda: False)
    lines, used, row, result_type = [], {"type": "T"}, {"stage": "result"}, {"type": "TR"}
    monkeypatch.setattr(outputs, "card_lines_of", lambda flow_id, node: [dict(line) for line in lines])
    monkeypatch.setattr(outputs, "instruction_type", lambda flow_id, node: used["type"])
    monkeypatch.setattr(legacy_adapter, "document", lambda doc_id: {"doc_id": doc_id, "type_code": result_type["type"]})
    monkeypatch.setattr(legacy_adapter, "active_run", lambda flow_id: {"run_id": "r"})
    monkeypatch.setattr(legacy_adapter, "node_run", lambda run_id, key: dict(row) if row is not None else None)
    card = {"node_key": "gn_t", "node_type": "document", "doc_type": "T"}

    def pending():
        return outputs.pending_outputs("f", card, "tr_1")

    assert pending() == ["missing:T"]                                    # no instruction line document
    lines[:] = [{"slot": "instruction", "doc_type": "T", "doc_id": "t_1", "doc_review_status": None, "written": False}]
    assert pending() == ["missing:T"]                                    # its untouched empty draft
    lines[0].update(doc_review_status="pending_review", stale=True)
    assert pending() == ["missing:T"]                                    # an ended attempt's document
    lines[0].pop("stale")
    assert pending() == ["t_1"]                                          # live, not approved
    lines[0]["doc_review_status"] = "approved"
    assert pending() == []                                               # approved: complete
    lines[:] = [{"slot": "report", "doc_type": "T", "doc_id": "t_9", "doc_review_status": "pending_review"}]
    assert pending() == ["missing:T"]                                    # another line's document is no instruction
    used["type"] = None
    assert pending() == []                                               # the instruction omitted
    used["type"], lines[:] = "T", []
    row["stage"] = None
    assert pending() == []                                               # a node run with no stage: one stage
    row["stage"], result_type["type"] = "result", "T"
    assert pending() == []                                               # its result is the instruction's type
    assert outputs.pending_outputs("f", {"node_key": "gn_g", "node_type": "gate"}, None) == []
@pytest.mark.parametrize("case", ["missing", "empty", "stale"])
def test_a_complete_result_stays_in_review_while_its_required_instruction_is_missing_empty_or_stale(
        env, decided, monkeypatch, case):
    """rej_01M6DQW6YF0SCQ5D P2: a complete result (the engine suites' stand-in reads the approved result as complete) of a
    card that uses its instruction stays in review while that instruction has no live document; once a valid
    instruction is approved, the next read's reconciliation makes the node done once (one propagation)."""
    from flowgate.card_completion import instruction as card_instruction
    from flowgate.card_completion import legacy_adapter as completion_records
    from modules.flow_gate.graph_workflow.services import card_completion_ports as ports

    key, result, _token = _in_review(env, monkeypatch, (), "TR")          # through its instruction stage (T)
    instruction = _line_doc(env, key, "instruction")
    t_card = {"node_key": key, "node_type": "document", "doc_type": "T"}
    kept = _sql(env["db"], "SELECT * FROM graph_node_documents WHERE group_id = ? AND node_key = ? AND slot = ?",
                (FA, key, "instruction"))
    if case == "missing":
        _sql(env["db"], "DELETE FROM graph_node_documents WHERE group_id = ? AND node_key = ? AND slot = ?",
             (FA, key, "instruction"))
    elif case == "empty":
        _write_body(instruction, "")
        _sql(env["db"], "UPDATE documents SET doc_review_status = NULL WHERE doc_id = ?", (instruction,))
    else:
        _reopened(instruction)
        monkeypatch.setattr(completion_records, "stale_documents", lambda flow_id, node_key: {instruction})
    assert ports.pending_outputs(FA, t_card, result) == ["missing:T"]
    seq = _current(env)["last_event_seq"]
    _v01_approve(result)                                                 # the result complete (stand-in)
    shown = _node_run(env, key)
    assert (shown["status"], shown["intervention"]["kind"]) == ("review", "needs_intervention")
    assert _causes_after(env, key, seq) == []
    if case == "missing":
        row = dict(kept[0])
        columns = ", ".join(row)
        _sql(env["db"], f"INSERT INTO graph_node_documents ({columns}) VALUES ({', '.join('?' for _ in row)})",
             tuple(row.values()))
    elif case == "empty":
        _write_body(instruction, "\n## 작업\n\n다시 쓴 지시서\n")
        assert card_instruction.approve(instruction, requested_by="u_kim") == card_instruction.WRITTEN
    else:
        monkeypatch.setattr(completion_records, "stale_documents", lambda flow_id, node_key: set())
        assert card_instruction.approve(instruction, requested_by="u_kim") == card_instruction.WRITTEN
    assert ports.pending_outputs(FA, t_card, result) == []
    assert _current(env) is not None and _node(env, key) == ("done", result)
    _current(env)                                                        # read again: nothing more
    assert _causes_after(env, key, seq) == ["review_approved"]
    assert _sql(env["db"], "SELECT COUNT(*) AS n FROM graph_propagations WHERE node_key = ?", (key,))[0]["n"] == 1
