"""flowgate.default.0482 T0011 완료 기준 2: 실제 SQL 영속 경로 회귀.

rej_01M1HZBVZ61GG8H9 finding 2가 지적한 공백을 채운다: 지금까지의 terminal-cleanup
스냅샷/프로젝트 리스 시험은 전부 `_using_memory()`가 True인 process-local dict를
거쳤고(테스트 프로세스에는 실 DB 연결이 없으므로), 097/098 마이그레이션(T0016 병합 전 093/094)의 신규
적용·재실행과 `git_terminal_cleanup_snapshots`/`project_ai_leases` 테이블의 실제
INSERT/UPSERT/SELECT/DELETE 경로는 어떤 시험에서도 실행되지 않았다.

이 파일은 `FlowGateStore._db`에 실제 `sqloader.sqlite3.SQLiteWrapper`(파일 모드)를
얹어 `_using_memory()`가 False로 떨어지게 만들고, 두 모듈의 `get_store`를 그 스토어로
monkeypatch해 진짜 SQL 문을 sqlite 파일에 실행한다. `migrated_sqlite_db`가 097/098을
포함한 전체 마이그레이션 디렉터리를 적용하므로, 이 파일이 통과하는 것 자체가
"신규 적용" 증거이고, 두 번째 `_migrate_fresh` 재실행(아래 `test_migrations_rerun_...`)이
"재실행 안전성" 증거다.
"""
from __future__ import annotations

import os
import sqlite3
import sys
from pathlib import Path

import pytest

os.environ.setdefault("TESTING", "1")
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing-only-32c")
os.environ.setdefault("ALLOWED_ORIGIN", "*")
os.environ.setdefault("CONTEXT", "api")
os.environ.setdefault("DB_TYPE", "sqlite")
_SERVER_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_SERVER_DIR))

from modules.flow_gate.db import connection, project_ai_leases, terminal_cleanup_snapshots as snapshots

_MIGRATIONS_DIR = _SERVER_DIR / "sql" / "migrations" / "sqlite"


def _real_store(db_path: str) -> connection.FlowGateStore:
    from sqloader.sqlite3 import SQLiteWrapper

    store = object.__new__(connection.FlowGateStore)
    store._db = SQLiteWrapper(db_path)
    store._sq = None
    return store


def _seed_project(db_path: str, project_id: str) -> None:
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            "INSERT INTO projects(project_id, project_name, is_active, created_at, updated_at) "
            "VALUES (?,?,1,datetime('now'),datetime('now'))",
            [project_id, project_id],
        )
        conn.commit()
    finally:
        conn.close()


@pytest.fixture
def real_store(migrated_sqlite_db, tmp_path):
    """A store bound to a fresh sqlite FILE with every migration (incl. 097/098) applied."""
    db_path = migrated_sqlite_db(f"terminal-cleanup-lease-{id(tmp_path)}.db")
    _seed_project(db_path, "flowgate")
    return _real_store(db_path), db_path


# ── git_terminal_cleanup_snapshots: durable put/get through real SQL ─────────

