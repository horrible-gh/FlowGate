"""preserve the three note/source values through real DB round trips.

Other suites replaced modules.flow_gate.db with recording doubles, proving only Python
argument propagation. They never exercised SQL writing and reading migration 079 columns.
The review found zero production rows with a populated note, demonstrating why that evidence
was insufficient. Run edit_workflow_pending against fully migrated SQLite and read it
back using the same SELECT as production.
"""
from __future__ import annotations

import os
import sqlite3
import sys
from contextlib import contextmanager
from pathlib import Path

import pytest

os.environ.setdefault("TESTING", "1")
os.environ.setdefault("DB_TYPE", "sqlite")
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing-only-32c")

_SERVER_DIR = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(_SERVER_DIR))

from modules.flow_gate.db import connection as db_connection  # noqa: E402
from modules.flow_gate.db import workflow_sequences as db_wfseq  # noqa: E402
from modules.flow_gate.services import workflow_decision_service  # noqa: E402

_PROJECT = "flowgate"
_GROUP = "flowgate.default.0406"
_ROOT_DOC = "flowgate.default.0406.0001-B"
_WP_DOC = "flowgate.default.0406.0004-WP"
_METADATA_KEYS = ("note", "source_doc_id", "source_revision_no")

_SEED_SQL = f"""
INSERT OR IGNORE INTO projects(project_id, project_name, is_active, created_at, updated_at)
    VALUES('{_PROJECT}', 'FlowGate', 1, datetime('now'), datetime('now'));
INSERT OR IGNORE INTO groups(group_id, project_id, module, title, status, created_at, updated_at)
    VALUES('{_GROUP}', '{_PROJECT}', 'default', '전달멘트 소거', 'OPEN', datetime('now'), datetime('now'));
INSERT OR IGNORE INTO documents(
        doc_id, project_id, module, group_id, type_code, seq, title, status,
        created_at, updated_at)
    VALUES('{_ROOT_DOC}', '{_PROJECT}', 'default', '{_GROUP}', 'B', 1,
           '전달멘트가 연계되지 않음', 'open', datetime('now'), datetime('now')),
          ('{_WP_DOC}', '{_PROJECT}', 'default', '{_GROUP}', 'WP', 4,
           '전달멘트 소거 수정 작업계획', 'open', datetime('now'), datetime('now')),
          ('flowgate.default.0406.0003-WP', '{_PROJECT}', 'default', '{_GROUP}', 'WP', 3,
           '리비전 0 계획', 'open', datetime('now'), datetime('now'));
"""


class _SqliteStore:
    """Minimal production FlowGateStore contract, deliberately without _sql.

    Let workflow_sequences._sql fall back to FlowGateStore._sql and the real registered
    SQL. Rewriting statements here would duplicate the contract this test must verify.
    """

    def __init__(self, db_path: str) -> None:
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")

    def _execute(self, sql, params=None):
        self._conn.execute(sql, params or [])
        self._conn.commit()

    def _fetch_one(self, sql, params=None):
        row = self._conn.execute(sql, params or []).fetchone()
        return dict(row) if row else None

    def _fetch_all(self, sql, params=None):
        return [dict(r) for r in self._conn.execute(sql, params or []).fetchall()]

    @contextmanager
    def transaction(self):
        yield self


@pytest.fixture
def real_store(migrated_sqlite_db):
    """Real storage backed by a SQLite file with every migration applied.

    Following 0393, replace connection.STORE, not get_store, so modules that already
    imported the function see the same store.
    """
    db_path = migrated_sqlite_db("seq_meta_roundtrip_0406.db", seed_sql=_SEED_SQL)
    store = _SqliteStore(db_path)
    previous = db_connection.STORE
    db_connection.STORE = store
    try:
        yield store
    finally:
        db_connection.STORE = previous
        store._conn.close()


