"""Commit-message pipeline and TR commit point / cancel / revert-reapply helpers.

Extracted from git_service.py (flowgate.default.0550 T0011, D0006 §3.2/부록 A).
"""
from __future__ import annotations

import logging
import re
import uuid
from pathlib import Path
from typing import Optional, Sequence

from modules.flow_gate.db import documents as db_documents
from modules.flow_gate.db import groups as db_groups
from modules.flow_gate.services import path_exclusion_rules

from .command import GIT_LOCAL_TIMEOUT_SEC
from .credentials import GitServiceError, _author_env_from_cfg
from .refs import _commits_present, _worktree_untracked_paths

_log = logging.getLogger(__name__)


AUTO_COMMIT_MSG = "chore: finalize workflow changes"


AUTO_COMMIT_DESIGN_TYPES = ("D", "DB", "P", "L")


# ── Commit message pipeline (flowgate.default.0173 — D0002/P0003/L0004) ────────
# Finalize-generated commit subjects are resolved through a fallback chain:
# approved-TR draft → ASCII group title → translated title → fixed English phrase.
COMMIT_SUBJECT_MAX = 200               # normalized subject max length (L0004 §1)


TRANSLATE_TIMEOUT_SEC = 3              # translate HTTP timeout (connect+read)


TRANSLATE_SOURCE = "auto"              # auto-detect source language (CH 0168.0008)


TRANSLATE_TARGET = "en"


# TR doc_review_status set whose commit_message draft is accepted (L0004 §1).
DRAFT_ACCEPT_STATUSES = ("approved", "wf_done")


FIXED_FALLBACK_SUBJECT = "{commit_type}: finalize workflow changes"   # L0004 §1 (D0002 §3-4)


# Known machine-translation hallucinations / web boilerplate (lowercased, punctuation
# stripped, exact match) that must never become a commit subject (CH 0168.0008).
BOILERPLATE_BLACKLIST = frozenset({
    "log in", "login", "sign in", "sign up", "sign out", "skip to content",
    "home", "menu", "search", "about", "contact", "register", "submit",
    "copyright", "all rights reserved", "read more", "learn more",
})


# flowgate.default.0462 T0005 — the TR commit point's ASCII fail-closed fallback. The
# TR's own draft failed every check (missing / non-ASCII / oversized); this keeps the
# document identifiable without a translate round-trip.
TR_FALLBACK_SUBJECT = "chore: approve {doc_code}"


# flowgate.default.0462 T0005 — a conventional-commit type prefix, e.g. "fix(git): " or
# "feat: ". Matched so an already-conventional TR draft is not double-wrapped with a
# second type (`conventional_subject("chore", "fix(git): x")` would read as noise).
_CONVENTIONAL_SUBJECT_RE = re.compile(r"^[a-z][a-z0-9]*(\([^()\r\n]+\))?!?: \S")


def is_conventional_subject(text: str) -> bool:
    """flowgate.default.0462 T0005 §4-1 — is ``text`` already ``type(scope): summary``?

    A capital type or a colon with no following space is not conventional and is passed
    through to be wrapped, not mistaken for one already in the right shape.
    """
    return bool(_CONVENTIONAL_SUBJECT_RE.match(text or ""))


def derive_commit_type(group_id: str) -> Optional[str]:
    """Conventional-commit type for a group (L0004 §2.3), or None when undecidable.

    B-rooted → fix; R-rooted → feat when a design doc exists else chore.
    """
    try:
        docs = db_documents.get_documents_by_group_id(group_id)
    except Exception:
        return None
    root_type: Optional[str] = None
    has_design_doc = False
    for doc in docs:
        doc_type = (doc.get("type_code") or doc.get("type") or "").upper()
        if doc_type in AUTO_COMMIT_DESIGN_TYPES:
            has_design_doc = True
        if root_type is None and doc_type in ("B", "R"):
            root_type = doc_type
    if root_type == "B":
        return "fix"
    if root_type == "R":
        return "feat" if has_design_doc else "chore"
    return None


def conventional_subject(commit_type: str, summary: str) -> str:
    """``"{commit_type}: {summary}"`` — the one place every conventional-commit subject
    is assembled. Public since flowgate.default.0462 T0005 §4-1: tr_commit_service uses
    it too, so a TR draft and a finalize auto-title are typed the same way."""
    return f"{commit_type}: {summary}"


def build_auto_commit_message(group_id: str) -> str:
    """Generate the finalize auto-commit subject from group metadata.

    Falls back to a conventional chore subject whenever metadata is incomplete or
    cannot be read, so finalize never fails because of commit-message generation.
    """
    from modules.flow_gate.services import git_service as _gs
    fallback = AUTO_COMMIT_MSG.format(group_id=group_id)
    try:
        group = db_groups.get_group(group_id)
        title = _gs._one_line_subject(group.get("title") if group else None)
        if not title:
            return fallback
        commit_type = derive_commit_type(group_id) or "chore"
        return conventional_subject(commit_type, title)
    except Exception:
        _log.warning("auto commit message generation failed for %s", group_id, exc_info=True)
        return fallback


