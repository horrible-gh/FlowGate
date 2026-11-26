"""Evidence persistence, isolation, previews and authoritative PASS checks."""
import io
import json
import pytest
from fastapi import HTTPException
from starlette.datastructures import UploadFile
from test_attachment_upload_requests_0017 import (
    env, fg_store, clean_runs, ADMIN_USER, VIEWER_USER, WORKER_USER, DOC, DOC2, GROUP,
)
from modules.flow_gate.db import documents
from modules.flow_gate.services import test_evidence_service as svc, test_run_service as runner
from modules.flow_gate.documents.attachments import flow
from modules.flow_gate.documents.attachments.errors import AttachmentError
from modules.flow_gate.documents.attachments.requests import RequestError


@pytest.fixture
def scenario(env, monkeypatch):
    store = env["store"]
    store._execute("UPDATE documents SET type_code = 'TS' WHERE doc_id = ?", [DOC2])
    doc = documents.get_by_id(DOC2)
    cases = [{"case_id": "TC-001", "check": "manual", "execution_mode": "manual"},
             {"case_id": "TC-002", "check": "code", "execution_mode": "automated"}]
    monkeypatch.setattr(runner, "load_spec_ts", lambda doc_id: (doc, {"cases": cases}))
    history = []
    monkeypatch.setattr(svc.test_runs, "list_by_doc", lambda doc_id: list(reversed(history)))
    return doc, cases, history


def part(data=b"hello", name="proof.txt"):
    return UploadFile(filename=name, file=io.BytesIO(data))


def test_upload_kinds_multiple_files_links_preview_and_real_count(scenario):
    doc, cases, _ = scenario
    assert svc.pass_reason(doc, cases[0]) == "output_evidence_required"
    for kind in ("input", "log", "output", "output"):
        svc.upload(doc, "TC-001", kind, part(), ADMIN_USER, 1)
    listed = svc.listing(doc, "TC-001")
    assert len(listed["attachments"]) == 4
    assert len({f["filename"] for f in listed["attachments"]}) == 4
    assert listed["cases"]["TC-001"]["pass_reason"] is None
    for item in listed["attachments"]:
        assert item["link"] == "fg-attachment:" + item["key"]
        assert svc.preview(doc, item["key"]) == {"kind": "text", "text": "hello"}
    assert len(svc.listing(doc, "TC-001", "output")["attachments"]) == 2
    report = runner.scenario_report(DOC2, [{**cases[0], "status": "NOT_RUN"}], None)
    assert report["cases"][0]["evidence"] == {"input": 1, "log": 1, "output": 2, "total": 4}
    assert svc.pass_reason(doc, cases[0]) is None
    assert svc.tagged({**doc, "doc_id": DOC}) == []


def test_wrong_case_flow_kind_attempt_and_rbac_fail_closed(scenario):
    doc, _, _ = scenario
    with pytest.raises(RequestError):
        svc.upload(doc, "TC-001", "output", part(), VIEWER_USER, 1)
    with pytest.raises(AttachmentError):
        svc.upload(doc, "TC-999", "output", part(), ADMIN_USER, 1)
    with pytest.raises(AttachmentError):
        svc.upload(doc, "TC-001", "url", part(), ADMIN_USER, 1)
    with pytest.raises(HTTPException) as stale:
        svc.upload(doc, "TC-001", "output", part(), ADMIN_USER, 2)
    assert stale.value.status_code == 409
    with pytest.raises(AttachmentError):
        svc.validate_tag("another-flow", {"ts_doc_id": DOC2, "case_id": "TC-001", "kind": "output", "attempt": 1})
    assert svc.listing(doc)["attachments"] == []


