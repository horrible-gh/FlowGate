"""question_items CRUD — follows the sqloader.load pattern.

Inline SQL is prohibited. Only use SQL registered in queries.json.
"""
from __future__ import annotations

from typing import Optional

from .connection import get_store


def get_by_pk(pk: int) -> Optional[dict]:
    """Return the question_items row by PK(id)."""
    store = get_store()
    return store._fetch_one(store._sql("question_items.get_question_item_by_pk"), [pk])


def list_by_question(question_pk: int) -> list[dict]:
    """List all items for the question PK (seq ASC)."""
    store = get_store()
    return store._fetch_all(store._sql("question_items.get_question_items"), [question_pk])


def list_unanswered(question_pk: int) -> list[dict]:
    """List unanswered items where answer_count = 0 (seq ASC)."""
    store = get_store()
    return store._fetch_all(store._sql("question_items.get_unanswered_items"), [question_pk])


def get_max_seq(question_pk: int) -> int:
    """Return the maximum seq for the question (0 if there are no items)."""
    store = get_store()
    row = store._fetch_one(store._sql("question_items.get_max_seq"), [question_pk])
    return row.get("max_seq", 0) if row else 0


def insert(
    question_pk: int,
    seq: int,
    body: str,
    title: Optional[str] = None,
    asker_kind: str = "human",
    options: str = "[]",
    asker_ai_run_id: Optional[str] = None,
    asker_actual_provider_id: Optional[str] = None,
    asker_actual_provider_name: Optional[str] = None,
) -> None:
    """question_items INSERT (DB0006 §3.3 — title + asker_kind; DB0007 §4 — options).

    ``options`` is the serialized [{"id", "label"}] JSON array (DB0007 §2); the caller
    validates and serializes it (L0008 §2.2/§2.3).

    The three ``asker_*`` columns are the AI run/provider snapshot for an AI-registered
    question (0582 T0005 §4/§D) — always None for a human [+query], and None for an AI
    question whose token carried no bound run (legacy/external — the caller could not
    resolve one, not that one was silently dropped here).
    """
    store = get_store()
    store._execute(
        store._sql("question_items.insert_question_item"),
        [
            question_pk, seq, title, body, asker_kind, options,
            asker_ai_run_id, asker_actual_provider_id, asker_actual_provider_name,
        ],
    )


def increment_answer_count(pk: int) -> bool:
    """answer_count += 1 for every writer other than the item's own responder run.

    An item the automatic responder has touched (responder_state set) takes ONE answer:
    the write is a CAS on answer_count = 0 there, so a person's answer that arrives just
    after the responder's committed one changes nothing (review of 0661 TR0005 rev1).
    An item no responder ever touched keeps the old multi-answer behaviour. Returns
    whether the row changed.
    """
    store = get_store()
    affected = store._execute_affected(
        store._sql("question_items.increment_answer_count"), [pk],
    )
    return affected > 0


def increment_answer_count_for_responder(pk: int, run_id: str) -> bool:
    """answer_count += 1 as a CAS for the item's own responder run (0661 T0004).

    Changes the row only while the item is still unanswered, still owned by ``run_id`` and
    not handed to a user ('user_decision'). Returns whether the row changed — False means
    the responder lost the race to a person's answer, or escalated, and must not answer.
    """
    store = get_store()
    affected = store._execute_affected(
        store._sql("question_items.increment_answer_count_for_responder"), [pk, run_id],
    )
    return affected > 0


# ── Automatic responder state (flowgate.default.0661 T0004, migration 142) ───────────
#
# One durable state machine per item, so "who is answering this, and if nobody, why not"
# survives the process and can be read by the Q&A panel (0003-NR F3/F5):
#   NULL -> 'dispatched' -> 'answered' | 'failed' | 'user_decision'
# The transitions are compare-and-swap writes keyed on the responder run that owns the
# item, so a late finalization of a superseded run cannot overwrite a newer run's state.

RESPONDER_DISPATCHED = "dispatched"
RESPONDER_ANSWERED = "answered"
RESPONDER_FAILED = "failed"
RESPONDER_USER_DECISION = "user_decision"


