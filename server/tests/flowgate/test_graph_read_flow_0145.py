"""0145 T#1 -> T#2 -> T#3, real SQLite/migrations/inbox/Graph; external AI is a recorder."""
import copy
import hashlib
import json
import sqlite3
import pytest

from test_result_slot_flow_0145 import env, db_path, decided, wired
from test_fg_ai_roles_l2_assignment import FA, X, B, flow, roles, start, line_doc, node_run, one, starts, _sql
from test_fg_card_settings import _h, _doc, node, edge
from test_fg_card_settings_review import _run_ended, _executions_ended
from test_fg_command_review_0125 import completions, fork_join, _pass
from test_fg_revision_adoption_precheck_0010 import document_write_ports
from test_latest_definition_0145 import draft_body, put
from modules.flow_gate.db import documents, groups
from modules.flow_gate.services import token_service, auth_outbound
from modules.flow_gate.graph_workflow.services.flow_guard import with_flow_guard
from modules.flow_gate.graph_workflow.store import run_store, definition_store
from flowgate.card_document_edit import chat_authz, guards
from flowgate.graph_read import authz, routes
from flowgate.graph_run import execution_store

pytestmark = pytest.mark.usefixtures("fork_join", "completions", "document_write_ports")
FOREIGN = "flowgate.v02.9898"


def digest(env):
    # Digest only: assertion output must never contain token hashes or execution lease secrets.
    with sqlite3.connect(env["db"]) as conn:
        return hashlib.sha256("\n".join(conn.iterdump()).encode()).hexdigest()


def headers(issue):
    return {"Authorization": "Bearer " + issue["raw_token"]}


def get(env, issue, resource="canvas", flow_id=FA, query=""):
    return env["client"].get(f"/api/v2/ai/flows/{flow_id}/{resource}" + query, headers=headers(issue))


def ok(response):
    assert response.status_code == 200, response.text
    return response.json()


@pytest.fixture
def active(env, decided, wired, monkeypatch, starts):
    from modules.flow_gate.graph_workflow.api import envelope
    from modules.flow_gate.api import inbox_routes
    env["client"].app.include_router(routes.router)
    env["client"].app.include_router(inbox_routes.router, prefix="/flowgate")
    original_verify = envelope.verify_bearer
    monkeypatch.setattr(envelope, "verify_bearer", lambda req: original_verify(req)
                        if req.headers.get("Authorization", "").startswith("Bearer user:")
                        else auth_outbound.verify_bearer(req))
    monkeypatch.setattr(guards, "has_permission", lambda *a: True)
    from modules.flow_gate.rbac import permission_service
    monkeypatch.setattr(permission_service, "_cache", {})
    _sql(env["db"], "INSERT INTO roles(role_id,role_name,created_at,updated_at) VALUES (\'read0145\',\'read0145\',\'t\',\'t\')")
    _sql(env["db"], "INSERT OR IGNORE INTO permissions(permission_id,permission_name,created_at) VALUES (\'perm_document_read\',\'perm_document_read\',\'t\')")
    _sql(env["db"], "INSERT INTO role_permissions(role_id,permission_id) VALUES (\'read0145\',\'perm_document_read\')")
    _sql(env["db"], "INSERT INTO user_project_roles(user_id,project_id,role_id,granted_at) VALUES (\'u_kim\',\'flowgate\',\'read0145\',\'t\')")
    key = flow(env, doc_type="N", extra_settings=(("instruction","omit"),("review_count",1)))
    roles(key, "not_used", X, B)
    graph = start(env)
    writer = one(env, key, "result")
    report = line_doc(env, key, "report")
    worker = token_service.issue("flowgate", FA, "edit", report, "u_kim", provider_id=X, ai_run_id="ai-read-worker")
    with_flow_guard(FA, lambda: run_store.map_token(worker["token_id"], graph["run_id"], key, "result",
                                                   map_kind=run_store.MAP_AI_START))
    execution_store.acquire(writer["intent_exec_id"], "ai-read-worker")
    chat_doc = _doc(FA,80,"CH")
    chat = token_service.issue("flowgate", FA, "chat", chat_doc, "u_kim", ai_run_id="ai-read-chat")
    live = {}
    for issue, kind, doc_id, run_id in ((worker,"edit",report,"ai-read-worker"),(chat,"chat",chat_doc,"ai-read-chat")):
        live[run_id] = dict(run_id=run_id, project_id="flowgate", group_id=FA, issued_to="u_kim",
                           action_scope=kind, mode="single", module="v02", doc_ref=doc_id,
                           current_token_id=issue["token_id"], status="running",
                           graph_exec_id=writer["intent_exec_id"] if kind=="edit" else None)
    from modules.flow_gate.services import ai_invoke_service
    monkeypatch.setattr(ai_invoke_service, "get_run_record", lambda run_id: live.get(run_id))
    groups.create(dict(group_id=FOREIGN,project_id="flowgate",module="v02",title="FOREIGN_MARKER",status="OPEN"))
    foreign_doc = _doc(FOREIGN,1,"R")
    documents.update(foreign_doc, {"title":"FOREIGN_TITLE_MARKER"})
    documents.update(FA+".0001-R", {"triggered_by":foreign_doc})
    return env,key,graph,writer,worker,chat,live


