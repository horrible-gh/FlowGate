"""Stale-session cleanup and startup recovery.

Extracted from git_service.py (flowgate.default.0550 T0013, D0006 §3.2/부록 A).
"""
from __future__ import annotations

import logging
import time
import uuid
from pathlib import Path
from typing import Optional

from modules.flow_gate.db import terminal_cleanup_snapshots as db_terminal_cleanup

from . import merge_target
from .credentials import GitServiceError
from .worktree import _abort_disposed_merge_session

_log = logging.getLogger(__name__)

MERGE_SESSION_TTL_HOURS = 24   # L0004 §1 — quiet-for-this-long conflict → auto-abort

SWEEP_INTERVAL_MIN = 30        # L0004 §1 — auto-recovery sweep period


def cleanup_disposed_group(project_id: str, group_id: str) -> dict:
    """Tear down a DISPOSED group's git leftovers (worktree dir + local work branch
    + ledger registration). Called right after dispose_group succeeds.

    dispose_group itself never touches git, so without this the discarded group's
    entire source-tree worktree copy, its unmerged local branch, and its ledger row
    all survived — the ledger row also kept the group in the §2 status dropdown as
    an unselectable ghost. Disposal has ALREADY succeeded when we run, so a git
    failure must never surface as an error: everything here is best-effort and
    swallowed. No-op when git integration is off or the group holds no slot."""
    from modules.flow_gate.services import git_service as _gs
    try:
        cfg = _gs.db_git.get_config(project_id)
        if cfg is None or not cfg.get("enabled"):
            return {"ok": True, "cleaned": False, "reason": "git_disabled"}
        state = _gs.db_git.get_state(group_id)
        if state is None or not state.get("worktree_registered"):
            return {"ok": True, "cleaned": False, "reason": "no_slot"}
        if not _gs.git_available():
            return {"ok": True, "cleaned": False, "reason": "git_unavailable"}
        # A conflict/merging slot holds the project lock as merge:{id}; abort +
        # release it BEFORE acquiring our own lock (else _acquire_lock times out).
        project_name = _gs._project_name(project_id)
        if project_name and (state.get("status") or "none") in ("conflict", "merging"):
            base_branch = (cfg.get("base_branch") or "main").strip() or "main"
            _abort_disposed_merge_session(project_id, group_id, _gs.src_root(project_name, base_branch))
        holder = f"dispose:{uuid.uuid4()}"
        if not _gs._acquire_lock(project_id, holder):
            return {"ok": False, "cleaned": False, "reason": "git_busy"}
        try:
            cleaned = _gs._cleanup_group_slot(project_id, group_id)
        finally:
            _gs.db_git.release_lock(project_id, holder)
        # The slot just left the ledger; nudge clients to re-fetch the group
        # dropdown (the explorer subscribes to git_pending_changed → reload slots).
        if cleaned:
            _gs._emit_pending_changed(project_id, group_id, "none")
        return {"ok": True, "cleaned": cleaned}
    except Exception:
        _log.warning("disposed group cleanup failed for %s", group_id, exc_info=True)
        return {"ok": False, "cleaned": False, "reason": "error"}


