"""Conflict-session lifecycle and merge-conflict resolution.

Extracted from git_service.py (flowgate.default.0550 T0015, D0006 §3.2/부록 A).
"""
from __future__ import annotations

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

from . import approval_intent
from .command import GIT_LOCAL_TIMEOUT_SEC
from .commit import _release_cancel_lock
from .credentials import GitServiceError, _author_env_from_cfg

_log = logging.getLogger(__name__)

TR_CONFLICT_REVIEW_OPEN = "open"
TR_CONFLICT_REVIEW_RESOLVED = "resolved"

_CONFLICT_SEP_RE = re.compile(r"^={7}$")
_CONFLICT_BASE_RE = re.compile(r"^\|{7}( |$)")
_CONFLICT_CHUNK_GROUP_MAX_COMMON_LINES = 3
_SUPERSEDE_SIDES = ("ours", "theirs")
# A dropped line counts as "changed in place" only when the kept side's replacement
# run holds an edited version of it (difflib ratio). This pairs lines; it never admits
# a chunk by an overall inclusion rate (D0005 §3.4 / T0008 §10).
_SUPERSEDE_CHANGED_LINE_MIN_RATIO = 0.6
_SUPERSEDE_HINT = (
    "Use a per-file `supersede` declaration ({\"side\": \"ours|theirs\", \"reason\": \"...\"}) "
    "ONLY when the side you kept already contains the other side's changes; otherwise merge "
    "both sides' changes into the chunk."
)


# ── 0608 T0005: line endings of conflict files ────────────────────────────────────
#
# 0599 merge 98 wrote its six resolved files through `Path.write_text`, which on the
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
    target.write_bytes(_encode_eol(content, _path_eol(root, path)))


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
            p.write_bytes(stage.replace(b"\r\n", b"\n"))
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
    """Take line-ending noise out of an in-flight merge's conflicts (0608 T0005).

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
            target.write_bytes(merged)
            proc = _gs._run_git(["add", "--", path], cwd=root)
            if proc.returncode != 0:
                _log.warning("eol-only conflict %s could not be staged: %s",
                             path, _gs._last_line(proc.stderr))
                target.write_bytes(before)
                continue
            eol_only.append(path)
        elif merged != before:
            target.write_bytes(merged)
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
) -> Optional[dict]:
    """Keep a conflicted revert alive as a conflict session instead of destroying it.

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


def commit_tr_conflict(group_id: str, merge_id: int) -> dict:
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

    holder = f"trconflict:{uuid.uuid4()}"
    if not _gs._acquire_lock(project_id, holder, wait_sec=_gs.CANCEL_LOCK_WAIT_SEC):
        raise GitServiceError(
            409, "git_busy", f"another git operation is in progress for '{project_id}'"
        )
    try:
        # A resolution that keeps the tree exactly as HEAD has it — "take ours" on every
        # chunk, which is a perfectly reasonable answer and one an AI will sometimes give.
        # There is nothing to commit then, and an empty commit is noise in the history
        # (D0005 K3). It is not a failure either: the outcome the person asked for is
        # already true of the tree, and the ledger records it the same way the cancel loop
        # records its own empty reverts.
        staged = _gs._run_git(
            ["diff", "--cached", "--quiet"], cwd=root, timeout=_gs.GIT_READ_TIMEOUT_SEC,
        )
        empty = staged.returncode == 0
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
    finally:
        _release_cancel_lock(project_id, holder)

    _gs.db_git.close_session(merge_id, "done")
    _gs._set_status(group_id, context.get("prev_status") or "waiting")
    return {
        "ok": True,
        "result": {
            "status": "empty" if empty else "committed", "kind": kind, "commit": commit,
            "doc_id": context.get("doc_id"), "doc_code": context.get("doc_code"),
            "ledger_row_id": context.get("ledger_row_id"),
            "target_sha": context.get("target_sha"),
            "original_sha": context.get("original_sha"),
            "commit_subject": context.get("subject"),
        },
    }


