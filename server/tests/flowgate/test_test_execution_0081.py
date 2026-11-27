"""F81-10: real Graph stages, case result storage, server report and slot identity."""
import io
import json
import pytest
from starlette.datastructures import UploadFile
from starlette.requests import Request
from test_fg_card_settings import env, db_path, legacy_cards, decided, AI, FA, node, edge, put_draft, _h, _start, _sql
from test_fg_card_settings_review import _admin_and_reviewer, _start_ai, _write_written, INSTRUCTION_RUN, WORK_RUN
from test_fg_question_pending_0111 import _ai_run_finished, _ended, card_outputs_port

pytestmark = pytest.mark.usefixtures("legacy_cards")


def start_test(env, monkeypatch, code_root=None, mixed=False):
    from flowgate.card_document_edit import integration
    from modules.flow_gate.services import test_spec_service as spec, test_run_service as runner, test_basis_service as basis
    from flowgate.card_documents import lines
    integration.register()
    import os
    monkeypatch.setenv("FLOWGATE_AGENT_PEPPER_ID", os.environ.get("FLOWGATE_AGENT_PEPPER_ID", "primary"))
    _admin_and_reviewer(env)
    first = put_draft(env, FA, 0, [node("tmp_r", "R"), node("tmp_t", "TS", assignee=AI)],
                      [edge("tmp_e", "tmp_r", "tmp_t")])
    key = first["key_map"]["tmp_t"]
    response = env["client"].post(f"/api/v1/flows/{FA}/graph/revisions", json={"draft_version": 1}, headers=_h())
    assert response.status_code == 201, response.text
    if code_root is None:
        monkeypatch.setattr(basis, "live_state", lambda *a, **kw: {"live": None, "no_worktree": True, "state": "unverifiable", "reasons": []})
    _start(env)
    [job] = env["jobs"]
    env["jobs"].clear()
    assert job["stage"] == "instruction"
    _start_ai(monkeypatch, job, INSTRUCTION_RUN)
    cases = [{"case_id": cid, "title": cid, "category": "normal", "content": "Check observed output",
              "check": "manual", "checker": "ai", "required": True, "procedure": "inspect", "expected": "ok"}
             for cid in ("TC-001", "TC-002")]
    if code_root is not None:
        # Phase 1 creates an untracked script only in the attempt, not in the group.
        (code_root / "tests" / "generated.py").write_text("assert 2 + 2 == 4\n")
        manual = cases[1:] if mixed else []
        cases = [{**cases[0], "check": "code", "checker": None,
                  "automation_ref": "tests/generated.py", "run_command": "python tests/generated.py"}] + manual
    from flowgate.card_document_edit import dedicated_bridge
    from flowgate.card_document_edit.contracts import Actor, Built, DedicatedRequest, Origin
    class Rule:
        needs_current = True
        def precheck(self, doc): pass
        def conflict(self, doc): return RuntimeError("conflict")
        def build(self, doc, previous):
            return Built(spec.render_spec_document(cases, title="Scenario").encode())
    dedicated_bridge.dedicated_save(DedicatedRequest(job["target_doc_id"], 0,
        Actor("u_kim", kind="worker"), Rule(), Origin("test_fixture")))
    _ai_run_finished(env, INSTRUCTION_RUN, job["graph_exec_id"], doc_ref=job["target_doc_id"])
    _ended(env, INSTRUCTION_RUN, job["graph_exec_id"], action_scope="edit", doc_ref=job["target_doc_id"])
    [work] = env["jobs"]
    env["jobs"].clear()
    assert work["stage"] == "result"
    ts_id = job["target_doc_id"]
    report_id = lines.line_document(FA, key, "report", "document", "TS")["doc_id"]
    _, token = _start_ai(monkeypatch, work, WORK_RUN)
    from modules.flow_gate.db import tokens
    auth = tokens.get_by_id(token)
    assert auth["action_scope"] == "test_run" and auth["doc_ref"] == ts_id
    return key, work, ts_id, report_id, auth


def record(monkeypatch, ts_id, auth, statuses):
    from modules.flow_gate.api.v1 import test_run_routes as routes
    from modules.flow_gate.db import documents
    from modules.flow_gate.services import test_evidence_service as evidence
    monkeypatch.setattr(routes, "verify_bearer", lambda r: auth)
    for i, status in enumerate(statuses, 1):
        cid = f"TC-{i:03d}"
        if status == "PASS":
            evidence.upload(documents.get_by_id(ts_id), cid, "output",
                            UploadFile(filename=cid+".txt", file=io.BytesIO(b"observed ok")),
                            {"user_id": "u_kim", "is_admin": True}, 1)
        response = routes.post_test_results(routes.TestResultsBody(doc_id=ts_id, results=[
            {"case_id": cid, "status": status, "actual": "observed", "skip_reason": "not available"}]),
            Request({"type": "http", "method": "POST", "path": "/", "headers": []}))
        assert response.status_code == 201, response.body
    return json.loads(response.body)


