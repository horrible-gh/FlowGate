"""0127 T#2 Agent: real SQLite, token verification/claim, common revision and file store."""
import importlib
import json
from datetime import datetime, timedelta, timezone
from dataclasses import replace
from types import SimpleNamespace

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from test_card_document_edit_0127 import env, F, D, HEADER, business_state, count
from modules.flow_gate.api import inbox_routes as inbox
from modules.flow_gate.db import documents, tokens, document_revisions
from modules.flow_gate.services import token_service, graph_source_hooks, api_server_tools
from modules.flow_gate.services.ai_invoke import worker
from modules.flow_gate.graph_workflow.services import card_document_ports as ports, flow_guard
from modules.flow_gate.graph_workflow.api import envelope
from modules.flow_gate.storage import paths
from flowgate.card_document_edit import integration, agent_adapter, agent_binding, agent_errors, guards, contacts, target, legacy_edit
from flowgate.card_documents import repository


@pytest.fixture
def agent(env, monkeypatch):
    monkeypatch.setenv("FLOWGATE_TOKEN_PEPPER_ACTIVE_ID", "test")
    monkeypatch.setenv("FLOWGATE_TOKEN_PEPPER_test", "agent-test-pepper-0127")
    integration.register()
    monkeypatch.setattr(legacy_edit, "has_permission", lambda *a: True)
    monkeypatch.setattr(graph_source_hooks, "_fence_handler", None)
    from modules.flow_gate.services import node_title_hooks
    monkeypatch.setattr(node_title_hooks, "after_registration", lambda *a: None)
    issued = token_service.issue("flowgate", F, "edit", D, "user")
    raw = issued["raw_token"]
    app = FastAPI()
    app.include_router(inbox.router, prefix="/flowgate")
    client = TestClient(app)
    run = {"run_id": "test-agent-run", "project_id": "flowgate", "group_id": F, "module": "v02",
           "action_scope": "edit", "doc_ref": D, "token_id": issued["token_id"], "current_token_id": issued["token_id"],
           "transport_api_base": "http://localhost/flowgate/api/v1"}
    def post(payload):
        return client.post("/flowgate/api/v1/inbox", headers={"Authorization": "Bearer " + raw}, json=payload)
    def transport(run, raw_token, body):
        response = client.post("/flowgate/api/v1/inbox", headers={"Authorization": "Bearer " + raw_token}, json=body)
        return response.status_code, response.json()
    monkeypatch.setattr(worker, "_post_inbox_json", transport)
    return SimpleNamespace(env=env, raw=raw, issued=issued, client=client, run=run, post=post)


def payload(**kw):
    return {"action": "edit", "project": "flowgate", "module": "v02", "group_name": F, "doc_id": D,
            "expected_revision": 0, "content": HEADER + "agent revised\n", "edit_reason": "worker_self", **kw}


def model(body):
    return {k: v for k, v in body.items() if k not in {"action", "project", "module", "group_name", "doc_id"}}


def errors(response):
    return response["error"]["params"]["errors"]


def assert_failure(response, status, code):
    assert response.status_code == status, response.text
    value = response.json()
    assert value["ok"] is False and value["http_status"] == status
    assert value["error"]["code"] == code
    assert value["error_message"] == value["error"]["message"]
    assert value["help_url"] == "/flowgate/api/v1/help"
    assert "server_ts" in value
    return value


def test_cli_real_store_token_once_and_compatible_summary(agent):
    env = agent.env
    response = agent.post(payload())
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["card_binding"] == {"flow_id": F, "node_key": "research", "slot": "instruction", "doc_id": D}
    assert result["revision_no"] == 1 and not result["replayed"]
    assert result["line"]["doc_id"] == D
    assert result["change_summary"]["before"]["revision_no"] == 0
    assert result["change_summary"]["after"]["revision_no"] == 1
    assert env.path.read_text() == payload()["content"]
    assert len(document_revisions.list_by_doc(D)) == 1
    assert [kind for kind, _ in env.events] == ["document", "canvas", "graph"]
    assert env.events[-1][1][-1] == agent.issued["token_id"]
    assert env.db._fetch_one("SELECT COUNT(*) n FROM workflow_events WHERE event_type='token_consumed'")["n"] == 1
    meta = json.loads(env.db._fetch_one("SELECT metadata FROM workflow_events WHERE event_type='doc_edited'")["metadata"])
    assert meta["origin"] == "agent" and meta["actor_kind"] == "worker"
    assert meta["token_id"] == agent.issued["token_id"] and meta["idem_key"] == agent.issued["token_id"]
    before = business_state(env)
    assert_failure(agent.post(payload()), 401, "unauthorized")
    assert business_state(env) == before


def test_api_uses_raw_expected_revision_and_same_success(agent):
    status, value = worker._inbox_register(agent.run, agent.raw, model(payload()))
    assert status == 200, value
    assert value["revision_no"] == 1 and value["card_binding"]["doc_id"] == D
    assert value["stored_path"] and value["previous_revision_path"]
    assert value["change_summary"]["changed"] is True
    status, value = worker._inbox_register(agent.run, agent.raw, {"flow_id": "forged"})
    assert status == 401 and value["error"]["code"] == "unauthorized"


@pytest.mark.parametrize("value,reason", [(None,"required"),(True,"invalid_type"),(False,"invalid_type"),("0","invalid_type"),(0.0,"invalid_type"),(-1,"invalid_format")])
def test_revision_input_same_cli_and_api(agent, value, reason):
    body = payload(expected_revision=value, content=None)
    cli = assert_failure(agent.post(body), 422, "expected_revision_missing")
    status, api = worker._inbox_register(agent.run, agent.raw, model(body))
    assert status == 422 and api["error"] == cli["error"]
    assert errors(cli) == [{"field": "expected_revision", "reason": reason}]
    assert documents.get_by_id(D)["revision_no"] == 0


