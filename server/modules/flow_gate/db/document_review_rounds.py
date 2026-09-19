"""Semantic claims for one AI review round (flowgate.default.0583 T0004).

A review round is ``(review_run_id, doc_id, revision_no)``: the server-owned ai-invoke
run that is reviewing, the document, and the revision it reviewed.  Two submissions that
share all three belong to the SAME round however they differ otherwise --
``attempt_no`` names a provider launch rather than a round (0486 NR0028 F1), and a
requested/actual provider difference is a fallback inside one round, not a new one.  An
explicit rerun is a different run id, so it keeps its own claim and its own row.

The table's PRIMARY KEY *is* the barrier.  ``claim()`` runs inside the same transaction
as the ``document_reviews`` INSERT it protects, so "this round is claimed" and "this
round has a durable verdict" are one fact: a rolled-back registration leaves neither
behind, and two review tokens racing the same round cannot both commit.

Why a separate table instead of a UNIQUE index on ``document_reviews``: existing
deployments already carry duplicate review history written before this barrier (T0004
§2), so an index added to that table could fail to build at boot.  Legacy / manual /
copy-mention rows -- the ones with ``review_run_id`` NULL -- stay outside it entirely
(T0004 §3.3).

That table being separate makes the deployment boundary its own problem, so the barrier
reads BOTH records of a round: the claim, and the ``document_reviews`` row that names the
same identity.  Migration 113 backfills every non-NULL ``(run, doc, revision)`` identity
that already exists, and :func:`existing_verdict` covers the rest of the boundary -- a row
an old process writes during a rolling deploy, after 113 already ran, carries no claim and
would otherwise let a second verdict through.
"""
from __future__ import annotations

from typing import Optional

from .connection import get_store, now_iso


class RoundAlreadyClaimed(Exception):
    """This review round is already registered, so its verdict is already durable."""


def get(review_run_id: str, doc_id: str, revision_no: int) -> Optional[dict]:
    """The claim row for one round, or None."""
    return get_store()._fetch_one(
        "SELECT * FROM document_review_round_claims "
        "WHERE review_run_id = ? AND doc_id = ? AND revision_no = ?",
        [review_run_id, doc_id, int(revision_no)],
    )


def existing_verdict(
    review_run_id: str, doc_id: str, revision_no: int
) -> Optional[dict]:
    """The verdict row this round already registered in ``document_reviews``, or None.

    The second source of the barrier, and the one that closes the deployment boundary.
    The claim table is born empty: it cannot answer for a round whose verdict was written
    before the deployment that created it, nor for one an old process writes after
    migration 113 has already run.  A review token issued before the deploy and submitted
    after it addresses exactly such a round, and ``document_reviews`` is the durable
    record of the same ``(review_run_id, doc_id, revision_no)`` identity, so it is read
    alongside the claim rather than trusted to have a claim beside it.
    """
    from . import document_reviews

    return document_reviews.get_for_round(doc_id, revision_no, review_run_id)


def is_registered(review_run_id: str, doc_id: str, revision_no: int) -> bool:
    """True when this round already holds a durable verdict, by either record.

    Used at the pre-write check and again after a rollback to tell a concurrent (or
    already-committed) winner apart from a real DB fault.
    """
    if get(review_run_id, doc_id, revision_no) is not None:
        return True
    return existing_verdict(review_run_id, doc_id, revision_no) is not None


def claim(
    review_run_id: str,
    doc_id: str,
    revision_no: int,
    token_id: Optional[str] = None,
) -> None:
    """Reserve one review round, or raise :class:`RoundAlreadyClaimed`.

    Call this inside the registration transaction, immediately before the review INSERT.

    A plain INSERT against the composite PRIMARY KEY is the whole mechanism: it is
    atomic on all three backends with no dialect-specific upsert syntax, and it needs no
    "SELECT then INSERT" window for a second writer to slip through.  Every driver error
    is translated to ``RoundAlreadyClaimed`` because a PostgreSQL failure aborts the
    surrounding transaction and the statement that failed can no longer be interrogated
    from inside it; the caller re-reads with :func:`is_registered` AFTER the rollback and
    only then decides between "a winner already owns this round" and a real DB fault.
    That is the same rollback-then-classify shape the review receipt CAS already uses.
    """
    if existing_verdict(review_run_id, doc_id, revision_no) is not None:
        # Absorb a round whose verdict is durable but unclaimed (the deployment
        # boundary, see :func:`existing_verdict`). Read inside the caller's
        # transaction, so it sees the same committed state the INSERT below would
        # have to live with. It does not weaken the PRIMARY KEY: two racing
        # submissions both pass this read and the key still picks one winner -- this
        # only adds the rows the key cannot see because they predate it.
        raise RoundAlreadyClaimed(
            f"round {review_run_id}/{doc_id}/{revision_no} already holds a verdict"
        )
    now = now_iso()
    try:
        get_store()._execute(
            "INSERT INTO document_review_round_claims "
            "(review_run_id, doc_id, revision_no, token_id, claimed_at, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            [review_run_id, doc_id, int(revision_no), token_id, now, now],
        )
    except Exception as exc:  # noqa: BLE001 -- classified by the caller after rollback
        raise RoundAlreadyClaimed(str(exc)) from exc
