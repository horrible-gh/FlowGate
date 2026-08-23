"""TR commit ledger storage (flowgate.default.0332 DB0008, migration 086).

One row per TR **approval round** — not per document. A TR that was rewound and
approved again gets a second row, and which row is live is read off ``state``
(D0005 K9). Rows are never deleted; a cancel writes ``state='canceled'`` onto the
row it cancels. FlowGate is a time machine, not an eraser.

``id`` is L0007's ``ledger_row_no``: insert order is commit topology order, because
every TR commit is made on the same branch under the project git lock (L0007 §2.4).
Nothing here stores ``seq`` or ``doc_code`` — both move when the workflow is edited,
so every read joins ``documents`` for the value of the moment (DB0008 §2-1/§4-6).

Follows the dominant inline-SQL pattern (``get_store()._fetch_one/_fetch_all/
_execute``) used by db/git_integration.py and db/workflow_return_points.py.
"""
from __future__ import annotations

import json
from typing import Any, Iterable, Optional

from .connection import get_store, now_iso

STATE_VALUES = ("no_commit", "live", "canceled")
# P0006 §5-2 closed this set; DB0008 §2-1 mirrors it in a CHECK. Nothing may add a
# seventh code without going back through P.
SKIP_REASONS = (
    "no_changes", "artifacts_only", "git_inactive",
    "no_worktree", "git_busy", "commit_failed",
)
# L0007 §4.1 blocked_reason + its sub-reason, parked on group_git_state because they
# belong to the group, not to one TR row (DB0008 §2-2).
BLOCK_REASONS = ("git_inactive", "already_merged", "no_worktree", "git_busy")


def get_by_id(row_id: int) -> Optional[dict]:
    return get_store()._fetch_one(
        "SELECT * FROM tr_commit_ledger WHERE id = ?", [row_id]
    )


def _latest_for_doc(doc_id: str) -> Optional[dict]:
    return get_store()._fetch_one(
        "SELECT * FROM tr_commit_ledger WHERE doc_id = ? ORDER BY id DESC", [doc_id]
    )


# ── writes (DB0008 §4.1) ──────────────────────────────────────────────────────

def record_commit(
    *, group_id: str, doc_id: str, commit_sha: str, commit_subject: str
) -> Optional[dict]:
    """The approval made a commit — one ``live`` row (DB0008 §4-1 first form)."""
    now = now_iso()
    get_store()._execute(
        "INSERT INTO tr_commit_ledger "
        "(group_id, doc_id, state, commit_sha, commit_subject, cancel_attempt_log, "
        " created_at, updated_at) "
        "VALUES (?, ?, 'live', ?, ?, '[]', ?, ?)",
        [group_id, doc_id, commit_sha, commit_subject, now, now],
    )
    return _latest_for_doc(doc_id)


def record_reapply(
    *, group_id: str, doc_id: str, commit_sha: str, commit_subject: str,
    restored_from_id: int,
) -> Optional[dict]:
    """The forward restore put this TR's source back — a NEW ``live`` row (T0018 K11).

    Deliberately not a state change on the canceled row. That row's entire content is
    "this commit existed and was reverted"; flipping it back to ``live`` would make the
    rewind disappear from the record, which is the history edit D0005 K5 refuses. So the
    ledger grows the same way git does: 085 wrote the commit, the cancel wrote the revert,
    and this writes the revert of the revert, each one still readable afterwards.

    ``restored_from_id`` names the canceled row this one put back. It is what lets a
    screen distinguish "this step was committed" from "this step was committed, reverted
    and restored" — two live rows for one document are otherwise identical.
    """
    now = now_iso()
    get_store()._execute(
        "INSERT INTO tr_commit_ledger "
        "(group_id, doc_id, state, commit_sha, commit_subject, restored_from_id, "
        " cancel_attempt_log, created_at, updated_at) "
        "VALUES (?, ?, 'live', ?, ?, ?, '[]', ?, ?)",
        [group_id, doc_id, commit_sha, commit_subject, int(restored_from_id), now, now],
    )
    return _latest_for_doc(doc_id)


def record_no_commit(
    *, group_id: str, doc_id: str, skip_reason: str
) -> Optional[dict]:
    """The approval made no commit — one ``no_commit`` row carrying why.

    K3 is the whole reason this row exists at all: "the TR changed no source" and
    "the commit could not even be attempted" must stay distinguishable later, when
    a rewind reports that there was nothing to cancel.
    """
    now = now_iso()
    get_store()._execute(
        "INSERT INTO tr_commit_ledger "
        "(group_id, doc_id, state, skip_reason, cancel_attempt_log, created_at, updated_at) "
        "VALUES (?, ?, 'no_commit', ?, '[]', ?, ?)",
        [group_id, doc_id, skip_reason, now, now],
    )
    return _latest_for_doc(doc_id)


