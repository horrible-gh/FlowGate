"""Central merge-target resolver for group finalize attempts.

flowgate.default.0594 T0012 (D0009 §T#2). The work base (group worktree origin,
update-from-base, base status, fetch/push monitoring, group diff, review package)
and the finalize TARGET (the branch a group's work is merged into) are separate
axes. This module owns only the second one:

* ``plan_finalize_target`` — validates a requested target (allowed local branch,
  never remote-only / never a registered group slot / never a workspace another
  attempt owns) and pins the base default when none is requested.
* ``open_attempt`` — every group finalize merge writes its attempt record into the
  existing ``git_merge_session`` row BEFORE git runs, with the target frozen in the
  session ``context`` (no new table/column). Every later step — conflict files,
  resolve, review, approve/reject/re-review, push reconciliation, abort, TTL sweep,
  startup recovery, unmerge eligibility — reads the target back through
  ``resolve_session_target`` instead of recomputing ``project.base_branch``.
* Managed target workspace — a non-base target is never checked out by switching
  the shared base checkout. It gets its own linked worktree at a deterministic
  path with an owner marker, owned by exactly one open attempt.

A session without a target record is a legacy session (created before this
change): it resolves to the project base and the shared base checkout.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Optional

from modules.flow_gate.db.connection import now_iso
from modules.flow_gate.storage.paths import project_dir_name

from .credentials import GitServiceError

_log = logging.getLogger(__name__)

# Session-context keys (git_merge_session.context JSON — no schema change).
TARGET_RECORD_KEY = "merge_target"
# Top-level mirror of the target branch; T#1's branch-delete guard reads this key.
TARGET_BRANCH_KEY = "target_branch"
ATTEMPT_STATE_KEY = "attempt_state"

ATTEMPT_IN_PROGRESS = "in_progress"
ATTEMPT_CONFLICT = "conflict"
ATTEMPT_COMPLETED = "completed"
ATTEMPT_FAILED = "failed"
ATTEMPT_INTERRUPTED = "interrupted"
ATTEMPT_ABORTED = "aborted"

# attempt_phase() vocabulary for OPEN sessions.
PHASE_IN_PROGRESS = "in_progress"
PHASE_INTERRUPTED = "interrupted"
PHASE_CONFLICT = "conflict"

# workspace_ownership() vocabulary — asked BEFORE any git write in a target root.
OWN_OWNED = "owned"
OWN_ABSENT = "absent"
OWN_STALE = "stale_other"
OWN_MISMATCH = "mismatch"

# What a merge was about to run from, pinned right before ``git merge`` so a crash
# between the merge commit / push and the attempt close is reconciled, not guessed.
MERGE_INPUTS_KEY = "merge_inputs"

_LANDED_NONE = "none"
_LANDED_COMPLETED = "completed"
_LANDED_UNKNOWN = "unknown"

MERGE_ACTIONS = ("merge", "merge_only")

WORKSPACES_DIR = "git_merge_targets"
WORKSPACE_TREE = "tree"
OWNER_MARKER = "owner.json"
_SLUG_RE = re.compile(r"[^A-Za-z0-9._-]+")
_SLUG_MAX = 40
_DIGEST_LEN = 16


@dataclass(frozen=True)
class MergeTargetContext:
    """One attempt's target, resolved from a single source of truth."""

    project_id: str
    base_branch: str
    target_branch: str
    is_project_base: bool
    root: Optional[Path]
    workspace_dir: Optional[Path] = None
    workspace_key: Optional[str] = None
    merge_id: Optional[int] = None
    owner: Optional[str] = None
    lock_holder: Optional[str] = None
    started_at: Optional[str] = None
    legacy: bool = False

    def public(self) -> dict:
        return {
            "target_branch": self.target_branch,
            "is_project_base": self.is_project_base,
            "merge_id": self.merge_id,
            "started_at": self.started_at,
            "legacy": self.legacy,
        }


def project_base_branch(cfg: Optional[dict]) -> str:
    return ((cfg or {}).get("base_branch") or "main").strip() or "main"


def normalize_requested_target(value: Optional[str]) -> Optional[str]:
    """``None`` for an omitted/blank request; the exact branch name otherwise.

    The name is never rewritten (no case folding, no prefix stripping) — it must
    name an existing local ref verbatim."""
    if value is None:
        return None
    if not isinstance(value, str):
        raise GitServiceError(422, "invalid_request", "git_target_branch must be a string")
    name = value.strip()
    return name or None


# ── Deterministic managed workspace path ─────────────────────────────────────

def workspace_key(branch: str) -> str:
    """Filesystem-safe, collision-free directory name for one target branch.

    ``<slug>-<sha256(branch)[:16]>``: the slug keeps the path readable (``/``,
    unicode and every other character outside ``[A-Za-z0-9._-]`` fold to ``_``),
    and the digest of the FULL, unmodified name keeps two branches that fold to the
    same slug (``a/b`` vs ``a_b``) apart. The slug never contains a separator and
    never starts with a dot, so the key cannot escape its parent directory."""
    digest = hashlib.sha256(branch.encode("utf-8")).hexdigest()[:_DIGEST_LEN]
    slug = _SLUG_RE.sub("_", branch).strip("._-")[:_SLUG_MAX].strip("._-") or "branch"
    return f"{slug}-{digest}"


