"""real SQLite transactions, independent processes and Windows scratch ownership.

All databases replay the complete migration chain. Files stay in pytest basetemp;
no live service, DB, token or scratch root is used.
"""
from __future__ import annotations
from contextlib import contextmanager
from datetime import datetime, timezone, timedelta
from pathlib import Path
import hashlib
import json
import multiprocessing
import os
import sqlite3
import threading
from uuid import uuid4

import pytest
from modules.flow_gate.db import connection, ai_run_identity as identity, ai_invoke_runs as records

DAY = datetime(2026, 11, 6, tzinfo=timezone.utc)


class Transaction:
    def __init__(self, conn):
        self.conn, self.cursor = conn, None

    def execute(self, sql, params=()):
        self.cursor = self.conn.execute(sql, params)
        return self.cursor

    def fetchone(self):
        return self.cursor.fetchone()

    def fetchall(self):
        return self.cursor.fetchall()


class SQLiteBackend:
    db_type = 1

    def __init__(self, path):
        self.path = str(path)
        self.fail_commit = False
        self.local = threading.local()
        self.connections = []

    def close(self):
        for conn in self.connections:
            conn.close()
        self.connections.clear()

    @contextmanager
    def begin_transaction(self):
        conn = getattr(self.local, "connection", None)
        if conn is None:
            conn = sqlite3.connect(self.path, timeout=300, check_same_thread=False)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA foreign_keys = ON")
            self.local.connection = conn
            self.connections.append(conn)
        try:
            # FlowGateStore sets a shorter runtime timeout after entry; restore the
            # isolated stress harness wait before the next BEGIN (no ID fallback).
            conn.execute("PRAGMA busy_timeout = 300000")
            conn.execute("BEGIN IMMEDIATE")
            yield Transaction(conn)
            if self.fail_commit:
                raise OSError("injected commit failure")
            conn.commit()
        except BaseException:
            conn.rollback()
            raise

    def fetch_one(self, sql, params):
        with self.begin_transaction() as tx:
            tx.execute(sql, params)
            return tx.fetchone()

    def fetch_all(self, sql, params):
        with self.begin_transaction() as tx:
            tx.execute(sql, params)
            return tx.fetchall()

    def execute(self, sql, params):
        with self.begin_transaction() as tx:
            tx.execute(sql, params)


def open_store(path):
    store = connection.FlowGateStore()
    store._db = SQLiteBackend(path)
    store._sq = None
    return store


@pytest.fixture
def identity_db(migrated_sqlite_db, monkeypatch, tmp_path):
    path = migrated_sqlite_db("identity-0138.db")
    with sqlite3.connect(path) as conn:
        conn.execute("PRAGMA journal_mode=WAL")
    store = open_store(path)
    store._execute("INSERT INTO projects (project_id, project_name, created_at, updated_at) VALUES (?, ?, ?, ?)",
                   ["flowgate", "FlowGate", DAY.isoformat(), DAY.isoformat()])
    store._execute("INSERT INTO groups (group_id, project_id, module, title, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
                   ["flowgate.v02.0138", "flowgate", "v02", "Identity", DAY.isoformat(), DAY.isoformat()])
    monkeypatch.setattr(connection, "STORE", store)
    monkeypatch.setenv("FLOWGATE_STORAGE_DIR", str(tmp_path / "storage"))
    identity.initialize(scratch_roots=[])
    yield path, store
    store._db.close()


def owner(run_id=None, **overrides):
    row = {"run_id": run_id or identity.allocate(at=DAY), "execution_nonce": str(uuid4()),
           "project_id": "flowgate", "group_id": "flowgate.v02.0138", "started_at": DAY.isoformat()}
    row.update(overrides)
    return row


def _allocate_process(path, result_path, count, initialize=False):
    connection.STORE = open_store(path)
    if initialize:
        identity.initialize(scratch_roots=[])
    try:
        ids = [identity.allocate(at=DAY) for _ in range(count)]
        Path(result_path).write_text(json.dumps(ids), encoding="utf-8")
    finally:
        connection.STORE._db.close()


def test_environment_and_committed_identity(identity_db):
    path, store = identity_db
    print("0138 worktree:", Path(__file__).resolve().parents[4])
    print("0138 database: SQLite", sqlite3.sqlite_version, "OS:", os.name)
    first = identity.allocate(at=DAY)
    # Another connection observes the committed reservation immediately.
    with sqlite3.connect(path) as conn:
        assert conn.execute("SELECT serial FROM ai_run_id_counters").fetchone()[0] == 1
    assert first == "aiv2_20261106_000001"
    assert identity.allocate(at=DAY + timedelta(days=1)) == "aiv2_20261107_000001"
    assert identity.allocate(at=DAY) == "aiv2_20261106_000002"


