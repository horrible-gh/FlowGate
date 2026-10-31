"""TR Self-check resource endpoints; all identity comes from the document/token."""
from __future__ import annotations

from fastapi import APIRouter, Body, Query, Request
from fastapi.responses import JSONResponse

from modules.flow_gate.services.auth_outbound import verify_bearer
from modules.flow_gate.rbac.permission_service import has_permission
from modules.flow_gate.services import tr_self_check_service as selfcheck

router = APIRouter(prefix="/api/v1/documents", tags=["TR Self-check"])


def _error(exc: selfcheck.SelfCheckError) -> JSONResponse:
    # 0080 T#2: already_running also carries the holder's summary (details.run) when known.
    return JSONResponse(status_code=exc.status, content={"ok": False,
        "error": {"code": exc.code, "message": exc.code,
                  "details": {"self_check_run_id": exc.detail, **exc.details}
                  if exc.code == "selfcheck_already_running" else exc.details}})


def _baseline_arg(baseline_run_id: str | None) -> dict:
    """0114 T#3: the optional ``baseline_run_id`` query, passed on only when it is named."""
    return {"baseline_run_id": baseline_run_id} if baseline_run_id else {}


def _forbidden() -> JSONResponse:
    return JSONResponse(status_code=403, content={"ok": False,
        "error": {"code": "forbidden", "message": "forbidden", "details": {}}})


def _auth(request: Request, doc_id: str, mutate: bool):
    # 0110 T#3: a Graph attempt token is no longer refused here; its run executes in its attempt
    # tree (tr_self_check_service.start -> _attempt_target), never in the group worktree.
    auth = verify_bearer(request)
    if isinstance(auth, JSONResponse):
        return auth
    try:
        doc = selfcheck._document(doc_id)
    except selfcheck.SelfCheckError as exc:
        return _error(exc)
    if auth.get("_is_user_jwt"):
        permission = "perm_document_update" if mutate else "perm_document_read"
        if not has_permission(auth.get("issued_to"), doc["project_id"], permission):
            return _forbidden()
    else:
        scope = auth.get("action_scope")
        scope_allowed = scope == "edit" if mutate else scope in {"edit", "review"}
        if not (scope_allowed and auth.get("doc_ref") == doc_id
                and auth.get("group_id") == doc["group_id"] and auth.get("project") == doc["project_id"]):
            return _forbidden()
    return auth


@router.post("/{tr_doc_id}/self-check/runs")
def start_run(request: Request, tr_doc_id: str, body: dict = Body(...)):
    auth = _auth(request, tr_doc_id, True)
    if isinstance(auth, JSONResponse):
        return auth
    try:
        row = selfcheck.start(tr_doc_id, body, auth.get("issued_to"), auth)
        return JSONResponse(status_code=202, content={"ok": True, **row})
    except selfcheck.SelfCheckError as exc:
        return _error(exc)


@router.get("/{tr_doc_id}/self-check/runs")
def list_runs(request: Request, tr_doc_id: str, limit: int = Query(20, ge=1, le=100)):
    auth = _auth(request, tr_doc_id, False)
    if isinstance(auth, JSONResponse):
        return auth
    try:
        return {"ok": True, "runs": selfcheck.list_runs(tr_doc_id, limit)}
    except selfcheck.SelfCheckError as exc:
        return _error(exc)


@router.get("/{tr_doc_id}/self-check/runs/{run_id}")
def read_run(request: Request, tr_doc_id: str, run_id: str,
             baseline_run_id: str | None = Query(None, min_length=1, max_length=191)):
    # 0114 T#3 (D 5): a named baseline run adds its reuse judgement to the read (advisory).
    auth = _auth(request, tr_doc_id, False)
    if isinstance(auth, JSONResponse):
        return auth
    try:
        return {"ok": True, **selfcheck.read(tr_doc_id, run_id, **_baseline_arg(baseline_run_id))}
    except selfcheck.SelfCheckError as exc:
        return _error(exc)


