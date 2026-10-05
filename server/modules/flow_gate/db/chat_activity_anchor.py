"""Conversation position of chat activity rows (flowgate.default.0675 T0004 §2-3/§2-5).

Command requests (``chat_command_requests``) and run change summaries
(``ai_run_source_changes``) are stored apart from the conversation turns. Their place
in the conversation is a server-decided, persisted anchor so a screen never has to
guess it from whatever page of turns it happens to hold:

    anchor_seq       the conversation turn the row is placed against (0 = before turn 1)
    anchor_position  'before' or 'after' that turn
    anchor_state     how the anchor was decided:
                       reply      the run's AI reply turn exists
                                  (command -> before the first reply, change -> after the last)
                       run_start  no reply (yet); placed after the conversation head the run
                                  started from (``run_start_seq``)
                       ambiguous  legacy row: several AI turns claim the run, start unknown
                       unresolved legacy row: no AI turn claims the run, start unknown

This module is the one rule both tables use. It never looks a store up itself (the
caller passes its own), so the writers (row insert, reply-turn append, startup backfill) all compute the same answer
from the same facts, and a retried or duplicated event recomputes, never re-guesses.
``created_at`` is never used to infer a position (NR0003 §7.2).

Monotonicity: a reply turn is append-only, so once a row is ``reply`` it can never
legitimately fall back to ``run_start``. ``should_replace`` refuses such downgrades so
a resolver that read the turns before a concurrent reply commit cannot overwrite the
newer answer written by the reply's own resolver.
"""
from __future__ import annotations

from typing import Any, Iterable, Optional

KIND_COMMAND = "command"
KIND_CHANGE = "change"

POSITIONS = ("before", "after")
STATES = ("reply", "run_start", "ambiguous", "unresolved")
UNPLACED_STATES = ("ambiguous", "unresolved")

_RANK = {None: 0, "unresolved": 1, "run_start": 1, "reply": 2, "ambiguous": 3}

Anchor = tuple[Optional[int], Optional[str], str]


def compute(kind: str, reply_seqs: Iterable[int], run_start_seq: Optional[int]) -> Anchor:
    """(anchor_seq, anchor_position, anchor_state) for one activity row."""
    seqs = sorted({int(seq) for seq in reply_seqs})
    if run_start_seq is None:
        # Legacy / start unknown: only a single unambiguous reply is trusted.
        if len(seqs) == 1:
            return _reply_anchor(kind, seqs)
        return (None, None, "ambiguous" if seqs else "unresolved")
    if seqs:
        return _reply_anchor(kind, seqs)
    return (int(run_start_seq), "after", "run_start")


def _reply_anchor(kind: str, seqs: list[int]) -> Anchor:
    if kind == KIND_COMMAND:
        return (seqs[0], "before", "reply")
    return (seqs[-1], "after", "reply")


def should_replace(kind: str, current: dict, new: Anchor) -> bool:
    """True when ``new`` is a forward move from the stored anchor of ``current``."""
    old = (current.get("anchor_seq"), current.get("anchor_position"), current.get("anchor_state"))
    old = (None if old[0] is None else int(old[0]), old[1], old[2])
    if old == new:
        return False
    old_rank, new_rank = _RANK.get(old[2], 0), _RANK.get(new[2], 0)
    if new_rank != old_rank:
        return new_rank > old_rank
    if old[2] == new[2] == "reply" and old[0] is not None and new[0] is not None:
        # More reply turns only ever appear later: the first reply never moves and the
        # last reply only moves forward.
        return new[0] < old[0] if kind == KIND_COMMAND else new[0] > old[0]
    return old[2] is None


def reply_seqs(store: Any, doc_id: str, run_id: str) -> list[int]:
    """Seqs of the AI turns the run itself appended (``source_run_id`` is set from the
    run's own token, so this is a recorded relation, not a guess)."""
    rows = store._fetch_all(
        "SELECT seq FROM conversation_turns WHERE doc_id = ? AND source_run_id = ? "
        "AND speaker = 'ai' ORDER BY seq ASC",
        [doc_id, run_id],
    )
    return [int(row["seq"]) for row in rows]


def needs_resolve(kind: str, alias: str, run_column: str) -> str:
    """WHERE fragment for rows whose stored anchor is not (or no longer) the answer the
    recorded AI turns give -- the rows a resolver must (re)visit:

    * no decided state yet (written before migration 137, or the insert-time resolve failed);
    * ``run_start`` / ``unresolved`` while an AI turn of the run is already recorded
      (the reply-time resolve after that turn's commit failed);
    * ``reply`` while a recorded reply lies on the side the anchor still has to move to
      (command: an earlier first reply, change: a later last reply).

    Once resolved a row leaves this set, so revisiting it is cheap and idempotent.
    """
    turns = (f"SELECT 1 FROM conversation_turns t WHERE t.doc_id = {alias}.doc_id "
             f"AND t.source_run_id = {alias}.{run_column} AND t.speaker = 'ai'")
    moved = "t.seq < {a}.anchor_seq" if kind == KIND_COMMAND else "t.seq > {a}.anchor_seq"
    return (f"({alias}.anchor_state IS NULL"
            f" OR ({alias}.anchor_state IN ('run_start', 'unresolved') AND EXISTS ({turns}))"
            f" OR ({alias}.anchor_state = 'reply' AND EXISTS ({turns} AND {moved.format(a=alias)})))")


def cas_guard(current: dict) -> tuple[str, list]:
    """WHERE fragment matching exactly the anchor the resolver read."""
    parts: list[str] = []
    params: list = []
    for column in ("anchor_seq", "anchor_state"):
        value = current.get(column)
        if value is None:
            parts.append(f"{column} IS NULL")
        else:
            parts.append(f"{column} = ?")
            params.append(value)
    return " AND ".join(parts), params


def range_filter(base: str, params: list, from_seq: Optional[int], to_seq: Optional[int],
                 include_unplaced: bool) -> tuple[str, list]:
    """Restrict a doc query to rows anchored in [from_seq, to_seq] (either end open)."""
    if from_seq is None and to_seq is None:
        return base, list(params)
    conds: list[str] = []
    extra: list = []
    if from_seq is not None:
        conds.append("anchor_seq >= ?")
        extra.append(int(from_seq))
    if to_seq is not None:
        conds.append("anchor_seq <= ?")
        extra.append(int(to_seq))
    window = " AND ".join(conds)
    if include_unplaced:
        window = f"(({window}) OR anchor_seq IS NULL)"
    return f"{base} AND {window}", [*params, *extra]


def summarize_counts(rows: Iterable[dict]) -> dict:
    """``summarize`` over ``SELECT anchor_state, COUNT(*) AS total ... GROUP BY`` rows."""
    counts = summarize(())
    for row in rows:
        state = row.get("anchor_state")
        total = int(row.get("total") or 0)
        counts["total"] += total
        counts[state if state in STATES else "pending"] += total
    return counts


def summarize(states: Iterable[Optional[str]]) -> dict:
    """Counts by state, for the backfill report (T0004 §2-5 item 5)."""
    counts = {"total": 0, "reply": 0, "run_start": 0, "ambiguous": 0, "unresolved": 0, "pending": 0}
    for state in states:
        counts["total"] += 1
        counts[state if state in STATES else "pending"] += 1
    return counts
