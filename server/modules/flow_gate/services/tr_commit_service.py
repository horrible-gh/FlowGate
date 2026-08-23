"""TR commit point service (flowgate.default.0332 D0005 §2.1 / P0006 §1 / L0007 §2.6).

A TR document being approved is the moment its work becomes history: this service is
called right after the approval transition has committed, decides whether that TR gets
a commit in the group worktree, makes it, and writes one ledger row either way.

Three rules run through every function here.

1. **The approval never fails because of git.** Nothing in this module raises: git
   disabled, no worktree, the lock held by a finalize, a broken commit — each one is a
   ``skipped_reason`` string in the returned payload and a ``no_commit`` ledger row, and
   the caller's approval stands (D0005 K1 / P0006 §1-6). Uncommitted work is never lost
   either — it stays in the worktree for the next TR commit or the finalize absorb.
2. **The commit scope is the worktree, not the document's reported list.** ``## 변경 파일``
   is prose a worker wrote; trusting it would silently hand one TR's file to the next TR's
   commit, and a later rewind would then revert somebody else's work (D0005 K2). The list
   is still used — as a *comparison*, reported back as ``reported_diff`` so the screen can
   warn — but never as a filter.
3. **A TR that changed nothing gets a row, not a commit.** An empty anchor commit would
   break finalize's "a group that did nothing is discarded" branch (D0005 K3), while a
   missing row would make "nothing to cancel" indistinguishable from "the commit could
   never be attempted" at rewind time.
"""
from __future__ import annotations

import logging
from typing import Any, Iterable, Optional

from modules.flow_gate.db import documents as db_docs
from modules.flow_gate.db import git_integration as db_git
from modules.flow_gate.db import tr_commit_ledger as db_ledger
from modules.flow_gate.services import git_service
from modules.flow_gate.services import tr_scope_service
from modules.flow_gate.storage import paths as storage_paths

_log = logging.getLogger(__name__)

TR_TYPE_CODE = "TR"

# L0007 §1 artifact_list_max — the same cap the finalize result already uses. P0006 §1
# spells the number "20" while calling it "마무리 목록 상한과 동일"; the finalize cap is
# 200 (git_service.FINALIZE_ARTIFACT_LIST_MAX), so the shared-cap intent both documents
# state is what is implemented, and the count is always exact regardless of the cap.
ARTIFACT_LIST_MAX = git_service.FINALIZE_ARTIFACT_LIST_MAX

# L0007 §1 panel_commit_list_max — how many rows the Git status panel shows before it
# folds the rest into "N개 더". The response never truncates silently: the total travels
# with the list (L0007 §5 "조용한 절단 금지").
PANEL_COMMIT_LIST_MAX = 20


def doc_code(doc: Optional[dict]) -> str:
    """``{seq:04d}-{TYPE}`` — P0006's short code, assembled from the document of the
    moment. Never stored (seq moves when the workflow is edited, D0005 K9)."""
    if not doc:
        return ""
    seq = doc.get("seq")
    type_code = (doc.get("type_code") or "").upper()
    if seq is None or not type_code:
        return str(doc.get("doc_id") or "")
    return f"{int(seq):04d}-{type_code}"


def commit_subject(doc: dict) -> str:
    """L0007 §2.6 ``tr_commit_subject`` — ``0009-TR: 제목``, one line, clipped.

    Deliberately NOT translated: the document code in front already says what the commit
    is, and a translate round-trip on every approval is not a cost this path can carry
    (L0007 §2.6). The finalize subject rules are untouched.
    """
    title = (doc.get("title") or "").strip()
    code = doc_code(doc)
    raw = f"{code}: {title}" if title else code
    subject = git_service.normalize_subject(raw)
    return subject[:git_service.COMMIT_SUBJECT_MAX]


def _reported_paths(doc: dict) -> Optional[list[str]]:
    """The TR's ``## 변경 파일`` list, or None when the body cannot be read.

    None and [] are different: an unreadable body must not be reported as "the TR
    declared nothing", which would mark every committed file as unreported.
    """
    try:
        raw_path = (doc.get("file_path") or "").strip()
        if not raw_path:
            return None
        path = storage_paths.resolve_storage_path(
            raw_path, doc.get("project_id"), branch=(doc.get("branch") or "main")
        )
        if path is None or not path.is_file():
            return None
        body = path.read_text(encoding="utf-8")
    except Exception:
        _log.warning("tr commit: reading %s failed", doc.get("doc_id"), exc_info=True)
        return None
    parsed = tr_scope_service.parse_reported_files(body)
    if not parsed.found:
        return None
    return list(parsed.paths)


def _reported_diff(doc: dict, committed_paths: list[str]) -> dict[str, list[str]]:
    """D0005 K2 — what the TR declared vs. what the commit actually carries.

    Advisory only. A mismatch is a warning badge, never a block: the commit already
    happened and the diff is how a human notices a forgotten line in the document.
    """
    reported = _reported_paths(doc)
    if reported is None:
        return {"unreported": [], "missing": []}
    reported_set = set(reported)
    committed_set = set(committed_paths)
    return {
        "unreported": sorted(committed_set - reported_set),
        "missing": sorted(reported_set - committed_set),
    }