def abort_tr_conflict(group_id: str, merge_id: int) -> dict:
    """Give up on a TR conflict: restore the worktree, close the session, keep the row.

    The body is the old :func:`restore_after_failed_revert` — ``revert --quit`` then
    ``reset --hard HEAD``, and still never ``git clean`` (0382). The ledger row is left
    exactly as it was, because nothing about it changed: the commit it names is still live
    (a cancel that gave up) or still cancelled (a reapply that gave up).
    """
    from modules.flow_gate.services import git_service as _gs
    session, _cfg, _project_id, root = _gs._session_context(group_id, merge_id)
    kind = _gs.db_git.session_kind(session)
    if kind not in _gs.db_git.TR_SESSION_KINDS:
        raise GitServiceError(409, "invalid_state", "not a TR conflict session")
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
    must sit (0602 T0004 rev2 review finding — see :func:`_anchor_chunk_selections`).
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
    """Lines in ``side`` that are not in ``base`` (trimmed comparison — E12 note in T0012)."""
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
    T0004 rev2 review finding: an unbounded, cursor-forward search over the WHOLE
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
    """0608 T0007: the whole-file submission a per-chunk resolution stands for.

    An API model's single reply is capped (NR0003 §8): 0594's finalize.py is 111,807
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

    The boolean view of :func:`_conflict_side_violations` (0604 T0008 split it out so
    the resolve path can report every violating chunk and check `supersede` against
    each one); the verdict itself is unchanged.
    """
    return bool(_conflict_side_violations(original, submitted))


def _supersede_evidence(kept: list[str], dropped: list[str], base: list[str]) -> dict:
    """0604 D0005 §3.4 condition 3·4 — does ``kept`` really carry ``dropped``'s changes?

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


def _supersede_findings(path: str, supersede, violations: list[dict]) -> tuple[list[dict], list[dict]]:
    """Check one file's ``supersede`` declaration against its violating chunks.

    Returns ``(problems, records)``: ``problems`` is empty only when the declaration is
    valid for EVERY violating chunk; ``records`` is then the per-chunk evidence to keep.
    A declaration is never a bypass: it can only excuse chunks the checker already
    rejects, only the side actually kept, and only with in-place evidence.
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
            })
    return problems, records

