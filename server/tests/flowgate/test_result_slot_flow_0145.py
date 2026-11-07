"""0145 T#1: isolated confirmed Flow, real dispatch, inbox and Graph result contact."""
import json
import pytest

from test_fg_ai_roles_l2_assignment import (env as graph_env, db_path, decided, wired, flow, roles, start, line_doc, node_run, FA, X, B)
from modules.flow_gate.api import inbox_routes as inbox
from modules.flow_gate.db import documents, tokens
from modules.flow_gate.graph_workflow.services import flow_guard, result_slot_binding
from modules.flow_gate.graph_workflow.store import run_store
from modules.flow_gate.services import token_service
from flowgate.card_document_edit import integration, guards


@pytest.fixture
def env(db_path, monkeypatch, tmp_path, request):
    from modules.flow_gate.graph_workflow.services import card_document_ports
    monkeypatch.setattr(card_document_ports, "_handlers", dict(card_document_ports._handlers))
    import os
    monkeypatch.setenv("FLOWGATE_AGENT_PEPPER_ID", os.environ.get("FLOWGATE_AGENT_PEPPER_ID", "primary"))
    integration.register()
    yield from graph_env.__wrapped__(db_path, monkeypatch, tmp_path, request)


def test_confirmed_flow_first_result_edit_binds_immediately_and_rewrite_stays_same_id(env,decided,wired,monkeypatch):
    integration.register()
    monkeypatch.setattr(guards,"has_permission",lambda *a:True)
    env["client"].app.include_router(inbox.router, prefix="/flowgate")
    key=flow(env,doc_type="N",extra_settings=(("instruction","omit"),));roles(key,"not_used",X,B)
    start(env)
    job=next(j for j in env["jobs"] if j["node_key"]==key)
    report=line_doc(env,key,"report")
    assert job["action_scope"]=="edit" and job["target_doc_id"]==report and job["doc_ref"]==report
    token=token_service.verify(job["token"]["raw_token"])
    assert token["action_scope"]=="edit" and token["doc_ref"]==report
    fixed=result_slot_binding.issued_binding(token)
    assert fixed["attempt_id"]==run_store.active_attempt_id(job["run_id"],key)
    from flowgate.card_document_edit.body import read_body_bytes
    header = read_body_bytes(documents.get_by_id(report)).text
    before=documents.get_documents_by_group_id(FA)
    body={"action":"edit","project":token["project"],"module":"v02","group_name":FA,"doc_id":report,
          "expected_revision":0,"edit_reason":"worker_self","content":header+"# Isolated first result\n\nEvidence for card " + key}
    response=env["client"].post("/flowgate/api/v1/inbox",headers={"Authorization":"Bearer "+job["token"]["raw_token"]},json=body)
    assert response.status_code==200,response.text
    assert response.json()["doc_id"]==report and response.json()["revision_no"]==1
    assert node_run(env,key)["result_doc_id"]==report
    assert line_doc(env,key,"report")==report
    assert len(documents.get_documents_by_group_id(FA))==len(before)
    issued=token_service.issue(token["project"],FA,"edit",report,token["issued_to"])
    flow_guard.with_flow_guard(FA,lambda:run_store.map_token(issued["token_id"],job["run_id"],key,"result",map_kind=run_store.MAP_ATTEMPT))
    body.update(expected_revision=1,content=header+"# Isolated revised result\n\nCorrected evidence for "+key)
    revised=env["client"].post("/flowgate/api/v1/inbox",headers={"Authorization":"Bearer "+issued["raw_token"]},json=body)
    assert revised.status_code==200,revised.text
    assert revised.json()["doc_id"]==report and revised.json()["revision_no"]==2
    assert node_run(env,key)["result_doc_id"]==report and line_doc(env,key,"report")==report
    assert len(documents.get_documents_by_group_id(FA))==len(before)


def test_ai_start_mints_edit_token_and_mention_for_bound_result_slot(env,decided,wired,monkeypatch):
    from test_fg_instruction_stage_0056 import _record_start
    key=flow(env,doc_type="N",extra_settings=(("instruction","omit"),));roles(key,"not_used",X,B)
    start(env)
    job=next(j for j in env["jobs"] if j["node_key"]==key)
    mention,started,issued=_record_start(monkeypatch,job,"isolated-ai-slot")
    report=line_doc(env,key,"report")
    token=token_service.verify(issued["raw_token"])
    assert started["action_scope"]=="edit" and started["doc_ref"]==report
    assert token["action_scope"]=="edit" and token["doc_ref"]==report
    assert mention["action_scope"]=="edit" and mention["first_write"] is True
    assert mention["parent_doc"]["doc_id"]==report
    assert result_slot_binding.issued_binding(token)==job["result_slot_binding"]
