import sqlite3
from pathlib import Path
import pytest
from modules.flow_gate.services import snapshot_request_service as service
from modules.flow_gate.services import api_server_tools
from modules.flow_gate.api.v1 import snapshot_routes

BASE={"project_id":"p","group_id":"p.m.0001","run_id":"run","token_id":"tok","provider_id":"provider","reason":"build requires a tree","scope":"single_file","requested_paths":["server/app.py"],"purpose":"run tests","source_kind":"current_worktree"}

def test_six_request_fields_and_current_worktree_contract():
 out=service.validate_request(BASE)
 assert [out[k] for k in ("run_id","token_id","provider_id","group_id","reason","scope","requested_paths","purpose")]
 assert out["reason"] != out["purpose"]
 assert out["source_kind"]=="current_worktree"
 with pytest.raises(service.SnapshotRequestError): service.validate_request(BASE|{"source_kind":"main"})
 with pytest.raises(service.SnapshotRequestError): service.validate_request(BASE|{"source_kind":"base"})

@pytest.mark.parametrize(("scope","paths"),[("single_file",["a"]),("selected_files",["a","b"]),("directory",["d"]),("whole_source",[])])
def test_supported_scope_path_combinations(scope,paths):
 assert service.validate_request(BASE|{"scope":scope,"requested_paths":paths})["scope"]==scope

@pytest.mark.parametrize(("scope","paths"),[("single_file",[]),("selected_files",[]),("directory",["a","b"]),("whole_source",["a"])])
def test_invalid_scope_path_combinations(scope,paths):
 with pytest.raises(service.SnapshotRequestError): service.validate_request(BASE|{"scope":scope,"requested_paths":paths})

def test_reason_and_purpose_required_including_whole_source():
 for key in ("reason","purpose"):
  with pytest.raises(service.SnapshotRequestError): service.validate_request(BASE|{"scope":"whole_source","requested_paths":[],key:" "})

@pytest.mark.skip(reason="Legacy Snapshot creation, approval or execution retired by T#3")
def test_api_tool_warns_against_misuse_and_has_no_decision():
 schema=api_server_tools.SCHEMAS["request_source_snapshot"]
 assert schema["properties"]["source_kind"]["enum"]==["current_worktree"]
 assert "approve" not in schema["properties"] and "reject" not in schema["properties"]
 text=api_server_tools.DESCRIPTIONS["request_source_snapshot"]
 assert "Merge Context Tool" in text and "never creates files" in text

@pytest.mark.parametrize("dialect",["sqlite","postgres","mysql"])
def test_all_dialects_have_durable_schema_and_purpose(dialect):
 sql=(Path(__file__).parents[1]/"sql"/"migrations"/dialect/"115_snapshot_requests.sql").read_text(encoding="utf-8")
 for column in ("run_id","token_id","provider_id","group_id","reason","scope","requested_paths","purpose","source_kind","status","requested_at","approved_at","approved_by"):
  assert column in sql
 assert "current_worktree" in sql

def test_sqlite_schema_persists_pending_across_connections(tmp_path):
 path=tmp_path/"db.sqlite"; sql=(Path(__file__).parents[1]/"sql"/"migrations"/"sqlite"/"115_snapshot_requests.sql").read_text()
 with sqlite3.connect(path) as conn:
  conn.executescript(sql)
  conn.execute("INSERT INTO snapshot_requests(snapshot_id,project_id,group_id,run_id,token_id,provider_id,reason,scope,requested_paths,purpose,source_kind,status,requested_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",("s","p","g","r","t","v","reason","whole_source","[]","purpose","current_worktree","requested","now"))
 with sqlite3.connect(path) as conn:
  row=conn.execute("SELECT reason,purpose,status FROM snapshot_requests WHERE snapshot_id='s'").fetchone()
 assert row==("reason","purpose","requested")


class _Txn:
 def __enter__(self): return self
 def __exit__(self,*args): return False
class _Store:
 def transaction(self): return _Txn()

@pytest.mark.skip(reason="Legacy Snapshot creation, approval or execution retired by T#3")
def test_audit_keys_and_idempotent_decision(monkeypatch):
 row=BASE|{"snapshot_id":"s","status":"approved","requested_at":"now","approved_by":"human"}
 calls=[]
 monkeypatch.setattr(service,"get_store",lambda:_Store())
 monkeypatch.setattr(service.db,"transition",lambda *args:(row,False))
 monkeypatch.setattr(service.workflow_events,"create",calls.append)
 assert service.decide("s","approved","human")["status"]=="approved"
 assert calls==[]
 monkeypatch.setattr(service.db,"transition",lambda *args:(row,True))
 service.decide("s","approved","human")
 assert calls[0]["event_type"]=="snapshot_approved"
 import json
 meta=json.loads(calls[0]["metadata"])
 assert set(("snapshot_id","run_id","group_id","provider_id","reason","purpose","scope","requested_paths","source_kind","requested_at","approved_by")) <= set(meta)


