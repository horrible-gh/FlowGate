"""flowgate.v02.0113 T#3 — P-1 §6 / §7: the three node run keys a run read adds, the no-call stage events and the
ledger's assignment reference / origin.

The harness of ``test_fg_ai_roles_l2_assignment``. A run read is ``GET /runs/current`` (its node runs are
``run_service.node_run_wire``); the serializer itself (``stage_view.wire``) is called directly where a case counts its
writes.
"""
from __future__ import annotations

import json

import pytest

from test_fg_ai_roles_l2_assignment import (  # noqa: F401 — fixtures used by name
    B, FA, STAGE_ASSIGNMENTS, X, _sql, _write_written, b1, db_path, decided, env, events, flow, ledger, line_doc,
    node_run, one, roles, save_instruction, start, starts, stored, wired, write_line,
)

WIRE_KEYS = {"wait_reason", "stage_assignments", "approval_wait_submission"}
ASSIGNMENT_KEYS = {"stage", "seq", "current", "role", "actor", "actor_reason", "provider_id", "provider_name",
                   "role_value", "source", "cause", "plan", "reused_doc_id", "review_rounds", "rework_provider_id",
                   "author", "author_provider_id", "basis", "decided_at", "stage_state", "fail_cause", "verdict",
                   "intent"}
STAGE_EVENT_KEYS = {"flow_id", "run_id", "revision_no", "event_seq", "from", "to", "cause", "reason", "node_key",
                    "attempt_id", "stage", "seq", "role", "actor", "actor_reason", "source", "cause_code", "provider_id",
                    "provider_name", "author", "decided_at"}


def _wire(env, key):
    res = env["client"].get(f"/api/v1/flows/{FA}/runs/current", headers={"Authorization": "Bearer user:u_kim"})
    assert res.status_code == 200, res.text
    return next(n for n in res.json()["run"]["node_runs"] if n["node_key"] == key)


def _tables(env):
    return {t: _sql(env["db"], f"SELECT * FROM {t}") for t in (
        "graph_stage_assignments", "graph_run_events", "graph_node_role_slots", "graph_node_role_init",
        "graph_attempt_runs", "graph_node_settings", "tokens")}


# ── §6.1 / §6.2: the keys, the manual write wait ─────────────────────────────

def test_p1_6_2_a_person_instruction_stage_shows_its_manual_write_wait_and_its_assignment(env, decided):
    key = flow(env)
    roles(key, "not_used", X, "not_used")
    start(env)
    wire = _wire(env, key)
    assert WIRE_KEYS <= set(wire)
    assert wire["wait_reason"] == {"code": "manual_write_wait", "role": "instruction", "stage": "instruction", "seq": 0,
                                   "detail": None}
    [a] = wire["stage_assignments"]
    assert set(a) == ASSIGNMENT_KEYS
    assert (a["current"], a["actor"], a["actor_reason"], a["role_value"], a["source"], a["plan"], a["intent"]) == \
        (True, "person", "not_used", {"kind": "not_used", "provider_id": None}, "card_explicit",
         {"instruction_type": "T", "result_type": "TR"}, None)
    assert wire["approval_wait_submission"] is None
    for kept in ("dispatch", "awaiting_approval", "approval_wait_reason", "stage", "instruction_doc_id", "attempt_id",
                 "hold", "question", "node_running"):
        assert kept in wire                                                    # the existing keys stay


def test_p1_6_1_order_single_current_snapshot_and_intent_after_the_result_stage_starts(env, decided, starts):
    key = flow(env)
    roles(key, "not_used", X, "not_used")
    start(env)
    instruction = save_instruction(env, key)
    _sql(env["db"], "UPDATE ai_providers SET name = 'renamed' WHERE provider_id = ?", (X,))
    wire = _wire(env, key)
    shown = wire["stage_assignments"]
    assert [(a["stage"], a["seq"]) for a in shown] == [("instruction", 0), ("instruction_review", 1), ("result", 0)]
    assert [a["current"] for a in shown] == [False, False, True]
    result = shown[2]
    assert (result["actor"], result["provider_id"], result["provider_name"]) == ("ai", X, "AI " + X)   # the snapshot
    assert result["intent"]["kind"] == "execution" and result["intent"]["exec_id"]
    review = shown[1]
    assert (review["basis"], review["verdict"], review["intent"]) == (
        {"doc_id": instruction, "revision_no": 0, "rejection_count": 0}, "auto_approved",
        {"kind": "approval", "exec_id": None})
    assert wire["wait_reason"] is None                                        # the AI works


