"""CLI transport for the same Source Bundle core used by API providers."""
from fastapi import APIRouter, HTTPException, Request

from modules.flow_gate.api.v1.snapshot_routes import _cli_context
from modules.flow_gate.services import source_bundle_access_service as core

router = APIRouter(prefix="/api/v1/source-bundles/cli", tags=["SourceBundles"])


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
