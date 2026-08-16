"""flowgate.default.0358 T0004 — immediate cancel: cancel route, cancelling/cancelled
status, process-tree kill, and every place a cancelled run must NOT go (auto-recovery,
chain-failure alarm, TSR assembly, chain auto-approve).

Covers the T's 완료 기준 신규 테스트 목록 1-8. Groups:
  * DB layer (migration 074 + CAS helpers) against a real sqlite connection with all
    migrations applied — the app's actual FlowGateStore is a thin wrapper over
    _execute/_fetch_one/_fetch_all/transaction, so a minimal stand-in bound to that
    connection exercises the real SQL (CHECK constraint, WHERE-gated writes).
  * Service layer (test_run_service.request_cancel / _execute_run_inner) with the
    existing codebase style: monkeypatched db_test_runs/db_docs, no real DB.
  * Route layer (test_run_routes.post_test_run_cancel) permission gate.
"""
from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException


# ── DB layer: real sqlite roundtrip ──────────────────────────────────────────


class _TestStore:
    """Minimal FlowGateStore stand-in bound to all_migrations_db's real connection.

    Deliberately has no _sql — db/test_runs.py never calls it, so absence of that
    method means any accidental call fails loudly instead of silently succeeding
    against a query this test isn't exercising (see memory:
    real-db-server-contract-test-recipe).
    """

    def __init__(self, conn):
        self._conn = conn

    @contextmanager
    def transaction(self):
        yield self

    def _execute(self, sql, params=None):
        self._conn.execute(sql, params or [])
        self._conn.commit()

    def _fetch_one(self, sql, params=None):
        row = self._conn.execute(sql, params or []).fetchone()
        return dict(row) if row is not None else None

    def _fetch_all(self, sql, params=None):
        rows = self._conn.execute(sql, params or []).fetchall()
        return [dict(r) for r in rows]


@pytest.fixture
def store(all_migrations_db, monkeypatch):
    from modules.flow_gate.db import connection

    test_store = _TestStore(all_migrations_db)
    monkeypatch.setattr(connection, "STORE", test_store)
    return test_store


def _seed_doc(conn, doc_id: str) -> None:
    conn.execute(
        "INSERT OR IGNORE INTO documents "
        "(doc_id, project_id, module, type_code, seq, title, status, created_at, updated_at) "
        "VALUES (?, '__SYSTEM__', 'default', 'TS', 1, 'seed TS', 'approved', "
        "datetime('now'), datetime('now'))",
        [doc_id],
    )
    conn.commit()


def _seed_run(conn, run_id: str, doc_id: str, *, status: str, picked_at: str | None) -> None:
    conn.execute(
        "INSERT INTO test_runs "
        "(run_id, doc_id, revision_no, status, triggered_via, runner_id, "
        "case_total, case_passed, case_failed, picked_at, started_at, created_at) "
        "VALUES (?, ?, 0, ?, 'ui', 'user-1', 1, 0, 0, ?, datetime('now'), datetime('now'))",
        [run_id, doc_id, status, picked_at],
    )
    conn.commit()


def test_migration_074_check_accepts_cancel_states_and_preserves_columns(all_migrations_db):
    conn = all_migrations_db
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(test_runs)").fetchall()}
    # Rebuilt table (SQLite CHECK change requires it) must still carry every column,
    # including the ones 069 added after 052 originally created the table.
    assert {"port", "source_root", "source_root_kind", "tsr_doc_id", "created_at"} <= columns

    _seed_doc(conn, "flowgate.default.0358.9001-TS")
    _seed_run(conn, "trun_0358_check_1", "flowgate.default.0358.9001-TS", status="cancelling", picked_at=None)
    row = conn.execute(
        "SELECT status FROM test_runs WHERE run_id = ?", ["trun_0358_check_1"]
    ).fetchone()
    assert row["status"] == "cancelling"

    conn.execute(
        "UPDATE test_runs SET status = 'cancelled' WHERE run_id = ?", ["trun_0358_check_1"]
    )
    conn.commit()

    with pytest.raises(Exception):
        conn.execute(
            "UPDATE test_runs SET status = 'bogus_status' WHERE run_id = ?",
            ["trun_0358_check_1"],
        )
    conn.rollback()