def cleanup_terminal_slots(project_id: str) -> dict:
    """POST …/projects/{id}/git/cleanup — backlog sweep of every registered
    slot already finalized (merged/pushed) OR belonging to a disposed group.
    Covers groups finalized/discarded before the per-finalize / per-dispose
    cleanup existed, and any slot whose immediate cleanup failed. (0192 T0005 §3
    adds the disposed backlog: one sweep clears every ghost slot left by a group
    that was discarded before dispose learned to touch git.)"""
    from modules.flow_gate.services import git_service as _gs
    _gs._require_enabled_config(project_id)
    if not _gs.git_available():
        raise GitServiceError(
            500, "git_unavailable",
            "git binary not found on server (install git in the runtime image)",
        )
    holder = f"op:{uuid.uuid4()}"
    if not _gs._acquire_lock(project_id, holder):
        raise GitServiceError(
            409, "git_busy",
            f"Another git operation is in progress for project '{project_id}' (try again shortly)",
        )
    cleaned: list[str] = []
    failed: list[str] = []
    pending: list[dict] = []
    try:
        for row in _gs.db_git.list_states_of_project(project_id):
            gid = row["group_id"]
            terminal = (row.get("status") or "none") in _gs.CLEANUP_STATUSES
            if not terminal and not _gs._is_group_disposed(gid):
                continue
            # An open TR revert/reapply conflict owns files in this worktree. Cleanup
            # must not destroy the session; it remains a separately actionable row.
            if _gs.tr_conflict_session(gid) is not None:
                pending.append({"group_id": gid, "reason": "revert_conflict"})
                continue
            if _gs._cleanup_group_slot(project_id, gid):
                cleaned.append(gid)
            else:
                failed.append(gid)
                pending.append({"group_id": gid, "reason": "teardown_failed"})
    finally:
        _gs.db_git.release_lock(project_id, holder)
    status = "ok" if not failed else ("partial" if cleaned else "failed")
    snapshot = db_terminal_cleanup.put(project_id, status, len(cleaned), pending)
    return {"ok": True, "result": {"cleaned": cleaned, "failed": failed},
            "terminal_cleanup": snapshot}


def _ttl_expired(last: Optional[str]) -> bool:
    """Whether an activity timestamp is older than MERGE_SESSION_TTL_HOURS."""
    if not last:
        return False   # unknown activity → never auto-abort on this basis
    try:
        from datetime import datetime, timedelta, timezone

        dt = datetime.fromisoformat(last)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return datetime.now(timezone.utc) - dt >= timedelta(hours=MERGE_SESSION_TTL_HOURS)
    except Exception:
        return False


def _emit_auto_aborted(project_id: str, group_id: str, merge_id: int, reason: str) -> None:
    from modules.flow_gate.services import git_service as _gs
    state = _gs.db_git.get_state(group_id) or {}
    _gs._emit("git_merge_auto_aborted", project_id, group_id, {
        "project": project_id, "group_id": group_id, "merge_id": merge_id,
        "reason": reason, "branch": state.get("branch"), "branch_preserved": True,
    })


def _close_orphan(session: dict, project_id: str) -> None:
    """A session whose base checkout has no MERGE_HEAD — the merge is gone from
    disk (manual cleanup / crash). Close it and return the group to 'waiting'
    (0205 L §2.5). branch_preserved: the work branch is untouched."""
    from modules.flow_gate.services import git_service as _gs
    merge_id = int(session["merge_id"])
    group_id = session["group_id"]
    # 0594 T0012: an attempt records the outcome and releases only its own
    # workspace (owner match, §9.3); a legacy row is closed exactly as before.
    # A workspace owned by someone else leaves the row open as well (§9.1).
    if not merge_target.close_session_attempt(
        session, merge_target.ATTEMPT_INTERRUPTED, error={"code": "orphan_recovered"},
    ):
        return
    _gs._set_status(group_id, "waiting")
    _gs.db_git.release_lock(project_id, f"merge:{merge_id}")   # legacy leftover, best-effort
    _emit_auto_aborted(project_id, group_id, merge_id, "orphan_recovered")


