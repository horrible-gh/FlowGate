"""T378 — Added tree & disposal API v1 track (R021 T-A).
T533 — Added single-file download + directory ZIP streaming download.

New endpoints:
  GET  /api/v1/projects/{project_id}/files/tree
  GET  /api/v1/projects/{project_id}/groups/tree
  GET  /api/v1/projects/{project_id}/files/src-content
  POST /api/v1/groups/{group_id}/dispose
  GET  /api/v1/projects/{project_id}/files/download
  GET  /api/v1/projects/{project_id}/files/download-zip
"""
from __future__ import annotations

import shutil
import tempfile
import threading
import zipfile
from urllib.parse import quote

import anyio.to_thread
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, StreamingResponse
from pydantic import BaseModel
from starlette.requests import Request

from modules.flow_gate import process_service
from modules.flow_gate.rbac.permission_service import has_permission
from modules.flow_gate.services.auth_outbound import verify_bearer

router = APIRouter(prefix="/api/v1", tags=["Tree"])


class SrcContentUpdate(BaseModel):
    content: str


class SrcDeleteRequest(BaseModel):
    path: str
    type: str
    # 0327 T0004: the client sends its current group-branch context here so the delete
    # is resolved against THAT group's worktree instead of the base checkout (fail-closed,
    # never a silent base fallback). Absent (base-checkout edit) → None.
    group_id: str | None = None


# 0275 T0005 (NR0003 cause 2): these handlers do sync DB/filesystem work, so they
# must be plain `def` — FastAPI then runs them in the threadpool instead of on
# the event loop, where a slow tree load froze every in-flight request (SSE
# heartbeats included). Only group_dispose stays async (it awaits the SSE
# broadcast) and pushes its sync work through anyio.to_thread.
@router.get("/projects/{project_id}/files/tree", response_class=JSONResponse)
def get_files_tree(project_id: str, branch: str = Query("main", description="branch (currently unused, kept for interface compatibility)")):
    """Return the file tree for the project."""
    tree = process_service.get_file_tree(project_id)
    return {"data": tree}


def _resolve_src_path(project_id: str, path: str, group_id: str | None = None):
    from modules.flow_gate.db import projects as _proj
    from modules.flow_gate.services import git_service

    # 0327 T0004 (B0001 / NR0003 recommendation 3): downloading is a read, so the group-branch
    # explorer may offer it — but it has to read the GROUP's tree. Resolving a group
    # download against the base checkout would hand the user the base version of the
    # file under a group tab, silently wrong. Fail-closed like the src-content reader.
    if group_id:
        from modules.flow_gate.db import groups as _groups

        group = _groups.get_by_id(group_id)
        if group is None or group.get("project_id") != project_id:
            raise HTTPException(status_code=404, detail="Group not found in this project")
        root, reason = git_service.effective_src_root_ex(project_id, group_id)
        if root is None:
            raise HTTPException(
                status_code=409,
                detail=f"Group worktree is unavailable ({reason}); base checkout was not used",
            )
        docs_root = root.resolve()
        try:
            full_path = (docs_root / path).resolve()
            full_path.relative_to(docs_root)
        except (ValueError, OSError):
            raise HTTPException(status_code=403, detail="Forbidden")
        return full_path

    row = _proj.get_by_id(project_id)
    project_name = (row.get("project_name") or "").strip() if row else ""
    settings = _proj.get_settings(project_id)
    branch = (settings.get("branch") or "main").strip() if settings else "main"

    if not project_name:
        raise HTTPException(status_code=404, detail="Not found")

    # 0319 B0001: a git-integrated project's base checkout lives under the git
    # base_branch; resolve base file reads/edits there (non-integrated → unchanged).
    docs_root = git_service.base_src_root(project_id, project_name, branch).resolve()
    try:
        full_path = (docs_root / path).resolve()
        full_path.relative_to(docs_root)  # prevent path traversal
    except (ValueError, OSError):
        raise HTTPException(status_code=403, detail="Forbidden")
    return full_path


