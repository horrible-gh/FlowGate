"""TR Agent edit -> real revision SQL -> approval/result subject, without a live service."""
import json
from contextlib import nullcontext
from types import SimpleNamespace
import pytest
from test_card_document_edit_agent_0127 import agent, model
from test_card_document_edit_0127 import env, F, D
from modules.flow_gate.db import documents, tokens
from modules.flow_gate.services import token_service, tr_commit_service, tr_scope_service
from modules.flow_gate.services.ai_invoke import worker
from modules.flow_gate.storage import paths
from flowgate.card_document_edit import target, store, idempotency
from flowgate.card_documents import repository

TR = F + ".0003-TR"
HEADER = "---\nproject: flowgate\nmodule: v02\ngroup: 0127\ntype: TR\ndoc_number: 0003-TR\n---\n"
MESSAGE = "fix(inbox): accept commit_message on TR edit"

@pytest.fixture
def tr(agent, monkeypatch):
    path = agent.env.path.with_name("report.md")
    path.write_text(HEADER, encoding="utf-8")
    documents.update(D, {"type_code": "T"})
    repository.delete(F, "research", "instruction", "N")
    repository.insert(F, "research", "instruction", "T", D, True, 0, "user")
    documents.create({"doc_id": TR, "project_id": "flowgate", "module": "v02", "group_id": F,
        "type_code": "TR", "seq": 3, "title": "Report", "status": "draft", "owner_id": "user",
        "file_path": paths.to_storage_relative(path, "flowgate")})
    repository.insert(F, "research", "report", "TR", TR, True, 0, "user")
    monkeypatch.setattr(target.settings, "card_node", lambda *a: {**agent.env.node, "doc_type": "T"})
    # Scope is orthogonal to commit-message validation; keep this integration deterministic.
    monkeypatch.setattr(tr_scope_service, "evaluate", lambda **kw: {"verdict": tr_scope_service.VERDICT_SKIPPED})
    issued = token_service.issue("flowgate", F, "edit", TR, "user")
    run = {**agent.run, "doc_ref": TR, "token_id": issued["token_id"], "current_token_id": issued["token_id"]}
    body = {"action": "edit", "project": "flowgate", "module": "v02", "group_name": F,
        "doc_id": TR, "expected_revision": 0, "edit_reason": "worker_self",
        "content": HEADER + "Report body\n\n## 변경 파일\n\n없음\n"}
    def post(body, api=False):
        if api:
            return worker._inbox_register(run, issued["raw_token"], model(body))
        r = agent.client.post("/flowgate/api/v1/inbox", headers={"Authorization": "Bearer " + issued["raw_token"]}, json=body)
        return r.status_code, r.json()
    def state():
        return (documents.get_by_id(TR), path.read_bytes(),
            agent.env.db._fetch_all("SELECT * FROM document_revisions"),
            agent.env.db._fetch_all("SELECT * FROM workflow_events"), tokens.get_by_id(issued["token_id"]))
    return SimpleNamespace(agent=agent, path=path, post=post, body=body, state=state, issued=issued)

@pytest.mark.parametrize("api,dry", [(False, False), (False, True), (True, False)])
@pytest.mark.parametrize("value", [None, "", "  ", "한글", "café", "a\nb", "a\rb", "a\vb", "x"*201])
def test_invalid_is_atomic(tr, value, dry, api):
    body = {**tr.body, "commit_message": value}
    if not api:
        body["dry_run"] = dry
    before = tr.state()
    status, result = tr.post(body, api)
    assert status == 422, result
    assert result["error"]["code"] == "invalid_commit_message"
    assert result["error"]["details"]["reason"] == "tr_commit_message_invalid"
    assert "type(scope): summary" in result["error_message"]
    assert tr.state() == before

@pytest.mark.parametrize("api", [False, True])
@pytest.mark.parametrize("reason", ["worker_self", "rejected"])
def test_missing_message_rejected(tr, api, reason):
    body = {**tr.body, "edit_reason": reason}
    if reason == "rejected":
        history = [{"rejection_id": "rej-test", "reason": "fix", "review_id": 1}]
        documents.update(TR, {"rejection_history": json.dumps(history), "doc_review_status": "rejected"})
        body.update(rejection_response="Fixed", rejection_id="rej-test")
    before = tr.state()
    status, result = tr.post(body, api)
    assert status == 422 and result["error"]["code"] == "invalid_commit_message", result
    assert tr.state() == before

@pytest.mark.parametrize("api", [False, True])
@pytest.mark.parametrize("message", [MESSAGE, "accept commit message", "x"*200, "  fix(inbox):   accept commit_message on TR edit  "])
def test_saved_message_drives_subject(tr, monkeypatch, api, message):
    status, result = tr.post({**tr.body, "commit_message": message}, api)
    assert status == 200, result
    saved = documents.get_by_id(TR)
    expected = " ".join(message.split())
    assert saved["commit_message"] == expected and saved["revision_no"] == 1
    monkeypatch.setattr(tr_commit_service, "_tr_commit_type", lambda d: "fix")
    subject = tr_commit_service.commit_subject(saved)
    assert subject == (expected if expected.startswith("fix(") else "fix: " + expected)[:200]
    assert tr.path.read_text(encoding="utf-8").startswith(HEADER)