_MIGRATIONS_DIR = Path(__file__).resolve().parents[1] / "sql" / "migrations" / "sqlite"


def _fk_parents(conn) -> list[tuple[str, str]]:
    """Every (child table, referenced parent table) pair declared in the schema."""
    tables = [
        row["name"]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' "
            "AND name NOT LIKE 'sqlite_%'"
        ).fetchall()
    ]
    pairs: list[tuple[str, str]] = []
    for table in tables:
        for row in conn.execute(f'PRAGMA foreign_key_list("{table}")').fetchall():
            pairs.append((table, row["table"]))
    return pairs


def _assert_schema_is_bound(conn) -> None:
    """No REFERENCES clause may name a table that does not exist."""
    tables = {
        row["name"]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        ).fetchall()
    }
    dangling = sorted({(c, p) for c, p in _fk_parents(conn) if p not in tables})
    assert dangling == [], f"foreign keys pointing at missing tables: {dangling}"

    case_sql = conn.execute(
        "SELECT sql FROM sqlite_master WHERE name = 'test_run_cases'"
    ).fetchone()["sql"]
    assert "REFERENCES test_runs(run_id)" in case_sql, case_sql


def test_migrations_leave_no_foreign_key_pointing_at_a_dropped_table(all_migrations_db):
    """Regression for the 074 table rebuild.

    The first revision of 074 rebuilt test_runs the way 042/052 rebuild `tokens`:
    ALTER TABLE test_runs RENAME TO test_runs_before_cancel_status, create the new
    table, copy, drop the backup. Since SQLite 3.25 that rename also rewrites the
    REFERENCES clause of every *other* table pointing at it — regardless of
    PRAGMA foreign_keys — so test_run_cases.run_id was left referencing the backup
    table, which the same migration then dropped. `tokens` never hit this because no
    table has a REFERENCES clause on it.

    This assertion is schema-wide on purpose: any future migration that renames a
    referenced table fails here instead of at the first INSERT in production.
    """
    _assert_schema_is_bound(all_migrations_db)


def test_test_run_cases_insert_and_cascade_work_with_foreign_keys_on(all_migrations_db):
    """The exact statement that 500'd after the broken 074: writing a run's case rows.

    connection.transaction() turns `PRAGMA foreign_keys = ON` on for every write, and
    an unresolvable parent table raises OperationalError
    ("no such table: main.test_runs_before_cancel_status") on INSERT — so no test run
    could start. Note PRAGMA foreign_key_check does NOT catch this on a freshly
    migrated database: with test_run_cases still empty it has no row to resolve and
    reports clean.
    """
    conn = all_migrations_db
    # all_migrations_db is session-scoped and shared, and other suites toggle this pragma
    # (and it is a no-op inside an open transaction), so set it explicitly rather than
    # asserting whatever ran before left it on. connection.transaction() turns it on for
    # every real write, which is the state this test is about.
    conn.commit()
    conn.execute("PRAGMA foreign_keys = ON")
    assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1

    doc_id = "flowgate.default.0358.9004-TS"
    run_id = "trun_0358_fk_bind"
    _seed_doc(conn, doc_id)
    _seed_run(conn, run_id, doc_id, status="running", picked_at=None)

    conn.execute(
        "INSERT INTO test_run_cases "
        "(run_id, kind, case_no, case_title, cmd, expect) "
        "VALUES (?, 'case', 'C-1', 'seed case', 'echo hi', 'exits zero')",
        [run_id],
    )
    conn.commit()
    assert conn.execute(
        "SELECT COUNT(*) AS n FROM test_run_cases WHERE run_id = ?", [run_id]
    ).fetchone()["n"] == 1

    # ON DELETE CASCADE proves the child rows are bound to the real test_runs table,
    # not merely to some name that happens to parse.
    conn.execute("DELETE FROM test_runs WHERE run_id = ?", [run_id])
    conn.commit()
    assert conn.execute(
        "SELECT COUNT(*) AS n FROM test_run_cases WHERE run_id = ?", [run_id]
    ).fetchone()["n"] == 0