def _resolve_delete_path(project_id: str, path: str, group_id: str | None = None):
    """Resolve without following symlinks and reject any symlink component.

    0327 T0004: with *group_id* the ROOT becomes that group's worktree — everything
    after it (containment, per-component symlink rejection) is the identical guard,
    just anchored at the group root instead of the base checkout.
    """
    from modules.flow_gate.db import projects as _proj
    from modules.flow_gate.services import git_service

    if group_id:
        # 0327 T0004: NR0003 recommendation 4 assumed "delete == hard-delete in the BASE checkout",
        # which is why it was entangled with the finalize E3 base-contamination guard.
        # Resolved against the group's own worktree the base is not touched at all, so a
        # group delete is just a working-tree change on that group's branch — the same
        # reasoning that already governs create/upload here. Fail-closed: an unresolvable
        # worktree is a 409, never a quiet fallback that would delete from base.
        from modules.flow_gate.db import groups as _groups

        group = _groups.get_by_id(group_id)
        if group is None or group.get("project_id") != project_id:
            raise HTTPException(status_code=404, detail={"code": "GROUP_NOT_FOUND"})
        wt_root, reason = git_service.effective_src_root_ex(project_id, group_id)
        if wt_root is None:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "WORKTREE_UNAVAILABLE",
                    "message": f"group worktree is unavailable ({reason}); base checkout was not used",
                },
            )
        root = wt_root.resolve()
        return _seal_under_root(root, path)

    row = _proj.get_by_id(project_id)
    project_name = (row.get("project_name") or "").strip() if row else ""
    settings = _proj.get_settings(project_id)
    branch = (settings.get("branch") or "main").strip() if settings else "main"
    if not project_name:
        raise HTTPException(status_code=404, detail="Not found")
    # 0319 B0001: delete targets the git base_branch checkout when integrated.
    root = git_service.base_src_root(project_id, project_name, branch).resolve()
    return _seal_under_root(root, path)


def _seal_under_root(root, path: str):
    """Join *path* under *root*, refusing escapes and symlink components."""
    # NR0003 finding: normalize a SINGLE backslash to '/', matching _validate_path_param.
    # Replacing only a double backslash left 'foo\bar' as one literal component, so its
    # per-component symlink check was bypassed and resolution disagreed with validation.
    candidate = root.joinpath(*path.replace("\\", "/").split("/"))
    try:
        candidate.relative_to(root)
    except ValueError:
        raise HTTPException(status_code=403, detail="Forbidden")
    current = root
    for part in candidate.relative_to(root).parts:
        current = current / part
        if current.is_symlink():
            raise HTTPException(status_code=400, detail="Symbolic links are not allowed")
    return candidate


@router.api_route("/projects/{project_id}/files/src-content", methods=["GET", "HEAD"], response_class=PlainTextResponse)
def get_src_file_content(request: Request, project_id: str, path: str = Query(..., description="relative path from docs_root")):
    """Return the content of a src-tree file as UTF-8 text.
    For HEAD requests, return only the Content-Length header (for file size checks).
    """
    full_path = _resolve_src_path(project_id, path)
    if not full_path.is_file():
        raise HTTPException(status_code=404, detail="Not found")

    file_size = full_path.stat().st_size
    if request.method == "HEAD":
        return PlainTextResponse("", headers={"Content-Length": str(file_size)})

    with open(full_path, encoding="utf-8", errors="replace") as f:
        return f.read()


@router.patch("/projects/{project_id}/files/src-content", response_class=JSONResponse)
def update_src_file_content(
    project_id: str,
    body: SrcContentUpdate,
    path: str = Query(..., description="relative path from docs_root"),
):
    """Save the content of a src-tree file as UTF-8 text."""
    full_path = _resolve_src_path(project_id, path)
    if not full_path.is_file():
        raise HTTPException(status_code=404, detail="Not found")

    full_path.write_text(body.content, encoding="utf-8")

    # flowgate.default.0176 T0010 §a: this write lands directly in the project's
    # base checkout (an intended admin edit — the write path is NOT changed). That
    # leaves the base dirty, which blocks merge finalize for EVERY group of this
    # project via the E3 guard (NR flowgate.default.0176.0009). Return the base git
    # status so the editor can warn the operator immediately, rather than the
    # contamination staying invisible until a later finalize returns a bare 500.
    from modules.flow_gate.services import git_service
    base_git = git_service.base_checkout_dirty_status(project_id)
    return {"path": path, "content_length": len(body.content), "base_git": base_git}