def workspaces_root() -> Path:
    from modules.flow_gate.services import git_service as _gs
    return _gs.get_storage_root() / WORKSPACES_DIR


def workspace_dir(project_id: str, branch: str) -> Path:
    """``{storage}/git_merge_targets/{project_dir}/{workspace_key}``.

    Outside ``{storage}/src`` (the source explorer / src_root area) and outside the
    OS temp directory; recomputable from (project, branch) alone after a restart."""
    root = workspaces_root()
    path = root / project_dir_name(project_id) / workspace_key(branch)
    resolved_root = root.resolve()
    resolved = path.resolve()
    if resolved_root not in resolved.parents:
        raise GitServiceError(422, "invalid_request", "unsafe merge target workspace path")
    return path


def read_owner_marker(wdir: Path) -> Optional[dict]:
    """The marker dict, ``None`` when absent, ``{"_invalid": True}`` when unreadable."""
    marker = wdir / OWNER_MARKER
    try:
        if not marker.exists():
            return None
        data = json.loads(marker.read_text(encoding="utf-8"))
    except Exception:
        return {"_invalid": True}
    return data if isinstance(data, dict) else {"_invalid": True}


def _write_owner_marker(ctx: MergeTargetContext) -> None:
    assert ctx.workspace_dir is not None
    ctx.workspace_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "merge_id": ctx.merge_id,
        "owner": ctx.owner,
        "project_id": ctx.project_id,
        "target_branch": ctx.target_branch,
        "created_at": now_iso(),
    }
    (ctx.workspace_dir / OWNER_MARKER).write_text(
        json.dumps(payload, ensure_ascii=False), encoding="utf-8"
    )


# ── Session record → context ─────────────────────────────────────────────────

def target_record(session: Optional[dict]) -> Optional[dict]:
    from modules.flow_gate.services import git_service as _gs
    if not session or _gs.db_git.session_kind(session) != _gs.db_git.SESSION_KIND_MERGE:
        return None
    rec = _gs.db_git.session_context(session).get(TARGET_RECORD_KEY)
    if isinstance(rec, dict) and isinstance(rec.get("branch"), str) and rec.get("branch"):
        return rec
    return None


def _base_context(project_id: str, base_branch: str, **extra) -> MergeTargetContext:
    from modules.flow_gate.services import git_service as _gs
    return MergeTargetContext(
        project_id=project_id, base_branch=base_branch, target_branch=base_branch,
        is_project_base=True, root=_gs._base_root_of(project_id), **extra,
    )


def resolve_session_target(session: dict) -> MergeTargetContext:
    """THE target of one merge session (the single source of truth after start).

    Legacy fallback: no record → the project base and its shared checkout."""
    from modules.flow_gate.services import git_service as _gs
    group_id = session["group_id"]
    project_id = _gs._project_of_group(group_id)
    cfg = _gs.db_git.get_config(project_id) or {}
    base_branch = project_base_branch(cfg)
    merge_id = int(session["merge_id"]) if session.get("merge_id") is not None else None
    rec = target_record(session)
    if rec is None:
        return _base_context(project_id, base_branch, merge_id=merge_id, legacy=True)
    branch = rec["branch"]
    common = {
        "merge_id": merge_id,
        "owner": rec.get("owner"),
        "lock_holder": rec.get("lock_holder"),
        "started_at": rec.get("started_at"),
    }
    if rec.get("is_project_base"):
        if branch == base_branch:
            return _base_context(project_id, base_branch, **common)
        # The base setting changed after this attempt started: keep the checkout the
        # attempt actually ran in instead of following the new setting.
        project_name = _gs._project_name(project_id)
        root = _gs.src_root(project_name, branch) if project_name else None
        return MergeTargetContext(
            project_id=project_id, base_branch=branch, target_branch=branch,
            is_project_base=True, root=root, **common,
        )
    wdir = workspace_dir(project_id, branch)
    return MergeTargetContext(
        project_id=project_id, base_branch=rec.get("base_branch") or base_branch,
        target_branch=branch, is_project_base=False, root=wdir / WORKSPACE_TREE,
        workspace_dir=wdir, workspace_key=workspace_key(branch), **common,
    )


def session_merge_root(session: dict) -> tuple[Optional[Path], bool]:
    """``(root, is_base)`` where this merge session's merge state lives.

    A legacy row takes exactly the pre-T0012 route (``_base_root_of``) so recovery
    of old sessions does not even read anything new."""
    from modules.flow_gate.services import git_service as _gs
    if target_record(session) is None:
        return _gs._base_root_of(_gs._project_of_group(session["group_id"])), True
    target = resolve_session_target(session)
    return target.root, target.is_project_base


