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

def test_a_complete_result_revised_while_it_waits_for_its_instruction_is_followed_after_a_lost_contact(
        env, decided, monkeypatch):
    from modules.flow_gate.db import documents as db_documents

    key, old, _token = _in_review(env, monkeypatch, (), "TR")
    instruction = _line_doc(env, key, "instruction")
    _reopened(instruction)
    seq = _current(env)["last_event_seq"]
    _v01_approve(old)                                                    # the TR complete: it waits for its T
    assert _node(env, key) == ("review", old)
    new = _result(91, "TR")                                              # the TR is revised and its
    db_documents.set_superseded_by(old, new)                             # registration's contact is lost
    run = _current(env)                                                  # the read reconciles: the revision first
    node_run = next(n for n in run["node_runs"] if n["node_key"] == key)
    assert (node_run["status"], node_run["result_doc_id"], node_run["intervention"]["kind"]) == (
        "review", new, "needs_intervention")                             # 0119 T-2: no person's approval follows
    assert _causes_after(env, key, seq) == ["revised"]
def test_the_reconciliation_follows_a_live_revision_before_a_complete_result_s_outputs(monkeypatch):
    from types import SimpleNamespace

    from modules.flow_gate.graph_workflow.domain.frontier import RunEvent
    from modules.flow_gate.graph_workflow.services import card_completion_ports as ports
    from modules.flow_gate.graph_workflow.services import card_settings_ports
    from modules.flow_gate.graph_workflow.services import result_binder as rb
    from modules.flow_gate.services import graph_execution_hooks

    docs = {"tr_old": {"doc_id": "tr_old", "doc_review_status": "approved", "superseded_by": "tr_new",
                       "group_id": "f", "status": "open"},
            "tr_new": {"doc_id": "tr_new", "doc_review_status": "pending_review", "group_id": "f", "status": "open"}}
    ctx = SimpleNamespace(flow_id="f", run={"run_id": "r"}, run_id="r", node_rows={"gn_t": {"node_key": "gn_t"}},
                          docs=lambda: docs)
    s = SimpleNamespace(node=lambda key: SimpleNamespace(status="review", result_doc_id="tr_old"))
    monkeypatch.setattr(ports, "completion_of", lambda flow_id, run, key, doc: {"completion_id": "c", "legacy": False})
    monkeypatch.setattr(card_settings_ports, "result_review_pending", lambda *a: False)
    monkeypatch.setattr(graph_execution_hooks, "call", lambda *a, **k: None)
    monkeypatch.setattr(ports, "pending_outputs", lambda flow_id, node, result_doc_id: ["t1"])   # its T waits
    assert rb.next_missing_event(ctx, s, "gn_t") == RunEvent("revised", "gn_t", doc_id="tr_new", replaces="tr_old")
    docs["tr_old"]["superseded_by"] = None                               # no revision: the instruction still waits
    assert rb.next_missing_event(ctx, s, "gn_t") is None
    monkeypatch.setattr(ports, "pending_outputs", lambda flow_id, node, result_doc_id: [])        # outputs complete
    assert rb.next_missing_event(ctx, s, "gn_t") == RunEvent("review_approved", "gn_t", doc_id="tr_old")
def test_the_pending_outputs_port_names_only_the_instruction_the_card_uses(env, decided, monkeypatch):
    from modules.flow_gate.graph_workflow.services import card_completion_ports as ports

    key, result, _token = _in_review(env, monkeypatch, (), "TR")          # its instruction used (T)
    instruction = _line_doc(env, key, "instruction")
    t_card = {"node_key": key, "node_type": "document", "doc_type": "T"}
    assert ports.pending_outputs(FA, t_card, result) == []               # approved by its stage
    _reopened(instruction)
    assert ports.pending_outputs(FA, t_card, result) == [instruction]
    assert ports.pending_outputs(FA, t_card, instruction) == []          # never the result itself
    _v01_approve(instruction, contact=False)
    assert ports.pending_outputs(FA, t_card, result) == []
    assert ports.pending_outputs(FA, {"node_key": key, "node_type": "gate"}, None) == []