@pytest.mark.parametrize("stored", [None, "", "한글", MESSAGE])
def test_omission_only_retains_valid_saved_message(tr, stored):
    documents.update(TR, {"commit_message": stored})
    before = tr.state()
    status, result = tr.post(tr.body)
    if stored == MESSAGE:
        assert status == 200, result
        assert documents.get_by_id(TR)["commit_message"] == MESSAGE
    else:
        assert status == 422 and result["error"]["code"] == "invalid_commit_message", result
        assert tr.state() == before


def test_valid_dry_run_does_not_store_or_consume(tr):
    before = tr.state()
    status, result = tr.post({**tr.body, "commit_message": MESSAGE, "dry_run": True})
    assert status == 200, result
    after = tr.state()
    assert after[:-1] == before[:-1]
    assert after[-1]["consumed_at"] == before[-1]["consumed_at"]


def test_revision_failure_rolls_back_message_and_token(tr, monkeypatch):
    before = tr.state()
    def fail(*a):
        raise RuntimeError("injected after row update")
    monkeypatch.setattr(store, "body_replace", fail)
    status, result = tr.post({**tr.body, "commit_message": MESSAGE})
    assert status == 500, result
    assert tr.state() == before

@pytest.mark.parametrize("api", [False, True])
def test_non_tr_field_rejected(agent, api):
    from test_card_document_edit_agent_0127 import payload
    body = payload(commit_message=MESSAGE)
    if api:
        status, result = worker._inbox_register(agent.run, agent.raw, model(body))
    else:
        r = agent.post(body); status, result = r.status_code, r.json()
    assert status == 422, result
    assert result["error"]["params"]["errors"] == [{"field": "commit_message", "reason": "unknown_field"}]


def test_replay_identity_includes_commit_message(tr):
    from flowgate.card_document_edit import agent_adapter
    a, _, _ = agent_adapter.prepare(tr.issued["raw_token"], {**tr.body, "commit_message": MESSAGE})
    b, _, _ = agent_adapter.prepare(tr.issued["raw_token"], {**tr.body, "commit_message": "fix(inbox): other"})
    doc = documents.get_by_id(TR)
    assert idempotency.make_binding(a, doc) != idempotency.make_binding(b, doc)


@pytest.mark.parametrize("message", [MESSAGE, "accept commit message"])
def test_edit_message_reaches_result_commit(tr, monkeypatch, message):
    from flowgate.graph_run import result_commit as rc
    status, response = tr.post({**tr.body, "commit_message": message})
    assert status == 200, response
    saved = documents.get_by_id(TR)
    monkeypatch.setattr(tr_commit_service, "_tr_commit_type", lambda group: "fix")
    row = {"attempt_id": "attempt-test", "group_id": F, "node_key": "research", "input_commit": "base"}
    monkeypatch.setattr(rc.workspace, "attempt", lambda a: row)
    monkeypatch.setattr(rc, "_ready", lambda *a: (row, documents.get_by_id(TR)))
    monkeypatch.setattr(rc.fencing, "exclusive", lambda *a, **kw: nullcontext())
    monkeypatch.setattr(rc.fencing, "guarded", lambda f, call: call())
    monkeypatch.setattr(rc.workspace, "source_root", lambda *a, **kw: tr.path.parent)
    monkeypatch.setattr(rc.workspace, "_project", lambda r: "flowgate")
    monkeypatch.setattr(rc.workspace, "location", lambda *a: (None, None, "attempt-branch"))
    monkeypatch.setattr(rc.workspace, "attempt_head", lambda *a: None)
    monkeypatch.setattr(rc.run_store, "_affected", lambda *a: 1)
    seen = []
    def create(*args, **kw):
        seen.append(args[6])
        return {"committed": True, "commit_sha": "result", "subject": args[6]}
    monkeypatch.setattr(rc.git_commit, "create_attempt_commit", create)
    monkeypatch.setattr(rc.tr_commit_ledger, "record_commit", lambda **kw: seen.append(kw["commit_subject"]))
    result = rc.commit_result("attempt-test", TR)
    expected = MESSAGE if message == MESSAGE else "fix: " + message
    assert result["result_commit"] == "result"
    assert seen == [expected, expected]


@pytest.mark.parametrize("message", [MESSAGE, "", None])
def test_legacy_tr_edit_same_validation_and_storage(tr, message):
    repository.delete(F, "research", "report", "TR")
    body = {**tr.body, "commit_message": message}
    body.pop("expected_revision")
    before = tr.state()
    status, response = tr.post(body)
    if message == MESSAGE:
        assert status == 200, response
        assert documents.get_by_id(TR)["commit_message"] == MESSAGE
    else:
        assert status == 422 and response["error"]["code"] == "invalid_commit_message", response
        assert tr.state() == before
