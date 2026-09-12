"""Group worktree creation, synchronization, and disposal lifecycle.

Extracted from git_service.py (flowgate.default.0550 T0013, D0006 §3.2/부록 A).
"""
from __future__ import annotations

import logging
import os
import re
import shutil
import stat
import sys
import uuid
from pathlib import Path
from typing import Optional

from modules.flow_gate.db import tr_commit_ledger as db_tr_ledger
from modules.flow_gate.storage.paths import get_storage_root

from .base_slot import _record_attempt
from .command import GIT_LOCAL_TIMEOUT_SEC
from .config import base_branch_for
from .credentials import GitServiceError, _scrub
from .refs import _commits_present, _untracked_files

_log = logging.getLogger(__name__)

BRANCH_MAX_LEN = 100

GIT_WORKTREE_RM_TIMEOUT_SEC = 300


def sanitize_branch(raw: str) -> str:
    s = (raw or "").lower()
    s = re.sub(r"[^a-z0-9._-]", "-", s)
    s = re.sub(r"-{2,}", "-", s)
    s = s.strip("-.")
    s = s[:BRANCH_MAX_LEN]
    if not s or ".." in s or "@{" in s:
        raise GitServiceError(422, "invalid_branch_name", f"cannot derive a branch name from {raw!r}")
    return s


def worktree_branch_name(project_id: str, module: str, group_id: str) -> str:
    group_no = (group_id or "").rsplit(".", 1)[-1]
    return sanitize_branch(f"{project_id}_{module}_{group_no}")


def _force_rmtree(path: Path) -> bool:
    """Delete a directory tree that git could not, best-effort. True when gone.

    Used for orphan slots only (a path git refuses to own). Read-only files are a
    normal Windows leftover — clear the attribute and retry rather than aborting
    the whole sweep on one file, which is exactly how the corpse trees were born."""

    def _retry(func, target, _exc):
        try:
            os.chmod(target, stat.S_IWRITE)
            func(target)
        except Exception:
            _log.debug("rmtree could not remove %s", target, exc_info=True)

    try:
        # `onerror` is deprecated since 3.12 in favour of `onexc`; the runtime is
        # already on 3.14, so prefer the supported hook and keep the old one as a
        # fallback rather than letting a removed kwarg fail the whole teardown.
        if sys.version_info >= (3, 12):
            shutil.rmtree(path, onexc=_retry)
        else:
            shutil.rmtree(path, onerror=_retry)
    except Exception:
        _log.warning("rmtree failed for %s", path, exc_info=True)
    return not path.exists()


def ensure_worktree(
    project_id: str, module: str, group_id: str, trigger: str = "remote_access",
    start_point: Optional[str] = None,
) -> str:
    """Create/guarantee the group's branch + worktree. Idempotent; never raises.

    Returns 'skipped' | 'ok' | 'failed'. A failure only emits git_worktree_failed —
    the workflow itself proceeds on the fallback source path (P0005 §4-2).
    """
    from modules.flow_gate.services import git_service as _gs
    try:
        cfg = _gs.db_git.get_config(project_id)
        if cfg is None or not cfg.get("enabled"):
            return "skipped"  # non-integrated project: strictly no-op
        project_name = _gs._project_name(project_id)
        if not project_name:
            _record_attempt(project_id, "failed", "project_name missing", trigger, "none")
            _gs._fail_worktree(project_id, group_id, None, "project_name missing")
            return "failed"
        try:
            branch = worktree_branch_name(project_id, module or _gs._module_of(group_id), group_id)
        except GitServiceError as exc:
            _gs._fail_worktree(project_id, group_id, None, exc.code)  # E9
            return "failed"
        if not _gs.git_available():
            _record_attempt(project_id, "failed", "git_unavailable", trigger, "none")
            _gs._fail_worktree(project_id, group_id, branch, "git_unavailable")  # E1
            return "failed"

        holder = f"op:{uuid.uuid4()}"
        if not _gs._acquire_lock(project_id, holder):
            _record_attempt(project_id, "failed", "git_busy", trigger, "none")
            _gs._fail_worktree(project_id, group_id, branch, "git_busy")  # E11
            return "failed"
        try:
            return _ensure_worktree_locked(
                cfg, project_id, project_name, group_id, branch, trigger, start_point,
            )
        finally:
            _gs.db_git.release_lock(project_id, holder)
    except Exception as exc:  # noqa: BLE001 — the hook must never break its caller
        _log.warning("ensure_worktree failed for %s", group_id, exc_info=True)
        try:
            _gs._fail_worktree(project_id, group_id, None, _scrub(str(exc)))
        except Exception:
            pass
        return "failed"