@router.post("/{tr_doc_id}/self-check/runs/{run_id}/cancel")
def cancel_run(request: Request, tr_doc_id: str, run_id: str):
    auth = _auth(request, tr_doc_id, True)
    if isinstance(auth, JSONResponse):
        return auth
    try:
        return {"ok": True, **selfcheck.cancel(tr_doc_id, run_id)}
    except selfcheck.SelfCheckError as exc:
        return _error(exc)


# ── 0638 T#1: Self-check before the TR exists ────────────────────────────────
# A TR(new) worker has no TR id to put in the path above, so its runs live here, owned by
# the token itself (tr_self_check_service.draft_target). The token is the only identity:
# nothing in the path or body names a document. Registering the TR links these runs to it,
# after which they are read through the TR-bound routes above. A console user can list,
# read and cancel a group's unlinked draft runs but never start one (no owner token).
draft_router = APIRouter(prefix="/api/v1/self-check/draft", tags=["TR Self-check"])


def _draft_token(request: Request):
    """The verified TR(new) owner token, the user JWT as-is, or the error response."""
    auth = verify_bearer(request)
    if isinstance(auth, JSONResponse) or auth.get("_is_user_jwt"):
        return auth
    try:
        selfcheck.draft_target(auth)
    except selfcheck.SelfCheckError as exc:
        return _forbidden() if exc.status == 403 else _error(exc)
    return auth


def _draft_user_row(auth: dict, run_id: str, mutate: bool):
    row = selfcheck.group_draft_run(run_id)
    if row is None:
        return _error(selfcheck.SelfCheckError(404, "selfcheck_run_not_found"))
    permission = "perm_document_update" if mutate else "perm_document_read"
    if not has_permission(auth.get("issued_to"), row["project_id"], permission):
        return _forbidden()
    return row


@draft_router.post("/runs")
def start_draft_run(request: Request, body: dict = Body(...)):
    auth = _draft_token(request)
    if isinstance(auth, JSONResponse):
        return auth
    if auth.get("_is_user_jwt"):
        return _forbidden()
    try:
        return JSONResponse(status_code=202, content={"ok": True, **selfcheck.start_draft(auth, body)})
    except selfcheck.SelfCheckError as exc:
        return _error(exc)


@draft_router.get("/runs")
def list_draft_runs(request: Request, limit: int = Query(20, ge=1, le=100), group_id: str | None = Query(None)):
    auth = _draft_token(request)
    if isinstance(auth, JSONResponse):
        return auth
    try:
        if not auth.get("_is_user_jwt"):
            return {"ok": True, "runs": selfcheck.list_draft_runs(auth, limit)}
        if not group_id:
            return _error(selfcheck.SelfCheckError(422, "selfcheck_invalid_request"))
        project_id, runs = selfcheck.list_group_draft_runs(group_id, limit)
        if not has_permission(auth.get("issued_to"), project_id, "perm_document_read"):
            return _forbidden()
        return {"ok": True, "runs": runs}
    except selfcheck.SelfCheckError as exc:
        return _error(exc)


@draft_router.get("/runs/{run_id}")
def read_draft_run(request: Request, run_id: str,
                   baseline_run_id: str | None = Query(None, min_length=1, max_length=191)):
    auth = _draft_token(request)
    if isinstance(auth, JSONResponse):
        return auth
    try:
        if not auth.get("_is_user_jwt"):
            return {"ok": True, **selfcheck.read_draft(auth, run_id, **_baseline_arg(baseline_run_id))}
        row = _draft_user_row(auth, run_id, False)
        if isinstance(row, JSONResponse):
            return row
        if baseline_run_id is None:
            return {"ok": True, **selfcheck.public(row)}
        return {"ok": True, **selfcheck._with_baseline(row, baseline_run_id)}
    except selfcheck.SelfCheckError as exc:
        return _error(exc)