def _translate_guard(text: str, source_title: str) -> bool:
    """Reject empty / non-English / echoed / boilerplate translations (L0004 §2.5).

    A blacklist, not a whitelist — full hallucination detection is impossible; the
    user confirmation step is the final defense.
    """
    from modules.flow_gate.services import git_service as _gs
    if not text:
        return False
    if not _gs._is_ascii(text):
        return False
    if not any(ch.isalpha() for ch in text):
        return False
    if text.lower() == (source_title or "").lower():
        return False
    stripped = text.strip(" .,!?:;\"'").strip()
    if stripped.lower() in BOILERPLATE_BLACKLIST:
        return False
    return True


def _try_translate(project_id: str, title: str) -> Optional[str]:
    """Translate a group title to an English subject fragment, or None on any failure.

    Never raises: translation is best-effort and must not fail finalize (L0004 §5).
    """
    from modules.flow_gate.services import git_service as _gs
    try:
        cfg = _gs.db_git.get_config(project_id)
        url = ((cfg or {}).get("translate_url") or "").strip()
        if not url:
            return None                       # unset = disabled (normal path, no log)
        import requests  # lazy: keeps the module import light
        resp = requests.post(
            url.rstrip("/") + "/translate",
            json={
                "q": title, "source": TRANSLATE_SOURCE,
                "target": TRANSLATE_TARGET, "format": "text",
            },
            timeout=TRANSLATE_TIMEOUT_SEC,
        )
        if resp.status_code != 200:
            _log.warning("translate server returned %s for %s", resp.status_code, project_id)
            return None
        translated = _gs.normalize_subject(resp.json().get("translatedText"))
        if _translate_guard(translated, title):
            return translated
        _log.info("translate result rejected by guard: %r", translated)
        return None
    except Exception:
        _log.warning("translate call failed for %s", project_id, exc_info=True)
        return None


def resolve_commit_message(group_id: str) -> tuple[str, str]:
    """Resolve the suggested finalize commit subject and its source (L0004 §2.4).

    Fallback chain: approved-TR draft (tr_draft) → ASCII title (auto_title) →
    translated title (translated) → fixed English phrase (fallback). Side-effect
    free; called by both the GET state query and POST finalize. Wrapped so an
    unexpected error still yields a conventional fallback (finalize never breaks).
    """
    from modules.flow_gate.services import git_service as _gs
    fallback = (AUTO_COMMIT_MSG.format(group_id=group_id), "fallback")
    try:
        # 1) latest approved-TR commit-message draft
        draft = db_documents.get_latest_tr_commit_message(group_id, DRAFT_ACCEPT_STATUSES)
        if draft:
            subject = _gs.normalize_subject(draft)
            if 0 < len(subject) <= COMMIT_SUBJECT_MAX:
                return (subject, "tr_draft")
            # abnormal stored value (empty / oversized) → silently fall through

        project_id = _gs._project_of_group(group_id)
        group = db_groups.get_group(group_id)
        title = _gs.normalize_subject(group.get("title") if group else None)
        ctype = derive_commit_type(group_id) or "chore"

        if title:
            # 2) ASCII title → existing auto-generation rule
            if _gs._is_ascii(title):
                subject = conventional_subject(ctype, title)
                if len(subject) <= COMMIT_SUBJECT_MAX:
                    return (subject, "auto_title")
            else:
                # 3) non-ASCII title → translate
                translated = _try_translate(project_id, title)
                if translated:
                    subject = conventional_subject(ctype, translated)
                    if len(subject) <= COMMIT_SUBJECT_MAX:
                        return (subject, "translated")

        # 4) fixed English phrase
        return (FIXED_FALLBACK_SUBJECT.format(commit_type=ctype), "fallback")
    except Exception:
        _log.warning("commit message resolution failed for %s", group_id, exc_info=True)
        return fallback


# Not a cap on how many new files the finalize commit stages, but on how many paths ride one
# command line. The 0382 incident brought 261 at once, and hitting the Windows command-line
# length limit would fail the finalize itself. It is split across several calls.
_ADD_PATHSPEC_CHUNK = 50


# Cap on how much excluded debris rides the result and the event. The screen announces the
# count first, so the total count is always exact and only the list is truncated.
FINALIZE_ARTIFACT_LIST_MAX = 200


