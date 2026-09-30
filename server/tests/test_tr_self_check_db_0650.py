"""Tests for TR Self-check database schema, repository, and project settings (flowgate.default.0650 T0002).

Covers all 23 items from flowgate.default.0650.0001-R §11:
1. existing Project migration -> tr_self_check_enabled=false
2. fresh schema default false
3. Self-check run insert
4. FK cascade / requested_by SET NULL
5. status constraint
6. recovery_state constraint
7. active_key unique
8. multiple terminal rows with NULL active_key allowed
9. same project/group active duplicate rejected (SelfCheckAlreadyRunningError)
10. different group active rows allowed
11. terminal transition clears active_key
12. recovery_incomplete keeps active_key
13. cancel_requested update
14. orphan active query
15. recovery_incomplete project query
16. document pagination ordering (created_at DESC, run_id DESC)
17. cleanup/purge candidate purge
18. active/recovery_incomplete/cleanup_pending protected from purge
19. settings read includes tr_self_check_enabled
20. settings update works
21. boolean validation on update
22. existing settings fields no regression
23. new/existing projects default to false
"""
from __future__ import annotations

import importlib
import os
import sys
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

_SERVER_DIR = Path(__file__).resolve().parents[1]
if str(_SERVER_DIR) not in sys.path:
    sys.path.insert(0, str(_SERVER_DIR))

from modules.flow_gate.auth.middleware import get_current_user
from modules.flow_gate.db import connection
from modules.flow_gate.db import projects as projects_db
from modules.flow_gate.db import tr_self_check_runs as selfcheck_db
from modules.flow_gate.settings import project_settings_service as settings_svc
from modules.flow_gate.settings.routers.project_settings import router as settings_router

_MIGRATION_SQLITE = Path(__file__).resolve().parents[1] / "sql" / "migrations" / "sqlite" / "127_tr_self_check_runs.sql"
_MIGRATION_MYSQL = Path(__file__).resolve().parents[1] / "sql" / "migrations" / "mysql" / "127_tr_self_check_runs.sql"
_MIGRATION_POSTGRES = Path(__file__).resolve().parents[1] / "sql" / "migrations" / "postgres" / "127_tr_self_check_runs.sql"


@pytest.fixture(scope="module")
def test_db_path(migrated_sqlite_db):
    return migrated_sqlite_db(
        "test_tr_self_check_0650.db",
        seed_sql="""
        INSERT OR IGNORE INTO roles(role_id,role_name,is_system,created_at,updated_at)
            VALUES('role_admin','Administrator',1,datetime('now'),datetime('now'));
        INSERT OR IGNORE INTO permissions(permission_id,permission_name,created_at)
            VALUES
                ('system.settings.manage','System settings management',datetime('now')),
                ('project.settings.read','Project settings read',datetime('now')),
                ('project.settings.edit','Project settings edit',datetime('now'));
        INSERT OR IGNORE INTO role_permissions(role_id,permission_id)
            VALUES
                ('role_admin','system.settings.manage'),
                ('role_admin','project.settings.read'),
                ('role_admin','project.settings.edit');
        """,
    )


def _real_store(db_path: str) -> connection.FlowGateStore:
    from sqloader.sqlite3 import SQLiteWrapper

    store = object.__new__(connection.FlowGateStore)
    store._db = SQLiteWrapper(db_path)
    store._sq = None
    return store


@pytest.fixture
def mock_db(test_db_path):
    store = _real_store(test_db_path)
    import modules.flow_gate.db.connection as _conn
    _real_get_store = _conn.get_store
    _modules = [
        importlib.import_module(_name)
        for _name in (
            "modules.flow_gate.db.connection",
            "modules.flow_gate.db.system_settings",
            "modules.flow_gate.db.projects",
            "modules.flow_gate.db.tr_self_check_runs",
            "modules.flow_gate.settings.project_settings_service",
            "modules.flow_gate.rbac.decorators",
        )
    ]
    for _m in _modules:
        _m.get_store = lambda store=store: store

    now = datetime.now(timezone.utc).isoformat()
    store._execute(
        "INSERT INTO users (user_id, username, email, password, is_active, created_at, updated_at) "
        "VALUES ('u_test', 'tester', 'test@test.com', 'dummy_hash', 1, ?, ?) "
        "ON CONFLICT(user_id) DO NOTHING",
        [now, now],
    )
    store._execute(
        "INSERT INTO projects (project_id, project_name, is_active, created_at, updated_at) "
        "VALUES ('p_0650', 'Test Project 0650', 1, ?, ?) "
        "ON CONFLICT(project_id) DO NOTHING",
        [now, now],
    )
    store._execute(
        "INSERT INTO groups (group_id, project_id, module, title, status, created_at, updated_at) "
        "VALUES ('g_0650_1', 'p_0650', 'default', 'Test Group 0650_1', 'OPEN', ?, ?) "
        "ON CONFLICT(group_id) DO NOTHING",
        [now, now],
    )
    store._execute(
        "INSERT INTO groups (group_id, project_id, module, title, status, created_at, updated_at) "
        "VALUES ('g_0650_2', 'p_0650', 'default', 'Test Group 0650_2', 'OPEN', ?, ?) "
        "ON CONFLICT(group_id) DO NOTHING",
        [now, now],
    )
    store._execute(
        "INSERT INTO documents (doc_id, project_id, group_id, module, type_code, seq, title, status, branch, revision_no, rejection_history, created_at, updated_at) "
        "VALUES ('d_tr_0001', 'p_0650', 'g_0650_1', 'default', 'TR', 1, 'TR 1', 'draft', 'main', 1, '[]', ?, ?) "
        "ON CONFLICT(doc_id) DO NOTHING",
        [now, now],
    )
    store._execute(
        "INSERT INTO documents (doc_id, project_id, group_id, module, type_code, seq, title, status, branch, revision_no, rejection_history, created_at, updated_at) "
        "VALUES ('d_tr_0002', 'p_0650', 'g_0650_1', 'default', 'TR', 2, 'TR 2', 'draft', 'main', 1, '[]', ?, ?) "
        "ON CONFLICT(doc_id) DO NOTHING",
        [now, now],
    )

    try:
        yield store
    finally:
        for _m in _modules:
            _m.get_store = _real_get_store
        if hasattr(store._db, "close"):
            try:
                store._db.close()
            except Exception:
                pass


# =============================================================================
# Migration & Schema Tests (Items 1 - 8)
# =============================================================================

def test_01_existing_project_migration_tr_self_check_enabled_false():
    """1. Existing project migration leaves tr_self_check_enabled=0 (false)."""
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE project_settings (project_id TEXT PRIMARY KEY, branch TEXT DEFAULT 'main', updated_at TEXT)")
    conn.execute("INSERT INTO project_settings (project_id, updated_at) VALUES ('p_existing', '2026-01-01')")

    migration_sql = _MIGRATION_SQLITE.read_text(encoding="utf-8")
    for stmt in migration_sql.split(";"):
        cleaned = "\n".join(l for l in stmt.splitlines() if not l.strip().startswith("--")).strip()
        if cleaned.startswith("ALTER TABLE project_settings"):
            conn.execute(cleaned)

    row = conn.execute("SELECT tr_self_check_enabled FROM project_settings WHERE project_id = 'p_existing'").fetchone()
    assert row is not None
    assert row[0] == 0


def test_02_fresh_schema_default_false():
    """2. Fresh project settings row defaults tr_self_check_enabled=0."""
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE project_settings (project_id TEXT PRIMARY KEY, updated_at TEXT)")
    conn.execute("ALTER TABLE project_settings ADD COLUMN tr_self_check_enabled INTEGER NOT NULL DEFAULT 0 CHECK (tr_self_check_enabled IN (0, 1))")
    conn.execute("INSERT INTO project_settings (project_id, updated_at) VALUES ('p_fresh', '2026-01-01')")
    row = conn.execute("SELECT tr_self_check_enabled FROM project_settings WHERE project_id = 'p_fresh'").fetchone()
    assert row[0] == 0


def test_03_self_check_run_insert(mock_db):
    """3. Inserting a valid Self-check run row succeeds."""
    mock_db._execute("DELETE FROM tr_self_check_runs WHERE project_id = 'p_0650'")
    run = selfcheck_db.create_pending(
        project_id="p_0650",
        group_id="g_0650_1",
        tr_doc_id="d_tr_0001",
        requested_by="u_test",
        policy_version="1.0",
        program="pytest",
        args=["-v", "tests/unit"],
        resolved_executable_path="/usr/bin/pytest",
        resolved_executable_name="pytest",
        executable_origin="local",
        cwd_relative="server",
        timeout_seconds=300,
        env_keys=["PATH", "PYTHONPATH"],
    )
    assert run["self_check_run_id"].startswith("scr_")
    assert run["status"] == "pending"
    assert run["active_key"] == selfcheck_db.compute_active_key("p_0650", "g_0650_1")
    assert run["timed_out"] is False
    assert run["cancel_requested"] is False