def _payload(
    *,
    committed: bool,
    commit: Optional[str],
    subject: Optional[str],
    skipped_reason: Optional[str],
    artifacts: list[str],
    reported_diff: dict[str, list[str]],
) -> dict[str, Any]:
    """The P0006 §1 ``tr_commit`` object — one shape for all three entry points."""
    return {
        "committed": committed,
        "commit": commit,
        "subject": subject,
        "skipped_reason": skipped_reason,
        "excluded_artifact_count": len(artifacts),
        "excluded_artifacts": list(artifacts[:ARTIFACT_LIST_MAX]),
        "reported_diff": reported_diff,
    }


def on_document_approved(doc_id: str, document: Optional[dict] = None) -> Optional[dict]:
    """The approval hook. Returns the ``tr_commit`` payload, or None for a non-TR.

    None means "this approval was not a TR" — the caller then omits the key entirely so
    every existing non-TR approval response stays byte-identical (P0006 §1-8).

    Called from all three approval entry points (the path route, the RPC route and the
    unmanned inbox auto-approve). It decides for itself whether the document is a TR and
    whether the group has git, so a fourth entry point only ever needs this one line.
    """
    try:
        doc = document if document is not None else db_docs.get_by_id(doc_id)
        if not doc or (doc.get("type_code") or "").upper() != TR_TYPE_CODE:
            return None
        group_id = doc.get("group_id")
        if not group_id:
            # No group means no worktree to commit into. Still a TR approval, so the
            # key is present and says why — silence here is what makes a missing
            # commit unexplainable later.
            return _payload(
                committed=False, commit=None, subject=None,
                skipped_reason="git_inactive", artifacts=[],
                reported_diff={"unreported": [], "missing": []},
            )

        subject = commit_subject(doc)
        outcome = git_service.create_tr_commit(group_id, subject)
        artifacts = list(outcome.get("excluded_artifacts") or [])

        if outcome.get("committed"):
            diff = _reported_diff(doc, list(outcome.get("committed_paths") or []))
            _record(
                group_id=group_id, doc_id=doc["doc_id"], outcome=outcome, subject=subject,
            )
            return _payload(
                committed=True, commit=outcome.get("commit"),
                subject=outcome.get("subject") or subject, skipped_reason=None,
                artifacts=artifacts, reported_diff=diff,
            )

        _record(group_id=group_id, doc_id=doc["doc_id"], outcome=outcome, subject=subject)
        return _payload(
            committed=False, commit=None, subject=None,
            skipped_reason=outcome.get("skipped_reason") or "commit_failed",
            artifacts=artifacts, reported_diff={"unreported": [], "missing": []},
        )
    except Exception:
        # DB0008 §4-7: the ledger write lives outside the approval transaction and its
        # failure is swallowed here, exactly like the commit's. An approval that already
        # committed must not be turned into a 500 by its own bookkeeping.
        _log.warning("tr commit hook failed for %s", doc_id, exc_info=True)
        return None


def _record(*, group_id: str, doc_id: str, outcome: dict, subject: str) -> None:
    """One ledger row per approval round, whichever way the commit went."""
    try:
        if outcome.get("committed") and outcome.get("commit_sha"):
            db_ledger.record_commit(
                group_id=group_id, doc_id=doc_id,
                commit_sha=str(outcome["commit_sha"]),
                commit_subject=outcome.get("subject") or subject,
            )
        else:
            db_ledger.record_no_commit(
                group_id=group_id, doc_id=doc_id,
                skip_reason=outcome.get("skipped_reason") or "commit_failed",
            )
    except Exception:
        _log.warning("tr commit ledger write failed for %s", doc_id, exc_info=True)


# ── read models for the two screens (D0005 §6.1 / §6.2) ───────────────────────

def _row_view(row: dict, doc: Optional[dict] = None) -> dict[str, Any]:
    """One ledger row as a screen reads it. Short hashes are cut here (P0006 서두);
    the ledger keeps the full 40 characters."""
    sha = row.get("commit_sha")
    cancel = row.get("cancel_commit")
    return {
        "doc_id": row.get("doc_id"),
        "doc_code": doc_code(doc) if doc is not None else _code_from_row(row),
        "state": row.get("state"),
        "commit": sha[:7] if sha else None,
        "subject": row.get("commit_subject"),
        "skipped_reason": row.get("skip_reason"),
        "cancel_commit": cancel[:7] if cancel else None,
        # T0018 K11 — two live rows for one step are otherwise indistinguishable.
        # A boolean, not the raw id: the screen says "restored", it does not join.
        "restored": row.get("restored_from_id") is not None,
    }


def _code_from_row(row: dict) -> str:
    seq, type_code = row.get("seq"), (row.get("type_code") or "").upper()
    if seq is None or not type_code:
        return str(row.get("doc_id") or "")
    return f"{int(seq):04d}-{type_code}"


