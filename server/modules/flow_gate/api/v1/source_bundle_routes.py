"""CLI transport for the same Source Bundle core used by API providers.

0672 T0004: the CLI routes are one of the two remaining Bundle entry points (with
``api_server_tools``) and answer only inside the TS/TSR preserved set judged by
``source_bundle_exposure``. Authentication is this module's own; it no longer rides on
the legacy Snapshot routes.
"""
import time

from fastapi import APIRouter, Depends, HTTPException, Request

from modules.flow_gate.auth.middleware import get_current_user
from modules.flow_gate.db import ai_invoke_runs
from modules.flow_gate.db import source_bundles as db
from modules.flow_gate.services import source_bundle_access_service as core
from modules.flow_gate.services import source_bundle_exposure as exposure
from modules.flow_gate.services import token_service
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



def _forbidden(code: str, message: str):
    raise HTTPException(403, detail={"ok": False, "error": {"code": code, "message": message}})


def _cli_context(request: Request):
    """The live AI worker token and its own run; every axis must match."""
    raw = request.headers.get("Authorization", "").removeprefix("Bearer ").strip()
    try:
        token = token_service.verify(raw)
    except Exception:
        _forbidden("bundle_forbidden", "live AI worker token required")
    from modules.flow_gate.services import ai_invoke_service  # lazy -- import cycle
    run_id = str(token.get("ai_run_id") or "")
    run = (ai_invoke_service.get_run_record(run_id) or ai_invoke_runs.get(token.get("ai_run_id")) or {}) if run_id else {}
    if not run:
        _forbidden("bundle_forbidden", "a live AI run/token is required")
    axes = (
        (token.get("project") or token.get("project_id"), run.get("project_id")),
        (token.get("group_id"), run.get("group_id")),
        (token.get("ai_run_id"), run.get("run_id")),
    )
    if any(not left or str(left) != str(right or "") for left, right in axes):
        _forbidden("bundle_forbidden", "AI run/token scope does not match")
    token_id = str(token.get("token_id") or "")
    current_token_id = str(run.get("current_token_id") or run.get("token_id") or "")
    if not token_id or (current_token_id and token_id != current_token_id):
        _forbidden("bundle_forbidden", "AI token is not current for this run")
    return raw, token, run


def _require_exposure(token: dict, run: dict) -> None:
    """403 outside the TS/TSR preserved set.

    Inside the set every CLI route keeps its pre-0672 behavior: the CLI ``run`` route was
    never gated by the advertised tool level, so TS(review)/TS(new, doc_ref=R)/TSR(review)
    keep reaching ``core.execute`` exactly as before (T0004 stage 2).
    """
    level = exposure.for_doc_ref(
        str(token.get("action_scope") or run.get("action_scope") or ""),
        run.get("doc_ref") or token.get("doc_ref"),
    )
    if not exposure.exposed(level):
        _forbidden("source_bundle_not_available",
                   "Source Bundle is available only to TS/TSR steps; use the live source tools")


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
    _raw, token, run = _cli_context(request)
    _require_exposure(token, run)
    return _result(lambda: core.access(run, {"operation": "status"}), "status")


@router.get("/{bundle_id}/status")
def bundle_status(bundle_id: str, request: Request, claim_current_worktree: bool = False):
    _raw, token, run = _cli_context(request)
    _require_exposure(token, run)
    return _result(lambda: core.access(run, {"bundle_id": bundle_id, "operation": "status",
                                             "claim_current_worktree": claim_current_worktree}), "status")


@router.post("/{bundle_id}/access")
def access_bundle(bundle_id: str, body: dict, request: Request):
    _raw, token, run = _cli_context(request)
    _require_exposure(token, run)
    data = dict(body)
    data["bundle_id"] = bundle_id
    return _result(lambda: core.access(run, data), str(data.get("operation") or "access"))


@router.post("/{bundle_id}/run")
def run_bundle(bundle_id: str, body: dict, request: Request):
    _raw, token, run = _cli_context(request)
    _require_exposure(token, run)
    data = dict(body)
    data["bundle_id"] = bundle_id
    return _result(lambda: core.execute(run, data, 300.0), "execute")