def _stage_worker_edits(wt_path: Path) -> tuple[list[str], bool]:
    """Stage a worktree's leftover edits under the tool-debris rule — the shared half.

    Returns ``(excluded_artifacts, has_staged)``. Two callers use it and they must not
    drift apart: the finalize absorb commit below, and the TR commit point
    (``create_tr_commit``, 0332 D0005 K2). 0382 happened because one rule lived in two
    places — the screen hid what the check caught — so the exclusion decision is made
    exactly once, here, and both commits inherit it.

    * ``git add -u`` stages every tracked change, **including deletions**. That matters:
      the cleanup of already-committed debris has to be committable through this same
      path, and a filter that also dropped deletions would make those files unremovable.
    * new files are added by explicit pathspec, so a rule-matching one is never staged in
      the first place (no ``reset`` dance, nothing half-staged on failure).

    ``has_staged`` is false when the index came out empty — a worktree dirty ONLY because
    of debris. That is the gate doing its job, not a failure, and each caller decides what
    to do with it (finalize skips the commit; the TR path reports ``artifacts_only``).
    """
    from modules.flow_gate.services import git_service as _gs
    kept, artifacts = path_exclusion_rules.partition_paths(
        _worktree_untracked_paths(wt_path)
    )
    proc = _gs._run_git(["add", "-u"], cwd=wt_path)
    if proc.returncode != 0:
        raise GitServiceError(500, "git_error", "Git command failed", diagnostic=_gs._last_line(proc.stderr))
    for index in range(0, len(kept), _ADD_PATHSPEC_CHUNK):
        chunk = kept[index:index + _ADD_PATHSPEC_CHUNK]
        proc = _gs._run_git(["add", "--", *chunk], cwd=wt_path)
        if proc.returncode != 0:
            raise GitServiceError(500, "git_error", "Git command failed", diagnostic=_gs._last_line(proc.stderr))
    staged = _gs._run_git(["diff", "--cached", "--quiet"], cwd=wt_path)
    return artifacts, staged.returncode != 0


def _absorb_worker_edits(
    wt_path: Path, subject: str, author_env: Optional[dict]
) -> list[str]:
    """Commit the worker's leftover edits — WITHOUT swallowing tool debris.

    0382 B0001 (NR0003 §2-4 / proposal 1). This used to be a bare ``git add -A``. It has
    no filter, so whatever sat in the worktree went in: commit ``0f502ce`` carries 5
    real files and 261 ``server/.test-tmp-*`` leftovers, and nobody could have caught
    it because the explorer hides exactly those paths (§2-3). One unfiltered line
    turned a local mess into permanent repository state on 11 branches.

    The gate is deliberately narrow — it only refuses to *add new untracked debris*:

    * ``git add -u`` stages every tracked change, **including deletions**. That
      matters: the follow-up cleanup of the already-committed 261 files has to be
      committable through this same path, and a filter that also dropped deletions
      would make those files unremovable.
    * new files are added by explicit pathspec, so a rule-matching one is never
      staged in the first place (no ``reset`` dance, nothing half-staged on failure).

    Excluded paths are RETURNED, never silently dropped — the caller puts them in the
    finalize result and the SSE event so the screen can say "N temporary artifacts excluded
    from the commit". Silently correct is how this bug survived; visible is the fix.

    Returns the excluded paths (sorted). Raises GitServiceError on a git failure.
    """
    from modules.flow_gate.services import git_service as _gs
    artifacts, has_staged = _stage_worker_edits(wt_path)
    if artifacts:
        _log.info(
            "finalize: excluding %d tool artifact(s) from the absorb commit in %s",
            len(artifacts), wt_path,
        )

    # A worktree dirty ONLY because of debris now has an empty index, and
    # `git commit` on an empty index exits non-zero ("nothing to commit"). That is
    # not a failure — it is the gate doing its job — so skip the commit instead of
    # turning a clean finalize into a 500.
    if not has_staged:
        return artifacts

    proc = _gs._run_git(
        [*_gs._GIT_IDENT, "commit", "-m", subject], cwd=wt_path, author_env=author_env
    )
    if proc.returncode != 0:
        raise GitServiceError(500, "git_error", "Git command failed", diagnostic=_gs._last_line(proc.stderr))
    return artifacts


def _artifact_payload(
    artifacts: Sequence[str], staged_new_file_count: int = 0,
) -> dict:
    """Finalize visibility for both excluded and accepted untracked files."""
    return {
        "excluded_artifact_count": len(artifacts),
        "excluded_artifacts": list(artifacts[:FINALIZE_ARTIFACT_LIST_MAX]),
        "staged_new_file_count": staged_new_file_count,
    }


# ── TR commit point (flowgate.default.0332 D0005 §3.1 / L0007 §1·§2.6) ────────

# L0007 §1 tr_commit_lock_wait_sec = 0. The approval does NOT queue behind a
# finalize: approvals happen many times an hour and a 5-second lock wait would make
# one finalize stall every approval in the project. Giving up costs nothing — the
# changes stay in the worktree for the next TR commit or the absorb commit, and the
# ledger records that this round was skipped for `git_busy` (D0005 §4).
TR_COMMIT_LOCK_WAIT_SEC = 0