def _classify_conflict_chunks(path: str, original: str, submitted: str) -> list[dict]:
    """D0006 §3.3 / L0007 §2.4 — per-chunk selection the review screen overlays on
    the real diff: which conflict chunk resolved to ``ours``/``theirs``/``both``/
    ``manual``, and (best-effort) where that ended up in the submitted text.

    Shares its chunk-local, context-anchored match with :func:`_conflict_side_dropped`
    via :func:`_anchor_chunk_selections` — the priority order in
    :func:`_select_conflict_chunk` mirrors L0007 §2.4, with the same pragmatic narrowing
    noted there: a left-to-right search within the anchored window rather than L0007's
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


def _session_context(group_id: str, merge_id: int) -> tuple[dict, dict, str, Path]:
    """``(session, cfg, project_id, root)`` — ``root`` is the repo the conflict lives in.

    A finalize merge conflicts in the base checkout; a TR revert or reapply conflicts in the
    group's own worktree (088). That one value is the entire difference for everything
    downstream — the file list, the resolved writes, the abort — which is why the two kinds
    can share a table, a screen, a set of endpoints and an AI run at all.
    """
    from modules.flow_gate.services import git_service as _gs
    session = _gs.db_git.get_session(merge_id)
    if session is None or session.get("group_id") != group_id or session.get("status") != "open":
        raise GitServiceError(404, "not_found", f"merge session {merge_id} not found")
    cfg, _state, project_id, base_root, wt_path = _gs._finalize_context(group_id)
    is_worktree_session = _gs.db_git.session_kind(session) in _gs.db_git.WORKTREE_SESSION_KINDS
    return session, cfg, project_id, (wt_path if is_worktree_session else base_root)


def resolve_conflict_src_root(group_id: str, merge_id: int) -> Path:
    """Return the checked-out root that owns the validated open conflict session."""
    from modules.flow_gate.services import git_service as _gs
    _session, _cfg, _project_id, root = _gs._session_context(group_id, merge_id)
    return root


def list_conflicts(group_id: str, merge_id: int) -> dict:
    from modules.flow_gate.services import git_service as _gs
    session, cfg, _project_id, root = _gs._session_context(group_id, merge_id)
    _gs.db_git.touch_session(merge_id)   # activity → resets the sweep TTL (0205 L §1)
    state = _gs.db_git.get_state(group_id) or {}
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
    return {
        "ok": True,
        "merge_id": merge_id,
        "branch": state.get("branch"),
        "base_branch": (cfg.get("base_branch") or "main"),
        "files": files,
        # 0608 T0005: session files that differed only in line endings and were already
        # merged on normalised text (apply_eol_separation) — nothing to resolve there.
        "eol_only_paths": list(context.get("eol_only_paths") or []),
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


def resolve_conflicts(
    group_id: str, merge_id: int, files: list[dict], complete: bool,
    *, resolver_run_id: Optional[str] = None,
) -> dict:
    from modules.flow_gate.services import git_service as _gs
    from modules.flow_gate.storage.safe_path import resolve_in_root

    session, cfg, project_id, root = _gs._session_context(group_id, merge_id)
    _gs.db_git.touch_session(merge_id)   # activity → resets the sweep TTL (0205 L §1)
    # 0481 T0010 rev6 (rejection 3): a review-conversation turn's run must never submit a
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
    # 0604 T0008: side-drop and supersede failures are collected across ALL files and
    # reported in one 422, instead of stopping at the first failing file.
    staged: list[tuple[str, Path, str]] = []
    side_dropped: list[dict] = []
    supersede_invalid: list[dict] = []
    supersedes: list[dict] = []
    evaluated_paths: set[str] = set()
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
            # 0608 T0007: per-chunk resolutions become the whole-file submission right
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
            # 0604 D0005 §3.4 — an explicit, evidence-checked declaration is the only
            # thing that can excuse a violating chunk; it never touches a clean one.
            supersede = f.get("supersede")
            if supersede is not None:
                problems, records = _supersede_findings(path, supersede, violations)
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

    for path, target, content, _original in staged:
        # 0608 T0005: bytes in the file's own line ending. `write_text` here is what
        # turned 0599's LF files CRLF (and a CRLF file CR-CR-LF) on the Windows server.
        _write_resolved_file(root, path, target, content)
        proc = _gs._run_git(["add", "--", path], cwd=root)
        if proc.returncode != 0:
            raise GitServiceError(500, "git_error", "Git command failed", diagnostic=_gs._last_line(proc.stderr))
        _gs.db_git.mark_file_resolved(merge_id, path)

    if staged:
        # 0604 D0005 §3.4 — the declaration record lives next to conflict_origins in
        # the session context (no schema). A path re-checked against its conflict
        # markers replaces its own record (dropping it when the new submission needs no
        # declaration). A marker-less rewrite of an already-written path was never
        # re-checked, so it cannot erase a declaration and win back auto-approval.
        context = _gs.db_git.session_context(session)
        previous = context.get("conflict_supersedes") or []
        if supersedes or any(row.get("path") in evaluated_paths for row in previous):
            context["conflict_supersedes"] = [
                row for row in previous if row.get("path") not in evaluated_paths
            ] + supersedes
            _gs.db_git.set_session_context(merge_id, context)
            session = _gs.db_git.get_session(merge_id)

    if staged and _gs.db_git.session_kind(session) == _gs.db_git.SESSION_KIND_MERGE:
        # D0006 §3.3 / L0007 §2.4: record which side each conflict chunk resolved to
        # (ours/theirs/both/manual) so the review screen can overlay it on the real
        # diff. Recomputed per path on every submission that touches it — a
        # re-instruction that changes a file's resolution replaces that path's
        # origins rather than appending stale ones.
        context = _gs.db_git.session_context(session)
        origins = [o for o in (context.get("conflict_origins") or []) if o.get("path") not in {p for p, *_ in staged}]
        for path, _target, content, original in staged:
            origins.extend(_classify_conflict_chunks(path, original, content))
        context["conflict_origins"] = origins
        _gs.db_git.set_session_context(merge_id, context)
        # `session` (fetched once, above) still carries the pre-write context JSON;
        # every read below this point goes through `db_git.session_context(session)`,
        # so re-fetch the row now or the conflict_origins write above would be
        # invisible to the rest of this call.
        session = _gs.db_git.get_session(merge_id)

    remaining = _gs.db_git.remaining_conflicts(merge_id)
    if not complete or remaining:
        return {
            "ok": True,
            "result": {
                "status": "conflict", "merge_commit": None, "pushed": False,
                "remaining_conflicts": remaining,
            },
        }

    if _gs.db_git.session_kind(session) == _gs.db_git.SESSION_KIND_GROUP_UPDATE:
        proc = _gs._run_git(
            [*_gs._GIT_IDENT, "commit", "-m", "Merge updated base into group"],
            cwd=root, author_env=_author_env_from_cfg(cfg),
        )
        if proc.returncode != 0:
            raise GitServiceError(500, "git_error", "Git command failed", diagnostic=_gs._last_line(proc.stderr))
        head = _gs._run_git(["rev-parse", "--short", "HEAD"], cwd=root)
        merge_commit = (head.stdout or "").strip() or None
        _gs.db_git.close_session(merge_id, "done")
        return {"ok": True, "result": {
            "status": "updated", "merge_commit": merge_commit, "pushed": False,
            "remaining_conflicts": [],
        }}

    if _gs.db_git.session_kind(session) in _gs.db_git.TR_SESSION_KINDS:
        # 088 — a TR conflict STOPS here. Every file is clean of markers and staged, and the
        # revert is one `git commit` from done, and that commit is exactly what this branch
        # refuses to make on its own.
        #
        # A merge conflict can end itself because a person still presses [병합] afterwards and
        # because "the markers are gone" is close to the whole question there — both sides were
        # written by people and the goal is to have both. A revert's question is not symmetric:
        # one side says "delete what this TR did" and the other is the work that landed on top
        # of it. A resolver — a person in a hurry or an AI that is confidently wrong — can
        # produce a marker-free file that undid half the TR, and if this branch committed it the
        # screen would say "cancelled" over a tree that is neither the old state nor the new one.
        # So the session stays open at `resolved`, the panel shows the diff, and
        # `commit_tr_conflict` is the second press that ends it.
        _set_tr_review_state(merge_id, TR_CONFLICT_REVIEW_RESOLVED)
        return {
            "ok": True,
            "result": {
                "status": "resolved_pending_review", "merge_commit": None, "pushed": False,
                "remaining_conflicts": [],
            },
        }

    # From here down the session is a finalize merge, so the conflict root IS the base
    # checkout; the name change keeps the merge/push reads saying what they mean.
    #
    # 0481 R0001/D0006/L0007 (T0008): a resolved general merge no longer commits on
    # "the markers are gone" alone. It freezes the FULL commit-candidate tree (every
    # path the merge commit would carry — resolved files, auto-merged files, deletes,
    # renames, mode changes) under the project lock, persists it as
    # resolved_pending_review, and stops there for a human to review real diff +
    # conflict-origin chunks and press [승인]/[반려]. The only bypass is
    # `auto_authority`, a boolean the session already carries BEFORE this submission
    # — stamped by a human's [AI 호출] or direct [해결 제출] press via
    # `record_auto_authority`, never by a field on this request (§2.2 — a worker
    # token cannot self-approve its own resolution).
    base_root = root
    base_branch = (cfg.get("base_branch") or "main").strip() or "main"
    holder = f"review:{merge_id}:{uuid.uuid4()}"
    if not _gs._acquire_lock(project_id, holder, wait_sec=_gs.LOCK_WAIT_SEC):
        raise GitServiceError(
            409, "git_busy", f"another git operation is in progress for '{project_id}'"
        )
    try:
        snapshot = _gs._freeze_commit_candidate(base_root, base_branch)
        context = _gs.db_git.session_context(session)
        context.update(snapshot)
        context["review_state"] = _gs.REVIEW_STATE_PENDING
        context["instruction_generation"] = int(context.get("instruction_generation") or 0)
        context["resolver_run_id"] = resolver_run_id
        provider_id, provider_name = _resolver_run_provider(resolver_run_id)
        context["resolver_provider"] = provider_name or provider_id
        context.setdefault("conversation", [])
        _gs.db_git.set_session_context(merge_id, context)
        # 0604 D0005 §3.4: a session carrying any `supersede` declaration always
        # stops for a person — the replaced lines must be read before the merge.
        automatic = bool(context.get("auto_authority")) and not context.get("conflict_supersedes")
    finally:
        _gs.db_git.release_lock(project_id, holder)

    if automatic:
        return _gs.approve_merge_review(
            group_id, merge_id,
            attempt_id=str(uuid.uuid4()),
            review_fingerprint=snapshot["review_fingerprint"],
            authority="automatic",
        )
    return {
        "ok": True,
        "result": {
            "status": "resolved_pending_review", "merge_commit": None, "pushed": False,
            "remaining_conflicts": [], "review_fingerprint": snapshot["review_fingerprint"],
        },
    }


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


def abort_merge(group_id: str, merge_id: int) -> dict:
    """Manual [hold] — abort the merge, preserve the work branch, reopen re-merge
    (0205 P scenario 9). Shares its end state with the auto-recovery sweep; only
    the trigger differs. The merge:{id} release is now best-effort legacy cleanup
    (0205 §2.1 stopped holding that lock). _set_status already broadcasts
    git_pending_changed so the badge clears immediately (0184 lesson).

    088: a TR conflict session arrives here from the same button on the same panel row,
    and `merge --abort` has nothing to abort in a group worktree — it is delegated whole
    to :func:`abort_tr_conflict` rather than given a second endpoint to learn."""
    from modules.flow_gate.services import git_service as _gs
    session, _cfg, project_id, root = _gs._session_context(group_id, merge_id)
    kind = _gs.db_git.session_kind(session)
    if kind in _gs.db_git.TR_SESSION_KINDS:
        return abort_tr_conflict(group_id, merge_id)
    _gs._run_git(["merge", "--abort"], cwd=root)
    if kind == _gs.db_git.SESSION_KIND_GROUP_UPDATE:
        _gs.db_git.close_session(merge_id, "aborted")
        return {"ok": True, "result": {
            "status": "aborted", "branch_preserved": True,
        }}
    # 0555 T0008 §8 (D0005 §3.6 B10): abort ends the Git attempt this final approval
    # was riding on, so its parked intent is discarded — never carried over to a
    # later session. The AC stays pending_review and the root stays in progress, so
    # pressing 최종승인 again simply starts over with a fresh intent id. Note this is
    # the ONLY discard path besides the §3.3 validity mismatch: a review rejection or
    # a re-review does NOT get here and must leave the intent alone.
    discarded = approval_intent.discard_intent(merge_id)
    _gs.db_git.close_session(merge_id, "aborted")
    _gs._set_status(group_id, "waiting")
    _gs.db_git.release_lock(project_id, f"merge:{merge_id}")   # legacy leftover, best-effort
    return {"ok": True, "result": {
        "status": "waiting", "branch_preserved": True,
        "final_approval_intent_discarded": discarded is not None,
    }}
