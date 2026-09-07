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
POST           /api/v1/projects/{project_id}/git/base-remove  (0350 T0004)
GET            /api/v1/projects/{project_id}/git/diff         (0326 NR0005 §4)
GET            /api/v1/projects/{project_id}/git/groups/{group_id}/diff (0326 NR0005 §4)
GET/POST       /api/v1/groups/{group_id}/git/finalize
POST           /api/v1/groups/{group_id}/git/unmerge
GET            /api/v1/groups/{group_id}/git/merge/{merge_id}/conflicts
POST           /api/v1/groups/{group_id}/git/merge/{merge_id}/tr-commit
POST           /api/v1/groups/{group_id}/git/merge/{merge_id}/resolve
POST           /api/v1/groups/{group_id}/git/merge/{merge_id}/resolve-token
POST           /api/v1/groups/{group_id}/git/merge/{merge_id}/abort
GET            /api/v1/groups/{group_id}/git/merge/{merge_id}/review          (0481 D0006/L0007)
GET            /api/v1/groups/{group_id}/git/merge/{merge_id}/review-diff     (0481 D0006/L0007)
POST           /api/v1/groups/{group_id}/git/merge/{merge_id}/approve         (0481 D0006/L0007 — human only)
POST           /api/v1/groups/{group_id}/git/merge/{merge_id}/reject          (0481 D0006/L0007 — human only)
POST           /api/v1/groups/{group_id}/git/merge/{merge_id}/review-message  (0481 D0006/L0007 — human only)
POST           /api/v1/groups/{group_id}/git/merge/{merge_id}/write-plan-token (0481 T0008 item 1 — resolve_conflict worker token only)

