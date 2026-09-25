"""Finalize state machine, group updates, push, and reopen operations.

Extracted from git_service.py (flowgate.default.0550 T0015, D0006 §3.2/부록 A).
"""
from __future__ import annotations

import logging
import re
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional, Sequence

from modules.flow_gate.db import documents as db_documents
from modules.flow_gate.db.connection import get_store

from . import approval_intent
from .base_slot import default_base_commit_message
from .commit import (
    COMMIT_SUBJECT_MAX,
    _absorb_worker_edits,
    _artifact_payload,
    _ledger_group_by_merge_sha,
    _merge_commit_subject,
)
from . import merge_target
from .credentials import GitServiceError, _author_env_from_cfg
from .refs import (
    _ahead_of_base,
    _dirty_files,
    _full_sha_matches,
    _is_ancestor,
    _rev_parse,
    _short_head,
    _unpushed_commits,
    _unpushed_local_merge_of,
    _untracked_files,
    _worktree_untracked_summary_for_path,
)
from .worktree import _ensure_worktree_locked, worktree_branch_name

_log = logging.getLogger(__name__)

ACTION_VALUES = ("merge", "merge_only", "push", "commit_push", "commit_only", "wait")
APPROVAL_FINALIZE_ACTIONS = ("merge", "merge_only", "push", "commit_push")
FINALIZE_MAIN_CHOICES = ("merge", "merge_only", "wait")
FINALIZE_AUX_CHOICES = ("push",)


@dataclass(frozen=True)
class ApprovalFinalizeContext:
    """Unforgeable-in-HTTP capability for finalize-before-approval execution."""

    doc_id: str
    group_id: str
    actor_user_id: str
    lock_holder: str
    # 0555 T0008 §2: the one-shot id this request would hand to a conflict session
    # so the deferred approval can be found again later (D0005 §3.8).  Generated per
    # final-approval REQUEST, never derived from the merge session it may create.
    approval_intent_id: str = ""

# NR flowgate.default.0331.0005 §8 — the approved v4 mockup drives the finalize
# UI from two INDEPENDENT axes (scope of application x push to remote) instead of a flat card
# list, so 6 actions fit where 4 used to. Published ADDITIVELY next to the legacy
# `choices`/`aux_choices` (which stay exactly as they were) so an older client
# keeps rendering while the axis client prefers this matrix. Display order is the
# approved one: merge → commit → wait.
FINALIZE_AXIS_SCOPES = ("merge", "commit", "none")
FINALIZE_AXIS_MATRIX = {
    "merge": {"push": "merge", "no_push": "merge_only"},
    "commit": {"push": "commit_push", "no_push": "commit_only"},
    "none": {"push": "push", "no_push": "wait"},
}

# Actions that produce a commit and therefore need a commit subject from the
# operator. `push` is deliberately absent: since the 0331 contract fix it only
# ships existing commits and 409s on a dirty worktree, so asking for a message
# there would promise a commit the server will not make.
FINALIZE_COMMIT_ACTIONS = ("merge", "merge_only", "commit_push", "commit_only")

UNMERGE_SHA_RE = re.compile(r"^[0-9a-f]{7,40}$", re.IGNORECASE)

# flowgate.default.0199 B0001 — RESPONSE/SSE label (not a persisted git state:
# the group_git_state.status CHECK has no such value). A wf_done group that
# produced NO work (its work branch sits at the base tip with a clean worktree —
# e.g. a pure R/CH/AC inquiry with no T) is auto-terminated: its slot is torn
# down (worktree removed, local branch force-deleted, ledger unregistered) with
# NO merge and NO push, so base never gets an empty `--no-ff` merge commit and
# origin never gets a leaked empty branch. The DB row is left status="none" +
# worktree_registered=0 (indistinguishable from an un-provisioned slot, which is
# exactly right — there is nothing to finalize); finalize/status responses report
# this label so callers can tell an auto-discard from a real merge/push.
DISCARDED_STATUS = "discarded"
NOOP_CONVERGEABLE_STATUSES = ("awaiting_choice",)

_NONE_STATE = {
    "branch": None, "base_branch": None, "status": "none", "default_action": None,
    "choices": [], "aux_choices": [], "action_axes": None,
    "ahead_count": None, "behind_count": None, "merge_id": None,
}

_UNTRACKED_MERGE_RE = re.compile(
    r"untracked working tree files? would be (?:overwritten|removed) by", re.I
)

_TRACKED_MERGE_RE = re.compile(
    r"local changes to the following files would be overwritten by merge", re.I
)

def _group_root_wf_done(group_id: str) -> bool:
    """True when the group's workflow root (R/B) reached final approval.

    0459 T0005 §2-2: the query itself moved to db.documents so the ai-invoke stale-card
    cleanup can ask the SAME question without importing this module's private helper.
    Kept as a name because this file calls it from three finalize paths."""
    return db_documents.group_root_wf_done(group_id)


def _groups_root_wf_done(group_ids: list[str]) -> set[str]:
    """Batch form of _group_root_wf_done (0282 NR0003 finding 1): one IN query
    instead of one probe per group. project_git_status ran the per-group probe
    inside its slot loop — 8 groups × 2 client calls = 12 of the 68 queries in
    the R0001 screen-load log, growing linearly with group count."""
    return db_documents.groups_root_wf_done(group_ids)


def _group_ac_doc_id(group_id: str) -> Optional[str]:
    """Newest AC (final-approval) doc id of the group, or None. Never raises —
    the field is advisory navigation state for the header [open] button
    (flowgate.default.0182 NR0003 §4)."""
    try:
        row = get_store()._fetch_one(
            "SELECT doc_id FROM documents "
            "WHERE group_id = ? AND type_code = 'AC' AND status != 'archived' "
            "ORDER BY doc_id DESC",
            [group_id],
        )
        return row["doc_id"] if row else None
    except Exception:
        _log.warning("ac_doc_id lookup failed for %s", group_id, exc_info=True)
        return None


def _group_ac_doc_ids(group_ids: list[str]) -> dict[str, str]:
    """Batch form of _group_ac_doc_id (0282 NR0003 finding 1). MAX(doc_id) per
    group ≡ the single version's ORDER BY doc_id DESC first row. Same advisory
    never-raise contract: on failure every pending row simply carries no
    ac_doc_id and the [open] button falls back to the R root."""
    if not group_ids:
        return {}
    try:
        placeholders = ", ".join("?" for _ in group_ids)
        rows = get_store()._fetch_all(
            "SELECT group_id, MAX(doc_id) AS doc_id FROM documents "
            f"WHERE group_id IN ({placeholders}) AND type_code = 'AC' "
            "AND status != 'archived' GROUP BY group_id",
            list(group_ids),
        )
        return {r["group_id"]: r["doc_id"] for r in rows if r.get("doc_id")}
    except Exception:
        _log.warning("ac_doc_id batch lookup failed", exc_info=True)
        return {}


def _group_has_changes(
    cfg: dict, state: dict, project_name: Optional[str]
) -> Optional[bool]:
    """Whether a group's work branch carries real, mergeable work.

    True  — commits ahead of the base branch, OR any uncommitted / untracked edit
            in the worktree (finalize's `add -A` absorb would turn these into a
            commit, so they count as work).
    False — nothing to merge/push. Either measured (branch at the base tip with a
            pristine worktree) or proven by ABSENCE: no branch was ever assigned,
            or a healthy base checkout cannot even name the branch AND no worktree
            directory exists — leaving nowhere a source change could be hiding.
    None  — divergence genuinely cannot be measured: git is off, the project's own
            base checkout is missing/broken, or the branch is uncountable while a
            worktree directory that could still be holding work is on disk. The
            caller must then keep the conservative awaiting_choice gate — never
            discard on doubt.

    flowgate.default.0548 T0004 §2 (revision 3 rejection): "Git으로 변경 유무를
    확인할 수 없음"을 "변경이 있음"으로 취급하지 말라. Absence used to fold into the
    blanket ``None``, which parked phantom slots — no branch at all, or a ledger
    row naming a branch whose ref and worktree are both already gone — in the
    finalize gate with nothing to finalize. Those two are now answers. Everything
    that is merely *unreadable* still answers ``None``.
    """
    from modules.flow_gate.services import git_service as _gs
    if not project_name or not _gs.git_available():
        return None
    branch = (state.get("branch") or "").strip()
    if not branch:
        # No branch was ever assigned: a source change has no branch to live on
        # and no worktree to live in. Proven empty, not unknown.
        return False
    wt_path = _gs.src_root(project_name, branch)
    base_branch = (cfg.get("base_branch") or "main").strip() or "main"
    base_root = _gs.src_root(project_name, base_branch)
    if not (base_root / ".git").exists():
        # The project's own checkout is missing: git cannot be consulted about
        # ANYTHING here, not even whether the branch exists. Still unknown.
        return None
    ahead = _ahead_of_base(base_root, base_branch, branch)
    if ahead is None:
        # A working base checkout that still cannot count this branch means the
        # ref is absent or unreadable. With no worktree directory either, the
        # group has nowhere left to hold work — that is "nothing", not "unknown"
        # (the shape flowgate.default.0548's reviewer actually hit: a ledger row
        # naming a branch whose ref and directory were both already gone).
        return None if wt_path.is_dir() else False
    if ahead > 0:
        return True
    # 0607 T0004 §3.3: merged into LOCAL base but never pushed is unfinished work,
    # not "nothing" — auto-discarding it here would tear the slot down unpushed.
    if _unpushed_local_merge_of(base_root, base_branch, branch) is not None:
        return True
    # ahead == 0: no committed work. Uncommitted/untracked worktree edits still
    # count (a merge/push would absorb them), so inspect the worktree too.
    if wt_path.is_dir():
        return bool(_gs._dirty(wt_path))
    return False


def _auto_discard_group(project_id: str, group_id: str) -> str:
    """Tear down a PROVEN no-work group's slot without any merge or push. Reuses
    `_cleanup_group_slot(force_discard=True)` (worktree remove --force → prune →
    branch -D → unregister); with ahead_count==0 the force-deleted local branch
    holds no unique commit, so nothing is lost, and origin is never touched.

    Best-effort: if the project git lock is busy the group is left `none` (its
    badge stays hidden — a no-work group is not "pending" — and the next status
    query retries the discard). Never raises. Returns the label the caller should
    treat the group as having: DISCARDED_STATUS on success, "none" when the lock
    was busy (the DB status stays "none" either way — see the constant)."""
    from modules.flow_gate.services import git_service as _gs
    holder = f"discard:{uuid.uuid4()}"
    # wait_sec=0: never block a status GET / an approval on a busy lock — retry
    # opportunistically on the next transition query instead.
    if not _gs._acquire_lock(project_id, holder, wait_sec=0):
        return "none"
    try:
        cleaned = _gs._cleanup_group_slot(project_id, group_id, force_discard=True)
    finally:
        _gs.db_git.release_lock(project_id, holder)
    if not cleaned:
        # Cleanup could not complete (e.g. worktree remove blocked) — leave the
        # slot registered so a later query retries rather than orphaning it.
        return "none"
    # Slot unregistered; clear any pending badge and nudge the explorer to re-fetch
    # the group dropdown (mirrors cleanup_disposed_group's post-cleanup emit).
    _gs._emit_pending_changed(project_id, group_id, "none")
    return DISCARDED_STATUS


