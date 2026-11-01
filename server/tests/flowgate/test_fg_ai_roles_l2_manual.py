"""flowgate.v02.0113 T#3 — L-2 §2.7 a person's stages: what is saved is judged, once, with no person's [승인].

The harness of ``test_fg_ai_roles_l2_assignment`` (real SQLite DB and routes, the feature on, recorders for every AI
start). C1 ~ C5 (run open, current attempt, a person's stage, no open question, a submission not judged yet), the
required contents per kind (an instruction only for the instruction stage; an approved instruction and a result, or a
result alone, for the result stage), the TEST (TS -> TSR) and PLAN (WP) cards, and the B-1 reasons it adds.
"""
from __future__ import annotations

import pytest

from test_fg_ai_roles_l2_assignment import (  # noqa: F401 — fixtures used by name
    FA, STAGE_ASSIGNMENTS, X, _doc, _review_status, _sql, _write_written, b1, db_path, decided, env, events, flow,
    line_doc, node_run, one, roles, save_instruction, start, starts, stored, tokens, wired, write_line,
)


def _person_card(env, doc_type="T", **settings):
    key = flow(env, doc_type, extra_settings=tuple(settings.items()))
    roles(key, "not_used", "not_used", "not_used")
    start(env)
    return key


# ── C1 ~ C5 and the required contents (L-2 §2.7.2 / §2.7.3, §4.4) ────────────

def test_e17_a_blank_or_unsaved_instruction_is_not_judged(env, decided):
    from modules.flow_gate.graph_workflow.services import card_settings_ports

    key = _person_card(env)
    doc_id = line_doc(env, key, "instruction")
    card_settings_ports.instruction_saved(FA, doc_id)                       # nothing written yet (the card's draft)
    _write_written(doc_id, "   \n\t\n")
    card_settings_ports.instruction_saved(FA, doc_id)                       # blank body: still not written
    assert one(env, key, "instruction_review", 1) is None
    assert (one(env, key, "instruction")["stage_state"], node_run(env, key)["stage"]) == ("waiting", "instruction")


def test_c4_an_open_question_comes_first_and_the_save_is_judged_after_it(env, decided, monkeypatch):
    from flowgate.card_settings import legacy_adapter

    key = _person_card(env)
    monkeypatch.setattr(legacy_adapter, "open_questions", lambda f, r, k: True)
    doc_id = save_instruction(env, key)
    assert one(env, key, "instruction_review", 1) is None and _review_status(doc_id) != "approved"
    monkeypatch.setattr(legacy_adapter, "open_questions", lambda f, r, k: False)
    save_instruction(env, key)
    assert _review_status(doc_id) == "approved"


@pytest.mark.parametrize("closed", ["stopped", "time_machine"])
def test_c1_a_stopped_run_or_a_time_machine_operation_judges_nothing(env, decided, monkeypatch, closed):
    key = _person_card(env)
    if closed == "stopped":
        _sql(env["db"], "UPDATE graph_runs SET status = 'idle', idle_reason = 'stopped'")
    else:
        from flowgate.graph_time_machine import operation
        monkeypatch.setattr(operation, "busy", lambda flow_id: True)
    doc_id = save_instruction(env, key)
    assert one(env, key, "instruction_review", 1) is None and _review_status(doc_id) != "approved"


def test_c2_an_old_attempt_s_judgement_takes_nothing(env, decided):
    from flowgate.card_settings import manual_stage

    key = _person_card(env)
    doc_id = line_doc(env, key, "instruction")
    _write_written(doc_id)
    run_id = node_run(env, key)["run_id"]
    manual_stage.follow_person_instruction(FA, run_id, key, doc_id, "gatt_old", "content_saved")
    assert one(env, key, "instruction_review", 1) is None


def test_e18_the_same_submission_saved_twice_is_judged_once(env, decided):
    key = _person_card(env)
    doc_id = save_instruction(env, key)
    save_instruction(env, key)
    rows = [a for a in _sql(env["db"], "SELECT * FROM graph_stage_assignments WHERE stage = 'instruction_review'")]
    assert len(rows) == 1 and rows[0]["seq"] == 1
    assert len([1 for _r, p in events(env, "auto_approval_authority") if p["doc_id"] == doc_id]) == 1
    assert len([1 for _r, p in events(env, "stage_assigned") if p["stage"] == "result"]) == 1


