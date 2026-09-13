"""Git integration service (flowgate.default.0115 — D0004/P0005/L0006/DB0007).

Bridges FlowGate projects and remote Git repositories:

  - per-project config + reversibly-encrypted credentials (L0006 §2.3)
  - connection test (ls-remote, L0006 §2.5)
  - base-slot provisioning: clone into an empty slot, or LOSSLESS adopt of an
    occupied slot + last-attempt ledger + manual trigger
    (flowgate.default.0161 — D0003/P0004/L0005)
  - per-group branch/worktree provisioning (L0006 §2.1·§2.4; hooks H1/H2)
  - effective source-root resolution for workers (L0006 §2.2 — fallback first:
    a non-integrated project NEVER changes behavior)
  - finalize state machine merge/push/wait (L0006 §2.6·§3), conflict sessions
    (L0006 §2.7) and the project-level git mutex (L0006 §2.8, DB-backed)

Secret invariant (L0006 §2.3): the plaintext secret never appears in responses,
logs, git argv, or repository URLs. Git authentication is injected via a
one-shot ASKPASS helper whose values travel in child-process env vars; stderr
is scrubbed before storage/return.
"""
from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path
from typing import Optional, Sequence

from Crypto.Cipher import AES as _AES

from modules.flow_gate.db import documents as db_documents
from modules.flow_gate.db import git_integration as db_git
from modules.flow_gate.db import groups as db_groups
from modules.flow_gate.db import project_ai_leases as db_project_ai_leases
from modules.flow_gate.db import projects as db_projects
from modules.flow_gate.db import system_settings as db_settings
from modules.flow_gate.db import terminal_cleanup_snapshots as db_terminal_cleanup
from modules.flow_gate.db import tr_commit_ledger as db_tr_ledger
from modules.flow_gate.db.connection import get_store, now_iso
from modules.flow_gate.services import path_exclusion_rules
from modules.flow_gate.storage.paths import get_storage_root, src_root

_log = logging.getLogger(__name__)

# ── Parameters (L0006 §1) ─────────────────────────────────────────────────────

GIT_TEST_TIMEOUT_SEC = 15
GIT_NET_TIMEOUT_SEC = 120
# 0287 NR0004 §3: `worktree remove` recursively deletes a FULL source checkout
# (measured: 864 files / 112 MB) and the storage root is routinely an SMB share,
# where every unlink is a round trip. Under the 30 s local budget the subprocess
# was killed MID-DELETE, leaving a half-erased tree whose `.git` file was already
# gone — the state that then failed every retry forever. Deletion gets its own,
# far larger budget; it is a local filesystem walk, not a network call.
# Group branch file explorer — checkout-free ref/tree/blob reads (0186 L0006 §1).
GIT_READ_TIMEOUT_SEC = 15          # local ls-tree / cat-file timeout (no network)
BLOB_MAX_RETURN_BYTES = 1048576    # 1 MiB blob content cap; over → truncated=true
BLOB_BINARY_SNIFF_BYTES = 8000     # NUL-scan window for binary detection (git heuristic)
# ── Git tangle prevention (flowgate.default.0205 — D0002/P0003/L0004/DB0005) ──
# A conflict wait no longer holds the project lock; abandoned sessions are
# reclaimed by a sweep so one stalled merge can never silently disable every
# later group's git management (0203 root cause).

from .git.commit import (
    COMMIT_SUBJECT_MAX,
    FINALIZE_ARTIFACT_LIST_MAX,
    FIXED_FALLBACK_SUBJECT,
    TR_FALLBACK_SUBJECT,
    _absorb_worker_edits,
    _artifact_payload,
    _cancel_prelock_gate,
    _ledger_group_by_merge_sha,
    _merge_commit_subject,
    _release_cancel_lock,
    _revert_one,
    _stage_worker_edits,
    _translate_guard,
    _try_translate,
    build_auto_commit_message,
    cancel_blocking_dirty,
    cancel_body,
    cancel_group_status,
    cancel_subject,
    close_cancel_session,
    conventional_subject,
    create_tr_commit,
    derive_commit_type,
    is_conventional_subject,
    open_cancel_session,
    open_terminal_reopen_session,
    reapply_body,
    reapply_subject,
    reapply_tr_commit,
    resolve_commit_message,
    restore_after_failed_revert,
    revert_tr_commit,
    uncommit_tr_suffix,
)


# ── Base-checkout explicit commit / revert (flowgate.default.0177 — L0002) ────
# Default subject for an explicit base-checkout commit: "fix: a.py, b.py", or the
# abbreviated "fix: a.py and N more" when the joined list overflows COMMIT_SUBJECT_MAX.
# Subject for the seed commit that BORNs the base branch when a brand-new EMPTY
# remote is connected (0313 B0001): `git clone --branch <base>` cannot create it,
# so provisioning initializes the slot with this one README.md commit instead.
# Present while an adopt is unfinished — the slot never reports "checkout"
# until the marker is removed (L0005 §2.1·§2.3, 0161).
# Per-project last-attempt ledger in the generic system_settings KV (no DDL).
SESSION_ACTION_DEFAULT = "merge"
# flowgate.default.0162 L §1 — group git status subsets.
PENDING_STATUSES = ("awaiting_choice", "waiting", "conflict")  # "finalize pending"
# "merging" is a transient state: recorded, but its transition is not broadcast
# (it would flicker the badge n→n-1→n before the terminal event lands, L §2.3).
TRANSIENT_STATUSES = ("merging",)
# flowgate.default.0182 NR0003 §5 — terminal statuses whose slot leftovers
# (worktree dir, local work branch, ledger registration) are cleanup targets.
CLEANUP_STATUSES = ("merged", "pushed")

# Identity for commits the SERVER makes (auto-commit / merge commits). Without
# an explicit identity `git commit` fails on hosts with no global user config.
# This is the COMMITTER (and the author fallback) — it stays "FlowGate" because the
# server really is what ran the commit.
_GIT_IDENT = ["-c", "user.name=FlowGate", "-c", "user.email=flowgate@localhost"]


from .git.credentials import (
    GitServiceError,
    _author_env_for,
    _author_env_from_cfg,
    _get_current_key,
    _key_file_path,
    _load_key_material,
    _load_secret_for,
    _scrub,
    decrypt_secret,
    encrypt_secret,
    mask_secret,
)


# ── Branch naming (L0006 §2.1) ────────────────────────────────────────────────





def _module_of(group_id: str) -> str:
    parts = (group_id or "").split(".", 2)
    return parts[1] if len(parts) == 3 else "default"


def _project_of_group(group_id: str) -> str:
    return (group_id or "").split(".", 1)[0]


def _one_line_subject(text: Optional[str]) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip()


# normalize_subject (L0004 §2.1): collapse newlines/tabs/runs of whitespace to a
# single space and trim — the one canonical subject cleaner for every path.
normalize_subject = _one_line_subject


def _is_ascii(text: str) -> bool:
    return all(ord(ch) < 128 for ch in text)


from .git.command import (
    GIT_LOCAL_TIMEOUT_SEC,
    _run_git,
    _write_askpass,
    git_available,
)


# ── SSE emission (P0005 §4·§5 events) ────────────────────────────────────────

def _emit(event_type: str, project: str, group_id: Optional[str], payload: dict) -> None:
    """Best-effort FlowEvent broadcast; never breaks the calling operation."""
    try:
        from modules.flow_gate.api.v1.events.publisher import (
            FlowEvent,
            broadcast_event_threadsafe,
        )

        broadcast_event_threadsafe(FlowEvent(
            event_type=event_type,
            payload=payload,
            audience="*",
            project=project,
            group_id=group_id,
            doc_id=None,
        ))
    except Exception:
        _log.warning("git SSE emit failed (%s)", event_type, exc_info=True)


# ── Pending-set broadcast (flowgate.default.0162 L §2.3) ─────────────────────

def _count_pending(project_id: str) -> int:
    """Project-wide "finalize pending" count, recomputed from the ledger.

    Never stored (DB0005) — a denormalized counter would drift on a missed
    emit; recompute is index-covered (idx_group_git_state_project).
    """
    rows = db_git.list_states_of_project(project_id)
    return sum(1 for r in rows if (r.get("status") in PENDING_STATUSES))


def _emit_pending_changed(project_id: str, group_id: Optional[str], new_status: Optional[str]) -> None:
    _emit("git_pending_changed", project_id, group_id, {
        "project": project_id,
        "group_id": group_id,
        "status": new_status,
        "pending_count": _count_pending(project_id),
    })


def _set_status(
    group_id: str,
    status: str,
    *,
    merge_id: Optional[int] = None,
    merge_commit: Optional[str] = None,
) -> None:
    """Record a group's git status AND broadcast git_pending_changed (L §2.3).

    Single convergence point so no transition can silently skip the badge
    update. The transient "merging" state is recorded but not broadcast.
    """
    db_git.set_status(group_id, status, merge_id=merge_id, merge_commit=merge_commit)
    if status not in TRANSIENT_STATUSES:
        _emit_pending_changed(_project_of_group(group_id), group_id, status)


# ── Project lock (L0006 §2.8) ────────────────────────────────────────────────


from .git.lock import (
    LOCK_WAIT_SEC,
    _acquire_lock,
    base_merge_in_progress,
    guard_base_free,
    open_merge_session_of_project,
)



# ── Base-protection gate (flowgate.default.0205 P scenario 3 / L §2.2) ────────







from .git.config import (
    PROVIDER_VALUES,
    DEFAULT_FINALIZE_ACTION_VALUES,
    _base_root_of,
    _config_view,
    _require_enabled_config,
    _resolve_author,
    _validate_repo_url,
    base_branch_for,
    base_src_root,
    delete_config,
    get_config_view,
    save_config,
)


# ── Connection test (P0005 §3 / L0006 §2.5) ──────────────────────────────────





# ── Worktree provisioning (L0006 §2.4 — hooks H1/H2) ─────────────────────────

def _project_name(project_id: str) -> Optional[str]:
    row = db_projects.get_by_id(project_id)
    name = (row.get("project_name") or "").strip() if row else ""
    return name or None


# ── Worktree liveness: is that directory a REAL worktree? (0287 NR0004) ──────
# Every gate in this module used to equate "the directory exists" with "a healthy
# registered worktree exists". A `worktree remove` interrupted mid-delete breaks
# that equivalence: the directory survives while its `.git` link and most of its
# content are already gone. Two corpse shapes were observed in the field —
#   B) admin dir still in .git/worktrees, worktree `.git` file gone → `prunable`
#   C) admin dir pruned away too → git no longer knows the path at all
# — and BOTH pass `is_dir()`. These helpers tell the three states apart.


def _worktree_link_ok(wt_path: Path) -> bool:
    """Whether *wt_path* still carries its worktree `.git` link.

    Cheap local check (one stat) and the discriminator that matters to readers:
    without this link the directory is a half-deleted corpse, not a source tree.
    A normal worktree has `.git` as a FILE ('gitdir: …'); the base checkout has it
    as a directory. Both count as linked — callers may hand either one in."""
    try:
        return (wt_path / ".git").exists()
    except OSError:
        return False


def _registered_worktrees(base_root: Path) -> Optional[set[Path]]:
    """Resolved paths git currently accepts as live worktrees, or None if unknown.

    Parses `git worktree list --porcelain`. An entry flagged `prunable` is git's
    own statement that the registration is stale, so it is EXCLUDED — for cleanup
    purposes a prunable entry is an orphan, not a worktree.

    Paths are compared resolved, never as strings: `git worktree list` reports the
    real path (e.g. a UNC share `//host/share/…`) while `src_root()` builds the
    junction/mapped form (`C:\\…\\storage\\…`), so the two spellings of one
    directory never match textually (0287 NR0004 §7-1).

    Returns None — meaning "cannot tell" — when git fails or times out, so callers
    can stay conservative instead of mistaking silence for "not registered"."""
    proc = _run_git(["worktree", "list", "--porcelain"], cwd=base_root)
    if proc.returncode != 0:
        _log.warning(
            "worktree list failed in %s: %s", base_root, _last_line(proc.stderr)
        )
        return None
    live: set[Path] = set()
    current: Optional[Path] = None
    prunable = False

    def _flush() -> None:
        if current is not None and not prunable:
            live.add(current)

    for line in (proc.stdout or "").splitlines():
        if line.startswith("worktree "):
            _flush()
            prunable = False
            raw = line[len("worktree "):].strip()
            try:
                current = Path(raw).resolve()
            except OSError:
                current = None
        elif line.startswith("prunable"):
            prunable = True
    _flush()
    return live


def _classify_worktree_dir(base_root: Path, wt_path: Path) -> str:
    """'live' | 'orphan' | 'unknown' for an EXISTING directory (0287 NR0004 §7-1).

    'live'    — git lists it as a non-prunable worktree AND its `.git` link is intact
    'orphan'  — the directory is there but git does not (or no longer can) own it:
                unregistered, prunable, or link destroyed by an interrupted delete
    'unknown' — git could not answer; the caller must not assume either way
    """
    if not _worktree_link_ok(wt_path):
        # Decisive on its own: `worktree remove` refuses such a path outright
        # ("validation failed, cannot remove working tree: '…/.git' does not exist").
        return "orphan"
    live = _registered_worktrees(base_root)
    if live is None:
        return "unknown"
    try:
        resolved = wt_path.resolve()
    except OSError:
        return "orphan"
    return "live" if resolved in live else "orphan"




# ── Base-slot provisioning: lossless adopt + attempt ledger (0161 L0005) ─────

from .git.base_slot import (
    _judge_base_slot,
    _record_attempt,
    _load_attempt_record,
    _provision_failed,
    _adopt,
    _absorb_snapshot,
    _remote_is_empty,
    _remote_lacks_base_branch,
    _bootstrap_empty_remote,
    _provision_base_locked,
    provision_base,
    provision_view,
    provision_manual,
    manual_fetch,
    default_base_commit_message,
    _require_base_checkout,
    _base_commit_locked,
    base_commit,
    _base_revert_locked,
    base_revert,
    base_remove,
)


























from .git.worktree import (
    BRANCH_MAX_LEN,
    GIT_WORKTREE_RM_TIMEOUT_SEC,
    sanitize_branch,
    worktree_branch_name,
    _force_rmtree,
    ensure_worktree,
    _ensure_worktree_locked,
    _worktree_start_point,
    _emit_worktree_ready,
    _emit_worktree_failed,
    _fail_worktree,
    ensure_worktree_async,
    _has_legacy_source_history,
    ensure_initial_group_source_sync,
    _is_group_disposed,
    _abort_disposed_merge_session,
    _cleanup_group_slot,
)














# ── Initial group source sync (flowgate.default.0511 T0004 / NR0003 v5) ──────
# ensure_worktree above provisions a group's worktree once, at creation, and its
# idempotence check only asks "does the directory exist and match the ledger" -
# never "is it current". A group that starts with N/NR investigation reads
# whatever base the worktree forked from, and by the time real source work (TR)
# begins the base may already have moved on. This is the ONE forced reset+clean
# that closes that gap: performed exactly once per group, right before the
# group's FIRST raw source-capable AI invocation. The caller
# (ai_invoke_service._ensure_initial_source_sync) gates the call on
# tool_registry.kind_for_token() in {"read", "read_write"} -- NEVER on
# resolve_registry()'s source_mode-adjusted advertising value, which never gates
# permission (kind_for_step's own docstring: "Source mode gates advertising
# only, never permission").





# ── Effective source-root resolution (L0006 §2.2·§4.1) ───────────────────────

# 0280 NR0003 §4-B: every reason the worktree is NOT used. The fallback itself is
# intended design; what was missing is any record of WHICH condition fired, so a
# "tests ran in main" report could never be confirmed or refuted after the fact.
# These constants are persisted (test_runs.source_root_kind) and rendered in TSR.
SRC_ROOT_WORKTREE = "worktree"
SRC_ROOT_NO_GROUP = "no_group_context"
SRC_ROOT_INTEGRATION_OFF = "git_integration_off"
SRC_ROOT_NO_STATE = "no_group_git_state"
SRC_ROOT_UNREGISTERED = "worktree_unregistered"
SRC_ROOT_NO_BRANCH = "state_branch_empty"
SRC_ROOT_NO_PROJECT_NAME = "project_name_missing"
SRC_ROOT_DIR_MISSING = "worktree_dir_missing"
# 0287 NR0004 §5: the directory is there but it is a corpse — an interrupted
# `worktree remove` took its `.git` link and most of its content with it. Distinct
# from *_dir_missing because the failure looks nothing alike in a TSR: the suite
# runs, finds a tree with its test files but not its modules, and reports import
# errors that read like product bugs.
SRC_ROOT_DIR_BROKEN = "worktree_dir_broken"
SRC_ROOT_ERROR = "resolution_error"


def effective_src_root_ex(
    project_id: Optional[str],
    group_id: Optional[str],
    state: Optional[dict] = None,
) -> tuple[Optional[Path], str]:
    """``effective_src_root`` plus the reason, and a log line on every fallback.

    Returns ``(worktree_path, "worktree")`` or ``(None, <SRC_ROOT_* reason>)``.
    0280 NR0003 §6-3: each fallback below used to be a bare ``return None`` with
    no log, no DB column and no UI trace, so a group that silently dropped to the
    base tree left zero evidence. Two of them are routine (integration off / no
    group context) and log at debug; the rest mean a worktree was *expected* and
    is not there — notably ``worktree_unregistered``, which is what a post-merge
    re-run hits (CLEANUP_STATUSES clears the flag) — so they log at warning.
    Never raises.

    0552 T0013 (0005-NR Set D): ``state`` lets a caller that ALREADY holds this
    group's ``group_git_state`` row hand it in instead of paying another
    ``SELECT * FROM group_git_state WHERE group_id = ?``. It is a pure read here —
    only ``worktree_registered`` / ``branch`` decide anything, and ``status`` is
    used solely in a fallback log line — so a supplied row cannot change the
    verdict, only who paid for the read. ``None`` means "not supplied" and keeps
    the original lookup verbatim, so every existing two-argument caller (and every
    test that patches this function with a two-parameter stub) is untouched.
    Reuse is the caller's own request/response assembly; nothing is cached here.
    """
    if not project_id or not group_id:
        return None, SRC_ROOT_NO_GROUP
    try:
        cfg = db_git.get_config(project_id)
        if cfg is None or not cfg.get("enabled"):
            _log.debug(
                "effective_src_root: base tree for %s (%s)",
                group_id,
                SRC_ROOT_INTEGRATION_OFF,
            )
            return None, SRC_ROOT_INTEGRATION_OFF
        if state is None:
            state = db_git.get_state(group_id)
        if state is None:
            _log.warning(
                "effective_src_root: base tree for %s (%s) — git integration is on "
                "but the group has no git state row",
                group_id,
                SRC_ROOT_NO_STATE,
            )
            return None, SRC_ROOT_NO_STATE
        if not state.get("worktree_registered"):
            _log.warning(
                "effective_src_root: base tree for %s (%s, status=%s) — the worktree "
                "was never registered or was released (merged/pushed cleanup)",
                group_id,
                SRC_ROOT_UNREGISTERED,
                state.get("status"),
            )
            return None, SRC_ROOT_UNREGISTERED
        branch = (state.get("branch") or "").strip()
        if not branch:
            _log.warning(
                "effective_src_root: base tree for %s (%s)", group_id, SRC_ROOT_NO_BRANCH
            )
            return None, SRC_ROOT_NO_BRANCH
        project_name = _project_name(project_id)
        if not project_name:
            _log.warning(
                "effective_src_root: base tree for %s (%s, project_id=%s)",
                group_id,
                SRC_ROOT_NO_PROJECT_NAME,
                project_id,
            )
            return None, SRC_ROOT_NO_PROJECT_NAME
        wt_path = src_root(project_name, branch)
        if not wt_path.is_dir():
            # E7/E13: ledger without directory → fallback
            _log.warning(
                "effective_src_root: base tree for %s (%s, expected=%s branch=%s)",
                group_id,
                SRC_ROOT_DIR_MISSING,
                wt_path,
                branch,
            )
            return None, SRC_ROOT_DIR_MISSING
        if not _worktree_link_ok(wt_path):
            # 0287 NR0004 §5: a directory is not a source tree. Without its `.git`
            # link the path is what an interrupted teardown left behind, and
            # returning it here is what silently pointed a suite at a half-erased
            # checkout while the TSR still labelled the root "worktree".
            _log.warning(
                "effective_src_root: base tree for %s (%s, path=%s branch=%s) — the "
                "directory has no .git link (leftover of an interrupted worktree "
                "teardown); it is NOT a usable source tree",
                group_id,
                SRC_ROOT_DIR_BROKEN,
                wt_path,
                branch,
            )
            return None, SRC_ROOT_DIR_BROKEN
        return wt_path.resolve(), SRC_ROOT_WORKTREE
    except Exception:
        _log.warning("effective_src_root failed for %s", group_id, exc_info=True)
        return None, SRC_ROOT_ERROR