def _decide_pending_transition(
    project_id: str, cfg: dict, state: dict, group_id: str
) -> str:
    """Resolve a wf_done group out of `none` into its real status.

    A group with actual work (or whose divergence cannot be proven empty) enters
    `awaiting_choice` — the finalize gate, exactly as before. A PROVEN no-work
    group is auto-discarded instead (no merge, no push). Returns the status the
    caller should treat the group as having: "awaiting_choice", "discarded", or
    "none" (no-work but the discard lock was busy — retry later). Never raises the
    git error out to the caller."""
    from modules.flow_gate.services import git_service as _gs
    project_name = _gs._project_name(project_id)
    if _group_has_changes(cfg, state, project_name) is False:
        return _gs._auto_discard_group(project_id, group_id)
    # Had changes, or divergence unmeasurable → preserve the original safe gate.
    _gs._set_status(group_id, "awaiting_choice")
    return "awaiting_choice"


def realize_wf_done_transition(group_id: str) -> None:
    """Eagerly realize the lazy none→awaiting_choice transition at final-approval
    time (0177 NR0016 §3). The lazy design (L0006 §3) only realizes on the NEXT
    status query, so a plain AC approval emitted no git_pending_changed and the
    header badge stayed stale until a reload. Called from the approval paths
    right after the workflow root flips to wf_done; never raises — a git hiccup
    must not disturb the approval that already stood."""
    from modules.flow_gate.services import git_service as _gs
    try:
        project_id = _gs._project_of_group(group_id)
        cfg = _gs.db_git.get_config(project_id)
        state = _gs.db_git.get_state(group_id)
        if (
            cfg is None or not cfg.get("enabled")
            or state is None or not state.get("worktree_registered")
        ):
            return
        if (state.get("status") or "none") == "none" and _gs._group_root_wf_done(group_id):
            # 0199 B0001: a no-work group is discarded (no merge/push) rather than
            # parked in awaiting_choice; a real group still gets the finalize gate.
            _gs._decide_pending_transition(project_id, cfg, state, group_id)
    except Exception:
        _log.warning(
            "wf_done git transition realization failed for %s", group_id, exc_info=True
        )


def _resolve_pending_noop(
    project_id: str, cfg: dict, state: dict, group_id: str, status: str
) -> str:
    """Converge an ALREADY-pending slot that turns out to have nothing to merge.

    flowgate.default.0548 T0004 §2/§3 — `_decide_pending_transition` only guards the
    none→awaiting_choice *transition*, so a slot that entered the gate before that
    guard existed (or entered it while divergence was briefly unmeasurable) keeps
    showing a finalize gate forever with nothing to finalize: the reviewer's
    "머지할게 없는데 … 문서에 머지 섹션이 그대로 뜬다". The emptiness proof and the
    teardown are exactly the ones 0199 B0001 already blessed for the transition —
    this only widens *when* they are applied, from "on entry" to "whenever the gate
    would be shown".

    Only `awaiting_choice` is eligible — see NOOP_CONVERGEABLE_STATUSES for why
    `waiting`, `conflict` and the terminal statuses are not.

    Returns the status the caller should treat the slot as having: the original
    `status` when it has (or might have) work, DISCARDED_STATUS after teardown, or
    "none" when the emptiness was proven but the discard lock was busy (the panel
    hides either way; the next query retries the teardown).
    """
    from modules.flow_gate.services import git_service as _gs
    if status not in NOOP_CONVERGEABLE_STATUSES:
        return status
    if _gs._group_has_changes(cfg, state, _gs._project_name(project_id)) is not False:
        return status
    return _gs._auto_discard_group(project_id, group_id)


def group_finalize_is_noop(group_id: str) -> bool:
    """True when this group provably has NOTHING to merge or push.

    flowgate.default.0548 T0004 §3/§4 — one server-side answer to "머지할게 없다"
    that every finalize surface reads, so the approval toast, the Git panel
    auto-open, the AC approval dialog's choice block and the document's Git card
    can never disagree with each other (T0004 §8 forbids the client re-deriving it).

    Conservative by construction: anything that might still carry work — a live
    conflict/merge session, a terminal slot that really did merge, an unmeasurable
    divergence with a worktree still on disk, or an unexpected failure — answers
    False, which preserves the existing actionable warning.

    A project with git off (or no project at all) also answers False, keeping the
    0162 D §3.1 contract that such a ride-along reports ``{ok: false}`` intact: a
    git_action cannot reach a git-inactive group in the first place
    (``precheck_approve_git_action`` refuses it with 422 before the approval runs),
    so quieting it here would only weaken a guard nothing legitimate depends on.
    """
    from modules.flow_gate.services import git_service as _gs
    try:
        project_id = _gs._project_of_group(group_id)
        if not project_id:
            return False
        cfg = _gs.db_git.get_config(project_id)
        if cfg is None or not cfg.get("enabled"):
            return False
        state = _gs.db_git.get_state(group_id)
        if state is None:
            return True
        status = (state.get("status") or "none")
        if status in ("conflict", "merging", "merged", "pushed"):
            # A real git operation is live or already ran — never quiet.
            return False
        # `worktree_registered=0` is NOT proof the slot is empty (this document's
        # revision 5 rejection): db_git.unregister_worktree() only flips the flag —
        # it never clears `branch` — so a slot torn down by the no-work auto-discard
        # race (or by cleanup running ahead of a stale caller) can still name a
        # branch whose on-disk worktree carries real, undeclared edits. Probe
        # `_group_has_changes` exactly as the registered path does instead of
        # trusting the flag alone; only a proven-empty (or never-assigned) branch
        # answers quiet, and an unmeasurable divergence still keeps the warning.
        return _gs._group_has_changes(cfg, state, _gs._project_name(project_id)) is False
    except Exception:
        _log.warning("finalize no-op probe failed for %s", group_id, exc_info=True)
        return False


def approval_git_in_flight(group_id: str) -> Optional[bool]:
    """Whether a final approval of THIS group is still running its Git right now.

    flowgate.default.0607 T0004 §3.6 (rev1): the approval orchestrator holds the
    project Git lock as ``approval:<AC doc id>:<uuid>`` from before Git starts until
    after the approval transaction has committed (slot cleanup runs after the
    release), and AC doc ids start with their group id. A browser whose approve
    request timed out reads this to tell "the server is still working" from "the
    server finished/failed" before it lets anyone press approve again. Reads the
    lock row only; never waits on it.

    Returns ``None`` — never ``False`` — when the lock row itself could not be
    read: a failed probe is not evidence that no approval is running, and the
    caller must keep the approve button locked until a probe actually succeeds
    and reports ``False``.
    """
    from modules.flow_gate.services import git_service as _gs
    try:
        lock = _gs.db_git.get_lock(_gs._project_of_group(group_id))
    except Exception:
        _log.warning("approval lock probe failed for %s", group_id, exc_info=True)
        return None
    holder = str((lock or {}).get("holder") or "")
    return holder.startswith(f"approval:{group_id}.")


def get_finalize_state(group_id: str, *, preview_ac: bool = False) -> dict:
    out = _finalize_state(group_id, preview_ac=preview_ac)
    out["state"]["approval_in_flight"] = approval_git_in_flight(group_id)
    return out