def create_tr_commit(group_id: str, subject: str) -> dict:
    """Commit the group worktree's pending work as one TR's commit point.

    Called right after a TR approval has committed (D0005 K1), so it must **never
    raise and never block**: every refusal is a ``skipped_reason`` from the closed set
    P0006 §5-2 fixed, and the approval stands either way. Returns::

        {committed, commit (7-char), commit_sha (40-char), subject,
         skipped_reason, excluded_artifacts, committed_paths}

    The scope is the worktree, not the document's reported file list — see
    tr_commit_service for why trusting that list would let one TR's commit carry (and
    a later rewind revert) another TR's work.

    The gates mirror the cancel side's G2~G9 (L0007 §4.1) in the same order, so the
    two halves of this feature never disagree about what "this group has git" means.
    """
    from modules.flow_gate.services import git_service as _gs
    blank = {
        "committed": False, "commit": None, "commit_sha": None, "subject": None,
        "skipped_reason": "commit_failed", "excluded_artifacts": [],
        "committed_paths": [],
    }

    def skip(reason: str, artifacts: Optional[Sequence[str]] = None) -> dict:
        return {**blank, "skipped_reason": reason,
                "excluded_artifacts": list(artifacts or [])}

    project_id = _gs._project_of_group(group_id)
    if not project_id:
        return skip("git_inactive")
    try:
        cfg = _gs.db_git.get_config(project_id)
        if cfg is None or not cfg.get("enabled"):
            return skip("git_inactive")
        if not _gs.git_available():
            return skip("git_inactive")
        state = _gs.db_git.get_state(group_id)
        if state is None:
            return skip("git_inactive")
        if not state.get("worktree_registered") or not state.get("branch"):
            return skip("no_worktree")
        project_name = _gs._project_name(project_id)
        if not project_name:
            return skip("no_worktree")
    except Exception:
        _log.warning("tr commit precheck failed for %s", group_id, exc_info=True)
        return skip("commit_failed")

    holder = f"trcommit:{uuid.uuid4()}"
    if not _gs._acquire_lock(project_id, holder, wait_sec=TR_COMMIT_LOCK_WAIT_SEC):
        return skip("git_busy")
    try:
        wt_path = _gs.src_root(project_name, state["branch"])
        if not wt_path.is_dir():
            return skip("no_worktree")

        artifacts, has_staged = _stage_worker_edits(wt_path)
        if not has_staged:
            # D0005 K3 / P0006 §1-5: "changed nothing" and "changed only debris" are
            # two different sentences on screen, so they stay two different codes.
            return skip("artifacts_only" if artifacts else "no_changes", artifacts)

        # -z keeps paths raw: git quotes non-ASCII names in the plain form, and a
        # quoted path would never match the document's reported list.
        listing = _gs._run_git(
            ["diff", "--cached", "--name-only", "-z"], cwd=wt_path,
            timeout=_gs.GIT_READ_TIMEOUT_SEC,
        )
        committed_paths = sorted(p for p in (listing.stdout or "").split("\0") if p)

        proc = _gs._run_git(
            [*_gs._GIT_IDENT, "commit", "-m", subject], cwd=wt_path,
            author_env=_author_env_from_cfg(cfg),
        )
        if proc.returncode != 0:
            _log.warning(
                "tr commit failed for %s: %s", group_id, _gs._last_line(proc.stderr)
            )
            return skip("commit_failed", artifacts)

        head = _gs._run_git(["rev-parse", "HEAD"], cwd=wt_path, timeout=_gs.GIT_READ_TIMEOUT_SEC)
        full = (head.stdout or "").strip() or None
        return {
            "committed": True,
            "commit": full[:7] if full else None,
            "commit_sha": full,
            "subject": subject,
            "skipped_reason": None,
            "excluded_artifacts": list(artifacts),
            "committed_paths": committed_paths,
        }
    except Exception:
        _log.warning("tr commit point failed for group %s", group_id, exc_info=True)
        return skip("commit_failed")
    finally:
        try:
            _gs.db_git.release_lock(project_id, holder)
        except Exception:
            _log.warning("tr commit lock release failed for %s", project_id, exc_info=True)


# L0007 §2.6 — the cancel commit's body always names the reverted commit in full, so
# `git log --grep` finds the pair from either side.
_CANCEL_TRAILER = "FlowGate: TR commit cancel for {code} (group {group_id})."


# T0018 K11 — the same idea for the other direction. A distinct wording, because a reader
# grepping the log has to be able to tell a cancel from the restore that undid it.
_REAPPLY_TRAILER = "FlowGate: TR commit reapply for {code} (group {group_id})."


def cancel_subject(commit_subject: Optional[str]) -> str:
    """``Revert "<original subject>"`` clipped to the shared subject cap (L0007 §2.6).

    Clipping puts the ellipsis INSIDE the quotes so the result still reads as one
    quoted title rather than a truncated sentence.
    """
    from modules.flow_gate.services import git_service as _gs
    original = _gs._one_line_subject(commit_subject or "")
    quoted = f'Revert "{original}"'
    if len(quoted) <= COMMIT_SUBJECT_MAX:
        return quoted
    keep = COMMIT_SUBJECT_MAX - len('Revert ""') - 1
    return f'Revert "{original[:max(keep, 0)]}…"'


def cancel_body(commit_sha: str, doc_code: str, group_id: str) -> str:
    return (
        f"This reverts commit {commit_sha}.\n\n"
        + _CANCEL_TRAILER.format(code=doc_code, group_id=group_id)
    )