def test_sqloader_migrator_end_to_end_leaves_test_run_cases_bound(tmp_path):
    """Same guarantee through the real deployment path.

    conftest's all_migrations_db applies migrations with sqlite3.executescript and
    prints (rather than raises) on error; production applies them with sqloader's
    DatabaseMigrator, one file per transaction, which is what actually ran on the
    server that failed. Assert against that path too, on a genuinely empty database.
    """
    import sqlite3

    from sqloader.migrator import DatabaseMigrator
    from sqloader.sqlite3 import SQLiteWrapper

    db_path = tmp_path / "all_migrations.db"
    DatabaseMigrator(SQLiteWrapper(str(db_path)), str(_MIGRATIONS_DIR), auto_run=True)

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA foreign_keys = ON")
        _assert_schema_is_bound(conn)

        applied = {
            row["filename"]
            for row in conn.execute("SELECT filename FROM migrations").fetchall()
        }
        assert "074_test_run_cancel_status.sql" in applied
        assert "075_test_run_cases_fk_repair.sql" in applied

        run_sql = conn.execute(
            "SELECT sql FROM sqlite_master WHERE name = 'test_runs'"
        ).fetchone()["sql"]
        assert "'cancelling','cancelled'" in run_sql.replace(" ", "")

        indexes = {
            row["name"]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'index' "
                "AND tbl_name IN ('test_runs', 'test_run_cases')"
            ).fetchall()
        }
        assert {"idx_test_runs_doc", "idx_test_run_cases_run"} <= indexes
    finally:
        conn.close()


