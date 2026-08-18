"""T358 — Module/Group/Document list API (for selecting reference documents in NextActionModal).
T505 — New module creation API.

Endpoints:
  GET  /flowgate/api/v1/modules                                      Module list
  GET  /flowgate/api/v1/modules/{module}/groups                      Group list
  GET  /flowgate/api/v1/modules/{module}/groups/{group_id}/documents Document list
  POST /flowgate/api/v1/projects/{project_id}/modules                Module creation (T505)

Document identifier format: {module}/{group_code}/{seq}/{doc_code}
  e.g.) server/0001/1/R0001
"""
from __future__ import annotations

import re
from typing import Optional

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from modules.flow_gate.db import conversation_turns as conversation_turn_store
from modules.flow_gate.db import documents as db_docs
from modules.flow_gate.db import workflow_sequences as db_wfseq
from modules.flow_gate.db.connection import get_store, now_iso
from modules.flow_gate.services.auth_outbound import verify_bearer
from modules.flow_gate.utils.help_url import help_url

router = APIRouter(prefix="/api/v1", tags=["Modules"])

_MODULE_NAME_RE = re.compile(r'^[a-z0-9_\-]+$')


def validate_module_name(value: str) -> str:
    """M031 policy: module name only allows [a-z0-9_-]."""
    if value is None:
        raise ValueError("module name cannot be None")
    value = str(value)
    if not _MODULE_NAME_RE.match(value):
        raise ValueError(f"module name format is invalid (M031): {value!r}")
    return value


def _fail(status: int, message: str) -> JSONResponse:
    return JSONResponse(
        status_code=status,
        content={"ok": False, "http_status": status,
                 "error_message": message, "help_url": help_url()},
    )


def _group_code(group_id: str) -> str:
    """Extract short group code from group_id (third component).

    e.g.) flowgate.server.0001 → 0001
    """
    parts = group_id.split(".", 2)
    return parts[2] if len(parts) == 3 else group_id


def _doc_identifier(module: str, group_id: str, seq: int, type_code: str) -> str:
    """T358 required identifier format: {module}/{group_code}/{seq}/{doc_code}."""
    code = _group_code(group_id)
    doc_code = f"{seq:04d}-{type_code}" if type_code and seq else ""
    return f"{module}/{code}/{seq}/{doc_code}"


# ── GET /modules ───────────────────────────────────────────────────────────────

@router.get("/modules")
def list_modules(
    request: Request,
    project_id: Optional[str] = None,
    limit: int = 100,
    offset: int = 0,
):
    """Return module list.

    Query params:
      project_id  (optional) project ID filter
      limit    (default 100, max 200)
      offset   (default 0)
    """
    auth = verify_bearer(request)
    if isinstance(auth, JSONResponse):
        return auth

    if limit < 1 or limit > 200:
        return _fail(400, "limit must be between 1 and 200")

    store = get_store()

    if project_id:
        count_sql = (
            "SELECT COUNT(*) as cnt FROM project_modules WHERE project_id = ?"
        )
        data_sql = (
            "SELECT pm.module_id AS module_id, pm.name AS name, pm.title AS title "
            "FROM project_modules pm "
            "WHERE pm.project_id = ? "
            "ORDER BY pm.name LIMIT ? OFFSET ?"
        )
        count_row = store._fetch_one(count_sql, [project_id])
        rows = store._fetch_all(data_sql, [project_id, limit, offset])
    else:
        count_sql = "SELECT COUNT(*) as cnt FROM project_modules"
        data_sql = (
            "SELECT pm.module_id AS module_id, pm.name AS name, pm.title AS title "
            "FROM project_modules pm "
            "ORDER BY pm.name LIMIT ? OFFSET ?"
        )
        count_row = store._fetch_one(count_sql, [])
        rows = store._fetch_all(data_sql, [limit, offset])

    total = count_row["cnt"] if count_row else 0
    items = [
        {
            "module_id": r["name"],   # backward compat: module_id is exposed as slug (name)
            "name": r["name"],
            "title": r["title"] or r["name"],  # fallback to name when title is empty
        }
        for r in rows
    ]
    
    # prepend if 'none' module is missing from items (backward compat: "All" label)
    has_none = any(it.get("name") == "none" for it in items)
    if not has_none:
        items.insert(0, {
            "module_id": "none",
            "name": "none",
            "title": "All",
        })
    
    return JSONResponse(content={
        "ok": True, "total": total, "offset": offset, "limit": limit, "items": items,
    })


# ── GET /modules/{module}/groups ───────────────────────────────────────────────