def reapply_subject(commit_subject: Optional[str]) -> str:
    """``Reapply "<original subject>"`` — the forward restore's commit (T0018 K11).

    Same clipping rule as :func:`cancel_subject`, ellipsis inside the quotes, so a step
    that went commit → revert → restore reads as three lines of one sentence in the log.
    Named after the ORIGINAL TR, not after the cancel commit it technically reverts:
    "Revert \"Revert \"0009-TR: ...\"\"" is what git would have written by itself and it
    tells a reader nothing.
    """
    from modules.flow_gate.services import git_service as _gs
    original = _gs._one_line_subject(commit_subject or "")
    quoted = f'Reapply "{original}"'
    if len(quoted) <= COMMIT_SUBJECT_MAX:
        return quoted
    keep = COMMIT_SUBJECT_MAX - len('Reapply ""') - 1
    return f'Reapply "{original[:max(keep, 0)]}…"'


def reapply_body(
    cancel_sha: str, original_sha: str, doc_code: str, group_id: str
) -> str:
    """The reapply commit's body — it names BOTH ends of the round trip.

    The first line is the one git's own tooling looks for, and it has to name the commit
    actually being reverted (the cancel). The original TR commit is named on its own line
    underneath, so one ``git log --grep`` on either sha pulls the whole triple — the TR
    commit, the cancel that undid it, the reapply that put it back.
    """
    lines = [f"This reverts commit {cancel_sha}.", ""]
    if original_sha:
        lines.append(f"Restores the TR commit {original_sha}.")
        lines.append("")
    lines.append(_REAPPLY_TRAILER.format(code=doc_code, group_id=group_id))
    return "\n".join(lines)


def cancel_blocking_dirty(wt_path: Path) -> bool:
    """Does this worktree hold changes a revert would get mixed up with? (L0007 §2.5)

    NOT the finalize ``_dirty()``: that one calls any untracked file dirty, and the TR
    commit path *deliberately leaves tool debris behind* (0382), so every group would
    permanently look dirty and no cancel would ever run. What actually mixes with a
    revert is a tracked-file edit or a new file the exclusion rules would have kept —
    and the exclusion rule used here is the same function the commit side uses, so a
    path can never be "not committed but still blocking".
    """
    from modules.flow_gate.services import git_service as _gs
    proc = _gs._run_git(
        ["status", "--porcelain", "--untracked-files=no"],
        cwd=wt_path, timeout=_gs.GIT_READ_TIMEOUT_SEC,
    )
    if proc.returncode != 0 or (proc.stdout or "").strip():
        return True
    kept, _artifacts = path_exclusion_rules.partition_paths(
        _worktree_untracked_paths(wt_path)
    )
    return bool(kept)


def _cancel_prelock_gate(group_id: str) -> dict:
    """L0007 §4.1 G2~G7 — everything that can be judged from the DB alone.

    Split out of :func:`open_cancel_session` because the rewind dialog's preview must
    answer with the SAME reading in the SAME order (L0007 §3 그룹 관측 상태); two copies
    of this ladder is exactly how a dialog ends up saying "2 commits will be reverted"
    about a group whose cancel then refuses.
    """
    from modules.flow_gate.services import git_service as _gs
    out = {
        "blocked_reason": None, "block_sub": None,
        "project_id": None, "cfg": None, "state": None,
    }

    def blocked(reason: str, sub: str) -> dict:
        return {**out, "blocked_reason": reason, "block_sub": sub}

    project_id = _gs._project_of_group(group_id)
    if not project_id:
        return blocked("git_inactive", "integration_disabled")
    out["project_id"] = project_id
    cfg = _gs.db_git.get_config(project_id)                                # G2
    if cfg is None or not cfg.get("enabled"):
        return blocked("git_inactive", "integration_disabled")
    out["cfg"] = cfg
    state = _gs.db_git.get_state(group_id)                                 # G3
    if state is None:
        return blocked("git_inactive", "no_group_git_state")
    out["state"] = state
    if not _gs.git_available():                                           # G4
        return blocked("git_inactive", "git_unavailable")
    status = state.get("status") or "none"
    if status in ("merged", "pushed"):                                     # G5
        return blocked("already_merged", "already_merged")
    if status in ("merging", "conflict"):                                  # G6
        return blocked("git_busy", "merge_in_flight")
    if not state.get("worktree_registered") or not state.get("branch"):    # G7
        return blocked("no_worktree", "worktree_unregistered")
    return out


# L0007 §3 — the preview's group-level status. `git_busy`/`dirty_worktree` are NOT in
# it: both are true only at the instant a revert is being laid down, and a dialog that
# opened ten seconds ago would be stating them as facts (P0006 §2 서두). A merge in
# flight therefore previews as "active" and the confirm press answers `git_busy` — with
# a [다시 시도] button, which is the honest sequence.
_PREVIEW_STATUS_OF_BLOCK = {
    "git_inactive": "git_inactive",
    "already_merged": "already_merged",
    "no_worktree": "no_worktree",
    "git_busy": "active",
}