def test_migration_075_repairs_a_database_poisoned_by_the_old_074(tmp_path):
    """075 must fix databases that already recorded the broken 074 and never re-run it.

    Rebuild that exact damaged shape by hand — test_run_cases referencing a dropped
    backup table, with rows in it — then apply 075's statements and assert the
    reference is bound again and no row was lost.
    """
    import sqlite3

    db_path = tmp_path / "poisoned.db"
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE test_runs (run_id TEXT PRIMARY KEY, status TEXT NOT NULL);
        CREATE TABLE test_run_cases (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id       TEXT    NOT NULL REFERENCES test_runs(run_id) ON DELETE CASCADE,
            kind         TEXT    NOT NULL DEFAULT 'case'
                                 CHECK (kind IN ('case','setup','service','wait','teardown')),
            case_no      TEXT    NOT NULL,
            case_title   TEXT    NOT NULL,
            cmd          TEXT    NOT NULL,
            expect       TEXT    NOT NULL,
            result       TEXT    CHECK (result IN ('pass','fail','timeout')),
            exit_code    INTEGER,
            duration_ms  INTEGER,
            output_tail  TEXT,
            finished_at  TEXT,
            UNIQUE(run_id, case_no)
        );
        INSERT INTO test_runs VALUES ('trun_old', 'cancelled');
        INSERT INTO test_run_cases (run_id, kind, case_no, case_title, cmd, expect)
            VALUES ('trun_old', 'case', 'C-1', 'kept', 'echo hi', 'exits zero');
        """
    )
    conn.commit()
    # Reproduce the damage the old 074 caused, with the same rename it used.
    conn.execute("ALTER TABLE test_runs RENAME TO test_runs_before_cancel_status")
    conn.execute("CREATE TABLE test_runs (run_id TEXT PRIMARY KEY, status TEXT NOT NULL)")
    conn.execute(
        "INSERT INTO test_runs SELECT * FROM test_runs_before_cancel_status"
    )
    conn.execute("DROP TABLE test_runs_before_cancel_status")
    conn.commit()
    poisoned = conn.execute(
        "SELECT sql FROM sqlite_master WHERE name = 'test_run_cases'"
    ).fetchone()["sql"]
    assert "test_runs_before_cancel_status" in poisoned, poisoned
    conn.execute("PRAGMA foreign_keys = ON")
    with pytest.raises(sqlite3.OperationalError, match="no such table"):
        conn.execute(
            "INSERT INTO test_run_cases (run_id, kind, case_no, case_title, cmd, expect) "
            "VALUES ('trun_old', 'case', 'C-2', 'blocked', 'echo hi', 'exits zero')"
        )
    conn.rollback()
    conn.close()

    repair = (_MIGRATIONS_DIR / "075_test_run_cases_fk_repair.sql").read_text(
        encoding="utf-8"
    )
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.executescript(repair)
    conn.commit()

    conn.execute("PRAGMA foreign_keys = ON")
    _assert_schema_is_bound(conn)
    assert conn.execute(
        "SELECT COUNT(*) AS n FROM test_run_cases"
    ).fetchone()["n"] == 1
    # The write that used to 500 now succeeds on the repaired database.
    conn.execute(
        "INSERT INTO test_run_cases (run_id, kind, case_no, case_title, cmd, expect) "
        "VALUES ('trun_old', 'case', 'C-2', 'now allowed', 'echo hi', 'exits zero')"
    )
    conn.commit()
    assert conn.execute(
        "SELECT COUNT(*) AS n FROM test_run_cases"
    ).fetchone()["n"] == 2
    conn.close()


def test_cas_running_to_cancelling_then_cancelling_to_cancelled_roundtrip(store, all_migrations_db):
    from modules.flow_gate.db import test_runs as db_test_runs

    conn = all_migrations_db
    _seed_doc(conn, "flowgate.default.0358.9002-TS")
    _seed_run(conn, "trun_0358_cas_1", "flowgate.default.0358.9002-TS", status="running", picked_at="2026-08-16T00:00:00+09:00")

    db_test_runs.cas_running_to_cancelling("trun_0358_cas_1")
    assert db_test_runs.get_run("trun_0358_cas_1")["status"] == "cancelling"

    # Second running->cancelling CAS is a safe no-op (already past 'running').
    db_test_runs.cas_running_to_cancelling("trun_0358_cas_1")
    assert db_test_runs.get_run("trun_0358_cas_1")["status"] == "cancelling"

    db_test_runs.cas_cancelling_to_cancelled("trun_0358_cas_1", error="cancelled_by_user")
    final = db_test_runs.get_run("trun_0358_cas_1")
    assert final["status"] == "cancelled"
    assert final["error"] == "cancelled_by_user"
    assert final["finished_at"] is not None


def test_finish_run_does_not_overwrite_already_cancelled_row(store, all_migrations_db):
    """0358 T0004 위험 2: a natural completion racing a cancel must not resurrect it."""
    from modules.flow_gate.db import test_runs as db_test_runs

    conn = all_migrations_db
    _seed_doc(conn, "flowgate.default.0358.9003-TS")
    _seed_run(conn, "trun_0358_race_1", "flowgate.default.0358.9003-TS", status="cancelled", picked_at="2026-08-16T00:00:00+09:00")

    db_test_runs.finish_run(run_id="trun_0358_race_1", status="passed", case_passed=1, case_failed=0)

    row = db_test_runs.get_run("trun_0358_race_1")
    assert row["status"] == "cancelled"  # NOT overwritten to 'passed'


def test_get_running_by_doc_treats_cancelling_as_active(store, all_migrations_db):
    from modules.flow_gate.db import test_runs as db_test_runs

    conn = all_migrations_db
    doc_id = "flowgate.default.0358.9004-TS"
    _seed_doc(conn, doc_id)
    _seed_run(conn, "trun_0358_active_1", doc_id, status="cancelling", picked_at="2026-08-16T00:00:00+09:00")

    active = db_test_runs.get_running_by_doc(doc_id)
    assert active is not None
    assert active["run_id"] == "trun_0358_active_1"

    conn.execute("UPDATE test_runs SET status = 'cancelled' WHERE run_id = ?", ["trun_0358_active_1"])
    conn.commit()
    assert db_test_runs.get_running_by_doc(doc_id) is None  # cancelled admits a fresh run


def test_mark_orphaned_running_handles_running_and_cancelling_rows(store, all_migrations_db):
    from modules.flow_gate.db import test_runs as db_test_runs

    conn = all_migrations_db
    doc_id = "flowgate.default.0358.9005-TS"
    _seed_doc(conn, doc_id)
    _seed_run(conn, "trun_0358_orphan_running", doc_id, status="running", picked_at="2026-08-16T00:00:00+09:00")
    _seed_run(conn, "trun_0358_orphan_cancelling", doc_id, status="cancelling", picked_at="2026-08-16T00:00:00+09:00")

    reaped = db_test_runs.mark_orphaned_running()
    assert reaped == 2

    running_row = db_test_runs.get_run("trun_0358_orphan_running")
    assert running_row["status"] == "failed"
    assert running_row["error"] == "orphaned_by_restart"

    cancelling_row = db_test_runs.get_run("trun_0358_orphan_cancelling")
    assert cancelling_row["status"] == "cancelled"
    assert cancelling_row["error"] == "cancelled_by_restart"


# ── Service layer: request_cancel decision logic (mocked db) ────────────────


@pytest.fixture(autouse=True)
def _clean_active_registry():
    from modules.flow_gate.services import test_run_service as svc

    yield
    svc._active_runs.clear()


def test_request_cancel_unknown_run_id_raises_404(monkeypatch):
    from modules.flow_gate.services import test_run_service as svc

    monkeypatch.setattr(svc.db_test_runs, "get_run", lambda _rid: None)

    with pytest.raises(HTTPException) as exc_info:
        svc.request_cancel("trun_does_not_exist")
    assert exc_info.value.status_code == 404
    assert exc_info.value.detail["error"] == "run_not_found"


def test_request_cancel_already_finished_returns_final_status(monkeypatch):
    from modules.flow_gate.services import test_run_service as svc

    monkeypatch.setattr(
        svc.db_test_runs, "get_run",
        lambda _rid: {"run_id": "trun_1", "status": "passed", "doc_id": "d"},
    )
    result = svc.request_cancel("trun_1")
    assert result == {"run_id": "trun_1", "status": "passed"}


def test_request_cancel_not_yet_picked_finalizes_immediately_to_cancelled(monkeypatch):
    """#6: no worker owns this row yet — cancel goes straight to 'cancelled'."""
    from modules.flow_gate.services import test_run_service as svc

    run_row = {"run_id": "trun_2", "status": "running", "picked_at": None, "doc_id": "d1"}
    cancelled_row = {**run_row, "status": "cancelled", "error": "cancelled_by_user"}

    calls = {"cas_running": 0, "cas_cancelled": 0}

    def fake_get_run(_rid):
        return cancelled_row if calls["cas_cancelled"] else run_row

    monkeypatch.setattr(svc.db_test_runs, "get_run", fake_get_run)
    monkeypatch.setattr(
        svc.db_test_runs, "cas_running_to_cancelling",
        lambda _rid: calls.__setitem__("cas_running", calls["cas_running"] + 1),
    )
    monkeypatch.setattr(
        svc.db_test_runs, "cas_cancelling_to_cancelled",
        lambda _rid, **_k: calls.__setitem__("cas_cancelled", calls["cas_cancelled"] + 1),
    )
    monkeypatch.setattr(svc.db_docs, "get_by_id", lambda _id: {"doc_id": "d1", "project_id": "flowgate"})
    emitted = []
    monkeypatch.setattr(svc, "_emit_finished", lambda doc, run, tsr: emitted.append((doc, run, tsr)))

    result = svc.request_cancel("trun_2")

    assert result == {"run_id": "trun_2", "status": "cancelled"}
    assert calls["cas_running"] == 1
    assert calls["cas_cancelled"] == 1
    assert len(emitted) == 1
    assert svc._get_active_run("trun_2") is None  # placeholder cleaned up


