"""F81 rework: real migrated DB, report files and route permission boundaries."""
import json
from types import SimpleNamespace
import pytest
from fastapi import HTTPException
from test_card_document_edit_0127 import env, D, F
from modules.flow_gate.db import documents, test_runs
from modules.flow_gate.services import test_spec_service as spec, test_run_service as runs
from modules.flow_gate.services import test_card_changes as changes, test_remark_service as remarks
from flowgate.card_document_edit import generated_bridge, dedicated_bridge, type_rules
from flowgate.card_document_edit.contracts import Actor, Built, DedicatedRequest, Origin


def case(cid="TC-001", title="First"):
    return {"case_id": cid, "title": title, "category": "normal", "content": "requirement",
            "check": "manual", "checker": "human", "required": True, "procedure": "inspect",
            "expected": "visible", "check_points": "visible"}


def setup(env, monkeypatch):
    documents.update(D, {"type_code": "TS", "doc_review_status": "approved", "status": "open"})
    env.path.write_text(spec.render_spec_document([case(), case("TC-002", "Second")], title="Scenario"), encoding="utf-8")
    monkeypatch.setattr(runs, "_register_tsr_result", lambda *args: None)
    doc = documents.get_by_id(D)
    rows = spec.map_results(spec.parse_spec(env.path.read_text(encoding="utf-8"))["cases"], [])['cases']
    run = test_runs.insert_spec_run(doc_id=D, revision_no=0, triggered_via="ui", runner_id="user",
        rows=rows, status="failed", overall="NOT_RUN", result_meta=json.dumps({"overall_comment": "Keep this comment"}),
        case_passed=0, case_failed=0, error=None, locale="en",
        case_meta=[spec.case_row_to_meta(r) for r in rows])
    rid = runs.assemble_tsr(doc, run, [], run_chain=False)
    test_runs.set_run_tsr_doc(run["run_id"], rid)
    documents.update(rid, {"doc_review_status": "approved"})
    return doc, run, rid


def body(doc_id):
    from modules.flow_gate.storage import paths
    doc = documents.get_by_id(doc_id)
    return paths.resolve_storage_path(doc["file_path"], "flowgate").read_text(encoding="utf-8")


def save(env, cases, kind="person"):
    class Rule:
        needs_current = True
        def precheck(self, fresh): pass
        def conflict(self, fresh): return RuntimeError("conflict")
        def build(self, fresh, previous):
            return Built(spec.render_spec_document(cases, title="Scenario").encode())
    return dedicated_bridge.dedicated_save(DedicatedRequest(D, documents.get_by_id(D)["revision_no"],
        Actor("user", kind=kind), Rule(), Origin("test_fixture")))


@pytest.mark.parametrize("kind", ["person", "worker"])
def test_approved_text_add_delete_regenerates_report_and_preserves_comment(env, monkeypatch, kind):
    doc, run, rid = setup(env, monkeypatch)
    save(env, [case(title="Wording changed"), case("TC-002", "Second")], kind)
    assert documents.get_by_id(D)["doc_review_status"] == "approved"
    assert documents.get_by_id(rid)["doc_review_status"] == "approved"
    assert "Wording changed" in body(rid) and "Keep this comment" in body(rid)
    save(env, [case(title="Wording changed")], kind)
    assert documents.get_by_id(rid)["doc_review_status"] == "pending_review"
    assert documents.get_by_id(D)["doc_review_status"] == "approved"
    assert "TC-002" not in body(rid) and "Keep this comment" in body(rid)
    assert len(test_runs.list_cases(run["run_id"])) == 2  # immutable execution history
    documents.update(rid, {"doc_review_status": "approved"})
    save(env, [case(), case("TC-003", "New")], kind)
    assert documents.get_by_id(rid)["doc_review_status"] == "pending_review"
    assert "TC-003" in body(rid)
    save(env, [], kind)
    assert "TC-001" not in body(rid) and "TC-003" not in body(rid)
    assert "Keep this comment" in body(rid)


