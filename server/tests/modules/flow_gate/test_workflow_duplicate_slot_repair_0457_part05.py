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

def test_the_orphan_verdict_flips_only_for_0007_tr(incident_db):
    from modules.flow_gate.db import workflow_sequences as db_wfseq

    with _store_over(incident_db):
        assert db_wfseq.is_orphaned_workflow_member(TR_SIX) is True
        assert db_wfseq.is_orphaned_workflow_member(TR_FOUR) is False

    _apply_repair(incident_db)

    with _store_over(incident_db):
        assert db_wfseq.is_orphaned_workflow_member(TR_SIX) is False, (
            "this is what B0001 asked for: 0007-TR belongs to slot 6 again"
        )
        assert db_wfseq.is_orphaned_workflow_member(TR_FOUR) is False
        assert db_wfseq.get_sequence_for_member_doc(TR_SIX)["id"] == SEQUENCE_ID
def test_the_duplicate_audit_reads_the_same_through_the_db_layer(incident_db):
    from modules.flow_gate.db import workflow_sequences as db_wfseq

    with _store_over(incident_db):
        rows = db_wfseq.find_duplicate_result_doc_slots()
    assert [(r["result_doc_id"], r["item_id"]) for r in rows] == [
        (TR_FOUR, SLOT_FOUR), (TR_FOUR, SLOT_SIX)]

    _apply_repair(incident_db)
    with _store_over(incident_db):
        assert db_wfseq.find_duplicate_result_doc_slots() == []
def test_linking_a_document_to_a_second_empty_slot_is_refused(repaired_db):
    """The uniqueness rule reaches the caller as a named workflow conflict, not a DB error."""
    from modules.flow_gate.workflow.pipeline_service import (
        WorkflowDocumentAlreadyLinkedError,
    )

    before = _slots(repaired_db)
    ledger_before = _ledger(repaired_db)
    events_before = len(_conflict_events(repaired_db))

    with _store_over(repaired_db):
        with pytest.raises(WorkflowDocumentAlreadyLinkedError) as excinfo:
            _register(5675, TR_FOUR)     # slot 7 is empty; 0005-TR is already in slot 4

    exc = excinfo.value
    assert exc.error["code"] == "workflow_document_already_linked"
    assert exc.item_id == 5675
    assert exc.existing_item_id == SLOT_FOUR
    assert exc.requested_doc_id == TR_FOUR
    assert exc.body() == {"error": exc.error}

    assert _slots(repaired_db) == before, "a refused link must leave the data alone"
    assert _ledger(repaired_db) == ledger_before, "and no ledger row that reads like a success"

    events = _conflict_events(repaired_db)
    assert len(events) == events_before + 1
    meta = json.loads(events[-1]["metadata"])
    assert meta["code"] == "workflow_document_already_linked"
    assert meta["item_id"] == 5675
    assert meta["existing_item_id"] == SLOT_FOUR
def test_a_document_may_still_re_register_into_its_own_slot(repaired_db):
    """The compatibility the rejected-resubmit path depends on, unchanged."""
    before = _slot(repaired_db, SLOT_SIX)["result_doc_id"]
    rows_before = len([r for r in _ledger(repaired_db) if r["item_id"] == SLOT_SIX])

    with _store_over(repaired_db):
        _register(SLOT_SIX, TR_SIX)

    assert _slot(repaired_db, SLOT_SIX)["result_doc_id"] == before == TR_SIX
    rows_after = len([r for r in _ledger(repaired_db) if r["item_id"] == SLOT_SIX])
    assert rows_after == rows_before + 1
def test_the_occupancy_conflict_keeps_its_own_meaning(repaired_db):
    """T0005's refusal is a different one and must not be blurred into the new code.

    Slot 6 holds 0007-TR; a document that is in no slot at all tries to take it. Nothing
    about uniqueness is involved — this is the eviction guard, and it must still answer
    `workflow_slot_occupied`.
    """
    from modules.flow_gate.workflow.pipeline_service import (
        WorkflowDocumentAlreadyLinkedError,
        WorkflowSlotConflictError,
    )

    repaired_db.execute(
        "INSERT INTO documents (doc_id, project_id, module, group_id, type_code, seq, title, "
        "status, owner_id, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        ["flowgate.default.0454.0008-TR", PROJECT_ID, "default", GROUP_ID, "TR", 8,
         "loose", "open", USER_ID, "2026-08-24T00:00:00Z", "2026-08-24T00:00:00Z"])
    repaired_db.commit()
    before = _slots(repaired_db)

    with _store_over(repaired_db):
        with pytest.raises(WorkflowSlotConflictError) as excinfo:
            _register(SLOT_SIX, "flowgate.default.0454.0008-TR")

    exc = excinfo.value
    assert not isinstance(exc, WorkflowDocumentAlreadyLinkedError)
    assert exc.error["code"] == "workflow_slot_occupied"
    assert exc.existing_doc_id == TR_SIX
    assert _slots(repaired_db) == before
def test_an_empty_slot_still_accepts_a_document_that_is_in_no_slot(repaired_db):
    """The refusals are refusals, not a freeze: the normal registration still works."""
    repaired_db.execute(
        "INSERT INTO documents (doc_id, project_id, module, group_id, type_code, seq, title, "
        "status, owner_id, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        ["flowgate.default.0454.0009-TS", PROJECT_ID, "default", GROUP_ID, "TS", 9,
         "scenario", "open", USER_ID, "2026-08-24T00:00:00Z", "2026-08-24T00:00:00Z"])
    repaired_db.commit()

    with _store_over(repaired_db):
        _register(5675, "flowgate.default.0454.0009-TS")

    assert _slot(repaired_db, 5675)["result_doc_id"] == "flowgate.default.0454.0009-TS"
    assert _duplicates(repaired_db) == []