@pytest.mark.parametrize("change,field,reason", [
    ({"content":None},"content","required"), ({"content":7},"content","invalid_type"),
    ({"content":None,"doc_path":None},"content","invalid_format"),
    ({"edit_reason":None},"edit_reason","required"),({"edit_reason":4},"edit_reason","invalid_type"),
    ({"edit_reason":"no"},"edit_reason","invalid_format")])
def test_content_input_contract(agent, change, field, reason):
    value = assert_failure(agent.post(payload(**change)),422,"invalid_content")
    assert errors(value) == [{"field":field,"reason":reason}]
    assert documents.get_by_id(D)["revision_no"] == 0


def test_missing_content_missing_revision_and_unknown_priority(agent):
    body = payload(); body.pop("content")
    cli = assert_failure(agent.post(body),422,"invalid_content")
    status, api = worker._inbox_register(agent.run, agent.raw, model(body))
    assert status == 422 and cli["error"] == api["error"]
    assert errors(cli) == [{"field":"content","reason":"required"}]
    body.pop("expected_revision")
    assert_failure(agent.post(body),422,"expected_revision_missing")
    body.update(actor="fake", flow_id="fake", force=True)
    value = assert_failure(agent.post(body),422,"invalid_request")
    assert [err["field"] for err in errors(value)] == ["actor","flow_id","force"]
    status, api = worker._inbox_register(agent.run,agent.raw,model(body))
    assert status == 422 and value["error"] == api["error"]


@pytest.mark.parametrize("field,value", [("action","new"),("project","wrong"),("group_name","flowgate.v02.9999"),("doc_id",F+".0009-N")])
def test_axes_before_unknown_and_payload(agent, field, value):
    body = payload(**{field:value,"flow_id":"fake","expected_revision":None})
    response = assert_failure(agent.post(body),403,"forbidden")
    assert response["error"]["params"] == {}
    assert response["error"]["details"]["reason"] == "context_binding_mismatch"
    assert field.replace("_name","").replace("_id","") in response["error"]["details"]["axes"]


@pytest.mark.parametrize("raw", ["null", "[]", "{"])
def test_json_object_errors_follow_auth_and_graph(agent, raw):
    response = agent.client.post("/flowgate/api/v1/inbox",headers={"Authorization":"Bearer "+agent.raw},content=raw)
    assert errors(assert_failure(response,422,"invalid_request")) == [{"field":"body","reason":"invalid_format"}]
    token_service.consume(agent.issued["token_id"],"flowgate",D,require_claim=True)
    response = agent.client.post("/flowgate/api/v1/inbox",headers={"Authorization":"Bearer "+agent.raw},content=raw)
    assert_failure(response,401,"unauthorized")


def test_attempt_and_stop_before_classification_and_params_empty(agent, monkeypatch):
    def inactive(*a, **kw):
        raise graph_source_hooks.GraphAttemptInactive(reason="run_stopping", attempt_id="private", operation="inbox_edit")
    monkeypatch.setattr(graph_source_hooks,"require_writable",inactive)
    value=assert_failure(agent.post(payload(expected_revision=None,flow_id="forged")),409,"graph_attempt_inactive")
    assert value["error"]["params"] == {}
    assert value["error"]["details"] == {"reason":"run_stopping", "attempt_id":"private", "operation":"inbox_edit"}
    status, api=worker._inbox_register(agent.run,agent.raw,{})
    assert status == 409 and api["error"] == value["error"]
    assert documents.get_by_id(D)["revision_no"] == 0


def test_permissions_precede_current_slot_and_no_current_information(agent,monkeypatch):
    old=target.resolve_current
    monkeypatch.setattr(target,"resolve_current",lambda *a:{**old(*a),"document":{**agent.env.doc,"doc_id":"private"}})
    monkeypatch.setattr(guards,"has_permission",lambda *a:False)
    value=assert_failure(agent.post(payload()),403,"permission_denied")
    assert value["error"]["params"] == {"flow_id":F,"required":"perm_document_update"}
    assert_failure(agent.post(payload(expected_revision=None)),422,"expected_revision_missing")
    monkeypatch.setattr(guards,"has_permission",lambda *a:True)
    assert_failure(agent.post(payload()),409,"slot_document_changed")


def test_no_document_and_unreadable_are_not_new_or_empty_stores(agent,monkeypatch):
    monkeypatch.setattr(target,"resolve_current",lambda *a:None)
    assert_failure(agent.post(payload()),404,"no_document")


def test_binding_ambiguous_and_unavailable_never_legacy(agent,monkeypatch):
    repository.insert(F,"other","instruction","N",D,True,0,"user")
    monkeypatch.setattr(target.settings,"card_node",lambda *a: {"node_key":a[1],"node_type":"document","doc_type":"N"})
    value=assert_failure(agent.post(payload()),422,"card_binding_missing")
    assert errors(value)[0]["validation"]["reason"] == "ambiguous_or_inconsistent_binding"
    monkeypatch.setattr(repository,"available",lambda:False)
    value=assert_failure(agent.post(payload()),422,"card_binding_missing")
    assert errors(value)[0]["validation"]["reason"] == "card_edit_unavailable"


def test_dry_run_only_counter_and_no_contacts(agent):
    flow_guard.layout_store.ensure_guard_row(F)
    before=business_state(agent.env)
    result=agent.post(payload(dry_run=True))
    assert result.status_code == 200, result.text
    assert result.json()["dry_run"] is True
    assert result.json()["would_register"]["expected_revision"] == 0
    assert business_state(agent.env) == before
    assert token_service.verify(agent.raw)["dry_run_count"] == 1
    assert not agent.env.events
    assert agent.post(payload()).status_code == 200


