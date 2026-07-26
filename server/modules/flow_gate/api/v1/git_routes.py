"""Git integration API (flowgate.default.0115 — P0005).

GET/PUT/DELETE /api/v1/projects/{project_id}/git/config
POST           /api/v1/projects/{project_id}/git/test-connection
GET/POST       /api/v1/projects/{project_id}/git/provision   (0161 P0004)
GET            /api/v1/projects/{project_id}/git/status       (0162 P §2)
POST           /api/v1/projects/{project_id}/git/fetch        (0162 P §3-1)
POST           /api/v1/projects/{project_id}/git/push         (0162 P §3-2)
POST           /api/v1/projects/{project_id}/git/cleanup      (0182 NR0003 §5)
POST           /api/v1/projects/{project_id}/git/base-commit  (0177 L0002 §2.3)
POST           /api/v1/projects/{project_id}/git/base-revert  (0177 L0002 §2.4)
GET            /api/v1/projects/{project_id}/git/diff         (0326 NR0005 §4)
GET            /api/v1/projects/{project_id}/git/groups/{group_id}/diff (0326 NR0005 §4)
GET/POST       /api/v1/groups/{group_id}/git/finalize
POST           /api/v1/groups/{group_id}/git/unmerge
GET            /api/v1/groups/{group_id}/git/merge/{merge_id}/conflicts
POST           /api/v1/groups/{group_id}/git/merge/{merge_id}/resolve
POST           /api/v1/groups/{group_id}/git/merge/{merge_id}/abort

RBAC (P0005 공통): read = project.settings.read, mutate = project.settings.edit.
Group-scoped routes resolve the project from the group_id prefix and check the
same keys manually (require_permission needs a path param named project_id).
Errors follow the source-mode envelope {"ok": false, "error": {code, message}}.
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from modules.flow_gate.auth.middleware import get_current_user
from modules.flow_gate.rbac.decorators import _has_permission, require_permission
from modules.flow_gate.services import git_service, token_service
from modules.flow_gate.services.auth_outbound import verify_bearer
from modules.flow_gate.services.git_service import GitServiceError

router = APIRouter(prefix="/api/v1", tags=["Git"])


def _error_response(status_code: int, code: str, message: str, details: Optional[dict] = None) -> JSONResponse:
    error: dict = {"code": code, "message": message}
    # Structured payload (e.g. base_dirty's file list) surfaced verbatim so the FE
    # can render an actionable, user-visible error (flowgate.default.0176 T0010 §b).
    if details:
        error["details"] = details
    return JSONResponse(
        status_code=status_code,
        content={"ok": False, "error": error},
    )


def _guard(exc: GitServiceError) -> JSONResponse:
    return _error_response(exc.status, exc.code, exc.message, getattr(exc, "details", None))


def _check_group_permission(user: dict, group_id: str, permission: str) -> Optional[JSONResponse]:
    project_id = (group_id or "").split(".", 1)[0]
    if not _has_permission(user, permission, project_id):
        return _error_response(403, "forbidden", "Forbidden")
    return None


# ── Project config (P0005 §1·§2) ─────────────────────────────────────────────

@router.get("/projects/{project_id}/git/config")
def get_git_config(
    project_id: str,
    user=Depends(require_permission("project.settings.read", "project_id")),
):
    try:
        return git_service.get_config_view(project_id)
    except GitServiceError as exc:
        return _guard(exc)


class GitConfigPut(BaseModel):
    repo_url: str
    provider: str | None = None
    username: str | None = None
    # None (or omitted) = keep the stored secret, "" = clear it (P0005 §2-1).
    secret: str | None = None
    base_branch: str | None = None
    default_finalize_action: str | None = None
    enabled: bool = False
    # LibreTranslate base URL for commit-subject translation (0173 P0003 §4-1).
    # Omitted = keep stored; "" = clear (disable). exclude_unset preserves "omitted".
    translate_url: str | None = None
    # Author of server-made commits (0237 R0001). Same omitted=keep / ""=clear
    # protocol; cleared → commits fall back to the default FlowGate identity.
    # Validated as a pair by the service (both set, or both cleared).
    author_name: str | None = None
    author_email: str | None = None
    # TR 작업범위 검증 적용 단계: observe | warn | enforce (0299 D0004 §3.6).
    # Omitted = keep stored (exclude_unset), so an older client cannot reset it.
    tr_scope_stage: str | None = None


@router.put("/projects/{project_id}/git/config")
def put_git_config(
    project_id: str,
    body: GitConfigPut,
    user=Depends(require_permission("project.settings.edit", "project_id")),
):
    try:
        return git_service.save_config(project_id, body.model_dump(exclude_unset=True))
    except GitServiceError as exc:
        return _guard(exc)


@router.delete("/projects/{project_id}/git/config")
def delete_git_config(
    project_id: str,
    user=Depends(require_permission("project.settings.edit", "project_id")),
):
    try:
        return git_service.delete_config(project_id)
    except GitServiceError as exc:
        return _guard(exc)


class GitTestBody(BaseModel):
    repo_url: str | None = None
    username: str | None = None
    secret: str | None = None
    base_branch: str | None = None
    provider: str | None = None


@router.post("/projects/{project_id}/git/test-connection")
def test_git_connection(
    project_id: str,
    body: GitTestBody | None = None,
    user=Depends(require_permission("project.settings.edit", "project_id")),
):
    try:
        override = body.model_dump(exclude_unset=True) if body is not None else {}
        result = git_service.test_connection(project_id, override)
        return {"ok": True, "result": result}
    except GitServiceError as exc:
        return _guard(exc)


# ── Base provisioning status / manual trigger (0161 P0004) ──────────────────

@router.get("/projects/{project_id}/git/provision")
def get_git_provision(
    project_id: str,
    user=Depends(require_permission("project.settings.read", "project_id")),
):
    try:
        return {"ok": True, "provision": git_service.provision_view(project_id)}
    except GitServiceError as exc:
        return _guard(exc)


@router.post("/projects/{project_id}/git/provision")
def post_git_provision(
    project_id: str,
    user=Depends(require_permission("project.settings.edit", "project_id")),
):
    # Synchronous by design (P0004): clone/fetch runs under GIT_NET_TIMEOUT_SEC;
    # a provisioning failure is a 200 with result.status="failed", not an error.
    try:
        return git_service.provision_manual(project_id)
    except GitServiceError as exc:
        return _guard(exc)


# ── Project git status + manual recovery (0162 P §2·§3) ─────────────────────

@router.get("/projects/{project_id}/git/status")
def get_git_status(
    project_id: str,
    user=Depends(require_permission("project.settings.read", "project_id")),
):
    """Aggregate status + finalize-pending list + count (control panel + badge)."""
    try:
        return git_service.project_git_status(project_id)
    except GitServiceError as exc:
        return _guard(exc)


@router.post("/projects/{project_id}/git/fetch")
def post_git_fetch(
    project_id: str,
    user=Depends(require_permission("project.settings.edit", "project_id")),
):
    """Recovery fetch of the base checkout (P §3-1)."""
    try:
        return git_service.manual_fetch(project_id)
    except GitServiceError as exc:
        return _guard(exc)


class GitPushBody(BaseModel):
    # base branch (default) or a group slot branch — exact match only (L §2.4).
    branch: str | None = None


@router.post("/projects/{project_id}/git/push")
def post_git_push(
    project_id: str,
    body: GitPushBody | None = None,
    user=Depends(require_permission("project.settings.edit", "project_id")),
):
    """Recovery re-push of an accumulated base/slot branch (P §3-2)."""
    try:
        return git_service.manual_push(project_id, body.branch if body else None)
    except GitServiceError as exc:
        return _guard(exc)


@router.post("/projects/{project_id}/git/cleanup")
def post_git_cleanup(
    project_id: str,
    user=Depends(require_permission("project.settings.edit", "project_id")),
):
    """Backlog sweep of finalized (merged/pushed) slot leftovers (0182 NR0003 §5)."""
    try:
        return git_service.cleanup_terminal_slots(project_id)
    except GitServiceError as exc:
        return _guard(exc)


class BaseCommitBody(BaseModel):
    # Commit subject; blank/omitted → the server derives "fix: <files>" itself
    # (0177 L0002 §2.2 — the same rule the FE uses to seed its input).
    message: str | None = None
    # 0296 T0004 (NR0003 R1): explicit base-checkout-relative paths to stage.
    # Omitted → the legacy all-dirty-tracked commit. Given → exactly these paths,
    # which MAY be untracked new files; that is the only way to get a new file
    # into the commit the group worktrees are cut from.
    paths: list[str] | None = None


@router.post("/projects/{project_id}/git/base-commit")
def post_git_base_commit(
    project_id: str,
    body: BaseCommitBody | None = None,
    user=Depends(require_permission("project.settings.edit", "project_id")),
):
    """Explicit commit of the base checkout (0177 L0002 §2.3, 0296 T0004)."""
    try:
        return git_service.base_commit(
            project_id,
            body.message if body else None,
            body.paths if body else None,
        )
    except GitServiceError as exc:
        return _guard(exc)


class BaseRevertBody(BaseModel):
    # Base-checkout-relative paths to restore to HEAD (1+ required).
    files: list[str] = []


@router.post("/projects/{project_id}/git/base-revert")
def post_git_base_revert(
    project_id: str,
    body: BaseRevertBody,
    user=Depends(require_permission("project.settings.edit", "project_id")),
):
    """Per-file restore of the base checkout to HEAD (0177 L0002 §2.4)."""
    try:
        return git_service.base_revert(project_id, body.files)
    except GitServiceError as exc:
        return _guard(exc)


# ── Group branch file explorer: checkout-free tree/blob (0186 P0005) ─────────

@router.get("/projects/{project_id}/git/groups/{group_id}/tree")
def get_group_branch_tree(
    project_id: str,
    group_id: str,
    user=Depends(require_permission("project.settings.read", "project_id")),
):
    """Recursive file tree of a group branch's HEAD commit (read-only, no checkout)."""
    try:
        return git_service.read_group_tree(project_id, group_id)
    except GitServiceError as exc:
        return _guard(exc)