# ── reads ─────────────────────────────────────────────────────────────────────

def live_rows(group_id: str, doc_ids: Iterable[str]) -> list[dict[str, Any]]:
    """L0007 §2.2 G1 — the cancel targets, newest commit first.

    Ordered by ``id DESC`` and NOT by seq: a TR that was rewound and re-approved
    has a small seq but a late commit, and reverts must peel the newest one first.
    """
    ids = [d for d in doc_ids if d]
    if not ids:
        return []
    placeholders = ", ".join("?" for _ in ids)
    return get_store()._fetch_all(
        "SELECT id, group_id, doc_id, commit_sha, commit_subject, state "
        "FROM tr_commit_ledger "
        f"WHERE group_id = ? AND state = 'live' AND doc_id IN ({placeholders}) "
        "ORDER BY id DESC",
        [group_id, *ids],
    )


def reappliable_rows(group_id: str, doc_ids: Iterable[str]) -> list[dict[str, Any]]:
    """T0018 K11 — the canceled rows a forward restore looks at, in peel order.

    Returns EVERY canceled row of the region, not only the ones that can be put back,
    with the two disqualifiers carried on the row so the caller can report them instead
    of dropping them. Silent truncation is what L0007 §5 forbids: a step whose source
    stayed reverted must say why, and a row filtered out inside this query has no way to.

    * ``newer_live`` > 0 — a later ``live`` row exists for the same document, i.e. the
      person redid that step by hand after the rewind and its commit is already in the
      tree. Reapplying would apply the same work twice (caller: ``superseded``).
    * ``cancel_commit IS NULL`` — the cancel was an empty revert, so there is no commit to
      peel back off (caller: ``no_cancel_commit``).

    Ordering is the exact inverse of the cancel's, and that one sentence is the whole
    rule: the cancel walked ``id DESC`` and laid its revert commits newest-original-first,
    so the LAST revert it laid down (the row with the SMALLEST id) is the one sitting on
    top of the tree, and that is the one that has to come off first. Across two separate
    rewinds the later batch sits above the earlier one, hence ``canceled_at DESC`` before
    ``id ASC``.
    """
    ids = [d for d in doc_ids if d]
    if not ids:
        return []
    placeholders = ", ".join("?" for _ in ids)
    return get_store()._fetch_all(
        "SELECT l.id, l.group_id, l.doc_id, l.state, l.commit_sha, l.commit_subject, "
        "       l.cancel_commit, l.cancel_reason, l.canceled_at, "
        "       (SELECT COUNT(*) FROM tr_commit_ledger n "
        "          WHERE n.doc_id = l.doc_id AND n.state = 'live' AND n.id > l.id) "
        "         AS newer_live "
        "FROM tr_commit_ledger l "
        f"WHERE l.group_id = ? AND l.state = 'canceled' AND l.doc_id IN ({placeholders}) "
        "ORDER BY l.canceled_at DESC, l.id ASC",
        [group_id, *ids],
    )


def newest_live_id_by_doc(doc_ids: Iterable[str]) -> dict[str, int]:
    """``doc_id -> the id of its newest live row``, for the cancel retry's row filter.

    A document that was re-approved after a failed cancel has TWO live rows: the stale one
    the cancel never managed to revert, and the fresh one the re-approval just committed.
    Only the fresh one is off limits, and this is how the caller names it — see
    :func:`~modules.flow_gate.services.tr_commit_service.cancel_retry`.
    """
    ids = [d for d in doc_ids if d]
    if not ids:
        return {}
    placeholders = ", ".join("?" for _ in ids)
    rows = get_store()._fetch_all(
        "SELECT doc_id, MAX(id) AS newest_id FROM tr_commit_ledger "
        f"WHERE state = 'live' AND doc_id IN ({placeholders}) "
        "GROUP BY doc_id",
        list(ids),
    )
    return {
        row["doc_id"]: int(row["newest_id"])
        for row in rows
        if row.get("doc_id") and row.get("newest_id") is not None
    }


def blocks_by_group(group_ids: Iterable[str]) -> dict[str, dict[str, Any]]:
    """``group_id -> the last time a cancel/reapply gate refused`` (085's three columns).

    One query for the whole Git status panel. The columns are shared by both directions on
    purpose: they answer "why did git refuse this group last time", and the person reading
    the panel needs the same sentence whichever button produced it.
    """
    ids = [g for g in group_ids if g]
    if not ids:
        return {}
    placeholders = ", ".join("?" for _ in ids)
    rows = get_store()._fetch_all(
        "SELECT group_id, last_cancel_block_reason, last_cancel_block_sub, "
        "       last_cancel_block_at "
        f"FROM group_git_state WHERE group_id IN ({placeholders})",
        list(ids),
    )
    return {row["group_id"]: row for row in rows if row.get("group_id")}


