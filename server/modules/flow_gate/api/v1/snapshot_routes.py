"""Snapshot request API. Decisions deliberately accept human sessions only."""
from typing import Literal
from fastapi import APIRouter,Depends,HTTPException,Request
from pydantic import BaseModel
from modules.flow_gate.auth.middleware import get_current_user
from modules.flow_gate.services import token_service,snapshot_request_service as service
from modules.flow_gate.services import snapshot_materialization_service as materialization
from modules.flow_gate.services import snapshot_access_service as snapshot_access
from modules.flow_gate.db import snapshot_requests as db, ai_invoke_runs
router=APIRouter(prefix="/api/v1/snapshots",tags=["Snapshots"])
class RequestIn(BaseModel):
 reason:str; scope:Literal["single_file","selected_files","directory","whole_source"]
 requested_paths:list[str]; purpose:str; source_kind:Literal["current_worktree"]="current_worktree"
class RejectIn(BaseModel):
 # T0026: named after the column (documents.rejection_reason) — `reason` is already the
 # AI's own request reason on the same row.
 rejection_reason:str|None=None
def _error(exc): raise HTTPException(exc.status,detail={"code":exc.code,"message":exc.message})
@router.post("")
def request_snapshot(body:RequestIn,request:Request):
 raw=request.headers.get("Authorization","").removeprefix("Bearer ").strip()
 try: token=token_service.verify(raw)
 except Exception: raise HTTPException(403,"snapshot requests require an AI worker token")
 from modules.flow_gate.services import ai_invoke_service
 run=(ai_invoke_service.get_run_record(str(token.get("ai_run_id") or ""))
      or ai_invoke_runs.get(token.get("ai_run_id")) or {})
 try:
  service.validate_request_authority(token,run)
  data=body.model_dump()|{"project_id":token.get("project") or token.get("project_id") or run.get("project_id"),"group_id":token.get("group_id") or run.get("group_id"),"run_id":token.get("ai_run_id"),"chain_id":run.get("chain_id") or run.get("run_id"),"token_id":token.get("token_id"),"provider_id":token.get("provider_id") or run.get("provider_id")}
  return {"ok":True,"request":service.create_request(data,token["issued_to"])}
 except service.SnapshotRequestError as exc: _error(exc)
@router.get("/pending")
def pending(project_id:str|None=None,group_id:str|None=None,user=Depends(get_current_user)):
 return {"ok":True,"requests":db.list_pending(project_id,group_id)}
@router.get("/active")
def active(project_id:str|None=None,group_id:str|None=None,user=Depends(get_current_user)):
 # T0012 §13: small read-model connection this UX needs and T#1/T#2 did not expose —
 # db.list_created() already existed for T#2's own use, nothing new is written here.
 # group_id-scoped calls refresh staleness live (bounded to one group's rows) so the
 # warning reflects the current worktree, not a value cached at materialize time.
 rows=db.list_created(project_id,group_id)
 if group_id:
  rows=[materialization.refresh_stale(row["snapshot_id"],actor=user["user_id"]) for row in rows]
 return {"ok":True,"requests":rows}
@router.get("/{snapshot_id}")
def detail(snapshot_id:str,user=Depends(get_current_user)):
 try: row=materialization.refresh_stale(snapshot_id,actor=user["user_id"])
 except service.SnapshotRequestError as exc: _error(exc)
 return {"ok":True,"request":row}

@router.post("/{snapshot_id}/materialize")
def materialize_snapshot(snapshot_id:str,user=Depends(get_current_user)):
 try: row=materialization.materialize(snapshot_id,user["user_id"])
 except service.SnapshotRequestError as exc: _error(exc)
 return {"ok":True,"request":row}

@router.post("/{snapshot_id}/cleanup")
def cleanup_snapshot(snapshot_id:str,user=Depends(get_current_user)):
 try: row=materialization.cleanup(snapshot_id,user["user_id"],trigger="explicit")
 except service.SnapshotRequestError as exc: _error(exc)
 return {"ok":True,"request":row}
@router.post("/{snapshot_id}/approve")
def approve(snapshot_id:str,user=Depends(get_current_user)):
 try:
  # The human decision endpoint is the production trigger: approval is not complete
  # until the approved request has been materialized (or a precise failure is returned).
  service.decide(snapshot_id,"approved",user["user_id"])
  return {"ok":True,"request":materialization.materialize(snapshot_id,user["user_id"])}
 except service.SnapshotRequestError as exc: _error(exc)
