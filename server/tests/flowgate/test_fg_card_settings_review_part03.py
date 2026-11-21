"""Independently selectable scenarios; shared fixtures remain in test_fg_card_settings_review.py."""
from __future__ import annotations

from test_fg_card_settings_review import (
    AI,
    FA,
    INSTRUCTION_RUN,
    Path,
    REVIEW_RUN,
    WORK_RUN,
    _SERVER_DIR,
    _admin_and_reviewer,
    _doc,
    _executions_ended,
    _h,
    _in_review,
    _loop_row,
    _node,
    _recorded_start_run,
    _result,
    _review_status,
    _run_card,
    _run_ended,
    _run_one_card,
    _source_scopes,
    _sql,
    _start,
    _start_ai,
    _through_instruction,
    _work_run_ended,
    _write_written,
    ast,
    db_path,
    decided,
    edge,
    env,
    get_settings,
    legacy_cards,
    legacy_setting,
    node,
    pre_feature_attempts,
    put_draft,
    pytest,
    pytestmark,
    setting,
)

@pytest.mark.parametrize("stop_reason, status, review", [
    ("review_passed", "done", "approved"),
    ("review_count_exhausted", "done", "approved"),
    ("review_verdict_hold", "review", "pending_review"),
    ("review_stalled", "review", "pending_review"),
    ("user_cancelled", "review", "pending_review"),
])
def test_pass_or_default_exhaustion_approves_but_other_stops_do_not(
        env, decided, monkeypatch, stop_reason, status, review):
    from modules.flow_gate.services import ai_run_end_hooks

    key, result, _token = _in_review(env, monkeypatch, (("review_count", 2), ("review_ai", "aip_review")))
    ai_run_end_hooks.run_ended({"run_id": REVIEW_RUN, "group_id": FA, "doc_ref": result, "action_scope": "review",
                                "mode": "single", "issued_to": "u_kim",
                                "document_review_loop": {"current_stage": "stopped", "stop_reason": stop_reason}})
    assert (_node(env, key)[0], _review_status(result)) == (status, review)
def test_a_card_review_rejection_is_no_node_event_and_the_card_is_locked_while_it_works(
        env, decided, monkeypatch):
    from modules.flow_gate.db import documents as db_documents
    from modules.flow_gate.graph_workflow.services import result_binder

    key, result, _token = _in_review(env, monkeypatch, (("review_count", 2), ("review_ai", "aip_review")))
    assert get_settings(env, FA, key)["locked"] is False                  # in review, no AI review yet
    _loop_row(result, "rework")
    db_documents.update(result, {"doc_review_status": "rejected"})       # the review's rejection, before rework
    result_binder.reconcile_if_needed(FA, "flowgate")
    assert _node(env, key) == ("review", result)
    assert get_settings(env, FA, key)["locked"] is True
    assert setting(env, FA, key, "restart", 1, expect=409)["error"]["code"] == "node_settings_locked"
    _sql(env["db"], "UPDATE ai_invoke_document_review_loops SET current_stage = 'stopped', "
                    "stop_reason = 'review_count_exhausted', stop_detail = 'spent' WHERE run_id = ?", (REVIEW_RUN,))
    result_binder.reconcile_if_needed(FA, "flowgate")                    # the review ended rejected:
    assert _node(env, key) == ("review", result)                          # no node event
    assert get_settings(env, FA, key)["locked"] is False
def test_the_review_loop_of_a_passed_result_is_read_from_its_row(env, decided, monkeypatch):
    from modules.flow_gate.services import ai_run_end_hooks

    key, result, _token = _in_review(env, monkeypatch, (("review_count", 2), ("review_ai", "aip_review")))
    _loop_row(result, "review")
    _sql(env["db"], "UPDATE ai_invoke_document_review_loops SET current_stage = 'stopped', "
                    "stop_reason = 'review_passed' WHERE run_id = ?", (REVIEW_RUN,))
    ai_run_end_hooks.run_ended({"run_id": REVIEW_RUN, "group_id": FA, "doc_ref": result, "action_scope": "review",
                                "mode": "single", "issued_to": "u_kim",
                                "document_review_loop": {"current_stage": "review"}})   # the stale copy
    assert (_node(env, key)[0], _review_status(result)) == ("done", "approved")
def test_the_engine_calls_the_run_end_port_after_the_release_and_the_port_is_stdlib_only():
    src = (_SERVER_DIR / "modules" / "flow_gate" / "services" / "ai_invoke" / "finalize.py").read_text(encoding="utf-8")
    body = src[src.index("def _finalize_run("):src.index("def _auto_resume_test_gate_chain(")]
    assert body.index('terminal.cleanup(run, handoff=respawn_pending, reason="normal_finish")') < \
        body.index("ai_run_end_hooks.run_ended(run)")
    tree = ast.parse((_SERVER_DIR / "modules" / "flow_gate" / "services" / "ai_run_end_hooks.py").read_text(
        encoding="utf-8"))
    imported = {(n.module or "").split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)}
    imported |= {a.name.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
    assert imported <= {"__future__", "logging", "threading", "typing"}
def test_the_source_tools_ask_the_card_port_before_the_sequence_and_the_port_is_stdlib_only():
    src = (_SERVER_DIR / "modules" / "flow_gate" / "services" / "remote_tool_service.py").read_text(encoding="utf-8")
    body = src[src.index("def _worker_token_step_type_result("):src.index("def _worker_token_step_type(")]
    assert body.index("_SELF_SCOPED_WORK_TYPES") < body.index("worker_step_type_hooks.token_step_type(token_rec)") \
        < body.index("db_wfseq.get_sequence_for_member_doc")
    tree = ast.parse((_SERVER_DIR / "modules" / "flow_gate" / "services" / "worker_step_type_hooks.py").read_text(
        encoding="utf-8"))
    imported = {(n.module or "").split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)}
    imported |= {a.name.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
    assert imported <= {"__future__", "threading", "typing"}
def test_integration_registers_every_port_and_the_run_end_hook():
    from modules.flow_gate.graph_workflow.services import card_settings_ports as ports
    from modules.flow_gate.services import ai_run_end_hooks, worker_step_type_hooks

    from flowgate.card_settings import integration

    integration.unregister()
    try:
        assert not any(ports.registered(n) for n in ports.PORT_NAMES)
        assert not ai_run_end_hooks.registered(ai_run_end_hooks.ON_RUN_ENDED)
        assert not worker_step_type_hooks.registered(worker_step_type_hooks.TOKEN_STEP_TYPE)
        integration.register()
        assert all(ports.registered(n) for n in ports.PORT_NAMES)
        assert ai_run_end_hooks.registered(ai_run_end_hooks.ON_RUN_ENDED)
        assert worker_step_type_hooks.registered(worker_step_type_hooks.TOKEN_STEP_TYPE)
    finally:
        integration.unregister()
    ai_run_end_hooks.run_ended({"run_id": "x"})                           # nothing registered: a no-op
    assert worker_step_type_hooks.token_step_type({"token_id": "x", "group_id": FA}) is None
