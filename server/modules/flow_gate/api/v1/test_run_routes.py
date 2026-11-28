"""Remote TS test execution endpoints."""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from modules.flow_gate.db import documents as db_docs
from modules.flow_gate.db import test_runs as db_test_runs
from modules.flow_gate.services import test_run_service
from modules.flow_gate.services import token_service
from modules.flow_gate.services.auth_outbound import verify_bearer
from modules.flow_gate.services import graph_source_hooks
from modules.flow_gate.rbac.permission_service import has_permission

router = APIRouter(prefix="/api/v1", tags=["TestRun"])


class TestRunBody(BaseModel):
    doc_id: str


def _err(exc) -> JSONResponse:
    detail = exc.detail if hasattr(exc, "detail") else {"error": "internal_error"}
    if isinstance(detail, dict):
        return JSONResponse(status_code=exc.status_code, content=detail)
    return JSONResponse(status_code=exc.status_code, content={"error": str(detail)})


@router.post("/documents/test-run")
def post_test_run(body: TestRunBody, request: Request):
    auth = verify_bearer(request)
    if isinstance(auth, JSONResponse):
        return auth
    try:
        graph_source_hooks.reject_group_source(auth, "test_run")
    except graph_source_hooks.GraphSourceError as exc:
        return JSONResponse(status_code=409, content={"ok": False, "error": exc.detail})
    doc = db_docs.get_by_id(body.doc_id)
    if doc is None:
        return JSONResponse(status_code=404, content={"error": "doc_not_found", "doc_id": body.doc_id})
    if auth.get("_is_user_jwt"):
        # UI / human-session run — the existing permission gate.
        if not test_run_service.user_can_run_tests(
            auth["issued_to"], doc.get("project_id") or "", bool(auth.get("is_admin"))
        ):
            return JSONResponse(status_code=403, content={"error": "permission_denied"})
        triggered_via = "ui"
        locale = request.headers.get("x-locale") or "ko"
    elif auth.get("action_scope") == "test_run" and auth.get("doc_ref") == body.doc_id:
        # (P §relaunch after repair): an auto-recovery repair token bound to THIS doc
        # opens the user_session wall so the unmanned chain re-fires itself without a human. Single-use
        # — consume it here so it cannot be replayed. The consumed token still carries the chain's
        # continuation fields, so a passing re-run auto-approves the TSR (_maybe_chain_auto_approve_tsr).
        # the token's own continuation_locale -- inherited hop-to-hop
        # by engine_recipe_service._emit_repair -- outranks the request header, the same priority order
        # every other continuation-token consumer in this codebase already uses (inbox_routes.py).
        locale = auth.get("continuation_locale") or request.headers.get("x-locale") or "ko"
        try:
            token_service.consume(auth["token_id"], doc.get("project_id") or "", body.doc_id)
        except Exception:
            pass
        triggered_via = "repair_token"
    else:
        return JSONResponse(status_code=403, content={"error": "user_session_required"})
    try:
        result = test_run_service.validate_and_create_run(
            doc_id=body.doc_id,
            runner_id=auth.get("issued_to") or "system",
            triggered_via=triggered_via,
            locale=locale,
        )
    except Exception as exc:
        if hasattr(exc, "status_code"):
            return _err(exc)
        raise
    return JSONResponse(status_code=202, content=result)


class TestResultsBody(BaseModel):
    doc_id: str
    results: Optional[list] = None
    junit_xml: Optional[str] = None
    source_identity: Optional[dict] = None
    replace: bool = False
    overall: Optional[str] = None
    # F81: the AI's few lines over the whole run (shown under the scenario list).
    overall_comment: Optional[str] = None