@pytest.mark.parametrize("workers", [2, 4])
def test_multiprocess_10000_each_and_restart(identity_db, tmp_path, workers):
    path, store = identity_db
    ctx = multiprocessing.get_context("spawn")
    outputs = [tmp_path / f"worker-{i}.json" for i in range(workers)]
    procs = [ctx.Process(target=_allocate_process, args=(path, str(out), 10000, True)) for out in outputs]
    try:
        for proc in procs:
            proc.start()
        for proc in procs:
            proc.join(1000)
            assert proc.exitcode == 0
        ids = [rid for out in outputs for rid in json.loads(out.read_text(encoding="utf-8"))]
        assert len(ids) == workers * 10000 == len(set(ids))
        restarted = tmp_path / "restarted.json"
        proc = ctx.Process(target=_allocate_process, args=(path, str(restarted), 3))
        procs.append(proc)
        proc.start()
        proc.join(30)
        assert proc.exitcode == 0
        assert set(ids).isdisjoint(json.loads(restarted.read_text(encoding="utf-8")))
        print(f"0138 workers={workers} allocations={len(ids)} duplicates=0; restart=3 reused=0")
    finally:
        for proc in procs:
            if proc.is_alive():
                proc.terminate()
                proc.join(10)


def test_disabled_cutover_commit_failure_exhaustion_and_nested_transaction(identity_db):
    _, store = identity_db
    store._execute("UPDATE ai_run_id_contract SET version = 99")  # unknown (newer) contract
    with pytest.raises(RuntimeError, match="incompatible_version"):
        identity.allocate(at=DAY)
    assert store._fetch_one("SELECT version FROM ai_run_id_contract")["version"] == 99
    store._execute("UPDATE ai_run_id_contract SET version = 0")
    store._db.fail_commit = True
    with pytest.raises(OSError, match="commit failure"):
        identity.allocate(at=DAY)
    store._db.fail_commit = False
    assert identity.allocate(at=DAY).endswith("000001")  # failed reservation was never returned
    with store.transaction():
        with pytest.raises(RuntimeError, match="independent_commit"):
            identity.allocate(at=DAY)
    store._execute("UPDATE ai_run_id_counters SET serial = 999999")
    with pytest.raises(RuntimeError, match="exhausted"):
        identity.allocate(at=DAY)
    assert store._fetch_one("SELECT serial FROM ai_run_id_counters")["serial"] == 999999


def test_offline_seed_inventory_and_burned_number_survive_purge(identity_db, tmp_path):
    _, store = identity_db
    scratch = tmp_path / "legacy-scratch"
    scratch.mkdir()
    (scratch / "aiv2_20261106_000999").mkdir()
    identity.initialize(scratch_roots=[scratch])
    assert identity.allocate(at=DAY).endswith("001000")
    # Simulate a crash immediately after commit: no token/lease/finished row exists.
    store._execute("DELETE FROM ai_invoke_runs")
    identity.initialize(scratch_roots=[scratch])
    assert identity.allocate(at=DAY).endswith("001001")
    with pytest.raises(RuntimeError, match="inventory_unavailable"):
        identity.initialize(scratch_roots=[scratch / "missing"])


def test_claim_rejects_same_id_different_nonce_and_identity(identity_db):
    row = owner()
    identity.claim(row)
    identity.claim(row)
    for key, val in (("execution_nonce", str(uuid4())), ("group_id", "other"),
                     ("project_id", "other"), ("started_at", "different")):
        with pytest.raises(identity.RunIdentityConflict):
            identity.claim({**row, key: val})
    identity.assert_owner(row)


@pytest.fixture
def scratch_case(identity_db, monkeypatch, tmp_path):
    from modules.flow_gate.services.ai_invoke import runtime
    from modules.flow_gate.services import ai_invoke_service as svc
    from modules.flow_gate.storage import paths
    root = tmp_path / "scratch-storage"
    root.mkdir()
    from modules.flow_gate.graph_workflow.services import card_document_ports
    def scratch_write_gate(operation, candidates, action=None, *, trees=()):
        from contextlib import nullcontext
        assert operation in ("paths", "paths_scope")
        assert all(Path(candidate).resolve().is_relative_to(root.resolve()) for candidate in (*candidates, *trees))
        return nullcontext() if operation == "paths_scope" else action()
    monkeypatch.setitem(card_document_ports._handlers, card_document_ports.DOCUMENT_WRITE_GATE, scratch_write_gate)
    monkeypatch.setattr(paths, "get_storage_root", lambda *a, **k: root)
    monkeypatch.setattr(runtime.db_projects, "get_by_id", lambda _: {"project_name": "FlowGate"})
    monkeypatch.setattr(runtime.db_group_ai_leases, "get_by_run_id", lambda _: None)
    monkeypatch.setattr(svc, "is_run_live", lambda _: False)
    row = owner()
    identity.claim(row)
    scratch = runtime._create_scratch(row["project_id"], row["run_id"], row["execution_nonce"])
    (scratch / "payload").write_bytes(b"must survive a different execution")
    return row, scratch


