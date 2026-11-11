from __future__ import annotations
from typing import Literal
from fastapi import APIRouter,Depends,Header,Request
from fastapi.responses import JSONResponse,Response
from pydantic import BaseModel,ConfigDict,Field,StrictBool,StrictInt,model_validator
from modules.flow_gate.rbac.decorators import require_permission
from modules.flow_gate.auth.middleware import get_current_user
from modules.flow_gate.services.agent_registry import AgentService,AgentError

router=APIRouter()
class Strict(BaseModel): model_config=ConfigDict(extra="forbid")
class Caps(Strict): ai_cli:bool;ai_api:bool;storage:bool
class AgentCreate(Strict):
 name:str=Field(min_length=1,max_length=100);enabled:bool;location:Literal["local","remote"];connection_mode:Literal["agent_pull"]|None;local_executable_path:str|None;configured_capabilities:Caps
 remote_workdir:str|None=None
 # flowgate.v02test.0019: workspace cleanup policy (default ON / 24 h, whole hours 1..720).
 workspace_cleanup_enabled:StrictBool=True
 workspace_cleanup_retention_hours:StrictInt=Field(default=24,ge=1,le=720)
 @model_validator(mode="after")
 def combo(self):
  if self.name.strip()=="" or (self.location=="remote" and(self.connection_mode!="agent_pull" or self.local_executable_path is not None)) or (self.location=="local" and self.connection_mode is not None): raise ValueError("invalid Agent field combination")
  return self
class AgentPatch(Strict):
 name:str|None=None;enabled:bool|None=None;location:Literal["local","remote"]|None=None;connection_mode:Literal["agent_pull"]|None=None;local_executable_path:str|None=None;configured_capabilities:Caps|None=None
 # flowgate.v02.0135 CH0006: null or blank clears it.
 remote_workdir:str|None=None
 workspace_cleanup_enabled:StrictBool|None=None
 workspace_cleanup_retention_hours:StrictInt|None=Field(default=None,ge=1,le=720)
 @model_validator(mode="after")
 def valid_changes(self):
  for field in ("name","enabled","configured_capabilities","workspace_cleanup_enabled","workspace_cleanup_retention_hours"):
   if field in self.model_fields_set and getattr(self,field) is None: raise ValueError(field+" cannot be null")
  if self.name is not None and not 1<=len(self.name.strip())<=100: raise ValueError("name must be 1 to 100 characters")
  return self
class Empty(Strict): pass
class Enroll(Strict): enrollment_token:str;protocol_version:int;agent_version:str;os:str;architecture:str
class Heartbeat(Strict): agent_id:str;protocol_version:int;agent_version:str;os:str;architecture:str;reported_capabilities:Caps
def svc(): return AgentService(owner_user_id=None)
def personal_svc(user): return AgentService(owner_user_id=user["user_id"])
def err(e): return JSONResponse({"ok":False,"error":{"code":e.code,"message":e.message,"details":e.details}},status_code=e.status)
def guard(fn):
 try:return fn()
 except AgentError as e:return err(e)
def no_store(value,status=200,headers=None):
 h={"Cache-Control":"no-store",**(headers or {})};return JSONResponse(value,status_code=status,headers=h)
@router.get("/system/agents")
def listing(user=Depends(require_permission("system.settings.manage"))): return no_store({"agents":svc().list()})
@router.post("/system/agents")
def create(b:AgentCreate,user=Depends(require_permission("system.settings.manage"))):
 def run():
  v=svc().create(b.model_dump());return no_store(v,201,{"Location":"/flowgate/api/v1/system/agents/"+v["agent_id"]})
 return guard(run)
@router.get("/system/agents/{aid}")
def get(aid:str,user=Depends(require_permission("system.settings.manage"))): return guard(lambda:no_store(svc().get(aid)))
@router.patch("/system/agents/{aid}")
def patch(aid:str,b:AgentPatch,user=Depends(require_permission("system.settings.manage"))):
 d=b.model_dump(exclude_unset=True)
 if not d:return err(AgentError(422,"validation_failed","At least one field is required",{"field":"body","reason":"required"}))
 return guard(lambda:no_store(svc().patch(aid,d)))
@router.post("/system/agents/{aid}/enrollments")
def issue(aid:str,b:Empty,user=Depends(require_permission("system.settings.manage"))): return guard(lambda:no_store(svc().issue(aid),201))
@router.post("/system/agents/{aid}/credential/revoke")
def revoke(aid:str,b:Empty,user=Depends(require_permission("system.settings.manage"))): return guard(lambda:no_store(svc().revoke(aid)))
@router.delete("/system/agents/{aid}",status_code=204)
def delete(aid:str,user=Depends(require_permission("system.settings.manage"))):
 r=guard(lambda:svc().delete(aid))
 return Response(status_code=204) if r is None else r

# Personal management uses the same registry and machine enrollment contract.
# The authenticated identity is the only source of ownership; owner fields are forbidden.
@router.get("/me/agents")
def personal_listing(user=Depends(get_current_user)):
 return no_store({"agents":personal_svc(user).list()})
@router.post("/me/agents")
def personal_create(b:AgentCreate,user=Depends(get_current_user)):
 def run():
  v=personal_svc(user).create(b.model_dump())
  return no_store(v,201,{"Location":"/flowgate/api/v1/me/agents/"+v["agent_id"]})
 return guard(run)
@router.get("/me/agents/{aid}")
def personal_get(aid:str,user=Depends(get_current_user)):
 return guard(lambda:no_store(personal_svc(user).get(aid)))
@router.patch("/me/agents/{aid}")
def personal_patch(aid:str,b:AgentPatch,user=Depends(get_current_user)):
 d=b.model_dump(exclude_unset=True)
 if not d:return err(AgentError(422,"validation_failed","At least one field is required"))
 return guard(lambda:no_store(personal_svc(user).patch(aid,d)))
@router.post("/me/agents/{aid}/enrollments")
def personal_issue(aid:str,b:Empty,user=Depends(get_current_user)):
 return guard(lambda:no_store(personal_svc(user).issue(aid),201))
@router.post("/me/agents/{aid}/credential/revoke")
def personal_revoke(aid:str,b:Empty,user=Depends(get_current_user)):
 return guard(lambda:no_store(personal_svc(user).revoke(aid)))
@router.delete("/me/agents/{aid}",status_code=204)
def personal_delete(aid:str,user=Depends(get_current_user)):
 r=guard(lambda:personal_svc(user).delete(aid))
 return Response(status_code=204) if r is None else r

@router.post("/agent/enrollments")
def enroll(b:Enroll): return guard(lambda:no_store(svc().enroll(b.model_dump()),201))
@router.post("/agent/heartbeat")
def heartbeat(b:Heartbeat,authorization:str|None=Header(default=None)):
 raw=authorization[7:] if authorization and authorization.startswith("Bearer ") else None
 return guard(lambda:no_store(svc().heartbeat(raw,b.model_dump())))