@router.post("/documents/test-results")
def post_test_results(body: TestResultsBody, request: Request):
    """Record results for an approved specification TS (0549 T0008).

    Manual, external and imported automated (JUnit XML) results share this one model.
    0684 T#3 (D#1 §3-8): a result for an automated Case the server runs is refused item by
    item (``refused_results``) -- its official result comes from a server run only.

    Same permission gate as starting a run (admin or project.run). The server maps the
    results onto the TS Case IDs, computes the overall verdict itself (a submitted
    ``overall`` is ignored), writes the TSR test report and applies the test gate.
    Plain ``def``: the synchronous DB/file work runs in the threadpool.
    """
    auth = verify_bearer(request)
    if isinstance(auth, JSONResponse):
        return auth
    try:
        graph_source_hooks.require_writable(auth, "test_run")
    except graph_source_hooks.GraphSourceError as exc:
        return JSONResponse(status_code=409, content={"ok": False, "error": exc.detail})
    if not auth.get("_is_user_jwt") and (auth.get("action_scope") != "test_run" or auth.get("doc_ref") != body.doc_id):
        return JSONResponse(status_code=403, content={"error": "test_run_scope_forbidden"})
    doc = db_docs.get_by_id(body.doc_id)
    if doc is None:
        return JSONResponse(status_code=404, content={"error": "doc_not_found", "doc_id": body.doc_id})
    allowed = (test_run_service.user_can_run_tests(
        auth["issued_to"], doc.get("project_id") or "", bool(auth.get("is_admin")))
        if auth.get("_is_user_jwt") else test_run_service.token_can_run_tests(
            auth["issued_to"], doc.get("project_id") or ""))
    if not allowed:
        return JSONResponse(status_code=403, content={"error": "permission_denied"})
    if not auth.get("_is_user_jwt") and (doc.get("type_code") != "TS" or auth.get("group_id") != doc.get("group_id")):
        return JSONResponse(status_code=403, content={"error": "test_run_scope_forbidden"})
    locale = request.headers.get("x-locale") or "ko"
    try:
        recorded = test_run_service.record_spec_results(
            doc_id=body.doc_id,
            runner_id=auth.get("issued_to") or "system",
            triggered_via="ui" if auth.get("_is_user_jwt") else "token",
            results=body.results,
            junit_xml=body.junit_xml,
            source_identity=body.source_identity,
            replace=body.replace,
            locale=locale,
            submitted_overall=body.overall,
            overall_comment=body.overall_comment,
        )
    except Exception as exc:
        if hasattr(exc, "status_code"):
            return _err(exc)
        raise
    outcome = test_run_service.finalize_spec_results(
        recorded["doc"], recorded["run"], locale=locale
    )
    return JSONResponse(
        status_code=201,
        content=test_run_service.spec_result_response(recorded["run"]["run_id"], outcome),
    )


@router.post("/documents/test-run/{run_id}/cancel")
def post_test_run_cancel(run_id: str, request: Request):
    """Immediate cancel for a running/cancelling test run (flowgate.default.0358 T0004).

    Plain `def`, like the two routes above: FastAPI runs it in the threadpool, so the
    synchronous DB/process work below never blocks the event loop
    (test_event_loop_blocking_0279.py only flags `async def` handlers).

    Same permission gate as starting a run (admin or project.run) rather than
    start-author-only — NR0003 §permissions: another operator must be able to reclaim a
    runaway execution, and start-author-only would be inconsistent with the admin
    bypass that already exists. Worker tokens (test_run/repair_token scopes) are not
    accepted here; consumed automation tokens are not widened into a cancel grant.
    """
    auth = verify_bearer(request)
    if isinstance(auth, JSONResponse):
        return auth
    try:
        graph_source_hooks.reject_group_source(auth, "test_run")
    except graph_source_hooks.GraphSourceError as exc:
        return JSONResponse(status_code=409, content={"ok": False, "error": exc.detail})
    if not auth.get("_is_user_jwt"):
        return JSONResponse(status_code=403, content={"error": "user_session_required"})
    run = db_test_runs.get_run(run_id)
    if run is None:
        return JSONResponse(status_code=404, content={"error": "run_not_found", "run_id": run_id})
    doc = db_docs.get_by_id(run.get("doc_id"))
    project_id = (doc or {}).get("project_id") or ""
    if not test_run_service.user_can_run_tests(
        auth["issued_to"], project_id, bool(auth.get("is_admin"))
    ):
        return JSONResponse(status_code=403, content={"error": "permission_denied"})
    try:
        result = test_run_service.request_cancel(run_id)
    except Exception as exc:
        if hasattr(exc, "status_code"):
            return _err(exc)
        raise
    return JSONResponse(status_code=200, content={"ok": True, **result})


@router.post("/documents/test-run-request")
def post_test_run_request(body: TestRunBody, request: Request):
    auth = verify_bearer(request)
    if isinstance(auth, JSONResponse):
        return auth
    try:
        graph_source_hooks.reject_group_source(auth, "test_run")
    except graph_source_hooks.GraphSourceError as exc:
        return JSONResponse(status_code=409, content={"ok": False, "error": exc.detail})
    if not auth.get("_is_user_jwt"):
        return JSONResponse(status_code=403, content={"error": "user_session_required"})
    doc = db_docs.get_by_id(body.doc_id)
    if doc is None:
        return JSONResponse(status_code=404, content={"error": "doc_not_found", "doc_id": body.doc_id})
    if not test_run_service.user_can_run_tests(
        auth["issued_to"], doc.get("project_id") or "", bool(auth.get("is_admin"))
    ):
        return JSONResponse(status_code=403, content={"error": "permission_denied"})
    try:
        result = test_run_service.issue_test_run_request(
            doc_id=body.doc_id,
            issued_to=auth["issued_to"],
            api_base_url=_build_api_base(request),
        )
    except LookupError as exc:
        _, _, doc_id = str(exc).partition(":")
        return JSONResponse(status_code=404, content={"error": "doc_not_found", "doc_id": doc_id})
    except ValueError as exc:
        return JSONResponse(status_code=400, content={"error": str(exc)})
    return JSONResponse(status_code=201, content=result)