def claim_responder_dispatch(
    pk: int,
    run_id: str,
    requested_provider_id: Optional[str] = None,
    provider_source: Optional[str] = None,
) -> bool:
    """Mark the item as being answered by ``run_id`` (state 'dispatched').

    Idempotent for the same run (a token re-issue inside one run does not count twice)
    and refused for an item that already carries an answer. Returns whether the row
    changed.
    """
    store = get_store()
    affected = store._execute_affected(
        store._sql("question_items.claim_responder_dispatch"),
        [run_id, requested_provider_id, provider_source, pk, run_id],
    )
    return affected > 0


def set_responder_state(
    pk: int,
    state: str,
    error_code: Optional[str] = None,
    error_message: Optional[str] = None,
    *,
    run_id: Optional[str] = None,
) -> bool:
    """Move the item to ``state``. With ``run_id`` the write is a CAS on the owning run
    (a run that no longer owns the item changes nothing). Returns whether a row changed."""
    store = get_store()
    if run_id is None:
        affected = store._execute_affected(
            store._sql("question_items.set_responder_state"),
            [state, error_code, error_message, pk],
        )
    else:
        affected = store._execute_affected(
            store._sql("question_items.set_responder_state_for_run"),
            [state, error_code, error_message, pk, run_id],
        )
    return affected > 0


def fail_responder(
    pk: int,
    error_code: Optional[str],
    error_message: Optional[str],
    *,
    run_id: Optional[str] = None,
    observed_state: Optional[str] = None,
    observed_run_id: Optional[str] = None,
) -> bool:
    """Move the item to 'failed' as one CAS (0661 TR0005 rev1/rev2 review).

    Both forms refuse an item that already carries an answer (answer_count = 0 is in the
    WHERE, not a prior read), so a person's answer that commits between the caller's
    check and this write is never relabelled a technical failure. With ``run_id`` only
    that run's still 'dispatched' claim can fail. Without ``run_id`` (an admission
    refusal, which never owned the item) the write only replaces an untouched (NULL) or
    already 'failed' state, and only while the state and responder run are still the
    ``observed_state`` / ``observed_run_id`` the caller read: a responder that claimed the
    item after that read ('dispatched', new run id) and a 'user_decision' hand-off are
    never overwritten. Returns whether the row changed.
    """
    store = get_store()
    if run_id is None:
        affected = store._execute_affected(
            store._sql("question_items.fail_responder"),
            [error_code, error_message, pk, observed_state or "", observed_run_id or ""],
        )
    else:
        affected = store._execute_affected(
            store._sql("question_items.fail_responder_for_run"),
            [error_code, error_message, pk, run_id],
        )
    return affected > 0


def escalate_responder(
    pk: int,
    run_id: str,
    error_code: Optional[str],
    error_message: Optional[str],
) -> bool:
    """Move the item to 'user_decision' as one CAS: only ``run_id``'s own 'dispatched'
    claim on a still unanswered item. Returns whether the row changed — False means a
    person answered first, another run owns it, or it is no longer 'dispatched'."""
    store = get_store()
    affected = store._execute_affected(
        store._sql("question_items.escalate_responder_for_run"),
        [error_code, error_message, pk, run_id],
    )
    return affected > 0


def list_by_responder_run(run_id: str) -> list[dict]:
    """Items whose latest responder run is ``run_id`` (seq ASC) — normally one."""
    store = get_store()
    return store._fetch_all(store._sql("question_items.list_by_responder_run"), [run_id])


def list_dispatched() -> list[dict]:
    """Every item still marked 'dispatched' (restart recovery sweep)."""
    store = get_store()
    return store._fetch_all(store._sql("question_items.list_dispatched_responder_items"), [])


def list_by_doc(doc_id: str) -> list[dict]:
    """List question_items for a document's container (seq ASC) — DB0006 §5.2."""
    store = get_store()
    return store._fetch_all(store._sql("question_items.list_by_doc"), [doc_id])


def qa_bundle_by_doc(doc_id: str) -> list[dict]:
    """Flattened question + answer rows for a document (the mention-assembly data provider, L0007 §6).

    Each row: {seq, title, body, asker_kind, options, author_kind, answer_body,
    answer_selected_options}. A question with no answer yields one row with
    answer_body/author_kind = NULL (LEFT JOIN).
    """
    store = get_store()
    return store._fetch_all(store._sql("question_items.qa_bundle_by_doc"), [doc_id])