def _finalize_state(group_id: str, *, preview_ac: bool = False) -> dict:
    from modules.flow_gate.services import git_service as _gs
    project_id = _gs._project_of_group(group_id)
    cfg = _gs.db_git.get_config(project_id)
    state = _gs.db_git.get_state(group_id)
    if cfg is None or not cfg.get("enabled") or state is None:
        return {"ok": True, "state": {
            "group_id": group_id, **_NONE_STATE, "base_remote_behind_count": None,
        }}

    persisted_status = state.get("status") or "none"
    stored_clean_retry = approval_intent.clean_retry_of_state(state)
    retry_status = (stored_clean_retry or {}).get("terminal_status")
    retry_matches_state = (
        retry_status in {"merged", "pushed"} and retry_status == persisted_status
    ) or (
        retry_status in {"discarded", "archived", "stashed"}
        and persisted_status == "none"
    )
    clean_retry = stored_clean_retry if retry_matches_state else None
    status = retry_status if clean_retry is not None else persisted_status
    if (
        not state.get("worktree_registered")
        and persisted_status not in _gs.CLEANUP_STATUSES
        and clean_retry is None
    ):
        return {"ok": True, "state": {
            "group_id": group_id, **_NONE_STATE, "base_remote_behind_count": None,
        }}
    # Lazy none→awaiting_choice transition (L0006 §3): the workflow module never
    # calls into git; the first state query after wf_done realizes the transition.
    # 0199 B0001: a proven no-work group is auto-discarded here instead of being
    # gated — it has nothing to finalize, so report it as the empty NONE state.
    if status == "none" and _gs._group_root_wf_done(group_id):
        status = _gs._decide_pending_transition(project_id, cfg, state, group_id)
        if status != "awaiting_choice":
            # discarded (torn down, no merge/push) or none (discard lock busy —
            # retry next query): either way there is no finalize gate to show.
            return {"ok": True, "state": {
                "group_id": group_id, **_NONE_STATE, "base_remote_behind_count": None,
            }}
    # 0548 T0004 §3 — an ALREADY-pending slot with nothing to merge must lose the
    # gate too, not just a slot entering it now ("문서에 머지 섹션이 그대로 뜬다").
    elif _gs._resolve_pending_noop(project_id, cfg, state, group_id, status) != status:
        return {"ok": True, "state": {
            "group_id": group_id, **_NONE_STATE, "base_remote_behind_count": None,
        }}

    base_branch = (cfg.get("base_branch") or "main").strip() or "main"
    branch = state.get("branch")
    ahead = behind = None
    project_name = _gs._project_name(project_id)
    base_remote_behind = None
    if project_name and _gs.git_available():
        base_root = _gs.src_root(project_name, base_branch)
        _base_remote_ahead, base_remote_behind = _gs._base_ahead_behind(base_root, base_branch)
        if (base_root / ".git").exists():
            proc = _gs._run_git(
                ["rev-list", "--left-right", "--count", f"{base_branch}...{branch}"],
                cwd=base_root,
            )
            if proc.returncode == 0:
                m = re.match(r"^\s*(\d+)\s+(\d+)\s*$", proc.stdout or "")
                if m:
                    behind, ahead = int(m.group(1)), int(m.group(2))

    # Display status for response: may differ from persisted status for preview_ac.
    # At this point, if status is 'none', the root must be wf_in_progress
    # (else lines 2336-2343 would have transitioned it when root is wf_done).
    # When preview_ac=True and status=='none', show a preliminary awaiting_choice
    # (0197 T0004 §B) for the AC approval dialog.
    display_status = status
    if status == "none" and preview_ac:
        # 0548 T0004 §3 — but only when the group actually has something to
        # finalize. Faking the gate unconditionally is what put a merge/push
        # choice block into the AC approval dialog of a group with no work at all
        # ("머지할게 없는데 … 머지 다이얼로그가 그대로 뜬다"), and then rode that stale
        # choice into an approval whose finalize could only fail: by the time it
        # ran, the approval's own no-work auto-discard had already unregistered
        # the slot, so the operator got "Git integration is not active" as a
        # warning toast plus an auto-opened Git panel.
        if _gs._group_has_changes(cfg, state, project_name) is not False:
            display_status = "awaiting_choice"

    # Suggested commit message (flowgate.default.0173 P0003 §2): only meaningful
    # while the group awaits a commit-producing choice; null otherwise.
    if display_status in ("awaiting_choice", "waiting"):
        subject, source = _gs.resolve_commit_message(group_id)
        commit_message: Optional[dict] = {"suggested": subject, "source": source}
    else:
        commit_message = None

    actionable = display_status in ("awaiting_choice", "waiting")
    open_session = _gs.db_git.get_open_session_by_group(group_id)
    group_update_merge_id = (
        open_session.get("merge_id")
        if open_session is not None
        and _gs.db_git.session_kind(open_session) == _gs.db_git.SESSION_KIND_GROUP_UPDATE
        else None
    )
    # 0481 D0006 §6.4 / L0007 §2.11: the finalize panel's own "승인 대기" entry
    # badge needs to tell a still-resolving conflict (review_state is None/absent)
    # apart from one already sitting in resolved_pending_review/re_review/
    # applying/reconciling — this endpoint is the only state poll GitFinalizePanel
    # makes, so the review gate's phase has to ride along with it rather than
    # forcing a second round-trip to GET .../review just to render a badge.
    review_state = None
    reconciliation_kind = None
    if display_status == "conflict" and state.get("merge_id") is not None:
        try:
            merge_session = _gs.db_git.get_session(int(state["merge_id"]))
        except Exception:
            merge_session = None
        if merge_session is not None and _gs.db_git.session_kind(merge_session) == _gs.db_git.SESSION_KIND_MERGE:
            merge_context = _gs.db_git.session_context(merge_session)
            review_state = merge_context.get("review_state")
            reconciliation_kind = merge_context.get("reconciliation_kind")
    # 0594 T0012: the ledger's merge_id now also points at a COMPLETED attempt (a
    # merged group keeps its attempt so the real target survives a restart). The
    # response keeps its old meaning — a merge_id is only published while it names
    # an actionable conflict session — and the target rides along separately.
    ledger_merge_id = state.get("merge_id") if status == "conflict" else None
    finalize_target = None
    try:
        if status == "conflict":
            pinned = merge_target.fixed_target_of_group(group_id)
            finalize_target = pinned.public() if pinned is not None else None
        elif status == "merged":
            done = merge_target.completed_target_of_state(state)
            finalize_target = done.public() if done is not None else None
    except Exception:
        _log.warning("finalize target lookup failed for %s", group_id, exc_info=True)
    return {"ok": True, "state": {
        "group_id": group_id,
        "branch": branch,
        "base_branch": base_branch,
        "status": display_status,
        "default_action": cfg.get("default_finalize_action") or "wait",
        "choices": list(FINALIZE_MAIN_CHOICES if actionable else ()),
        "aux_choices": list(FINALIZE_AUX_CHOICES if actionable else ()),
        # Additive (NR 0331.0005 §8): the axis client renders from this and
        # ignores choices/aux_choices; a client that does not know the key falls
        # back to the legacy card list untouched above.
        "action_axes": {
            "scopes": list(FINALIZE_AXIS_SCOPES),
            "matrix": {k: dict(v) for k, v in FINALIZE_AXIS_MATRIX.items()},
            "commit_actions": list(FINALIZE_COMMIT_ACTIONS),
        } if actionable else None,
        "ahead_count": ahead,
        "behind_count": behind,
        # Local-ref-only measurement: state polling never fetches the network.
        "base_remote_behind_count": base_remote_behind,
        "merge_id": group_update_merge_id or ledger_merge_id,
        "merge_commit": (clean_retry or {}).get("merge_commit") or state.get("merge_commit"),
        "review_state": review_state,
        "reconciliation_kind": reconciliation_kind,
        # 0555 T0008 §10 / D0005 §3.11: the coupling marker. True means this
        # group's Git is finishing a final approval that is still waiting, so no
        # surface may offer a NEW finalize — only "go resolve the conflict". The
        # same fact the finalize() guard above rejects on, so the screen and the
        # server never disagree about it.
        "final_approval_bound": approval_intent.group_is_final_approval_bound(group_id),
        # A11/B8: terminal Git with an unconsumed intent is approval-only retry.
        "approval_pending": (
            display_status in approval_intent.CLEAN_TERMINAL_STATUSES
            and (clean_retry is not None or approval_intent.find_intent_session(group_id)[1] is not None)
        ),
        # Additive (0594 T0012): the attempt's pinned target (conflict) or the
        # completed attempt's actual target (merged); null for legacy/no attempt.
        "finalize_target": finalize_target,
        "commit_message": commit_message,
        # True only for the display-only pre-approval preview (0197 T0004 §B);
        # the persisted status is still 'none'. Advisory for the FE.
        "preview": preview_ac,
    }}


def _finalize_context(group_id: str) -> tuple[dict, dict, str, Path, Path]:
    """(cfg, state, project_id, base_root, wt_path) with the entry guards applied."""
    from modules.flow_gate.services import git_service as _gs
    project_id = _gs._project_of_group(group_id)
    cfg = _gs.db_git.get_config(project_id)
    state = _gs.db_git.get_state(group_id)
    if cfg is None or not cfg.get("enabled") or state is None or not state.get("worktree_registered"):
        raise GitServiceError(
            409, "invalid_state", f"Git integration is not active for group '{group_id}'"
        )
    project_name = _gs._project_name(project_id)
    if not project_name:
        raise GitServiceError(404, "not_found", f"project '{project_id}' not found")
    base_branch = (cfg.get("base_branch") or "main").strip() or "main"
    base_root = _gs.src_root(project_name, base_branch)
    wt_path = _gs.src_root(project_name, state["branch"])
    return cfg, state, project_id, base_root, wt_path


def update_from_base(group_id: str) -> dict:
    """Explicit-only strict refresh: fetch, fast-forward base, then merge base into group."""
    from modules.flow_gate.services import git_service as _gs
    cfg, state, project_id, base_root, wt_path = _gs._finalize_context(group_id)
    if _gs.db_git.get_open_session_by_group(group_id) is not None:
        raise GitServiceError(409, "invalid_state", "resolve or abort the current group update first")
    _gs.guard_base_free(project_id)
    if not _gs.git_available():
        raise GitServiceError(500, "git_unavailable", "git binary not found on server")
    holder = f"op:{uuid.uuid4()}"
    if not _gs._acquire_lock(project_id, holder):
        raise GitServiceError(409, "git_busy", "another git operation is in progress")
    try:
        _gs.guard_base_free(project_id)
        if _gs._dirty(base_root, include_untracked=False):
            raise GitServiceError(
                409, "base_dirty", "base checkout has local modifications",
                details={"files": _dirty_files(base_root, include_untracked=False)},
            )
        base_branch = (cfg.get("base_branch") or "main").strip() or "main"
        username = cfg.get("username")
        secret = _gs._load_secret_for(cfg) or ""
        # flowgate.default.0361 NR0003 §5.3/§8.1: the operator-facing "update from
        # base" action is exactly the surface that must reflect a repo_url change.
        _gs.ensure_origin_matches_config(base_root, (cfg.get("repo_url") or "").strip())
        proc = _gs._run_git(
            ["fetch", "origin"], cwd=base_root, timeout=_gs.GIT_NET_TIMEOUT_SEC,
            username=username, secret=secret,
        )
        if proc.returncode != 0:
            raise GitServiceError(500, "git_error", "Git command failed", diagnostic=_gs._last_line(proc.stderr))
        if _gs._ref_exists(base_root, f"refs/remotes/origin/{base_branch}"):
            proc = _gs._run_git(["merge", "--ff-only", f"origin/{base_branch}"], cwd=base_root)
            if proc.returncode != 0:
                ahead, _behind = _gs._base_ahead_behind(base_root, base_branch)
                if ahead is not None and ahead > 0:
                    raise GitServiceError(
                        500, "base_diverged",
                        "base checkout has local-only commits and cannot fast-forward",
                    )
                raise GitServiceError(500, "git_error", "Git command failed", diagnostic=_gs._last_line(proc.stderr))

        _absorb_worker_edits(
            wt_path, f"chore: preserve {group_id} work before base update",
            _author_env_from_cfg(cfg),
        )
        before = _gs._run_git(["rev-parse", "HEAD"], cwd=wt_path)
        proc = _gs._run_git(
            [*_gs._GIT_IDENT,
             "merge", "--no-ff", base_branch, "-m",
             f"Merge base '{base_branch}' into '{state['branch']}'"],
            cwd=wt_path, author_env=_author_env_from_cfg(cfg),
        )
        if proc.returncode != 0:
            untracked_blockers = _untracked_merge_blockers(proc.stderr)
            tracked_blockers = _tracked_merge_blockers(proc.stderr)
            if untracked_blockers is not None or tracked_blockers is not None:
                _gs._run_git(["merge", "--abort"], cwd=wt_path)
                blockers = (untracked_blockers or []) + (tracked_blockers or [])
                raise GitServiceError(
                    409, "group_untracked_conflict",
                    "group update is blocked by local worktree files",
                    details={
                        "group_id": group_id, "files": blockers, "scope": "group",
                        "untracked_files": untracked_blockers or [],
                        "tracked_files": tracked_blockers or [],
                    },
                )
            # NR0025 §8: the old undefined probe name is gone; refs._unmerged_paths is the same probe.
            conflicts = _gs._unmerged_paths(wt_path)
            if conflicts:
                merge_id = _gs.db_git.create_session(
                    group_id, conflicts, kind=_gs.db_git.SESSION_KIND_GROUP_UPDATE,
                    context={"prev_status": state.get("status") or "none",
                             "branch": state.get("branch")},
                )
                # 0608 T0005: line-ending-only conflicts need no resolver.
                _gs.apply_eol_separation(merge_id, wt_path)
                return {"ok": True, "result": {
                    "status": "conflict", "merge_id": merge_id,
                    "conflict_files": conflicts,
                }}
            _gs._run_git(["merge", "--abort"], cwd=wt_path)
            raise GitServiceError(500, "git_error", "Git command failed", diagnostic=_gs._last_line(proc.stderr))
        after = _gs._run_git(["rev-parse", "HEAD"], cwd=wt_path)
        changed = (before.stdout or "").strip() != (after.stdout or "").strip()
        return {"ok": True, "result": {
            "status": "updated" if changed else "no_change",
            "branch": state.get("branch"),
        }}
    finally:
        _gs.db_git.release_lock(project_id, holder)


