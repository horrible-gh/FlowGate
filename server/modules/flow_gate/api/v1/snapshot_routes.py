"""Snapshot request API. Decisions deliberately accept human sessions only."""
from typing import Literal
from fastapi import APIRouter,Depends,HTTPException,Request
from pydantic import BaseModel
from modules.flow_gate.auth.middleware import get_current_user
from modules.flow_gate.services import token_service,snapshot_request_service as service
from modules.flow_gate.services import snapshot_materialization_service as materialization
from modules.flow_gate.db import snapshot_requests as db, ai_invoke_runs
router=APIRouter(prefix="/api/v1/snapshots",tags=["Snapshots"])
class RequestIn(BaseModel):
 reason:str; scope:Literal["single_file","selected_files","directory","whole_source"]
 requested_paths:list[str]; purpose:str; source_kind:Literal["current_worktree"]="current_worktree"
def _error(exc): raise HTTPException(exc.status,detail={"code":exc.code,"message":exc.message})
@router.post("")
def request_snapshot(body:RequestIn,request:Request):
 raw=request.headers.get("Authorization","").removeprefix("Bearer ").strip()
 try: token=token_service.verify(raw)
 except Exception: raise HTTPException(403,"snapshot requests require an AI worker token")
 run=ai_invoke_runs.get(token.get("ai_run_id")) or {}
 data=body.model_dump()|{"project_id":token.get("project") or token.get("project_id") or run.get("project_id"),"group_id":token.get("group_id") or run.get("group_id"),"run_id":token.get("ai_run_id"),"token_id":token.get("token_id"),"provider_id":token.get("provider_id") or run.get("provider_id")}
 try: return {"ok":True,"request":service.create_request(data,token["issued_to"])}
 except service.SnapshotRequestError as exc: _error(exc)
@router.get("/pending")
def pending(project_id:str|None=None,group_id:str|None=None,user=Depends(get_current_user)):
 return {"ok":True,"requests":db.list_pending(project_id,group_id)}
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
 try: return {"ok":True,"request":service.decide(snapshot_id,"approved",user["user_id"])}
 except service.SnapshotRequestError as exc: _error(exc)
@router.post("/{snapshot_id}/reject")
def reject(snapshot_id:str,user=Depends(get_current_user)):
 try: return {"ok":True,"request":service.decide(snapshot_id,"rejected",user["user_id"])}
 except service.SnapshotRequestError as exc: _error(exc)
