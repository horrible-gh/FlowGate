"""Recorded work-base commits and the one group change-scope floor resolver.

flowgate.default.0665 T0004 (NR0003 §5.2/§6/§7.2).  A group forks from its work
base (``groups.work_base_ref``), but every "what did this group change" question used
to be answered against ``merge-base(<project base_branch>, HEAD)``.  A group forked
from ``v0.2`` therefore reported the whole ``main..v0.2`` history as its own work, and
even a name-based ``merge-base(HEAD, v0.2)`` breaks once update-from-base absorbed
``origin/v0.2`` while the local ``v0.2`` tip stayed behind.

So the floor is never a branch NAME.  It is a commit this module recorded:

* ``work_base_sha``      — the exact start point the worktree forked from (new fork,
                           terminal reopen fork, the 0511 initial-sync reset, a
                           verified backfill or an administrator confirmation).
* ``work_base_sync_sha`` — the exact source commit the last successful
                           update-from-base merged.

``scope_floor = work_base_sync_sha ?? work_base_sha``.  Every consumer (scope,
manifest, TR checks, final-approval recheck, approval change list, per-file diff,
review package, archive) goes through :func:`resolve_scope_floor`; a floor that is
missing, unverified or fails an integrity rule is an explicit error, never a silent
fallback to the project base or ``main``.

Integrity rules (NR0003 §6):

3. the floor is an ancestor of the group HEAD; ``work_base_sha`` is an ancestor of
   ``work_base_sync_sha`` when the latter exists;
6. no recorded group commit (``tr_commit_ledger``) that is in the HEAD history is
   at-or-before the floor (except commits a terminal reopen already merged, which are
   ancestors of the recorded terminal commit);
7. ``work_base_sha`` and ``work_base_sync_sha`` belong to the work base history
   (``refs/heads/<W>`` or ``refs/remotes/origin/<W>``).
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

from modules.flow_gate.db import groups as db_groups
from modules.flow_gate.db import tr_commit_ledger as db_tr_ledger
from modules.flow_gate.db.connection import now_iso

from .credentials import GitServiceError

_log = logging.getLogger(__name__)

ERR_UNVERIFIED = "group_work_base_unverified"
ERR_DIVERGED = "group_work_base_diverged"
ERR_SOURCE_REWRITTEN = "group_work_base_source_rewritten"
ERR_RECORD_FAILED = "group_work_base_record_failed"
ERR_CONFIRM_INVALID = "group_work_base_confirm_invalid"

# Classification reasons (stored in work_base_evidence.reason).
REASON_NOT_MIGRATED = "not_migrated"
REASON_UPDATE_HISTORY_UNPROVEN = "update_history_unproven"
REASON_FORK_AMBIGUOUS = "fork_ambiguous"
REASON_FORK_CONTAINS_GROUP_WORK = "fork_contains_group_work"
REASON_WORK_BASE_MISSING = "work_base_missing"
REASON_LEDGER_UNAVAILABLE = "ledger_unavailable"
REASON_WORKTREE_UNAVAILABLE = "worktree_unavailable"
REASON_TERMINAL = "terminal_group"

USABLE_STATES = ("verified", "confirmed")
_CHECK_TIMEOUT_SEC = 15


class FloorCheckFailed(Exception):
    def __init__(self, reason: str, **details):
        super().__init__(reason)
        self.reason = reason
        self.details = details


# ── small Git probes ─────────────────────────────────────────────────────────

def _git(cwd: Path, args: list[str]):
    from modules.flow_gate.services import git_service as _gs
    return _gs._run_git(args, cwd=cwd, timeout=_CHECK_TIMEOUT_SEC)


def rev_commit(cwd: Path, rev: Optional[str]) -> Optional[str]:
    """Full SHA of ``rev`` when it names an existing commit, else None."""
    if not rev:
        return None
    proc = _git(cwd, ["rev-parse", "--verify", "--quiet", f"{rev}^{{commit}}"])
    sha = (proc.stdout or "").strip()
    return sha if proc.returncode == 0 and sha else None


def is_ancestor(cwd: Path, ancestor: str, descendant: str) -> bool:
    """``git merge-base --is-ancestor``; an error is a failed check, not a pass."""
    proc = _git(cwd, ["merge-base", "--is-ancestor", ancestor, descendant])
    if proc.returncode not in (0, 1):
        raise FloorCheckFailed("git_error", diagnostic=(proc.stderr or "").strip()[:200])
    return proc.returncode == 0


def work_base_history_refs(cwd: Path, work_base_ref: str) -> list[str]:
    """The work-base refs that currently exist (local first, then origin)."""
    from modules.flow_gate.services import git_service as _gs
    refs = [f"refs/heads/{work_base_ref}", f"refs/remotes/origin/{work_base_ref}"]
    return [ref for ref in refs if _gs._ref_exists(cwd, ref)]


def in_work_base_history(cwd: Path, sha: str, work_base_ref: str) -> bool:
    refs = work_base_history_refs(cwd, work_base_ref)
    if not refs:
        raise FloorCheckFailed(REASON_WORK_BASE_MISSING, work_base_ref=work_base_ref)
    return any(is_ancestor(cwd, sha, ref) for ref in refs)


def _ledger_commits(group_id: str) -> list[dict]:
    try:
        rows = db_tr_ledger.group_commit_evidence(group_id)
    except Exception as exc:  # noqa: BLE001 — an unreadable ledger proves nothing
        _log.warning("work base ledger probe failed for %s", group_id, exc_info=True)
        raise FloorCheckFailed(REASON_LEDGER_UNAVAILABLE) from exc
    return rows or []


def _ledger_shas(rows: list[dict]) -> list[str]:
    shas: list[str] = []
    for row in rows:
        for key in ("commit_sha", "cancel_commit"):
            sha = str(row.get(key) or "").strip()
            if sha and sha not in shas:
                shas.append(sha)
    return shas


# ── integrity rules ──────────────────────────────────────────────────────────

def check_floor(
    cwd: Path,
    *,
    group_id: str,
    work_base_ref: Optional[str],
    work_base_sha: Optional[str],
    work_base_sync_sha: Optional[str] = None,
    head: str = "HEAD",
    exempt_upto: Optional[str] = None,
    ledger_rows: Optional[list[dict]] = None,
) -> dict:
    """Run rules 3/6/7 and return the verified floor; raise FloorCheckFailed otherwise.

    ``exempt_upto`` is a terminal reopen's C1: ledger commits already merged with it
    are work-base history now, not pending group work.
    """
    if not work_base_ref:
        raise FloorCheckFailed(REASON_WORK_BASE_MISSING)
    base_sha = rev_commit(cwd, work_base_sha)
    if base_sha is None:
        raise FloorCheckFailed("work_base_sha_missing", work_base_sha=work_base_sha)
    sync_sha = None
    if work_base_sync_sha:
        sync_sha = rev_commit(cwd, work_base_sync_sha)
        if sync_sha is None:
            raise FloorCheckFailed("work_base_sync_sha_missing", work_base_sync_sha=work_base_sync_sha)
        if not is_ancestor(cwd, base_sha, sync_sha):
            raise FloorCheckFailed("sync_not_descendant_of_fork",
                                   work_base_sha=base_sha, work_base_sync_sha=sync_sha)
    floor = sync_sha or base_sha
    head_sha = rev_commit(cwd, head)
    if head_sha is None:
        raise FloorCheckFailed("head_unavailable")
    # Rule 3
    if not is_ancestor(cwd, floor, head_sha):
        raise FloorCheckFailed("floor_not_ancestor_of_head", floor_sha=floor, head=head_sha)
    # Rule 7
    for label, sha in (("work_base_sha", base_sha), ("work_base_sync_sha", sync_sha)):
        if sha and not in_work_base_history(cwd, sha, work_base_ref):
            raise FloorCheckFailed("outside_work_base_history", field=label, sha=sha,
                                   work_base_ref=work_base_ref)
    # Rule 6
    rows = ledger_rows if ledger_rows is not None else _ledger_commits(group_id)
    exempt = rev_commit(cwd, exempt_upto) if exempt_upto else None
    for sha in _ledger_shas(rows):
        full = rev_commit(cwd, sha)
        if full is None:
            # Every commit reachable from HEAD exists in the object store, so a
            # missing ledger object cannot be part of this history.
            continue
        if not is_ancestor(cwd, full, head_sha):
            continue
        if exempt is not None and is_ancestor(cwd, full, exempt):
            continue
        if is_ancestor(cwd, full, floor):
            raise FloorCheckFailed("floor_contains_group_work", ledger_commit=full, floor_sha=floor)
    return {"floor_sha": floor, "work_base_sha": base_sha, "work_base_sync_sha": sync_sha,
            "head": head_sha}


# ── resolver (the single source of truth for every scope consumer) ──────────

def _state_of(group_id: str, state: Optional[dict]) -> dict:
    from modules.flow_gate.services import git_service as _gs
    return state if state is not None else (_gs.db_git.get_state(group_id) or {})


def describe(state: Optional[dict]) -> dict:
    """Public, display-only description of a group's recorded floor."""
    from modules.flow_gate.services import git_service as _gs
    state = state or {}
    evidence = _gs.db_git.work_base_evidence(state)
    return {
        "work_base_sha": state.get("work_base_sha"),
        "work_base_sync_sha": state.get("work_base_sync_sha"),
        "work_base_state": state.get("work_base_state") or None,
        "work_base_origin": evidence.get("origin"),
        "work_base_reason": evidence.get("reason") if state.get("work_base_state") != "verified" else None,
    }