def test_04_fk_cascade_and_set_null(mock_db):
    """4. FK cascade on document & project delete & SET NULL on user delete (exercising real store path)."""
    now = datetime.now(timezone.utc).isoformat()
    mock_db._execute(
        "INSERT INTO users (user_id, username, email, password, is_active, created_at, updated_at) "
        "VALUES ('u_cascade', 'cascader', 'c@c.com', 'dummy_hash', 1, ?, ?) ON CONFLICT DO NOTHING",
        [now, now],
    )
    mock_db._execute(
        "INSERT INTO documents (doc_id, project_id, group_id, module, type_code, seq, title, status, branch, revision_no, rejection_history, created_at, updated_at) "
        "VALUES ('d_cascade', 'p_0650', 'g_0650_1', 'default', 'TR', 99, 'Cascade TR', 'draft', 'main', 1, '[]', ?, ?) ON CONFLICT DO NOTHING",
        [now, now],
    )
    run_doc = selfcheck_db.create_pending(
        project_id="p_0650",
        group_id="g_0650_2",
        tr_doc_id="d_cascade",
        requested_by="u_cascade",
        policy_version="1.0",
        program="pytest",
        args=[],
        resolved_executable_path="/bin/pytest",
        resolved_executable_name="pytest",
        executable_origin="local",
        cwd_relative=".",
        timeout_seconds=60,
        env_keys=[],
    )
    run_doc_id = run_doc["self_check_run_id"]

    # Delete user -> requested_by becomes NULL (ON DELETE SET NULL)
    with mock_db.transaction():
        mock_db._execute("DELETE FROM users WHERE user_id = 'u_cascade'")
    updated_run = selfcheck_db.get_run(run_doc_id)
    assert updated_run is not None
    assert updated_run["requested_by"] is None

    # Delete document -> run row cascades and is removed (ON DELETE CASCADE)
    with mock_db.transaction():
        mock_db._execute("DELETE FROM documents WHERE doc_id = 'd_cascade'")
    assert selfcheck_db.get_run(run_doc_id) is None

    # Distinct project to verify projects_db.delete cascade via real production store path
    projects_db.create({
        "project_id": "p_fk_cascade_test",
        "project_name": "FK Cascade Test",
        "is_active": 1,
    })
    run_proj = selfcheck_db.create_pending(
        project_id="p_fk_cascade_test",
        group_id="g_0650_1",
        tr_doc_id="d_tr_0001",
        requested_by=None,
        policy_version="1.0",
        program="pytest",
        args=[],
        resolved_executable_path="/bin/pytest",
        resolved_executable_name="pytest",
        executable_origin="local",
        cwd_relative=".",
        timeout_seconds=60,
        env_keys=[],
    )
    run_proj_id = run_proj["self_check_run_id"]
    assert selfcheck_db.get_run(run_proj_id) is not None

    # Delete project via production projects_db.delete -> run row cascades and is removed
    # This verifies foreign_keys enforcement during project deletion on the real store path
    projects_db.delete("p_fk_cascade_test")
    assert selfcheck_db.get_run(run_proj_id) is None


def test_05_status_constraint(mock_db):
    """5. Status CHECK constraint allows only pending, running, completed, failed, cancelled."""
    with pytest.raises(Exception):
        mock_db._execute(
            "INSERT INTO tr_self_check_runs (self_check_run_id, project_id, group_id, tr_doc_id, "
            "policy_version, program, args_json, resolved_executable_path, resolved_executable_name, "
            "executable_origin, cwd_relative, timeout_seconds, env_keys_json, status, created_at, updated_at) "
            "VALUES ('bad_status', 'p_0650', 'g_0650_1', 'd_tr_0001', '1.0', 'p', '[]', '/p', 'p', 'l', '.', 10, '[]', "
            "'passed', '2026-01-01', '2026-01-01')"
        )


def test_06_recovery_state_constraint(mock_db):
    """6. Recovery_state CHECK constraint allows only none, recovering, incomplete, recovered."""
    with pytest.raises(Exception):
        mock_db._execute(
            "INSERT INTO tr_self_check_runs (self_check_run_id, project_id, group_id, tr_doc_id, "
            "policy_version, program, args_json, resolved_executable_path, resolved_executable_name, "
            "executable_origin, cwd_relative, timeout_seconds, env_keys_json, status, recovery_state, created_at, updated_at) "
            "VALUES ('bad_rec', 'p_0650', 'g_0650_1', 'd_tr_0001', '1.0', 'p', '[]', '/p', 'p', 'l', '.', 10, '[]', "
            "'pending', 'broken_state', '2026-01-01', '2026-01-01')"
        )


def test_07_active_key_unique(mock_db):
    """7. Active_key unique constraint blocks two rows with the same non-null active_key."""
    key = selfcheck_db.compute_active_key("p_0650", "g_unique")
    mock_db._execute(
        "INSERT INTO tr_self_check_runs (self_check_run_id, project_id, group_id, tr_doc_id, "
        "policy_version, program, args_json, resolved_executable_path, resolved_executable_name, "
        "executable_origin, cwd_relative, timeout_seconds, env_keys_json, status, active_key, created_at, updated_at) "
        "VALUES ('u1', 'p_0650', 'g_0650_1', 'd_tr_0001', '1.0', 'p', '[]', '/p', 'p', 'l', '.', 10, '[]', "
        "'pending', ?, '2026-01-01', '2026-01-01')",
        [key],
    )
    with pytest.raises(Exception):
        mock_db._execute(
            "INSERT INTO tr_self_check_runs (self_check_run_id, project_id, group_id, tr_doc_id, "
            "policy_version, program, args_json, resolved_executable_path, resolved_executable_name, "
            "executable_origin, cwd_relative, timeout_seconds, env_keys_json, status, active_key, created_at, updated_at) "
            "VALUES ('u2', 'p_0650', 'g_0650_1', 'd_tr_0001', '1.0', 'p', '[]', '/p', 'p', 'l', '.', 10, '[]', "
            "'pending', ?, '2026-01-01', '2026-01-01')",
            [key],
        )
    mock_db._execute("DELETE FROM tr_self_check_runs WHERE self_check_run_id = 'u1'")


def test_08_multiple_terminal_rows_with_null_active_key(mock_db):
    """8. Multiple terminal rows with NULL active_key are allowed."""
    for i in range(3):
        mock_db._execute(
            "INSERT INTO tr_self_check_runs (self_check_run_id, project_id, group_id, tr_doc_id, "
            "policy_version, program, args_json, resolved_executable_path, resolved_executable_name, "
            "executable_origin, cwd_relative, timeout_seconds, env_keys_json, status, active_key, created_at, updated_at) "
            f"VALUES ('t_{i}', 'p_0650', 'g_0650_1', 'd_tr_0001', '1.0', 'p', '[]', '/p', 'p', 'l', '.', 10, '[]', "
            "'completed', NULL, '2026-01-01', '2026-01-01')"
        )
    rows = mock_db._fetch_all("SELECT self_check_run_id FROM tr_self_check_runs WHERE active_key IS NULL AND self_check_run_id LIKE 't_%'")
    assert len(rows) == 3
    mock_db._execute("DELETE FROM tr_self_check_runs WHERE self_check_run_id LIKE 't_%'")


# =============================================================================
# Repository Functionality Tests (Items 9 - 18)
# =============================================================================

def test_09_same_project_group_active_duplicate_rejected(mock_db):
    """9. Attempting to create a second active run on the same project/group raises SelfCheckAlreadyRunningError."""
    mock_db._execute("DELETE FROM tr_self_check_runs WHERE project_id = 'p_0650'")
    run1 = selfcheck_db.create_pending(
        project_id="p_0650",
        group_id="g_0650_1",
        tr_doc_id="d_tr_0001",
        requested_by=None,
        policy_version="1.0",
        program="pytest",
        args=[],
        resolved_executable_path="/p",
        resolved_executable_name="p",
        executable_origin="local",
        cwd_relative=".",
        timeout_seconds=60,
        env_keys=[],
    )
    assert run1 is not None

    with pytest.raises(selfcheck_db.SelfCheckAlreadyRunningError) as exc_info:
        selfcheck_db.create_pending(
            project_id="p_0650",
            group_id="g_0650_1",
            tr_doc_id="d_tr_0001",
            requested_by=None,
            policy_version="1.0",
            program="pytest",
            args=[],
            resolved_executable_path="/p",
            resolved_executable_name="p",
            executable_origin="local",
            cwd_relative=".",
            timeout_seconds=60,
            env_keys=[],
        )
    assert exc_info.value.existing_run_id == run1["self_check_run_id"]
    mock_db._execute("DELETE FROM tr_self_check_runs WHERE project_id = 'p_0650'")


def test_10_different_group_active_rows_allowed(mock_db):
    """10. Different groups can each have an active run in DB."""
    mock_db._execute("DELETE FROM tr_self_check_runs WHERE project_id = 'p_0650'")
    r1 = selfcheck_db.create_pending(
        project_id="p_0650",
        group_id="g_0650_1",
        tr_doc_id="d_tr_0001",
        requested_by=None,
        policy_version="1.0",
        program="pytest",
        args=[],
        resolved_executable_path="/p",
        resolved_executable_name="p",
        executable_origin="local",
        cwd_relative=".",
        timeout_seconds=60,
        env_keys=[],
    )
    r2 = selfcheck_db.create_pending(
        project_id="p_0650",
        group_id="g_0650_2",
        tr_doc_id="d_tr_0002",
        requested_by=None,
        policy_version="1.0",
        program="pytest",
        args=[],
        resolved_executable_path="/p",
        resolved_executable_name="p",
        executable_origin="local",
        cwd_relative=".",
        timeout_seconds=60,
        env_keys=[],
    )
    assert r1["self_check_run_id"] != r2["self_check_run_id"]
    assert selfcheck_db.get_active("p_0650", "g_0650_1")["self_check_run_id"] == r1["self_check_run_id"]
    assert selfcheck_db.get_active("p_0650", "g_0650_2")["self_check_run_id"] == r2["self_check_run_id"]
    mock_db._execute("DELETE FROM tr_self_check_runs WHERE project_id = 'p_0650'")


