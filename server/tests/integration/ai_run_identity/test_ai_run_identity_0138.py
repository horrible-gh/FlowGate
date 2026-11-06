"""0138 real SQLite transactions, independent processes and Windows scratch ownership.

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
    identity.initialize(scratch_roots=[], all_issuers_stopped=True)
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
        identity.initialize(scratch_roots=[], all_issuers_stopped=True)
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
    assert first == "aiv_20261106_000001"
    assert identity.allocate(at=DAY + timedelta(days=1)) == "aiv_20261107_000001"
    assert identity.allocate(at=DAY) == "aiv_20261106_000002"


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
    store._execute("UPDATE ai_run_id_contract SET version = 0")
    with pytest.raises(RuntimeError, match="cutover_required"):
        identity.allocate(at=DAY)
    identity.initialize(scratch_roots=[], all_issuers_stopped=True)
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
    (scratch / "aiv_20261106_000999").mkdir()
    with pytest.raises(RuntimeError, match="must_be_stopped"):
        identity.initialize(scratch_roots=[scratch], all_issuers_stopped=False)
    identity.initialize(scratch_roots=[scratch], all_issuers_stopped=True)
    assert identity.allocate(at=DAY).endswith("001000")
    # Simulate a crash immediately after commit: no token/lease/finished row exists.
    store._execute("DELETE FROM ai_invoke_runs")
    identity.initialize(scratch_roots=[scratch], all_issuers_stopped=True)
    assert identity.allocate(at=DAY).endswith("001001")
    with pytest.raises(RuntimeError, match="inventory_unavailable"):
        identity.initialize(scratch_roots=[scratch / "missing"], all_issuers_stopped=True)


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


def test_duplicate_mkdir_preserves_first_owner(scratch_case):
    from modules.flow_gate.services.ai_invoke import runtime
    row, scratch = scratch_case
    before = tree_hash(scratch)
    with pytest.raises(FileExistsError):
        runtime._create_scratch(row["project_id"], row["run_id"], row["execution_nonce"])
    assert tree_hash(scratch) == before


def record(row):
    return {**row, "doc_ref": "flowgate.v02.0138.0005-TR", "mode": "single", "outcome": "complete",
            "finished_at": (DAY + timedelta(minutes=1)).isoformat(), "created_at": DAY.isoformat(),
            "updated_at": DAY.isoformat(), "token_id": "token-A"}


@pytest.mark.parametrize("order", [("A", "B"), ("B", "A")])
def test_record_collision_both_finish_orders(identity_db, order):
    a = owner()
    identity.claim(a)
    b = {**a, "execution_nonce": str(uuid4()), "doc_ref": "other-document", "token_id": "token-B"}
    for name in order:
        if name == "A":
            records.upsert(record(a))
        else:
            with pytest.raises(identity.RunIdentityConflict):
                records.upsert({**record(b), "doc_ref": "other-document", "token_id": "token-B"})
    persisted = records.get(a["run_id"])
    assert persisted["execution_nonce"] == a["execution_nonce"]
    assert persisted["doc_ref"] == "flowgate.v02.0138.0005-TR"
    assert persisted["token_id"] == "token-A"
    records.upsert({**record(a), "outcome": "partial"})
    assert records.get(a["run_id"])["outcome"] == "partial"
    for key, val in (("group_id", "other"), ("project_id", "other"), ("started_at", "later")):
        with pytest.raises(identity.RunIdentityConflict):
            records.upsert({**record(a), key: val})


def test_legacy_rows_readable_but_never_adopted(identity_db):
    legacy = owner()
    legacy.pop("execution_nonce")
    records.upsert(record(legacy))
    before = records.get(legacy["run_id"])
    with pytest.raises(identity.RunIdentityConflict):
        records.upsert({**record(legacy), "token_id": "other"})
    with pytest.raises(identity.RunIdentityConflict):
        identity.claim({**legacy, "execution_nonce": str(uuid4())})
    assert records.get(legacy["run_id"]) == before


def test_persist_failure_never_publishes_success(scratch_case, monkeypatch):
    from modules.flow_gate.services.ai_invoke import terminal
    from modules.flow_gate.services import ai_invoke_service as svc
    row, scratch = scratch_case
    events = []
    monkeypatch.setattr(svc, "_persist_run_record", lambda _: False)
    monkeypatch.setattr(svc, "_broadcast", lambda *a: events.append(a))
    terminal.publish(row)
    assert not events and not row.get("_record_persisted")
    assert scratch.exists()


def test_physical_path_reuse_rejects_delayed_a(scratch_case, identity_db):
    from modules.flow_gate.services.ai_invoke import runtime, finalize
    from modules.flow_gate.storage import filesystem
    a, path = scratch_case
    _, store = identity_db
    assert runtime._delete_owned_scratch(a["project_id"], a["run_id"], path, a["execution_nonce"])[0]
    # Fault injection only in the isolated DB: force reuse that the allocator forbids.
    store._execute("DELETE FROM ai_run_owners WHERE run_id = ?", [a["run_id"]])
    b = {**a, "execution_nonce": str(uuid4())}
    identity.claim(b)
    new_path = runtime._create_scratch(b["project_id"], b["run_id"], b["execution_nonce"])
    (new_path / "B").write_bytes(b"B-owned-data")
    before = tree_hash(new_path), filesystem.path_identity(new_path)
    finalize._finalize_scratch({**a, "scratch_dir": str(path), "outcome": "complete"})
    runtime._cleanup_retained_scratches(b["project_id"])
    assert (tree_hash(new_path), filesystem.path_identity(new_path)) == before


def _legacy_writer(path, run_id):
    with sqlite3.connect(path) as conn:
        conn.execute("UPDATE ai_invoke_runs SET doc_ref = ?, token_id = ? WHERE run_id = ?",
                     ["legacy-overwrite", "legacy-token", run_id])


def test_mixed_old_binary_can_bypass_application_guard(identity_db):
    path, _ = identity_db
    a = owner()
    identity.claim(a)
    records.upsert(record(a))
    proc = multiprocessing.get_context("spawn").Process(target=_legacy_writer, args=(path, a["run_id"]))
    try:
        proc.start()
        proc.join(30)
        assert proc.exitcode == 0
        assert records.get(a["run_id"])["doc_ref"] == "legacy-overwrite"
        print("0138 mixed-version residual reproduced: raw legacy writer bypasses application guard; fence required")
    finally:
        if proc.is_alive():
            proc.terminate()
            proc.join(10)


def test_runtime_allocator_has_no_local_fallback(identity_db, monkeypatch):
    from modules.flow_gate.services.ai_invoke import runtime, provider_cli
    from modules.flow_gate.services import ai_evaluation_service as evaluation
    ids = [runtime._next_run_id(), provider_cli._next_run_id(), evaluation.probe._next_run_id()]
    assert len(set(ids)) == 3
    def unavailable():
        raise OSError("isolated database unavailable")
    monkeypatch.setattr(identity, "get_store", unavailable)
    for allocate in (runtime._next_run_id, provider_cli._next_run_id, evaluation.probe._next_run_id):
        with pytest.raises(OSError):
            allocate()


def _commit_then_crash(path, output):
    connection.STORE = open_store(path)
    Path(output).write_text(identity.allocate(at=DAY), encoding="utf-8")
    os._exit(73)


def test_process_crash_after_commit_burns_id(identity_db, tmp_path):
    path, _ = identity_db
    output = tmp_path / "committed-id.txt"
    proc = multiprocessing.get_context("spawn").Process(target=_commit_then_crash, args=(path, str(output)))
    try:
        proc.start()
        proc.join(30)
        assert proc.exitcode == 73
        committed = output.read_text(encoding="utf-8")
        assert committed == "aiv_20261106_000001"
        assert identity.allocate(at=DAY) == "aiv_20261106_000002"
    finally:
        if proc.is_alive():
            proc.terminate()
            proc.join(10)


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


def test_concurrent_mkdir_one_owner(identity_db, tmp_path):
    db_path, _ = identity_db
    row = owner()
    identity.claim(row)
    root = tmp_path / "multi-scratch"
    root.mkdir()
    ctx = multiprocessing.get_context("spawn")
    barrier = ctx.Barrier(2)
    outputs = [tmp_path / f"mkdir-{n}.txt" for n in range(2)]
    procs = [ctx.Process(target=_scratch_process, args=(db_path, str(root), row, str(out), barrier)) for out in outputs]
    try:
        for proc in procs:
            proc.start()
        for proc in procs:
            proc.join(45)
            assert proc.exitcode == 0
        results = [p.read_text(encoding="utf-8") for p in outputs]
        assert results.count("created") == 1
        manifest = json.loads((root / "scratch" / "FlowGate" / row["run_id"] / ".flowgate-ai-scratch.json").read_text(encoding="utf-8"))
        assert manifest["execution_nonce"] == row["execution_nonce"]
    finally:
        for proc in procs:
            if proc.is_alive():
                proc.terminate()
                proc.join(10)


def test_pre_manifest_legacy_cleanup_requires_terminal_record(scratch_case, identity_db):
    from modules.flow_gate.services.ai_invoke import runtime
    row, current = scratch_case
    legacy_id = "aiv_20260827_000001"
    legacy = current.parent / legacy_id
    legacy.mkdir()
    (legacy / "payload").write_text("legacy", encoding="utf-8")
    old = datetime(2026, 8, 27, tzinfo=timezone.utc)
    os.utime(legacy, (old.timestamp(), old.timestamp()))
    runtime._cleanup_retained_scratches(row["project_id"])
    assert legacy.exists()  # no proven terminal owner yet
    legacy_row = record({**row, "run_id": legacy_id, "started_at": old.isoformat()})
    legacy_row.pop("execution_nonce")
    legacy_row["finished_at"] = (old + timedelta(minutes=1)).isoformat()
    records.upsert(legacy_row)
    runtime._cleanup_retained_scratches(row["project_id"])
    assert not legacy.exists()
    assert current.exists()


def test_admission_rollback_continues_after_token_revoke_failure(scratch_case, monkeypatch):
    from fastapi import HTTPException
    from modules.flow_gate.services import ai_invoke_service as svc
    from modules.flow_gate.services.ai_invoke import runtime
    row, scratch = scratch_case
    released = []
    monkeypatch.setattr(svc.db_group_ai_leases, "get_active", lambda _: None)
    monkeypatch.setattr(svc.db_group_ai_leases, "release", lambda *a, **k: released.append(a))
    monkeypatch.setattr(svc, "_runs", {})
    def revoke_failure(*a, **k):
        raise RuntimeError("token DB unavailable")
    monkeypatch.setattr(svc.token_service, "revoke", revoke_failure)
    def fail_after_scratch(**kwargs):
        runtime.admission_identity.set({**row, "token_id": "test-token", "scratch_dir": str(scratch)})
        raise RuntimeError("after scratch")
    monkeypatch.setattr(svc, "_admission_start_run", fail_after_scratch)
    with pytest.raises(HTTPException):
        svc.start_run(project_id=row["project_id"], group_id=row["group_id"])
    assert not scratch.exists()
    assert released == [(row["group_id"], row["run_id"])]


def test_restart_orphan_preserves_committed_execution_identity(identity_db, monkeypatch):
    from modules.flow_gate.services.ai_invoke import admission
    from modules.flow_gate.db import ai_invoke_document_review_loops
    monkeypatch.setattr(ai_invoke_document_review_loops, "stop_for_restart_orphan", lambda *a, **k: None)
    row = owner()
    identity.claim(row)
    lease = {"run_id": row["run_id"], "group_id": row["group_id"], "project_id": row["project_id"],
             "acquired_at": "2026-11-06T00:00:05+00:00", "token_id": None}
    admission._record_orphaned_lease_run(lease, "orphaned_by_restart")
    saved = records.get(row["run_id"])
    assert saved["execution_nonce"] == row["execution_nonce"]
    assert saved["started_at"] == row["started_at"]
    assert saved["end_reason"] == "orphaned_by_restart"
    with pytest.raises(identity.RunIdentityConflict):
        admission._record_orphaned_lease_run({**lease, "group_id": "another"}, "orphaned_by_restart")
    assert records.get(row["run_id"])["group_id"] == row["group_id"]


def test_cli_launch_rejects_another_execution_before_spawn(scratch_case):
    from modules.flow_gate.services.ai_invoke import provider_cli
    row, scratch = scratch_case
    run = {**row, "scratch_dir": str(scratch)}
    provider = {"kind": "custom"}
    assert provider_cli._resolve_cli_launch(provider, run, "worker")[1] == "valid"
    before = tree_hash(scratch)
    decision, reason = provider_cli._resolve_cli_launch(provider, {**run, "execution_nonce": str(uuid4())}, "worker")
    assert decision is None and reason == "scratch_owner_mismatch"
    assert tree_hash(scratch) == before
