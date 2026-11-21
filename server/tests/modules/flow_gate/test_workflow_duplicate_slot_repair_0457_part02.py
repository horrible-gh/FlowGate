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

def test_I_0007_tr_is_no_longer_an_orphan_and_0005_tr_holds_one_slot(repaired_db):
    holders = {
        doc: [r["id"] for r in repaired_db.execute(
            "SELECT id FROM workflow_sequence_items WHERE result_doc_id = ? ORDER BY id", [doc])]
        for doc in (TR_FOUR, TR_SIX)
    }
    assert holders == {TR_FOUR: [SLOT_FOUR], TR_SIX: [SLOT_SIX]}
def test_the_audit_finds_the_one_known_duplicate_before_the_repair(incident_db):
    rows = _duplicates(incident_db)
    assert [r["result_doc_id"] for r in rows] == [TR_FOUR, TR_FOUR]
    assert [(r["sequence_id"], r["item_id"], r["item_seq"], r["sort_order"]) for r in rows] == [
        (SEQUENCE_ID, SLOT_FOUR, 4, 3),
        (SEQUENCE_ID, SLOT_SIX, 6, 5),
    ], "the audit must name both sides by sequence, slot id, item_seq and sort_order"
def test_the_audit_is_empty_after_the_repair(repaired_db):
    assert _duplicates(repaired_db) == []
def test_re_running_the_repair_changes_nothing(repaired_db):
    before = _slots(repaired_db)
    ledger_before = _ledger(repaired_db)
    counts = _apply_repair(repaired_db)
    assert counts[0] == 0, "the second application must match no rows"
    assert _slots(repaired_db) == before
    assert _ledger(repaired_db) == ledger_before
def test_a_database_that_never_saw_the_incident_is_untouched(tmp_path):
    """A fresh install: the file applies, the constraint appears, no data is invented."""
    conn = _build_db(tmp_path, "fresh.db", include_repair=False)
    counts = _apply_repair(conn)
    assert counts[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM workflow_sequence_items").fetchone()[0] == 0
    assert "uq_wfseq_items_result_doc" in _indexes(conn)
    conn.close()
