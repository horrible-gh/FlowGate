import json,os,sqlite3
from datetime import datetime,timezone,timedelta
from pathlib import Path
import pytest
os.environ.setdefault("TESTING","1")
os.environ.setdefault("FLOWGATE_AGENT_PEPPER","test-only-pepper")
from modules.flow_gate.services import agent_registry as ar
from modules.flow_gate.api.v1 import agent_routes as routes

ROOT=Path(__file__).resolve().parents[1]
MIG=ROOT/"sql"/"migrations"

def fresh():
 db=sqlite3.connect(":memory:")
 db.execute("PRAGMA foreign_keys=ON")
 db.executescript((MIG/"sqlite"/"121_agent_registry.sql").read_text())
 return db
def agent(db,aid="agt_x",location="remote"):
 now="2026-01-01T00:00:00Z"
 db.execute("""INSERT INTO agents(agent_id,name,enabled,location,connection_mode,local_executable_path,created_at,updated_at)
 VALUES(?,?,?,?,?,?,?,?)""",(aid,"runner",1,location,"agent_pull" if location=="remote" else None,None,now,now))
 db.commit()

def test_migration_121_has_three_dialect_parity():
 texts=[(MIG/d/"121_agent_registry.sql").read_text() for d in ("sqlite","mysql","postgres")]
 for text in texts:
  for table in ("agents","agent_credentials","agent_enrollments"): assert "CREATE TABLE "+table in text
  assert "protocol_compatibility" in text and "last_heartbeat_at" in text
  assert "uq_agent_credentials_active" in text and "uq_agent_enrollments_pending" in text
  assert "code_hash" not in text and "secret_hash" not in text

def test_sqlite_enforces_single_active_credential_and_history_delete_guard():
 db=fresh();agent(db); now="2026-01-01T00:00:00Z"
 db.execute("INSERT INTO agent_credentials VALUES(?,?,?,?,?,?,?)",("c1","agt_x","a"*64,"p",now,None,None))
 with pytest.raises(sqlite3.IntegrityError):
  db.execute("INSERT INTO agent_credentials VALUES(?,?,?,?,?,?,?)",("c2","agt_x","b"*64,"p",now,None,None))
 with pytest.raises(sqlite3.IntegrityError): db.execute("DELETE FROM agent_credentials WHERE credential_id='c1'")

def test_sqlite_pending_enrollment_is_unique_and_cascades_for_never_registered_agent():
 db=fresh();agent(db);now="2026-01-01T00:00:00Z";exp="2026-01-01T00:10:00Z"
 db.execute("INSERT INTO agent_enrollments VALUES(?,?,?,?,?,?,?,?)",("e1","agt_x","a"*64,"p",now,exp,None,None))
 with pytest.raises(sqlite3.IntegrityError):
  db.execute("INSERT INTO agent_enrollments VALUES(?,?,?,?,?,?,?,?)",("e2","agt_x","b"*64,"p",now,exp,None,None))
 db.execute("DELETE FROM agents WHERE agent_id='agt_x'")
 assert db.execute("SELECT count(*) FROM agent_enrollments").fetchone()[0]==0

def test_digest_is_domain_separated_and_never_raw(monkeypatch):
 monkeypatch.setenv("FLOWGATE_AGENT_PEPPER","pepper")
 raw="agc_super-secret"
 a=ar.digest("agent_credential",raw);b=ar.digest("enrollment",raw)
 assert a!=b and len(a)==64 and raw not in a

def test_missing_pepper_fails_closed(monkeypatch):
 monkeypatch.delenv("FLOWGATE_AGENT_PEPPER",raising=False)
 with pytest.raises(ar.AgentError) as e: ar.digest("agent_credential","agc_x")
 assert (e.value.status,e.value.code)==(503,"temporarily_unavailable")

def test_route_error_response_preserves_status_and_structured_body():
 response=routes.err(ar.AgentError(404,"agent_not_found","Agent not found",{"agent_id":"agt_missing"}))
 assert response.status_code==404
 assert json.loads(response.body)=={"ok":False,"error":{"code":"agent_not_found","message":"Agent not found","details":{"agent_id":"agt_missing"}}}

def test_route_guard_converts_agent_error_without_crashing():
 def fail(): raise ar.AgentError(409,"protocol_incompatible","Protocol incompatible",{"supported":[1]})
 response=routes.guard(fail)
 assert response.status_code==409
 assert json.loads(response.body)["error"]=={"code":"protocol_incompatible","message":"Protocol incompatible","details":{"supported":[1]}}

def test_detail_online_requires_current_credential_heartbeat_and_ttl():
 n=datetime(2026,1,1,tzinfo=timezone.utc); t=(n-timedelta(seconds=89)).isoformat()
 row={"agent_id":"a","name":"n","enabled":1,"location":"remote","connection_mode":"agent_pull","local_executable_path":None,
 "configured_ai_cli":1,"configured_ai_api":0,"configured_storage":1,"reported_ai_cli":1,"reported_ai_api":0,"reported_storage":0,
 "credential_count":1,"active_count":1,"enrollment_expires_at":None,"current_heartbeat_at":t,"last_seen_at":t,
 "agent_version":"1","protocol_version":1,"protocol_compatibility":"compatible","last_rejected_protocol_version":None,
 "last_protocol_rejection_at":None,"os":"linux","architecture":"amd64"}
 assert ar.detail(row,n)["online_status"]=="online"
 assert ar.detail(row,n+timedelta(seconds=1))["online_status"]=="offline"