def group_update_untracked_recover(
    group_id: str, files: list[str], action: str, message: Optional[str] = None
) -> dict:
    """Recover only paths that currently block base->group update in the group worktree."""
    from modules.flow_gate.services import git_service as _gs
    cfg, _state, project_id, _base_root, wt_path = _gs._finalize_context(group_id)
    cleaned: list[str] = []
    for raw in files or []:
        path = str(raw or "").strip().replace("\\", "/")
        if not path or path.startswith("/") or re.match(r"^[A-Za-z]:", path) or ".." in path.split("/"):
            raise GitServiceError(422, "invalid_request", f"invalid path: {raw!r}")
        if path not in cleaned:
            cleaned.append(path)
    if not cleaned:
        raise GitServiceError(422, "invalid_request", "files must name at least one path")
    if action not in {"commit", "revert", "remove"}:
        raise GitServiceError(422, "invalid_request", "invalid recovery action")
    if not _gs.git_available():
        raise GitServiceError(500, "git_unavailable", "git binary not found on server")
    holder = f"op:{uuid.uuid4()}"
    if not _gs._acquire_lock(project_id, holder):
        raise GitServiceError(409, "git_busy", "another git operation is in progress")
    try:
        if _gs.db_git.get_open_session_by_group(group_id) is not None:
            raise GitServiceError(409, "invalid_state", "resolve or abort the current group update first")
        base_branch = (cfg.get("base_branch") or "main").strip() or "main"
        probe = _gs._run_git(["merge", "--no-commit", "--no-ff", base_branch], cwd=wt_path)
        untracked_blockers = _untracked_merge_blockers(probe.stderr) or []
        tracked_blockers = _tracked_merge_blockers(probe.stderr) or []
        blockers = tracked_blockers if action == "revert" else untracked_blockers
        # The probe is classification-only. Whether it stopped on a blocker,
        # entered conflicts, or produced a clean no-commit merge, restore HEAD
        # before applying the explicitly requested recovery.
        _gs._run_git(["merge", "--abort"], cwd=wt_path)
        unknown = [path for path in cleaned if path not in blockers]
        if unknown:
            raise GitServiceError(
                422, "invalid_request", "paths are not current group-update blockers",
                details={"files": unknown, "allowed": blockers, "scope": "group"},
            )
        if action == "commit":
            proc = _gs._run_git(["add", "--", *cleaned], cwd=wt_path)
            if proc.returncode == 0:
                subject = _gs.normalize_subject(message) or default_base_commit_message(cleaned)
                proc = _gs._run_git(
                    [*_gs._GIT_IDENT, "commit", "-m", subject], cwd=wt_path,
                    author_env=_author_env_from_cfg(cfg),
                )
        elif action == "revert":
            proc = _gs._run_git(["checkout", "HEAD", "--", *cleaned], cwd=wt_path)
        else:
            current = set(_untracked_files(wt_path, limit=0))
            if any(path not in current for path in cleaned):
                raise GitServiceError(422, "invalid_request", "remove accepts untracked files only")
            proc = _gs._run_git(["clean", "-f", "-q", "--", *cleaned], cwd=wt_path)
        if proc.returncode != 0:
            raise GitServiceError(500, "git_error", "Git command failed", diagnostic=_gs._last_line(proc.stderr))
        return {"ok": True, "result": {
            "action": action, "files": cleaned, "scope": "group",
            "remaining_untracked": _untracked_files(wt_path),
        }}
    finally:
        _gs.db_git.release_lock(project_id, holder)


def _validate_approval_context(
    context: ApprovalFinalizeContext,
    group_id: str,
    project_id: str,
) -> dict:
    """Revalidate the document capability and borrowed mutex under the lock."""
    from modules.flow_gate.services import git_service as _gs

    doc = db_documents.get_by_id(context.doc_id)
    root = db_documents.get_by_id((doc or {}).get("target_id") or "")
    lock = _gs.db_git.get_lock(project_id)
    if (
        context.group_id != group_id
        or doc is None
        or doc.get("group_id") != group_id
        or str(doc.get("type_code") or "").upper() != "AC"
        or doc.get("doc_review_status") != "pending_review"
        or root is None
        or root.get("group_id") != group_id
        or str(root.get("type_code") or "").upper() not in {"R", "B"}
        or root.get("doc_review_status") != "wf_in_progress"
        or lock is None
        or lock.get("holder") != context.lock_holder
    ):
        raise GitServiceError(
            409, "invalid_state", "approval finalize context is no longer valid"
        )
    return doc


def _terminal_retry_result(state: dict, action: str) -> dict:
    status = state.get("status") or "none"
    return {
        "ok": True,
        "result": {
            "action": action,
            "status": status,
            "merge_commit": state.get("merge_commit"),
            "pushed": status == "pushed" or (status == "merged" and action == "merge"),
            "merge_id": None,
            "conflict_files": [],
            "terminal_retry": True,
        },
    }