def group_worktree_writable(
    project_id: Optional[str],
    group_id: Optional[str],
    state: Optional[dict] = None,
) -> bool:
    """True when *group_id* has a live worktree that may be written to.

    0327 T0004 (B0001 / NR0003 recommendation 1): the explorer used to treat "a group is
    selected" as "read-only", so create/upload stayed blocked even for the group
    the user is actively working in — while the server could already tell the two
    apart. This is that answer, in the one shape the client needs, so the UI stops
    guessing. Groups with no worktree (finalized, disposed, never provisioned)
    remain fully read-only, exactly as before (recommendation 5).

    0552 T0013: ``state`` is passed straight through to ``effective_src_root_ex``
    — see its docstring for what an already-read ledger row does and does not
    change. Positional so a stub of the shape ``lambda *args: True`` keeps working.
    """
    return effective_src_root_ex(project_id, group_id, state)[0] is not None


def effective_src_root(project_id: Optional[str], group_id: Optional[str]) -> Optional[Path]:
    """Group worktree path when it must be used, else None (= caller falls back).

    Fallback-first (L0006 §2.2): missing config, disabled integration, missing
    ledger entry, or a vanished directory all yield None so the caller resolves
    the ordinary project-branch folder. Never raises. Thin wrapper over
    ``effective_src_root_ex`` — callers that need to record WHY the worktree was
    skipped use that one directly (0280 T0005).
    """
    return effective_src_root_ex(project_id, group_id)[0]


# ── Finalize state (P0005 §5-1 / L0006 §3) ───────────────────────────────────

from .git.finalize import (
    ACTION_VALUES,
    DISCARDED_STATUS,
    FINALIZE_AUX_CHOICES,
    FINALIZE_MAIN_CHOICES,
    _auto_discard_group,
    _decide_pending_transition,
    _finalize_context,
    _finalize_result,
    _group_ac_doc_id,
    _group_ac_doc_ids,
    _group_has_changes,
    _resolve_pending_noop,
    group_finalize_is_noop,
    NOOP_CONVERGEABLE_STATUSES,
    _group_root_wf_done,
    _groups_root_wf_done,
    _tracked_merge_blockers,
    _untracked_merge_blockers,
    finalize,
    get_finalize_state,
    group_update_untracked_recover,
    manual_push,
    precheck_approve_git_action,
    raise_if_git_session_blocks_reopen,
    realize_wf_done_transition,
    reopen_group_git,
    run_approve_git_action,
    unmerge,
    update_from_base,
)












# ── No-work divergence gating (flowgate.default.0199 B0001) ──────────────────
#
# The none→awaiting_choice transition (three sites: realize_wf_done_transition,
# get_finalize_state lazy, project_git_status aggregation) used to fire on
# `wf_done` + `worktree_registered` ALONE, never checking whether the group's
# work branch actually diverged from base. A pure R/CH/AC inquiry (no T = no code
# change) thus landed in `awaiting_choice`, and the only exits were merge (empty
# `--no-ff` commit + base push) or push (empty branch leaked to origin) — hence
# the "forced git finalize with nothing to finalize" bug. These helpers let each
# transition site prove emptiness first and, when proven, auto-discard the slot
# with no merge and no push instead.












# ── Group branch file explorer: checkout-free ref/tree/blob (0186 L0006 §2) ──
#
# Pure read layer. The group worktree shares base_root/.git with the base
# checkout (git worktree add), so a group branch's ref/tree/blob objects can be
# served straight from the shared object store WITHOUT switching a checkout.
# These functions acquire no git_project_lock, never provision a branch, and
# write no DB row — a missing / disabled / unregistered group is a 409.

_REF_PIN_RE = re.compile(r"^[0-9a-f]{40}$")


def resolve_group_ref(project_id: str, group_id: str) -> tuple[Path, str, str]:
    """(base_root, branch, commit) for a group branch. Pure read (L0006 §2.1)."""
    cfg = db_git.get_config(project_id)
    if cfg is None or not cfg.get("enabled"):
        raise GitServiceError(
            409, "invalid_state", f"Git integration is not active for group '{group_id}'"
        )
    state = db_git.get_state(group_id)
    if state is None or not state.get("worktree_registered"):
        raise GitServiceError(
            409, "invalid_state", f"Git integration is not active for group '{group_id}'"
        )
    # Guard against a project_id path param that does not own this group: the
    # config was looked up by project_id but the branch by group_id, so a mismatch
    # would resolve the wrong repository.
    if (state.get("project_id") or _project_of_group(group_id)) != project_id:
        raise GitServiceError(
            409, "invalid_state", f"group '{group_id}' does not belong to project '{project_id}'"
        )
    branch = state.get("branch")
    project_name = _project_name(project_id)
    if not project_name:
        raise GitServiceError(404, "not_found", f"project '{project_id}' not found")
    base_branch = (cfg.get("base_branch") or "main").strip() or "main"
    base_root = src_root(project_name, base_branch)
    if not (base_root / ".git").exists():
        raise GitServiceError(409, "invalid_state", "base checkout is not provisioned")
    if not git_available():
        raise GitServiceError(500, "git_unavailable", "git binary not found on server")
    proc = _run_git(
        ["rev-parse", "--verify", "--quiet", f"refs/heads/{branch}"],
        cwd=base_root, timeout=GIT_READ_TIMEOUT_SEC,
    )
    commit = (proc.stdout or "").strip()
    if proc.returncode != 0 or not commit:
        raise GitServiceError(409, "invalid_state", f"branch '{branch}' not found in repository")
    return base_root, branch, commit


def _tree_sort_key(name: str, is_dir: bool) -> tuple:
    # Reuse the exact file-tree ordering (folders-first, natural, case-insensitive)
    # so the group explorer matches the base-branch tree. Lazy import avoids a
    # module-load cycle with process_service.
    from modules.flow_gate.process_service import _file_tree_sort_key
    return _file_tree_sort_key(name, is_dir)


def _build_tree_nodes(files: list[str], dirs: Sequence[str] = ()) -> list[dict]:
    """FileNode list (same contract as process_service.get_file_tree) from a flat
    list of visible blob paths.

    0327 T0004 (B0001): *dirs* carries directory paths that hold no file at all.
    Git has no way to express an empty directory, so a folder just created in a
    group worktree is invisible to every file-based listing — the new folder the
    user asked for would silently not appear. Those paths are registered with
    every segment forced to folder so the tree shows them.
    """
    children: dict[str, dict[str, bool]] = {"": {}}

    def register(path: str, leaf_is_dir: bool) -> None:
        segs = [seg for seg in path.split("/") if seg]
        for i, name in enumerate(segs):
            parent = "/".join(segs[:i])
            is_dir = leaf_is_dir or i < len(segs) - 1
            children.setdefault(parent, {})
            prev = children[parent].get(name)
            children[parent][name] = bool(prev) or is_dir
            if is_dir:
                children.setdefault("/".join(segs[: i + 1]), {})

    for path in files:
        register(path, False)
    for path in dirs:
        register(path, True)
    nodes: list[dict] = []
    counter = [0]

    def walk(dirpath: str, parent_id: Optional[str]) -> None:
        entries = sorted(
            children.get(dirpath, {}).items(),
            key=lambda kv: _tree_sort_key(kv[0], kv[1]),
        )
        for name, is_dir in entries:
            counter[0] += 1
            cur = str(counter[0])
            full = f"{dirpath}/{name}" if dirpath else name
            if is_dir:
                nodes.append({
                    "id": cur, "parent_id": parent_id, "type": "folder",
                    "name": name, "label": name, "path": full,
                    "permissions": ["read"], "children": [],
                })
                walk(full, cur)
            else:
                nodes.append({
                    "id": cur, "parent_id": parent_id, "type": "file",
                    "name": name, "label": name, "path": full,
                    "permissions": ["read", "download"],
                })

    walk("", None)
    return nodes


def _is_hidden_source_path(path: str) -> bool:
    """Group-explorer exposure rule (shared by tree/changes/blob).

    0382 NR0003 proposal 3: this used to be a *second*, hand-rolled rule that disagreed
    with the submission check — it hid ``server/.test-tmp-0313/...`` while
    ``tr_scope_service`` demanded those same 261 paths be reported. The shared rule
    in ``path_exclusion_rules`` is now the base, so a path the explorer hides as
    "tool debris" is one the submission check also drops.

    The one addition on top of the shared rule is a nested dotfile
    (``server/.env.local``): the shared rule deliberately keeps those reportable so a
    genuinely edited ``client/src/.eslintrc.json`` is still cross-checked, but the
    explorer must not serve a secret-shaped file through the blob/write endpoints.
    That direction is safe — it never hides debris the check would then demand.
    """
    if path_exclusion_rules.is_excluded_path(path):
        return True
    return path.split("/")[-1].startswith(".")


def _group_worktree_path(project_id: str, group_id: str, branch: str) -> Optional[Path]:
    """Absolute path of a group's live worktree, or None when it is unavailable.

    NR0003: the checkout-free explorer reads committed git objects only, so a new
    file the worker has not committed is invisible until finalize. The tree/changes/
    blob readers use this worktree to surface those untracked files. A finalized or
    not-yet-provisioned group has no worktree — a normal, non-fatal state (returns
    None), so the committed view still renders on its own."""
    state = db_git.get_state(group_id) or {}
    project_name = _project_name(project_id)
    if not project_name:
        return None
    wt_path = src_root(project_name, state.get("branch") or branch)
    return wt_path if wt_path.exists() else None


def _group_untracked_visible(wt_path: Path) -> list[str]:
    """Exposed untracked (never-committed) paths in a group worktree, sorted.

    ``git diff`` / ``ls-tree`` never report untracked files (NR0003 §3.1·§3.2), so
    these are collected with ``ls-files --others --exclude-standard`` and filtered by
    the same exposure rule as the committed tree. git emits '/'-separated paths."""
    proc = _run_git(
        ["ls-files", "--others", "--exclude-standard", "-z"],
        cwd=wt_path, timeout=GIT_READ_TIMEOUT_SEC,
    )
    if proc.returncode != 0:
        raise GitServiceError(
            500, "git_error", _one_line_subject(proc.stderr) or "ls-files failed"
        )
    out: list[str] = []
    for path in (proc.stdout or "").split("\0"):
        if path and not _is_hidden_source_path(path):
            out.append(path)
    return sorted(out)


def _group_empty_dirs_visible(wt_path: Path) -> list[str]:
    """Exposed untracked directories that contain no file anywhere beneath them.

    0327 T0004 (B0001): creating a folder in a group worktree used to leave no
    trace in the explorer — git tracks files, so an empty directory is reported by
    no file listing and the new folder simply never appeared. ``ls-files --others
    --directory`` names the shallowest untracked directory; the ones that do hold
    files are already covered by ``_group_untracked_visible`` (their file paths
    imply the folders), so only the file-less ones are expanded here, together
    with their equally empty subdirectories.
    """
    proc = _run_git(
        ["ls-files", "--others", "--exclude-standard", "--directory", "-z"],
        cwd=wt_path, timeout=GIT_READ_TIMEOUT_SEC,
    )
    if proc.returncode != 0:
        raise GitServiceError(
            500, "git_error", _one_line_subject(proc.stderr) or "ls-files failed"
        )
    out: list[str] = []
    for entry in (proc.stdout or "").split("\0"):
        rel = entry.rstrip("/")
        # Only collapsed directory entries ('dir/') are of interest; plain files
        # come from the untracked-file scan.
        if not entry.endswith("/") or not rel or _is_hidden_source_path(rel):
            continue
        root = wt_path / rel
        if not root.is_dir():
            continue
        if any(p.is_file() for p in root.rglob("*")):
            continue  # holds files → its untracked file paths already imply it
        out.append(rel)
        for sub in root.rglob("*"):
            if not sub.is_dir():
                continue
            sub_rel = sub.relative_to(wt_path).as_posix()
            if not _is_hidden_source_path(sub_rel):
                out.append(sub_rel)
    return sorted(set(out))


def _group_empty_dirs_safe(project_id: str, group_id: str, branch: str) -> list[str]:
    """``_group_empty_dirs_visible`` for a resolved group, degrading to ``[]``.

    Same contract as ``_group_untracked_safe``: a supplemental channel must never
    break the committed tree read that worked before it existed."""
    try:
        wt_path = _group_worktree_path(project_id, group_id, branch)
        if wt_path is None:
            return []
        return _group_empty_dirs_visible(wt_path)
    except Exception:  # noqa: BLE001 — supplemental channel, never fatal
        _log.warning("group empty-dir scan failed for %s", group_id, exc_info=True)
        return []


def _group_untracked_safe(project_id: str, group_id: str, branch: str) -> list[str]:
    """``_group_untracked_visible`` for a resolved group, degrading to ``[]`` on any
    failure. Untracked files SUPPLEMENT the committed view: a worktree hiccup must
    never break the tree/changes read that worked before this channel existed."""
    try:
        wt_path = _group_worktree_path(project_id, group_id, branch)
        if wt_path is None:
            return []
        return _group_untracked_visible(wt_path)
    except Exception:  # noqa: BLE001 — supplemental channel, never fatal
        _log.warning("group untracked scan failed for %s", group_id, exc_info=True)
        return []


# 0325 T0006: per-file +/- line counts for the changes list. `git diff --numstat`
# already reports them for tracked paths; an untracked file has no diff entry at
# all, so its "added" count is read off disk. Both channels degrade to None (=
# "unknown", e.g. binary) rather than 0, so the client never shows a made-up 0.
_UNTRACKED_STAT_MAX_BYTES = 1_000_000


def _untracked_added_lines(wt_path: Path, rel_path: str) -> Optional[int]:
    """Line count of an untracked file, or None when it is binary/oversized/unreadable."""
    try:
        target = wt_path / rel_path
        if not target.is_file() or target.stat().st_size > _UNTRACKED_STAT_MAX_BYTES:
            return None
        data = target.read_bytes()
    except OSError:
        return None
    if b"\0" in data:  # same binary heuristic git uses for --numstat's "-"
        return None
    if not data:
        return 0
    return data.count(b"\n") + (0 if data.endswith(b"\n") else 1)


def _diff_line_stats(wt_path: Path, merge_base: str) -> dict[str, tuple[Optional[int], Optional[int]]]:
    """path -> (insertions, deletions) from ``git diff --numstat``.

    Supplemental like the untracked channel: a failure here must not break the
    changes list, so an unusable run yields an empty map and every file falls
    back to None.
    """
    proc = _run_git(
        ["diff", "--numstat", "--no-renames", "-z", merge_base, "--"],
        cwd=wt_path, timeout=GIT_READ_TIMEOUT_SEC,
    )
    if proc.returncode != 0:
        _log.warning("numstat failed in %s: %s", wt_path, _one_line_subject(proc.stderr))
        return {}
    stats: dict[str, tuple[Optional[int], Optional[int]]] = {}
    # -z record shape (renames excluded): "<added>\t<deleted>\t<path>\0".
    # Binary files report "-" for both counts.
    for record in (proc.stdout or "").split("\0"):
        if not record:
            continue
        added, sep, rest = record.partition("\t")
        deleted, sep2, path = rest.partition("\t")
        if not sep or not sep2 or not path:
            continue
        stats[path] = (
            int(added) if added.isdigit() else None,
            int(deleted) if deleted.isdigit() else None,
        )
    return stats


def read_group_tree(project_id: str, group_id: str) -> dict:
    """checkout-free recursive tree of a group branch's HEAD commit (L0006 §2.2)."""
    base_root, branch, commit = resolve_group_ref(project_id, group_id)
    proc = _run_git(
        ["ls-tree", "-r", "-z", commit], cwd=base_root, timeout=GIT_READ_TIMEOUT_SEC
    )
    if proc.returncode != 0:
        raise GitServiceError(500, "git_error", _one_line_subject(proc.stderr) or "ls-tree failed")
    visible_files: list[str] = []
    for record in (proc.stdout or "").split("\0"):
        if not record:
            continue
        meta, _, path = record.partition("\t")
        if not path:
            continue
        parts = meta.split()
        # entry: "<mode> <type> <sha>"; only blobs are files.
        if len(parts) < 2 or parts[1] != "blob":
            continue
        segments = path.split("/")
        # Same exposure rule as the base-branch tree: hide dotfiles and *.db.
        if any(seg.startswith(".") for seg in segments) or segments[-1].lower().endswith(".db"):
            continue
        visible_files.append(path)
    untracked = _group_untracked_safe(project_id, group_id, branch)
    # 0327 T0004 (B0001): folders created in the group worktree that hold no file
    # yet — they exist on disk but in no file listing, so they need their own channel.
    empty_dirs = _group_empty_dirs_safe(project_id, group_id, branch)
    # _build_tree_nodes dedups by name per directory, so committed + untracked paths
    # can be concatenated directly. worktree_untracked is ALSO returned as a separate
    # channel (NR0003 recommendation 1): the client caches the tree by commit, but untracked
    # files change without advancing the commit, so this list must not be cached there.
    nodes = _build_tree_nodes(visible_files + untracked, empty_dirs)
    return {"ok": True, "data": {
        "group_id": group_id, "branch": branch, "commit": commit, "nodes": nodes,
        "worktree_untracked": untracked,
        "worktree_untracked_dirs": empty_dirs,
    }}


def _group_diff_context(project_id: str, group_id: str) -> tuple[str, str, str, Path, str]:
    """(base_branch, branch, commit, worktree_path, merge_base) for group-vs-base diffs.

    Shared by the changes list and the per-file diff reader (0325 TR0007 rev1) so both
    compare against the SAME merge-base — otherwise the summary and the diff a reviewer
    opens from it could disagree about what this group changed.
    """
    base_root, branch, commit = resolve_group_ref(project_id, group_id)
    cfg = db_git.get_config(project_id) or {}
    state = db_git.get_state(group_id) or {}
    project_name = _project_name(project_id)
    if not project_name:
        raise GitServiceError(404, "not_found", f"project '{project_id}' not found")
    wt_path = src_root(project_name, state.get("branch") or branch)
    if not wt_path.exists():
        raise GitServiceError(409, "invalid_state", "group worktree is not available")

    base_branch = (cfg.get("base_branch") or "main").strip() or "main"
    merge_proc = _run_git(
        ["merge-base", f"refs/heads/{base_branch}", commit],
        cwd=base_root, timeout=GIT_READ_TIMEOUT_SEC,
    )
    merge_base = (merge_proc.stdout or "").strip()
    if merge_proc.returncode != 0 or not merge_base:
        raise GitServiceError(
            500, "git_error", _one_line_subject(merge_proc.stderr) or "merge-base failed"
        )
    return base_branch, branch, commit, wt_path, merge_base