def test_an_instruction_whose_automatic_approval_is_refused_starts_the_result_stage_and_the_result_waits(
        env, decided, monkeypatch, starts):
    """E-25 / L-2 §2.7.3: the refusal is B-1 ``approval_refused`` (this submission's mark), the result stage still starts;
    the person's result is then ``instruction_not_approved`` (B-1 once) and nothing is approved."""
    from flowgate.card_approval import legacy_adapter as approvals

    key = _person_card(env)
    real = approvals.approve_document
    monkeypatch.setattr(approvals, "approve_document",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("gate refused")))
    instruction = save_instruction(env, key)
    monkeypatch.setattr(approvals, "approve_document", real)
    assert [(d, r) for d, r, _m in b1(env)] == [(instruction, "approval_refused")]
    assert b1(env)[0][2] == {"doc_id": instruction, "revision_no": 0, "rejection_count": 0}
    assert one(env, key, "instruction_review", 1)["verdict"] == "auto_approved"
    assert one(env, key, "result")["actor"] == "person"
    report = write_line(env, key, "report", "## 결과\n\n본문\n")["doc_id"]
    assert [(d, r) for d, r, _m in b1(env)][1:] == [(report, "instruction_not_approved")]
    assert one(env, key, "result_review", 1) is None
    assert _review_status(report) != "approved" and node_run(env, key)["status"] == "review"
    write_line(env, key, "report", "again", expect=409)                      # the line holds its document
    assert len(b1(env)) == 2 and starts == []


# ── TEST and PLAN cards (L-2 §1.1, §2.5.1, G-10 ~ G-14) ──────────────────────

def test_g12_k1_a_person_test_card_writes_ts_then_tsr_and_both_are_approved_by_the_system(env, decided, starts):
    key = _person_card(env, "TS")
    ia = one(env, key, "instruction")
    assert (ia["plan_instruction_type"], ia["plan_result_type"]) == ("TS", "TSR")
    ts = save_instruction(env, key, "## 시험 시나리오\n\n사람이 쓴 TS\n")
    assert _review_status(ts) == "approved"
    assert one(env, key, "result")["actor"] == "person"
    tsr = write_line(env, key, "report", "## 시험 결과\n\n사람이 쓴 TSR\n")
    assert tsr["doc_type"] == "TSR" and tsr["bound_as_result"] is True           # K-2: the report line is the result path
    assert _review_status(tsr["doc_id"]) == "approved" and node_run(env, key)["status"] == "done"
    assert starts == [] and env["jobs"] == []


def test_g13_a_test_card_with_its_instruction_omitted_judges_its_tsr_alone(env, decided):
    key = _person_card(env, "TS", instruction="omit")
    assert one(env, key, "instruction") is None
    ra = one(env, key, "result")
    assert (ra["plan_instruction_type"], ra["plan_result_type"], ra["actor"]) == (None, "TSR", "person")
    tsr = write_line(env, key, "report", "## 시험 결과\n\nTSR\n")
    assert _review_status(tsr["doc_id"]) == "approved" and node_run(env, key)["status"] == "done"


def test_k2_the_token_step_type_and_the_result_type_follow_the_plan(env, decided):
    from flowgate.card_settings import settings

    key = _person_card(env, "TS")
    [node_token] = [t["token_id"] for t in tokens(env, key) if t["stage"] is None]
    token = {"token_id": node_token, "group_id": FA}
    assert settings.token_step_type(token) == "TS"                          # the instruction stage writes the TS
    save_instruction(env, key, "## TS\n\n본문\n")
    assert settings.token_step_type(token) == "TSR"                         # the result stage writes the TSR
    node = {"node_key": key, "node_type": "document", "doc_type": "TS"}
    assert settings.result_type(FA, node) == "TSR"


def test_g14_a_pre_feature_test_attempt_keeps_ts_as_its_result_to_its_end(env, decided, monkeypatch):
    """G-14 / E-37: an attempt opened with the feature off has no assignment; after the feature is on it still binds TS as
    its result and no assignment is made for it."""
    from flowgate.card_settings import settings, stage_assignment

    feature = {"on": False}
    monkeypatch.setattr(stage_assignment, "enabled", lambda: feature["on"])
    key = flow(env, "TS")
    start(env)
    feature["on"] = True                                                     # the feature arrives after the opening
    node = {"node_key": key, "node_type": "document", "doc_type": "TS"}
    assert stage_assignment.is_legacy_attempt(node_run_attempt(env, key))
    assert settings.result_type(FA, node) in (None, "TS")
    assert _sql(env["db"], "SELECT * FROM graph_stage_assignments") == []


