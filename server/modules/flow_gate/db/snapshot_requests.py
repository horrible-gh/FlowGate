"""Durable snapshot request and lifecycle persistence."""
import json
import uuid

from .connection import get_store, now_iso


def _decode(row):
    if row is None:
        return None
    row = dict(row)
    try:
        row["requested_paths"] = json.loads(row.get("requested_paths") or "[]")
    except (TypeError, ValueError):
        row["requested_paths"] = []
    for key in ("stale", "cleanup_failed"):
        if key in row:
            row[key] = bool(row.get(key))
    for key in ("cleanup_attempts", "copied_file_count", "copied_byte_size"):
        if key in row and row.get(key) is not None:
            row[key] = int(row[key])
    return row


def create(data):
    sid = data.get("snapshot_id") or "snap_" + uuid.uuid4().hex
    at = data.get("requested_at") or now_iso()
    get_store()._execute(
        "INSERT INTO snapshot_requests "
        "(snapshot_id,project_id,group_id,run_id,chain_id,token_id,provider_id,reason,scope,"
        "requested_paths,purpose,source_kind,status,requested_at,source_revision,source_fingerprint) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        [
            sid, data["project_id"], data["group_id"], data["run_id"], data.get("chain_id"),
            data["token_id"], data["provider_id"], data["reason"], data["scope"],
            json.dumps(data["requested_paths"], ensure_ascii=False), data["purpose"],
            data["source_kind"], "requested", at, data.get("source_revision"),
            data.get("source_fingerprint"),
        ],
    )
    return get(sid)


def get(snapshot_id):
    return _decode(get_store()._fetch_one(
        "SELECT * FROM snapshot_requests WHERE snapshot_id=?", [snapshot_id]
    ))


def list_pending(project_id=None, group_id=None):
    where = ["status='requested'"]
    params = []
    if project_id:
        where.append("project_id=?")
        params.append(project_id)
    if group_id:
        where.append("group_id=?")
        params.append(group_id)
    return [
        _decode(row) for row in get_store()._fetch_all(
            "SELECT * FROM snapshot_requests WHERE " + " AND ".join(where)
            + " ORDER BY requested_at ASC",
            params,
        )
    ]


def list_created(project_id=None, group_id=None, run_id=None, *, include_lineage=True):
    where = ["status='created'"]
    if not include_lineage:
        where.append("(chain_id IS NULL OR chain_id='')")
    params = []
    for column, value in (
        ("project_id", project_id), ("group_id", group_id), ("run_id", run_id)
    ):
        if value:
            where.append(f"{column}=?")
            params.append(value)
    return [
        _decode(row) for row in get_store()._fetch_all(
            "SELECT * FROM snapshot_requests WHERE " + " AND ".join(where)
            + " ORDER BY created_at ASC",
            params,
        )
    ]


def list_materialization_candidates():
    return [
        _decode(row) for row in get_store()._fetch_all(
            "SELECT * FROM snapshot_requests WHERE status IN ('approved','created') "
            "ORDER BY requested_at ASC",
            [],
        )
    ]


def transition(snapshot_id, decision, actor, rejection_reason=None):
    if decision not in ("approved", "rejected"):
        raise ValueError("invalid decision")
    store = get_store()
    stamp = now_iso()
    columns = (
        ["approved_at", "approved_by"]
        if decision == "approved"
        else ["rejected_at", "rejected_by", "rejection_reason"]
    )
    values = [stamp, actor] + ([rejection_reason] if decision == "rejected" else [])
    row = get(snapshot_id)
    if row is None or row["status"] != "requested":
        # Terminal/decided rows keep their first decision, reason included — a repeated
        # reject never overwrites the reason the first human wrote.
        return row, False
    changed = store._execute_affected(
        "UPDATE snapshot_requests SET status=?," + ",".join(f"{c}=?" for c in columns)
        + " WHERE snapshot_id=? AND status='requested'",
        [decision, *values, snapshot_id],
    )
    return get(snapshot_id), changed == 1