def slot_commit_states(doc_ids: Iterable[str]) -> dict[str, dict[str, Any]]:
    """``doc_id -> marker state`` for the workflow strip (D0005 §6.1).

    The newest ledger row per document wins — a TR rewound and re-approved shows its
    new commit, not the canceled one. ``no_commit`` rows return nothing at all: a step
    that changed no source stays quiet, which is the point of the marker being absent.
    Never raises — a failed lookup just means no markers, and the strip looks exactly
    as it did before this feature.
    """
    try:
        latest = db_ledger.latest_by_doc(doc_ids)
    except Exception:
        _log.warning("tr commit slot states lookup failed", exc_info=True)
        return {}
    out: dict[str, dict[str, Any]] = {}
    for doc_id, row in latest.items():
        if row.get("state") not in ("live", "canceled"):
            continue
        sha, cancel = row.get("commit_sha"), row.get("cancel_commit")
        out[doc_id] = {
            "state": row.get("state"),
            "commit": sha[:7] if sha else None,
            "subject": row.get("commit_subject"),
            "cancel_commit": cancel[:7] if cancel else None,
            # T0018 K11 — the marker comes back to "committed" through the exact same
            # newest-row-wins rule that made it go away; this only lets the hover line
            # say the commit is a restored one rather than a fresh approval.
            "restored": row.get("restored_from_id") is not None,
        }
    return out


def _reapply_pending(rows: list[dict]) -> bool:
    """Is there a canceled commit this group could still put back? (T0018 §3-5)

    Computed from the rows already in hand rather than by a second query — this panel
    is where 0282 NR0003 finding 1 pulled an N+1 out, and one more per-slot round trip is
    how that comes back. It answers the same question
    :func:`~modules.flow_gate.db.tr_commit_ledger.reappliable_rows` answers: a canceled
    row with a cancel commit and no newer live row for the same document.

    Bounded by the caller's LIMIT, so on a group with hundreds of rounds this can read
    False while an older row is still restorable. That is the honest direction to be
    wrong in: it hides a button, it never offers one that would do nothing.
    """
    newest_live: dict[str, int] = {}
    for row in rows:
        if row.get("state") != "live":
            continue
        doc_id = row.get("doc_id")
        row_id = int(row.get("id") or 0)
        if row_id > newest_live.get(doc_id, 0):
            newest_live[doc_id] = row_id
    for row in rows:
        if row.get("state") != "canceled" or not row.get("cancel_commit"):
            continue
        if int(row.get("id") or 0) > newest_live.get(row.get("doc_id"), 0):
            return True
    return False


def _last_block(block: Optional[dict]) -> Optional[dict[str, Any]]:
    """The last time the cancel/reapply gate refused this group, with its verdict.

    ``retryable`` is read off the ONE table (:data:`CANCEL_BLOCK_RETRYABLE`) rather than
    re-decided here, so the panel and the result screen can never disagree about whether
    a second press is worth offering (P0006 §5-3).
    """
    reason = (block or {}).get("last_cancel_block_reason")
    if not reason:
        return None
    return {
        "reason": reason,
        "sub": (block or {}).get("last_cancel_block_sub"),
        "at": (block or {}).get("last_cancel_block_at"),
        "retryable": CANCEL_BLOCK_RETRYABLE.get(reason, False),
    }


def _summarize(rows: list[dict], block: Optional[dict] = None) -> dict[str, Any]:
    counts = {"live": 0, "canceled": 0, "no_commit": 0}
    for row in rows:
        state = row.get("state")
        if state in counts:
            counts[state] += 1
    listed = [_row_view(row) for row in rows[:PANEL_COMMIT_LIST_MAX]]
    return {
        **counts,
        "commits": listed,
        "more": max(0, len(rows) - len(listed)),
        # T0018 §3-5 — whether the panel may draw [restore the source again] is decided
        # here and sent, not guessed on the screen from the row list.
        "reapply_pending": _reapply_pending(rows),
        "last_block": _last_block(block),
    }


EMPTY_SUMMARY = {
    "live": 0, "canceled": 0, "no_commit": 0, "commits": [], "more": 0,
    "reapply_pending": False, "last_block": None,
    # TR0019 — the parked conflict this group is sitting on, if any. Sent rather than
    # inferred: the panel must not have to guess from a `git_busy` block whether the group
    # is waiting on somebody else's git command or on a conflict of its own.
    "conflict_session": None,
}


def group_commit_summary(group_id: str) -> dict[str, Any]:
    """The Git status panel's per-slot block (D0005 §6.2).

    ``live``/``canceled``/``no_commit`` counts are always exact; ``commits`` carries at
    most PANEL_COMMIT_LIST_MAX rows and ``more`` says how many were folded away, so the
    panel can render "N개 더" instead of quietly showing a short list.
    """
    try:
        rows = db_ledger.list_by_group(group_id)
        block = db_ledger.blocks_by_group([group_id]).get(group_id)
    except Exception:
        _log.warning("tr commit summary failed for %s", group_id, exc_info=True)
        return dict(EMPTY_SUMMARY)
    return {
        **_summarize(rows, block),
        "conflict_session": git_service.tr_conflict_session(group_id),
    }