def test_request_cancel_picked_by_worker_kills_active_proc_and_returns_cancelling(monkeypatch):
    """#1: the worker owns this row — kill the tracked proc, return 'cancelling'
    immediately without waiting for cleanup."""
    from modules.flow_gate.services import test_run_service as svc

    run_row = {"run_id": "trun_3", "status": "running", "picked_at": "2026-08-16T00:00:00+09:00", "doc_id": "d1"}
    monkeypatch.setattr(svc.db_test_runs, "get_run", lambda _rid: run_row)
    monkeypatch.setattr(svc.db_test_runs, "cas_running_to_cancelling", lambda _rid: None)

    # Simulate the worker already having registered an active run with a live proc
    # and a live service proc before the cancel arrives.
    entry = svc._register_active_run("trun_3")
    fake_proc = MagicMock()
    fake_service_proc = MagicMock()
    entry.proc = fake_proc
    entry.service_procs.add(fake_service_proc)

    killed = []
    monkeypatch.setattr(svc, "_kill_process_tree", lambda proc: killed.append(proc))

    result = svc.request_cancel("trun_3")

    assert result == {"run_id": "trun_3", "status": "cancelling"}
    assert set(killed) == {fake_proc, fake_service_proc}
    assert entry.cancel_event.is_set()


def test_request_cancel_idempotent_second_call_no_overwrite(monkeypatch):
    """#3: cancel twice — second call is a 200 idempotent replay, no duplicate work."""
    from modules.flow_gate.services import test_run_service as svc

    state = {"status": "running", "picked_at": None}

    def fake_get_run(_rid):
        return {"run_id": "trun_4", "doc_id": "d1", **state}

    def fake_cas_running(_rid):
        if state["status"] == "running":
            state["status"] = "cancelling"

    def fake_cas_cancelled(_rid, **_k):
        if state["status"] == "cancelling":
            state["status"] = "cancelled"

    monkeypatch.setattr(svc.db_test_runs, "get_run", fake_get_run)
    monkeypatch.setattr(svc.db_test_runs, "cas_running_to_cancelling", fake_cas_running)
    monkeypatch.setattr(svc.db_test_runs, "cas_cancelling_to_cancelled", fake_cas_cancelled)
    monkeypatch.setattr(svc.db_docs, "get_by_id", lambda _id: {"doc_id": "d1", "project_id": "flowgate"})
    emit_count = []
    monkeypatch.setattr(svc, "_emit_finished", lambda *a, **k: emit_count.append(1))

    first = svc.request_cancel("trun_4")
    second = svc.request_cancel("trun_4")

    assert first == {"run_id": "trun_4", "status": "cancelled"}
    assert second == {"run_id": "trun_4", "status": "cancelled"}
    assert state["status"] == "cancelled"  # never bounced back to running/cancelling
    assert len(emit_count) == 1  # only the winner emits


