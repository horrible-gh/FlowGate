"""Conflict-session lifecycle and merge-conflict resolution.

Extracted from git_service.py (Appendix A).
"""
from __future__ import annotations
from modules.flow_gate.graph_workflow.services import card_document_ports as document_write_ports

import difflib
import hashlib
import logging
import os
import re
import subprocess
import tempfile
import uuid
from pathlib import Path
from typing import Optional

from modules.flow_gate.db.git_integration import (
    MERGE_REVIEW_SESSION_KINDS as _MERGE_REVIEW_KINDS,
    is_branch_merge_session as _is_branch_merge_session,
    session_owner_type as _session_owner_type,
    session_project_id as _session_project_id,
)

from . import conflict_companions
from . import approval_intent
from . import merge_target
from .command import GIT_LOCAL_TIMEOUT_SEC
from .commit import _acquire_group_lock, _release_group_lock
from .credentials import GitServiceError, _author_env_from_cfg

_log = logging.getLogger(__name__)

TR_CONFLICT_REVIEW_OPEN = "open"
TR_CONFLICT_REVIEW_RESOLVED = "resolved"
# ``context.abort_failure.trigger`` of a failed Graph
# attempt merge give-up (person, sweep or Stop).
GRAPH_ATTEMPT_ABORT_TRIGGER = "graph_attempt_abort"

_CONFLICT_SEP_RE = re.compile(r"^={7}$")
_CONFLICT_BASE_RE = re.compile(r"^\|{7}( |$)")
_CONFLICT_CHUNK_GROUP_MAX_COMMON_LINES = 3
_SUPERSEDE_SIDES = ("ours", "theirs")
# A dropped line counts as "changed in place" only when the kept side's replacement
# run holds an edited version of it (difflib ratio). This pairs lines; it never admits
# a chunk by an overall inclusion rate.
_SUPERSEDE_CHANGED_LINE_MIN_RATIO = 0.6
_SUPERSEDE_HINT = (
    "Use a per-file `supersede` declaration ({\"side\": \"ours|theirs\", \"reason\": \"...\"}) "
    "ONLY when the side you kept already contains the other side's changes; otherwise merge "
    "both sides' changes into the chunk. For verified target-side moves, submit per-file "
    "companion_files: [{path, content}] using companion_candidates from the conflict query; "
    "preserve the incoming chunk in that file and declare supersede side=ours."
)


# ── the earlier implementation: line endings of conflict files ────────────────────────────────────
#
# merge 98 wrote its six resolved files through `Path.write_text`, which on the
# Windows server turns every "\n" into "\r\n": the LF i18n files and two LF server files
# came out CRLF and the CRLF WorkPlanEditor.vue came out "\r\r\n" (6132cf58). From then on
# every group touching those files met a whole-file EOL conflict — 0594's ten conflict
# files were 83% EOL noise. Two things below stop that: a resolution is written in the
# file's own line ending (`_write_resolved_file`), and a conflict that is only a line-ending
# difference is merged on LF-normalised text and taken out of the resolver's hands
# (`separate_eol_conflicts`).

_EOL_ATTR_RE = re.compile(r": eol: (lf|crlf)\s*$")