def finalize(
    group_id: str,
    action: Optional[str],
    commit_message: Optional[str] = None,
    *,
    approval_context: Optional[ApprovalFinalizeContext] = None,
    target_branch: Optional[str] = None,
) -> dict:
    """Group finalize. ``target_branch`` (0594 T0012) names the local branch a merge
    lands on; omitted → the project base (unchanged legacy behavior) or, while an
    attempt is open, that attempt's pinned target. ``approval_context`` (0555) is the
    final approval's capability: it borrows the orchestrator's project lock."""
    from modules.flow_gate.services import git_service as _gs
    cfg, state, project_id, base_root, wt_path = _gs._finalize_context(group_id)
    borrowed_lock = approval_context is not None
    if approval_context is not None:
        _validate_approval_context(approval_context, group_id, project_id)
    open_session = _gs.db_git.get_open_session_by_group(group_id)
    if (open_session is not None
            and _gs.db_git.session_kind(open_session) == _gs.db_git.SESSION_KIND_GROUP_UPDATE):
        raise GitServiceError(409, "invalid_state", "resolve or abort the group update first")
    action = action or cfg.get("default_finalize_action") or "wait"
    if action not in ACTION_VALUES:
        raise GitServiceError(422, "invalid_request", f"invalid action: {action!r}")
    # 0555 T0008 §10 / D0005 §3.11: Git work already coupled to a waiting final
    # approval is not something a second surface may restart. The screen hides the
    # control; this is the same fact enforced on the request, so hiding it is not
    # the only thing standing between a stale tab and a duplicate finalize. The
    # approval's own continuation carries the capability and is exempt.
    if approval_context is None and approval_intent.group_is_final_approval_bound(group_id):
        raise GitServiceError(
            409, "final_approval_bound",
            "this group's git finalize belongs to a final approval that is waiting "
            "for the conflict review to finish",
        )
    requested_target = merge_target.normalize_requested_target(target_branch)
    # Retarget contract (T0012 §11): checked before every state guard so an open
    # conflict/review attempt reports its pinned target instead of a bare 409.
    pinned_target = merge_target.raise_if_retarget(group_id, requested_target)
    if (
        requested_target is not None
        and action not in merge_target.MERGE_ACTIONS
        and requested_target != merge_target.project_base_branch(cfg)
    ):
        raise GitServiceError(
            422, "merge_target_requires_merge",
            "a finalize target other than the base branch applies to merge actions only",
            details={"target_branch": requested_target, "action": action},
        )

    # Confirmed commit subject (flowgate.default.0173 P0003 §3): normalize+validate
    # BEFORE any state transition or lock acquisition (422 has no side effects). A
    # blank/omitted value means the unmanned path — resolve it just before use.
    provided_subject = _gs.normalize_subject(commit_message)
    if len(provided_subject) > COMMIT_SUBJECT_MAX:
        raise GitServiceError(
            422, "invalid_request",
            "commit_message must be a single line of at most 200 characters.",
        )

    # Public finalize remains admitted only after wf_done.  The internal approval
    # capability is the sole exception: it permits the pending AC to run Git while
    # borrowing the mutex already owned by the orchestrator.
    status = (state.get("status") or "none")
    if approval_context is not None and action not in APPROVAL_FINALIZE_ACTIONS:
        raise GitServiceError(
            422, "invalid_request",
            "final approval requires a terminal Git action",
        )
    root_wf_done = (
        _gs._group_root_wf_done(group_id)
        if approval_context is None and status in ("none", "awaiting_choice", "waiting")
        else False
    )
    if approval_context is None and status == "none" and root_wf_done:
        _gs._set_status(group_id, "awaiting_choice")
        status = "awaiting_choice"
    if approval_context is not None and approval_intent.clean_retry_of_state(state) is not None:
        raise GitServiceError(
            409, "approval_retry_pending",
            "terminal Git already belongs to an approval-only retry; resend without git_action",
        )
    if status in ("merged", "pushed"):
        if approval_context is not None:
            return _terminal_retry_result(state, action)
        raise GitServiceError(409, "invalid_state", "already finalized")
    if status == "conflict":
        raise GitServiceError(
            409, "invalid_state", "resolve or abort the merge first",
            details=pinned_target.public() if pinned_target is not None else None,
        )
    if status == "merging":
        raise GitServiceError(
            409, "git_busy",
            f"Another git operation is in progress for project '{project_id}' (try again shortly)",
        )
    allowed_statuses = (
        ("none", "awaiting_choice", "waiting")
        if approval_context is not None
        else ("awaiting_choice", "waiting")
    )
    if status not in allowed_statuses:
        raise GitServiceError(409, "invalid_state", f"finalize not available in state '{status}'")
    if approval_context is None and not root_wf_done:
        raise GitServiceError(
            409,
            "invalid_state",
            "final workflow approval is required before Git finalize",
        )

    if action == "wait":
        _gs._set_status(group_id, "waiting")
        return _finalize_result(group_id, project_id, "wait", "waiting")

    # 0594 T0012 §9.2 (1st check): resolve + validate the target and its workspace
    # ownership before waiting on the lock — the authoritative check repeats below.
    target: Optional[merge_target.MergeTargetContext] = None
    if action in merge_target.MERGE_ACTIONS:
        target = merge_target.plan_finalize_target(group_id, project_id, cfg, requested_target)

    # 0205 L §2.2: a merge mutates the shared base checkout — refuse while another
    # group's unresolved conflict session holds it. Checked BEFORE the lock wait
    # (cheap reject) and again after (race close, below). push never touches base.
    # 0594 T0012: a non-base target merges in its own managed workspace instead.
    if target is not None and target.is_project_base:
        _gs.guard_base_free(project_id)

    if not _gs.git_available():
        raise GitServiceError(
            500, "git_unavailable",
            "git binary not found on server (install git in the runtime image)",
        )
    # The lock holder carries the attempt owner id, so "is this open attempt's runner
    # still alive?" is answerable from the lock table alone (T0012 §7). A final
    # approval borrows its orchestrator's lock instead; the attempt then records that
    # holder, which stays live for exactly as long as the approval runs.
    attempt_owner = uuid.uuid4().hex
    holder = (
        approval_context.lock_holder
        if approval_context is not None
        else f"op:{attempt_owner}"
    )
    if not borrowed_lock and not _gs._acquire_lock(project_id, holder):
        raise GitServiceError(
            409, "git_busy",
            f"Another git operation is in progress for project '{project_id}' (try again shortly)",
        )
    try:
        # flowgate.default.0361 NR0003 §5.3/§8.1: every fetch/push this attempt may
        # issue below (unpushed-merge recovery fetch/push, the pinned-attempt merge
        # fetch, the work-branch push) shares this one repository's `origin` — sync
        # it once, up front, before any of them run.
        _gs.ensure_origin_matches_config(base_root, (cfg.get("repo_url") or "").strip())
        if action in merge_target.MERGE_ACTIONS:
            # 0594 T0012 §4/§9.2 (2nd check — the real guard): re-resolve the target,
            # re-validate the branch and re-read workspace ownership now that the
            # project lock is ours; a stale UI candidate or a racing attempt stops here.
            target = merge_target.plan_finalize_target(
                group_id, project_id, cfg, requested_target,
            )
            if target.is_project_base:
                # 2nd gate: a conflict session may have opened while we waited on the
                # lock (conflict no longer holds it — 0205 §2.1), so re-check now.
                _gs.guard_base_free(project_id)
        branch = state["branch"]
        base_branch = (cfg.get("base_branch") or "main").strip() or "main"
        username = cfg.get("username")
        secret = _gs._load_secret_for(cfg) or ""
        author_env = _author_env_from_cfg(cfg)   # 0237 — configured commit author
        resolved_subject: Optional[str] = None
        # 0382 proposal 1: paths the absorb commit refused to swallow. Reported on every
        # exit path below — a silently-dropped list is what let 261 files through.
        excluded_artifacts: list[str] = []
        staged_new_file_count = 0

        def finalize_subject() -> str:
            nonlocal resolved_subject
            if resolved_subject is None:
                resolved_subject = provided_subject or _gs.resolve_commit_message(group_id)[0]
            return resolved_subject

        def record_clean_approval_retry(status: str, merge_commit: Optional[str]) -> None:
            if approval_context is None or not approval_context.approval_intent_id:
                return
            intent = approval_intent.build_intent(
                approval_intent_id=approval_context.approval_intent_id,
                group_id=group_id,
                ac_doc_id=approval_context.doc_id,
                requested_by=approval_context.actor_user_id,
                git_action=action,
            )
            approval_intent.record_clean_retry(
                group_id=group_id,
                intent=intent,
                terminal_status=status,
                merge_commit=merge_commit,
            )

        def finish_merged(
            merge_commit: Optional[str], wants_push: bool,
            attempt: Optional["merge_target.MergeTargetContext"] = None,
        ) -> dict:
            # The one terminal tail for a merge that is really in its target (and,
            # for `merge`, really on origin): a merge this request made, one it only
            # proved after a lost command result, or one an earlier request left
            # unpushed (0607 T0004 §3.2/§3.3). Cleanup stays here, after the push.
            # 0594 T0012 §6.3: a pinned attempt stays as history and the ledger
            # points at it, so the real target survives a restart. Ledger FIRST: a
            # crash between the two leaves an OPEN attempt that recovery reconciles
            # from its merge inputs — never a closed attempt with the group stuck.
            ledger_merge_id = attempt.merge_id if attempt is not None else None
            target_branch = attempt.target_branch if attempt is not None else base_branch
            if approval_context is not None:
                record_clean_approval_retry("merged", merge_commit)
                if ledger_merge_id is not None:
                    # The retry snapshot write above leaves merge_id empty; point
                    # the ledger at the attempt without touching that snapshot.
                    _gs.db_git.set_status(
                        group_id, "merged", merge_id=ledger_merge_id, merge_commit=merge_commit,
                    )
            else:
                _gs._set_status(
                    group_id, "merged", merge_id=ledger_merge_id, merge_commit=merge_commit,
                )
            if attempt is not None:
                merge_target.complete_attempt(attempt, merge_commit=merge_commit, pushed=wants_push)
            # Approval owns cleanup/notification after its atomic DB commit.
            if approval_context is None:
                # 0182 NR0003 §5: merged content lives in the target — remove the
                # group's worktree, work branch and ledger registration best-effort.
                _gs._cleanup_group_slot(project_id, group_id)
                _gs._emit("git_finalize_done", project_id, group_id, {
                    "project": project_id, "group_id": group_id,
                    "action": action, "status": "merged", "merge_commit": merge_commit,
                    "pushed": wants_push, "target_branch": target_branch,
                    **_artifact_payload(excluded_artifacts, staged_new_file_count),
                })
            return {
                "ok": True,
                "result": {
                    "action": action, "status": "merged", "merge_commit": merge_commit,
                    "pushed": wants_push, "merge_id": None, "conflict_files": [],
                    "target_branch": target_branch,
                    **_artifact_payload(excluded_artifacts, staged_new_file_count),
                },
            }

        if not wt_path.is_dir():
            raise GitServiceError(409, "invalid_state", "group worktree directory is missing")

        # T4 (flowgate.default.0351 §6): freeze this group's migrated conversations
        # into their markdown files now, before any commit/absorb below, so the file
        # that lands in the git snapshot matches the DB record of truth from this
        # point on. Skipped for a bare `push` — that action requires an already-clean
        # worktree (see the dirty check right below) and must not be newly dirtied by
        # this write; a group with pending conversation writes should use commit_push
        # instead. A write failure here is logged and never blocks finalize — the DB
        # stays authoritative regardless of whether the file update landed.
        if action != "push":
            try:
                from modules.flow_gate.services import conversation_markdown_service
                conversation_markdown_service.snapshot_group_conversations(project_id, group_id)
            except Exception:
                _log.exception("conversation markdown snapshot failed for group %s", group_id)

        # NR flowgate.default.0331.0005 §3: `push` sends only commits that
        # already exist — it must never fabricate one. A dirty worktree under
        # `push` is rejected (409) instead of silently absorbed, so it stays
        # distinguishable from `commit_push` (which is allowed to commit first)
        # and so uncommitted work is never lost to a bare push. merge/merge_only/
        # commit_push/commit_only all still absorb leftover worker edits first;
        # the subject is the user-confirmed message, or the resolver result on
        # the unmanned path (flowgate.default.0173 L0004 §2.6), resolved lazily
        # so a clean worktree never triggers a translate round-trip.
        if action == "push":
            if _gs._dirty(wt_path):
                raise GitServiceError(
                    409, "dirty_worktree",
                    "group worktree has uncommitted changes; use commit_push to "
                    "commit and push together, or archive before pushing",
                    details={"files": _dirty_files(wt_path)},
                )
        elif _gs._dirty(wt_path):
            # Count accepted untracked paths before staging consumes that state. The
            # classifier is shared with submission-time visibility and staging itself.
            staged_new_file_count = _worktree_untracked_summary_for_path(
                wt_path
            )["staged_new_file_count"]
            # 0382 B0001: NOT `git add -A`. See _absorb_worker_edits — the unfiltered
            # form is how 261 test-scratch files reached main inside an unrelated
            # commit, invisible to every screen that could have caught them.
            excluded_artifacts = _absorb_worker_edits(
                wt_path, finalize_subject(), author_env
            )

        # 0199 B0001: no-change short-circuit. After absorbing any worker edits,
        # if the work branch still holds NO commit beyond base there is nothing to
        # merge or push — an explicit merge/push here would only stamp an empty
        # `--no-ff` commit on base or leak an empty branch to origin. Tear the slot
        # down with no merge and no push (mirrors the auto-discard transition).
        # ahead is None when it cannot be counted → fall through to the normal
        # merge/push path (never discard on doubt).
        ahead = _ahead_of_base(base_root, base_branch, branch)
        # 0607 T0004 §3.3/§3.4 (NR0003 §3 request B): ahead == 0 also describes a
        # branch whose merge commit is already in LOCAL base but never reached
        # origin — a merge whose result an earlier request lost. That is not "no
        # work": finish it (push for `merge`), and never discard/clean the slot
        # before the push has landed.
        unpushed_merge = (
            _unpushed_local_merge_of(base_root, base_branch, branch)
            if (
                ahead == 0 and action in ("merge", "merge_only")
                # 0594 T0012: the recovery reads the base checkout, so it can only
                # finish a merge whose target IS the base; a non-base target keeps
                # its own attempt path.
                and target is not None and target.is_project_base
            )
            else None
        )
        if unpushed_merge is not None:
            _log.warning(
                "finalize %s: branch %s is already merged into local %s by %s but "
                "not pushed — finishing that merge instead of discarding",
                group_id, branch, base_branch, unpushed_merge,
            )
            wants_push = action == "merge"
            already_published = False
            if wants_push:
                proc = _gs._run_git(
                    ["fetch", "origin"],
                    cwd=base_root, timeout=_gs.GIT_NET_TIMEOUT_SEC, username=username, secret=secret,
                )
                if proc.returncode != 0:
                    raise GitServiceError(500, "git_error", "Git command failed", diagnostic=_gs._last_line(proc.stderr))
                already_published = _is_ancestor(
                    base_root, unpushed_merge, f"refs/remotes/origin/{base_branch}",
                ) is True
            if wants_push and not already_published:
                push = _gs._run_git(
                    ["push", "origin", base_branch],
                    cwd=base_root, timeout=_gs.GIT_NET_TIMEOUT_SEC, username=username, secret=secret,
                )
                if push.returncode != 0:
                    # The merge predates this request: never rewind it (that would
                    # drop the only copy of the work). Keep the slot and stay
                    # retryable; a later finalize finds the same merge again.
                    _gs._set_status(group_id, "waiting")
                    raise GitServiceError(500, "push_rejected", "Git push was rejected", diagnostic=_gs._last_line(push.stderr))
            return finish_merged(
                _rev_parse(base_root, unpushed_merge, short=True) or unpushed_merge[:7],
                wants_push,
            )
        if ahead == 0:
            # Approval-coupled no-work is only labelled here.  Slot teardown is
            # delayed until the AC/root transaction commits, so a failed approval
            # can safely prove the same no-work condition again on retry.
            if approval_context is None:
                _gs._cleanup_group_slot(project_id, group_id, force_discard=True)
                _gs._set_status(group_id, "none")
                _gs._emit("git_finalize_done", project_id, group_id, {
                    "project": project_id, "group_id": group_id,
                    "action": action, "status": DISCARDED_STATUS, "merge_commit": None,
                    **_artifact_payload(excluded_artifacts, staged_new_file_count),
                })
            else:
                record_clean_approval_retry(DISCARDED_STATUS, None)
            return {"ok": True, "result": {
                "action": action, "status": DISCARDED_STATUS, "merge_commit": None,
                "pushed": False, "merge_id": None, "conflict_files": [],
                **_artifact_payload(excluded_artifacts, staged_new_file_count),
            }}

        # Publish the work branch to origin ONLY for a bare push. A merge lands
        # the worker's commits into base/default locally (the work branch is a
        # worktree of the same repository, reachable by the base merge without a
        # remote round-trip) and pushes only base; the intermediate work branch
        # is never published to origin on a merge.
        # B flowgate.default.0172.0001-B: the user pressed no push, yet the work
        # branch appeared on the remote and default moved. Only the final merge
        # into default is intended to reach origin.
        if action in ("push", "commit_push"):
            proc = _gs._run_git(
                ["push", "origin", branch],
                cwd=wt_path, timeout=_gs.GIT_NET_TIMEOUT_SEC, username=username, secret=secret,
            )
            if proc.returncode != 0:
                raise GitServiceError(500, "push_rejected", "Git push was rejected", diagnostic=_gs._last_line(proc.stderr))
            if approval_context is not None:
                record_clean_approval_retry("pushed", None)
            else:
                _gs._set_status(group_id, "pushed")
            # Approval keeps the terminal ledger and slot intact until its DB
            # transaction decides; manual finalize preserves immediate cleanup.
            if approval_context is None:
                _gs._cleanup_group_slot(project_id, group_id)
            return _finalize_result(
                group_id, project_id, action, "pushed",
                pushed=True, artifacts=excluded_artifacts,
                staged_new_file_count=staged_new_file_count,
                emit=approval_context is None,
            )

        if action == "commit_only":
            # NR §3: a local-only commit cannot be followed by terminal cleanup
            # (nothing has left the worktree) — leave the group `waiting` so
            # merge/push/archive can still be chosen for it later.
            _gs._set_status(group_id, "waiting")
            return _finalize_result(
                group_id, project_id, "commit_only", "waiting",
                artifacts=excluded_artifacts,
                staged_new_file_count=staged_new_file_count,
            )

        # action == "merge" / "merge_only"
        # 0594 T0012 §6.1: the attempt record exists BEFORE any merge work — clean
        # merges included — with the target pinned in it. Every exit below either
        # completes it, turns it into the conflict session, or closes it as failed.
        assert target is not None
        attempt = merge_target.open_attempt(group_id, target, action, holder, attempt_owner)
        try:
            return _finalize_merge_attempt(
                group_id, project_id, cfg, attempt, action, branch, base_root,
                username=username, secret=secret, author_env=author_env,
                excluded_artifacts=excluded_artifacts,
                staged_new_file_count=staged_new_file_count,
                approval_context=approval_context,
                finish_merged=finish_merged,
            )
        except BaseException as exc:
            # Only an attempt still in progress is closed as failed — one that has
            # already become a conflict session or completed is left as it is.
            merge_target.fail_attempt(attempt, {
                "code": getattr(exc, "code", None) or type(exc).__name__,
                "message": str(getattr(exc, "message", "") or exc)[:500],
            })
            raise
    finally:
        if not borrowed_lock:
            _gs.db_git.release_lock(project_id, holder)