def test_11_terminal_transition_clears_active_key(mock_db):
    """11. finish_completed, finish_failed, and finish_cancelled clear active_key atomically."""
    mock_db._execute("DELETE FROM tr_self_check_runs WHERE project_id = 'p_0650'")
    # Completed
    r = selfcheck_db.create_pending(
        project_id="p_0650", group_id="g_0650_1", tr_doc_id="d_tr_0001",
        requested_by=None, policy_version="1.0", program="pytest", args=[],
        resolved_executable_path="/p", resolved_executable_name="p", executable_origin="local",
        cwd_relative=".", timeout_seconds=60, env_keys=[],
    )
    selfcheck_db.mark_running(r["self_check_run_id"], source_lock_holder="holder:1")
    finished = selfcheck_db.finish_completed(r["self_check_run_id"], exit_code=0, stdout_tail="all good")
    assert finished["status"] == "completed"
    assert finished["active_key"] is None
    assert finished["finished_at"] is not None
    assert selfcheck_db.get_active("p_0650", "g_0650_1") is None

    # Failed
    r = selfcheck_db.create_pending(
        project_id="p_0650", group_id="g_0650_1", tr_doc_id="d_tr_0001",
        requested_by=None, policy_version="1.0", program="pytest", args=[],
        resolved_executable_path="/p", resolved_executable_name="p", executable_origin="local",
        cwd_relative=".", timeout_seconds=60, env_keys=[],
    )
    failed = selfcheck_db.finish_failed(r["self_check_run_id"], error_code="process_crashed", exit_code=1)
    assert failed["status"] == "failed"
    assert failed["active_key"] is None
    assert selfcheck_db.get_active("p_0650", "g_0650_1") is None

    # Cancelled
    r = selfcheck_db.create_pending(
        project_id="p_0650", group_id="g_0650_1", tr_doc_id="d_tr_0001",
        requested_by=None, policy_version="1.0", program="pytest", args=[],
        resolved_executable_path="/p", resolved_executable_name="p", executable_origin="local",
        cwd_relative=".", timeout_seconds=60, env_keys=[],
    )
    cancelled = selfcheck_db.finish_cancelled(r["self_check_run_id"], exit_code=130)
    assert cancelled["status"] == "cancelled"
    assert cancelled["active_key"] is None
    assert selfcheck_db.get_active("p_0650", "g_0650_1") is None


def test_12_recovery_incomplete_keeps_active_key(mock_db):
    """12. mark_recovery_incomplete keeps status active and retains active_key."""
    mock_db._execute("DELETE FROM tr_self_check_runs WHERE project_id = 'p_0650'")
    r = selfcheck_db.create_pending(
        project_id="p_0650", group_id="g_0650_1", tr_doc_id="d_tr_0001",
        requested_by=None, policy_version="1.0", program="pytest", args=[],
        resolved_executable_path="/p", resolved_executable_name="p", executable_origin="local",
        cwd_relative=".", timeout_seconds=60, env_keys=[],
    )
    selfcheck_db.mark_running(r["self_check_run_id"])
    selfcheck_db.mark_recovering(r["self_check_run_id"])
    rec_incomplete = selfcheck_db.mark_recovery_incomplete(r["self_check_run_id"], "Process still running")

    assert rec_incomplete["recovery_state"] == "incomplete"
    assert rec_incomplete["recovery_reason"] == "Process still running"
    assert rec_incomplete["status"] == "running"
    assert rec_incomplete["active_key"] is not None
    assert selfcheck_db.get_active("p_0650", "g_0650_1") is not None
    assert selfcheck_db.has_recovery_incomplete("p_0650") is True

    # Explicitly claim incomplete recovery before terminal transition.
    assert selfcheck_db.finish_recovered_interrupted(r["self_check_run_id"]) is None
    assert selfcheck_db.retry_recovery_incomplete(r["self_check_run_id"]) is not None
    recovered = selfcheck_db.finish_recovered_interrupted(r["self_check_run_id"])
    assert recovered["status"] == "failed"
    assert recovered["error_code"] == "interrupted_by_restart"
    assert recovered["recovery_state"] == "recovered"
    assert recovered["active_key"] is None
    mock_db._execute("DELETE FROM tr_self_check_runs WHERE project_id = 'p_0650'")


def test_13_cancel_requested_update(mock_db):
    """13. request_cancel sets cancel_requested=1 on pending/running runs."""
    mock_db._execute("DELETE FROM tr_self_check_runs WHERE project_id = 'p_0650'")
    r = selfcheck_db.create_pending(
        project_id="p_0650", group_id="g_0650_1", tr_doc_id="d_tr_0001",
        requested_by=None, policy_version="1.0", program="pytest", args=[],
        resolved_executable_path="/p", resolved_executable_name="p", executable_origin="local",
        cwd_relative=".", timeout_seconds=60, env_keys=[],
    )
    assert r["cancel_requested"] is False
    assert selfcheck_db.request_cancel(r["self_check_run_id"]) is True
    updated = selfcheck_db.get_run(r["self_check_run_id"])
    assert updated["cancel_requested"] is True

    # Terminal run cannot be requested for cancel
    selfcheck_db.finish_completed(r["self_check_run_id"])
    assert selfcheck_db.request_cancel(r["self_check_run_id"]) is False
    mock_db._execute("DELETE FROM tr_self_check_runs WHERE project_id = 'p_0650'")


def test_14_orphan_active_query(mock_db):
    """14. list_orphan_active_runs returns pending/running runs."""
    mock_db._execute("DELETE FROM tr_self_check_runs WHERE project_id = 'p_0650'")
    r = selfcheck_db.create_pending(
        project_id="p_0650", group_id="g_0650_1", tr_doc_id="d_tr_0001",
        requested_by=None, policy_version="1.0", program="pytest", args=[],
        resolved_executable_path="/p", resolved_executable_name="p", executable_origin="local",
        cwd_relative=".", timeout_seconds=60, env_keys=[],
    )
    orphans = selfcheck_db.list_orphan_active_runs()
    assert any(o["self_check_run_id"] == r["self_check_run_id"] for o in orphans)

    selfcheck_db.finish_completed(r["self_check_run_id"])
    orphans2 = selfcheck_db.list_orphan_active_runs()
    assert not any(o["self_check_run_id"] == r["self_check_run_id"] for o in orphans2)
    mock_db._execute("DELETE FROM tr_self_check_runs WHERE project_id = 'p_0650'")


def test_15_recovery_incomplete_project_query(mock_db):
    """15. list_recovery_incomplete_project_ids and has_recovery_incomplete identify protected projects."""
    mock_db._execute("DELETE FROM tr_self_check_runs WHERE project_id = 'p_0650'")
    assert selfcheck_db.has_recovery_incomplete("p_0650") is False
    assert "p_0650" not in selfcheck_db.list_recovery_incomplete_project_ids()

    r = selfcheck_db.create_pending(
        project_id="p_0650", group_id="g_0650_1", tr_doc_id="d_tr_0001",
        requested_by=None, policy_version="1.0", program="pytest", args=[],
        resolved_executable_path="/p", resolved_executable_name="p", executable_origin="local",
        cwd_relative=".", timeout_seconds=60, env_keys=[],
    )
    selfcheck_db.mark_recovering(r["self_check_run_id"])
    selfcheck_db.mark_recovery_incomplete(r["self_check_run_id"], "test incomplete")

    assert selfcheck_db.has_recovery_incomplete("p_0650") is True
    assert "p_0650" in selfcheck_db.list_recovery_incomplete_project_ids()
    mock_db._execute("DELETE FROM tr_self_check_runs WHERE project_id = 'p_0650'")


def test_16_document_pagination_ordering(mock_db):
    """16. list_by_doc orders by created_at DESC, self_check_run_id DESC and supports cursor."""
    mock_db._execute("DELETE FROM tr_self_check_runs WHERE tr_doc_id = 'd_tr_0001'")
    for i in range(3):
        ts = f"2026-09-29T10:0{i}:00+00:00"
        mock_db._execute(
            "INSERT INTO tr_self_check_runs (self_check_run_id, project_id, group_id, tr_doc_id, "
            "policy_version, program, args_json, resolved_executable_path, resolved_executable_name, "
            "executable_origin, cwd_relative, timeout_seconds, env_keys_json, status, created_at, updated_at) "
            f"VALUES ('p_{i}', 'p_0650', 'g_0650_1', 'd_tr_0001', '1.0', 'p', '[]', '/p', 'p', 'l', '.', 10, '[]', "
            "'completed', ?, ?)",
            [ts, ts],
        )

    runs = selfcheck_db.list_by_doc("d_tr_0001", limit=10)
    assert len(runs) == 3
    assert [r["self_check_run_id"] for r in runs] == ["p_2", "p_1", "p_0"]

    # Pagination using cursor of p_2
    first = runs[0]
    cursor = (first["created_at"], first["self_check_run_id"])
    page2 = selfcheck_db.list_by_doc("d_tr_0001", limit=10, cursor=cursor)
    assert len(page2) == 2
    assert [r["self_check_run_id"] for r in page2] == ["p_1", "p_0"]
    mock_db._execute("DELETE FROM tr_self_check_runs WHERE tr_doc_id = 'd_tr_0001'")