@pytest.mark.skip(reason="Legacy Snapshot creation, approval or execution retired by T#3")
def test_worker_token_http_route_uses_live_token_metadata(monkeypatch):
 captured={}
 token={
  "token_id":"tok_live", "project":"flowgate", "group_id":"flowgate.default.0517",
  "ai_run_id":"run_live", "provider_id":"provider_live", "issued_to":"worker_user",
  "action_scope":"new", "doc_ref":"flowgate.default.0517.0014-T",
 }
 run={"run_id":"run_live","token_id":"tok_live","project_id":"flowgate",
      "group_id":"flowgate.default.0517","action_scope":"new",
      "doc_ref":"flowgate.default.0517.0014-T"}
 monkeypatch.setattr(snapshot_routes.token_service,"verify",lambda raw:token)
 monkeypatch.setattr(snapshot_routes.ai_invoke_runs,"get",lambda run_id:run)
 monkeypatch.setattr(service.db_documents,"get_by_id",lambda doc_id:{"type_code":"T"})
 monkeypatch.setattr(snapshot_routes.service,"create_request",lambda data,actor:captured.update(data=data,actor=actor) or data)
 request=type("WorkerRequest",(),{"headers":{"Authorization":"Bearer live-token"}})()
 body=snapshot_routes.RequestIn(
  reason="build requires a tree", scope="single_file", requested_paths=["server/app.py"],
  purpose="run tests", source_kind="current_worktree",
 )
 result=snapshot_routes.request_snapshot(body,request)
 assert result["ok"] is True
 assert captured["data"]["project_id"]=="flowgate"
 assert captured["data"]["provider_id"]=="provider_live"
 assert captured["data"]["run_id"]=="run_live"
 assert captured["actor"]=="worker_user"


@pytest.mark.skip(reason="Legacy Snapshot creation, approval or execution retired by T#3")
def test_human_approval_http_endpoint_materializes_to_created(monkeypatch):
 from fastapi import FastAPI
 from fastapi.testclient import TestClient
 from modules.flow_gate.auth.middleware import get_current_user
 captured=[]
 approved=BASE|{"snapshot_id":"snap_route","status":"approved"}
 monkeypatch.setattr(snapshot_routes.service,"decide",lambda *args:approved)
 monkeypatch.setattr(
  snapshot_routes.materialization,"materialize",
  lambda snapshot_id,actor:captured.append((snapshot_id,actor)) or approved|{"status":"created"},
 )
 app=FastAPI(); app.include_router(snapshot_routes.router)
 app.dependency_overrides[get_current_user]=lambda:{"user_id":"human"}
 response=TestClient(app).post("/api/v1/snapshots/snap_route/approve",json={})
 assert response.status_code==200
 assert response.json()["request"]["status"]=="created"
 assert captured==[("snap_route","human")]


# T0012 §12/§13 — the client's Pending badge/list and the auto-open dialog have nothing
# to refresh on without a signal; §17 allows a small connection fix within T#3's scope.
@pytest.mark.skip(reason="Legacy Snapshot creation, approval or execution retired by T#3")
def test_create_and_approve_broadcast_refresh_signal_not_the_list_itself(monkeypatch):
 events=[]
 monkeypatch.setattr(service,"broadcast_event_threadsafe",lambda event:events.append(event) or 1)
 monkeypatch.setattr(service,"get_store",lambda:_Store())
 monkeypatch.setattr(service.db,"pending_for_owner",lambda *args:None)
 monkeypatch.setattr(service.db,"create",lambda n:BASE|{"snapshot_id":"snap_evt","status":"requested"})
 monkeypatch.setattr(service.workflow_events,"create",lambda data:data)
 row=service.create_request(BASE,"worker_user")
 assert len(events)==1
 assert events[0].event_type=="snapshot_request_updated"
 assert events[0].payload=={"snapshot_id":"snap_evt","group_id":"p.m.0001","status":"requested"}
 assert events[0].project=="p" and events[0].group_id=="p.m.0001"
 assert "reason" not in events[0].payload and "purpose" not in events[0].payload

 approved=BASE|{"snapshot_id":"snap_evt","status":"approved"}
 monkeypatch.setattr(service.db,"transition",lambda *args:(approved,True))
 service.decide("snap_evt","approved","human")
 assert len(events)==2
 assert events[1].payload["status"]=="approved"

 # A no-op decision (already-decided request) changes nothing and must not re-broadcast.
 monkeypatch.setattr(service.db,"transition",lambda *args:(approved,False))
 service.decide("snap_evt","approved","human")
 assert len(events)==2


