"""CLI transport for the same Source Bundle core used by API providers."""
import time

from fastapi import APIRouter, Depends, HTTPException, Request

from modules.flow_gate.api.v1.snapshot_routes import _cli_context
from modules.flow_gate.auth.middleware import get_current_user
from modules.flow_gate.db import source_bundles as db
from modules.flow_gate.services import source_bundle_access_service as core
from modules.flow_gate.services import source_bundle_service as bundles
from modules.flow_gate.services import source_bundle_materializer as materializer

router = APIRouter(prefix="/api/v1/source-bundles/cli", tags=["SourceBundles"])
overview_router = APIRouter(prefix="/api/v1/source-bundles", tags=["SourceBundles"])


@overview_router.get("")
def list_bundles(project_id: str, group_id: str, user=Depends(get_current_user)):
    """Metadata only. This endpoint never ensures or mutates a Bundle."""
    rows = db.list_recent(project_id, group_id)
    current = None
    if any(row["status"] == "created" for row in rows):
        try:
            root = materializer.resolve_worktree(project_id, group_id)
            current = materializer.inspect_source(root, time.monotonic() + materializer.BUILD_SECONDS)
        except Exception:
            pass
    result = []
    for row in rows:
        item = bundles._public(row)
        if row["status"] == "created":
            fresh = bool(current) and (
                current["source_revision"] == row["source_revision"] and
                bool(current["source_dirty"]) == bool(row["source_dirty"]) and
                current["content_fingerprint"] == row["content_fingerprint"]
            )
            item["freshness"] = "current" if fresh else "stale"
        else:
            item["freshness"] = "not_applicable"
        result.append(item)
    scratches = [{
        "scratch_key": row["scratch_key"][:16], "bundle_id": row["bundle_id"],
        "status": row["status"], "created_at": row["created_at"],
        "expires_at": row["expires_at"], "deleted_at": row["deleted_at"],
        "byte_size": row["byte_size"], "build_duration_ms": row["build_duration_ms"],
        "reuse_count": row["reuse_count"], "cleanup_attempts": row["cleanup_attempts"],
        "cleanup_state": "warning" if row["cleanup_last_error"] else row["status"],
        "cleanup_last_error": row["cleanup_last_error"],
    } for row in db.list_scratch_recent(project_id, group_id)]
    return {"ok": True, "bundles": result, "scratches": scratches}



def _result(call, operation):
    try:
        status, payload = call()
    except core.BundleAccessError as exc:
        status, payload = exc.status, exc.payload(operation)
    if status >= 400:
        raise HTTPException(status, detail=payload)
    return payload


@router.post("/ensure")
def ensure_bundle(request: Request):
    _raw, _token, run = _cli_context(request)
    return _result(lambda: core.access(run, {"operation": "status"}), "status")


@router.get("/{bundle_id}/status")
def bundle_status(bundle_id: str, request: Request, claim_current_worktree: bool = False):
    _raw, _token, run = _cli_context(request)
    return _result(lambda: core.access(run, {"bundle_id": bundle_id, "operation": "status",
                                             "claim_current_worktree": claim_current_worktree}), "status")


@router.post("/{bundle_id}/access")
def access_bundle(bundle_id: str, body: dict, request: Request):
    _raw, _token, run = _cli_context(request)
    data = dict(body)
    data["bundle_id"] = bundle_id
    return _result(lambda: core.access(run, data), str(data.get("operation") or "access"))


@router.post("/{bundle_id}/run")
def run_bundle(bundle_id: str, body: dict, request: Request):
    _raw, _token, run = _cli_context(request)
    data = dict(body)
    data["bundle_id"] = bundle_id
    return _result(lambda: core.execute(run, data, 300.0), "execute")