def cancel_group_status(group_id: str) -> str:
    """``active`` | ``already_merged`` | ``no_worktree`` | ``git_inactive`` (P0006 §2)."""
    gate = _cancel_prelock_gate(group_id)
    reason = gate["blocked_reason"]
    if not reason:
        return "active"
    return _PREVIEW_STATUS_OF_BLOCK.get(reason, "active")


def open_cancel_session(group_id: str, target_shas: Sequence[str]) -> dict:
    """Evaluate L0007 §4.1 G2~G11 and, if all pass, hold the project git lock.

    Returns ``{"ok": True, "session": {...}}`` or
    ``{"ok": False, "blocked_reason": <P0006 §5-3 code>, "block_sub": <L0007 detail>}``.

    The gate ORDER is the part that carries meaning, and it is the same order the
    preview reads (L0007 §3 그룹 관측 상태) so the dialog never promises something the
    press of the button then refuses:

    * ``already_merged`` is checked BEFORE ``worktree_registered`` — cleanup unregisters
      a merged slot's worktree, so the other order would report every merged group as
      ``no_worktree`` and the screen would lose its one useful sentence ("use [병합
      되돌리기]").
    * the lock is taken BEFORE the disk is read — "clean" decided outside the lock is
      already stale by the time the first revert lands.

    The caller MUST call :func:`close_cancel_session` in a ``finally``; the re-arm that
    follows a rewind takes the same lock and it is not re-entrant (L0007 §2.1 ③).

    T0018 K11: the forward restore opens this SAME session, deliberately un-renamed. Every
    gate above applies to a reapply word for word — a merged group, a missing worktree, a
    busy lock and a dirty tree block putting source back for exactly the reasons they block
    taking it away — and G11 keeps its meaning because the restore passes the CANCEL
    commits as ``target_shas``: "are the commits I am about to peel off still in this tree".
    """
    from modules.flow_gate.services import git_service as _gs
    def block(reason: str, sub: str) -> dict:
        return {"ok": False, "blocked_reason": reason, "block_sub": sub, "session": None}

    gate = _cancel_prelock_gate(group_id)                                 # G2~G7
    if gate["blocked_reason"]:
        return block(gate["blocked_reason"], gate["block_sub"])
    project_id, cfg, state = gate["project_id"], gate["cfg"], gate["state"]

    holder = f"cancel:{uuid.uuid4()}"
    if not _gs._acquire_lock(project_id, holder, wait_sec=_gs.CANCEL_LOCK_WAIT_SEC):  # G8
        return block("git_busy", "lock_timeout")
    try:
        project_name = _gs._project_name(project_id)
        wt_path = _gs.src_root(project_name, state["branch"]) if project_name else None
        if wt_path is None or not wt_path.is_dir():                       # G9
            raise _CancelGateFailed("no_worktree", "worktree_missing")
        if _gs.cancel_blocking_dirty(wt_path):                                # G10
            raise _CancelGateFailed("dirty_worktree", "dirty_worktree")
        if not _commits_present(wt_path, target_shas):                    # G11
            raise _CancelGateFailed("no_worktree", "commits_absent")
    except _CancelGateFailed as gate:
        _release_cancel_lock(project_id, holder)
        return block(gate.reason, gate.sub)
    except Exception:
        # A gate that blew up must not leave the project lock behind — the re-arm
        # right after this would then wait five seconds and fail silently.
        _release_cancel_lock(project_id, holder)
        raise
    return {
        "ok": True, "blocked_reason": None, "block_sub": None,
        "session": {
            "project_id": project_id, "group_id": group_id, "holder": holder,
            "wt_path": wt_path, "author_env": _author_env_from_cfg(cfg),
        },
    }


def open_terminal_reopen_session(group_id: str) -> dict:
    """Lock and check a terminal reopen before it can re-provision a worktree.

    The ordinary cancel gate returns ``already_merged`` before G8--G10 because a
    merged slot may legitimately be unregistered. Terminal reopen does not reset or
    revert, but its following re-arm can recreate the slot; therefore any existing
    worktree must still be checked under the project lock so unrelated edits cannot
    be overwritten. A missing terminal worktree is valid and needs no cleanliness
    check.
    """
    from modules.flow_gate.services import git_service as _gs
    def block(reason: str, sub: str) -> dict:
        return {"ok": False, "blocked_reason": reason, "block_sub": sub, "session": None}

    gate = _cancel_prelock_gate(group_id)
    if gate["blocked_reason"] != "already_merged":
        return block(gate["blocked_reason"] or "git_inactive", gate["block_sub"] or "terminal_status_changed")
    project_id, cfg, state = gate["project_id"], gate["cfg"], gate["state"]
    holder = f"terminal-reopen:{uuid.uuid4()}"
    if not _gs._acquire_lock(project_id, holder, wait_sec=_gs.CANCEL_LOCK_WAIT_SEC):
        return block("git_busy", "lock_timeout")
    try:
        project_name = _gs._project_name(project_id)
        wt_path = _gs.src_root(project_name, state["branch"]) if project_name else None
        if wt_path is not None and wt_path.is_dir() and _gs.cancel_blocking_dirty(wt_path):
            raise _CancelGateFailed("dirty_worktree", "dirty_worktree")
    except _CancelGateFailed as gate_error:
        _release_cancel_lock(project_id, holder)
        return block(gate_error.reason, gate_error.sub)
    except Exception:
        _release_cancel_lock(project_id, holder)
        raise
    return {
        "ok": True, "blocked_reason": None, "block_sub": None,
        "session": {
            "project_id": project_id, "group_id": group_id, "holder": holder,
            "wt_path": wt_path, "author_env": _author_env_from_cfg(cfg),
        },
    }


