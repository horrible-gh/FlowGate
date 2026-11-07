"""0145 T#1 self-checks: isolated migrated DB/files and real inbox/common revision path.

Only display-card definitions, authorization and notification endpoints are mocked.
No production flow or historical example document is loaded or changed.
"""
import json
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest
from test_card_document_edit_0127 import env, F, D, count
from test_card_document_edit_agent_0127 import agent
from modules.flow_gate.db import documents, document_revisions, tokens
from modules.flow_gate.graph_workflow.domain.graph_model import Graph, GraphNode
from modules.flow_gate.graph_workflow.store import run_store
from modules.flow_gate.graph_workflow.services import node_dispatch, result_slot_binding as binding, flow_guard
from modules.flow_gate.services import token_service
from modules.flow_gate.storage import paths
from flowgate.card_documents import repository
from flowgate.card_document_edit import target
from flowgate.card_document_edit.errors import GraphApiError


@pytest.fixture
def result_env(agent, monkeypatch):
    e = agent.env
    nodes = {key: {"node_key": key, "node_type": "document", "doc_type": "N"}
             for key in ("research", "second", "third", "fourth")}
    monkeypatch.setattr(target.settings, "card_node", lambda f,k: nodes.get(k) if f == F else None)
    graph = Graph(tuple(GraphNode(k,"document","N",k) for k in nodes))
    monkeypatch.setattr(run_store, "run_scope", lambda g: SimpleNamespace(targets=set(g.node_keys())))
    from modules.flow_gate.graph_workflow.store import definition_store
    definition_store.create_draft(F, None, "user")
    definition_store.replace_draft_content(F, [{**v, "label": k} for k,v in nodes.items()], [])
    definition_store.insert_revision_from_draft(F, 1, 1, None, "user")
    run_store.create_run("isolated-result", F, 1, "user",
        {"source":"system_default","set_id":"isolated","set_version":1,"digest":"0"*64,"entries":{}}, graph)
    reports = {}
    for i,key in enumerate(nodes, 3):
        e.db._execute("UPDATE graph_node_runs SET status = 'open', stage = 'result' WHERE run_id = ? AND node_key = ?",
                      ["isolated-result",key])
        run_store.open_attempt("isolated-result",key,1)
        doc_id = F + f".{i:04d}-NR"
        header = f"---\nproject: flowgate\nmodule: v02\ngroup: 0127\ntype: NR\ndoc_number: {i:04d}-NR\n---\n"
        path = e.root / "storage" / "documents" / f"report-{i}.md"
        path.write_text(header, encoding="utf-8")
        documents.create({"doc_id":doc_id,"project_id":"flowgate","module":"v02","group_id":F,
            "type_code":"NR","seq":i,"title":key,"status":"draft","owner_id":"user",
            "file_path":paths.to_storage_relative(path,"flowgate")})
        repository.insert(F,key,"report","NR",doc_id,True,1,"user")
        reports[key] = SimpleNamespace(doc_id=doc_id,header=header,path=path)
    def issue(key="research", scope="edit", mapped=True):
        doc = reports[key]
        issued = token_service.issue("flowgate",F,scope,doc.doc_id if scope=="edit" else D,"user")
        if mapped:
            flow_guard.with_flow_guard(F, lambda: run_store.map_token(issued["token_id"],"isolated-result",key,
                "result",map_kind=run_store.MAP_AI_START))
        return issued
    def payload(key="research",rev=0,**extra):
        doc=reports[key]
        return {"action":"edit","project":"flowgate","module":"v02","group_name":F,"doc_id":doc.doc_id,
            "expected_revision":rev,"edit_reason":"worker_self","content":doc.header+key+f" body revision {rev+1}\n",**extra}
    def post(issued,body):
        return agent.client.post("/flowgate/api/v1/inbox",headers={"Authorization":"Bearer "+issued["raw_token"]},json=body)
    return SimpleNamespace(agent=agent,env=e,nodes=nodes,reports=reports,issue=issue,payload=payload,post=post)


def test_first_write_reload_and_revision_same_slot_no_allocation(result_env):
    e=result_env;issued=e.issue();before=count(e.env,"documents")
    response=e.post(issued,e.payload())
    assert response.status_code==200,response.text
    value=response.json();doc=e.reports["research"]
    assert value["doc_id"]==doc.doc_id and value["revision_no"]==1
    assert value["card_binding"]["slot"]=="report" and value["line"]["doc_id"]==doc.doc_id
    assert value["change_summary"]["after"]["revision_no"]==1
    assert count(e.env,"documents")==before
    for _ in range(2):  # fresh DB/projection reads (browser F5 itself is a separate, unrun check)
        assert documents.get_by_id(doc.doc_id)["revision_no"]==1
        assert target.line_projection(F,e.nodes["research"],target.check_card({"group_id":F},"research","report")[1])["doc_id"]==doc.doc_id
        assert doc.path.read_text(encoding="utf-8")==e.payload()["content"]
    assert len(document_revisions.list_by_doc(doc.doc_id))==1