def _git_bytes(args: list[str], cwd: Path) -> Optional[bytes]:
    """Raw stdout of a read-only git command (``_run_git`` decodes text, which would fold
    the very line endings this has to see). None on any failure."""
    env = os.environ.copy()
    env["GIT_TERMINAL_PROMPT"] = "0"
    try:
        proc = subprocess.run(
            ["git", *args], cwd=str(cwd), capture_output=True, env=env,
            timeout=GIT_LOCAL_TIMEOUT_SEC,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    return proc.stdout if proc.returncode == 0 else None


def _majority_eol(data: bytes) -> str:
    return "crlf" if data.count(b"\r\n") * 2 > data.count(b"\n") else "lf"


def _path_eol(root: Path, path: str, fallback: Optional[bytes] = None) -> str:
    """The line ending a resolved ``path`` is written in: the repo's ``eol`` attribute when
    it names one, otherwise whatever the checked-out HEAD version uses (so the resolution
    adds no line-ending churn to the branch it lands on), otherwise ``fallback``'s, else LF.
    """
    from modules.flow_gate.services import git_service as _gs

    attr = _gs._run_git(["check-attr", "eol", "--", path], cwd=root)
    match = _EOL_ATTR_RE.search((attr.stdout or "").strip()) if attr.returncode == 0 else None
    if match:
        return match.group(1)
    head = _git_bytes(["cat-file", "blob", f"HEAD:{path}"], root)
    if head is not None:
        return _majority_eol(head)
    if fallback is not None:
        return _majority_eol(fallback)
    return "lf"


def _encode_eol(content: str, eol: str) -> bytes:
    text = content.replace("\r\n", "\n")
    if eol == "crlf":
        text = text.replace("\n", "\r\n")
    return text.encode("utf-8", errors="surrogateescape")


def _write_resolved_file(root: Path, path: str, target: Path, content: str) -> None:
    """Write a resolution in bytes, in the file's own line ending — never text mode."""
    target.parent.mkdir(parents=True, exist_ok=True)
    document_write_ports.write_gate("paths", [target], lambda: target.write_bytes(_encode_eol(content, _path_eol(root, path))))


def _marker_labels(text: str) -> tuple[str, str, str]:
    """``(ours, base, theirs)`` labels of the first conflict chunk git wrote, so a
    re-merged file carries the same marker lines the original merge produced."""
    ours = base = theirs = None
    for line in text.splitlines():
        if ours is None and line.startswith("<<<<<<< "):
            ours = line[8:].strip()
        elif base is None and _CONFLICT_BASE_RE.match(line):
            base = line[8:].strip()
        elif theirs is None and line.startswith(">>>>>>> "):
            theirs = line[8:].strip()
        if ours is not None and base is not None and theirs is not None:
            break
    return ours or "HEAD", base or "merged common ancestors", theirs or "theirs"


# `git merge-file` reports the number of conflicts as its exit status, capped at 127.
_MERGE_FILE_MAX_CONFLICT_EXIT = 127


def _eol_normalized_merge(root: Path, path: str) -> Optional[tuple[int, bytes]]:
    """Re-run the 3-way merge of an unmerged ``path`` with every stage's CRLF folded to LF.

    Returns ``(conflict_count, merged_bytes)`` — ``merged_bytes`` already in the path's
    own line ending — or None when this does not apply: a stage is missing (add/add,
    modify/delete), a stage is binary, or no stage has a CR (so the git conflict is
    already free of line-ending noise).
    """
    stages = [_git_bytes(["cat-file", "blob", f":{n}:{path}"], root) for n in (2, 1, 3)]
    if any(stage is None for stage in stages):
        return None
    if any(b"\x00" in stage for stage in stages) or not any(b"\r" in stage for stage in stages):
        return None
    try:
        current = (root / path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        current = ""
    ours_label, base_label, theirs_label = _marker_labels(current)
    with tempfile.TemporaryDirectory(prefix="fg-eol-merge-") as tmp:
        names = []
        for name, stage in zip(("ours", "base", "theirs"), stages):
            p = Path(tmp) / name
            document_write_ports.write_gate("paths", [p], lambda: p.write_bytes(stage.replace(b"\r\n", b"\n")))
            names.append(str(p))
        try:
            proc = subprocess.run(
                ["git", "merge-file", "-p", "--zdiff3",
                 "-L", ours_label, "-L", base_label, "-L", theirs_label, *names],
                capture_output=True, timeout=GIT_LOCAL_TIMEOUT_SEC,
            )
        except (OSError, subprocess.TimeoutExpired):
            return None
    # git merge-file exits with the conflict count (0..127); anything else — a negative
    # error, 129 for a usage error, 128 for a fatal — is a failure whose stdout is not a
    # merge. Treating it as a count would overwrite the conflicted file with that stdout.
    if not 0 <= proc.returncode <= _MERGE_FILE_MAX_CONFLICT_EXIT:
        return None
    if proc.returncode > 0 and b"<<<<<<<" not in proc.stdout:
        return None
    merged = proc.stdout.decode("utf-8", errors="surrogateescape")
    return proc.returncode, _encode_eol(merged, _path_eol(root, path, fallback=stages[0]))


def separate_eol_conflicts(root: Path, paths: list[str]) -> dict:
    """Take line-ending noise out of an in-flight merge's conflicts.

    For each unmerged path whose stages differ in CRLF/LF, merge again on LF-normalised
    text. A path that then merges cleanly is an **EOL-only** conflict: it is written in its
    own line ending, staged, and reported under ``eol_only`` — nothing is left for a
    resolver to decide there, and handing it over would mean re-typing the whole file.
    A path that still conflicts gets the normalised merge's (smaller, real) conflict
    markers written in place of the whole-file one git produced, under ``renormalized``;
    it stays unmerged, so the ordinary resolve contract and its validation apply as-is.
    """
    from modules.flow_gate.services import git_service as _gs
    from modules.flow_gate.storage.safe_path import resolve_in_root

    with document_write_ports.write_paths([root / path for path in paths]):
        eol_only: list[str] = []
        renormalized: list[str] = []
        for path in paths:
            target = resolve_in_root(root, path)
            if target is None:
                continue
            result = _eol_normalized_merge(root, path)
            if result is None:
                continue
            conflicts, merged = result
            try:
                before = target.read_bytes()
            except OSError:
                before = b""
            if conflicts == 0:
                document_write_ports.write_gate("paths", [target], lambda: target.write_bytes(merged))
                proc = _gs._run_git(["add", "--", path], cwd=root)
                if proc.returncode != 0:
                    _log.warning("eol-only conflict %s could not be staged: %s",
                                 path, _gs._last_line(proc.stderr))
                    document_write_ports.write_gate("paths", [target], lambda: target.write_bytes(before))
                    continue
                eol_only.append(path)
            elif merged != before:
                document_write_ports.write_gate("paths", [target], lambda: target.write_bytes(merged))
                renormalized.append(path)
        return {"eol_only": eol_only, "renormalized": renormalized}


def apply_eol_separation(merge_id: int, root: Path) -> dict:
    """``separate_eol_conflicts`` over a session's files, recorded on the session: an
    EOL-only file is marked resolved and listed in ``context["eol_only_paths"]`` so the
    screen, the AI mention and the reviewer can all say why it needs no resolution.
    Best-effort — a failure here leaves the ordinary git conflict exactly as it was."""
    from modules.flow_gate.services import git_service as _gs

    try:
        paths = [row["path"] for row in _gs.db_git.session_files(merge_id)]
        outcome = separate_eol_conflicts(root, paths)
        for path in outcome["eol_only"]:
            _gs.db_git.mark_file_resolved(merge_id, path)
        session = _gs.db_git.get_session(merge_id)
        context = _gs.db_git.session_context(session)
        context["eol_only_paths"] = outcome["eol_only"]
        context["eol_renormalized_paths"] = outcome["renormalized"]
        _gs.db_git.set_session_context(merge_id, context)
        return outcome
    except Exception:
        _log.warning("eol separation failed for merge %s", merge_id, exc_info=True)
        return {"eol_only": [], "renormalized": []}


def _revert_in_flight(wt_path: Path) -> bool:
    """Is a `revert --no-commit` still open in this worktree?

    ``REVERT_HEAD`` is asked for through ``rev-parse --git-path`` rather than by joining
    ``.git``: a group slot is a real ``git worktree``, so its ``.git`` is a FILE pointing at
    the shared gitdir and every ``wt / ".git" / "X"`` test would answer False forever.
    """
    from modules.flow_gate.services import git_service as _gs
    proc = _gs._run_git(
        ["rev-parse", "--git-path", "REVERT_HEAD"],
        cwd=wt_path, timeout=_gs.GIT_READ_TIMEOUT_SEC,
    )
    if proc.returncode != 0:
        return False
    raw = (proc.stdout or "").strip()
    if not raw:
        return False
    candidate = Path(raw)
    if not candidate.is_absolute():
        candidate = wt_path / candidate
    return candidate.exists()


def _set_tr_review_state(merge_id: int, review_state: str) -> None:
    """Move a TR conflict session between `open` and `resolved` (read-modify-write)."""
    from modules.flow_gate.services import git_service as _gs
    session = _gs.db_git.get_session(merge_id)
    context = _gs.db_git.session_context(session)
    context["review_state"] = review_state
    _gs.db_git.set_session_context(merge_id, context)


def open_tr_conflict_session(
    cancel_session: dict,
    *,
    kind: str,
    group_id: str,
    ledger_row_id: int,
    doc_id: str,
    doc_code: str,
    target_sha: str,
    original_sha: Optional[str],
    subject: str,
    body: str,
    extra_context: Optional[dict] = None,
) -> Optional[dict]:
    """Keep a conflicted revert alive as a conflict session instead of destroying it.

    a Graph attempt's conflicted merge is kept the same way (``kind``
    ``graph_attempt_merge``); ``extra_context`` adds what names it (the attempt, its node and result).

    Returns ``{"merge_id": int, "files": [...]}``, or **None** when the conflict cannot be
    kept — git reports no unmerged path (so the revert failed for some other reason and
    there is nothing for anyone to edit), or the session row could not be written (a merge
    session is already open for this group; one open session per group is a DB0007 I3
    invariant and not one to bend). None means the caller falls back to the old destroy,
    which is the only safe default: an in-flight revert nobody can see, finish or abort is
    worse than no revert at all.

    The group goes to ``status='conflict'`` with this ``merge_id``, exactly as a finalize
    merge does, because that is what makes the existing screen open on it. The status it
    came from is stored so the abort can put it back — the group was mid-rewind, not
    mid-merge, and 'waiting' would be a lie about which button to press next.
    """
    from modules.flow_gate.services import git_service as _gs
    wt_path: Path = cancel_session["wt_path"]
    paths = _gs._unmerged_paths(wt_path)
    if not paths:
        return None
    state = _gs.db_git.get_state(group_id) or {}
    try:
        merge_id = _gs.db_git.create_session(
            group_id, paths, kind=kind,
            context={
                "review_state": TR_CONFLICT_REVIEW_OPEN,
                "ledger_row_id": int(ledger_row_id),
                "doc_id": doc_id,
                "doc_code": doc_code,
                "target_sha": target_sha,
                "original_sha": original_sha,
                "subject": subject,
                "body": body,
                "prev_status": state.get("status") or "none",
                "branch": state.get("branch"),
                **(extra_context or {}),
            },
        )
    except Exception:
        _log.warning(
            "tr conflict session could not be opened for %s; falling back to restore",
            group_id, exc_info=True,
        )
        return None
    apply_eol_separation(merge_id, wt_path)
    _gs._set_status(group_id, "conflict", merge_id=merge_id)
    return {"merge_id": int(merge_id), "files": paths}


def tr_conflict_session(group_id: str) -> Optional[dict]:
    """This group's open TR conflict session as a screen needs it, or None.

    Best-effort by construction: it feeds a panel block, and a group whose session table
    cannot be read should render without that block rather than fail the whole panel.
    """
    from modules.flow_gate.services import git_service as _gs
    try:
        session = _gs.db_git.get_open_session_by_group(group_id)
    except Exception:
        _log.warning("tr conflict session lookup failed for %s", group_id, exc_info=True)
        return None
    kind = _gs.db_git.session_kind(session) if session else None
    if not session or kind not in _gs.db_git.TR_SESSION_KINDS:
        return None
    merge_id = int(session["merge_id"])
    context = _gs.db_git.session_context(session)
    try:
        files = [row["path"] for row in _gs.db_git.session_files(merge_id)]
        remaining = _gs.db_git.remaining_conflicts(merge_id)
    except Exception:
        files, remaining = [], []
    return {
        "merge_id": merge_id,
        "kind": kind,
        "doc_id": context.get("doc_id"),
        "doc_code": context.get("doc_code"),
        "subject": context.get("subject"),
        "files": files,
        "remaining": remaining,
        "review_state": context.get("review_state") or TR_CONFLICT_REVIEW_OPEN,
    }


def commit_tr_conflict(
    group_id: str, merge_id: int, *, review_fingerprint: Optional[str] = None,
) -> dict:
    """Commit a resolved TR conflict — the second press, and the reason there is one.

    A merge conflict may finish itself: both sides were written by people, "keep both" is
    close to the whole question, and a person still presses [병합] at the end. A revert is
    not symmetric. One side says "remove what this TR did" and the other is every change
    that landed on top of it, so a resolver in a hurry — or an AI that is confidently
    wrong — can hand back a file with no conflict markers in it that undid half the TR.
    Marker-free is not the same claim as correct. If ``resolve_conflicts`` committed on the
    AI's say-so, the strip would draw "cancelled" over a tree that is neither the old state
    nor the new one, and that is worse than the dead end this whole block replaces.

    So: the session parks at ``review_state='resolved'``, the person reads the diff, and
    this is what they press. Returns the new commit and the session context; the ledger
    writes are the caller's (tr_commit_service owns the ledger, this module owns git).

    0668 T0004: the common review screen's [승인] arrives here with the
    ``review_fingerprint`` it showed. Under the lock the fingerprint must still be the
    session's and the live tree must still be the frozen one; otherwise nothing is
    committed, the candidate is re-frozen and ``stale_review`` asks for a fresh look —
    the same contract ``approve_merge_review`` gives a merge. The legacy ``tr-commit``
    route passes no fingerprint and keeps its old behaviour.
    """
    from modules.flow_gate.services import git_service as _gs
    session, cfg, project_id, root = _gs._session_context(group_id, merge_id)
    kind = _gs.db_git.session_kind(session)
    if kind not in _gs.db_git.TR_SESSION_KINDS:
        raise GitServiceError(409, "invalid_state", "not a TR conflict session")
    context = _gs.db_git.session_context(session)
    if context.get("review_state") != TR_CONFLICT_REVIEW_RESOLVED:
        raise GitServiceError(
            409, "conflict_markers_remain", "resolve every file in this session first"
        )
    remaining = _gs.db_git.remaining_conflicts(merge_id)
    if remaining:
        raise GitServiceError(
            409, "conflict_markers_remain",
            f"{len(remaining)} file(s) still unresolved in this session",
        )
    # Asked of git, not just of our own bookkeeping: the resolve endpoint marks a row
    # resolved, and between then and now somebody could have touched the worktree.
    unmerged = _gs._unmerged_paths(root)
    if unmerged:
        raise GitServiceError(
            409, "conflict_markers_remain",
            f"git still reports {len(unmerged)} unmerged path(s)",
        )

    # D 3.10: the Group's G — another Group's work no longer blocks this commit.
    lock = _acquire_group_lock(project_id, group_id, "tr_conflict")
    if lock is None:
        raise GitServiceError(
            409, "git_busy", f"another git operation is in progress for '{project_id}'"
        )
    graph_merge = kind == _gs.db_git.SESSION_KIND_GRAPH_ATTEMPT_MERGE
    try:
        if graph_merge:
            # under G the session must still be open and its merge the one in flight — an abort (a
            # person's, the sweep's, a Stop's) that won the lock leaves nothing to commit on.
            _graph_merge_owned(group_id, merge_id, root)
        if review_fingerprint is not None:
            fresh = _gs.db_git.session_context(_gs.db_git.get_session(merge_id))
            if not fresh.get("review_fingerprint"):
                fresh.update(_freeze_tr_candidate(root, fresh))
                _gs.db_git.set_session_context(merge_id, fresh)
            stale = review_fingerprint != fresh.get("review_fingerprint")
            if not stale and not _live_tr_candidate_matches(root, fresh):
                fresh.update(_freeze_tr_candidate(root, fresh))
                fresh["last_error"] = {"code": "identity_mismatch"}
                _gs.db_git.set_session_context(merge_id, fresh)
                stale = True
            if stale:
                raise GitServiceError(
                    409, "stale_review",
                    "the reviewed tree has changed since this fingerprint was shown",
                    {"review_fingerprint": fresh.get("review_fingerprint")},
                )
        # A resolution that keeps the tree exactly as HEAD has it — "take ours" on every
        # chunk, which is a perfectly reasonable answer and one an AI will sometimes give.
        # There is nothing to commit then, and an empty commit is noise in the history
        #. It is not a failure either: the outcome the person asked for is
        # already true of the tree, and the ledger records it the same way the cancel loop
        # records its own empty reverts.
        staged = _gs._run_git(
            ["diff", "--cached", "--quiet"], cwd=root, timeout=_gs.GIT_READ_TIMEOUT_SEC,
        )
        # a merge is committed even when its tree is HEAD's — its commit is what records the attempt's
        # result as a parent on the group branch, and it ends the in-flight merge.
        empty = staged.returncode == 0 and kind != _gs.db_git.SESSION_KIND_GRAPH_ATTEMPT_MERGE
        commit = None
        if not empty:
            proc = _gs._run_git(
                [*_gs._GIT_IDENT, "commit",
                 "-m", context.get("subject") or "", "-m", context.get("body") or ""],
                cwd=root, author_env=_author_env_from_cfg(cfg), timeout=GIT_LOCAL_TIMEOUT_SEC,
            )
            if proc.returncode != 0:
                raise GitServiceError(500, "git_error", "Git command failed", diagnostic=_gs._last_line(proc.stderr))
            head = _gs._run_git(["rev-parse", "HEAD"], cwd=root, timeout=_gs.GIT_READ_TIMEOUT_SEC)
            commit = (head.stdout or "").strip() or None
        # Either way the revert is over; the sequencer state is what is left of it.
        _gs._run_git(["revert", "--quit"], cwd=root, timeout=_gs.GIT_READ_TIMEOUT_SEC)
        if graph_merge:
            # closed under the same G as the commit, so no abort can come between them.
            _gs.db_git.close_session(merge_id, "done")
            _gs._set_status(group_id, context.get("prev_status") or "waiting")
    finally:
        _release_group_lock(lock)

    if not graph_merge:
        _gs.db_git.close_session(merge_id, "done")
        _gs._set_status(group_id, context.get("prev_status") or "waiting")
    return {
        "ok": True,
        "result": {
            "status": "empty" if empty else "committed", "kind": kind, "commit": commit,
            # the common review vocabulary, next to the TR-specific status.
            "review_state": "completed",
            "doc_id": context.get("doc_id"), "doc_code": context.get("doc_code"),
            "ledger_row_id": context.get("ledger_row_id"),
            "target_sha": context.get("target_sha"),
            "original_sha": context.get("original_sha"),
            "commit_subject": context.get("subject"),
        },
    }


def abort_tr_conflict(group_id: str, merge_id: int, *, no_wait: bool = False) -> dict:
    """Give up on a TR conflict: restore the worktree, close the session, keep the row.

    The body is the old :func:`restore_after_failed_revert` — ``revert --quit`` then
    ``reset --hard HEAD``, and still never ``git clean`` (0382). The ledger row is left
    exactly as it was, because nothing about it changed: the commit it names is still live
    (a cancel that gave up) or still cancelled (a reapply that gave up).

    a ``graph_attempt_merge`` session is aborted under the group's G
    (:func:`_abort_graph_attempt_merge`; ``no_wait`` for the sweep).
    """
    from modules.flow_gate.services import git_service as _gs
    session, _cfg, project_id, root = _gs._session_context(group_id, merge_id)
    kind = _gs.db_git.session_kind(session)
    if kind not in _gs.db_git.TR_SESSION_KINDS:
        raise GitServiceError(409, "invalid_state", "not a TR conflict session")
    if kind == _gs.db_git.SESSION_KIND_GRAPH_ATTEMPT_MERGE:
        return _abort_graph_attempt_merge(group_id, merge_id, project_id, root, no_wait=no_wait)
    context = _gs.db_git.session_context(session)
    _gs._run_git(["revert", "--quit"], cwd=root, timeout=_gs.GIT_READ_TIMEOUT_SEC)
    _gs._run_git(["reset", "--hard", "HEAD"], cwd=root, timeout=GIT_LOCAL_TIMEOUT_SEC)
    _gs.db_git.close_session(merge_id, "aborted")
    _gs._set_status(group_id, context.get("prev_status") or "waiting")
    return {
        "ok": True,
        "result": {
            "status": "aborted", "kind": kind,
            "doc_id": context.get("doc_id"), "doc_code": context.get("doc_code"),
            "ledger_row_id": context.get("ledger_row_id"),
        },
    }


def _graph_merge_owned(group_id: str, merge_id: int, root: Path) -> dict:
    """under G: the ``graph_attempt_merge`` session is still open and the merge in flight
    in the group worktree, if any, is its own (``MERGE_HEAD`` = its result). Returns its fresh context."""
    from modules.flow_gate.services import git_service as _gs
    fresh = _gs.db_git.get_session(merge_id)
    if fresh is None or fresh.get("status") != "open" or fresh.get("group_id") != group_id:
        raise GitServiceError(409, "invalid_state", f"merge session {merge_id} is no longer open")
    context = _gs.db_git.session_context(fresh)
    merge_head = _gs._rev_parse(root, "MERGE_HEAD")
    if merge_head is not None and merge_head != context.get("target_sha"):
        raise GitServiceError(409, "invalid_state", "the merge in flight is not this session's")
    return context


def _abort_graph_attempt_merge(
    group_id: str, merge_id: int, project_id: str, root: Path, *, no_wait: bool = False,
) -> dict:
    """give up on a Graph attempt's conflicted merge — a person's abort, the sweep's (TTL,
    orphan) and a Stop's alike. Under the group's G (the lock its resolution and commit take): the session must still
    be open and the merge in flight its own; ``merge --abort`` must succeed and leave no ``MERGE_HEAD`` and no
    unmerged path before the session closes. A refused lock or a failed abort raises and keeps the session open (the
    caller retries); a commit that won the lock closed the session first (409 ``invalid_state``) and its close
    finishes the integration. Never ``reset --hard`` and never ``git clean``."""
    from modules.flow_gate.services import git_service as _gs
    lock = _acquire_group_lock(project_id, group_id, "tr_conflict", no_wait=no_wait)
    if lock is None:
        raise GitServiceError(
            409, "git_busy", f"another git operation is in progress for '{project_id}'"
        )
    try:
        context = _graph_merge_owned(group_id, merge_id, root)
        if _gs._rev_parse(root, "MERGE_HEAD") is not None:
            # task 3(b): audited on the session
            # (``ref_operations[].stderr_tail``) and, on failure, marked "정리 실패"
            # (``context.abort_failure``, trigger ``graph_attempt_abort``) — a person's
            # abort, the sweep's and a Stop's all come through here.
            proc = _gs._abort_merge(
                root, merge_id=merge_id, context=context, reason="graph_attempt_abort",
                timeout=GIT_LOCAL_TIMEOUT_SEC,
            )
            if proc.returncode != 0:
                _gs._record_abort_failure(merge_id, GRAPH_ATTEMPT_ABORT_TRIGGER, proc)
                # The admin force cleanup covers finalize merge sessions only.
                raise _abort_failed_error(proc, merge_id, recovery=None)
        if _gs._rev_parse(root, "MERGE_HEAD") is not None or _gs._unmerged_paths(root):
            remains = subprocess.CompletedProcess(
                ["git", "merge", "--abort"], 0, "",
                "error: the merge state remains after the abort",
            )
            _gs._record_abort_failure(merge_id, GRAPH_ATTEMPT_ABORT_TRIGGER, remains)
            raise GitServiceError(500, "git_error", "the merge state remains after the abort",
                                  details={"reason": "the merge state remains after the abort",
                                           "merge_id": merge_id},
                                  params={"reason": "the merge state remains after the abort"})
        _gs.db_git.close_session(merge_id, "aborted")
        _gs._set_status(group_id, context.get("prev_status") or "waiting")
    finally:
        _release_group_lock(lock)
    return {
        "ok": True,
        "result": {
            "status": "aborted", "kind": _gs.db_git.SESSION_KIND_GRAPH_ATTEMPT_MERGE,
            "doc_id": context.get("doc_id"), "doc_code": context.get("doc_code"),
            "ledger_row_id": context.get("ledger_row_id"),
        },
    }


def _freeze_tr_candidate(root: Path, context: dict) -> dict:
    """the TR conflict's review candidate, frozen like a merge's.

    A TR conflict used to end at ``review_state='resolved'`` with no record of WHAT was
    resolved, so the commit press could not tell the tree it was shown from the tree it
    commits. This records the same fields the merge review gate reads (``base_head``,
    ``snapshot_tree``, ``changes``, ``review_fingerprint``) so the common review screen can
    show the diff and ``approve`` can demand the fingerprint the person actually saw.
    There is no MERGE_HEAD in a revert; the reverted commit stands in for it in the
    fingerprint so two TRs resolving to the same tree still fingerprint differently.
    """
    from modules.flow_gate.services import git_service as _gs
    unmerged = _gs._unmerged_paths(root)
    if unmerged:
        raise GitServiceError(
            409, "conflict_markers_remain",
            f"git still reports {len(unmerged)} unmerged path(s)",
        )
    base_head = _gs._rev_parse(root, "HEAD")
    write_tree = _gs._run_git(["write-tree"], cwd=root, timeout=_gs.GIT_READ_TIMEOUT_SEC)
    if write_tree.returncode != 0:
        raise GitServiceError(500, "git_error", "Git command failed", diagnostic=_gs._last_line(write_tree.stderr))
    snapshot_tree = (write_tree.stdout or "").strip()
    ls = _gs._run_git(["ls-tree", "-r", "-z", snapshot_tree], cwd=root, timeout=_gs.GIT_READ_TIMEOUT_SEC)
    if ls.returncode != 0:
        raise GitServiceError(500, "git_error", "Git command failed", diagnostic=_gs._last_line(ls.stderr))
    manifest = _gs._parse_ls_tree_z(ls.stdout or "")
    diff_proc = _gs._run_git(
        ["diff", "--name-status", "-M", "-z", base_head or "", snapshot_tree, "--"],
        cwd=root, timeout=_gs.GIT_READ_TIMEOUT_SEC,
    )
    if diff_proc.returncode != 0:
        raise GitServiceError(500, "git_error", "Git command failed", diagnostic=_gs._last_line(diff_proc.stderr))
    return {
        "base_head": base_head,
        "snapshot_tree": snapshot_tree,
        "changes": _gs._parse_name_status_manifest(diff_proc.stdout or ""),
        "review_fingerprint": _gs._compute_review_fingerprint(
            base_head, context.get("target_sha"), None, manifest,
        ),
    }


def _live_tr_candidate_matches(root: Path, context: dict) -> bool:
    """Is the tree the TR commit would record RIGHT NOW the one that was reviewed?"""
    from modules.flow_gate.services import git_service as _gs
    if _gs._unmerged_paths(root):
        return False
    if _gs._rev_parse(root, "HEAD") != context.get("base_head"):
        return False
    write_tree = _gs._run_git(["write-tree"], cwd=root, timeout=_gs.GIT_READ_TIMEOUT_SEC)
    if write_tree.returncode != 0:
        return False
    return (write_tree.stdout or "").strip() == context.get("snapshot_tree")


def _tr_review_context(group_id: str, merge_id: int) -> tuple[dict, dict, Path]:
    """``(session, context, root)`` of a resolved TR conflict, freezing a candidate for a
    session parked before 0668 (resolved, but never fingerprinted)."""
    from modules.flow_gate.services import git_service as _gs
    session, _cfg, _project_id, root = _gs._session_context(group_id, merge_id)
    if _gs.db_git.session_kind(session) not in _gs.db_git.TR_SESSION_KINDS:
        raise GitServiceError(409, "review_not_ready", "not a TR conflict session")
    context = _gs.db_git.session_context(session)
    if context.get("review_state") != TR_CONFLICT_REVIEW_RESOLVED:
        raise GitServiceError(409, "review_not_ready", "this conflict has not been resolved yet")
    if not context.get("review_fingerprint"):
        context.update(_freeze_tr_candidate(root, context))
        _gs.db_git.set_session_context(merge_id, context)
    return session, context, root


def tr_conflict_review(group_id: str, merge_id: int) -> dict:
    """GET …/review for a TR conflict: the common review screen's payload.

    Same shape as ``get_merge_review`` so one screen serves both. What a TR review does
    not have is said with the capability flags rather than with a different screen: no
    review conversation and no reject (there is no resolver baseline to re-run a revert
    from — the resolver itself is where a TR resolution is redone), only approve.

    task 4(c): opening (reading) the review is NOT activity;
    it never calls ``touch_session`` and leaves ``touched_at`` (the sweep TTL basis) as it is.
    """
    from modules.flow_gate.services import git_service as _gs
    session, context, _root = _tr_review_context(group_id, merge_id)
    return {
        "ok": True,
        "result": {
            "group_id": group_id,
            "merge_id": merge_id,
            "kind": _gs.db_git.session_kind(session),
            "review_state": _gs.REVIEW_STATE_PENDING,
            "review_fingerprint": context.get("review_fingerprint"),
            "instruction_generation": 0,
            "base_head": context.get("base_head"),
            "merge_head": None,
            "snapshot_tree": context.get("snapshot_tree"),
            "changes": context.get("changes") or [],
            "conflict_origins": [],
            "conflict_supersedes": [],
            "conversation": [],
            "held_test_operations": [],
            "pending_conversation": None,
            "resolver_provider": context.get("resolver_provider"),
            "auto_authority": False,
            "reconciliation_kind": None,
            "last_error": context.get("last_error"),
            "can_approve": True,
            "can_reject": False,
            "can_send": False,
            "owner_type": _session_owner_type(session),
            "tr_conflict": {
                "doc_id": context.get("doc_id"),
                "doc_code": context.get("doc_code"),
                "target_sha": (context.get("target_sha") or "")[:7] or None,
                "subject": context.get("subject"),
            },
        },
    }


def tr_conflict_review_file_diff(group_id: str, merge_id: int, path: str) -> dict:
    """Old(``base_head``)/new(``snapshot_tree``) of one path in a TR review candidate."""
    from modules.flow_gate.services import git_service as _gs
    normalized = path.replace("\\", "/")
    _session, context, root = _tr_review_context(group_id, merge_id)
    old = _gs._diff_side_from_commit(root, context.get("base_head"), normalized)
    new = _gs._diff_side_from_commit(root, context.get("snapshot_tree"), normalized)
    return {"ok": True, "data": {
        "group_id": group_id, "merge_id": merge_id, "path": path,
        "status": _gs._diff_status(old, new, path), "old": old, "new": new,
    }}


def _split_content_segments(content: str) -> Optional[list[dict]]:
    """Parse ``content`` into ordered segments alternating common text and marker
    chunks: ``{"type": "common", "lines": [...]}`` or ``{"type": "chunk", "ours": [...],
    "base": [...] | None, "theirs": [...]}`` — ``base`` is ``None`` when the chunk has no
    ``|||||||`` section (pre-zdiff3 sessions, or a merge that could not produce a common
    ancestor). Returns ``None`` if malformed (unbalanced or nested markers).

    Segments always alternate common/chunk/common/chunk/.../common — a leading or
    trailing common segment may be empty, but it is always there, so a chunk segment
    always has a common segment immediately before and after it in this list. That is
    what lets a chunk's resolution be found by ANCHOR instead of by scanning the whole
    file: the text immediately around a chunk is unedited context, so locating where
    THAT landed in the submitted file brackets exactly where this chunk's own resolution
    must sit (review finding — see:func:`_anchor_chunk_selections`).
    """
    from modules.flow_gate.services import git_service as _gs
    segments: list[dict] = []
    common: list[str] = []
    state = "COMMON"
    chunk: Optional[dict] = None
    for line in (content or "").splitlines():
        if state == "COMMON":
            if _gs._CONFLICT_OPEN_RE.match(line):
                segments.append({"type": "common", "lines": common})
                common = []
                chunk = {"ours": [], "base": None, "theirs": []}
                state = "OURS"
            else:
                common.append(line)
        elif state == "OURS":
            if _CONFLICT_BASE_RE.match(line):
                chunk["base"] = []
                state = "BASE"
            elif _CONFLICT_SEP_RE.match(line):
                state = "THEIRS"
            elif _gs._CONFLICT_OPEN_RE.match(line) or _gs._CONFLICT_CLOSE_RE.match(line):
                return None
            else:
                chunk["ours"].append(line)
        elif state == "BASE":
            if _CONFLICT_SEP_RE.match(line):
                state = "THEIRS"
            elif _gs._CONFLICT_OPEN_RE.match(line) or _gs._CONFLICT_CLOSE_RE.match(line):
                return None
            else:
                chunk["base"].append(line)
        elif state == "THEIRS":
            if _gs._CONFLICT_CLOSE_RE.match(line):
                segments.append({"type": "chunk", **chunk})
                chunk = None
                state = "COMMON"
            elif (
                _gs._CONFLICT_OPEN_RE.match(line)
                or _CONFLICT_SEP_RE.match(line)
                or _CONFLICT_BASE_RE.match(line)
            ):
                return None
            else:
                chunk["theirs"].append(line)
    if state != "COMMON":
        return None
    segments.append({"type": "common", "lines": common})
    return segments


def _split_conflict_chunks_with_base(content: str) -> Optional[list[dict]]:
    """The chunk segments from :func:`_split_content_segments`, common text dropped —
    ``{"ours": [...], "base": [...] | None, "theirs": [...]}`` per chunk, or ``None`` if
    malformed. Kept as its own name for callers that only need chunk identity, not the
    surrounding-context anchors.
    """
    segments = _split_content_segments(content)
    if segments is None:
        return None
    return [{"ours": s["ours"], "base": s["base"], "theirs": s["theirs"]}
            for s in segments if s["type"] == "chunk"]


def _chunk_added_lines(side: list[str], base: list[str]) -> list[str]:
    """Lines in ``side`` that are not in ``base`` (trimmed comparison — E12 note in the earlier implementation)."""
    base_set = {line.strip() for line in base}
    return [line for line in side if line.strip() not in base_set]


def _find_subsequence(haystack: list[str], needle: list[str], start: int) -> Optional[int]:
    """First index ``i >= start`` where ``haystack[i:i+len(needle)] == needle``, else None."""
    if not needle:
        return None
    n = len(needle)
    for i in range(start, len(haystack) - n + 1):
        if haystack[i:i + n] == needle:
            return i
    return None


def _find_subsequence_window(haystack: list[str], needle: list[str], start: int, end: int) -> Optional[int]:
    """Like :func:`_find_subsequence`, but the match must fit entirely inside
    ``[start, end)`` — a chunk's anchored window — so a candidate cannot spill past the
    common context that brackets it."""
    if not needle:
        return None
    n = len(needle)
    limit = min(end, len(haystack)) - n
    if limit < start:
        return None
    for i in range(start, limit + 1):
        if haystack[i:i + n] == needle:
            return i
    return None


def _select_conflict_chunk(
    submitted_lines: list[str], start: int, end: int, ours: list[str], theirs: list[str],
) -> tuple[str, Optional[int], Optional[int]]:
    """Classify one chunk's resolution against ``submitted_lines[start:end]`` — its own
    ANCHORED window, bracketed by the common context that :func:`_split_content_segments`
    found immediately before and after it in the original (see
    :func:`_anchor_chunk_selections`) — as an exact ``ours``/``theirs``/``both``
    selection, or ``manual`` if nothing inside the window matches. Returns
    ``(selection, start_line, end_line)``, a 1-based inclusive line range, or
    ``("manual", None, None)``.

    Chunk-local by construction: a candidate can only match inside ``[start, end)``, so a
    piece of common text elsewhere in the file that happens to equal ``ours`` or
    ``theirs`` verbatim — before this chunk, after it, or belonging to a neighboring
    chunk's own window — can never stand in for this chunk's actual resolution (0602
    review finding: an unbounded, cursor-forward search over the WHOLE
    remaining file let a coincidental match sitting in ordinary, unedited context
    outrank — or hide — the chunk's real resolved text).

    Classification compares the ENTIRE anchored window with each candidate.  A side's
    original text merely appearing as a subsequence is not a one-side selection: any
    additional resolved text makes the window ``manual`` so synthesized resolutions can
    proceed to review.  ``both`` is checked first only to handle an identical/empty-side
    overlap consistently; hard rejection remains limited to a whole-window exact
    ``ours`` or ``theirs`` selection.
    """
    window = submitted_lines[start:end]
    ranked = [(ours + theirs, "both"), (theirs + ours, "both"), (ours, "ours"), (theirs, "theirs")]
    for candidate_lines, label in ranked:
        if candidate_lines and window == candidate_lines:
            return label, start + 1, end
    return "manual", None, None


def _all_subsequence_positions(haystack: list[str], needle: list[str]) -> list[int]:
    """Every start position where ``needle`` occurs in ``haystack``."""
    if not needle:
        return []
    n = len(needle)
    return [i for i in range(len(haystack) - n + 1) if haystack[i:i + n] == needle]


def _anchor_chunk_selections(segments: list[dict], submitted_lines: list[str]) -> list[dict]:
    """Align all common segments and conflict resolutions as one ordered sequence.

    A greedy "first next-common match" is not a stable boundary: the same text can occur
    inside the resolution.  Instead, dynamic programming considers every order-preserving
    placement of the surviving common segments.  Paths first maximize preserved common
    context and then the amount of exact conflict-side text explained inside the resulting
    windows.  The latter tie-break selects the real second ``tail`` as common in
    ``ours=tail; common=tail; submitted=tail,tail``: it leaves the first ``tail`` available
    to classify as ``ours`` rather than creating an empty, falsely-manual window.

    If a non-empty common segment has no usable verbatim occurrence, it contributes no
    context score and falls back to the end of the remaining submitted text, preserving
    the previous best-effort wide window for submissions that also edited common context.
    """
    chunks = [segments[i] for i in range(1, len(segments), 2)]
    commons = [segments[i]["lines"] for i in range(0, len(segments), 2)]
    if not chunks:
        return []

    # Each state is end_cursor -> (score, anchor placements).  score is lexicographic:
    # matched common lines, exact side lines explained, then earlier total anchor starts.
    first = commons[0]
    first_positions = _all_subsequence_positions(submitted_lines, first)
    if not first:
        states = {0: ((0, 0, 0), [(0, 0)])}
    elif first_positions:
        states = {
            pos + len(first): ((len(first), 0, -pos), [(pos, pos + len(first))])
            for pos in first_positions
        }
    else:
        states = {0: ((0, 0, 0), [(0, 0)])}

    for chunk_index, chunk in enumerate(chunks):
        following = commons[chunk_index + 1]
        positions = _all_subsequence_positions(submitted_lines, following)
        next_states: dict[int, tuple[tuple[int, int, int], list[tuple[int, int]]]] = {}
        for cursor, (score, anchors) in states.items():
            candidates = [p for p in positions if p >= cursor]
            if not following:
                candidates = [len(submitted_lines)]
            elif not candidates:
                candidates = [len(submitted_lines)]
            for start in candidates:
                selection, start_line, end_line = _select_conflict_chunk(
                    submitted_lines, cursor, start, chunk["ours"], chunk["theirs"],
                )
                exact_lines = 0 if start_line is None else end_line - start_line + 1
                matched_common = len(following) if start in positions else 0
                end = start + matched_common
                candidate_score = (
                    score[0] + matched_common,
                    score[1] + exact_lines,
                    score[2] - start,
                )
                previous = next_states.get(end)
                placement = anchors + [(start, end)]
                if previous is None or candidate_score > previous[0]:
                    next_states[end] = (candidate_score, placement)
        states = next_states

    _score, anchors = max(states.values(), key=lambda item: item[0])
    results: list[dict] = []
    for idx, chunk in enumerate(chunks):
        window_start = anchors[idx][1]
        window_end = anchors[idx + 1][0]
        selection, start_line, end_line = _select_conflict_chunk(
            submitted_lines, window_start, window_end, chunk["ours"], chunk["theirs"],
        )
        results.append({
            "chunk": chunk, "selection": selection,
            "start_line": start_line, "end_line": end_line,
        })
    return results

def _chunk_original_ranges(segments: list[dict]) -> list[tuple[int, int]]:
    """1-based inclusive line range each chunk's marker block occupies in the original."""
    ranges: list[tuple[int, int]] = []
    line = 1
    for segment in segments:
        if segment["type"] == "common":
            line += len(segment["lines"])
            continue
        size = 3 + len(segment["ours"]) + len(segment["theirs"])
        if segment["base"] is not None:
            size += 1 + len(segment["base"])
        ranges.append((line, line + size - 1))
        line += size
    return ranges


def _content_from_chunk_resolutions(path: str, original: str, chunks) -> str:
    """the whole-file submission a per-chunk resolution stands for.

    An API model's single reply is capped: 0594's finalize.py is 111,807
    chars as a file but its 10 chunks are at most ~22k chars even when both sides are
    kept. So a file may be sent as ``chunks: [{"chunk": n, "content": "<lines that
    replace the whole marker block n>"}]`` instead of ``content``. The chunks are
    numbered 1.. in file order, exactly as the conflict mention lists them.

    This only ASSEMBLES the file: every chunk not listed keeps its marker block
    verbatim, so the caller's existing ``conflict_markers_remain`` check rejects it, and
    the assembled text then goes through the same side-drop / supersede validation as a
    whole-file submission. Nothing is written or staged here.
    """
    segments = _split_content_segments(original) if original else None
    ranges = _chunk_original_ranges(segments) if segments else []
    if not ranges:
        raise GitServiceError(
            422, "invalid_request",
            f"'{path}' has no conflict chunks left to resolve by number; send content instead",
        )
    if not isinstance(chunks, list) or not chunks:
        raise GitServiceError(422, "invalid_request", f"'{path}': chunks must be a non-empty list")
    resolutions: dict[int, str] = {}
    for item in chunks:
        number = item.get("chunk") if isinstance(item, dict) else None
        text = item.get("content") if isinstance(item, dict) else None
        if isinstance(number, bool) or not isinstance(number, int) or not isinstance(text, str):
            raise GitServiceError(
                422, "invalid_request",
                f"'{path}': each chunk needs an integer chunk number and a content string",
            )
        if not 1 <= number <= len(ranges):
            raise GitServiceError(
                422, "invalid_request",
                f"'{path}': chunk {number} does not exist (this file has chunks 1..{len(ranges)})",
            )
        if number in resolutions:
            raise GitServiceError(422, "invalid_request", f"'{path}': chunk {number} was sent twice")
        resolutions[number] = text
    lines = original.splitlines()
    assembled: list[str] = []
    cursor = 1
    for number, (start, end) in enumerate(ranges, start=1):
        assembled.extend(lines[cursor - 1:start - 1])
        if number in resolutions:
            assembled.extend(resolutions[number].splitlines())
        else:
            assembled.extend(lines[start - 1:end])
        cursor = end + 1
    assembled.extend(lines[cursor - 1:])
    trailing = "\n" if original.endswith(("\n", "\r")) else ""
    return "\n".join(assembled) + trailing


def _conflict_side_violations(original: str, submitted: str) -> list[dict]:
    """Every chunk :func:`_conflict_side_dropped` would reject, in file order.

    Each item is ``{"chunk": <0-based chunk index>, "start_line", "end_line"`` (the
    marker block's range in the original), ``"side"`` (the side selected verbatim),
    ``"dropped_side"``, ``"entry"`` (the anchored selection)}.

    Per-chunk selections remain owned by :func:`_anchor_chunk_selections` and are shared
    unchanged with :func:`_classify_conflict_chunks`.  Base-having conflict chunks are
    grouped when each intervening common segment has at most
    ``_CONFLICT_CHUNK_GROUP_MAX_COMMON_LINES`` lines.  A group is considered synthesized
    only when at least one chunk where both sides changed over base resolves as ``manual``
    or ``both``; exact-side selections elsewhere in that same nearby group are then part
    of the synthesis instead of independent side drops.

    A base-less chunk still participates in anchoring, but belongs to no group and breaks
    grouping on both sides.  A chunk where only one side changed cannot make a group
    synthesized.  Consequently all-ours, all-theirs, and alternating exact-side choices
    remain rejected when no genuinely synthesized both-changed chunk exists.
    """
    segments = _split_content_segments(original)
    if segments is None:
        return []
    chunks = [s for s in segments if s["type"] == "chunk"]
    if not chunks:
        return []

    submitted_lines = (submitted or "").splitlines()
    anchored = _anchor_chunk_selections(segments, submitted_lines)
    ranges = _chunk_original_ranges(segments)
    groups: list[list[tuple[int, dict]]] = []
    group: list[tuple[int, dict]] = []
    for segment_index in range(1, len(segments), 2):
        chunk_index = (segment_index - 1) // 2
        entry = anchored[chunk_index]
        if entry["chunk"].get("base") is None:
            if group:
                groups.append(group)
                group = []
            continue
        if group and len(segments[segment_index - 1]["lines"]) > _CONFLICT_CHUNK_GROUP_MAX_COMMON_LINES:
            groups.append(group)
            group = []
        group.append((chunk_index, entry))
    if group:
        groups.append(group)

    violations: list[dict] = []
    for entries in groups:
        both_changed_entries = []
        for chunk_index, entry in entries:
            chunk = entry["chunk"]
            base = chunk["base"]
            if bool(_chunk_added_lines(chunk["ours"], base)) and bool(
                _chunk_added_lines(chunk["theirs"], base)
            ):
                both_changed_entries.append((chunk_index, entry))
        if any(entry["selection"] in ("manual", "both") for _i, entry in both_changed_entries):
            continue
        for chunk_index, entry in both_changed_entries:
            if entry["selection"] not in _SUPERSEDE_SIDES:
                continue
            start_line, end_line = ranges[chunk_index]
            violations.append({
                "chunk": chunk_index, "start_line": start_line, "end_line": end_line,
                "side": entry["selection"],
                "dropped_side": "theirs" if entry["selection"] == "ours" else "ours",
                "entry": entry,
            })
    return violations


def _conflict_side_dropped(original: str, submitted: str) -> bool:
    """True if an unmerged nearby chunk group selects exactly one changed side.

    The boolean view of:func:`_conflict_side_violations` (the earlier implementation split it out so
    the resolve path can report every violating chunk and check `supersede` against
    each one); the verdict itself is unchanged.
    """
    return bool(_conflict_side_violations(original, submitted))


def _supersede_evidence(kept: list[str], dropped: list[str], base: list[str]) -> dict:
    """condition 3·4 — does ``kept`` really carry ``dropped``'s changes?

    Only the dropped side's own meaningful additions over ``base`` are evidence
    subjects. They are aligned against the kept side of THE SAME chunk, in order
    (``difflib`` opcodes, stripped comparison), so a copy elsewhere in the file never
    counts. Each such line is ``preserved`` (inside an ``equal`` run), ``changed`` (inside
    the ``replace`` run that took its place, paired in order with a kept line at least
    ``_SUPERSEDE_CHANGED_LINE_MIN_RATIO`` similar — an edited version of THAT line), or
    ``removed`` (a ``delete`` run, or no such counterpart: its place is gone). Pairing by
    similarity rather than by offset keeps a block of brand-new kept lines from
    absorbing dropped lines as "changed".
    """
    added = {line.strip() for line in _chunk_added_lines(dropped, base) if line.strip()}
    matcher = difflib.SequenceMatcher(
        None, [line.strip() for line in dropped], [line.strip() for line in kept], autojunk=False,
    )
    preserved: list[str] = []
    changed: list[dict] = []
    removed: list[str] = []
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "insert":
            continue
        cursor = j1
        for line in dropped[i1:i2]:
            if line.strip() not in added:
                continue
            if tag == "equal":
                preserved.append(line)
                continue
            counterpart = None
            if tag == "replace":
                counterpart = next((
                    k for k in range(cursor, j2)
                    if difflib.SequenceMatcher(
                        None, line.strip(), kept[k].strip(), autojunk=False,
                    ).ratio() >= _SUPERSEDE_CHANGED_LINE_MIN_RATIO
                ), None)
            if counterpart is None:
                removed.append(line)
            else:
                changed.append({"line": line, "replacement": kept[counterpart]})
                cursor = counterpart + 1
    return {"preserved": preserved, "changed": changed, "removed": removed}


def _supersede_findings(path: str, supersede, violations: list[dict], companions: Optional[list[dict]] = None) -> tuple[list[dict], list[dict]]:
    """Check one file's ``supersede`` declaration against its violating chunks.

    Returns ``(problems, records)``: ``problems`` is empty only when the declaration is
    valid for EVERY violating chunk; ``records`` is then the per-chunk evidence to keep.
    A declaration is never a bypass: it can only excuse chunks the checker already
    rejects, only the side actually kept, and only with in-place evidence or
    verbatim relocation into a verified target-side companion.
    """
    if not isinstance(supersede, dict):
        return [{"path": path, "chunk": None, "side": None, "condition": "malformed"}], []
    side = supersede.get("side")
    reason = supersede.get("reason")
    problems: list[dict] = []
    if side not in _SUPERSEDE_SIDES:
        problems.append({"path": path, "chunk": None, "side": side, "condition": "side_invalid"})
    if not isinstance(reason, str) or not reason.strip():
        problems.append({"path": path, "chunk": None, "side": side, "condition": "reason_empty"})
    if not violations:
        problems.append({"path": path, "chunk": None, "side": side, "condition": "no_violation"})
    if problems:
        return problems, []
    records: list[dict] = []
    for violation in violations:
        where = {
            "path": path, "chunk": violation["chunk"], "side": side,
            "start_line": violation["start_line"], "end_line": violation["end_line"],
        }
        if violation["side"] != side:
            problems.append({**where, "condition": "side_mismatch", "selected_side": violation["side"]})
            continue
        chunk = violation["entry"]["chunk"]
        evidence = _supersede_evidence(chunk[side], chunk[violation["dropped_side"]], chunk["base"])
        relocated = conflict_companions.relocated_evidence(chunk, side, companions or [])
        if relocated is not None:
            relocated["preserved"] = _chunk_added_lines(chunk[violation["dropped_side"]], chunk["base"])
            evidence = relocated
        counts = {
            "preserved": len(evidence["preserved"]), "changed": len(evidence["changed"]),
            "removed": len(evidence["removed"]),
        }
        if evidence["removed"]:
            problems.append({**where, "condition": "dropped_lines_removed", **counts,
                             "removed_lines": evidence["removed"]})
        elif not evidence["preserved"] or len(evidence["preserved"]) <= len(evidence["changed"]):
            problems.append({**where, "condition": "insufficient_preserved", **counts})
        else:
            records.append({
                "chunk": violation["chunk"],
                "start_line": violation["start_line"], "end_line": violation["end_line"],
                "kept_side": side, "dropped_side": violation["dropped_side"],
                "preserved_lines": evidence["preserved"],
                "changed_lines": evidence["changed"],
                **({"companion_path": evidence["companion_path"]} if "companion_path" in evidence else {}),
            })
    return problems, records

def _classify_conflict_chunks(path: str, original: str, submitted: str) -> list[dict]:
    """per-chunk selection the review screen overlays on
    the real diff: which conflict chunk resolved to ``ours``/``theirs``/``both``/
    ``manual``, and (best-effort) where that ended up in the submitted text.

    Shares its chunk-local, context-anchored match with :func:`_conflict_side_dropped`
    via :func:`_anchor_chunk_selections` — the priority order in
    :func:`_select_conflict_chunk` mirrors the earlier implementation, with the same pragmatic narrowing
    noted there: a left-to-right search within the anchored window rather than the earlier implementation's
    stricter "unique match only" rule, so an ambiguous/no-match chunk still gets a
    selection label, just no line range.
    """
    segments = _split_content_segments(original)
    if segments is None:
        return []
    if not any(s["type"] == "chunk" for s in segments):
        return []
    submitted_lines = (submitted or "").splitlines()
    results: list[dict] = []
    for idx, entry in enumerate(_anchor_chunk_selections(segments, submitted_lines)):
        chunk = entry["chunk"]
        ours_text, theirs_text = "\n".join(chunk["ours"]), "\n".join(chunk["theirs"])
        chunk_id = hashlib.sha256(
            "\x00".join((path, str(idx), ours_text, theirs_text)).encode("utf-8", errors="surrogateescape")
        ).hexdigest()
        results.append({
            "path": path, "chunk_id": chunk_id, "selection": entry["selection"],
            "start_line": entry["start_line"], "end_line": entry["end_line"],
            "range_ambiguous": entry["start_line"] is None,
        })
    return results


def _session_context(
    group_id: Optional[str], merge_id: int, *, project_id: Optional[str] = None,
) -> tuple[dict, dict, str, Path]:
    """``(session, cfg, project_id, root)`` — ``root`` is the repo the conflict lives in.

    A finalize merge conflicts in the base checkout; a TR revert or reapply conflicts in the
    group's own worktree (088). That one value is the entire difference for everything
    downstream — the file list, the resolved writes, the abort — which is why the two kinds
    can share a table, a screen, a set of endpoints and an AI run at all.

    for a finalize merge the root comes from the attempt's pinned target
    (``merge_target.resolve_session_target``) — the shared base checkout for a base
    (or legacy) target, the managed target workspace otherwise. This is the ONLY
    place a conflict file read/write root is decided.

    a ``branch_merge`` session has no group. It is addressed by
    ``project_id`` (and ``group_id=None``) and its root is its managed target
    workspace. A group-addressed call can never reach it and a project-addressed call
    can never reach a group's session — the owner is part of the identity check.
    """
    from modules.flow_gate.services import git_service as _gs
    session = _gs.db_git.get_session(merge_id)
    if session is None or session.get("status") != "open":
        raise GitServiceError(404, "not_found", f"merge session {merge_id} not found")
    if _is_branch_merge_session(session):
        if group_id is not None or not project_id or _session_project_id(session) != project_id:
            raise GitServiceError(404, "not_found", f"merge session {merge_id} not found")
        cfg = _gs.db_git.get_config(project_id) or {}
        target = merge_target.resolve_session_target(session)
        if target.root is None:
            raise GitServiceError(409, "invalid_state", "target checkout is not available")
        return session, cfg, project_id, target.root
    if project_id is not None or session.get("group_id") != group_id:
        raise GitServiceError(404, "not_found", f"merge session {merge_id} not found")
    cfg, _state, project_id, _base_root, wt_path = _gs._finalize_context(group_id)
    if _gs.db_git.session_kind(session) in _gs.db_git.WORKTREE_SESSION_KINDS:
        return session, cfg, project_id, wt_path
    target = merge_target.resolve_session_target(session)
    if target.root is None:
        raise GitServiceError(409, "invalid_state", "target checkout is not available")
    return session, cfg, project_id, target.root


def resolve_conflict_src_root(
    group_id: Optional[str], merge_id: int, *, project_id: Optional[str] = None,
) -> Path:
    """Return the checked-out root that owns the validated open conflict session."""
    from modules.flow_gate.services import git_service as _gs
    _session, _cfg, _project_id, root = _gs._session_context(group_id, merge_id, **({"project_id": project_id} if project_id else {}))
    return root


def list_conflicts(group_id: Optional[str], merge_id: int, *, project_id: Optional[str] = None) -> dict:
    from modules.flow_gate.services import git_service as _gs
    session, cfg, _project_id, root = _gs._session_context(group_id, merge_id, **({"project_id": project_id} if project_id else {}))
    # reading the list is NOT activity. Only a real operation
    # (a resolution submission, an abort) bumps the sweep TTL; opening the screen used to
    # restart the 24 h clock every time, so a stuck session could never age out.
    state = (_gs.db_git.get_state(group_id) if group_id else None) or {}
    files = []
    for row in _gs.db_git.session_files(merge_id):
        path = row["path"]
        try:
            content = (root / path).read_text(encoding="utf-8", errors="replace")
        except OSError:
            content = ""
        files.append({
            "path": path,
            "content": content,
            "conflict_count": sum(
                1 for l in content.splitlines() if _gs._CONFLICT_OPEN_RE.match(l)
            ),
        })
    kind = _gs.db_git.session_kind(session)
    context = _gs.db_git.session_context(session)
    baseline = context.get("resolver_baseline") or {}
    target_branch = (
        merge_target.resolve_session_target(session).target_branch
        if kind in _MERGE_REVIEW_KINDS else None
    )
    branch_merge_info = context.get("branch_merge") or {}
    return {
        "ok": True,
        "merge_id": merge_id,
        # for an ordinary branch merge "the incoming branch" is its source.
        "branch": state.get("branch") or branch_merge_info.get("source_branch"),
        "owner_type": _session_owner_type(session),
        "project_id": _session_project_id(session),
        "source_branch": branch_merge_info.get("source_branch"),
        "base_branch": (cfg.get("base_branch") or "main"),
        # (additive): the branch this merge lands on ("ours").
        "target_branch": target_branch,
        "files": files,
        "resolved_paths": [row["path"] for row in _gs.db_git.session_files(merge_id) if row["resolved"]],
        "companion_candidates": conflict_companions.candidates(root, {f["path"] for f in files}),
        "companion_files": context.get("conflict_companions") or [],
        "checkpoint_recovery": context.get("checkpoint_recovery"),
        # session files that differed only in line endings and were already
        # merged on normalised text (apply_eol_separation) — nothing to resolve there.
        "eol_only_paths": list(context.get("eol_only_paths") or []),
        # (additive): the last failed abort/cleanup of this
        # session — {trigger, reason (git's last stderr line), returncode, at} — or None.
        "abort_failure": context.get("abort_failure"),
        # The two commits a finalize merge joined, for reading either side's whole file
        # (`/remote/read` with `ref`). None for a TR session, which has no MERGE_HEAD.
        "refs": (
            {"ours": baseline.get("base_head"), "theirs": baseline.get("merge_head")}
            if baseline.get("base_head") and baseline.get("merge_head") else None
        ),
        # 088 — the same payload for both kinds, plus what a reader needs to know WHICH
        # question is being asked. "Combine two branches" and "undo this TR's commit" want
        # very different resolutions out of the same conflict markers, and the editor, the
        # toast and the AI mention all read this to say so.
        "kind": kind,
        "tr_conflict": (
            None if kind not in _gs.db_git.TR_SESSION_KINDS else {
                "doc_id": context.get("doc_id"),
                "doc_code": context.get("doc_code"),
                "target_sha": (context.get("target_sha") or "")[:7] or None,
                "subject": context.get("subject"),
                "review_state": context.get("review_state") or TR_CONFLICT_REVIEW_OPEN,
            }
        ),
    }


def _apply_conflict_resolution_locked(
    group_id: Optional[str],
    merge_id: int,
    staged: list[tuple[str, Path, str, str]],
    complete: bool,
    supersedes: list[dict],
    evaluated_paths: set[str],
    resolver_run_id: Optional[str],
    *,
    expected_project_id: str,
    companion_sources: Optional[dict[str, str]] = None,
) -> dict:
    """Apply a validated conflict submission inside the session's workspace lock.

    The pre-lock pass in resolve_conflicts performs expensive semantic validation.
    This boundary then re-reads the authoritative session/root and verifies both the
    registered path set and the exact pre-write file content before the first write.
    Thus no Git mutation of the same workspace (TR2 approval, review action, merge)
    can interleave between the final ownership check and write/git-add/session update.
    unit 8b: that lock is the session's G / W / B, no longer the project mutex.
    """
    from modules.flow_gate.services import git_service as _gs
    from modules.flow_gate.storage.safe_path import resolve_in_root

    # unit 8b: the session's workspace domain (TR / group update: the Group's G;
    # finalize / branch merge: the target's G / W / B) instead of the project mutex. The
    # domain comes from a pre-lock read; everything below re-reads under it as before.
    from . import branch_merge_publish as _bmp
    session_kwargs = (
        {"project_id": expected_project_id} if group_id is None else {}
    )
    prelock_session = _gs._session_context(group_id, merge_id, **session_kwargs)[0]
    lock_ctx, held = _bmp.session_lock(
        prelock_session, expected_project_id,
        holder_kind=(
            "tr_conflict"
            if _gs.db_git.session_kind(prelock_session) in _gs.db_git.TR_SESSION_KINDS
            else "merge_resolve"
        ),
    )

    automatic = False
    snapshot: Optional[dict] = None
    result: Optional[dict] = None
    try:
        # Owner identity and source root are authoritative only after the session's
        # domain is ours. Branch-merge sessions are project-addressed; all other sessions
        # stay group-addressed exactly as before.
        session, cfg, project_id, root = _gs._session_context(
            group_id, merge_id, **session_kwargs
        )
        if project_id != expected_project_id:
            raise GitServiceError(
                409, "invalid_state", "conflict session project changed"
            )
        is_branch_merge = _is_branch_merge_session(session)
        _gs.db_git.touch_session(merge_id)

        # Re-check the review-conversation exclusion under the same mutex that guards
        # the following source write.
        if resolver_run_id and (
            _gs.db_git.session_context(session).get("pending_conversation_run_id")
            == resolver_run_id
        ):
            raise GitServiceError(
                409, "review_conversation_cannot_resolve",
                "this run is a review conversation turn: answer in your final message, "
                "do not submit a resolution",
            )

        # a merge session whose workspace is no
        # longer merging (MERGE_HEAD gone) is refused BEFORE any file is written. It used
        # to write + `git add` + mark "resolved" and only then fail at the freeze, so every
        # retry just grew the "resolved" count while the session stayed stuck.
        _raise_if_merge_gone(session, root, merge_id)

        session_paths = {row["path"] for row in _gs.db_git.session_files(merge_id)}
        companion_sources = companion_sources or {}
        allowed = conflict_companions.candidates(root, session_paths) if companion_sources else {}
        locked_staged: list[tuple[str, Path, str, str]] = []
        for path, _prelock_target, content, original in staged:
            if path not in session_paths and path not in allowed.get(companion_sources.get(path), []):
                raise GitServiceError(
                    409, "conflict_session_changed",
                    f"'{path}' is no longer registered in merge session {merge_id}",
                )
            target = resolve_in_root(root, path)
            if target is None:
                raise GitServiceError(422, "invalid_request", f"unsafe path: '{path}'")
            try:
                current = target.read_text(encoding="utf-8", errors="replace")
            except OSError:
                current = ""
            if current != original:
                raise GitServiceError(
                    409, "conflict_source_changed",
                    f"'{path}' changed after conflict validation; reload the session",
                )
            locked_staged.append((path, target, content, original))

        for path, target, content, _original in locked_staged:
            _write_resolved_file(root, path, target, content)
            if _gs.db_git.session_kind(session) == _gs.db_git.SESSION_KIND_MERGE:
                from . import rerere_checkpoint
                rerere_checkpoint.record_resolution(merge_id, root, path)
            proc = _gs._run_git(["add", "--", path], cwd=root)
            if proc.returncode != 0:
                raise GitServiceError(
                    500, "git_error", "Git command failed",
                    diagnostic=_gs._last_line(proc.stderr),
                )
            if path in session_paths:
                _gs.db_git.mark_file_resolved(merge_id, path)

        if companion_sources:
            context = _gs.db_git.session_context(session)
            previous_companions = context.get("conflict_companions") or []
            context["conflict_companions"] = [r for r in previous_companions if r["path"] not in companion_sources] + [
                {"path": p, "source_path": source} for p, source in companion_sources.items()
            ]
            _gs.db_git.set_session_context(merge_id, context)
            session = _gs.db_git.get_session(merge_id)

        if locked_staged:
            context = _gs.db_git.session_context(session)
            previous = context.get("conflict_supersedes") or []
            if supersedes or any(
                row.get("path") in evaluated_paths for row in previous
            ):
                context["conflict_supersedes"] = [
                    row for row in previous
                    if row.get("path") not in evaluated_paths
                ] + supersedes
                _gs.db_git.set_session_context(merge_id, context)
                session = _gs.db_git.get_session(merge_id)

        if locked_staged and is_branch_merge:
            from . import branch_merge as _branch_merge
            _branch_merge.note_resolution_submitted(session, resolver_run_id)
            session = _gs.db_git.get_session(merge_id)

        if locked_staged and _gs.db_git.session_kind(session) in _MERGE_REVIEW_KINDS:
            context = _gs.db_git.session_context(session)
            touched = {p for p, *_ in locked_staged}
            origins = [
                o for o in (context.get("conflict_origins") or [])
                if o.get("path") not in touched
            ]
            for path, _target, content, original in locked_staged:
                origins.extend(_classify_conflict_chunks(path, original, content))
            context["conflict_origins"] = origins
            _gs.db_git.set_session_context(merge_id, context)
            session = _gs.db_git.get_session(merge_id)

        remaining = _gs.db_git.remaining_conflicts(merge_id)
        if not complete or remaining:
            result = {
                "ok": True,
                "result": {
                    "status": "conflict",
                    "merge_commit": None,
                    "pushed": False,
                    "remaining_conflicts": remaining,
                },
            }
        elif _gs.db_git.session_kind(session) == _gs.db_git.SESSION_KIND_GROUP_UPDATE:
            update_context = _gs.db_git.session_context(session)
            proc = _gs._run_git(
                [*_gs._GIT_IDENT, "commit", "-m", "Merge updated base into group"],
                cwd=root,
                author_env=_author_env_from_cfg(cfg),
            )
            if proc.returncode != 0:
                raise GitServiceError(
                    500, "git_error", "Git command failed",
                    diagnostic=_gs._last_line(proc.stderr),
                )
            head = _gs._run_git(["rev-parse", "--short", "HEAD"], cwd=root)
            merge_commit = (head.stdout or "").strip() or None
            recorded = _record_group_update_resolution(
                group_id, merge_id, root, update_context, cfg,
            )
            if not update_context.get("reconstruct_head"):
                _gs.db_git.close_session(merge_id, "done")
            result = {
                "ok": True,
                "result": {
                    "status": "updated",
                    "merge_commit": merge_commit,
                    "pushed": False,
                    "remaining_conflicts": [],
                    "source_ref": recorded.get("source_ref"),
                    "source_sha": recorded.get("source_sha"),
                },
            }
        elif _gs.db_git.session_kind(session) in _gs.db_git.TR_SESSION_KINDS:
            # freeze the candidate under the same mutex as the writes, so the
            # common review screen shows exactly what the commit press will record.
            context = _gs.db_git.session_context(session)
            context.update(_freeze_tr_candidate(root, context))
            context["review_state"] = TR_CONFLICT_REVIEW_RESOLVED
            provider_id, provider_name = _resolver_run_provider(resolver_run_id)
            context["resolver_provider"] = provider_name or provider_id
            context["last_error"] = None
            _gs.db_git.set_session_context(merge_id, context)
            result = {
                "ok": True,
                "result": {
                    "status": "resolved_pending_review",
                    "merge_commit": None,
                    "pushed": False,
                    "remaining_conflicts": [],
                    "review_fingerprint": context["review_fingerprint"],
                },
            }
        else:
            # Finalize merge and ordinary branch-merge conflicts freeze their complete
            # candidate while the SAME mutex that protected resolution writes is held.
            base_branch = merge_target.resolve_session_target(session).target_branch
            snapshot = _gs._freeze_commit_candidate(root, base_branch)
            context = _gs.db_git.session_context(session)
            context.update(snapshot)
            context["review_state"] = _gs.REVIEW_STATE_PENDING
            context["instruction_generation"] = int(
                context.get("instruction_generation") or 0
            )
            context["resolver_run_id"] = resolver_run_id
            provider_id, provider_name = _resolver_run_provider(resolver_run_id)
            context["resolver_provider"] = provider_name or provider_id
            context.setdefault("conversation", [])
            _gs.db_git.set_session_context(merge_id, context)
            automatic = (
                bool(context.get("auto_authority"))
                and not context.get("conflict_supersedes")
                and not is_branch_merge
            )

            if is_branch_merge:
                from . import branch_merge as _branch_merge
                _branch_merge.note_review_pending(session)

            result = {
                "ok": True,
                "result": {
                    "status": "resolved_pending_review",
                    "merge_commit": None,
                    "pushed": False,
                    "remaining_conflicts": [],
                    "review_fingerprint": snapshot["review_fingerprint"],
                },
            }
    finally:
        _bmp.session_unlock(lock_ctx, held)

    # approve_merge_review takes its own locks (the same target domain plus R). Taking R
    # while this hold is still open would be legal now, but the review keeps its own
    # boundary as before.
    if automatic:
        assert snapshot is not None
        return _gs.approve_merge_review(
            group_id,
            merge_id,
            attempt_id=str(uuid.uuid4()),
            review_fingerprint=snapshot["review_fingerprint"],
            authority="automatic",
        )
    assert result is not None
    return result


def _record_group_update_resolution(
    group_id: Optional[str], merge_id: int, root: Path, context: dict, cfg: dict,
) -> dict:
    """the resolved update advances the scope floor to the
    source SHA the session pinned when it opened.  A session opened before that pin
    existed records nothing (the floor stays at the older, over-inclusive value).  A
    failed record undoes the merge commit and closes the session as aborted, so an
    update is never reported successful with a mismatched floor."""
    from modules.flow_gate.services import git_service as _gs
    from . import scope_base
    source_sha = context.get("source_sha")
    if not group_id or not source_sha:
        return {}
    project_id = _gs._project_of_group(group_id)
    try:
        from modules.flow_gate.db.connection import get_store
        with get_store().transaction():
            recorded = scope_base.record_update(
                project_id, group_id, root, source_ref=context.get("source_ref") or source_sha,
                source_sha=source_sha, floor_before=context.get("floor_before") or "",
                path="conflict", merge_id=merge_id, config=cfg,
            )
            if context.get("reconstruct_head"):
                _gs.db_git.close_session(merge_id, "done")
        return recorded
    except Exception:
        if context.get("reconstruct_head"):
            from .reconstruct import restore
            restore(root, context)
        else:
            _gs._run_git(["reset", "--hard", "HEAD^1"], cwd=root)
        _gs.db_git.close_session(merge_id, "aborted")
        raise


def resolve_conflicts(
    group_id: Optional[str], merge_id: int, files: list[dict], complete: bool,
    *, resolver_run_id: Optional[str] = None, project_id: Optional[str] = None,
) -> dict:
    from modules.flow_gate.services import git_service as _gs
    from modules.flow_gate.storage.safe_path import resolve_in_root

    session, cfg, project_id, root = _gs._session_context(group_id, merge_id, **({"project_id": project_id} if project_id else {}))
    is_branch_merge = _is_branch_merge_session(session)
    _gs.db_git.touch_session(merge_id)   # activity → resets the sweep TTL (0205 L §1)
    # (rejection 3): a review-conversation turn's run must never submit a
    # resolution. Until rev5 it was launched with the ordinary resolver mention -- resolve
    # every conflict, call the bound endpoint -- so it did, and a submission re-freezes the
    # candidate further down. That new fingerprint then fails
    # `_materialize_pending_conversation_run`'s identity check and the run's OWN answer is
    # discarded as `stale_run`. Both thrown-away answers in the rejected transcript were
    # self-inflicted exactly this way: the human asked a question, the run answered it AND
    # submitted, and its submission deleted the answer. The mention no longer asks for one;
    # this refuses it even if a model tries anyway, and it refuses BEFORE any file is
    # written, so the reviewer's frozen candidate never moves mid-question.
    if resolver_run_id and (
        _gs.db_git.session_context(session).get("pending_conversation_run_id") == resolver_run_id
    ):
        raise GitServiceError(
            409, "review_conversation_cannot_resolve",
            "this run is a review conversation turn: answer in your final message, "
            "do not submit a resolution",
        )
    session_paths = {row["path"] for row in _gs.db_git.session_files(merge_id)}

    # Validate EVERYTHING before writing anything (E12 — all-or-nothing).
    # side-drop and supersede failures are collected across ALL files and
    # reported in one 422, instead of stopping at the first failing file.
    staged: list[tuple[str, Path, str, str]] = []
    side_dropped: list[dict] = []
    supersede_invalid: list[dict] = []
    supersedes: list[dict] = []
    evaluated_paths: set[str] = set()
    companion_sources: dict[str, str] = {}
    verified_companions: dict[str, list[dict]] = {}
    if any(not isinstance(f, dict) or not isinstance(f.get("path"), str) for f in files or []):
        raise GitServiceError(422, "invalid_request", "each file needs a string path")
    allowed = conflict_companions.candidates(root, session_paths) if any(f.get("companion_files") for f in files or []) else {}
    submitted_paths = [f.get("path") for f in files or []]
    if len(set(submitted_paths)) != len(submitted_paths):
        raise GitServiceError(422, "invalid_request", "duplicate resolution path")
    for f in files or []:
        companions = f.get("companion_files") or []
        if not isinstance(companions, list):
            raise GitServiceError(422, "invalid_request", "companion_files must be a list")
        for companion in companions:
            if not isinstance(companion, dict):
                raise GitServiceError(422, "invalid_request", "invalid companion file")
            path, content = companion.get("path"), companion.get("content")
            if not isinstance(path, str) or not isinstance(content, str) or path not in allowed.get(f.get("path"), []):
                raise GitServiceError(422, "invalid_request", "companion is not a verified target-side move")
            if path in companion_sources or path in submitted_paths:
                raise GitServiceError(422, "invalid_request", "duplicate companion path")
            target = resolve_in_root(root, path)
            if target is None:
                raise GitServiceError(422, "invalid_request", "unsafe companion path")
            if _gs.has_conflict_markers(content):
                raise GitServiceError(422, "conflict_markers_remain", f"Conflict markers remain in '{path}'")
            original = target.read_text(encoding="utf-8", errors="replace")
            companion_sources[path] = f["path"]
            verified_companions.setdefault(f["path"], []).append(companion)
            staged.append((path, target, content, original))
    for f in files or []:
        path = f.get("path")
        content = f.get("content")
        chunks = f.get("chunks")
        if not isinstance(path, str) or (chunks is None and not isinstance(content, str)):
            raise GitServiceError(422, "invalid_request", "each file needs path and content (or chunks)")
        if chunks is not None and content is not None:
            raise GitServiceError(
                422, "invalid_request", f"'{path}': send either content or chunks, not both",
            )
        if path not in session_paths:
            raise GitServiceError(
                422, "invalid_request", f"'{path}' is not part of merge session {merge_id}"
            )
        if chunks is not None:
            # per-chunk resolutions become the whole-file submission right
            # here, so everything below validates them exactly like `content`.
            try:
                current = (root / path).read_text(encoding="utf-8", errors="replace")
            except OSError:
                current = ""
            content = _content_from_chunk_resolutions(path, current, chunks)
        if _gs.has_conflict_markers(content):
            line_no = next(
                (i for i, l in enumerate(content.splitlines(), start=1)
                 if _gs._CONFLICT_OPEN_RE.match(l) or _gs._CONFLICT_CLOSE_RE.match(l)),
                1,
            )
            raise GitServiceError(
                422, "conflict_markers_remain",
                f"Conflict markers remain in '{path}' (line {line_no})",
            )
        else:
            # Markers are gone — but "gone" also happens when an entire side of a real
            # base-having chunk got dropped instead of merged. Compare against the
            # pre-write working-tree original; if THAT never had markers either (e.g. a
            # retry after an earlier file in this same request already failed), this file
            # is out of scope for the check and passes silently.
            try:
                original = (root / path).read_text(encoding="utf-8", errors="replace")
            except OSError:
                original = ""
            violations = []
            if _gs.has_conflict_markers(original):
                evaluated_paths.add(path)
                violations = _conflict_side_violations(original, content)
            # an explicit, evidence-checked declaration is the only
            # thing that can excuse a violating chunk; it never touches a clean one.
            supersede = f.get("supersede")
            if supersede is not None:
                problems, records = _supersede_findings(path, supersede, violations, verified_companions.get(path))
                if problems:
                    supersede_invalid.extend(problems)
                else:
                    supersedes.append({
                        "path": path, "side": supersede["side"],
                        "reason": supersede["reason"].strip(), "chunks": records,
                    })
            elif violations:
                side_dropped.append({
                    "path": path,
                    "chunks": [
                        {key: v[key] for key in ("chunk", "start_line", "end_line", "side", "dropped_side")}
                        for v in violations
                    ],
                })
        target = resolve_in_root(root, path)
        if target is None:
            raise GitServiceError(422, "invalid_request", f"unsafe path: '{path}'")
        staged.append((path, target, content, original))

    if side_dropped:
        details = {"files": side_dropped, "hint": _SUPERSEDE_HINT}
        if supersede_invalid:
            details["supersede_invalid"] = supersede_invalid
        names = ", ".join(f"'{row['path']}'" for row in side_dropped)
        raise GitServiceError(
            422, "conflict_side_dropped",
            f"{names} dropped one whole side of a resolved conflict chunk. {_SUPERSEDE_HINT}",
            details,
        )
    if supersede_invalid:
        names = ", ".join(sorted({f"'{row['path']}'" for row in supersede_invalid}))
        raise GitServiceError(
            422, "conflict_supersede_invalid",
            f"supersede declaration rejected for {names}; see details.files for the failed "
            "condition of each chunk",
            {"files": supersede_invalid},
        )

    return _apply_conflict_resolution_locked(
        group_id,
        merge_id,
        staged,
        complete,
        supersedes,
        evaluated_paths,
        resolver_run_id,
        expected_project_id=project_id,
        companion_sources=companion_sources,
    )


def _resolver_run_provider(run_id: Optional[str]) -> tuple[Optional[str], Optional[str]]:
    """``(provider_id, provider_name)`` of the AI run that produced this
    resolution, or ``(None, None)`` for a human-typed resolution or an unknown
    run — best-effort, a lookup failure must never break the resolve response."""
    if not run_id:
        return None, None
    try:
        from modules.flow_gate.services.ai_invoke import diagnostics as ai_diagnostics

        payload = ai_diagnostics.get_run_detail(run_id)
        return payload.get("provider_id"), payload.get("provider_name")
    except Exception:
        return None, None


# ── the earlier change the earlier implementation: abort unblocking ─────────────────────────────────────

# The resolve refusal for a session whose workspace is no longer merging.
MERGE_NOT_IN_PROGRESS = "merge_not_in_progress"
# Session kinds whose conflict lives in an in-flight `git merge` (MERGE_HEAD on disk).
_MERGE_HEAD_SESSION_KINDS = ("merge", "branch_merge", "group_update")
FORCE_CLEANUP_CODE = "admin_force_cleanup"


def _merge_head_observed(root: Path) -> Optional[bool]:
    """MERGE_HEAD present (True), absent (False) or not observable (None) in ``root`` —
    the sweep's tri-state reader, so a failed read is never taken for "absent"."""
    from .cleanup import _observe_merge_head
    return _observe_merge_head(root)


def _abort_failed_error(
    proc, merge_id: Optional[int], *, recovery: Optional[str] = "force_cleanup",
) -> GitServiceError:
    """The abort failure as the client sees it: code stays ``git_error`` (existing
    screens), git's own one-line reason rides in ``error.details.reason`` and
    ``error.params.reason``; ``details.recovery = "force_cleanup"`` names the way out
    (``None`` for a session the admin force cleanup does not cover, e.g. a Graph
    attempt merge)."""
    from modules.flow_gate.services import git_service as _gs
    reason = getattr(proc, "reason", None) or _gs._git_reason_line(getattr(proc, "stderr", ""))
    return GitServiceError(
        500, "git_error", "Merge abort failed",
        details={
            "reason": reason,
            "returncode": getattr(proc, "returncode", None),
            "merge_id": merge_id,
            "recovery": recovery,
        },
        params={"reason": reason},
        diagnostic=reason,
    )


def _raise_if_merge_gone(session: dict, root: Path, merge_id: int) -> None:
    """Refuse a resolution for a merge-kind session whose workspace observably has no
    MERGE_HEAD. 409 ``merge_not_in_progress`` with a one-line reason and
    ``next_action="abort"`` — the abort now closes such a session as already cleaned."""
    from modules.flow_gate.services import git_service as _gs
    if _gs.db_git.session_kind(session) not in _MERGE_HEAD_SESSION_KINDS:
        return
    if _merge_head_observed(root) is not False:
        return
    reason = "MERGE_HEAD missing: the workspace is no longer merging"
    raise GitServiceError(
        409, MERGE_NOT_IN_PROGRESS,
        "the merge is no longer in progress; close the session with abort",
        details={"reason": reason, "merge_id": merge_id, "next_action": "abort",
                 "recovery": "force_cleanup"},
        params={"reason": reason},
        diagnostic=reason,
    )


def _changed_paths(root: Path) -> list[str]:
    """Tracked paths a reset would discard (staged or unstaged), read-only."""
    from modules.flow_gate.services import git_service as _gs
    proc = _gs._run_git(["status", "--porcelain", "--untracked-files=no", "-z"], cwd=root)
    if proc.returncode != 0:
        return []
    paths: list[str] = []
    entries = (proc.stdout or "").split("\0")
    i = 0
    while i < len(entries):
        entry = entries[i]
        i += 1
        if len(entry) < 4:
            continue
        paths.append(entry[3:])
        if entry[0] in ("R", "C"):
            i += 1  # the rename/copy source follows as its own NUL field
    return sorted(set(paths))


def _force_cleanup_scope(group_id: str, merge_id: int) -> tuple[dict, Path, object]:
    """``(session, root, target)`` of a session the administrator force cleanup may touch,
    or a 4xx. Only an open finalize ``merge`` session whose target is its OWN managed
    target workspace (the per-target reflection checkout) qualifies: the shared base
    checkout may hold other groups' work (the 0203 accident) and a group worktree IS the
    group branch, so neither is ever reset here."""
    from modules.flow_gate.services import git_service as _gs
    from . import recovery
    session = _gs._review_session_of_owner(group_id, merge_id, None)
    if session.get("status") != "open":
        raise GitServiceError(409, "invalid_state", f"merge session {merge_id} is not open")
    if _gs.db_git.session_kind(session) != _gs.db_git.SESSION_KIND_MERGE or _is_branch_merge_session(session):
        raise GitServiceError(409, "force_cleanup_unsupported_session",
                              "force cleanup applies to a finalize merge session only")
    context = _gs.db_git.session_context(session)
    if recovery.supported(session) and (recovery.needed(context) or recovery.has_preservation(context)):
        raise GitServiceError(409, "recovery_required", "use the versioned preservation abort",
                              details={"recovery": recovery.projection(session)})
    target = merge_target.resolve_session_target(session)
    if (target.is_project_base or not target.managed_workspace
            or target.target_kind != "branch" or target.root is None):
        raise GitServiceError(
            409, "force_cleanup_unsupported_target",
            "force cleanup only resets a managed target workspace, never the shared base checkout",
            details={"target_branch": target.target_branch, "is_project_base": bool(target.is_project_base)},
        )
    merge_target.raise_if_not_workspace_owner(target)
    return session, target.root, target


def _force_cleanup_plan(session: dict, root: Path, target) -> dict:
    """What a force cleanup would do, read-only — the confirmation screen's content."""
    from modules.flow_gate.services import git_service as _gs
    context = _gs.db_git.session_context(session)
    exists = root.exists()
    head = _gs._rev_parse(root, "HEAD") if exists else None
    branch_proc = _gs._run_git(["symbolic-ref", "-q", "--short", "HEAD"], cwd=root) if exists else None
    branch = (branch_proc.stdout or "").strip() if branch_proc is not None and branch_proc.returncode == 0 else None
    baseline_head = (context.get("resolver_baseline") or {}).get("base_head")
    unmerged = _gs._unmerged_paths(root) if exists else []
    return {
        "merge_id": int(session["merge_id"]),
        "group_id": session.get("group_id"),
        "workspace": str(root),
        "workspace_exists": exists,
        "target_branch": target.target_branch,
        "checked_out_branch": branch,
        "head": head,
        "baseline_head": baseline_head,
        "head_matches_baseline": (not baseline_head) or baseline_head == head,
        "merge_head": _gs._rev_parse(root, "MERGE_HEAD") if exists else None,
        "unmerged_paths": sorted(unmerged),
        "discarded_paths": _changed_paths(root) if exists else [],
        "abort_failure": context.get("abort_failure"),
        "group_branch_untouched": True,
    }


def force_cleanup_preview(group_id: str, merge_id: int) -> dict:
    """``GET …/force-cleanup``: what the administrator force cleanup would reset."""
    session, root, target = _force_cleanup_scope(group_id, merge_id)
    return {"ok": True, "result": _force_cleanup_plan(session, root, target)}


def force_cleanup_merge(group_id: str, merge_id: int, *, actor: Optional[str] = None) -> dict:
    """Administrator-only way out of a merge session whose ``merge --abort`` fails
    (B section 1 3, the earlier implementation task 5). Under the same session lock as [abort]:

    1. the ordinary abort (index refresh + ``merge --abort``) is tried first;
    2. only if it fails: the session's own managed target workspace is reset to the
       target branch HEAD (``reset --hard <HEAD>``, ``merge --quit`` if MERGE_HEAD is
       still there) and verified (HEAD unchanged, no MERGE_HEAD, no unmerged path);
    3. the session closes ``aborted`` exactly like an [abort]: parked approval intent
       discarded, checkpoint invalidated, workspace released, group back to ``waiting``.

    Never touches the group branch, the group's result commits, the approval record or
    the pinned commit. Refuses when HEAD moved from the recorded merge baseline or the
    workspace has another branch checked out (nothing is guessed)."""
    from modules.flow_gate.services import git_service as _gs
    from . import branch_merge_publish as bmp
    row = _gs._review_session_of_owner(group_id, merge_id, None)
    lock, held = bmp.session_lock(row, _gs._session_project(row), holder_kind="review_action", publish=True)
    try:
        session, root, target = _force_cleanup_scope(group_id, merge_id)
        plan = _force_cleanup_plan(session, root, target)
        if plan["workspace_exists"]:
            if not plan["head_matches_baseline"]:
                raise GitServiceError(
                    409, "force_cleanup_head_moved",
                    "the target workspace HEAD is not the merge's recorded base; left as it is",
                    details={"head": plan["head"], "baseline_head": plan["baseline_head"]},
                )
            if plan["checked_out_branch"] and plan["checked_out_branch"] != target.target_branch:
                raise GitServiceError(
                    409, "force_cleanup_branch_mismatch",
                    "the target workspace has another branch checked out; left as it is",
                    details={"checked_out_branch": plan["checked_out_branch"],
                             "target_branch": target.target_branch},
                )
        last_failure = plan.get("abort_failure")
        abort_reason = last_failure.get("reason") if isinstance(last_failure, dict) else None
        forced = False
        if plan["workspace_exists"] and (plan["merge_head"] or plan["unmerged_paths"]):
            proc = _gs._abort_merge(root, merge_id=merge_id, reason="admin_force_cleanup_abort")
            if proc.returncode:
                abort_reason = proc.reason
                head = plan["head"]
                reset = _gs._run_session_git(merge_id, None, "reset", FORCE_CLEANUP_CODE,
                                             ["reset", "--hard", head], cwd=root)
                if reset.returncode == 0 and _gs._rev_parse(root, "MERGE_HEAD") is not None:
                    _gs._run_session_git(merge_id, None, "merge", "admin_force_cleanup_quit",
                                         ["merge", "--quit"], cwd=root)
                if (reset.returncode != 0 or _gs._rev_parse(root, "MERGE_HEAD") is not None
                        or _gs._unmerged_paths(root) or _gs._rev_parse(root, "HEAD") != head):
                    failure = reset if reset.returncode else proc
                    _gs._record_abort_failure(merge_id, FORCE_CLEANUP_CODE, failure)
                    reason = _gs._git_reason_line(getattr(failure, "stderr", ""))
                    raise GitServiceError(
                        500, "force_cleanup_failed", "the target workspace could not be reset",
                        details={"reason": reason, "workspace": str(root), "merge_id": merge_id},
                        params={"reason": reason}, diagnostic=reason,
                    )
                forced = True
        _log.warning(
            "merge %s force cleanup by %s: workspace %s reset=%s head=%s abort_reason=%s discarded=%s",
            merge_id, actor, root, forced, plan["head"], abort_reason, plan["discarded_paths"],
        )
        _gs.db_git.invalidate_resolution_checkpoint_for_merge(merge_id)
        discarded = approval_intent.discard_intent(merge_id)
        closed = merge_target.close_session_attempt(
            session, merge_target.ATTEMPT_ABORTED,
            error={"code": FORCE_CLEANUP_CODE, "actor": actor, "forced": forced,
                   "abort_reason": abort_reason},
        )
        if not closed:
            raise GitServiceError(409, "merge_target_not_owner",
                                  "the target workspace is not this merge's; session left open")
        _gs._set_status(group_id, "waiting")
    finally:
        bmp.session_unlock(lock, held)
    return {"ok": True, "result": {
        "status": "aborted",
        "group_status": "waiting",
        "forced": forced,
        "already_cleaned": not forced and not (plan["merge_head"] or plan["unmerged_paths"]),
        "workspace": plan["workspace"],
        "target_branch": plan["target_branch"],
        "reset_head": plan["head"],
        "discarded_paths": sorted(set(plan["discarded_paths"]) | set(plan["unmerged_paths"])),
        "abort_reason": abort_reason,
        "workspace_released": not root.exists(),
        "branch_preserved": True,
        "final_approval_intent_discarded": discarded is not None,
    }}


def list_abort_failures(project_id: str) -> dict:
    """Open sessions of ``project_id`` marked "정리 실패" (``context.abort_failure``) — the
    administrator's view of what the sweep or a person could not abort."""
    from modules.flow_gate.services import git_service as _gs
    items = []
    for session in _gs.db_git.list_open_sessions():
        try:
            if _gs._session_project(session) != project_id:
                continue
        except Exception:
            continue
        context = _gs.db_git.session_context(session)
        failure = context.get("abort_failure")
        if not isinstance(failure, dict):
            continue
        target_branch = None
        try:
            if _gs.db_git.session_kind(session) in _MERGE_REVIEW_KINDS:
                target_branch = merge_target.resolve_session_target(session).target_branch
        except Exception:
            target_branch = None
        items.append({
            "merge_id": int(session["merge_id"]),
            "group_id": session.get("group_id"),
            "kind": _gs.db_git.session_kind(session),
            "target_branch": target_branch,
            "abort_failure": failure,
            "created_at": session.get("created_at"),
            "touched_at": session.get("touched_at"),
        })
    items.sort(key=lambda item: item["merge_id"])
    return {"ok": True, "result": {"project_id": project_id, "sessions": items}}


def abort_merge(group_id: Optional[str], merge_id: int, *, project_id: Optional[str] = None) -> dict:
    from modules.flow_gate.services import git_service as _gs
    from . import branch_merge_publish as bmp
    row = _gs._review_session_of_owner(group_id, merge_id, project_id)
    if _gs.db_git.session_kind(row) != _gs.db_git.SESSION_KIND_MERGE or _is_branch_merge_session(row):
        return _abort_merge_locked(group_id, merge_id, project_id=project_id)
    lock, held = bmp.session_lock(row, _gs._session_project(row), holder_kind="review_action", publish=True)
    try:
        # The implementation re-reads owner, phase and recovery evidence under this lock.
        return _abort_merge_locked(group_id, merge_id, project_id=project_id)
    finally:
        bmp.session_unlock(lock, held)


def _abort_merge_locked(group_id: Optional[str], merge_id: int, *, project_id: Optional[str] = None) -> dict:
    """Manual [abort] — discard this merge attempt and preserve the work branch
    (0205 P scenario 9). Shares its end state with the auto-recovery sweep; only
    the trigger differs. The merge:{id} release is now best-effort legacy cleanup
    (0205 §2.1 stopped holding that lock). _set_status already broadcasts
    git_pending_changed so the badge clears immediately (0184 lesson).

    088: a TR conflict session arrives here from the same button on the same panel row,
    and `merge --abort` has nothing to abort in a group worktree — it is delegated whole
    to :func:`abort_tr_conflict` rather than given a second endpoint to learn."""
    from modules.flow_gate.services import git_service as _gs
    from . import recovery
    row = _gs._review_session_of_owner(group_id, merge_id, project_id)
    if recovery.supported(row) and (recovery.needed(_gs.db_git.session_context(row))
                                    or recovery.has_preservation(_gs.db_git.session_context(row))):
        raise GitServiceError(409, "recovery_required", "use the versioned preservation abort",
                              details={"recovery": recovery.projection(row)})
    session, _cfg, project_id, root = _gs._session_context(group_id, merge_id, **({"project_id": project_id} if project_id else {}))
    kind = _gs.db_git.session_kind(session)
    if _is_branch_merge_session(session):
        # no group status to return to — the attempt itself records
        # `aborted`, its tokens/run are stopped and only its own workspace is released.
        from . import branch_merge as _branch_merge
        return _branch_merge.abort(project_id, merge_id)
    if kind in _gs.db_git.TR_SESSION_KINDS:
        return abort_tr_conflict(group_id, merge_id)
    if kind == _gs.db_git.SESSION_KIND_MERGE:
        # ownership is proven BEFORE git touches the target
        # root. A workspace whose marker names another owner keeps its MERGE_HEAD,
        # index and conflict files, and this session stays open (fail-closed 409).
        merge_target.raise_if_not_workspace_owner(merge_target.resolve_session_target(session))
    already_cleaned = False
    if root.exists():
        # a workspace that is observably no
        # longer merging — MERGE_HEAD absent and no unmerged path — has nothing to abort.
        # It is "already cleaned": the session closes exactly like a successful abort.
        # An unobservable MERGE_HEAD (a failed read) is never taken as absent.
        merging = _merge_head_observed(root)
        if merging is False:
            already_cleaned = True
            if _gs._unmerged_paths(root):
                # MERGE_HEAD is gone but conflict stages remain in the index: undo them
                # the way `merge --abort` itself does (`reset --merge`, which keeps any
                # unrelated local change and refuses rather than lose one).
                _gs._run_git(["update-index", "-q", "--refresh"], cwd=root)
                proc = _gs._run_session_git(merge_id, None, "merge", "user_abort_reset_merge",
                                            ["reset", "--merge"], cwd=root)
                if proc.returncode or _gs._unmerged_paths(root):
                    proc.reason = _gs._git_reason_line(proc.stderr)
                    _gs._record_abort_failure(merge_id, "user_abort", proc)
                    raise _abort_failed_error(proc, merge_id)
            _log.info("merge %s: MERGE_HEAD already gone in %s — closing as already cleaned",
                      merge_id, root)
        else:
            proc = _gs._abort_merge(root, merge_id=merge_id, reason="user_abort")
            if proc.returncode:
                _gs._record_abort_failure(merge_id, "user_abort", proc)
                raise _abort_failed_error(proc, merge_id)
    if kind == _gs.db_git.SESSION_KIND_GROUP_UPDATE:
        from .reconstruct import restore
        restore(root, _gs.db_git.session_context(session))
        _gs.db_git.close_session(merge_id, "aborted")
        return {"ok": True, "result": {
            "status": "aborted", "branch_preserved": True,
            "already_cleaned": already_cleaned,
        }}
    # abort ends the Git attempt this final approval
    # was riding on, so its parked intent is discarded — never carried over to a
    # later session. The AC stays pending_review and the root stays in progress, so
    # pressing 최종승인 again simply starts over with a fresh intent id. Note this is
    # the ONLY discard path besides the §3.3 validity mismatch: a review rejection or
    # a re-review does NOT get here and must leave the intent alone.
    if kind == _gs.db_git.SESSION_KIND_MERGE:
        _gs.db_git.invalidate_resolution_checkpoint_for_merge(merge_id)
    discarded = approval_intent.discard_intent(merge_id)
    # an attempt records `aborted` and releases only its own
    # workspace (owner marker match); a legacy row is closed exactly as before.
    merge_target.close_session_attempt(
        session, merge_target.ATTEMPT_ABORTED, error={"code": "user_abort"},
    )
    _gs._set_status(group_id, "waiting")
    return {"ok": True, "result": {
        "status": "waiting", "branch_preserved": True,
        "final_approval_intent_discarded": discarded is not None,
        "already_cleaned": already_cleaned,
    }}
