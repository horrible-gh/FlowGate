"""0428 T#1: real migrated SQL, slot decisions, shared head/manual/chain reads.

No public proceed command or worker is activated by this suite. Document rows
and the source commit ledger must survive the storage operation.
"""
from __future__ import annotations

import json
import shutil
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from modules.flow_gate.db import connection, workflow_completion as db_completion
from modules.flow_gate.db import workflow_sequences as seqdb
from modules.flow_gate.services.workflow_completion_service import (
    completion_reason, slot_completed, slot_status,
)

ROOT = "flowgate.default.0428.0001-R"
RESULT = "flowgate.default.0428.0002-TSR"
GROUP = "flowgate.default.0428"
SERVER = Path(__file__).resolve().parents[1]


class SQLiteBridge:
    """Exercise real FlowGateStore transaction/affected-count/registered-SQL code."""
    db_type = 1

    def __init__(self, path):
        self.path = path
        self.local = threading.local()
        self.connections = []
        self.lock = threading.Lock()

    def conn(self):
        if not hasattr(self.local, "conn"):
            con = sqlite3.connect(self.path, timeout=15, isolation_level=None, check_same_thread=False)
            con.row_factory = sqlite3.Row
            con.execute("PRAGMA foreign_keys=ON")
            self.local.conn = con
            with self.lock:
                self.connections.append(con)
        return self.local.conn

    def execute(self, sql, params=()):
        return self.conn().execute(sql, params)

    @contextmanager
    def begin_transaction(self):
        con = self.conn()
        con.execute("BEGIN")
        class Tx:
            def execute(self, sql, params=()):
                self.cursor = con.execute(sql, params)
                return self.cursor
            def fetchone(self):
                return self.cursor.fetchone()
            def fetchall(self):
                return self.cursor.fetchall()
        try:
            yield Tx()
            con.commit()
        except BaseException:
            con.rollback()
            raise


@pytest.fixture(scope="session")
def completion_database(migrated_sqlite_db):
    # Replay the entire migration chain once, then copy that verified baseline
    # per case. Tests still get isolated real databases without repeated DDL fsync.
    return migrated_sqlite_db("completion_0428_baseline.db")


@pytest.fixture
def db(completion_database, tmp_path, monkeypatch):
    path = str(tmp_path / "completion_0428.db")
    shutil.copyfile(completion_database, path)
    bridge = SQLiteBridge(path)
    store = connection.FlowGateStore()
    store._db = bridge
    store._sq = None
    monkeypatch.setattr(connection, "STORE", store)
    con = bridge.conn()
    con.execute("INSERT INTO projects(project_id,project_name,created_at,updated_at) VALUES ('flowgate','FlowGate',datetime('now'),datetime('now'))")
    con.execute("INSERT INTO groups(group_id,project_id,module,title,status,created_at,updated_at) VALUES (?, 'flowgate','default','completion','OPEN',datetime('now'),datetime('now'))", [GROUP])
    for index, (doc_id, kind, review) in enumerate([
        (ROOT, "R", "wf_in_progress"), (RESULT, "TSR", "rejected"),
        ("flowgate.default.0428.0003-TR2", "TR2", "rejected"),
        ("flowgate.default.0428.0004-M", "M", "pending_review"),
        ("flowgate.default.0428.0005-D", "D", "approved"),
    ], 1):
        con.execute("INSERT INTO documents(doc_id,project_id,module,group_id,type_code,seq,title,status,revision_no,doc_review_status,created_at,updated_at) VALUES (?, 'flowgate','default',?,?,?,'fixture','open',7,?,datetime('now'),datetime('now'))", [doc_id, GROUP, kind, index, review])
    # An unused alternative result must not be an open orphan group-head candidate.
    con.execute("UPDATE documents SET status = 'archived' WHERE doc_id = 'flowgate.default.0428.0003-TR2'")
    seqdb.insert_sequence(ROOT)
    sequence = seqdb.get_sequence_by_doc_id(ROOT)
    seqdb.insert_sequence_item(sequence["id"], 1, "TSR", "report", "R", 0)
    seqdb.insert_sequence_item(sequence["id"], 2, "TR2", "apply plan", "R", 1)
    items = seqdb.get_sequence_items(sequence["id"])
    seqdb.set_item_result_doc_id(items[0]["id"], RESULT)
    yield store, sequence, items[0]["id"]
    for con in bridge.connections:
        con.close()


