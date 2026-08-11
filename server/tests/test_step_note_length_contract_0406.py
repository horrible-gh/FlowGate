"""0406 T0022 — 1,000-character sequence/work-plan note contract with real DB round trips."""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager

import pytest

from modules.flow_gate.db import connection as db_connection
from modules.flow_gate.db import workflow_sequences as db_wfseq
from modules.flow_gate.documents.constants import STEP_NOTE_MAX_CHARS
from modules.flow_gate.services import work_plan_sequence_service as wpseq
from modules.flow_gate.services import work_plan_service as wp
from modules.flow_gate.services import workflow_decision_service as wds

PROJECT = "flowgate"
GROUP = "flowgate.default.0406"
DOC = "flowgate.default.0406.0001-B"

SEED_SQL = f"""
INSERT OR IGNORE INTO projects(project_id, project_name, is_active, created_at, updated_at)
VALUES('{PROJECT}', 'FlowGate', 1, datetime('now'), datetime('now'));
INSERT OR IGNORE INTO groups(group_id, project_id, module, title, status, created_at, updated_at)
VALUES('{GROUP}', '{PROJECT}', 'default', 'note length', 'OPEN', datetime('now'), datetime('now'));
INSERT OR IGNORE INTO documents(
    doc_id, project_id, module, group_id, type_code, seq, title, status, created_at, updated_at)
VALUES('{DOC}', '{PROJECT}', 'default', '{GROUP}', 'B', 1, 'note length', 'open',
       datetime('now'), datetime('now'));
"""


class SqliteStore:
    def __init__(self, path):
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")

    def _execute(self, sql, params=None):
        self._conn.execute(sql, params or [])
        self._conn.commit()

    def _fetch_one(self, sql, params=None):
        row = self._conn.execute(sql, params or []).fetchone()
        return dict(row) if row else None

    def _fetch_all(self, sql, params=None):
        return [dict(row) for row in self._conn.execute(sql, params or []).fetchall()]

    @contextmanager
    def transaction(self):
        yield self


@pytest.fixture
def real_store(migrated_sqlite_db):
    path = migrated_sqlite_db("step_note_length_0406.db", seed_sql=SEED_SQL)
    store = SqliteStore(path)
    previous = db_connection.STORE
    db_connection.STORE = store
    try:
        yield store
    finally:
        db_connection.STORE = previous
        store._conn.close()


def seed_sequence(note):
    db_wfseq.insert_sequence(DOC)
    seq = db_wfseq.get_sequence_by_doc_id(DOC)
    db_wfseq.insert_sequence_item(
        sequence_id=seq["id"], item_seq=1, type_="T", label="Task", doc_class="B",
        sort_order=0, note=note, source_doc_id=None, source_revision_no=None,
    )
    return seq


def test_one_canonical_limit_is_shared_by_both_server_services():
    assert STEP_NOTE_MAX_CHARS == 1000
    assert wp.NOTE_MAX_CHARS == STEP_NOTE_MAX_CHARS
    assert wpseq.NOTE_MAX_CHARS == STEP_NOTE_MAX_CHARS


def test_exactly_1000_characters_survive_real_db_round_trip(real_store):
    note = "가" * STEP_NOTE_MAX_CHARS
    seed_sequence(note)

    loaded = wds.get_workflow_sequence(DOC)
    assert loaded["note_max_chars"] == 1000
    assert loaded["items"][0]["note"] == note

    wds.edit_workflow_pending(
        DOC,
        [{
            "type": "T", "label": "Task", "note": note,
            "source_doc_id": None, "source_revision_no": None,
        }],
    )
    after = wds.get_workflow_sequence(DOC)["items"][0]["note"]
    assert after == note
    assert len(after) == 1000


def test_1001_characters_are_rejected_without_truncating_existing_db_value(real_store):
    existing = "기" * STEP_NOTE_MAX_CHARS
    seed_sequence(existing)
    too_long = "나" * (STEP_NOTE_MAX_CHARS + 1)

    with pytest.raises(wpseq.NoteTooLong) as exc:
        wds.edit_workflow_pending(
            DOC,
            [{
                "type": "T", "label": "Task", "note": too_long,
                "source_doc_id": None, "source_revision_no": None,
            }],
        )

    assert exc.value.code == "note_too_long"
    assert exc.value.length == 1001
    assert exc.value.max_chars == 1000
    assert wds.get_workflow_sequence(DOC)["items"][0]["note"] == existing


def test_normalizer_never_silently_cuts_read_values():
    too_long = "x" * 1001
    assert wpseq.normalize_note(too_long) == too_long
    with pytest.raises(wpseq.NoteTooLong):
        wpseq.normalize_note(too_long, strict=True)