@router.delete("/projects/{project_id}/files", response_class=JSONResponse)
def delete_src_path(request: Request, project_id: str, body: SrcDeleteRequest):
    """Delete one file or directory from the base checkout, or from a group's worktree."""
    auth = _check_project_auth(request, project_id)
    if isinstance(auth, JSONResponse): return auth
    # NR0003 recommendation 4: deletion requires a WRITE permission (perm_document_delete), not read.
    # Enforce it for EVERY caller — user JWTs and worker/outbound tokens alike. verify_bearer
    # only guarantees perm_document_read, so gating this check on _is_user_jwt let any worker
    # token hard-delete base-checkout files with mere read access. Checking the token's
    # issued_to against the request project can only restrict access, never widen it.
    if not has_permission(auth["issued_to"], project_id, "perm_document_delete"):
        return _err(403, "FORBIDDEN", "insufficient permission for this operation")
    # 0327 T0004: group context is resolved, not refused. NR0003 recommendation 5's blanket 403 came
    # from delete meaning "base checkout", which no longer holds — a group delete targets
    # that group's own worktree. The permission gate above still runs FIRST and unchanged.
    group_id = (body.group_id or "").strip() or None
    try: _validate_path_param(body.path)
    except HTTPException: return _err(400, "INVALID_PATH", "path is invalid")
    if body.path.replace("\\", "/").strip("/") in ("", "."): return _err(400, "INVALID_PATH", "project root cannot be deleted")
    if body.type not in ("file", "folder"): return _err(400, "TYPE_MISMATCH", "type must be file or folder")
    try: full_path = _resolve_delete_path(project_id, body.path, group_id)
    except HTTPException as exc:
        # Keep the group failures distinguishable instead of flattening every resolver
        # error into INVALID_PATH: 403 here would tell the operator "no permission" for
        # what is really a missing worktree, and mismatched semantics against the
        # download/create/upload paths that already answer 404/409.
        detail = exc.detail if isinstance(exc.detail, dict) else {}
        if detail.get("code") == "GROUP_NOT_FOUND":
            return _err(404, "GROUP_NOT_FOUND", "group not found in this project")
        if detail.get("code") == "WORKTREE_UNAVAILABLE":
            return _err(409, "WORKTREE_UNAVAILABLE", detail.get("message") or "group worktree is unavailable")
        return _err(400, "INVALID_PATH", "symbolic links are not allowed")
    if not full_path.exists(): return _err(404, "NOT_FOUND", "path does not exist")
    actual_type = "folder" if full_path.is_dir() else "file" if full_path.is_file() else ""
    if actual_type != body.type: return _err(409, "TYPE_MISMATCH", "path type does not match request")
    try: shutil.rmtree(full_path) if actual_type == "folder" else full_path.unlink()
    except OSError: return _err(500, "DELETE_FAILED", "failed to delete path")
    # NR0003 recommendation 8: a BASE delete leaves the base checkout dirty (blocking merge finalize for
    # every group of this project). Return the base git status so the explorer can refresh its
    # base-dirty markers and Git finalize warning immediately, instead of the contamination
    # staying invisible until a later finalize. A group delete never dirties base — the status
    # is still returned (unchanged contract) and simply reports base as it already was.
    from modules.flow_gate.services import git_service
    base_git = git_service.base_checkout_dirty_status(project_id)
    return {"deleted": body.path, "type": actual_type, "base_git": base_git}


# ---------------------------------------------------------------------------
# 0454 T0006 §1 — display-state pruning for the group tree
# ---------------------------------------------------------------------------
#
# B0001 ("releasing the completed-hide toggle is slow"): the explorer hides terminal
# (final-approved / discarded) groups by default, but the server shipped them anyway —
# the whole subtree travelled the wire and got JSON-parsed on every load, SSE refresh and
# retry, only for GroupExplorer to filter it back out. `include_terminal=false` drops that
# freight at the source; `true` (the default, so every existing API caller is unaffected)
# returns the flat payload byte-for-byte as before.

# The two SERVER-derived terminal flags on a group node (process_service.get_group_tree).
# A group is terminal when either is True; a missing or False flag is NOT terminal, so a
# legacy payload that predates the fields hides nothing.
_TERMINAL_GROUP_FLAGS = ("is_final_approved", "is_discarded")

