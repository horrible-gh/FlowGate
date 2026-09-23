"""Conflict-session lifecycle and merge-conflict resolution.

Extracted from git_service.py (flowgate.default.0550 T0015, D0006 §3.2/부록 A).
"""
from __future__ import annotations

import hashlib
import logging
import re
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

def _conflict_side_dropped(original: str, submitted: str) -> bool:
    """True if a base-having chunk where BOTH sides changed something over the common
    ancestor resolved to an exact, whole-side selection of just one of them.

    Classification is chunk-local, anchored by the unedited common context around each
    chunk rather than a whole-file line-membership test or an unbounded scan (see
    :func:`_anchor_chunk_selections`, shared with :func:`_classify_conflict_chunks`'s
    ours/theirs/both/manual labelling): a manual/synthesized resolution that rewrites
    both sides' intent into a new line is ``manual``, not ``ours``/``theirs``, and is not
    rejected here — nothing was dropped in the sense this check exists for. Because each
    chunk's search window is bracketed by the common text immediately before and after
    it, the same text sitting anywhere else in the file — in ordinary unedited context,
    before the chunk, after it, or claimed by a neighboring chunk's own window — cannot
    stand in for a side this chunk actually dropped, and cannot hide a side it actually
    kept either.

    Chunks without a base (no common ancestor available) are still walked — to keep the
    anchor aligned with later chunks — but never trigger rejection: there is nothing to
    diff against. A chunk where only one side actually changed anything over base is also
    exempt: keeping the changed side and dropping the unchanged one is a normal, correct
    resolution.
    """
    segments = _split_content_segments(original)
    if segments is None:
        return False
    chunks = [s for s in segments if s["type"] == "chunk"]
    if not chunks:
        return False
    submitted_lines = (submitted or "").splitlines()
    for entry in _anchor_chunk_selections(segments, submitted_lines):
        chunk = entry["chunk"]
        base = chunk.get("base")
        ours, theirs = chunk["ours"], chunk["theirs"]
        both_changed = False
        if base is not None:
            both_changed = bool(_chunk_added_lines(ours, base)) and bool(_chunk_added_lines(theirs, base))
        if both_changed and entry["selection"] in ("ours", "theirs"):
            return True
    return False


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
    return {
        "ok": True,
        "merge_id": merge_id,
        "branch": state.get("branch"),
        "base_branch": (cfg.get("base_branch") or "main"),
        "files": files,
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
    staged: list[tuple[str, Path, str]] = []
    for f in files or []:
        path = f.get("path")
        content = f.get("content")
        if not isinstance(path, str) or not isinstance(content, str):
            raise GitServiceError(422, "invalid_request", "each file needs path and content")
        if path not in session_paths:
            raise GitServiceError(
                422, "invalid_request", f"'{path}' is not part of merge session {merge_id}"
            )
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
            if _gs.has_conflict_markers(original) and _conflict_side_dropped(original, content):
                raise GitServiceError(
                    422, "conflict_side_dropped",
                    f"'{path}' dropped one whole side of a resolved conflict chunk",
                )
        target = resolve_in_root(root, path)
        if target is None:
            raise GitServiceError(422, "invalid_request", f"unsafe path: '{path}'")
        staged.append((path, target, content, original))

    for path, target, content, _original in staged:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        proc = _gs._run_git(["add", "--", path], cwd=root)
        if proc.returncode != 0:
            raise GitServiceError(500, "git_error", "Git command failed", diagnostic=_gs._last_line(proc.stderr))
        _gs.db_git.mark_file_resolved(merge_id, path)

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
        automatic = bool(context.get("auto_authority"))
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