# ── Service layer: _execute_run_inner cancellation checkpoints ──────────────


def _base_doc():
    return {
        "doc_id": "flowgate.default.0358.9100-TS",
        "project_id": "flowgate",
        "branch": "main",
        "group_id": "flowgate.default.0358",
    }


def test_execute_run_inner_cancelled_before_start_skips_all_side_effects(monkeypatch, tmp_path):
    """#2/#4 core + 위험 1: cancel accepted before the worker even resolves src_root —
    no setup/case/teardown runs, no TSR/auto-recovery/chain-failure notify fires."""
    from modules.flow_gate.services import test_run_service as svc

    doc = _base_doc()
    run = {"run_id": "trun_5", "doc_id": doc["doc_id"], "revision_no": 1}

    monkeypatch.setattr(svc.db_docs, "get_by_id", lambda _id: doc)
    monkeypatch.setattr(svc.db_test_runs, "cas_cancelling_to_cancelled", lambda _rid, **_k: None)
    monkeypatch.setattr(
        svc.db_test_runs, "get_run",
        lambda _rid: {**run, "status": "cancelled", "error": "cancelled_by_user"},
    )
    resolver_calls = []
    monkeypatch.setattr(
        svc.storage_paths, "resolve_project_src_root",
        lambda *a, **k: resolver_calls.append(1) or tmp_path,
    )
    emitted = []
    monkeypatch.setattr(svc, "_emit_finished", lambda d, r, t: emitted.append((d, r, t)))
    recovery_calls = []
    monkeypatch.setattr(
        svc.engine_recipe_service, "handle_run_failure",
        lambda *a, **k: recovery_calls.append(1),
    )
    notify_calls = []
    monkeypatch.setattr(svc, "_maybe_notify_chain_failure", lambda *a, **k: notify_calls.append(1))
    assemble_calls = []
    monkeypatch.setattr(svc, "assemble_tsr", lambda *a, **k: assemble_calls.append(1))

    # Pre-register cancellation, as request_cancel would before the worker picks up.
    entry = svc._register_active_run("trun_5")
    entry.cancel_event.set()

    svc._execute_run_inner(run)

    assert resolver_calls == []  # bailed before src_root resolution
    assert len(emitted) == 1
    assert emitted[0][2] is None  # no tsr_doc_id
    assert recovery_calls == []
    assert notify_calls == []
    assert assemble_calls == []
    assert svc._get_active_run("trun_5") is None  # unregistered on the way out