def record(db, request_key="request-1", **overrides):
    _, seq, slot = db
    args = dict(sequence_id=seq["id"],slot_id=slot,slot_generation=0,
                result_doc_id=RESULT,document_revision=7,actor_user_id="human-1",
                request_key=request_key)
    args.update(overrides)
    return db_completion.record_decision(**args)


def items(db):
    return seqdb.get_sequence_items(db[1]["id"])


def test_migration_legacy_defaults_and_review_unchanged(db):
    store, seq, slot = db
    before = store._fetch_one("SELECT * FROM documents WHERE doc_id = ?", [RESULT])
    assert items(db)[0]["slot_generation"] == 0
    assert items(db)[0]["completion_reason"] == "review_pending"
    assert seqdb.get_item_by_id(slot)["slot_decision_id"] is None
    decision = record(db)
    assert decision["actor_user_id"] == "human-1" and decision["decided_at"]
    assert _decision_key(decision) == (seq["id"], slot, 0, RESULT, 7)
    assert store._fetch_one("SELECT * FROM documents WHERE doc_id = ?", [RESULT]) == before
    assert store._fetch_one("SELECT COUNT(*) AS n FROM tr_commit_ledger", [])["n"] == 0


def _decision_key(d):
    return tuple(d[k] for k in ("sequence_id","slot_id","slot_generation","result_doc_id","document_revision"))


@pytest.mark.parametrize("review", ["rejected", "pending_review", "revised"])
@pytest.mark.parametrize("kind", ["TSR", "TR2"])
def test_sql_head_api_canonical_manual_chain_share_completion(db, monkeypatch, review, kind):
    from modules.flow_gate.api.v1 import workflow_head_routes as head
    from modules.flow_gate.api.v1 import ai_invoke_routes as invoke
    from modules.flow_gate.services import workflow_decision_service as manual
    from modules.flow_gate.services.ai_invoke import chain
    store, seq, slot = db
    store._execute("UPDATE documents SET type_code = ?, doc_review_status = ? WHERE doc_id = ?", [kind, review, RESULT])
    store._execute("UPDATE workflow_sequence_items SET type = ? WHERE id = ?", [kind, slot])
    # Approval side effects are never part of the independent storage path.
    approval = MagicMock(side_effect=AssertionError("approval invoked"))
    from modules.flow_gate.workflow import pipeline_service
    monkeypatch.setattr(pipeline_service, "transition_document_review", approval)
    with pytest.raises(ValueError, match="head_in_progress"):
        manual.advance_workflow(ROOT, "human-1", "http://example")
    record(db)
    row = items(db)[0]
    assert row["status"] == "done"
    assert row["completion_reason"] == completion_reason(row) == "proceeded_unapproved"
    assert row["result_doc_review_status"] == review
    serialized = head._serialize_item(row)
    canonical = manual.get_workflow_sequence(ROOT)["items"][0]
    for out in [serialized, canonical]:
        assert out["slot_completed"] is True
        assert out["completion_reason"] == "proceeded_unapproved"
        assert out["result_doc_review_status"] == review
        assert out["completion_decision_id"] == row["completion_decision_id"]
    assert seqdb.get_effective_head(seq["id"])["item_seq"] == 2
    assert seqdb.get_in_progress_head_by_group(GROUP, "flowgate") is None
    assert seqdb.get_pending_head_by_group(GROUP, "flowgate")["item_seq"] == 2
    assert seqdb.get_sequence_head_pending(seq["id"])["item_seq"] == 2
    snapshot = seqdb.get_sequence_snapshots_for_member_docs([ROOT, RESULT])
    assert snapshot[ROOT][1][0]["completion_reason"] == "proceeded_unapproved"
    assert chain._sequence_completion_state(ROOT) == (True, 2)
    assert chain._sequence_completion_state(RESULT, _snapshot=snapshot[RESULT]) == (True, 2)
    assert chain._next_incomplete_item_seq(ROOT) == 2
    assert chain._resolve_continuation_target(ROOT, None, to_end=True, _sequence=seq, _items=items(db)) == 2
    assert "already complete" in invoke._continuation_target_error(ROOT, 1)["msg"]
    assert invoke._continuation_target_error(ROOT, 2) is None
    monkeypatch.setattr(manual.token_service, "issue", lambda **kw: {
        "raw_token":"fixture-token", "token_id":"fixture-id", "scratch_dir":"fixture",
        "expires_at":"2099-01-01T00:00:00Z",
    })
    monkeypatch.setattr(manual.mention_service, "build_mention_from_token_rec", lambda **kw: "next")
    advanced = manual.advance_workflow(ROOT, "human-1", "http://example")
    assert advanced["head_item_seq"] == 2 and advanced["worker_document_type"] == "TR2"
    assert advanced["slot_completed"] is False and advanced["completion_reason"] == "pending"
    from modules.flow_gate.documents.routers import documents as document_routes
    doc = manual.db_documents.get_by_id(RESULT)
    parsed = document_routes._parse_doc_workflow(doc)
    assert parsed["workflow_head_type"] == "TR2"
    assert parsed["workflow_head_status"] == "pending"
    assert parsed["doc_review_status"] == review
    assert parsed["workflow_completion"]["completion_reason"] == "proceeded_unapproved"
    approval.assert_not_called()