@pytest.mark.parametrize("deleting", [False, True])
def test_running_scenario_conflict_response_and_atomicity(env, monkeypatch, deleting):
    from test_card_document_edit_0127 import _ts_env, _ts_post, TS_ID, TS_CASE

    path, _ = _ts_env(env, monkeypatch)
    before = path.read_bytes()
    cases = [] if deleting else [{**TS_CASE, "title": "Updated"}]
    payload = {"expected_doc_id": TS_ID, "expected_revision": 0,
               "idempotency_key": "ts-running", "cases": cases}
    monkeypatch.setattr(test_runs, "get_running_by_doc", lambda doc_id: {"run_id": "active"})
    result = _ts_post(env, "test-spec", payload)
    assert result.status_code == 409, result.json()
    assert result.json()["error"]["code"] == "run_in_progress"
    assert result.json()["error"]["message"]
    assert path.read_bytes() == before
    assert documents.get_by_id(TS_ID)["revision_no"] == 0

    monkeypatch.setattr(test_runs, "get_running_by_doc", lambda doc_id: None)
    result = _ts_post(env, "test-spec", payload)
    assert result.status_code == 200, result.json()
    assert documents.get_by_id(TS_ID)["revision_no"] == 1


@pytest.mark.parametrize("phase", ["case_write", "test_run"])
@pytest.mark.parametrize("name", ["Kim", "Test AI"])
def test_remark_resolver_and_only_its_stage_reopens(env, monkeypatch, phase, name):
    doc, run, rid = setup(env, monkeypatch)
    row = remarks.add_remark(D, "TC-001", {"text": "Check this", "phase": phase}, author_kind="human", author_name="Kim")
    assert documents.get_by_id(D)["doc_review_status"] == ("pending_review" if phase == "case_write" else "approved")
    assert documents.get_by_id(rid)["doc_review_status"] == ("pending_review" if phase == "test_run" else "approved")
    resolved = remarks.update_remark(D, row["remark_id"], {"state": "resolved"}, resolved_by=name)
    assert resolved["resolved_by"] == resolved["ai_reply"] == name
    assert remarks.list_remarks(D)["remarks"][0]["resolved_by"] == name
    assert remarks.update_remark(D, row["remark_id"], {"state": "open"})["resolved_by"] is None


def test_report_upload_is_server_generated(env):
    from flowgate.card_document_edit.errors import GraphApiError
    with pytest.raises(GraphApiError) as exc:
        type_rules.rule_for({"doc_id": "report", "type_code": "TSR"})
    assert exc.value.code == "report_server_generated"


@pytest.mark.parametrize("mutation", [{}, {"doc_ref": "foreign"}, {"group_id": "foreign"}, {"action_scope": "read"}])
def test_ai_case_run_token_exact_binding(env, monkeypatch, mutation):
    from modules.flow_gate.api.v1 import test_run_routes as routes
    from modules.flow_gate.services import spec_execution_service as execution
    documents.update(D, {"type_code": "TS"})
    auth = {"issued_to": "user", "doc_ref": D, "group_id": F, "action_scope": "test_run", **mutation}
    monkeypatch.setattr(routes, "verify_bearer", lambda req: auth)
    monkeypatch.setattr(runs, "user_can_run_tests", lambda *a: True)
    called = []
    monkeypatch.setattr(execution, "admit", lambda *a, **kw: called.append(kw) or {"run_id": "run"})
    result = routes._post_spec_execution(D, "TC-001", SimpleNamespace(headers={}))
    assert result.status_code == (403 if mutation else 202)
    assert bool(called) == (not mutation)
    if called: assert called[0]["triggered_via"] == "token"
    assert routes._post_spec_execution(D, None, SimpleNamespace(headers={})).status_code == 403