def resolve_scope_floor(
    project_id: str,
    group_id: str,
    cwd: Path,
    *,
    state: Optional[dict] = None,
    config: Optional[dict] = None,
    head: str = "HEAD",
) -> dict:
    """``{floor_sha, work_base_sha, work_base_sync_sha, work_base_ref, work_base_state}``.

    Raises ``GitServiceError(409, group_work_base_unverified | group_work_base_diverged)``.
    Never substitutes the project base or a branch tip for a missing record.
    """
    from modules.flow_gate.services import git_service as _gs
    state = _state_of(group_id, state)
    work_base_ref = _gs.resolve_group_work_base_ref(project_id, group_id, config=config)
    wb_state = state.get("work_base_state") or None
    evidence = _gs.db_git.work_base_evidence(state)
    if wb_state not in USABLE_STATES:
        raise GitServiceError(
            409, ERR_UNVERIFIED,
            "the group's work-base commit is not verified; an administrator must confirm it",
            details={
                "group_id": group_id,
                "work_base_ref": work_base_ref,
                "work_base_state": wb_state,
                "reason": evidence.get("reason") or REASON_NOT_MIGRATED,
                "candidates": evidence.get("candidates") or [],
            },
        )
    try:
        checked = check_floor(
            cwd, group_id=group_id, work_base_ref=work_base_ref,
            work_base_sha=state.get("work_base_sha"),
            work_base_sync_sha=state.get("work_base_sync_sha"),
            head=head, exempt_upto=evidence.get("terminal_commit"),
        )
    except FloorCheckFailed as exc:
        raise GitServiceError(
            409, ERR_DIVERGED,
            "the group's recorded work-base commit no longer matches its history",
            details={
                "group_id": group_id, "work_base_ref": work_base_ref,
                "work_base_sha": state.get("work_base_sha"),
                "work_base_sync_sha": state.get("work_base_sync_sha"),
                "reason": exc.reason, **exc.details,
            },
        ) from exc
    return {
        "floor_sha": checked["floor_sha"],
        "work_base_sha": checked["work_base_sha"],
        "work_base_sync_sha": checked["work_base_sync_sha"],
        "work_base_ref": work_base_ref,
        "work_base_state": wb_state,
    }


