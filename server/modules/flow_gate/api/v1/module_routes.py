"""T358 — Module/Group/Document list API (for selecting reference documents in NextActionModal).
T505 — New module creation API.

Endpoints:
  GET  /flowgate/api/v1/modules                                      Module list
  GET  /flowgate/api/v1/modules/{module}/groups                      Group list
  GET  /flowgate/api/v1/modules/{module}/groups/{group_id}/documents Document list
  POST /flowgate/api/v1/projects/{project_id}/modules                Module creation (T505)
  POST /flowgate/api/v1/projects/{project_id}/modules/{name}/archive Group archive (v02gui.0027)
  POST /flowgate/api/v1/projects/{project_id}/modules/{name}/restore Group restore (v02gui.0027)
  DELETE /flowgate/api/v1/projects/{project_id}/modules/{name}       Empty Group delete (v02gui.0027)

Document identifier format: {module}/{group_code}/{seq}/{doc_code}
  e.g.) server/0001/1/the requirement
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
    # Display name. Omitted (or unchanged) keeps the current title.
    title: Optional[str] = None
    # New exposed identifier. Omitted keeps it.
    name: Optional[str] = None


def _fail_code(status: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(
        status_code=status,
        content={"ok": False, "http_status": status, "code": code,
                 "error_message": message, "help_url": help_url()},
    )


@router.patch("/projects/{project_id}/modules/{module_name}")
def patch_module(request: Request, project_id: str, module_name: str, body: _PatchModuleBody):
    """Update a module's display name (title) and/or its identifier (T559, v02gui.0012).

    The identifier is the module's key itself. Changing it renames the key everywhere
    (``services.module_rename.rename_module``): ``groups.module``, ``documents.module``, every
    group / document id, ``id_counter``, the storage directories, front matter and the group
    branches / worktrees. The old identifier is free afterwards. Refused while anything runs in the
    module's groups (409 ``module_rename_busy``) or when another group uses the identifier
    (409 ``module_identifier_taken``, ``details`` names that group).
    Title and identifier are independent: either, both or neither may change.
    """
    from modules.flow_gate.db import module_identifiers
    from modules.flow_gate.db.module_rename import ModuleRenameRefused
    from modules.flow_gate.services import module_rename

    auth = verify_bearer(request)
    if isinstance(auth, JSONResponse):
        return auth

    if body.title is None and body.name is None:
        return _fail_code(400, "module_patch_empty", "title or name is required")
    title_in = None if body.title is None else body.title.strip()
    if title_in is not None and not title_in:
        return _fail_code(400, "module_title_required", "title is required")
    if title_in is not None and len(title_in) > 100:
        return _fail_code(422, "module_title_too_long", "title must be 100 characters or fewer")

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
        # A module known only through groups.module (created before project_modules) can still be
        # edited: give it its project_modules row first.
        in_groups = store._fetch_one(
            "SELECT 1 AS hit FROM groups WHERE project_id = ? AND module = ? LIMIT 1",
            [project_id, module_name],
        )
        if in_groups is None or module_name == "none":
            return _fail_code(404, "module_not_found", f"Module '{module_name}' not found")

    current_title = ((existing or {}).get("title") or "").strip() or module_name
    new_title = title_in if title_in is not None else current_title
    new_identifier = module_name if body.name is None else body.name.strip()
    title_changed = new_title != current_title
    identifier_changed = new_identifier != module_name

    if identifier_changed:
        try:
            module_identifiers.validate_identifier_format(new_identifier)
        except module_identifiers.ModuleIdentifierError as exc:
            return _fail_code(exc.status, exc.code, exc.message)

    now = now_iso()
    module_id = (existing or {}).get("module_id") or f"{project_id}:{module_name}"
    if existing is None or title_changed:
        with store.transaction():
            if existing is None:
                store._execute(
                    "INSERT INTO project_modules (module_id, project_id, name, title, created_at, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    [module_id, project_id, module_name, new_title, now, now],
                )
            else:
                store._execute(
                    "UPDATE project_modules SET title = ?, updated_at = ? WHERE project_id = ? AND name = ?",
                    [new_title, now, project_id, module_name],
                )

    renamed = None
    if identifier_changed:
        try:
            renamed = module_rename.rename_module(
                project_id, module_name, new_identifier, actor=_actor_id(auth),
            )
        except ModuleRenameRefused as exc:
            return JSONResponse(
                status_code=exc.status,
                content={"ok": False, "http_status": exc.status, "code": exc.code,
                         "error_message": exc.message, "details": exc.details,
                         "help_url": help_url(),
                         # The title above is already saved; say so, so the client does not resend it.
                         "title_changed": title_changed, "title": new_title},
            )
        module_id = f"{project_id}:{new_identifier}"

    return JSONResponse(content={
        "ok": True,
        "module_id": module_id,
        "project_id": project_id,
        "name": new_identifier,
        "previous_name": module_name,
        "title": new_title,
        "title_changed": title_changed,
        "identifier_changed": identifier_changed,
        # Old id -> new id for every renamed group (the client follows open tabs, recents, the tree).
        "renamed_groups": (renamed or {}).get("groups", {}),
        "updated_at": now,
    })


def _actor_id(auth) -> Optional[str]:
    if isinstance(auth, dict):
        return auth.get("issued_to") or auth.get("user_id") or auth.get("sub")
    return getattr(auth, "user_id", None)


# ── POST /projects/{project_id}/modules/{module_name}/archive | /restore (v02gui.0027) ─────────

def _module_archive_view(row: dict, project_id: str, module_name: str) -> dict:
    return {
        "ok": True,
        "module_id": row.get("module_id") or f"{project_id}:{module_name}",
        "project_id": project_id,
        "name": module_name,
        "title": (row.get("title") or "").strip() or module_name,
        "archived": row.get("archived_at") is not None,
        "archived_at": row.get("archived_at"),
        "archived_by": row.get("archived_by"),
    }


def _write_module_archive(store, project_id: str, module_name: str, archived: bool, actor: Optional[str]):
    """One conditional write. Returns the row after it, or an error response (nothing written)."""
    existing = store._fetch_one(
        "SELECT * FROM project_modules WHERE project_id = ? AND name = ?", [project_id, module_name],
    )
    now = now_iso()
    if existing is None:
        # A Group known only through groups.module (no project_modules row yet) gets its row here,
        # as the title edit does, so the flag has somewhere to live.
        in_groups = store._fetch_one(
            "SELECT 1 AS hit FROM groups WHERE project_id = ? AND module = ? LIMIT 1", [project_id, module_name],
        )
        if in_groups is None:
            return _fail_code(404, "module_not_found", f"Module '{module_name}' not found")
        if not archived:
            return _fail_code(409, "not_archived", f"Module '{module_name}' is not archived")
        store._execute(
            "INSERT INTO project_modules (module_id, project_id, name, title, created_at, updated_at, "
            "archived_at, archived_by) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [f"{project_id}:{module_name}", project_id, module_name, module_name, now, now, now, actor],
        )
    elif archived:
        if store._execute_affected(
            "UPDATE project_modules SET archived_at = ?, archived_by = ? "
            "WHERE project_id = ? AND name = ? AND archived_at IS NULL",
            [now, actor, project_id, module_name],
        ) == 0:
            return _fail_code(409, "already_archived", f"Module '{module_name}' is already archived")
    elif store._execute_affected(
        "UPDATE project_modules SET archived_at = NULL, archived_by = NULL "
        "WHERE project_id = ? AND name = ? AND archived_at IS NOT NULL",
        [project_id, module_name],
    ) == 0:
        return _fail_code(409, "not_archived", f"Module '{module_name}' is not archived")
    return store._fetch_one(
        "SELECT * FROM project_modules WHERE project_id = ? AND name = ?", [project_id, module_name],
    )


def _change_module_archive(request: Request, project_id: str, module_name: str, archived: bool):
    """Display-only Group archive (flowgate.v02gui.0027 0006-T §4).

    Only ``project_modules.archived_at / archived_by`` change: the Group's Flows, documents, runs,
    worktrees and branches stay exactly as they are, and running work is not a reason to refuse
    (the Group is only hidden from the default tree). Same authentication as the Group create /
    title edit. Already archived / not archived answer 409 ``already_archived`` / ``not_archived``.
    """
    auth = verify_bearer(request)
    if isinstance(auth, JSONResponse):
        return auth

    try:
        validate_module_name(module_name)
    except ValueError as exc:
        return _fail(422, str(exc))
    if module_name == "none":
        # The "All" bucket of Flows without a Group is not a Group of its own (as in patch_module).
        return _fail_code(404, "module_not_found", f"Module '{module_name}' not found")

    store = get_store()
    actor = _actor_id(auth)
    try:
        with store.transaction():
            result = _write_module_archive(store, project_id, module_name, archived, actor)
    except Exception:
        # Two first archives of a groups.module-only Group raced on the row insert: the other won.
        row = store._fetch_one(
            "SELECT archived_at FROM project_modules WHERE project_id = ? AND name = ?", [project_id, module_name],
        )
        if row is None or (row.get("archived_at") is not None) != archived:
            raise
        return _fail_code(409, "already_archived" if archived else "not_archived",
                          f"Module '{module_name}' archive state already changed")
    if isinstance(result, JSONResponse):
        return result

    try:
        from modules.flow_gate.graph_workflow.services import canvas_events
        canvas_events.publish_module_archive_changed(
            project_id=project_id, module=module_name, archived=archived, actor_user_id=actor,
        )
    except Exception:
        pass  # Best-effort refresh signal; the stored state is the source of truth.
    return JSONResponse(content=_module_archive_view(result or {}, project_id, module_name))


@router.post("/projects/{project_id}/modules/{module_name}/archive")
def archive_module(request: Request, project_id: str, module_name: str):
    """Hide a Group from the default project tree (``include_archived=true`` still shows it)."""
    return _change_module_archive(request, project_id, module_name, True)


@router.post("/projects/{project_id}/modules/{module_name}/restore")
def restore_module(request: Request, project_id: str, module_name: str):
    """Show an archived Group in the default project tree again. Its Flows' own archive state is kept."""
    return _change_module_archive(request, project_id, module_name, False)


