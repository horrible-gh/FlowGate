"""API help endpoints (D021 §4-1, group 0372 P-0004).

GET /api/v1/help
No authentication required. Without a bearer token this is the endpoint catalog;
with a worker token it is that token's personalized help index. ?items=a,b returns
several items in one round trip and ?detail=true expands everything visible.

GET /api/v1/help/items/{name}
GET /api/v1/help/items/{name}/{child}
Authentication required (Bearer token)

GET /api/v1/help/doc_type
Authentication required (Bearer token)

GET /api/v1/help/question
Authentication required (Bearer token)
"""
import json
import logging
from typing import Optional

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from modules.flow_gate.db import documents as db_documents
from modules.flow_gate.db import events as db_events
from modules.flow_gate.db import templates as db_templates
from modules.flow_gate import template_provision
from modules.flow_gate.services import (
    auth_outbound,
    help_catalog,
    remote_tool_service,
    tool_registry,
)
from modules.flow_gate.services.auth_outbound import verify_bearer
from modules.flow_gate.utils.help_url import help_url, outbound_api_base

router = APIRouter(prefix="/api/v1", tags=["Help"])


@router.get("/help/doc_type")
def get_help_doc_type(request: Request):
    """Document type code list. Used by workers to look up the meaning of type_code (e.g. D, DS, R …).

    Auth: Bearer token required.
    Data source: DB document_types table (is_active=1, global default types).
    """
    auth = verify_bearer(request)
    if isinstance(auth, JSONResponse):
        return auth

    locale = template_provision.normalize_locale(auth.get("continuation_locale"))
    rows = db_templates.list_document_types(project_id=None, locale=locale)
    result = [
        {
            "type_code": r["type_code"],
            "name": r["type_name"],
            "series": r["series"],
            "description": r.get("description"),
        }
        for r in rows
        if r.get("is_active", 1)
    ]
    return JSONResponse(content=result)


# The copy moved to help_catalog so this alias route and the `question` help item
# are served by one supplier (0372 L-0005 §2-8). Kept as a name for existing callers.
_QUESTION_HELP_COPY = help_catalog.QUESTION_HELP_COPY


@router.get("/help/question")
def get_help_question(request: Request):
    """Localized query-registration guide for authenticated workers."""
    auth = verify_bearer(request)
    if isinstance(auth, JSONResponse):
        return auth

    locale = template_provision.normalize_locale(auth.get("continuation_locale"))
    return JSONResponse(content=help_catalog.build_question_content(locale))


_logger = logging.getLogger(__name__)