# ── recording (fork / reopen / initial sync / update) ───────────────────────

def _log_safe(group_id: str, project_id: str, kind: str, **values) -> None:
    from modules.flow_gate.services import git_service as _gs
    try:
        _gs.db_git.append_work_base_log(group_id, project_id, kind, **values)
    except Exception:
        _log.warning("work base audit log failed for %s (%s)", group_id, kind, exc_info=True)
        raise


def record_fork(
    project_id: str,
    group_id: str,
    repo: Path,
    *,
    start_ref: str,
    work_base_ref: str,
    kind: str = "fork",
    terminal_commit: Optional[str] = None,
    head: Optional[str] = None,
    source_ref: Optional[str] = None,
) -> dict:
    """Record the exact commit a NEW group worktree (or a reopen) starts from.

    Called inside the project Git lock right after ``worktree add`` succeeded.  The
    value is checked against rules 3/6/7 before it is called ``verified``; a failure
    stores ``unverified`` with the reason so scope reads fail explicitly.
    """
    from modules.flow_gate.services import git_service as _gs
    sha = rev_commit(repo, start_ref)
    evidence: dict = {"origin": kind, "source_ref": source_ref or start_ref,
                      "recorded_at": now_iso()}
    if terminal_commit:
        evidence["terminal_commit"] = terminal_commit
    state = "verified"
    if sha is None:
        state = "unverified"
        evidence["reason"] = REASON_WORK_BASE_MISSING
    else:
        try:
            check_floor(repo, group_id=group_id, work_base_ref=work_base_ref,
                        work_base_sha=sha, head=head or sha, exempt_upto=terminal_commit)
        except FloorCheckFailed as exc:
            state = "unverified"
            evidence["reason"] = exc.reason
            evidence["check"] = exc.details
    _gs.db_git.set_work_base_record(
        group_id, work_base_sha=sha, work_base_sync_sha=None, state=state, evidence=evidence,
    )
    _log_safe(group_id, project_id, kind, source_ref=source_ref or start_ref, source_sha=sha,
              result_head=head or sha, evidence={"state": state, **evidence})
    return {"work_base_sha": sha, "work_base_state": state}