def _build_api_base(request: Request) -> str:
    from config import settings

    base = str(request.base_url).rstrip("/")
    context = settings.CONTEXT.rstrip("/")
    return f"{base}{context}/api/v1"


@router.post("/documents/{ts_id}/test-spec/runs")
def post_spec_run(ts_id: str, request: Request):
    return _post_spec_execution(ts_id, None, request)


@router.post("/documents/{ts_id}/test-spec/cases/{case_id}/run")
def post_spec_case_run(ts_id: str, case_id: str, request: Request):
    return _post_spec_execution(ts_id, case_id, request)


def _post_spec_execution(ts_id: str, case_id: Optional[str], request: Request):
    with graph_source_hooks.write_session():
        return _post_spec_execution_in_session(ts_id, case_id, request)


def _post_spec_execution_in_session(ts_id: str, case_id: Optional[str], request: Request):
    auth = verify_bearer(request)
    if isinstance(auth, JSONResponse):
        return auth
    try:
        graph_source_hooks.require_writable(auth, "test_run")
    except graph_source_hooks.GraphSourceError as exc:
        return JSONResponse(status_code=409, content={"ok": False, "error": exc.detail})
    doc = db_docs.get_by_id(ts_id)
    if doc is None:
        return JSONResponse(status_code=404, content={"error": "doc_not_found", "doc_id": ts_id})
    if not auth.get("_is_user_jwt") and (
        case_id is None or doc.get("type_code") != "TS"
        or auth.get("action_scope") != "test_run" or auth.get("doc_ref") != ts_id
        or not doc.get("group_id") or auth.get("group_id") != doc["group_id"]
    ):
        return JSONResponse(status_code=403, content={"error": "test_run_scope_forbidden"})
    allowed = (test_run_service.user_can_run_tests(
        auth["issued_to"], doc.get("project_id") or "", bool(auth.get("is_admin")))
        if auth.get("_is_user_jwt") else test_run_service.token_can_run_tests(
            auth["issued_to"], doc.get("project_id") or ""))
    if not allowed:
        return JSONResponse(status_code=403, content={"error": "permission_denied"})
    from modules.flow_gate.services import spec_execution_service
    try:
        if not auth.get("_is_user_jwt") and graph_source_hooks.binding(auth):
            graph_source_hooks.source_root(auth)  # fail closed before admission
        result = spec_execution_service.admit(
            ts_id, case_id=case_id, runner_id=auth.get("issued_to") or "system",
            locale=request.headers.get("x-locale") or "ko",
            triggered_via="ui" if auth.get("_is_user_jwt") else "token",
            execution_token=auth if not auth.get("_is_user_jwt") else None,
        )
    except Exception as exc:
        if hasattr(exc, "status_code"):
            return _err(exc)
        raise
    return JSONResponse(status_code=202, content=result)


class TestRemarkBody(BaseModel):
    """A new remark on a case. ``phase`` is ``case_write`` (default) or ``test_run``."""

    text: str
    phase: Optional[str] = None


class TestRemarkUpdateBody(BaseModel):
    """Resolve / open again (``state``: ``open`` | ``resolved``) and / or the AI's one-line answer."""

    state: Optional[str] = None
    ai_reply: Optional[str] = None


def _remark_auth(ts_id: str, request: Request, permission: str):
    auth = verify_bearer(request)
    if isinstance(auth, JSONResponse):
        return auth
    doc = db_docs.get_by_id(ts_id)
    if doc is None:
        return JSONResponse(status_code=404, content={"error": "doc_not_found", "doc_id": ts_id})
    if not auth.get("_is_user_jwt") and (
        doc.get("type_code") != "TS" or auth.get("doc_ref") != ts_id
        or not doc.get("group_id") or auth.get("group_id") != doc["group_id"]
        or auth.get("action_scope") not in {"edit", "test_run"}
    ):
        return JSONResponse(status_code=403, content={"error": "remark_scope_forbidden"})
    if not (bool(auth.get("is_admin"))
            or has_permission(auth["issued_to"], doc.get("project_id") or "", permission)):
        return JSONResponse(status_code=403, content={"error": "permission_denied"})
    return auth