def test_17_and_18_purge_and_protection(mock_db):
    """17 & 18. Purge deletes old terminal runs while protecting active, incomplete, and cleanup_pending."""
    mock_db._execute("DELETE FROM tr_self_check_runs WHERE project_id = 'p_0650'")
    old_ts = "2026-01-01T00:00:00+00:00"
    cutoff = "2026-06-01T00:00:00+00:00"

    # Eligible for purge: completed, old finished_at, cleanup_pending=0, active_key=NULL
    mock_db._execute(
        "INSERT INTO tr_self_check_runs (self_check_run_id, project_id, group_id, tr_doc_id, "
        "policy_version, program, args_json, resolved_executable_path, resolved_executable_name, "
        "executable_origin, cwd_relative, timeout_seconds, env_keys_json, status, active_key, finished_at, cleanup_pending, recovery_state, created_at, updated_at) "
        "VALUES ('purge_me', 'p_0650', 'g_0650_1', 'd_tr_0001', '1.0', 'p', '[]', '/p', 'p', 'l', '.', 10, '[]', "
        "'completed', NULL, ?, 0, 'none', ?, ?)",
        [old_ts, old_ts, old_ts],
    )

    # Protected 1: active (status=running, active_key set)
    mock_db._execute(
        "INSERT INTO tr_self_check_runs (self_check_run_id, project_id, group_id, tr_doc_id, "
        "policy_version, program, args_json, resolved_executable_path, resolved_executable_name, "
        "executable_origin, cwd_relative, timeout_seconds, env_keys_json, status, active_key, finished_at, cleanup_pending, recovery_state, created_at, updated_at) "
        "VALUES ('prot_active', 'p_0650', 'g_0650_1', 'd_tr_0001', '1.0', 'p', '[]', '/p', 'p', 'l', '.', 10, '[]', "
        "'running', 'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa', ?, 0, 'none', ?, ?)",
        [old_ts, old_ts, old_ts],
    )

    # Protected 2: recovery_state = incomplete
    mock_db._execute(
        "INSERT INTO tr_self_check_runs (self_check_run_id, project_id, group_id, tr_doc_id, "
        "policy_version, program, args_json, resolved_executable_path, resolved_executable_name, "
        "executable_origin, cwd_relative, timeout_seconds, env_keys_json, status, active_key, finished_at, cleanup_pending, recovery_state, created_at, updated_at) "
        "VALUES ('prot_incomplete', 'p_0650', 'g_0650_1', 'd_tr_0001', '1.0', 'p', '[]', '/p', 'p', 'l', '.', 10, '[]', "
        "'failed', NULL, ?, 0, 'incomplete', ?, ?)",
        [old_ts, old_ts, old_ts],
    )

    # Protected 3: cleanup_pending = 1
    mock_db._execute(
        "INSERT INTO tr_self_check_runs (self_check_run_id, project_id, group_id, tr_doc_id, "
        "policy_version, program, args_json, resolved_executable_path, resolved_executable_name, "
        "executable_origin, cwd_relative, timeout_seconds, env_keys_json, status, active_key, finished_at, cleanup_pending, recovery_state, created_at, updated_at) "
        "VALUES ('prot_cleanup', 'p_0650', 'g_0650_1', 'd_tr_0001', '1.0', 'p', '[]', '/p', 'p', 'l', '.', 10, '[]', "
        "'completed', NULL, ?, 1, 'none', ?, ?)",
        [old_ts, old_ts, old_ts],
    )

    purged = selfcheck_db.purge_old_terminal(cutoff)
    assert purged == 1

    assert selfcheck_db.get_run("purge_me") is None
    assert selfcheck_db.get_run("prot_active") is not None
    assert selfcheck_db.get_run("prot_incomplete") is not None
    assert selfcheck_db.get_run("prot_cleanup") is not None
    mock_db._execute("DELETE FROM tr_self_check_runs WHERE project_id = 'p_0650'")


def test_18b_purge_mutation_guards_and_race_condition(mock_db, monkeypatch):
    """Purge DELETE query guards (terminal, cutoff, cleanup_pending, recovery_state, active_key)
    protect against concurrent modifications between SELECT and DELETE mutations."""
    mock_db._execute("DELETE FROM tr_self_check_runs WHERE project_id = 'p_0650'")
    old_ts = "2026-01-01T00:00:00+00:00"
    cutoff = "2026-06-01T00:00:00+00:00"

    # Create 5 runs initially eligible for purge (sequentially completing each to clear active_key)
    run_ids = []
    for i in range(5):
        r = selfcheck_db.create_pending(
            project_id="p_0650", group_id="g_0650_1", tr_doc_id="d_tr_0001",
            requested_by=None, policy_version="1.0", program="pytest", args=[],
            resolved_executable_path="/p", resolved_executable_name="p", executable_origin="local",
            cwd_relative=".", timeout_seconds=60, env_keys=[],
        )
        selfcheck_db.finish_completed(r["self_check_run_id"])
        mock_db._execute(
            "UPDATE tr_self_check_runs SET finished_at = ? WHERE self_check_run_id = ?",
            [old_ts, r["self_check_run_id"]],
        )
        run_ids.append(r["self_check_run_id"])

    # Simulate race: after SELECT candidates, concurrently mutate rows before DELETE:
    # row 0: marked cleanup_pending = 1 (via set_cleanup_result)
    # row 1: status changed to running
    # row 2: recovery_state changed to incomplete
    # row 3: active_key re-assigned
    # row 4: remains untouched (still eligible)
    orig_fetch_all = mock_db._fetch_all
    def hooked_fetch_all(sql, params=None):
        res = orig_fetch_all(sql, params)
        if "ORDER BY finished_at ASC LIMIT" in sql and res:
            selfcheck_db.set_cleanup_result(run_ids[0], cleanup_pending=True)
            mock_db._execute("UPDATE tr_self_check_runs SET status = 'running' WHERE self_check_run_id = ?", [run_ids[1]])
            mock_db._execute("UPDATE tr_self_check_runs SET recovery_state = 'incomplete' WHERE self_check_run_id = ?", [run_ids[2]])
            mock_db._execute("UPDATE tr_self_check_runs SET active_key = 'bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb' WHERE self_check_run_id = ?", [run_ids[3]])
        return res

    monkeypatch.setattr(mock_db, "_fetch_all", hooked_fetch_all)
    purged_count = selfcheck_db.purge_old_terminal(cutoff)

    # Only row 4 should have been deleted! The 4 raced rows are protected by the DELETE's guards.
    assert purged_count == 1
    assert selfcheck_db.get_run(run_ids[0]) is not None, "cleanup_pending guard failed"
    assert selfcheck_db.get_run(run_ids[1]) is not None, "status guard failed"
    assert selfcheck_db.get_run(run_ids[2]) is not None, "recovery_state guard failed"
    assert selfcheck_db.get_run(run_ids[3]) is not None, "active_key guard failed"
    assert selfcheck_db.get_run(run_ids[4]) is None, "untouched row should have been purged"

    mock_db._execute("DELETE FROM tr_self_check_runs WHERE project_id = 'p_0650'")


# =============================================================================
# Project Settings Integration Tests (Items 19 - 23)
# =============================================================================

def test_19_settings_read_includes_tr_self_check_enabled(mock_db):
    """19. get_project_settings includes tr_self_check_enabled."""
    settings = settings_svc.get_project_settings("p_0650")
    if settings is None:
        settings = settings_svc.update_project_settings("p_0650", {})
    assert "tr_self_check_enabled" in settings
    assert isinstance(settings["tr_self_check_enabled"], bool)


def test_20_and_22_settings_update_and_no_regression(mock_db):
    """20 & 22. Settings update toggles tr_self_check_enabled without breaking other fields."""
    updated = settings_svc.update_project_settings("p_0650", {
        "digits_group": 5,
        "tr_self_check_enabled": True,
    })
    assert updated["digits_group"] == 5
    assert updated["tr_self_check_enabled"] is True

    read_back = settings_svc.get_project_settings("p_0650")
    assert read_back["digits_group"] == 5
    assert read_back["tr_self_check_enabled"] is True

    # Toggle back to False
    updated2 = settings_svc.update_project_settings("p_0650", {
        "tr_self_check_enabled": False,
    })
    assert updated2["digits_group"] == 5
    assert updated2["tr_self_check_enabled"] is False


def test_21_settings_boolean_validation(mock_db):
    """21. Passing a non-boolean for tr_self_check_enabled raises ValueError."""
    with pytest.raises(ValueError, match="must be a boolean"):
        settings_svc.update_project_settings("p_0650", {
            "tr_self_check_enabled": "not_a_bool",  # type: ignore[dict-item]
        })


