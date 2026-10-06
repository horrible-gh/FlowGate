"""Command + run change activity of a CH and its conversation anchors (D0006 §3.9).

Command requests and run change summaries are stored apart from the conversation
turns. Their place in the conversation is a persisted, server-decided anchor, so a
screen never guesses it from ``created_at`` or from whatever page of turns it holds:

    anchor_seq       the turn the row is placed against (0 = before turn 1)
    anchor_position  ``before`` or ``after`` that turn
    anchor_state     reply      the run's AI reply turn exists
                                (command -> before the first reply, change -> after the last)
                     run_start  no reply (yet): after the conversation head the run started from
                     ambiguous  start unknown and several AI turns claim the run
                     unresolved start unknown and no AI turn claims the run

The anchor is decided when a row is written, again when the run's AI reply turn is
appended (:func:`on_turn_appended`), and repaired on every activity read
(:func:`reconcile`) and at startup. Every update is a compare-and-set on the anchor
that was read, and a ``reply`` anchor never falls back to ``run_start``, so a stale
resolver cannot overwrite a newer answer.
"""
from __future__ import annotations

import logging
from typing import Iterable, Optional

from flowgate.conversation import contracts as c
from flowgate.conversation import legacy_adapter
from flowgate.conversation import repository as repo

logger = logging.getLogger(__name__)

KIND_COMMAND = "command"
KIND_CHANGE = "change"

# Immediate attempts of the post-commit re-anchor before leaving it to ``reconcile``.
HOOK_ATTEMPTS = 2

_RANK = {None: 0, c.ANCHOR_UNRESOLVED: 1, c.ANCHOR_RUN_START: 1, c.ANCHOR_REPLY: 2, c.ANCHOR_AMBIGUOUS: 3}

Anchor = tuple[Optional[int], Optional[str], str]


def compute(kind: str, reply_seqs: Iterable[int], run_start_seq: Optional[int]) -> Anchor:
    """(anchor_seq, anchor_position, anchor_state) of one activity row."""
    seqs = sorted({int(seq) for seq in reply_seqs})
    if run_start_seq is None:
        # Start unknown: only a single unambiguous reply is trusted.
        if len(seqs) == 1:
            return _reply_anchor(kind, seqs)
        return (None, None, c.ANCHOR_AMBIGUOUS if seqs else c.ANCHOR_UNRESOLVED)
    if seqs:
        return _reply_anchor(kind, seqs)
    return (int(run_start_seq), c.ANCHOR_AFTER, c.ANCHOR_RUN_START)


def _reply_anchor(kind: str, seqs: list[int]) -> Anchor:
    if kind == KIND_COMMAND:
        return (seqs[0], c.ANCHOR_BEFORE, c.ANCHOR_REPLY)
    return (seqs[-1], c.ANCHOR_AFTER, c.ANCHOR_REPLY)


def should_replace(kind: str, current: dict, new: Anchor) -> bool:
    """True when ``new`` is a forward move from the stored anchor of ``current``."""
    old_seq = current.get("anchor_seq")
    old = (None if old_seq is None else int(old_seq), current.get("anchor_position"), current.get("anchor_state"))
    if old == new:
        return False
    old_rank, new_rank = _RANK.get(old[2], 0), _RANK.get(new[2], 0)
    if new_rank != old_rank:
        return new_rank > old_rank
    if old[2] == new[2] == c.ANCHOR_REPLY and old[0] is not None and new[0] is not None:
        # Reply turns are append-only: the first reply never moves, the last only forward.
        return new[0] < old[0] if kind == KIND_COMMAND else new[0] > old[0]
    return old[2] is None


def run_start_seq(doc_id: Optional[str]) -> Optional[int]:
    """Conversation head of ``doc_id`` now -- the turn a starting chat run follows.

    None when it cannot be read; the row is then anchored by its reply alone (or stays
    ``unresolved``), which is the honest answer.
    """
    if not doc_id:
        return None
    try:
        return legacy_adapter.current_head_seq(doc_id)
    except Exception:
        logger.warning("chat run start seq read failed for %s", doc_id, exc_info=True)
        return None


