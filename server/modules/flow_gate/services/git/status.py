"""Git connectivity probes and project/base status aggregation.

Extracted from git_service.py (flowgate.default.0550 T0013, D0006 §3.2/부록 A).
"""
from __future__ import annotations

import logging
import re
import time
from pathlib import Path
from typing import Optional

from modules.flow_gate.db import project_ai_leases as db_project_ai_leases
from modules.flow_gate.db import projects as db_projects
from modules.flow_gate.db import terminal_cleanup_snapshots as db_terminal_cleanup

from .commit import _ledger_group_by_merge_sha
from .finalize import NOOP_CONVERGEABLE_STATUSES
from .credentials import GitServiceError, decrypt_secret
from .refs import (
    UNTRACKED_LIST_MAX,
    _dirty_files,
    _local_commit_count,
    _remote_base_missing,
    _unpushed_commits,
    _untracked_files,
)

_log = logging.getLogger(__name__)

SLOT_STATUSES = ("none", "awaiting_choice", "merging", "conflict", "waiting")  # not terminal

_AUTH_FAIL_PATTERNS = (
    "authentication failed", "invalid username", "401", "403",
    "could not read username", "permission denied (publickey",
)

_UNREACHABLE_PATTERNS = (
    "could not resolve host", "connection refused", "connection timed out",
    "unable to access", "timeout_expired", "network is unreachable",
)


def test_connection(project_id: str, override: Optional[dict] = None) -> dict:
    from modules.flow_gate.services import git_service as _gs
    override = override or {}
    stored = _gs.db_git.get_config(project_id)
    cfg = dict(stored) if stored else {}
    for k in ("repo_url", "username", "base_branch", "provider"):
        if override.get(k) is not None:
            cfg[k] = override[k]
    if not (cfg.get("repo_url") or "").strip():
        raise GitServiceError(
            409, "not_configured",
            f"Git integration is not configured for project '{project_id}'",
        )
    if not _gs.git_available():
        raise GitServiceError(
            500, "git_unavailable",
            "git binary not found on server (install git in the runtime image)",
        )
    if override.get("secret") is not None and override.get("secret") != "":
        secret: Optional[str] = str(override["secret"])
    elif stored and stored.get("secret_enc"):
        secret = decrypt_secret(stored["secret_enc"])
    else:
        secret = None

    base_branch = (cfg.get("base_branch") or "main").strip() or "main"
    repo_url = (cfg.get("repo_url") or "").strip()
    t0 = time.monotonic()
    proc = _gs._run_git(
        ["ls-remote", "--symref", repo_url, "HEAD", f"refs/heads/{base_branch}"],
        timeout=_gs.GIT_TEST_TIMEOUT_SEC,
        username=cfg.get("username"),
        secret=secret if secret is not None else "",
    )
    elapsed_ms = int((time.monotonic() - t0) * 1000)

    if proc.returncode == 0:
        default_branch = None
        base_exists = False
        for line in (proc.stdout or "").splitlines():
            line = line.strip()
            if line.startswith("ref:") and line.endswith("HEAD"):
                m = re.match(r"ref:\s+refs/heads/(\S+)\s+HEAD", line)
                if m:
                    default_branch = m.group(1)
            if line.endswith(f"refs/heads/{base_branch}"):
                base_exists = True
        return {
            "reachable": True,
            "authenticated": True,
            "remote_default_branch": default_branch,
            "base_branch_exists": base_exists,
            "elapsed_ms": elapsed_ms,
        }

    err = (proc.stderr or "").strip()
    low = err.lower()
    if any(p in low for p in _AUTH_FAIL_PATTERNS):
        code, reachable, authenticated = "auth_failed", True, False
    elif any(p in low for p in _UNREACHABLE_PATTERNS):
        code, reachable, authenticated = "unreachable", False, None
    else:
        code, reachable, authenticated = "git_error", True, None
    last_line = err.splitlines()[-1] if err else "git command failed"
    return {
        "reachable": reachable,
        "authenticated": authenticated,
        "remote_default_branch": None,
        "base_branch_exists": None,
        "elapsed_ms": elapsed_ms,
        "failure": {"code": code, "message": last_line},
    }