def test_rewrite_and_rejected_resubmit_keep_id_and_history(result_env,monkeypatch):
    from modules.flow_gate.workflow import pipeline_service as pipeline
    from modules.flow_gate.db import workflow_sequences
    e=result_env;doc=e.reports["research"]
    assert e.post(e.issue(),e.payload()).status_code==200
    assert e.post(e.issue(),e.payload(rev=1)).status_code==200
    history=[{"rejection_id":"isolated-rejection","review_id":7,"reason":"revise result"}]
    documents.update(doc.doc_id,{"doc_review_status":"rejected","rejection_history":json.dumps(history)})
    monkeypatch.setattr(workflow_sequences,"get_item_by_result_doc_id",lambda d:None)
    transitions=[]
    def transition(**kw):
        transitions.append(kw);documents.update(doc.doc_id,{"doc_review_status":"revised"})
    monkeypatch.setattr(pipeline,"transition_document_review",transition)
    value=e.post(e.issue(),e.payload(rev=2,edit_reason="rejected",rejection_response="corrected",
        rejection_id="isolated-rejection",rejection_review_id=7))
    assert value.status_code==200,value.text
    saved=documents.get_by_id(doc.doc_id)
    assert saved["revision_no"]==3 and value.json()["doc_id"]==doc.doc_id
    assert json.loads(saved["rejection_history"])[0]["reason"]==history[0]["reason"]
    assert json.loads(saved["rejection_history"])[0]["response_revision_no"]==3
    assert len(document_revisions.list_by_doc(doc.doc_id))==3 and len(transitions)==1


def test_stale_token_cannot_refresh_issued_revision(result_env):
    e=result_env;first=e.issue();second=e.issue()
    assert e.post(first,e.payload()).status_code==200
    stale=e.post(second,e.payload());assert stale.status_code==409,stale.text
    refresh=e.post(second,e.payload(rev=1));assert refresh.status_code==409,refresh.text
    assert refresh.json()["error"]["params"]["reason"]=="issued_revision_changed"
    assert tokens.get_by_id(second["token_id"])["consumed_at"] is None


def test_replaced_attempt_refuses_old_token(result_env):
    e=result_env;issued=e.issue()
    run_store.end_attempt("isolated-result","research")
    run_store.open_attempt("isolated-result","research",1)
    value=e.post(issued,e.payload());assert value.status_code==409,value.text
    assert documents.get_by_id(e.reports["research"].doc_id)["revision_no"]==0
    assert e.post(e.issue(),e.payload()).status_code==200


@pytest.mark.parametrize("change", ["replace", "supersede"])
def test_slot_replacement_and_superseded_are_refused(result_env, change):
    e=result_env;issued=e.issue();token=token_service.verify(issued["raw_token"])
    if change == "replace":
        repository.rebind(F,"research","report","NR",e.reports["second"].doc_id)
    else:
        documents.update(e.reports["research"].doc_id, {"superseded_by": e.reports["second"].doc_id})
    with pytest.raises(GraphApiError) as err: binding.validate(token,0)
    assert err.value.code=="result_slot_binding_conflict"
    assert tokens.get_by_id(issued["token_id"])["consumed_at"] is None


def test_four_parallel_cards_and_one_retry_no_cross_links(result_env):
    e=result_env;before=count(e.env,"documents")
    issued={k:e.issue(k) for k in e.nodes};retry=e.issue("second")
    def submit(k):return k,e.post(issued[k],e.payload(k))
    with ThreadPoolExecutor(max_workers=4) as pool:
        results=list(pool.map(submit,e.nodes))
    for key,response in results:
        assert response.status_code==200,response.text
        assert response.json()["doc_id"]==e.reports[key].doc_id
        assert e.reports[key].path.read_text(encoding="utf-8")==e.payload(key)["content"]
    assert e.post(retry,e.payload("second")).status_code==409
    assert e.post(e.issue("second"),e.payload("second",rev=1)).status_code==200
    assert count(e.env,"documents")==before
    for key in e.nodes:
        row=next(r for r in repository.rows(F,[key]) if r["slot"]=="report")
        assert row["doc_id"]==e.reports[key].doc_id