def resolve_merge_id_target(merge_id: int) -> Optional[MergeTargetContext]:
    from modules.flow_gate.services import git_service as _gs
    session = _gs.db_git.get_session(int(merge_id))
    return resolve_session_target(session) if session else None


def open_merge_attempts(project_id: str) -> list[dict]:
    """Every open finalize-merge session of the project (any phase, any target)."""
    from modules.flow_gate.services import git_service as _gs
    out: list[dict] = []
    for session in _gs.db_git.list_open_sessions():
        try:
            if _gs.db_git.session_kind(session) != _gs.db_git.SESSION_KIND_MERGE:
                continue
            if _gs._project_of_group(session["group_id"]) != project_id:
                continue
        except Exception:
            continue
        out.append(session)
    return out


def attempt_phase(session: dict, *, at_boot: bool = False) -> str:
    """Tell an in-flight attempt from a stopped one from a real conflict session.

    open + conflict files (or a review state)       → conflict
    open + no files + its project-lock holder alive → in_progress
    open + no files + lock gone                     → interrupted

    A legacy row (no target record) was only ever written on a conflict, so it
    keeps meaning exactly that. ``at_boot``: no runner survives a restart, so a
    pre-restart lock row (released later in startup) never proves liveness."""
    from modules.flow_gate.services import git_service as _gs
    rec = target_record(session)
    if rec is None:
        return PHASE_CONFLICT
    context = _gs.db_git.session_context(session)
    if context.get(ATTEMPT_STATE_KEY) == ATTEMPT_CONFLICT or context.get("review_state"):
        return PHASE_CONFLICT
    if _gs.db_git.session_files(int(session["merge_id"])):
        return PHASE_CONFLICT
    if at_boot:
        return PHASE_INTERRUPTED
    try:
        project_id = _gs._project_of_group(session["group_id"])
        lock = _gs.db_git.get_lock(project_id)
    except Exception:
        lock = None
    if lock and rec.get("lock_holder") and lock.get("holder") == rec.get("lock_holder"):
        return PHASE_IN_PROGRESS
    return PHASE_INTERRUPTED


def holds_base_checkout(session: dict) -> bool:
    """Whether this open session owns the shared base checkout's merge state.

    A non-base target never touches the base checkout. A base attempt that has not
    produced a conflict is either running under the project lock (the lock already
    serializes it) or interrupted (startup/TTL recovery closes it) — neither is a
    conflict the base gate should report."""
    from modules.flow_gate.services import git_service as _gs
    if _gs.db_git.session_kind(session) != _gs.db_git.SESSION_KIND_MERGE:
        return True
    rec = target_record(session)
    if rec is None:
        return True
    if not rec.get("is_project_base"):
        return False
    return attempt_phase(session) == PHASE_CONFLICT


# ── Validation / ownership ───────────────────────────────────────────────────

def _target_error(status: int, code: str, message: str, branch: str, **details) -> GitServiceError:
    return GitServiceError(status, code, message, details={"target_branch": branch, **details})


def validate_target_branch(project_id: str, base_root: Optional[Path], branch: str) -> None:
    """Server-side allow-list for a non-base target (never trusts a UI filter)."""
    from modules.flow_gate.services import git_service as _gs
    from .branches import internal_slot_owner
    if base_root is None or not (base_root / ".git").exists():
        raise GitServiceError(409, "invalid_state", "base checkout is not available")
    if branch.startswith("-") or branch == "HEAD":
        raise _target_error(422, "merge_target_invalid", "invalid target branch", branch)
    if not _gs._ref_exists(base_root, f"refs/heads/{branch}"):
        if _gs._ref_exists(base_root, f"refs/remotes/origin/{branch}"):
            raise _target_error(
                409, "merge_target_remote_only",
                "a remote-only branch cannot be a finalize target", branch,
            )
        raise _target_error(404, "merge_target_not_found", "target branch was not found", branch)
    # Internal slot = an ACTUAL registered group worktree branch (ledger), never a
    # name pattern.
    owner = internal_slot_owner(project_id, branch)
    if owner is not None:
        raise _target_error(
            409, "merge_target_internal_slot",
            "a registered group worktree branch cannot be a finalize target", branch,
            connected_group_id=owner.get("group_id"),
        )