def base_checkout_dirty_status(project_id: str) -> dict:
    """Lightweight base-checkout dirty status for the file-editor save response
    (flowgate.default.0176 T0010 §a).

    A src-content save writes straight into the base checkout by design (an admin
    edit), which leaves the base dirty and — via the E3 guard — blocks merge
    finalize for EVERY group of the project. The editor calls this right after a
    save so the contamination is visible immediately instead of surfacing later as
    a bare finalize 500. `dirty`/`files` scope matches the guard exactly:
    tracked-file changes only (`include_untracked=False`).

    `untracked` is a SEPARATE field (0296 T0004 / NR0003 R1) and is NOT reflected
    in `dirty`: a brand-new file blocks nothing, but it is invisible to every
    worker until committed (the group worktree is built from a commit — NR §C1),
    so the editor needs to name it without the guard treating it as contamination.

    Never raises: the file write already succeeded, so a git-disabled project, a
    missing base checkout, or any git failure all yield a benign
    {"enabled": ..., "dirty": False, "files": [], "untracked": []} — status is
    advisory and must not turn a saved file into an error.
    """
    from modules.flow_gate.services import git_service as _gs
    empty = {"enabled": False, "dirty": False, "files": [], "untracked": []}
    try:
        cfg = _gs.db_git.get_config(project_id)
        if cfg is None or not cfg.get("enabled"):
            return dict(empty)
        base_branch = (cfg.get("base_branch") or "main").strip() or "main"
        project_name = _gs._project_name(project_id)
        base_root = _gs.src_root(project_name, base_branch) if project_name else None
        if base_root is None or not Path(base_root).is_dir():
            return {**empty, "enabled": True}
        files = _dirty_files(base_root, include_untracked=False)
        return {
            "enabled": True, "dirty": bool(files), "files": files,
            "untracked": _untracked_files(base_root),
        }
    except Exception:
        _log.warning("base_checkout_dirty_status failed for %s", project_id, exc_info=True)
        return dict(empty)


def _build_unpushed(
    project_id: str,
    base_root: Optional[Path],
    base_branch: str,
    commit_count: Optional[int] = None,
) -> dict:
    commits = _unpushed_commits(base_root, base_branch)
    if commits is None:
        # 0297 B0001: an unmeasured result used to be indistinguishable from "in
        # sync" downstream (commit_count 0), which hid the ONLY push entry point
        # while the remote was still empty. These two fields carry the bootstrap
        # case explicitly so the client decides instead of guessing.
        return {
            "count": 0, "commit_count": 0, "merges": [], "measured": False,
            "remote_branch_missing": _remote_base_missing(base_root, base_branch),
            "local_commit_count": _local_commit_count(base_root),
        }
    merge_commits = [c for c in commits if len(c["parents"]) >= 2]
    merges: list[dict] = []
    top_sha = commits[0]["full_sha"] if commits else None
    for c in merge_commits:
        group_id = _ledger_group_by_merge_sha(project_id, c["full_sha"])
        is_top = bool(top_sha and c["full_sha"] == top_sha)
        can_unmerge = is_top and group_id is not None
        if can_unmerge:
            blocked_reason = None
        elif group_id is None:
            blocked_reason = "unmapped"
        else:
            blocked_reason = "not_top"
        merges.append({
            "merge_commit": c["full_sha"][:7],
            "group_id": group_id,
            "subject": c["subject"],
            "merged_at": c["committed_at"],
            "can_unmerge": can_unmerge,
            "blocked_reason": blocked_reason,
        })
    return {
        "count": len(merges),
        "commit_count": commit_count if commit_count is not None else len(commits),
        "merges": merges,
        "measured": True,
        # Measured implies origin/{base} exists; keep the shape stable so the
        # client can read both fields unconditionally.
        "remote_branch_missing": False,
        "local_commit_count": None,
    }


