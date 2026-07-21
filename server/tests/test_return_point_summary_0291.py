"""return-point 4종 세트 단일 쿼리화 (flowgate.default.0291 T2).

문서 응답 하나를 만들 때마다 이 넷이 통째로 돌았다 — CH0016 덤프에 네 번 반복된다::

    SELECT * FROM workflow_return_points WHERE group_id = ?
    SELECT doc_id, seq, type_code, title FROM documents WHERE group_id = ? AND seq = ?
    SELECT MIN(d.seq) ... WHERE s.return_point_id = ? AND d.doc_review_status='pending_review'
    SELECT COUNT(*) FROM workflow_return_point_docs WHERE return_point_id = ?

``workflow_return_points.summary()`` 가 하나로 접는다.

이 스위트가 보는 것은 둘이다.

1. **왕복이 정말 1회인가.** 실제 DB 왕복을 세는 백엔드를 쓴다. 결과값만 비교하면
   "합쳐졌다" 를 증명하지 못한다.
2. **합침이 값을 바꾸지 않는가.** 모든 케이스에서 새 ``summary()`` 와 **예전 4호출
   조합**을 나란히 돌려 결과가 같은지 본다. 회귀를 잡는 유일하게 정직한 기준은
   "예전 코드가 내던 값" 이고, 그 코드가 아직 살아 있으므로(단독 호출부가 있다)
   그대로 기준으로 쓴다.

경계는 상관 서브쿼리 + LEFT JOIN 이 만든 것들이다: 반환점 없음, 스냅샷 0건, pending
문서 없음(``MIN`` 이 NULL), front 문서 삭제됨(LEFT JOIN 이 안 맞음). 마지막 둘이 예전
코드에서 각각 ``None`` / 라벨 없음으로 떨어지던 자리다.
"""
from __future__ import annotations

import os
import sqlite3
import sys
from pathlib import Path

import pytest

os.environ["TESTING"] = "1"
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing-only-32c")

_SERVER_DIR = Path(__file__).resolve().parents[1]
_SCHEMA_DIR = _SERVER_DIR / "sql" / "migrations" / "sqlite"
sys.path.insert(0, str(_SERVER_DIR))

from modules.flow_gate.db import connection as _connection  # noqa: E402
from modules.flow_gate.db import workflow_return_points as db_rp  # noqa: E402
from modules.flow_gate.db.connection import FlowGateStore  # noqa: E402

GROUP_ID = "t2.default.0291"