def test_23_new_and_existing_project_default_false(mock_db):
    """23. Newly created project and existing project default to tr_self_check_enabled=False."""
    now = datetime.now(timezone.utc).isoformat()
    projects_db.create({
        "project_id": "p_brand_new",
        "project_name": "Brand New Project",
        "is_active": 1,
    })
    settings = settings_svc.get_project_settings("p_brand_new")
    assert settings is not None
    assert settings["tr_self_check_enabled"] is False

    from fastapi import FastAPI
    app = FastAPI()
    app.include_router(settings_router, prefix="/api/v1")
    app.dependency_overrides[get_current_user] = lambda: {"user_id": "u_test", "is_admin": 1}
    client = TestClient(app)

    res = client.get("/api/v1/projects/p_brand_new/settings")
    assert res.status_code == 200
    assert res.json()["tr_self_check_enabled"] is False

    # PATCH via HTTP endpoint
    patch_res = client.patch("/api/v1/projects/p_brand_new/settings", json={"tr_self_check_enabled": True})
    assert patch_res.status_code == 200
    assert patch_res.json()["tr_self_check_enabled"] is True

    # PATCH invalid type via HTTP endpoint
    bad_res = client.patch("/api/v1/projects/p_brand_new/settings", json={"tr_self_check_enabled": "invalid"})
    assert bad_res.status_code == 422


# =============================================================================
# Additional Robustness & Dialect Verification Tests
# =============================================================================

def test_24_truncate_tail_utf8_64kib_boundary():
    """Verify _truncate_tail enforces 64 KiB byte limit and does not slice multi-byte UTF-8 code points."""
    # 1 byte ascii: 70,000 bytes
    ascii_tail = "a" * 70_000
    truncated_ascii = selfcheck_db._truncate_tail(ascii_tail)
    assert truncated_ascii is not None
    assert len(truncated_ascii.encode("utf-8")) == 64 * 1024
    assert truncated_ascii == "a" * (64 * 1024)

    # 3-byte characters (Korean '가'): 30,000 chars = 90,000 bytes
    korean_tail = "가" * 30_000
    truncated_korean = selfcheck_db._truncate_tail(korean_tail)
    assert truncated_korean is not None
    encoded = truncated_korean.encode("utf-8")
    assert len(encoded) <= 64 * 1024
    assert truncated_korean.endswith("가")

    # 4-byte emojis: 20,000 emojis = 80,000 bytes
    emoji_tail = "🚀" * 20_000
    truncated_emoji = selfcheck_db._truncate_tail(emoji_tail)
    assert truncated_emoji is not None
    assert len(truncated_emoji.encode("utf-8")) <= 64 * 1024
    assert truncated_emoji.endswith("🚀")


def test_25_request_cancel_cas_and_affected_rows(mock_db):
    """Verify request_cancel uses _execute_affected with cancel_requested = 0 CAS predicate."""
    mock_db._execute("DELETE FROM tr_self_check_runs WHERE project_id = 'p_0650'")
    r = selfcheck_db.create_pending(
        project_id="p_0650", group_id="g_0650_1", tr_doc_id="d_tr_0001",
        requested_by=None, policy_version="1.0", program="pytest", args=[],
        resolved_executable_path="/p", resolved_executable_name="p", executable_origin="local",
        cwd_relative=".", timeout_seconds=60, env_keys=[],
    )
    run_id = r["self_check_run_id"]

    # 1st cancel succeeds
    assert selfcheck_db.request_cancel(run_id) is True
    assert selfcheck_db.get_run(run_id)["cancel_requested"] is True

    # 2nd cancel fails because cancel_requested = 0 predicate does not match
    assert selfcheck_db.request_cancel(run_id) is False

    # Non-existent run fails
    assert selfcheck_db.request_cancel("scr_nonexistent") is False

    # Terminal run fails
    selfcheck_db.finish_cancelled(run_id)
    assert selfcheck_db.request_cancel(run_id) is False


def test_26_lifecycle_transition_guards_and_late_executor_protection(mock_db):
    """Late executor or recovery cannot mutate already-terminal or wrongly-stated runs."""
    mock_db._execute("DELETE FROM tr_self_check_runs WHERE project_id = 'p_0650'")
    r = selfcheck_db.create_pending(
        project_id="p_0650", group_id="g_0650_1", tr_doc_id="d_tr_0001",
        requested_by=None, policy_version="1.0", program="pytest", args=[],
        resolved_executable_path="/p", resolved_executable_name="p", executable_origin="local",
        cwd_relative=".", timeout_seconds=60, env_keys=[],
    )
    run_id = r["self_check_run_id"]
    selfcheck_db.mark_running(run_id)

    # Recovery takes over the run
    rec = selfcheck_db.mark_recovering(run_id)
    assert rec is not None
    assert rec["recovery_state"] == "recovering"

    # Late executor tries to complete the run while it's in recovering state
    late_complete = selfcheck_db.finish_completed(run_id, exit_code=0)
    assert late_complete is None, "Late executor finish_completed must be rejected while recovering"

    # Recovery marks it incomplete
    inc = selfcheck_db.mark_recovery_incomplete(run_id, reason="Daemon not found")
    assert inc is not None
    assert inc["recovery_state"] == "incomplete"

    # Late executor tries to fail or cancel the run while it's incomplete
    late_failed = selfcheck_db.finish_failed(run_id, error_code="late_fail")
    assert late_failed is None, "Late executor finish_failed must be rejected while incomplete"
    late_cancel = selfcheck_db.finish_cancelled(run_id)
    assert late_cancel is None, "Late executor finish_cancelled must be rejected while incomplete"

    # Incomplete cannot finish directly; retry must claim recovery first.
    assert selfcheck_db.finish_recovered_interrupted(run_id) is None
    assert selfcheck_db.retry_recovery_incomplete(run_id) is not None
    rec_finish = selfcheck_db.finish_recovered_interrupted(run_id)
    assert rec_finish is not None
    assert rec_finish["status"] == "failed"
    assert rec_finish["active_key"] is None

    # Late executor tries to complete after terminal
    late_complete2 = selfcheck_db.finish_completed(run_id, exit_code=0)
    assert late_complete2 is None, "Cannot complete a terminal run"

    # Recovery cannot mark a terminal run recovering
    assert selfcheck_db.mark_recovering(run_id) is None
    assert selfcheck_db.finish_recovered_interrupted(run_id) is None


