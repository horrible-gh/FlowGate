"""Verify insertion, lookup, parsing, and counting of AI review child records.

A review is a document_reviews child record attached to its target document, not a document.
The server derives the finding count from findings; the AI does not provide the number.
Environment: TESTING=1 with temporary SQLite and no sqloader, mirroring test_documents.py fixtures.
"""
from __future__ import annotations

import os
import sqlite3
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path

import pytest

os.environ["TESTING"] = "1"
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing-only-32c")

_SERVER_DIR = Path(__file__).resolve().parents[1]
_MIGRATIONS_DIR = _SERVER_DIR / "sql" / "migrations" / "sqlite"
sys.path.insert(0, str(_SERVER_DIR))


def _migrations() -> list[Path]:
    return sorted(_MIGRATIONS_DIR.glob("*.sql"))


class _MockTxn:
    def __init__(self, conn):
        self._conn = conn
        self._cur = None

    def execute(self, sql, params=None):
        self._cur = self._conn.execute(sql, params or [])
        self._conn.commit()

    def fetchone(self):
        row = self._cur.fetchone() if self._cur else None
        return dict(row) if row else None

    def fetchall(self):
        return [dict(r) for r in self._cur.fetchall()] if self._cur else []


class _MockDB:
    def __init__(self, db_path: str):
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")

    def execute(self, sql, params=None):
        self._conn.execute(sql, params or [])
        self._conn.commit()

    def fetch_one(self, sql, params=None):
        row = self._conn.execute(sql, params or []).fetchone()
        return dict(row) if row else None

    def fetch_all(self, sql, params=None):
        return [dict(r) for r in self._conn.execute(sql, params or []).fetchall()]

    @contextmanager
    def begin_transaction(self):
        yield _MockTxn(self._conn)

    def close(self):
        self._conn.close()


@pytest.fixture(scope="module")
def tmp_db():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    mock_db = _MockDB(db_path)
    for mig in _migrations():
        try:
            mock_db._conn.executescript(mig.read_text(encoding="utf-8"))
        except sqlite3.OperationalError:
            pass
    yield mock_db, db_path
    mock_db.close()
    os.unlink(db_path)


@pytest.fixture(scope="module", autouse=True)
def patch_store(tmp_db):
    mock_db, _ = tmp_db
    from modules.flow_gate.db import connection as conn_mod

    original = conn_mod.STORE

    class _PatchedStore(conn_mod.FlowGateStore):
        def __init__(self):
            self._db = mock_db
            self._sq = None

        def _sql(self, key: str) -> str:  # document_reviews uses inline SQL, so this is not called.
            raise NotImplementedError("_sql not used in tests")

    conn_mod.STORE = _PatchedStore()
    yield
    conn_mod.STORE = original


@pytest.fixture(scope="module")
def seed_doc(tmp_db):
    from modules.flow_gate.db import projects, users
    from modules.flow_gate.documents import document_service

    projects.create({"project_id": "REVTEST", "project_name": "Review Test"})
    users.create({
        "user_id": "usr_rev_001",
        "username": "revuser",
        "email": "rev@example.com",
        "password": "hashed_pw",
    })
    document_service.create_document(
        {
            "doc_id": "REVTEST-T-0001",
            "project_id": "REVTEST",
            "type_code": "T",
            "seq": 1,
            "title": "검수 대상 작업지시",
        },
        actor_user_id="usr_rev_001",
    )
    yield "REVTEST-T-0001"