def test_reads_are_observational_and_isolate_worker_and_foreign_relations(active, monkeypatch):
    env,key,graph,writer,worker,chat,live = active
    from modules.flow_gate.graph_workflow.services import canvas_service, run_service, result_binder, card_settings_ports
    called = []
    for module, name in ((canvas_service,"ensure_identity"),(result_binder,"reconcile_if_needed"),
                         (run_service,"settle_if_needed"),(card_settings_ports,"reconcile_stage")):
        monkeypatch.setattr(module, name, lambda *a, name=name: called.append(name))
    before = digest(env)
    events = len(env["events"])
    chat_canvas = ok(get(env,chat))
    worker_canvas = ok(get(env,worker))
    chat_current = ok(get(env,chat,"runs/current"))
    worker_current = ok(get(env,worker,"runs/current"))
    assert len(chat_canvas["nodes"]) >= 2
    assert [n["node_key"] for n in worker_canvas["nodes"]] == [key]
    assert [n["node_key"] for n in worker_current["run"]["node_runs"]] == [key]
    assert worker_current["scope"]["attempt_id"] == writer["attempt_id"]
    assert worker_canvas["graph"] is None and worker_canvas["edges"] == []
    root = next(n["node_key"] for n in chat_canvas["nodes"] if n["node_key"] != key)
    assert root not in json.dumps(worker_current)
    for body in (chat_canvas, worker_canvas, chat_current, worker_current):
        text = json.dumps(body)
        assert FOREIGN not in text and "FOREIGN_TITLE_MARKER" not in text
        assert all(s not in text for s in ("raw_token","lease_token","lease_expires_at","token_hash","stage_assignments","dispatch"))
        assert body["capabilities"] == {"read":True,"write":False,"execute":False}
    assert called == [] and len(env["events"]) == events and digest(env) == before


def test_real_tokens_v1_contract_and_all_refusals_preserve_database(active):
    env,key,graph,writer,worker,chat,live = active
    before = digest(env)
    for issue in (chat,worker):
        for resource in ("canvas","runs/current"):
            response = env["client"].get(f"/api/v1/flows/{FA}/{resource}",headers=headers(issue))
            assert response.status_code == 403 and response.json()["error"]["code"] == "token_kind_not_allowed"
            for target in (FOREIGN,"different.v02.9999","flowgate.v02.9999"):
                response = get(env,issue,resource,target)
                assert response.status_code == 403 and response.json()["error"]["code"] == "flow_access_denied"
        for method, path in (("put","canvas/layout"),("put","graph/draft"),("post","runs")):
            response = getattr(env["client"],method)(f"/api/v1/flows/{FA}/{path}",json={},headers=headers(issue))
            assert response.status_code == 403
        for method in ("post","put","patch","delete"):
            response = getattr(env["client"],method)(f"/api/v2/ai/flows/{FA}/canvas",headers=headers(issue))
            assert response.status_code == 405
    assert digest(env) == before
    # Existing user read paths still succeed.
    for resource in ("canvas","runs/current"):
        assert env["client"].get(f"/api/v1/flows/{FA}/{resource}",headers=_h()).status_code == 200