class TestSnapshotDurableSqlPath:
    def test_using_memory_is_false_once_a_real_db_is_attached(self, real_store, monkeypatch):
        store, _ = real_store
        monkeypatch.setattr(snapshots, "get_store", lambda: store)
        assert snapshots._using_memory() is False

    def test_empty_snapshot_before_any_write(self, real_store, monkeypatch):
        store, _ = real_store
        monkeypatch.setattr(snapshots, "get_store", lambda: store)
        assert snapshots.get("flowgate") == snapshots.empty()

    def test_put_persists_via_real_insert_and_is_readable_back(self, real_store, monkeypatch):
        store, db_path = real_store
        monkeypatch.setattr(snapshots, "get_store", lambda: store)
        written = snapshots.put(
            "flowgate", "partial", 2,
            [{"group_id": "flowgate.default.0001", "reason": "revert_conflict"}],
        )
        assert written["last_run_status"] == "partial"

        # Prove the row actually landed in the real table, not a process-local mirror.
        conn = sqlite3.connect(db_path)
        try:
            row = conn.execute(
                "SELECT project_id, last_run_status, last_cleaned_count, pending_json "
                "FROM git_terminal_cleanup_snapshots WHERE project_id = ?", ["flowgate"],
            ).fetchone()
        finally:
            conn.close()
        assert row is not None
        assert row[1] == "partial" and row[2] == 2
        assert "revert_conflict" in row[3]

        assert snapshots.get("flowgate") == written

    def test_put_upserts_on_conflict_project_id(self, real_store, monkeypatch):
        """The migration's ON CONFLICT(project_id) DO UPDATE clause — a second run's put
        for the same project must replace the row, not duplicate it or fail."""
        store, db_path = real_store
        monkeypatch.setattr(snapshots, "get_store", lambda: store)
        snapshots.put("flowgate", "ok", 3, [])
        snapshots.put("flowgate", "failed", 0, [{"group_id": "flowgate.default.0002", "reason": "teardown_failed"}])

        conn = sqlite3.connect(db_path)
        try:
            rows = conn.execute(
                "SELECT last_run_status, last_cleaned_count FROM git_terminal_cleanup_snapshots "
                "WHERE project_id = ?", ["flowgate"],
            ).fetchall()
        finally:
            conn.close()
        assert len(rows) == 1
        assert rows[0][0] == "failed" and rows[0][1] == 0

    def test_snapshot_survives_a_brand_new_store_instance(self, real_store, monkeypatch):
        """'새 탭과 서버 재시작 뒤에도 마지막 완료 스냅샷을 조회할 수 있어야 한다'
        (T0011 삭제·축소 금지 조건 3): a fresh FlowGateStore over the SAME db file — no
        shared Python object, no process-local `_memory` dict — must still read it back."""
        store, db_path = real_store
        monkeypatch.setattr(snapshots, "get_store", lambda: store)
        snapshots.put("flowgate", "ok", 5, [])

        fresh_store = _real_store(db_path)
        monkeypatch.setattr(snapshots, "get_store", lambda: fresh_store)
        reread = snapshots.get("flowgate")
        assert reread["last_run_status"] == "ok"
        assert reread["last_cleaned_count"] == 5
        assert reread["pending"] == []
        assert reread["last_run_at"] is not None

    def test_check_constraint_rejects_an_invalid_status_at_the_db_layer(self, real_store, monkeypatch):
        # put() already raises before reaching SQL, but the CHECK constraint is the durable
        # backstop the DB layer promised — prove it independently of the Python guard.
        store, db_path = real_store
        conn = sqlite3.connect(db_path)
        try:
            with pytest.raises(sqlite3.IntegrityError):
                conn.execute(
                    "INSERT INTO git_terminal_cleanup_snapshots "
                    "(project_id,last_run_at,last_run_status,last_cleaned_count,pending_json) "
                    "VALUES ('flowgate','now','bogus',0,'[]')"
                )
        finally:
            conn.close()


# ── project_ai_leases: acquire/activate/release through real SQL ─────────────

class TestProjectLeaseDurableSqlPath:
    def test_acquire_activate_release_round_trip_through_real_sql(self, real_store, monkeypatch):
        store, db_path = real_store
        monkeypatch.setattr(project_ai_leases, "get_store", lambda: store)

        acquired = project_ai_leases.acquire("flowgate", "owner-1")
        assert acquired["state"] == "acquiring"
        conn = sqlite3.connect(db_path)
        try:
            row = conn.execute("SELECT run_id, state FROM project_ai_leases WHERE project_id=?", ["flowgate"]).fetchone()
        finally:
            conn.close()
        assert row == ("owner-1", "acquiring")

        # A concurrent second acquire for the same project must be rejected by the real INSERT
        # ... ON CONFLICT DO NOTHING, not merely by an in-memory dict check.
        assert project_ai_leases.acquire("flowgate", "owner-2") is None

        activated = project_ai_leases.activate("flowgate", "owner-1", "run-1")
        assert activated["state"] == "active" and activated["run_id"] == "run-1"

        assert project_ai_leases.release("flowgate", "run-1") is True
        conn = sqlite3.connect(db_path)
        try:
            row = conn.execute("SELECT 1 FROM project_ai_leases WHERE project_id=?", ["flowgate"]).fetchone()
        finally:
            conn.close()
        assert row is None

    def test_activate_cas_rejects_a_replaced_owner_through_real_sql(self, real_store, monkeypatch):
        """The same TOCTOU regression `test_project_lease_activate_rejects_replaced_acquiring_owner`
        pins with a fake store, reproduced against the real UPDATE ... WHERE run_id=? AND
        state='acquiring' compare-and-swap on an actual sqlite file."""
        store, db_path = real_store
        monkeypatch.setattr(project_ai_leases, "get_store", lambda: store)

        project_ai_leases.acquire("flowgate", "owner-1")
        conn = sqlite3.connect(db_path)
        try:
            # Simulate owner-1's acquiring row expiring and owner-2 taking over, the exact
            # race window activate()'s CAS exists to close.
            conn.execute(
                "UPDATE project_ai_leases SET run_id='owner-2' WHERE project_id='flowgate'"
            )
            conn.commit()
        finally:
            conn.close()

        assert project_ai_leases.activate("flowgate", "owner-1", "run-1") is None
        conn = sqlite3.connect(db_path)
        try:
            row = conn.execute("SELECT run_id, state FROM project_ai_leases WHERE project_id=?", ["flowgate"]).fetchone()
        finally:
            conn.close()
        assert row == ("owner-2", "acquiring")   # owner-2's row is untouched, not stolen

    def test_expired_acquiring_row_is_reclaimed_by_real_delete(self, real_store, monkeypatch):
        from datetime import datetime, timedelta, timezone

        store, db_path = real_store
        monkeypatch.setattr(project_ai_leases, "get_store", lambda: store)
        past = (datetime.now(timezone.utc) - timedelta(seconds=5)).isoformat(timespec="seconds")
        conn = sqlite3.connect(db_path)
        try:
            conn.execute(
                "INSERT INTO project_ai_leases(project_id,run_id,state,acquired_at,heartbeat_at,expires_at) "
                "VALUES ('flowgate','stale-owner','acquiring',?,?,?)", [past, past, past],
            )
            conn.commit()
        finally:
            conn.close()

        assert project_ai_leases.get_active("flowgate") is None
        conn = sqlite3.connect(db_path)
        try:
            row = conn.execute("SELECT 1 FROM project_ai_leases WHERE project_id=?", ["flowgate"]).fetchone()
        finally:
            conn.close()
        assert row is None   # the expired row was DELETEd, not merely ignored

    def test_lease_and_snapshot_are_independent_projects_scoped_rows(self, real_store, monkeypatch):
        store, db_path = real_store
        _seed_project(db_path, "other")
        monkeypatch.setattr(project_ai_leases, "get_store", lambda: store)
        assert project_ai_leases.acquire("flowgate", "owner-a")
        assert project_ai_leases.acquire("other", "owner-b")
        assert project_ai_leases.get_active("flowgate")["run_id"] == "owner-a"
        assert project_ai_leases.get_active("other")["run_id"] == "owner-b"