def latest_by_doc(doc_ids: Iterable[str]) -> dict[str, dict[str, Any]]:
    """``doc_id -> the newest row that actually made a commit``, for the strip marker.

    One query for the whole strip (the N+1 lesson of 0282 NR0003 발견 1). Ascending
    order makes the last write per doc_id win, i.e. the highest id.

    ``no_commit`` rows are filtered out **in the query**, not after it. Taking the
    newest row of any kind would hide a real commit: a TR that was rewound and
    re-approved while git was busy gets a fresh `no_commit` row on top of its still-live
    commit, and the cell would go blank even though that commit is right there in the
    branch. The marker answers "does this step have a source commit", so the newest row
    that has one is the answer.
    """
    ids = [d for d in doc_ids if d]
    if not ids:
        return {}
    placeholders = ", ".join("?" for _ in ids)
    rows = get_store()._fetch_all(
        "SELECT id, group_id, doc_id, state, commit_sha, commit_subject, "
        "       cancel_commit, cancel_reason, skip_reason, restored_from_id, created_at "
        "FROM tr_commit_ledger "
        f"WHERE doc_id IN ({placeholders}) AND state IN ('live', 'canceled') "
        "ORDER BY id ASC",
        list(ids),
    )
    latest: dict[str, dict[str, Any]] = {}
    for row in rows:
        latest[row["doc_id"]] = row
    return latest


def list_by_group(group_id: str, limit: int = 200) -> list[dict[str, Any]]:
    """A group's ledger, newest first, joined to the document of the moment.

    ``doc_code`` is assembled by the caller from ``seq``/``type_code`` (DB0008 §4-6
    — the three dialects spell string formatting differently and the application
    already assembles the same short code elsewhere).
    """
    return get_store()._fetch_all(
        "SELECT l.id, l.doc_id, l.state, l.commit_sha, l.commit_subject, "
        "       l.skip_reason, l.cancel_commit, l.cancel_reason, l.canceled_at, "
        "       l.restored_from_id, l.created_at, d.seq, d.type_code, d.title "
        "FROM tr_commit_ledger l "
        "LEFT JOIN documents d ON d.doc_id = l.doc_id "
        "WHERE l.group_id = ? "
        "ORDER BY l.id DESC "
        "LIMIT ?",
        [group_id, limit],
    )


def list_by_groups(group_ids: Iterable[str], limit: int = 400) -> list[dict[str, Any]]:
    """Same rows as :func:`list_by_group`, for several groups in ONE query.

    The Git status panel asks for every active slot at once; a per-slot call here is
    the N+1 that 0282 NR0003 발견 1 pulled out of this very screen.
    """
    ids = [g for g in group_ids if g]
    if not ids:
        return []
    placeholders = ", ".join("?" for _ in ids)
    return get_store()._fetch_all(
        "SELECT l.id, l.group_id, l.doc_id, l.state, l.commit_sha, l.commit_subject, "
        "       l.skip_reason, l.cancel_commit, l.cancel_reason, l.canceled_at, "
        "       l.restored_from_id, l.created_at, d.seq, d.type_code, d.title "
        "FROM tr_commit_ledger l "
        "LEFT JOIN documents d ON d.doc_id = l.doc_id "
        f"WHERE l.group_id IN ({placeholders}) "
        "ORDER BY l.id DESC "
        "LIMIT ?",
        [*ids, limit],
    )


def commit_rows_by_group(group_id: str) -> list[dict[str, Any]]:
    """The rewind preview's rows — every round that actually made a commit (DB0008 §4-6).

    ``no_commit`` rows are filtered in the query: the preview draws a step line per
    *commit*, and a round that changed no source is drawn from the absence of a row
    ("소스 변경 없음"), not from a row saying so (P0006 §2 서두).

    Ordered by the document's ``seq`` because the dialog lists steps in workflow order,
    not in commit order — the cancel loop is the one that needs ``id DESC``
    (:func:`live_rows`), and mixing the two orders is how a preview ends up disagreeing
    with what the cancel actually does.
    """
    return get_store()._fetch_all(
        "SELECT l.id, l.doc_id, l.state, l.commit_sha, l.commit_subject, "
        "       l.cancel_commit, l.cancel_reason, l.restored_from_id, d.seq, d.type_code "
        "FROM tr_commit_ledger l "
        "JOIN documents d ON d.doc_id = l.doc_id "
        "WHERE l.group_id = ? AND l.state IN ('live', 'canceled') "
        "ORDER BY d.seq ASC, l.id ASC",
        [group_id],
    )