def read_group_changes(project_id: str, group_id: str) -> dict:
    """Tracked paths changed from the group's base commit through its worktree."""
    base_branch, branch, commit, wt_path, merge_base = _group_diff_context(project_id, group_id)

    diff_proc = _run_git(
        ["diff", "--name-status", "--no-renames", "-z", merge_base, "--"],
        cwd=wt_path, timeout=GIT_READ_TIMEOUT_SEC,
    )
    if diff_proc.returncode != 0:
        raise GitServiceError(
            500, "git_error", _one_line_subject(diff_proc.stderr) or "git diff failed"
        )

    # 0325 T0006: the final-approval sidebar summarizes "how big is this change",
    # which --name-status cannot answer. A second read-only pass over the same
    # merge-base supplies the per-file +/- counts.
    line_stats = _diff_line_stats(wt_path, merge_base)

    fields = (diff_proc.stdout or "").split("\0")
    changes: list[dict] = []
    # 0382 NR0003 proposal 3: hide the debris but **never pretend it does not exist**. The whole
    # incident was 261 files being approved and merged without appearing on any screen, so what
    # is filtered out is counted on a separate channel and the screen always shows a "N tool-left files" line.
    tool_artifacts: list[str] = []
    for index in range(0, len(fields) - 1, 2):
        status, path = fields[index], fields[index + 1]
        if not status or not path:
            continue
        if _is_hidden_source_path(path):
            if path_exclusion_rules.is_excluded_path(path):
                tool_artifacts.append(path)
            continue
        insertions, deletions = line_stats.get(path, (None, None))
        changes.append({
            "path": path, "status": status[:1],
            "insertions": insertions, "deletions": deletions,
        })
    # NR0003 recommendation 2: git diff never lists untracked files, so a brand-new file would be
    # absent from the changes list entirely — the exact "edits show up but new files do not"
    # asymmetry B0001 reports. Surface each with "?" (git porcelain's untracked marker).
    existing = {change["path"] for change in changes}
    # Untracked debris never reaches _group_untracked_safe (it filters by the same
    # exposure rule), so the artifact channel reads the raw list once more.
    tool_artifacts.extend(
        path for path in _worktree_untracked_paths(wt_path)
        if path_exclusion_rules.is_excluded_path(path)
    )
    for path in _group_untracked_safe(project_id, group_id, branch):
        if path not in existing:
            # A never-added file deletes nothing, so 0 here is a fact, not a guess.
            changes.append({
                "path": path, "status": "?",
                "insertions": _untracked_added_lines(wt_path, path), "deletions": 0,
            })
    return {"ok": True, "data": {
        "group_id": group_id, "branch": branch, "commit": commit,
        # 0325 TR0007 rev1: the changes viewer titles itself "<branch> ↔ <base>", and
        # the base branch is a project setting the client had no other way to read.
        "base_branch": base_branch, "changes": changes,
        # 0382 proposal 3: the list may be collapsed but the count is always visible — nothing is pretended away.
        "tool_artifacts": sorted(set(tool_artifacts)),
    }}


def collect_scope_changes(project_id: str, group_id: str) -> dict:
    """Every path this group actually changed, seen from its OWN worktree (0299 D0004 §3.3).

    Deliberately NOT ``read_group_changes``. That one resolves the tree through
    ``src_root(project_name, branch)`` and only checks that the directory exists,
    and it looks at committed/tracked changes alone. For the work-scope check both gaps are
    fatal: a group whose worktree is missing must NOT silently be measured against
    the base checkout (that is the very accident this feature exists to catch), and
    a brand-new file that was never ``git add``-ed is the most ordinary shape of
    real work there is — missing it would produce a bogus TRV-003 on an honest
    report. So this resolves strictly via ``effective_src_root_ex`` (no main
    fallback) and unions three sources:

      * merge-base..worktree diff — committed + staged + unstaged tracked changes
      * ``ls-files --others`` — untracked new files
      * renames resolved to the NEW path only (D0004 §3.2: "for a rename, record only
        the path after the rename"), hence ``-M`` instead of ``--no-renames``

    Returns ``{"available": bool, "reason": str, "worktree": str|None,
    "branch": str|None, "paths": [str], "entries": [dict]}``. ``entries`` carries one
    manifest row per path in ``paths``: ``{"path", "status", "old_path"}``, where
    ``status`` is one of ``A``/``M``/``D``/``R`` (falling back to ``M`` — best-effort,
    still content-changed — for a git status letter this check does not otherwise
    recognise) and ``old_path`` is set only for a rename. A path is never omitted from
    ``entries`` just because its status could not be classified precisely (0493 T0005 —
    reviewers need per-file status, not just a bare path list). Never raises: an
    unavailable worktree or a failing git call is a *result* (``available=False`` +
    reason), because the caller must turn that into TRV-006 rather than a 500 on
    someone's TR. Exclusion rules are NOT applied here — tr_scope_service owns them so
    the same filter runs over the reported list too.
    """
    result: dict = {
        "available": False, "reason": SRC_ROOT_ERROR,
        "worktree": None, "branch": None, "paths": [], "entries": [],
    }
    wt_path, reason = effective_src_root_ex(project_id, group_id)
    result["reason"] = reason
    if wt_path is None:
        return result
    result["worktree"] = str(wt_path)
    try:
        state = db_git.get_state(group_id) or {}
        result["branch"] = (state.get("branch") or "").strip() or None
        cfg = db_git.get_config(project_id) or {}
        base_branch = (cfg.get("base_branch") or "main").strip() or "main"

        entries_by_path: dict[str, dict] = {}
        merge_proc = _run_git(
            ["merge-base", f"refs/heads/{base_branch}", "HEAD"],
            cwd=wt_path, timeout=GIT_READ_TIMEOUT_SEC,
        )
        merge_base = (merge_proc.stdout or "").strip()
        if merge_proc.returncode == 0 and merge_base:
            diff_proc = _run_git(
                ["diff", "--name-status", "-M", "-z", merge_base, "--"],
                cwd=wt_path, timeout=GIT_READ_TIMEOUT_SEC,
            )
            if diff_proc.returncode != 0:
                result["reason"] = SRC_ROOT_ERROR
                return result
            for entry in _parse_name_status_manifest(diff_proc.stdout or ""):
                entries_by_path[entry["path"]] = entry
        else:
            # No merge base (unrelated histories / missing base branch) — the
            # committed half cannot be computed. Working-tree state alone would be
            # a partial answer that reads as "you reported files you never changed",
            # so refuse the whole measurement instead of half of it.
            result["reason"] = SRC_ROOT_ERROR
            return result

        others = _run_git(
            ["ls-files", "--others", "--exclude-standard", "-z"],
            cwd=wt_path, timeout=GIT_READ_TIMEOUT_SEC,
        )
        if others.returncode != 0:
            result["reason"] = SRC_ROOT_ERROR
            return result
        for path in (others.stdout or "").split("\0"):
            if path and path not in entries_by_path:
                # A never-added file deletes nothing and was never renamed from anywhere.
                entries_by_path[path] = {"path": path, "status": "A", "old_path": None}

        result["available"] = True
        result["reason"] = SRC_ROOT_WORKTREE
        result["paths"] = sorted(entries_by_path)
        result["entries"] = [entries_by_path[path] for path in sorted(entries_by_path)]
        return result
    except Exception:  # noqa: BLE001 — a verification helper must never 500 a TR
        _log.warning("collect_scope_changes failed for %s", group_id, exc_info=True)
        result["available"] = False
        result["reason"] = SRC_ROOT_ERROR
        return result

from .git.refs import (
    UNTRACKED_LIST_MAX,
    _ahead_of_base,
    _base_ahead_behind,
    _cat_file_blob_head,
    _cat_file_size,
    _commits_present,
    _dirty,
    _dirty_files,
    _full_sha_matches,
    _ignored_paths,
    _local_commit_count,
    _ls_tree_entry,
    _merge_in_progress,
    _normalize_git_status,
    _parse_name_status_manifest,
    _parse_name_status_z,
    _query_remote_ref,
    _ref_exists,
    _remote_base_missing,
    _rev_parse,
    _short_head,
    _unmerged_paths,
    _unpushed_commits,
    _untracked_files,
    _validate_blob_path,
    _worktree_untracked_paths,
    probe_worktree_pending_changes,
    _worktree_untracked_summary_for_path,
    worktree_untracked_summary,
)


def _read_group_untracked_blob(
    project_id: str, group_id: str, branch: str, path: str
) -> Optional[dict]:
    """Read an untracked worktree file for the group explorer, or None when the path
    is not an exposed untracked file (the caller then 404s as before).

    NR0003 recommendation 3: git objects hold committed content only, so a not-yet-committed file
    can be read solely off the worktree disk. The read is gated three ways — the
    exposure filter, git's own untracked list, and a resolved-path containment check
    against the worktree root — so it can never serve a tracked, hidden, or out-of-tree
    file. Binary sniff / truncation mirror read_group_blob. The response carries
    commit=None + untracked=True: it has no point-in-time, so it must not be pinned."""
    wt_path = _group_worktree_path(project_id, group_id, branch)
    if wt_path is None:
        return None
    normalized = path.replace("\\", "/")
    if _is_hidden_source_path(normalized):
        return None
    try:
        if normalized not in set(_group_untracked_visible(wt_path)):
            return None
    except GitServiceError:
        return None
    try:
        wt_resolved = wt_path.resolve()
        file_path = (wt_resolved / normalized).resolve()
        file_path.relative_to(wt_resolved)
    except (ValueError, OSError):
        return None
    if not file_path.is_file():
        return None
    try:
        size = file_path.stat().st_size
        with open(file_path, "rb") as handle:
            head = handle.read(BLOB_MAX_RETURN_BYTES)
    except OSError:
        return None
    if b"\x00" in head[:BLOB_BINARY_SNIFF_BYTES]:
        return {"ok": True, "data": {
            "group_id": group_id, "branch": branch, "commit": None, "path": path,
            "size": size, "binary": True, "truncated": False,
            "encoding": None, "content": None, "untracked": True,
        }}
    truncated = size > BLOB_MAX_RETURN_BYTES
    body = head[:BLOB_MAX_RETURN_BYTES] if truncated else head[:size]
    content = body.decode("utf-8", errors="replace")
    return {"ok": True, "data": {
        "group_id": group_id, "branch": branch, "commit": None, "path": path,
        "size": size, "binary": False, "truncated": truncated,
        "encoding": "utf-8", "content": content, "untracked": True,
    }}


def read_group_blob(
    project_id: str, group_id: str, path: str, ref: Optional[str] = None
) -> dict:
    """checkout-free single-file read from a group branch (L0006 §2.3)."""
    _validate_blob_path(path)
    base_root, branch, head_commit = resolve_group_ref(project_id, group_id)
    commit = head_commit
    if ref:
        if not _REF_PIN_RE.match(ref):
            raise GitServiceError(400, "invalid_ref", "ref must be a full 40-hex commit sha")
        tproc = _run_git(["cat-file", "-t", ref], cwd=base_root, timeout=GIT_READ_TIMEOUT_SEC)
        if tproc.returncode != 0 or (tproc.stdout or "").strip() != "commit":
            raise GitServiceError(404, "not_found", f"commit '{ref}' not found")
        commit = ref
    entry = _ls_tree_entry(base_root, commit, path)
    if entry is None or entry[0] != "blob":
        # NR0003 recommendation 3: the path may be a new file that lives only in the group
        # worktree (no commit object yet). Fall back to reading it off disk before
        # giving up — this is what makes a just-created file openable from the tree.
        fallback = _read_group_untracked_blob(project_id, group_id, branch, path)
        if fallback is not None:
            return fallback
        raise GitServiceError(404, "not_found", f"path '{path}' not found in commit {commit}")
    sha = entry[1]
    size = _cat_file_size(base_root, sha)
    head = _cat_file_blob_head(base_root, sha, BLOB_MAX_RETURN_BYTES)
    if b"\x00" in head[:BLOB_BINARY_SNIFF_BYTES]:
        return {"ok": True, "data": {
            "group_id": group_id, "branch": branch, "commit": commit, "path": path,
            "size": size, "binary": True, "truncated": False,
            "encoding": None, "content": None,
        }}
    truncated = size > BLOB_MAX_RETURN_BYTES
    body = head[:BLOB_MAX_RETURN_BYTES] if truncated else head[:size]
    content = body.decode("utf-8", errors="replace")
    return {"ok": True, "data": {
        "group_id": group_id, "branch": branch, "commit": commit, "path": path,
        "size": size, "binary": False, "truncated": truncated,
        "encoding": "utf-8", "content": content,
    }}


# ── Single-file change view (0326 R0001 / NR0005 §4) ─────────────────────────
#
# The backend half of R0001's complaint that you could only see "a file changed" and never
# "where and how". Option (b) of NR0005 §4 was chosen: the server builds no patch text, only
# serves one path's old/new contents, and the line diff is computed by the engine the client
# already has (useConflictChunks.buildChunkSideDiff). That overlaps cleanly with the existing
# blob reader (read_group_blob) rather than running `git diff` and parsing a patch, and
# switching between unified and split views needs no server round trip.
#
# There are two variants for the reason §4 gives: the base checkout reads the working tree on
# disk, while the group-branch view is checkout-free and must read from git objects.

def _diff_side_payload(head: bytes, size: int) -> dict:
    """One side of a diff from raw bytes. Binary sniff / 1 MiB cap mirror read_group_blob:
    a diff of a binary or oversize file must degrade to a flag, never to a wall of
    replacement characters."""
    if b"\x00" in head[:BLOB_BINARY_SNIFF_BYTES]:
        return {"exists": True, "binary": True, "truncated": False, "size": size, "content": None}
    truncated = size > BLOB_MAX_RETURN_BYTES
    body = head[:BLOB_MAX_RETURN_BYTES] if truncated else head[:size]
    return {
        "exists": True, "binary": False, "truncated": truncated, "size": size,
        "content": body.decode("utf-8", errors="replace"),
    }


def _diff_side_missing() -> dict:
    """The absent side of an add (no old) or a delete (no new)."""
    return {"exists": False, "binary": False, "truncated": False, "size": 0, "content": None}


def _diff_side_from_commit(base_root: Path, commit: Optional[str], path: str) -> dict:
    """Blob content of ``path`` in ``commit``; missing path/commit → the absent side."""
    if not commit:
        return _diff_side_missing()
    entry = _ls_tree_entry(base_root, commit, path)
    if entry is None or entry[0] != "blob":
        return _diff_side_missing()
    sha = entry[1]
    size = _cat_file_size(base_root, sha)
    return _diff_side_payload(_cat_file_blob_head(base_root, sha, BLOB_MAX_RETURN_BYTES), size)


def _diff_side_from_disk(root: Path, path: str) -> dict:
    """Working-tree content of ``path`` under ``root``, containment-checked.

    Mirrors _read_group_untracked_blob's resolve+relative_to guard so a symlink or a
    crafted path can never read outside the checkout; anything unreadable is reported
    as the absent side (i.e. "deleted"), never as a 500."""
    try:
        root_resolved = root.resolve()
        file_path = (root_resolved / path).resolve()
        file_path.relative_to(root_resolved)
    except (ValueError, OSError):
        return _diff_side_missing()
    if not file_path.is_file():
        return _diff_side_missing()
    try:
        size = file_path.stat().st_size
        with open(file_path, "rb") as handle:
            head = handle.read(BLOB_MAX_RETURN_BYTES)
    except OSError:
        return _diff_side_missing()
    return _diff_side_payload(head, size)


def _diff_status(old: dict, new: dict, path: str) -> str:
    """git --name-status letter for the pair. Neither side existing is a 404: the
    caller asked about a path that is neither in the old snapshot nor on disk."""
    if not old["exists"] and not new["exists"]:
        raise GitServiceError(404, "not_found", f"path '{path}' not found")
    if not old["exists"]:
        return "A"
    if not new["exists"]:
        return "D"
    return "M"


def read_base_file_diff(project_id: str, path: str) -> dict:
    """old (HEAD blob) / new (working tree) content of one base-checkout file.

    The base file explorer's dirty/untracked markers come from ``project_git_status``
    (HEAD vs the checkout on disk), so the diff must be measured over exactly that
    same pair — otherwise a file the tree marks as changed could open with an empty
    diff."""
    _validate_blob_path(path)
    normalized = path.replace("\\", "/")
    cfg = _require_enabled_config(project_id)
    project_name = _project_name(project_id)
    if not project_name:
        raise GitServiceError(404, "not_found", f"project '{project_id}' not found")
    if _is_hidden_source_path(normalized):
        # Same exposure rule as the file tree (dotfiles / *.db are never listed);
        # a path the tree hides must not become readable through the diff view.
        raise GitServiceError(404, "not_found", f"path '{path}' not found")
    base_branch = (cfg.get("base_branch") or "main").strip() or "main"
    base_root = src_root(project_name, base_branch)
    if not (base_root / ".git").exists():
        raise GitServiceError(409, "invalid_state", "base checkout is not provisioned")
    if not git_available():
        raise GitServiceError(500, "git_unavailable", "git binary not found on server")
    head_proc = _run_git(
        ["rev-parse", "--verify", "--quiet", "HEAD"],
        cwd=base_root, timeout=GIT_READ_TIMEOUT_SEC,
    )
    # An empty repository (no HEAD yet) is not an error here — every file simply
    # reads as added.
    head_commit = (head_proc.stdout or "").strip() if head_proc.returncode == 0 else ""
    old = _diff_side_from_commit(base_root, head_commit or None, normalized)
    new = _diff_side_from_disk(base_root, normalized)
    return {"ok": True, "data": {
        "group_id": None, "branch": base_branch, "base_branch": base_branch,
        "commit": head_commit or None, "path": path,
        "status": _diff_status(old, new, path), "old": old, "new": new,
    }}


def read_group_file_diff(
    project_id: str, group_id: str, path: str, ref: Optional[str] = None
) -> dict:
    """old (merge-base blob) / new (group worktree, else branch commit) content.

    The old side is the merge base with the configured base branch — the same
    reference ``read_group_changes`` diffs against, so the tree's changed markers and
    this view can never disagree. The new side prefers the live worktree file (which
    is what ``read_group_changes`` measures, so uncommitted work shows up) and falls
    back to the branch commit's blob for a finalized group whose worktree is gone.
    ``ref`` pins the commit exactly as ``read_group_blob`` does."""
    _validate_blob_path(path)
    normalized = path.replace("\\", "/")
    if _is_hidden_source_path(normalized):
        raise GitServiceError(404, "not_found", f"path '{path}' not found")
    base_root, branch, head_commit = resolve_group_ref(project_id, group_id)
    commit = head_commit
    if ref:
        if not _REF_PIN_RE.match(ref):
            raise GitServiceError(400, "invalid_ref", "ref must be a full 40-hex commit sha")
        tproc = _run_git(["cat-file", "-t", ref], cwd=base_root, timeout=GIT_READ_TIMEOUT_SEC)
        if tproc.returncode != 0 or (tproc.stdout or "").strip() != "commit":
            raise GitServiceError(404, "not_found", f"commit '{ref}' not found")
        commit = ref

    cfg = db_git.get_config(project_id) or {}
    base_branch = (cfg.get("base_branch") or "main").strip() or "main"
    merge_proc = _run_git(
        ["merge-base", f"refs/heads/{base_branch}", commit],
        cwd=base_root, timeout=GIT_READ_TIMEOUT_SEC,
    )
    merge_base = (merge_proc.stdout or "").strip()
    if merge_proc.returncode != 0 or not merge_base:
        raise GitServiceError(
            500, "git_error", _one_line_subject(merge_proc.stderr) or "merge-base failed"
        )

    old = _diff_side_from_commit(base_root, merge_base, normalized)
    wt_path = _group_worktree_path(project_id, group_id, branch)
    new = _diff_side_from_disk(wt_path, normalized) if wt_path is not None else _diff_side_missing()
    if not new["exists"] and wt_path is None:
        new = _diff_side_from_commit(base_root, commit, normalized)
    return {"ok": True, "data": {
        "group_id": group_id, "branch": branch, "base_branch": base_branch,
        "commit": commit, "merge_base": merge_base, "path": path,
        "status": _diff_status(old, new, path), "old": old, "new": new,
    }}