def resolve_commands(doc_id: str, run_id: str) -> int:
    rows = repo.command_anchor_rows(doc_id, run_id)
    if not rows:
        return 0
    replies = repo.reply_seqs(doc_id, run_id)
    moved = 0
    for row in rows:
        new = compute(KIND_COMMAND, replies, row.get("run_start_seq"))
        if should_replace(KIND_COMMAND, row, new):
            moved += repo.set_command_anchor(row["request_id"], new, row)
    return moved


def resolve_change(doc_id: str, run_id: str) -> int:
    row = repo.run_change_anchor_row(doc_id, run_id)
    if row is None:
        return 0
    new = compute(KIND_CHANGE, repo.reply_seqs(doc_id, run_id), row.get("run_start_seq"))
    if not should_replace(KIND_CHANGE, row, new):
        return 0
    return repo.set_run_change_anchor(run_id, new, row)


def resolve_run(doc_id: str, run_id: str) -> int:
    """Re-decide every activity row of one run; how many rows moved."""
    return resolve_commands(doc_id, run_id) + resolve_change(doc_id, run_id)


def on_turn_appended(doc_id: str, turn: dict) -> int:
    """Post-commit hook of a new turn. Only an AI turn written by a run can move rows."""
    run_id = (turn or {}).get("source_run_id")
    if (turn or {}).get("speaker") != "ai" or not run_id:
        return 0
    for attempt in range(1, HOOK_ATTEMPTS + 1):
        try:
            return resolve_run(doc_id, str(run_id))
        except Exception:
            # The turn is committed; the append never fails for this. Resolving is
            # idempotent and CAS-guarded, so retrying is safe, and a row still left
            # behind is found again by ``reconcile``.
            logger.warning("chat activity anchor resolve failed doc=%s run=%s attempt=%d/%d",
                           doc_id, run_id, attempt, HOOK_ATTEMPTS, exc_info=True)
    return 0


def reconcile(doc_id: Optional[str] = None) -> int:
    """Re-resolve every run whose rows are undecided or behind their recorded replies, in
    one doc or (``None``) all docs. A failing run is logged and skipped."""
    moved = 0
    for list_runs, resolve in ((repo.command_runs_to_resolve, resolve_commands),
                               (repo.run_change_runs_to_resolve, resolve_change)):
        try:
            runs = list_runs(doc_id)
        except Exception:
            logger.warning("chat activity anchor reconcile scan failed doc=%s", doc_id, exc_info=True)
            continue
        for row in runs:
            try:
                moved += resolve(row["doc_id"], row["ai_run_id"])
            except Exception:
                logger.warning("chat activity anchor reconcile failed doc=%s run=%s",
                               row["doc_id"], row["ai_run_id"], exc_info=True)
    return moved


def readable_chat_document(doc_id: str, user: dict) -> dict:
    """The CH a user may read activity of: 404 when it is not a CH, 403 without read permission."""
    doc = legacy_adapter.get_document(doc_id)
    if not doc or str(doc.get("type_code") or doc.get("type") or "").upper() != c.CHAT_TYPE_CODE:
        raise c.ConversationError(404, c.ERR_CHAT_DOCUMENT_NOT_FOUND, "chat document not found")
    if not legacy_adapter.has_permission(user, "perm_document_read", doc.get("project_id")):
        raise c.ConversationError(403, c.ERR_ACTIVITY_FORBIDDEN, "no read permission for this document")
    return doc


def list_for_doc(doc_id: str, *, from_seq: Optional[int] = None, to_seq: Optional[int] = None,
                 include_unplaced: bool = True) -> dict:
    """Commands and run changes of one CH, each with its anchor (reconciled first)."""
    from flowgate.conversation import commands, run_changes

    reconcile(doc_id)
    window = {"from_seq": from_seq, "to_seq": to_seq, "include_unplaced": include_unplaced}
    return {
        "ok": True, "doc_id": doc_id, "from_seq": from_seq, "to_seq": to_seq,
        "commands": [commands.public(row) for row in repo.list_commands_for_doc(doc_id, **window)],
        "changes": [run_changes.public(row) for row in repo.list_run_changes_for_doc(doc_id, **window)],
    }