def record_reset(project_id: str, group_id: str, repo: Path, *, sha: str,
                 work_base_ref: str) -> None:
    """The 0511 initial sync reset the worktree to ``sha``: that IS the fork point now."""
    from modules.flow_gate.services import git_service as _gs
    state = _gs.db_git.get_state(group_id) or {}
    if state.get("work_base_sha") == sha and state.get("work_base_state") in USABLE_STATES:
        return
    record_fork(project_id, group_id, repo, start_ref=sha, work_base_ref=work_base_ref,
                kind="fork", head=sha)


def guard_update_source(
    project_id: str, group_id: str, wt_path: Path, *, source_ref: str,
    state: Optional[dict] = None, config: Optional[dict] = None,
) -> dict:
    """Pre-merge contract of update-from-base.  Returns the floor + exact source SHA.

    Refuses an unverified group (administrator confirmation comes first) and a
    source that no longer contains the current floor (force-reset / rewritten work
    base) — the floor must never move backwards or sideways.
    """
    floor = resolve_scope_floor(project_id, group_id, wt_path, state=state, config=config)
    source_sha = rev_commit(wt_path, source_ref)
    if source_sha is None:
        raise GitServiceError(409, ERR_SOURCE_REWRITTEN, "update source does not resolve to a commit",
                              details={"source_ref": source_ref})
    try:
        contains = is_ancestor(wt_path, floor["floor_sha"], source_sha)
    except FloorCheckFailed as exc:
        raise GitServiceError(500, "git_error", "Git command failed",
                              diagnostic=exc.details.get("diagnostic")) from exc
    if not contains:
        raise GitServiceError(
            409, ERR_SOURCE_REWRITTEN,
            "the work base no longer contains the group's last absorbed commit "
            "(it was reset or rewritten); update refused",
            details={"source_ref": source_ref, "source_sha": source_sha,
                     "floor_sha": floor["floor_sha"], "work_base_ref": floor["work_base_ref"]},
        )
    return {**floor, "source_sha": source_sha, "source_ref": source_ref}