@router.get("/projects/{project_id}/git/groups/{group_id}/changes")
def get_group_branch_changes(
    project_id: str,
    group_id: str,
    user=Depends(require_permission("project.settings.read", "project_id")),
):
    """Tracked paths changed from the base branch through the group worktree."""
    try:
        return git_service.read_group_changes(project_id, group_id)
    except GitServiceError as exc:
        return _guard(exc)


@router.get("/projects/{project_id}/git/diff")
def get_base_file_diff(
    project_id: str,
    path: str,
    user=Depends(require_permission("project.settings.read", "project_id")),
):
    """Old/new content of one base-checkout file (0326 R0001 / NR0005 §4).

    HEAD blob vs the working tree — the same pair the base file explorer's dirty
    markers are computed from. The client renders the line diff (NR0005 안 b).
    """
    try:
        return git_service.read_base_file_diff(project_id, path)
    except GitServiceError as exc:
        return _guard(exc)


@router.get("/projects/{project_id}/git/groups/{group_id}/diff")
def get_group_file_diff(
    project_id: str,
    group_id: str,
    path: str,
    ref: str | None = None,
    user=Depends(require_permission("project.settings.read", "project_id")),
):
    """Old/new content of one group-branch file (0326 R0001 / NR0005 §4).

    merge-base blob vs the group worktree (checkout-free fallback: the branch
    commit's blob). ``ref`` pins the commit like the blob endpoint does.
    """
    try:
        return git_service.read_group_file_diff(project_id, group_id, path, ref)
    except GitServiceError as exc:
        return _guard(exc)


