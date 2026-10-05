"""Conversation anchors of chat activity (flowgate.default.0675 T0004 §2-3 / §2-5).

The rule itself lives in ``db.chat_activity_anchor``; each table's writer applies it
to its own rows on insert. This module covers the two moments that are not a row
insert:

* an AI reply turn of a run is appended -> that run's command and change rows move to
  the reply (``on_turn_appended``, called by ``conversation_turn_service.append_turn``
  after commit and before the turn is broadcast, so a screen that re-reads activity on
  that turn already sees the new anchor);
* startup -> rows written before the anchor columns existed are decided once
  (``backfill``), with the per-state counts before and after returned for the log;
* a re-anchor that failed anyway (the hook is post-commit and must not fail the append)
  leaves a row behind its recorded reply. ``reconcile`` finds exactly those rows again
  from the stored facts (``chat_activity_anchor.needs_resolve``) -- at startup for every
  doc, and on each activity read for that doc -- so a reconnecting or re-entering screen
  never reads the stale position even when no later writer of that run ever comes.
"""
from __future__ import annotations

import logging
from typing import Optional

from modules.flow_gate.db import ai_run_source_changes as db_changes
from modules.flow_gate.db import chat_command_requests as db_commands

logger = logging.getLogger(__name__)

# Immediate attempts of the post-commit re-anchor before leaving it to ``reconcile``.
HOOK_ATTEMPTS = 2


def run_start_seq(doc_id: Optional[str]) -> Optional[int]:
    """Conversation head of ``doc_id`` right now -- the turn a starting chat run follows.

    None when it cannot be read: the row then stays anchored by its reply alone (or
    ``unresolved``), which is the honest answer, never a guess.
    """
    if not doc_id:
        return None
    try:
        from modules.flow_gate.db import conversation_turns

        return conversation_turns.current_head_seq(doc_id)
    except Exception:
        logger.warning("chat run start seq read failed for %s", doc_id, exc_info=True)
        return None


def resolve_run(doc_id: str, run_id: str) -> int:
    """Re-decide every activity row of one run; how many rows moved."""
    return db_commands.resolve_anchors_for_run(doc_id, run_id) + db_changes.resolve_anchor_for_run(doc_id, run_id)


def on_turn_appended(doc_id: str, turn: dict) -> int:
    """Post-commit hook of a new turn. Only an AI turn written by a run can move rows."""
    run_id = turn.get("source_run_id")
    if turn.get("speaker") != "ai" or not run_id:
        return 0
    for attempt in range(1, HOOK_ATTEMPTS + 1):
        try:
            return resolve_run(doc_id, str(run_id))
        except Exception:
            # The turn is already committed, so the append never fails for this. A
            # resolve is idempotent and CAS-guarded, so retrying is safe; a row still
            # left behind is found again by ``reconcile`` (next activity read / startup).
            logger.warning("chat activity anchor resolve failed doc=%s run=%s attempt=%d/%d",
                           doc_id, run_id, attempt, HOOK_ATTEMPTS, exc_info=True)
    return 0


def reconcile(doc_id: Optional[str] = None) -> int:
    """Re-resolve every run whose rows are undecided or behind their recorded replies,
    in one doc or (``None``) all docs; how many rows moved. A failing run is logged and
    skipped so it never blocks the others (or the read that called this)."""
    moved = 0
    for list_runs, resolve in ((db_commands.list_runs_to_resolve, db_commands.resolve_anchors_for_run),
                               (db_changes.list_runs_to_resolve, db_changes.resolve_anchor_for_run)):
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


def counts() -> dict:
    return {"commands": db_commands.anchor_state_counts(), "changes": db_changes.anchor_state_counts()}


def backfill() -> dict:
    """Decide every row still without an anchor state, and repair every row a failed
    reply-time re-anchor left behind (``reconcile``). Idempotent; returns
    ``{"before": counts, "after": counts, "resolved": n}``."""
    before = counts()
    resolved = reconcile()
    return {"before": before, "after": counts(), "resolved": resolved}
