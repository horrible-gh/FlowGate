"""Durable snapshot request validation, transitions, and audit."""
import json
import os
import time
from pathlib import PurePosixPath, PureWindowsPath
from modules.flow_gate.db import snapshot_requests as db
from modules.flow_gate.db import ai_providers as db_ai_providers
from modules.flow_gate.db import workflow_events
from modules.flow_gate.db import documents as db_documents
from modules.flow_gate.services import tool_registry
from modules.flow_gate.services.ai_invoke.provenance import resolve_run_provenance
from modules.flow_gate.db.connection import get_store
from modules.flow_gate.api.v1.events.event_types import EventType
from modules.flow_gate.api.v1.events.publisher import FlowEvent, broadcast_event_threadsafe
SCOPES={"single_file","selected_files","directory","whole_source"}
SOURCE_KIND="current_worktree"
# Same cap the merge-review reject prompt uses for its typed reason (GitMergeRejectDialog).
REJECTION_REASON_MAX=4000
SNAPSHOT_WAIT_SECONDS=max(0.0,min(30.0,float(os.getenv("FLOWGATE_SNAPSHOT_WAIT_SECONDS","20"))))
SNAPSHOT_WAIT_SAFETY_SECONDS=5.0
SNAPSHOT_WAIT_POLL_SECONDS=0.25
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
 n["chain_id"]=str(n.get("chain_id") or "").strip() or None
 return n

def request_data_for_run(run,token,body):
 """Capture canonical run/provider evidence once for API and CLI request paths."""
 data=dict(body)
 evidence=resolve_run_provenance(run.get("run_id"),run=run)
 actual_id=evidence.get("actual_provider_id") or run.get("provider_id") or token.get("provider_id")
 actual_name=evidence.get("actual_provider_name")
 if not actual_name and actual_id:
  try:
   actual_name=(db_ai_providers.get_row(run.get("project_id"),actual_id) or {}).get("name")
  except Exception:
   pass
 data.update({
  "project_id":run.get("project_id"),"group_id":run.get("group_id"),
  "run_id":run.get("run_id"),"chain_id":run.get("chain_id") or run.get("run_id"),
  "token_id":token.get("token_id"),"provider_id":actual_id,
  "requested_provider_id":evidence.get("requested_provider_id"),
  "actual_provider_name":actual_name,
  "provider_source":evidence.get("provider_source"),
  "attempt_no":evidence.get("attempt_no"),"fallback_used":evidence.get("fallback_used"),
 })
 return data

def _meta(row):
 keys=("snapshot_id","run_id","chain_id","group_id","provider_id","requested_provider_id","actual_provider_name","provider_source","attempt_no","fallback_used","reason","purpose","scope","requested_paths","source_kind","requested_at","approved_by","rejection_reason")
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

def validate_request_authority(token: dict, run: dict) -> dict:
 if not token.get("ai_run_id") or not run:
  raise SnapshotRequestError(403,"snapshot_request_forbidden","a live AI run/token is required")
 axes=(
  ("project_id", token.get("project") or token.get("project_id"), run.get("project_id")),
  ("group_id", token.get("group_id"), run.get("group_id")),
  ("run_id", token.get("ai_run_id"), run.get("run_id")),
 )
 if any(not left or str(left)!=str(right or "") for _name,left,right in axes):
  raise SnapshotRequestError(403,"snapshot_request_forbidden","AI run/token scope does not match")
 token_id=str(token.get("token_id") or "")
 current_token_id=str(run.get("current_token_id") or run.get("token_id") or "")
 if not token_id or (current_token_id and token_id!=current_token_id):
  raise SnapshotRequestError(403,"snapshot_request_forbidden","AI token is not current for this run")
 action_scope=str(token.get("action_scope") or run.get("action_scope") or "")
 if action_scope not in {"new","edit","review","test_run"}:
  raise SnapshotRequestError(403,"snapshot_request_capability_required","request_snapshot capability is not granted")
 doc=db_documents.get_by_id(run.get("doc_ref") or token.get("doc_ref"))
 step_type=str((doc or {}).get("type_code") or (doc or {}).get("type") or "").upper()
 kind,_reason=tool_registry.kind_for_step(action_scope,step_type)
 if kind not in {"read","read_write"}:
  raise SnapshotRequestError(403,"snapshot_source_read_required","source read authority is required")
 return token

def create_request(data,actor):
 n=validate_request(data)
 def pending(): return db.pending_for_owner(n["project_id"],n["group_id"],n["chain_id"],n["run_id"])
 existing=pending()
 if existing: return existing|{"reused_pending":True}
 try:
  with get_store().transaction():
   # The migration's unique guard is the final arbiter when callers race this read.
   existing=pending()
   if existing: return existing|{"reused_pending":True}
   row=db.create(n)
   workflow_events.create({"event_type":"snapshot_requested","project_id":row["project_id"],"group_id":row["group_id"],"actor_user_id":actor,"to_state":"requested","metadata":_meta(row)})
 except Exception:
  # A competing INSERT can win after our read. Query only after rollback, especially
  # on PostgreSQL where an integrity error aborts the current transaction.
  existing=pending()
  if existing: return existing|{"reused_pending":True}
  raise
 _notify(row,"requested")
 return row|{"reused_pending":False}

def wait_for_decision(row,remaining_sec):
 """Wait within both the configured tool window and the remaining run budget."""
 budget=max(0.0,float(remaining_sec or 0)-SNAPSHOT_WAIT_SAFETY_SECONDS)
 duration=min(SNAPSHOT_WAIT_SECONDS,budget)
 deadline=time.monotonic()+duration
 while row.get("status") in {"requested","approved"}:
  left=deadline-time.monotonic()
  if left<=0: break
  time.sleep(min(SNAPSHOT_WAIT_POLL_SECONDS,left))
  row=db.get(row["snapshot_id"]) or row
 return row|{"wait_timed_out":row.get("status") in {"requested","approved"}}

def clean_rejection_reason(value):
 """The human's own text only: trimmed, never composed with server-side detail."""
 text=str(value or "").strip()
 if len(text)>REJECTION_REASON_MAX:
  raise SnapshotRequestError(422,"rejection_reason_too_long",f"rejection_reason must be at most {REJECTION_REASON_MAX} characters")
 return text or None

def decide(snapshot_id,decision,actor,rejection_reason=None):
 event="snapshot_approved" if decision=="approved" else "snapshot_rejected"
 reason=None
 if decision=="rejected":
  reason=clean_rejection_reason(rejection_reason)
  if reason is None:
   # T0026 §2: a human rejection carries a reason (FlowGate's document reject and merge
   # reject both refuse an empty one). Only a request still awaiting a decision needs it —
   # an already-decided/terminal request keeps answering exactly as before (idempotent).
   current=db.get(snapshot_id)
   if current is None: raise SnapshotRequestError(404,"not_found","snapshot request not found")
   if current.get("status")=="requested":
    raise SnapshotRequestError(422,"rejection_reason_required","a rejection reason is required")
 with get_store().transaction():
  # The approve call is left exactly as it was; only a rejection carries the reason.
  row,changed=(db.transition(snapshot_id,decision,actor,reason) if decision=="rejected"
               else db.transition(snapshot_id,decision,actor))
  if row is None: raise SnapshotRequestError(404,"not_found","snapshot request not found")
  if changed: workflow_events.create({"event_type":event,"project_id":row["project_id"],"group_id":row["group_id"],"actor_user_id":actor,"from_state":"requested","to_state":decision,"metadata":_meta(row)})
 if changed: _notify(row,decision)
 return row