def _finalize_merge_attempt(
    group_id: str, project_id: str, cfg: dict, attempt: "merge_target.MergeTargetContext",
    action: str, branch: str, base_root: Path, *, username, secret, author_env,
    excluded_artifacts: list[str], staged_new_file_count: int,
    approval_context: Optional[ApprovalFinalizeContext] = None,
    finish_merged: Callable[..., dict],
) -> dict:
    """Run one pinned finalize attempt in its target root (lock held by caller).

    ``merge_root`` is the shared base checkout for a base target and the managed
    workspace for any other target; ``target_branch`` is the attempt's pinned
    branch. The base checkout is never switched to another branch."""
    from modules.flow_gate.services import git_service as _gs
    target_branch = attempt.target_branch
    if not attempt.is_project_base:
        merge_target.prepare_workspace(attempt)
    merge_root = attempt.root
    if merge_root is None:
        raise GitServiceError(409, "invalid_state", "target checkout is not available")
    if attempt.is_project_base and _gs._dirty(base_root, include_untracked=False):
        # E3 — never auto-stash the server's own checkout. Name the dirty files
        # so the FE can tell the operator exactly what to commit or revert in
        # the header Git panel instead of showing a bare 500 (T0010 §b).
        # 409, not 500 (0177 L0002 §2.5): a user-resolvable precondition, in
        # line with the invalid_state/git_busy family; code+details unchanged.
        raise GitServiceError(
            409, "base_dirty",
            "base checkout has local modifications; operator intervention required",
            details={"files": _dirty_files(base_root, include_untracked=False)},
        )
    # One repository: a fetch from the base checkout refreshes the remote refs
    # the managed workspace reads as well (the base working tree is untouched).
    proc = _gs._run_git(
        ["fetch", "origin"],
        cwd=base_root, timeout=_gs.GIT_NET_TIMEOUT_SEC, username=username, secret=secret,
    )
    if proc.returncode != 0:
        raise GitServiceError(500, "git_error", "Git command failed", diagnostic=_gs._last_line(proc.stderr))
    if _gs._ref_exists(merge_root, f"refs/remotes/origin/{target_branch}"):
        proc = _gs._run_git(["merge", "--ff-only", f"origin/{target_branch}"], cwd=merge_root)
        if proc.returncode != 0:
            if attempt.is_project_base:
                raise GitServiceError(  # E4
                    500, "base_diverged",
                    "base checkout has local-only commits and cannot fast-forward",
                )
            raise GitServiceError(
                500, "target_diverged",
                "target branch has local-only commits and cannot fast-forward",
                details={"target_branch": target_branch},
            )
    # T0012 §6.3: pin the merge inputs BEFORE git merge so a crash between the merge
    # commit / push and the attempt close is reconciled by recovery, not guessed.
    # 0607 T0004 §3.2: the same exact inputs of THIS merge let a lost command result
    # be judged against git's own facts instead of the return code (below).
    pre_merge_head = _rev_parse(merge_root, "HEAD")
    branch_tip = _rev_parse(merge_root, f"refs/heads/{branch}")
    merge_target.record_merge_inputs(
        attempt,
        pre_head=pre_merge_head,
        source_head=branch_tip,
        expected_remote_head=_rev_parse(merge_root, f"refs/remotes/origin/{target_branch}"),
    )
    _gs._set_status(group_id, "merging")
    # 0232 B0001: the merge commit carries a conventional Merge subject, NOT the
    # work subject — the absorb commit above already holds finalize_subject().
    # Reusing it here stamped two commits of identical title+diff onto origin.
    proc = _gs._run_git(
        [*_gs._GIT_IDENT, "-c", "merge.conflictStyle=zdiff3", "merge", "--no-ff", "-m",
         _merge_commit_subject(branch, target_branch), branch],
        cwd=merge_root, author_env=author_env,
    )
    merge_landed = proc.returncode == 0
    files: list[str] = []
    if not merge_landed:
        # A non-zero result (a timeout kill is -1) is not yet a verdict: git may
        # have written the merge commit and then been held by housekeeping
        # (NR0003 §5). Conflicts are checked FIRST and keep their own path.
        files_proc = _gs._run_git(["diff", "--name-only", "--diff-filter=U"], cwd=merge_root)
        files = [l.strip() for l in (files_proc.stdout or "").splitlines() if l.strip()]
        if not files and _merge_commit_landed(merge_root, pre_merge_head, branch_tip):
            _log.warning(
                "finalize %s: git merge returned %s (%s) but merge commit %s of %s "
                "is in %s — continuing as a successful merge",
                group_id, proc.returncode, _gs._last_line(proc.stderr),
                _rev_parse(merge_root, "HEAD", short=True), branch, target_branch,
            )
            # A process killed after its commit can leave MERGE_HEAD behind;
            # forget it without touching HEAD, index or worktree.
            if _rev_parse(merge_root, "MERGE_HEAD"):
                _gs._run_git(["merge", "--quit"], cwd=merge_root)
            merge_landed = True
    if merge_landed:
        wants_push = action == "merge"
        if wants_push:
            push = _gs._run_git(
                ["push", "origin", target_branch],
                cwd=merge_root, timeout=_gs.GIT_NET_TIMEOUT_SEC, username=username, secret=secret,
            )
            if push.returncode != 0:
                # E6 — atomicity: never report merged unless the push landed.
                # Rewind exactly the merge this request made; the slot stays.
                _gs._run_git(["reset", "--hard", pre_merge_head or "ORIG_HEAD"], cwd=merge_root)
                _gs._set_status(group_id, "waiting")
                raise GitServiceError(500, "push_rejected", "Git push was rejected", diagnostic=_gs._last_line(push.stderr))
        head = _gs._run_git(["rev-parse", "--short", "HEAD"], cwd=merge_root)
        # finish_merged completes the attempt (T0012 §6.3) after the ledger write.
        return finish_merged((head.stdout or "").strip() or None, wants_push, attempt)

    # Merge failed: conflicts keep MERGE_HEAD and become a session; anything
    # else is rolled back to waiting.
    if not files:
        # 0296 T0004 (NR0003 §5 / R5): one non-conflict failure has a specific,
        # user-fixable cause and used to arrive as a bare 500 — an untracked
        # file sitting in the base checkout on a path the merge wants to
        # create. The E3 guard cannot catch it (that guard is tracked-only, by
        # design), so this is where it must be named. git refuses BEFORE
        # starting the merge here, so `merge --abort` below is a harmless no-op.
        blockers = _untracked_merge_blockers(proc.stderr)
        _gs._run_git(["merge", "--abort"], cwd=merge_root)
        _gs._set_status(group_id, "waiting")
        if blockers is not None:
            raise GitServiceError(
                409, "base_untracked_conflict",
                "the merge is blocked by uncommitted new files in the base "
                "checkout; commit or remove them, then retry",
                details={"files": blockers},
            )
        raise GitServiceError(500, "git_error", "Git command failed", diagnostic=_gs._last_line(proc.stderr))
    # 0481 D0006 §3.4 / L0007 §2.1: the review gate's `resolver_baseline` and its
    # eventual `expected_remote_head` CAS-push condition both need the exact
    # inputs this merge attempt started from, captured now while MERGE_HEAD is
    # still the one this conflict is about — a later fetch/merge on this base
    # checkout must never be mistaken for the same merge.
    review_base_head = _rev_parse(merge_root, "HEAD")
    review_merge_head = _rev_parse(merge_root, "MERGE_HEAD")
    review_expected_remote_head = _rev_parse(merge_root, f"refs/remotes/origin/{target_branch}")
    session_context = {
        "review_state": None,
        "auto_authority": False,
        "resolver_baseline": {
            "base_head": review_base_head,
            "merge_head": review_merge_head,
            "expected_remote_head": review_expected_remote_head,
        },
    }
    # 0555 T0008 §2 / D0005 §3.5: a conflict that interrupts a FINAL APPROVAL
    # parks that approval here — in the same context write that turns the attempt
    # into a conflict session, so an "open conflict session, no intent" orphan (a
    # merge nobody could ever approve) is never produced. A plain manual finalize
    # passes no context and its session keeps exactly the shape it always had.
    if approval_context is not None and approval_context.approval_intent_id:
        session_context[approval_intent.INTENT_KEY] = approval_intent.build_intent(
            approval_intent_id=approval_context.approval_intent_id,
            group_id=group_id,
            ac_doc_id=approval_context.doc_id,
            requested_by=approval_context.actor_user_id,
            git_action=action,
        )
    merge_id = attempt.merge_id
    # The attempt record becomes the conflict session: same merge_id, conflict
    # files attached, target context unchanged.
    merge_target.mark_conflict(attempt, files, session_context)
    # 0608 T0005 (NR0003 §2.2): a file whose sides differ only in CRLF/LF merges
    # cleanly on normalised text — it is resolved here, before any resolver sees it,
    # and a real conflict keeps only its real chunks. 0594: 10 files / 28 chunks
    # became 6 / 24.
    _gs.apply_eol_separation(merge_id, merge_root)
    _gs._set_status(group_id, "conflict", merge_id=merge_id)
    # 0205 L §2.1: DO NOT transfer the lock to the session. The conflict wait
    # is expressed by the persistent 'conflict' state + open session. Manual
    # finalize releases its own lock in finalize(); approval finalize leaves the
    # borrowed lock for its orchestrator to release exactly once.
    session = _gs.db_git.get_session(merge_id)
    _gs._emit("git_merge_conflict", project_id, group_id, {
        "project": project_id, "group_id": group_id,
        "merge_id": merge_id, "conflict_count": len(files),
        "conflict_since": session.get("created_at") if session else None,
    })
    return {
        "ok": True,
        "result": {
            "action": action, "status": "conflict", "merge_commit": None,
            "pushed": False, "merge_id": merge_id, "conflict_files": files,
            # None for a manual finalize conflict: only an approval-coupled one
            # parks an intent here (T0008 §15 "no intent is forced on every session").
            "approval_intent_id": (
                session_context.get(approval_intent.INTENT_KEY) or {}
            ).get("approval_intent_id"),
            "target_branch": target_branch,
            **_artifact_payload(excluded_artifacts, staged_new_file_count),
        },
    }