def inspect_workspace(project_id: str, branch: str) -> tuple[str, dict]:
    """``(verdict, details)`` for the managed workspace of ``branch``.

    free     — no open attempt claims it and nothing is on disk
    stale    — only a marker/tree left behind by a CLOSED attempt whose record
               names the same owner (safe for the next attempt to reclaim)
    busy     — exactly one open attempt claims it and the marker agrees
    mismatch — anything else (two claims, claim without matching marker, marker
               of an unknown/other owner, unowned directory): fail closed
    """
    from modules.flow_gate.services import git_service as _gs
    wdir = workspace_dir(project_id, branch)
    claims = []
    for session in open_merge_attempts(project_id):
        rec = target_record(session)
        if rec and not rec.get("is_project_base") and rec.get("branch") == branch:
            claims.append((session, rec))
    marker = read_owner_marker(wdir)
    if claims:
        session, rec = claims[0]
        details = {
            "blocking_group_id": session.get("group_id"),
            "merge_id": session.get("merge_id"),
            "started_at": rec.get("started_at"),
        }
        if (
            len(claims) == 1 and marker and not marker.get("_invalid")
            and str(marker.get("merge_id")) == str(session.get("merge_id"))
            and marker.get("owner") == rec.get("owner")
        ):
            return "busy", details
        return "mismatch", {**details, "reason": "owner_marker_mismatch"}
    if marker is None:
        tree = wdir / WORKSPACE_TREE
        if tree.exists():
            return "mismatch", {"reason": "unowned_workspace"}
        return "free", {}
    if marker.get("_invalid"):
        return "mismatch", {"reason": "owner_marker_unreadable"}
    try:
        prior = _gs.db_git.get_session(int(marker.get("merge_id")))
    except Exception:
        prior = None
    prior_rec = target_record(prior)
    if (
        prior is not None and prior.get("status") != "open" and prior_rec
        and prior_rec.get("owner") == marker.get("owner")
        and prior_rec.get("branch") == branch
    ):
        return "stale", {"merge_id": prior.get("merge_id")}
    return "mismatch", {"reason": "owner_marker_mismatch", "merge_id": marker.get("merge_id")}


def raise_if_workspace_unavailable(project_id: str, branch: str) -> str:
    verdict, details = inspect_workspace(project_id, branch)
    if verdict == "busy":
        raise _target_error(
            409, "merge_target_busy",
            "another finalize attempt owns this target branch's workspace", branch, **details,
        )
    if verdict == "mismatch":
        raise _target_error(
            409, "merge_target_owner_mismatch",
            "the target workspace owner does not match any open attempt; refusing to take it over",
            branch, **details,
        )
    return verdict


def fixed_target_of_group(group_id: str) -> Optional[MergeTargetContext]:
    """The target pinned by this group's open finalize attempt, if any."""
    from modules.flow_gate.services import git_service as _gs
    session = _gs.db_git.get_open_session_by_group(group_id)
    if session is None or _gs.db_git.session_kind(session) != _gs.db_git.SESSION_KIND_MERGE:
        return None
    return resolve_session_target(session)


def raise_if_retarget(group_id: str, requested: Optional[str]) -> Optional[MergeTargetContext]:
    """Retarget contract: an open attempt's target never changes.

    Omitting the target is NOT a retarget request — it continues with the pinned
    target. Naming a different one is refused with the pinned target + start."""
    fixed = fixed_target_of_group(group_id)
    if fixed is not None and requested is not None and requested != fixed.target_branch:
        raise GitServiceError(
            409, "merge_target_locked",
            f"an open finalize attempt is pinned to '{fixed.target_branch}'",
            details={
                "target_branch": fixed.target_branch,
                "requested_target_branch": requested,
                "started_at": fixed.started_at,
                "merge_id": fixed.merge_id,
            },
        )
    return fixed


def set_project_default_target(project_id: str, requested: Optional[str]) -> dict:
    """Directly set/clear the project's suggested finalize target (T0016 §3.2's
    "merge target 지정/변경" action, distinct from actually running a branch
    merge). Reuses the same server-side allow-list a finalize attempt is
    checked against — never trusts the UI's filtered branch list alone."""
    from modules.flow_gate.services import git_service as _gs
    branch = normalize_requested_target(requested)
    if branch is None:
        _gs.db_git.set_default_merge_target(project_id, None)
        return {"ok": True, "default_merge_target": None}
    validate_target_branch(project_id, _gs._base_root_of(project_id), branch)
    _gs.db_git.set_default_merge_target(project_id, branch)
    return {"ok": True, "default_merge_target": branch}


def plan_finalize_target(
    group_id: str, project_id: str, cfg: dict, requested: Optional[str],
) -> MergeTargetContext:
    """Resolve + validate the target for a NEW attempt. Called once before the
    project lock (cheap reject) and again right after it (the real guard)."""
    from modules.flow_gate.services import git_service as _gs
    fixed = raise_if_retarget(group_id, requested)
    if fixed is not None:
        return fixed
    base_branch = project_base_branch(cfg)
    branch = requested or base_branch
    if branch == base_branch:
        return _base_context(project_id, base_branch)
    validate_target_branch(project_id, _gs._base_root_of(project_id), branch)
    raise_if_workspace_unavailable(project_id, branch)
    wdir = workspace_dir(project_id, branch)
    return MergeTargetContext(
        project_id=project_id, base_branch=base_branch, target_branch=branch,
        is_project_base=False, root=wdir / WORKSPACE_TREE, workspace_dir=wdir,
        workspace_key=workspace_key(branch),
    )


# ── Attempt lifecycle ────────────────────────────────────────────────────────