def test_a_card_that_omits_its_instruction_has_no_pending_output(env, decided, monkeypatch):
    from modules.flow_gate.graph_workflow.services import card_completion_ports as ports

    key, result, _token = _in_review(env, monkeypatch, (("instruction", "omit"),), "TR")
    instruction = _line_doc(env, key, "instruction")
    t_card = {"node_key": key, "node_type": "document", "doc_type": "T"}
    _write_body(instruction, "written")
    assert ports.pending_outputs(FA, t_card, result) == []
    _submit_written(instruction)
    assert ports.pending_outputs(FA, t_card, result) == []
def test_the_instruction_a_card_requires_by_its_kind_and_its_stored_use(monkeypatch):
    """``card_settings.settings.required_instruction``: an assigned attempt's plan names it; a pre-feature attempt reads
    the card's stored instruction use (a run card's instruction stage type, a test card's TS unless omitted)."""
    from flowgate.card_settings import repository, settings

    stored = {}
    plan = {"value": None}
    monkeypatch.setattr(settings, "_assigned", lambda flow_id, n: ("att", plan["value"]) if plan["value"] else None)
    monkeypatch.setattr(repository, "available", lambda: True)
    monkeypatch.setattr(repository, "values", lambda flow_id, key: dict(stored))

    def required(node_type, doc_type):
        return settings.required_instruction("f", {"node_key": "gn_x", "node_type": node_type, "doc_type": doc_type})

    assert [required("document", t) for t in ("N", "T", "T2", "D")] == ["N", "T", "T2", "DS"]
    assert required("custom", None) == "T" and required("document", "TS") == "TS"
    assert [required("document", t) for t in ("WP", "R", "B")] == [None, None, None] and required("gate", None) is None
    stored["instruction"] = "omit"
    assert required("document", "T") is None and required("document", "TS") is None
    plan["value"] = {"instruction_type": "DS"}                           # an assigned attempt: its plan decides
    assert required("document", "T") == "DS"
    plan["value"] = {"instruction_type": None, "result_type": "WP"}
    assert required("document", "WP") is None
def test_the_output_lines_moved_from_the_card_approval_read_written_and_stale_documents():
    from flowgate.card_completion.outputs import is_target

    assert is_target({"slot": "instruction", "doc_id": "t", "doc_review_status": "pending_review"})
    assert not is_target({"slot": "instruction", "doc_id": "t", "doc_review_status": None})            # a draft
    assert not is_target({"slot": "instruction", "doc_id": "t", "doc_review_status": None, "written": False})
    assert is_target({"slot": "instruction", "doc_id": "t", "doc_review_status": None, "written": True})
    assert not is_target({"slot": "instruction", "doc_id": "t", "doc_review_status": "pending_review",
                          "stale": True})                                # 0061 T#7: an ended attempt's document
    assert not is_target({"slot": "instruction", "doc_id": None, "doc_review_status": "approved"})
def test_the_card_approval_route_is_gone_and_a_person_s_result_completes_its_card(pre_feature_attempts, env, decided):
    """0119 T-3 rework (rej_01M6DQW6YF0SCQ5D P1, was ``..._is_the_card_s_intervention``): no route takes a person's
    approval, and a pre-feature person card whose card reviews nothing completes its result as ``human_submitted``."""
    _admin_and_reviewer(env)
    t1, t2 = _chain(env, [("T", LEE), ("T", LEE)])
    _start(env)
    for key in (t1, t2, "gn_nothing"):
        _approval_route_gone(env, key)
    _approval_route_gone(env, t1, run_id="run_missing")
    result = _human_result(env, t1, state="done")                       # review 'none': completed as submitted
    _approval_route_gone(env, t1)
    shown = _node_run(env, t1)
    assert (shown["status"], shown["awaiting_approval"], shown["intervention"]) == ("done", False, None)
    assert "viewer" not in _current(env)                                 # 0119 T-3: no viewer.can_approve
    assert _review_status(result) == "approved" and _node(env, t2)[0] == "open"
    [done] = _sql(env["db"], "SELECT cause, actor_kind FROM graph_card_completions WHERE node_key = ?", (t1,))
    assert (done["cause"], done["actor_kind"]) == ("human_submitted", "user")