def _auto_abort_session(
    session: dict, project_id: str, base_root: Path, reason: str
) -> None:
    """Reclaim an abandoned conflict session: git merge --abort (work branch
    preserved), close it, return the group to 'waiting' (0205 L §2.5).

    Takes a short sweep lock; if the project is busy it simply retries next cycle.
    If merge --abort fails (e.g. it collides with unrelated local base changes)
    the session is LEFT intact — a forced reset is never issued, protecting a base
    checkout that has other groups' work mixed in (the exact 0203 accident)."""
    from modules.flow_gate.services import git_service as _gs
    merge_id = int(session["merge_id"])
    group_id = session["group_id"]
    holder = f"sweep:{uuid.uuid4()}"
    if not _gs._acquire_lock(project_id, holder):
        return   # another git op in progress — try again next cycle
    try:
        # 0594 T0012: base_root is the attempt's pinned target root (the managed
        # workspace for a non-base target) — see merge_session_sweep. Ownership is
        # re-read under the sweep lock BEFORE merge --abort (§9.1/§9.3).
        if merge_target.session_workspace_ownership(session) != merge_target.OWN_OWNED:
            _log.warning(
                "sweep: merge %s target workspace is not its own — left intact", merge_id,
            )
            return
        if _gs._merge_in_progress(base_root):
            proc = _gs._run_git(["merge", "--abort"], cwd=base_root)
            if proc.returncode != 0:
                _log.warning(
                    "sweep merge --abort failed for %s (%s) — left intact",
                    group_id, _gs._last_line(proc.stderr),
                )
                return   # never force-reset (L §5)
        # owner-matched workspace release for an attempt; legacy close unchanged
        merge_target.close_session_attempt(
            session, merge_target.ATTEMPT_ABORTED, error={"code": reason},
        )
        _gs._set_status(group_id, "waiting")
        _gs.db_git.release_lock(project_id, f"merge:{merge_id}")   # legacy leftover, best-effort
        _emit_auto_aborted(project_id, group_id, merge_id, reason)
    finally:
        _gs.db_git.release_lock(project_id, holder)


def _sweep_tr_session(session: dict, project_id: str) -> None:
    """The sweep's TR branch (088) — the same two outcomes, read off the group worktree.

    Orphan: the revert is no longer in flight, so somebody finished or unwound it outside
    FlowGate; close the row and put the group status back where the session found it, or
    the group would claim a conflict forever. TTL: hand it to the ordinary abort, which is
    the same restore a person's [give up] press performs.
    """
    from modules.flow_gate.services import git_service as _gs
    merge_id = int(session["merge_id"])
    group_id = session["group_id"]
    context = _gs.db_git.session_context(session)
    state = _gs.db_git.get_state(group_id) or {}
    project_name = _gs._project_name(project_id)
    branch = state.get("branch") or context.get("branch")
    wt_path = _gs.src_root(project_name, branch) if (project_name and branch) else None
    if wt_path is None or not wt_path.is_dir():
        return   # slot gone — never guess, same rule as the base-checkout branch
    if not _gs._revert_in_flight(wt_path):
        _gs.db_git.close_session(merge_id, "aborted")
        _gs._set_status(group_id, context.get("prev_status") or "waiting")
        _emit_auto_aborted(project_id, group_id, merge_id, "orphan_recovered")
        return
    if not _ttl_expired(session.get("touched_at") or session.get("created_at")):
        return
    try:
        _gs.abort_tr_conflict(group_id, merge_id)
    except Exception:
        _log.warning(
            "tr conflict session auto-abort failed for merge %s", merge_id, exc_info=True
        )
        return
    _emit_auto_aborted(project_id, group_id, merge_id, "ttl_expired")


def _sweep_group_update_session(session: dict, project_id: str) -> None:
    """Recover a group update against the group worktree without changing its state."""
    from modules.flow_gate.services import git_service as _gs
    merge_id = int(session["merge_id"])
    group_id = session["group_id"]
    context = _gs.db_git.session_context(session)
    state = _gs.db_git.get_state(group_id) or {}
    project_name = _gs._project_name(project_id)
    branch = state.get("branch") or context.get("branch")
    root = _gs.src_root(project_name, branch) if (project_name and branch) else None
    if root is None or not root.is_dir():
        return
    merge_head = _gs._run_git(["rev-parse", "--verify", "MERGE_HEAD"], cwd=root)
    if merge_head.returncode != 0:
        _gs.db_git.close_session(merge_id, "aborted")
        return
    if not _ttl_expired(session.get("touched_at") or session.get("created_at")):
        return
    proc = _gs._run_git(["merge", "--abort"], cwd=root)
    if proc.returncode == 0:
        _gs.db_git.close_session(merge_id, "aborted")
        _emit_auto_aborted(project_id, group_id, merge_id, "ttl_expired")