def test_real_worker_fences_and_revocation(active):
    env,key,graph,writer,worker,chat,live = active
    changes = [
        ("UPDATE graph_run_token_map SET attempt_id=NULL WHERE token_id=?", (worker["token_id"],),
         "UPDATE graph_run_token_map SET attempt_id=? WHERE token_id=?", (writer["attempt_id"],worker["token_id"]), 403),
        ("UPDATE graph_attempt_runs SET cancel_requested_at='x' WHERE exec_id=?", (writer["intent_exec_id"],),
         "UPDATE graph_attempt_runs SET cancel_requested_at=NULL WHERE exec_id=?", (writer["intent_exec_id"],), 409),
        ("UPDATE graph_node_attempts SET status='invalidated',active_node_key=NULL WHERE attempt_id=?", (writer["attempt_id"],),
         "UPDATE graph_node_attempts SET status='active',active_node_key=? WHERE attempt_id=?", (key,writer["attempt_id"]), 409)]
    for sql,args,restore,values,status in changes:
        _sql(env["db"],sql,args)
        before=digest(env)
        assert get(env,worker).status_code == status
        assert digest(env)==before
        _sql(env["db"],restore,values)
    from modules.flow_gate.rbac import permission_service
    _sql(env["db"],"DELETE FROM user_project_roles WHERE user_id=\'u_kim\' AND role_id=\'read0145\'")
    permission_service.invalidate_cache("u_kim","flowgate")
    before=digest(env)
    assert get(env,chat).status_code==get(env,worker).status_code==403
    assert digest(env)==before
    _sql(env["db"],"INSERT INTO user_project_roles(user_id,project_id,role_id,granted_at) VALUES (\'u_kim\',\'flowgate\',\'read0145\',\'t\')")
    permission_service.invalidate_cache("u_kim","flowgate")
    live["ai-read-chat"]["status"]="cancelling"
    assert get(env,chat).status_code == 409
    live["ai-read-chat"]["status"]="finished"
    assert get(env,chat).status_code == 409
    live["ai-read-chat"]["status"]="running"
    token_service.revoke(chat["token_id"],reason="test")
    before=digest(env)
    assert get(env,chat).status_code == 401
    assert digest(env)==before