@pytest.mark.skip(reason="Legacy Snapshot creation, approval or execution retired by T#3")
def test_broadcast_failure_never_breaks_the_decision(monkeypatch):
 def _boom(event): raise RuntimeError("no subscribers reachable")
 monkeypatch.setattr(service,"broadcast_event_threadsafe",_boom)
 monkeypatch.setattr(service,"get_store",lambda:_Store())
 monkeypatch.setattr(service.db,"pending_for_owner",lambda *args:None)
 monkeypatch.setattr(service.db,"create",lambda n:BASE|{"snapshot_id":"snap_evt2","status":"requested"})
 monkeypatch.setattr(service.workflow_events,"create",lambda data:data)
 row=service.create_request(BASE,"worker_user")
 assert row["snapshot_id"]=="snap_evt2"


@pytest.mark.skip(reason="Legacy Snapshot creation, approval or execution retired by T#3")
def test_c16_pending_reused_per_chain_without_duplicate_events(monkeypatch):
 rows={}
 events=[]
 signals=[]
 lock=__import__("threading").Lock()
 monkeypatch.setattr(service,"get_store",lambda:_Store())
 monkeypatch.setattr(service.workflow_events,"create",events.append)
 monkeypatch.setattr(service,"_notify",lambda *args:signals.append(args))
 def pending(project,group,chain,run):
  with lock:
   return next((dict(row) for row in rows.values() if row["project_id"]==project and row["group_id"]==group and row["status"]=="requested" and (row.get("chain_id") or row["run_id"])==(chain or run)),None)
 def create(data):
  with lock:
   owner=data.get("chain_id") or data["run_id"]
   if any(row["status"]=="requested" and (row.get("chain_id") or row["run_id"])==owner for row in rows.values()):
    raise sqlite3.IntegrityError("uq_snapshot_pending_owner")
   row=dict(data)|{"snapshot_id":data["snapshot_id"],"status":"requested","requested_at":"now"}
   rows[row["snapshot_id"]]=row
   return dict(row)
 monkeypatch.setattr(service.db,"pending_for_owner",pending)
 monkeypatch.setattr(service.db,"create",create)
 first=service.create_request(BASE|{"chain_id":"chain-a","snapshot_id":"snap_a"},"worker")
 assert first["reused_pending"] is False
 from concurrent.futures import ThreadPoolExecutor
 with ThreadPoolExecutor(max_workers=10) as pool:
  repeated=list(pool.map(lambda _:service.create_request(BASE|{"chain_id":"chain-a","snapshot_id":"snap_other"},"worker"),range(10)))
 assert {row["snapshot_id"] for row in repeated}=={"snap_a"}
 assert all(row["reused_pending"] for row in repeated)
 assert len(rows)==len(events)==len(signals)==1
 other=service.create_request(BASE|{"chain_id":"chain-b","snapshot_id":"snap_b"},"worker")
 assert other["snapshot_id"]=="snap_b"
 rows["snap_a"]["status"]="rejected"
 replacement=service.create_request(BASE|{"chain_id":"chain-a","snapshot_id":"snap_new"},"worker")
 assert replacement["snapshot_id"]=="snap_new"

def test_active_route_lists_created_and_refreshes_stale_when_group_scoped(monkeypatch):
 created_row=BASE|{"snapshot_id":"snap_active","status":"created","stale":False}
 calls=[]
 monkeypatch.setattr(snapshot_routes.db,"list_created",lambda project_id,group_id:[created_row])
 monkeypatch.setattr(
  snapshot_routes.materialization,"refresh_stale",
  lambda snapshot_id,actor:calls.append((snapshot_id,actor)) or created_row|{"stale":True},
 )
 result=snapshot_routes.active(project_id="p",group_id="p.m.0001",user={"user_id":"human"})
 assert result["ok"] is True
 assert result["requests"][0]["stale"] is True
 assert calls==[("snap_active","human")]

 # No group scope: the cheap path — no per-row live refresh.
 calls.clear()
 result=snapshot_routes.active(project_id="p",group_id=None,user={"user_id":"human"})
 assert result["requests"]==[created_row]
 assert calls==[]