# ── Finalize execution (P0005 §5 / L0006 §2.6·§4.2) ──────────────────────────



# ── TR commit cancel (flowgate.default.0332 D0005 §3.2 / L0007 §2.2~§2.6) ─────

# L0007 §1 cancel_lock_wait_sec = 5, deliberately NOT the approval side's 0. An
# approval happens many times an hour and its commit is a by-product, so waiting is
# pure latency; a rewind is a person pressing [되돌리기] once and the cancel IS the
# point of that press. Giving up because a finalize held the lock for two seconds
# would hand them a [다시 시도] button that does nothing new.
CANCEL_LOCK_WAIT_SEC = LOCK_WAIT_SEC


# ── TR revert/reapply conflict session (TR0019, migration 088) ───────────────
#
# Before this block a conflicted revert was a dead end with a `retryable=false` label on
# it: the loop wiped the index and told the person to go fix their worktree by hand. The
# machinery for the opposite outcome already existed one module over — the finalize merge
# keeps its conflict as a session row, the Git status panel opens an inline editor on that
# row, and an AI can be handed a token bound to that merge_id and asked to resolve it. All

from .git.conflict import (
    TR_CONFLICT_REVIEW_OPEN,
    _chunk_added_lines,
    _classify_conflict_chunks,
    _conflict_side_dropped,
    _find_subsequence,
    _resolver_run_provider,
    _revert_in_flight,
    _session_context,
    _set_tr_review_state,
    _split_conflict_chunks_with_base,
    abort_merge,
    abort_tr_conflict,
    commit_tr_conflict,
    list_conflicts,
    open_tr_conflict_session,
    resolve_conflict_src_root,
    resolve_conflicts,
    tr_conflict_session,
)

# of it is keyed on a merge session, so the whole change is: let a TR conflict BE one.

# `context.review_state`. Two values, and the gap between them is the point: a TR conflict
# that has been resolved is NOT a TR conflict that has been committed.
























def _last_line(text: Optional[str]) -> str:
    lines = [l for l in (text or "").strip().splitlines() if l.strip()]
    return lines[-1] if lines else "git command failed"


# git aborts a merge that would clobber an untracked file with:
#   error: The following untracked working tree files would be overwritten by merge:
#           path/one.txt
#           path/two.txt
#   Please move or remove them before you merge.
# ("removed by merge" is the delete-side wording of the same refusal.)








# ── Conflict session: list / resolve / abort (P0005 §6 / L0006 §2.7) ─────────

_CONFLICT_OPEN_RE = re.compile(r"^<{7}( |$)")
_CONFLICT_CLOSE_RE = re.compile(r"^>{7}( |$)")


def has_conflict_markers(content: str) -> bool:
    # A bare "=======" line doubles as a Markdown H1 underline — not checked (L0006 §2.7).
    for line in (content or "").splitlines():
        if _CONFLICT_OPEN_RE.match(line) or _CONFLICT_CLOSE_RE.match(line):
            return True
    return False


# ── One-side-dropped detection (0478 T0012) ───────────────────────────────
# Same marker grammar and state machine as client/src/main/composables/useConflictChunks.ts
# (MARKER_OPEN_RE/MARKER_CLOSE_RE/MARKER_SEP_RE/MARKER_BASE_RE, parseConflictFile), reimplemented
# server-side so `resolve_conflicts` can catch a resolver that dropped an entire side even though
# no markers remain.




















# ── General-merge review gate (flowgate.default.0481 D0006/L0007, T0008) ─────
# TR revert/reapply conflicts have stopped at a human commit press since 0332 (see
# TR_CONFLICT_REVIEW_* above); this extends the same stop-and-look principle to an
# ordinary finalize merge, whose resolution used to commit and push itself the
# moment conflict markers were gone. The full state machine lives in the session's
# free-form `context` (no schema change, matching every TR field before it):
#
#   review_state          resolved_pending_review | re_review | applying |
#                         reconciling | completed
#   auto_authority        recorded ONLY by record_auto_authority, from a human's
#                         [AI 호출] or direct [해결 제출] press — never from a
#                         resolve/approve/reject request's own fields
#   snapshot_tree/_manifest, base_head, merge_head, expected_remote_head,
#   review_fingerprint    the ONE frozen commit-candidate the human reviews,
#                         approves against, and that is committed verbatim
#   resolver_baseline     {base_head, merge_head} captured at session creation —
#                         a reject re-runs THIS SAME merge to regenerate byte-
#                         identical conflict markers, rather than hand-snapshotting
#                         every conflicted file's raw index stage
#   approval_attempt_id, merge_commit, apply_phase   idempotent approval/push
#   reconciliation_kind, reconcile_next_at, reconcile_attempt_count   push outcome
#                         unknown → durable retry, survives a server restart
#   conversation          human/AI turns exchanged before approval

REVIEW_STATE_PENDING = "resolved_pending_review"
REVIEW_STATE_RE_REVIEW = "re_review"
REVIEW_STATE_APPLYING = "applying"
REVIEW_STATE_RECONCILING = "reconciling"
REVIEW_STATE_COMPLETED = "completed"
REVIEW_PENDING_STATES = (REVIEW_STATE_PENDING, REVIEW_STATE_RE_REVIEW)

REVIEW_FINGERPRINT_VERSION = "flowgate-review-fingerprint-v1"
MAX_CHAT_TURNS = 20
MAX_CHAT_MESSAGE_CHARS = 4000
PUSH_RECONCILE_DELAYS_SEC = (0, 1, 3)
PUSH_RECONCILE_RETRY_INTERVAL_SEC = 60
# Reconciliation kinds a durable worker (§2.8.1) is allowed to keep retrying.
RECONCILE_AUTO_RETRY_KINDS = ("push_remote_unknown", "post_push_cleanup")


def _parse_ls_tree_z(stdout: str) -> list[dict]:
    """``git ls-tree -r -z <tree>`` → ``[{path, mode, kind, oid}]`` sorted by the
    raw path bytes (not a locale collation), matching L0007 §2.3's manifest order."""
    entries: list[dict] = []
    for record in (stdout or "").split("\0"):
        if not record:
            continue
        meta, sep, path = record.partition("\t")
        if not sep:
            continue
        parts = meta.split(" ", 2)
        if len(parts) != 3:
            continue
        mode, kind, oid = parts
        entries.append({"path": path, "mode": mode, "kind": kind, "oid": oid})
    entries.sort(key=lambda e: e["path"].encode("utf-8", errors="surrogateescape"))
    return entries


def _canonical_encode_manifest(manifest: list[dict]) -> bytes:
    """Length-prefixed encoding of the manifest so no field boundary is ambiguous
    (L0007 §2.3 — the same principle as the conflict-chunk ``canonical_encode``)."""
    chunks: list[bytes] = []
    for entry in manifest:
        path_bytes = entry["path"].encode("utf-8", errors="surrogateescape")
        chunks.append(str(len(path_bytes)).encode("ascii"))
        chunks.append(b":")
        chunks.append(path_bytes)
        chunks.append(b"|")
        chunks.append((entry.get("mode") or "").encode("ascii"))
        chunks.append(b"|")
        chunks.append((entry.get("oid") or "").encode("ascii"))
        chunks.append(b"\n")
    return b"".join(chunks)


def _compute_review_fingerprint(
    base_head: Optional[str], merge_head: Optional[str],
    expected_remote_head: Optional[str], manifest: list[dict],
) -> str:
    digest = hashlib.sha256()
    for part in (
        REVIEW_FINGERPRINT_VERSION, base_head or "", merge_head or "",
        expected_remote_head or "",
    ):
        digest.update(part.encode("ascii", errors="ignore"))
        digest.update(b"\x00")
    digest.update(_canonical_encode_manifest(manifest))
    return digest.hexdigest()


def _freeze_commit_candidate(base_root: Path, base_branch: str) -> dict:
    """Fork of D0006 §3.4 / L0007 §2.3's ``freeze_commit_candidate``.

    MUST be called with the project git lock already held by the caller — this
    function never acquires or releases it. Returns the one frozen commit
    candidate: the full index/tree this merge would commit (every path, not just
    the ones a person or AI resolved), the heads it was built from, and the
    fingerprint that ties a review screen to a specific approval."""
    if not _merge_in_progress(base_root):
        raise GitServiceError(409, "invalid_state", "no merge in progress to freeze")
    unmerged = _unmerged_paths(base_root)
    if unmerged:
        raise GitServiceError(
            409, "conflict_markers_remain",
            f"git still reports {len(unmerged)} unmerged path(s)",
        )
    base_head = _rev_parse(base_root, "HEAD")
    merge_head = _rev_parse(base_root, "MERGE_HEAD")
    expected_remote_head = _rev_parse(base_root, f"refs/remotes/origin/{base_branch}")
    write_tree = _run_git(["write-tree"], cwd=base_root, timeout=GIT_READ_TIMEOUT_SEC)
    if write_tree.returncode != 0:
        raise GitServiceError(500, "git_error", _last_line(write_tree.stderr))
    snapshot_tree = (write_tree.stdout or "").strip()
    ls = _run_git(["ls-tree", "-r", "-z", snapshot_tree], cwd=base_root, timeout=GIT_READ_TIMEOUT_SEC)
    if ls.returncode != 0:
        raise GitServiceError(500, "git_error", _last_line(ls.stderr))
    manifest = _parse_ls_tree_z(ls.stdout or "")
    diff_proc = _run_git(
        ["diff", "--name-status", "-M", "-z", base_head or "", snapshot_tree, "--"],
        cwd=base_root, timeout=GIT_READ_TIMEOUT_SEC,
    )
    if diff_proc.returncode != 0:
        raise GitServiceError(500, "git_error", _last_line(diff_proc.stderr))
    changes = _parse_name_status_manifest(diff_proc.stdout or "")
    fingerprint = _compute_review_fingerprint(base_head, merge_head, expected_remote_head, manifest)
    return {
        "snapshot_tree": snapshot_tree,
        "snapshot_manifest": manifest,
        "base_head": base_head,
        "merge_head": merge_head,
        "expected_remote_head": expected_remote_head,
        "changes": changes,
        "review_fingerprint": fingerprint,
    }


def _live_candidate_matches_snapshot(base_root: Path, context: dict) -> bool:
    """The TOCTOU identity check (D0006 §3.4): is the tree the base checkout would
    commit RIGHT NOW, from the SAME heads, byte-identical to the frozen one? A tree
    object id already encodes every path's content/mode/existence recursively, so
    comparing two tree ids is exactly the manifest comparison L0007 describes."""
    if not _merge_in_progress(base_root):
        return False
    if _unmerged_paths(base_root):
        return False
    if _rev_parse(base_root, "HEAD") != context.get("base_head"):
        return False
    if _rev_parse(base_root, "MERGE_HEAD") != context.get("merge_head"):
        return False
    write_tree = _run_git(["write-tree"], cwd=base_root, timeout=GIT_READ_TIMEOUT_SEC)
    if write_tree.returncode != 0:
        return False
    return (write_tree.stdout or "").strip() == context.get("snapshot_tree")




_JS_LIKE_EXTENSIONS = {"js", "mjs", "cjs", "jsx", "ts", "mts", "cts", "tsx"}
_CSS_LIKE_EXTENSIONS = {"css", "scss"}
_VOID_HTML_ELEMENTS = {
    "area", "base", "br", "col", "embed", "hr", "img", "input",
    "link", "meta", "param", "source", "track", "wbr",
}


_FLOWGATE_REPO_ROOT = Path(__file__).resolve().parents[4]
_FLOWGATE_CLIENT_DIR = _FLOWGATE_REPO_ROOT / "client"
_SYNTAX_CHECK_SCRIPT = _FLOWGATE_CLIENT_DIR / "scripts" / "git-review-syntax-check.mjs"
_NODE_SYNTAX_CHECK_TIMEOUT_SEC = 20


def _run_node_syntax_check(ext: str, text: str) -> Optional[dict]:
    """Real-parser syntax check for JS/TS/CSS/SCSS/Vue (D0006 §3.5 / L0007 §2.7):
    shells out to ``client/scripts/git-review-syntax-check.mjs``, which parses
    ``text`` with the SAME compiler packages the client build already depends on
    (typescript's no-emit `transpileModule`, `@vue/compiler-sfc`, `postcss`,
    `@babel/parser`) — a real ECMAScript/TypeScript/Vue-SFC/CSS grammar check,
    not a delimiter-balance heuristic. Runs against THIS server's own
    ``client/node_modules`` (``cwd=_FLOWGATE_CLIENT_DIR``), never the reviewed
    project's own checkout — ``text`` goes over stdin and nothing touches disk,
    so this works for any target project regardless of whether it has a JS
    toolchain of its own. Returns ``None`` on success, or
    ``{"line": int | None, "message": str}`` on the first syntax error."""
    node = shutil.which("node")
    if not node:
        return {"line": None, "message": "node executable not found on PATH — cannot run the real syntax checker"}
    try:
        proc = subprocess.run(
            [node, str(_SYNTAX_CHECK_SCRIPT)],
            input=json.dumps({"ext": ext, "content": text}),
            capture_output=True, text=True, encoding="utf-8",
            cwd=str(_FLOWGATE_CLIENT_DIR), timeout=_NODE_SYNTAX_CHECK_TIMEOUT_SEC,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"line": None, "message": f"syntax checker subprocess failed: {exc}"}
    try:
        payload = json.loads(proc.stdout or "{}")
    except ValueError:
        detail = (proc.stderr or proc.stdout or "").strip()[:500]
        return {"line": None, "message": f"syntax checker returned invalid output: {detail}"}
    if payload.get("ok"):
        return None
    return {"line": payload.get("line"), "message": payload.get("message") or "syntax error"}


def _check_html_syntax(text: str) -> Optional[str]:
    """Dependency-free balanced-tag check for `*.html`/`*.htm`/`*.vue` (D0006
    §3.5 / L0007 §2.7) — a tag stack over a lightweight regex tokenizer, not a
    real HTML5 parser. `<script>`/`<style>` bodies are skipped verbatim so angle
    brackets inside JS/CSS content never desync the stack."""
    tag_re = re.compile(r"<(/?)([a-zA-Z][a-zA-Z0-9:_-]*)([^>]*)>")
    stack: list[str] = []
    pos, n = 0, len(text)
    while pos < n:
        m = tag_re.search(text, pos)
        if not m:
            break
        closing, name, attrs = m.group(1), m.group(2).lower(), m.group(3)
        pos = m.end()
        if closing:
            if not stack:
                return f"closing tag </{name}> with nothing open"
            if name not in stack:
                return f"closing tag </{name}> does not match any open tag"
            while stack and stack[-1] != name:
                stack.pop()
            stack.pop()
            continue
        if name in _VOID_HTML_ELEMENTS or attrs.rstrip().endswith("/"):
            continue
        if name in ("script", "style"):
            close_m = re.search(r"</" + name + r"\s*>", text[pos:], re.IGNORECASE)
            if not close_m:
                return f"<{name}> is never closed"
            pos += close_m.end()
            continue
        stack.append(name)
    if stack:
        return f"<{stack[-1]}> is never closed"
    return None


def _validate_review_changed_paths(
    base_root: Path, context: dict, *, unregistered_extension: str = "reject",
) -> list[dict]:
    """Pre-commit content sanity over every changed/created path in the frozen
    candidate (D0006 §3.5 / L0007 §2.7). Every text path must decode as UTF-8 and
    carry no leftover conflict marker. Extension selects the validator per
    L0007 §2.7's table:

    `*.py` parses AND `py_compile`s; `*.json` and `*.yaml`/`*.yml` get a real
    strict parser (the stdlib's / PyYAML, already a dependency); `*.html`/`*.htm`
    get `_check_html_syntax`; the JS/TS family, `*.vue`, and `*.css`/`*.scss` get
    a real compiler/parser via `_run_node_syntax_check` (see its docstring).
    Deletes are skipped (nothing to validate); oversized/binary blobs are skipped
    (nothing this check can read).

    ``unregistered_extension`` decides what an extension outside that table means,
    and the two callers genuinely need different answers.

    ``"reject"`` (the default, and what `_apply_write_plan_locked` passes) is
    L0007 §2.7 as written: the scope of that rule is the WRITE PLAN's own target
    files (`syntax_validation_scope` = "변경되거나 생성된 모든 **plan** 대상 파일"),
    so a plan that asks to write a file type we cannot syntax-check is rejected
    whole. There is no fallback that lets an unvalidated AI-written file reach
    approval.

    ``"skip"`` is what `approve_merge_review` passes, and 0481 T0010 rev3 is why.
    Approval validates the WHOLE merge candidate, not a plan — every file the two
    branches happen to touch. Reusing "reject" there made the rule mean something
    it never said: any merge carrying a `.md`, `.txt`, `.lock`, `.png` or (the
    reviewer's actual case) a `.tsbuildinfo` was permanently unapprovable, and
    [승인] could only ever answer `pre_commit_validation_failed`. That is the
    "머지는 되지도 않음" rejection of 2026-09-08 10:33, reproduced on a copy of
    the reviewer's own base checkout. Nothing is lost by skipping here: a
    plan-written path with an unregistered extension can never be in the
    candidate in the first place, because the apply gate above already refused
    it. UTF-8 and conflict-marker checks still run on EVERY path either way —
    only the "I have no validator for this" verdict is dropped.
    """
    if unregistered_extension not in ("reject", "skip"):
        raise ValueError(f"unregistered_extension must be 'reject' or 'skip', got {unregistered_extension!r}")
    import ast
    import json as _json
    import py_compile
    import tempfile

    import yaml as _yaml

    manifest_by_path = {entry["path"]: entry for entry in (context.get("snapshot_manifest") or [])}
    errors: list[dict] = []
    for change in context.get("changes") or []:
        if change.get("status") == "D":
            continue
        path = change.get("path") or ""
        entry = manifest_by_path.get(path)
        if entry is None or entry.get("kind") != "blob":
            continue
        size = _cat_file_size(base_root, entry["oid"])
        if size > BLOB_MAX_RETURN_BYTES:
            continue
        blob = _cat_file_blob_head(base_root, entry["oid"], size)
        if b"\x00" in blob[:BLOB_BINARY_SNIFF_BYTES]:
            continue
        try:
            text = blob.decode("utf-8")
        except UnicodeDecodeError as exc:
            errors.append({"path": path, "validator": "utf8", "line": None, "message": str(exc)})
            continue
        if has_conflict_markers(text):
            errors.append({
                "path": path, "validator": "conflict_marker", "line": None,
                "message": "conflict markers remain in the frozen candidate",
            })
            continue
        ext = path.rsplit(".", 1)[-1].lower() if "." in path else ""
        if ext == "py":
            try:
                ast.parse(text, filename=path)
                tmp_path = None
                try:
                    with tempfile.NamedTemporaryFile(
                        suffix=".py", delete=False, dir=str(base_root),
                    ) as handle:
                        handle.write(blob)
                        tmp_path = handle.name
                    py_compile.compile(tmp_path, doraise=True)
                finally:
                    if tmp_path:
                        try:
                            os.unlink(tmp_path)
                        except OSError:
                            pass
            except SyntaxError as exc:
                errors.append({
                    "path": path, "validator": "python",
                    "line": getattr(exc, "lineno", None), "message": str(exc),
                })
            except py_compile.PyCompileError as exc:
                errors.append({"path": path, "validator": "py_compile", "line": None, "message": str(exc)})
        elif ext == "json":
            try:
                _json.loads(text)
            except ValueError as exc:
                errors.append({"path": path, "validator": "json", "line": None, "message": str(exc)})
        elif ext in ("yaml", "yml"):
            try:
                _yaml.safe_load(text)
            except _yaml.YAMLError as exc:
                mark = getattr(exc, "problem_mark", None)
                errors.append({
                    "path": path, "validator": "yaml",
                    "line": (mark.line + 1) if mark else None, "message": str(exc),
                })
        elif ext in ("html", "htm"):
            msg = _check_html_syntax(text)
            if msg:
                errors.append({"path": path, "validator": "html", "line": None, "message": msg})
        elif ext in _CSS_LIKE_EXTENSIONS:
            result = _run_node_syntax_check(ext, text)
            if result:
                errors.append({"path": path, "validator": "css", "line": result["line"], "message": result["message"]})
        elif ext in _JS_LIKE_EXTENSIONS:
            result = _run_node_syntax_check(ext, text)
            if result:
                errors.append({"path": path, "validator": "ecmascript", "line": result["line"], "message": result["message"]})
        elif ext == "vue":
            result = _run_node_syntax_check(ext, text)
            if result:
                errors.append({"path": path, "validator": "vue", "line": result["line"], "message": result["message"]})
        elif unregistered_extension == "reject":
            # L0007 §2.7: an extension outside the registered table is not
            # generically accepted — reject the whole plan instead.
            label = f"'.{ext}'" if ext else "files without an extension"
            errors.append({
                "path": path, "validator": "unsupported", "line": None,
                "message": f"no syntax validator registered for {label}",
            })
    return errors