def open_attempt(
    group_id: str, target: MergeTargetContext, action: str, lock_holder: str, owner: str,
) -> MergeTargetContext:
    """Write the attempt record (before any git merge work) and return the pinned context."""
    from modules.flow_gate.services import git_service as _gs
    base_root = _gs._base_root_of(target.project_id)
    remote_expectation = (
        _gs._rev_parse(base_root, f"refs/remotes/origin/{target.target_branch}")
        if base_root is not None and (base_root / ".git").exists() else None
    )
    started_at = now_iso()
    record = {
        "version": 1,
        "branch": target.target_branch,
        "base_branch": target.base_branch,
        "is_project_base": target.is_project_base,
        "workspace_key": target.workspace_key,
        "owner": owner,
        "lock_holder": lock_holder,
        "started_at": started_at,
        "remote_expectation": remote_expectation,
        "finalize_action": action,
        "push": action == "merge",
    }
    merge_id = _gs.db_git.create_session(
        group_id, [], finalize_action=action,
        context={
            TARGET_BRANCH_KEY: target.target_branch,
            TARGET_RECORD_KEY: record,
            ATTEMPT_STATE_KEY: ATTEMPT_IN_PROGRESS,
        },
    )
    return replace(
        target, merge_id=merge_id, owner=owner, lock_holder=lock_holder,
        started_at=started_at, legacy=False,
    )


def _update_context(merge_id: int, **values) -> dict:
    from modules.flow_gate.services import git_service as _gs
    session = _gs.db_git.get_session(merge_id)
    context = _gs.db_git.session_context(session)
    context.update(values)
    _gs.db_git.set_session_context(merge_id, context)
    return context


def mark_conflict(ctx: MergeTargetContext, files: list[str], extra_context: dict) -> None:
    from modules.flow_gate.services import git_service as _gs
    assert ctx.merge_id is not None
    _gs.db_git.add_session_files(ctx.merge_id, files)
    _update_context(ctx.merge_id, **extra_context, **{ATTEMPT_STATE_KEY: ATTEMPT_CONFLICT})


def close_attempt(merge_id: int, state: str, *, result: Optional[dict] = None,
                  error: Optional[dict] = None) -> None:
    """Close an attempt row with its outcome. The row itself is never deleted."""
    from modules.flow_gate.services import git_service as _gs
    values = {ATTEMPT_STATE_KEY: state, "attempt_closed_at": now_iso()}
    if result is not None:
        values["attempt_result"] = result
    if error is not None:
        values["attempt_error"] = error
    _update_context(merge_id, **values)
    _gs.db_git.close_session(merge_id, "done" if state == ATTEMPT_COMPLETED else "aborted")


def close_session_attempt(session: dict, state: str, *, error: Optional[dict] = None) -> bool:
    """Close an open merge session found by abort/sweep/orphan recovery.

    A T0012 attempt records its outcome and releases ONLY its own workspace; a
    legacy row (no target record) is closed exactly as before this change.

    Returns False — and changes nothing — when the target workspace is not this
    attempt's (§9.1: a mismatch is fail-closed for the row as well as the tree)."""
    from modules.flow_gate.services import git_service as _gs
    merge_id = int(session["merge_id"])
    if target_record(session) is None:
        _gs.db_git.close_session(merge_id, "done" if state == ATTEMPT_COMPLETED else "aborted")
        return True
    target = resolve_session_target(session)
    verdict = workspace_ownership(target)
    if verdict not in (OWN_OWNED, OWN_ABSENT):
        _log.warning(
            "merge session %s: target workspace is %s — session and workspace left intact",
            merge_id, verdict,
        )
        return False
    close_attempt(merge_id, state, error=error)
    release_workspace(target)
    return True


def _return_merging_to_waiting(group_id: str) -> None:
    from modules.flow_gate.services import git_service as _gs
    state = _gs.db_git.get_state(group_id) or {}
    if (state.get("status") or "none") == "merging":
        _gs._set_status(group_id, "waiting")


def record_merge_inputs(
    ctx: MergeTargetContext, *, pre_head: Optional[str], source_head: Optional[str],
    expected_remote_head: Optional[str],
) -> None:
    """Pin what the merge is about to run from, BEFORE ``git merge`` (T0012 §6.3).

    After a crash these let recovery tell "never merged" from "the merge commit
    landed (and maybe was pushed)" instead of reading a vanished MERGE_HEAD as
    "nothing happened"."""
    assert ctx.merge_id is not None
    _update_context(ctx.merge_id, **{MERGE_INPUTS_KEY: {
        "pre_head": pre_head,
        "source_head": source_head,
        "expected_remote_head": expected_remote_head,
    }})


def _remember_default_target(ctx: MergeTargetContext) -> None:
    """T0016 §2.2: a non-base integration branch (e.g. "flowgate-v0.2") that a
    finalize actually merged into becomes the project's suggested default target
    for the NEXT group's finalize dialog, instead of resetting to base_branch
    every time this dialog opens. The base branch itself is never remembered
    here — it is already the fallback default when nothing is suggested."""
    if ctx.is_project_base:
        return
    from modules.flow_gate.services import git_service as _gs
    try:
        _gs.db_git.set_default_merge_target(ctx.project_id, ctx.target_branch)
    except Exception:
        _log.warning(
            "could not persist default merge target for project %s", ctx.project_id,
            exc_info=True,
        )