def merge_session_sweep(sessions: Optional[list[dict]] = None) -> None:
    """Auto-recover abandoned / orphaned conflict sessions (0205 L §2.5).

    A caller may pass an already-read open-session list; without one this reads
    its own list (the periodic sweep-daemon path). For each open session: skip
    if the base checkout is gone (never guess); close it as an orphan if the
    merge left no MERGE_HEAD on disk; auto-abort it if it has been quiet past
    the TTL; otherwise leave it. Best-effort and fully isolated per session so
    one bad row cannot sink the pass.
    """
    from modules.flow_gate.services import git_service as _gs
    if sessions is None:
        try:
            sessions = _gs.db_git.list_open_sessions()
        except Exception:
            _log.info("merge session sweep skipped (session table unavailable)", exc_info=True)
            return
    for session in sessions:
        try:
            group_id = session["group_id"]
            project_id = _gs._project_of_group(group_id)
            kind = _gs.db_git.session_kind(session)
            if kind == _gs.db_git.SESSION_KIND_GROUP_UPDATE:
                _gs._sweep_group_update_session(session, project_id)
                continue
            if kind in _gs.db_git.TR_SESSION_KINDS:
                # 088 — a TR conflict has no MERGE_HEAD anywhere and does not live in the
                # base checkout, so every branch below would call it an orphan and close it
                # while the conflicted revert sat on disk with nothing pointing at it.
                _sweep_tr_session(session, project_id)
                continue
            review_state = _gs.db_git.session_context(session).get("review_state")
            if review_state == _gs.REVIEW_STATE_COMPLETED:
                # 0594 T0012 §6.3: the review finished but the row was never closed.
                _finish_completed_review(session, project_id)
                continue
            if review_state in (_gs.REVIEW_STATE_APPLYING, _gs.REVIEW_STATE_RECONCILING):
                # 0481 T0008: `approve_merge_review` already committed by this point, so
                # MERGE_HEAD is gone from disk exactly like a normal successful merge —
                # every branch below would misread that as an orphan and abort a session
                # that is mid-push or already pushed. This state belongs to
                # reconcile_push_session, not the orphan/TTL sweep.
                if review_state == _gs.REVIEW_STATE_RECONCILING:
                    try:
                        _gs.reconcile_push_session(int(session["merge_id"]), trigger="periodic")
                    except Exception:
                        _log.warning(
                            "merge review reconciliation failed for merge_id=%s",
                            session.get("merge_id"), exc_info=True,
                        )
                continue
            # 0594 T0012 §7: an attempt record exists from BEFORE its merge runs.
            # A live runner (its lock holder still owns the project lock) is left
            # alone; one whose runner is gone without ever reaching a conflict is
            # closed as interrupted. Neither is a conflict session.
            phase = merge_target.attempt_phase(session)
            if phase == merge_target.PHASE_IN_PROGRESS:
                continue
            if phase == merge_target.PHASE_INTERRUPTED:
                _recover_interrupted(session, project_id, "interrupted")
                continue
            # §9.1/§9.3: a workspace whose marker names another attempt is never
            # read as this session's orphan/TTL case — row and merge state stay.
            if merge_target.session_workspace_ownership(session) in (
                merge_target.OWN_MISMATCH, merge_target.OWN_STALE,
            ):
                _log.warning(
                    "merge session %s: target workspace owned by another attempt — left intact",
                    session.get("merge_id"),
                )
                continue
            base_root, is_base = merge_target.session_merge_root(session)
            if is_base:
                if base_root is None or not (base_root / ".git").exists():
                    continue   # checkout gone — do not touch (log only)
            elif base_root is None or not base_root.exists():
                # The managed workspace is gone: no merge state is left on disk to
                # protect, so the session is an orphan like a vanished MERGE_HEAD.
                _gs._close_orphan(session, project_id)
                continue
            if not _gs._merge_in_progress(base_root):
                _gs._close_orphan(session, project_id)
                continue
            last = session.get("touched_at") or session.get("created_at")
            if not _ttl_expired(last):
                continue
            _auto_abort_session(session, project_id, base_root, "ttl_expired")
        except Exception:
            _log.warning(
                "merge session sweep failed for merge %s", session.get("merge_id"),
                exc_info=True,
            )