@pytest.mark.parametrize("stop", [None, "report_server_generated", "question_pending"])
def test_ai_two_stages_complete_with_slot_report_and_rejected_upload(env, decided, monkeypatch, stop):
    from modules.flow_gate.db import documents
    from modules.flow_gate.services import test_run_service as runs
    from flowgate.card_document_edit import type_rules
    from flowgate.card_document_edit.errors import GraphApiError
    from flowgate.card_documents import lines
    key, work, ts_id, report_id, auth = start_test(env, monkeypatch)
    data = record(monkeypatch, ts_id, auth, ["PASS", "PASS"])
    assert data["tsr_doc_id"] == report_id
    report = documents.get_by_id(report_id)
    assert report["revision_no"] >= 1 and lines.has_body(report)
    assert "TC-001" in runs._read_doc_content_or_empty(report)
    with pytest.raises(GraphApiError) as error:
        type_rules.rule_for(report)
    assert error.value.code == "report_server_generated"
    _ai_run_finished(env, WORK_RUN, work["graph_exec_id"], stop_code=stop, doc_ref=ts_id)
    _ended(env, WORK_RUN, work["graph_exec_id"], action_scope="test_run", doc_ref=ts_id, stop_code=stop)
    row = _sql(env["db"], "SELECT * FROM graph_node_runs WHERE node_key = ?", (key,))[0]
    assert row["status"] == "done", row
    assert row["result_doc_id"] == report_id
    assert not row["dispatch_error_code"]
    assert documents.get_by_id(report_id)["doc_review_status"] == "approved"


@pytest.mark.parametrize("statuses, expected", [(["FAIL", "PASS"], "test_gate_fail"), (["BLOCKED", "PASS"], "test_gate_blocked"), (["PASS"], "test_gate_not_run")])
def test_terminal_nonpass_fails_instead_of_waiting(env, decided, monkeypatch, statuses, expected):
    key, work, ts_id, report_id, auth = start_test(env, monkeypatch)
    record(monkeypatch, ts_id, auth, statuses)
    _ai_run_finished(env, WORK_RUN, work["graph_exec_id"], doc_ref=ts_id)
    _ended(env, WORK_RUN, work["graph_exec_id"], action_scope="test_run", doc_ref=ts_id)
    row = _sql(env["db"], "SELECT * FROM graph_node_runs WHERE node_key = ?", (key,))[0]
    assert row["dispatch_error_code"] == expected
    assert _sql(env["db"], "SELECT stage_state FROM graph_stage_assignments WHERE stage = 'result'")[0]["stage_state"] == "failed"


def test_report_assembly_error_is_explicit_failure(env, decided, monkeypatch):
    from modules.flow_gate.services import test_run_service as runs
    key, work, ts_id, report_id, auth = start_test(env, monkeypatch)
    record(monkeypatch, ts_id, auth, ["PASS", "PASS"])
    def fail(*args, **kw):
        raise RuntimeError("disk unavailable")
    monkeypatch.setattr(runs, "assemble_tsr", fail)
    _ai_run_finished(env, WORK_RUN, work["graph_exec_id"], doc_ref=ts_id)
    _ended(env, WORK_RUN, work["graph_exec_id"], action_scope="test_run", doc_ref=ts_id)
    row = _sql(env["db"], "SELECT * FROM graph_node_runs WHERE node_key = ?", (key,))[0]
    assert row["dispatch_error_code"] == "report_assembly_failed"


@pytest.mark.parametrize("change", [None, "group", "attempt", "missing", "integrated", "mixed",
                                  "group_unmeasurable", "binding_changed"])