def _ensure_worktree_locked(
    cfg: dict, project_id: str, project_name: str, group_id: str, branch: str,
    trigger: str = "remote_access", start_point: Optional[str] = None,
) -> str:
    from modules.flow_gate.services import git_service as _gs
    base_branch = (cfg.get("base_branch") or "main").strip() or "main"
    base_root = _gs.src_root(project_name, base_branch)
    wt_path = _gs.src_root(project_name, branch)
    username = cfg.get("username")
    secret = _gs._load_secret_for(cfg) or ""

    # Base checkout: clone (empty slot) or lossless adopt (occupied slot) —
    # 0161 replaces the old clone-only path that died with E7 base_path_occupied.
    provision = _gs._provision_base_locked(cfg, project_id, project_name, trigger)
    if provision["status"] == "failed":
        _gs._fail_worktree(project_id, group_id, branch, provision["reason"] or "git_error")
        return "failed"

    # Idempotence: ledger says the worktree exists and the directory is present.
    state = _gs.db_git.get_state(group_id)
    if (
        state is not None
        and state.get("worktree_registered")
        and state.get("branch") == branch
        and wt_path.is_dir()
        # 0287 NR0004 §5.1: `is_dir()` alone declared a half-deleted corpse "ready"
        # and returned ok — no re-provisioning, and a git_worktree_ready event for
        # a tree that no longer holds the source.
        and _gs._worktree_link_ok(wt_path)
    ):
        if start_point and not _commits_present(wt_path, [start_point]):
            _gs._fail_worktree(project_id, group_id, branch, "terminal_commit_absent")
            return "failed"
        _gs.db_git.clear_provision_failure(group_id)   # a stale marker must not linger (L §2.4)
        _gs._emit_worktree_ready(
            project_id, group_id, branch, base_branch, wt_path,
            created=False, base_root=base_root,
        )
        return "ok"

    if wt_path.exists():
        # Unregistered directory squatting on the slot (E7): never delete automatically.
        _gs._fail_worktree(project_id, group_id, branch, "worktree_path_occupied")
        return "failed"

    proc = _gs._run_git(
        ["fetch", "origin"],
        cwd=base_root, timeout=_gs.GIT_NET_TIMEOUT_SEC, username=username, secret=secret,
    )
    if proc.returncode != 0:
        _gs._fail_worktree(project_id, group_id, branch, proc.stderr.strip())
        return "failed"

    # Terminal reopen supplies C1 explicitly.  Never silently fall back to base HEAD:
    # pushed-but-unmerged content normally is not in the configured base yet.
    if start_point:
        present = _gs._run_git(["cat-file", "-e", f"{start_point}^{{commit}}"], cwd=base_root)
        if present.returncode != 0:
            _gs._fail_worktree(project_id, group_id, branch, "terminal_commit_absent")
            return "failed"
        if _gs._ref_exists(base_root, f"refs/heads/{branch}"):
            contains = _gs._run_git(["merge-base", "--is-ancestor", start_point, branch], cwd=base_root)
            if contains.returncode != 0:
                _gs._fail_worktree(project_id, group_id, branch, "terminal_branch_mismatch")
                return "failed"
            proc = _gs._run_git(["worktree", "add", str(wt_path), branch], cwd=base_root)
        elif _gs._ref_exists(base_root, f"refs/remotes/origin/{branch}"):
            contains = _gs._run_git(
                ["merge-base", "--is-ancestor", start_point, f"origin/{branch}"], cwd=base_root,
            )
            if contains.returncode != 0:
                _gs._fail_worktree(project_id, group_id, branch, "terminal_branch_mismatch")
                return "failed"
            proc = _gs._run_git(
                ["worktree", "add", "--track", "-b", branch, str(wt_path), f"origin/{branch}"],
                cwd=base_root,
            )
        else:
            # T0007 §4 condition 1 — re-provisioning from bare C1 would silently
            # drop any base commit made after C1 was merged (base B1 = B0+C1 may
            # already have moved on to B2). C1 is an ancestor of the current base
            # tip whenever the merge that terminalized it actually landed, so fork
            # from that tip instead — C1's content stays in history either way.
            # If the tip does NOT contain C1, this base/history relationship
            # cannot be trusted; fail closed rather than guess (T0007 §11).
            base_tip = _worktree_start_point(base_root, base_branch)
            contains_c1 = _gs._run_git(
                ["merge-base", "--is-ancestor", start_point, base_tip], cwd=base_root,
            )
            if contains_c1.returncode != 0:
                _gs._fail_worktree(project_id, group_id, branch, "terminal_base_diverged")
                return "failed"
            proc = _gs._run_git(["worktree", "add", "-b", branch, str(wt_path), base_tip], cwd=base_root)
    elif _gs._ref_exists(base_root, f"refs/heads/{branch}"):
        proc = _gs._run_git(["worktree", "add", str(wt_path), branch], cwd=base_root)
    elif _gs._ref_exists(base_root, f"refs/remotes/origin/{branch}"):
        # Reconnect to the group's existing remote branch (restart survival).
        proc = _gs._run_git(
            ["worktree", "add", "--track", "-b", branch, str(wt_path), f"origin/{branch}"],
            cwd=base_root,
        )
    else:
        proc = _gs._run_git(
            ["worktree", "add", "-b", branch, str(wt_path),
             _worktree_start_point(base_root, base_branch)],
            cwd=base_root,
        )
    if proc.returncode != 0:
        _gs._fail_worktree(project_id, group_id, branch, proc.stderr.strip())
        return "failed"

    _gs.db_git.register_worktree(group_id, project_id, branch)
    _gs.db_git.clear_provision_failure(group_id)   # success clears the failure marker (L §2.4)
    _gs._emit_worktree_ready(
        project_id, group_id, branch, base_branch, wt_path,
        created=True, base_root=base_root,
    )
    return "ok"