def test_consume_failure_retry_replays_no_revision_or_history(agent,monkeypatch):
    consume=token_service.consume
    attempts=[]
    def fail_once(*a,**kw):
        attempts.append(1)
        if len(attempts)==1: raise RuntimeError("consume unavailable")
        return consume(*a,**kw)
    monkeypatch.setattr(token_service,"consume",fail_once)
    assert_failure(agent.post(payload()),500,"internal_error")
    assert documents.get_by_id(D)["revision_no"] == 1
    before=agent.env.db._fetch_all("SELECT * FROM document_revisions")
    dry=agent.post(payload(dry_run=True))
    assert dry.status_code == 200 and dry.json()["dry_run"] is True
    value=agent.post(payload()).json()
    assert value["replayed"] is True and value["revision_no"] == 1
    assert value["change_summary"]["before"]["revision_no"] == 0
    assert agent.env.db._fetch_all("SELECT * FROM document_revisions") == before
    assert len(attempts)==2
    assert [kind for kind,_ in agent.env.events] == ["document","canvas","graph"]*2
    assert agent.env.db._fetch_one("SELECT COUNT(*) n FROM workflow_events WHERE event_type='doc_edited'")["n"] == 1


def test_changed_content_with_same_unconsumed_token_is_not_saved(agent,monkeypatch):
    monkeypatch.setattr(token_service,"consume",lambda **kw:False)
    assert_failure(agent.post(payload()),500,"internal_error")
    assert_failure(agent.post(payload(content=HEADER+"different\n")),409,"idempotency_key_reused")
    assert_failure(agent.post(payload(expected_revision=1)),409,"idempotency_key_reused")
    assert documents.get_by_id(D)["revision_no"] == 1


def test_noncard_expected_revision_forbidden_but_extra_coordinates_lenient(agent):
    repository.delete(F,"research","instruction","N")
    for value in (None,0,True,"1"):
        body=payload(expected_revision=value)
        cli=assert_failure(agent.post(body),422,"invalid_request")
        status, api=worker._inbox_register(agent.run,agent.raw,model(body))
        assert status==422 and api["error"]==cli["error"]
        assert errors(cli)==[{"field":"expected_revision","reason":"unknown_field"}]
    body=payload(flow_id="ignored",actor="ignored");body.pop("expected_revision")
    value=agent.post(body)
    assert value.status_code == 200,value.text
    assert "card_binding" not in value.json()
    assert [kind for kind,_ in agent.env.events] == ["document","graph"]


def test_port_registration_is_required_and_repeatable(agent):
    integration.register();integration.register()
    ports.unregister(ports.CARD_DOCUMENT_EDIT)
    response=agent.post(payload())
    assert response.status_code == 422 and response.json()["error"]["code"] == "card_binding_missing"
    assert documents.get_by_id(D)["revision_no"] == 0
    integration.register()


def test_descriptor_card_and_noncard_revision_exposure(agent):
    schema=api_server_tools.REGISTER_SCHEMAS["edit"]
    assert schema["required"]==["content","expected_revision","edit_reason"]
    assert list(worker._REGISTER_MODEL_FIELDS["edit"])==list(agent_adapter.MODEL_FIELDS)
    assert agent_adapter.edit_schema(agent.run,schema)==schema
    repository.delete(F,"research","instruction","N")
    noncard=agent_adapter.edit_schema(agent.run,schema)
    assert "expected_revision" not in noncard["properties"] and "expected_revision" not in noncard["required"]
    assert "expected_revision" in schema["properties"]


def test_worker_transport_and_binding_errors_normalized(agent,monkeypatch):
    monkeypatch.setattr(worker,"_post_inbox_json",lambda *a:(0,{"error":"timeout"}))
    status,value=worker._inbox_register(agent.run,agent.raw,model(payload()))
    assert status==500 and value["http_status"]==500 and value["error"]["code"]=="internal_error"
    assert set(value["error"]["params"])=={"trace_id"}
    agent.run["current_token_id"]="old-hop"
    status,value=worker._inbox_register(agent.run,agent.raw,{"flow_id":"bad"})
    assert status==403 and value["error"]["code"]=="forbidden" and value["error"]["params"]=={}
    assert value["error"]["details"]["reason"]=="token_not_current"


def test_doc_path_read_once_and_jail(agent,monkeypatch):
    scratch=token_service.verify(agent.raw)["scratch_dir"]
    from pathlib import Path
    path=Path(scratch)/"submission.md"; path.write_text(payload()["content"],encoding="utf-8")
    body=payload(doc_path=str(path));body.pop("content")
    original=agent_adapter.read_content
    def read_once(body,token):
        result=original(body,token);path.write_text("changed after read",encoding="utf-8");return result
    monkeypatch.setattr(agent_adapter,"read_content",read_once)
    value=agent.post(body)
    assert value.status_code==200,value.text
    assert agent.env.path.read_text()==payload()["content"]


def test_rejected_history_fresh_and_replay_does_not_write_it_again(agent,monkeypatch):
    from modules.flow_gate.workflow import pipeline_service as pipeline
    from modules.flow_gate.db import workflow_sequences
    history=[{"rejection_id":"rej-one","reason":"fix it","review_id":7}]
    documents.update(D,{"doc_review_status":"rejected","rejection_history":json.dumps(history)})
    monkeypatch.setattr(workflow_sequences,"get_item_by_result_doc_id",lambda doc:None)
    transitions=[]
    def transition(**kw):
        transitions.append(kw);documents.update(D,{"doc_review_status":"revised"})
    monkeypatch.setattr(pipeline,"transition_document_review",transition)
    consume=token_service.consume
    calls=[]
    def fail_once(**kw):
        calls.append(kw)
        if len(calls)==1:raise RuntimeError("consume")
        return consume(**kw)
    monkeypatch.setattr(token_service,"consume",fail_once)
    body=payload(edit_reason="rejected",rejection_response="addressed",rejection_id="rej-one",rejection_review_id=7)
    assert_failure(agent.post(body),500,"internal_error")
    saved=documents.get_by_id(D)["rejection_history"]
    assert json.loads(saved)[0]["response_revision_no"]==1
    assert json.loads(saved)[0]["ai_response"]=="addressed"
    result=agent.post(body)
    assert result.status_code==200,result.text
    assert result.json()["replayed"] and documents.get_by_id(D)["rejection_history"]==saved
    assert len(transitions)==1