def complete_attempt(ctx: MergeTargetContext, *, merge_commit: Optional[str], pushed: bool) -> None:
    assert ctx.merge_id is not None
    close_attempt(ctx.merge_id, ATTEMPT_COMPLETED,
                  result={"merge_commit": merge_commit, "pushed": pushed})
    release_workspace(ctx)
    _remember_default_target(ctx)


def fail_attempt(ctx: MergeTargetContext, error: dict) -> None:
    """Close a still-in-progress attempt as failed and release its own workspace.

    An attempt that already turned into a conflict session (or completed) is left
    exactly as it is — its workspace still carries that session's merge state."""
    from modules.flow_gate.services import git_service as _gs
    if ctx.merge_id is None:
        return
    session = _gs.db_git.get_session(ctx.merge_id)
    if session is None or session.get("status") != "open":
        return
    if _gs.db_git.session_context(session).get(ATTEMPT_STATE_KEY) != ATTEMPT_IN_PROGRESS:
        return
    try:
        close_attempt(ctx.merge_id, ATTEMPT_FAILED, error=error)
        _return_merging_to_waiting(session["group_id"])
    finally:
        release_workspace(ctx)


def prepare_workspace(ctx: MergeTargetContext, *, detached: bool = False) -> None:
    """Materialize the managed worktree for a non-base attempt (under the lock).

    The marker is written BEFORE the worktree so a crash never leaves an unowned
    tree; a stale leftover of a closed attempt (verified by plan_finalize_target)
    is reclaimed first."""
    from modules.flow_gate.services import git_service as _gs
    if ctx.is_project_base:
        return
    assert ctx.workspace_dir is not None and ctx.root is not None
    base_root = _gs._base_root_of(ctx.project_id)
    if base_root is None:
        raise GitServiceError(409, "invalid_state", "base checkout is not available")
    if read_owner_marker(ctx.workspace_dir) is not None or ctx.root.exists():
        _remove_workspace(ctx.workspace_dir, base_root)
    _write_owner_marker(ctx)
    _gs._run_git(["worktree", "prune"], cwd=base_root)
    add_args = ["worktree", "add"]
    if detached:
        add_args.append("--detach")
    add_args.extend([str(ctx.root), ctx.target_branch])
    proc = _gs._run_git(add_args, cwd=base_root)
    if proc.returncode != 0 or not ctx.root.is_dir():
        raise _target_error(
            409, "merge_target_checkout_failed",
            "could not prepare the target branch workspace", ctx.target_branch,
            diagnostic=_gs._last_line(proc.stderr),
        )


def _remove_workspace(wdir: Path, base_root: Path) -> bool:
    from modules.flow_gate.services import git_service as _gs
    from .worktree import _force_rmtree
    tree = wdir / WORKSPACE_TREE
    if tree.exists():
        _gs._run_git(["worktree", "remove", "--force", "--force", str(tree)], cwd=base_root)
        if tree.exists() and not _force_rmtree(tree):
            _log.warning("merge target workspace could not be removed: %s", tree)
            return False
    _gs._run_git(["worktree", "prune"], cwd=base_root)
    try:
        (wdir / OWNER_MARKER).unlink()
    except FileNotFoundError:
        pass
    try:
        wdir.rmdir()
    except OSError:
        pass
    return not wdir.exists()


def release_workspace(ctx: MergeTargetContext) -> bool:
    """Remove the managed workspace ONLY when its owner marker names this attempt."""
    from modules.flow_gate.services import git_service as _gs
    if ctx.is_project_base or ctx.workspace_dir is None:
        return False
    marker = read_owner_marker(ctx.workspace_dir)
    if (
        not marker or marker.get("_invalid")
        or marker.get("owner") != ctx.owner
        or str(marker.get("merge_id")) != str(ctx.merge_id)
    ):
        if marker is not None:
            _log.warning(
                "merge target workspace %s is owned by another attempt — left intact",
                ctx.workspace_dir,
            )
        return False
    base_root = _gs._base_root_of(ctx.project_id)
    if base_root is None:
        return False
    return _remove_workspace(ctx.workspace_dir, base_root)