def test_result_save_review_completion_latest_card_then_ai_status(active, monkeypatch, starts):
    env,key,graph,writer,worker,chat,live = active
    foreign_before = _sql(env["db"],"SELECT * FROM documents WHERE group_id=?",(FOREIGN,))
    worker_before = ok(get(env,worker,"runs/current"))
    assert worker_before["run"]["node_runs"][0]["status"] == "open"
    report = line_doc(env,key,"report")
    from flowgate.card_document_edit.body import read_body_bytes
    header = read_body_bytes(documents.get_by_id(report)).text
    response = env["client"].post("/flowgate/api/v1/inbox", headers=headers(worker), json={
        "action":"edit","project":"flowgate","module":"v02","group_name":FA,"doc_id":report,
        "expected_revision":0,"edit_reason":"worker_self","content":header+"# Isolated result\n\nReviewed evidence.\n"})
    saved=ok(response)
    assert saved["doc_id"]==report and saved["revision_no"]==1
    assert node_run(env,key)["result_doc_id"]==line_doc(env,key,"report")==report
    assert get(env,worker).status_code==401  # consumption never grants subsequent reads
    _executions_ended()
    live["ai-read-worker"]["status"]="finished"
    _run_ended("ai-read-worker", action_scope="edit",doc_ref=report,provider_id=X,graph_exec_id=writer["intent_exec_id"])
    rr=one(env,key,"result_review",1)
    assert rr is not None and rr["verdict"] is None
    assert _sql(env["db"],"SELECT * FROM graph_card_completions")==[]
    state=ok(get(env,chat,"runs/current"))
    assert next(n for n in state["run"]["node_runs"] if n["node_key"]==key)["status"]=="review"
    _pass(env,key,1,report,"ai-read-review")
    completion=_sql(env["db"],"SELECT * FROM graph_card_completions WHERE node_key=?",(key,))[0]
    assert completion["status"]=="applied"
    assert node_run(env,key)["status"]=="done"
    assert run_store.active_attempt_id(graph["run_id"],key) is None
    refreshed=ok(get(env,chat))
    assert next(n for n in refreshed["nodes"] if n["node_key"]==key)["node_run"]["status"]=="done"
    # Latest definition can gain a new card without rewriting the completed execution.
    old_run=copy.deepcopy(run_store.run_by_id(graph["run_id"]))
    body=draft_body(definition_store.revision(FA,1))
    body["nodes"].append(node("tmp_after_completion","T"))
    root=next(n["node_key"] for n in body["nodes"] if n.get("doc_type")=="R")
    body["edges"].append(edge("tmp_new_edge",root,"tmp_after_completion"))
    added=ok(put(env,body))["key_map"]["tmp_after_completion"]
    response=env["client"].post(f"/api/v1/flows/{FA}/graph/revisions",
        json={"draft_version":1,"base_revision_no":1},headers=_h())
    assert response.status_code==201,response.text
    before=digest(env)
    latest=ok(get(env,chat))
    current=ok(get(env,chat,"runs/current"))
    assert latest["view"]["revision_no"]==2 and added in {n["node_key"] for n in latest["nodes"]}
    assert current["run"]["revision_no"]==1
    assert next(n for n in current["run"]["node_runs"] if n["node_key"]==key)["status"]=="done"
    assert run_store.run_by_id(graph["run_id"])==old_run
    assert _sql(env["db"],"SELECT * FROM graph_card_completions WHERE node_key=?",(key,))[0]==completion
    assert _sql(env["db"],"SELECT * FROM documents WHERE group_id=?",(FOREIGN,))==foreign_before
    assert digest(env)==before


@pytest.mark.parametrize("case", ["cancelled", "foreign_execution", "ordinary_edit", "stale_attempt"])
def test_result_edit_cancelled_or_unrelated_end_never_completes(active, case):
    env,key,graph,writer,worker,chat,live = active
    report=line_doc(env,key,"report")
    from flowgate.card_document_edit.body import read_body_bytes
    header=read_body_bytes(documents.get_by_id(report)).text
    saved=env["client"].post("/flowgate/api/v1/inbox",headers=headers(worker),json={
        "action":"edit","project":"flowgate","module":"v02","group_name":FA,"doc_id":report,
        "expected_revision":0,"edit_reason":"worker_self","content":header+"# Result\n\nPending review.\n"})
    assert saved.status_code==200,saved.text
    _executions_ended()
    ending=dict(action_scope="edit",doc_ref=report,provider_id=X,graph_exec_id=writer["intent_exec_id"])
    if case=="cancelled":
        ending.update(end_reason="cancelled",stop_code="cancelled")
    elif case=="foreign_execution":
        ending["graph_exec_id"]="unrelated-execution"
    elif case=="ordinary_edit":
        ending["graph_exec_id"]=None
    else:
        _sql(env["db"],"UPDATE graph_node_attempts SET status='invalidated',active_node_key=NULL WHERE attempt_id=?",
             (writer["attempt_id"],))
    _run_ended("ai-read-worker",**ending)
    assert one(env,key,"result_review",1) is None
    assert _sql(env["db"],"SELECT * FROM graph_card_completions")==[]
    assert node_run(env,key)["status"]=="review"
    assert documents.get_by_id(report)["revision_no"]==1