def group_commit_summaries(group_ids: Iterable[str]) -> dict[str, dict[str, Any]]:
    """:func:`group_commit_summary` for a whole panel in one query.

    Groups with no ledger row are absent from the result; the caller substitutes the
    empty summary, so a group that predates this feature renders exactly as before.
    """
    ids = [g for g in group_ids if g]
    if not ids:
        return {}
    try:
        rows = db_ledger.list_by_groups(ids)
        blocks = db_ledger.blocks_by_group(ids)
    except Exception:
        _log.warning("tr commit summaries failed", exc_info=True)
        return {}
    grouped: dict[str, list[dict]] = {}
    for row in rows:
        grouped.setdefault(row.get("group_id"), []).append(row)
    return {
        gid: {
            **_summarize(rows_of, blocks.get(gid)),
            "conflict_session": git_service.tr_conflict_session(gid),
        }
        for gid, rows_of in grouped.items()
    }


# ── cancel side (D0005 §3.2 / P0006 §3·§4 / L0007 §2.2) ───────────────────────

# P0006 §5-3. Two of the five are worth a person's second press and three are not:
# a dirty worktree and a busy lock go away when the person tidies up or waits, while
# a merged group, a missing worktree and a git-inactive project will answer exactly
# the same thing forever — offering [다시 시도] there is a button that lies.
CANCEL_BLOCK_RETRYABLE = {
    "already_merged": False,
    "no_worktree": False,
    "git_inactive": False,
    "dirty_worktree": True,
    "git_busy": True,
}


def empty_cancel_result() -> dict[str, Any]:
    """The P0006 §3 ``tr_commit_cancel`` object in its "nothing happened" form."""
    return {
        "attempted": False, "blocked_reason": None,
        "canceled": [], "skipped": [], "stopped_reason": None, "retryable": False,
        # TR0019 — the parked conflict, when a revert stopped on one. Always present so a
        # reader never has to tell "no conflict" apart from "an older build's payload".
        "conflict_session": None,
    }


def _park_conflict(
    session: dict,
    *,
    kind: str,
    group_id: str,
    row: dict,
    code: str,
    target_sha: str,
    original_sha: Optional[str],
    subject: str,
    body: str,
) -> Optional[dict[str, Any]]:
    """Keep a conflicted revert as a session instead of wiping it (TR0019).

    Until this existed, both loops answered a conflict by destroying the evidence and
    labelling the step `retryable=False` — truthfully, since pressing again produces the
    same conflict, and uselessly, since the one thing that WOULD help (looking at the
    file) had just been deleted. FlowGate already resolves conflicts, with an inline
    editor and an AI run, for the finalize merge; all of that hangs off a conflict session
    row, so this makes one.

    Returns the session summary for the response, or None when the conflict could not be
    parked at all — no unmerged path, or a session already open for this group. None means
    the old destroy ran instead, so the caller's remaining bookkeeping is unchanged either
    way. `git clean` appears in neither path (0382).
    """
    parked = git_service.open_tr_conflict_session(
        session,
        kind=kind,
        group_id=group_id,
        ledger_row_id=int(row["id"]),
        doc_id=str(row.get("doc_id") or ""),
        doc_code=code,
        target_sha=target_sha,
        original_sha=original_sha,
        subject=subject,
        body=body,
    )
    if not parked:
        git_service.restore_after_failed_revert(session)
        return None
    return {
        "merge_id": parked["merge_id"],
        "kind": kind,
        "files": parked["files"],
        "doc_id": row.get("doc_id"),
        "doc_code": code,
        "review_state": git_service.TR_CONFLICT_REVIEW_OPEN,
    }


def _line(row: dict, code: str) -> dict[str, Any]:
    sha = row.get("commit_sha")
    return {
        "doc_id": row.get("doc_id"), "doc_code": code,
        "commit": sha[:7] if sha else None,
    }


def _skip_line(row: dict, code: str, reason: str) -> dict[str, Any]:
    return {**_line(row, code), "reason": reason}


def _cancel_line(row: dict, code: str, cancel_sha: Optional[str]) -> dict[str, Any]:
    return {**_line(row, code), "cancel_commit": cancel_sha[:7] if cancel_sha else None}


def _doc_codes(rows: list[dict]) -> dict[str, str]:
    """``doc_id -> 0009-TR`` for the rows in play, assembled from the documents of the
    moment (never from a stored copy — seq moves, D0005 K9)."""
    codes: dict[str, str] = {}
    for doc_id in {row.get("doc_id") for row in rows if row.get("doc_id")}:
        try:
            codes[doc_id] = doc_code(db_docs.get_by_id(doc_id))
        except Exception:
            codes[doc_id] = str(doc_id)
    return codes