class _CancelGateFailed(Exception):
    """Internal: a post-lock gate refused. Carries the pair the caller reports."""

    def __init__(self, reason: str, sub: str) -> None:
        super().__init__(f"{reason}:{sub}")
        self.reason = reason
        self.sub = sub


def _release_cancel_lock(project_id: str, holder: str) -> None:
    from modules.flow_gate.services import git_service as _gs
    try:
        _gs.db_git.release_lock(project_id, holder)
    except Exception:
        _log.warning("tr cancel lock release failed for %s", project_id, exc_info=True)


def close_cancel_session(session: Optional[dict]) -> None:
    if not session:
        return
    _release_cancel_lock(session["project_id"], session["holder"])


def uncommit_tr_suffix(session: dict, target_shas: Sequence[str]) -> dict:
    """Remove an exact TR-only HEAD suffix while preserving its tree delta unstaged.

    The validation and reset run under the cancel session's project lock.  Every target
    must equal the current first-parent suffix in the supplied newest-first order; an
    unknown/manual or non-target commit therefore fails closed before history moves.
    """
    from modules.flow_gate.services import git_service as _gs
    wt_path = session["wt_path"]
    expected = [str(sha or "").strip() for sha in target_shas]
    if not expected or any(not sha for sha in expected):
        return {"kind": "blocked", "sub": "unsafe_suffix", "before": None}

    head_proc = _gs._run_git(
        ["rev-parse", "HEAD"], cwd=wt_path, timeout=_gs.GIT_READ_TIMEOUT_SEC,
    )
    before = (head_proc.stdout or "").strip()
    if head_proc.returncode != 0 or not before:
        return {"kind": "blocked", "sub": "unsafe_suffix", "before": before or None}

    cursor = before
    for sha in expected:
        if cursor != sha:
            return {"kind": "blocked", "sub": "unsafe_suffix", "before": before}
        parent_proc = _gs._run_git(
            ["rev-parse", f"{cursor}^"], cwd=wt_path, timeout=_gs.GIT_READ_TIMEOUT_SEC,
        )
        cursor = (parent_proc.stdout or "").strip()
        if parent_proc.returncode != 0 or not cursor:
            return {"kind": "blocked", "sub": "unsafe_suffix", "before": before}

    reset = _gs._run_git(["reset", "--mixed", cursor], cwd=wt_path)
    if reset.returncode != 0:
        return {"kind": "blocked", "sub": "reset_failed", "before": before}

    after_proc = _gs._run_git(
        ["rev-parse", "HEAD"], cwd=wt_path, timeout=_gs.GIT_READ_TIMEOUT_SEC,
    )
    after = (after_proc.stdout or "").strip()
    if after_proc.returncode != 0 or after != cursor:
        return {"kind": "blocked", "sub": "reset_failed", "before": before}
    return {"kind": "ok", "before": before, "head": after}


def revert_tr_commit(session: dict, *, commit_sha: str, subject: str, body: str) -> dict:
    """Lay one revert commit on top of the worktree (L0007 §2.3). One TR, one commit.

    Returns ``{"kind": "ok"|"empty"|"blocked", "commit": <full 40-char sha>|None,
    "sub": str|None}``. The ledger stores the full hash (DB0008 §4-3); the 7-character
    form is cut where a screen reads it, never on the way in.

    ``--no-commit`` then our own ``commit``: the subject, the body trailer and the
    commit identity have to match the rest of FlowGate's commits, and ``git revert``'s
    self-generated message follows none of those rules. Reverts are never batched — one
    revert commit per TR is what lets the ledger point at them one to one (D0005 K6).
    """
    return _revert_one(session, commit_sha=commit_sha, subject=subject, body=body)


def reapply_tr_commit(session: dict, *, cancel_commit: str, subject: str, body: str) -> dict:
    """Peel one cancel commit back off — the forward restore's git step (T0018 K11).

    A reapply IS a revert: reverting the revert is what puts the original TR's source
    back, and it is the only form that keeps the rewind itself visible in the log
    (D0005 K5). So this is deliberately a two-line wrapper over the same helper
    :func:`revert_tr_commit` uses rather than a second copy of the procedure —
    ``--no-commit``, the empty check, our own message, the same three ``kind`` values.
    A copy would drift the moment one of the two learns something, and a clean automatic
    merge is perfectly happy to keep both (see [[clean-automerge-can-shadow-duplicate-defs]]).

    ``cancel_commit`` is the cancel commit's sha, not the original TR commit's: what is
    being undone here is the cancel.
    """
    return _revert_one(session, commit_sha=cancel_commit, subject=subject, body=body)