def _remark_author_name(auth: dict) -> str:
    if not auth.get("_is_user_jwt"):
        from modules.flow_gate.services.ai_invoke.provenance import resolve_run_provenance
        provenance = resolve_run_provenance(auth.get("ai_run_id"), doc_id=None,
                                           allowed_action_scopes=("edit", "test_run"))
        return str(provenance.get("actual_provider_name") or "AI")
    from modules.flow_gate.db import users as db_users

    user = db_users.get_by_id(auth.get("issued_to") or "") or {}
    return str(user.get("display_name") or user.get("name") or user.get("username")
               or auth.get("issued_to") or "")


@router.get("/documents/{ts_id}/test-spec/remarks")
def get_spec_remarks(ts_id: str, request: Request, case_id: Optional[str] = None):
    """Remarks of a scenario: ``{ok, doc_id, remarks: [...], counts: {case: {open, resolved}}}``."""
    auth = _remark_auth(ts_id, request, "perm_document_read")
    if isinstance(auth, JSONResponse):
        return auth
    from modules.flow_gate.services import test_remark_service
    try:
        return {"ok": True, **test_remark_service.list_remarks(ts_id, case_id)}
    except Exception as exc:
        if hasattr(exc, "status_code"):
            return _err(exc)
        raise


@router.post("/documents/{ts_id}/test-spec/cases/{case_id}/remarks")
def post_spec_remark(ts_id: str, case_id: str, body: TestRemarkBody, request: Request):
    """A person's remark on a case -> 201 ``{ok, remark}``. Nothing is locked or approved by it."""
    auth = _remark_auth(ts_id, request, "perm_document_update")
    if isinstance(auth, JSONResponse):
        return auth
    from modules.flow_gate.services import test_remark_service
    try:
        remark = test_remark_service.add_remark(
            ts_id, case_id, {"text": body.text, **({"phase": body.phase} if body.phase else {})},
            author_kind="human" if auth.get("_is_user_jwt") else "ai", author_name=_remark_author_name(auth))
    except Exception as exc:
        if hasattr(exc, "status_code"):
            return _err(exc)
        raise
    return JSONResponse(status_code=201, content={"ok": True, "remark": remark})


@router.patch("/documents/{ts_id}/test-spec/remarks/{remark_id}")
def patch_spec_remark(ts_id: str, remark_id: str, body: TestRemarkUpdateBody, request: Request):
    """Resolve a remark or open it again -> ``{ok, remark}``."""
    auth = _remark_auth(ts_id, request, "perm_document_update")
    if isinstance(auth, JSONResponse):
        return auth
    from modules.flow_gate.services import test_remark_service
    try:
        remark = test_remark_service.update_remark(
            ts_id, remark_id, body.model_dump(exclude_none=True),
            resolved_by=_remark_author_name(auth))
    except Exception as exc:
        if hasattr(exc, "status_code"):
            return _err(exc)
        raise
    return {"ok": True, "remark": remark}


class TestAssetWriteBody(BaseModel):
    expected_hash: str
    content: str


def _test_asset_auth(ts_id: str, request: Request):
    auth = verify_bearer(request)
    if isinstance(auth, JSONResponse):
        return auth
    try:
        graph_source_hooks.reject_group_source(auth, "test_run")
    except graph_source_hooks.GraphSourceError as exc:
        return JSONResponse(status_code=409, content={"ok": False, "error": exc.detail})
    if not auth.get("_is_user_jwt"):
        return JSONResponse(status_code=403, content={"error": "user_session_required"})
    doc = db_docs.get_by_id(ts_id)
    if doc is None:
        return JSONResponse(status_code=404, content={"error": "doc_not_found", "doc_id": ts_id})
    if not test_run_service.user_can_run_tests(
        auth["issued_to"], doc.get("project_id") or "", bool(auth.get("is_admin"))
    ):
        return JSONResponse(status_code=403, content={"error": "permission_denied"})
    return auth


@router.get("/documents/{ts_id}/test-spec/assets")
def get_spec_assets(ts_id: str, request: Request):
    auth = _test_asset_auth(ts_id, request)
    if isinstance(auth, JSONResponse):
        return auth
    from modules.flow_gate.services import test_asset_service
    try:
        return test_asset_service.manifest(ts_id)
    except Exception as exc:
        if hasattr(exc, "status_code"):
            return _err(exc)
        raise