def test_v1_full_and_fragment_reads_use_snapshot(agent,monkeypatch):
    from modules.flow_gate.api.v1 import document_routes
    app=FastAPI();app.include_router(document_routes.router,prefix="/flowgate")
    monkeypatch.setattr(document_routes,"verify_bearer",lambda req:{"_is_user_jwt":True,"issued_to":"user"})
    client=TestClient(app)
    response=client.get("/flowgate/api/v1/document/"+D)
    assert response.status_code==200,response.text
    assert response.json()["revision_no"]==0 and response.json()["content"]==agent.env.path.read_bytes().decode()
    result=agent.post(payload())
    assert result.status_code==200,result.text
    full=client.get("/flowgate/api/v1/document/"+D).json()
    assert full["revision_no"]==1 and full["content"]==payload()["content"]
    section=client.get("/flowgate/api/v1/document/"+D+"/section",params={"lines":"1-30"})
    assert section.status_code==200,section.text
    assert section.json()["revision_no"]==1

@pytest.mark.parametrize("path_value,reason", [(None,"required"),(4,"invalid_type"),("relative.md","invalid_format"),("","invalid_format")])
def test_doc_path_input_errors(agent,path_value,reason):
    body=payload(doc_path=path_value);body.pop("content")
    value=assert_failure(agent.post(body),422,"invalid_content")
    assert errors(value)==[{"field":"doc_path","reason":reason}]


def test_read_file_preserves_crlf_and_fingerprint(agent):
    import hashlib
    from pathlib import Path
    text=HEADER.replace("\n","\r\n")+"CRLF body\r\n"
    scratch=Path(token_service.verify(agent.raw)["scratch_dir"])
    file=scratch/"crlf.md";file.write_bytes(text.encode())
    body=payload(doc_path=str(file),body_sha256=hashlib.sha256(text.encode()).hexdigest(),body_chars=len(text));body.pop("content")
    result=agent.post(body)
    assert result.status_code==200,result.text
    assert agent.env.path.read_bytes()==text.encode()
    assert file.read_bytes()==text.encode()


def test_unreadable_body_rejects_without_recovery(agent):
    agent.env.path.unlink()
    result=assert_failure(agent.post(payload()),422,"not_editable")
    assert result["error"]["params"]["reason"]=="body_unreadable"
    assert not agent.env.path.exists() and documents.get_by_id(D)["revision_no"]==0


def test_noncard_late_link_is_reclassified_before_storage(agent,monkeypatch):
    from flowgate.card_document_edit import legacy_store
    repository.delete(F,"research","instruction","N")
    original=legacy_store.legacy_store
    def attach_before_guard(req):
        repository.insert(F,"research","instruction","N",D,True,0,"user")
        return original(req)
    monkeypatch.setattr(legacy_store,"legacy_store",attach_before_guard)
    body=payload();body.pop("expected_revision")
    assert_failure(agent.post(body),422,"card_binding_missing")
    assert documents.get_by_id(D)["revision_no"]==0
    assert not document_revisions.list_by_doc(D)


def test_mapped_node_never_searches_another_card(agent,monkeypatch):
    from modules.flow_gate.graph_workflow.store import run_store
    monkeypatch.setattr(run_store,"node_run_by_token",lambda _: {"run_id":"gr-one","node_key":"different","stage":"instruction"})
    monkeypatch.setattr(run_store,"run_by_id",lambda _: {"group_id":F})
    monkeypatch.setattr(run_store,"node_runs",lambda _: [])
    assert_failure(agent.post(payload()),422,"card_binding_missing")
    assert documents.get_by_id(D)["revision_no"]==0


def test_recorded_coordinate_is_fixed_even_when_slot_is_empty(agent,monkeypatch):
    from modules.flow_gate.graph_workflow.store import run_store
    token=token_service.verify(agent.raw)
    monkeypatch.setattr(run_store,"node_run_by_token",lambda _: {"run_id":"gr-one","node_key":"research","stage":"instruction"})
    monkeypatch.setattr(run_store,"run_by_id",lambda _: {"group_id":F})
    monkeypatch.setattr(run_store,"node_runs",lambda _: [{"node_key":"research","instruction_doc_id":D}])
    # Keep the recorded owner row, but no current link. Binding does not turn that into a current-document error.
    original_rows=repository.rows
    monkeypatch.setattr(repository,"rows",lambda *a,**kw:[{**row,"doc_id":None} for row in original_rows(*a,**kw)])
    value=assert_failure(agent.post(payload(expected_revision=None)),422,"expected_revision_missing")
    assert "revision_no" not in value["error"]["params"]
    monkeypatch.setattr(guards,"has_permission",lambda *a:False)
    assert_failure(agent.post(payload()),403,"permission_denied")
    monkeypatch.setattr(guards,"has_permission",lambda *a:True)
    assert_failure(agent.post(payload()),404,"no_document")


def test_ts_shared_scenario_is_one_instruction_coordinate(agent,monkeypatch):
    documents.update(D,{"type_code":"TS"})
    repository.delete(F,"research","instruction","N")
    repository.insert(F,"research","instruction","TS",D,True,0,"user")
    repository.insert(F,"research","scenario","TS",D,False,1,"user")
    monkeypatch.setattr(target.settings,"card_node",lambda *a:{**agent.env.node,"doc_type":"TS"})
    bound=agent_binding.bind(token_service.verify(agent.raw),documents.get_by_id(D))
    assert bound["slot"]=="instruction"


def test_supersede_binding_keeps_old_token_coordinate(agent):
    other=F+".0003-N"
    documents.create({"doc_id":other,"project_id":"flowgate","module":"v02","group_id":F,
        "type_code":"N","seq":3,"title":"replacement","status":"draft","owner_id":"user","file_path":agent.env.doc["file_path"]})
    documents.update(D,{"superseded_by":other})
    repository.rebind(F,"research","instruction","N",other)
    assert agent_binding.bind(token_service.verify(agent.raw),documents.get_by_id(D))["doc_id"]==D
    assert_failure(agent.post(payload(expected_revision=None)),422,"expected_revision_missing")
    assert_failure(agent.post(payload()),409,"slot_document_changed")