def record_update(
    project_id: str, group_id: str, wt_path: Path, *, source_ref: str, source_sha: str,
    floor_before: str, path: str, merge_id: Optional[int] = None,
    config: Optional[dict] = None,
) -> dict:
    """Advance the floor to the merged source after a completed update.

    Raises ``GitServiceError(group_work_base_record_failed)`` when the new floor
    does not verify, the compare-and-set lost, or the floor and its audit row could
    not be written together (the previous floor is kept); the caller rolls the merge
    back so an update is never reported successful with a stale floor.
    """
    from modules.flow_gate.services import git_service as _gs
    state = _gs.db_git.get_state(group_id) or {}
    work_base_ref = _gs.resolve_group_work_base_ref(project_id, group_id, config=config)
    evidence = _gs.db_git.work_base_evidence(state)
    try:
        checked = check_floor(
            wt_path, group_id=group_id, work_base_ref=work_base_ref,
            work_base_sha=state.get("work_base_sha"), work_base_sync_sha=source_sha,
            exempt_upto=evidence.get("terminal_commit"),
        )
    except FloorCheckFailed as exc:
        raise GitServiceError(
            409, ERR_RECORD_FAILED,
            "the merged update source cannot become the group's scope floor",
            details={"source_ref": source_ref, "source_sha": source_sha,
                     "reason": exc.reason, **exc.details},
        ) from exc
    try:
        advanced = _gs.db_git.advance_work_base_sync(
            group_id, project_id, source_sha, floor_before, source_ref=source_ref,
            result_head=checked["head"], merge_id=merge_id,
            evidence={"path": path, "floor_before": floor_before},
        )
    except Exception as exc:
        _log.warning("work base update record failed for %s", group_id, exc_info=True)
        raise GitServiceError(
            500, ERR_RECORD_FAILED,
            "the update's scope floor and audit record could not be written; "
            "the previous floor is kept",
            details={"source_ref": source_ref, "source_sha": source_sha,
                     "floor_before": floor_before},
        ) from exc
    if not advanced:
        raise GitServiceError(
            409, ERR_RECORD_FAILED,
            "the group's scope floor changed while the update ran",
            details={"source_ref": source_ref, "source_sha": source_sha},
        )
    return {"source_ref": source_ref, "source_sha": source_sha, "result_head": checked["head"]}


# ── existing groups: classification (dry-run first) ─────────────────────────

def _first_parent_merges(cwd: Path, since: str, head: str) -> list[dict]:
    proc = _git(cwd, ["rev-list", "--first-parent", "--merges",
                      "--format=%H%x00%P%x00%s%x00%an <%ae>%x00%cn <%ce>%x00%cI",
                      f"{since}..{head}"])
    if proc.returncode != 0:
        raise FloorCheckFailed("git_error", diagnostic=(proc.stderr or "").strip()[:200])
    merges = []
    for line in (proc.stdout or "").splitlines():
        if line.startswith("commit ") or "\x00" not in line:
            continue
        sha, parents, subject, author, committer, when = (line.split("\x00") + [""] * 6)[:6]
        merges.append({"sha": sha, "parents": parents.split(), "subject": subject,
                       "author": author, "committer": committer, "committed_at": when})
    return merges


def _done_update_sessions(group_id: str) -> list[dict]:
    from modules.flow_gate.services import git_service as _gs
    return [
        {"merge_id": s.get("merge_id"), "closed_at": s.get("closed_at")}
        for s in _gs.db_git.sessions_by_group(group_id)
        if _gs.db_git.session_kind(s) == _gs.db_git.SESSION_KIND_GROUP_UPDATE
        and s.get("status") == "done"
    ]


def _candidate_rows(cwd: Path, merges: list[dict], work_base_ref: str) -> list[dict]:
    rows = []
    for merge in merges:
        second = merge["parents"][1] if len(merge["parents"]) > 1 else None
        in_local = in_origin = None
        if second:
            in_local = (_ref_has(cwd, f"refs/heads/{work_base_ref}", second))
            in_origin = (_ref_has(cwd, f"refs/remotes/origin/{work_base_ref}", second))
        rows.append({**merge, "second_parent": second,
                     "second_parent_in_local_work_base": in_local,
                     "second_parent_in_origin_work_base": in_origin})
    return rows


def _ref_has(cwd: Path, ref: str, sha: str) -> Optional[bool]:
    from modules.flow_gate.services import git_service as _gs
    if not _gs._ref_exists(cwd, ref):
        return None
    try:
        return is_ancestor(cwd, sha, ref)
    except FloorCheckFailed:
        return None