@pytest.mark.parametrize("column,value", [
    ("slot_generation",1), ("result_doc_id","flowgate.default.0428.0003-TR2"),
    ("sequence_id",999),
])
def test_any_slot_key_mismatch_stops_completion(db, column, value):
    record(db)
    store, seq, slot = db
    if column == "sequence_id":
        # Keep FK validity while moving the slot to a different sequence identity.
        seqdb.insert_sequence("flowgate.default.0428.0003-TR2")
        value = seqdb.get_sequence_by_doc_id("flowgate.default.0428.0003-TR2")["id"]
    store._execute(f"UPDATE workflow_sequence_items SET {column} = ? WHERE id = ?", [value, slot])
    row = seqdb.get_sequence_items(value if column == "sequence_id" else seq["id"])[0]
    assert not slot_completed(row)
    assert row["completion_decision_id"] is None
    assert db_completion.pending_dispatches() == []


def test_revision_invalidation_and_generation_reopen_preserve_audit(db):
    store, seq, slot = db
    decision = record(db, dispatch={"chain_id":"chain-1","stop_run_id":"stop-1","stop_snapshot":{"stop_kind":"system","stop_code":"test_gate_blocked"}})
    assert len(db_completion.pending_dispatches()) == 1
    store._execute("UPDATE documents SET revision_no = 8 WHERE doc_id = ?", [RESULT])
    assert completion_reason(items(db)[0]) == "review_pending"
    assert db_completion.pending_dispatches() == []
    assert db_completion.invalidate_slot(seq["id"], slot, expected_generation=0, reason="reopen")
    assert not db_completion.invalidate_slot(seq["id"], slot, expected_generation=0, reason="stale")
    store._execute("UPDATE documents SET revision_no = 7 WHERE doc_id = ?", [RESULT])
    assert completion_reason(items(db)[0]) == "review_pending"
    old = db_completion.get_decision(decision["decision_id"])
    assert old["invalidated_at"] and old["invalidation_reason"] == "reopen"
    assert db_completion.get_dispatch(old["decision_id"], "chain-1", "stop-1")["state"] == "cancelled"
    new = record(db, request_key="after-reopen", slot_generation=1)
    assert new["decision_id"] != old["decision_id"]
    assert record(db)["decision_id"] == old["decision_id"]  # replay cannot resurrect it
    assert items(db)[0]["completion_decision_id"] == new["decision_id"]


