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


def test_worker_token_http_route_uses_live_token_metadata(monkeypatch):
 captured={}
 token={
  "token_id":"tok_live", "project":"flowgate", "group_id":"flowgate.default.0517",
  "ai_run_id":"run_live", "provider_id":"provider_live", "issued_to":"worker_user",
 }
 monkeypatch.setattr(snapshot_routes.token_service,"verify",lambda raw:token)
 monkeypatch.setattr(snapshot_routes.ai_invoke_runs,"get",lambda run_id:None)
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
