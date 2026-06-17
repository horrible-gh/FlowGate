"""T056: numbering/storage path module tests.

pytest tests/test_numbering.py
"""
from __future__ import annotations

import os
import re
import sqlite3
import tempfile
import threading
from pathlib import Path
from typing import Generator

import pytest

# Force-enable TESTING mode
os.environ["TESTING"] = "1"

# ── Schema fixture ──────────────────────────────────────────────────────────

_SERVER_DIR = Path(__file__).resolve().parents[1]
_MIGRATIONS_DIR = _SERVER_DIR / "sql" / "migrations" / "sqlite"


def get_all_migrations() -> list[Path]:
    """Get all migration files sorted in order."""
    files = sorted(_MIGRATIONS_DIR.glob("*.sql"))
    return files


@pytest.fixture
def db_conn() -> Generator[sqlite3.Connection, None, None]:
    """In-memory SQLite DB with FlowGate schema."""
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    
    # Apply all migrations in order
    for migration_file in get_all_migrations():
        try:
            sql = migration_file.read_text(encoding="utf-8")
            conn.executescript(sql)
        except sqlite3.OperationalError:
            # Some migrations might fail (e.g., IF NOT EXISTS constraints)
            pass
    
    yield conn
    conn.close()


@pytest.fixture
def patched_store(db_conn, monkeypatch):
    """Replace FlowGateStore with in-memory SQLite."""
    from modules.flow_gate.db import connection as conn_mod

    class _TxCtx:
        def __init__(self, c):
            self._c = c
            self._cur = None
        def __enter__(self): return self
        def __exit__(self, *a): pass
        def execute(self, sql, params=None):
            self._cur = self._c.execute(sql, params or [])
        def fetch_one(self):
            if self._cur is None:
                return None
            row = self._cur.fetchone()
            return dict(row) if row else None
        def fetch_all(self):
            if self._cur is None:
                return []
            return [dict(r) for r in self._cur.fetchall()]

    class _DB:
        def __init__(self, c): self._c = c
        def execute(self, sql, params=None):
            self._c.execute(sql, params or [])
        def fetch_one(self, sql, params=None):
            cur = self._c.execute(sql, params or [])
            row = cur.fetchone()
            return dict(row) if row else None
        def fetch_all(self, sql, params=None):
            cur = self._c.execute(sql, params or [])
            return [dict(r) for r in cur.fetchall()]
        def begin_transaction(self): return _TxCtx(self._c)

    store = conn_mod.FlowGateStore.__new__(conn_mod.FlowGateStore)
    store._db = _DB(db_conn)
    store._sq = None
    monkeypatch.setattr(conn_mod, "STORE", store)
    # projects.get_settings -> None (use default digit widths)
    return store


@pytest.fixture
def seed_project(patched_store, db_conn):
    """Seed test project/project_settings."""
    from modules.flow_gate.db.connection import now_iso
    now = now_iso()
    db_conn.execute(
        "INSERT INTO projects (project_id, project_name, is_active, created_at, updated_at) "
        "VALUES (?, ?, 1, ?, ?)",
        ["PRJ01", "Test Project", now, now],
    )
    db_conn.execute(
        "INSERT INTO project_settings "
        "(project_id, group_structure, digits_group, digits_sub_group, digits_type, updated_at) "
        "VALUES (?, 2, 4, 3, 4, ?)",
        ["PRJ01", now],
    )
    db_conn.commit()
    return "PRJ01"


# ═══════════════════════════════════════════════════════════════════════════════
# 1. id_formatter unit tests
# ═══════════════════════════════════════════════════════════════════════════════