def _worktree_start_point(base_root: Path, base_branch: str) -> str:
    """Where a brand-new group branch forks from.

    Historically always `origin/<base>` when that ref existed, so a group always
    started from the newest published state. But `base_commit` deliberately does
    NOT push (its commit rides along on the next finalize's base push), so a
    locally committed file stayed absent from every worktree created afterwards —
    i.e. "commit it and the agent can see it" was still false even *after* 0296
    T0004 gave the operator a way to commit untracked files. The whole fix would
    have stopped one step short.

    So: prefer the LOCAL base branch whenever it already contains everything
    origin has (fast-forward-ahead or equal) — it is then strictly the newer of
    the two and loses nothing. Only when origin is ahead or the two have
    diverged does `origin/<base>` win, preserving the original intent; a genuine
    divergence is the E4 `base_diverged` condition and stays finalize's problem,
    not this function's.
    """
    from modules.flow_gate.services import git_service as _gs
    remote = f"origin/{base_branch}"
    if not _gs._ref_exists(base_root, f"refs/remotes/{remote}"):
        return base_branch
    if not _gs._ref_exists(base_root, f"refs/heads/{base_branch}"):
        return remote
    contains = _gs._run_git(["merge-base", "--is-ancestor", remote, base_branch], cwd=base_root)
    return base_branch if contains.returncode == 0 else remote


def _emit_worktree_ready(
    project_id: str, group_id: str, branch: str, base_branch: str, wt_path: Path, *,
    created: bool, base_root: Optional[Path] = None,
) -> None:
    from modules.flow_gate.services import git_service as _gs
    try:
        rel = wt_path.relative_to(get_storage_root()).as_posix()
    except Exception:
        rel = str(wt_path)
    payload = {
        "project": project_id,
        "group_id": group_id,
        "branch": branch,
        "base_branch": base_branch,
        "worktree_path": rel,
        "created": created,
    }
    # 0296 T0004 (NR0003 R3): `worktree add` checks out a COMMIT, so whatever is
    # sitting uncommitted in the base checkout does not exist in the tree the
    # workers read (NR §C1). That isolation is correct and stays — but the
    # operator learns about it, today, only by watching an agent claim a file is
    # missing. Ship the count at the moment the worktree appears so the UI can
    # warn up front. Advisory only: never let it fail the provisioning.
    try:
        if base_root is not None:
            untracked = _untracked_files(base_root)
            if untracked:
                payload["base_untracked_count"] = len(untracked)
                payload["base_untracked"] = untracked[:20]
    except Exception:
        _log.warning("worktree-ready untracked probe failed for %s", group_id, exc_info=True)
    _gs._emit("git_worktree_ready", project_id, group_id, payload)