def cancel_tr_commits(
    group_id: str,
    reopened_doc_ids: Iterable[str],
    *,
    exclude_row_ids: Optional[Iterable[int]] = None,
) -> dict[str, Any]:
    """Revert the rewound region's TR commits, newest first (L0007 §2.2).

    Shared by the rewind (P0006 §3) and the [다시 시도] retry (P0006 §4) — one gate
    ladder, one loop, one fallback set, so the two can never answer differently.

    ``exclude_row_ids`` takes single ledger ROWS out of the target list. It exists because
    "do not touch a step the person has already redone" is a statement about a row, not
    about a document: a step that was re-approved after a failed cancel has a fresh live
    row AND the stale one the cancel never got to, and dropping the whole document drops
    both (T0018 §3-4). Only :func:`cancel_retry` passes it; the rewind's own cancel has no
    such rows yet.

    Raises nothing that the caller has to translate; it does let a truly unexpected
    error propagate so ``_rearm_git`` can drop the whole key (L0007 §5) rather than
    report a half-truth. The rewind itself has already committed and stands either way
    (D0005 K8).
    """
    result = empty_cancel_result()

    # G1 before the gates, on purpose: a group that never made a commit — documents
    # only, or git off — must end on the quiet "취소할 커밋이 없었습니다", not on a
    # `git_inactive` result screen implying something went wrong (L0007 §4.1 note 1).
    excluded = {int(row_id) for row_id in (exclude_row_ids or []) if row_id is not None}
    targets = [
        row for row in db_ledger.live_rows(group_id, reopened_doc_ids)
        if int(row["id"]) not in excluded
    ]
    if not targets:
        result["attempted"] = True
        return result

    codes = _doc_codes(targets)
    opened = git_service.open_cancel_session(
        group_id, [row.get("commit_sha") for row in targets]
    )
    if not opened.get("ok"):
        return _blocked(result, group_id, opened["blocked_reason"], opened["block_sub"])

    session = opened["session"]
    try:
        result["attempted"] = True
        stopped = False
        for row in targets:
            row_id, code = row["id"], codes.get(row.get("doc_id"), "")
            if stopped:
                # Ordered work: skipping the conflicting commit and reverting the one
                # under it would conflict harder, so everything older is left alone.
                result["skipped"].append(_skip_line(row, code, "not_attempted"))
                continue
            if db_ledger.is_canceled(row_id):
                result["skipped"].append(_skip_line(row, code, "already_canceled"))
                continue

            sha = row.get("commit_sha") or ""
            subject = git_service.cancel_subject(row.get("commit_subject"))
            body = git_service.cancel_body(sha, code, group_id)
            outcome = git_service.revert_tr_commit(
                session, commit_sha=sha, subject=subject, body=body,
            )

            if outcome["kind"] == "blocked":
                sub = outcome.get("sub") or "revert_conflict"
                if sub == "revert_conflict":
                    # TR0019 — park it so it can still be resolved. Only a genuine conflict
                    # is parked: a timeout or a refused commit leaves nothing anybody could
                    # edit their way out of, and an unfinishable session on a group would
                    # block the next rewind for no gain.
                    result["conflict_session"] = _park_conflict(
                        session, kind=db_git.SESSION_KIND_TR_REVERT, group_id=group_id,
                        row=row, code=code, target_sha=sha, original_sha=sha,
                        subject=subject, body=body,
                    )
                else:
                    git_service.restore_after_failed_revert(session)
                db_ledger.record_cancel_attempt(row_id, failed_reason=sub)
                result["skipped"].append(_skip_line(row, code, "conflict"))
                result["stopped_reason"] = "conflict"
                stopped = True
                continue

            if outcome["kind"] == "empty":
                # Nothing left to undo. The row still becomes `canceled` — the state
                # is about the commit's effect being gone, not about who removed it —
                # but with no cancel commit, because an empty commit is noise.
                db_ledger.mark_canceled(row_id, cancel_commit=None, reason="empty_revert")
                result["skipped"].append(_skip_line(row, code, "already_canceled"))
                continue

            # Written per row, immediately (L0007 §4.3): a crash one row later must
            # not leave a revert in git that the ledger still calls live, or the next
            # rewind would revert the revert.
            if db_ledger.mark_canceled(row_id, cancel_commit=outcome["commit"]):
                result["canceled"].append(_cancel_line(row, code, outcome["commit"]))
            else:
                _log.warning(
                    "tr cancel: ledger row %s was already canceled by another writer",
                    row_id,
                )
                result["skipped"].append(_skip_line(row, code, "already_canceled"))

        # Never true out of this loop: a conflict repeats on retry and the worktree
        # needs a person, so the screen offers no button (L0007 §4.2).
        result["retryable"] = False
        return result
    finally:
        # Before the re-arm, always: `reopen_group_git` takes the same project lock and
        # it is not re-entrant (L0007 §2.1 ③).
        git_service.close_cancel_session(session)


def _blocked(
    result: dict, group_id: str, reason: str, sub: Optional[str]
) -> dict[str, Any]:
    result["attempted"] = False
    result["blocked_reason"] = reason
    result["retryable"] = CANCEL_BLOCK_RETRYABLE.get(reason, False)
    try:
        db_ledger.record_block(group_id, reason, sub)
    except Exception:
        _log.warning("tr cancel block record failed for %s", group_id, exc_info=True)
    return result


def cancel_for_reopen(group_id: str, reopened_doc_ids: Iterable[str]) -> dict[str, Any]:
    """The rewind's cancel (P0006 §3). Runs BEFORE the git re-arm — a re-armed slot is
    rebuilt from base HEAD and holds none of the commits this is here to undo."""
    return cancel_tr_commits(group_id, reopened_doc_ids)


