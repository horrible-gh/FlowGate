"""Five outbound list endpoints (D021 §3).

GET /api/v1/list/projects
GET /api/v1/list/projects/{p}/modules
GET /api/v1/list/projects/{p}/groups
GET /api/v1/list/groups/{gid}/documents
GET /api/v1/list/doc-types
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Request, Query
from fastapi.responses import JSONResponse

from modules.flow_gate.db import documents as db_docs
from modules.flow_gate.db import groups as db_groups
from modules.flow_gate.db import projects as db_projects
from modules.flow_gate.db.connection import get_store
from modules.flow_gate.services import card_ids, display_names
from modules.flow_gate.services.auth_outbound import verify_bearer, can_read_project, readable_projects
from modules.flow_gate.utils.help_url import help_url

router = APIRouter(prefix="/api/v1", tags=["OutboundList"])


def _fail(status: int, message: str) -> JSONResponse:
    return JSONResponse(
        status_code=status,
        content={"ok": False, "http_status": status,
                 "error_message": message, "help_url": help_url()},
    )


def _validate_limit(limit: int) -> Optional[JSONResponse]:
    if limit < 1 or limit > 200:
        return _fail(400, "limit must be between 1 and 200")
    return None


# ── 3-1. GET /list/projects ───────────────────────────────────────────────────

@router.get("/list/projects")
def list_projects(
    request: Request,
    limit: int = 50,
    offset: int = 0,
    status: Optional[str] = None,
    is_active: Optional[str] = Query(None),
):
    auth = verify_bearer(request)
    if isinstance(auth, JSONResponse):
        return auth
    err = _validate_limit(limit)
    if err:
        return err

    status_filter = is_active if is_active is not None else status
    active_filter: Optional[int] = 1
    if status_filter is not None:
        normalized = str(status_filter).strip().lower()
        if normalized in {"all", "any", "*"}:
            active_filter = None
        elif normalized in {"active", "1", "true", "yes"}:
            active_filter = 1
        elif normalized in {"inactive", "archive", "archived", "0", "false", "no"}:
            active_filter = 0
        else:
            return _fail(400, "status/is_active must be active, inactive, or all")

    store = get_store()
    rows = store._fetch_all("SELECT * FROM projects ORDER BY project_id", [])
    rows = [r for r in rows if can_read_project(auth, r["project_id"])
            and (active_filter is None or r.get("is_active", 1) == active_filter)]
    total = len(rows)
    rows = rows[offset:offset + limit]

    items = [
        {
            "project_id": r["project_id"],
            "title": r.get("project_name") or r["project_id"],
            "description": r.get("description"),
            "status": "active" if r.get("is_active", 1) else "inactive",
            "source_path": r.get("source_path"),
            "created_at": r.get("created_at"),
        }
        for r in rows
    ]
    return JSONResponse(content={
        "ok": True, "total": total, "offset": offset, "limit": limit, "items": items,
    })


# ── 3-2. GET /list/projects/{p}/modules ──────────────────────────────────────

@router.get("/list/projects/{p}/modules")
def list_modules(
    request: Request,
    p: str,
    limit: int = 50,
    offset: int = 0,
):
    auth = verify_bearer(request)
    if isinstance(auth, JSONResponse):
        return auth
    err = _validate_limit(limit)
    if err:
        return err

    project = db_projects.get_by_id(p)
    if project is None or not can_read_project(auth, p):
        return _fail(404, f"Project {p} does not exist")

    # modules: distinct module values from groups
    store = get_store()
    total_row = store._fetch_one(
        "SELECT COUNT(DISTINCT module) as cnt FROM groups WHERE project_id = ?", [p]
    )
    total = total_row["cnt"] if total_row else 0

    rows = store._fetch_all(
        "SELECT DISTINCT module FROM groups WHERE project_id = ? "
        "ORDER BY module LIMIT ? OFFSET ?",
        [p, limit, offset],
    )
    items = [
        {
            "module_id": r["module"],
            "title": r["module"] if r["module"] != "none" else "All",
            "description": None,
            "created_at": None,
        }
        for r in rows
    ]
    return JSONResponse(content={
        "ok": True, "project": p,
        "total": total, "offset": offset, "limit": limit, "items": items,
    })


# ── 3-3. GET /list/projects/{p}/groups ───────────────────────────────────────

@router.get("/list/projects/{p}/groups")
def list_groups(
    request: Request,
    p: str,
    limit: int = 50,
    offset: Optional[int] = None,
    before: Optional[str] = None,
    status: Optional[str] = None,
    module: Optional[str] = None,
):
    auth = verify_bearer(request)
    if isinstance(auth, JSONResponse):
        return auth
    err = _validate_limit(limit)
    if err:
        return err

    # P005 §4: return 400 when before and offset are both specified
    if before is not None and offset is not None:
        return JSONResponse(
            status_code=400,
            content={"error": "invalid_params", "message": "before and offset cannot be used simultaneously"},
        )

    project = db_projects.get_by_id(p)
    if project is None or not can_read_project(auth, p):
        return _fail(404, f"Project {p} does not exist")

    store = get_store()

    # ── before cursor approach (P005 §1-2) ───────────────────────────────────────
    if before is not None:
        before_group = store._fetch_one(
            "SELECT group_id FROM groups WHERE group_id = ? AND project_id = ?",
            [before, p],
        )
        if before_group is None:
            return JSONResponse(
                status_code=404,
                content={"error": "not_found", "before": before},
            )

        base_sql = "SELECT * FROM groups WHERE project_id = ? AND group_id <= ?"
        params: list = [p, before]
        if status:
            base_sql += " AND status = ?"
            params.append(status.upper())
        if module:
            base_sql += " AND module = ?"
            params.append(module)

        data_sql = base_sql + " ORDER BY group_id DESC LIMIT ?"
        rows = store._fetch_all(data_sql, params + [limit])

        items = [
            {
                "group_id": r["group_id"],
                "title": r.get("title"),
                "module": r.get("module"),
                "status": r.get("status"),
                "seq": None,
                "flow_label": card_ids.flow_label(r["group_id"]),
                "flow_name": display_names.flow_name(r["group_id"], r.get("title")),
                "created_at": r.get("created_at"),
            }
            for r in rows
        ]
        return JSONResponse(content={
            "ok": True, "project": p,
            "before": before, "limit": limit, "items": items, "count": len(items),
        })

    # ── existing offset/limit approach ───────────────────────────────────────────────
    effective_offset = offset if offset is not None else 0
    base_sql = "SELECT * FROM groups WHERE project_id = ?"
    count_sql = "SELECT COUNT(*) as cnt FROM groups WHERE project_id = ?"
    params = [p]

    if status:
        base_sql += " AND status = ?"
        count_sql += " AND status = ?"
        params.append(status.upper())
    if module:
        base_sql += " AND module = ?"
        count_sql += " AND module = ?"
        params.append(module)

    total_row = store._fetch_one(count_sql, params)
    total = total_row["cnt"] if total_row else 0

    data_sql = base_sql + " ORDER BY group_id LIMIT ? OFFSET ?"
    rows = store._fetch_all(data_sql, params + [limit, effective_offset])

    items = [
        {
            "group_id": r["group_id"],
            "title": r.get("title"),
            "module": r.get("module"),
            "status": r.get("status"),
            "seq": None,  # groups table has no seq column
            "flow_label": card_ids.flow_label(r["group_id"]),
            "flow_name": display_names.flow_name(r["group_id"], r.get("title")),
            "created_at": r.get("created_at"),
        }
        for r in rows
    ]
    return JSONResponse(content={
        "ok": True, "project": p,
        "total": total, "offset": effective_offset, "limit": limit, "items": items,
    })


# ── 3-4. GET /list/groups/{gid}/documents ────────────────────────────────────

_SORT_MAP = {
    "seq_asc": "seq ASC",
    "seq_desc": "seq DESC",
    "updated_desc": "updated_at DESC",
}


@router.get("/list/groups/{gid}/documents")
def list_documents(
    request: Request,
    gid: str,
    limit: int = 50,
    offset: Optional[int] = None,
    before: Optional[str] = None,
    status: Optional[str] = None,
    type: Optional[str] = None,
    sort: str = "seq_asc",
):
    auth = verify_bearer(request)
    if isinstance(auth, JSONResponse):
        return auth
    err = _validate_limit(limit)
    if err:
        return err

    # P005 §4: return 400 when before and offset are both specified
    if before is not None and offset is not None:
        return JSONResponse(
            status_code=400,
            content={"error": "invalid_params", "message": "before and offset cannot be used simultaneously"},
        )

    if sort not in _SORT_MAP:
        return _fail(400, f"Invalid sort value: {sort}")

    group = db_groups.get_by_id(gid)
    if group is None or not can_read_project(auth, group.get("project_id")):
        return _fail(404, f"Group {gid} does not exist")

    store = get_store()

    # ── before cursor approach (P005 §1-1) ───────────────────────────────────────
    if before is not None:
        before_doc = store._fetch_one(
            "SELECT seq FROM documents WHERE doc_id = ? AND group_id = ?",
            [before, gid],
        )
        if before_doc is None:
            return JSONResponse(
                status_code=404,
                content={"error": "not_found", "before": before},
            )
        before_seq = before_doc["seq"]

        base_sql = "SELECT * FROM documents WHERE group_id = ? AND seq <= ?"
        params: list = [gid, before_seq]
        if status:
            base_sql += " AND status = ?"
            params.append(status)
        if type:
            base_sql += " AND type_code LIKE ?"
            params.append(f"{type}%")

        data_sql = base_sql + " ORDER BY seq DESC LIMIT ?"
        rows = store._fetch_all(data_sql, params + [limit])

        items = [
            {
                "doc_id": r["doc_id"],
                "group_title": group.get("title"),
            "group_title": group.get("title"),
                "doc_type": r.get("type_code"),
                "title": r.get("title"),
                "status": r.get("status"),
                "revision_no": r.get("revision_no", 0),
                "owner_id": r.get("owner_id"),
                "triggered_by": r.get("triggered_by"),
                "created_at": r.get("created_at"),
                "updated_at": r.get("updated_at"),
                "origin_provider_name": r.get("origin_provider_name"),
                "origin_ai_run_id": r.get("origin_ai_run_id"),
            }
            for r in rows
        ]
        return JSONResponse(content={
            "ok": True, "group_id": gid,
            "before": before, "limit": limit, "items": items, "count": len(items),
        })

    # ── existing offset/limit approach ───────────────────────────────────────────────
    effective_offset = offset if offset is not None else 0
    base_sql = "SELECT * FROM documents WHERE group_id = ?"
    count_sql = "SELECT COUNT(*) as cnt FROM documents WHERE group_id = ?"
    params = [gid]

    if status:
        base_sql += " AND status = ?"
        count_sql += " AND status = ?"
        params.append(status)
    if type:
        base_sql += " AND type_code LIKE ?"
        count_sql += " AND type_code LIKE ?"
        params.append(f"{type}%")

    total_row = store._fetch_one(count_sql, params)
    total = total_row["cnt"] if total_row else 0

    order = _SORT_MAP[sort]
    data_sql = base_sql + f" ORDER BY {order} LIMIT ? OFFSET ?"
    rows = store._fetch_all(data_sql, params + [limit, effective_offset])

    items = [
        {
            "doc_id": r["doc_id"],
            "group_title": group.get("title"),
            "type": r.get("type_code"),
            "title": r.get("title"),
            "status": r.get("status"),
            "revision_no": r.get("revision_no", 0),
            "owner_id": r.get("owner_id"),
            "triggered_by": r.get("triggered_by"),
            "created_at": r.get("created_at"),
            "updated_at": r.get("updated_at"),
            "origin_provider_name": r.get("origin_provider_name"),
            "origin_ai_run_id": r.get("origin_ai_run_id"),
        }
        for r in rows
    ]
    return JSONResponse(content={
        "ok": True, "group_id": gid,
        "total": total, "offset": effective_offset, "limit": limit, "items": items,
    })


# ── 3-6. GET /search/documents (Phase 1: global title/doc_id search) ─────

@router.get("/search/documents")
def search_documents(
    request: Request,
    q: str = "",
    project: Optional[str] = None,
    type: Optional[str] = None,
    status: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
    flow: Optional[str] = None,
):
    """Title / doc_id search. A Flow or card display id (``F77-12``, ``flowgate.v02test.F77``, ``#12`` with
    ``flow``) also matches exactly: those items come first on the first page, ``match`` = card / flow, with
    ``card`` telling what opens it. ``flow`` (a group id) is the context a module-less id is read in."""
    auth = verify_bearer(request)
    if isinstance(auth, JSONResponse):
        return auth
    err = _validate_limit(limit)
    if err:
        return err
    if offset < 0:
        return _fail(400, "offset must be >= 0")

    query = (q or "").strip()
    if not query:
        return _fail(400, "q is required")

    if project and not can_read_project(auth, project):
        return _fail(404, f"Project {project} does not exist")
    ref_items = _ref_items(auth, query, project, flow)
    allowed_projects = [project] if project else readable_projects(auth)
    rows, total = db_docs.search_documents(
        q=query, project=project, doc_type=type, status=status,
        limit=limit, offset=offset, allowed_projects=allowed_projects,
    )
    # Attach a brief body preview to every result so the default explorer search
    # (the one that runs when "search inside contents" is off) shows the document's simplified
    # body — not just its id/title. The body lives on the filesystem; we read only
    # this page of rows through the same mtime cache the content search uses, so it
    # stays cheap. This is the the earlier change fix: prior revisions only added the
    # body preview on the content endpoint, but the reviewer searches in this default
    # mode, so the brief body never appeared.
    from modules.flow_gate.services import content_search_service
    items = [
        {
            "doc_id": r.get("doc_id"),
            "type": r.get("type_code"),
            "title": r.get("title"),
            "status": r.get("status"),
            "project_id": r.get("project_id"),
            "group_id": r.get("group_id"),
            "revision_no": r.get("revision_no", 0),
            "owner_id": r.get("owner_id"),
            "created_at": r.get("created_at"),
            "updated_at": r.get("updated_at"),
            "snippet": content_search_service.body_preview_for_doc(r),
        }
        for r in rows
    ]
    if ref_items:
        exact = {(i["doc_id"], i["group_id"]) for i in ref_items}
        kept = [i for i in items if (i["doc_id"], i["group_id"]) not in exact]
        total += len(ref_items) - (len(items) - len(kept))
        items = ref_items + kept if offset == 0 else kept
    _add_group_titles(items)
    return JSONResponse(content={
        "ok": True, "query": query, "total": total,
        "offset": offset, "limit": limit, "items": items,
    })


# ── 3-7. GET /search/documents/content (Phase 2: body full-text search) ─

@router.get("/search/documents/content")
def search_documents_content(
    request: Request,
    q: str = "",
    project: Optional[str] = None,
    type: Optional[str] = None,
    status: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
    context_lines: Optional[int] = None,
    hits_per_doc: Optional[int] = None,
    include_matches: bool = True,
    doc_id: Optional[str] = None,
    group_id: Optional[str] = None,
    sort: str = "desc",
    cursor: Optional[str] = None,
):
    """Full document search incl. body text (Phase 2). Same auth/validation/paging
    as Phase 1; each item adds a ``snippet`` (excerpt for body matches), ``matched_in``
    (body|title|doc_id), and ``match_kind`` (``document_body``|``conversation_turn``).

    0370 set 2 (P0002 scenarios 9-11): each item additionally carries ``match_total`` and
    ``matches`` — where in the document each hit is (a locator identical in shape to the
    one ``/outline`` and ``/section`` return) plus the matched line and its neighbours. The
    pre-existing keys keep their meaning *and their values*, so a screen reading this
    response today needs no change; ``include_matches=false`` drops the two new keys
    entirely for callers that want the old payload byte for byte.

    Reads document bodies from the filesystem via a self-healing mtime cache — no
    schema/migration, multi-dialect safe. A migrated CH conversation's turns (T4,
    L0004 §2-15) are searched separately and appended after the document-body page:
    ``total``/``offset``/``limit`` keep their original document-body-only meaning
    (paging contract preserved for existing callers). Turn results rank per document
    before the global cap. doc_id/group_id restrict both result kinds; sort controls
    turn ordering (desc by default). doc_id lifts the per-document turn cap and uses
    limit (up to SEARCH_TURN_LIMIT) and next_cursor for keyset paging. Pass a cursor
    back with the same search conditions; document-body rows are omitted on subsequent
    cursor pages. A group/global search remains capped and has no next cursor.
    """
    auth = verify_bearer(request)
    if isinstance(auth, JSONResponse):
        return auth
    err = _validate_limit(limit)
    if err:
        return err
    if offset < 0:
        return _fail(400, "offset must be >= 0")

    query = (q or "").strip()
    if not query:
        return _fail(400, "q is required")

    if doc_id is not None:
        doc_id = doc_id.strip()
        if not doc_id:
            return _fail(400, "doc_id must not be empty")
    if group_id is not None:
        group_id = group_id.strip()
        if not group_id:
            return _fail(400, "group_id must not be empty")
    if doc_id and group_id:
        return _fail(400, "doc_id and group_id are mutually exclusive")
    if sort not in {"asc", "desc"}:
        return _fail(400, "sort must be asc or desc")
    if cursor is not None and not doc_id:
        return _fail(400, "cursor requires doc_id")
    if cursor is not None and type and not "CH".startswith(type.strip().upper()):
        return _fail(400, "cursor requires a conversation type")

    from modules.flow_gate.services import content_search_service

    from modules.flow_gate.services import document_outline_service as outline_svc

    # Out-of-range values are clamped rather than rejected with a 422: search is how unattended
    # work moves around, and losing a round trip to one typo costs more. The response echoes
    # **the value actually applied** — echoing the request would report a number the server never honoured.
    ctx = outline_svc.CONTEXT_LINES_DEFAULT if context_lines is None else context_lines
    ctx = max(0, min(int(ctx), outline_svc.CONTEXT_LINES_MAX))
    per_doc_hits = (
        outline_svc.HITS_PER_DOC_DEFAULT if hits_per_doc is None else hits_per_doc
    )
    per_doc_hits = max(1, min(int(per_doc_hits), outline_svc.HITS_PER_DOC_MAX))

    if project and not can_read_project(auth, project):
        return _fail(404, f"Project {project} does not exist")
    allowed_projects = [project] if project else readable_projects(auth)
    items, total = content_search_service.search_document_bodies(
        q=query, project=project, doc_type=type, status=status,
        limit=limit, offset=offset,
        include_matches=include_matches, context_lines=ctx, hits_per_doc=per_doc_hits,
        allowed_projects=allowed_projects, doc_id=doc_id, group_id=group_id,
    )
    if cursor is not None:
        items = []
    next_cursor = None
    turn_items: list = []
    if not type or "CH".startswith(type.strip().upper()):
        try:
            turn_items, next_cursor = content_search_service.search_conversation_turns_page(
                q=query, project=project, status=status,
                limit=limit if doc_id else content_search_service.SEARCH_TURN_LIMIT,
                include_matches=include_matches, context_lines=ctx, hits_per_doc=per_doc_hits,
                allowed_projects=allowed_projects, doc_id=doc_id, group_id=group_id,
                sort=sort, cursor=cursor,
            )
        except ValueError as exc:
            return _fail(400, str(exc))
    _add_group_titles(items + turn_items)
    return JSONResponse(content={
        "ok": True, "query": query, "scope": "content", "total": total,
        "offset": offset, "limit": limit,
        "context_lines": ctx, "hits_per_doc": per_doc_hits,
        "items": items + turn_items,
        "turn_total": len(turn_items),
        "next_cursor": next_cursor,
    })


# ── 3-5. GET /list/doc-types ─────────────────────────────────────────────────

@router.get("/list/doc-types")
def list_doc_types(
    request: Request,
    limit: int = 50,
    offset: int = 0,
):
    auth = verify_bearer(request)
    if isinstance(auth, JSONResponse):
        return auth
    err = _validate_limit(limit)
    if err:
        return err

    store = get_store()
    total_row = store._fetch_one(
        "SELECT COUNT(*) as cnt FROM document_types WHERE is_active = 1", []
    )
    total = total_row["cnt"] if total_row else 0

    rows = store._fetch_all(
        "SELECT * FROM document_types WHERE is_active = 1 "
        "ORDER BY sort_order, type_code LIMIT ? OFFSET ?",
        [limit, offset],
    )
    items = [
        {
            "prefix": r["type_code"],
            "name": r.get("type_name"),
            "description": r.get("series"),
            "category": r.get("series"),
            "is_active": bool(r.get("is_active", 1)),
        }
        for r in rows
    ]
    return JSONResponse(content={
        "ok": True, "total": total, "offset": offset, "limit": limit, "items": items,
    })


def _add_group_titles(items):
    groups = {}
    for item in items:
        gid = item.get("group_id")
        if gid not in groups:
            groups[gid] = db_groups.get_by_id(gid) if gid else None
        group = groups[gid]
        item["group_title"] = (group.get("title") if group and
                               group.get("project_id") == item.get("project_id") else None)


@router.get("/list/groups/{group_id}")
def get_group(request: Request, group_id: str):
    auth = verify_bearer(request)
    if isinstance(auth, JSONResponse):
        return auth
    requested = group_id
    group = db_groups.get_by_id(group_id)
    card = None
    if group is None:
        # A Flow / card display id (F77, flowgate.v02test.F77, F77-12): the stored group id is still the key.
        found = _readable_refs(auth, group_id)
        if len(found) == 1:
            group_id, card = found[0]["group_id"], found[0] if found[0]["kind"] == "card" else None
            group = db_groups.get_by_id(group_id)
    if group is None or not can_read_project(auth, group.get("project_id")):
        return _fail(404, f"Group {requested} does not exist")
    count = get_store()._fetch_one(
        "SELECT COUNT(*) AS cnt FROM documents WHERE group_id = ?", [group_id])
    body = {
        "ok": True,
        **{key: group.get(key) for key in (
            "group_id", "title", "module", "status", "parent_id",
            "created_at", "updated_at", "closed_at")},
        "document_count": count["cnt"] if count else 0,
        "flow_ref": card_ids.flow_ref(group_id),
        # How AI text names this Flow to the user (F77(name)); API calls keep group_id.
        "flow_name": display_names.flow_name(group_id, group.get("title")),
    }
    if requested != group_id:
        body["requested_id"] = requested
    if card is not None:
        body["card"] = {key: card[key] for key in ("node_key", "card_ref", "doc_id")}
    return JSONResponse(content=body)


def _readable_refs(auth, text: str, context_flow_id: Optional[str] = None, *, allow_bare: bool = True) -> list:
    """The Flows / cards a typed display id names, limited to the projects this caller reads."""
    from modules.flow_gate.services import card_numbers
    return card_numbers.resolve(text, context_flow_id=context_flow_id, allowed_projects=readable_projects(auth),
                                allow_bare=allow_bare)


def _ref_items(auth, query: str, project: Optional[str], flow: Optional[str]) -> list:
    """Search items for an exact Flow / card id match (they come first): the representative document plus
    ``match`` (card / flow) and ``card`` (what opens the card: group_id, node_key, its display ids)."""
    items = []
    for ref in _readable_refs(auth, query, flow):
        if project and ref["project_id"] != project:
            continue
        doc = db_docs.get_by_id(ref["doc_id"]) if ref.get("doc_id") else None
        items.append({
            "doc_id": ref.get("doc_id"),
            "type": (doc or {}).get("type_code"),
            "title": (doc or {}).get("title"),
            "status": (doc or {}).get("status"),
            "project_id": ref["project_id"],
            "group_id": ref["group_id"],
            "revision_no": (doc or {}).get("revision_no", 0),
            "owner_id": (doc or {}).get("owner_id"),
            "created_at": (doc or {}).get("created_at"),
            "updated_at": (doc or {}).get("updated_at"),
            "snippet": None,
            "match": ref["kind"],
            "card": {"group_id": ref["group_id"], "node_key": ref.get("node_key"),
                     "flow_ref": ref["flow_ref"], "card_ref": ref.get("card_ref")},
        })
    return items
