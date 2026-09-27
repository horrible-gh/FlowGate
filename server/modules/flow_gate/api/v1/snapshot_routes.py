"""Read and cleanup transport for legacy created Snapshots."""
from fastapi import APIRouter,Depends,HTTPException,Request
from modules.flow_gate.auth.middleware import get_current_user
from modules.flow_gate.services import token_service,snapshot_request_service as service
from modules.flow_gate.services import snapshot_materialization_service as materialization
from modules.flow_gate.services import snapshot_access_service as snapshot_access
from modules.flow_gate.db import snapshot_requests as db, ai_invoke_runs
router=APIRouter(prefix="/api/v1/snapshots",tags=["Snapshots"])
def _error(exc): raise HTTPException(exc.status,detail={"code":exc.code,"message":exc.message})
@router.post("")
def request_snapshot(request:Request):
 raise HTTPException(410,detail={"code":"snapshot_feature_retired","message":"Legacy Snapshot requests are retired; use Source Bundle"})
@router.get("/pending")
def pending(project_id:str|None=None,group_id:str|None=None,user=Depends(get_current_user)):
 service.retire_unmaterialized(materialization.SYSTEM_ACTOR_USER_ID)
 return {"ok":True,"requests":[]}
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
 raise HTTPException(410,detail={"code":"snapshot_feature_retired","message":"Legacy Snapshot materialization is retired"})

@router.post("/{snapshot_id}/cleanup")
def cleanup_snapshot(snapshot_id:str,user=Depends(get_current_user)):
 try: row=materialization.cleanup(snapshot_id,user["user_id"],trigger="explicit")
 except service.SnapshotRequestError as exc: _error(exc)
 return {"ok":True,"request":row}
@router.post("/{snapshot_id}/approve")
def approve(snapshot_id:str,user=Depends(get_current_user)):
 raise HTTPException(410,detail={"code":"snapshot_feature_retired","message":"Legacy Snapshot decisions are retired"})
@router.post("/{snapshot_id}/reject")
def reject(snapshot_id:str,user=Depends(get_current_user)):
 raise HTTPException(410,detail={"code":"snapshot_feature_retired","message":"Legacy Snapshot decisions are retired"})

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
def cli_request_snapshot(request:Request):
 raise HTTPException(410,detail={"code":"snapshot_feature_retired","message":"Legacy Snapshot requests are retired; use Source Bundle"})

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
 raise HTTPException(410,detail={"code":"snapshot_feature_retired","message":"Legacy Snapshot materialization is retired"})

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
def cli_run_snapshot(snapshot_id:str,request:Request):
 raise HTTPException(410,detail={"code":"snapshot_feature_retired","message":"Legacy Snapshot execution is retired; use Source Bundle"})