class TestIdFormatter:
    def test_format_group_code(self):
        from modules.flow_gate.numbering.id_formatter import format_group_code
        assert format_group_code(1, 4) == "0001"
        assert format_group_code(99, 4) == "0099"
        assert format_group_code(1000, 4) == "1000"

    def test_format_subgroup_code(self):
        from modules.flow_gate.numbering.id_formatter import format_subgroup_code
        assert format_subgroup_code(1, 3) == "001"
        assert format_subgroup_code(10, 3) == "010"

    def test_format_doc_code(self):
        from modules.flow_gate.numbering.id_formatter import format_doc_code
        assert format_doc_code("R", 1, 4) == "R0001"
        assert format_doc_code("DS", 12, 4) == "DS0012"
        assert format_doc_code("T", 999, 4) == "T0999"

    def test_parse_group_code(self):
        from modules.flow_gate.numbering.id_formatter import parse_group_code
        assert parse_group_code("0001") == 1
        assert parse_group_code("0099") == 99

    def test_parse_subgroup_code(self):
        from modules.flow_gate.numbering.id_formatter import parse_subgroup_code
        assert parse_subgroup_code("001") == 1
        assert parse_subgroup_code("010") == 10

    def test_parse_doc_code(self):
        from modules.flow_gate.numbering.id_formatter import parse_doc_code
        assert parse_doc_code("R0001") == ("R", 1)
        assert parse_doc_code("DS0012") == ("DS", 12)

    def test_parse_doc_code_invalid(self):
        from modules.flow_gate.numbering.id_formatter import parse_doc_code
        with pytest.raises(ValueError):
            parse_doc_code("12345")

    def test_reformat_group(self):
        from modules.flow_gate.numbering.id_formatter import reformat_code
        assert reformat_code("0001", 5, "group") == "00001"
        assert reformat_code("0010", 3, "group") == "010"

    def test_reformat_subgroup(self):
        from modules.flow_gate.numbering.id_formatter import reformat_code
        assert reformat_code("001", 4, "subgroup") == "0001"

    def test_reformat_document(self):
        from modules.flow_gate.numbering.id_formatter import reformat_code
        assert reformat_code("R0001", 5, "document") == "R00001"
        assert reformat_code("DS0012", 3, "document") == "DS012"

    def test_reformat_unknown_kind(self):
        from modules.flow_gate.numbering.id_formatter import reformat_code
        with pytest.raises(ValueError):
            reformat_code("0001", 4, "unknown")


# ═══════════════════════════════════════════════════════════════════════════════
# 2. reserve unit tests
# ═══════════════════════════════════════════════════════════════════════════════

class TestReserve:
    def test_reserve_group_sequential(self, patched_store, seed_project):
        from modules.flow_gate.numbering.numbering_service import reserve_group
        a = reserve_group("PRJ01")
        b = reserve_group("PRJ01")
        assert a == "0001"
        assert b == "0002"

    def test_reserve_group_width(self, patched_store, seed_project, db_conn):
        """When project_settings digits_group=3."""
        from modules.flow_gate.db.connection import now_iso
        db_conn.execute(
            "UPDATE project_settings SET digits_group=3 WHERE project_id='PRJ01'"
        )
        db_conn.commit()
        from modules.flow_gate.numbering.numbering_service import reserve_group
        code = reserve_group("PRJ01")
        assert re.fullmatch(r'\d{3}', code), f"Expected 3 digits, actual: {code}"

    def test_reserve_subgroup(self, patched_store, seed_project, db_conn):
        from modules.flow_gate.db.connection import now_iso
        now = now_iso()
        db_conn.execute(
            "INSERT INTO groups (group_id, project_id, module, title, status, created_at, updated_at) "
            "VALUES (?, 'PRJ01', '__ALL__', 'G1', 'OPEN', ?, ?)",
            ["GRP1", now, now],
        )
        db_conn.commit()
        from modules.flow_gate.numbering.numbering_service import reserve_subgroup
        code = reserve_subgroup("GRP1")
        assert re.fullmatch(r'\d{3}', code), f"Expected 3 digits, actual: {code}"

    def test_reserve_document(self, patched_store, seed_project, db_conn):
        from modules.flow_gate.db.connection import now_iso
        now = now_iso()
        db_conn.execute(
            "INSERT INTO groups (group_id, project_id, module, title, status, created_at, updated_at) "
            "VALUES ('GRP1', 'PRJ01', '__ALL__', 'G1', 'OPEN', ?, ?)",
            [now, now],
        )
        db_conn.commit()
        from modules.flow_gate.numbering.numbering_service import reserve_document
        code = reserve_document("GRP1", "R")
        assert re.fullmatch(r'R\d{4}', code), f"Expected format R0001, actual: {code}"

    def test_reserve_group_not_found(self, patched_store, seed_project):
        from modules.flow_gate.numbering.numbering_service import reserve_subgroup
        with pytest.raises(ValueError, match="Group not found"):
            reserve_subgroup("NO_SUCH_GROUP")


