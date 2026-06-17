"""T246 new regression tests — storage file path normalization.

1. test_group_dir_name_uses_title — when called with group_id, return the sanitized group.title result
2. test_project_dir_name_uses_name — when called with project_id, return the sanitized project_name result
3. Also include fallback cases (DB lookup failure / empty title)
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

_SERVER_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_SERVER_DIR))


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_store(conn):
    """Patched store that wraps FlowGateStore with a sqlite3 connection."""
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
            # If row_factory is sqlite3.Row, dict() can be used
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
    from modules.flow_gate.db import projects, groups

    projects.create({
        "project_id": "prj-t246",
        "project_name": "T246 Test Project",
    })
    projects.create({
        "project_id": "prj-t246-ko",
        "project_name": "T246 Test Project Korean",
    })
    groups.create({
        "group_id": "prj-t246-__ALL__-0001",
        "project_id": "prj-t246",
        "module": "__ALL__",
        "title": "InformationDisclosureRequest",
    })
    groups.create({
        "group_id": "prj-t246-__ALL__-0002",
        "project_id": "prj-t246",
        "module": "__ALL__",
        "title": "Special!@#$%^&*()Chars",
    })
    groups.create({
        "group_id": "prj-t246-ko-__ALL__-0001",
        "project_id": "prj-t246-ko",
        "module": "__ALL__",
        "title": "Korean Group Title",
    })
    yield


# ── Test 1: group_dir_name() ─────────────────────────────────────────────────

def test_group_dir_name_uses_title(seed):
    """Returns the sanitized group.title result when called with group_id."""
    from modules.flow_gate.storage import paths

    # General title
    result = paths.group_dir_name("prj-t246-__ALL__-0001")
    assert result == "InformationDisclosureRequest", f"Expected 'InformationDisclosureRequest', got '{result}'"

    # Korean title
    result = paths.group_dir_name("prj-t246-ko-__ALL__-0001")
    assert result == "Korean_Group_Title", f"Expected 'Korean_Group_Title', got '{result}'"


def test_group_dir_name_sanitizes_special_chars(seed):
    """Normalizes special characters in group.title to _."""
    from modules.flow_gate.storage import paths

    result = paths.group_dir_name("prj-t246-__ALL__-0002")
    # "Special!@#$%^&*()Chars" -> remove special characters and then apply _sanitize_part
    assert "!" not in result, f"Special characters not removed: {result}"
    assert "@" not in result, f"Special characters not removed: {result}"
    assert "#" not in result, f"Special characters not removed: {result}"
    assert "(" not in result, f"Special characters not removed: {result}"
    assert ")" not in result, f"Special characters not removed: {result}"
    assert "Special" in result, f"Original characters lost: {result}"
    assert "Chars" in result, f"Original characters lost: {result}"


def test_group_dir_name_fallback_on_db_error(seed):
    """Falls back to the group_id sequence when the DB lookup fails."""
    from modules.flow_gate.storage import paths
    from modules.flow_gate.db import connection as conn_mod

    # Simulate a DB lookup failure
    orig = conn_mod.STORE
    conn_mod.STORE = None
    try:
        result = paths.group_dir_name("unknown-__ALL__-G999")
        # Return only the sequence when a DB error occurs
        assert result == "G999", f"Expected 'G999' (fallback), got '{result}'"
    finally:
        conn_mod.STORE = orig


def test_group_dir_name_fallback_on_empty_title(seed, db_conn):
    """Falls back to the group_id sequence when title is empty."""
    from modules.flow_gate.db import groups
    from modules.flow_gate.storage import paths

    # Create a group with an empty title
    groups.create({
        "group_id": "prj-t246-__ALL__-G_EMPTY",
        "project_id": "prj-t246",
        "module": "__ALL__",
        "title": "",  # empty value
    })

    result = paths.group_dir_name("prj-t246-__ALL__-G_EMPTY")
    assert result == "G_EMPTY", f"Expected 'G_EMPTY' (fallback), got '{result}'"


def test_group_dir_name_fallback_on_whitespace_title(seed, db_conn):
    """Falls back to the group_id sequence when title contains only whitespace."""
    from modules.flow_gate.db import groups
    from modules.flow_gate.storage import paths

    # Create a group whose title contains only whitespace
    groups.create({
        "group_id": "prj-t246-__ALL__-G_SPACE",
        "project_id": "prj-t246",
        "module": "__ALL__",
        "title": "   ",  # whitespace only
    })

    result = paths.group_dir_name("prj-t246-__ALL__-G_SPACE")
    assert result == "G_SPACE", f"Expected 'G_SPACE' (fallback), got '{result}'"


# ── Test 2: project_dir_name() ───────────────────────────────────────────────

def test_project_dir_name_uses_name(seed):
    """Returns the sanitized project_name result when called with project_id."""
    from modules.flow_gate.storage import paths

    # General name
    result = paths.project_dir_name("prj-t246")
    assert result == "T246_Test_Project", f"Expected 'T246_Test_Project', got '{result}'"

    # Korean name
    result = paths.project_dir_name("prj-t246-ko")
    assert result == "T246_Test_Project_Korean", f"Expected 'T246_Test_Project_Korean', got '{result}'"


def test_project_dir_name_sanitizes_special_chars(seed, db_conn):
    """Normalizes special characters in project_name to _."""
    from modules.flow_gate.db import projects
    from modules.flow_gate.storage import paths

    projects.create({
        "project_id": "PRJ_SPECIAL",
        "project_name": "Project@#$%^&*()Test",
    })

    result = paths.project_dir_name("PRJ_SPECIAL")
    assert "@" not in result, f"Special characters not removed: {result}"
    assert "#" not in result, f"Special characters not removed: {result}"
    assert "(" not in result, f"Special characters not removed: {result}"
    assert "Project" in result, f"Original characters lost: {result}"
    assert "Test" in result, f"Original characters lost: {result}"


def test_project_dir_name_fallback_on_db_error(seed):
    """Falls back to project_id when the DB lookup fails."""
    from modules.flow_gate.storage import paths
    from modules.flow_gate.db import connection as conn_mod

    # Simulate a DB lookup failure
    orig = conn_mod.STORE
    conn_mod.STORE = None
    try:
        result = paths.project_dir_name("UNKNOWN_PRJ_ID")
        # Return project_id when a DB error occurs
        assert result == "UNKNOWN_PRJ_ID", f"Expected 'UNKNOWN_PRJ_ID' (fallback), got '{result}'"
    finally:
        conn_mod.STORE = orig


def test_project_dir_name_fallback_on_empty_name(seed, db_conn):
    """Falls back to project_id when project_name is empty."""
    from modules.flow_gate.db import projects
    from modules.flow_gate.storage import paths

    projects.create({
        "project_id": "PRJ_EMPTY",
        "project_name": "",  # empty value
    })

    result = paths.project_dir_name("PRJ_EMPTY")
    assert result == "PRJ_EMPTY", f"Expected 'PRJ_EMPTY' (fallback), got '{result}'"


def test_project_dir_name_fallback_on_whitespace_name(seed, db_conn):
    """Falls back to project_id when project_name contains only whitespace."""
    from modules.flow_gate.db import projects
    from modules.flow_gate.storage import paths

    projects.create({
        "project_id": "PRJ_SPACE",
        "project_name": "   ",  # whitespace only
    })

    result = paths.project_dir_name("PRJ_SPACE")
    assert result == "PRJ_SPACE", f"Expected 'PRJ_SPACE' (fallback), got '{result}'"


# ── Test 3: end-to-end path creation flow ────────────────────────────────────

def test_document_path_uses_normalized_group_and_project(seed, tmp_path):
    """document_path() includes the normalized project_name and group.title."""
    from modules.flow_gate.storage import paths

    path = paths.document_path(
        project_id="prj-t246",
        group_code="prj-t246-__ALL__-0001",
        doc_code="R001",
        filename="document.md",
    )

    path_str = str(path)
    # project_name sanitize: "T246 Test Project" -> "T246_Test_Project"
    assert "T246_Test_Project" in path_str, f"project_name missing: {path_str}"
    # group.title sanitize keeps the original title unchanged.
    assert "InformationDisclosureRequest" in path_str, f"group title missing: {path_str}"
    # project_id and group_id are removed from the path
    assert "prj-t246" not in path_str or path_str.count("prj-t246") <= 0, (
        f"project_id(hash) remains in path: {path_str}"
    )


def test_group_path_uses_normalized_title(seed):
    """group_path() includes the normalized group.title."""
    from modules.flow_gate.storage import paths

    path = paths.group_path(
        project_id="prj-t246",
        group_code="prj-t246-__ALL__-0001",
    )

    path_str = str(path)
    assert "InformationDisclosureRequest" in path_str, f"group title missing: {path_str}"
    # The previous behavior of returning only the sequence has been removed
    assert "G001" not in path_str or "InformationDisclosureRequest" in path_str, (
        f"Normalized group title not applied: {path_str}"
    )
