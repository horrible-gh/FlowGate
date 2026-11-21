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

@pytest.mark.parametrize("difference", sorted(GUARDS))
def test_the_repair_is_a_no_op_when_any_identifying_fact_differs(incident_db, difference):
    """Change one fact from the review and the repair declines to act.

    Only 090's UPDATE runs here. These cases deliberately leave the duplicate in place, so
    the file's CREATE UNIQUE INDEX would refuse to build — which would tell us nothing
    about whether the *repair* held its fire.
    """
    if difference == "the slot belongs to a different sequence":
        incident_db.execute(
            "INSERT INTO workflow_sequences (id, doc_id, created_at, updated_at) VALUES "
            "(999, 'flowgate.default.0454.0004-T', '2026-01-01', '2026-01-01')")
    sql, params = GUARDS[difference]
    incident_db.execute(sql, params)
    incident_db.commit()

    before = _slots(incident_db)
    assert _apply_repair_update_only(incident_db) == 0, (
        f"the repair fired even though {difference}"
    )
    assert _slots(incident_db) == before
def test_the_repair_replaces_the_plain_index_with_a_unique_one(repaired_db):
    indexes = _indexes(repaired_db)
    assert "uq_wfseq_items_result_doc" in indexes
    assert "idx_wfseq_items_result_doc" not in indexes, (
        "the plain index served the same reverse lookup; leaving both would be two "
        "definitions of one thing"
    )
    ddl = repaired_db.execute(
        "SELECT sql FROM sqlite_master WHERE name = 'uq_wfseq_items_result_doc'").fetchone()[0]
    assert "UNIQUE" in ddl.upper()
    assert "WHERE result_doc_id IS NOT NULL" in ddl
def test_a_second_slot_cannot_take_a_document_another_slot_already_holds(repaired_db):
    with pytest.raises(sqlite3.IntegrityError):
        repaired_db.execute(
            "UPDATE workflow_sequence_items SET result_doc_id = ? WHERE id = ?",
            [TR_FOUR, 5675])
    repaired_db.rollback()
    assert _slot(repaired_db, 5675)["result_doc_id"] is None
def test_many_slots_may_stay_empty(repaired_db):
    repaired_db.execute(
        "UPDATE workflow_sequence_items SET result_doc_id = NULL WHERE id IN (?,?)",
        [5671, 5673])
    repaired_db.commit()
    empty = repaired_db.execute(
        "SELECT COUNT(*) FROM workflow_sequence_items WHERE result_doc_id IS NULL").fetchone()[0]
    assert empty == 4, "NULL is not a value the uniqueness rule applies to"
def test_a_slot_may_be_rewritten_with_the_document_it_already_holds(repaired_db):
    repaired_db.execute(
        "UPDATE workflow_sequence_items SET result_doc_id = ? WHERE id = ?", [TR_SIX, SLOT_SIX])
    repaired_db.commit()
    assert _slot(repaired_db, SLOT_SIX)["result_doc_id"] == TR_SIX