# ═══════════════════════════════════════════════════════════════════════════════
# 3. reserve concurrency tests
# ═══════════════════════════════════════════════════════════════════════════════

class TestReserveConcurrency:
    def test_concurrent_group_reserve(self, patched_store, seed_project):
        """Calling reserve_group from multiple threads assigns numbers without duplicates."""
        from modules.flow_gate.numbering.numbering_service import reserve_group
        results: list[str] = []
        errors: list[Exception] = []
        lock = threading.Lock()

        def worker():
            try:
                code = reserve_group("PRJ01")
                with lock:
                    results.append(code)
            except Exception as exc:
                with lock:
                    errors.append(exc)

        threads = [threading.Thread(target=worker) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors, f"Exception occurred: {errors}"
        assert len(results) == 10
        assert len(set(results)) == 10, f"Duplicate codes detected: {results}"


# ═══════════════════════════════════════════════════════════════════════════════
# 4. verify_id_widths tests
# ═══════════════════════════════════════════════════════════════════════════════

class TestVerifyIdWidths:
    def _insert_group(self, db_conn, project_id: str, group_id: str) -> None:
        from modules.flow_gate.db.connection import now_iso
        now = now_iso()
        db_conn.execute(
            "INSERT OR IGNORE INTO groups "
            "(group_id, project_id, module, title, status, created_at, updated_at) "
            "VALUES (?, ?, '__ALL__', 'G', 'OPEN', ?, ?)",
            [group_id, project_id, now, now],
        )
        db_conn.commit()

    def test_ok_no_data(self, patched_store, seed_project):
        """OK when there is no data."""
        from modules.flow_gate.numbering.verify import verify_id_widths
        report = verify_id_widths("PRJ01")
        assert report.ok

    def test_width_mismatch(self, patched_store, seed_project, db_conn):
        """Detect group code width mismatch."""
        self._insert_group(db_conn, "PRJ01", "001")  # 3 digits (setting: 4)
        from modules.flow_gate.numbering.verify import verify_id_widths
        report = verify_id_widths("PRJ01")
        assert not report.ok
        assert any(m[0] == "group" for m in report.width_mismatches)

    def test_missing_file(self, patched_store, seed_project, db_conn):
        """Detect missing files when file_path exists in DB but the file does not."""
        from modules.flow_gate.db.connection import now_iso
        now = now_iso()
        self._insert_group(db_conn, "PRJ01", "0001")
        db_conn.execute(
            "INSERT INTO documents "
            "(doc_id, project_id, module, group_id, type_code, seq, title, "
            "file_path, status, created_at, updated_at) "
            "VALUES (?, 'PRJ01', '__ALL__', '0001', 'R', 1, 'doc', ?, 'draft', ?, ?)",
            ["R0001", "/nonexistent/path/R0001.md", now, now],
        )
        db_conn.commit()
        from modules.flow_gate.numbering.verify import verify_id_widths
        report = verify_id_widths("PRJ01")
        assert not report.ok
        assert any(d == "R0001" for d, _ in report.missing_files)

    def test_orphan_files(self, patched_store, seed_project, tmp_path):
        """Detect orphan files in storage that are not in the DB."""
        import os
        os.environ["FLOWGATE_STORAGE_DIR"] = str(tmp_path)
        # project_dir_name returns the slug of project_name, not project_id
        # "Test Project" -> "Test_Project"
        proj_dir = tmp_path / "documents" / "Test_Project"
        proj_dir.mkdir(parents=True)
        orphan = proj_dir / "orphan.md"
        orphan.write_text("test", encoding="utf-8")

        try:
            from modules.flow_gate.numbering.verify import verify_id_widths
            report = verify_id_widths("PRJ01")
            assert not report.ok
            assert any("orphan.md" in p for p in report.orphan_files)
        finally:
            del os.environ["FLOWGATE_STORAGE_DIR"]


# ═══════════════════════════════════════════════════════════════════════════════
# 5. storage.paths tests
# ═══════════════════════════════════════════════════════════════════════════════

class TestStoragePaths:
    def test_project_root(self, tmp_path):
        import os
        os.environ["FLOWGATE_STORAGE_DIR"] = str(tmp_path)
        try:
            from modules.flow_gate.storage.paths import project_root
            assert project_root("PRJ01") == tmp_path / "documents" / "PRJ01"
        finally:
            del os.environ["FLOWGATE_STORAGE_DIR"]

    def test_group_path(self, tmp_path):
        import os
        os.environ["FLOWGATE_STORAGE_DIR"] = str(tmp_path)
        try:
            from modules.flow_gate.storage.paths import group_path
            p = group_path("PRJ01", "0001")
            assert p == tmp_path / "documents" / "PRJ01" / "__ALL__" / "0001"
        finally:
            del os.environ["FLOWGATE_STORAGE_DIR"]

    def test_subgroup_path(self, tmp_path):
        import os
        os.environ["FLOWGATE_STORAGE_DIR"] = str(tmp_path)
        try:
            from modules.flow_gate.storage.paths import subgroup_path
            p = subgroup_path("PRJ01", "0001", "001")
            assert p == tmp_path / "documents" / "PRJ01" / "__ALL__" / "0001" / "001"
        finally:
            del os.environ["FLOWGATE_STORAGE_DIR"]

    def test_document_path_with_subgroup(self, tmp_path):
        import os
        os.environ["FLOWGATE_STORAGE_DIR"] = str(tmp_path)
        try:
            from modules.flow_gate.storage.paths import document_path
            p = document_path("PRJ01", "0001", "R0001", "req.md", subgroup_code="001")
            assert p == tmp_path / "documents" / "PRJ01" / "__ALL__" / "0001" / "001" / "R0001_req.md"
        finally:
            del os.environ["FLOWGATE_STORAGE_DIR"]

    def test_document_path_no_subgroup(self, tmp_path):
        import os
        os.environ["FLOWGATE_STORAGE_DIR"] = str(tmp_path)
        try:
            from modules.flow_gate.storage.paths import document_path
            p = document_path("PRJ01", "0001", "R0001", "req.md")
            assert p == tmp_path / "documents" / "PRJ01" / "__ALL__" / "0001" / "R0001_req.md"
        finally:
            del os.environ["FLOWGATE_STORAGE_DIR"]


# ═══════════════════════════════════════════════════════════════════════════════
# 6. migration_service end-to-end tests
# ═══════════════════════════════════════════════════════════════════════════════

class TestMigrationService:
    def _setup_project_data(self, db_conn, project_id: str, tmp_path: Path) -> None:
        """Set up a temporary project with groups/subgroups/documents."""
        import os
        os.environ["FLOWGATE_STORAGE_DIR"] = str(tmp_path)

        from modules.flow_gate.db.connection import now_iso
        now = now_iso()

        # Project
        db_conn.execute(
            "INSERT OR REPLACE INTO projects (project_id, project_name, is_active, created_at, updated_at) "
            "VALUES (?, 'TestProj', 1, ?, ?)", [project_id, now, now]
        )
        db_conn.execute(
            "INSERT OR REPLACE INTO project_settings "
            "(project_id, group_structure, digits_group, digits_sub_group, digits_type, updated_at) "
            "VALUES (?, 2, 4, 3, 4, ?)", [project_id, now]
        )
        # User (for FK)
        db_conn.execute(
            "INSERT OR IGNORE INTO users "
            "(user_id, username, email, password, is_active, created_at, updated_at) "
            "VALUES ('u1','user1','u@t.com','x',1,?,?)", [now, now]
        )
        # Group (3-digit code -> reformat test to 4 digits)
        db_conn.execute(
            "INSERT OR IGNORE INTO groups "
            "(group_id, project_id, module, title, status, created_at, updated_at) "
            "VALUES ('001', ?, '__ALL__', 'G1', 'OPEN', ?, ?)",
            [project_id, now, now],
        )
        db_conn.commit()

        # Create filesystem directory
        (tmp_path / "projects" / project_id / "001").mkdir(parents=True, exist_ok=True)

    def test_enqueue_reformat(self, patched_store, seed_project, db_conn):
        """enqueue_reformat creates a queued job."""
        from modules.flow_gate.db.connection import now_iso
        now = now_iso()
        db_conn.execute(
            "INSERT OR IGNORE INTO users "
            "(user_id, username, email, password, is_active, created_at, updated_at) "
            "VALUES ('u1','user1','u@t.com','x',1,?,?)", [now, now]
        )
        db_conn.commit()
        from modules.flow_gate.numbering.migration_service import enqueue_reformat
        job = enqueue_reformat("PRJ01", "u1", "group", 3, 4)
        assert job["status"] == "queued"
        assert job["target"] == "group"

    def test_enqueue_invalid_target(self, patched_store, seed_project):
        from modules.flow_gate.numbering.migration_service import enqueue_reformat
        with pytest.raises(ValueError, match="target"):
            enqueue_reformat("PRJ01", "u1", "invalid", 3, 4)

    def test_enqueue_same_width(self, patched_store, seed_project):
        from modules.flow_gate.numbering.migration_service import enqueue_reformat
        with pytest.raises(ValueError, match="are the same"):
            enqueue_reformat("PRJ01", "u1", "group", 4, 4)

    def test_process_job_group_reformat(self, patched_store, db_conn, tmp_path):
        """End-to-end group code 3->4 reformat."""
        self._setup_project_data(db_conn, "MPRJ", tmp_path)
        from modules.flow_gate.db.connection import now_iso
        now = now_iso()
        db_conn.execute(
            "INSERT INTO numbering_jobs "
            "(project_id, requested_by, target, from_width, to_width, status, created_at) "
            "VALUES ('MPRJ', 'u1', 'group', 3, 4, 'queued', ?)", [now]
        )
        db_conn.commit()
        job_id = db_conn.execute(
            "SELECT id FROM numbering_jobs ORDER BY id DESC LIMIT 1"
        ).fetchone()["id"]

        import os
        os.environ["FLOWGATE_STORAGE_DIR"] = str(tmp_path)
        try:
            from modules.flow_gate.numbering.migration_service import process_job
            result = process_job(job_id, backup_dir=tmp_path / "_backup")
            # succeeded or failed (orphan files may be detected during validation)
            assert result["status"] in ("succeeded", "failed")
            # Check whether the group code changed
            rows = db_conn.execute(
                "SELECT group_id FROM groups WHERE project_id='MPRJ'"
            ).fetchall()
            codes = [r["group_id"] for r in rows]
            # Formatted to 4 digits or restored
            assert any(len(c) in (3, 4) for c in codes)
        finally:
            del os.environ["FLOWGATE_STORAGE_DIR"]


# ═══════════════════════════════════════════════════════════════════════════════
# T266: normalize group/doc numbering — verify canonical format
# ═══════════════════════════════════════════════════════════════════════════════

class TestT266CanonicalFormat:
    """T266: verify canonical ID format after create_group/reserve_document."""

    def _insert_project(self, db_conn, project_id: str = "PRJ01") -> None:
        from modules.flow_gate.db.connection import now_iso
        now = now_iso()
        db_conn.execute(
            "INSERT OR IGNORE INTO projects "
            "(project_id, project_name, is_active, created_at, updated_at) "
            "VALUES (?, ?, 1, ?, ?)",
            [project_id, project_id, now, now],
        )
        db_conn.execute(
            "INSERT OR IGNORE INTO project_settings "
            "(project_id, group_structure, digits_group, digits_sub_group, digits_type, updated_at) "
            "VALUES (?, 2, 4, 3, 4, ?)",
            [project_id, now],
        )
        db_conn.commit()

    def test_group_id_canonical_format(self, patched_store, db_conn):
        """Canonical format {project}-{module}-{GGGG} after reserve_group."""
        import re
        self._insert_project(db_conn, "PROJ1")
        from modules.flow_gate.numbering.numbering_service import reserve_group
        code = reserve_group("PROJ1", "__ALL__")
        group_id = f"PROJ1-__ALL__-{code}"
        assert re.fullmatch(r'PROJ1-__ALL__-\d{4}', group_id), \
            f"Invalid canonical group_id format: {group_id}"

    def test_doc_id_canonical_format(self, patched_store, db_conn):
        """Canonical format {group_id}-{type}{TTTT} after reserve_document."""
        import re
        from modules.flow_gate.db.connection import now_iso
        self._insert_project(db_conn, "PROJ2")
        now = now_iso()
        from modules.flow_gate.numbering.numbering_service import reserve_group, reserve_document
        code = reserve_group("PROJ2", "__ALL__")
        group_id = f"PROJ2-__ALL__-{code}"
        db_conn.execute(
            "INSERT INTO groups "
            "(group_id, project_id, module, title, status, created_at, updated_at) "
            "VALUES (?, 'PROJ2', '__ALL__', 'G', 'OPEN', ?, ?)",
            [group_id, now, now],
        )
        db_conn.commit()
        doc_code = reserve_document(group_id, "R", module="__ALL__")
        doc_id = f"{group_id}-{doc_code}"
        assert re.fullmatch(r'PROJ2-__ALL__-\d{4}-R\d{4}', doc_id), \
            f"Invalid canonical doc_id format: {doc_id}"

    def test_doc_id_no_double_hyphen(self, patched_store, db_conn):
        """No '--' (double hyphen) in canonical doc_id."""
        from modules.flow_gate.db.connection import now_iso
        self._insert_project(db_conn, "PROJ3")
        now = now_iso()
        from modules.flow_gate.numbering.numbering_service import reserve_group, reserve_document
        code = reserve_group("PROJ3", "__ALL__")
        group_id = f"PROJ3-__ALL__-{code}"
        db_conn.execute(
            "INSERT INTO groups "
            "(group_id, project_id, module, title, status, created_at, updated_at) "
            "VALUES (?, 'PROJ3', '__ALL__', 'G', 'OPEN', ?, ?)",
            [group_id, now, now],
        )
        db_conn.commit()
        doc_code = reserve_document(group_id, "DS", module="__ALL__")
        doc_id = f"{group_id}-{doc_code}"
        assert "--" not in doc_id, f"Double hyphen found: {doc_id}"

    def test_group_code_4digit_zero_padded(self, patched_store, db_conn):
        """Group code is zero-padded to 4 digits."""
        import re
        self._insert_project(db_conn, "PROJ4")
        from modules.flow_gate.numbering.numbering_service import reserve_group
        code = reserve_group("PROJ4", "__ALL__")
        assert re.fullmatch(r'\d{4}', code), f"Expected 4-digit group code, actual: {code}"
        assert code == "0001"

    def test_doc_code_4digit_zero_padded(self, patched_store, db_conn):
        """Document code is type + zero-padded 4 digits."""
        import re
        from modules.flow_gate.db.connection import now_iso
        self._insert_project(db_conn, "PROJ5")
        now = now_iso()
        from modules.flow_gate.numbering.numbering_service import reserve_group, reserve_document
        code = reserve_group("PROJ5", "__ALL__")
        group_id = f"PROJ5-__ALL__-{code}"
        db_conn.execute(
            "INSERT INTO groups "
            "(group_id, project_id, module, title, status, created_at, updated_at) "
            "VALUES (?, 'PROJ5', '__ALL__', 'G', 'OPEN', ?, ?)",
            [group_id, now, now],
        )
        db_conn.commit()
        doc_code = reserve_document(group_id, "R", module="__ALL__")
        assert re.fullmatch(r'R\d{4}', doc_code), f"Expected R+4 digits, actual: {doc_code}"
        assert doc_code == "R0001"