@pytest.fixture
def seeded_sequence(real_store):
    """Three pending rows shaped like an applied WP: two with provenance and one without."""
    db_wfseq.insert_sequence(_ROOT_DOC)
    seq = db_wfseq.get_sequence_by_doc_id(_ROOT_DOC)
    rows = [
        ("D", "기본설계", "계획이 준 전달멘트", _WP_DOC, 7),
        ("P", "절차서", "", None, None),
        ("M", "메모", "리비전 0도 출처다", "flowgate.default.0406.0003-WP", 0),
    ]
    for index, (type_, label, note, source_doc_id, source_revision_no) in enumerate(rows):
        db_wfseq.insert_sequence_item(
            sequence_id=seq["id"],
            item_seq=index + 1,
            type_=type_,
            label=label,
            doc_class="B",
            sort_order=index,
            note=note,
            source_doc_id=source_doc_id,
            source_revision_no=source_revision_no,
        )
    return seq


def _metadata(items):
    return [{key: item.get(key) for key in _METADATA_KEYS} for item in items]


def test_insert_and_select_carry_the_three_columns_through_real_sql(seeded_sequence):
    """Production INSERT/SELECT statements preserve values in migration 079 columns."""
    stored = db_wfseq.get_sequence_items(seeded_sequence["id"])

    assert _metadata(stored) == [
        {"note": "계획이 준 전달멘트", "source_doc_id": _WP_DOC, "source_revision_no": 7},
        {"note": "", "source_doc_id": None, "source_revision_no": None},
        {"note": "리비전 0도 출처다", "source_doc_id": "flowgate.default.0406.0003-WP", "source_revision_no": 0},
    ]
    print("DB_STORED_METADATA=" + repr(_metadata(stored)))


def test_canonical_query_reads_the_metadata_out_of_the_database(seeded_sequence):
    """Canonical lookup returns all three stored keys; the old serializer omitted them."""
    result = workflow_decision_service.get_workflow_sequence(_ROOT_DOC)

    assert result["decided"] is True
    assert result["doc_class"] == "B"
    assert len(result["items"]) == 3
    assert all(set(_METADATA_KEYS) <= set(item) for item in result["items"])
    assert result["items"][0]["note"] == "계획이 준 전달멘트"
    assert result["items"][0]["source_doc_id"] == _WP_DOC
    assert result["items"][2]["source_revision_no"] == 0


def test_no_edit_save_round_trip_keeps_every_value_in_the_database(seeded_sequence):
    """regression: saving without edits preserves all three values."""
    loaded = workflow_decision_service.get_workflow_sequence(_ROOT_DOC)
    before = _metadata(loaded["items"])

    # Mirror the UI: send the received rows unchanged as the PATCH body.
    workflow_decision_service.edit_workflow_pending(
        _ROOT_DOC,
        [
            {
                "type": item["type"],
                "label": item["label"],
                "note": item["note"],
                "source_doc_id": item["source_doc_id"],
                "source_revision_no": item["source_revision_no"],
            }
            for item in loaded["items"]
        ],
    )

    after = _metadata(workflow_decision_service.get_workflow_sequence(_ROOT_DOC)["items"])
    assert after == before
    print("DB_ROUNDTRIP_METADATA=" + repr({"before": before, "after": after}))


def test_a_save_without_the_keys_is_what_wiped_the_rows(seeded_sequence):
    """Saving without the three keys really clears them; the UI guard prevents that request."""
    workflow_decision_service.edit_workflow_pending(
        _ROOT_DOC,
        [
            {"type": item["type"], "label": item["label"]}
            for item in workflow_decision_service.get_workflow_sequence(_ROOT_DOC)["items"]
        ],
    )

    after = _metadata(workflow_decision_service.get_workflow_sequence(_ROOT_DOC)["items"])
    assert after == [{"note": "", "source_doc_id": None, "source_revision_no": None}] * 3
    print("DB_WIPED_METADATA=" + repr(after))


def test_revision_without_a_source_document_is_refused_before_any_row_is_touched(seeded_sequence):
    """The SQLite check enforcing invariant 2 must reject without altering earlier rows."""
    with pytest.raises(ValueError, match="invalid_sequence_item:0:"):
        workflow_decision_service.edit_workflow_pending(
            _ROOT_DOC,
            [{"type": "D", "label": "기본설계", "note": "", "source_doc_id": None, "source_revision_no": 3}],
        )

    still_there = _metadata(workflow_decision_service.get_workflow_sequence(_ROOT_DOC)["items"])
    assert still_there[0] == {
        "note": "계획이 준 전달멘트", "source_doc_id": _WP_DOC, "source_revision_no": 7,
    }