# 0454 T0007 review — the overview cards (MainPanel.vue) used to warm the FULL group tree on
# every default-screen entry just to read three numbers off it, which the review found doubles
# the default (hidden) entry's transfer/parse cost past the pre-pruning baseline: pruned
# (735,105 bytes) + full (1,713,209 bytes) > full alone (rev0 finding). rev1 fixed the transfer
# cost with a SEPARATE `/groups/tree/overview_summary` route, but that route called
# process_service.get_group_tree() on its own, so the default overview screen (GroupExplorer's
# tree fetch + this route, both mounted together) ran the full DB tree-building path TWICE per
# load; and nothing refetched rev1's cache when a refresh invalidated it, so the cards went stale
# until an unrelated project/tab switch happened to reload them (both rev1 review findings).
#
# rev2 folded build_overview_summary's result into the `/groups/tree` envelope itself so both
# problems above went away — but that made `include_terminal=true` (the default) stop being
# byte-identical to the pre-T0006 API for every existing caller, which is exactly what T0006
# §1.1 ("그대로 반환한다") and the completion criteria ("기본 API 호환 ... 유지된다") rule out.
# The review on rev2 called this out.
#
# rev3 put `/groups/tree/overview_summary` back as its own route, so `/groups/tree` reverted to
# exactly the pre-T0006 `{"data": {"nodes": [...]}}` envelope for every variant, and shared the
# raw node list between the two routes through `_get_raw_tree_nodes` — but rev3's version of that
# function memoized `process_service.get_group_tree()` per project for a flat 5-second TTL, which
# the rev3 review correctly rejected on two counts: the cache was checked and released BEFORE the
# DB call ran (so two requests landing on an empty cache both missed and both ran the DB path —
# the exact rev1 regression rev3 claimed was fixed), and nothing invalidated the cache on a write
# (so a just-created document could be missing from the response for up to 5 seconds afterward).
#
# rev4 replaced the TTL cache with a plain single-flight JOIN — concurrent callers for the same
# project_id shared the one `process_service.get_group_tree()` call actually in flight, discarded
# the moment it returned — but the review on rev4 found the join itself did not know about writes
# EITHER: a caller that arrived after a document was created could still join a fetch that had
# started (and, worse, could still be running) before that write, and hand back pre-write nodes
# to a `force=true` caller that exists specifically to find the just-created document
# (DashboardView.vue's `handleRequirementCreated` / `handleRelatedDocCreated`) — rev4's join did
# not distinguish "this caller needs a guaranteed-fresh read" from "any recent-enough read will
# do". And because the tree route and the separate overview-summary route were fetched from two
# INDEPENDENT Vue watchers (GroupExplorer.vue and MainPanel.vue) reacting to the same trigger
# (project switch, SSE refresh, manual refresh), nothing guaranteed the two requests actually
# overlapped — a sequential arrival (the second landing only after the first had already
# returned) left rev4's own test suite documenting two separate `get_group_tree` calls per screen
# load, the exact rev1 regression T0006 §1.3 rules out.
#
# rev5 removes both gaps at their root instead of tuning the join window:
#
# 1. `include_summary` (see `get_groups_tree` below) folds the overview aggregate into the SAME
#    `/groups/tree` response as an OPT-IN query flag, defaulting to `False` — every caller that
#    does not pass it (every pre-existing caller, since the flag did not exist before this
#    revision) gets the exact `{"data": {"nodes": [...]}}` envelope T0006 §1.1 requires. This is
#    NOT rev2's design: rev2 added the key unconditionally to the default variant every existing
#    caller already receives; here the key only ever appears on a request that explicitly asks
#    for it. GroupExplorer.vue is now the ONLY caller that asks for it, on the exact same fetch
#    call that already serves the sidebar tree — so there is no second request left to race or to
#    duplicate the DB read: one HTTP round trip produces both artifacts, in every arrival order.
#    `get_groups_tree_overview_summary` and MainPanel's own fetch are removed as now-dead code.
# 2. `_get_raw_tree_nodes` below now takes the SAME `force` flag `fetchGroupTree`'s callers
#    already pass client-side (explorer.ts already threads it through the URL as of this
#    revision). `force=True` means, precisely, "I need a read that starts no earlier than THIS
#    request" — which is exactly what a caller that just awaited a write's own response needs
#    (DashboardView.vue's two reveal-after-create calls both pass `force=true` already, for that
#    reason, since 0454 T0006). A forced fetch therefore NEVER joins an already-in-flight one,
#    however recent — it always starts its own `process_service.get_group_tree()` call, which
#    cannot have begun before the caller's own request arrived, and therefore cannot have begun
#    before any write that request's own caller had already observed complete (the client only
#    issues the follow-up GET after awaiting the write's HTTP response). A forced fetch still
#    REGISTERS itself as the current in-flight entry, so a non-forced caller arriving while it
#    runs still shares it — sharing is only given up on the side that actually needs the
#    guarantee, not globally. There is no window, of any duration, in which a `force=true` caller
#    can be handed nodes older than a write its own request causally followed.
_WORKING_HEAD_TYPES = ("T", "N", "TS")