def _finalize_result(
    group_id: str, project_id: str, action: str, status: str, *,
    pushed: bool = False, artifacts: Sequence[str] = (),
    staged_new_file_count: int = 0, emit: bool = True,
) -> dict:
    from modules.flow_gate.services import git_service as _gs
    if emit and status in ("pushed", "merged"):
        _gs._emit("git_finalize_done", project_id, group_id, {
            "project": project_id, "group_id": group_id,
            "action": action, "status": status, "merge_commit": None,
            **_artifact_payload(artifacts, staged_new_file_count),
        })
    return {
        "ok": True,
        "result": {
            "action": action, "status": status, "merge_commit": None,
            "pushed": pushed, "merge_id": None, "conflict_files": [],
            **_artifact_payload(artifacts, staged_new_file_count),
        },
    }


def _merge_commit_landed(
    base_root: Path, pre_merge_head: Optional[str], branch_tip: Optional[str]
) -> bool:
    """Whether a `merge --no-ff` whose command failed really committed the merge.

    flowgate.default.0607 T0004 §3.2 (NR0003 §3: 0600's merge commit existed 11ms
    before auto-gc held the process past the 30s kill). HEAD merely moving proves
    nothing; it must be a merge commit whose first parent is the base HEAD this
    merge started from and whose second parent is exactly the branch tip it was
    asked to merge. Anything else — including an unreadable repository — is a real
    failure for the caller's existing error path.
    """
    if not pre_merge_head or not branch_tip:
        return False
    head = _rev_parse(base_root, "HEAD")
    if not head or head == pre_merge_head:
        return False
    if _rev_parse(base_root, "HEAD^1") != pre_merge_head:
        return False
    if _rev_parse(base_root, "HEAD^2") != branch_tip:
        return False
    return _is_ancestor(base_root, branch_tip, "HEAD") is True


def _untracked_merge_blockers(stderr: Optional[str]) -> Optional[list[str]]:
    """The untracked paths that made git refuse a merge, or None if that is not
    why it failed (0296 T0004 / NR0003 R5).

    None vs [] is load-bearing: the caller only swaps in the dedicated error code
    when this failure was actually identified, so an unrelated git error keeps its
    honest 500 instead of being mislabelled. A recognized header with no parsable
    file lines still returns [] — the diagnosis holds even if the list does not.
    """
    lines = (stderr or "").splitlines()
    found = False
    out: list[str] = []
    for line in lines:
        if not found:
            if _UNTRACKED_MERGE_RE.search(line):
                found = True
            continue
        # git indents the offending paths; the first unindented line ends the block.
        if not line[:1].isspace():
            break
        path = line.strip()
        if path:
            out.append(path)
    return out if found else None


def _tracked_merge_blockers(stderr: Optional[str]) -> Optional[list[str]]:
    """Tracked paths whose local modifications made Git refuse the merge."""
    lines = (stderr or "").splitlines()
    found = False
    out: list[str] = []
    for line in lines:
        if not found:
            if _TRACKED_MERGE_RE.search(line):
                found = True
            continue
        if not line[:1].isspace():
            break
        path = line.strip()
        if path:
            out.append(path)
    return out if found else None


def manual_push(project_id: str, branch: Optional[str]) -> dict:
    """POST …/projects/{id}/git/push — recovery re-push of an accumulated branch.

    ``branch`` must EXACTLY match the base branch or one of this project's
    registered slot branches (no prefix/pattern matching, L §2.4). Terminal
    (merged/pushed) slots are allowed — a re-push is a recovery operation."""
    from modules.flow_gate.services import git_service as _gs
    cfg = _gs._require_enabled_config(project_id)
    base_branch = (cfg.get("base_branch") or "main").strip() or "main"
    branch = (branch or base_branch).strip() or base_branch
    project_name = _gs._project_name(project_id)
    if not project_name:
        raise GitServiceError(404, "not_found", f"project '{project_id}' not found")

    allowed = {base_branch}
    for r in _gs.db_git.list_states_of_project(project_id):
        if r.get("branch"):
            allowed.add(r["branch"])
    if branch not in allowed:
        raise GitServiceError(
            422, "invalid_request",
            f"branch '{branch}' is not the base branch or a group slot of project '{project_id}'",
        )
    cwd = _gs.src_root(project_name, branch)
    if not cwd.is_dir():
        raise GitServiceError(
            409, "invalid_state", f"checkout for branch '{branch}' is missing"
        )
    # 0205 §2.2: pushing the BASE branch publishes the shared base checkout —
    # gate it while a conflict session holds base. A work-branch re-push does not
    # touch base and is never gated.
    pushes_base = branch == base_branch
    if pushes_base:
        _gs.guard_base_free(project_id)   # 1st gate (before lock)
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
    try:
        if pushes_base:
            _gs.guard_base_free(project_id)   # 2nd gate (race close, after lock)
        # flowgate.default.0361 NR0003 §8.1: `cwd` may be a group worktree rather
        # than the base checkout, but its `origin` remote lives in the shared
        # `.git` directory either way, so syncing through `cwd` is sufficient.
        _gs.ensure_origin_matches_config(cwd, (cfg.get("repo_url") or "").strip())
        proc = _gs._run_git(
            ["push", "origin", branch],
            cwd=cwd, timeout=_gs.GIT_NET_TIMEOUT_SEC,
            username=cfg.get("username"), secret=_gs._load_secret_for(cfg) or "",
        )
        if proc.returncode != 0:
            raise GitServiceError(500, "push_rejected", "Git push was rejected", diagnostic=_gs._last_line(proc.stderr))
        ahead, behind = _gs._base_ahead_behind(cwd, base_branch) if pushes_base else (None, None)
        if pushes_base:
            _gs._emit_pending_changed(project_id, None, None)
        return {"ok": True, "result": {
            "pushed": True, "branch": branch,
            "ahead_count": ahead, "behind_count": behind,
        }}
    finally:
        _gs.db_git.release_lock(project_id, holder)


def unmerge(group_id: str, merge_commit: str) -> dict:
    """Undo the latest unpushed merge for a group and re-open its worktree."""
    from modules.flow_gate.services import git_service as _gs
    req_sha = (merge_commit or "").strip().lower()
    if not UNMERGE_SHA_RE.match(req_sha):
        raise GitServiceError(
            422, "invalid_request",
            "merge_commit must be a 7 to 40 character hexadecimal sha prefix.",
        )
    project_id = _gs._project_of_group(group_id)
    cfg = _gs._require_enabled_config(project_id)
    state = _gs.db_git.get_state(group_id)
    ledger_sha = str((state or {}).get("merge_commit") or "").lower()
    if (
        state is None
        or (state.get("status") or "none") != "merged"
        or not ledger_sha
        or _gs._is_group_disposed(group_id)
    ):
        raise GitServiceError(409, "invalid_state", "group is not an unmergeable merged group")
    if not _full_sha_matches(req_sha, ledger_sha):
        raise GitServiceError(409, "stale_target", "requested merge commit does not match this group")
    # 0594 T0012 §15: unmerge rewinds the SHARED BASE checkout. A group whose
    # completed attempt landed on another branch is refused before anything
    # touches base (no attempt record / no target → legacy base merge, unchanged).
    if not merge_target.merged_on_project_base(state):
        done = merge_target.completed_target_of_state(state)
        raise GitServiceError(
            409, "unmerge_unsupported_target",
            "unmerge is only available for merges into the project base branch",
            details=done.public() if done is not None else None,
        )

    base_branch = (cfg.get("base_branch") or "main").strip() or "main"
    project_name = _gs._project_name(project_id)
    if not project_name:
        raise GitServiceError(404, "not_found", f"project '{project_id}' not found")
    base_root = _gs.src_root(project_name, base_branch)
    if _gs._judge_base_slot(base_root, base_branch) != "checkout":
        raise GitServiceError(409, "invalid_state", "base checkout is not available")
    if not _gs.git_available():
        raise GitServiceError(
            500, "git_unavailable",
            "git binary not found on server (install git in the runtime image)",
        )

    _gs.guard_base_free(project_id)
    holder = f"op:{uuid.uuid4()}"
    if not _gs._acquire_lock(project_id, holder):
        raise GitServiceError(
            409, "git_busy",
            f"Another git operation is in progress for project '{project_id}' (try again shortly)",
        )
    try:
        _gs.guard_base_free(project_id)
        commits = _unpushed_commits(base_root, base_branch)
        if commits is None:
            raise GitServiceError(409, "invalid_state", "unpushed base history is not measurable")
        if not commits:
            raise GitServiceError(409, "already_pushed", "merge commit is no longer unpushed")

        top = commits[0]
        target_in_chain = any(_full_sha_matches(c["full_sha"], ledger_sha) for c in commits)
        if not _full_sha_matches(top["full_sha"], ledger_sha):
            if target_in_chain:
                raise GitServiceError(
                    409, "not_top_merge",
                    "a newer unpushed commit blocks unmerge",
                    details={
                        "top_merge_commit": top["full_sha"][:7],
                        "top_group_id": _ledger_group_by_merge_sha(project_id, top["full_sha"]),
                    },
                )
            raise GitServiceError(409, "already_pushed", "merge commit is no longer unpushed")
        if len(top["parents"]) < 2 or not _full_sha_matches(top["full_sha"], req_sha):
            raise GitServiceError(
                409, "stale_target",
                "requested merge commit is no longer the current top merge",
                details={"current_top": top["full_sha"][:7]},
            )

        branch = (state.get("branch") or worktree_branch_name(project_id, _gs._module_of(group_id), group_id)).strip()
        restored_head = _rev_parse(base_root, f"{top['full_sha']}^2")
        if not restored_head:
            raise GitServiceError(500, "git_error", "cannot resolve merged work branch head")
        if _gs._ref_exists(base_root, f"refs/heads/{branch}"):
            current = _rev_parse(base_root, f"refs/heads/{branch}")
            if current != restored_head:
                raise GitServiceError(
                    500, "git_error",
                    f"local branch '{branch}' exists at an unexpected commit",
                )
        else:
            proc = _gs._run_git(["branch", branch, f"{top['full_sha']}^2"], cwd=base_root)
            if proc.returncode != 0:
                raise GitServiceError(500, "git_error", "Git command failed", diagnostic=_gs._last_line(proc.stderr))

        _gs._set_status(group_id, "awaiting_choice")
        reset = _gs._run_git(["reset", "--hard", f"{top['full_sha']}^1"], cwd=base_root)
        if reset.returncode != 0:
            _gs._set_status(group_id, "merged", merge_id=state.get("merge_id"), merge_commit=ledger_sha)
            raise GitServiceError(500, "git_error", "Git command failed", diagnostic=_gs._last_line(reset.stderr))
        base_head = _short_head(base_root)
    finally:
        _gs.db_git.release_lock(project_id, holder)

    reprovision_result = _gs.ensure_worktree(project_id, _gs._module_of(group_id), group_id, trigger="unmerge")
    return {"ok": True, "result": {
        "unmerged": True,
        "merge_commit": top["full_sha"][:7],
        "base_head": base_head,
        "group_status": "awaiting_choice",
        "reprovisioned": reprovision_result == "ok",
    }}