# ── Anchored write-plan engine (flowgate.default.0481 T0008 item 1 / L0007 §2.5-§2.9,
# Q&A on 0009-TR) ─────────────────────────────────────────────────────────────
# The merge review's explicit [수정 적용] turn is the only conversation turn that
# may change the source tree. Its AI run has no write tool at all (SCOPE_BOUND_TOOLS
# demotes action_scope=resolve_conflict to "read") — the anchored plan it submits to
# `POST .../write-plan-token` (git_routes.post_merge_write_plan_token ->
# submit_review_write_plan below) is the only channel, and everything below builds
# and validates the result ENTIRELY off-tree before ever touching the live checkout.

WRITE_PLAN_SCHEMA_VERSION = "flowgate.write-plan.v1"
MAX_APPLY_OPERATIONS = 200


def _is_test_write_plan_path(path: str) -> bool:
    """L0007 §2.7's "test file" definition for held_test_operations gating."""
    if path.startswith("server/tests/") or path.startswith("client/tests/"):
        return True
    return any(seg in ("test", "tests") for seg in path.split("/"))


def _validate_write_plan_path(path) -> None:
    if not isinstance(path, str) or not path:
        raise GitServiceError(422, "invalid_write_plan", "operation path must be a non-empty string")
    if "\\" in path or path.startswith("/") or re.match(r"^[A-Za-z]:", path):
        raise GitServiceError(
            422, "invalid_write_plan",
            f"operation path must be a project-root-relative '/'-separated path: {path!r}",
        )
    segments = path.split("/")
    if any(seg in ("", ".", "..") for seg in segments):
        raise GitServiceError(422, "invalid_write_plan", f"operation path is not a clean relative path: {path!r}")
    if ".git" in segments:
        raise GitServiceError(422, "invalid_write_plan", f"operation path may not touch .git: {path!r}")


def _decode_write_plan_bytes(value, field: str) -> bytes:
    if not isinstance(value, str) or not value:
        raise GitServiceError(422, "invalid_write_plan", f"{field} must be a non-empty base64 string")
    try:
        return base64.b64decode(value, validate=True)
    except ValueError:
        raise GitServiceError(422, "invalid_write_plan", f"{field} is not valid base64")


def _validate_write_plan_structure(plan: dict, *, allow_test_edits: bool) -> None:
    """Structural/shape validation only — everything that does not require reading
    the live tree (L0007 §2.5, Q&A on 0009-TR's schema). ``_apply_write_plan_locked``
    separately re-validates each operation's claims (expected blob, anchor
    occurrence count, path absence) against the tree it is actually about to
    touch; a plan can pass this and still fail there."""
    if not isinstance(plan, dict):
        raise GitServiceError(422, "invalid_write_plan", "write plan must be a JSON object")
    if plan.get("schema_version") != WRITE_PLAN_SCHEMA_VERSION:
        raise GitServiceError(422, "invalid_write_plan", f"schema_version must be {WRITE_PLAN_SCHEMA_VERSION!r}")
    base_fingerprint = plan.get("base_fingerprint")
    if not isinstance(base_fingerprint, str) or not re.fullmatch(r"[0-9a-f]{64}", base_fingerprint):
        raise GitServiceError(422, "invalid_write_plan", "base_fingerprint must be a 64-char lowercase sha256 hex string")
    operations = plan.get("operations")
    if operations is None:
        operations = []
    if not isinstance(operations, list):
        raise GitServiceError(422, "invalid_write_plan", "operations must be an array")
    if len(operations) > MAX_APPLY_OPERATIONS:
        raise GitServiceError(422, "invalid_write_plan", f"operations exceeds the {MAX_APPLY_OPERATIONS}-operation limit")
    held = plan.get("held_test_operations") or []
    if not isinstance(held, list):
        raise GitServiceError(422, "invalid_write_plan", "held_test_operations must be an array")
    # 0009-TR rev3 (AI review finding 2): a plan whose AI proposed ONLY test-path
    # edits is legitimate — every proposed operation lands in held_test_operations
    # and operations[] is empty. L0007 §2.7's held-edit flow requires that to be
    # SHOWN, not rejected outright; the plan is only meaningless if BOTH arrays
    # are empty.
    if not operations and not held:
        raise GitServiceError(422, "invalid_write_plan", "plan must contain at least one operation or held_test_operation")

    seen_ids: set = set()
    seen_paths: set = set()
    for op in [*operations, *held]:
        if not isinstance(op, dict):
            raise GitServiceError(422, "invalid_write_plan", "each operation must be a JSON object")
        operation_id = op.get("operation_id")
        if not isinstance(operation_id, str) or not operation_id:
            raise GitServiceError(422, "invalid_write_plan", "operation_id must be a non-empty string")
        if operation_id in seen_ids:
            raise GitServiceError(422, "invalid_write_plan", f"duplicate operation_id: {operation_id!r}")
        seen_ids.add(operation_id)
        purpose = op.get("purpose")
        if not isinstance(purpose, str) or not purpose.strip():
            raise GitServiceError(422, "invalid_write_plan", f"{operation_id}: purpose must be a non-empty string")
        path = op.get("path")
        _validate_write_plan_path(path)
        kind = op.get("kind")
        if kind == "edit":
            if not isinstance(op.get("expected_before_blob"), str) or not op["expected_before_blob"]:
                raise GitServiceError(422, "invalid_write_plan", f"{operation_id}: edit requires expected_before_blob")
            anchor = op.get("anchor")
            if not isinstance(anchor, dict):
                raise GitServiceError(422, "invalid_write_plan", f"{operation_id}: edit requires anchor")
            _decode_write_plan_bytes(anchor.get("body_base64"), f"{operation_id}.anchor.body_base64")
            expected_count = anchor.get("expected_count")
            if not isinstance(expected_count, int) or isinstance(expected_count, bool) or expected_count < 1:
                raise GitServiceError(422, "invalid_write_plan", f"{operation_id}: anchor.expected_count must be a positive integer")
            _decode_write_plan_bytes(op.get("replacement_bytes_base64"), f"{operation_id}.replacement_bytes_base64")
        elif kind == "create_file":
            if op.get("absent") is not True:
                raise GitServiceError(422, "invalid_write_plan", f"{operation_id}: create_file requires absent=true")
            _decode_write_plan_bytes(op.get("content_bytes_base64"), f"{operation_id}.content_bytes_base64")
            if op.get("mode") not in ("100644", "100755"):
                raise GitServiceError(422, "invalid_write_plan", f"{operation_id}: mode must be '100644' or '100755'")
        else:
            raise GitServiceError(422, "invalid_write_plan", f"{operation_id}: kind must be 'edit' or 'create_file'")
        if path in seen_paths:
            raise GitServiceError(422, "invalid_write_plan", f"duplicate/overlapping path across operations: {path!r}")
        seen_paths.add(path)
        if not allow_test_edits and op not in held and _is_test_write_plan_path(path):
            raise GitServiceError(
                422, "invalid_write_plan",
                f"{operation_id}: test-path operations must go in held_test_operations unless "
                "this turn was started with the human's second explicit authorization "
                "(allow_test_edits)",
            )

    encoded_size = len(json.dumps(plan, ensure_ascii=False).encode("utf-8"))
    if encoded_size > _WRITE_PLAN_MAX_SERIALIZED_BYTES:
        raise GitServiceError(422, "invalid_write_plan", "write plan is too large to persist")


# Mirrors db.ai_invoke_runs._WRITE_PLAN_MAX_SERIALIZED_BYTES (MySQL TEXT's 65,535-byte
# ceiling) — enforced here too so an oversized plan is rejected at submission with a
# clear 422 instead of silently losing operations at the storage layer later.
_WRITE_PLAN_MAX_SERIALIZED_BYTES = 65000


def submit_review_write_plan(group_id: str, merge_id: int, *, plan: dict, ai_run_id: Optional[str] = None) -> dict:
    """``POST .../write-plan-token`` (git_routes.post_merge_write_plan_token) — the
    ONLY entry point by which a review-message write turn's AI run can change the
    source tree. Attaches ``plan`` to whichever run this session's pending write
    turn is currently waiting on; the plan is VALIDATED here (structure only) but
    not applied — apply happens once the run is observed finished
    (`_materialize_pending_conversation_run` -> `_apply_write_plan_locked`), never
    synchronously from this worker-token call.

    0009-TR rev3 (AI review finding 1): ``ai_run_id`` is the SUBMITTING token's
    own bound run — the caller (git_routes) reads it from the verified worker
    token, never from the request body. A resolve_conflict token that is still
    valid for this group/merge but belongs to a different (stale, or simply
    another) run must not be able to attach a plan to the CURRENT pending write
    turn, so this is checked against ``pending_conversation_run_id`` before the
    plan is recorded — the same run-bound authority the rest of this session's
    state machine already assumes."""
    session, context, _project_id, _base_root, _base_branch = _merge_review_session(group_id, merge_id)
    if context.get("review_state") not in REVIEW_PENDING_STATES:
        raise GitServiceError(409, "review_not_ready", f"session is in state {context.get('review_state')!r}")
    run_id = context.get("pending_conversation_run_id")
    if not run_id or not context.get("pending_conversation_write_requested"):
        raise GitServiceError(
            409, "write_plan_not_requested",
            "no pending write turn on this session is waiting for a plan",
        )
    if not ai_run_id or ai_run_id != run_id:
        raise GitServiceError(
            403, "write_plan_run_mismatch",
            "this token's ai_run_id does not match the session's pending write turn",
        )
    allow_test_edits = bool(context.get("pending_conversation_allow_test_edits"))
    _validate_write_plan_structure(plan, allow_test_edits=allow_test_edits)

    from modules.flow_gate.services import ai_invoke_service

    ai_invoke_service.record_run_write_plan(run_id, plan)
    return {"ok": True, "result": {"status": "accepted", "run_id": run_id}}


def _tree_manifest_map(base_root: Path, tree: str) -> dict[str, dict]:
    ls = _run_git(["ls-tree", "-r", "-z", tree], cwd=base_root, timeout=GIT_READ_TIMEOUT_SEC)
    if ls.returncode != 0:
        raise GitServiceError(500, "git_error", _last_line(ls.stderr))
    return {entry["path"]: entry for entry in _parse_ls_tree_z(ls.stdout or "")}


def _git_hash_object_write(base_root: Path, content: bytes) -> str:
    """Writes ``content`` as a new blob, byte-exact — a raw-bytes subprocess call,
    deliberately NOT routed through ``_run_git`` (whose ``text=True`` UTF-8
    decode/re-encode is safe for valid UTF-8 but must never be trusted with
    arbitrary plan-supplied bytes)."""
    env = os.environ.copy()
    env["GIT_TERMINAL_PROMPT"] = "0"
    try:
        proc = subprocess.run(
            ["git", "hash-object", "-w", "--stdin"], cwd=str(base_root),
            input=content, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            timeout=GIT_LOCAL_TIMEOUT_SEC, env=env,
        )
    except FileNotFoundError:
        raise GitServiceError(500, "git_unavailable", "git binary not found on server")
    except subprocess.TimeoutExpired:
        raise GitServiceError(500, "git_error", "hash-object timed out")
    if proc.returncode != 0:
        raise GitServiceError(500, "git_error", _last_line((proc.stderr or b"").decode("utf-8", "replace")))
    return proc.stdout.decode("ascii", "strict").strip()


def _count_nonoverlapping(haystack: bytes, needle: bytes) -> int:
    return haystack.count(needle) if needle else 0


def _build_write_plan_tree(
    base_root: Path, before_tree: str, operations: list[dict],
) -> tuple[Optional[str], list[dict]]:
    """Materializes ``operations`` (already structurally valid) into a NEW tree
    object entirely inside an isolated scratch git index — ``GIT_INDEX_FILE``
    points at a temp file only this function touches, so neither the real
    ``.git/index`` nor the working tree are read or written no matter what this
    returns (L0007 §2.6 ``isolated_apply_area``). Returns ``(new_tree, errors)``;
    ``new_tree`` is ``None`` whenever ``errors`` is non-empty."""
    before_map = _tree_manifest_map(base_root, before_tree)
    errors: list[dict] = []
    updates: list[tuple[str, str, str]] = []  # (mode, oid, path)
    for op in operations:
        path = op["path"]
        if op["kind"] == "edit":
            entry = before_map.get(path)
            if entry is None or entry.get("kind") != "blob":
                errors.append({"operation_id": op["operation_id"], "path": path,
                                "message": "path does not exist in the reviewed candidate"})
                continue
            if entry["oid"] != op["expected_before_blob"]:
                errors.append({"operation_id": op["operation_id"], "path": path,
                                "message": "expected_before_blob does not match the current blob"})
                continue
            size = _cat_file_size(base_root, entry["oid"])
            content = _cat_file_blob_head(base_root, entry["oid"], size)
            anchor_bytes = base64.b64decode(op["anchor"]["body_base64"])
            expected_count = op["anchor"]["expected_count"]
            actual_count = _count_nonoverlapping(content, anchor_bytes)
            if actual_count != expected_count:
                errors.append({
                    "operation_id": op["operation_id"], "path": path,
                    "message": f"anchor occurs {actual_count} time(s) in the current file, expected {expected_count}",
                })
                continue
            replacement = base64.b64decode(op["replacement_bytes_base64"])
            new_content = content.replace(anchor_bytes, replacement)
            updates.append((entry["mode"], _git_hash_object_write(base_root, new_content), path))
        else:  # kind == "create_file" (the only other structurally-valid kind)
            if path in before_map:
                errors.append({"operation_id": op["operation_id"], "path": path,
                                "message": "path already exists in the reviewed candidate"})
                continue
            content = base64.b64decode(op["content_bytes_base64"])
            updates.append((op["mode"], _git_hash_object_write(base_root, content), path))
    if errors:
        return None, errors

    index_path = str(base_root / ".git" / f"flowgate-writeplan-{uuid.uuid4().hex}.index")
    extra_env = {"GIT_INDEX_FILE": index_path}
    try:
        read = _run_git(["read-tree", before_tree], cwd=base_root, extra_env=extra_env)
        if read.returncode != 0:
            return None, [{"message": f"isolated read-tree failed: {_last_line(read.stderr)}"}]
        for mode, oid, path in updates:
            upd = _run_git(
                ["update-index", "--add", "--cacheinfo", f"{mode},{oid},{path}"],
                cwd=base_root, extra_env=extra_env,
            )
            if upd.returncode != 0:
                return None, [{"message": f"isolated update-index failed for {path!r}: {_last_line(upd.stderr)}"}]
        write = _run_git(["write-tree"], cwd=base_root, extra_env=extra_env)
        if write.returncode != 0:
            return None, [{"message": f"isolated write-tree failed: {_last_line(write.stderr)}"}]
        return (write.stdout or "").strip(), []
    finally:
        try:
            os.unlink(index_path)
        except OSError:
            pass


def _restore_write_plan_worktree(base_root: Path, before_tree: str) -> bool:
    """Best-effort restore + VERIFIED check — used only when the one real-tree
    step (the final ``read-tree --reset -u`` in ``_apply_write_plan_locked``) itself
    fails partway. Every earlier step only reads blobs/builds an isolated tree,
    so there is nothing to restore if THEY fail."""
    _run_git(["read-tree", "--reset", "-u", before_tree], cwd=base_root, timeout=GIT_LOCAL_TIMEOUT_SEC)
    check = _run_git(["write-tree"], cwd=base_root, timeout=GIT_READ_TIMEOUT_SEC)
    if check.returncode != 0:
        return False
    return (check.stdout or "").strip() == before_tree and not _unmerged_paths(base_root)