def test_requests_replay_canonical_actor_and_unique_key_conflict(db):
    first = record(db)
    assert record(db, actor_user_id="human-2") == first
    assert record(db, request_key="request-2", actor_user_id="human-2") == first
    with pytest.raises(db_completion.CompletionConflict, match="request_key_conflict"):
        record(db, document_revision=8)
    assert db[0]._fetch_one("SELECT COUNT(*) AS n FROM workflow_completion_decisions", [])["n"] == 1
    with pytest.raises(sqlite3.IntegrityError):
        db[0]._execute("INSERT INTO workflow_completion_decisions SELECT 'duplicate', sequence_id,slot_id,slot_generation,result_doc_id,document_revision,decision_kind,actor_user_id,decided_at,invalidated_at,invalidation_reason FROM workflow_completion_decisions WHERE decision_id = ?", [first["decision_id"]])


@pytest.mark.parametrize("same_request", [False, True])
def test_simultaneous_cas_has_one_decision_and_intent(db, same_request):
    barrier = threading.Barrier(2)
    def click(index):
        barrier.wait(timeout=10)
        return record(db, request_key="same" if same_request else f"click-{index}", dispatch={
            "chain_id":"chain-1","stop_run_id":"stop-1","stop_snapshot":{"target_seq":2},
        })
    with ThreadPoolExecutor(max_workers=2) as pool:
        answers = list(pool.map(click, range(2)))
    assert answers[0]["decision_id"] == answers[1]["decision_id"]
    store = db[0]
    assert store._fetch_one("SELECT COUNT(*) AS n FROM workflow_completion_decisions", [])["n"] == 1
    assert len(db_completion.pending_dispatches()) == 1


def test_failed_dispatch_rolls_back_slot_decision_and_request(db):
    with pytest.raises(ValueError, match="identity_missing"):
        record(db, dispatch={"chain_id":"", "stop_run_id":"stop"})
    assert seqdb.get_item_by_id(db[2])["slot_decision_id"] is None
    for table in ["workflow_completion_decisions", "workflow_completion_requests", "workflow_completion_dispatches"]:
        assert db[0]._fetch_one(f"SELECT COUNT(*) AS n FROM {table}", [])["n"] == 0


def test_dispatch_unique_and_versioned_cas_durable_run_identity(db):
    decision = record(db, dispatch={"chain_id":"chain", "stop_run_id":"stop", "stop_snapshot":{"target_seq":2}})
    record(db, request_key="retry", dispatch={"chain_id":"chain", "stop_run_id":"stop"})
    dispatch = db_completion.pending_dispatches()[0]
    assert json.loads(dispatch["stop_snapshot_json"]) == {"target_seq":2}
    assert dispatch["execution_key"] == "completion:" + decision["decision_id"]
    assert db_completion.cas_dispatch(dispatch["dispatch_id"],expected_state="pending",expected_version=0,state="running",run_id="reserved-run",token_id="reserved-token")
    assert not db_completion.cas_dispatch(dispatch["dispatch_id"],expected_state="pending",expected_version=0,state="running")
    assert db_completion.cas_dispatch(dispatch["dispatch_id"],expected_state="running",expected_version=1,state="retryable_failed",error_code="start_failed")
    saved = db_completion.get_dispatch(decision["decision_id"], "chain", "stop")
    assert saved["run_id"] == "reserved-run" and saved["token_id"] == "reserved-token"
    assert saved["state_version"] == 2
    assert not db_completion.cas_dispatch(dispatch["dispatch_id"],expected_state="retryable_failed",expected_version=2,state="running",run_id="different-run")
    with pytest.raises(ValueError, match="transition_invalid"):
        db_completion.cas_dispatch(dispatch["dispatch_id"],expected_state="completed",expected_version=3,state="running")
    # New store object, same durable DB, same saved identity and decision.
    reloaded = connection.FlowGateStore()
    reloaded._db = db[0]._db
    assert reloaded._fetch_one(reloaded._sql("workflow_completion.get_decision"), [decision["decision_id"]]) == decision


@pytest.mark.parametrize("kind,review,reason", [
    ("D","approved","approved"), ("M","pending_review","auto_completed"),
    ("M","approved","approved"), ("D","rejected","review_pending"),
])
def test_legacy_approval_and_m_special_case(db, kind, review, reason):
    store, seq, slot = db
    store._execute("UPDATE documents SET type_code = ?, doc_review_status = ? WHERE doc_id = ?", [kind, review, RESULT])
    store._execute("UPDATE workflow_sequence_items SET type = ? WHERE id = ?", [kind, slot])
    row = items(db)[0]
    assert row["completion_reason"] == completion_reason(row) == reason
    assert row["status"] == slot_status(row)
    head = seqdb.get_effective_head(seq["id"])
    assert head["item_seq"] == (1 if reason == "review_pending" else 2)
    with pytest.raises(db_completion.CompletionConflict):
        record(db)