def test_store_event_failure_rolls_back_and_does_not_consume(agent,monkeypatch):
    from flowgate.card_document_edit import store as revision_store
    original=revision_store.workflow_events.create
    def broken(event):
        if event["event_type"]=="doc_edited":raise RuntimeError("event fail")
        return original(event)
    monkeypatch.setattr(revision_store.workflow_events,"create",broken)
    before=agent.env.path.read_bytes()
    assert_failure(agent.post(payload()),500,"internal_error")
    assert agent.env.path.read_bytes()==before and documents.get_by_id(D)["revision_no"]==0
    assert not document_revisions.list_by_doc(D)
    assert token_service.verify(agent.raw)["consumed_at"] is None


def test_stop_shared_hold_remains_through_contacts_and_consumption(agent,monkeypatch):
    states=[]
    def writable(token,operation,**kw):
        assert graph_source_hooks._session.stack is not None
        states.append(operation)
    monkeypatch.setattr(graph_source_hooks,"require_writable",writable)
    consume=token_service.consume
    def claim(**kw):
        assert graph_source_hooks._session.stack is not None
        assert flow_guard.held_guard() is None
        states.append("consume")
        return consume(**kw)
    monkeypatch.setattr(token_service,"consume",claim)
    assert agent.post(payload()).status_code==200
    assert states==["inbox_edit","inbox_edit","consume"]


def test_real_workplan_agent_and_replay_share_common_result(agent,monkeypatch):
    from modules.flow_gate.services import work_plan_service as wp, work_plan_apply_service as apply
    plan=wp.empty_recovery_body("flowgate")
    agent.env.path.write_bytes(wp.dumps(plan).encode())
    documents.update(D,{"type_code":"WP"})
    repository.delete(F,"research","instruction","N")
    repository.insert(F,"research","plan","WP",D,True,0,"user")
    monkeypatch.setattr(target.settings,"card_node",lambda *a:{**agent.env.node,"doc_type":"WP"})
    monkeypatch.setattr(apply,"instruction_mode_lock_errors",lambda *a:[])
    consume=token_service.consume
    monkeypatch.setattr(token_service,"consume",lambda **kw:False)
    body=payload(content=wp.dumps(plan))
    assert_failure(agent.post(body),500,"internal_error")
    monkeypatch.setattr(token_service,"consume",consume)
    result=agent.post(body)
    assert result.status_code==200,result.text
    value=result.json()
    assert value["replayed"] and value["doc_type"]=="WP" and value["body"]==plan
    assert value["card_binding"]["slot"]=="plan" and value["revision_no"]==1
    assert "title" in value and "change_summary" in value


def test_real_tr2_store_does_not_call_legacy_save_or_replace_expected(agent,monkeypatch):
    from modules.flow_gate.documents import tr2_service as tr2
    proposal={"edit_spec":{"edits":[],"deferred":[],"termination":"done","gate":{"commands":[]}}}
    documents.update(D,{"type_code":"TR2"})
    agent.env.path.write_bytes(b"{}")
    repository.delete(F,"research","instruction","N")
    repository.insert(F,"research","report","TR2",D,True,0,"user")
    monkeypatch.setattr(target.settings,"card_node",lambda *a:{**agent.env.node,"doc_type":"T2"})
    monkeypatch.setattr(tr2,"_assert_mutable",lambda d:None)
    monkeypatch.setattr(tr2,"validate",lambda data,**kw:data)
    monkeypatch.setattr(tr2,"verify_pair",lambda *a:None)
    monkeypatch.setattr(tr2,"canonicalize",lambda d:d)
    monkeypatch.setattr(tr2,"target_set",lambda *a:[])
    monkeypatch.setattr(tr2,"resolve_source_root",lambda *a:agent.env.root)
    monkeypatch.setattr(tr2,"target_fingerprint",lambda *a:"baseline")
    monkeypatch.setattr(tr2,"derived_files",lambda *a:[])
    monkeypatch.setattr(tr2,"spec_fingerprint",lambda *a:"spec")
    monkeypatch.setattr(tr2,"canonical_path_for_doc",lambda *a:agent.env.path.with_suffix(".json"))
    monkeypatch.setattr(tr2,"save",lambda *a,**kw:pytest.fail("old TR2 save must not be called"))
    body=payload(content=json.dumps(proposal),dry_run=True)
    assert agent.post(body).json()["dry_run"] is True
    assert not document_revisions.list_by_doc(D)
    body["dry_run"]=False;body["expected_revision"]=9
    assert_failure(agent.post(body),409,"revision_conflict")
    body["expected_revision"]=0
    result=agent.post(body)
    assert result.status_code==200,result.text
    value=result.json()
    assert value["revision_no"]==1 and value["card_binding"]["slot"]=="report" and value["doc_type"]=="TR2"
    assert value["line"] and value["updated_at"] and value["stored_path"]
    assert len(document_revisions.list_by_doc(D))==1 and document_revisions.list_by_doc(D)[0]["revision_no"]==1
    assert [k for k,_ in agent.env.events]==["document","canvas","graph"]


def test_worker_null_object_and_missing_registration_fail_closed(agent):
    status,value=worker._inbox_register(agent.run,agent.raw,None)
    assert status==422 and value["error"]["code"]=="invalid_request"
    assert errors(value)==[{"field":"body","reason":"invalid_format"}]
    ports.unregister(ports.CARD_DOCUMENT_EDIT)
    status,value=worker._inbox_register(agent.run,agent.raw,model(payload()))
    assert status==422 and value["error"]["code"]=="card_binding_missing"
    integration.register()


def test_snapshot_revision_is_printed_even_in_first_write_mention(agent):
    from modules.flow_gate.services import mention_service
    token=token_service.verify(agent.raw)
    text=mention_service.build_mention_from_token_rec(token,"N","in_progress",documents.get_by_id(D),
        "http://localhost/flowgate/api/v1",agent.raw,action_scope="edit",first_write=True)
    assert "doc_id="+D+", revision_no=0, content_state=readable" in text
    assert '"expected_revision": 0' in text