def _apply_write_plan_locked(
    group_id: str, merge_id: int, plan: dict, base_root: Path, base_branch: str,
) -> dict:
    """L0007 §2.6 ``apply_write_plan`` — the anchored write-plan engine's atomic
    apply/rollback core (flowgate.default.0481 T0008 item 1). Never reached
    directly by a route: `_materialize_pending_conversation_run` applies a plan
    once a review-message write turn's AI run finishes with a ``write_plan``
    attached (Q&A on 0009-TR's ``GET /ai-invoke/{run_id}`` contract). The new
    candidate tree is built ENTIRELY off-tree (`_build_write_plan_tree`'s
    isolated scratch index) and validated (real syntax/py_compile checks,
    reusing `_validate_review_changed_paths`) before the live checkout is ever
    touched — a plan that fails validation leaves the real index/worktree
    completely untouched, so recovery is only ever needed for the one step that
    DOES touch them (the final ``read-tree --reset -u``).

    0009-TR rev5: the project git lock is the CALLER's to hold, not this
    function's. L0007 §2.6 opens with `acquire project_git_lock`, but the only
    caller must decide staleness (L0007 §2.9) and apply in the SAME critical
    section, and the lock is non-reentrant single-owner (D0006 §3.3) — so a
    self-locking wrapper could not be called from inside that decision at all,
    and having one for nobody would be dead code that invites exactly the
    check-then-act split this revision removes. Every piece of session state
    decided on here is re-read from the DB inside the caller's hold, so the
    view of review_state/review_fingerprint is consistent from the check right
    through to the tree swap."""
    session = db_git.get_session(merge_id)
    context = db_git.session_context(session)
    if context.get("review_state") not in REVIEW_PENDING_STATES:
        return {"status": "apply_failed",
                "errors": [{"message": f"session is in state {context.get('review_state')!r}"}]}
    if plan.get("base_fingerprint") != context.get("review_fingerprint"):
        return {"status": "apply_failed",
                "errors": [{"message": "base_fingerprint does not match the current review_fingerprint"}]}
    if not _live_candidate_matches_snapshot(base_root, context):
        return _refreeze_for_re_review(group_id, merge_id, base_root, base_branch, context, "identity_mismatch")

    held_test_operations = plan.get("held_test_operations") or []
    operations = plan.get("operations") or []
    if not operations:
        # 0009-TR rev3 (AI review finding 2): a plan whose only content is
        # held test edits touches nothing — there is no tree to build or
        # apply. Persist the held operations onto the session so the review
        # screen can show them (L0007 §2.7's "이유와 예상 검증을 화면에
        # 표시") instead of silently discarding them, and leave
        # review_state/review_fingerprint exactly where they were: nothing
        # changed, so nothing needs re-review.
        context["held_test_operations"] = held_test_operations
        db_git.set_session_context(merge_id, context)
        return {"status": "held_only", "held_test_operations": held_test_operations}

    before_write = _run_git(["write-tree"], cwd=base_root, timeout=GIT_READ_TIMEOUT_SEC)
    if before_write.returncode != 0:
        return {"status": "apply_failed", "errors": [{"message": _last_line(before_write.stderr)}]}
    before_tree = (before_write.stdout or "").strip()

    new_tree, build_errors = _build_write_plan_tree(base_root, before_tree, operations)
    if build_errors:
        return {"status": "apply_failed", "errors": build_errors}

    # L0007 §2.7 step 6: nothing outside the plan's own paths may have moved —
    # checked here directly against the two isolated manifests (no git diff
    # needed for this check, and no live tree involved yet).
    before_map = _tree_manifest_map(base_root, before_tree)
    new_map = _tree_manifest_map(base_root, new_tree)
    plan_paths = {op["path"] for op in operations}
    drifted = [
        path for path in set(before_map) | set(new_map)
        if path not in plan_paths
        and (before_map.get(path, {}).get("oid"), before_map.get(path, {}).get("mode"))
            != (new_map.get(path, {}).get("oid"), new_map.get(path, {}).get("mode"))
    ]
    if drifted:
        return {"status": "apply_failed",
                "errors": [{"message": f"unexpected change outside the plan: {sorted(drifted)[:5]}"}]}

    diff_proc = _run_git(
        ["diff", "--name-status", "-M", "-z", before_tree, new_tree, "--"],
        cwd=base_root, timeout=GIT_READ_TIMEOUT_SEC,
    )
    if diff_proc.returncode != 0:
        return {"status": "apply_failed", "errors": [{"message": _last_line(diff_proc.stderr)}]}
    plan_changes = _parse_name_status_manifest(diff_proc.stdout or "")
    syntax_errors = _validate_review_changed_paths(
        base_root, {"snapshot_manifest": list(new_map.values()), "changes": plan_changes},
    )
    if syntax_errors:
        return {"status": "apply_failed", "errors": syntax_errors}

    # The one step that touches the REAL index/worktree — everything above only
    # read blobs or built the isolated tree above.
    apply_proc = _run_git(["read-tree", "--reset", "-u", new_tree], cwd=base_root, timeout=GIT_LOCAL_TIMEOUT_SEC)
    if apply_proc.returncode != 0:
        if not _restore_write_plan_worktree(base_root, before_tree):
            context["review_state"] = REVIEW_STATE_RECONCILING
            context["reconciliation_kind"] = "apply_restore_failed"
            context["last_error"] = {"code": "rollback_verification_failed"}
            db_git.set_session_context(merge_id, context)
            return {"status": "rollback_verification_failed"}
        return {"status": "apply_failed", "errors": [{"message": _last_line(apply_proc.stderr)}]}

    if not _merge_in_progress(base_root) or _unmerged_paths(base_root):
        # Should be unreachable — read-tree --reset -u never touches MERGE_HEAD
        # and this plan never introduces conflict markers — but this is the one
        # invariant _freeze_commit_candidate below requires, verified explicitly
        # rather than letting IT raise mid-apply with the tree already swapped.
        context["review_state"] = REVIEW_STATE_RECONCILING
        context["reconciliation_kind"] = "apply_restore_failed"
        context["last_error"] = {"code": "merge_state_lost_after_apply"}
        db_git.set_session_context(merge_id, context)
        return {"status": "rollback_verification_failed"}

    snapshot = _freeze_commit_candidate(base_root, base_branch)
    context.update(snapshot)
    context["review_state"] = REVIEW_STATE_RE_REVIEW
    context["instruction_generation"] = int(context.get("instruction_generation") or 0) + 1
    context["approval_attempt_id"] = None
    context["merge_commit"] = None
    context["apply_phase"] = None
    context["last_error"] = None
    # 0009-TR rev3 (AI review finding 2): a MIXED plan (some operations applied,
    # some test-path proposals held) must not silently drop the held half —
    # persist it alongside the freshly-applied candidate so it stays visible.
    context["held_test_operations"] = held_test_operations
    db_git.set_session_context(merge_id, context)
    return {
        "status": "re_review", "review_state": REVIEW_STATE_RE_REVIEW,
        "review_fingerprint": snapshot["review_fingerprint"],
        "changed_paths": sorted(plan_paths),
        "held_test_operations": held_test_operations,
    }


def _merge_review_session(group_id: str, merge_id: int) -> tuple[dict, dict, str, Path, str]:
    """``(session, context, project_id, base_root, base_branch)`` for a general
    merge review session, or raises 404/409 when this merge_id is not one."""
    session = db_git.get_session(merge_id)
    if session is None or session.get("group_id") != group_id:
        raise GitServiceError(404, "review_not_found", f"merge session {merge_id} not found")
    if db_git.session_kind(session) != db_git.SESSION_KIND_MERGE:
        raise GitServiceError(409, "review_not_ready", "not a general merge review session")
    context = db_git.session_context(session)
    project_id = _project_of_group(group_id)
    cfg = db_git.get_config(project_id) or {}
    base_root = _base_root_of(project_id)
    if base_root is None:
        raise GitServiceError(409, "invalid_state", "base checkout is not provisioned")
    base_branch = (cfg.get("base_branch") or "main").strip() or "main"
    return session, context, project_id, base_root, base_branch


def record_auto_authority(group_id: str, merge_id: int, requested_auto: bool) -> None:
    """D0006 §3.2 / L0007 §2.2 ``record_auto_authority`` — the ONLY place
    ``auto_authority`` is ever written. Called from a human-authenticated request
    at the exact moment a resolution run starts ([AI 호출]) or a human submits a
    direct [해결 제출], with THAT request's checkbox value; a worker token's own
    resolve submission never reaches this function.

    A TR revert/reapply conflict silently ignores this call — "TR 세션에는 적용되지
    않는다" (D0006 §3.2) is modeled as a no-op here, not an error, because the
    generic [AI 호출] start path calls this unconditionally for every
    resolve_conflict invocation and must not regress the pre-existing,
    already-working TR conflict AI-call flow (T0008 completion criteria: no
    regression in the TR/group-update flows)."""
    session = db_git.get_session(merge_id)
    if session is None or session.get("group_id") != group_id:
        raise GitServiceError(404, "review_not_found", f"merge session {merge_id} not found")
    if db_git.session_kind(session) != db_git.SESSION_KIND_MERGE:
        return
    context = db_git.session_context(session)
    context["auto_authority"] = bool(requested_auto)
    db_git.set_session_context(merge_id, context)


def get_merge_review(group_id: str, merge_id: int) -> dict:
    """Approval-screen payload (L0007 §2.11 GET .../review) assembled ENTIRELY from
    the frozen candidate — never a fresh worktree read (D0006 §3.3/§3.4)."""
    _session, _context, project_id, base_root, base_branch = _merge_review_session(group_id, merge_id)
    _materialize_pending_conversation_run(group_id, merge_id, project_id, base_root, base_branch)
    session = db_git.get_session(merge_id)
    context = db_git.session_context(session)
    review_state = context.get("review_state")
    if not review_state:
        raise GitServiceError(409, "review_not_ready", "this merge has not reached review yet")
    pending = review_state in REVIEW_PENDING_STATES
    return {
        "ok": True,
        "result": {
            "group_id": group_id,
            "merge_id": merge_id,
            "review_state": review_state,
            "review_fingerprint": context.get("review_fingerprint"),
            "instruction_generation": int(context.get("instruction_generation") or 0),
            "base_head": context.get("base_head"),
            "merge_head": context.get("merge_head"),
            "snapshot_tree": context.get("snapshot_tree"),
            "changes": context.get("changes") or [],
            "conflict_origins": context.get("conflict_origins") or [],
            "conversation": context.get("conversation") or [],
            "held_test_operations": context.get("held_test_operations") or [],
            # 0481 T0010 rev1 — non-null while a chat turn's run is still working, so
            # the approval screen can show the wait in place instead of sending the
            # operator out to the generic AI-run dialog to find out what is happening.
            "pending_conversation": _pending_conversation_view(context),
            "resolver_provider": context.get("resolver_provider"),
            "auto_authority": bool(context.get("auto_authority")),
            "reconciliation_kind": context.get("reconciliation_kind"),
            "last_error": context.get("last_error"),
            "can_approve": pending,
            "can_reject": pending,
            "can_send": pending,
        },
    }


def review_conversation_brief(group_id: str, merge_id: int) -> dict:
    """What a review-conversation run has to be told about the review it is answering
    (0481 T0010 rev6, rejection 3).

    A chat turn on the approval screen used to be launched with the ORDINARY conflict
    mention: "two branches changed the same lines, resolve every conflict, call the bound
    resolve endpoint", plus a dump of a conflict session that is empty by then because the
    conflicts were resolved before the review even opened. The run therefore answered the
    prompt it was given instead of the question it was asked -- "conflict_count: 0, chunks:
    [], there is nothing to resolve, tell me what is confusing" -- and it could not resolve a
    reference like "what was the problem THIS time?", because not one turn of the
    conversation was ever handed to it.

    Deliberately does NOT go through get_merge_review: that materializes the pending run and
    takes the project git lock, and this is called from inside `send_review_message`'s
    `start_run()` -- before the turn is even recorded. A plain session read is all it needs.
    """
    session = db_git.get_session(merge_id)
    if session is None or session.get("group_id") != group_id:
        return {}
    context = db_git.session_context(session)
    return {
        "review_state": context.get("review_state"),
        "base_head": context.get("base_head"),
        "merge_head": context.get("merge_head"),
        "resolver_provider": context.get("resolver_provider"),
        "changes": context.get("changes") or [],
        "conversation": context.get("conversation") or [],
        "last_error": context.get("last_error"),
        "held_test_operations": context.get("held_test_operations") or [],
    }


def read_merge_review_file_diff(group_id: str, merge_id: int, path: str) -> dict:
    """Old(``base_head``)/new(``snapshot_tree``) content of one changed path in the
    frozen candidate — the review screen's per-file expand, reusing the same
    old/new payload shape ``read_group_file_diff`` already returns so the client's
    existing file-diff viewer needs no new prop shape."""
    _validate_blob_path(path)
    normalized = path.replace("\\", "/")
    _session, context, _project_id, base_root, _base_branch = _merge_review_session(group_id, merge_id)
    if not context.get("snapshot_tree"):
        raise GitServiceError(409, "review_not_ready", "this merge has not reached review yet")
    old = _diff_side_from_commit(base_root, context.get("base_head"), normalized)
    new = _diff_side_from_commit(base_root, context.get("snapshot_tree"), normalized)
    return {"ok": True, "data": {
        "group_id": group_id, "merge_id": merge_id, "path": path,
        "status": _diff_status(old, new, path), "old": old, "new": new,
    }}


def _refreeze_for_re_review(
    group_id: str, merge_id: int, base_root: Path, base_branch: str,
    context: dict, reason: str,
) -> dict:
    """D0006 §3.4/§3.6 — the target changed under review (or a conditional push was
    rejected). Discard the stale approval bookkeeping, re-freeze the WHOLE tree
    from scratch, and land back at re_review with a new fingerprint. Never
    commits, never reuses the old approval_attempt_id."""
    if reason == "push_rejected":
        # The caller already `git reset --hard ORIG_HEAD`ed the rolled-back merge
        # commit — but that commit had ALREADY completed the merge, so git cleared
        # MERGE_HEAD the moment it was created; resetting the ref/tree does not
        # bring MERGE_HEAD back. There is nothing "in progress" left to write-tree
        # from, and the base_head this candidate was built on may not even be
        # origin/base_branch's ancestor anymore (someone else's push moved it) —
        # so re-fetch and redo the SAME merge against the base's current tip
        # before falling through to the ordinary freeze below.
        project_id = _project_of_group(group_id)
        cfg = db_git.get_config(project_id) or {}
        fetch = _run_git(
            ["fetch", "origin"], cwd=base_root, timeout=GIT_NET_TIMEOUT_SEC,
            username=cfg.get("username"), secret=_load_secret_for(cfg) or "",
        )
        ff = _run_git(
            ["merge", "--ff-only", f"origin/{base_branch}"],
            cwd=base_root, timeout=GIT_LOCAL_TIMEOUT_SEC,
        ) if fetch.returncode == 0 else None
        if fetch.returncode != 0 or ff is None or ff.returncode != 0:
            context["review_state"] = REVIEW_STATE_RECONCILING
            context["reconciliation_kind"] = "push_remote_third"
            context["last_error"] = {"code": "base_diverged_after_rollback"}
            db_git.set_session_context(merge_id, context)
            return {"ok": True, "result": {
                "status": "reconciling", "review_state": REVIEW_STATE_RECONCILING,
            }}
        # Redo against the OLD MERGE COMMIT itself, not the raw group branch: that
        # commit's tree already carries the human-reviewed resolution (shared.txt
        # etc.), so re-merging it onto the refreshed base only re-raises a conflict
        # when the concurrent remote change actually touches the same content —
        # an unrelated concurrent change (the common case) auto-merges cleanly and
        # the approved resolution is preserved instead of being thrown away.
        old_merge_commit = context.get("merge_commit")
        redo = _run_git(
            ["-c", "merge.conflictStyle=zdiff3", "merge", "--no-commit", "--no-ff", old_merge_commit],
            cwd=base_root, timeout=GIT_LOCAL_TIMEOUT_SEC,
        )
        if redo.returncode != 0 and _unmerged_paths(base_root):
            # The advanced base now genuinely conflicts with the reviewed
            # resolution — a fresh conflict, not something a push-result
            # reconciler should try to auto-resolve. Park it for a human;
            # automatic re-resolution is out of scope for this retry path.
            _run_git(["merge", "--abort"], cwd=base_root, timeout=GIT_LOCAL_TIMEOUT_SEC)
            context["review_state"] = REVIEW_STATE_RECONCILING
            context["reconciliation_kind"] = "push_remote_third"
            context["last_error"] = {"code": "conflicts_after_remote_moved"}
            db_git.set_session_context(merge_id, context)
            return {"ok": True, "result": {
                "status": "reconciling", "review_state": REVIEW_STATE_RECONCILING,
            }}
    if not _merge_in_progress(base_root) or _unmerged_paths(base_root):
        context["review_state"] = REVIEW_STATE_RECONCILING
        context["reconciliation_kind"] = "apply_restore_failed"
        context["last_error"] = {"code": reason}
        db_git.set_session_context(merge_id, context)
        return {"ok": True, "result": {
            "status": "reconciling", "review_state": REVIEW_STATE_RECONCILING,
            "error": reason,
        }}
    snapshot = _freeze_commit_candidate(base_root, base_branch)
    context.update(snapshot)
    context["review_state"] = REVIEW_STATE_RE_REVIEW
    context["instruction_generation"] = int(context.get("instruction_generation") or 0) + 1
    context["approval_attempt_id"] = None
    context["merge_commit"] = None
    context["apply_phase"] = None
    context["last_error"] = {"code": reason}
    db_git.set_session_context(merge_id, context)
    return {"ok": True, "result": {
        "status": "re_review", "review_state": REVIEW_STATE_RE_REVIEW,
        "review_fingerprint": snapshot["review_fingerprint"],
        "changed_paths": [c["path"] for c in snapshot["changes"]],
        "error": reason,
    }}


def _complete_merge_review(
    group_id: str, merge_id: int, project_id: str, context: dict, *, pushed: bool,
) -> dict:
    context["review_state"] = REVIEW_STATE_COMPLETED
    context["apply_phase"] = "completed"
    db_git.set_session_context(merge_id, context)
    merge_commit = context.get("merge_commit") or ""
    merge_commit_short = merge_commit[:7] or None
    db_git.close_session(merge_id, "done")
    _set_status(group_id, "merged", merge_commit=merge_commit_short)
    _cleanup_group_slot(project_id, group_id)
    _emit("git_finalize_done", project_id, group_id, {
        "project": project_id, "group_id": group_id,
        "action": context.get("finalize_action") or SESSION_ACTION_DEFAULT,
        "status": "merged", "merge_commit": merge_commit_short, "pushed": pushed,
    })
    # "merged" (not "completed") on the top-level `status` — the pre-existing
    # external contract every caller of resolve_conflicts/resolve-token already
    # matches on (git_routes.py's token-consume check, the resolver dialog, the
    # worker's HTTP tool reader). `review_state` is where the NEW completed/
    # reconciling/re_review vocabulary lives; `status` keeps meaning what it
    # always meant to keep this a non-breaking extension (T0008 completion
    # criteria: existing TR/group-update flows unaffected).
    return {"ok": True, "result": {
        "status": "merged", "review_state": REVIEW_STATE_COMPLETED,
        "merge_commit": merge_commit_short, "pushed": pushed,
    }}


def _enter_reconciling(merge_id: int, context: dict, kind: str, *, schedule_retry: bool) -> dict:
    context["review_state"] = REVIEW_STATE_RECONCILING
    context["reconciliation_kind"] = kind
    context["reconcile_attempt_count"] = int(context.get("reconcile_attempt_count") or 0)
    context["reconcile_next_at"] = _seconds_from_now_iso(PUSH_RECONCILE_RETRY_INTERVAL_SEC) if schedule_retry else None
    db_git.set_session_context(merge_id, context)
    return {"ok": True, "result": {
        "status": "reconciling", "review_state": REVIEW_STATE_RECONCILING,
        "reconciliation_kind": kind,
    }}


def _seconds_from_now_iso(seconds: float) -> str:
    from datetime import datetime, timedelta, timezone
    return (datetime.now(timezone.utc) + timedelta(seconds=seconds)).isoformat()


def _iso_is_due(value: Optional[str]) -> bool:
    if not value:
        return False
    from datetime import datetime, timezone
    try:
        dt = datetime.fromisoformat(value)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return datetime.now(timezone.utc) >= dt
    except Exception:
        return False


def _conditionally_push_or_reconcile(
    group_id: str, merge_id: int, session: dict, project_id: str,
    base_root: Path, cfg: dict, base_branch: str, context: dict,
) -> dict:
    """D0006 §3.6 conditional push — CAS on ``expected_remote_head`` via
    ``--force-with-lease`` (safe for a fast-forward update, not a history rewrite),
    with an explicit unknown-result branch instead of folding it into failure."""
    session_action = session.get("finalize_action") or SESSION_ACTION_DEFAULT
    if session_action == "merge_only":
        return _complete_merge_review(group_id, merge_id, project_id, context, pushed=False)
    expected = context.get("expected_remote_head") or ""
    lease = f"{base_branch}:{expected}" if expected else base_branch
    push = _run_git(
        ["push", f"--force-with-lease={lease}", "origin", base_branch],
        cwd=base_root, timeout=GIT_NET_TIMEOUT_SEC,
        username=cfg.get("username"), secret=_load_secret_for(cfg) or "",
    )
    if push.returncode == 0:
        return _complete_merge_review(group_id, merge_id, project_id, context, pushed=True)
    stderr_l = (push.stderr or "").lower()
    if push.returncode == -1 or "timeout" in stderr_l or "could not resolve host" in stderr_l:
        return _enter_reconciling(merge_id, context, "push_remote_unknown", schedule_retry=True)
    if "stale info" in stderr_l or "rejected" in stderr_l or "fetch first" in stderr_l or "non-fast-forward" in stderr_l:
        _run_git(["reset", "--hard", "ORIG_HEAD"], cwd=base_root, timeout=GIT_LOCAL_TIMEOUT_SEC)
        return _refreeze_for_re_review(group_id, merge_id, base_root, base_branch, context, "push_rejected")
    # An error this function cannot classify is treated as "result unknown" rather
    # than assumed-failed (D0006 §3.6): the remote may or may not have the commit.
    return _enter_reconciling(merge_id, context, "push_remote_unknown", schedule_retry=True)


