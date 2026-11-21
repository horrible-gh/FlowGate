"""Independently selectable scenarios; shared fixtures remain in test_workflow_duplicate_slot_repair_0457.py."""
from __future__ import annotations

from tests.modules.flow_gate.test_workflow_duplicate_slot_repair_0457 import (
    EARLY_SLOT,
    GROUP_ID,
    GUARDS,
    INCIDENT_DOCS,
    INCIDENT_LEDGER,
    INCIDENT_SLOTS,
    LATE_SLOT,
    PROJECT_ID,
    Path,
    REPAIR_MIGRATION,
    ROOT,
    SEQUENCE_ID,
    SHARED_DOC,
    SLOT_FOUR,
    SLOT_SIX,
    TR_FOUR,
    TR_SIX,
    USER_ID,
    _Backend,
    _MIGRATIONS,
    _QUERIES,
    _QUERIES_JSON,
    _SERVER_DIR,
    _TXN,
    _Txn,
    _UNORDERED_LOOKUP,
    _apply_repair,
    _apply_repair_update_only,
    _build_db,
    _conflict_events,
    _documents,
    _duplicates,
    _indexes,
    _ledger,
    _migration_files,
    _register,
    _repair_sql,
    _run_statements,
    _seed_disagreeing_row_order,
    _seed_incident,
    _slot,
    _slots,
    _statements,
    _store_over,
    _strip_comments,
    contextmanager,
    incident_db,
    json,
    os,
    pytest,
    re,
    repaired_db,
    sqlite3,
    sys,
)

def test_the_constraint_refuses_to_build_over_an_unresolved_duplicate(incident_db):
    """The failure mode the migration comment documents, pinned rather than assumed.

    A database carrying a duplicate other than 0454's does not get the constraint applied
    quietly around it — the migration stops. That is the intended behaviour for an
    integrity constraint, and the audit query is how the offending rows are found.
    """
    index_stmts = [
        s for s in _statements(_repair_sql()) if s.upper().startswith("CREATE UNIQUE INDEX")
    ]
    assert len(index_stmts) == 1
    # Leave the duplicate unrepaired by making the repair's guard miss.
    incident_db.execute("UPDATE workflow_sequence_items SET type = 'TSR' WHERE id = ?", [SLOT_SIX])
    incident_db.commit()
    with pytest.raises(sqlite3.IntegrityError):
        incident_db.execute(index_stmts[0])
    incident_db.rollback()
    assert len(_duplicates(incident_db)) == 2
def test_a_document_in_two_slots_resolves_to_the_earlier_one(incident_db):
    """The legacy shape: the lookup must not be at the engine's discretion.

    Before the ordering was added the query was `WHERE result_doc_id = ? LIMIT 1` with no
    `ORDER BY`, so which slot 0005-TR belonged to was whatever came back first. Databases
    that have not yet run 090 still hold states like this one.
    """
    from modules.flow_gate.db import workflow_sequences as db_wfseq

    with _store_over(incident_db):
        item = db_wfseq.get_item_by_result_doc_id(TR_FOUR)
        assert item["id"] == SLOT_FOUR, "the earlier slot (sort_order 3) is the answer"
        assert item["item_seq"] == 4
        # …and repeatedly, not by luck.
        for _ in range(5):
            assert db_wfseq.get_item_by_result_doc_id(TR_FOUR)["id"] == SLOT_FOUR
        assert db_wfseq.get_item_by_result_doc_id(TR_SIX) is None
def test_the_lookup_follows_sort_order_and_not_the_row_id(incident_db):
    """The earlier slot wins even when it is the later row.

    This is the assertion the ordering exists for. Without `ORDER BY sort_order ASC` the
    answer here is the *later* slot — see the control below, which runs the previous query
    text against the same rows.
    """
    from modules.flow_gate.db import workflow_sequences as db_wfseq

    _seed_disagreeing_row_order(incident_db)

    with _store_over(incident_db):
        item = db_wfseq.get_item_by_result_doc_id(SHARED_DOC)

    assert item["id"] == EARLY_SLOT
    assert item["sort_order"] == 1
    assert item["item_seq"] == 1
def test_control_the_previous_unordered_lookup_answers_the_later_slot(incident_db):
    """The control for the test above: the old query text really does get this wrong.

    Without it, a green ordering test cannot be told apart from a fixture in which any
    query would have answered correctly — which is exactly what 0454's own rows are.
    """
    _seed_disagreeing_row_order(incident_db)

    row = incident_db.execute(_UNORDERED_LOOKUP, [SHARED_DOC]).fetchone()
    assert row["id"] == LATE_SLOT, (
        "if this ever stops picking the later slot the control has gone stale and the "
        "ordering test above is no longer measuring anything"
    )
    assert row["sort_order"] == 5
def test_after_the_repair_each_document_resolves_to_its_own_slot(repaired_db):
    from modules.flow_gate.db import workflow_sequences as db_wfseq

    with _store_over(repaired_db):
        assert db_wfseq.get_item_by_result_doc_id(TR_FOUR)["id"] == SLOT_FOUR
        assert db_wfseq.get_item_by_result_doc_id(TR_SIX)["id"] == SLOT_SIX
