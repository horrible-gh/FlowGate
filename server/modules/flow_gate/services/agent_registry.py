from __future__ import annotations
import hashlib,hmac,os,secrets
from datetime import datetime,timezone,timedelta
from modules.flow_gate.db.connection import FlowGateStore,get_store

SUPPORTED=[1]; ENROLLMENT_TTL=600; HEARTBEAT_INTERVAL=30; ONLINE_TTL=90
CAPS=("ai_cli","ai_api","storage")

class AgentError(Exception):
 def __init__(self,status,code,message,details=None): self.status=status;self.code=code;self.message=message;self.details=details or {}

def now():
 return datetime.now(timezone.utc)
def stamp(v=None):
 return (v or now()).isoformat(timespec="seconds").replace("+00:00","Z")
def secret(prefix):
 return prefix+secrets.token_urlsafe(32)
def digest(domain,raw):
 pepper=os.environ.get("FLOWGATE_AGENT_PEPPER")
 if not pepper: raise AgentError(503,"temporarily_unavailable","Agent secret service is unavailable")
 return hmac.new(pepper.encode(),(domain+"\0"+raw).encode(),hashlib.sha256).hexdigest()
def pepper_id():
 return os.environ.get("FLOWGATE_AGENT_PEPPER_ID","primary")

def _bool(v): return bool(v)
def _row(s,agent_id):
 return s._fetch_one("""SELECT a.*,
 (SELECT COUNT(*) FROM agent_credentials c WHERE c.agent_id=a.agent_id) credential_count,
 (SELECT COUNT(*) FROM agent_credentials c WHERE c.agent_id=a.agent_id AND c.revoked_at IS NULL) active_count,
 (SELECT MAX(e.expires_at) FROM agent_enrollments e WHERE e.agent_id=a.agent_id AND e.consumed_at IS NULL AND e.revoked_at IS NULL) enrollment_expires_at,
 (SELECT MAX(c.last_heartbeat_at) FROM agent_credentials c WHERE c.agent_id=a.agent_id AND c.revoked_at IS NULL) current_heartbeat_at
 FROM agents a WHERE a.agent_id=?""",[agent_id])
def detail(r,at=None):
 if not r: return None
 cfg={k:_bool(r["configured_"+k]) for k in CAPS}
 rep={k:(None if r["reported_"+k] is None else _bool(r["reported_"+k])) for k in CAPS}
 active=int(r.get("active_count") or 0)>0; history=int(r.get("credential_count") or 0)>0
 online="not_applicable" if r["location"]=="local" else "offline"
 hb=r.get("current_heartbeat_at")
 if online=="offline" and _bool(r["enabled"]) and active and r["protocol_compatibility"]=="compatible" and hb:
  try:
   t=datetime.fromisoformat(str(hb).replace("Z","+00:00")); n=at or now()
   if t<=n<t+timedelta(seconds=ONLINE_TTL): online="online"
  except ValueError: pass
 return {"agent_id":r["agent_id"],"name":r["name"],"enabled":_bool(r["enabled"]),"location":r["location"],
 "connection_mode":r["connection_mode"],"local_executable_path":r["local_executable_path"],
 "configured_capabilities":cfg,"reported_capabilities":rep,
 "effective_capabilities":{k:cfg[k] and rep[k] is True for k in CAPS},
 "registration_status":("registered" if active else "revoked" if history else "unregistered"),
 "credential_status":("active" if active else "revoked" if history else "none"),"has_registration_history":history,
 "enrollment_status":"pending" if r.get("enrollment_expires_at") else "none","enrollment_expires_at":r.get("enrollment_expires_at"),
 "online_status":online,"last_seen_at":r["last_seen_at"],"agent_version":r["agent_version"],"protocol_version":r["protocol_version"],
 "protocol_compatibility":r["protocol_compatibility"],"last_rejected_protocol_version":r["last_rejected_protocol_version"],
 "last_protocol_rejection_at":r["last_protocol_rejection_at"],"os":r["os"],"architecture":r["architecture"]}