class _CountingBackend:
    """진짜 SQLite + 읽기 왕복 카운터."""

    db_type = 1  # dialect.SQLITE — translate 는 no-op

    def __init__(self):
        self.conn = sqlite3.connect(":memory:", check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        for sql_file in sorted(_SCHEMA_DIR.glob("*.sql")):
            try:
                self.conn.executescript(sql_file.read_text(encoding="utf-8"))
            except sqlite3.Error:
                # 마이그레이션 일부는 이 스위트가 쓰지 않는 테이블용이고, 순서에 따라
                # 재적용에서 걸린다. 필요한 세 테이블만 있으면 된다.
                pass
        # 이 스위트가 보는 것은 SQL 의 모양(상관 서브쿼리 · LEFT JOIN · 왕복 수)이지
        # 참조 무결성이 아니다. projects/groups/users 까지 채우면 그 셋의 seed 코드가
        # 케이스보다 길어지고, 정작 무엇을 검사하는지가 묻힌다.
        self.conn.execute("PRAGMA foreign_keys = OFF")
        self.conn.commit()
        self.reads: list[str] = []

    def fetch_one(self, sql, params=None):
        self.reads.append(sql)
        row = self.conn.execute(sql, params or []).fetchone()
        return dict(row) if row else None

    def fetch_all(self, sql, params=None):
        self.reads.append(sql)
        return [dict(r) for r in self.conn.execute(sql, params or []).fetchall()]

    def execute(self, sql, params=None):
        cur = self.conn.execute(sql, params or [])
        self.conn.commit()
        return cur

    def commit(self):
        self.conn.commit()


@pytest.fixture
def backend(monkeypatch):
    be = _CountingBackend()
    store = FlowGateStore.__new__(FlowGateStore)
    store._db = be
    store._sq = None
    # db 모듈들은 ``from .connection import get_store`` 로 이름을 자기 네임스페이스에
    # 복사해 둔다. connection 쪽만 갈아끼우면 그 복사본은 그대로 원래 store 를 본다.
    monkeypatch.setattr(_connection, "get_store", lambda: store)
    monkeypatch.setattr(db_rp, "get_store", lambda: store)
    return be


def _doc(be, seq: int, type_code: str, title: str, status: str) -> str:
    doc_id = f"{GROUP_ID}.{seq:04d}-{type_code}"
    be.conn.execute(
        "INSERT INTO documents (doc_id, project_id, type_code, seq, title, group_id, "
        "module, doc_review_status, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,'now','now')",
        [doc_id, "t2", type_code, seq, title, GROUP_ID, "default", status],
    )
    return doc_id


def _return_point(be, front_seq: int) -> int:
    be.conn.execute(
        "INSERT INTO workflow_return_points (group_id, front_seq, created_at, updated_at) "
        "VALUES (?,?,'now','now')",
        [GROUP_ID, front_seq],
    )
    return int(be.conn.execute(
        "SELECT id FROM workflow_return_points WHERE group_id = ?", [GROUP_ID]
    ).fetchone()["id"])


def _snapshot(be, rp_id: int, doc_id: str, seq: int) -> None:
    be.conn.execute(
        "INSERT INTO workflow_return_point_docs (return_point_id, doc_id, seq, prev_status, fingerprint) "
        "VALUES (?,?,?,'approved','fp')",
        [rp_id, doc_id, seq],
    )


def _legacy(group_id: str) -> dict | None:
    """예전 4호출 조합. 동치성 판단의 기준이다."""
    rp = db_rp.get_by_group(group_id)
    if rp is None:
        return None
    front = db_rp.get_front_doc(group_id, int(rp["front_seq"]))
    return {
        "id": rp["id"],
        "front_seq": rp["front_seq"],
        "front_title": (front or {}).get("title"),
        "front_type_code": (front or {}).get("type_code"),
        "restorable_count": db_rp.count_docs(rp["id"]),
        "current_min_seq": db_rp.current_pending_min_seq(rp["id"]),
    }


def _assert_matches_legacy(be):
    """새 것과 예전 것이 같은 값을 주는지 + 왕복이 1 대 4 인지 함께 본다."""
    be.reads.clear()
    merged = db_rp.summary(GROUP_ID)
    merged_reads = len(be.reads)

    be.reads.clear()
    legacy = _legacy(GROUP_ID)
    legacy_reads = len(be.reads)

    assert merged == legacy, f"합침이 값을 바꿨다\n new={merged}\n old={legacy}"
    assert merged_reads == 1, f"합침 쿼리가 {merged_reads}회 갔다"
    assert legacy_reads > merged_reads, "예전 경로가 더 적게 갔다 — 카운터가 잘못됐다"
    return merged


class TestReturnPointSummary:
    def test_full_payload_in_one_query(self, backend):
        """정상 케이스: 반환점 + front 문서 + 스냅샷 + pending 문서."""
        front = _doc(backend, 8, "T", "Task", "approved")
        pending = _doc(backend, 5, "L", "Logic", "pending_review")
        rp_id = _return_point(backend, 8)
        _snapshot(backend, rp_id, front, 8)
        _snapshot(backend, rp_id, pending, 5)

        merged = _assert_matches_legacy(backend)
        assert merged["front_seq"] == 8
        assert merged["front_title"] == "Task"
        assert merged["restorable_count"] == 2
        assert merged["current_min_seq"] == 5

    def test_absent_return_point(self, backend):
        """반환점이 없으면 None — 파생 조회는 아예 일어나지 않는다."""
        _doc(backend, 8, "T", "Task", "approved")
        backend.reads.clear()
        assert db_rp.summary(GROUP_ID) is None
        assert len(backend.reads) == 1

    def test_no_pending_docs_gives_null_min_seq(self, backend):
        """스냅샷 문서가 전부 승인 상태면 MIN 이 NULL 이다 → current_min_seq is None.

        복원 완료 판정(``current_pending_min_seq() is None``)이 이 값에 걸려 있어서,
        0 이나 누락으로 새면 반환점이 안 지워진다.
        """
        front = _doc(backend, 8, "T", "Task", "approved")
        rp_id = _return_point(backend, 8)
        _snapshot(backend, rp_id, front, 8)

        merged = _assert_matches_legacy(backend)
        assert merged["current_min_seq"] is None
        assert merged["restorable_count"] == 1

    def test_empty_snapshot_counts_zero(self, backend):
        """스냅샷이 0건이어도 COUNT 서브쿼리는 행을 지우지 않는다."""
        _doc(backend, 8, "T", "Task", "approved")
        _return_point(backend, 8)

        merged = _assert_matches_legacy(backend)
        assert merged["restorable_count"] == 0
        assert merged["current_min_seq"] is None
        assert merged["front_title"] == "Task"

    def test_missing_front_doc_keeps_the_return_point(self, backend):
        """front_seq 가 가리키는 문서가 없어도 반환점은 살아 있어야 한다.

        LEFT JOIN 이 아니라 INNER JOIN 으로 썼다면 여기서 반환점이 통째로 사라진다 —
        UI 가 '되돌릴 것이 없다' 고 표시하게 되는 조용한 회귀다.
        """
        rp_id = _return_point(backend, 8)   # seq=8 문서를 만들지 않는다
        other = _doc(backend, 5, "L", "Logic", "pending_review")
        _snapshot(backend, rp_id, other, 5)

        merged = _assert_matches_legacy(backend)
        assert merged["front_seq"] == 8
        assert merged["front_title"] is None
        assert merged["front_type_code"] is None
        assert merged["current_min_seq"] == 5

    def test_other_groups_pending_docs_do_not_leak_in(self, backend):
        """MIN 은 이 반환점의 스냅샷 문서로만 한정된다 (0142 rework 의 취지).

        같은 그룹의 관계없는 pending 문서가 current_min_seq 를 끌어내리면 복원
        범위가 틀어진다. 상관 서브쿼리로 옮기면서 이 한정이 유지되는지 본다.
        """
        front = _doc(backend, 8, "T", "Task", "approved")
        _doc(backend, 2, "D", "Phantom", "pending_review")   # 스냅샷에 없는 pending
        rp_id = _return_point(backend, 8)
        _snapshot(backend, rp_id, front, 8)
        snap_pending = _doc(backend, 6, "P", "Protocol", "pending_review")
        _snapshot(backend, rp_id, snap_pending, 6)

        merged = _assert_matches_legacy(backend)
        assert merged["current_min_seq"] == 6, "스냅샷 밖의 pending 문서가 새어 들어왔다"


class TestPayloadWiring:
    def test_router_payload_uses_the_merged_query(self, backend):
        """라우터의 응답 조각도 왕복 1회로 만들어진다 — T2 의 수치 자체."""
        from modules.flow_gate.documents.routers.documents import _return_point_payload

        front = _doc(backend, 8, "T", "Task", "approved")
        pending = _doc(backend, 5, "L", "Logic", "pending_review")
        rp_id = _return_point(backend, 8)
        _snapshot(backend, rp_id, front, 8)
        _snapshot(backend, rp_id, pending, 5)

        backend.reads.clear()
        payload = _return_point_payload(GROUP_ID)
        assert len(backend.reads) == 1, f"{len(backend.reads)}회 갔다: {backend.reads}"
        assert payload == {
            "exists": True,
            "front_seq": 8,
            "front_label": "Task",
            "restorable_count": 2,
            "current_min_seq": 5,
            "destination_default": 8,
            "destination_min": 5,
        }

    def test_absent_return_point_payload_unchanged(self, backend):
        from modules.flow_gate.documents.routers.documents import _return_point_payload

        assert _return_point_payload(GROUP_ID) == {
            "exists": False,
            "front_seq": None,
            "front_label": None,
            "restorable_count": 0,
            "current_min_seq": None,
            "destination_default": None,
            "destination_min": None,
        }

    def test_front_label_falls_back_to_type_code(self, backend):
        """제목이 비면 type_code 로 떨어진다 — 예전 ``or`` 연쇄와 같은 동작."""
        from modules.flow_gate.documents.routers.documents import _return_point_payload

        _doc(backend, 8, "AC", "", "approved")
        _return_point(backend, 8)
        assert _return_point_payload(GROUP_ID)["front_label"] == "AC"