def _recover_interrupted(session: dict, project_id: str, reason: str) -> None:
    """Close an interrupted finalize attempt under a short sweep lock (T0012 §6.3)."""
    from modules.flow_gate.services import git_service as _gs
    holder = f"sweep:{uuid.uuid4()}"
    if not _gs._acquire_lock(project_id, holder):
        return   # another git op in progress — try again next cycle
    try:
        fresh = _gs.db_git.get_session(int(session["merge_id"]))
        if fresh is None or fresh.get("status") != "open":
            return
        if merge_target.attempt_phase(fresh) != merge_target.PHASE_INTERRUPTED:
            return
        outcome = merge_target.recover_interrupted_attempt(fresh, reason)
        if outcome == merge_target.ATTEMPT_INTERRUPTED:
            _emit_auto_aborted(project_id, fresh["group_id"], int(fresh["merge_id"]), reason)
    finally:
        _gs.db_git.release_lock(project_id, holder)


def _finish_completed_review(session: dict, project_id: str) -> None:
    """Finish a completed review whose row was left open, under a short sweep lock
    (a live approve holds the project lock until it has closed the row itself)."""
    from modules.flow_gate.services import git_service as _gs
    holder = f"sweep:{uuid.uuid4()}"
    if not _gs._acquire_lock(project_id, holder):
        return   # another git op in progress — try again next cycle
    try:
        fresh = _gs.db_git.get_session(int(session["merge_id"]))
        if fresh is None or fresh.get("status") != "open":
            return
        merge_target.finish_completed_review(fresh)
    finally:
        _gs.db_git.release_lock(project_id, holder)


def _start_sweep_daemon() -> None:
    """Launch the periodic sweep loop once (0205 L §2.6). Idempotent."""
    from modules.flow_gate.services import git_service as _gs
    if _gs._sweep_daemon_started:
        return
    _gs._sweep_daemon_started = True
    import threading

    def _loop() -> None:
        from modules.flow_gate.services import git_service as _gs
        while True:
            time.sleep(SWEEP_INTERVAL_MIN * 60)
            try:
                _gs.merge_session_sweep()
            except Exception:
                _log.warning("periodic merge session sweep failed", exc_info=True)

    threading.Thread(target=_loop, name="git-merge-sweep", daemon=True).start()


def _open_sessions_after(sessions: list[dict], touched: set[int]) -> list[dict]:
    """Refresh only rows this startup path may have changed, retaining open ones."""
    from modules.flow_gate.services import git_service as _gs
    if not touched:
        return sessions
    result: list[dict] = []
    for session in sessions:
        merge_id = int(session["merge_id"])
        if merge_id not in touched:
            result.append(session)
            continue
        try:
            fresh = _gs.db_git.get_session(merge_id)
        except Exception:
            # A failed refresh must not risk a duplicate orphan abort/event.
            continue
        if fresh and (fresh.get("status") or "") == "open":
            result.append(fresh)
    return result