def test_27_migration_ddl_parity_all_three_dialects():
    """Verify DDL parity across SQLite, MySQL, and PostgreSQL migrations, plus SQLite in-memory execution."""
    assert _MIGRATION_SQLITE.is_file(), f"Missing SQLite migration: {_MIGRATION_SQLITE}"
    assert _MIGRATION_MYSQL.is_file(), f"Missing MySQL migration: {_MIGRATION_MYSQL}"
    assert _MIGRATION_POSTGRES.is_file(), f"Missing PostgreSQL migration: {_MIGRATION_POSTGRES}"

    sql_sqlite = _MIGRATION_SQLITE.read_text(encoding="utf-8")
    sql_mysql = _MIGRATION_MYSQL.read_text(encoding="utf-8")
    sql_postgres = _MIGRATION_POSTGRES.read_text(encoding="utf-8")

    # Check project_settings column addition across all 3 dialects
    for sql, dialect in [(sql_sqlite, "SQLite"), (sql_mysql, "MySQL"), (sql_postgres, "PostgreSQL")]:
        assert "ALTER TABLE project_settings" in sql, f"{dialect} missing ALTER TABLE"
        assert "tr_self_check_enabled" in sql, f"{dialect} missing tr_self_check_enabled column"
        assert "CREATE TABLE tr_self_check_runs" in sql, f"{dialect} missing CREATE TABLE"

    # Check common required columns in all 3 dialects
    required_cols = [
        "self_check_run_id", "project_id", "group_id", "tr_doc_id", "policy_version",
        "program", "args_json", "resolved_executable_path", "resolved_executable_name",
        "executable_origin", "cwd_relative", "timeout_seconds", "env_keys_json",
        "status", "active_key", "recovery_state", "cancel_requested", "timed_out",
        "finished_at", "cleanup_pending", "created_at", "updated_at",
    ]
    for col in required_cols:
        assert col in sql_sqlite, f"SQLite migration missing column: {col}"
        assert col in sql_mysql, f"MySQL migration missing column: {col}"
        assert col in sql_postgres, f"PostgreSQL migration missing column: {col}"

    # Check FK constraints in all 3 dialects
    for sql, dialect in [(sql_sqlite, "SQLite"), (sql_mysql, "MySQL"), (sql_postgres, "PostgreSQL")]:
        assert "REFERENCES projects(project_id) ON DELETE CASCADE" in sql, f"{dialect} missing project cascade FK"
        assert "REFERENCES groups(group_id) ON DELETE CASCADE" in sql, f"{dialect} missing group cascade FK"
        assert "REFERENCES documents(doc_id) ON DELETE CASCADE" in sql, f"{dialect} missing document cascade FK"
        assert "REFERENCES users(user_id) ON DELETE SET NULL" in sql, f"{dialect} missing user SET NULL FK"

    # Check status and recovery_state constraints in all 3 dialects
    assert "status IN ('pending', 'running', 'completed', 'failed', 'cancelled')" in sql_sqlite
    assert "recovery_state IN ('none', 'recovering', 'incomplete', 'recovered')" in sql_sqlite
    assert "status IN ('pending', 'running', 'completed', 'failed', 'cancelled')" in sql_mysql
    assert "recovery_state IN ('none', 'recovering', 'incomplete', 'recovered')" in sql_mysql
    assert "CHECK (status IN ('pending', 'running', 'completed', 'failed', 'cancelled'))" in sql_postgres
    assert "CHECK (recovery_state IN ('none', 'recovering', 'incomplete', 'recovered'))" in sql_postgres

    # In-memory SQLite executable verification of migration behaviors
    con = sqlite3.connect(":memory:")
    con.execute("PRAGMA foreign_keys = ON")
    cur = con.cursor()
    cur.execute("CREATE TABLE project_settings (project_id TEXT PRIMARY KEY, digits_group INTEGER NOT NULL DEFAULT 4)")
    cur.execute("INSERT INTO project_settings (project_id) VALUES ('p_exist')")
    cur.execute("CREATE TABLE projects (project_id TEXT PRIMARY KEY, project_name TEXT NOT NULL, is_active INTEGER NOT NULL DEFAULT 1)")
    cur.execute("CREATE TABLE groups (group_id TEXT PRIMARY KEY, project_id TEXT NOT NULL, group_name TEXT NOT NULL)")
    cur.execute("CREATE TABLE documents (doc_id TEXT PRIMARY KEY, project_id TEXT NOT NULL, group_id TEXT NOT NULL)")
    cur.execute("CREATE TABLE users (user_id TEXT PRIMARY KEY, user_name TEXT NOT NULL)")

    cur.executescript(sql_sqlite)

    # Defaults
    assert cur.execute("SELECT tr_self_check_enabled FROM project_settings WHERE project_id = 'p_exist'").fetchone()[0] == 0
    cur.execute("INSERT INTO project_settings (project_id) VALUES ('p_fresh')")
    assert cur.execute("SELECT tr_self_check_enabled FROM project_settings WHERE project_id = 'p_fresh'").fetchone()[0] == 0

    # Insert & FK
    cur.execute("INSERT INTO projects (project_id, project_name) VALUES ('p_1', 'P1')")
    cur.execute("INSERT INTO groups (group_id, project_id, group_name) VALUES ('g_1', 'p_1', 'G1')")
    cur.execute("INSERT INTO documents (doc_id, project_id, group_id) VALUES ('d_1', 'p_1', 'g_1')")
    cur.execute("INSERT INTO users (user_id, user_name) VALUES ('u_1', 'U1')")

    cur.execute("""
        INSERT INTO tr_self_check_runs (
            self_check_run_id, project_id, group_id, tr_doc_id, requested_by,
            policy_version, program, args_json, resolved_executable_path,
            resolved_executable_name, executable_origin, cwd_relative,
            timeout_seconds, env_keys_json, status, active_key, recovery_state,
            created_at, updated_at
        ) VALUES (
            'r_1', 'p_1', 'g_1', 'd_1', 'u_1',
            '1.0', 'pytest', '[]', '/p', 'p', 'local', '.',
            60, '[]', 'running', 'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa', 'none',
            datetime('now'), datetime('now')
        )
    """)

    # Unique active_key
    with pytest.raises(sqlite3.IntegrityError):
        cur.execute("""
            INSERT INTO tr_self_check_runs (
                self_check_run_id, project_id, group_id, tr_doc_id, requested_by,
                policy_version, program, args_json, resolved_executable_path,
                resolved_executable_name, executable_origin, cwd_relative,
                timeout_seconds, env_keys_json, status, active_key, recovery_state,
                created_at, updated_at
            ) VALUES (
                'r_2', 'p_1', 'g_1', 'd_1', 'u_1',
                '1.0', 'pytest', '[]', '/p', 'p', 'local', '.',
                60, '[]', 'running', 'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa', 'none',
                datetime('now'), datetime('now')
            )
        """)

    # Multiple NULL terminal keys
    for r_id in ['r_t1', 'r_t2']:
        cur.execute(f"""
            INSERT INTO tr_self_check_runs (
                self_check_run_id, project_id, group_id, tr_doc_id, requested_by,
                policy_version, program, args_json, resolved_executable_path,
                resolved_executable_name, executable_origin, cwd_relative,
                timeout_seconds, env_keys_json, status, active_key, recovery_state,
                created_at, updated_at
            ) VALUES (
                '{r_id}', 'p_1', 'g_1', 'd_1', 'u_1',
                '1.0', 'pytest', '[]', '/p', 'p', 'local', '.',
                60, '[]', 'completed', NULL, 'none',
                datetime('now'), datetime('now')
            )
        """)

    # CHECK constraints
    with pytest.raises(sqlite3.IntegrityError):
        cur.execute("UPDATE tr_self_check_runs SET status = 'bad' WHERE self_check_run_id = 'r_1'")
    with pytest.raises(sqlite3.IntegrityError):
        cur.execute("UPDATE tr_self_check_runs SET recovery_state = 'bad' WHERE self_check_run_id = 'r_1'")
    with pytest.raises(sqlite3.IntegrityError):
        cur.execute("UPDATE project_settings SET tr_self_check_enabled = 2 WHERE project_id = 'p_fresh'")

    # FK actions
    cur.execute("DELETE FROM users WHERE user_id = 'u_1'")
    assert cur.execute("SELECT requested_by FROM tr_self_check_runs WHERE self_check_run_id = 'r_1'").fetchone()[0] is None
    cur.execute("DELETE FROM projects WHERE project_id = 'p_1'")
    assert cur.execute("SELECT COUNT(*) FROM tr_self_check_runs").fetchone()[0] == 0
    con.close()


def _resolve_pg_conn():
    """Live checks run only when an explicit test DSN is supplied."""
    dsn = os.environ.get("FLOWGATE_PG_TEST_DSN")
    if not dsn:
        return None, "FLOWGATE_PG_TEST_DSN not provided"
    try:
        import psycopg2
    except ImportError:
        return None, "psycopg2 is not installed"
    conn = psycopg2.connect(dsn, connect_timeout=5)
    conn.autocommit = True
    return conn, None


def test_28_live_postgres_migration_or_skip():
    """Executable PostgreSQL migration and contract verification (defaults, insert, FK, CHECK, unique, null active)."""
    conn, skip_reason = _resolve_pg_conn()
    if conn is None:
        pytest.skip(skip_reason)

    import uuid
    import psycopg2
    schema = f"fg_test_pg_{uuid.uuid4().hex[:8]}"
    cur = conn.cursor()
    try:
        cur.execute(f"CREATE SCHEMA {schema}")
        cur.execute(f"SET search_path TO {schema}")
        cur.execute("CREATE TABLE project_settings (project_id VARCHAR(64) PRIMARY KEY, digits_group INT NOT NULL DEFAULT 4)")
        cur.execute("INSERT INTO project_settings (project_id) VALUES ('p_existing')")
        cur.execute("CREATE TABLE projects (project_id VARCHAR(64) PRIMARY KEY, project_name VARCHAR(255) NOT NULL, is_active INT NOT NULL DEFAULT 1)")
        cur.execute("CREATE TABLE groups (group_id VARCHAR(64) PRIMARY KEY, project_id VARCHAR(64) NOT NULL, group_name VARCHAR(255) NOT NULL)")
        cur.execute("CREATE TABLE documents (doc_id VARCHAR(64) PRIMARY KEY, project_id VARCHAR(64) NOT NULL, group_id VARCHAR(64) NOT NULL)")
        cur.execute("CREATE TABLE users (user_id VARCHAR(64) PRIMARY KEY, user_name VARCHAR(255) NOT NULL)")

        # Run migration
        cur.execute(_MIGRATION_POSTGRES.read_text(encoding="utf-8"))

        # 1. Existing/fresh defaults
        cur.execute("SELECT tr_self_check_enabled FROM project_settings WHERE project_id = 'p_existing'")
        assert cur.fetchone()[0] == 0, "PostgreSQL existing project_settings default must be 0"
        cur.execute("INSERT INTO project_settings (project_id) VALUES ('p_fresh')")
        cur.execute("SELECT tr_self_check_enabled FROM project_settings WHERE project_id = 'p_fresh'")
        assert cur.fetchone()[0] == 0, "PostgreSQL fresh project_settings default must be 0"

        # Parent rows
        cur.execute("INSERT INTO projects (project_id, project_name) VALUES ('p_1', 'P1')")
        cur.execute("INSERT INTO groups (group_id, project_id, group_name) VALUES ('g_1', 'p_1', 'G1')")
        cur.execute("INSERT INTO documents (doc_id, project_id, group_id) VALUES ('d_1', 'p_1', 'g_1')")
        cur.execute("INSERT INTO users (user_id, user_name) VALUES ('u_1', 'U1')")

        # 2. Run insertion
        cur.execute("""
            INSERT INTO tr_self_check_runs (
                self_check_run_id, project_id, group_id, tr_doc_id, requested_by,
                policy_version, program, args_json, resolved_executable_path,
                resolved_executable_name, executable_origin, cwd_relative,
                timeout_seconds, env_keys_json, status, active_key, recovery_state,
                cancel_requested, timed_out, cleanup_pending, created_at, updated_at
            ) VALUES (
                'r_1', 'p_1', 'g_1', 'd_1', 'u_1',
                '1.0', 'pytest', '[]', '/p', 'p', 'local', '.',
                60, '[]', 'running', 'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa', 'none',
                0, 0, 0, NOW(), NOW()
            )
        """)

        # 3. Unique active key constraint
        with pytest.raises(psycopg2.IntegrityError):
            cur.execute("""
                INSERT INTO tr_self_check_runs (
                    self_check_run_id, project_id, group_id, tr_doc_id, requested_by,
                    policy_version, program, args_json, resolved_executable_path,
                    resolved_executable_name, executable_origin, cwd_relative,
                    timeout_seconds, env_keys_json, status, active_key, recovery_state,
                    cancel_requested, timed_out, cleanup_pending, created_at, updated_at
                ) VALUES (
                    'r_2', 'p_1', 'g_1', 'd_1', 'u_1',
                    '1.0', 'pytest', '[]', '/p', 'p', 'local', '.',
                    60, '[]', 'running', 'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa', 'none',
                    0, 0, 0, NOW(), NOW()
                )
            """)

        # 4. Multiple NULL terminal keys allowed
        for r_id in ['r_term_1', 'r_term_2']:
            cur.execute(f"""
                INSERT INTO tr_self_check_runs (
                    self_check_run_id, project_id, group_id, tr_doc_id, requested_by,
                    policy_version, program, args_json, resolved_executable_path,
                    resolved_executable_name, executable_origin, cwd_relative,
                    timeout_seconds, env_keys_json, status, active_key, recovery_state,
                    cancel_requested, timed_out, cleanup_pending, created_at, updated_at
                ) VALUES (
                    '{r_id}', 'p_1', 'g_1', 'd_1', 'u_1',
                    '1.0', 'pytest', '[]', '/p', 'p', 'local', '.',
                    60, '[]', 'completed', NULL, 'none',
                    0, 0, 0, NOW(), NOW()
                )
            """)

        # 5. CHECK constraints
        with pytest.raises(psycopg2.IntegrityError):
            cur.execute("UPDATE tr_self_check_runs SET status = 'invalid_status' WHERE self_check_run_id = 'r_1'")
        with pytest.raises(psycopg2.IntegrityError):
            cur.execute("UPDATE tr_self_check_runs SET recovery_state = 'invalid_rec' WHERE self_check_run_id = 'r_1'")
        with pytest.raises(psycopg2.IntegrityError):
            cur.execute("UPDATE project_settings SET tr_self_check_enabled = 2 WHERE project_id = 'p_fresh'")

        # HASH64 constraints reject short, uppercase and non-hex digests in every field.
        for col in (
            "active_key", "source_refs_hash_before", "source_refs_hash_after",
            "source_index_hash_before", "source_index_hash_after",
            "source_status_hash_before", "source_status_hash_after",
        ):
            for bad in ("abc", "A" * 64, "g" * 64):
                with pytest.raises(psycopg2.IntegrityError):
                    cur.execute(
                        f"UPDATE tr_self_check_runs SET {col} = %s WHERE self_check_run_id = 'r_1'",
                        (bad,),
                    )

        # 6. FK actions: SET NULL & CASCADE
        cur.execute("DELETE FROM users WHERE user_id = 'u_1'")
        cur.execute("SELECT requested_by FROM tr_self_check_runs WHERE self_check_run_id = 'r_1'")
        assert cur.fetchone()[0] is None, "PostgreSQL FK ON DELETE SET NULL failed"

        cur.execute("DELETE FROM projects WHERE project_id = 'p_1'")
        cur.execute("SELECT COUNT(*) FROM tr_self_check_runs")
        assert cur.fetchone()[0] == 0, "PostgreSQL FK ON DELETE CASCADE failed"

    finally:
        try:
            cur.execute(f"DROP SCHEMA IF EXISTS {schema} CASCADE")
        except Exception:
            pass
        conn.close()