def test_automatic_attachment_failure_preserves_exit_observation(env, monkeypatch):
    from modules.flow_gate.services import test_evidence_service as evidence
    from modules.flow_gate.documents.attachments.errors import AttachmentError
    doc, run, rid = setup(env, monkeypatch)
    monkeypatch.setattr(evidence, "attempt_for", lambda *a: 1)
    def fail(*a, **kw): raise AttachmentError(404, "FLOW_NOT_FOUND", "missing")
    monkeypatch.setattr(evidence.flow, "upload", fail)
    evidence.record_execution(doc, run, "TC-001", 0, 12, "log")
    observed = test_runs.list_cases(run["run_id"])[0]
    assert observed["exit_code"] == 0 and observed["duration_ms"] == 12
    assert spec.load_result_meta(test_runs.get_run(run["run_id"])["result_meta"])["evidence_errors"]


def test_text_save_preserves_valid_human_approval_journal(env, monkeypatch):
    from flowgate.card_documents import decision_repository as repo
    from flowgate.card_settings.report_baseline import fingerprint
    doc, run, rid = setup(env, monkeypatch)
    repo.insert_request({"request_id": "original", "group_id": F, "node_key": "research", "request_key": "original",
        "request_hash": "original", "decision_version": 1, "actor_id": "user", "context_json": "{}", "result_json": "{}", "created_at": "t"})
    repo.append({"decision_id": "original", "request_id": "original", "group_id": F, "node_key": "research",
        "slot": "scenario", "doc_id": D, "revision_no": 0, "document_mark_json": json.dumps(fingerprint(doc)),
        "decision_version": 1, "action": "approve", "before_state": None, "after_state": "approved",
        "actor_kind": "user", "actor_id": "user", "cause": "human_approved", "reason": None,
        "request_key": "original", "cascade_from": None, "created_at": "t"})
    assert repo.valid_approval(F, "research", "scenario", doc)
    save(env, [case(title="Text corrected"), case("TC-002", "Second")])
    fresh = documents.get_by_id(D)
    assert repo.valid_approval(F, "research", "scenario", fresh)
    assert len(env.db._fetch_all("SELECT * FROM graph_document_decisions")) == 2
    assert repo.version(F, "research") == 2


def test_evidence_upload_rebuilds_approved_report(env, monkeypatch):
    import io
    from starlette.datastructures import UploadFile
    from modules.flow_gate.services import test_evidence_service as evidence
    doc, run, rid = setup(env, monkeypatch)
    # Flow upload itself is covered by test_test_evidence_0081; exercise its regeneration handoff here.
    files = []
    monkeypatch.setattr(evidence, "tagged", lambda d: files)
    def uploaded(*args, **kw):
        files.append({"key": "proof-key", "display_name": "proof.txt", "test_evidence": kw["test_evidence"]})
        return {"link": "fg-attachment:proof-key"}
    monkeypatch.setattr(evidence.flow, "upload", uploaded)
    before = documents.get_by_id(rid)["revision_no"]
    result = evidence.upload(doc, "TC-001", "output", UploadFile(filename="proof.txt", file=io.BytesIO(b"ok")), {"user_id": "user"}, 1)
    assert result["link"] in body(rid)
    assert documents.get_by_id(rid)["revision_no"] == before + 1
    assert documents.get_by_id(rid)["doc_review_status"] == "approved"
    assert "Keep this comment" in body(rid)


def test_evidence_delete_rebuilds_report_at_once(env, monkeypatch):
    """F81 v8 one-by-one delete (Flow attachment DELETE): the report md drops the file right away (R §3-1)."""
    import io
    from starlette.datastructures import UploadFile
    from modules.flow_gate.services import test_evidence_service as evidence
    doc, run, rid = setup(env, monkeypatch)
    files = []
    monkeypatch.setattr(evidence, "tagged", lambda d: files)
    def uploaded(*args, **kw):
        files.append({"key": "proof-key", "display_name": "proof.txt", "test_evidence": kw["test_evidence"]})
        return {"link": "fg-attachment:proof-key"}
    monkeypatch.setattr(evidence.flow, "upload", uploaded)
    evidence.upload(doc, "TC-001", "output", UploadFile(filename="proof.txt", file=io.BytesIO(b"ok")), {"user_id": "user"}, 1)
    assert "fg-attachment:proof-key" in body(rid)
    before = documents.get_by_id(rid)["revision_no"]
    tag = files.pop()["test_evidence"]
    evidence.evidence_removed(json.dumps(tag))
    assert "fg-attachment:proof-key" not in body(rid)
    assert documents.get_by_id(rid)["revision_no"] == before + 1
    assert "Keep this comment" in body(rid)
    # A tag of no TS (or none) changes nothing.
    evidence.evidence_removed(json.dumps({"ts_doc_id": "missing", "case_id": "TC-001", "kind": "output", "attempt": 1}))
    evidence.evidence_removed(None)
    assert documents.get_by_id(rid)["revision_no"] == before + 1