def cancel_retry(group_id: str) -> dict[str, Any]:
    """The [다시 시도] path (P0006 §4 / L0007 §2.7).

    The rewind is long over, so there is no ``reopened`` list to work from; the targets
    come from the stored return point. A step re-approved in the meantime made a NEW
    commit, and reverting that would undo work the person just redid.

    T0018 §3-4 fixed what "do not touch it" was doing here. It used to drop the whole
    DOCUMENT the moment its approval was back, and a document is not the unit: a step
    whose cancel was blocked (a dirty worktree, say) and which was then re-approved holds
    TWO live rows — the stale one the cancel never reached, and the fresh one the
    re-approval just committed. Dropping the document dropped both, so the stale row could
    never be reached again by any path, stayed ``live`` forever, and disappeared from the
    workflow strip (which only reads the newest row per step) while still counting in the
    panel badge. The exclusion is now stated on the row it is actually about: the newest
    live row of a re-approved document, and nothing else.

    Touches no document — this endpoint re-runs a cancel, it does not rewind anything.
    """
    from modules.flow_gate.db import workflow_return_points as db_rp

    doc_ids: list[str] = []
    reapproved: list[str] = []
    rp = db_rp.get_by_group(group_id)
    if rp:
        # `list_candidates(..., front_seq)` is the whole snapshot: front_seq is the
        # highest seq the return point holds, so nothing in it is filtered out here.
        for item in db_rp.list_candidates(rp["id"], int(rp["front_seq"])):
            doc_id = item.get("doc_id")
            if not doc_id:
                continue
            doc_ids.append(doc_id)
            doc = db_docs.get_by_id(doc_id)
            if doc and (doc.get("doc_review_status") or "") == "approved":
                reapproved.append(doc_id)
    if not doc_ids:
        result = empty_cancel_result()
        result["attempted"] = True
        return result
    exclude: set[int] = set()
    if reapproved:
        exclude = set(db_ledger.newest_live_id_by_doc(reapproved).values())
    return cancel_tr_commits(group_id, doc_ids, exclude_row_ids=exclude)


# ── reapply side (T0018 K11 — the forward restore peels the cancel commits off) ──

# P0006 §5-4's closed set for the other direction. Four of the five are the cancel loop's
# own vocabulary; `superseded` and `no_cancel_commit` are what only a reapply can hit.
REAPPLY_SKIP_REASONS = (
    "superseded", "no_cancel_commit", "empty_revert", "conflict", "not_attempted",
)


def empty_restore_result() -> dict[str, Any]:
    """The ``tr_commit_restore`` object in its "nothing happened" form.

    Same five keys as :func:`empty_cancel_result` with ``canceled`` renamed ``reapplied``,
    on purpose: the screen that reads one reads the other, and a second shape would be a
    second set of branches to get wrong.
    """
    return {
        "attempted": False, "blocked_reason": None,
        "reapplied": [], "skipped": [], "stopped_reason": None, "retryable": False,
        "conflict_session": None,
    }


def _reapply_line(row: dict, code: str, new_sha: Optional[str]) -> dict[str, Any]:
    """One restored step. ``commit`` stays the ORIGINAL TR commit — that is the thing the
    person recognises; the reapply commit is the mechanism and rides alongside."""
    cancel = row.get("cancel_commit")
    return {
        **_line(row, code),
        "cancel_commit": cancel[:7] if cancel else None,
        "reapply_commit": new_sha[:7] if new_sha else None,
    }


