from __future__ import annotations
from typing import Literal
from fastapi import APIRouter,Depends,Header,Request
from fastapi.responses import JSONResponse,Response
from pydantic import BaseModel,ConfigDict,Field,model_validator
from modules.flow_gate.rbac.decorators import require_permission
from modules.flow_gate.services.agent_registry import AgentService,AgentError

router=APIRouter()
class Strict(BaseModel): model_config=ConfigDict(extra="forbid")
class Caps(Strict): ai_cli:bool;ai_api:bool;storage:bool
class AgentCreate(Strict):
 name:str=Field(min_length=1,max_length=100);enabled:bool;location:Literal["local","remote"];connection_mode:Literal["agent_pull"]|None;local_executable_path:str|None;configured_capabilities:Caps
 @model_validator(mode="after")
 def combo(self):
  if self.name.strip()=="" or (self.location=="remote" and(self.connection_mode!="agent_pull" or self.local_executable_path is not None)) or (self.location=="local" and self.connection_mode is not None): raise ValueError("invalid Agent field combination")
  return self
class AgentPatch(Strict):
 name:str|None=None;enabled:bool|None=None;location:Literal["local","remote"]|None=None;connection_mode:Literal["agent_pull"]|None=None;local_executable_path:str|None=None;configured_capabilities:Caps|None=None
class Empty(Strict): pass
class Enroll(Strict): enrollment_token:str;protocol_version:int;agent_version:str;os:str;architecture:str
class Heartbeat(Strict): agent_id:str;protocol_version:int;agent_version:str;os:str;architecture:str;reported_capabilities:Caps
def svc(): return AgentService()
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
@router.post("/agent/enrollments")
def enroll(b:Enroll): return guard(lambda:no_store(svc().enroll(b.model_dump()),201))
@router.post("/agent/heartbeat")
def heartbeat(b:Heartbeat,authorization:str|None=Header(default=None)):
 raw=authorization[7:] if authorization and authorization.startswith("Bearer ") else None
 return guard(lambda:no_store(svc().heartbeat(raw,b.model_dump())))