def test_flow_remove_hands_a_tagged_file_to_the_report_rebuild(monkeypatch):
    from modules.flow_gate.documents.attachments import flow
    from modules.flow_gate.services import test_evidence_service as evidence
    calls = []
    monkeypatch.setattr(evidence, "evidence_removed", lambda raw: calls.append(raw))
    monkeypatch.setattr(flow, "context", lambda *a, **kw: {"doc_id": "owner"})
    monkeypatch.setattr(flow, "run_in_document_guard", lambda doc_id, commit: commit())
    monkeypatch.setattr(flow, "emit", lambda owner: None)
    monkeypatch.setattr(flow.service, "load_document", lambda doc_id: {"doc_id": doc_id})
    monkeypatch.setattr(flow, "resolve_registered_attachment", lambda doc, name, require_file=False: (None, SimpleNamespace(exists=lambda: False)))
    monkeypatch.setattr(flow, "registry_delete", lambda doc_id, name: None)
    rows = {"tagged": {"doc_id": "d", "filename": "a.txt", "content_sha256": "s", "test_evidence": '{"ts_doc_id": "ts"}'},
            "plain": {"doc_id": "d", "filename": "b.txt", "content_sha256": "s", "test_evidence": None}}
    monkeypatch.setattr(flow, "find", lambda flow_id, key: rows[key])
    assert flow.remove("f", "plain", {}, "s")["deleted"] is True
    assert calls == []
    assert flow.remove("f", "tagged", {}, "s")["deleted"] is True
    assert calls == ['{"ts_doc_id": "ts"}']


def test_fail_result_reopens_report_and_partial_submission_preserves_earlier_case(env, monkeypatch):
    from modules.flow_gate.services import test_basis_service as basis
    doc, run, rid = setup(env, monkeypatch)
    monkeypatch.setattr(basis, "live_state", lambda *a, **kw: {"live": None, "no_worktree": True, "state": "unverifiable", "reasons": []})
    recorded = runs.record_spec_results(doc_id=D, runner_id="user", triggered_via="ui",
        results=[{"case_id": "TC-001", "status": "FAIL", "actual": "wrong"}])
    assert documents.get_by_id(D)["doc_review_status"] == "approved"
    assert documents.get_by_id(rid)["doc_review_status"] == "pending_review"
    runs.assemble_tsr(doc, recorded["run"], [], run_chain=False)
    assert "**FAIL**" in body(rid) and "Keep this comment" in body(rid)
    second = runs.record_spec_results(doc_id=D, runner_id="user", triggered_via="token",
        results=[{"case_id": "TC-002", "status": "NOT_RUN"}])
    assert next(r for r in second["mapped"]["cases"] if r["case_id"] == "TC-001")["status"] == "FAIL"
    runs.assemble_tsr(doc, second["run"], [], run_chain=False)
    assert "**FAIL**" in body(rid) and "Keep this comment" in body(rid)