class AgentService:
 def __init__(self,store=None): self.s=store or get_store()
 def get(self,aid):
  r=_row(self.s,aid)
  if not r: raise AgentError(404,"agent_not_found","Agent not found")
  return detail(r)
 def list(self):
  ids=self.s._fetch_all("SELECT agent_id FROM agents ORDER BY agent_id")
  return [detail(_row(self.s,x["agent_id"])) for x in ids]
 def create(self,b):
  aid="agt_"+secrets.token_urlsafe(20); ts=stamp(); c=b["configured_capabilities"]
  with self.s.transaction():
   self.s._execute("""INSERT INTO agents(agent_id,name,enabled,location,connection_mode,local_executable_path,configured_ai_cli,configured_ai_api,configured_storage,created_at,updated_at)
 VALUES(?,?,?,?,?,?,?,?,?,?,?)""",[aid,b["name"].strip(),b["enabled"],b["location"],b["connection_mode"],b["local_executable_path"],c["ai_cli"],c["ai_api"],c["storage"],ts,ts])
  return self.get(aid)
 def patch(self,aid,b):
  if "location" in b: raise AgentError(422,"validation_failed","Invalid Agent field",{"field":"location","reason":"immutable"})
  current=self.get(aid); merged={k:b.get(k,current[k]) for k in ("name","enabled","connection_mode","local_executable_path")}
  caps=b.get("configured_capabilities",current["configured_capabilities"])
  loc=current["location"]
  if (loc=="remote" and (merged["connection_mode"]!="agent_pull" or merged["local_executable_path"] is not None)) or (loc=="local" and merged["connection_mode"] is not None):
   raise AgentError(422,"validation_failed","Invalid Agent field",{"field":"connection_mode","reason":"invalid_combination"})
  with self.s.transaction():
   self.s._execute("UPDATE agents SET name=?,enabled=?,connection_mode=?,local_executable_path=?,configured_ai_cli=?,configured_ai_api=?,configured_storage=?,updated_at=? WHERE agent_id=?",
   [merged["name"].strip(),merged["enabled"],merged["connection_mode"],merged["local_executable_path"],caps["ai_cli"],caps["ai_api"],caps["storage"],stamp(),aid])
  return self.get(aid)
 def issue(self,aid):
  raw=secret("enr_"); ts=now(); exp=ts+timedelta(seconds=ENROLLMENT_TTL)
  with self.s.transaction():
   r=_row(self.s,aid)
   if not r: raise AgentError(404,"agent_not_found","Agent not found")
   if r["location"]!="remote" or not _bool(r["enabled"]): raise AgentError(409,"enrollment_not_allowed","Enrollment requires an enabled Remote Agent")
   self.s._execute("UPDATE agent_enrollments SET revoked_at=? WHERE agent_id=? AND consumed_at IS NULL AND revoked_at IS NULL",[stamp(ts),aid])
   self.s._execute("INSERT INTO agent_enrollments(enrollment_id,agent_id,token_digest,pepper_id,created_at,expires_at) VALUES(?,?,?,?,?,?)",
    ["enr_"+secrets.token_urlsafe(20),aid,digest("enrollment",raw),pepper_id(),stamp(ts),stamp(exp)])
  return {"agent_id":aid,"enrollment_token":raw,"expires_at":stamp(exp)}
 def enroll(self,b):
  raw=secret("agc_"); d=digest("enrollment",b["enrollment_token"]); ts=stamp()
  with self.s.transaction():
   e=self.s._fetch_one("""SELECT e.*,a.enabled,a.location FROM agent_enrollments e JOIN agents a ON a.agent_id=e.agent_id
 WHERE e.token_digest=? AND e.consumed_at IS NULL AND e.revoked_at IS NULL AND e.expires_at>?""",[d,ts])
   if not e or not _bool(e["enabled"]) or e["location"]!="remote": raise AgentError(409,"enrollment_unavailable","Enrollment token is unavailable")
   if b["protocol_version"] not in SUPPORTED: raise AgentError(409,"protocol_incompatible","Unsupported Agent protocol version",{"received_version":b["protocol_version"],"supported_versions":SUPPORTED})
   if self.s._execute_affected("UPDATE agent_enrollments SET consumed_at=? WHERE enrollment_id=? AND consumed_at IS NULL AND revoked_at IS NULL AND expires_at>?",[ts,e["enrollment_id"],ts])!=1:
    raise AgentError(409,"enrollment_unavailable","Enrollment token is unavailable")
   self.s._execute("UPDATE agent_credentials SET revoked_at=? WHERE agent_id=? AND revoked_at IS NULL",[ts,e["agent_id"]])
   self.s._execute("INSERT INTO agent_credentials(credential_id,agent_id,credential_digest,pepper_id,issued_at) VALUES(?,?,?,?,?)",
    ["agc_"+secrets.token_urlsafe(20),e["agent_id"],digest("agent_credential",raw),pepper_id(),ts])
  return {"agent_id":e["agent_id"],"agent_credential":raw,"protocol_version":1,"supported_protocol_versions":SUPPORTED,"heartbeat_interval_seconds":HEARTBEAT_INTERVAL,"online_ttl_seconds":ONLINE_TTL}
 def authenticate(self,raw):
  if not raw or not raw.startswith("agc_"): raise AgentError(401,"unauthorized","Invalid Agent credential")
  r=self.s._fetch_one("""SELECT c.credential_id,c.agent_id,a.enabled,a.location FROM agent_credentials c JOIN agents a ON a.agent_id=c.agent_id WHERE c.credential_digest=? AND c.revoked_at IS NULL""",[digest("agent_credential",raw)])
  if not r: raise AgentError(401,"unauthorized","Invalid Agent credential")
  if not _bool(r["enabled"]): raise AgentError(403,"agent_disabled","Agent is disabled")
  return r
 def heartbeat(self,raw,b):
  auth=self.authenticate(raw); ts=stamp()
  if auth["agent_id"]!=b["agent_id"]: raise AgentError(403,"agent_identity_mismatch","Agent identity does not match credential")
  if auth["location"]!="remote": raise AgentError(401,"unauthorized","Invalid Agent credential")
  incompatible=b["protocol_version"] not in SUPPORTED
  with self.s.transaction():
   live=self.s._fetch_one("SELECT revoked_at FROM agent_credentials WHERE credential_id=?",[auth["credential_id"]])
   if not live or live["revoked_at"] is not None: raise AgentError(401,"unauthorized","Invalid Agent credential")
   if incompatible:
    self.s._execute("UPDATE agents SET protocol_compatibility='incompatible',last_rejected_protocol_version=?,last_protocol_rejection_at=?,updated_at=? WHERE agent_id=?",[b["protocol_version"],ts,ts,auth["agent_id"]])
   else:
    c=b["reported_capabilities"]
    self.s._execute("""UPDATE agents SET reported_ai_cli=?,reported_ai_api=?,reported_storage=?,last_seen_at=?,agent_version=?,protocol_version=?,os=?,architecture=?,protocol_compatibility='compatible',last_rejected_protocol_version=NULL,last_protocol_rejection_at=NULL,updated_at=? WHERE agent_id=?""",
     [c["ai_cli"],c["ai_api"],c["storage"],ts,b["agent_version"],b["protocol_version"],b["os"],b["architecture"],ts,auth["agent_id"]])
    self.s._execute("UPDATE agent_credentials SET last_heartbeat_at=? WHERE credential_id=?",[ts,auth["credential_id"]])
  if incompatible: raise AgentError(409,"protocol_incompatible","Unsupported Agent protocol version",{"received_version":b["protocol_version"],"supported_versions":SUPPORTED})
  return {"ok":True,"agent_id":auth["agent_id"],"server_time":ts,"heartbeat_interval_seconds":HEARTBEAT_INTERVAL,"online_ttl_seconds":ONLINE_TTL}
 def revoke(self,aid):
  self.get(aid); ts=stamp()
  with self.s.transaction():
   self.s._execute("UPDATE agent_credentials SET revoked_at=? WHERE agent_id=? AND revoked_at IS NULL",[ts,aid])
   self.s._execute("UPDATE agent_enrollments SET revoked_at=? WHERE agent_id=? AND consumed_at IS NULL AND revoked_at IS NULL",[ts,aid])
  return self.get(aid)
 def delete(self,aid):
  self.get(aid)
  with self.s.transaction():
   if self.s._fetch_one("SELECT 1 ok FROM agent_credentials WHERE agent_id=?",[aid]): raise AgentError(409,"agent_has_registration_history","Registered Agent cannot be deleted")
   self.s._execute("DELETE FROM agents WHERE agent_id=?",[aid])