@pytest.mark.parametrize("field",["revoked_at","consumed_at","expires_at"])
def test_verify_state_precedes_input_errors(agent,field):
    value=(datetime.now(timezone.utc)-timedelta(days=1)).isoformat()
    agent.env.db._execute("UPDATE tokens SET "+field+" = ? WHERE token_id = ?",[value,agent.issued["token_id"]])
    cli=assert_failure(agent.post(payload(expected_revision=None,actor="forged")),401,"unauthorized")
    status,api=worker._inbox_register(agent.run,agent.raw,{})
    assert status==401 and api["error"]==cli["error"]


def test_rejected_own_registration_and_transition_resume_once(agent,monkeypatch):
    from modules.flow_gate.workflow import pipeline_service as pipeline
    from modules.flow_gate.db import workflow_sequences,workflow_item_results
    from modules.flow_gate.api.v1.events import publisher,event_types
    documents.update(D,{"doc_review_status":"rejected","rejection_history":json.dumps([
        {"rejection_id":"rej-own","reason":"fix it","review_id":7}])})
    monkeypatch.setattr(workflow_sequences,"get_item_by_result_doc_id",lambda doc:{"id":17,"result_doc_id":D})
    registrations=[];latest=[None];steps=[];published=[]
    monkeypatch.setattr(workflow_item_results,"get_latest_result_by_item",lambda item:latest[0])
    def register(**kw):
        assert kw["item_id"]==17 and kw["registered_doc_id"]==D
        registrations.append(kw);latest[0]={**kw,"status":"pending_approval"};steps.append("register")
    monkeypatch.setattr(pipeline,"register_workflow_result",register)
    def transition(**kw):
        steps.append("transition")
        if steps.count("transition")==1:raise RuntimeError("transition failed")
        documents.update(D,{"doc_review_status":"revised"})
    monkeypatch.setattr(pipeline,"transition_document_review",transition)
    monkeypatch.setattr(publisher,"broadcast_event_threadsafe",lambda ev:published.append(ev))
    body=payload(edit_reason="rejected",rejection_response="addressed",rejection_id="rej-own",rejection_review_id=7)
    assert_failure(agent.post(body),500,"internal_error")
    assert not agent.env.events and not token_service.verify(agent.raw)["consumed_at"]
    result=agent.post(body)
    assert result.status_code==200,result.text
    assert result.json()["line"]["doc_review_status"]=="revised"
    assert "_agent_review_status_before" not in result.json()
    assert len(registrations)==1 and steps==["register","transition","transition"]
    assert len([ev for ev in published if ev.event_type==event_types.EventType.DOC_REVIEW_STATUS_CHANGED])==1


def test_late_title_failure_and_summary_failure_remain_success(agent,monkeypatch):
    from modules.flow_gate.services import node_title_hooks,change_summary_service
    monkeypatch.setattr(node_title_hooks,"after_registration",lambda *a:(_ for _ in ()).throw(RuntimeError("hint")))
    monkeypatch.setattr(change_summary_service,"build_text",lambda **kw:(_ for _ in ()).throw(RuntimeError("summary")))
    result=agent.post(payload(node_title={"invalid":"lenient"}))
    assert result.status_code==200,result.text
    assert result.json()["change_summary"]=={"changed":None,"error":"summary unavailable"}


def test_agent_run_missing_from_current_server_is_forbidden(agent,monkeypatch):
    from modules.flow_gate.services import ai_invoke_service
    agent.env.db._execute("UPDATE tokens SET ai_run_id=? WHERE token_id=?",["missing-run",agent.issued["token_id"]])
    monkeypatch.setattr(ai_invoke_service,"get_run_record",lambda _:None)
    assert_failure(agent.post(payload(expected_revision=None)),403,"forbidden")


def test_summary_text_has_same_canonical_line_and_section_counts(agent):
    from modules.flow_gate.services import change_summary_service as summaries
    before=agent.env.path.parent/"before.md";after=agent.env.path.parent/"after.md"
    a=HEADER.replace("\n","\r\n")+"# Heading\r\nfirst\r\n"
    b=HEADER.replace("\n","\r\n")+"# Heading\r\nsecond\r\n"
    before.write_bytes(a.encode());after.write_bytes(b.encode())
    expected=summaries.build(doc_id=D,after_path=after,after_revision_no=1,before_path=before,before_revision_no=0)
    actual=summaries.build_text(doc_id=D,after_text=b,after_revision_no=1,before_text=a,before_revision_no=0)
    assert actual==expected

@pytest.mark.parametrize("kind",["WP","TR2"])
def test_noncard_type_errors_keep_legacy_status_and_envelope(agent,kind):
    repository.delete(F,"research","instruction","N")
    documents.update(D,{"type_code":kind})
    agent.env.path.write_bytes(b"{}")
    body=payload(content="not json");body.pop("expected_revision")
    value=agent.post(body)
    assert value.status_code==(400 if kind=="WP" else 422),value.text
    assert value.json()["ok"] is False and value.json()["http_status"]==value.status_code
    if kind == "TR2":
        assert value.json()["error_message"]==value.json()["error"]["message"]
    else:
        assert "error" not in value.json()
    assert documents.get_by_id(D)["revision_no"]==0


def test_noncard_conversation_migration_guard_before_identity(agent,monkeypatch):
    repository.delete(F,"research","instruction","N")
    documents.update(D,{"type_code":"CH"})
    monkeypatch.setattr(inbox,"_conversation_full_body_edit_fail",lambda doc:inbox._fail(409,"Use conversation turns"))
    body=payload(content="---\nproject: wrong\n---\nbad");body.pop("expected_revision")
    response=agent.post(body)
    assert response.status_code == 409
    value=response.json()
    assert "error" not in value
    assert value["error_message"]=="Use conversation turns"
    assert documents.get_by_id(D)["revision_no"]==0


def test_current_continuation_context_selects_edit_schema(agent):
    run={**agent.run,"action_scope":"new","register_context":{
        "action":"edit","project":"flowgate","group":F,"doc":D}}
    specs=api_server_tools.definitions_for_run(run)
    schema=next(s["schema"] for s in specs if s["name"]=="register_document")
    assert "expected_revision" in schema["required"]