def _emit_worktree_failed(
    project_id: str, group_id: str, branch: Optional[str], error: str
) -> None:
    from modules.flow_gate.services import git_service as _gs
    _gs._emit("git_worktree_failed", project_id, group_id, {
        "project": project_id,
        "group_id": group_id,
        "branch": branch,
        "error": error,
    })


def _fail_worktree(
    project_id: str, group_id: str, branch: Optional[str], error: str
) -> None:
    """Persist the provisioning failure (0205 L §2.4) then emit the live SSE.

    The persistent record lets the status query resurface the failure long after
    the one-shot SSE is gone (P scenario 4) — a worker without a slot no longer
    fails silently. Persistence is best-effort so a bookkeeping error never
    swallows the operator-facing SSE."""
    from modules.flow_gate.services import git_service as _gs
    try:
        _gs.db_git.upsert_provision_failure(group_id, project_id, branch or "", error)
    except Exception:
        _log.warning("record provision failure failed for %s", group_id, exc_info=True)
    _emit_worktree_failed(project_id, group_id, branch, error)


def ensure_worktree_async(project_id: str, module: str, group_id: str) -> None:
    """H1 wrapper: run provisioning off the request thread (clone can be slow).

    The decide response must not wait on network git; H2 (worker-token grant
    creation) re-guarantees the worktree before any source access anyway.
    """
    from modules.flow_gate.services import git_service as _gs
    import threading

    threading.Thread(
        target=_gs.ensure_worktree,
        args=(project_id, module, group_id, "workflow_decide"),
        daemon=True,
    ).start()


def _has_legacy_source_history(group_id: str) -> bool:
    """Trustworthy evidence this group already did real source work before this
    marker existed (T0004 SS16-19/SS24-25: a false-positive SKIP is preferable to
    destroying prior work). A tr_commit_ledger row -- live OR canceled -- proves a
    TR actually committed source under this group at some point; canceled still
    counts because the commit genuinely happened (FlowGate never erases
    history -- a cancel only records a revert on top of it, D0005).

    A lookup failure is itself ambiguous evidence and is read the same way
    (T0004 SS19: safety first, never destructively reset on an unclear signal).
    """
    try:
        return bool(db_tr_ledger.commit_rows_by_group(group_id))
    except Exception:
        _log.warning("legacy source history probe failed for %s", group_id, exc_info=True)
        return True