def _revert_one(session: dict, *, commit_sha: str, subject: str, body: str) -> dict:
    """The shared body of :func:`revert_tr_commit` and :func:`reapply_tr_commit`."""
    from modules.flow_gate.services import git_service as _gs
    wt_path: Path = session["wt_path"]
    proc = _gs._run_git(
        ["revert", "--no-commit", "--no-edit", commit_sha],
        cwd=wt_path, timeout=GIT_LOCAL_TIMEOUT_SEC,
    )
    if proc.returncode != 0:
        # Conflict, failed application and timeout are one outcome here on purpose:
        # the person's next move is identical in all three (P0006 §5-4 closed the set),
        # and the difference is kept in the ledger's attempt log, not in the response.
        sub = "timeout" if "timeout_expired" in (proc.stderr or "") else "revert_conflict"
        return {"kind": "blocked", "commit": None, "sub": sub}
    staged = _gs._run_git(["diff", "--cached", "--quiet"], cwd=wt_path, timeout=_gs.GIT_READ_TIMEOUT_SEC)
    if staged.returncode == 0:
        # Nothing to undo — the same content was already reverted by another route.
        # An empty commit would be noise in the history for a no-op (D0005 K3).
        return {"kind": "empty", "commit": None, "sub": "empty_revert"}
    proc = _gs._run_git(
        [*_gs._GIT_IDENT, "commit", "-m", subject, "-m", body],
        cwd=wt_path, author_env=session.get("author_env"), timeout=GIT_LOCAL_TIMEOUT_SEC,
    )
    if proc.returncode != 0:
        return {"kind": "blocked", "commit": None, "sub": "commit_failed"}
    head = _gs._run_git(["rev-parse", "HEAD"], cwd=wt_path, timeout=_gs.GIT_READ_TIMEOUT_SEC)
    full = (head.stdout or "").strip() or None
    return {"kind": "ok", "commit": full, "sub": None}


def restore_after_failed_revert(session: dict) -> None:
    """Put the worktree back the way the failed revert found it (L0007 §2.3).

    ``git clean`` is NOT called and never will be on this path — 0382 is what happens
    when a git command that deletes untracked files sits in an automatic flow. Return
    codes are ignored: the outcome is already ``blocked`` and the loop stops here.

    TR0019: this is no longer what a CONFLICT does. A conflict now becomes a session
    (:func:`open_tr_conflict_session`) so a person or an AI can still see it; this
    stays as the fallback for the failures nobody can resolve by editing a file — a
    timeout, a commit that would not run, a session row that could not be written —
    and as the body of the explicit [give up] press (:func:`abort_tr_conflict`).
    Destroying the evidence was never wrong; being the only option was.
    """
    from modules.flow_gate.services import git_service as _gs
    wt_path: Path = session["wt_path"]
    _gs._run_git(["revert", "--quit"], cwd=wt_path, timeout=_gs.GIT_READ_TIMEOUT_SEC)
    _gs._run_git(["reset", "--hard", "HEAD"], cwd=wt_path, timeout=GIT_LOCAL_TIMEOUT_SEC)


def _merge_commit_subject(branch: str, base_branch: str) -> str:
    """flowgate.default.0232 B0001 — the `--no-ff` merge commit must NOT reuse the
    work subject. Back when a work branch held exactly ONE absorb commit carrying
    finalize_subject(), wrapping that single commit in a merge commit of the SAME
    memoized subject made origin show identical title+diff twice ("same code committed
    twice"). A conventional Merge subject makes the pair read as a normal work-commit +
    merge-commit instead of a duplicate.

    That "exactly one commit" premise is gone: since flowgate.default.0332 every TR
    approval leaves its own commit point on the branch and the absorb commit only
    picks up what is left over (D0005 K4). The rule above still stands — with several
    commits on the branch the duplicate-title collision is even less likely — but the
    old sentence stated a fact that no longer holds, and leaving it would have the next
    reader reason from a premise the code abandoned. `--no-ff` (the two-parent
    topology) is deliberately kept so unmerge's `^2` restore (flowgate.default.0202)
    still resolves the merged work branch."""
    return f"Merge branch '{branch}' into '{base_branch}'"


def _ledger_group_by_merge_sha(project_id: str, full_sha: str) -> Optional[str]:
    from modules.flow_gate.services import git_service as _gs
    from .merge_target import merged_on_project_base
    matches: list[str] = []
    for row in _gs.db_git.list_states_of_project_any(project_id):
        if row.get("status") != "merged" or not row.get("merge_commit"):
            continue
        # 0594 T0012 §15: a merge that landed on a non-base target never maps to a
        # base commit, so the base unmerge list can never offer it (fail closed).
        if not merged_on_project_base(row):
            continue
        if full_sha.lower().startswith(str(row["merge_commit"]).lower()):
            matches.append(row["group_id"])
    return matches[0] if len(matches) == 1 else None