def _resolve_mysql_conn():
    """Live checks run only when an explicit test DSN is supplied."""
    dsn = os.environ.get("FLOWGATE_MYSQL_TEST_DSN")
    if not dsn:
        return None, "FLOWGATE_MYSQL_TEST_DSN not provided"
    try:
        import pymysql
    except ImportError:
        return None, "pymysql is not installed"
    from urllib.parse import urlparse, unquote
    u = urlparse(dsn)
    if u.scheme not in ("mysql", "mariadb") or not u.hostname or not u.path.strip("/"):
        raise ValueError("FLOWGATE_MYSQL_TEST_DSN must be a mysql:// or mariadb:// URL with a database")
    conn = pymysql.connect(
        host=u.hostname,
        port=u.port or 3306,
        user=unquote(u.username or ""),
        password=unquote(u.password or ""),
        database=unquote(u.path.lstrip("/")),
        connect_timeout=5,
        autocommit=True,
    )
    return conn, None


def test_29_live_mysql_migration_or_skip():
    """Executable MySQL migration and contract verification (defaults, insert, FK, CHECK, unique, null active)."""
    conn, skip_reason = _resolve_mysql_conn()
    if conn is None:
        pytest.skip(skip_reason)

    import uuid
    import re
    import pymysql

    pfx = f"t0650_{uuid.uuid4().hex[:6]}"
    tbl_ps = f"{pfx}_ps"
    tbl_proj = f"{pfx}_proj"
    tbl_grp = f"{pfx}_grp"
    tbl_doc = f"{pfx}_doc"
    tbl_usr = f"{pfx}_usr"
    tbl_runs = f"{pfx}_runs"

    cur = conn.cursor()
    try:
        cur.execute(f"CREATE TABLE {tbl_ps} (project_id VARCHAR(64) PRIMARY KEY, digits_group INT NOT NULL DEFAULT 4) ENGINE=InnoDB")
        cur.execute(f"INSERT INTO {tbl_ps} (project_id) VALUES ('p_existing')")
        cur.execute(f"CREATE TABLE {tbl_proj} (project_id VARCHAR(64) PRIMARY KEY, project_name VARCHAR(255) NOT NULL, is_active INT NOT NULL DEFAULT 1) ENGINE=InnoDB")
        cur.execute(f"CREATE TABLE {tbl_grp} (group_id VARCHAR(64) PRIMARY KEY, project_id VARCHAR(64) NOT NULL, group_name VARCHAR(255) NOT NULL) ENGINE=InnoDB")
        cur.execute(f"CREATE TABLE {tbl_doc} (doc_id VARCHAR(64) PRIMARY KEY, project_id VARCHAR(64) NOT NULL, group_id VARCHAR(64) NOT NULL) ENGINE=InnoDB")
        cur.execute(f"CREATE TABLE {tbl_usr} (user_id VARCHAR(64) PRIMARY KEY, user_name VARCHAR(255) NOT NULL) ENGINE=InnoDB")

        # Rewrite migration SQL targeting prefixed test tables
        raw_sql = _MIGRATION_MYSQL.read_text(encoding="utf-8")
        sql = re.sub(r"\bproject_settings\b", tbl_ps, raw_sql)
        sql = re.sub(r"\btr_self_check_runs\b", tbl_runs, sql)
        sql = re.sub(r"\bprojects\b", tbl_proj, sql)
        sql = re.sub(r"\bgroups\b", tbl_grp, sql)
        sql = re.sub(r"\bdocuments\b", tbl_doc, sql)
        sql = re.sub(r"\busers\b", tbl_usr, sql)
        sql = re.sub(r"\bfk_selfcheck_", f"fk_{pfx}_", sql)
        sql = re.sub(r"\bidx_selfcheck_", f"idx_{pfx}_", sql)

        for stmt in sql.split(";"):
            stmt = stmt.strip()
            if stmt:
                cur.execute(stmt)

        # 1. Existing/fresh defaults
        cur.execute(f"SELECT tr_self_check_enabled FROM {tbl_ps} WHERE project_id = 'p_existing'")
        assert cur.fetchone()[0] == 0, "MySQL existing project_settings default must be 0"
        cur.execute(f"INSERT INTO {tbl_ps} (project_id) VALUES ('p_fresh')")
        cur.execute(f"SELECT tr_self_check_enabled FROM {tbl_ps} WHERE project_id = 'p_fresh'")
        assert cur.fetchone()[0] == 0, "MySQL fresh project_settings default must be 0"

        # Parent rows
        cur.execute(f"INSERT INTO {tbl_proj} (project_id, project_name) VALUES ('p_1', 'P1')")
        cur.execute(f"INSERT INTO {tbl_grp} (group_id, project_id, group_name) VALUES ('g_1', 'p_1', 'G1')")
        cur.execute(f"INSERT INTO {tbl_doc} (doc_id, project_id, group_id) VALUES ('d_1', 'p_1', 'g_1')")
        cur.execute(f"INSERT INTO {tbl_usr} (user_id, user_name) VALUES ('u_1', 'U1')")

        # 2. Run insertion
        cur.execute(f"""
            INSERT INTO {tbl_runs} (
                self_check_run_id, project_id, group_id, tr_doc_id, requested_by,
                policy_version, program, args_json, resolved_executable_path,
                resolved_executable_name, executable_origin, cwd_relative,
                timeout_seconds, env_keys_json, status, active_key, recovery_state,
                cancel_requested, timed_out, cleanup_pending, created_at, updated_at
            ) VALUES (
                'r_1', 'p_1', 'g_1', 'd_1', 'u_1',
                '1.0', 'pytest', '[]', '/p', 'p', 'local', '.',
                60, '[]', 'running', 'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa', 'none',
                0, 0, 0, '2026-09-30 00:00:00', '2026-09-30 00:00:00'
            )
        """)

        # 3. Unique active key constraint
        with pytest.raises(pymysql.IntegrityError):
            cur.execute(f"""
                INSERT INTO {tbl_runs} (
                    self_check_run_id, project_id, group_id, tr_doc_id, requested_by,
                    policy_version, program, args_json, resolved_executable_path,
                    resolved_executable_name, executable_origin, cwd_relative,
                    timeout_seconds, env_keys_json, status, active_key, recovery_state,
                    cancel_requested, timed_out, cleanup_pending, created_at, updated_at
                ) VALUES (
                    'r_2', 'p_1', 'g_1', 'd_1', 'u_1',
                    '1.0', 'pytest', '[]', '/p', 'p', 'local', '.',
                    60, '[]', 'running', 'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa', 'none',
                    0, 0, 0, '2026-09-30 00:00:00', '2026-09-30 00:00:00'
                )
            """)

        # 4. Multiple NULL terminal keys allowed
        for r_id in ['r_term_1', 'r_term_2']:
            cur.execute(f"""
                INSERT INTO {tbl_runs} (
                    self_check_run_id, project_id, group_id, tr_doc_id, requested_by,
                    policy_version, program, args_json, resolved_executable_path,
                    resolved_executable_name, executable_origin, cwd_relative,
                    timeout_seconds, env_keys_json, status, active_key, recovery_state,
                    cancel_requested, timed_out, cleanup_pending, created_at, updated_at
                ) VALUES (
                    '{r_id}', 'p_1', 'g_1', 'd_1', 'u_1',
                    '1.0', 'pytest', '[]', '/p', 'p', 'local', '.',
                    60, '[]', 'completed', NULL, 'none',
                    0, 0, 0, '2026-09-30 00:00:00', '2026-09-30 00:00:00'
                )
            """)

        # 5. CHECK constraints
        with pytest.raises((pymysql.IntegrityError, pymysql.OperationalError)):
            cur.execute(f"UPDATE {tbl_runs} SET status = 'invalid_status' WHERE self_check_run_id = 'r_1'")
        with pytest.raises((pymysql.IntegrityError, pymysql.OperationalError)):
            cur.execute(f"UPDATE {tbl_runs} SET recovery_state = 'invalid_rec' WHERE self_check_run_id = 'r_1'")
        with pytest.raises((pymysql.IntegrityError, pymysql.OperationalError)):
            cur.execute(f"UPDATE {tbl_ps} SET tr_self_check_enabled = 2 WHERE project_id = 'p_fresh'")

        # HASH64 constraints reject short, uppercase and non-hex digests in every field.
        for col in (
            "active_key", "source_refs_hash_before", "source_refs_hash_after",
            "source_index_hash_before", "source_index_hash_after",
            "source_status_hash_before", "source_status_hash_after",
        ):
            for bad in ("abc", "A" * 64, "g" * 64):
                with pytest.raises((pymysql.IntegrityError, pymysql.OperationalError)):
                    cur.execute(
                        f"UPDATE {tbl_runs} SET {col} = %s WHERE self_check_run_id = 'r_1'",
                        (bad,),
                    )

        # 6. FK actions: SET NULL & CASCADE
        cur.execute(f"DELETE FROM {tbl_usr} WHERE user_id = 'u_1'")
        cur.execute(f"SELECT requested_by FROM {tbl_runs} WHERE self_check_run_id = 'r_1'")
        assert cur.fetchone()[0] is None, "MySQL FK ON DELETE SET NULL failed"

        cur.execute(f"DELETE FROM {tbl_proj} WHERE project_id = 'p_1'")
        cur.execute(f"SELECT COUNT(*) FROM {tbl_runs}")
        assert cur.fetchone()[0] == 0, "MySQL FK ON DELETE CASCADE failed"

    finally:
        for t in [tbl_runs, tbl_doc, tbl_grp, tbl_proj, tbl_ps, tbl_usr]:
            try:
                cur.execute(f"DROP TABLE IF EXISTS {t}")
            except Exception:
                pass
        conn.close()