def _record_help_tools_event(
    token_rec: dict,
    *,
    view: str,
    tool: Optional[str],
    kind: str,
    locale: str,
    source_mode: Optional[str],
    http_status: int,
) -> None:
    """Best-effort audit for authenticated help views."""
    doc_ref = token_rec.get("doc_ref")
    if not doc_ref:
        _logger.warning("Skipping help_tools_viewed event: authenticated token has no doc_ref")
        return
    try:
        if db_documents.get_by_id(doc_ref) is None:
            _logger.warning("Skipping help_tools_viewed event: document %s does not exist", doc_ref)
            return
        note = json.dumps(
            {
                "view": view,
                "tool": tool,
                "kind": kind,
                "locale": locale,
                "source_mode": source_mode,
                "http_status": http_status,
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )
        db_events.insert_event(
            doc_id=doc_ref,
            event_type="help_tools_viewed",
            note=note,
        )
    except Exception:
        _logger.warning("Failed to record help_tools_viewed event for %s", doc_ref, exc_info=True)


def _tools_context(request: Request, locale: Optional[str]):
    auth = verify_bearer(request)
    if isinstance(auth, JSONResponse):
        return auth, None, None
    normalized_locale = template_provision.normalize_locale(
        locale if locale is not None else request.headers.get("x-locale")
    )
    registry = tool_registry.resolve_registry(
        auth,
        auth.get("project"),
        normalized_locale,
    )
    return auth, normalized_locale, registry


@router.get("/help/tools")
def get_help_tools(request: Request, locale: Optional[str] = None):
    """Remote source tools available to this authenticated token."""
    auth, normalized_locale, registry = _tools_context(request, locale)
    if isinstance(auth, JSONResponse):
        return auth

    base_url = outbound_api_base()
    payload = {
        "ok": True,
        "version": tool_registry.VERSION,
        "base_url": base_url,
        "locale": normalized_locale,
        "kind": registry["kind"],
        "source_mode": registry["source_mode"],
        "reason": registry["reason"],
        "detail_url": f"{base_url}/help/tools/{{name}}",
        "tools": registry["tools"],
        "notes": registry["notes"],
    }
    _record_help_tools_event(
        auth,
        view="list",
        tool=None,
        kind=registry["kind"],
        locale=normalized_locale,
        source_mode=registry["source_mode"],
        http_status=200,
    )
    return JSONResponse(content=payload)


@router.get("/help/tools/{name}")
def get_help_tool(request: Request, name: str, locale: Optional[str] = None):
    """Request contract and example for one remote source tool."""
    auth, normalized_locale, registry = _tools_context(request, locale)
    if isinstance(auth, JSONResponse):
        return auth

    if name not in remote_tool_service.OPS:
        status = 404
        response = auth_outbound._fail(status, f"Unknown tool: {name}")
    elif name not in {item["name"] for item in registry["tools"]}:
        status = 403
        response = auth_outbound._fail(
            status, f"Tool '{name}' is not available for this token"
        )
    else:
        status = 200
        base_url = outbound_api_base()
        response = JSONResponse(content={
            "ok": True,
            "version": tool_registry.VERSION,
            "base_url": base_url,
            "locale": normalized_locale,
            "kind": registry["kind"],
            "tool": tool_registry.build_tool_detail(name, normalized_locale, base_url),
            "notes": tool_registry.detail_notes(name, normalized_locale),
        })

    _record_help_tools_event(
        auth,
        view="detail",
        tool=name,
        kind=registry["kind"],
        locale=normalized_locale,
        source_mode=registry["source_mode"],
        http_status=status,
    )
    return response


def endpoint_catalog() -> dict:
    """The catalog an unauthenticated caller receives (P-0004 [엣지3]).

    A human in a browser lands here; ``form`` tells the two /help answers apart.
    """
    return {
        "ok": True,
        "version": "v1",
        "base_url": outbound_api_base(),
        "form": "endpoints",
        "endpoints": [
            {"method": "POST", "path": "/token/issue", "summary": "Issue a work token (screen action trigger)", "auth": "session_cookie"},
            {"method": "POST", "path": "/inbox", "summary": "Register/update an artifact (action: new | edit)", "auth": "bearer_token"},
            {"method": "GET", "path": "/list/projects", "summary": "Project list", "auth": "bearer_token"},
            {"method": "GET", "path": "/list/projects/{p}/modules", "summary": "Module list", "auth": "bearer_token", "example": "/list/projects/myproject/modules"},
            {"method": "GET", "path": "/list/projects/{p}/groups", "summary": "Group list", "auth": "bearer_token",
             "example": "/list/projects/myproject/groups?before=myproject.none.0002&limit=5",
             "params": {
                 "before": "Reference group_id — returns the N items up to and including that group (group_number descending). Ignores offset when before is set",
                 "limit": "Number of items to return (default 50)",
                 "offset": "Offset (when before is not used)",
             }},
            {"method": "GET", "path": "/list/groups/{gid}/documents", "summary": "Document list within a group", "auth": "bearer_token",
             "example": "/list/groups/myproject.none.0001/documents?before=0003-DS&limit=5",
             "params": {
                 "before": "Reference doc_id — returns the N items up to and including that document (doc_number descending). Ignores offset when before is set",
                 "limit": "Number of items to return (default 50)",
                 "offset": "Offset (when before is not used)",
             }},
            {"method": "GET", "path": "/list/doc-types", "summary": "Supported document type list", "auth": "bearer_token"},
            {"method": "GET", "path": "/document/{id}", "summary": "Document body + metadata", "auth": "bearer_token", "example": "/document/myproject.none.0001.0001-R"},
            {"method": "GET", "path": "/document/{id}/path", "summary": "Document file path", "auth": "bearer_token", "example": "/document/myproject.none.0001.0001-R/path"},
            {"method": "GET", "path": "/project/{p}/source-path", "summary": "Project source code path", "auth": "bearer_token", "example": "/project/myproject/source-path"},
            {"method": "GET", "path": "/group/{gid}/next-action", "summary": "Last/expected next action of the group", "auth": "bearer_token", "example": "/group/myproject.none.0001/next-action"},
            {"method": "GET", "path": "/workflow/{doc_id}/head", "summary": "Query current head step of the workflow sequence (including doc_class)", "auth": "bearer_token", "example": "/workflow/R016/head"},
            {"method": "GET", "path": "/workflow/{doc_id}/sequence", "summary": "Query all workflow sequence items + head", "auth": "bearer_token", "example": "/workflow/R016/sequence"},
            {"method": "POST", "path": "/workflow/{doc_id}/decide", "summary": "Save workflow decision (once at initialization, defines sequence)", "auth": "bearer_token", "example": "/workflow/R016/decide"},
            {"method": "POST", "path": "/workflow/{doc_id}/advance", "summary": "Advance to next step (number assignment + token issue + ment creation)", "auth": "bearer_token", "example": "/workflow/R016/advance"},
            {"method": "GET", "path": "/help/doc_type", "summary": "Document type code list", "auth": "bearer_token"},
            {"method": "GET", "path": "/help/question", "summary": "Query registration guide (register as document-bound query data, not a Q document)", "auth": "bearer_token"},
            {"method": "GET", "path": "/help/tools", "summary": "Remote source tools available to this token (name + one-line summary)", "auth": "bearer_token"},
            {"method": "GET", "path": "/help/tools/{name}", "summary": "Usage detail for one remote source tool (request format + example)", "auth": "bearer_token", "example": "/help/tools/read"},
            {"method": "GET", "path": "/help/items/{name}", "summary": "One help item for this token (index at GET /help)", "auth": "bearer_token", "example": "/help/items/submit"},
            {"method": "GET", "path": "/help/items/{name}/{child}", "summary": "One child of a help item", "auth": "bearer_token", "example": "/help/items/design_template/P"},
            {"method": "GET", "path": "/events/stream", "summary": "SSE screen push stream (screen only)", "auth": "session_cookie"},
        ],
        "notes": [
            "Every path above is relative to base_url.",
            "A worker token may call any endpoint listed here with auth=bearer_token; there is no "
            "per-path scope allowlist. Read a document body with GET /document/{id} — singular.",
            "Call GET /help with a worker bearer token to receive the personalized help index "
            "instead of this endpoint catalog.",
            "The console UI is served by a separate, unlisted API under the same base_url whose "
            "paths are PLURAL (/documents/…). It accepts only a signed-in user session and answers "
            "a worker token with 401 'Invalid authentication credentials'. That 401 means the wrong "
            "API was called, not that the token lacks a scope.",
        ],
        "error_format": {
            "ok": False,
            "http_status": "<int>",
            "error_message": "<string>",
            "help_url": help_url(),
        },
    }


# ── Help index / items (0372 D-0003 §3-4, P-0004, L-0005) ────────────────────
# Everything the mention used to carry inline is a named item here. The index and
# every direct call share one visibility judgment
# (``help_catalog.decide_visibility``), so an item listed in the index can never
# answer a direct call with 403 — and an item hidden from the index can never be
# reached by guessing its name.


def _record_help_event(
    token_rec: dict,
    ctx: dict,
    *,
    view: str,
    name: Optional[str] = None,
    child: Optional[str] = None,
    names: Optional[list] = None,
    count: int = 0,
    http_status: int = 200,
) -> None:
    """Best-effort audit of one authenticated help view (P-0004 [엣지2]).

    One event per request whatever the item count, and a failed insert never turns
    a good help answer into an error — same contract as ``help_tools_viewed``.
    """
    doc_ref = token_rec.get("doc_ref")
    if not doc_ref:
        _logger.warning("Skipping help_viewed event: authenticated token has no doc_ref")
        return
    try:
        if db_documents.get_by_id(doc_ref) is None:
            _logger.warning("Skipping help_viewed event: document %s does not exist", doc_ref)
            return
        note = json.dumps(
            {
                "view": view,
                "name": name,
                "child": child,
                "names": names,
                "count": count,
                "locale": ctx["locale"],
                "doc_type": ctx["doc_type"],
                "action_scope": ctx["action_scope"],
                "tool_kind": ctx["tool_kind"],
                "source_mode": ctx["source_mode"],
                "http_status": http_status,
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )
        db_events.insert_event(doc_id=doc_ref, event_type="help_viewed", note=note)
    except Exception:
        _logger.warning("Failed to record help_viewed event for %s", doc_ref, exc_info=True)


def _help_locale(request: Request, auth: dict, locale: Optional[str]) -> str:
    """query ``locale`` → ``x-locale`` header → the token's carried locale → ko.

    The first candidate that is present wins even when it names an unsupported
    locale: ``?locale=zh`` folds to ko rather than falling through to the header,
    which is what ``/help/tools`` already does (P-0004 [엣지1]).
    """
    for candidate in (locale, request.headers.get("x-locale"), auth.get("continuation_locale")):
        if candidate is not None and str(candidate).strip():
            return help_catalog.normalize_locale(str(candidate))
    return help_catalog.FALLBACK_LOCALE


def _help_context(request: Request, locale: Optional[str]):
    auth = verify_bearer(request)
    if isinstance(auth, JSONResponse):
        return auth, None
    normalized_locale = _help_locale(request, auth, locale)
    return auth, help_catalog.resolve_context(auth, normalized_locale, outbound_api_base())


def _help_envelope(ctx: dict) -> dict:
    return {
        "ok": True,
        "version": help_catalog.VERSION,
        "base_url": ctx["base_url"],
        "locale": ctx["locale"],
    }


def _help_error(status: int, message: str, extra: Optional[dict] = None) -> JSONResponse:
    content = {
        "ok": False,
        "http_status": status,
        "error_message": message,
        "help_url": help_url(),
    }
    if extra:
        content.update(extra)
    return JSONResponse(status_code=status, content=content)


def _help_index_response(auth: dict, ctx: dict) -> JSONResponse:
    base = ctx["base_url"]
    assembled = help_catalog.build_index(ctx)
    payload = _help_envelope(ctx)
    payload.update({
        "form": "index",
        "item_url": f"{base}/help/items/{{name}}",
        "child_url": f"{base}/help/items/{{name}}/{{child}}",
        "bulk_url": f"{base}/help?items={{name1}},{{name2}}",
        "detail_url": f"{base}/help?detail=true",
        "context": help_catalog.context_envelope(ctx),
        "items": assembled["items"],
        "hidden": assembled["hidden"],
    })
    _record_help_event(auth, ctx, view="index", count=len(assembled["items"]))
    return JSONResponse(content=payload)


def _help_bulk_response(auth: dict, ctx: dict, names: list) -> JSONResponse:
    items, unavailable = help_catalog.build_bulk(names, ctx)
    if not items:
        # Nothing was built. 404 when every requested name is simply unknown; 403 as
        # soon as one of them exists but is not this token's to see — the worker has
        # to be able to tell a typo from a permission it does not hold.
        all_unknown = all(entry["http_status"] == 404 for entry in unavailable)
        status = 404 if all_unknown else 403
        message = (
            "None of the requested help items exist"
            if all_unknown
            else "None of the requested help items are available for this token"
        )
        _record_help_event(auth, ctx, view="bulk", names=names, count=0, http_status=status)
        return _help_error(status, message, {"unavailable": unavailable})

    payload = _help_envelope(ctx)
    payload.update({
        "form": "bulk",
        "requested": names,
        "returned": len(items),
        "items": items,
        "unavailable": unavailable,
    })
    _record_help_event(auth, ctx, view="bulk", names=names, count=len(items))
    return JSONResponse(content=payload)


@router.get("/help/items/{name}")
def get_help_item(request: Request, name: str, locale: Optional[str] = None):
    """One help item, or the child list of an item that has children."""
    auth, ctx = _help_context(request, locale)
    if isinstance(auth, JSONResponse):
        return auth

    # Unknown before forbidden: a name that does not exist is a typo the worker can
    # fix from the index, and calling it a permission failure sends it looking for
    # a permission that was never the problem (P-0004 [실패3]).
    if name not in help_catalog.CATALOG_ORDER:
        return _help_error(404, f"Unknown help item: {name}")

    decision = help_catalog.decide_visibility(name, ctx)
    if not decision.visible:
        _record_help_event(auth, ctx, view="item", name=name, count=0, http_status=403)
        return _help_error(403, f"Help item '{name}' is not available for this token")

    try:
        body = help_catalog.build_item(name, ctx)
    except help_catalog.HelpSupplierError:
        # A storage failure stays a storage failure. Dressing it up as 403 would send
        # the worker hunting for a permission it already holds (L-0005 §2-7).
        _logger.exception("help item supplier failed: %s", name)
        return _help_error(500, f"Failed to build help item '{name}'")

    payload = _help_envelope(ctx)
    payload.update(body)
    _record_help_event(auth, ctx, view="item", name=name, count=1)
    return JSONResponse(content=payload)


@router.get("/help/items/{name}/{child}")
def get_help_item_child(
    request: Request, name: str, child: str, locale: Optional[str] = None
):
    """One child of a help item — a source tool, a design template, a guide."""
    auth, ctx = _help_context(request, locale)
    if isinstance(auth, JSONResponse):
        return auth

    if name not in help_catalog.CATALOG_ORDER:
        return _help_error(404, f"Unknown help item: {name}")

    decision = help_catalog.decide_visibility(name, ctx)
    if not decision.visible:
        _record_help_event(
            auth, ctx, view="child", name=name, child=child, count=0, http_status=403
        )
        return _help_error(403, f"Help item '{name}' is not available for this token")

    # The child list is derived from the same context, so a tool this token may not
    # call is not merely hidden from the list — it is not a known child at all.
    known = {entry["name"] for entry in help_catalog.enumerate_children(name, ctx)}
    if child not in known:
        _record_help_event(
            auth, ctx, view="child", name=name, child=child, count=0, http_status=404
        )
        return _help_error(404, f"Unknown child '{child}' of help item '{name}'")

    try:
        body = help_catalog.build_child(name, child, ctx)
    except help_catalog.HelpSupplierError:
        _logger.exception("help child supplier failed: %s/%s", name, child)
        return _help_error(500, f"Failed to build help item '{name}/{child}'")

    payload = _help_envelope(ctx)
    payload.update(body)
    _record_help_event(auth, ctx, view="child", name=name, child=child, count=1)
    return JSONResponse(content=payload)


@router.get("/help")
def get_help(
    request: Request,
    items: Optional[str] = None,
    detail: Optional[str] = None,
    locale: Optional[str] = None,
):
    """Single entry point for API usage.

    No bearer token → the endpoint catalog, unauthenticated, as before. With a
    worker token → that token's help index, or the items it asked for.
    """
    has_bearer = request.headers.get("Authorization", "").startswith("Bearer ")
    if not has_bearer:
        # A bare index is public; asking for actual item bodies is not.
        if items is not None or detail is not None:
            return _help_error(401, "Authorization header is required")
        return JSONResponse(content=endpoint_catalog())

    auth, ctx = _help_context(request, locale)
    if isinstance(auth, JSONResponse):
        return auth

    # `items` wins over `detail`, and `detail` is true only for the exact string
    # "true" — anything else folds to false instead of being rejected (P-0004 §0-3).
    if items is not None:
        try:
            names = help_catalog.parse_bulk_names(items)
        except help_catalog.BulkRequestError as exc:
            return _help_error(422, str(exc))
        return _help_bulk_response(auth, ctx, names)

    if detail == "true":
        return _help_bulk_response(auth, ctx, help_catalog.visible_names(ctx))

    return _help_index_response(auth, ctx)