@pytest.fixture
def heartbeat_api(monkeypatch):
 from contextlib import contextmanager
 from fastapi import FastAPI
 from fastapi.testclient import TestClient

 db=sqlite3.connect(":memory:",check_same_thread=False)
 db.execute("PRAGMA foreign_keys=ON")
 db.executescript((MIG/"sqlite"/"121_agent_registry.sql").read_text())
 agent(db)
 raw="agc_test-credential"
 db.execute("INSERT INTO agent_credentials(credential_id,agent_id,credential_digest,pepper_id,issued_at) VALUES(?,?,?,?,?)",
  ("cred_x","agt_x",ar.digest("agent_credential",raw),"test","2026-01-01T00:00:00Z"))
 db.commit()

 class Store:
  @contextmanager
  def transaction(self):
   with db:
    yield self
  def _fetch_one(self,sql,params):
   cursor=db.execute(sql,params)
   row=cursor.fetchone()
   return dict(zip([col[0] for col in cursor.description],row)) if row else None
  def _execute(self,sql,params):
   return db.execute(sql,params)

 monkeypatch.setattr(routes,"svc",lambda: ar.AgentService(Store()))
 app=FastAPI()
 app.include_router(routes.router,prefix="/flowgate/api/v1")
 with TestClient(app) as client:
  yield client,db,raw
 db.close()


def heartbeat_body(**overrides):
 body={"agent_id":"agt_x","protocol_version":1,"agent_version":"1.2.3",
  "os":"linux","architecture":"amd64",
  "reported_capabilities":{"ai_cli":True,"ai_api":False,"storage":True}}
 body.update(overrides)
 return body


def test_heartbeat_api_success_contract_and_runtime_state(heartbeat_api):
 client,db,raw=heartbeat_api
 response=client.post("/flowgate/api/v1/agent/heartbeat",
  headers={"Authorization":"Bearer "+raw},json=heartbeat_body())
 assert response.status_code==200
 payload=response.json()
 assert set(payload)=={"accepted","agent_id","server_time","protocol_version",
  "heartbeat_interval_seconds","online_ttl_seconds"}
 assert payload["accepted"] is True
 assert payload["agent_id"]=="agt_x"
 assert type(payload["protocol_version"]) is int and payload["protocol_version"]==1
 assert payload["heartbeat_interval_seconds"]==30
 assert payload["online_ttl_seconds"]==90
 assert datetime.fromisoformat(payload["server_time"].replace("Z","+00:00")).tzinfo is not None
 row=db.execute("SELECT last_seen_at,agent_version,protocol_version,os,architecture,"
  "reported_ai_cli,reported_ai_api,reported_storage,protocol_compatibility "
  "FROM agents WHERE agent_id='agt_x'").fetchone()
 assert row==(payload["server_time"],"1.2.3",1,"linux","amd64",1,0,1,"compatible")
 assert db.execute("SELECT last_heartbeat_at FROM agent_credentials WHERE credential_id='cred_x'").fetchone()[0]==payload["server_time"]


@pytest.mark.parametrize(("headers","body","setup","status","code"),[
 ({},heartbeat_body(),None,401,"unauthorized"),
 (None,heartbeat_body(), "disable",403,"agent_disabled"),
 (None,heartbeat_body(agent_id="agt_other"),None,403,"agent_identity_mismatch"),
 (None,heartbeat_body(protocol_version=2),None,409,"protocol_incompatible"),
 (None,{"agent_id":"agt_x"},None,422,None),
])
def test_heartbeat_api_failure_contract(heartbeat_api,headers,body,setup,status,code):
 client,db,raw=heartbeat_api
 if setup=="disable":
  db.execute("UPDATE agents SET enabled=0 WHERE agent_id='agt_x'")
  db.commit()
 response=client.post("/flowgate/api/v1/agent/heartbeat",
  headers=headers if headers is not None else {"Authorization":"Bearer "+raw},json=body)
 assert response.status_code==status
 if code:
  assert response.json()["ok"] is False
  assert response.json()["error"]["code"]==code
 if status==409:
  row=db.execute("SELECT last_seen_at,agent_version,protocol_version,"
   "reported_ai_cli,reported_ai_api,reported_storage,protocol_compatibility,"
   "last_rejected_protocol_version,last_protocol_rejection_at FROM agents WHERE agent_id='agt_x'").fetchone()
  assert row[:6]==(None,)*6
  assert row[6:8]==("incompatible",2)
  assert row[8] is not None
  assert db.execute("SELECT last_heartbeat_at FROM agent_credentials WHERE credential_id='cred_x'").fetchone()[0] is None