@router.post("/{snapshot_id}/reject")
def reject(snapshot_id:str,body:RejectIn|None=None,user=Depends(get_current_user)):
 reason=body.rejection_reason if body else None
 try: return {"ok":True,"request":service.decide(snapshot_id,"rejected",user["user_id"],reason)}
 except service.SnapshotRequestError as exc: _error(exc)

def _cli_context(request:Request):
 raw=request.headers.get("Authorization","").removeprefix("Bearer ").strip()
 try: token=token_service.verify(raw)
 except Exception: raise HTTPException(403,detail={"code":"snapshot_forbidden","message":"live AI worker token required"})
 from modules.flow_gate.services import ai_invoke_service
 run=(ai_invoke_service.get_run_record(str(token.get("ai_run_id") or ""))
      or ai_invoke_runs.get(token.get("ai_run_id")) or {})
 try: service.validate_request_authority(token,run)
 except service.SnapshotRequestError as exc: _error(exc)
 return raw,token,run

@router.post("/cli/request")
def cli_request_snapshot(body:RequestIn,request:Request):
 _raw,token,run=_cli_context(request)
 data=body.model_dump()|{"project_id":run.get("project_id"),"group_id":run.get("group_id"),
  "run_id":run.get("run_id"),"chain_id":run.get("chain_id") or run.get("run_id"),
  "token_id":token.get("token_id"),"provider_id":run.get("provider_id")}
 try: row=service.create_request(data,str(token.get("issued_to") or "cli-worker"))
 except service.SnapshotRequestError as exc: _error(exc)
 return {"ok":True,"request":row,"materialized":False,"requires_human_decision":True}

@router.get("/cli/{snapshot_id}/status")
def cli_snapshot_status(snapshot_id:str,request:Request):
 _raw,_token,run=_cli_context(request)
 # Re-verifies content freshness for a created snapshot; the stored stale flag alone
 # would report a disguised edit as active with current-worktree validation allowed.
 try:
  return {"ok":True,"snapshot":snapshot_access.status_metadata(run,snapshot_id)}
 except snapshot_access.SnapshotAccessError as exc:
  raise HTTPException(exc.status,detail=exc.payload("status"))

@router.post("/cli/{snapshot_id}/materialize")
def cli_materialize_snapshot(snapshot_id:str,request:Request):
 _raw,_token,run=_cli_context(request)
 try:
  snapshot_access._authorize(run,snapshot_id)
  row=materialization.materialize(snapshot_id,run.get("issued_to") or materialization.SYSTEM_ACTOR_USER_ID)
  return {"ok":True,"snapshot":snapshot_access._metadata(row)}
 except snapshot_access.SnapshotAccessError as exc:
  raise HTTPException(exc.status,detail=exc.payload("materialize"))
 # Worker-facing: a materialize error message may be built from exception text naming the
 # live worktree or scratch path, so it passes the same scrub as every snapshot error.
 except service.SnapshotRequestError as exc:
  raise HTTPException(exc.status,detail={"code":exc.code,"message":snapshot_access.public_error_text(exc.message,snapshot_id)})
 except Exception as exc:
  raise HTTPException(500,detail={"code":"snapshot_materialize_failed","message":snapshot_access.public_error_text(
   f"snapshot materialize failed ({type(exc).__name__}: {exc})",snapshot_id)}) from exc

@router.post("/cli/{snapshot_id}/access")
def cli_access_snapshot(snapshot_id:str,body:dict,request:Request):
 _raw,_token,run=_cli_context(request)
 tool_input=dict(body); tool_input["snapshot_id"]=snapshot_id
 try:
  status,payload=snapshot_access.access(run,tool_input)
 except snapshot_access.SnapshotAccessError as exc:
  status,payload=exc.status,exc.payload(str(tool_input.get("operation") or "access"))
 if status>=400: raise HTTPException(status,detail=payload)
 return payload

@router.post("/cli/{snapshot_id}/run")
def cli_run_snapshot(snapshot_id:str,body:dict,request:Request):
 _raw,_token,run=_cli_context(request)
 tool_input=dict(body); tool_input["snapshot_id"]=snapshot_id
 try:
  status,payload=snapshot_access.execute(run,tool_input,remaining_sec=300.0,
   source_tool_calls=int(run.get("source_tool_calls") or 0),
   snapshot_reads=int(run.get("snapshot_reads") or 0))
 except snapshot_access.SnapshotAccessError as exc:
  status,payload=exc.status,exc.payload("execute")
 if status>=400: raise HTTPException(status,detail=payload)
 return payload