def list_unmaterialized(*, run_id=None, group_id=None, include_lineage=True):
    """List lifecycle-close candidates so callers can lock each snapshot first."""
    where = ["status IN ('requested','approved')"]
    if not include_lineage:
        where.append("(chain_id IS NULL OR chain_id='')")
    params = []
    if run_id:
        where.append("run_id=?")
        params.append(run_id)
    if group_id:
        where.append("group_id=?")
        params.append(group_id)
    return [
        _decode(row) for row in get_store()._fetch_all(
            "SELECT * FROM snapshot_requests WHERE " + " AND ".join(where)
            + " ORDER BY requested_at ASC",
            params,
        )
    ]


def close_unmaterialized_for_run(run_id, actor):
    """Close legacy run-bound rows; lineage capabilities survive for a successor."""
    stamp = now_iso()
    rows = list_unmaterialized(run_id=run_id, include_lineage=False)
    get_store()._execute_affected(
        "UPDATE snapshot_requests SET status='rejected',rejected_at=?,rejected_by=? "
        "WHERE run_id=? AND status IN ('requested','approved') "
        "AND (chain_id IS NULL OR chain_id='')",
        [stamp, actor, run_id],
    )
    return [get(row["snapshot_id"]) for row in rows]


def close_unmaterialized_for_group(group_id, actor):
    """Group-terminal equivalent of close_unmaterialized_for_run."""
    stamp = now_iso()
    rows = list_unmaterialized(group_id=group_id)
    get_store()._execute_affected(
        "UPDATE snapshot_requests SET status='rejected',rejected_at=?,rejected_by=? "
        "WHERE group_id=? AND status IN ('requested','approved')",
        [stamp, actor, group_id],
    )
    return [get(row["snapshot_id"]) for row in rows]


def mark_created(snapshot_id, created_at, expires_at, revision, fingerprint,
                 copied_file_count, copied_byte_size):
    changed = get_store()._execute_affected(
        "UPDATE snapshot_requests SET status='created',created_at=?,expires_at=?,"
        "source_revision=?,source_fingerprint=?,copied_file_count=?,copied_byte_size=?,"
        "stale=?,stale_detected_at=NULL,cleanup_failed=?,cleanup_attempts=0,"
        "cleanup_last_error=NULL,cleanup_next_at=NULL,failure_code=NULL,failure_reason=NULL "
        "WHERE snapshot_id=? AND status='approved'",
        [
            created_at, expires_at, revision, fingerprint, copied_file_count,
            copied_byte_size, False, False, snapshot_id,
        ],
    )
    return get(snapshot_id), changed == 1


def mark_failed(snapshot_id, code, reason):
    changed = get_store()._execute_affected(
        "UPDATE snapshot_requests SET status='failed',failure_code=?,failure_reason=? "
        "WHERE snapshot_id=? AND status='approved'",
        [code, reason, snapshot_id],
    )
    return get(snapshot_id), changed == 1


def mark_stale(snapshot_id, detected_at):
    changed = get_store()._execute_affected(
        "UPDATE snapshot_requests SET stale=?,stale_detected_at=? "
        "WHERE snapshot_id=? AND status='created' AND (stale=? OR stale IS NULL)",
        [True, detected_at, snapshot_id, False],
    )
    return get(snapshot_id), changed == 1


def mark_cleanup_failed(snapshot_id, reason, next_at):
    get_store()._execute_affected(
        "UPDATE snapshot_requests SET cleanup_failed=?,"
        "cleanup_attempts=COALESCE(cleanup_attempts,0)+1,"
        "cleanup_last_error=?,cleanup_next_at=? "
        "WHERE snapshot_id=? AND status='created'",
        [True, reason, next_at, snapshot_id],
    )
    return get(snapshot_id)


def mark_deleted(snapshot_id, deleted_at):
    changed = get_store()._execute_affected(
        "UPDATE snapshot_requests SET status='deleted',deleted_at=?,cleanup_failed=?,"
        "cleanup_last_error=NULL,cleanup_next_at=NULL "
        "WHERE snapshot_id=? AND status='created'",
        [deleted_at, False, snapshot_id],
    )
    return get(snapshot_id), changed == 1