def tree_hash(path):
    return [(p.relative_to(path).as_posix(), hashlib.sha256(p.read_bytes()).hexdigest())
            for p in sorted(path.rglob("*")) if p.is_file()]


def test_stale_finalize_and_sweep_preserve_reused_path(scratch_case):
    from modules.flow_gate.services.ai_invoke import runtime, finalize, terminal
    from modules.flow_gate.storage import filesystem
    row, scratch = scratch_case
    stale = {**row, "execution_nonce": str(uuid4()), "scratch_dir": str(scratch), "outcome": "complete"}
    before = tree_hash(scratch), filesystem.path_identity(scratch)
    finalize._finalize_scratch(stale)
    assert not runtime._mark_scratch_completed(row["project_id"], row["run_id"], scratch,
                                              DAY.isoformat(), stale["execution_nonce"])
    assert runtime._delete_owned_scratch(row["project_id"], row["run_id"], scratch,
                                        stale["execution_nonce"])[0] is False
    terminal.publish(stale)
    runtime._cleanup_retained_scratches(row["project_id"])
    assert (tree_hash(scratch), filesystem.path_identity(scratch)) == before
    assert stale["identity_conflict"]


def test_legacy_manifest_cannot_be_adopted(scratch_case):
    from modules.flow_gate.services.ai_invoke import runtime
    row, scratch = scratch_case
    file = scratch / runtime.SCRATCH_MANIFEST_NAME
    manifest = json.loads(file.read_text(encoding="utf-8"))
    manifest.pop("execution_nonce")
    file.write_text(json.dumps(manifest), encoding="utf-8")
    before = tree_hash(scratch)
    assert not runtime._delete_owned_scratch(row["project_id"], row["run_id"], scratch,
                                            row["execution_nonce"])[0]
    assert tree_hash(scratch) == before


def test_owned_completion_delete_and_journal_lock(scratch_case):
    from modules.flow_gate.services.ai_invoke import runtime
    from modules.flow_gate.storage import filesystem
    row, scratch = scratch_case
    with filesystem.journal_file_lock("ai-run", row["project_id"], row["run_id"]):
        assert not runtime._mark_scratch_completed(row["project_id"], row["run_id"], scratch,
                                                  DAY.isoformat(), row["execution_nonce"])
        assert not runtime._delete_owned_scratch(row["project_id"], row["run_id"], scratch,
                                                row["execution_nonce"])[0]
    assert runtime._mark_scratch_completed(row["project_id"], row["run_id"], scratch,
                                          DAY.isoformat(), row["execution_nonce"])
    assert runtime._delete_owned_scratch(row["project_id"], row["run_id"], scratch,
                                        row["execution_nonce"]) == (True, "deleted")
    assert not scratch.exists()




def record(row):
    return {**row, "doc_ref": "flowgate.v02.0138.0005-TR", "mode": "single", "outcome": "complete",
            "finished_at": (DAY + timedelta(minutes=1)).isoformat(), "created_at": DAY.isoformat(),
            "updated_at": DAY.isoformat(), "token_id": "token-A"}










def _legacy_writer(path, run_id):
    with sqlite3.connect(path) as conn:
        conn.execute("UPDATE ai_invoke_runs SET doc_ref = ?, token_id = ? WHERE run_id = ?",
                     ["legacy-overwrite", "legacy-token", run_id])






def _commit_then_crash(path, output):
    connection.STORE = open_store(path)
    Path(output).write_text(identity.allocate(at=DAY), encoding="utf-8")
    os._exit(73)




def _scratch_process(db_path, root, row, output, barrier):
    from contextlib import nullcontext
    from modules.flow_gate.services.ai_invoke import runtime
    from modules.flow_gate.storage import paths
    from modules.flow_gate.graph_workflow.services import card_document_ports
    connection.STORE = open_store(db_path)
    paths.get_storage_root = lambda *a, **k: Path(root)
    runtime.db_projects.get_by_id = lambda _: {"project_name": "FlowGate"}
    def write_gate(operation, candidates, action=None, *, trees=()):
        assert all(Path(p).resolve().is_relative_to(Path(root).resolve()) for p in (*candidates, *trees))
        return nullcontext() if operation == "paths_scope" else action()
    card_document_ports.register(card_document_ports.DOCUMENT_WRITE_GATE, write_gate)
    barrier.wait(30)
    try:
        runtime._create_scratch(row["project_id"], row["run_id"], row["execution_nonce"])
        result = "created"
    except (OSError, ValueError) as exc:
        result = type(exc).__name__
    Path(output).write_text(result, encoding="utf-8")












# ── the earlier change: v0.2 issues its own namespace next to a live v0.1 ──────────

def _version(store):
    return store._fetch_one("SELECT version FROM ai_run_id_contract WHERE singleton = 1")["version"]