@pytest.mark.parametrize("valid",[True,False])
def test_provider_loop_delivers_complete_edit_json_without_summary_truncation(agent,monkeypatch,valid):
    from modules.flow_gate.services import ai_invoke_service as invoke
    sent=[]
    run={**agent.run,"raw_token":agent.raw,"docs_target":1,"mode":"single","timed_out":False,
         "cancel_event":SimpleNamespace(is_set=lambda:bool(sent))}
    body=model(payload(expected_revision=0 if valid else None))
    monkeypatch.setattr(invoke.ai_settings_service,"get_provider_secret",lambda *a:"test-key")
    monkeypatch.setattr(invoke,"_remaining_sec",lambda run:30)
    monkeypatch.setattr(invoke,"_call_openai",lambda *a:(None,[{"id":"call-one","name":"register_document","input":body}],
        {"role":"assistant","content":None}))
    original=invoke._tool_result_msg
    def capture(kind,call,text):
        sent.append(json.loads(text))
        return original(kind,call,text)
    monkeypatch.setattr(invoke,"_tool_result_msg",capture)
    status,detail=invoke._api_execute({"id":"test-provider","kind":"openai","api_base_url":"https://example.invalid","api_model":"test"},"prompt",run)
    assert status=="started_ok" and detail is None
    assert len(sent)==1
    if valid:
        assert sent[0]["revision_no"]==1 and sent[0]["card_binding"]["doc_id"]==D
        assert "line" in sent[0] and "change_summary" in sent[0] and "previous_revision_path" in sent[0]
    else:
        assert sent[0]["http_status"]==422 and sent[0]["error"]["code"]=="expected_revision_missing"
        assert run["last_tool_status"]==422


def test_shared_slot_without_owner_record_is_binding_failure(agent,monkeypatch):
    documents.update(D,{"type_code":"TS"})
    repository.delete(F,"research","instruction","N")
    repository.insert(F,"research","scenario","TS",D,False,1,"user")
    monkeypatch.setattr(target.settings,"card_node",lambda *a:{**agent.env.node,"doc_type":"TS"})
    assert_failure(agent.post(payload(expected_revision=None)),422,"card_binding_missing")


@pytest.mark.parametrize("code", ["GROUP_AI_RUN_OWNER_MISMATCH", "GRAPH_EXECUTION_LEASE_LOST"])
def test_mutation_policy_preserves_code_and_params_cli_api(agent, monkeypatch, code):
    from modules.flow_gate.services import mutation_policy
    fields = {"group_id":F, "run_id":"lease-owner", "operation":"inbox_edit"}
    def denied(*args):
        raise mutation_policy.MutationPolicyError(403, code, "lease ownership rejected", **fields)
    monkeypatch.setattr(mutation_policy, "assert_group_mutation_allowed", denied)
    flow_guard.layout_store.ensure_guard_row(F)
    before = business_state(agent.env)
    cli = assert_failure(agent.post(payload()), 403, code)
    status, api = worker._inbox_register(agent.run, agent.raw, model(payload()))
    assert status == 403 and api["error"] == cli["error"]
    assert cli["error"]["params"] == fields
    assert business_state(agent.env) == before
    assert token_service.verify(agent.raw)["consumed_at"] is None


@pytest.mark.parametrize("status", [400, 409, 422])
def test_uncoded_legacy_failures_are_not_action_scope_failures(status):
    value = {"ok":False, "http_status":status, "error_message":"legacy guard", "help_url":"help"}
    assert agent_errors.normalize(status, value) == (status, value)


def test_only_action_scope_source_gets_action_validation(agent, monkeypatch):
    from modules.flow_gate.services import register_binding
    # The real _bind_register_context source marks only unsupported server scope.
    run = {**agent.run, "register_context":{"action":"chat", "project":"flowgate", "group":F, "doc":D},
           "_register_edit_context":True}
    monkeypatch.setattr(register_binding, "compare_axes", lambda *a: [])
    status, value = worker._inbox_register(run, agent.raw, {})
    assert status == 422 and value["error"]["code"] == "invalid_request"
    assert errors(value) == [{"field":"action", "reason":"invalid_format",
        "validation":{"reason":"action_scope_not_registerable"}}]


def legacy_body(**kwargs):
    body = payload(**kwargs)
    body.pop("expected_revision")
    return body


@pytest.mark.parametrize("reason,message", [(None,"Required field missing: edit_reason"),
    ("", "Required field missing: edit_reason"), ("bad", "edit_reason value is invalid"),
    (4, "edit_reason value is invalid")])
def test_noncard_reason_preserves_old_400_cli_api(agent, reason, message):
    repository.delete(F,"research","instruction","N")
    body = legacy_body(edit_reason=reason)
    response = agent.post(body)
    status, api = worker._inbox_register(agent.run, agent.raw, model(body))
    assert response.status_code == status == 400
    assert response.json() == api
    assert api["error_message"] == message and "error" not in api


def test_noncard_null_content_with_path_keeps_value_xor(agent):
    from pathlib import Path
    repository.delete(F,"research","instruction","N")
    path = Path(token_service.verify(agent.raw)["scratch_dir"])/"legacy.md"
    path.write_bytes(payload()["content"].encode())
    response = agent.post(legacy_body(content=None, doc_path=str(path)))
    assert response.status_code == 200, response.text
    assert agent.env.path.read_bytes() == payload()["content"].encode()