def test_effective_head_keeps_in_progress_priority_over_earlier_pending(db):
    store, seq, slot = db
    rows = items(db)
    seqdb.set_item_result_doc_id(slot, None)
    seqdb.set_item_result_doc_id(rows[1]["id"], "flowgate.default.0428.0003-TR2")
    assert seqdb.get_effective_head(seq["id"])["item_seq"] == 2


def test_all_slots_done_does_not_forge_final_approval(db):
    from modules.flow_gate.services.ai_invoke import chain
    from modules.flow_gate.services import workflow_decision_service as manual
    from modules.flow_gate.documents.routers import documents as document_routes
    store, seq, slot = db
    record(db)
    second_doc = "flowgate.default.0428.0003-TR2"
    store._execute("UPDATE documents SET status = 'open', doc_review_status = 'approved' WHERE doc_id = ?", [second_doc])
    seqdb.set_item_result_doc_id(items(db)[1]["id"], second_doc)
    assert seqdb.get_effective_head(seq["id"]) is None
    assert chain._sequence_completion_state(ROOT) == (True,None)
    with pytest.raises(ValueError,match="sequence_exhausted"):
        manual.advance_workflow(ROOT,"human-1","http://example")
    parsed = document_routes._parse_doc_workflow(manual.db_documents.get_by_id(ROOT))
    assert parsed["workflow_head_type"] == "AC" and parsed["workflow_head_status"] == "pending"
    assert parsed["is_final_approved"] is False
    assert manual.db_documents.get_by_id(RESULT)["doc_review_status"] == "rejected"


def test_missing_empty_and_unreadable_sequence_are_not_completed(db, monkeypatch):
    from modules.flow_gate.services.ai_invoke import chain
    from modules.flow_gate.api.v1 import ai_invoke_routes as invoke
    assert chain._sequence_completion_state(None) == (False,None)
    assert chain._sequence_completion_state(ROOT,_snapshot=(None,[])) == (False,None)
    assert chain._sequence_completion_state(ROOT,_snapshot=({"id":99},[])) == (False,None)
    monkeypatch.setattr(seqdb,"get_sequence_for_member_doc",MagicMock(side_effect=RuntimeError("db_unavailable")))
    with pytest.raises(RuntimeError,match="db_unavailable"):
        chain._sequence_completion_state(ROOT)
    assert invoke._continuation_target_error(ROOT,1) is None  # existing fail-open typo guard


@pytest.mark.parametrize("field", [
    "decision_sequence_id","decision_slot_id","decision_slot_generation",
    "decision_result_doc_id","decision_document_revision",
])
def test_python_snapshot_rejects_partial_or_stale_key(db, field):
    record(db)
    row = items(db)[0]
    assert slot_completed(row)
    row[field] = "stale"
    assert not slot_completed(row)


def test_sql_translation_and_three_dialect_migration_contract():
    from modules.flow_gate.db import dialect
    store = connection.FlowGateStore()
    queries = json.loads((SERVER/'sql/queries/queries.json').read_text(encoding='utf-8'))
    for backend in [dialect.MYSQL, dialect.POSTGRESQL]:
        for section in ['workflow_completion','workflow_sequences']:
            for sql in queries[section].values():
                translated = dialect.translate(sql,backend)
                assert 'strftime(' not in translated
    for backend in ['mysql','postgres','sqlite']:
        sql=(SERVER/f'sql/migrations/{backend}/143_workflow_completion_decisions.sql').read_text(encoding='utf-8')
        assert 'UNIQUE (sequence_id, slot_id, slot_generation)' in sql
        assert 'UNIQUE (decision_id, chain_id, stop_run_id)' in sql
        assert 'slot_generation' in sql
        assert 'UPDATE documents' not in sql