def ensure_initial_group_source_sync(project_id: str, module: str, group_id: str) -> dict:
    """One-time, forced reset --hard + clean -fd of the group worktree to
    the current configured base-branch HEAD (flowgate.default.0511 T0004).

    module is accepted only for call-site symmetry with ensure_worktree()
    -- the branch this function acts on always comes from db_git.get_state().

    Never raises (same contract as ensure_worktree): every failure mode
    comes back as performed=False with a reason, and the caller decides
    which reasons are a safe no-op (git disabled, already synced, legacy
    history) versus which must block the run (T0004 SS26-28/SS34 -- a verify
    failure or a marker-write failure must never let the worker launch against
    an unconfirmed tree).

    Returns {"performed": bool, "reason": str, "sha": Optional[str]}.
    """
    from modules.flow_gate.services import git_service as _gs
    # A config lookup failure must not block a run (same contract as
    # _require_group_worktree's own get_config try/except above): an unreadable
    # config reads as "not integrated", never as license to hold the AI run hostage.
    try:
        cfg = _gs.db_git.get_config(project_id)
    except Exception:
        _log.warning("initial source sync: config lookup failed for %s", group_id, exc_info=True)
        return {"performed": False, "reason": "config_lookup_failed", "sha": None}
    if cfg is None or not cfg.get("enabled"):
        return {"performed": False, "reason": "git_disabled", "sha": None}

    try:
        # Precheck OUTSIDE the lock (T0004 SS23/SS25.12): the common case -- a group
        # long past its first sync -- never waits on the mutex at all.
        state = _gs.db_git.get_state(group_id)
        if state is not None and state.get("initial_source_sync_at"):
            return {"performed": False, "reason": "already_synced", "sha": None}

        project_name = _gs._project_name(project_id)
        if not project_name:
            return {"performed": False, "reason": "project_name_missing", "sha": None}

        holder = f"initial_sync:{uuid.uuid4()}"
        if not _gs._acquire_lock(project_id, holder):
            return {"performed": False, "reason": "git_busy", "sha": None}
        try:
            # Marker recheck INSIDE the lock -- the second half of the race guard a
            # concurrent first-read pair needs to land exactly one destructive sync.
            state = _gs.db_git.get_state(group_id)
            if state is not None and state.get("initial_source_sync_at"):
                return {"performed": False, "reason": "already_synced", "sha": None}
            if state is None or not state.get("worktree_registered"):
                return {"performed": False, "reason": "worktree_missing", "sha": None}
            branch = (state.get("branch") or "").strip()
            if not branch:
                return {"performed": False, "reason": "worktree_missing", "sha": None}
            wt_path = _gs.src_root(project_name, branch)
            if not wt_path.is_dir():
                return {"performed": False, "reason": "worktree_missing", "sha": None}

            if _has_legacy_source_history(group_id):
                # T0004 SS18: safe backfill, never a destructive reset -- the group
                # already has real source work; this only stops future invocations
                # from re-running this same legacy check.
                head_proc = _gs._run_git(["rev-parse", "HEAD"], cwd=wt_path)
                legacy_sha = head_proc.stdout.strip() if head_proc.returncode == 0 else None
                try:
                    _gs.db_git.set_initial_source_sync(group_id, legacy_sha)
                except Exception:
                    _log.warning(
                        "legacy source sync backfill failed for %s", group_id, exc_info=True,
                    )
                    return {"performed": False, "reason": "marker_persist_failed", "sha": None}
                return {"performed": False, "reason": "legacy_source_history", "sha": legacy_sha}

            base_branch = base_branch_for(project_id) or "main"
            base_root = _gs.src_root(project_name, base_branch)
            head_proc = _gs._run_git(["rev-parse", "HEAD"], cwd=base_root)
            if head_proc.returncode != 0:
                return {"performed": False, "reason": "reset_failed", "sha": None}
            base_sha = head_proc.stdout.strip()

            reset_proc = _gs._run_git(
                ["reset", "--hard", base_sha], cwd=wt_path, timeout=GIT_LOCAL_TIMEOUT_SEC,
            )
            if reset_proc.returncode != 0:
                return {"performed": False, "reason": "reset_failed", "sha": None}
            # T0004 SS25: -fd only, never -x -- ignored files are not this
            # feature's business, the same restraint the existing worktree-clean
            # paths use.
            clean_proc = _gs._run_git(
                ["clean", "-fd"], cwd=wt_path, timeout=GIT_LOCAL_TIMEOUT_SEC,
            )
            if clean_proc.returncode != 0:
                return {"performed": False, "reason": "reset_failed", "sha": None}

            verify_proc = _gs._run_git(["rev-parse", "HEAD"], cwd=wt_path)
            if verify_proc.returncode != 0 or verify_proc.stdout.strip() != base_sha:
                return {"performed": False, "reason": "head_mismatch", "sha": None}

            try:
                _gs.db_git.set_initial_source_sync(group_id, base_sha)
            except Exception:
                _log.warning(
                    "initial source sync marker persist failed for %s", group_id, exc_info=True,
                )
                return {"performed": False, "reason": "marker_persist_failed", "sha": None}

            _gs._emit("git_initial_source_sync", project_id, group_id, {
                "project": project_id, "group_id": group_id, "branch": branch, "sha": base_sha,
            })
            return {"performed": True, "reason": "ok", "sha": base_sha}
        finally:
            _gs.db_git.release_lock(project_id, holder)
    except Exception:
        _log.warning("ensure_initial_group_source_sync failed for %s", group_id, exc_info=True)
        return {"performed": False, "reason": "error", "sha": None}


def _is_group_disposed(group_id: str) -> bool:
    """Whether the group has been disposed (terminal DC discard). Lazy import to
    avoid a process_service ↔ git_service import cycle; fail-closed on error so a
    lookup failure never force-deletes a live group's branch."""
    try:
        from modules.flow_gate import process_service
        return bool(process_service.is_group_disposed(group_id))
    except Exception:
        return False