def test_p1_the_serializer_writes_nothing_however_often_it_is_called(env, decided):
    from flowgate.card_settings import stage_view

    key = flow(env)
    roles(key, "not_used", X, "not_used", init="pending_init")
    start(env)
    row = node_run(env, key)
    before = _tables(env)
    for _ in range(3):
        stage_view.wire(FA, row, row.get("attempt_id") or one(env, key, "instruction")["attempt_id"], None)
    assert _tables(env) == before


@pytest.mark.parametrize("case", ["no_attempt", "feature_off", "read_failure", "pre_feature"])
def test_p1_null_and_empty_contracts(env, decided, monkeypatch, case):
    from flowgate.ai_roles import assignment_store
    from flowgate.card_settings import stage_assignment, stage_view

    if case == "pre_feature":
        feature = {"on": False}
        monkeypatch.setattr(stage_assignment, "enabled", lambda: feature["on"])
    key = flow(env)
    roles(key, "not_used", X, "not_used")
    start(env)
    row = node_run(env, key)
    attempt = _sql(env["db"], "SELECT attempt_id FROM graph_node_attempts WHERE node_key = ?", (key,))[0]["attempt_id"]
    if case == "no_attempt":
        assert stage_view.wire(FA, row, None, None) == dict.fromkeys(WIRE_KEYS)
        return
    if case == "feature_off":
        monkeypatch.setattr(stage_assignment, "enabled", lambda: False)
    if case == "read_failure":
        monkeypatch.setattr(assignment_store, "for_node", lambda *a: (_ for _ in ()).throw(RuntimeError("down")))
    if case == "pre_feature":
        feature["on"] = True
        assert stage_view.wire(FA, row, attempt, None) == {"wait_reason": None, "stage_assignments": [],
                                                           "approval_wait_submission": None}
        return
    assert stage_view.wire(FA, row, attempt, None) == dict.fromkeys(WIRE_KEYS)
    assert _wire(env, key)["stage_assignments"] is None                        # the read itself goes on


# ── §6.3 / §6.4: setup, unavailable, the resubmission wait, the B-1 mark ──────

def test_p1_6_3_a_result_review_wait_setup_is_shown_on_the_node_in_review(env, decided):
    key = flow(env, extra_settings=(("instruction", "omit"),))
    roles(key, execution="not_used")
    start(env)
    write_line(env, key, "report", "## 결과\n\n본문\n")
    wire = _wire(env, key)
    assert wire["wait_reason"] == {"code": "ai_setup_required", "role": "review", "stage": "result_review", "seq": 1,
                                   "detail": "none"}
    assert wire["awaiting_approval"] is True and wire["approval_wait_reason"] is None


def test_p1_6_4_the_b1_submission_mark_is_the_one_of_the_row_approval_wait_reason_shows(env, decided, monkeypatch):
    from flowgate.card_approval import legacy_adapter as approvals

    key = flow(env)
    roles(key, "not_used", "not_used", "not_used")
    start(env)
    real = approvals.approve_document
    monkeypatch.setattr(approvals, "approve_document", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("refused")))
    save_instruction(env, key)
    monkeypatch.setattr(approvals, "approve_document", real)
    report = write_line(env, key, "report", "## 결과\n\n본문\n")["doc_id"]
    wire = _wire(env, key)
    assert wire["approval_wait_reason"] == "instruction_not_approved"
    assert wire["approval_wait_submission"] == {"doc_id": report, "revision_no": 0, "rejection_count": 0}
    assert wire["wait_reason"] == {"code": "manual_write_wait", "role": "execution", "stage": "result", "seq": 0,
                                   "detail": None}


