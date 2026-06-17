"""Verify the document_mention_copies store (R0001 group 0015 / NR0003 rev4 — option B header badge).

One row per (user, doc): the last mention block the user copied to hand the document off to an
AI worker. A fresh copy OVERWRITES the previous (UPSERT) because the badge shows only the latest.
Environment: TESTING=1 with temporary SQLite and no sqloader, mirroring test_document_reviews.py.
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

        def _sql(self, key: str) -> str:  # mention_copies uses inline SQL, so this is not called.
            raise NotImplementedError("_sql not used in tests")

    conn_mod.STORE = _PatchedStore()
    yield
    conn_mod.STORE = original


@pytest.fixture(scope="module")
def seed_docs(tmp_db):
    from modules.flow_gate.db import projects, users
    from modules.flow_gate.documents import document_service

    projects.create({"project_id": "MCTEST", "project_name": "Mention Copy Test"})
    users.create({
        "user_id": "usr_mc_001",
        "username": "mcuser",
        "email": "mc@example.com",
        "password": "hashed_pw",
    })
    users.create({
        "user_id": "usr_mc_002",
        "username": "mcuser2",
        "email": "mc2@example.com",
        "password": "hashed_pw",
    })
    for seq, doc_id in ((1, "MCTEST-T-0001"), (2, "MCTEST-T-0002")):
        document_service.create_document(
            {
                "doc_id": doc_id,
                "project_id": "MCTEST",
                "type_code": "T",
                "seq": seq,
                "title": f"작업지시 {seq}",
            },
            actor_user_id="usr_mc_001",
        )
    yield


def test_get_returns_none_before_any_copy(seed_docs):
    from modules.flow_gate.db import mention_copies as db_mc

    assert db_mc.get("usr_mc_001", "MCTEST-T-0001") is None


def test_upsert_records_and_get_reads_back(seed_docs):
    from modules.flow_gate.db import mention_copies as db_mc

    row = db_mc.upsert("usr_mc_001", "MCTEST-T-0001", "edit")
    assert row["mention_kind"] == "edit"
    assert row["copied_at"]

    got = db_mc.get("usr_mc_001", "MCTEST-T-0001")
    assert got is not None
    assert got["mention_kind"] == "edit"


def test_upsert_overwrites_previous_so_only_latest_remains(seed_docs):
    """The badge shows only the last copied mention — a second copy overwrites the first."""
    from modules.flow_gate.db import mention_copies as db_mc

    db_mc.upsert("usr_mc_001", "MCTEST-T-0002", "review")
    db_mc.upsert("usr_mc_001", "MCTEST-T-0002", "next_step")

    got = db_mc.get("usr_mc_001", "MCTEST-T-0002")
    assert got["mention_kind"] == "next_step"

    # Exactly one row per (user, doc) — UPSERT, not append.
    rows = db_mc.get_store()._fetch_all(
        "SELECT * FROM document_mention_copies WHERE user_id = ? AND doc_id = ?",
        ["usr_mc_001", "MCTEST-T-0002"],
    )
    assert len(rows) == 1


def test_state_is_per_user(seed_docs):
    """Copy state is the caller's own — one user's copy is invisible to another."""
    from modules.flow_gate.db import mention_copies as db_mc

    db_mc.upsert("usr_mc_001", "MCTEST-T-0001", "edit")
    assert db_mc.get("usr_mc_002", "MCTEST-T-0001") is None

    db_mc.upsert("usr_mc_002", "MCTEST-T-0001", "reject")
    assert db_mc.get("usr_mc_002", "MCTEST-T-0001")["mention_kind"] == "reject"
    # usr_mc_001 unaffected.
    assert db_mc.get("usr_mc_001", "MCTEST-T-0001")["mention_kind"] == "edit"