def test_execute_run_inner_cancel_mid_run_skips_remaining_cases_and_cleans_up(monkeypatch, tmp_path):
    """#2/#7: cancel arrives after one case already ran — remaining cases are not
    executed (left NULL, not timeout), and services/scratch are still cleaned up."""
    from modules.flow_gate.services import test_run_service as svc

    doc = _base_doc()
    run_id = "trun_6"
    run = {"run_id": run_id, "doc_id": doc["doc_id"], "revision_no": 1}

    case1 = {"id": 1, "kind": "case", "case_no": "TC-1", "cmd": "echo 1", "expect": "ok"}
    case2 = {"id": 2, "kind": "case", "case_no": "TC-2", "cmd": "echo 2", "expect": "ok"}
    all_items = [case1, case2]

    monkeypatch.setattr(svc.db_docs, "get_by_id", lambda _id: doc)
    monkeypatch.setattr(svc.storage_paths, "resolve_project_src_root", lambda *a, **k: tmp_path)
    monkeypatch.setattr(svc, "_record_source_root", lambda *a, **k: None)
    monkeypatch.setattr(svc.db_test_runs, "set_run_port", lambda *a, **k: None)
    monkeypatch.setattr(svc.db_test_runs, "list_cases", lambda _rid: all_items)
    monkeypatch.setattr(svc.db_test_runs, "cas_cancelling_to_cancelled", lambda _rid, **_k: None)
    monkeypatch.setattr(
        svc.db_test_runs, "get_run",
        lambda _rid: {**run, "status": "cancelled", "error": "cancelled_by_user", "port": 1},
    )

    entry = svc._register_active_run(run_id)
    executed_cases = []

    def fake_execute_case(_doc, _run, case, idx, total, root, port, scratch, env, active):
        executed_cases.append(case["case_no"])
        entry.cancel_event.set()  # cancel fires mid-loop, after the first case

    monkeypatch.setattr(svc, "_execute_case", fake_execute_case)
    monkeypatch.setattr(
        svc, "_execute_setup", lambda *a, **k: (False, None)
    )
    teardown_calls = []
    monkeypatch.setattr(svc, "_execute_teardown", lambda *a, **k: teardown_calls.append(1))
    scratch_removed = []
    monkeypatch.setattr(svc, "_remove_scratch", lambda p: scratch_removed.append(p))
    finalize_services_calls = []
    monkeypatch.setattr(
        svc, "_finalize_services", lambda services, active=None: finalize_services_calls.append((services, active))
    )
    monkeypatch.setattr(svc, "_emit_finished", lambda *a, **k: None)

    svc._execute_run_inner(run)

    assert executed_cases == ["TC-1"]  # TC-2 never ran
    assert teardown_calls == [1]  # teardown still attempted (best-effort)
    assert len(scratch_removed) == 1  # scratch cleanup ran despite cancellation
    assert len(finalize_services_calls) == 1  # service cleanup ran despite cancellation
    assert svc._get_active_run(run_id) is None