def precheck_approve_git_action(doc: Optional[dict], git_action: str) -> str:
    """Validate the terminal Git choice before the approval mutex is acquired."""
    from modules.flow_gate.services import git_service as _gs
    invalid = GitServiceError(
        422, "invalid_request",
        "git_action is only accepted on AC documents of a git-active group",
    )
    if git_action not in APPROVAL_FINALIZE_ACTIONS:
        raise invalid
    doc = doc or {}
    if doc.get("type_code") != "AC":
        raise invalid
    group_id = doc.get("group_id") or ""
    project_id = _gs._project_of_group(group_id)
    cfg = _gs.db_git.get_config(project_id)
    state = _gs.db_git.get_state(group_id)
    if (
        cfg is None or not cfg.get("enabled")
        or state is None or not state.get("worktree_registered")
    ):
        raise invalid
    return group_id


def precheck_approve_git_target(
    group_id: str, git_action: str, git_target_branch: Optional[str],
) -> Optional[str]:
    """Validate the finalize target carried on an AC approval BEFORE it runs.

    0594 T0012 §10: the same server-side resolver the finalize itself uses (allowed
    local branch, not remote-only, not a registered slot, workspace not owned by
    another attempt, no retarget of an open attempt). Raises GitServiceError so the
    approval is refused without being applied; returns the normalized target."""
    from modules.flow_gate.services import git_service as _gs
    requested = merge_target.normalize_requested_target(git_target_branch)
    if requested is None:
        return None
    project_id = _gs._project_of_group(group_id)
    cfg = _gs.db_git.get_config(project_id) or {}
    if git_action not in merge_target.MERGE_ACTIONS:
        if requested == merge_target.project_base_branch(cfg):
            return requested
        raise GitServiceError(
            422, "merge_target_requires_merge",
            "a finalize target other than the base branch applies to merge actions only",
            details={"target_branch": requested, "action": git_action},
        )
    merge_target.plan_finalize_target(group_id, project_id, cfg, requested)
    return requested


def run_approve_git_action(
    group_id: str,
    git_action: str,
    git_target_branch: Optional[str] = None,
    *,
    approval_context: Optional[ApprovalFinalizeContext] = None,
) -> dict:
    """Run Git without assuming approval and return a branchable verdict.

    ``git_target_branch`` (0594 T0012) is the merge target the approval carries;
    omitted → the project base (or an open attempt's pinned target)."""
    from modules.flow_gate.services import git_service as _gs
    try:
        finalize_kwargs: dict = {"approval_context": approval_context}
        if git_target_branch is not None:
            # 0594 T0012: the finalize target carrier must survive this seam.
            finalize_kwargs["target_branch"] = git_target_branch
        outcome = _gs.finalize(group_id, git_action, **finalize_kwargs)
        result = outcome["result"]
        if result.get("status") == "conflict":
            return {
                "ok": True,
                "terminal": False,
                "deferred": True,
                "result": result,
            }
        terminal = result.get("status") in {
            "merged", "pushed", DISCARDED_STATUS, "stashed"
        }
        return {"ok": True, "terminal": terminal, "result": result}
    except GitServiceError as exc:
        error = {"code": exc.code, "message": exc.message}
        if getattr(exc, "details", None):
            error["details"] = exc.details
        return {
            "ok": False,
            "terminal": False,
            "http_status": exc.status,
            "error": error,
        }


def complete_approve_git_action(
    group_id: str,
    git_action: str,
    outcome: dict,
    *,
    approved: bool,
) -> None:
    """Best-effort cleanup and terminal notification after approval is decided."""
    from modules.flow_gate.services import git_service as _gs

    result = outcome.get("result") or {}
    status = result.get("status")
    try:
        project_id = _gs._project_of_group(group_id)
        if approved:
            if status == DISCARDED_STATUS:
                _gs._cleanup_group_slot(project_id, group_id, force_discard=True)
                _gs._set_status(group_id, "none")
            elif status in ("merged", "pushed"):
                _gs._cleanup_group_slot(project_id, group_id)
        _gs._emit_pending_changed(project_id, group_id, status)
        _gs._emit("git_finalize_done", project_id, group_id, {
            "project": project_id,
            "group_id": group_id,
            "action": git_action,
            **result,
            "approval": {
                "approved": approved,
                "document_status": "approved" if approved else "pending_review",
                "root_status": "wf_done" if approved else "wf_in_progress",
                "stage": "complete" if approved else "approval_commit",
                "deferred": False,
            },
        })
    except Exception:
        _log.warning(
            "approval finalize post-processing failed for %s", group_id, exc_info=True
        )


def reopen_group_git(
    project_id: str, group_id: str, terminal_commit_sha: Optional[str] = None,
    terminal_session: Optional[dict] = None,
) -> None:
    """Re-arm a group's git slot after a time-machine rewind past finalize (B0001,
    flowgate.default.0211; extended by NR0003 R1/R2, flowgate.default.0477).

    The reverse-time-machine rewinds only the document/workflow layer; the git
    ledger keeps whatever the prior finalize left it in. Restore the invariant
    this module now states explicitly:

        Git status in (awaiting_choice, waiting) ⇒ workflow root == wf_done

    A rewind that takes root back to wf_in_progress must therefore also take the
    git ledger back to a non-pending state, or the header/finalize gate keeps
    treating an unapproved group as "ready to merge" (NR0003 §9-§13).

        merged / pushed
            → terminal: the group's worktree was already torn down and
              unregistered by slot cleanup (0182), so the next finalize on the
              re-worked group is impossible (precheck_approve_git_action 422
              "not a git-active group", or finalize 409 "already finalized" once
              a write-gate self-heal re-registers the worktree but leaves status
              terminal — register_worktree never touches status). Drop the status
              back to 'none' and re-provision the worktree from base HEAD.

        awaiting_choice / waiting
            → inert bookkeeping: finalize was reachable (root had reached
              wf_done) but no real git operation ever ran — no worktree/session
              to lose. Drop the status straight back to 'none'; the existing
              worktree is untouched and stays usable.

        conflict / merging
            → a real git operation/session (an open merge, a base checkout
              mid-conflict) may be live for this group. Silently resetting it out
              from under a rewind would either orphan the session or corrupt the
              shared base checkout, so these are NEVER touched here — the reopen
              itself is refused with 409 before this function runs
              (``raise_if_git_session_blocks_reopen``), leaving both the
              workflow layer and the git session exactly as they were.

    Never raises: the caller's document rewind has already committed and must
    stand regardless.
    """
    from modules.flow_gate.services import git_service as _gs
    try:
        cfg = _gs.db_git.get_config(project_id)
        if cfg is None or not cfg.get("enabled"):
            return
        state = _gs.db_git.get_state(group_id)
        if state is None:
            return
        status = (state.get("status") or "none")
        if status in ("merged", "pushed"):
            if not terminal_commit_sha:
                raise GitServiceError(409, "terminal_commit_absent", "terminal reopen requires C1")
            # When called from reopen_to_target, the terminal session still owns the
            # project lock and this code runs inside the workflow DB transaction.
            # Provision directly under that lock so no source write can interleave.
            if terminal_session is not None:
                project_name = _gs._project_name(project_id)
                if not project_name:
                    raise GitServiceError(409, "terminal_reprovision_failed", "project name missing")
                branch = worktree_branch_name(project_id, _gs._module_of(group_id), group_id)
                provisioned = _ensure_worktree_locked(
                    cfg, project_id, project_name, group_id, branch,
                    "timemachine_reopen", terminal_commit_sha,
                )
            else:
                provisioned = _gs.ensure_worktree(
                    project_id, _gs._module_of(group_id), group_id,
                    trigger="timemachine_reopen", start_point=terminal_commit_sha,
                )
            if provisioned == "failed":
                raise GitServiceError(
                    409, "terminal_reprovision_failed",
                    "cannot preserve the terminal commit while reopening the worktree",
                )
            _gs._set_status(group_id, "none")
            return
        if status in ("awaiting_choice", "waiting"):
            _gs._set_status(group_id, "none")
            return
        # status in ("none", "conflict", "merging"): nothing to do. conflict/merging are
        # deliberately left alone — see the docstring above.
    except GitServiceError:
        raise
    except Exception:
        _log.warning("git reopen re-arm failed for %s", group_id, exc_info=True)


def raise_if_git_session_blocks_reopen(project_id: str, group_id: str) -> Optional[dict]:
    """Refuse a workflow reopen (Time Machine rewind) outright while the group's git
    ledger holds an active session (NR0003 R2, flowgate.default.0477).

    ``conflict``/``merging`` mean a real git operation is in flight for this group — an
    open merge session or a base checkout mid-conflict. ``reopen_group_git`` never touches
    those statuses (nothing to silently reset without risking an orphaned session or a
    corrupted shared base checkout), so the reopen request itself must be rejected before
    the rewind transaction runs, preserving both the workflow state and the git session
    untouched. ``awaiting_choice``/``waiting`` are fine to let through — they hold no live
    session and ``reopen_group_git`` resets them to ``none`` after the rewind commits.
    """
    from modules.flow_gate.services import git_service as _gs
    cfg = _gs.db_git.get_config(project_id)
    if cfg is None or not cfg.get("enabled"):
        return
    state = _gs.db_git.get_state(group_id)
    if state is None:
        return
    status = (state.get("status") or "none")
    if status in ("merged", "pushed"):
        # A terminal reopen will re-provision this slot after the workflow transaction.
        # Check an extant worktree before that transaction, so a dirty terminal group
        # cannot reopen documents or have its user edits overwritten.
        terminal = _gs.open_terminal_reopen_session(group_id)
        if not terminal.get("ok"):
            reason = terminal.get("blocked_reason") or "git_busy"
            if reason == "dirty_worktree":
                raise GitServiceError(
                    409, "dirty_worktree",
                    "cannot reopen while the terminal worktree has uncommitted changes",
                )
            raise GitServiceError(
                409, reason,
                f"Terminal reopen is currently blocked for project '{project_id}' (try again shortly)",
            )
        return terminal["session"]
    if status == "conflict":
        raise GitServiceError(
            409, "invalid_state",
            "cannot reopen while a merge conflict is unresolved for this group; "
            "resolve or abort it first",
        )
    if status == "merging":
        raise GitServiceError(
            409, "git_busy",
            f"Another git operation is in progress for project '{project_id}' (try again shortly)",
        )