def node_run_attempt(env, key):
    return _sql(env["db"], "SELECT attempt_id FROM graph_node_attempts WHERE node_key = ? AND status = 'active'",
                (key,))[0]["attempt_id"]


def test_g10_a_former_plan_card_with_nothing_set_is_a_person_s_and_its_wp_is_approved_by_the_system(
        env, decided, monkeypatch):
    """G-10: a PLAN card from before the role feature (no init row, nothing stored): result E-7 person, review R-1 no
    review; the saved WP (work sets >= 1) is approved under the system's authority (AUTOMATION), not its author's."""
    from flowgate.card_documents import legacy_adapter as documents
    from flowgate.card_settings import legacy_adapter

    key = flow(env, "WP")
    roles(key, init="legacy")
    start(env)
    ra = one(env, key, "result")
    assert (ra["actor"], ra["actor_reason"], ra["source"], ra["plan_result_type"]) == \
        ("person", "legacy_person", "legacy_fields", "WP")
    monkeypatch.setattr(documents, "work_sets", lambda doc: 1)
    monkeypatch.setattr(legacy_adapter, "work_sets", lambda doc: 1)
    calls = []
    real = legacy_adapter.approve
    monkeypatch.setattr(legacy_adapter, "approve",
                        lambda *a, **k: calls.append((a, k)) or real(*a, **k))
    plan = line_doc(env, key, "plan")
    _write_written(plan, "{\"steps\": []}")
    write_line(env, key, "plan", None)
    rr = one(env, key, "result_review", 1)
    assert (rr["actor"], rr["actor_reason"]) == ("no_review", "legacy_no_review")
    assert calls and calls[0][0][3] == legacy_adapter.AUTOMATION
    assert not [r for _d, r, _m in b1(env) if r == "issuer_lacks_approve"]


# ── the save contacts (L-2 §2.7.1 CONTENT_SAVED, T §4.3) ─────────────────────

def test_content_saved_a_body_save_tells_the_instruction_stage_after_its_commit(env, decided, monkeypatch):
    """``stage_body_save`` (the content PATCH and its RPC alias) queues the contact after the save's commit; a rolled
    back save tells nothing."""
    from modules.flow_gate.db import documents as db_documents
    from modules.flow_gate.db.connection import get_store
    from modules.flow_gate.documents.routers import documents
    from modules.flow_gate.graph_workflow.services import card_settings_ports

    told = []
    monkeypatch.setattr(card_settings_ports, "instruction_saved", lambda f, d, *a: told.append((f, d)))
    doc_id = _doc(FA, 60, "T")
    doc = db_documents.get_by_id(doc_id)
    with get_store().transaction():
        documents.stage_body_save(doc, relative=f"docs/{doc_id}.md", size=1, actor_user_id="u_kim",
                                  source="console_save", canvas_event=False)
        assert told == []                                                    # not before the commit
    assert told == [(FA, doc_id)]
    with pytest.raises(RuntimeError):
        with get_store().transaction():
            documents.stage_body_save(doc, relative=f"docs/{doc_id}.md", size=1, actor_user_id="u_kim",
                                      source="console_save", canvas_event=False)
            raise RuntimeError("rolled back")
    assert told == [(FA, doc_id)]


def test_uses_ai_keeps_its_handler_and_delegates_to_the_attempt_s_judgement_when_the_feature_is_on(env, decided,
                                                                                                    monkeypatch):
    """T §4.1 / L-1 §2.9: the registered USES_AI handler is still ``settings.uses_ai``; with the feature on it is
    ``card_execution_is_ai`` of the node's active attempt, with it off the function before."""
    from flowgate.ai_roles import resolve
    from flowgate.card_settings import settings, stage_assignment
    from modules.flow_gate.graph_workflow.services import card_settings_ports

    assert card_settings_ports._handler(card_settings_ports.USES_AI) is settings.uses_ai
    key = _person_card(env)
    attempt = node_run_attempt(env, key)
    asked = []
    monkeypatch.setattr(resolve, "card_execution_is_ai", lambda f, n, a=None: asked.append(a) or True)
    node = {"node_key": key, "node_type": "document", "doc_type": "T"}
    assert settings.uses_ai(FA, node) is True and asked == [attempt]
    monkeypatch.setattr(stage_assignment, "enabled", lambda: False)
    assert settings.uses_ai(FA, node) is settings.legacy_uses_ai(FA, node) and asked == [attempt]