_raw_tree_lock = threading.Lock()
_raw_tree_inflight: dict[str, "_RawTreeFetch"] = {}


class _RawTreeFetch:
    """A `process_service.get_group_tree()` call in flight for one project_id, that later
    NON-FORCED callers for the SAME project_id join instead of starting a redundant call of their
    own (see `_get_raw_tree_nodes`). Discarded the moment the call finishes — this is a join
    point, not a cache."""

    __slots__ = ("done", "nodes", "error")

    def __init__(self) -> None:
        self.done = threading.Event()
        self.nodes: list[dict] | None = None
        self.error: Exception | None = None


def _get_raw_tree_nodes(project_id: str, force: bool = False) -> list[dict]:
    """`process_service.get_group_tree(project_id)["nodes"]`, with concurrent NON-FORCED callers
    for the same project_id joining a single in-flight DB read instead of each starting their
    own. `force=True` (rev5) opts a caller OUT of joining — see the module note above — but it
    still becomes the new in-flight entry, so later callers (forced or not) may join IT.

    Never mutated by callers (`prune_terminal_subtrees` and `build_overview_summary` both only
    read their input), so the SAME list — and the same node objects — can be handed to every
    joiner without copying.
    """
    with _raw_tree_lock:
        fetch = _raw_tree_inflight.get(project_id)
        joined = fetch is not None and not force
        if not joined:
            fetch = _raw_tree_inflight[project_id] = _RawTreeFetch()

    if joined:
        fetch.done.wait()
        if fetch.error is not None:
            raise fetch.error
        return fetch.nodes  # type: ignore[return-value]

    try:
        fetch.nodes = process_service.get_group_tree(project_id).get("nodes") or []
    except Exception as exc:
        fetch.error = exc
        raise
    finally:
        with _raw_tree_lock:
            # Only retire OUR OWN entry. A `force=True` caller arriving while this fetch was
            # still running may already have installed a fresher `_RawTreeFetch` in our place —
            # popping unconditionally would delete that newer entry instead and strand its own
            # waiters.
            if _raw_tree_inflight.get(project_id) is fetch:
                _raw_tree_inflight.pop(project_id, None)
        fetch.done.set()
    return fetch.nodes


def _is_working_head_type(type_code: str | None) -> bool:
    if not type_code:
        return False
    if type_code in _WORKING_HEAD_TYPES:
        return True
    return len(type_code) >= 2 and type_code.endswith("R")  # xR series (NR / TR / TSR / ...)


def build_overview_summary(nodes: list[dict]) -> dict:
    """Project-wide totals for the overview cards, mirroring MainPanel.vue's prior
    client-side computation exactly (including reading is_final_approved/is_discarded off the
    picked document node, which are group-only fields and so never exclude anything — an
    existing MainPanel behavior this function reproduces rather than changes).
    """
    total_documents = 0
    type_counts: dict[str, int] = {}
    last_doc_by_parent: dict[object, dict] = {}
    for node in nodes:
        if node.get("node_type") != "document":
            continue
        total_documents += 1
        type_code = node.get("type_code")
        if type_code:
            type_counts[type_code] = type_counts.get(type_code, 0) + 1
        parent_id = node.get("parent_id")
        if parent_id is None:
            continue
        current = last_doc_by_parent.get(parent_id)
        if current is None or (node.get("number") or "") > (current.get("number") or ""):
            last_doc_by_parent[parent_id] = node

    working_groups = 0
    for doc in last_doc_by_parent.values():
        if doc.get("is_final_approved") or doc.get("is_discarded"):
            continue
        if _is_working_head_type(doc.get("type_code")):
            working_groups += 1

    return {
        "total_documents": total_documents,
        "working_groups": working_groups,
        "type_distribution": [
            {"type": type_code, "count": count} for type_code, count in type_counts.items()
        ],
    }