def project_git_status(project_id: str) -> dict:
    """GET …/projects/{id}/git/status — status + finalize-pending list + count.

    Local repository only (no network git). Realizes the lazy none→
    awaiting_choice transition for wf_done groups at aggregation time (L §2.2).
    """
    from modules.flow_gate.services import git_service as _gs
    if db_projects.get_by_id(project_id) is None:
        raise GitServiceError(404, "not_found", f"project '{project_id}' not found")
    cfg = _gs.db_git.get_config(project_id)
    if cfg is None or not cfg.get("enabled"):
        return {"ok": True, "status": {
            "enabled": False, "base_branch": None, "base_path_state": "empty",
            "ahead_count": None, "behind_count": None,
            "slots": [], "pending": [], "pending_count": 0, "cleanable_count": 0,
            "terminal_cleanup": db_terminal_cleanup.get(project_id),
        }}

    base_branch = (cfg.get("base_branch") or "main").strip() or "main"
    default_action = cfg.get("default_finalize_action") or "wait"
    project_name = _gs._project_name(project_id)
    base_root = _gs.src_root(project_name, base_branch) if project_name else None

    # 0282 NR0003 finding 1: one ledger scan serves both the registered-slot
    # aggregation and the provision-failure surface below (previously two
    # near-identical project scans), and the per-slot wf_done probe is batched
    # into a single IN query so the loop only does set membership.
    all_rows = _gs.db_git.list_states_of_project_any(project_id)
    rows = [r for r in all_rows if r.get("worktree_registered")]
    # T0009: one batched root lookup serves both the existing lazy transition
    # and stale-pending recovery. A pending ledger row is displayable only while
    # its workflow root remains wf_done; interrupted/rework paths can otherwise
    # leave awaiting_choice/waiting visible after approval was withdrawn.
    root_candidate_statuses = ("none", "awaiting_choice", "waiting")
    wf_done_groups = _gs._groups_root_wf_done(
        [
            r["group_id"] for r in rows
            if (r.get("status") or "none") in root_candidate_statuses
        ]
    )
    for row in rows:
        status = row.get("status") or "none"
        group_id = row["group_id"]
        if status in ("awaiting_choice", "waiting") and group_id not in wf_done_groups:
            try:
                # Status-only repair: preserve the branch, registered worktree, and
                # any merge ledger fields. conflict remains an active-session state
                # and is deliberately excluded from this recovery. The row's status
                # is now "none", which SLOT_STATUSES already keeps in `slots` below
                # and PENDING_STATUSES already keeps out of `pending` — no separate
                # exclusion list is needed.
                _gs._set_status(group_id, "none")
                row["status"] = "none"
            except Exception:
                # Keep the row visible when persistence fails; hiding it without
                # repairing the ledger would make the UI disagree with the SSOT.
                _log.warning(
                    "stale git pending recovery failed for %s", group_id, exc_info=True
                )
        elif status in NOOP_CONVERGEABLE_STATUSES:
            try:
                # 0548 T0004 §3: the root is still wf_done, but the slot may hold
                # nothing to merge — converge it the same way the finalize panel
                # does, so the header's pending badge and the document's Git card
                # never disagree about whether this group has work.
                row["status"] = _gs._resolve_pending_noop(
                    project_id, cfg, row, group_id, status
                )
            except Exception:
                _log.warning(
                    "no-work pending convergence failed for %s", group_id, exc_info=True
                )
        elif status == "none" and group_id in wf_done_groups:
            try:
                # 0199 B0001: proven no-work groups are discarded (torn down, no
                # merge/push) here; real groups still transition to awaiting_choice.
                # A discarded group's slot is unregistered by the cleanup, so it
                # drops out of every list below (SLOT/PENDING/CLEANUP filters).
                row["status"] = _gs._decide_pending_transition(
                    project_id, cfg, row, group_id
                )
            except Exception:
                # One broken group must not sink the whole aggregation
                # (0115 batch-fetch exception-isolation lesson, L §5).
                _log.warning(
                    "lazy git transition failed for %s", group_id, exc_info=True
                )

    # 0327 T0004 (B0001): `writable` tells the file explorer whether this slot's
    # worktree is really there, so a working group can offer create/upload instead
    # of the blanket read-only it applied to every selected group. `rows` is already
    # filtered to worktree_registered=1, so this only re-checks the on-disk side
    # (directory present, .git link intact) — a handful of stats per status call.
    # 0552 T0013 (0005-NR Set D): `r` IS this group's group_git_state row, already
    # read by the one project-wide ledger scan above, so it is handed to the
    # writable probe instead of letting it run `SELECT * FROM group_git_state
    # WHERE group_id = ?` once per slot — the last group_git_state read that still
    # grew with slot count (8 slots = 8 queries in the R0001 screen-load log).
    # Same row, same request: `list_states_of_project_any` and `db_git.get_state`
    # are both `SELECT *` on that one table, and the only fields the probe reads —
    # worktree_registered and branch — are never written by the transition loop
    # above (`_set_status` writes status only; an auto-discard that DOES unregister
    # a slot returns DISCARDED_STATUS, which SLOT_STATUSES already excludes here).
    # Nothing is cached beyond this response; the on-disk check is untouched.
    slots = [
        {"group_id": r["group_id"], "branch": r.get("branch"),
         "status": r.get("status"), "merge_id": r.get("merge_id"),
         "writable": _gs.group_worktree_writable(project_id, r["group_id"], r)}
        for r in rows if r.get("status") in SLOT_STATUSES
    ]
    # 0332 D0005 §6.2: a group's commits are no longer one absorb commit, so each slot
    # row carries its TR commit ledger — counts always, the newest rows for the folded
    # list. One query for every slot (the N+1 this function paid off in 0282), and a
    # lazy import because tr_commit_service imports this module.
    try:
        from modules.flow_gate.services import tr_commit_service
        summaries = tr_commit_service.group_commit_summaries(
            [s["group_id"] for s in slots]
        )
        for slot in slots:
            slot["tr_commits"] = summaries.get(
                slot["group_id"], dict(tr_commit_service.EMPTY_SUMMARY)
            )
    except Exception:
        # Advisory display state: a ledger that cannot be read leaves the panel looking
        # exactly as it did before this feature, never breaks the status call.
        _log.warning("tr commit slot summaries failed for %s", project_id, exc_info=True)
    pending_rows = [r for r in rows if r.get("status") in _gs.PENDING_STATUSES]
    # 0282 NR0003 finding 1: the AC lookup was the next N+1 in line — batched
    # before pending grows with adoption.
    ac_doc_ids = _gs._group_ac_doc_ids([r["group_id"] for r in pending_rows])
    pending = [
        {"group_id": r["group_id"], "branch": r.get("branch"),
         "status": r.get("status"), "default_action": default_action,
         # 0165 T0004: merge_id lets the header panel resolve conflicts inline
         # (no need to open the group's R document / GitFinalizePanel).
         "merge_id": r.get("merge_id"),
         # 0182 NR0003 §4: pending implies the workflow root is wf_done, so the
         # header [open] button targets the AC document (which hosts the git
         # finalize UI since §3) instead of detouring through the R root.
         "ac_doc_id": ac_doc_ids.get(r["group_id"])}
        for r in pending_rows
    ]
    # 0205 P scenario 8: annotate conflict pending rows with how long they have
    # been unresolved (elapsed = now − conflict_since), so the panel can surface
    # the wait time and offer [resume resolution]/[hold]. Other rows carry no field.
    for row in pending:
        if row.get("status") == "conflict" and row.get("merge_id") is not None:
            try:
                s = _gs.db_git.get_session(int(row["merge_id"]))
                row["conflict_since"] = s.get("created_at") if s else None
                # 0481 D0006 §6.4: the same badge slot doubles as the general-merge
                # review gate's entry point — None/absent means "still resolving"
                # (the resolver dialog), any REVIEW_PENDING_STATES value means
                # "승인 대기" (the review dialog instead).
                if s is not None and _gs.db_git.session_kind(s) == _gs.db_git.SESSION_KIND_MERGE:
                    ctx = _gs.db_git.session_context(s)
                    row["review_state"] = ctx.get("review_state")
                    row["reconciliation_kind"] = ctx.get("reconciliation_kind")
                else:
                    row["review_state"] = None
                    row["reconciliation_kind"] = None
            except Exception:
                row["conflict_since"] = None
                row["review_state"] = None
                row["reconciliation_kind"] = None
    # 0205 P scenario 8: persisted worktree provisioning failures (unregistered
    # rows with a provision_error) so a slot-less group's "not tracked by git" warning
    # survives the one-shot SSE. Disposed groups are excluded. Newest first.
    provision_failures: list[dict] = []
    try:
        for r in all_rows:
            if (
                r.get("provision_error")
                and not r.get("worktree_registered")
                and not _gs._is_group_disposed(r["group_id"])
            ):
                provision_failures.append({
                    "group_id": r["group_id"],
                    "error": r.get("provision_error"),
                    "failed_at": r.get("provision_failed_at"),
                })
        provision_failures.sort(key=lambda x: x.get("failed_at") or "", reverse=True)
    except Exception:
        _log.warning("provision_failures aggregation failed for %s", project_id, exc_info=True)
        provision_failures = []
    # 0182 NR0003 §5: registered slots already finalized (merged/pushed) are
    # cleanup backlog — surfaced so the panel can offer the [clean up] action.
    cleanable_count = sum(1 for r in rows if r.get("status") in _gs.CLEANUP_STATUSES)
    ahead, behind = _gs._base_ahead_behind(base_root, base_branch)
    base_path_state = _gs._judge_base_slot(base_root, base_branch) if base_root else "occupied"
    # 0177 L0002 §2.1: base-checkout dirty set (tracked files only) so the header
    # panel can offer commit/revert BEFORE a merge bounces off the E3 guard.
    # Never-raise, matching base_checkout_dirty_status: a missing checkout or any
    # git failure reads as clean — the field is advisory display state.
    # 0296 T0004 (NR0003 R1): the untracked set rides alongside in its OWN field.
    # It must never fold into base_dirty — that would widen the E3 guard to build
    # artifacts, the exact regression 0165.0009 fixed. It exists so the panel can
    # say "N new files are not in any group worktree yet" and offer the commit.
    base_readable = (
        base_root is not None and (base_root / ".git").exists() and _gs.git_available()
    )
    try:
        base_dirty_files = _dirty_files(base_root, include_untracked=False) if base_readable else []
    except Exception:
        _log.warning("base_dirty aggregation failed for %s", project_id, exc_info=True)
        base_dirty_files = []
    # 0481 T0010 #1: whose changes are these? While a merge is stopped on a conflict the
    # base checkout's dirty set IS the merge, and the panel must offer the resolver instead
    # of the commit / revert / AI-delegation cleanup it offers for stray edits.
    try:
        base_dirty_merge = _gs.base_merge_in_progress(project_id) if base_readable else None
    except Exception:
        _log.warning("base merge-in-progress lookup failed for %s", project_id, exc_info=True)
        base_dirty_merge = None
    # 0481 T0010 rev6 (rejection 2): whether a base-branch AI cleanup run owns this
    # project RIGHT NOW, straight from the durable admission lease the start route
    # takes. Before this the panel only had its own in-browser latch, so a 409
    # "already running" refusal disabled [AI에게 맡기기] for good in that tab: the
    # blocking run belongs to another session, so no SSE frame for it ever arrives
    # and nothing could clear the latch. Advisory display state, never-raise, exactly
    # like the two lookups above.
    try:
        base_ai_lease = db_project_ai_leases.get_active(project_id)
    except Exception:
        _log.warning("base AI cleanup lease lookup failed for %s", project_id, exc_info=True)
        base_ai_lease = None
    try:
        base_untracked_files = _untracked_files(base_root) if base_readable else []
    except Exception:
        _log.warning("base_untracked aggregation failed for %s", project_id, exc_info=True)
        base_untracked_files = []
    unpushed = _gs._build_unpushed(project_id, base_root, base_branch, ahead)
    return {"ok": True, "status": {
        "enabled": True, "base_branch": base_branch,
        "base_path_state": base_path_state,
        "ahead_count": ahead, "behind_count": behind,
        "base_dirty": {
            "dirty": bool(base_dirty_files), "files": base_dirty_files,
            "merge_in_progress": base_dirty_merge,
            "ai_run": (
                {
                    "run_id": base_ai_lease.get("run_id"),
                    "state": base_ai_lease.get("state"),
                    "acquired_at": base_ai_lease.get("acquired_at"),
                }
                if base_ai_lease else None
            ),
        },
        "base_untracked": {
            "count": len(base_untracked_files),
            "files": base_untracked_files,
            "truncated": len(base_untracked_files) >= UNTRACKED_LIST_MAX,
        },
        "slots": slots, "pending": pending, "pending_count": len(pending),
        "cleanable_count": cleanable_count,
        "terminal_cleanup": db_terminal_cleanup.get(project_id),
        "provision_failures": provision_failures,
        "unpushed": unpushed,
    }}