def _recover_fork(cwd: Path, group_id: str, work_base_ref: str, rows: list[dict]) -> tuple[Optional[str], str, dict]:
    """§7.2 4-3: fork from the parent of the first group commit, against W local/origin."""
    from modules.flow_gate.services import git_service as _gs
    head = rev_commit(cwd, "HEAD")
    first = None
    for sha in _ledger_shas(sorted(rows, key=lambda r: r.get("id") or 0)):
        full = rev_commit(cwd, sha)
        if full and is_ancestor(cwd, full, head):
            first = full
            break
    anchor = rev_commit(cwd, f"{first}^1") if first else head
    if anchor is None:
        return None, REASON_FORK_CONTAINS_GROUP_WORK, {"first_group_commit": first}
    passed: list[str] = []
    tried: dict = {}
    for ref in (f"refs/heads/{work_base_ref}", f"refs/remotes/origin/{work_base_ref}"):
        if not _gs._ref_exists(cwd, ref):
            continue
        proc = _git(cwd, ["merge-base", anchor, ref])
        cand = (proc.stdout or "").strip() if proc.returncode == 0 else ""
        if not cand:
            tried[ref] = "no_merge_base"
            continue
        try:
            check_floor(cwd, group_id=group_id, work_base_ref=work_base_ref,
                        work_base_sha=cand, ledger_rows=rows)
        except FloorCheckFailed as exc:
            tried[ref] = exc.reason
            continue
        tried[ref] = cand
        if cand not in passed:
            passed.append(cand)
    details = {"anchor": anchor, "first_group_commit": first, "candidates": tried}
    if not tried:
        return None, REASON_WORK_BASE_MISSING, details
    if not passed:
        return None, REASON_FORK_CONTAINS_GROUP_WORK, details
    if len(passed) == 1:
        return passed[0], "", details
    a, b = passed
    if is_ancestor(cwd, a, b):
        return b, "", details
    if is_ancestor(cwd, b, a):
        return a, "", details
    return None, REASON_FORK_AMBIGUOUS, details


def classify_group(project_id: str, group_id: str, *, config: Optional[dict] = None) -> dict:
    """Decide what an existing group's floor provably is.  Pure read.

    Returns ``{group_id, verdict, reason, work_base_ref, work_base_sha,
    work_base_sync_sha, origin, candidates, details}`` where verdict is
    ``already_recorded`` | ``verified`` | ``unverified``.
    """
    from modules.flow_gate.services import git_service as _gs
    state = _gs.db_git.get_state(group_id) or {}
    group = db_groups.get_by_id(group_id) or {}
    stored_ref = (group.get("work_base_ref") or "").strip() or None
    work_base_ref = _gs.resolve_group_work_base_ref(project_id, group_id, group=group, config=config)
    out = {"group_id": group_id, "verdict": "unverified", "reason": None,
           "work_base_ref": work_base_ref, "work_base_ref_stored": stored_ref,
           "work_base_sha": None, "work_base_sync_sha": None, "origin": None,
           "candidates": [], "details": {}}
    if state.get("work_base_state"):
        out.update(verdict="already_recorded", reason=state.get("work_base_state"),
                   work_base_sha=state.get("work_base_sha"),
                   work_base_sync_sha=state.get("work_base_sync_sha"))
        return out
    if (state.get("status") or "none") in ("merged", "pushed") or _gs._is_group_disposed(group_id):
        out["reason"] = REASON_TERMINAL
        return out
    wt_path, _reason = _gs.effective_src_root_ex(project_id, group_id, state)
    if wt_path is None or not work_base_ref:
        out["reason"] = REASON_WORKTREE_UNAVAILABLE if work_base_ref else REASON_WORK_BASE_MISSING
        return out
    try:
        rows = _ledger_commits(group_id)
        marker = state.get("initial_source_sync_sha")
        sync_at = state.get("initial_source_sync_at")
        legacy_marker = bool(sync_at) and any(
            (row.get("created_at") or "") < sync_at for row in rows
        )
        floor: Optional[str] = None
        if marker and not legacy_marker:
            try:
                check_floor(wt_path, group_id=group_id, work_base_ref=work_base_ref,
                            work_base_sha=marker, ledger_rows=rows)
                floor = rev_commit(wt_path, marker)
                out["origin"] = "initial_sync_marker"
            except FloorCheckFailed as exc:
                out["details"]["marker_rejected"] = exc.reason
        if floor is None:
            floor, reason, details = _recover_fork(wt_path, group_id, work_base_ref, rows)
            out["details"]["recovery"] = details
            out["details"]["legacy_marker"] = legacy_marker
            if floor is None:
                out["reason"] = reason
                return out
            out["origin"] = "recovered_merge_base"
        out["work_base_sha"] = floor
        merges = _first_parent_merges(wt_path, floor, "HEAD")
        sessions = _done_update_sessions(group_id)
        if merges or sessions:
            out["reason"] = REASON_UPDATE_HISTORY_UNPROVEN
            out["candidates"] = _candidate_rows(wt_path, merges, work_base_ref)
            out["details"]["done_update_sessions"] = sessions
            return out
        out["verdict"] = "verified"
        return out
    except FloorCheckFailed as exc:
        out["reason"] = exc.reason
        out["details"]["check"] = exc.details
        return out