def approve_merge_review(
    group_id: str, merge_id: int, *, attempt_id: str, review_fingerprint: str,
    authority: str,
) -> dict:
    """D0006 §3.5·§3.6 / L0007 §2.8 ``approve_snapshot``.

    ``authority`` is ``"human"`` (the person pressing [승인]) or ``"automatic"``
    (the internal call `submit_resolution` makes for its own auto_authority
    session, immediately after freezing and BEFORE ever returning a review screen
    to the client — an automatic approval never reaches this function through the
    public HTTP route)."""
    session = db_git.get_session(merge_id)
    if session is None or session.get("group_id") != group_id:
        raise GitServiceError(404, "review_not_found", f"merge session {merge_id} not found")
    if db_git.session_kind(session) != db_git.SESSION_KIND_MERGE:
        raise GitServiceError(409, "review_not_ready", "not a general merge review session")
    project_id = _project_of_group(group_id)
    holder = f"review:{merge_id}:{uuid.uuid4()}"
    if not _acquire_lock(project_id, holder, wait_sec=LOCK_WAIT_SEC):
        raise GitServiceError(409, "git_busy", f"another git operation is in progress for '{project_id}'")
    try:
        session = db_git.get_session(merge_id)
        context = db_git.session_context(session)
        if authority == "automatic" and not context.get("auto_authority"):
            raise GitServiceError(403, "human_authority_required", "auto_authority was not recorded for this session")
        if context.get("approval_attempt_id") == attempt_id and context.get("merge_commit"):
            state = context.get("review_state")
            if state == REVIEW_STATE_COMPLETED:
                return {"ok": True, "result": {
                    "status": "already_applied", "review_state": state,
                    "merge_commit": (context.get("merge_commit") or "")[:7],
                }}
            return {"ok": True, "result": {
                "status": state or "reconciling", "review_state": state,
                "reconciliation_kind": context.get("reconciliation_kind"),
            }}
        if context.get("review_state") not in REVIEW_PENDING_STATES:
            raise GitServiceError(409, "review_not_ready", f"session is in state {context.get('review_state')!r}")
        if review_fingerprint != context.get("review_fingerprint"):
            raise GitServiceError(
                409, "stale_review", "the reviewed target has changed since this fingerprint was shown",
            )
        cfg = db_git.get_config(project_id) or {}
        base_root = _base_root_of(project_id)
        if base_root is None:
            raise GitServiceError(409, "invalid_state", "base checkout is not provisioned")
        base_branch = (cfg.get("base_branch") or "main").strip() or "main"

        pre_apply_review_state = context["review_state"]
        context["review_state"] = REVIEW_STATE_APPLYING
        db_git.set_session_context(merge_id, context)

        if not _live_candidate_matches_snapshot(base_root, context):
            return _refreeze_for_re_review(group_id, merge_id, base_root, base_branch, context, "identity_mismatch")

        try:
            # 0481 T0010 rev3: the candidate is a MERGE, not a write plan — see
            # `_validate_review_changed_paths`' docstring for why an extension we
            # have no validator for must not veto a merge here.
            errors = _validate_review_changed_paths(
                base_root, context, unregistered_extension="skip",
            )
            if errors:
                context["review_state"] = pre_apply_review_state
                # L0007 §2.7/§2.11: an unregistered-extension rejection is a
                # distinct error code from an ordinary parser/compile failure.
                code = (
                    "unsupported_syntax_validation"
                    if any(e.get("validator") == "unsupported" for e in errors)
                    else "syntax_validation_failed"
                )
                context["last_error"] = {"code": code, "files": errors}
                db_git.set_session_context(merge_id, context)
                return {"ok": True, "result": {
                    "status": "pre_commit_validation_failed", "review_state": pre_apply_review_state,
                    "errors": errors,
                }}

            context["approval_attempt_id"] = attempt_id
            context["apply_phase"] = "committing"
            db_git.set_session_context(merge_id, context)

            state = db_git.get_state(group_id) or {}
            branch = (state.get("branch")
                      or worktree_branch_name(project_id, _module_of(group_id), group_id))
            commit_proc = _run_git(
                [*_GIT_IDENT, "commit", "-m", _merge_commit_subject(branch, base_branch)],
                cwd=base_root, author_env=_author_env_from_cfg(cfg),
            )
            if commit_proc.returncode != 0:
                context["approval_attempt_id"] = None
                context["apply_phase"] = None
                context["review_state"] = pre_apply_review_state
                context["last_error"] = {"code": "commit_creation_failed", "detail": _last_line(commit_proc.stderr)}
                db_git.set_session_context(merge_id, context)
                return {"ok": True, "result": {
                    "status": "commit_creation_failed", "review_state": pre_apply_review_state,
                }}
            commit_sha = _rev_parse(base_root, "HEAD")
            commit_tree = _rev_parse(base_root, "HEAD^{tree}")
            if commit_tree != context.get("snapshot_tree"):
                # Defensive only — the identity check above already guarantees this.
                # The restore itself is verified, not assumed: a `reset --hard` that
                # fails (or leaves HEAD somewhere other than the pre-commit head) is
                # a DIFFERENT failure than "the commit did not match" — the tree this
                # process actually left behind is now unknown, so this must park at
                # `reconciling`/`apply_restore_failed` for a human, never silently
                # report the safe `pre_apply_review_state` it did not actually reach.
                reset_proc = _run_git(
                    ["reset", "--hard", "ORIG_HEAD"], cwd=base_root, timeout=GIT_LOCAL_TIMEOUT_SEC,
                )
                restored = (
                    reset_proc.returncode == 0
                    and _rev_parse(base_root, "HEAD") == context.get("base_head")
                )
                if not restored:
                    context["review_state"] = REVIEW_STATE_RECONCILING
                    context["reconciliation_kind"] = "apply_restore_failed"
                    context["last_error"] = {
                        "code": "commit_creation_failed",
                        "detail": "tree_mismatch_reset_failed",
                    }
                    db_git.set_session_context(merge_id, context)
                    return {"ok": True, "result": {
                        "status": "reconciling", "review_state": REVIEW_STATE_RECONCILING,
                        "reconciliation_kind": "apply_restore_failed",
                    }}
                context["approval_attempt_id"] = None
                context["apply_phase"] = None
                context["review_state"] = pre_apply_review_state
                context["last_error"] = {"code": "commit_creation_failed", "detail": "tree_mismatch"}
                db_git.set_session_context(merge_id, context)
                return {"ok": True, "result": {
                    "status": "commit_creation_failed", "review_state": pre_apply_review_state,
                }}
            context["merge_commit"] = commit_sha
            context["apply_phase"] = "committed_local"
            db_git.set_session_context(merge_id, context)
        except Exception:
            # Nothing has committed yet on ANY path that can reach this except
            # clause (every commit/tree-mismatch failure above already returns
            # instead of raising) — so an unexpected exception here (a git binary
            # gone missing mid-call, a DB write failure, …) is still safe to fully
            # revert, unlike a failure after the commit exists (handled below).
            context["approval_attempt_id"] = None
            context["apply_phase"] = None
            context["review_state"] = pre_apply_review_state
            context["last_error"] = {"code": "commit_creation_failed", "detail": "unexpected_error"}
            db_git.set_session_context(merge_id, context)
            raise
        try:
            return _conditionally_push_or_reconcile(
                group_id, merge_id, session, project_id, base_root, cfg, base_branch, context,
            )
        except Exception:
            # The commit above already exists locally — D0006 §3.6's rollback
            # promise ends at push, so an unexpected exception here (as opposed to
            # the classified push outcomes _conditionally_push_or_reconcile already
            # returns instead of raising) must land at reconciling/push_remote_unknown,
            # never revert review_state, and never touch the local commit.
            context["review_state"] = REVIEW_STATE_RECONCILING
            context["reconciliation_kind"] = "push_remote_unknown"
            context["reconcile_attempt_count"] = int(context.get("reconcile_attempt_count") or 0)
            context["reconcile_next_at"] = _seconds_from_now_iso(PUSH_RECONCILE_RETRY_INTERVAL_SEC)
            context["last_error"] = {"code": "push_exception", "detail": "unexpected_error"}
            db_git.set_session_context(merge_id, context)
            return {"ok": True, "result": {
                "status": "reconciling", "review_state": REVIEW_STATE_RECONCILING,
                "reconciliation_kind": "push_remote_unknown",
            }}
    finally:
        db_git.release_lock(project_id, holder)


def reject_merge_review(
    group_id: str, merge_id: int, *, reason: str, provider_id: Optional[str],
    provider_pinned: bool, start_run: "Callable[[Optional[str]], Optional[str]]",
) -> dict:
    """D0006 §3.7 / L0007 §2.9 ``reject_and_return_to_resolver``.

    Restores the conflict by re-running the ORIGINAL merge (same base_head,
    same merge_head, captured in ``resolver_baseline`` at session creation) —
    git's merge algorithm is deterministic over the same two inputs, so this
    regenerates byte-identical conflict markers without needing a separate
    file-by-file snapshot of the pre-resolution working tree. ``start_run`` is
    supplied by the caller (the API layer already knows how to build a
    resolve_conflict mention/token; this module must not reach into it) and is
    invoked with the new run's id filled in once known, mirroring every other
    ``issue_builder``/``ai_run_id`` seam in this codebase."""
    session, context, project_id, base_root, base_branch = _merge_review_session(group_id, merge_id)
    if context.get("review_state") not in REVIEW_PENDING_STATES:
        raise GitServiceError(409, "review_not_ready", f"session is in state {context.get('review_state')!r}")
    baseline = context.get("resolver_baseline") or {}
    base_head = baseline.get("base_head")
    merge_head = baseline.get("merge_head")
    if not base_head or not merge_head:
        raise GitServiceError(409, "restoration_verification_failed", "no resolver baseline recorded for this session")

    holder = f"review:{merge_id}:{uuid.uuid4()}"
    if not _acquire_lock(project_id, holder, wait_sec=LOCK_WAIT_SEC):
        raise GitServiceError(409, "git_busy", f"another git operation is in progress for '{project_id}'")
    try:
        # 0009-TR rev5 (AI review finding): the checks above ran WITHOUT the lock,
        # so an approval (or another rejection) may have moved this session in the
        # meantime. Re-read and re-check here: restoring the conflict from a
        # context read before that transition would both undo it in the checkout
        # and write that transition's bookkeeping back out of a pre-transition copy.
        context = db_git.session_context(db_git.get_session(merge_id))
        if context.get("review_state") not in REVIEW_PENDING_STATES:
            raise GitServiceError(409, "review_not_ready", f"session is in state {context.get('review_state')!r}")
        baseline = context.get("resolver_baseline") or {}
        base_head = baseline.get("base_head")
        merge_head = baseline.get("merge_head")
        if not base_head or not merge_head:
            raise GitServiceError(409, "restoration_verification_failed", "no resolver baseline recorded for this session")
        if (base_root / ".git" / "MERGE_HEAD").exists():
            _run_git(["merge", "--abort"], cwd=base_root, timeout=GIT_LOCAL_TIMEOUT_SEC)
        current_head = _rev_parse(base_root, "HEAD")
        if current_head != base_head:
            context["review_state"] = REVIEW_STATE_RECONCILING
            context["reconciliation_kind"] = "restoration_verification_failed"
            context["last_error"] = {"code": "base_head_moved"}
            db_git.set_session_context(merge_id, context)
            raise GitServiceError(
                409, "restoration_verification_failed",
                "the base checkout has moved since this merge started",
            )
        # Merge by the group's BRANCH NAME, not the raw merge_head sha: git's own
        # conflict-marker label (">>>>>>> <name>") comes from whatever ref name was
        # given to `merge`, and the original conflict was created the same way
        # (finalize()'s `merge ... branch`). Merging the bare sha would still
        # conflict on the same content but relabel every ">>>>>>> branch" marker
        # as ">>>>>>> <sha>" — byte-DIFFERENT from what the AI/human saw before,
        # which is exactly the identity this restore promises. merge_head is kept
        # as the integrity check: if the branch has moved, its tip no longer
        # matches the sha this session was frozen against, and that mismatch (not
        # a silently-different merge) is what must fail restoration.
        state = db_git.get_state(group_id) or {}
        branch = (state.get("branch")
                  or worktree_branch_name(project_id, _module_of(group_id), group_id))
        redo = _run_git(
            ["-c", "merge.conflictStyle=zdiff3", "merge", "--no-commit", "--no-ff", branch],
            cwd=base_root, timeout=GIT_LOCAL_TIMEOUT_SEC,
        )
        remaining = _unmerged_paths(base_root)
        original_paths = {row["path"] for row in db_git.session_files(merge_id)}
        redo_merge_head = _rev_parse(base_root, "MERGE_HEAD")
        if redo.returncode == 0 or redo_merge_head != merge_head or set(remaining) != original_paths:
            context["review_state"] = REVIEW_STATE_RECONCILING
            context["reconciliation_kind"] = "restoration_verification_failed"
            context["last_error"] = {"code": "restoration_verification_failed"}
            db_git.set_session_context(merge_id, context)
            raise GitServiceError(
                409, "restoration_verification_failed",
                "could not restore the original conflict markers",
            )
        for key in (
            "snapshot_tree", "snapshot_manifest", "review_fingerprint", "changes",
            "resolver_run_id", "resolver_provider", "approval_attempt_id",
            "merge_commit", "apply_phase", "last_error", "held_test_operations",
        ):
            context.pop(key, None)
        db_git.set_session_context(merge_id, context)
        # Reset every file's resolved flag — the redo merge put fresh markers back.
        for row in db_git.session_files(merge_id):
            get_store()._execute(
                "UPDATE git_merge_session_file SET resolved = 0, resolved_at = NULL "
                "WHERE merge_id = ? AND path = ?",
                [merge_id, row["path"]],
            )
        context["auto_authority"] = False
        context["review_state"] = None
        conversation = context.get("conversation") or []
        conversation.append({
            "turn_id": str(uuid.uuid4()), "role": "human", "message": reason,
            "provider_id": provider_id, "status": "rejected", "created_at": now_iso(),
        })
        context["conversation"] = conversation[-MAX_CHAT_TURNS:]
        db_git.set_session_context(merge_id, context)
    finally:
        db_git.release_lock(project_id, holder)

    run_id = start_run(reason)
    return {"ok": True, "result": {
        "status": "returned_to_resolver", "review_state": None,
        "instruction_generation": int(context.get("instruction_generation") or 0),
        "resolver_run_id": run_id,
    }}


def _conversation_run_detail(run_id: str) -> tuple[Optional[dict], bool]:
    """Read a review-conversation run's detail. Returns ``(detail, lost)``.

    ``lost`` is True ONLY when the run id itself is gone (``get_run_detail``
    answers 404 ``run_not_found``): the process that owned it restarted before
    finalize wrote a row, so no later poll can ever observe it finishing. Any
    other failure (a transient DB error, an import problem) returns
    ``(None, False)`` — "ask again next poll" — because treating a hiccup as a
    lost run would throw away an answer that is still coming.
    """
    try:
        from modules.flow_gate.services.ai_invoke import diagnostics as ai_diagnostics

        return ai_diagnostics.get_run_detail(run_id), False
    except Exception as exc:  # HTTPException(404) is the only *decidable* failure
        return None, getattr(exc, "status_code", None) == 404


def _pending_conversation_view(context: dict) -> Optional[dict]:
    """0481 T0010 rev1 — the in-flight chat turn, as the approval screen sees it.

    The review screen's chat used to be fire-and-forget: ``send_review_message``
    starts a run and nothing in the payload said one was in flight, so the only
    surface that could tell the operator whether the AI was working at all was
    the generic AI-run dialog — "it leaves the dialog entirely and shows the default
    AI-run dialog" (2026-09-08 rejection). This block is what lets the operator stay
    in the dialog and wait: it names the run, its live status, its provider and
    how long it has been going, so the screen can show the wait in place and keep
    polling until the answer lands. ``None`` means "no turn is in flight", which
    is also the signal the client stops waiting on.
    """
    run_id = context.get("pending_conversation_run_id")
    if not run_id:
        return None
    detail, lost = _conversation_run_detail(run_id)
    detail = detail or {}
    provider = detail.get("provider")
    if isinstance(provider, dict):
        provider_name = provider.get("name") or provider.get("id")
    else:
        provider_name = detail.get("provider_name") or detail.get("provider_id") or (
            provider if isinstance(provider, str) else None
        )
    return {
        "run_id": run_id,
        "status": "lost" if lost else (str(detail.get("status") or "unknown").lower() or "unknown"),
        "provider": provider_name,
        "started_at": detail.get("started_at"),
        "elapsed_ms": detail.get("elapsed_ms"),
        "write_requested": bool(context.get("pending_conversation_write_requested")),
        "allow_test_edits": bool(context.get("pending_conversation_allow_test_edits")),
    }