def reapply_tr_commits(group_id: str, doc_ids: Iterable[str]) -> dict[str, Any]:
    """Put the canceled TR commits back, oldest cancel LAST (T0018 K11).

    The exact mirror of :func:`cancel_tr_commits`: same gate ladder (the very same
    ``open_cancel_session``), same one-commit-per-TR rule, same stop-at-the-first-conflict
    fallback, same retryable table. Reverting the revert is the only form that leaves the
    rewind itself readable in the log — FlowGate is a time machine, not an eraser
    (D0005 K5), so the history gains a third commit rather than losing the second.

    Nothing here raises for the caller to translate. The forward restore has already
    re-approved its documents and stands whatever git says (D0005 K8).
    """
    result = empty_restore_result()

    rows = db_ledger.reappliable_rows(group_id, doc_ids)
    codes = _doc_codes(rows)
    targets: list[dict] = []
    for row in rows:
        code = codes.get(row.get("doc_id"), "")
        if int(row.get("newer_live") or 0) > 0:
            # The person redid this step by hand after the rewind and its commit is
            # already in the tree. Putting the old one back on top would apply the same
            # work twice, so this row is reported, not applied.
            result["skipped"].append(_skip_line(row, code, "superseded"))
        elif not row.get("cancel_commit"):
            # The cancel was an empty revert (`cancel_reason='empty_revert'`): the row is
            # canceled but there is no commit to peel back off.
            result["skipped"].append(_skip_line(row, code, "no_cancel_commit"))
        else:
            targets.append(row)

    # Same reason G1 sits before the gates on the cancel side: a restore that had nothing
    # to put back must end quietly, not on a git error screen (L0007 §4.1 note 1).
    if not targets:
        result["attempted"] = True
        return result

    opened = git_service.open_cancel_session(
        group_id, [row.get("cancel_commit") for row in targets]
    )
    if not opened.get("ok"):
        return _blocked(result, group_id, opened["blocked_reason"], opened["block_sub"])

    session = opened["session"]
    try:
        result["attempted"] = True
        stopped = False
        for row in targets:
            row_id, code = row["id"], codes.get(row.get("doc_id"), "")
            if stopped:
                result["skipped"].append(_skip_line(row, code, "not_attempted"))
                continue

            cancel_sha = row.get("cancel_commit") or ""
            subject = git_service.reapply_subject(row.get("commit_subject"))
            body = git_service.reapply_body(
                cancel_sha, row.get("commit_sha") or "", code, group_id
            )
            outcome = git_service.reapply_tr_commit(
                session, cancel_commit=cancel_sha, subject=subject, body=body,
            )

            if outcome["kind"] == "blocked":
                sub = outcome.get("sub") or "reapply_conflict"
                if sub == "revert_conflict":
                    # Same parking as the cancel loop, and deliberately the same function:
                    # a reapply IS a revert, so a resolver sees one kind of session with one
                    # label on it saying which direction it is going.
                    result["conflict_session"] = _park_conflict(
                        session, kind=db_git.SESSION_KIND_TR_REAPPLY, group_id=group_id,
                        row=row, code=code, target_sha=cancel_sha,
                        original_sha=row.get("commit_sha"), subject=subject, body=body,
                    )
                else:
                    git_service.restore_after_failed_revert(session)
                # The attempt log lives on the canceled row it failed to restore — the
                # response collapses every failure into `conflict` (P0006 §5-4) and this
                # is where the difference survives.
                db_ledger.record_cancel_attempt(row_id, failed_reason=sub)
                result["skipped"].append(_skip_line(row, code, "conflict"))
                result["stopped_reason"] = "conflict"
                stopped = True
                continue

            if outcome["kind"] == "empty":
                # This TR's content is already in the tree by some other route. An empty
                # commit would be noise, and writing a live row would claim a commit that
                # does not exist — the row stays canceled and says why.
                result["skipped"].append(_skip_line(row, code, "empty_revert"))
                continue

            # Written per row, immediately (L0007 §4.3): dying one row later must not
            # leave a reapply in git that the ledger still calls canceled, or the next
            # restore would apply it a second time.
            new_row = db_ledger.record_reapply(
                group_id=group_id, doc_id=row["doc_id"],
                commit_sha=outcome["commit"],
                commit_subject=row.get("commit_subject") or "",
                restored_from_id=row_id,
            )
            if new_row and new_row.get("state") == "live":
                result["reapplied"].append(_reapply_line(row, code, outcome["commit"]))
            else:
                # `_execute` reports no row count, so the write is judged by reading the
                # row back, the same way `mark_canceled` judges its own. git did put the
                # source back either way; staying silent about it is the louder lie.
                _log.warning("tr reapply: ledger row for %s was not written", row_id)
                result["reapplied"].append(_reapply_line(row, code, outcome["commit"]))

        # Never true out of this loop, for the same reason the cancel's is not: a conflict
        # answers the same on the next press and the worktree needs a person (L0007 §4.2).
        result["retryable"] = False
        return result
    finally:
        git_service.close_cancel_session(session)


def restore_for_return(group_id: str, restored_doc_ids: Iterable[str]) -> dict[str, Any]:
    """The forward restore's reapply — the mirror of :func:`cancel_for_reopen`.

    Runs AFTER the document transaction has committed, never inside it: git is slow, it
    takes the project lock, and the rewind side already split for exactly that reason.
    """
    return reapply_tr_commits(group_id, restored_doc_ids)


def reapply_retry(group_id: str) -> dict[str, Any]:
    """The Git status panel's "restore the source again" button — cancel_retry's mirror.

    Deliberately does NOT read the return point. A completed forward restore deletes it
    (`return_point_cleared`), so a retry hung off the snapshot would answer "nothing to do"
    in precisely the case that needs an answer: the documents came forward, the source did
    not, and the return point is gone. The targets are instead every canceled row of the
    group whose document is approved right now, and the "no newer live row" condition
    inside :func:`~modules.flow_gate.db.tr_commit_ledger.reappliable_rows` is what keeps
    that from applying anything twice.

    Touches no document.
    """
    doc_ids: list[str] = []
    seen: set[str] = set()
    for row in db_ledger.list_by_group(group_id):
        doc_id = row.get("doc_id")
        if not doc_id or doc_id in seen or row.get("state") != "canceled":
            continue
        seen.add(doc_id)
        doc = db_docs.get_by_id(doc_id)
        if doc and (doc.get("doc_review_status") or "") == "approved":
            doc_ids.append(doc_id)
    if not doc_ids:
        result = empty_restore_result()
        result["attempted"] = True
        return result
    return reapply_tr_commits(group_id, doc_ids)


# ── parked conflict resolution (TR0019) ──────────────────────────────────────

def conflict_session(group_id: str) -> Optional[dict[str, Any]]:
    """This group's open TR conflict session, or None. Thin pass-through by design —
    the screen asks one service for everything about TR commits."""
    return git_service.tr_conflict_session(group_id)