def test_ai_route_reaches_real_admission_with_approved_report(env, monkeypatch):
    from modules.flow_gate.api.v1 import test_run_routes as routes
    doc, old_run, rid = setup(env, monkeypatch)
    code_cases, errors = spec.validate_cases_payload([{**case(), "check": "code", "checker": None, "run_command": "python --version"}])
    assert not errors
    env.path.write_text(spec.render_spec_document(code_cases, title="Code"), encoding="utf-8")
    auth = {"issued_to": "user", "doc_ref": D, "group_id": F, "action_scope": "test_run"}
    monkeypatch.setattr(routes, "verify_bearer", lambda req: auth)
    monkeypatch.setattr(runs, "user_can_run_tests", lambda *a: True)
    monkeypatch.setattr(runs, "_emit_started", lambda *a: None)
    response = routes._post_spec_execution(D, "TC-001", SimpleNamespace(headers={}))
    assert response.status_code == 202, response.body
    run = test_runs.get_run(json.loads(response.body)["run_id"])
    assert run["triggered_via"] == "token"
    assert spec.load_result_meta(run["result_meta"])["selected_case_ids"] == ["TC-001"]
    assert "Keep this comment" in body(rid)


def test_progress_updates_approved_report_before_next_case(env, monkeypatch):
    from modules.flow_gate.services import spec_execution_service as execution
    doc, previous, rid = setup(env, monkeypatch)
    cases, errors = spec.validate_cases_payload([
        {**case(cid), "check": "code", "checker": None, "run_command": "python --version"}
        for cid in ("TC-001", "TC-002")])
    assert not errors
    env.path.write_text(spec.render_spec_document(cases, title="Code"), encoding="utf-8")
    monkeypatch.setattr(runs, "_emit_started", lambda *a: None)
    admitted = execution.admit(D, case_id=None, runner_id="user", locale="en")
    rid_run = admitted["run_id"]
    from modules.flow_gate.services import test_evidence_service as evidence
    evidence.record_execution(doc, test_runs.get_run(rid_run), "TC-001", 0, 10, "")
    execution.merge_run_meta(rid_run, reported_case_ids=["TC-001"])
    runs.record_execution_progress(doc, rid_run, [{"case_id": "TC-001", "status": "PASS", "actual": "exit 0"}])
    text = body(rid)
    assert "PASS" in next(line for line in text.splitlines() if "| TC-001 |" in line)
    assert "PENDING" in next(line for line in text.splitlines() if "| TC-002 |" in line)
    assert "Keep this comment" in text
    live = {c["case_no"]: c["case_status"] for c in test_runs.list_cases(rid_run)}
    assert live == {"TC-001": "PASS", "TC-002": None}
    assert runs.tsr_gate_state(documents.get_by_id(rid))["passed"] is False


def test_ui_pass_uses_real_output_files_and_returns_current_report(env, monkeypatch):
    import io
    from starlette.datastructures import UploadFile
    from starlette.requests import Request
    from modules.flow_gate.api.v1 import test_run_routes as routes
    from modules.flow_gate.services import test_evidence_service as evidence, test_basis_service as basis
    doc, previous, rid = setup(env, monkeypatch)
    monkeypatch.setattr(basis, "live_state", lambda *a, **kw: {"live": None, "no_worktree": True, "state": "unverifiable", "reasons": []})
    monkeypatch.setattr(routes, "verify_bearer", lambda req: {"_is_user_jwt": True, "issued_to": "user", "is_admin": True})
    for cid in ("TC-001", "TC-002"):
        evidence.upload(doc, cid, "output", UploadFile(filename=cid+".txt", file=io.BytesIO(b"observed")),
                        {"user_id": "user", "is_admin": True}, 1)
    request = Request({"type": "http", "method": "POST", "path": "/", "headers": []})
    response = routes.post_test_results(routes.TestResultsBody(doc_id=D, overall="FAIL",
        results=[{"case_id": cid, "status": "PASS"} for cid in ("TC-001", "TC-002")]), request)
    assert response.status_code == 201, response.body
    data = json.loads(response.body)
    assert data["overall"] == "PASS" and data["gate_passed"]
    assert data["tsr_doc_id"] == rid
    assert [c["case_status"] for c in data["cases"]] == ["PASS", "PASS"]
    assert "Keep this comment" in body(rid)