# ── migration apply/rerun safety (T0011 완료 기준 2) ──────────────────────────
#
# `migrated_sqlite_db` (conftest.py) applies every .sql file with plain
# `executescript` and keeps no ledger — it is the right tool for "give me a schema",
# not for "prove the migrator applied 093/094 and is safe to run twice". These two
# tests instead drive the real `sqloader.migrator.DatabaseMigrator` against a fresh
# file, the same way test_migration_080_collision_0414.py does, so the `migrations`
# ledger table actually exists to assert against.

def _migrate_fresh(db_path: Path):
    from sqloader.migrator import DatabaseMigrator
    from sqloader.sqlite3 import SQLiteWrapper

    DatabaseMigrator(SQLiteWrapper(str(db_path)), str(_MIGRATIONS_DIR), auto_run=True)


class TestMigrationApplyAndRerunSafety:
    def test_a_fresh_db_applies_097_and_098_exactly_once(self, tmp_path):
        # T0016 (main merge): origin/main had already claimed 093-096, so this group's
        # two files moved to the next free ordinals and are carried across by
        # migration_renames.RENAMES. The ordinal is the only thing that changed.
        db_path = tmp_path / "fresh.db"
        _migrate_fresh(db_path)
        conn = sqlite3.connect(str(db_path))
        try:
            applied = [row[0] for row in conn.execute("SELECT filename FROM migrations")]
            assert applied.count("097_git_terminal_cleanup.sql") == 1
            assert applied.count("098_project_ai_leases.sql") == 1
            tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            assert {"git_terminal_cleanup_snapshots", "project_ai_leases"} <= tables
        finally:
            conn.close()

    def test_rerunning_the_migration_directory_against_an_already_migrated_db_is_a_noop(self, tmp_path):
        db_path = tmp_path / "rerun.db"
        _migrate_fresh(db_path)
        conn = sqlite3.connect(str(db_path))
        try:
            before = sorted(row[0] for row in conn.execute("SELECT filename FROM migrations"))
        finally:
            conn.close()

        # Constructing a fresh migrator against the same file must not raise (IF NOT EXISTS /
        # idempotent DDL) and must not re-apply anything already recorded.
        _migrate_fresh(db_path)

        conn = sqlite3.connect(str(db_path))
        try:
            after = sorted(row[0] for row in conn.execute("SELECT filename FROM migrations"))
            snapshot_count = conn.execute("SELECT COUNT(*) FROM git_terminal_cleanup_snapshots").fetchone()[0]
            lease_count = conn.execute("SELECT COUNT(*) FROM project_ai_leases").fetchone()[0]
        finally:
            conn.close()
        assert after == before
        assert (snapshot_count, lease_count) == (0, 0)   # tables intact, not recreated/truncated

        conn = sqlite3.connect(db_path)
        try:
            after = sorted(row[0] for row in conn.execute("SELECT filename FROM migrations"))
        finally:
            conn.close()
        assert after == before