@draft_router.post("/runs/{run_id}/cancel")
def cancel_draft_run(request: Request, run_id: str):
    auth = _draft_token(request)
    if isinstance(auth, JSONResponse):
        return auth
    try:
        if not auth.get("_is_user_jwt"):
            return {"ok": True, **selfcheck.cancel_draft(auth, run_id)}
        row = _draft_user_row(auth, run_id, True)
        if isinstance(row, JSONResponse):
            return row
        return {"ok": True, **selfcheck.cancel_group_draft(run_id)}
    except selfcheck.SelfCheckError as exc:
        return _error(exc)


# ── 0080 T#2: who holds the Group's run slot (NR0007 §5 item 1) ──────────────
# The active-run slot is per Group, so a start can be refused for another TR's run or a
# draft run its caller can neither read nor cancel on the routes above. These two routes
# let any worker token of that Group (new / edit / review) and a console user with read
# permission see the holder's summary. Read only: cancelling stays with the owning TR's
# edit token, the owning draft token, or a user with perm_document_update.
group_router = APIRouter(prefix="/api/v1/self-check/group", tags=["TR Self-check"])
_GROUP_READ_SCOPES = {"new", "edit", "review"}


def _group_reader(request: Request):
    auth = verify_bearer(request)
    if isinstance(auth, JSONResponse) or auth.get("_is_user_jwt"):
        return auth
    if auth.get("action_scope") not in _GROUP_READ_SCOPES or not (auth.get("project") and auth.get("group_id")):
        return _forbidden()
    return auth


@group_router.get("/runs")
def list_group_runs(request: Request, group_id: str | None = Query(None)):
    auth = _group_reader(request)
    if isinstance(auth, JSONResponse):
        return auth
    try:
        if not auth.get("_is_user_jwt"):
            return {"ok": True, "runs": selfcheck.group_active_runs(auth["project"], auth["group_id"])}
        if not group_id:
            return _error(selfcheck.SelfCheckError(422, "selfcheck_invalid_request"))
        project_id = selfcheck.group_project(group_id)
        if not has_permission(auth.get("issued_to"), project_id, "perm_document_read"):
            return _forbidden()
        return {"ok": True, "runs": selfcheck.group_active_runs(project_id, group_id)}
    except selfcheck.SelfCheckError as exc:
        return _error(exc)


@group_router.get("/runs/{run_id}")
def read_group_run(request: Request, run_id: str):
    auth = _group_reader(request)
    if isinstance(auth, JSONResponse):
        return auth
    row = selfcheck.group_run(run_id)
    if auth.get("_is_user_jwt"):
        if row is not None and not has_permission(auth.get("issued_to"), row["project_id"], "perm_document_read"):
            return _forbidden()
    elif row is not None and (row["project_id"], row["group_id"]) != (auth["project"], auth["group_id"]):
        row = None      # another Group's run: not found, as for an unknown id
    if row is None:
        return _error(selfcheck.SelfCheckError(404, "selfcheck_run_not_found"))
    return {"ok": True, **selfcheck.summary(row)}


# ── 0080 T#3: operator re-verification of a protected run (NR0007 §5 item 6) ──
# A run left recovery_incomplete (owner or tree not proved) keeps its Group closed until a
# proof exists. A console user with perm_document_update asks the server to run the proof
# again now; the server refuses when it still proves nothing. No forced release exists, and
# no worker token reaches this route.
recovery_router = APIRouter(prefix="/api/v1/self-check/recovery", tags=["TR Self-check"])


@recovery_router.post("/runs/{run_id}/reverify")
def reverify_run(request: Request, run_id: str):
    auth = verify_bearer(request)
    if isinstance(auth, JSONResponse):
        return auth
    if not auth.get("_is_user_jwt"):
        return _forbidden()
    row = selfcheck.group_run(run_id)
    if row is None:
        return _error(selfcheck.SelfCheckError(404, "selfcheck_run_not_found"))
    if not has_permission(auth.get("issued_to"), row["project_id"], "perm_document_update"):
        return _forbidden()
    try:
        return {"ok": True, **selfcheck.reverify(run_id)}
    except selfcheck.SelfCheckError as exc:
        return _error(exc)