# ── cancel-side writes (DB0008 §4.3~§4.5, L0007 §4.3 "행마다 즉시") ─────────────

def is_canceled(row_id: int) -> bool:
    """Has this row already been canceled? Checked per row inside the revert loop.

    D0005 K6 forbids a revert of a revert, and the ledger is the only place that
    knows a commit was already peeled off — git itself would happily revert it twice.
    """
    row = get_store()._fetch_one(
        "SELECT state FROM tr_commit_ledger WHERE id = ?", [row_id]
    )
    return bool(row) and row.get("state") == "canceled"


def mark_canceled(
    row_id: int, *, cancel_commit: Optional[str], reason: Optional[str] = None
) -> bool:
    """Write the cancel onto the row it cancels. Returns whether THIS call did it.

    ``WHERE state = 'live'`` closes the window between the caller's
    :func:`is_canceled` check and this statement (DB0008 §4-3). ``_execute`` reports
    no row count, so the outcome is read back from the row: if it now carries our
    cancel commit (or our ``empty_revert`` reason) the write was ours, and if another
    writer got there first the caller reclassifies the row as ``already_canceled``
    instead of claiming a cancel it did not make.

    The row is never deleted and ``commit_sha`` is left intact — "this commit existed
    and was reverted" is the fact being recorded (D0005 K5).
    """
    now = now_iso()
    get_store()._execute(
        "UPDATE tr_commit_ledger "
        "SET state = 'canceled', cancel_commit = ?, cancel_reason = ?, "
        "    canceled_at = ?, updated_at = ? "
        "WHERE id = ? AND state = 'live'",
        [cancel_commit, reason, now, now, row_id],
    )
    row = get_by_id(row_id)
    if not row or row.get("state") != "canceled":
        return False
    if cancel_commit is not None:
        return row.get("cancel_commit") == cancel_commit
    return row.get("cancel_reason") == reason and not row.get("cancel_commit")


def record_cancel_attempt(row_id: int, *, failed_reason: str) -> None:
    """Append one failed cancel attempt to the row's log; the row stays ``live``.

    The response collapses every revert failure into one ``conflict`` code (P0006
    §5-4 closed that set), so the distinction between a conflict, a failed commit and
    a timeout has to live somewhere — it lives here, per attempt, with a timestamp.

    Read-modify-write in Python rather than a dialect JSON function: SQLite, Postgres
    and MySQL spell array append three different ways, and this row is already
    serialized by the project git lock (DB0008 §4-4).
    """
    row = get_by_id(row_id)
    if not row:
        return
    try:
        log = json.loads(row.get("cancel_attempt_log") or "[]")
        if not isinstance(log, list):
            log = []
    except (TypeError, ValueError):
        log = []
    now = now_iso()
    log.append({"at": now, "reason": failed_reason})
    get_store()._execute(
        "UPDATE tr_commit_ledger SET cancel_attempt_log = ?, updated_at = ? WHERE id = ?",
        [json.dumps(log, ensure_ascii=False), now, row_id],
    )


def record_block(group_id: str, reason: str, sub: Optional[str] = None) -> None:
    """The group-level "the cancel gate refused, and why" (DB0008 §4-5).

    Only the three diagnostic columns are written — never ``status`` — so recording
    why a cancel was skipped can never move the group's git state. A group that never
    enabled git has no ``group_git_state`` row and this quietly updates nothing, which
    is correct: there is no git state to annotate (DB0008 §5-2).
    """
    get_store()._execute(
        "UPDATE group_git_state "
        "SET last_cancel_block_reason = ?, last_cancel_block_sub = ?, "
        "    last_cancel_block_at = ? "
        "WHERE group_id = ?",
        [reason, sub, now_iso(), group_id],
    )


def counts_by_group(group_ids: Iterable[str]) -> dict[str, dict[str, int]]:
    """``group_id -> {live, canceled, no_commit}`` in one query (panel badges)."""
    ids = [g for g in group_ids if g]
    if not ids:
        return {}
    placeholders = ", ".join("?" for _ in ids)
    rows = get_store()._fetch_all(
        "SELECT group_id, state, COUNT(*) AS n FROM tr_commit_ledger "
        f"WHERE group_id IN ({placeholders}) "
        "GROUP BY group_id, state",
        list(ids),
    )
    counts: dict[str, dict[str, int]] = {}
    for row in rows:
        bucket = counts.setdefault(
            row["group_id"], {"live": 0, "canceled": 0, "no_commit": 0}
        )
        state = row.get("state")
        if state in bucket:
            bucket[state] = int(row.get("n") or 0)
    return counts