@router.get("/projects/{project_id}/git/groups/{group_id}/blob")
def get_group_branch_blob(
    project_id: str,
    group_id: str,
    path: str,
    ref: str | None = None,
    user=Depends(require_permission("project.settings.read", "project_id")),
):
    """Single-file content from a group branch (read-only, checkout-free).

    ``ref`` (optional) pins the read to a full 40-hex commit sha so the client can
    align blob reads with the tree's ``commit`` and avoid a tree/blob point-in-time
    race (P0005 §3 / L0006 §2.3).
    """
    try:
        return git_service.read_group_blob(project_id, group_id, path, ref)
    except GitServiceError as exc:
        return _guard(exc)


# ── Group finalize (P0005 §5) ────────────────────────────────────────────────

@router.get("/groups/{group_id}/git/finalize")
def get_group_finalize_state(
    group_id: str, context: str | None = None, user=Depends(get_current_user)
):
    denied = _check_group_permission(user, group_id, "project.settings.read")
    if denied:
        return denied
    try:
        # context="approval" → the AC final-approval confirm dialog, which asks
        # for a display-only preliminary awaiting_choice so the git choice block
        # renders while the root is still wf_in_progress (0197 T0004 §B). Any
        # other caller (GitFinalizePanel, header) gets the persisted state.
        return git_service.get_finalize_state(
            group_id, preview_ac=(context == "approval")
        )
    except GitServiceError as exc:
        return _guard(exc)


