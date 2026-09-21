"""Durable snapshot request persistence. This module never materializes files."""
import json, uuid
from .connection import get_store, now_iso

def _decode(row):
    if row is None: return None
    row=dict(row)
    try: row["requested_paths"]=json.loads(row.get("requested_paths") or "[]")
    except (TypeError,ValueError): row["requested_paths"]=[]
    return row

def create(data):
    sid=data.get("snapshot_id") or "snap_"+uuid.uuid4().hex
    at=data.get("requested_at") or now_iso()
    get_store()._execute(
      "INSERT INTO snapshot_requests (snapshot_id,project_id,group_id,run_id,token_id,provider_id,reason,scope,requested_paths,purpose,source_kind,status,requested_at,source_revision,source_fingerprint) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
      [sid,data["project_id"],data["group_id"],data["run_id"],data["token_id"],data["provider_id"],data["reason"],data["scope"],json.dumps(data["requested_paths"],ensure_ascii=False),data["purpose"],data["source_kind"],"requested",at,data.get("source_revision"),data.get("source_fingerprint")])
    return get(sid)

def get(snapshot_id):
    return _decode(get_store()._fetch_one("SELECT * FROM snapshot_requests WHERE snapshot_id=?",[snapshot_id]))

def list_pending(project_id=None,group_id=None):
    where=["status='requested'"]; params=[]
    if project_id: where.append("project_id=?"); params.append(project_id)
    if group_id: where.append("group_id=?"); params.append(group_id)
    return [_decode(r) for r in get_store()._fetch_all("SELECT * FROM snapshot_requests WHERE "+" AND ".join(where)+" ORDER BY requested_at ASC",params)]

def transition(snapshot_id,decision,actor):
    if decision not in ("approved","rejected"): raise ValueError("invalid decision")
    store=get_store(); stamp=now_iso()
    tc,ac=("approved_at","approved_by") if decision=="approved" else ("rejected_at","rejected_by")
    row=get(snapshot_id)
    if row is None or row["status"]!="requested": return row,False
    changed=store._execute_affected(f"UPDATE snapshot_requests SET status=?,{tc}=?,{ac}=? WHERE snapshot_id=? AND status='requested'",[decision,stamp,actor,snapshot_id])
    return get(snapshot_id),changed==1