def commit_conflict_resolution(group_id: str, merge_id: int) -> dict[str, Any]:
    """Finish a resolved TR conflict: make the commit, then write the ledger row.

    The two halves are split across the module boundary because that is where they belong
    — git_service owns git and the session table, this module owns the ledger — and the
    order is the one L0007 §4.3 asks for everywhere else: the commit exists before anything
    claims it does. If the ledger write is what fails, git holds a revert the ledger still
    calls live, and the next rewind reads that as "not cancelled yet" and tries again. The
    other order would leave the ledger claiming a cancel that never happened, and nothing
    ever goes back to check.

    Raises through :class:`GitServiceError` for the caller to translate: unlike the cancel
    and reapply loops this is a person pressing a button and waiting for an answer, not a
    best-effort step hanging off a document transition that must stand regardless.
    """
    outcome = git_service.commit_tr_conflict(group_id, merge_id)
    result = dict(outcome.get("result") or {})
    kind, commit = result.get("kind"), result.get("commit")
    row_id = result.get("ledger_row_id")
    row = db_ledger.get_by_id(int(row_id)) if row_id is not None else None
    written = False
    if row is not None and result.get("status") == "empty":
        # The resolution kept the tree as it already was, so there is no commit. Exactly
        # the cancel loop's `empty_revert`: a cancel becomes canceled-with-no-commit
        # (the effect is gone, nobody had to remove it), and a reapply writes nothing —
        # a live row would claim a commit that does not exist.
        written = (
            bool(db_ledger.mark_canceled(
                int(row_id), cancel_commit=None, reason="empty_revert"))
            if kind == db_git.SESSION_KIND_TR_REVERT else True
        )
    elif row is not None and kind == db_git.SESSION_KIND_TR_REVERT:
        written = bool(db_ledger.mark_canceled(int(row_id), cancel_commit=commit))
    elif row is not None and kind == db_git.SESSION_KIND_TR_REAPPLY:
        new_row = db_ledger.record_reapply(
            group_id=group_id,
            doc_id=str(row.get("doc_id") or ""),
            commit_sha=commit or "",
            commit_subject=row.get("commit_subject") or "",
            restored_from_id=int(row_id),
        )
        written = bool(new_row and new_row.get("state") == "live")
    if not written:
        # Judged by reading the row back — `_execute` returns no row count. The commit is
        # real either way, so this is reported, not swallowed.
        _log.warning(
            "tr conflict resolution: ledger row %s not updated after commit %s",
            row_id, commit,
        )
    result["ledger_written"] = written

    # And then keep going. A rewind asks for a RUN of commits to come off, and a conflict
    # stops the loop at that commit with everything older left `not_attempted` — correct,
    # because peeling the one underneath would conflict harder. But the person asked for
    # the whole run, and once this one is resolved nothing else in the product would pick
    # the rest up: a stop-at-conflict result carries `retryable=False`, so no screen offers
    # a second press. Resuming here is the only place the run can continue from, and the
    # alternative is a tree that is half-rewound with no button admitting it.
    #
    # Bounded by construction: each pass either commits a revert or parks a new conflict
    # session, and a parked session is what the caller sees next.
    try:
        result["continued"] = (
            cancel_retry(group_id) if kind == db_git.SESSION_KIND_TR_REVERT
            else reapply_retry(group_id)
        )
    except Exception:
        _log.warning(
            "tr conflict resolution: continuing the run failed for %s", group_id,
            exc_info=True,
        )
        result["continued"] = None
    return {"ok": True, "result": result}


def abort_conflict_resolution(group_id: str, merge_id: int) -> dict[str, Any]:
    """Give up on a parked TR conflict.

    The ledger row keeps exactly the state it already had — a cancel that gave up leaves a
    live commit, a reapply that gave up leaves a canceled one — so all that is written is
    one more line in the attempt log, which is where "we tried and stopped" has always
    lived (DB0008 §4-4).
    """
    outcome = git_service.abort_tr_conflict(group_id, merge_id)
    result = dict(outcome.get("result") or {})
    row_id = result.get("ledger_row_id")
    if row_id is not None:
        try:
            db_ledger.record_cancel_attempt(
                int(row_id), failed_reason="conflict_abandoned"
            )
        except Exception:
            _log.warning(
                "tr conflict abort: attempt log failed for row %s", row_id, exc_info=True
            )
    return {"ok": True, "result": result}


# ── rewind preview (P0006 §2 / D0005 §6.3) ────────────────────────────────────

def commit_preview(group_id: str) -> dict[str, Any]:
    """What the rewind confirm dialog shows BEFORE the button is pressed.

    Rows are per commit, keyed by the step's ``seq`` so the dialog can put each one on
    its own step line; a step with no row renders as "소스 변경 없음" from the absence,
    which is why ``no_commit`` rows are not carried here.

    ``group_status`` is read through the same gate ladder the cancel uses, so the
    dialog and the button cannot disagree about the group.
    """
    status = git_service.cancel_group_status(group_id)
    rows = db_ledger.commit_rows_by_group(group_id)
    commits: list[dict[str, Any]] = []
    for row in rows:
        sha, cancel = row.get("commit_sha"), row.get("cancel_commit")
        commits.append({
            "seq": int(row["seq"]) if row.get("seq") is not None else None,
            "doc_id": row.get("doc_id"),
            "doc_code": _code_from_row(row),
            "commit": sha[:7] if sha else None,
            "subject": row.get("commit_subject"),
            "status": row.get("state"),
            "cancel_commit": cancel[:7] if cancel else None,
        })
    return {"group_status": status, "commits": commits}