def test_insert_and_list_reviews_newest_first(seed_doc):
    from modules.flow_gate.db import document_reviews as db_reviews

    doc_id = seed_doc
    # Revision 0 passes with no findings; revision 1 has two findings.
    db_reviews.insert_review(
        doc_id=doc_id, revision_no=0, reviewer_id="usr_rev_001",
        verdict="pass", findings_json="[]", comment="초안 양호",
        reviewed_at="2026-06-07T10:30:00",
    )
    db_reviews.insert_review(
        doc_id=doc_id, revision_no=1, reviewer_id="usr_rev_001",
        verdict="issues",
        findings_json='[{"locus":"3.2","note":"에러처리 누락"},{"locus":"함수명","note":"컨벤션 불일치"}]',
        comment="2건 수정 후 승인 권장",
        reviewed_at="2026-06-09T14:22:00",
    )

    rows = db_reviews.list_by_doc(doc_id)
    assert len(rows) == 2
    # Newest first: revision 1 was inserted later and has the larger ID.
    assert rows[0]["revision_no"] == 1
    assert rows[1]["revision_no"] == 0

    latest = db_reviews.get_latest_by_doc(doc_id)
    assert latest["verdict"] == "issues"


def test_provider_provenance_is_snapshotted_and_exposed(seed_doc):
    from modules.flow_gate.db import document_reviews as db_reviews
    from modules.flow_gate.api.v1.document_routes import _shape_review

    row = db_reviews.insert_review(
        doc_id=seed_doc, revision_no=2, reviewer_id="usr_rev_001",
        verdict="pass", findings_json="[]", comment="fallback review",
        reviewed_at="2026-09-06T18:00:00",
        review_run_id="air_20260906_000001",
        requested_provider_id="aip_sonnet",
        actual_provider_id="aip_opus",
        actual_provider_name="Opus at review time",
        provider_source="fallback",
        attempt_no=2,
        fallback_used=True,
    )
    shaped = _shape_review(row)
    assert shaped["review_provider"] == {
        "run_id": "air_20260906_000001",
        "requested_provider_id": "aip_sonnet",
        "actual_provider_id": "aip_opus",
        "actual_provider_name": "Opus at review time",
        "provider_source": "fallback",
        "attempt_no": 2,
        "fallback_used": True,
    }
    # The name is a row snapshot: no provider-settings lookup participates in shaping.
    assert db_reviews.get_latest_by_doc(seed_doc)["actual_provider_name"] == "Opus at review time"


def test_legacy_review_provider_fields_are_nullable():
    from modules.flow_gate.api.v1.document_routes import _shape_review

    shaped = _shape_review({"findings": "[]"})
    assert shaped["review_provider"] == {
        "run_id": None,
        "requested_provider_id": None,
        "actual_provider_id": None,
        "actual_provider_name": None,
        "provider_source": None,
        "attempt_no": None,
        "fallback_used": None,
    }


def test_shape_review_derives_finding_count(seed_doc):
    """The server derives the finding count from findings; the AI does not provide it."""
    from modules.flow_gate.documents.routers.documents import _shape_review

    row = {
        "id": 9, "revision_no": 1, "reviewer_id": "ai",
        "verdict": "issues",
        "findings": '[{"locus":"a","note":"x"},{"locus":"b","note":"y"},{"locus":"c","note":"z"}]',
        "comment": "총평", "reviewed_at": "2026-06-09T14:22:00", "created_at": "2026-06-09T14:22:00",
    }
    shaped = _shape_review(row)
    assert shaped["finding_count"] == 3          # Computed by the server.
    assert len(shaped["findings"]) == 3          # Parsed successfully.
    assert shaped["verdict"] == "issues"

    # Malformed findings fall back defensively to zero.
    bad = dict(row, findings="not json")
    assert _shape_review(bad)["finding_count"] == 0


def test_load_ai_reviews_latest_and_history(seed_doc):
    """The detail-handler helper returns the latest review and full history."""
    from modules.flow_gate.documents.routers.documents import _load_ai_reviews

    latest, history = _load_ai_reviews(seed_doc)
    assert latest is not None
    assert latest["verdict"] == "pass"         # Provenance case inserted revision 2 last.
    assert latest["review_provider"]["actual_provider_name"] == "Opus at review time"
    assert len(history) == 3                    # Full history.
    assert history[0]["revision_no"] == 2
    assert history[-1]["revision_no"] == 0

    # A document with no reviews returns (None, []).
    none_latest, none_hist = _load_ai_reviews("REVTEST-T-9999")
    assert none_latest is None
    assert none_hist == []
