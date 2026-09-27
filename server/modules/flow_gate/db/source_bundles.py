"""Source Bundle persistence and database-owned build slot."""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone

from .connection import get_store, now_iso

POLICY_VERSION = "source-bundle-v1"


def _row(value):
    return dict(value) if value is not None else None


def get(bundle_id):
    return _row(get_store()._fetch_one("SELECT * FROM source_bundles WHERE bundle_id=?", [bundle_id]))


def reusable(project_id, group_id, revision, dirty, fingerprint, now):
    return [_row(row) for row in get_store()._fetch_all(
        "SELECT * FROM source_bundles WHERE project_id=? AND group_id=? "
        "AND exclusion_policy_version=? AND status='created' AND source_revision=? "
        "AND source_dirty=? AND content_fingerprint=? AND expires_at>? "
        "ORDER BY created_at DESC",
        [project_id, group_id, POLICY_VERSION, revision, dirty, fingerprint, now],
    )]


def slot(project_id, group_id):
    return _row(get_store()._fetch_one(
        "SELECT * FROM source_bundle_builds WHERE project_id=? AND group_id=? "
        "AND exclusion_policy_version=?",
        [project_id, group_id, POLICY_VERSION],
    ))


def claim(project_id, group_id, *, lease_seconds=150):
    """Unique DB row is the cross-process owner; an expired owner is replaced by CAS."""
    owner = uuid.uuid4().hex
    bundle_id = "sb_" + uuid.uuid4().hex
    until = (datetime.now(timezone.utc) + timedelta(seconds=lease_seconds)).isoformat()
    try:
        get_store()._execute(
            "INSERT INTO source_bundle_builds "
            "(project_id,group_id,exclusion_policy_version,owner_id,bundle_id,lease_until,outcome) "
            "VALUES (?,?,?,?,?,?,'building')",
            [project_id, group_id, POLICY_VERSION, owner, bundle_id, until],
        )
        return owner, bundle_id
    except Exception:
        existing = slot(project_id, group_id)
        if existing is None:
            raise
        if existing["outcome"] == "building" and existing["lease_until"] > datetime.now(timezone.utc).isoformat():
            return None, existing["bundle_id"]
        changed = get_store()._execute_affected(
            "UPDATE source_bundle_builds SET owner_id=?,bundle_id=?,lease_until=?,"
            "outcome='building',failure_code=NULL WHERE project_id=? AND group_id=? "
            "AND exclusion_policy_version=? AND owner_id=? AND (outcome<>'building' OR lease_until<=?)",
            [owner, bundle_id, until, project_id, group_id, POLICY_VERSION,
             existing["owner_id"], datetime.now(timezone.utc).isoformat()],
        )
        return (owner, bundle_id) if changed == 1 else (None, existing["bundle_id"])


def start(bundle_id, project_id, group_id):
    get_store()._execute(
        "INSERT INTO source_bundles "
        "(bundle_id,project_id,group_id,scope,exclusion_policy_version,status,started_at) "
        "VALUES (?,?,?,'whole_source',?,'building',?)",
        [bundle_id, project_id, group_id, POLICY_VERSION, now_iso()],
    )


def created(bundle_id, owner, metadata):
    store = get_store()
    with store.transaction():
        changed = store._execute_affected(
            "UPDATE source_bundles SET status='created',source_revision=?,source_dirty=?,"
            "content_fingerprint=?,bundle_sha256=?,file_count=?,byte_size=?,"
            "created_at=?,expires_at=? WHERE bundle_id=? AND status='building'",
            [metadata["source_revision"], metadata["source_dirty"],
             metadata["content_fingerprint"], metadata["bundle_sha256"],
             metadata["file_count"], metadata["byte_size"], metadata["created_at"],
             metadata["expires_at"], bundle_id],
        )
        if changed != 1:
            raise RuntimeError("bundle lifecycle changed during publish")
        store._execute_affected(
            "UPDATE source_bundle_builds SET outcome='created' "
            "WHERE bundle_id=? AND owner_id=? AND outcome='building'",
            [bundle_id, owner],
        )
    return get(bundle_id)


def failed(bundle_id, owner, code, reason):
    store = get_store()
    with store.transaction():
        store._execute_affected(
            "UPDATE source_bundles SET status='failed',failure_code=?,failure_reason=? "
            "WHERE bundle_id=? AND status='building'", [code, reason, bundle_id],
        )
        store._execute_affected(
            "UPDATE source_bundle_builds SET outcome='failed',failure_code=? "
            "WHERE bundle_id=? AND owner_id=? AND outcome='building'",
            [code, bundle_id, owner],
        )


def deleted(bundle_id):
    return get_store()._execute_affected(
        "UPDATE source_bundles SET status='deleted',deleted_at=? "
        "WHERE bundle_id=? AND status='created'", [now_iso(), bundle_id],
    ) == 1


def record_usage(bundle_id, operation, *, run_id=None, token_id=None, document_id=None,
                 success=True, current_worktree_claim=False, detail=None):
    usage_id = "sbu_" + uuid.uuid4().hex
    get_store()._execute(
        "INSERT INTO source_bundle_usages "
        "(usage_id,bundle_id,run_id,token_id,document_id,operation,success,"
        "current_worktree_claim,detail,used_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
        [usage_id, bundle_id, run_id, token_id, document_id, operation, success,
         current_worktree_claim, json.dumps(detail, sort_keys=True) if detail is not None else None,
         now_iso()],
    )
    return usage_id


def list_usage_for_run(run_id):
    return [_row(row) for row in get_store()._fetch_all(
        "SELECT * FROM source_bundle_usages WHERE run_id=? ORDER BY used_at,usage_id", [run_id]
    )]


def attach_document(run_id, document_id):
    return get_store()._execute_affected(
        "UPDATE source_bundle_usages SET document_id=? WHERE run_id=? AND document_id IS NULL",
        [document_id, run_id],
    )