@router.get("/modules/{module}/groups")
def list_groups_for_module(
    request: Request,
    module: str,
    project_id: Optional[str] = None,
    status: Optional[str] = None,
    limit: int = 100,
    offset: int = 0,
):
    """Return list of groups belonging to a module.

    Query params:
      project_id  (optional) project ID filter
      status   (optional) group status filter (e.g. OPEN)
      limit, offset
    """
    auth = verify_bearer(request)
    if isinstance(auth, JSONResponse):
        return auth

    if limit < 1 or limit > 200:
        return _fail(400, "limit must be between 1 and 200")

    try:
        validate_module_name(module)
    except ValueError as exc:
        return _fail(422, str(exc))

    store = get_store()

    module_title_row = store._fetch_one(
        "SELECT title FROM project_modules WHERE project_id = ? AND name = ?",
        [project_id, module],
    )
    module_title = (module_title_row["title"] if module_title_row else None) or module

    base_sql = "SELECT * FROM groups WHERE module = ?"
    count_sql = "SELECT COUNT(*) as cnt FROM groups WHERE module = ?"
    params: list = [module]

    if project_id:
        base_sql += " AND project_id = ?"
        count_sql += " AND project_id = ?"
        params.append(project_id)
    if status:
        base_sql += " AND status = ?"
        count_sql += " AND status = ?"
        params.append(status.upper())

    count_row = store._fetch_one(count_sql, params)
    total = count_row["cnt"] if count_row else 0

    data_sql = base_sql + " ORDER BY group_id LIMIT ? OFFSET ?"
    rows = store._fetch_all(data_sql, params + [limit, offset])

    items = [
        {
            "group_id": r["group_id"],
            "group_code": _group_code(r["group_id"]),
            "title": r.get("title"),
            "module": r.get("module"),
            "module_title": module_title,   # new field
            "project": r.get("project_id"),
            "status": r.get("status"),
            "created_at": r.get("created_at"),
        }
        for r in rows
    ]
    return JSONResponse(content={
        "ok": True, "module": module,
        "total": total, "offset": offset, "limit": limit, "items": items,
    })


# ── GET /modules/{module}/groups/{group_id}/documents ─────────────────────────

@router.get("/modules/{module}/groups/{group_id}/documents")
def list_documents_for_group(
    request: Request,
    module: str,
    group_id: str,
    status: Optional[str] = None,
    type: Optional[str] = None,
    limit: int = 100,
    offset: int = 0,
):
    """Return list of documents belonging to a group.

    Document identifier format: {module}/{group_code}/{seq}/{doc_code}

    Query params:
      status  (optional) document status filter
      type    (optional) doc_type prefix filter (e.g. R)
      limit, offset
    """
    auth = verify_bearer(request)
    if isinstance(auth, JSONResponse):
        return auth

    if limit < 1 or limit > 200:
        return _fail(400, "limit must be between 1 and 200")

    try:
        validate_module_name(module)
    except ValueError as exc:
        return _fail(422, str(exc))

    store = get_store()

    group_row = store._fetch_one(
        "SELECT * FROM groups WHERE group_id = ?", [group_id]
    )
    if group_row is None:
        return _fail(404, f"Group {group_id} does not exist")

    # module match check skipped (if URL param differs from DB value, proceed silently;
    # group_id takes precedence — allowed for development convenience)

    base_sql = "SELECT * FROM documents WHERE group_id = ?"
    count_sql = "SELECT COUNT(*) as cnt FROM documents WHERE group_id = ?"
    params: list = [group_id]

    if status:
        base_sql += " AND status = ?"
        count_sql += " AND status = ?"
        params.append(status)
    if type:
        base_sql += " AND type_code LIKE ?"
        count_sql += " AND type_code LIKE ?"
        params.append(f"{type}%")

    count_row = store._fetch_one(count_sql, params)
    total = count_row["cnt"] if count_row else 0

    data_sql = base_sql + " ORDER BY seq ASC LIMIT ? OFFSET ?"
    rows = store._fetch_all(data_sql, params + [limit, offset])

    grp_module = group_row.get("module") or module
    items = []
    for r in rows:
        seq = r.get("seq") or 0
        type_code = r.get("type_code") or ""
        identifier = _doc_identifier(grp_module, group_id, seq, type_code)
        items.append({
            "doc_id": r["doc_id"],
            "doc_identifier": identifier,
            "type_code": type_code,
            "seq": seq,
            "title": r.get("title"),
            "status": r.get("status"),
            "created_at": r.get("created_at"),
            "updated_at": r.get("updated_at"),
        })

    return JSONResponse(content={
        "ok": True, "group_id": group_id, "module": grp_module,
        "total": total, "offset": offset, "limit": limit, "items": items,
    })


# ── GET /documents/{doc_id}/predecessors ──────────────────────────────────────