def _materialize_pending_conversation_run(
    group_id: str, merge_id: int, project_id: str, base_root: Path, base_branch: str,
) -> None:
    """Lazily folds a finished conversation run's answer into ``conversation`` the
    next time the review screen is read (L0007 §2.9 — this process starts runs
    asynchronously; nothing else calls back into this module when one finishes).

    A propose-only run (or a write turn whose run finished without ever
    submitting a plan) just appends its ``last_message`` — L0007 §2.9's "on run
    success without plan" branch. A write turn whose run DID submit a plan
    (``get_run_detail(run_id).write_plan`` — the bound contract from the Q&A on
    0009-TR) is applied here via ``_apply_write_plan_locked``, and its outcome
    (new fingerprint / held_only / apply_failed / rollback_verification_failed)
    becomes the AI turn instead of the run's raw last_message. Whenever the
    plan is actually recorded against the session (``re_review`` or
    ``held_only`` — i.e. the plan passed the fingerprint/identity checks), any
    ``held_test_operations`` it carried (L0007 §2.7) are persisted onto the
    session (``get_merge_review``'s ``held_test_operations``) so they stay
    visible for the human's second explicit [테스트 편집 포함 재지시] action;
    a rejected plan (``apply_failed``/``rollback_verification_failed``)
    changes nothing about the session's prior held-operations display.

    Before any of the above: if the review this run was bound to at launch
    (``pending_conversation_start_fingerprint``/``_start_generation``, and
    implicitly its pending ``review_state``) is no longer the one on screen —
    approved, rejected, or superseded by an earlier write turn's new candidate
    while this run was in flight — the result is discarded as ``stale_run``
    instead (L0007 §2.9), regardless of whether it carried a write plan.

    0009-TR rev5 (AI review finding): that decision and everything it authorizes
    happen inside ONE hold of the project git lock — the same lock every review
    state transition (``approve_merge_review``, ``reject_merge_review``,
    ``_apply_write_plan_locked``) takes before it moves the session — so nothing
    can approve, reject or refreeze between "this run is not stale" and the
    result being applied or appended. Clearing the pending bookkeeping under
    that hold is also the CLAIM on the run: two review screens polling at the
    same instant materialize it exactly once. The lock is taken non-blocking
    (``wait_sec=0``) because this is the read path — when another git operation
    owns the project, the honest thing is to leave the run pending and fold it
    in on the next poll against whatever state that operation leaves behind,
    never against a state nobody re-checked."""
    session = db_git.get_session(merge_id)
    context = db_git.session_context(session) if session else {}
    run_id = context.get("pending_conversation_run_id")
    if not run_id:
        return
    detail, lost = _conversation_run_detail(run_id)
    if detail is None and not lost:
        return
    if detail is not None and (detail.get("status") or "").lower() not in ("finished", "done", "completed", "failed", "error"):
        return  # still running

    holder = f"review:{merge_id}:{uuid.uuid4()}"
    if not _acquire_lock(project_id, holder, wait_sec=0):
        return
    try:
        context = db_git.session_context(db_git.get_session(merge_id))
        if context.get("pending_conversation_run_id") != run_id:
            return  # a concurrent poll already claimed and materialized this run

        # 0009-TR rev5: the RUN row is re-read inside the hold too, not just the
        # session. `detail` above was fetched BEFORE the lock, and
        # `submit_review_write_plan` (the worker-token window, which takes no git
        # lock) can still attach a newer plan to this run right up until the claim
        # below — the run's LAST submission is the one that counts
        # (`test_review_gate_write_plan_submission_overwrites_the_runs_own_prior_plan`),
        # so applying the pre-lock copy would silently drop a submission the worker
        # was told was `accepted`. EVERY input to the decision below has to come
        # from inside this hold, not just the session state.
        detail, lost = _conversation_run_detail(run_id)
        if detail is None and not lost:
            return
        if detail is not None and (detail.get("status") or "").lower() not in ("finished", "done", "completed", "failed", "error"):
            return  # went back to running under the lock: leave it pending
        detail = detail or {}

        # Only fold this run's result in if the review it started against is
        # STILL the one on screen — same pending review_state, same
        # review_fingerprint, same instruction_generation as when
        # `send_review_message` launched it (start values persisted there). A
        # human can approve, reject, or (via an EARLIER write turn) already
        # refreeze a new candidate while this run was in flight; in every one of
        # those cases the run's answer/plan describes a target that no longer
        # exists and must be discarded as `stale_run` (L0007 §2.9) — never
        # applied, never appended as if it still answered the current candidate.
        # Checking `review_state` in addition to fingerprint/generation matters
        # because approval does NOT change either of those.
        start_fingerprint = context.get("pending_conversation_start_fingerprint")
        start_generation = int(context.get("pending_conversation_start_generation") or 0)
        stale_run = (
            context.get("review_state") not in REVIEW_PENDING_STATES
            or context.get("review_fingerprint") != start_fingerprint
            or int(context.get("instruction_generation") or 0) != start_generation
        )

        write_requested = bool(context.get("pending_conversation_write_requested"))
        succeeded = bool(detail.get("succeeded"))
        plan = detail.get("write_plan") if (write_requested and succeeded and not stale_run) else None

        # The claim is persisted BEFORE the plan is applied: should this process
        # die mid-apply, the next poll must not apply the same plan a second
        # time on top of the candidate this one already changed.
        for key in (
            "pending_conversation_run_id", "pending_conversation_write_requested",
            "pending_conversation_allow_test_edits",
            "pending_conversation_start_fingerprint", "pending_conversation_start_generation",
        ):
            context.pop(key, None)
        db_git.set_session_context(merge_id, context)

        apply_result: Optional[dict] = None
        if plan:
            try:
                apply_result = _apply_write_plan_locked(
                    group_id, merge_id, plan, base_root, base_branch,
                )
            except GitServiceError as exc:
                apply_result = {"status": "apply_failed", "errors": [{"message": f"{exc.code}: {exc.message}"}]}
            # _apply_write_plan_locked persisted its own context changes (under
            # this same hold) — re-read so the turn appended below lands on top
            # of them instead of a stale copy that would silently undo them.
            context = db_git.session_context(db_git.get_session(merge_id))

        conversation = context.get("conversation") or []
        if stale_run:
            # 0481 T0010 rev6 (rejection 3): the answer is KEPT. Until rev5 this branch
            # replaced whatever the run had said with one sentence -- "the approval target
            # changed, so the result was discarded (stale_run). Instruct again." -- and the
            # operator, who had asked "what was the problem this time?", got that instead of
            # the answer, twice, with no way to tell WHICH of the three identity checks
            # fired. The plan-discard rule (L0007 §2.9) is unchanged and `plan` above still
            # enforces it: nothing a stale run submitted is ever applied. But an ANSWER is
            # text about a candidate one revision behind, not a danger, so it is appended
            # under a line that says exactly what moved underneath it.
            changed = []
            if context.get("review_state") not in REVIEW_PENDING_STATES:
                changed.append(f"승인 대기가 끝났습니다(현재 {context.get('review_state')})")
            if context.get("review_fingerprint") != start_fingerprint:
                changed.append("승인 대상이 새 후보로 바뀌었습니다")
            if int(context.get("instruction_generation") or 0) != start_generation:
                changed.append("재지시로 지시 회차가 올라갔습니다")
            note = "이 답을 만드는 동안 " + ", ".join(changed) + "."
            note += " 아래 내용은 그 이전 후보를 보고 쓴 것입니다(stale_run)."
            if write_requested and detail.get("write_plan"):
                note += " 함께 제출된 수정안은 적용하지 않았습니다."
            answer = (detail.get("last_message") or "").strip()
            conversation.append({
                "turn_id": str(uuid.uuid4()), "role": "ai",
                "message": f"{note}\n\n{answer}" if answer else note,
                "provider_id": detail.get("provider_id"), "status": "stale_run",
                "created_at": now_iso(),
            })
        elif lost:
            # 0481 T0010 rev1: the run id is gone for good, so no later poll can ever
            # fold an answer in. Before this branch the pending bookkeeping stayed set
            # forever and every further message was refused with `re_instruction_busy`
            # — the operator waited in the approval screen for a reply that could not
            # arrive, and had nowhere to go but the generic AI-run dialog to find out.
            # Say so in the conversation and free the chat for another turn.
            conversation.append({
                "turn_id": str(uuid.uuid4()), "role": "ai",
                "message": "이 지시를 맡은 실행의 기록이 남아 있지 않아 답을 받지 못했습니다(run_lost). 같은 내용을 다시 보내 주십시오.",
                "provider_id": None, "status": "run_lost",
                "created_at": now_iso(),
            })
        elif apply_result is not None:
            status = apply_result.get("status")
            # 0009-TR rev3 (AI review finding 2): held test operations must be
            # OBSERVABLE, not just structurally accepted — surface them in the same
            # AI turn that reports the apply outcome, in addition to the structured
            # `held_test_operations` context field `get_merge_review` now exposes.
            held = apply_result.get("held_test_operations") or []
            held_note = ""
            if held:
                held_desc = ", ".join(f"{op.get('path', '?')}({op.get('purpose', '')})" for op in held)
                held_note = f" 보류된 테스트 편집 {len(held)}건(미적용, [테스트 편집 포함 재지시]로 재요청 가능): {held_desc}"
            if status == "re_review":
                paths = ", ".join(apply_result.get("changed_paths") or []) or "(없음)"
                message = f"요청한 수정을 적용해 새 승인 대상을 만들었습니다. 변경된 파일: {paths}" + held_note
            elif status == "held_only":
                held_desc = ", ".join(f"{op.get('path', '?')}({op.get('purpose', '')})" for op in held) or "(없음)"
                message = f"제출된 연산이 모두 테스트 경로라 보류했습니다({len(held)}건, 미적용): {held_desc}. [테스트 편집 포함 재지시]로 다시 요청하십시오."
            elif status == "rollback_verification_failed":
                message = "수정 적용 실패 후 상태 복구 확인에도 실패했습니다 — 사람 확인이 필요합니다."
            else:
                message = "수정 적용에 실패했습니다: " + json.dumps(apply_result.get("errors") or [], ensure_ascii=False) + held_note
            conversation.append({
                "turn_id": str(uuid.uuid4()), "role": "ai", "message": message,
                "provider_id": detail.get("provider_id"),
                "status": "accepted" if status in ("re_review", "held_only") else "failed",
                "created_at": now_iso(),
            })
        else:
            message = detail.get("last_message") or ("(no answer)" if succeeded else "(run failed)")
            conversation.append({
                "turn_id": str(uuid.uuid4()), "role": "ai", "message": message,
                "provider_id": detail.get("provider_id"), "status": "accepted" if succeeded else "failed",
                "created_at": now_iso(),
            })
        context["conversation"] = conversation[-MAX_CHAT_TURNS:]
        db_git.set_session_context(merge_id, context)
    finally:
        db_git.release_lock(project_id, holder)


def send_review_message(
    group_id: str, merge_id: int, *, message: str, provider_id: Optional[str],
    provider_pinned: bool, apply_requested: bool,
    start_run: "Callable[[], Optional[str]]",
    allow_test_edits: bool = False,
) -> dict:
    """D0006 §3.7 / L0007 §2.9 ``send_review_message``. A propose-only turn
    (``apply_requested=False``) asks a question against the frozen candidate and
    never touches the source tree. An explicit-write turn (``apply_requested=True``,
    flowgate.default.0481 T0008 item 1) starts the SAME kind of run but marks the
    session as waiting for an anchored write plan (Q&A on 0009-TR's bound
    contract); the run's AI has no write tool at all and can only submit that plan
    to ``POST .../write-plan-token`` (``submit_review_write_plan``). Nothing is
    applied synchronously here — ``_materialize_pending_conversation_run`` calls
    ``_apply_write_plan_locked`` once the run is later observed finished, exactly like the
    propose-only turn's answer is folded in lazily today. This launch also
    freezes ``pending_conversation_start_fingerprint``/``_start_generation`` from
    the CURRENT (already-validated-pending) ``review_fingerprint``/
    ``instruction_generation``, so materialization can later tell a `stale_run`
    apart from one still answering the review on screen (L0007 §2.9). Both that
    freeze and the human turn are written from a session re-read INSIDE the
    project git lock (0009-TR rev5), so this turn can never write back over an
    approval/rejection that landed while the run was starting."""
    session, context, project_id, _base_root, _base_branch = _merge_review_session(group_id, merge_id)
    if context.get("review_state") not in REVIEW_PENDING_STATES:
        raise GitServiceError(409, "review_not_ready", f"session is in state {context.get('review_state')!r}")
    if context.get("pending_conversation_run_id"):
        raise GitServiceError(409, "re_instruction_busy", "a review conversation run is already active")
    if not (message or "").strip():
        raise GitServiceError(400, "invalid_message", "message must not be blank")
    if len(message) > MAX_CHAT_MESSAGE_CHARS:
        raise GitServiceError(400, "invalid_message", f"message exceeds {MAX_CHAT_MESSAGE_CHARS} characters")
    if not provider_pinned:
        raise GitServiceError(422, "provider_not_pinned", "provider_pinned must be true")

    # Starting the run stays OUTSIDE the project git lock (the same shape
    # `reject_merge_review` uses): `start_run` reaches into the AI-invoke stack,
    # which is not this module's critical section and must never run while the
    # non-reentrant git lock is held. That makes the checks above advisory — a
    # human can approve or reject while the run is starting — so NOTHING is
    # written from the context read above. Everything below re-reads the session
    # inside the lock and re-checks the same conditions, so this turn can never
    # overwrite (or resurrect) a state transition that won the race; the run it
    # started is simply left unrecorded, and that run's own write-plan
    # submission is refused by `submit_review_write_plan`'s pending-run check.
    run_id = start_run()
    holder = f"review:{merge_id}:{uuid.uuid4()}"
    if not _acquire_lock(project_id, holder, wait_sec=LOCK_WAIT_SEC):
        raise GitServiceError(409, "git_busy", f"another git operation is in progress for '{project_id}'")
    try:
        context = db_git.session_context(db_git.get_session(merge_id))
        if context.get("review_state") not in REVIEW_PENDING_STATES:
            raise GitServiceError(409, "review_not_ready", f"session is in state {context.get('review_state')!r}")
        if context.get("pending_conversation_run_id"):
            raise GitServiceError(409, "re_instruction_busy", "a review conversation run is already active")
        conversation = context.get("conversation") or []
        conversation.append({
            "turn_id": str(uuid.uuid4()), "role": "human", "message": message,
            "provider_id": provider_id, "status": "accepted", "created_at": now_iso(),
        })
        context["conversation"] = conversation[-MAX_CHAT_TURNS:]
        if apply_requested:
            context["pending_conversation_write_requested"] = True
            context["pending_conversation_allow_test_edits"] = bool(allow_test_edits)
        # L0007 §2.9 stale_run guard: freeze the review identity THIS run is bound to
        # at launch, so `_materialize_pending_conversation_run` can tell — once the
        # run finishes, possibly much later — whether the review it answered is
        # still the one on screen.
        context["pending_conversation_start_fingerprint"] = context.get("review_fingerprint")
        context["pending_conversation_start_generation"] = int(context.get("instruction_generation") or 0)
        context["pending_conversation_run_id"] = run_id
        db_git.set_session_context(merge_id, context)
    finally:
        db_git.release_lock(project_id, holder)
    return {"ok": True, "result": {
        "status": "accepted", "review_state": context.get("review_state"),
        "run_id": run_id, "review_fingerprint": context.get("review_fingerprint"),
        "instruction_generation": int(context.get("instruction_generation") or 0),
    }}


def reconcile_push_session(merge_id: int, trigger: str = "periodic") -> Optional[dict]:
    """D0006 §3.6 / L0007 §2.8.1 ``reconcile_push_session`` — re-asks the remote
    where the base branch actually points and settles a session stuck in
    ``reconciling`` after a push whose result this process never learned. Not a
    re-entry into ``approve_merge_review``: it never commits or pushes again."""
    session = db_git.get_session(merge_id)
    if session is None or db_git.session_kind(session) != db_git.SESSION_KIND_MERGE:
        return None
    group_id = session["group_id"]
    context = db_git.session_context(session)
    if context.get("review_state") != REVIEW_STATE_RECONCILING:
        return None
    if context.get("reconciliation_kind") not in RECONCILE_AUTO_RETRY_KINDS:
        return None
    if trigger != "server_startup" and not _iso_is_due(context.get("reconcile_next_at")):
        return None
    project_id = _project_of_group(group_id)
    holder = f"reconcile:{merge_id}:{uuid.uuid4()}"
    if not _acquire_lock(project_id, holder, wait_sec=LOCK_WAIT_SEC):
        return None
    try:
        session = db_git.get_session(merge_id)
        context = db_git.session_context(session)
        if context.get("review_state") != REVIEW_STATE_RECONCILING:
            return {"ok": True, "result": {"status": context.get("review_state")}}
        context["reconcile_next_at"] = _seconds_from_now_iso(PUSH_RECONCILE_RETRY_INTERVAL_SEC)
        context["reconcile_attempt_count"] = int(context.get("reconcile_attempt_count") or 0) + 1
        db_git.set_session_context(merge_id, context)
        cfg = db_git.get_config(project_id) or {}
        base_root = _base_root_of(project_id)
        base_branch = (cfg.get("base_branch") or "main").strip() or "main"
        if base_root is None:
            return {"ok": True, "result": {"status": "retry_scheduled"}}

        observed: Optional[str] = None
        for i, delay in enumerate(PUSH_RECONCILE_DELAYS_SEC):
            if delay:
                time.sleep(delay)
            observed = _query_remote_ref(base_root, cfg, base_branch)
            if observed is not None:
                break

        merge_commit = context.get("merge_commit")
        expected = context.get("expected_remote_head")
        if observed is not None and merge_commit and observed == merge_commit:
            context["review_state"] = REVIEW_STATE_COMPLETED
            context["apply_phase"] = "completed"
            context.pop("reconciliation_kind", None)
            context.pop("reconcile_next_at", None)
            db_git.set_session_context(merge_id, context)
            db_git.close_session(merge_id, "done")
            _set_status(group_id, "merged", merge_commit=merge_commit[:7])
            _cleanup_group_slot(project_id, group_id)
            _emit("git_finalize_done", project_id, group_id, {
                "project": project_id, "group_id": group_id, "status": "merged",
                "merge_commit": merge_commit[:7], "pushed": True,
            })
            return {"ok": True, "result": {"status": "completed"}}
        if observed is not None and observed == expected:
            _run_git(["reset", "--hard", "ORIG_HEAD"], cwd=base_root, timeout=GIT_LOCAL_TIMEOUT_SEC)
            context["approval_attempt_id"] = None
            context["merge_commit"] = None
            context["apply_phase"] = None
            context["review_state"] = REVIEW_STATE_PENDING
            context.pop("reconciliation_kind", None)
            context.pop("reconcile_next_at", None)
            db_git.set_session_context(merge_id, context)
            return {"ok": True, "result": {"status": "pending_review"}}
        if observed is not None:
            context["reconciliation_kind"] = "push_remote_third"
            context["reconcile_next_at"] = None
            db_git.set_session_context(merge_id, context)
            _emit("git_merge_review_manual_reconciliation", project_id, group_id, {
                "project": project_id, "group_id": group_id, "merge_id": merge_id,
                "observed": observed,
            })
            return {"ok": True, "result": {"status": "manual_reconciliation_needed"}}
        return {"ok": True, "result": {"status": "retry_scheduled"}}
    finally:
        db_git.release_lock(project_id, holder)


def reconcile_due_merge_review_sessions(
    trigger: str, sessions: Optional[list[dict]] = None
) -> None:
    """Scan open general-merge sessions for a due reconciliation.

    A caller may pass an already-read open-session list; without one this reads
    its own list (the periodic sweep-daemon path).
    """
    for session in (sessions if sessions is not None else db_git.list_open_sessions()):
        if db_git.session_kind(session) != db_git.SESSION_KIND_MERGE:
            continue
        context = db_git.session_context(session)
        if context.get("review_state") != REVIEW_STATE_RECONCILING:
            continue
        try:
            reconcile_push_session(int(session["merge_id"]), trigger=trigger)
        except Exception:
            _log.warning(
                "merge review reconciliation failed for merge_id=%s", session.get("merge_id"),
                exc_info=True,
            )


# ── Post-finalize slot cleanup (flowgate.default.0182 NR0003 §5) ─────────────
# Before 0182 nothing ever removed a finalized group's leftovers: the worktree
# directory (a full source copy per group), the local work branch ref, and the
# ledger row accumulated forever (delete_config intentionally leaves worktrees
# alone, P0005 §2-3 — that guard is about CONFIG deletion and stays). Cleanup
# now runs best-effort right after a finalize reaches merged/pushed, plus a
# manual backlog sweep for everything that piled up before this landed.













# ── Auto-recovery sweep (flowgate.default.0205 P scenario 6 / L §2.5) ─────────

from .git.cleanup import (
    cleanup_disposed_group,
    cleanup_terminal_slots,
    _ttl_expired,
    _emit_auto_aborted,
    _close_orphan,
    _auto_abort_session,
    _sweep_tr_session,
    _sweep_group_update_session,
    merge_session_sweep,
    _open_sessions_after,
    _start_sweep_daemon,
    startup_recovery,
)


















# ── Boot recovery (flowgate.default.0205 P scenario 7 / L §2.6) ───────────────



# ── Project git status aggregation (flowgate.default.0162 P §2 / L §2.2) ──────


from .git.status import (
    test_connection,
    base_checkout_dirty_status,
    _build_unpushed,
    project_git_status,
)




# ── Manual recovery operations (flowgate.default.0162 P §3 / L §2.4) ──────────







# ── Base-checkout explicit commit / revert (flowgate.default.0177 — L0002) ────
# A src-content save writes straight into the base checkout (an admin edit),
# leaving it dirty and tripping the E3 merge guard for EVERY group of the
# project. These operations are the sanctioned way OUT of that state: an
# explicit, visible commit onto the base branch (no push — the next merge
# finalize carries it), or a per-file restore to HEAD. Scope always matches the
# E3 guard exactly: tracked-file changes only.















# ── Approval-ride-along git action (flowgate.default.0162 P §1 / L §2.1) ──────