def prune_terminal_subtrees(nodes: list[dict]) -> list[dict]:
    """Return `nodes` without terminal groups and everything reachable below them.

    Pure and flat-in / flat-out, so it can be exercised on synthetic fixtures without a DB
    (0454 T0006 §1.3). Properties it holds, each pinned by a test:

    * A terminal root is a `node_type == "group"` node carrying `is_final_approved is True`
      or `is_discarded is True`. Nothing else is a root.
    * Removal follows `parent_id` to ARBITRARY depth — one parent->children index is built
      in a single pass and walked, rather than assuming a group's only children are its own
      direct documents. A nested subgroup (and its documents) goes with its terminal ancestor.
    * A `visited` set bounds the walk, so cyclic or duplicated `parent_id` data terminates.
    * The surviving nodes keep their ORIGINAL relative order and their original objects:
      project / module / orphan nodes and non-terminal groups are untouched, no `parent_id`
      is rewritten, and no node is dropped for any other reason. A module left empty by the
      pruning still ships (registered-module display contract).
    """
    children_by_parent: dict[object, list[object]] = {}
    terminal_roots: list[object] = []
    for node in nodes:
        node_id = node.get("id")
        if node_id is None:
            continue
        children_by_parent.setdefault(node.get("parent_id"), []).append(node_id)
        if node.get("node_type") != "group":
            continue
        if any(node.get(flag) is True for flag in _TERMINAL_GROUP_FLAGS):
            terminal_roots.append(node_id)

    if not terminal_roots:
        return list(nodes)

    hidden: set = set()
    stack = list(terminal_roots)
    while stack:
        current = stack.pop()
        if current in hidden:
            continue
        hidden.add(current)
        stack.extend(children_by_parent.get(current, ()))

    return [node for node in nodes if node.get("id") not in hidden]


@router.get("/projects/{project_id}/groups/tree", response_class=JSONResponse)
def get_groups_tree(
    project_id: str,
    branch: str = Query("main", description="branch (currently unused, kept for interface compatibility)"),
    include_terminal: bool = Query(
        True,
        description=(
            "true (default) returns the full flat tree unchanged; false prunes "
            "final-approved/discarded groups and every descendant"
        ),
    ),
    include_summary: bool = Query(
        False,
        description=(
            "false (default) — envelope is exactly {\"data\": {\"nodes\": [...]}}, unchanged for "
            "every pre-existing caller. true adds an `overview_summary` key alongside `nodes` in "
            "the SAME response, computed from the raw (unpruned) node list regardless of "
            "include_terminal — see build_overview_summary."
        ),
    ),
    force: bool = Query(
        False,
        description=(
            "false (default) — this read MAY join another request's raw-tree fetch that is "
            "already in flight for the same project. true guarantees a read that starts no "
            "earlier than THIS request (never joins an in-flight fetch) — for a caller that must "
            "see the effect of a write it already knows completed. See _get_raw_tree_nodes."
        ),
    ),
):
    """Return the group tree for the project.

    T0006 §1.1 / §1.2: the envelope is `{"data": {"nodes": [...]}}` for both `include_terminal`
    variants when `include_summary` is left at its default `False` — `include_terminal=true` (the
    default, so every pre-existing caller is unaffected) returns
    `process_service.get_group_tree(project_id)`'s flat `nodes` unchanged in content, order and
    object fields, and `false` prunes terminal subtrees from the same node list and the same
    envelope shape; neither ever reintroduces nested `children`.

    `include_summary=true` (0454 T0007 rev5) is an OPT-IN addition, not a change to what any
    existing caller receives — no caller before this revision could pass it, so the default
    response for every one of them is untouched. It is how the MainPanel overview cards' totals
    now travel: GroupExplorer.vue is the only caller that passes it, on the SAME request that
    already serves the sidebar tree, so one HTTP round trip (and, thanks to `_get_raw_tree_nodes`,
    at most one `process_service.get_group_tree()` call) produces both `nodes` and
    `overview_summary` regardless of arrival order relative to any other tree fetch — see the
    module note above `_get_raw_tree_nodes` for why a separate route/request pair could not give
    that guarantee.

    `force=true` (0454 T0007 rev5) is the SERVER half of the client's existing `force` fetch
    parameter (explorer.ts fetchGroupTree) — see `_get_raw_tree_nodes`'s docstring for the
    freshness guarantee it carries.
    """
    raw_nodes = _get_raw_tree_nodes(project_id, force=force)
    nodes = raw_nodes if include_terminal else prune_terminal_subtrees(raw_nodes)
    data: dict = {"nodes": nodes}
    if include_summary:
        data["overview_summary"] = build_overview_summary(raw_nodes)
    return {"data": data}