def workspace_ownership(ctx: MergeTargetContext) -> str:
    """Whose merge state sits in this attempt's target root — asked BEFORE any git
    write there (abort, TTL, orphan / startup / interrupted recovery; §9.1/§9.3).

    owned    — a base/legacy target (the shared checkout is serialized by the
               project lock and the base gate, not by a marker), or the marker names
               exactly this attempt (merge_id AND owner)
    absent   — no marker and no tree: nothing of anybody's is on disk
    stale    — the marker of ANOTHER attempt that is already closed and whose record
               agrees with it (inspect_workspace's reclaimable leftover): this
               attempt never took the workspace
    mismatch — anything else (another/unknown owner, unreadable marker, a tree
               without a marker): fail closed — touch neither git nor the row
    """
    from modules.flow_gate.services import git_service as _gs
    if ctx.is_project_base or ctx.workspace_dir is None:
        return OWN_OWNED
    marker = read_owner_marker(ctx.workspace_dir)
    if marker is None:
        return OWN_MISMATCH if (ctx.workspace_dir / WORKSPACE_TREE).exists() else OWN_ABSENT
    if marker.get("_invalid"):
        return OWN_MISMATCH
    same_id = str(marker.get("merge_id")) == str(ctx.merge_id)
    if same_id and marker.get("owner") == ctx.owner:
        return OWN_OWNED
    if not same_id:
        try:
            prior = _gs.db_git.get_session(int(marker.get("merge_id")))
        except Exception:
            prior = None
        prior_rec = target_record(prior)
        if (
            prior is not None and prior.get("status") != "open" and prior_rec
            and prior_rec.get("owner") == marker.get("owner")
            and prior_rec.get("branch") == ctx.target_branch
        ):
            return OWN_STALE
    return OWN_MISMATCH


def session_workspace_ownership(session: dict) -> str:
    """:func:`workspace_ownership` of one open session; a legacy row is base-owned."""
    if target_record(session) is None:
        return OWN_OWNED
    return workspace_ownership(resolve_session_target(session))


def raise_if_not_workspace_owner(ctx: MergeTargetContext) -> None:
    """409 before a user action changes git state in a workspace it does not own."""
    verdict = workspace_ownership(ctx)
    if verdict in (OWN_OWNED, OWN_ABSENT):
        return
    raise _target_error(
        409, "merge_target_owner_mismatch",
        "the target workspace is not owned by this attempt; its merge state was left intact",
        ctx.target_branch, merge_id=ctx.merge_id, reason=verdict,
    )


def recover_interrupted_attempt(session: dict, reason: str) -> Optional[str]:
    """Settle an open attempt that never reached a conflict and whose runner is gone.

    Returns ``"interrupted"`` (closed, group back to ``waiting``), ``"completed"``
    (the merge had already landed — reconciled to what really happened) or ``None``
    (left intact: not this attempt's workspace, or the outcome cannot be proven).

    * Ownership is checked FIRST; a workspace owned by someone else keeps its git
      state, and the row stays open (§9.1/§9.3).
    * A half-started merge (MERGE_HEAD) is undone with ``merge --abort`` only.
    * A merge commit that already landed is never called interrupted: without a
      push it IS the result; with a push the remote decides — our commit there →
      completed, still the pre-push position → the local merge commit is undone
      (``reset --keep`` to the recorded pre-merge head: the E6 rollback, refusing
      rather than discarding local changes) and the attempt is interrupted;
      unreachable or a third position → left open for the next pass.
    """
    ctx = resolve_session_target(session)
    assert ctx.merge_id is not None
    ownership = workspace_ownership(ctx)
    if ownership == OWN_MISMATCH:
        _log.warning(
            "interrupted attempt %s: target workspace is not its own — left intact", ctx.merge_id,
        )
        return None
    from modules.flow_gate.services import git_service as _gs
    root = ctx.root
    if ownership == OWN_OWNED and root is not None and root.exists():
        if _gs._merge_in_progress(root):
            proc = _gs._run_git(["merge", "--abort"], cwd=root)
            if proc.returncode != 0:
                _log.warning(
                    "interrupted attempt %s merge --abort failed — left intact", ctx.merge_id,
                )
                return None
        else:
            landed = _settle_landed_merge(session, ctx)
            if landed == _LANDED_COMPLETED:
                return ATTEMPT_COMPLETED
            if landed == _LANDED_UNKNOWN:
                return None
    # OWN_STALE: this attempt never took the workspace (the marker is a closed
    # attempt's reclaimable leftover) — close the row, leave the tree to the next
    # attempt's reclaim. release_workspace() refuses it by marker anyway.
    close_attempt(ctx.merge_id, ATTEMPT_INTERRUPTED, error={"code": reason})
    _return_merging_to_waiting(session["group_id"])
    release_workspace(ctx)
    return ATTEMPT_INTERRUPTED


def _flag_manual_reconciliation(merge_id: int, reason: str, observed: Optional[str]) -> None:
    _update_context(merge_id, attempt_reconcile={
        "status": "manual_reconciliation_needed", "reason": reason,
        "observed": observed, "at": now_iso(),
    })


def _query_target_remote(ctx: MergeTargetContext) -> tuple[bool, Optional[str]]:
    """``(reachable, sha)`` of ``refs/heads/<target>`` on origin; sha None = absent."""
    from modules.flow_gate.services import git_service as _gs
    cfg = _gs.db_git.get_config(ctx.project_id) or {}
    proc = _gs._run_git(
        ["ls-remote", "origin", f"refs/heads/{ctx.target_branch}"],
        cwd=ctx.root, timeout=_gs.GIT_NET_TIMEOUT_SEC,
        username=cfg.get("username"), secret=_gs._load_secret_for(cfg) or "",
    )
    if proc.returncode != 0:
        return False, None
    lines = (proc.stdout or "").strip().splitlines()
    sha = lines[0].split("\t", 1)[0].strip() if lines else ""
    return True, sha or None


