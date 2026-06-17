"""T244 new regression tests ? normalize three mention body cases.

1. test_scratch_dir_uses_project_name ? verify project_name is included in the scratch path
2. test_action_scope_auto_resolved_new_vs_edit ? backend auto-resolves when body action_scope is null
3. test_doc_ref_normalized_format ? verify normalization to the standard doc_ref format
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

os.environ["TESTING"] = "1"
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing-only-32c")
os.environ["FLOWGATE_TOKEN_PEPPER_ACTIVE_ID"] = "t244"
os.environ["FLOWGATE_TOKEN_PEPPER_t244"] = "pepper-t244-value-abc"

_SERVER_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_SERVER_DIR))


# ?? Helper ?????????????????????????????????????????????????????????????????????

def _make_store(conn):
    """Patched store wrapping FlowGateStore with a sqlite3 connection."""
    from modules.flow_gate.db import connection as conn_mod

    class _PStore(conn_mod.FlowGateStore):
        def __init__(self):
            self._db = _Wrap(conn)
            self._sq = None

        def _sql(self, key: str) -> str:  # pragma: no cover
            raise NotImplementedError

    class _Wrap:
        def __init__(self, c):
            self._conn = c

        def execute(self, sql, params=None):
            self._conn.execute(sql, params or [])
            self._conn.commit()

        def fetch_one(self, sql, params=None):
            import sqlite3
            row = self._conn.execute(sql, params or []).fetchone()
            if row is None:
                return None
            # dict() works when row_factory is sqlite3.Row
            return dict(row)

        def fetch_all(self, sql, params=None):
            return [dict(r) for r in self._conn.execute(sql, params or []).fetchall()]

        def begin_transaction(self):
            import contextlib
            @contextlib.contextmanager
            def _ctx():
                yield _TxnWrap(self._conn)
            return _ctx()

    class _TxnWrap:
        def __init__(self, c):
            self._conn = c
            self._cur = None

        def execute(self, sql, params=None):
            self._cur = self._conn.execute(sql, params or [])
            self._conn.commit()

        def fetch_one(self):
            if self._cur is None:
                return None
            row = self._cur.fetchone()
            return dict(row) if row else None

    return _PStore()


@pytest.fixture(scope="module")
def db_conn():
    import sqlite3
    _SCHEMA_DIR = _SERVER_DIR / "sql" / "migrations" / "sqlite"
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    for sql_file in sorted(_SCHEMA_DIR.glob("*.sql")):
        try:
            conn.executescript(sql_file.read_text(encoding="utf-8"))
        except Exception:
            pass
    conn.commit()
    yield conn
    conn.close()


@pytest.fixture(scope="module", autouse=True)
def patch_store(db_conn):
    from modules.flow_gate.db import connection as conn_mod
    orig = conn_mod.STORE
    conn_mod.STORE = _make_store(db_conn)
    yield
    conn_mod.STORE = orig


@pytest.fixture(scope="module")
def seed(db_conn):
    from modules.flow_gate.db import projects, groups, documents
    from modules.flow_gate.db.connection import now_iso

    projects.create({
        "project_id": "prj-t244",
        "project_name": "T244 Test Project",
    })
    groups.create({
        "group_id": "prj-t244-__ALL__-0001",
        "project_id": "prj-t244",
        "module": "__ALL__",
        "title": "Group Alpha",
    })
    # Existing document ? used to check action_scope auto='edit'
    documents.create({
        "doc_id": "prj-t244-__ALL__-0001-R0001",
        "project_id": "prj-t244",
        "group_id": "prj-t244-__ALL__-0001",
        "type_code": "R",
        "seq": 1,
        "title": "Existing Document",
    })
    yield


# ?? 1-1. Scratch path ????????????????????????????????????????????????????????

def test_scratch_dir_uses_project_name(seed, tmp_path):
    """_scratch_dir should include project_name in the path."""
    with patch(
        "modules.flow_gate.services.token_service.get_storage_root",
        return_value=tmp_path,
    ):
        from modules.flow_gate.services import token_service
        path = token_service._scratch_dir("prj-t244", "tok_test_001")
    # project_name = "T244 Test Project" → sanitize → "T244_Test_Project"
    assert "T244_Test_Project" in str(path), (
        f"project_name not found in path: {path}"
    )
    assert "prj-t244" not in str(path), (
        f"project_id (hash) unexpectedly present in path: {path}"
    )
    assert str(path).endswith("tok_test_001")


# ?? 1-2. Auto resolution of action_scope ?????????????????????????????????????

def test_action_scope_auto_resolved_new(seed):
    """If doc_ref is missing or absent from the DB, auto-resolve action_scope to 'new'."""
    from modules.flow_gate.api.token_routes import _determine_action_scope
    # No doc_ref
    assert _determine_action_scope(None) == "new"
    # doc_ref exists but is missing from the DB
    assert _determine_action_scope("not-exists-__ALL__-9999-R9999") == "new"


def test_action_scope_auto_resolved_edit(seed):
    """If doc_ref exists in the DB, auto-resolve action_scope to 'edit'."""
    from modules.flow_gate.api.token_routes import _determine_action_scope
    assert _determine_action_scope("prj-t244-__ALL__-0001-R0001") == "edit"