# ── DELETE /projects/{project_id}/modules/{module_name} (v02gui.0027 the earlier implementation) ──────────────────

_COUNT_MODULE_FLOWS = (
    "SELECT COUNT(*) AS n FROM groups g WHERE g.project_id = ? AND g.module = ? AND g.deleted_at IS NULL"
)


@router.delete("/projects/{project_id}/modules/{module_name}")
def delete_module(request: Request, project_id: str, module_name: str):
    """Delete an empty Group (flowgate.v02gui.0027 0008-T §4).

    "Empty" = no Flow of the Group (``groups`` row with ``deleted_at IS NULL``) at all, whatever its
    Flow archive or discard (DC) state: a discarded Flow is history too. Only the Group's
    ``project_modules`` row is removed; ``groups``, documents, runs and Git tables are never touched
    (deleting a ``groups`` row would cascade into the run history). The emptiness check and the
    delete are one conditional statement, so a Flow created in between keeps the Group.
    A Group with Flows answers 409 ``module_not_empty`` with ``flow_count``; an unknown Group (or
    the ``none`` "All" bucket) 404 ``module_not_found``. Same authentication as create / archive.
    """
    auth = verify_bearer(request)
    if isinstance(auth, JSONResponse):
        return auth

    try:
        validate_module_name(module_name)
    except ValueError as exc:
        return _fail(422, str(exc))
    if module_name == "none":
        return _fail_code(404, "module_not_found", f"Module '{module_name}' not found")

    store = get_store()
    with store.transaction():
        row = store._fetch_one(
            "SELECT * FROM project_modules WHERE project_id = ? AND name = ?", [project_id, module_name],
        )
        deleted = 0
        if row is not None:
            deleted = store._execute_affected(
                "DELETE FROM project_modules WHERE project_id = ? AND name = ? AND NOT EXISTS ("
                + _COUNT_MODULE_FLOWS.replace("COUNT(*) AS n", "1") + ")",
                [project_id, module_name, project_id, module_name],
            )
        flow_count = 0
        if not deleted:
            counted = store._fetch_one(_COUNT_MODULE_FLOWS, [project_id, module_name]) or {}
            flow_count = int(counted.get("n") or 0)

    if not deleted:
        if flow_count > 0:
            return JSONResponse(status_code=409, content={
                "ok": False, "http_status": 409, "code": "module_not_empty", "flow_count": flow_count,
                "error_message": f"Module '{module_name}' still has {flow_count} Flow(s)",
                "help_url": help_url(),
            })
        return _fail_code(404, "module_not_found", f"Module '{module_name}' not found")

    actor = _actor_id(auth)
    try:
        from modules.flow_gate.graph_workflow.services import canvas_events
        canvas_events.publish_module_deleted(project_id=project_id, module=module_name, actor_user_id=actor)
    except Exception:
        pass  # Best-effort refresh signal; the stored state is the source of truth.
    return JSONResponse(content={
        "ok": True,
        "deleted": True,
        "module_id": row.get("module_id") or f"{project_id}:{module_name}",
        "project_id": project_id,
        "name": module_name,
    })