def test_retry_keeps_previous_files_and_cannot_pass_on_old_output(scenario):
    doc, cases, history = scenario
    svc.upload(doc, "TC-001", "output", part(b"old"), WORKER_USER, 1)
    history.extend({"run_id": name, "result_meta": json.dumps({"run_kind": "spec_execution", "selected_case_ids": ["TC-001"]})}
                   for name in ("r1", "r2"))
    assert svc.attempt_for(DOC2, "TC-001") == 2
    assert svc.pass_reason(doc, cases[0]) == "output_evidence_required"
    svc.upload(doc, "TC-001", "output", part(b"new"), WORKER_USER, 2)
    items = svc.listing(doc)["attachments"]
    items.sort(key=lambda i: i["test_evidence"]["attempt"])
    assert [i["test_evidence"]["attempt"] for i in items] == [1, 2]
    assert [svc.preview(doc, i["key"])["text"] for i in items] == ["old", "new"]
    assert svc.pass_reason(doc, cases[0]) is None


@pytest.mark.parametrize("exit_code", [None, 0, 1, -9])
def test_code_pass_uses_stored_exit_only(scenario, monkeypatch, exit_code):
    doc, cases, history = scenario
    history.append({"run_id": "r1", "result_meta": json.dumps({"run_kind": "spec_execution", "selected_case_ids": ["TC-002"]})})
    monkeypatch.setattr(svc.test_runs, "list_cases", lambda rid: [{"case_no": "TC-002", "exit_code": exit_code}])
    result = {"case_id": "TC-002", "status": "PASS", "exit_code": 0}
    if exit_code == 0:
        svc.validate_passes(doc, cases, [result])
    else:
        with pytest.raises(HTTPException) as error:
            svc.validate_passes(doc, cases, [result])
        assert error.value.detail["error"] == "exit_zero_required"
    svc.validate_passes(doc, cases, [{"case_id": "TC-002", "status": "FAIL"}])


def test_manual_pass_cannot_use_payload_text_or_input_as_output(scenario):
    doc, cases, _ = scenario
    svc.upload(doc, "TC-001", "input", part(), ADMIN_USER, 1)
    with pytest.raises(HTTPException) as error:
        svc.validate_passes(doc, cases, [{"case_id": "TC-001", "status": "PASS", "evidence": [{"kind": "text", "value": "passed"}]}])
    assert error.value.detail["error"] == "output_evidence_required"
    svc.upload(doc, "TC-001", "output", part(), ADMIN_USER, 1)
    svc.validate_passes(doc, cases, [{"case_id": "TC-001", "status": "PASS"}])


def test_exit_summary_and_error_tail_are_registered_files(scenario, monkeypatch):
    doc, _, history = scenario
    run = {"run_id": "r1", "runner_id": WORKER_USER["user_id"], "locale": "en",
           "result_meta": json.dumps({"run_kind": "spec_execution", "selected_case_ids": ["TC-002"]})}
    history.append(run)
    monkeypatch.setattr(svc.test_runs, "list_cases", lambda rid: [{"id": 7, "case_no": "TC-002"}])
    observed = []
    monkeypatch.setattr(svc.test_runs, "update_case_observation", lambda **kw: observed.append(kw))
    svc.record_execution(doc, run, "TC-002", 1, 123, "\n".join(str(i) for i in range(250)))
    assert observed[0]["exit_code"] == 1 and observed[0]["duration_ms"] == 123
    items = svc.listing(doc, "TC-002", "log")["attachments"]
    assert len(items) == 2
    from modules.flow_gate.documents.attachments import service
    contents = {}
    for item in items:
        row = svc.file_row(doc, item["key"])
        path, _ = service.resolve_download(row["doc_id"], row["filename"])
        contents[item["filename"]] = path.read_text(encoding="utf-8")
    assert any("Error exit" in c and "123 ms" in c for c in contents.values())
    tail = next(c for name, c in contents.items() if name.endswith(".log"))
    assert len(tail.splitlines()) == 200 and tail.splitlines()[0] == "50"


