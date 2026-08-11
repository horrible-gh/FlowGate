"""0406 T0013 — note·source 세 값이 진짜 DB 왕복에서 살아남는지 (NR0003 §6-2).

이 그룹의 다른 시험은 모두 ``modules.flow_gate.db`` 를 기록용 대역으로 바꾼다. 그래서
지금까지 증명된 것은 "파이썬 호출에 값이 실렸다"까지이고, 마이그레이션 079 가 만든 세
컬럼을 실제로 쓰고 다시 읽는 SQL 은 한 번도 실행되지 않았다. B0001 의 실측이 운영
테이블에서 note 가 채워진 행 0 개였다는 점을 생각하면, 그 수준의 증명이 바로 실패한
수준이다.

그래서 여기서는 모든 마이그레이션을 적용한 sqlite 파일에 실제 저장 경로
(``edit_workflow_pending``)를 돌리고, 운영과 같은 SELECT 로 다시 읽는다.
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

_SERVER_DIR = Path(__file__).resolve().parents[1]
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
    """운영 FlowGateStore 와 같은 최소 계약. ``_sql`` 은 일부러 두지 않는다.

    ``workflow_sequences._sql`` 이 ``FlowGateStore._sql`` 로 떨어지면서 등록된 진짜 SQL
    본문을 그대로 쓰게 하려는 것이다 — 여기서 문장을 새로 쓰면 검사하려던 계약을 시험이
    다시 작성해 버린다.
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
    """마이그레이션 전부를 적용한 sqlite 파일 위의 진짜 저장소.

    0393 의 교훈대로 ``get_store`` 함수를 갈아끼우지 않고 connection 의 STORE 객체만
    바꾼다. 이미 ``get_store`` 를 임포트해 둔 모듈도 같은 객체를 보게 된다.
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
    """WP 가 부은 것과 같은 모양의 대기 행 3개. 두 행은 출처가 있고 한 행은 없다."""
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
    """079 가 만든 컬럼이 운영 INSERT/SELECT 문 그대로 값을 실어 나른다."""
    stored = db_wfseq.get_sequence_items(seeded_sequence["id"])

    assert _metadata(stored) == [
        {"note": "계획이 준 전달멘트", "source_doc_id": _WP_DOC, "source_revision_no": 7},
        {"note": "", "source_doc_id": None, "source_revision_no": None},
        {"note": "리비전 0도 출처다", "source_doc_id": "flowgate.default.0406.0003-WP", "source_revision_no": 0},
    ]
    print("DB_STORED_METADATA=" + repr(_metadata(stored)))


def test_canonical_query_reads_the_metadata_out_of_the_database(seeded_sequence):
    """정본 조회가 DB 에 실제로 있는 값을 세 키로 돌려준다 (구형 serializer 였다면 키 자체가 없다)."""
    result = workflow_decision_service.get_workflow_sequence(_ROOT_DOC)

    assert result["decided"] is True
    assert result["doc_class"] == "B"
    assert len(result["items"]) == 3
    assert all(set(_METADATA_KEYS) <= set(item) for item in result["items"])
    assert result["items"][0]["note"] == "계획이 준 전달멘트"
    assert result["items"][0]["source_doc_id"] == _WP_DOC
    assert result["items"][2]["source_revision_no"] == 0


def test_no_edit_save_round_trip_keeps_every_value_in_the_database(seeded_sequence):
    """B0001 원래 증상의 회귀 항목: 아무것도 고치지 않은 저장이 세 값을 그대로 둔다."""
    loaded = workflow_decision_service.get_workflow_sequence(_ROOT_DOC)
    before = _metadata(loaded["items"])

    # 화면이 하는 일과 같다: 받은 행을 그대로 PATCH 본문으로 되돌린다.
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
    """세 키가 빠진 저장은 실제로 값을 지운다 — 화면 가드가 막는 것이 바로 이 요청이다."""
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
    """불변식 2 는 sqlite 에서 이 검사뿐이다. 거절이 앞선 행을 건드리지 않는 것까지 본다."""
    with pytest.raises(ValueError, match="invalid_sequence_item:0:"):
        workflow_decision_service.edit_workflow_pending(
            _ROOT_DOC,
            [{"type": "D", "label": "기본설계", "note": "", "source_doc_id": None, "source_revision_no": 3}],
        )

    still_there = _metadata(workflow_decision_service.get_workflow_sequence(_ROOT_DOC)["items"])
    assert still_there[0] == {
        "note": "계획이 준 전달멘트", "source_doc_id": _WP_DOC, "source_revision_no": 7,
    }