def backfill_project(project_id: str, *, apply: bool = False, actor: Optional[str] = None) -> dict:
    """Dry-run (default) or apply the classification for every group of a project.

    Apply also pins a NULL ``groups.work_base_ref`` of a Git-active group to the
    current effective value (NR0003 §7.3) so a later project base change cannot move it.
    A group whose pin fails is left unapplied (``applied=False`` + ``apply_error``,
    listed in ``apply_failed``): a floor recorded against a still-mutable name could
    be reinterpreted by the next base change.
    """
    from modules.flow_gate.services import git_service as _gs
    cfg = _gs.db_git.get_config(project_id)
    if cfg is None or not cfg.get("enabled"):
        return {"ok": True, "project_id": project_id, "applied": False, "git_enabled": False,
                "groups": [], "summary": {}}
    results = []
    for row in _gs.db_git.list_states_of_project_any(project_id):
        group_id = row["group_id"]
        try:
            result = classify_group(project_id, group_id, config=cfg)
        except Exception as exc:  # noqa: BLE001 — one group never stops the report
            _log.warning("work base classification failed for %s", group_id, exc_info=True)
            result = {"group_id": group_id, "verdict": "unverified", "reason": "error",
                      "details": {"error": str(exc)[:200]}}
        if apply:
            _apply_classification(project_id, result, actor=actor)
        results.append(result)
    summary: dict = {}
    for result in results:
        key = result["verdict"]
        summary[key] = summary.get(key, 0) + 1
    apply_failed = [r["group_id"] for r in results if r.get("apply_error")]
    return {"ok": True, "project_id": project_id, "applied": apply, "git_enabled": True,
            "groups": results, "summary": summary, "apply_failed": apply_failed}


def _pin_work_base_ref(group_id: str, work_base_ref: Optional[str]) -> Optional[str]:
    """Pin a NULL ``groups.work_base_ref``; returns an error code, ``None`` when pinned."""
    if not work_base_ref:
        return "work_base_ref_unresolved"
    try:
        db_groups.update_work_base_ref(group_id, work_base_ref)
        stored = ((db_groups.get_by_id(group_id) or {}).get("work_base_ref") or "").strip()
    except Exception:
        _log.warning("work_base_ref pin failed for %s", group_id, exc_info=True)
        return "work_base_ref_pin_failed"
    return None if stored == work_base_ref else "work_base_ref_pin_failed"