@router.get("/documents/{doc_id}/predecessors")
def list_predecessor_docs(request: Request, doc_id: str, limit: int = 2):
    """Return the workflow-sequence predecessor result documents for ``doc_id``.

    R0001 / TR0005 (group 0061) — NextActionModal needs to *visibly* auto-check the
    "previous + the one before it" documents (e.g. T and NR when advancing to TR), not
    just rely on the server merging them at token time. This endpoint is the single
    source of truth the modal queries so the dialog's checked set matches exactly what
    `_build_mention_for_token` / `advance_workflow` will hand the worker.

    It mirrors that token-path computation 1:1 — same sequence, same effective head,
    same `get_predecessor_result_doc_ids(limit)` helper — so there is no client/server
    drift. ``doc_id`` is the document the modal advances FROM's spine ref (the locked R
    in the non-R case, the doc itself for an R). Returns ``[]`` when the document has no
    sequence or no prior step has produced a document yet.
    """
    auth = verify_bearer(request)
    if isinstance(auth, JSONResponse):
        return auth

    if limit < 1 or limit > 10:
        return _fail(400, "limit must be between 1 and 10")

    predecessor_doc_ids: list[str] = []
    seq = db_wfseq.get_sequence_by_doc_id(doc_id)
    if seq is not None:
        head = db_wfseq.get_effective_head(seq["id"])
        head_item_id = head.get("id") if head else None
        predecessor_doc_ids = db_wfseq.get_predecessor_result_doc_ids(
            seq["id"], head_item_id, limit=limit
        )

    predecessors: list[dict] = []
    for predecessor_id in predecessor_doc_ids:
        try:
            predecessor = db_docs.get_by_id(predecessor_id)
        except Exception:
            # Metadata is additive; an unavailable document store must not break the
            # long-standing predecessor_doc_ids contract used by token creation.
            continue
        if predecessor is None:
            continue
        item = {
            "doc_id": predecessor_id,
            "type_code": predecessor.get("type_code"),
            "title": predecessor.get("title"),
        }
        if predecessor.get("type_code") == "CH":
            try:
                state = conversation_turn_store.migration_state(predecessor_id)
                item["conversation"] = {
                    "migration_state": state,
                    "turn_count": conversation_turn_store.count_turns(predecessor_id),
                    "live_content": state == "migrated",
                }
            except Exception:
                pass
        predecessors.append(item)

    return JSONResponse(content={
        "ok": True,
        "doc_id": doc_id,
        "predecessor_doc_ids": predecessor_doc_ids,
        "predecessors": predecessors,
    })


# ── POST /projects/{project_id}/modules (T505) ────────────────────────────────

class _CreateModuleBody(BaseModel):
    name: str
    title: Optional[str] = None


@router.post("/projects/{project_id}/modules", status_code=201)
def create_module(request: Request, project_id: str, body: _CreateModuleBody):
    """Create module (T505).

    Creates a module record directly under the project and registers it as a module node in the tree.
    Returns 409 if the same project_id + name combination already exists.
    """
    auth = verify_bearer(request)
    if isinstance(auth, JSONResponse):
        return auth

    name = (body.name or "").strip()
    if not name:
        return _fail(400, "name is required")

    try:
        validate_module_name(name)
    except ValueError as exc:
        return _fail(422, str(exc))

    store = get_store()

    project = store._fetch_one(
        "SELECT project_id FROM projects WHERE project_id = ?", [project_id]
    )
    if project is None:
        return _fail(404, f"Project {project_id} does not exist")

    existing = store._fetch_one(
        "SELECT module_id FROM project_modules WHERE project_id = ? AND name = ?",
        [project_id, name],
    )
    if existing:
        return _fail(409, f"Module '{name}' already exists")

    title = (body.title or "").strip() or name
    now = now_iso()
    module_id = f"{project_id}:{name}"

    store._execute(
        "INSERT INTO project_modules (module_id, project_id, name, title, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        [module_id, project_id, name, title, now, now],
    )

    return JSONResponse(status_code=201, content={
        "ok": True,
        "module_id": module_id,
        "project_id": project_id,
        "name": name,
        "title": title,
        "created_at": now,
    })


# ── PATCH /projects/{project_id}/modules/{module_name} (T559) ────────────────

class _PatchModuleBody(BaseModel):
    title: str


@router.patch("/projects/{project_id}/modules/{module_name}")
def patch_module(request: Request, project_id: str, module_name: str, body: _PatchModuleBody):
    """Update module display name (title) (T559).

    slug(name) is immutable. Only title is updated.
    """
    auth = verify_bearer(request)
    if isinstance(auth, JSONResponse):
        return auth

    title = (body.title or "").strip()
    if not title:
        return _fail(400, "title is required")

    try:
        validate_module_name(module_name)
    except ValueError as exc:
        return _fail(422, str(exc))

    store = get_store()

    existing = store._fetch_one(
        "SELECT module_id, name, title FROM project_modules WHERE project_id = ? AND name = ?",
        [project_id, module_name],
    )
    if existing is None:
        return _fail(404, f"Module '{module_name}' not found")

    now = now_iso()
    store._execute(
        "UPDATE project_modules SET title = ?, updated_at = ? WHERE project_id = ? AND name = ?",
        [title, now, project_id, module_name],
    )

    return JSONResponse(content={
        "ok": True,
        "module_id": existing["module_id"],
        "project_id": project_id,
        "name": module_name,
        "title": title,
        "updated_at": now,
    })