@pytest.mark.parametrize("guard", ["final", "status", "frontmatter", "scope", "size", "encoding", "rejection"])
def test_noncard_guards_keep_legacy_failure_contract(agent, monkeypatch, guard):
    repository.delete(F,"research","instruction","N")
    body = legacy_body()
    expected = None
    if guard in {"final", "status"}:
        monkeypatch.setattr(inbox.document_service,"is_final_approved",lambda *a:guard=="final")
        monkeypatch.setattr(inbox.document_service,"is_document_editable",lambda *a,**kw:False)
        expected=inbox._fail(422,"Modification not allowed after final approval." if guard=="final" else
            f"Modification not allowed for status: {documents.get_by_id(D)['status']}")
    elif guard=="frontmatter":
        expected=inbox._fail(422,"legacy frontmatter failure")
        monkeypatch.setattr(inbox,"_frontmatter_submission_guard",lambda *a,**kw:expected)
    elif guard=="scope":
        from modules.flow_gate.services import tr_scope_service
        monkeypatch.setattr(inbox.tool_registry,"MUTATING_STEP_TYPES",{"N"})
        monkeypatch.setattr(tr_scope_service,"evaluate",lambda **kw:{"verdict":tr_scope_service.VERDICT_REJECT,"notice":"scope rejected"})
        expected=inbox._fail(422,"scope rejected")
    elif guard=="size":
        monkeypatch.setattr(inbox,"_content_max",lambda:3)
        expected=inbox._fail(422,"content size exceeds the limit (max 3 bytes)")
    elif guard=="encoding":
        body["body_sha256"]="bad"
        expected=inbox._encoding_guard(fields={"body":body["content"],"edit_reason":body["edit_reason"]},
            fingerprint_field="body",body_sha256="bad",body_chars=None,force_encoding_reason=None)
    else:
        body.update(edit_reason="rejected",rejection_response="fixed",rejection_id="missing")
        expected=inbox._fail(409,"The submitted rejection target does not match the current rejection history. Refresh the document and retry.")
    flow_guard.layout_store.ensure_guard_row(F)
    before=business_state(agent.env)
    cli=agent.post(body)
    # API filters CLI-only operational keys; exercise CLI when testing fingerprint metadata.
    if guard != "encoding":
        status,api=worker._inbox_register(agent.run,agent.raw,model(body))
        assert status==expected.status_code and api==json.loads(expected.body)
    assert cli.status_code==expected.status_code,cli.text
    assert cli.json()==json.loads(expected.body)
    assert business_state(agent.env)==before


@pytest.mark.parametrize("card",[True,False])
def test_linked_document_keeps_existing_existence_only_contract(agent,monkeypatch,card):
    if not card:
        repository.delete(F,"research","instruction","N")
    original=inbox.db_docs.get_by_id
    monkeypatch.setattr(inbox.db_docs,"get_by_id",lambda doc: {"doc_id":"external","project_id":"other","group_id":"other"}
        if doc=="external" else original(doc))
    # Validate/build directly: the fake reference is deliberately outside this test DB's FK set.
    from flowgate.card_document_edit import agent_rules
    req,_,_=agent_adapter.prepare(agent.raw,payload(linked_doc_id="external") if card else legacy_body(linked_doc_id="external"))
    if not card:
        req=replace(req,transport_extras={**req.transport_extras,"legacy":True})
    rule=agent_rules.AgentRule(req,None)
    body=SimpleNamespace(path=agent.env.path,text=agent.env.path.read_text(),data=agent.env.path.read_bytes())
    assert rule.validate_and_build(rule.prepare(req.content,agent.env.doc,body),agent.env.doc,body).stored_bytes


def test_missing_port_preserves_verify_axes_graph_object_order(agent,monkeypatch):
    ports.unregister(ports.CARD_DOCUMENT_EDIT)
    try:
        assert_failure(agent.client.post("/flowgate/api/v1/inbox",json=payload()),401,"unauthorized")
        assert_failure(agent.post(payload(project="wrong")),403,"forbidden")
        def inactive(*a,**kw):
            raise graph_source_hooks.GraphAttemptInactive(reason="run_stopping",attempt_id="old",operation="inbox_edit")
        monkeypatch.setattr(graph_source_hooks,"require_writable",inactive)
        cli=assert_failure(agent.post(payload()),409,"graph_attempt_inactive")
        status,api=worker._inbox_register(agent.run,agent.raw,{})
        assert status==409 and api["error"]==cli["error"]
        assert cli["error"]["details"]=={"reason":"run_stopping","attempt_id":"old","operation":"inbox_edit"}
        monkeypatch.setattr(graph_source_hooks,"require_writable",lambda *a,**kw:None)
        assert_failure(agent.post(payload()),422,"card_binding_missing")
        status,api=worker._inbox_register(agent.run,agent.raw,{})
        assert status==422 and api["error"]["code"]=="card_binding_missing"
        response=agent.client.post("/flowgate/api/v1/inbox",headers={"Authorization":"Bearer "+agent.raw},content="[]")
        assert_failure(response,422,"invalid_request")
        assert documents.get_by_id(D)["revision_no"]==0
    finally:
        integration.register()


def test_noncard_empty_body_keeps_legacy_contract(agent):
    repository.delete(F,"research","instruction","N")
    response=agent.post(legacy_body(content=""))
    assert response.status_code==200,response.text
    assert agent.env.path.read_bytes()==b""
    assert documents.get_by_id(D)["revision_no"]==1


def test_noncard_new_rejection_alias_stays_an_ignored_extra(agent, monkeypatch):
    from modules.flow_gate.workflow import pipeline_service as pipeline
    from modules.flow_gate.db import workflow_sequences
    repository.delete(F,"research","instruction","N")
    documents.update(D,{"doc_review_status":"rejected","rejection_history":json.dumps([
        {"rejection_id":"old","review_id":1,"reason":"old"},
        {"rejection_id":"latest","review_id":2,"reason":"latest"}])})
    monkeypatch.setattr(workflow_sequences,"get_item_by_result_doc_id",lambda doc:None)
    monkeypatch.setattr(pipeline,"transition_document_review",lambda **kw:documents.update(D,{"doc_review_status":"revised"}))
    response=agent.post(legacy_body(edit_reason="rejected",rejection_response="legacy latest response",
        rejection_review_id=None))
    assert response.status_code==200,response.text
    history=json.loads(documents.get_by_id(D)["rejection_history"])
    assert not history[0].get("ai_response")
    assert history[1]["ai_response"]=="legacy latest response"