@pytest.mark.parametrize("column", [
    "active_key",
    "source_refs_hash_before", "source_refs_hash_after",
    "source_index_hash_before", "source_index_hash_after",
    "source_status_hash_before", "source_status_hash_after",
])
@pytest.mark.parametrize("bad", ["abc", "A" * 64, "g" * 64])
def test_30_sqlite_hash64_rejects_invalid_values(mock_db, column, bad):
    mock_db._execute("DELETE FROM tr_self_check_runs WHERE project_id = 'p_0650'")
    run = selfcheck_db.create_pending(
        project_id="p_0650", group_id="g_0650_1", tr_doc_id="d_tr_0001",
        requested_by=None, policy_version="1.0", program="pytest", args=[],
        resolved_executable_path="/p", resolved_executable_name="p",
        executable_origin="local", cwd_relative=".", timeout_seconds=60, env_keys=[],
    )
    with pytest.raises(Exception):
        with mock_db.transaction():
            mock_db._execute(
                f"UPDATE tr_self_check_runs SET {column} = ? WHERE self_check_run_id = ?",
                [bad, run["self_check_run_id"]],
            )
    assert selfcheck_db.get_run(run["self_check_run_id"])[column] != bad


@pytest.mark.parametrize("bad", ["abc", "A" * 64, "g" * 64])
def test_31_repository_hash64_validation(mock_db, bad):
    mock_db._execute("DELETE FROM tr_self_check_runs WHERE project_id = 'p_0650'")
    run = selfcheck_db.create_pending(
        project_id="p_0650", group_id="g_0650_1", tr_doc_id="d_tr_0001",
        requested_by=None, policy_version="1.0", program="pytest", args=[],
        resolved_executable_path="/p", resolved_executable_name="p",
        executable_origin="local", cwd_relative=".", timeout_seconds=60, env_keys=[],
    )
    run_id = run["self_check_run_id"]
    for field in ("source_refs_hash_before", "source_index_hash_before", "source_status_hash_before"):
        with pytest.raises(ValueError, match=field):
            selfcheck_db.mark_running(run_id, **{field: bad})
    for field in ("source_refs_hash_after", "source_index_hash_after", "source_status_hash_after"):
        with pytest.raises(ValueError, match=field):
            selfcheck_db.finish_completed(run_id, **{field: bad})
        with pytest.raises(ValueError, match=field):
            selfcheck_db.finish_failed(run_id, **{field: bad})
    assert selfcheck_db.get_run(run_id)["status"] == "pending"


def test_32_create_pending_keeps_non_active_key_errors(mock_db):
    mock_db._execute("DELETE FROM tr_self_check_runs WHERE project_id = 'p_0650'")
    kwargs = dict(
        project_id="p_0650", group_id="g_0650_1", tr_doc_id="missing_document",
        requested_by=None, policy_version="1.0", program="pytest", args=[],
        resolved_executable_path="/p", resolved_executable_name="p",
        executable_origin="local", cwd_relative=".", timeout_seconds=60, env_keys=[],
    )
    with pytest.raises(Exception) as fk:
        selfcheck_db.create_pending(**kwargs)
    assert not isinstance(fk.value, selfcheck_db.SelfCheckAlreadyRunningError)
    kwargs["tr_doc_id"] = "d_tr_0001"
    kwargs["run_id"] = "duplicate_id"
    first = selfcheck_db.create_pending(**kwargs)
    selfcheck_db.finish_completed(first["self_check_run_id"])
    with pytest.raises(Exception) as duplicate:
        selfcheck_db.create_pending(**kwargs)
    assert not isinstance(duplicate.value, selfcheck_db.SelfCheckAlreadyRunningError)


def test_33_recovery_cas_rejects_shortcuts(mock_db):
    mock_db._execute("DELETE FROM tr_self_check_runs WHERE project_id = 'p_0650'")
    run = selfcheck_db.create_pending(
        project_id="p_0650", group_id="g_0650_1", tr_doc_id="d_tr_0001",
        requested_by=None, policy_version="1.0", program="pytest", args=[],
        resolved_executable_path="/p", resolved_executable_name="p",
        executable_origin="local", cwd_relative=".", timeout_seconds=60, env_keys=[],
    )
    rid = run["self_check_run_id"]
    assert selfcheck_db.mark_recovery_incomplete(rid, "early") is None
    assert selfcheck_db.finish_recovered_interrupted(rid) is None
    assert selfcheck_db.mark_running(rid) is not None
    assert selfcheck_db.mark_running(rid) is None
    assert selfcheck_db.mark_recovering(rid) is not None
    assert selfcheck_db.mark_running(rid) is None
    assert selfcheck_db.mark_recovery_incomplete(rid, "retry") is not None
    assert selfcheck_db.finish_recovered_interrupted(rid) is None
    assert selfcheck_db.retry_recovery_incomplete(rid) is not None
    assert selfcheck_db.finish_recovered_interrupted(rid) is not None


def test_34_active_key_insert_race_maps_only_unique_collision(mock_db, monkeypatch):
    mock_db._execute("DELETE FROM tr_self_check_runs WHERE project_id = 'p_0650'")
    kwargs = dict(
        project_id="p_0650", group_id="g_0650_1", tr_doc_id="d_tr_0001",
        requested_by=None, policy_version="1.0", program="pytest", args=[],
        resolved_executable_path="/p", resolved_executable_name="p",
        executable_origin="local", cwd_relative=".", timeout_seconds=60, env_keys=[],
    )
    existing = selfcheck_db.create_pending(**kwargs)
    actual_get_active = selfcheck_db.get_active
    calls = 0

    def race_get_active(project_id, group_id):
        nonlocal calls
        calls += 1
        if calls == 1:
            return None
        return actual_get_active(project_id, group_id)

    monkeypatch.setattr(selfcheck_db, "get_active", race_get_active)
    with pytest.raises(selfcheck_db.SelfCheckAlreadyRunningError) as raised:
        selfcheck_db.create_pending(**kwargs)
    assert raised.value.existing_run_id == existing["self_check_run_id"]
    assert calls == 2