class FinalizeBody(BaseModel):
    action: str | None = None  # default = the project's configured default
    # Confirmed commit subject for the absorb commit (0173 P0003 §3). Blank/omitted
    # → the server resolves it (unmanned path); >200 chars (normalized) → 422.
    commit_message: str | None = None


@router.post("/groups/{group_id}/git/finalize")
def post_group_finalize(
    group_id: str,
    body: FinalizeBody | None = None,
    user=Depends(get_current_user),
):
    denied = _check_group_permission(user, group_id, "project.settings.edit")
    if denied:
        return denied
    try:
        return git_service.finalize(
            group_id,
            body.action if body else None,
            body.commit_message if body else None,
        )
    except GitServiceError as exc:
        return _guard(exc)


class UnmergeBody(BaseModel):
    merge_commit: str


@router.post("/groups/{group_id}/git/unmerge")
def post_group_unmerge(
    group_id: str,
    body: UnmergeBody,
    user=Depends(get_current_user),
):
    denied = _check_group_permission(user, group_id, "project.settings.edit")
    if denied:
        return denied
    try:
        return git_service.unmerge(group_id, body.merge_commit)
    except GitServiceError as exc:
        return _guard(exc)


# ── Conflict session (P0005 §6) ──────────────────────────────────────────────

@router.get("/groups/{group_id}/git/merge/{merge_id}/conflicts")
def get_merge_conflicts(group_id: str, merge_id: int, user=Depends(get_current_user)):
    denied = _check_group_permission(user, group_id, "project.settings.read")
    if denied:
        return denied
    try:
        return git_service.list_conflicts(group_id, merge_id)
    except GitServiceError as exc:
        return _guard(exc)


class ResolveFile(BaseModel):
    path: str
    content: str


class ResolveBody(BaseModel):
    files: list[ResolveFile] = []
    complete: bool = False


@router.post("/groups/{group_id}/git/merge/{merge_id}/resolve")
def post_merge_resolve(
    group_id: str,
    merge_id: int,
    body: ResolveBody,
    user=Depends(get_current_user),
):
    denied = _check_group_permission(user, group_id, "project.settings.edit")
    if denied:
        return denied
    try:
        return git_service.resolve_conflicts(
            group_id, merge_id,
            [f.model_dump() for f in body.files],
            bool(body.complete),
        )
    except GitServiceError as exc:
        return _guard(exc)


@router.post("/groups/{group_id}/git/merge/{merge_id}/resolve-token")
def post_merge_resolve_token(
    group_id: str,
    merge_id: int,
    body: ResolveBody,
    request: Request,
):
    auth = verify_bearer(request)
    if isinstance(auth, JSONResponse):
        return auth
    if auth.get("_is_user_jwt"):
        return _error_response(403, "conflict_token_required", "A resolve_conflict worker token is required")
    if (
        auth.get("action_scope") != "resolve_conflict"
        or auth.get("group_id") != group_id
        or int(auth.get("merge_id") or -1) != int(merge_id)
    ):
        return _error_response(403, "conflict_token_scope_mismatch", "Token is not bound to this merge session")
    try:
        result = git_service.resolve_conflicts(
            group_id, merge_id,
            [f.model_dump() for f in body.files],
            bool(body.complete),
        )
        if result.get("ok") and result.get("result", {}).get("status") == "merged":
            token_service.consume(auth["token_id"], auth["project"])
        return result
    except GitServiceError as exc:
        return _guard(exc)


@router.post("/groups/{group_id}/git/merge/{merge_id}/abort")
def post_merge_abort(group_id: str, merge_id: int, user=Depends(get_current_user)):
    denied = _check_group_permission(user, group_id, "project.settings.edit")
    if denied:
        return denied
    try:
        return git_service.abort_merge(group_id, merge_id)
    except GitServiceError as exc:
        return _guard(exc)