def test_cross_card_payload_and_duplicate_token_are_refused(result_env):
    e=result_env;issued=e.issue()
    assert e.post(issued,e.payload("second")).status_code==403
    assert e.post(issued,e.payload()).status_code==200
    assert e.post(issued,e.payload()).status_code==401


def test_old_new_token_refused_without_allocating_or_consuming(result_env):
    e=result_env;issued=e.issue(scope="new");before=count(e.env,"documents")
    value=e.post(issued,{"action":"new","project":"flowgate","module":"v02","group_name":F,
        "prev_doc_id":D,"doc_type":"NR","content":"legacy result"})
    assert value.status_code==409,value.text
    assert value.json()["error"]["code"]=="result_slot_token_upgrade_required"
    assert count(e.env,"documents")==before and tokens.get_by_id(issued["token_id"])["consumed_at"] is None


def test_unmapped_new_keeps_global_creation_contract(result_env,monkeypatch):
    from modules.flow_gate.api import inbox_routes as inbox
    e=result_env;issued=e.issue(scope="new",mapped=False)
    monkeypatch.setattr(inbox,"has_permission",lambda *a:True)
    before=count(e.env,"documents")
    value=e.post(issued,{"action":"new","project":"flowgate","module":"v02","group_name":F,
        "prev_doc_id":D,"doc_type":"NR","content":"# Explicit independent report\n\nA separate document requested with action=new."})
    assert value.status_code==201,value.text
    assert value.json()["doc_id"] not in {d.doc_id for d in e.reports.values()}
    assert count(e.env,"documents")==before+1
    assert all(next(r for r in repository.rows(F,[key]) if r["slot"]=="report")["doc_id"]==e.reports[key].doc_id
               for key in e.nodes)


def test_job_binds_result_and_rejects_delay_until_revision_changes(result_env):
    e=result_env;ctx=SimpleNamespace(flow_id=F,run_id="isolated-result",project_id="flowgate",root_document=lambda:{"doc_id":D})
    job=node_dispatch._ai_job(ctx,e.nodes["research"],{"result_doc_type":"NR"},None,"user",{},"result")
    assert job["action_scope"]=="edit" and job["target_doc_id"]==e.reports["research"].doc_id
    assert job["result_first_write"] and job["ref_doc_ids"]==[D]
    binding.check_job(job)
    assert e.post(e.issue(),e.payload()).status_code==200
    with pytest.raises(GraphApiError):binding.check_job(job)
    retry=node_dispatch._ai_job(ctx,e.nodes["research"],{},None,"user",{},"result")
    assert retry["result_slot_binding"]["revision_no"]==1 and not retry["result_first_write"]
    instruction=node_dispatch._ai_job(ctx,e.nodes["research"],{},None,"user",{},"instruction")
    assert instruction["action_scope"]=="new" and instruction["target_doc_id"] is None


def test_bound_edit_dry_run_keeps_slot_and_token(result_env):
    e=result_env;issued=e.issue();response=e.post(issued,e.payload(dry_run=True))
    assert response.status_code==200,response.text
    assert response.json()["would_register"]["card_binding"]["slot"]=="report"
    assert documents.get_by_id(e.reports["research"].doc_id)["revision_no"]==0
    assert tokens.get_by_id(issued["token_id"])["consumed_at"] is None


def test_api_rereads_keep_same_body_id_and_revision(result_env,monkeypatch):
    from modules.flow_gate.api.v1 import document_routes
    e=result_env
    e.agent.client.app.include_router(document_routes.router,prefix="/flowgate")
    monkeypatch.setattr(document_routes,"verify_bearer",lambda req:{"_is_user_jwt":True,"issued_to":"user"})
    assert e.post(e.issue(),e.payload()).status_code==200
    for _ in range(2):
        response=e.agent.client.get("/flowgate/api/v1/document/"+e.reports["research"].doc_id)
        assert response.status_code==200,response.text
        value=response.json()
        assert value["doc_id"]==e.reports["research"].doc_id and value["revision_no"]==1
        assert value["content"]==e.payload()["content"]


def test_concurrent_same_slot_edit_has_one_winner(result_env):
    e=result_env
    issued=[e.issue(),e.issue()]
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses=list(pool.map(lambda token:e.post(token,e.payload()), issued))
    assert sorted(r.status_code for r in responses)==[200,409], [r.text for r in responses]
    assert documents.get_by_id(e.reports["research"].doc_id)["revision_no"]==1
    assert len(document_revisions.list_by_doc(e.reports["research"].doc_id))==1