@router.post("/groups/{group_id}/dispose", response_class=JSONResponse)
async def group_dispose(request: Request, group_id: str):
    """Process group disposal."""
    try:
        body = await request.json()
    except Exception:
        body = {}
    reason_option = str(body.get("reason_option", "")).strip()
    reason_detail = str(body.get("reason_detail", "")).strip()
    result = await anyio.to_thread.run_sync(
        lambda: process_service.dispose_group(
            group_id,
            reason_option=reason_option,
            reason_detail=reason_detail,
        )
    )
    # TR0079.0003 (rework): a group disposal must propagate over SSE so already-open
    # clients react immediately — without it the only refresh was the disposing user's
    # local explorer reload, leaving open document tabs (and other users' views) stale:
    # the action bar kept offering approve/reject/workflow actions on a now-discarded
    # group ("the action bar still lets you act on documents in a disposed group"), and nothing
    # changed until F5 ("SSE isn't applied immediately, so the action bar doesn't update"). Reuse the
    # existing GROUP_VIEW_REFRESH event: its FE handler invalidates the dashboard +
    # explorer and signals open tabs to refetch, which now surfaces group_disposed and
    # collapses the action bar. Best-effort; never fail the request on a delivery error.
    if isinstance(result, dict) and result.get("status") == "success":
        # 0192 T0005 §3: dispose_group only writes the file-less DC marker — it never
        # touched git, so the discarded group's worktree directory, unmerged local
        # work branch and ledger row all leaked (and the stale ledger row lingered in
        # the file-explorer group dropdown). Tear the slot down now, before the SSE
        # refresh below, so clients re-fetching the slot list see it gone. Best-effort
        # by contract: disposal has already succeeded and a git failure must not undo it.
        try:
            from modules.flow_gate.services import git_service
            await anyio.to_thread.run_sync(
                lambda: git_service.cleanup_disposed_group(result.get("project") or "", group_id)
            )
        except Exception:
            pass
        try:
            from modules.flow_gate.api.v1.events import publisher as _events_pub
            from modules.flow_gate.api.v1.events.event_types import EventType

            await _events_pub.broadcast_event(_events_pub.FlowEvent(
                event_type=EventType.GROUP_VIEW_REFRESH,
                payload={"group_id": group_id, "reason": "group_disposed"},
                audience="*",
                project=result.get("project"),
                group_id=group_id,
            ))
        except Exception:
            pass
    return JSONResponse(content=result)


# ---------------------------------------------------------------------------
# T533 — Download endpoint helpers
# ---------------------------------------------------------------------------

def _err(status: int, code: str, message: str) -> JSONResponse:
    """P007 §3 error response format."""
    return JSONResponse(status_code=status, content={"error": {"code": code, "message": message}})


def _validate_path_param(path: str) -> None:
    """Pre-validate the path parameter.

    Absolute paths, drive prefixes, and '..' segments are rejected immediately with 400.
    Actual path traversal prevention is handled with 403 in _resolve_src_path().
    """
    if not path:
        raise HTTPException(status_code=400, detail={"error": {"code": "INVALID_PATH", "message": "path parameter is required"}})
    normalized = path.replace("\\", "/")
    if normalized.startswith("/"):
        raise HTTPException(status_code=400, detail={"error": {"code": "INVALID_PATH", "message": "absolute paths are not allowed"}})
    if len(normalized) >= 2 and normalized[1] == ":":
        raise HTTPException(status_code=400, detail={"error": {"code": "INVALID_PATH", "message": "drive prefix is not allowed"}})
    if ".." in normalized.split("/"):
        raise HTTPException(status_code=400, detail={"error": {"code": "INVALID_PATH", "message": "'..' path segments are not allowed"}})


def _rfc5987_encode(name: str) -> str:
    """RFC 5987 UTF-8 percent-encoding — Content-Disposition filename* value."""
    return "UTF-8''" + quote(name, safe="")


def _check_project_auth(request: Request, project_id: str):
    """Verify Bearer token and check project_id permission.

    - Worker token: perm_document_read is checked inside verify_bearer() against the token's project.
    - User JWT: after verify_bearer() validation, perm_document_read is additionally checked
      against the requested project_id.

    Success: returns token_rec dict.
    Failure: returns JSONResponse (caller must return immediately).
    """
    token_rec = verify_bearer(request)
    if isinstance(token_rec, JSONResponse):
        return token_rec

    if token_rec.get("_is_user_jwt"):
        user_id: str = token_rec["issued_to"]
        if not has_permission(user_id, project_id, "perm_document_read"):
            return _err(403, "FORBIDDEN", "insufficient permission for this operation")

    return token_rec