# ── §7.1: the no-call stage events ───────────────────────────────────────────

def test_p1_7_1_every_stage_without_a_call_has_its_stage_assigned_event(env, decided):
    key = flow(env)
    roles(key, "not_used", "not_used", "not_used")
    start(env)
    save_instruction(env, key)
    shown = events(env, "stage_assigned")
    assert [(p["stage"], r) for r, p in shown] == [("instruction", "person"), ("instruction_review", "no_review"),
                                                   ("result", "person")]
    for reason, payload in shown:
        assert set(payload) == STAGE_EVENT_KEYS and payload["from"] == payload["to"] and reason == payload["actor"]
    nodes = _sql(env["db"], "SELECT cause FROM graph_run_events WHERE event_type = 'graph_node_state_changed' "
                            "AND cause LIKE 'stage_%'")
    assert nodes == []                                                        # no node event for them


# ── §7.2: the ledger's reference and origin on every hop ─────────────────────

def test_p1_7_2_a_write_request_carries_its_assignment_and_origin_at_the_top_and_in_its_columns(env, decided):
    key = flow(env, extra_settings=(("instruction", "omit"),))
    roles(key, execution=X, review="not_used")
    start(env)
    [req] = ledger(env)
    raw = json.loads(req["request_json"])
    ra = one(env, key, "result")
    assert raw["assignment_ref"] == {"attempt_id": ra["attempt_id"], "stage": "result", "seq": 0}
    assert (req["assignment_stage"], req["assignment_seq"]) == ("result", 0)
    origin = raw["invocation_origin"]
    assert (origin["origin_kind"], origin["purpose"], origin["role"], origin["role_source"], origin["requested_ai_id"],
            origin["round_no"]) == ("card_assigned", "result_write", "execution", "card_explicit", X, None)
    assert origin["target"] == {"flow_id": FA, "graph_run_id": ra["run_id"], "node_key": key,
                                "attempt_id": ra["attempt_id"], "stage": "result", "seq": 0, "doc_id": None,
                                "doc_revision_no": None}
    assert "token" in raw["job"] and "raw_token" not in json.dumps(raw)


def test_p1_7_2_a_loop_s_next_hop_keeps_its_reference_with_the_hop_s_purpose_provider_and_round():
    from flowgate.graph_run import execution

    payload = {"assignment_ref": {"attempt_id": "gatt_1", "stage": "result_review", "seq": 1},
               "invocation_origin": {"origin_kind": "card_assigned", "purpose": "result_review_round", "role": "review",
                                     "requested_ai_id": B, "target": {"seq": 1}, "round_no": 1}}
    rework = execution._hop_origin(payload, {"current_stage": "rework", "rework_provider_id": X,
                                             "reviewer_provider_id": B, "round_no": 1})
    assert (rework["purpose"], rework["role"], rework["requested_ai_id"], rework["target"]) == \
        ("result_rework", "rework", X, {"seq": 1})
    review = execution._hop_origin(payload, {"current_stage": "review", "rework_provider_id": X,
                                             "reviewer_provider_id": B, "round_no": 2})
    assert (review["purpose"], review["requested_ai_id"], review["round_no"]) == ("result_review_round", B, 2)
    assert execution._hop_origin({}, {"current_stage": "review"}) is None


def test_p1_7_2_the_run_memory_takes_the_card_call_s_origin(env, decided, monkeypatch):
    """T#4's input: the AI start hands the job's origin to the admission (its run memory)."""
    from modules.flow_gate.graph_workflow.services import ai_node_starter
    from modules.flow_gate.services import ai_invoke_service

    key = flow(env, extra_settings=(("instruction", "omit"),))
    roles(key, execution=X, review="not_used")
    start(env)
    [job] = env["jobs"]
    seen = []
    monkeypatch.setattr(ai_invoke_service, "start_run", lambda **kw: seen.append(kw) or {"run_id": "r", "status": "x"})
    ai_node_starter.start_ai_invoke(job)
    assert seen[0]["invocation_origin"] == job["invocation_origin"]
    assert seen[0]["provider_id"] == X