@router.get("/documents/{ts_id}/test-spec/assets/{path:path}")
def get_spec_asset(ts_id: str, path: str, request: Request):
    auth = _test_asset_auth(ts_id, request)
    if isinstance(auth, JSONResponse):
        return auth
    from modules.flow_gate.services import test_asset_service
    try:
        return test_asset_service.content(ts_id, path)
    except Exception as exc:
        if hasattr(exc, "status_code"):
            return _err(exc)
        raise


@router.put("/documents/{ts_id}/test-spec/assets/{path:path}")
def put_spec_asset(ts_id: str, path: str, body: TestAssetWriteBody, request: Request):
    auth = _test_asset_auth(ts_id, request)
    if isinstance(auth, JSONResponse):
        return auth
    from modules.flow_gate.services import test_asset_service
    try:
        return test_asset_service.update(
            ts_id, path, expected_hash=body.expected_hash, content=body.content,
            actor_id=auth["issued_to"], locale=request.headers.get("x-locale") or "ko",
        )
    except Exception as exc:
        if hasattr(exc, "status_code"):
            return _err(exc)
        raise


# Evidence uses the same project permissions and the worker's exact document binding.
def _evidence_auth(ts_id, request, write=False):
    from fastapi import HTTPException
    auth = verify_bearer(request)
    if isinstance(auth, JSONResponse):
        return auth
    doc = db_docs.get_by_id(ts_id)
    if not doc or doc.get("type_code") != "TS":
        raise HTTPException(404, detail={"error": "doc_not_found"})
    if not auth.get("_is_user_jwt"):
        if (auth.get("doc_ref") != ts_id or auth.get("group_id") != doc.get("group_id")
                or auth.get("action_scope") not in ("test_run", "edit")):
            raise HTTPException(403, detail={"error": "evidence_scope_forbidden"})
        # This grants only evidence on the exact bound TS, never general source access.
    permission = "perm_document_update" if write else "perm_document_read"
    if not (auth.get("is_admin") or has_permission(auth.get("issued_to"), doc["project_id"], permission)):
        raise HTTPException(403, detail={"error": "permission_denied"})
    return doc, {"user_id": auth.get("issued_to"), "is_admin": bool(auth.get("is_admin"))}


@router.get("/documents/{ts_id}/test-spec/evidence")
def get_test_evidence(ts_id: str, request: Request, case_id: Optional[str] = None, kind: Optional[str] = None):
    from modules.flow_gate.services import test_evidence_service as evidence
    auth = _evidence_auth(ts_id, request)
    if isinstance(auth, JSONResponse):
        return auth
    return evidence.listing(auth[0], case_id, kind)


from fastapi import File, Form, UploadFile


@router.post("/documents/{ts_id}/test-spec/cases/{case_id}/evidence", status_code=201)
def post_test_evidence(ts_id: str, case_id: str, request: Request,
                       file: UploadFile = File(...), kind: str = Form(...), attempt: int = Form(...)):
    from modules.flow_gate.services import test_evidence_service as evidence
    from modules.flow_gate.documents.attachments.flow_routes import invoke
    auth = _evidence_auth(ts_id, request, write=True)
    if isinstance(auth, JSONResponse):
        return auth
    return invoke(lambda: evidence.upload(auth[0], case_id, kind, file, auth[1], attempt))


@router.get("/documents/{ts_id}/test-spec/evidence/{key}/preview")
def preview_test_evidence(ts_id: str, key: str, request: Request):
    from modules.flow_gate.services import test_evidence_service as evidence
    from modules.flow_gate.documents.attachments.flow_routes import invoke
    auth = _evidence_auth(ts_id, request)
    if isinstance(auth, JSONResponse):
        return auth
    return invoke(lambda: evidence.preview(auth[0], key))


@router.get("/documents/{ts_id}/test-spec/evidence/{key}/download")
def download_test_evidence(ts_id: str, key: str, request: Request):
    from fastapi.responses import FileResponse
    from modules.flow_gate.services import test_evidence_service as evidence
    from modules.flow_gate.documents.attachments import service
    from modules.flow_gate.documents.attachments.flow_routes import invoke
    auth = _evidence_auth(ts_id, request)
    if isinstance(auth, JSONResponse):
        return auth
    def download():
        row = evidence.file_row(auth[0], key)
        path, meta = service.resolve_download(row["doc_id"], row["filename"])
        return FileResponse(path, media_type=meta["media_type"], headers=meta["headers"])
    return invoke(download)