# ---------------------------------------------------------------------------
# T533 — Single-file download
# ---------------------------------------------------------------------------

@router.get("/projects/{project_id}/files/download")
def download_file(
    request: Request,
    project_id: str,
    path: str = Query(..., description="relative path from project root (POSIX '/' separator)"),
    group_id: str | None = Query(None, description="read from this group's worktree instead of the base checkout"),
):
    """Single-file download.

    Complies with P007 §2-1 spec.
    - Response: application/octet-stream, Content-Disposition RFC 5987 UTF-8 encoding.
    - Auth: verify_bearer() + perm_document_read.
    - Path guard: _validate_path_param (400) → _resolve_src_path (403).
    - 0327 T0004: group_id downloads that group's worktree copy (NR0003 recommendation 3).
    """
    auth = _check_project_auth(request, project_id)
    if isinstance(auth, JSONResponse):
        return auth

    _validate_path_param(path)

    full_path = _resolve_src_path(project_id, path, group_id)
    if not full_path.is_file():
        return _err(404, "FILE_NOT_FOUND", "file does not exist")

    filename = full_path.name
    # RFC 5987: filename* required; include filename fallback if name is ASCII
    encoded = _rfc5987_encode(filename)
    try:
        ascii_name = filename.encode("ascii").decode("ascii")
        disposition = f'attachment; filename="{ascii_name}"; filename*={encoded}'
    except UnicodeEncodeError:
        disposition = f"attachment; filename*={encoded}"

    return FileResponse(
        path=str(full_path),
        media_type="application/octet-stream",
        headers={"Content-Disposition": disposition},
    )


# ---------------------------------------------------------------------------
# T533 — Directory ZIP streaming download
# ---------------------------------------------------------------------------

@router.get("/projects/{project_id}/files/download-zip")
def download_zip(
    request: Request,
    project_id: str,
    path: str = Query(..., description="relative path from project root to the directory to compress"),
    group_id: str | None = Query(None, description="read from this group's worktree instead of the base checkout"),
):
    """Directory ZIP streaming download.

    Complies with P007 §2-2 spec.
    - Reads the ZIP from SpooledTemporaryFile in chunks and sends via StreamingResponse.
    - Preserves directory structure; empty folders are not included.
    - Content-Disposition RFC 5987 UTF-8 encoding.
    - Auth: verify_bearer() + perm_document_read.
    - Path guard: _validate_path_param (400) → _resolve_src_path (403).
    - 0327 T0004: group_id zips that group's worktree copy (NR0003 recommendation 3).
    """
    auth = _check_project_auth(request, project_id)
    if isinstance(auth, JSONResponse):
        return auth

    _validate_path_param(path)

    full_path = _resolve_src_path(project_id, path, group_id)
    if not full_path.is_dir():
        return _err(404, "DIRECTORY_NOT_FOUND", "directory does not exist")

    dir_name = full_path.name
    zip_filename = f"{dir_name}.zip"

    def _generate_zip():
        # SpooledTemporaryFile: kept in memory if below max_size, spooled to disk if exceeded.
        buf = tempfile.SpooledTemporaryFile(max_size=16 * 1024 * 1024)
        try:
            with zipfile.ZipFile(buf, mode="w", compression=zipfile.ZIP_DEFLATED, allowZip64=True) as zf:
                for file_path in sorted(full_path.rglob("*")):
                    if file_path.is_file():
                        arcname = file_path.relative_to(full_path)
                        zf.write(str(file_path), arcname=str(arcname))
            buf.seek(0)
            while True:
                chunk = buf.read(65536)
                if not chunk:
                    break
                yield chunk
        finally:
            buf.close()

    encoded_zip = _rfc5987_encode(zip_filename)
    try:
        ascii_zip = zip_filename.encode("ascii").decode("ascii")
        disposition = f'attachment; filename="{ascii_zip}"; filename*={encoded_zip}'
    except UnicodeEncodeError:
        disposition = f"attachment; filename*={encoded_zip}"

    return StreamingResponse(
        _generate_zip(),
        media_type="application/zip",
        headers={"Content-Disposition": disposition},
    )
