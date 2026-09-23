"""Durable snapshot request validation, transitions, and audit."""
import json
from pathlib import PurePosixPath, PureWindowsPath
from modules.flow_gate.db import snapshot_requests as db
from modules.flow_gate.db import workflow_events
from modules.flow_gate.db.connection import get_store
from modules.flow_gate.api.v1.events.event_types import EventType
from modules.flow_gate.api.v1.events.publisher import FlowEvent, broadcast_event_threadsafe
SCOPES={"single_file","selected_files","directory","whole_source"}
SOURCE_KIND="current_worktree"
class SnapshotRequestError(ValueError):
 def __init__(self,status,code,message): self.status,self.code,self.message=status,code,message; super().__init__(message)

def validate_request(data):
 n=dict(data)
 for key in ("project_id","group_id","run_id","token_id","provider_id","reason","purpose"):
  n[key]=str(n.get(key) or "").strip()
  if not n[key]: raise SnapshotRequestError(422,"missing_field",key+" is required")
 if n.get("scope") not in SCOPES: raise SnapshotRequestError(422,"invalid_scope","unsupported scope")
 if n.get("source_kind",SOURCE_KIND)!=SOURCE_KIND: raise SnapshotRequestError(422,"invalid_source_kind","only current_worktree is supported")
 paths=n.get("requested_paths")
 if not isinstance(paths,list) or any(not isinstance(p,str) or not p.strip() for p in paths): raise SnapshotRequestError(422,"invalid_paths","requested_paths must be a string array")
 clean=[]
 for raw in paths:
  value=raw.replace("\\","/").strip(); path=PurePosixPath(value); windows=PureWindowsPath(value)
  if (path.is_absolute() or windows.is_absolute() or windows.drive or value.startswith("//")
      or "\x00" in value or any(ord(ch)<32 for ch in value)
      or any(part in ("",".","..") or ":" in part for part in value.split("/"))):
   raise SnapshotRequestError(422,"invalid_paths","paths must be normal worktree-relative paths")
  clean.append(path.as_posix().rstrip("/"))
 scope=n["scope"]
 if scope in ("single_file","directory") and len(clean)!=1: raise SnapshotRequestError(422,"invalid_scope_paths",scope+" requires one path")
 if scope=="selected_files" and not clean: raise SnapshotRequestError(422,"invalid_scope_paths","selected_files requires paths")
 if scope=="whole_source" and clean: raise SnapshotRequestError(422,"invalid_scope_paths","whole_source must not include paths")
 n["requested_paths"]=list(dict.fromkeys(clean)); n["source_kind"]=SOURCE_KIND
 return n

def _meta(row):
 keys=("snapshot_id","run_id","group_id","provider_id","reason","purpose","scope","requested_paths","source_kind","requested_at","approved_by")
 return json.dumps({k:row.get(k) for k in keys},ensure_ascii=False,sort_keys=True)

def _notify(row,status):
 # T0012 §12: a best-effort "go re-read the durable list" signal only — never the
 # list itself (D0007 §4.1). Broadcast, not user-targeted: any reviewer's Pending
 # panel for this project should refresh, not just the requester's.
 try:
  broadcast_event_threadsafe(FlowEvent(
   event_type=EventType.SNAPSHOT_REQUEST_UPDATED,
   payload={"snapshot_id":row["snapshot_id"],"group_id":row["group_id"],"status":status},
   audience="*",project=row["project_id"],group_id=row["group_id"],
  ))
 except Exception:
  pass

def create_request(data,actor):
 n=validate_request(data)
 with get_store().transaction():
  row=db.create(n)
  workflow_events.create({"event_type":"snapshot_requested","project_id":row["project_id"],"group_id":row["group_id"],"actor_user_id":actor,"to_state":"requested","metadata":_meta(row)})
 _notify(row,"requested")
 return row

def decide(snapshot_id,decision,actor):
 event="snapshot_approved" if decision=="approved" else "snapshot_rejected"
 with get_store().transaction():
  row,changed=db.transition(snapshot_id,decision,actor)
  if row is None: raise SnapshotRequestError(404,"not_found","snapshot request not found")
  if changed: workflow_events.create({"event_type":event,"project_id":row["project_id"],"group_id":row["group_id"],"actor_user_id":actor,"from_state":"requested","to_state":decision,"metadata":_meta(row)})
 if changed: _notify(row,decision)
 return row