def startup_recovery() -> None:
    """Heal conflict sessions, drop every stale lock, then sweep + start the
    daemon at boot (0205 L §2.6).

    A live MERGE_HEAD session is left in 'conflict' (state re-affirmed) but its
    lock is NOT re-acquired — the base is protected by the state gate, not a mutex
    (0205 §2.1). Sessions with no MERGE_HEAD are auto-aborted (orphan recovery).
    Any surviving lock is stale by definition (nothing legitimately outlives a
    restart) — every project lock row is force-released regardless of holder.
    Finally a sweep reclaims TTL-expired sessions and the daemon repeats it
    periodically."""
    from modules.flow_gate.services import git_service as _gs
    try:
        sessions = _gs.db_git.list_open_sessions()
        touched: set[int] = set()
        for session in sessions:
            merge_id = session["merge_id"]
            group_id = session["group_id"]
            try:
                project_id = _gs._project_of_group(group_id)
                kind = _gs.db_git.session_kind(session)
                if kind == _gs.db_git.SESSION_KIND_GROUP_UPDATE:
                    # Its MERGE_HEAD lives in the group worktree; never rewrite group status.
                    continue
                if kind in _gs.db_git.TR_SESSION_KINDS:
                    # 088 — re-affirm the status and leave the on-disk question to the
                    # sweep at the end of this function, which knows where to look.
                    _gs._set_status(group_id, "conflict", merge_id=merge_id)
                    continue
                review_state = _gs.db_git.session_context(session).get("review_state")
                if review_state == _gs.REVIEW_STATE_COMPLETED:
                    # 0594 T0012 §6.3: the review finished (ledger may already say
                    # merged) but the process died before the row was closed.
                    merge_target.finish_completed_review(session)
                    touched.add(int(merge_id))
                    continue
                if review_state in (_gs.REVIEW_STATE_APPLYING, _gs.REVIEW_STATE_RECONCILING):
                    # 0481 T0008 / L0007 §2.8.1 item 1: the commit already landed by this
                    # point, so MERGE_HEAD is gone exactly like an ordinary successful
                    # merge — re-affirm 'conflict' (still not merged from the group's
                    # point of view) and let the immediate reconcile scan below settle
                    # push-unknown/post-push-cleanup sessions without waiting a full
                    # PUSH_RECONCILE_RETRY_INTERVAL_SEC.
                    _gs._set_status(group_id, "conflict", merge_id=merge_id)
                    touched.add(int(merge_id))
                    continue
                # 0594 T0012 §6.3/§7: every lock is stale at boot, so an attempt that
                # never reached a conflict was interrupted by the restart itself.
                # (The pre-restart lock row is still present until the force-release
                # below, so the live-runner test is skipped here: at boot nothing runs.)
                if merge_target.attempt_phase(session, at_boot=True) != merge_target.PHASE_CONFLICT:
                    # A merge that already landed is reconciled to completed; one that
                    # did not is closed as interrupted; an unprovable one stays open.
                    outcome = merge_target.recover_interrupted_attempt(session, "interrupted_by_restart")
                    if outcome == merge_target.ATTEMPT_INTERRUPTED:
                        _emit_auto_aborted(project_id, group_id, int(merge_id), "interrupted_by_restart")
                    touched.add(int(merge_id))
                    continue
                # §9.1/§9.3: a workspace owned by another attempt is left as it is.
                if merge_target.session_workspace_ownership(session) in (
                    merge_target.OWN_MISMATCH, merge_target.OWN_STALE,
                ):
                    _log.warning(
                        "startup: merge %s target workspace owned by another attempt — left intact",
                        merge_id,
                    )
                    continue
                # The conflict lives where the attempt pinned it: the base checkout
                # for a base/legacy target, the deterministic managed workspace
                # (recomputed from the recorded target) otherwise.
                base_root, _is_base = merge_target.session_merge_root(session)
                merge_head_exists = bool(
                    base_root and base_root.exists() and _gs._merge_in_progress(base_root)
                )
                if merge_head_exists:
                    # Re-affirm the status; do NOT reclaim a merge:{id} lock (§2.6).
                    _gs._set_status(group_id, "conflict", merge_id=merge_id)
                else:
                    _gs._close_orphan(session, project_id)
                    touched.add(int(merge_id))
            except Exception:
                _log.warning("git session recovery failed for merge %s", merge_id, exc_info=True)
        # One-time lock cleanup: no lock legitimately survives a restart, so
        # every row is force-released regardless of holder string (no prefix
        # whitelist here, or a new holder prefix silently becomes another leak).
        for lock in _gs.db_git.list_locks():
            _gs.db_git.force_release_lock(lock["project_id"])
        # The original snapshot is only a candidate-id list here; reconcile_push_session
        # re-reads each row and re-checks its guard before changing it.
        _gs.reconcile_due_merge_review_sessions("server_startup", sessions=sessions)
        # Sweep consumes context/timestamps, so refresh rows startup/reconcile could change.
        _gs.merge_session_sweep(sessions=_open_sessions_after(sessions, touched))
        _gs._start_sweep_daemon()
    except Exception:
        # Table may not exist yet (pre-migration boot) — recovery is best-effort.
        _log.info("git startup recovery skipped", exc_info=True)