def _abort_disposed_merge_session(project_id: str, group_id: str, base_root: Path) -> None:
    """Abort an in-progress merge for a disposed group and release its merge lock.

    A group discarded mid-conflict still owns an open git_merge_session and holds
    the project lock as ``merge:{merge_id}``. Abort the merge (clears the base
    checkout's MERGE_HEAD/index), close the session, and release that lock so slot
    teardown can proceed. Best-effort; idempotent (no open session → no-op)."""
    from modules.flow_gate.services import git_service as _gs
    try:
        session = _gs.db_git.get_open_session_by_group(group_id)
        if session is None:
            return
        if (base_root / ".git" / "MERGE_HEAD").exists():
            _gs._run_git(["merge", "--abort"], cwd=base_root)
        merge_id = session.get("merge_id")
        if merge_id is not None:
            _gs.db_git.close_session(int(merge_id), "aborted")
            _gs.db_git.release_lock(project_id, f"merge:{merge_id}")
    except Exception:
        _log.warning("disposed merge-session abort failed for %s", group_id, exc_info=True)


def _cleanup_group_slot(
    project_id: str, group_id: str, *, force_discard: bool = False
) -> bool:
    """Best-effort removal of one terminal slot's leftovers. Never raises.

    Removes, in order: the worktree directory (`git worktree remove --force` —
    merged/pushed content already lives in base/origin, and stray build
    artifacts must not park the leftovers forever), the local work branch, a
    pre-0172 leftover origin work branch (merged groups only — a PUSHED branch
    on origin is the user's chosen outcome and is never touched), and finally
    the ledger registration (status/merge_commit stay as history).

    Scope guard, consistent with E7: only a ledger-registered slot that is in a
    terminal status (merged/pushed), belongs to a disposed group, OR is being
    force-discarded (0199 B0001: a no-work group, branch at base) is touched — an
    unregistered directory is never deleted. A disposed or force-discarded work
    branch is force-deleted; for a disposed group its unmerged content is
    intentionally thrown away, and for a no-work group the branch holds no unique
    commit so nothing is lost, and origin was never pushed. The caller must hold
    the project git lock. Returns True when the slot ended up unregistered.

    0287 NR0004: the worktree step is three-way, not two-way. A slot whose
    directory git no longer owns (registration pruned, or the `.git` link
    destroyed by a delete that was interrupted mid-run) is an ORPHAN: `worktree
    remove` rejects it on every attempt, so it is pruned + deleted directly and
    the teardown continues to the branch and the ledger. That does not widen the
    E7 scope — we are past the gates above, so the ledger itself says this path is
    THIS group's slot and the group is terminal/disposed/no-work. An undeterminable
    registration (git unavailable/timed out) still defers rather than deleting.
    """
    from modules.flow_gate.services import git_service as _gs
    try:
        cfg = _gs.db_git.get_config(project_id)
        project_name = _gs._project_name(project_id)
        if cfg is None or not cfg.get("enabled") or not project_name:
            return False
        state = _gs.db_git.get_state(group_id)
        if state is None or not state.get("worktree_registered"):
            return False
        status = (state.get("status") or "none")
        # 0192 T0005 §3: a DISPOSED group's slot is a cleanup target regardless of
        # status. dispose_group never touched git, and the ledger gate below was
        # merged/pushed-only, so a discarded group's worktree dir + local work
        # branch + ledger row survived forever (and the stale row kept polluting
        # the §2 dropdown). Its work branch is UNMERGED, so it is force-deleted
        # (-D) — the discarded work is intentionally lost, matching the meaning of
        # disposal.
        disposed = _gs._is_group_disposed(group_id)
        # 0199 B0001: a force-discarded no-work slot is cleaned up exactly like a
        # disposed one — worktree torn down and the (base-tip, no-unique-commit)
        # local work branch force-deleted, with NO merge and NO push.
        if status not in _gs.CLEANUP_STATUSES and not disposed and not force_discard:
            return False
        branch = (state.get("branch") or "").strip()
        if not branch:
            return False
        base_branch = (cfg.get("base_branch") or "main").strip() or "main"
        base_root = _gs.src_root(project_name, base_branch)
        if not (base_root / ".git").exists() or not _gs.git_available():
            return False
        # A disposed group may still hold an in-progress merge session (conflict/
        # merging): abort it so base checkout's MERGE_HEAD/index are clean before
        # the worktree teardown, and close the ledger session. Idempotent — a no-op
        # once the session is already closed (e.g. cleanup_disposed_group aborted it
        # before taking the lock).
        if disposed and status in ("conflict", "merging"):
            _abort_disposed_merge_session(project_id, group_id, base_root)
        wt_path = _gs.src_root(project_name, branch)

        if wt_path.is_dir():
            # 0287 NR0004 §4: this used to be a two-state branch — directory present
            # meant "healthy worktree, call remove". The third state (directory
            # present, git registration missing or destroyed) fell into the remove
            # path, where git rejects it every single time ("is not a working tree"
            # / "validation failed … '.git' does not exist"), and the bare
            # `return False` below then skipped the branch delete AND the ledger
            # unregister — so the slot could never leave this state. Classify first.
            kind = _gs._classify_worktree_dir(base_root, wt_path)
            if kind == "live":
                proc = _gs._run_git(
                    ["worktree", "remove", "--force", str(wt_path)],
                    cwd=base_root, timeout=GIT_WORKTREE_RM_TIMEOUT_SEC,
                )
                if proc.returncode != 0 or wt_path.exists():
                    # A remove that fails HALFWAY leaves an orphan behind (that is
                    # how B/C above are created), so re-classify instead of giving
                    # up: if git no longer owns the path, finish the job ourselves.
                    kind = _gs._classify_worktree_dir(base_root, wt_path)
                    if kind == "live":
                        # Still a genuine registered worktree — something outside
                        # our control blocked it (file lock, permissions). Preserve
                        # the ledger row so a later sweep retries, as before.
                        _log.warning(
                            "worktree remove failed for %s (rc=%s, still registered): %s",
                            group_id, proc.returncode, _gs._last_line(proc.stderr),
                        )
                        return False
                    _log.warning(
                        "worktree remove for %s left an orphan directory (rc=%s: %s) — "
                        "reclaiming it directly",
                        group_id, proc.returncode, _gs._last_line(proc.stderr),
                    )
            if kind == "unknown":
                # git could not tell us whether the path is registered. Deleting a
                # possibly-live worktree is the one irreversible mistake here, so
                # stay conservative and let the next sweep retry.
                _log.warning(
                    "worktree registration for %s is undeterminable — cleanup deferred",
                    group_id,
                )
                return False
            if kind == "orphan" and wt_path.exists():
                # Orphan: git refuses to own this path, so `worktree remove` can
                # never clear it. Drop the stale bookkeeping, then delete the
                # directory ourselves and CONTINUE to the branch/ledger teardown.
                _gs._run_git(["worktree", "prune"], cwd=base_root)
                if not _force_rmtree(wt_path):
                    _log.warning(
                        "orphan worktree directory for %s could not be removed: %s",
                        group_id, wt_path,
                    )
                    return False
                _log.info("orphan worktree directory reclaimed for %s: %s", group_id, wt_path)
        else:
            # Directory already gone (manual removal) — just drop the stale
            # worktree bookkeeping so the branch delete below can proceed.
            _gs._run_git(["worktree", "prune"], cwd=base_root)

        if _gs._ref_exists(base_root, f"refs/heads/{branch}"):
            if disposed or force_discard:
                # disposed: unmerged work intentionally thrown away.
                # force_discard (0199): branch sits at base tip with no unique
                # commit, so -D loses nothing; origin was never pushed, so no
                # origin ref to retro-delete below. Force-delete (`-d` refuses).
                proc = _gs._run_git(["branch", "-D", branch], cwd=base_root)
            elif status == "merged":
                proc = _gs._run_git(["branch", "-d", branch], cwd=base_root)
            elif _gs._ref_exists(base_root, f"refs/remotes/origin/{branch}"):
                # pushed: origin retains the content, the local ref is disposable.
                proc = _gs._run_git(["branch", "-D", branch], cwd=base_root)
            else:
                proc = None  # pushed but no origin ref visible — keep the local ref
            if proc is not None and proc.returncode != 0:
                _log.warning(
                    "branch delete failed for %s: %s", group_id, _gs._last_line(proc.stderr)
                )

        if status == "merged" and _gs._ref_exists(base_root, f"refs/remotes/origin/{branch}"):
            # Work branches pushed before the 0172 fix were never meant to be
            # published; retro-delete best-effort (failure is not a cleanup failure).
            _gs._run_git(
                ["push", "origin", "--delete", branch],
                cwd=base_root, timeout=_gs.GIT_NET_TIMEOUT_SEC,
                username=cfg.get("username"), secret=_gs._load_secret_for(cfg) or "",
            )

        _gs.db_git.unregister_worktree(group_id)
        return True
    except Exception:
        _log.warning("slot cleanup failed for %s", group_id, exc_info=True)
        return False
