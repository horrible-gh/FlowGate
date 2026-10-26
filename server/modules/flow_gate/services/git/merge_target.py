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
import contextvars
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Optional

from modules.flow_gate.db.connection import now_iso
from modules.flow_gate.db.git_integration import (  # pure session readers (0630 T0005)
    MERGE_REVIEW_SESSION_KINDS as _MERGE_REVIEW_KINDS,
    is_branch_merge_session as _is_branch_merge_session,
)
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

# 0066 D#1 §2-2: optional target-record keys. A record without them is read exactly as
# before; ``origin=manual_merge`` marks a manual merge attempt (Group kept on success),
# ``recovery_merge_commit`` an attempt that completes an earlier unpushed merge.
ORIGIN_MANUAL_MERGE = "manual_merge"
RECOVERY_MERGE_COMMIT_KEY = "recovery_merge_commit"

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
    target_kind: str = "branch"
    target_group_id: Optional[str] = None
    managed_workspace: bool = True
    # 0665 T0004: the target came from the group's work base (no explicit request).
    defaulted_to_work_base: bool = False

    @property
    def unmerge_supported(self) -> bool:
        """``finalize.unmerge`` rewinds the shared base checkout only (0594 §15)."""
        return self.is_project_base

    def public(self) -> dict:
        return {
            "target_branch": self.target_branch,
            "target_kind": self.target_kind,
            "target_group_id": self.target_group_id,
            "managed_workspace": self.managed_workspace,
            "is_project_base": self.is_project_base,
            "unmerge_supported": self.unmerge_supported,
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
    # 0630 T0005: a branch_merge attempt pins its target the same way a finalize does.
    if not session or _gs.db_git.session_kind(session) not in _MERGE_REVIEW_KINDS:
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


def _resolve_worktree_target_root(
    project_id: str, group_id: Optional[str], expected_branch: str
) -> Optional[Path]:
    """Resolve and validate the actual group worktree root for a worktree target.

    Fail closed (returns None) on missing group, deleted group, project mismatch,
    missing git state, unverified registration, ledger branch mismatch, missing directory,
    or git metadata mismatch. Never fall back to base or managed workspace.
    """
    if not group_id or not expected_branch:
        return None
    from modules.flow_gate.services import git_service as _gs
    from modules.flow_gate.db import groups as db_groups

    try:
        group_rec = db_groups.get_by_id(group_id)
        if group_rec is None or group_rec.get("deleted_at") is not None:
            return None
        if group_rec.get("project_id") != project_id:
            return None
        state = _gs.db_git.get_state(group_id)
        if state is None or state.get("project_id") != project_id:
            return None
        if not state.get("worktree_registered"):
            return None
        if (state.get("branch") or "").strip() != expected_branch:
            return None
        project_name = _gs._project_name(project_id)
        if not project_name:
            return None
        wt_path = _gs.src_root(project_name, expected_branch)
        if wt_path is None or not wt_path.exists() or not wt_path.is_dir():
            return None
        base_root = _gs._base_root_of(project_id)
        if base_root is None or not base_root.exists():
            return None
        from .branches import _validate_worktree_git_metadata
        _validate_worktree_git_metadata(base_root, wt_path, expected_branch)
        return wt_path.resolve()
    except Exception:
        _log.warning("failed to resolve worktree target root for group %s", group_id, exc_info=True)
        return None


def resolve_session_target(session: dict) -> MergeTargetContext:
    """THE target of one merge session (the single source of truth after start).

    Legacy fallback: no record → the project base and its shared checkout.
    0630 T0005: the project comes from the session's owner — a branch_merge row has no group."""
    from modules.flow_gate.services import git_service as _gs
    project_id = _gs._session_project(session)
    cfg = _gs.db_git.get_config(project_id) or {}
    base_branch = project_base_branch(cfg)
    merge_id = int(session["merge_id"]) if session.get("merge_id") is not None else None
    rec = target_record(session)
    if rec is None:
        return _base_context(project_id, base_branch, merge_id=merge_id, legacy=True)
    branch = rec["branch"]
    target_kind = rec.get("target_kind") or "branch"
    target_group_id = rec.get("target_group_id")
    managed_workspace = bool(rec.get("managed_workspace", target_kind == "branch" and not rec.get("is_project_base")))
    common = {
        "merge_id": merge_id,
        "owner": rec.get("owner"),
        "lock_holder": rec.get("lock_holder"),
        "started_at": rec.get("started_at"),
        "target_kind": target_kind,
        "target_group_id": target_group_id,
        "managed_workspace": managed_workspace,
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
    if target_kind == "worktree":
        root = _resolve_worktree_target_root(project_id, target_group_id, branch)
        return MergeTargetContext(
            project_id=project_id, base_branch=rec.get("base_branch") or base_branch,
            target_branch=branch, is_project_base=False, root=root,
            workspace_dir=None, workspace_key=None, **common,
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
    """Every open merge attempt of the project (any phase, any target): group finalize
    merges and — 0630 T0005 — ordinary branch merges, so a workspace/target claim or a
    branch-delete guard sees both owners."""
    from modules.flow_gate.services import git_service as _gs
    out: list[dict] = []
    for session in _gs.db_git.list_open_sessions():
        try:
            if _gs.db_git.session_kind(session) not in _MERGE_REVIEW_KINDS:
                continue
            if _gs._session_project(session) != project_id:
                continue
        except Exception:
            continue
        out.append(session)
    return out


def attempt_phase(session: dict, *, at_boot: bool = False) -> str:
    """Tell an in-flight attempt from a stopped one from a real conflict session.

    open + conflict files (or a review state)       → conflict
    open + no files + its job not terminal          → in_progress
    open + no files + job terminal / gone / no job  → interrupted

    A legacy row (no target record) was only ever written on a conflict, so it
    keeps meaning exactly that.

    0669 unit 6b: a final approval's attempt names ``job:{job_id}``. That job owns
    the attempt's recovery for as long as it is not terminal — it settles the attempt
    itself, under its own W/B and R, when it resumes (``approval_publish``) — so it
    reads as in progress, at boot too. Only a terminal (or vanished) job hands the
    attempt to the sweep/startup recovery. Unit 9c: every attempt runner is a job now
    (approval, manual finalize, branch merge); an attempt naming any other holder was
    left by the old project mutex, which no longer exists, so nothing can be running
    it. ``at_boot`` no longer changes the answer and is kept for the callers."""
    from modules.flow_gate.services import git_service as _gs
    rec = target_record(session)
    if rec is None:
        return PHASE_CONFLICT
    context = _gs.db_git.session_context(session)
    if context.get(ATTEMPT_STATE_KEY) == ATTEMPT_CONFLICT or context.get("review_state"):
        return PHASE_CONFLICT
    if _gs.db_git.session_files(int(session["merge_id"])):
        return PHASE_CONFLICT
    if _job_owns_attempt(rec.get("lock_holder")):
        return PHASE_IN_PROGRESS
    return PHASE_INTERRUPTED


JOB_HOLDER_PREFIX = "job:"


def _job_owns_attempt(lock_holder: Optional[str]) -> Optional[bool]:
    """None: not a job holder. True: the job is not terminal (or cannot be read — never
    take an attempt away from a job on a failed read). False: terminal or gone."""
    if not lock_holder or not str(lock_holder).startswith(JOB_HOLDER_PREFIX):
        return None
    from modules.flow_gate.db import operation_job as db_jobs
    from modules.flow_gate.db import request_cache as _request_cache
    try:
        _request_cache.invalidate()
        job = db_jobs.get_job(str(lock_holder)[len(JOB_HOLDER_PREFIX):])
    except Exception:
        _log.warning("attempt job probe failed for %s", lock_holder, exc_info=True)
        return True
    return bool(job) and job.get("status") not in db_jobs.TERMINAL_STATUSES


def holds_base_checkout(session: dict) -> bool:
    """Whether this open session owns the shared base checkout's merge state.

    A non-base target never touches the base checkout — but that does not make it
    harmless: it owns its own target's managed workspace and blocks every other attempt
    into that target (``inspect_workspace`` → ``merge_target_busy``, 0683 T0004 §5).
    This predicate answers only the base-checkout question. A base attempt that has not
    produced a conflict is either running under the project lock (the lock already
    serializes it) or interrupted (startup/TTL recovery closes it) — neither is a
    conflict the base gate should report."""
    from modules.flow_gate.services import git_service as _gs
    if _gs.db_git.session_kind(session) not in _MERGE_REVIEW_KINDS:
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
        if rec and not rec.get("is_project_base") and rec.get("managed_workspace", True) and rec.get("branch") == branch:
            claims.append((session, rec))
    marker = read_owner_marker(wdir)
    if claims:
        session, rec = claims[0]
        details = {
            "blocking_group_id": session.get("group_id"),
            "merge_id": session.get("merge_id"),
            "started_at": rec.get("started_at"),
        }
        if _is_branch_merge_session(session):
            # 0630 T0005: the claim is an ordinary branch merge, not a group's finalize.
            details["blocking_owner_type"] = _gs.db_git.OWNER_BRANCH_MERGE
            details["source_branch"] = rec.get("source_branch")
        details.update(_blocker_details(project_id, session, rec, details))
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


def _blocker_details(project_id: str, session: dict, rec: dict, details: dict) -> dict:
    """0683 T0004 §3 — what the refused caller needs to find the blocker: its owner type,
    source, where it stands (state/review_state) and the route of its own screen.
    Best-effort: a read failure here never changes the verdict it decorates."""
    from modules.flow_gate.services import git_service as _gs
    merge_id = session.get("merge_id")
    group_id = session.get("group_id")
    out: dict = {"project_id": project_id, "touched_at": session.get("touched_at")}
    try:
        context = _gs.db_git.session_context(session)
        out["attempt_state"] = context.get(ATTEMPT_STATE_KEY)
        out["review_state"] = context.get("review_state")
        if details.get("blocking_owner_type") == _gs.db_git.OWNER_BRANCH_MERGE:
            from . import branch_merge
            out["blocking_state"] = branch_merge.ui_state(session)
            out["route"] = f"/api/v1/projects/{project_id}/git/merge/{merge_id}"
        else:
            out["blocking_owner_type"] = _gs.db_git.OWNER_GROUP
            out["source_branch"] = (_gs.db_git.get_state(group_id) or {}).get("branch") if group_id else None
            out["blocking_state"] = context.get("review_state") or context.get(ATTEMPT_STATE_KEY)
            out["route"] = f"/api/v1/groups/{group_id}/git/merge/{merge_id}" if group_id else None
    except Exception:
        _log.warning("merge target blocker details failed for merge %s", merge_id, exc_info=True)
    return out


def _busy_message(branch: str, details: dict) -> str:
    """The owner type decides the wording — a branch merge is never called a finalize."""
    from modules.flow_gate.services import git_service as _gs
    merge_id = details.get("merge_id")
    if details.get("blocking_owner_type") == _gs.db_git.OWNER_BRANCH_MERGE:
        return (
            f"branch merge #{merge_id} ({details.get('source_branch') or '?'} → {branch}) "
            f"owns this target branch's workspace (state: {details.get('blocking_state') or '?'})"
        )
    return (
        f"the finalize attempt #{merge_id} of group {details.get('blocking_group_id') or '?'} "
        f"owns this target branch's workspace (state: {details.get('blocking_state') or '?'})"
    )


def _reclaim_interrupted_claim(project_id: str, merge_id) -> bool:
    """0683 T0004 §4 — re-check a busy claim at plan time instead of waiting for the sweep.

    Only an attempt whose runner is gone without ever reaching a conflict/review
    (``PHASE_INTERRUPTED``) is settled here, through the same lock + recovery the sweep
    uses (``cleanup.recover_interrupted_claim``). A live in-progress attempt, a
    conflict/review wait, an unprovable landed merge and an owner mismatch all stay
    exactly as they are, and the caller keeps refusing (fail closed)."""
    from modules.flow_gate.services import git_service as _gs
    try:
        session = _gs.db_git.get_session(int(merge_id))
    except Exception:
        return False
    if session is None or session.get("status") != "open":
        return False
    try:
        if attempt_phase(session) != PHASE_INTERRUPTED:
            return False
        from . import cleanup
        return cleanup.recover_interrupted_claim(session, project_id)
    except Exception:
        _log.warning("plan-time recovery of merge %s failed", merge_id, exc_info=True)
        return False


def raise_if_workspace_unavailable(project_id: str, branch: str) -> str:
    verdict, details = inspect_workspace(project_id, branch)
    if verdict == "busy" and _reclaim_interrupted_claim(project_id, details.get("merge_id")):
        verdict, details = inspect_workspace(project_id, branch)
    if verdict == "busy":
        raise _target_error(
            409, "merge_target_busy", _busy_message(branch, details), branch, **details,
        )
    if verdict == "mismatch":
        raise _target_error(
            409, "merge_target_owner_mismatch",
            "the target workspace owner does not match any open attempt; refusing to take it over",
            branch, **details,
        )
    return verdict


def raise_if_base_checkout_off_branch(project_id: str, base_branch: str) -> None:
    """Invariant: the shared base checkout has the project ``base_branch`` checked out.

    flowgate.v02.0086 NR0003 §5.2: a base checkout switched to another branch makes a
    non-base attempt fail at ``worktree add`` (the branch is "already used") and a base
    attempt merge into the wrong branch. Read-only — one ``symbolic-ref`` — and never
    switches, stashes or resets the checkout. A base slot that is not ``checkout``
    (empty, adopt pending, base ref missing) is left to the existing base-slot guards."""
    from modules.flow_gate.services import git_service as _gs
    base_root = _gs._base_root_of(project_id)
    if base_root is None or not _gs.git_available():
        return
    if _gs._judge_base_slot(base_root, base_branch) != "checkout":
        return
    # The full refname less its exact ``refs/heads/`` prefix (flowgate.v02.0098 T#1): ``--short``
    # answers ``heads/main`` once a tag ``main`` exists.
    proc = _gs._run_git(["symbolic-ref", "--quiet", "HEAD"], cwd=base_root)
    head_ref = (proc.stdout or "").strip() if proc.returncode == 0 else ""
    checked_out = head_ref[len("refs/heads/"):] if head_ref.startswith("refs/heads/") else ""
    if checked_out == base_branch:
        return
    shown = f"'{checked_out}'" if checked_out else "a detached HEAD"
    raise GitServiceError(
        409, "base_checkout_branch_mismatch",
        f"base checkout is on {shown} but the project base branch is '{base_branch}'",
        details={
            "base_branch": base_branch,
            "checked_out_branch": checked_out or None,
            "base_root": str(base_root),
        },
    )


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
    # flowgate.v02.0086: before the base/non-base split and outside the work-base
    # re-wrap below, so the code reaches the caller unchanged and no attempt opens.
    raise_if_base_checkout_off_branch(project_id, base_branch)
    # flowgate.default.0665 T0004 (W7): an omitted target is the GROUP's work base,
    # never the project base -- merging a branch forked from ``v0.2`` into ``main``
    # would carry the whole ``main..v0.2`` history along.  The project's remembered
    # default target is a UI suggestion only and does not participate here.
    defaulted = requested is None
    branch = requested or default_target_of_group(project_id, group_id, cfg)
    if branch == base_branch:
        return replace(_base_context(project_id, base_branch), defaulted_to_work_base=defaulted)
    try:
        validate_target_branch(project_id, _gs._base_root_of(project_id), branch)
    except GitServiceError as exc:
        if not defaulted:
            raise
        # A work base that cannot be a target is refused, not silently replaced
        # by the project base.
        raise GitServiceError(
            exc.status, "merge_target_work_base_invalid",
            "the group's work base cannot be used as the merge target; choose a target explicitly",
            details={**(exc.details or {}), "work_base_ref": branch, "cause": exc.code},
        ) from exc
    raise_if_workspace_unavailable(project_id, branch)
    wdir = workspace_dir(project_id, branch)
    return MergeTargetContext(
        project_id=project_id, base_branch=base_branch, target_branch=branch,
        is_project_base=False, root=wdir / WORKSPACE_TREE, workspace_dir=wdir,
        workspace_key=workspace_key(branch), defaulted_to_work_base=defaulted,
    )


def default_target_of_group(project_id: str, group_id: str, cfg: Optional[dict]) -> str:
    """The merge target an omitted request means: the group's work base (0665 T0004)."""
    from modules.flow_gate.services import git_service as _gs
    return (
        _gs.resolve_group_work_base_ref(project_id, group_id, config=cfg)
        or project_base_branch(cfg)
    )


def preview_target(
    group_id: str, requested: Optional[str] = None, *,
    work_base_ref: Optional[str] = None, floor_sha: Optional[str] = None,
) -> dict:
    """What merging this group into ``requested`` (or its default) would mean.

    Read-only.  ``incoming_work_base_commits`` counts work-base commits the target
    does not contain yet -- history that would flow into the target alongside the
    group's own change when the target is not the group's work base.

    flowgate.v02.0098 T#1: ``work_base_ref`` / ``floor_sha`` stand in for the group's
    current work base and its scope floor (a check card's execution re-checks against
    what its confirm recorded); ``scope_floor`` is the commit-ish the count used
    (None when nothing was counted).
    """
    from modules.flow_gate.services import git_service as _gs
    project_id = _gs._project_of_group(group_id)
    cfg = _gs.db_git.get_config(project_id) or {}
    base_branch = project_base_branch(cfg)
    work_base_ref = work_base_ref or default_target_of_group(project_id, group_id, cfg)
    target = normalize_requested_target(requested) or work_base_ref
    is_work_base = target == work_base_ref
    preview = {
        "target_branch": target,
        "work_base_ref": work_base_ref,
        "project_base_branch": base_branch,
        "is_work_base": is_work_base,
        "is_project_base": target == base_branch,
        "unmerge_supported": target == base_branch,
        "incoming_work_base_commits": None,
        "scope_floor": None,
        "warnings": [],
    }
    preview["warnings"] = _preview_warnings(target, work_base_ref, base_branch)
    base_root = _gs._base_root_of(project_id)
    if is_work_base or base_root is None or not (base_root / ".git").exists():
        return preview
    upper = floor_sha or work_base_floor(project_id, group_id, base_root, cfg, work_base_ref)
    if not _gs._ref_exists(base_root, f"refs/heads/{target}"):
        return preview
    preview["scope_floor"] = upper
    proc = _gs._run_git(["rev-list", "--count", f"refs/heads/{target}..{upper}"], cwd=base_root)
    if proc.returncode == 0:
        try:
            preview["incoming_work_base_commits"] = int((proc.stdout or "0").strip())
        except ValueError:
            pass
    return preview


def _preview_warnings(target: str, work_base_ref: str, base_branch: str) -> list:
    warnings = []
    if target != work_base_ref:
        warnings.append("target_differs_from_work_base")
    if target != base_branch:
        warnings.append("unmerge_unsupported_for_non_base_target")
    return warnings


def work_base_floor(project_id: str, group_id: str, base_root: Path, cfg: Optional[dict],
                    work_base_ref: str) -> str:
    """The upper end of :func:`preview_target`'s incoming count: the group's verified
    scope floor, else where a new group branch would fork from its work base.

    The preview's own (display) floor, unchanged; the check card judgement never uses it --
    it passes a floor read without a write and refuses when there is none
    (:func:`check_scope_floor`)."""
    from modules.flow_gate.services import git_service as _gs
    state = _gs.db_git.get_state(group_id) or {}
    upper = None
    try:
        branch = (state.get("branch") or "").strip()
        if branch and _gs._ref_exists(base_root, f"refs/heads/{branch}"):
            upper = _gs.resolve_scope_floor(
                project_id, group_id, base_root, state=state, config=cfg,
                head=f"refs/heads/{branch}",
            )["floor_sha"]
    except GitServiceError:
        upper = None
    if upper is None:
        upper = _gs._worktree_start_point(base_root, work_base_ref)
    return upper


# ── Check card merge target (flowgate.v02.0098 T#1, D0004 §3.2 / §3.3) ───────
#
# One read-only judgement of "may this group's confirm merge into this local branch",
# used alike by the check card's candidate list, its ``merge_target_branch`` save, the
# confirm's preflight and the run right before the merge (T#2). It reuses
# ``validate_target_branch``, ``inspect_workspace``, ``preview_target`` and the base
# checkout checks, and never changes a setting, a ref, a workspace or a ledger row,
# never records a floor (no legacy auto-recovery), never reclaims a stale owner and
# never runs a merge. A read it cannot complete refuses; it never stands in for "allowed".

CHECK_GIT_UNAVAILABLE = "gate_git_unavailable"
CHECK_OPTIONS_TIMEOUT = "merge_target_options_timeout"
CHECK_OPTIONS_UNAVAILABLE = "merge_target_options_unavailable"
CHECK_TARGET_CACHE_SEC = 5.0
CHECK_TARGET_BUDGET_SEC = 3.0
# The candidate list's lookup status.
CHECK_STATUS_OK = "ok"
CHECK_STATUS_EMPTY = "empty"
CHECK_STATUS_GIT_UNAVAILABLE = "git_unavailable"
CHECK_STATUS_FAILED = "failed"
CHECK_STATUS_TIMEOUT = "timeout"
# A name failing on one of these is no candidate (left out of the list) and is refused when saved, confirmed or run.
# Any other failure is the judgement's own basis failing (Git, the ledger): never read as "allowed".
CHECK_TARGET_UNFIT = frozenset({
    "merge_target_invalid", "merge_target_remote_only", "merge_target_internal_slot", "merge_target_not_found",
    "merge_target_same_branch", "merge_target_incoming_work_base", "merge_target_preview_unavailable",
    "merge_target_busy", "merge_target_owner_mismatch", "base_dirty", "merge_conflict_open",
})


@dataclass(frozen=True)
class CheckTargetBasis:
    """What every judgement of one group's check card target reads first: the authenticated group's project Git, its
    base checkout, its own branch and the work base (the group's, or the one a confirm recorded)."""

    project_id: str
    group_id: str
    cfg: dict
    base_root: Path
    base_branch: str
    work_base_ref: str
    source_branch: str
    floor_sha: Optional[str] = None
    floor_known: bool = False                  # floor_sha was read (None then: the group has no verified floor)
    base_checked: bool = False
    base_dirty: Optional[bool] = None          # read once for the candidate list; None: read when judged


def _git_unavailable(reason: str) -> GitServiceError:
    return GitServiceError(409, CHECK_GIT_UNAVAILABLE, "Git is not available for this merge target",
                           details={"reason": reason})


def check_target_basis(project_id: Optional[str], group_id: str, *,
                       work_base_ref: Optional[str] = None) -> CheckTargetBasis:
    """The basis of a check card target judgement, or 409 ``gate_git_unavailable`` with ``details.reason``:
    ``no_project`` / ``project_mismatch`` / ``git_disabled`` / ``git_unavailable`` / ``checkout_unavailable`` /
    ``group_slot_missing`` / ``probe_failed``. ``project_id`` is the authenticated group's own project."""
    from modules.flow_gate.services import git_service as _gs
    if not project_id:
        raise _git_unavailable("no_project")
    if _gs._project_of_group(group_id) != project_id:
        raise _git_unavailable("project_mismatch")
    try:
        cfg = _gs.db_git.get_config(project_id)
        if cfg is None or not cfg.get("enabled"):
            raise _git_unavailable("git_disabled")
        if not _gs.git_available():
            raise _git_unavailable("git_unavailable")
        base_root = _gs._base_root_of(project_id)
        if base_root is None or not (base_root / ".git").exists():
            raise _git_unavailable("checkout_unavailable")
        state = _gs.db_git.get_state(group_id)
        branch = ((state or {}).get("branch") or "").strip()
        if not state or not branch or not int(state.get("worktree_registered") or 0):
            raise _git_unavailable("group_slot_missing")
        work_base = work_base_ref or default_target_of_group(project_id, group_id, cfg)
    except GitServiceError:
        raise
    except Exception as exc:  # noqa: BLE001 — a basis that cannot be read is no basis (fail closed)
        _log.warning("check target basis failed for %s", group_id, exc_info=True)
        raise _git_unavailable("probe_failed") from exc
    return CheckTargetBasis(
        project_id=project_id, group_id=group_id, cfg=cfg, base_root=base_root,
        base_branch=project_base_branch(cfg), work_base_ref=work_base, source_branch=branch,
    )


def _commit_of(root: Path, ref: str) -> Optional[str]:
    from modules.flow_gate.services import git_service as _gs
    proc = _gs._run_git(["rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}"], cwd=root)
    sha = (proc.stdout or "").strip() if proc.returncode == 0 else ""
    return sha or None


def check_scope_floor(basis: CheckTargetBasis) -> Optional[str]:
    """The group's verified scope floor (the upper end of the incoming count), read without any write.

    ``resolve_scope_floor`` with ``recover=False``: a legacy group without a floor record is never recorded here
    (no floor, no audit row, no ``groups.work_base_ref`` pin). None when there is no verified floor -- no usable
    record, or one failing an integrity rule: a target other than the work base then cannot be told
    (``merge_target_preview_unavailable``) and is never measured from a branch tip instead. A ledger or Git read
    failure is the judgement's own basis failing: 409 ``gate_git_unavailable``."""
    from modules.flow_gate.services import git_service as _gs
    from .scope_base import REASON_LEDGER_UNAVAILABLE
    try:
        state = _gs.db_git.get_state(basis.group_id) or {}
        return _gs.resolve_scope_floor(basis.project_id, basis.group_id, basis.base_root, state=state,
                                       config=basis.cfg, head=f"refs/heads/{basis.source_branch}",
                                       recover=False)["floor_sha"]
    except GitServiceError as exc:
        reason = (exc.details or {}).get("reason")
        if reason == REASON_LEDGER_UNAVAILABLE:
            raise _git_unavailable("ledger_unavailable") from exc
        if reason == "git_error":
            raise _git_unavailable("probe_failed") from exc
        return None
    except Exception as exc:  # noqa: BLE001 — an unreadable record is no floor and no "allowed"
        _log.warning("check target scope floor read failed for %s", basis.group_id, exc_info=True)
        raise _git_unavailable("probe_failed") from exc


def _base_dirty(root: Path) -> bool:
    """``refs._dirty``'s tracked-change question (``--untracked-files=no``), except that a failed or timed-out
    ``git status`` stays a failure (409 ``gate_git_unavailable``) instead of reading as a clean checkout."""
    from modules.flow_gate.services import git_service as _gs
    proc = _gs._run_git(["status", "--porcelain", "--untracked-files=no"], cwd=root)
    if proc.returncode != 0:
        raise _git_unavailable("probe_failed")
    return bool((proc.stdout or "").strip())


def _raise_if_base_slot_unavailable(basis: CheckTargetBasis, own_merge_id: Optional[int]) -> None:
    """The project base target's existing slot checks: an open conflict / stopped merge, tracked local changes, and
    another open attempt into the base (in use). ``own_merge_id`` is the caller's own attempt, never "another".
    The tracked-change read happens only when nothing is stopped; it failing refuses (``gate_git_unavailable``)."""
    from .lock import base_merge_in_progress, guard_base_free

    own = str(own_merge_id) if own_merge_id is not None else None
    try:
        guard_base_free(basis.project_id)
    except GitServiceError as exc:
        if own is None or str((exc.details or {}).get("merge_id")) != own:
            raise
    stopped = base_merge_in_progress(basis.project_id)
    if stopped is not None and (own is None or str(stopped.get("merge_id")) != own):
        raise GitServiceError(409, "merge_conflict_open", "a merge is stopped in the base checkout",
                              details={"merge_id": stopped.get("merge_id"),
                                       "blocking_group_id": stopped.get("group_id")})
    if stopped is None and (basis.base_dirty if basis.base_dirty is not None else _base_dirty(basis.base_root)):
        raise GitServiceError(409, "base_dirty", "base checkout has local modifications")
    for session in open_merge_attempts(basis.project_id):
        rec = target_record(session)
        if rec and rec.get("is_project_base") and str(session.get("merge_id")) != own:
            raise _target_error(409, "merge_target_busy", "another merge attempt uses the project base",
                                basis.base_branch, merge_id=session.get("merge_id"),
                                blocking_group_id=session.get("group_id"))


def judge_check_target(
    project_id: Optional[str], group_id: str, target: str, *,
    work_base_ref: Optional[str] = None, scope_floor_sha: Optional[str] = None,
    own_merge_id: Optional[int] = None, basis: Optional[CheckTargetBasis] = None,
) -> dict:
    """THE judgement of one exact local branch name as the check card merge target of ``group_id``.

    Raises ``GitServiceError`` with the D0004 §3.3 codes: 409 ``gate_git_unavailable`` (``details.reason``), 422
    ``merge_target_invalid``, 409 ``merge_target_remote_only`` / ``merge_target_internal_slot`` /
    ``merge_target_same_branch`` / ``base_checkout_branch_mismatch`` / ``base_dirty`` / ``merge_conflict_open`` /
    ``merge_target_busy`` / ``merge_target_owner_mismatch`` / ``merge_target_preview_unavailable`` /
    ``merge_target_incoming_work_base``, 404 ``merge_target_not_found``. Otherwise returns::

        {target_branch, target_sha, project_base_branch, work_base_ref, source_branch, is_project_base,
         is_work_base, unmerge_supported, incoming_work_base_commits, scope_floor_sha, warnings}

    ``incoming_work_base_commits`` / ``scope_floor_sha`` are None for the work base itself (no count needed).
    ``work_base_ref`` / ``scope_floor_sha`` are what a confirm recorded (T#2 re-checks a run against them instead of
    the group's current work base and floor); ``own_merge_id`` is the caller's own open attempt, never "busy" (an owner
    mismatch on its workspace is still refused). Without a recorded floor the group's verified floor is read without
    any write (:func:`check_scope_floor`); a group with none cannot have a target other than its work base told.
    """
    from modules.flow_gate.services import git_service as _gs
    if basis is None:
        basis = check_target_basis(project_id, group_id, work_base_ref=work_base_ref)
    root = basis.base_root
    name = target
    if not _plain_branch_name(name):
        raise _target_error(422, "merge_target_invalid", "invalid target branch", str(name))
    checked = _gs._run_git(["check-ref-format", "--branch", name], cwd=root)
    if checked.returncode != 0 or (checked.stdout or "").strip() != name:
        raise _target_error(422, "merge_target_invalid", "target is not a valid Git branch name", name)
    try:
        validate_target_branch(basis.project_id, root, name)
    except GitServiceError as exc:
        if exc.code == "merge_target_internal_slot" and name == basis.source_branch:
            raise _slot_error(basis, name) from exc
        if exc.code == "invalid_state":
            raise _git_unavailable("checkout_unavailable") from exc
        raise
    except Exception as exc:  # noqa: BLE001 — the ledger could not be read: never an allowed target
        _log.warning("check target ledger read failed for %s", group_id, exc_info=True)
        raise _git_unavailable("ledger_unavailable") from exc
    sha = _commit_of(root, f"refs/heads/{name}")
    if sha is None:
        raise _target_error(422, "merge_target_invalid", "target branch does not resolve to a commit", name)

    def incoming_of(branch: str) -> tuple[Optional[int], Optional[str]]:
        # The floor is a recorded one or the group's verified floor, read without a write; with none there is
        # nothing to count from (never a branch tip): "cannot be told".
        floor_sha = scope_floor_sha or (basis.floor_sha if basis.floor_known else check_scope_floor(basis))
        if not floor_sha:
            return None, None
        preview = preview_target(basis.group_id, branch, work_base_ref=basis.work_base_ref, floor_sha=floor_sha)
        floor = preview.get("scope_floor")
        return preview.get("incoming_work_base_commits"), (_commit_of(root, floor) if floor else None)

    return _judge_local_target(basis, name, sha, incoming_of, own_merge_id)


def _plain_branch_name(name) -> bool:
    return (isinstance(name, str) and bool(name) and name == name.strip() and name != "HEAD"
            and not name.startswith("-") and not any(ord(c) < 32 or ord(c) == 127 for c in name))


def _slot_error(basis: CheckTargetBasis, name: str, owner_group_id: Optional[str] = None) -> GitServiceError:
    """``validate_target_branch``'s ledger verdict; the group's own slot is named as the same branch."""
    if name == basis.source_branch:
        return _target_error(409, "merge_target_same_branch", "the group's own branch cannot be its merge target", name)
    return _target_error(409, "merge_target_internal_slot",
                         "a registered group worktree branch cannot be a finalize target", name,
                         connected_group_id=owner_group_id)


def _judge_local_target(basis: CheckTargetBasis, name: str, sha: str, incoming_of,
                        own_merge_id: Optional[int]) -> dict:
    """The part of the judgement after the name resolved to a local, non-slot commit — shared by the single
    judgement and the candidate list (which only gathers the same facts in batch). ``incoming_of(name)`` answers
    ``(incoming_work_base_commits | None when it cannot be told, scope floor sha)`` for a name other than the work
    base."""
    if name == basis.source_branch:
        raise _target_error(409, "merge_target_same_branch", "the group's own branch cannot be its merge target", name)
    if not basis.base_checked:
        raise_if_base_checkout_off_branch(basis.project_id, basis.base_branch)
    is_base = name == basis.base_branch
    if is_base:
        _raise_if_base_slot_unavailable(basis, own_merge_id)
    else:
        verdict, details = inspect_workspace(basis.project_id, name)
        # Only a ``busy`` workspace (one claim, its marker naming the same attempt and owner) can be the caller's
        # own resumed attempt; a mismatch -- another owner's marker, several claims -- stays refused whatever id.
        own = verdict == "busy" and own_merge_id is not None and str(details.get("merge_id")) == str(own_merge_id)
        if verdict == "busy" and not own:
            raise _target_error(409, "merge_target_busy", _busy_message(name, details), name, **details)
        if verdict == "mismatch":
            raise _target_error(409, "merge_target_owner_mismatch",
                                "the target workspace owner does not match any open attempt", name, **details)
    incoming, floor = None, None
    if name != basis.work_base_ref:
        incoming, floor = incoming_of(name)
        if incoming is None:
            raise _target_error(409, "merge_target_preview_unavailable",
                                "whether the target lacks work base history could not be told", name,
                                work_base_ref=basis.work_base_ref)
        if incoming > 0:
            raise _target_error(409, "merge_target_incoming_work_base",
                                "the target lacks work base history that would flow into it", name,
                                work_base_ref=basis.work_base_ref, incoming_work_base_commits=incoming)
    return {
        "target_branch": name, "target_sha": sha,
        "project_base_branch": basis.base_branch, "work_base_ref": basis.work_base_ref,
        "source_branch": basis.source_branch,
        "is_project_base": is_base, "is_work_base": name == basis.work_base_ref,
        "unmerge_supported": is_base,
        "incoming_work_base_commits": incoming,
        "scope_floor_sha": floor,
        "warnings": _preview_warnings(name, basis.work_base_ref, basis.base_branch),
    }
# The candidate cache: (project, group) -> snapshot, at most CHECK_TARGET_CACHE_SEC old. Concurrent misses of one key
# share one computation; ``invalidate_check_targets`` moves the epoch, so a computation that began before it is
# neither cached nor joined afterwards.
_check_cache: dict[tuple, dict] = {}
_check_inflight: dict[tuple, "_CheckFlight"] = {}
_check_lock = threading.Lock()
_check_epoch = 0


class _CheckFlight:
    def __init__(self) -> None:
        self.done = threading.Event()
        self.result: Optional[dict] = None


def invalidate_check_targets(project_id: Optional[str] = None, group_id: Optional[str] = None) -> None:
    """Drop the cached check card candidates of one group, of a whole project (``group_id`` None) or of all.

    Called when a card's target, a work base or a project's Git settings change, and by every server path that
    changes refs, the internal slot ledger, a merge attempt or a target workspace (T#2's run paths)."""
    global _check_epoch
    with _check_lock:
        _check_epoch += 1
        for key in list(_check_cache):
            if (project_id is None or key[0] == project_id) and (group_id is None or key[1] == group_id):
                del _check_cache[key]


def _entry(branch: Optional[str], *, checked: bool, error: Optional[str] = None,
           unmerge_supported: Optional[bool] = None, warnings: Optional[list] = None) -> dict:
    return {"branch": branch, "checked": checked, "error": error, "unmerge_supported": unmerge_supported,
            "warnings": list(warnings or [])}


def _verdict(basis: CheckTargetBasis, name: str) -> dict:
    """One name's entry; a basis failure (not ``CHECK_TARGET_UNFIT``) is raised."""
    try:
        judged = judge_check_target(basis.project_id, basis.group_id, name, basis=basis)
    except GitServiceError as exc:
        if exc.code not in CHECK_TARGET_UNFIT:
            raise
        return _entry(name, checked=True, error=exc.code, unmerge_supported=name == basis.base_branch,
                      warnings=_preview_warnings(name, basis.work_base_ref, basis.base_branch))
    return _entry(name, checked=True, unmerge_supported=judged["unmerge_supported"], warnings=judged["warnings"])


def _failed_snapshot(status: str, code: str, reason: Optional[str] = None, **extra) -> dict:
    return {"status": status, "code": code, "reason": reason, "options": [], "verdicts": {}, **extra}


def _branches_containing(root: Path, commitish: str) -> set:
    """The local branches whose tip contains ``commitish`` (``target..commitish`` is empty for exactly these). A Git
    failure is the list's own basis failing (409 ``gate_git_unavailable``), never "no branch contains it". Names are
    the full refname less its exact ``refs/heads/`` prefix, as :func:`group_work_base.local_branch_refs` lists them."""
    from modules.flow_gate.services import git_service as _gs
    proc = _gs._run_git(["for-each-ref", "--contains", commitish, "--format=%(refname)", "refs/heads"], cwd=root)
    if proc.returncode != 0:
        raise _git_unavailable("probe_failed")
    names = (line.strip() for line in (proc.stdout or "").splitlines())
    return {ref[len("refs/heads/"):] for ref in names if ref.startswith("refs/heads/") and ref != "refs/heads/"}


def _listed_verdict(basis: CheckTargetBasis, name: str, ref: tuple, slots: dict, incoming_of) -> dict:
    """One listed local branch's entry from the batch facts — the judgement's own steps: the ledger verdict of
    ``validate_target_branch`` (the listed name exists locally), a commit tip, then ``_judge_local_target``. A tip
    that is no plain commit takes the single judgement."""
    sha, kind = ref
    try:
        if name in slots:
            raise _slot_error(basis, name, slots[name])
        if not _plain_branch_name(name):
            raise _target_error(422, "merge_target_invalid", "invalid target branch", name)
        if kind != "commit":
            return _verdict(basis, name)
        judged = _judge_local_target(basis, name, sha, incoming_of, None)
    except GitServiceError as exc:
        if exc.code not in CHECK_TARGET_UNFIT:
            raise
        return _entry(name, checked=True, error=exc.code, unmerge_supported=name == basis.base_branch,
                      warnings=_preview_warnings(name, basis.work_base_ref, basis.base_branch))
    return _entry(name, checked=True, unmerge_supported=judged["unmerge_supported"], warnings=judged["warnings"])


def _spent(budget) -> bool:
    return budget.expired or time.monotonic() > budget.deadline


class _BudgetSpent(Exception):
    """The view's one CHECK_TARGET_BUDGET_SEC budget ran out after its snapshot (judging a name it did not hold)."""


def _compute_snapshot(basis: CheckTargetBasis, budget, extra: tuple = ()) -> dict:
    """Judge every local branch name, the work base and ``extra`` (the stored target) under ``budget`` — the view's
    one CHECK_TARGET_BUDGET_SEC budget, started before the basis was read and bounding every Git call of this context.
    The facts every name shares are read once (the base checkout's branch, the names and tips, the slot ledger, the
    verified scope floor and which tips contain it, the base checkout's tracked changes); each name then takes the
    judgement's own decision. Nothing partial survives: a basis failure is ``merge_target_options_unavailable`` and a
    spent budget ``merge_target_options_timeout``."""
    from modules.flow_gate.services import git_service as _gs
    from .group_work_base import local_branch_refs

    common = {"default_branch": basis.work_base_ref, "basis": basis}

    def floor_and_containing() -> tuple:
        floor = check_scope_floor(basis)          # read only; None: no verified floor (no tip stands in)
        return floor, (_branches_containing(basis.base_root, floor) if floor else None)

    try:
        if _spent(budget):
            return _failed_snapshot(CHECK_STATUS_TIMEOUT, CHECK_OPTIONS_TIMEOUT, **common)
        # The shared reads run side by side under the one budget (each thread carries it) and are all joined
        # here: nothing outlives this call.
        with ThreadPoolExecutor(max_workers=4, thread_name_prefix="check-target") as pool:
            def run(fn, *args):
                return pool.submit(contextvars.copy_context().run, fn, *args)
            checkout = run(raise_if_base_checkout_off_branch, basis.project_id, basis.base_branch)
            listed = run(local_branch_refs, basis.base_root)
            history = run(floor_and_containing)
            dirty = run(_base_dirty, basis.base_root)
            # The ledger ``branches.internal_slot_owner`` reads (registered group worktrees), read once.
            slots = {row.get("branch"): row.get("group_id")
                     for row in _gs.db_git.list_states_of_project(basis.project_id)}
            checkout.result()
            refs = listed.result()
            floor, contained = history.result()
            base_dirty = dirty.result()
        judged = replace(basis, base_checked=True, floor_sha=floor, floor_known=True, base_dirty=base_dirty)

        def incoming_of(name: str) -> tuple[Optional[int], Optional[str]]:
            # ``preview_target``'s count is ``target..floor``: 0 exactly when the tip contains the floor (the
            # list needs no more than "none" / "some": 1 stands for at least one). No verified floor: cannot tell.
            if contained is None:
                return None, None
            return (0 if name in contained else 1), None

        verdicts: dict[str, dict] = {}
        for name in sorted(refs):
            verdicts[name] = _listed_verdict(judged, name, refs[name], slots, incoming_of)
            if _spent(budget):
                return _failed_snapshot(CHECK_STATUS_TIMEOUT, CHECK_OPTIONS_TIMEOUT, **common)
        for name in (basis.work_base_ref, *extra):   # a work base / stored target that is no listed local branch
            if name and name not in verdicts:
                verdicts[name] = _verdict(judged, name)
        if _spent(budget):
            return _failed_snapshot(CHECK_STATUS_TIMEOUT, CHECK_OPTIONS_TIMEOUT, **common)
    except GitServiceError as exc:
        if _spent(budget):
            return _failed_snapshot(CHECK_STATUS_TIMEOUT, CHECK_OPTIONS_TIMEOUT, **common)
        if exc.code == "base_checkout_branch_mismatch":
            return _failed_snapshot(CHECK_STATUS_GIT_UNAVAILABLE, exc.code, **common)
        reason = (exc.details or {}).get("reason") if exc.code == CHECK_GIT_UNAVAILABLE else exc.code
        return _failed_snapshot(CHECK_STATUS_FAILED, CHECK_OPTIONS_UNAVAILABLE, reason, **common)
    except Exception:  # noqa: BLE001 — a basis read failed: no partial list
        _log.warning("check target candidates failed for %s", basis.group_id, exc_info=True)
        if _spent(budget):
            return _failed_snapshot(CHECK_STATUS_TIMEOUT, CHECK_OPTIONS_TIMEOUT, **common)
        return _failed_snapshot(CHECK_STATUS_FAILED, CHECK_OPTIONS_UNAVAILABLE, "probe_failed", **common)
    options = sorted(name for name in refs if verdicts[name]["error"] is None)
    return {"status": CHECK_STATUS_OK if options else CHECK_STATUS_EMPTY, "code": None, "reason": None,
            "options": options, "verdicts": verdicts, "default_branch": basis.work_base_ref, "basis": judged}


def _check_snapshot(project_id: Optional[str], group_id: str, refresh: bool, budget,
                    selected: Optional[str] = None) -> dict:
    """The (cached) snapshot of one group's candidates, read under the view's ``budget``: the basis, the shared
    computation and the wait for another request's computation all spend the same deadline."""
    try:
        basis = check_target_basis(project_id, group_id)
    except GitServiceError as exc:
        default = None
        try:
            from modules.flow_gate.services import git_service as _gs
            default = _gs.resolve_group_work_base_ref(project_id, group_id) if project_id else None
        except Exception:  # noqa: BLE001 — the default is display only
            default = None
        if _spent(budget):
            return _failed_snapshot(CHECK_STATUS_TIMEOUT, CHECK_OPTIONS_TIMEOUT, default_branch=default, basis=None)
        return _failed_snapshot(CHECK_STATUS_GIT_UNAVAILABLE, exc.code, (exc.details or {}).get("reason"),
                                default_branch=default, basis=None)
    key = (project_id, group_id)
    fingerprint = (basis.base_branch, basis.work_base_ref, basis.source_branch, str(basis.base_root))
    with _check_lock:
        epoch = _check_epoch
        hit = _check_cache.get(key)
        if (not refresh and hit is not None and hit["fingerprint"] == fingerprint
                and time.monotonic() - hit["at"] < CHECK_TARGET_CACHE_SEC):
            return hit
        flight_key = (key, epoch, fingerprint)
        flight = None if refresh else _check_inflight.get(flight_key)
        owner = flight is None
        if owner:
            flight = _CheckFlight()
            _check_inflight[flight_key] = flight
    if not owner:
        # Joining another request's computation spends this request's own deadline; one that does not end inside
        # it is this request's timeout (never a second computation past the budget).
        flight.done.wait(max(budget.remaining(), 0.0))
        if flight.result is not None:
            return flight.result
        return _failed_snapshot(CHECK_STATUS_TIMEOUT, CHECK_OPTIONS_TIMEOUT,
                                default_branch=basis.work_base_ref, basis=None)
    snapshot: Optional[dict] = None
    try:
        snapshot = _compute_snapshot(basis, budget, (selected,) if selected else ())
    except Exception:  # noqa: BLE001
        _log.warning("check target snapshot failed for %s", group_id, exc_info=True)
        snapshot = _failed_snapshot(CHECK_STATUS_FAILED, CHECK_OPTIONS_UNAVAILABLE, "probe_failed",
                                    default_branch=basis.work_base_ref, basis=None)
    finally:
        if snapshot is None:
            snapshot = _failed_snapshot(CHECK_STATUS_FAILED, CHECK_OPTIONS_UNAVAILABLE, "probe_failed",
                                        default_branch=basis.work_base_ref, basis=None)
        snapshot.update(fingerprint=fingerprint, at=time.monotonic())
        with _check_lock:
            if _check_inflight.get(flight_key) is flight:
                del _check_inflight[flight_key]
            if snapshot["status"] in (CHECK_STATUS_OK, CHECK_STATUS_EMPTY) and epoch == _check_epoch:
                _check_cache[key] = snapshot
        flight.result = snapshot
        flight.done.set()
    return snapshot


def _entry_of(snapshot: dict, name: Optional[str], budget) -> dict:
    """A name's entry from the snapshot. A name the snapshot did not judge (a cached snapshot read for another
    stored target) is judged on its own under what is left of the view's ``budget`` and kept; running out of it
    raises ``_BudgetSpent`` (the view then times out as a whole)."""
    if name is None:
        return _entry(None, checked=True)
    status = snapshot["status"]
    if status == CHECK_STATUS_GIT_UNAVAILABLE:
        return _entry(name, checked=True, error=snapshot["code"])
    if status not in (CHECK_STATUS_OK, CHECK_STATUS_EMPTY) or snapshot.get("basis") is None:
        return _entry(name, checked=False)
    with _check_lock:
        found = snapshot["verdicts"].get(name)
    if found is not None:
        return {**found, "warnings": list(found["warnings"])}
    if _spent(budget):
        raise _BudgetSpent()
    try:
        found = _verdict(snapshot["basis"], name)
    except Exception:  # noqa: BLE001 — could not be judged now; the list keeps its own status
        if _spent(budget):
            raise _BudgetSpent() from None
        _log.warning("check target judgement of %r failed", name, exc_info=True)
        return _entry(name, checked=False)
    if _spent(budget):
        raise _BudgetSpent()
    with _check_lock:
        snapshot["verdicts"][name] = found
    return {**found, "warnings": list(found["warnings"])}


def check_target_view(project_id: Optional[str], group_id: str, selected: Optional[str], *,
                      refresh: bool = False) -> dict:
    """The read-only merge target outputs of a check card's settings response (D0004 §3.1 / §5)::

        {"merge_target_options": [branch, ...],          # names passing judge_check_target, sorted
         "default_target_branch": work base | None,
         "merge_target_state": {
             "status": ok | empty | git_unavailable | failed | timeout,
             "code": None | gate_git_unavailable | base_checkout_branch_mismatch
                     | merge_target_options_unavailable | merge_target_options_timeout,
             "reason": None | <why>,
             "current": {branch, inherited, checked, error, unmerge_supported, warnings},
             "default": {branch, checked, error, unmerge_supported, warnings}}}

    ``selected`` is the stored ``merge_target_branch`` (None: the work base is inherited). ``refresh`` skips the
    cache. One CHECK_TARGET_BUDGET_SEC budget covers the whole view -- the basis, the candidates and the current /
    default states; when it runs out the view is ``timeout`` with no candidates and unchecked states, and that is
    never cached. Never raises for a Git / ledger read: the status says what failed. Saving, confirming and running
    never read this (they call ``judge_check_target``)."""
    from .command import git_budget

    with git_budget(CHECK_TARGET_BUDGET_SEC) as budget:
        snapshot = _check_snapshot(project_id, group_id, refresh, budget, selected)
        default_branch = snapshot.get("default_branch")
        try:
            default = _entry_of(snapshot, default_branch, budget) if default_branch else _entry(None, checked=True)
            current = dict(default) if selected is None else _entry_of(snapshot, selected, budget)
        except _BudgetSpent:
            snapshot = _failed_snapshot(CHECK_STATUS_TIMEOUT, CHECK_OPTIONS_TIMEOUT,
                                        default_branch=default_branch, basis=None)
            default = _entry(default_branch, checked=False) if default_branch else _entry(None, checked=True)
            current = dict(default) if selected is None else _entry(selected, checked=False)
    current["inherited"] = selected is None
    return {
        "merge_target_options": list(snapshot["options"]),
        "default_target_branch": default_branch,
        "merge_target_state": {"status": snapshot["status"], "code": snapshot["code"], "reason": snapshot["reason"],
                               "current": current, "default": default},
    }


# ── Attempt lifecycle ────────────────────────────────────────────────────────

def open_attempt(
    group_id: str, target: MergeTargetContext, action: str, lock_holder: str, owner: str,
    *, origin: Optional[str] = None, recovery_merge_commit: Optional[str] = None,
) -> MergeTargetContext:
    """Write the attempt record (before any git merge work) and return the pinned context.

    ``origin`` / ``recovery_merge_commit`` (0066 D#1) are written only when given."""
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
        # 0665 T0004: recorded for unattended runs that had no confirmation step.
        "unmerge_supported": target.unmerge_supported,
        "defaulted_to_work_base": target.defaulted_to_work_base,
    }
    if origin is not None:
        record["origin"] = origin
    if recovery_merge_commit is not None:
        record[RECOVERY_MERGE_COMMIT_KEY] = recovery_merge_commit
    merge_id = _gs.db_git.create_session(
        group_id, [], finalize_action=action,
        context={
            TARGET_BRANCH_KEY: target.target_branch,
            TARGET_RECORD_KEY: record,
            ATTEMPT_STATE_KEY: ATTEMPT_IN_PROGRESS,
        },
    )
    invalidate_check_targets(target.project_id)
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
    invalidate_check_targets()
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
    invalidate_check_targets()


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
        invalidate_check_targets()
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
    if target.managed_workspace:
        release_workspace(target)
    return True


def _return_merging_to_waiting(group_id: Optional[str]) -> None:
    from modules.flow_gate.services import git_service as _gs
    if not group_id:
        return   # 0630 T0005: a branch_merge attempt has no group ledger row to move
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
    here — it is already the fallback default when nothing is suggested.

    0665 T0004: this project-level memory ranks BELOW the group's own work base
    (server default and UI order: pinned target → work base → remembered → base),
    so it can no longer send a main-based group into ``v0.2``."""
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


def is_manual_record(rec: Optional[dict]) -> bool:
    return bool(rec) and rec.get("origin") == ORIGIN_MANUAL_MERGE


def is_manual_session(session: Optional[dict]) -> bool:
    return is_manual_record(target_record(session))


def finish_manual_attempt(
    ctx: MergeTargetContext, *, merge_commit: Optional[str], merge_commit_sha: Optional[str],
    pushed: bool, recovered: bool = False,
) -> None:
    """0066 D#1 §3-6(7): the end of a manual merge that really is in its target.

    The ledger goes back to ``none`` (merge id/commit cleared) FIRST, then the attempt
    row is closed as completed — it is the per-target merge history E10/E11 read — and
    only the target workspace is released. The Group slot, work branch and scope floor
    are kept. A crash between the two leaves an open attempt that recovery settles."""
    from modules.flow_gate.services import git_service as _gs
    assert ctx.merge_id is not None
    session = _gs.db_git.get_session(ctx.merge_id) or {}
    group_id = session.get("group_id")
    _gs._set_status(group_id, "none")
    result = {"merge_commit": merge_commit, "merge_commit_sha": merge_commit_sha, "pushed": pushed}
    if recovered:
        result["recovered"] = True
    close_attempt(ctx.merge_id, ATTEMPT_COMPLETED, result=result)
    release_workspace(ctx)
    _remember_default_target(ctx)
    payload = {
        "project": ctx.project_id, "group_id": group_id, "status": "merged",
        "merge_commit": merge_commit, "pushed": pushed, "target_branch": ctx.target_branch,
        "merge_id": ctx.merge_id, "manual": True, "group_status": "none",
    }
    if recovered:
        payload["recovered"] = True
    _gs._emit("git_finalize_done", ctx.project_id, group_id, payload)


def _manual_result_row(session: Optional[dict]) -> Optional[dict]:
    from modules.flow_gate.services import git_service as _gs
    if not session or _gs.db_git.session_kind(session) != _gs.db_git.SESSION_KIND_MERGE:
        return None
    rec = target_record(session)
    if not is_manual_record(rec):
        return None
    context = _gs.db_git.session_context(session)
    if context.get(ATTEMPT_STATE_KEY) != ATTEMPT_COMPLETED:
        return None
    result = context.get("attempt_result") or {}
    return {
        "merge_id": int(session["merge_id"]),
        "group_id": session.get("group_id"),
        "target_branch": rec["branch"],
        "recorded_is_project_base": bool(rec.get("is_project_base")),
        "merge_commit": result.get("merge_commit_sha") or result.get("merge_commit"),
        "pushed": bool(result.get("pushed")),
        "recovered": bool(result.get("recovered") or rec.get(RECOVERY_MERGE_COMMIT_KEY)),
        "completed_at": context.get("attempt_closed_at"),
        "started_at": rec.get("started_at"),
    }


def manual_merge_results(group_id: str) -> list[dict]:
    """0066 D#1 §3-8(0): the latest completed manual merge attempt of each target branch
    of the group, newest completion first. Read from the existing attempt rows."""
    from modules.flow_gate.services import git_service as _gs
    latest: dict[str, dict] = {}
    for session in _gs.db_git.sessions_by_group(group_id):      # newest merge_id first
        row = _manual_result_row(session)
        if row is not None and row["target_branch"] not in latest:
            latest[row["target_branch"]] = row
    return sorted(latest.values(), key=lambda r: (r["completed_at"] or "", r["merge_id"]), reverse=True)


def manual_merge_standing(group_id: str, merge_id) -> tuple[Optional[dict], Optional[dict]]:
    """``(row, latest)``: the completed manual attempt ``merge_id`` of this group (None when
    it is not one) and the latest completed manual attempt of the same target."""
    from modules.flow_gate.services import git_service as _gs
    try:
        session = _gs.db_git.get_session(int(merge_id))
    except (TypeError, ValueError):
        return None, None
    if session is None or session.get("group_id") != group_id:
        return None, None
    row = _manual_result_row(session)
    if row is None:
        return None, None
    latest = next((r for r in manual_merge_results(group_id)
                   if r["target_branch"] == row["target_branch"]), None)
    return row, latest


def recorded_manual_merge(group_id: str, branch: str, merge_commit: str) -> Optional[int]:
    """The latest completed manual attempt id of ``branch`` when any completed manual
    attempt of that target recorded ``merge_commit`` (§3-6(7-1) "already recorded")."""
    from modules.flow_gate.services import git_service as _gs
    from .refs import _full_sha_matches
    rows = [r for r in (_manual_result_row(s) for s in _gs.db_git.sessions_by_group(group_id))
            if r is not None and r["target_branch"] == branch]
    if any(_full_sha_matches(r["merge_commit"] or "", merge_commit) for r in rows):
        return rows[0]["merge_id"]
    return None


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
        _return_merging_to_waiting(session.get("group_id"))
    finally:
        if ctx.managed_workspace:
            release_workspace(ctx)


def prepare_workspace(ctx: MergeTargetContext, *, detached: bool = False) -> None:
    """Materialize the managed worktree for a non-base attempt (under the lock).

    The marker is written BEFORE the worktree so a crash never leaves an unowned
    tree; a stale leftover of a closed attempt (verified by plan_finalize_target)
    is reclaimed first."""
    from modules.flow_gate.services import git_service as _gs
    if ctx.is_project_base or ctx.target_kind == "worktree" or not ctx.managed_workspace:
        return
    assert ctx.workspace_dir is not None and ctx.root is not None
    base_root = _gs._base_root_of(ctx.project_id)
    if base_root is None:
        raise GitServiceError(409, "invalid_state", "base checkout is not available")
    if read_owner_marker(ctx.workspace_dir) is not None or ctx.root.exists():
        _remove_workspace(ctx.workspace_dir, base_root)
    _write_owner_marker(ctx)
    invalidate_check_targets(ctx.project_id)
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
    if (
        ctx.is_project_base
        or ctx.workspace_dir is None
        or ctx.target_kind == "worktree"
        or not ctx.managed_workspace
    ):
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
    removed = _remove_workspace(ctx.workspace_dir, base_root)
    invalidate_check_targets(ctx.project_id)
    return removed


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
    if ctx.target_kind == "worktree" or not ctx.managed_workspace:
        if ctx.root is None:
            return OWN_MISMATCH
        if not ctx.root.exists():
            return OWN_ABSENT
        return OWN_OWNED
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
    rec = target_record(session) or {}
    if rec.get(RECOVERY_MERGE_COMMIT_KEY):
        # 0066 D#1 §3-6(7-1): an unpushed-merge recovery row has no merge inputs; it is
        # judged by the merge commit it names.
        return _recover_recovery_attempt(session, ctx, rec, reason)
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
    _return_merging_to_waiting(session.get("group_id"))
    if ctx.managed_workspace:
        release_workspace(ctx)
    return ATTEMPT_INTERRUPTED


def _recover_recovery_attempt(
    session: dict, ctx: MergeTargetContext, rec: dict, reason: str,
) -> Optional[str]:
    """Restart table of D#1 §3-6(7-1): local target lost M -> interrupted; no push ->
    completed; push and origin has M (a crash right after the push) -> completed without
    pushing again; push and origin lacks M -> interrupted (M is never rewound); origin
    unreadable -> left open."""
    from modules.flow_gate.services import git_service as _gs
    merge_commit = rec[RECOVERY_MERGE_COMMIT_KEY]
    root = ctx.root
    if root is None or not root.exists():
        return None
    contained = _gs._is_ancestor(root, merge_commit, f"refs/heads/{ctx.target_branch}")
    if contained is None:
        return None
    if contained:
        published: Optional[bool] = False
        if rec.get("push"):
            published = _remote_contains(ctx, merge_commit)
            if published is None:
                return None
        if not rec.get("push") or published:
            finish_manual_attempt(
                ctx, merge_commit=_gs._rev_parse(root, merge_commit, short=True) or merge_commit[:7],
                merge_commit_sha=merge_commit, pushed=bool(rec.get("push")), recovered=True,
            )
            return ATTEMPT_COMPLETED
    close_attempt(ctx.merge_id, ATTEMPT_INTERRUPTED, error={"code": reason})
    _return_merging_to_waiting(session.get("group_id"))
    return ATTEMPT_INTERRUPTED


def _remote_contains(ctx: MergeTargetContext, sha: str) -> Optional[bool]:
    """Whether origin's target branch contains ``sha`` (see origin_branch_contains)."""
    return origin_branch_contains(ctx.project_id, ctx.root, ctx.target_branch, sha)


def fetch_origin_pruned(project_id: str, repo: Path) -> bool:
    """0066 D#1 §3-7/§3-8: origin synced with the config, then ``fetch --prune origin``.
    Pruning drops the tracking ref of a branch deleted on origin, so a later read of
    ``refs/remotes/origin/<b>`` never reports a deleted branch as still holding its old
    tip (``fetch.prune=false`` repositories included). False = sync or fetch failed."""
    from modules.flow_gate.services import git_service as _gs
    cfg = _gs.db_git.get_config(project_id) or {}
    try:
        _gs.ensure_origin_matches_config(repo, (cfg.get("repo_url") or "").strip())
    except GitServiceError:
        return False
    proc = _gs._run_git(
        ["fetch", "--prune", "origin"], cwd=repo, timeout=_gs.GIT_NET_TIMEOUT_SEC,
        username=cfg.get("username"), secret=_gs._load_secret_for(cfg) or "",
    )
    return proc.returncode == 0


def origin_branch_contains(project_id: str, repo: Path, branch: str, sha: str) -> Optional[bool]:
    """Whether origin's ``branch`` contains ``sha``: a pruned fetch, then ancestry against
    the refreshed tracking ref. A branch absent on origin = False. None = cannot be told."""
    from modules.flow_gate.services import git_service as _gs
    if not fetch_origin_pruned(project_id, repo):
        return None
    remote = f"refs/remotes/origin/{branch}"
    if not _gs._ref_exists(repo, remote):
        return False
    return _gs._is_ancestor(repo, sha, remote)


def _flag_manual_reconciliation(merge_id: int, reason: str, observed: Optional[str]) -> None:
    _update_context(merge_id, attempt_reconcile={
        "status": "manual_reconciliation_needed", "reason": reason,
        "observed": observed, "at": now_iso(),
    })


def _query_target_remote(ctx: MergeTargetContext) -> tuple[bool, Optional[str]]:
    """``(reachable, sha)`` of ``refs/heads/<target>`` on origin; sha None = absent.

    flowgate.default.0361 NR0003 §7/§16: this backs crash/startup recovery of an
    interrupted finalize attempt — if ``repo_url`` was repointed at a different
    remote after the merge/push that this call is trying to settle, reading a
    stale ``origin`` could close the attempt as pushed, undo a landed merge
    commit, or route it to manual reconciliation on the WRONG remote's data. Sync
    origin to the current config first; a sync failure is treated the same as an
    unreachable remote (``ls-remote`` never runs against a possibly-stale origin).
    """
    from modules.flow_gate.services import git_service as _gs
    cfg = _gs.db_git.get_config(ctx.project_id) or {}
    try:
        _gs.ensure_origin_matches_config(ctx.root, (cfg.get("repo_url") or "").strip())
    except GitServiceError:
        return False, None
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
    if _is_branch_merge_session(session):
        # 0630 T0005: no group ledger, no slot — the attempt row IS the whole record.
        from . import branch_merge
        branch_merge.finish_landed_merge(session, ctx, merge_commit, pushed=pushed)
        return
    group_id = session["group_id"]
    if is_manual_session(session):
        # 0066 D#1 §3-6(7): a recovered manual merge keeps the Group as well.
        finish_manual_attempt(
            ctx, merge_commit=merge_commit,
            merge_commit_sha=_gs._rev_parse(ctx.root, merge_commit) if ctx.root and merge_commit else None,
            pushed=pushed, recovered=True,
        )
        return
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
    if _is_branch_merge_session(session):
        from . import branch_merge
        branch_merge.finish_completed_review(session)
        return True
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

def check_unmerge_target(group_id: str, state: Optional[dict], requested_sha: str) -> Optional[dict]:
    """Read the completed session/decision evidence before unmerge can enqueue or change anything.

    Only old decisions without the new shown key may use the legacy no-record interpretation.
    """
    from modules.flow_gate.services import git_service as gs
    from flowgate.gate_decisions import repository as decisions
    session = gs.db_git.get_session(int(state["merge_id"])) if state and state.get("merge_id") is not None else None
    if session is None:
        for candidate in gs.db_git.sessions_by_group(group_id):
            context = gs.db_git.session_context(candidate)
            result = context.get("attempt_result") or {}
            sha = result.get("merge_commit_sha") or result.get("merge_commit") or ""
            if sha and (sha.startswith(requested_sha) or requested_sha.startswith(sha)):
                session = candidate
                break
    context = gs.db_git.session_context(session) if session else {}
    linked = context.get("gate_decision_id")
    decision = decisions.get(linked) if linked else None
    if not linked:
        # Without a session link, only a completed merge result identifies a decision.
        # An unfinished decision's target_sha is its acceptance HEAD, not its result.
        store = decisions.get_store()
        if store.table_exists("gate_decisions"):
            rows = store._fetch_all(
                "SELECT d.decision_id FROM gate_decisions d WHERE d.group_id = ? "
                "AND (EXISTS (SELECT 1 FROM gate_decision_effects e WHERE e.decision_id = d.decision_id "
                "AND e.phase = 'merge' AND e.status = ? AND e.sha_after LIKE ?) "
                "OR (d.status = ? AND d.completed_at IS NOT NULL AND d.target_sha LIKE ?)) "
                "ORDER BY d.accepted_at DESC, d.decision_id DESC",
                [group_id, decisions.DONE, requested_sha + "%", decisions.SUCCEEDED, requested_sha + "%"]) or []
            for row in rows:
                candidate = decisions.get(dict(row)["decision_id"])
                if (((candidate or {}).get("settings") or {}).get("effective") or {}).get("merge_mode") == "merge":
                    decision = candidate
                    break
    settings = (decision or {}).get("settings") or {}
    new = "merge_target_branch" in (settings.get("shown") or {})
    rec = target_record(session)
    if linked and decision is None and rec and "accepted_target_sha" in rec:
        raise GitServiceError(409, "unmerge_target_unknown", "the linked decision cannot be verified",
                              details={"reason": "decision_missing", "target_branch": rec["branch"]})
    raw_basis = settings.get("target_basis")
    basis = raw_basis if isinstance(raw_basis, dict) else {}
    effect = decisions.effect(decision["decision_id"], "merge") if decision else None
    requires_session = bool(((effect or {}).get("evidence") or {}).get("merge_id"))
    if new:
        valid = (basis.get("target_branch") == decision.get("target_branch") and
                 isinstance(basis.get("is_project_base"), bool) and
                 isinstance(basis.get("unmerge_supported"), bool) and
                 basis.get("unmerge_supported") == basis.get("is_project_base") and
                 bool(basis.get("project_base_branch")) and bool(basis.get("accepted_target_sha")) and
                 basis["is_project_base"] == (basis.get("target_branch") == basis["project_base_branch"]))
        if session is not None or requires_session:
            valid = valid and bool(rec) and all(rec.get(k) == basis.get(k) for k in (
                "is_project_base", "unmerge_supported", "project_base_branch", "accepted_target_sha")) and rec.get("branch") == basis.get("target_branch")
        if not valid:
            raise GitServiceError(409, "unmerge_target_unknown", "the recorded merge target cannot be verified",
                                  details={"target_branch": decision.get("target_branch"), "reason": "target_record_missing_or_invalid",
                                           "recovery_options": ["revert_merge_on_target", "timemachine_reopen", "manual_git"]})
    non_base = (new and basis.get("is_project_base") is False) or (rec is not None and rec.get("is_project_base") is False)
    if non_base:
        raise GitServiceError(409, "unmerge_unsupported_target", "unmerge is unavailable for this target",
                              details={"target_branch": (basis.get("target_branch") if new else rec["branch"]),
                                       "reason": "non_project_base_target", "unmerge_supported": False,
                                       "recovery_options": ["revert_merge_on_target", "timemachine_reopen", "manual_git"]})

    if new:
        return basis
    if rec is not None:
        return {**rec, "target_branch": rec["branch"]}
    return None


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