def _settle_landed_merge(session: dict, ctx: MergeTargetContext) -> str:
    """Did this attempt's merge commit land before the process died? (see caller)"""
    from modules.flow_gate.services import git_service as _gs
    inputs = _gs.db_git.session_context(session).get(MERGE_INPUTS_KEY)
    if not isinstance(inputs, dict) or not inputs.get("pre_head") or not inputs.get("source_head"):
        return _LANDED_NONE                     # the merge itself never started
    root = ctx.root
    assert root is not None and ctx.merge_id is not None
    head = _gs._rev_parse(root, "HEAD")
    if not head or head == inputs["pre_head"]:
        return _LANDED_NONE                     # never committed, or already rolled back
    if (
        _gs._rev_parse(root, "HEAD^1") != inputs["pre_head"]
        or _gs._rev_parse(root, "HEAD^2") != inputs["source_head"]
    ):
        # HEAD moved to something that is not this attempt's merge commit.
        _flag_manual_reconciliation(ctx.merge_id, "target_head_moved", head)
        return _LANDED_UNKNOWN
    merge_commit = _gs._rev_parse(root, "HEAD", short=True)
    rec = target_record(session) or {}
    if not rec.get("push"):
        _finish_landed_merge(session, ctx, merge_commit, pushed=False)
        return _LANDED_COMPLETED
    reachable, observed = _query_target_remote(ctx)
    if not reachable:
        return _LANDED_UNKNOWN
    if observed == head:
        _finish_landed_merge(session, ctx, merge_commit, pushed=True)
        return _LANDED_COMPLETED
    if observed == inputs.get("expected_remote_head"):
        proc = _gs._run_git(["reset", "--keep", inputs["pre_head"]], cwd=root)
        if proc.returncode != 0:
            _log.warning(
                "interrupted attempt %s: unpushed merge commit could not be undone — left intact",
                ctx.merge_id,
            )
            return _LANDED_UNKNOWN
        return _LANDED_NONE
    _flag_manual_reconciliation(ctx.merge_id, "push_remote_third", observed)
    return _LANDED_UNKNOWN


def _finish_landed_merge(
    session: dict, ctx: MergeTargetContext, merge_commit: Optional[str], *, pushed: bool,
) -> None:
    """The clean-path end state for a merge that landed before the crash: ledger
    first, then the attempt (the same order finalize now uses), then slot cleanup."""
    from modules.flow_gate.services import git_service as _gs
    assert ctx.merge_id is not None
    group_id = session["group_id"]
    _gs._set_status(group_id, "merged", merge_id=ctx.merge_id, merge_commit=merge_commit)
    close_attempt(ctx.merge_id, ATTEMPT_COMPLETED, result={
        "merge_commit": merge_commit, "pushed": pushed, "recovered": True,
    })
    release_workspace(ctx)
    _remember_default_target(ctx)
    try:
        _gs._cleanup_group_slot(ctx.project_id, group_id)
    except Exception:
        _log.warning("slot cleanup after recovered merge %s failed", ctx.merge_id, exc_info=True)
    _gs._emit("git_finalize_done", ctx.project_id, group_id, {
        "project": ctx.project_id, "group_id": group_id, "status": "merged",
        "merge_commit": merge_commit, "pushed": pushed,
        "target_branch": ctx.target_branch, "recovered": True,
    })


def finish_completed_review(session: dict) -> bool:
    """A reviewed attempt whose review reached ``completed`` but whose row is still
    open (the process died inside the finish): finish it the same way, idempotently."""
    from modules.flow_gate.services import git_service as _gs
    context = _gs.db_git.session_context(session)
    if context.get("review_state") != _gs.REVIEW_STATE_COMPLETED:
        return False
    rec = target_record(session)
    if rec is not None:
        pushed = bool(rec.get("push"))
    else:
        pushed = (session.get("finalize_action") or context.get("finalize_action")) == "merge"
    group_id = session["group_id"]
    _gs._finish_reviewed_attempt(
        group_id, int(session["merge_id"]), _gs._project_of_group(group_id),
        (context.get("merge_commit") or "")[:7] or None, pushed,
    )
    return True


# ── Completed-attempt readers (unmerge / ledger) ─────────────────────────────

def completed_target_of_state(state: Optional[dict]) -> Optional[MergeTargetContext]:
    """The completed attempt the group ledger points at, or None (legacy/none)."""
    from modules.flow_gate.services import git_service as _gs
    if not state or state.get("merge_id") is None:
        return None
    try:
        session = _gs.db_git.get_session(int(state["merge_id"]))
    except Exception:
        return None
    if session is None or session.get("group_id") != state.get("group_id"):
        return None
    if target_record(session) is None:
        return None
    return resolve_session_target(session)


def merged_on_project_base(state: Optional[dict]) -> bool:
    """False only when the ledger proves the merge landed on a non-base target."""
    ctx = completed_target_of_state(state)
    return ctx is None or ctx.is_project_base
