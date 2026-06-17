"""Five outbound list endpoints (D021 §3).

GET /api/v1/list/projects
GET /api/v1/list/projects/{p}/modules
GET /api/v1/list/projects/{p}/groups
GET /api/v1/list/groups/{gid}/documents
GET /api/v1/list/doc-types
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from modules.flow_gate.db import documents as db_docs
from modules.flow_gate.db import groups as db_groups
from modules.flow_gate.db import projects as db_projects
from modules.flow_gate.db.connection import get_store
from modules.flow_gate.services.auth_outbound import verify_bearer

router = APIRouter(prefix="/api/v1", tags=["OutboundList"])

_HELP_URL = "https://example.com/api/v1/help"


def _fail(status: int, message: str) -> JSONResponse:
    return JSONResponse(
        status_code=status,
        content={"ok": False, "http_status": status,
                 "error_message": message, "help_url": _HELP_URL},
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
):
    auth = verify_bearer(request)
    if isinstance(auth, JSONResponse):
        return auth
    err = _validate_limit(limit)
    if err:
        return err

    is_active: Optional[int] = None
    if status == "active":
        is_active = 1
    elif status == "inactive":
        is_active = 0

    store = get_store()
    count_sql = "SELECT COUNT(*) as cnt FROM projects"
    count_params: list = []
    data_sql = "SELECT * FROM projects"
    data_params: list = []

    if is_active is not None:
        count_sql += " WHERE is_active = ?"
        data_sql += " WHERE is_active = ?"
        count_params.append(is_active)
        data_params.append(is_active)

    total_row = store._fetch_one(count_sql, count_params)
    total = total_row["cnt"] if total_row else 0

    data_sql += " ORDER BY project_id LIMIT ? OFFSET ?"
    data_params += [limit, offset]
    rows = store._fetch_all(data_sql, data_params)

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
    if project is None:
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
    if project is None:
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
    if group is None:
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
                "doc_type": r.get("type_code"),
                "title": r.get("title"),
                "status": r.get("status"),
                "revision_no": r.get("revision_no", 0),
                "owner_id": r.get("owner_id"),
                "triggered_by": r.get("triggered_by"),
                "created_at": r.get("created_at"),
                "updated_at": r.get("updated_at"),
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
            "type": r.get("type_code"),
            "title": r.get("title"),
            "status": r.get("status"),
            "revision_no": r.get("revision_no", 0),
            "owner_id": r.get("owner_id"),
            "triggered_by": r.get("triggered_by"),
            "created_at": r.get("created_at"),
            "updated_at": r.get("updated_at"),
        }
        for r in rows
    ]
    return JSONResponse(content={
        "ok": True, "group_id": gid,
        "total": total, "offset": effective_offset, "limit": limit, "items": items,
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