def test_previews_are_bounded_and_unknown_files_are_download_only(scenario):
    doc, _, _ = scenario
    for name, data, kind in [("data.csv", b"row\n" * 30, "text"), ("result.bin", b"xyz", "file"), ("picture.png", b"png", "image")]:
        svc.upload(doc, "TC-001", "output", part(data, name), ADMIN_USER, 1)
        item = next(i for i in svc.listing(doc)["attachments"] if i["filename"] == name)
        result = svc.preview(doc, item["key"])
        assert result["kind"] == kind
        if kind == "text":
            assert len(result["text"].splitlines()) == 12
    with pytest.raises(HTTPException):
        svc.preview({**doc, "doc_id": DOC}, item["key"])


def test_worker_evidence_scope_is_exact(scenario, monkeypatch):
    from modules.flow_gate.api.v1 import test_run_routes as routes
    from modules.flow_gate.api.v1 import document_routes
    doc, _, _ = scenario
    auth = {"issued_to": WORKER_USER["user_id"], "doc_ref": DOC2, "group_id": GROUP, "action_scope": "test_run"}
    monkeypatch.setattr(routes, "verify_bearer", lambda request: auth)
    assert routes._evidence_auth(DOC2, None, True)[0]["doc_id"] == DOC2
    auth["doc_ref"] = DOC
    with pytest.raises(HTTPException) as denied:
        routes._evidence_auth(DOC2, None, True)
    assert denied.value.status_code == 403


def test_http_upload_filter_preview_download_and_invalid_kind(scenario, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from modules.flow_gate.api.v1 import test_run_routes as routes
    auth = {"_is_user_jwt": True, "issued_to": WORKER_USER["user_id"]}
    monkeypatch.setattr(routes, "verify_bearer", lambda request: auth)
    app = FastAPI()
    app.include_router(routes.router)
    client = TestClient(app)
    base = f"/api/v1/documents/{DOC2}/test-spec"
    response = client.post(base + "/cases/TC-001/evidence", files={"file": ("out.txt", b"evidence")}, data={"kind": "output", "attempt": 1})
    assert response.status_code == 201, response.text
    assert response.json()["link"].startswith("fg-attachment:")
    listing = client.get(base + "/evidence", params={"case_id": "TC-001", "kind": "output"})
    item = listing.json()["attachments"][0]
    assert client.get(item["download_url"]).content == b"evidence"
    assert client.get(item["preview_url"]).json()["text"] == "evidence"
    bad = client.post(base + "/cases/TC-001/evidence", files={"file": ("out.txt", b"evidence")}, data={"kind": "url", "attempt": 1})
    assert bad.status_code == 422
    auth["issued_to"] = VIEWER_USER["user_id"]
    denied = client.post(base + "/cases/TC-001/evidence", files={"file": ("out.txt", b"evidence")}, data={"kind": "output", "attempt": 1})
    assert denied.status_code == 403


def test_manual_reset_starts_a_new_attempt_without_discarding_files(scenario, monkeypatch):
    doc, cases, history = scenario
    svc.upload(doc, "TC-001", "output", part(b"previous"), WORKER_USER, 1)
    records = {}
    monkeypatch.setattr(svc.test_runs, "list_cases", lambda rid: records[rid])
    for rid, status in [("pass", "PASS"), ("reset", "NOT_RUN"), ("again", "NOT_RUN")]:
        history.append({"run_id": rid, "result_meta": "{}"})
        records[rid] = [{"case_no": "TC-001", "case_status": status,
                         "case_meta": json.dumps({"result_origin": "manual"})}]
    assert svc.attempt_for(DOC2, "TC-001") == 2
    assert svc.pass_reason(doc, cases[0]) == "output_evidence_required"
    svc.upload(doc, "TC-001", "output", part(b"current"), WORKER_USER, 2)
    assert svc.pass_reason(doc, cases[0]) is None
    assert len(svc.listing(doc)["attachments"]) == 2