RBAC (P0005, common): read = project.settings.read, mutate = project.settings.edit.
Group-scoped routes resolve the project from the group_id prefix and check the
same keys manually (require_permission needs a path param named project_id).
Errors follow the source-mode envelope {"ok": false, "error": {code, message}}.
"""
from __future__ import annotations

import re
from typing import Optional

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict

from modules.flow_gate.auth.middleware import get_current_user
from modules.flow_gate.rbac.decorators import _has_permission, require_permission
from modules.flow_gate.services import git_service, token_service, tr_commit_service
from modules.flow_gate.services.auth_outbound import verify_bearer
from modules.flow_gate.services.git_service import GitServiceError

router = APIRouter(prefix="/api/v1", tags=["Git"])

_UUID_RE = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")


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
    # TR work-scope check enforcement stage: observe | warn | enforce (0299 D0004 §3.6).
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


class BaseRemoveBody(BaseModel):
    # Base-checkout-relative UNTRACKED paths to delete (1+ required). Unlike
    # base-revert this is destructive: the file has no committed copy to fall
    # back to, so the caller loses it for good.
    files: list[str] = []


@router.post("/projects/{project_id}/git/base-remove")
def post_git_base_remove(
    project_id: str,
    body: BaseRemoveBody,
    user=Depends(require_permission("project.settings.edit", "project_id")),
):
    """Delete untracked files from the base checkout (0350 T0004) — the "remove"
    half of the base_untracked_conflict 409's "commit or remove them" guidance
    that base-commit alone could not fulfil."""
    try:
        return git_service.base_remove(project_id, body.files)
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
    markers are computed from. The client renders the line diff (NR0005 option b).
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


@router.post("/groups/{group_id}/git/update-from-base")
def post_group_update_from_base(group_id: str, user=Depends(get_current_user)):
    denied = _check_group_permission(user, group_id, "project.settings.edit")
    if denied:
        return denied
    try:
        return git_service.update_from_base(group_id)
    except GitServiceError as exc:
        return _guard(exc)


class GroupUntrackedBody(BaseModel):
    files: list[str] = []
    message: str | None = None


def _group_untracked_recover(group_id: str, body: GroupUntrackedBody, action: str, user: dict):
    denied = _check_group_permission(user, group_id, "project.settings.edit")
    if denied:
        return denied
    try:
        return git_service.group_update_untracked_recover(group_id, body.files, action, body.message)
    except GitServiceError as exc:
        return _guard(exc)


@router.post("/groups/{group_id}/git/untracked-commit")
def post_group_untracked_commit(group_id: str, body: GroupUntrackedBody, user=Depends(get_current_user)):
    return _group_untracked_recover(group_id, body, "commit", user)


@router.post("/groups/{group_id}/git/untracked-revert")
def post_group_untracked_revert(group_id: str, body: GroupUntrackedBody, user=Depends(get_current_user)):
    return _group_untracked_recover(group_id, body, "revert", user)


@router.post("/groups/{group_id}/git/untracked-remove")
def post_group_untracked_remove(group_id: str, body: GroupUntrackedBody, user=Depends(get_current_user)):
    return _group_untracked_recover(group_id, body, "remove", user)


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
    # 0481 D0006 §3.2 / L0007 §2.2: extra="forbid" rejects (422) any stray
    # auto_apply/write/approve field outright instead of silently ignoring it —
    # this endpoint has exactly two legitimate fields, files and complete, plus
    # (on the human route's subclass below) the [자동] checkbox itself.
    model_config = ConfigDict(extra="forbid")

    files: list[ResolveFile] = []
    complete: bool = False


class ResolveBodyHuman(ResolveBody):
    # The [자동] checkbox, sent ONLY on a human's own direct [해결 제출] (no AI
    # call). record_auto_authority — the one function allowed to write
    # session.auto_authority — is called from THIS route, never from
    # git_service.resolve_conflicts itself, so there is no path by which the
    # value can travel through the worker-token route below (whose body model,
    # plain ResolveBody, has no such field and forbids it as an extra key).
    auto: Optional[bool] = None


@router.post("/groups/{group_id}/git/merge/{merge_id}/resolve")
def post_merge_resolve(
    group_id: str,
    merge_id: int,
    body: ResolveBodyHuman,
    user=Depends(get_current_user),
):
    denied = _check_group_permission(user, group_id, "project.settings.edit")
    if denied:
        return denied
    try:
        if body.auto is not None:
            git_service.record_auto_authority(group_id, merge_id, bool(body.auto))
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
            resolver_run_id=auth.get("ai_run_id"),
        )
        # TR0019 — a TR conflict session ends the worker's job at `resolved_pending_review`,
        # not at `merged`: the commit is a person's press. Both are "this token is done".
        # 0481 T0008 — a general merge session now ends the worker's job the same way, at
        # resolved_pending_review (this submission's job was resolving, not approving).
        if result.get("ok") and result.get("result", {}).get("status") in (
            "merged", "resolved_pending_review",
        ):
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
        parked = git_service.tr_conflict_session(group_id)
        if parked and int(parked.get("merge_id") or -1) == int(merge_id):
            # TR0019 — same button and the same restore underneath, routed through the
            # service that owns the ledger so the abandoned attempt gets its log line.
            return tr_commit_service.abort_conflict_resolution(group_id, merge_id)
        return git_service.abort_merge(group_id, merge_id)
    except GitServiceError as exc:
        return _guard(exc)


@router.post("/groups/{group_id}/git/merge/{merge_id}/tr-commit")
def post_tr_conflict_commit(group_id: str, merge_id: int, user=Depends(get_current_user)):
    """Commit a TR conflict session whose files are all resolved (TR0019).

    Deliberately NOT part of `/resolve`. `/resolve` is reachable with the worker token an
    AI holds, and this press must not be: a revert's two sides are "delete what this TR
    did" and "the work that landed on top of it", so a marker-free file is not by itself
    evidence that the revert is right. A merge conflict already has this seam — the AI
    clears the markers, a person presses [병합] — and it matters more here, because after
    this commit the workflow strip will say the step is cancelled and nothing downstream
    re-examines that claim.
    """
    denied = _check_group_permission(user, group_id, "project.settings.edit")
    if denied:
        return denied
    try:
        return tr_commit_service.commit_conflict_resolution(group_id, merge_id)
    except GitServiceError as exc:
        return _guard(exc)


# ── General-merge review gate (flowgate.default.0481 D0006/L0007, T0008) ────
# GET review / POST approve / POST reject / POST review-message are the four new
# windows D0006 §4 [DEFERRED] left to this WP's logic design (L0007 §2.11); all
# four require a human (project.settings.edit) — a resolve_conflict worker token
# can reach /resolve and /resolve-token above, never these.

def _review_group_parts(group_id: str) -> tuple[str, Optional[str]]:
    parts = group_id.split(".")
    project_id = parts[0] if parts else group_id
    module = parts[1] if len(parts) > 1 else None
    return project_id, module


def _resolve_conflict_mention_builder(
    *, group_id: str, project_id: str, merge_id: int,
    request: Request, locale: str, messages: list[str],
    write_requested_by_human: bool = False, allow_test_edits: bool = False,
):
    def _builder(raw_token: str, scratch_dir: str) -> Optional[str]:
        from modules.flow_gate.api import token_routes as _token_routes
        from modules.flow_gate.services import invoke_mention_service

        base = _token_routes._build_mention_for_token(
            doc_ref="", group_id=group_id, project_id=project_id,
            scratch_dir=scratch_dir, raw_token=raw_token, request=request,
            ref_doc_ids=None, action_scope="resolve_conflict", locale=locale,
            continuous=False, merge_id=merge_id, continuous_review_mode=False,
            write_requested_by_human=write_requested_by_human,
            allow_test_edits=allow_test_edits,
        )
        if not base:
            return None
        return invoke_mention_service.prepend_messages_section(base, messages, locale)
    return _builder


def _start_resolve_conflict_run(
    *, group_id: str, merge_id: int, request: Request, user_id: str,
    provider_id: Optional[str], provider_pinned: bool, messages: list[str],
    write_requested_by_human: bool = False, allow_test_edits: bool = False,
) -> Optional[str]:
    """Kicks off a fresh resolve_conflict run bound to this merge session — the
    same mechanism [AI 호출] already uses, reused here for the [반려] retry run
    (D0006 §3.7), the review screen's propose-only conversation turn (§3.7/§3.9),
    and (flowgate.default.0481 T0008 item 1) its explicit [수정 적용] write turn.
    ``write_requested_by_human``/``allow_test_edits`` ride the run exactly like
    ``merge_id`` does — see ``admission.start_run``'s matching parameters — and
    are the run-start half of the anchored write-plan engine's bound contract
    (L0007 §2.5-§2.9, Q&A on 0009-TR)."""
    from modules.flow_gate.api import token_routes as _token_routes
    from modules.flow_gate.services import ai_invoke_service

    project_id, module = _review_group_parts(group_id)
    locale = request.headers.get("x-locale") or "ko"
    api_base_url = _token_routes._build_api_base(request)
    result = ai_invoke_service.start_run(
        project_id=project_id, module=module, group_id=group_id,
        doc_ref="", action_scope="resolve_conflict", mode="single",
        continuation_target_seq=None, continuation_review_mode=False,
        continuation_instruction_mode=None, continuation_locale=None,
        issued_to=user_id, api_base_url=api_base_url,
        mention_builder=_resolve_conflict_mention_builder(
            group_id=group_id, project_id=project_id, merge_id=merge_id,
            request=request, locale=locale, messages=messages,
            write_requested_by_human=write_requested_by_human,
            allow_test_edits=allow_test_edits,
        ),
        provider_id=provider_id, provider_pinned=bool(provider_pinned),
        merge_id=merge_id,
        write_requested_by_human=bool(write_requested_by_human),
        allow_test_edits=bool(allow_test_edits),
    )
    return result.get("run_id")


@router.get("/groups/{group_id}/git/merge/{merge_id}/review")
def get_merge_review(group_id: str, merge_id: int, user=Depends(get_current_user)):
    denied = _check_group_permission(user, group_id, "project.settings.read")
    if denied:
        return denied
    try:
        return git_service.get_merge_review(group_id, merge_id)
    except GitServiceError as exc:
        return _guard(exc)


@router.get("/groups/{group_id}/git/merge/{merge_id}/review-diff")
def get_merge_review_diff(
    group_id: str, merge_id: int, path: str, user=Depends(get_current_user),
):
    denied = _check_group_permission(user, group_id, "project.settings.read")
    if denied:
        return denied
    try:
        return git_service.read_merge_review_file_diff(group_id, merge_id, path)
    except GitServiceError as exc:
        return _guard(exc)


class ApproveBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    attempt_id: str
    review_fingerprint: str


@router.post("/groups/{group_id}/git/merge/{merge_id}/approve")
def post_merge_review_approve(
    group_id: str, merge_id: int, body: ApproveBody, user=Depends(get_current_user),
):
    denied = _check_group_permission(user, group_id, "project.settings.edit")
    if denied:
        return denied
    if not _UUID_RE.match(body.attempt_id or ""):
        return _error_response(400, "invalid_attempt_id", "attempt_id must be a UUID")
    try:
        return git_service.approve_merge_review(
            group_id, merge_id,
            attempt_id=body.attempt_id, review_fingerprint=body.review_fingerprint,
            authority="human",
        )
    except GitServiceError as exc:
        return _guard(exc)


class RejectBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str
    provider_id: str
    provider_pinned: bool = False


@router.post("/groups/{group_id}/git/merge/{merge_id}/reject")
def post_merge_review_reject(
    group_id: str, merge_id: int, body: RejectBody, request: Request,
    user=Depends(get_current_user),
):
    denied = _check_group_permission(user, group_id, "project.settings.edit")
    if denied:
        return denied
    reason = (body.reason or "").strip()
    if not reason or len(reason) > 4000:
        return _error_response(400, "invalid_reason", "reason must be 1..4000 characters")
    if body.provider_pinned is not True:
        return _error_response(422, "provider_not_pinned", "provider_pinned must be true")
    user_id = user.get("user_id") or user.get("id") or user.get("email") or "unknown"
    try:
        return git_service.reject_merge_review(
            group_id, merge_id, reason=reason,
            provider_id=body.provider_id, provider_pinned=True,
            start_run=lambda first_message: _start_resolve_conflict_run(
                group_id=group_id, merge_id=merge_id, request=request, user_id=user_id,
                provider_id=body.provider_id, provider_pinned=True,
                messages=[first_message] if first_message else [],
            ),
        )
    except GitServiceError as exc:
        return _guard(exc)


class ReviewMessageBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    message: str
    provider_id: str
    provider_pinned: bool = False
    apply_requested: bool = False
    # 0481 T0008 item 1 / L0007 §2.7: only meaningful with apply_requested=true —
    # [테스트 편집 포함 재지시]. A propose-only or plain apply turn defaults this
    # to false, which is also what a missing/omitted value must mean (the server
    # fixes this, never the model's own plan — see _apply_write_plan_locked).
    allow_test_edits: bool = False


@router.post("/groups/{group_id}/git/merge/{merge_id}/review-message")
def post_merge_review_message(
    group_id: str, merge_id: int, body: ReviewMessageBody, request: Request,
    user=Depends(get_current_user),
):
    denied = _check_group_permission(user, group_id, "project.settings.edit")
    if denied:
        return denied
    if body.provider_pinned is not True:
        return _error_response(422, "provider_not_pinned", "provider_pinned must be true")
    user_id = user.get("user_id") or user.get("id") or user.get("email") or "unknown"
    apply_requested = bool(body.apply_requested)
    allow_test_edits = bool(body.allow_test_edits) and apply_requested
    try:
        return git_service.send_review_message(
            group_id, merge_id, message=body.message,
            provider_id=body.provider_id, provider_pinned=True,
            apply_requested=apply_requested, allow_test_edits=allow_test_edits,
            start_run=lambda: _start_resolve_conflict_run(
                group_id=group_id, merge_id=merge_id, request=request, user_id=user_id,
                provider_id=body.provider_id, provider_pinned=True,
                messages=[body.message],
                write_requested_by_human=apply_requested,
                allow_test_edits=allow_test_edits,
            ),
        )
    except GitServiceError as exc:
        return _guard(exc)


class WritePlanAnchor(BaseModel):
    model_config = ConfigDict(extra="forbid")

    body_base64: str
    expected_count: int


class WritePlanOperation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    operation_id: str
    kind: str
    path: str
    purpose: str
    # `edit`-only fields
    expected_before_blob: Optional[str] = None
    anchor: Optional[WritePlanAnchor] = None
    replacement_bytes_base64: Optional[str] = None
    # `create_file`-only fields
    absent: Optional[bool] = None
    content_bytes_base64: Optional[str] = None
    mode: Optional[str] = None


class WritePlanBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: str
    base_fingerprint: str
    operations: list[WritePlanOperation] = []
    held_test_operations: list[WritePlanOperation] = []


@router.post("/groups/{group_id}/git/merge/{merge_id}/write-plan-token")
def post_merge_write_plan_token(
    group_id: str, merge_id: int, body: WritePlanBody, request: Request,
):
    """flowgate.default.0481 T0008 item 1 / L0007 §2.5-§2.9, Q&A on 0009-TR: the
    ONLY channel by which a review-message write turn's AI run can change the
    source tree — the run's own toolset is read-only (SCOPE_BOUND_TOOLS demotes
    action_scope=resolve_conflict to "read"). Same worker-token shape as
    /resolve-token: bound to exactly this group_id/merge_id, never a human JWT.

    0009-TR rev3 (AI review finding 1): action_scope/group_id/merge_id alone
    bind a token to the MERGE, not to the specific pending write TURN — any
    still-valid resolve_conflict token issued for this merge (a stale/earlier
    run's token included) would otherwise be able to inject or overwrite the
    current human-authorized turn's plan. The token's own `ai_run_id` claim is
    forwarded so the service can require it match `pending_conversation_run_id`
    exactly.
    """
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
        return _error_response(403, "conflict_token_required", "A resolve_conflict worker token is required")
    try:
        return git_service.submit_review_write_plan(
            group_id, merge_id, plan=body.model_dump(exclude_none=True),
            ai_run_id=auth.get("ai_run_id"),
        )
    except GitServiceError as exc:
        return _guard(exc)