def _apply_classification(project_id: str, result: dict, *, actor: Optional[str]) -> None:
    from modules.flow_gate.services import git_service as _gs
    group_id = result["group_id"]
    result["applied"] = False
    if result["verdict"] == "already_recorded":
        return
    if not result.get("work_base_ref_stored"):
        # The recorded SHA only means something against a fixed name: without the pin
        # the group still follows the project base, so nothing is recorded.
        error = _pin_work_base_ref(group_id, result.get("work_base_ref"))
        if error:
            result["apply_error"] = error
            return
    evidence = {"origin": result.get("origin") or "backfill", "reason": result.get("reason"),
                "candidates": result.get("candidates") or [], "details": result.get("details") or {},
                "recorded_at": now_iso(), "classified_by": actor}
    state = "verified" if result["verdict"] == "verified" else "unverified"
    _gs.db_git.set_work_base_record(
        group_id, work_base_sha=result.get("work_base_sha"),
        work_base_sync_sha=None, state=state, evidence=evidence,
    )
    _log_safe(group_id, project_id, "backfill", source_sha=result.get("work_base_sha"),
              actor=actor, evidence={"state": state, "reason": result.get("reason"),
                                     "origin": result.get("origin")})
    result["applied"] = True


# ── administrator confirmation ───────────────────────────────────────────────

def confirm_work_base(
    project_id: str,
    group_id: str,
    *,
    work_base_sha: str,
    work_base_sync_sha: Optional[str],
    actor: Optional[str],
    basis: Optional[str] = None,
) -> dict:
    """Administrator confirmation of an (unverified) group floor.

    Every integrity rule runs again on the submitted values; any failure is a 422
    with the failing rule and no state change.  Choosing ``work_base_sync_sha=None``
    (floor = fork) over-includes absorbed commits and is always allowed.
    """
    from modules.flow_gate.services import git_service as _gs
    state = _gs.db_git.get_state(group_id)
    if state is None or (state.get("project_id") and state.get("project_id") != project_id):
        raise GitServiceError(404, "not_found", f"group '{group_id}' has no Git state")
    wt_path, _reason = _gs.effective_src_root_ex(project_id, group_id, state)
    if wt_path is None:
        raise GitServiceError(409, "invalid_state", "group worktree is not available")
    work_base_ref = _gs.resolve_group_work_base_ref(project_id, group_id)
    evidence = _gs.db_git.work_base_evidence(state)
    sync = (work_base_sync_sha or "").strip() or None
    if sync and sync.lower() == "none":
        sync = None
    # 0669 unit 9b: the Group's G (the floor check reads only its worktree) through the
    # freeze guard, instead of the project mutex.
    from .worktree import _slot_lock, _slot_unlock
    lock_ctx, held, refused = _slot_lock(project_id, group_id, holder_kind="work_base_confirm")
    if held is None:
        raise GitServiceError(409, "git_busy", "another git operation is in progress",
                              details=refused)
    try:
        try:
            checked = check_floor(
                wt_path, group_id=group_id, work_base_ref=work_base_ref,
                work_base_sha=(work_base_sha or "").strip() or None, work_base_sync_sha=sync,
                exempt_upto=evidence.get("terminal_commit"),
            )
        except FloorCheckFailed as exc:
            raise GitServiceError(
                422, ERR_CONFIRM_INVALID, "the submitted work-base commit fails verification",
                details={"reason": exc.reason, **exc.details},
            ) from exc
        new_evidence = {
            "origin": "manual_confirm", "confirmed_by": actor, "confirmed_at": now_iso(),
            "basis": basis, "previous_state": state.get("work_base_state"),
            "previous_reason": evidence.get("reason"),
            "over_inclusive": sync is None and bool(evidence.get("candidates")),
        }
        if evidence.get("terminal_commit"):
            new_evidence["terminal_commit"] = evidence["terminal_commit"]
        _gs.db_git.set_work_base_record(
            group_id, work_base_sha=checked["work_base_sha"],
            work_base_sync_sha=checked["work_base_sync_sha"], state="confirmed",
            evidence=new_evidence,
        )
        _log_safe(group_id, project_id, "manual_confirm", source_sha=checked["floor_sha"],
                  result_head=checked["head"], actor=actor,
                  evidence={"work_base_sha": checked["work_base_sha"],
                            "work_base_sync_sha": checked["work_base_sync_sha"],
                            "basis": basis})
    finally:
        _slot_unlock(lock_ctx, held)
    return {"ok": True, "group_id": group_id, "work_base_ref": work_base_ref,
            "work_base_sha": checked["work_base_sha"],
            "work_base_sync_sha": checked["work_base_sync_sha"],
            "work_base_state": "confirmed",
            "over_inclusive": new_evidence["over_inclusive"]}