# ── Route layer: permission gate ─────────────────────────────────────────────


class _FakeRequest:
    def __init__(self):
        self.headers = {"Authorization": "Bearer raw-token"}


def test_cancel_route_denies_without_perm_test_run(monkeypatch):
    from modules.flow_gate.api.v1 import test_run_routes as routes

    monkeypatch.setattr(
        routes, "verify_bearer",
        lambda _req: {"_is_user_jwt": True, "issued_to": "user-2", "is_admin": False},
    )
    monkeypatch.setattr(
        routes.db_test_runs, "get_run",
        lambda _rid: {"run_id": "trun_7", "doc_id": "d1", "status": "running"},
    )
    monkeypatch.setattr(routes.db_docs, "get_by_id", lambda _id: {"doc_id": "d1", "project_id": "flowgate"})
    monkeypatch.setattr(routes.test_run_service, "user_can_run_tests", lambda *_a, **_k: False)
    cancel_calls = []
    monkeypatch.setattr(routes.test_run_service, "request_cancel", lambda _rid: cancel_calls.append(1))

    resp = routes.post_test_run_cancel("trun_7", _FakeRequest())

    assert resp.status_code == 403
    assert cancel_calls == []


def test_cancel_route_allows_permission_holder_regardless_of_runner_mismatch(monkeypatch):
    """#5: a different user than the one who started the run can still cancel it, as
    long as they hold perm_test_run (or are admin) — no runner_id match required."""
    from modules.flow_gate.api.v1 import test_run_routes as routes

    monkeypatch.setattr(
        routes, "verify_bearer",
        lambda _req: {"_is_user_jwt": True, "issued_to": "user-2", "is_admin": False},
    )
    monkeypatch.setattr(
        routes.db_test_runs, "get_run",
        lambda _rid: {"run_id": "trun_8", "doc_id": "d1", "status": "running", "runner_id": "user-1"},
    )
    monkeypatch.setattr(routes.db_docs, "get_by_id", lambda _id: {"doc_id": "d1", "project_id": "flowgate"})
    monkeypatch.setattr(routes.test_run_service, "user_can_run_tests", lambda *_a, **_k: True)
    monkeypatch.setattr(
        routes.test_run_service, "request_cancel",
        lambda _rid: {"run_id": "trun_8", "status": "cancelling"},
    )

    resp = routes.post_test_run_cancel("trun_8", _FakeRequest())

    assert resp.status_code == 200


def test_cancel_route_unknown_run_id_404s(monkeypatch):
    from modules.flow_gate.api.v1 import test_run_routes as routes

    monkeypatch.setattr(
        routes, "verify_bearer",
        lambda _req: {"_is_user_jwt": True, "issued_to": "user-2", "is_admin": True},
    )
    monkeypatch.setattr(routes.db_test_runs, "get_run", lambda _rid: None)

    resp = routes.post_test_run_cancel("trun_missing", _FakeRequest())

    assert resp.status_code == 404