def test_code_attempt_basis_completes_or_reports_source_change(env, decided, monkeypatch, tmp_path, change):
    from basis_support import make_repo
    from group_lock_stub import stub_group_lock
    from modules.flow_gate.db import documents, test_runs
    from modules.flow_gate.services import (test_basis_service as basis,
        test_run_service as runner, spec_execution_service as execution,
        source_fingerprint, graph_source_hooks)
    from modules.flow_gate.workflow import pipeline_service
    group = make_repo(tmp_path / "group-source")
    attempt = make_repo(tmp_path / "attempt-source")
    monkeypatch.setattr(source_fingerprint, "resolve_worktree", lambda *a: group)
    monkeypatch.setattr(source_fingerprint, "_MEMO", {})
    monkeypatch.setattr(graph_source_hooks, "source_root", lambda *a, **kw: attempt)
    monkeypatch.setattr(runner, "_scratch_dir", lambda doc, rid: tmp_path / "runs" / rid)
    stub_group_lock(monkeypatch)
    key, work, ts_id, report_id, auth = start_test(env, monkeypatch, code_root=attempt, mixed=change == "mixed")
    assert not (group / "tests" / "generated.py").exists()
    admitted = execution.admit(ts_id, case_id=None, runner_id="u_kim", locale="ko",
                               triggered_via="token", execution_token=auth)
    if change == "group_unmeasurable":
        def unavailable(*args):
            raise source_fingerprint.SourceFingerprintError("group_worktree_unavailable", "missing group")
        monkeypatch.setattr(source_fingerprint, "resolve_worktree", unavailable)
    elif change == "binding_changed":
        from modules.flow_gate.db import tokens
        original_get = tokens.get_by_id
        monkeypatch.setattr(tokens, "get_by_id", lambda tid:
            {**auth, "doc_ref": "another-ts"} if tid == auth["token_id"] else original_get(tid))
    execution.execute(test_runs.get_run(admitted["run_id"]))
    if change in ("group_unmeasurable", "binding_changed"):
        refused = test_runs.get_run(admitted["run_id"])
        reason = ("group_worktree_unavailable" if change == "group_unmeasurable"
                  else "execution_token_binding_changed")
        assert refused["status"] == "failed" and refused["error"] == "basis_capture_failed", refused
        meta = json.loads(refused["result_meta"])
        assert meta["phase"] == execution.PHASE_FINISHED
        assert meta["prepare_refused"] == {"error": "basis_capture_failed", "reasons": reason}
        assert not meta.get("reported_case_ids") and not meta.get("test_basis")
        assert not (tmp_path / "runs" / admitted["run_id"]).exists()
        assert not runner.tsr_gate_state(documents.get_by_id(report_id))["passed"]
        with pytest.raises(pipeline_service.TransitionError):
            pipeline_service._require_test_gate_for_approval(documents.get_by_id(report_id))
        assert execution.attach_chain(documents.get_by_id(ts_id), {}) is None
        return
    if change == "mixed":
        from modules.flow_gate.api.v1 import test_run_routes as routes
        from modules.flow_gate.services import test_evidence_service as evidence
        monkeypatch.setattr(routes, "verify_bearer", lambda r: auth)
        evidence.upload(documents.get_by_id(ts_id), "TC-002", "output",
                        UploadFile(filename="observed.txt", file=io.BytesIO(b"ok")),
                        {"user_id": "u_kim", "is_admin": True}, 1)
        response = routes.post_test_results(routes.TestResultsBody(doc_id=ts_id, results=[
            {"case_id": "TC-002", "status": "PASS", "actual": "observed"}]),
            Request({"type": "http", "method": "POST", "path": "/", "headers": []}))
        assert response.status_code == 201, response.body
    latest = test_runs.latest_spec_run(ts_id)
    assert latest and latest["overall"] == "PASS", test_runs.latest_by_doc(ts_id)
    meta = json.loads(latest["result_meta"])
    assert meta["live_verdict"]["state"] == basis.VALID
    assert meta["test_basis"]["binding"]["execution_token_id"] == auth["token_id"]
    report = documents.get_by_id(report_id)
    assert runner.tsr_gate_state(report)["passed"]
    pipeline_service._require_test_gate_for_approval(report)
    assert execution.attach_chain(documents.get_by_id(ts_id), {})["finished"]
    if change in ("group", "attempt"):
        root = group if change == "group" else attempt
        (root / "server" / "app.py").write_text("VALUE = 99\n")
    elif change == "missing":
        monkeypatch.setattr(graph_source_hooks, "source_root", lambda *a, **kw: None)
    elif change == "integrated":
        (group / "tests" / "generated.py").write_bytes((attempt / "tests" / "generated.py").read_bytes())
        monkeypatch.setattr(graph_source_hooks, "source_root", lambda *a, **kw: None)
    blocked = change in ("group", "attempt", "missing")
    gate = runner.tsr_gate_state(documents.get_by_id(report_id))
    assert gate["passed"] is not blocked, gate
    if blocked:
        assert gate["basis_reasons"]
        assert gate["basis_state"] == (basis.UNVERIFIABLE if change == "missing" else basis.STALE)
        with pytest.raises(pipeline_service.TransitionError):
            pipeline_service._require_test_gate_for_approval(documents.get_by_id(report_id))
        assert execution.attach_chain(documents.get_by_id(ts_id), {}) is None
    _ai_run_finished(env, WORK_RUN, work["graph_exec_id"], doc_ref=ts_id)
    _ended(env, WORK_RUN, work["graph_exec_id"], action_scope="test_run", doc_ref=ts_id)
    row = _sql(env["db"], "SELECT * FROM graph_node_runs WHERE node_key = ?", (key,))[0]
    if blocked:
        assert row["status"] != "done", row
        assert "test_gate_" + gate["basis_state"] in row["dispatch_error_code"]
        assert gate["basis_reasons"][0] in row["dispatch_error_code"]
    else:
        assert row["status"] == "done", row
        assert not row["dispatch_error_code"]
        assert row["result_doc_id"] == report_id
        assert documents.get_by_id(report_id)["doc_review_status"] == "approved"
