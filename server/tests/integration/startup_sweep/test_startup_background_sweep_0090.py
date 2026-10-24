"""flowgate.v02.0090 T0015 — the bootstrap never waits for a scratch delete backlog (NR0014).

1. ``import routers.main`` binds the lifespan to ``startup.run_all``.
2. Lifespan + API: with eligible AI-run scratch (ReadOnly Git objects included) whose
   delete is held, ``TestClient(app)`` enters the lifespan and answers a request; once
   released, the background thread removes the targets and their delete records.
3. Order: lease recovery on the caller, then AI-run -> Token disk -> purge on the one
   ``startup-scratch-sweep`` thread; a thread that cannot start is logged, never run inline.
4. Snapshot: the first ``startup=True`` cleanup and TTL sweep run on the existing
   ``snapshot-cleanup-sweep`` thread; a failed first pass keeps the periodic sweep.
5. Concurrency: a second delete of the target the background sweep holds returns at
   once on the non-blocking journal lock and never deletes it twice.
6. Graph recovery (NR0014 §2.6): the real ``execution.recover()`` replays a requested
   write through ``node_dispatch.start_ai`` -> ``start_ai_invoke`` -> the real
   ``start_run``; with several eligible scratch held mid-delete, the lifespan and the API
   are ready and no delete runs on the thread that replayed the start.
7. Admission: ``request_run_scratch_sweep`` returns at once, runs one sweep per project
   at a time (one more pass when asked during it), and never sweeps inline.
8. Snapshot: while the first TTL sweep holds a snapshot's delete, an explicit cleanup of
   the same snapshot (service and route) returns a retryable busy result at once and the
   snapshot is deleted exactly once.
"""
from __future__ import annotations

import inspect
import json
import os
import stat
import threading
import time
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace

import pytest

# Isolated import of routers.main: a SQLite settings shape, and TESTING=1 skips the DB init.
os.environ.setdefault("TESTING", "1")
os.environ.setdefault("DB_TYPE", "sqlite")
os.environ.setdefault("CONTEXT", "/flowgate")
os.environ.setdefault("ALLOWED_ORIGIN", "")

import startup  # noqa: E402
from test_token_scratch_delete_journal_0090 import owned, PROJECT  # noqa: F401
from test_ai_scratch_delete_journal_0090 import ai_owned, _new, _journal  # noqa: F401
from test_snapshot_materialization_0517 import snapshot_env  # noqa: F401
from test_snapshot_delete_journal_0090 import _publish, _record
from modules.flow_gate.services import snapshot_materialization_service as snapshots
from modules.flow_gate.services import token_scratch
from modules.flow_gate.services.ai_invoke import runtime
from modules.flow_gate.storage import filesystem as storage_fs

THREAD = "startup-scratch-sweep"
OTHER_STEPS = (
    "configure_console_encoding", "record_deployment", "preload_singletons", "register_server_instance",
    "recover_git_sessions", "start_conversation_integration", "start_card_document_integration",
    "start_card_settings_integration", "start_card_approval_integration", "start_card_memo_integration",
    "start_graph_policy_integration", "start_graph_validation_integration", "start_graph_run_integration",
    "start_work_plan_deploy_integration", "recover_graph_time_machine", "encrypt_ai_provider_keys",
    "check_agent_pepper", "recover_ai_evaluations", "start_snapshot_cleanup",
    "remove_retired_source_bundle_storage", "recover_chat_commands",
)


def _other_steps_noop(monkeypatch, calls=None):
    for name in OTHER_STEPS:
        monkeypatch.setattr(startup, name, (lambda name=name: calls.append(name)) if calls is not None else (lambda: None))


def _only_scratch_steps(monkeypatch, calls=None):
    _other_steps_noop(monkeypatch, calls)
    from flowgate.graph_run import execution
    monkeypatch.setattr(execution, "recover", lambda: None)


def _readonly_scratch(serial):
    run_id, path = _new(serial)
    obj = path / "tmp" / "repo" / ".git" / "objects" / "ab" / f"cdef{serial:04d}"
    obj.parent.mkdir(parents=True)
    obj.write_bytes(b"loose object")
    os.chmod(obj, stat.S_IREAD)
    return run_id, path


def _held_remove(monkeypatch, storage, project_storage):
    """Hold every managed delete until released; every target must be test storage."""
    real = storage_fs.remove_managed_tree
    gate, entered, targets = threading.Event(), threading.Event(), []

    def held(target, root):
        resolved = os.path.abspath(str(target))
        assert resolved.startswith((os.path.abspath(str(storage)), os.path.abspath(str(project_storage))))
        targets.append((resolved, threading.current_thread().name))
        entered.set()
        assert gate.wait(30), "held delete was never released"
        return real(target, root)

    monkeypatch.setattr(storage_fs, "remove_managed_tree", held)
    return gate, entered, targets


def _join(*names):
    for thread in threading.enumerate():
        if thread.name in (names or (THREAD,)):
            thread.join(10)
            assert not thread.is_alive()


def test_import_binds_lifespan_to_run_all():
    import routers.main as main
    from config import settings

    assert os.environ["TESTING"] == "1" and settings.DB_TYPE.value == "sqlite"
    assert main._bootstrap is startup.run_all
    assert main.app.router.lifespan_context is not None


def test_lifespan_and_api_do_not_wait_for_held_scratch_delete(ai_owned, monkeypatch):
    from fastapi.testclient import TestClient
    from modules.flow_gate.services.git import instance_registry
    import routers.main as main

    storage, project_storage, _, _, _ = ai_owned
    scratches = [_readonly_scratch(serial) for serial in (1, 2, 3)]
    _only_scratch_steps(monkeypatch)
    monkeypatch.setattr(startup, "recover_ai_invoke_leases", lambda: None)
    monkeypatch.setattr(instance_registry, "shutdown", lambda: None)
    gate, entered, targets = _held_remove(monkeypatch, storage, project_storage)
    watchdog = threading.Timer(20, gate.set)
    watchdog.start()
    try:
        began = time.monotonic()
        with TestClient(main.app) as client:
            assert not gate.is_set() and time.monotonic() - began < 10
            assert entered.wait(10)                      # the delete really is in progress, and held
            response = client.get(f"{main.CONTEXT}/api/v1/t0015-probe")
            assert response.status_code < 500
            assert not gate.is_set()
        gate.set()
        _join()
    finally:
        gate.set()
        watchdog.cancel()
    assert {name for _, name in targets} == {THREAD}
    for run_id, path in scratches:
        assert not path.exists() and not _journal(storage, run_id).exists()


def test_order_lease_recovery_then_one_background_thread(monkeypatch):
    calls, gate = [], threading.Event()

    def record(label, hold=False):
        def step(*_a, **_k):
            calls.append((label, threading.current_thread().name))
            if hold:
                assert gate.wait(30)
            return 0
        return step

    _only_scratch_steps(monkeypatch)
    monkeypatch.setattr(startup, "recover_ai_invoke_leases", record("recover"))
    from modules.flow_gate.services import ai_invoke_service
    monkeypatch.setattr(ai_invoke_service, "startup_sweep_run_scratches", record("ai", hold=True))
    monkeypatch.setattr(token_scratch, "startup_sweep", record("token"))
    monkeypatch.setattr(token_scratch, "purge_expired_rows", record("purge"))
    try:
        startup.run_all()                                # returns while the AI-run sweep is held
        assert [label for label, _ in calls] in (["recover"], ["recover", "ai"])
    finally:
        gate.set()
    _join()
    assert calls == [("recover", threading.current_thread().name), ("ai", THREAD), ("token", THREAD), ("purge", THREAD)]


def test_thread_start_failure_is_logged_and_never_runs_inline(monkeypatch):
    calls, warnings = [], []
    _only_scratch_steps(monkeypatch, calls)
    monkeypatch.setattr(startup, "recover_ai_invoke_leases", lambda: calls.append("recover"))
    monkeypatch.setattr(startup, "sweep_ai_run_scratches", lambda: calls.append("ai"))
    monkeypatch.setattr(startup, "sweep_token_scratches", lambda: calls.append("token"))
    monkeypatch.setattr(startup.logger, "warning", lambda message, *a, **k: warnings.append(str(message)))

    class Refused:
        def __init__(self, *_a, **_k):
            pass

        def start(self):
            raise RuntimeError("can't start new thread")

    monkeypatch.setattr(startup, "threading", SimpleNamespace(Thread=Refused))
    startup.run_all()
    assert "ai" not in calls and "token" not in calls
    assert calls.index("recover") < calls.index("register_server_instance")
    assert calls[-1] == "recover_chat_commands"
    assert any("scratch sweep thread could not start" in message for message in warnings)


def test_snapshot_first_cleanup_moves_to_thread_and_periodic_survives(monkeypatch):
    from modules.flow_gate.services import snapshot_request_service

    calls, gate, entered = [], threading.Event(), threading.Event()

    def first_cleanup(*, startup=False):
        calls.append(("cleanup", startup, threading.current_thread().name))
        entered.set()
        assert gate.wait(30)
        raise RuntimeError("injected first-pass failure")

    monkeypatch.setattr(snapshots, "_sweep_started", False)
    monkeypatch.setattr(snapshots, "SNAPSHOT_SWEEP_INTERVAL_SECONDS", 0.01)
    monkeypatch.setattr(snapshot_request_service, "retire_unmaterialized",
                        lambda _actor: calls.append(("retire", None, threading.current_thread().name)))
    monkeypatch.setattr(snapshots, "cleanup_orphans", first_cleanup)
    monkeypatch.setattr(snapshots, "sweep_expired",
                        lambda: calls.append(("sweep", None, threading.current_thread().name)) or {})
    before = set(threading.enumerate())
    try:
        snapshots.startup()                              # returns while the first cleanup is held
        started = [t for t in threading.enumerate() if t not in before and t.name == "snapshot-cleanup-sweep"]
        assert len(started) == 1 and started[0].daemon
        assert calls[0] == ("retire", None, threading.current_thread().name)
        assert entered.wait(10) and not gate.is_set()
        assert calls[1] == ("cleanup", True, "snapshot-cleanup-sweep")
        gate.set()
        deadline = time.monotonic() + 10
        while sum(1 for label, _, _ in calls if label == "sweep") < 3 and time.monotonic() < deadline:
            time.sleep(0.01)
        sweeps = [name for label, _, name in calls if label == "sweep"]
        assert len(sweeps) >= 3 and set(sweeps) == {"snapshot-cleanup-sweep"}
        snapshots.startup()                              # already started: no second thread
        assert sum(1 for label, _, _ in calls if label == "retire") == 1
    finally:
        gate.set()
        snapshots.shutdown()
    started[0].join(10)
    assert not started[0].is_alive()


def test_held_target_is_not_deleted_twice_and_nobody_waits(ai_owned, monkeypatch):
    storage, project_storage, _, _, _ = ai_owned
    run_id, path = _readonly_scratch(7)
    gate, entered, targets = _held_remove(monkeypatch, storage, project_storage)
    try:
        thread = startup.start_scratch_sweeps()
        assert thread is not None and thread.name == THREAD and thread.daemon
        assert entered.wait(10)
        began = time.monotonic()
        assert runtime._delete_owned_scratch(PROJECT, run_id, path)[0] is False
        runtime._cleanup_retained_scratches(PROJECT)     # the issue/admission sweep shape
        assert time.monotonic() - began < 5
        assert [name for _, name in targets] == [THREAD]
    finally:
        gate.set()
    _join()
    assert len(targets) == 1
    assert not path.exists() and not _journal(storage, run_id).exists()


ADMISSION_THREAD = "ai-run-scratch-sweep"
FLOW = "project-0090.v02.0090"


def _sqlite_store(migrated_sqlite_db, monkeypatch):
    """A migrated test SQLite file as the process store (group_lock_stub's shape)."""
    from sqloader.sqlite3 import SQLiteWrapper
    from modules.flow_gate.db import connection

    store = object.__new__(connection.FlowGateStore)
    store._db = SQLiteWrapper(migrated_sqlite_db("startup_background_sweep_0090.db"))
    store._sq = None
    monkeypatch.setattr(connection, "STORE", store)
    with store.transaction():
        store._execute("INSERT INTO projects (project_id, project_name, is_active, created_at, updated_at) "
                       "VALUES (?, ?, 1, ?, ?) ON CONFLICT(project_id) DO NOTHING",
                       [PROJECT, "Project", "2026-10-23T00:00:00+09:00", "2026-10-23T00:00:00+09:00"])
    return store


def _graph_replay_reaches_start_run(monkeypatch, tmp_path):
    """One eligible requested write row for the real execution.recover(); only the Graph
    ledger/guards and the start_run boundaries (token, worker, provider settings) are stubbed."""
    from flowgate.graph_run import execution
    from modules.flow_gate.graph_workflow.services import ai_node_starter, node_dispatch
    from modules.flow_gate.services import ai_invoke_service as svc, graph_execution_hooks

    job = {"flow_id": FLOW, "run_id": "grun_t0015", "node_key": "n1", "project_id": PROJECT,
           "issued_to": "usr_admin", "doc_ref": f"{FLOW}.0001-T", "doc_type": "TR", "result_doc_type": "TR",
           "provider_id": "aip_t0015", "stage": "result"}
    row = {"exec_id": "gex_t0015", "run_id": "grun_t0015", "attempt_id": "gat_t0015", "kind": "write",
           "status": "requested", "ai_run_id": None, "request_json": json.dumps({"type": "write", "job": job})}
    monkeypatch.setattr(execution, "get_store", lambda: SimpleNamespace(table_exists=lambda _name: True))
    monkeypatch.setattr(execution.result_merge, "recover", lambda: None)
    monkeypatch.setattr(execution.stop, "recover", lambda: None)
    monkeypatch.setattr(execution.run_store, "_all", lambda _sql, _params: [{**row, "group_id": FLOW}])
    monkeypatch.setattr(execution.run_store, "_affected", lambda _sql, _params: 1)
    monkeypatch.setattr(execution.ledger, "get", lambda _exec_id: dict(row))
    monkeypatch.setattr(execution.ledger, "eligible", lambda _row: True)
    monkeypatch.setattr(execution, "_time_machine", lambda: SimpleNamespace(busy=lambda _flow: False))
    monkeypatch.setattr(execution.fencing, "exclusive", lambda _flow, own_ok=False: nullcontext())
    monkeypatch.setattr(execution, "with_flow_guard", lambda _flow, body: body())
    monkeypatch.setattr(execution.workspace, "cleanup_finished", lambda: None)

    src_root = tmp_path / "graph-src"
    src_root.mkdir()
    hooks, failures, starts, requests = [], [], [], []

    def hook(action, *args, **_kwargs):
        hooks.append(action)
        if action == "admission":
            return args[0]()
        if action == "admit":
            return row["attempt_id"]
        if action == "prepare_workspace":
            return src_root
        return True if action == "start_failed" else None

    monkeypatch.setattr(graph_execution_hooks, "_handler", hook)
    monkeypatch.setattr(node_dispatch, "_ai_starter", node_dispatch._start_with_ai_invoke)
    monkeypatch.setattr(node_dispatch, "record_ai_failure", failures.append)
    monkeypatch.setattr(ai_node_starter.db_documents, "get_by_id", lambda doc_id: {"doc_id": doc_id, "branch": "main"})
    monkeypatch.setattr(ai_node_starter, "_map_if_open", lambda *_a, **_k: False)
    monkeypatch.setattr(ai_node_starter.mention_service, "build_mention_from_token_rec", lambda **_k: "work")
    monkeypatch.setattr(ai_node_starter.ai_start_precheck, "node_mention", lambda mention, *_a, **_k: mention)
    monkeypatch.setattr(ai_node_starter.card_memo_hooks, "prepend_memo_section", lambda mention, *_a, **_k: mention)

    provider = {"id": "aip_t0015", "name": "T0015", "exec_type": "cli", "kind": "claude", "enabled": True,
                "cli_command": "unused", "api_base_url": None, "api_model": None, "api_key_set": False,
                "api_key_hint": None}
    monkeypatch.setattr(svc.ai_settings_service, "resolve_effective",
                        lambda _pid: {"ok": True, "providers": [provider], "source": "system", "registered_count": 1})
    monkeypatch.setattr(svc.db_docs, "get_group_max_seq", lambda _group_id: 1)
    monkeypatch.setattr(svc.db_docs, "get_documents_by_group_id", lambda _group_id: [])
    monkeypatch.setattr(svc.db_docs, "get_by_id", lambda doc_id: {"doc_id": doc_id, "branch": "main"})
    monkeypatch.setattr(svc.db_projects, "get_by_id", lambda _pid: {"project_name": "Project"})
    monkeypatch.setattr(svc.db_git, "get_config", lambda _pid: None)
    monkeypatch.setattr(svc.db_wfseq, "get_sequence_for_member_doc", lambda _doc_id: None)
    monkeypatch.setattr(svc.db_wfseq, "get_sequence_by_doc_id", lambda _doc_id: None)
    monkeypatch.setattr(svc.db_wfseq, "get_sequence_items", lambda _seq_id: [])
    monkeypatch.setattr(svc.db_wfseq, "get_effective_head", lambda _seq_id: None)
    monkeypatch.setattr(svc.token_service, "issue", lambda **_kw: {
        "raw_token": "tok_t0015", "token_id": "tok_t0015",
        "expires_at": "2999-01-01T00:00:00+00:00", "scratch_dir": str(tmp_path / "token-scratch")})
    monkeypatch.setattr(svc, "_create_scratch", lambda _pid, _run_id: src_root)
    monkeypatch.setattr(svc, "_git_status_paths", lambda _root: set())
    monkeypatch.setattr(svc, "_worker", lambda *_a: None)
    monkeypatch.setattr(svc, "_broadcast", lambda *_a: None)
    monkeypatch.setattr(svc, "_runs", {})

    real_start, real_request = svc.start_run, svc.request_run_scratch_sweep

    def observed_start(**kwargs):
        try:
            result = real_start(**kwargs)
        except BaseException as exc:
            starts.append(("error", repr(exc), threading.current_thread().name))
            raise
        starts.append(("ok", kwargs.get("graph_exec_id"), threading.current_thread().name))
        return result

    def observed_request(project_id):
        thread = real_request(project_id)
        requests.append((project_id, threading.current_thread().name))
        return thread

    monkeypatch.setattr(svc, "start_run", observed_start)
    monkeypatch.setattr(svc, "request_run_scratch_sweep", observed_request)
    return SimpleNamespace(hooks=hooks, failures=failures, starts=starts, requests=requests)


@pytest.mark.parametrize("startup_sweep_running", [True, False], ids=["with-startup-sweep", "admission-only"])
def test_graph_recovery_replay_admission_never_holds_the_lifespan(ai_owned, migrated_sqlite_db, monkeypatch,
                                                                   tmp_path, startup_sweep_running):
    from fastapi.testclient import TestClient
    from modules.flow_gate.services.git import instance_registry
    import routers.main as main

    storage, project_storage, _, _, _ = ai_owned
    scratches = [_readonly_scratch(serial) for serial in (11, 12, 13)]
    _other_steps_noop(monkeypatch)                       # the real Graph execution recovery stays
    monkeypatch.setattr(startup, "recover_ai_invoke_leases", lambda: None)
    if not startup_sweep_running:
        monkeypatch.setattr(startup, "start_scratch_sweeps", lambda: None)
    monkeypatch.setattr(instance_registry, "shutdown", lambda: None)
    _sqlite_store(migrated_sqlite_db, monkeypatch)
    replay = _graph_replay_reaches_start_run(monkeypatch, tmp_path)
    gate, entered, targets = _held_remove(monkeypatch, storage, project_storage)
    watchdog = threading.Timer(25, gate.set)
    watchdog.start()
    try:
        began = time.monotonic()
        with TestClient(main.app) as client:
            assert not gate.is_set() and time.monotonic() - began < 10
            assert entered.wait(10)                      # a backlog delete really is in progress, and held
            response = client.get(f"{main.CONTEXT}/api/v1/t0015-probe")
            assert response.status_code < 500
            assert not gate.is_set()
            assert len(targets) == 1                     # the other eligible targets wait behind the held one
        gate.set()
        _join(THREAD, ADMISSION_THREAD)
    finally:
        gate.set()
        watchdog.cancel()
    assert replay.failures == [] and len(replay.starts) == 1, (replay.starts, replay.failures)
    status, exec_id, replay_thread = replay.starts[0]
    assert (status, exec_id) == ("ok", "gex_t0015") and "admit" in replay.hooks
    assert replay.requests == [(PROJECT, replay_thread)]  # the replayed admission reached its sweep request
    delete_threads = {name for _, name in targets}
    assert replay_thread not in delete_threads
    assert delete_threads <= {THREAD, ADMISSION_THREAD}
    if not startup_sweep_running:
        assert delete_threads == {ADMISSION_THREAD}
    assert len(targets) == len({target for target, _ in targets}) == 3
    for run_id, path in scratches:
        assert not path.exists() and not _journal(storage, run_id).exists()
    assert PROJECT not in runtime._run_sweeping


def test_admission_sweep_request_is_single_flight_and_never_inline(monkeypatch):
    from modules.flow_gate.services import ai_invoke_service as svc
    from modules.flow_gate.services.ai_invoke import admission

    source = inspect.getsource(admission.start_run)
    assert "request_run_scratch_sweep(project_id)" in source and "_cleanup_retained_scratches(" not in source
    calls, gate, entered = [], threading.Event(), threading.Event()

    def held(project_id):
        calls.append((project_id, threading.current_thread().name))
        entered.set()
        assert gate.wait(30)

    monkeypatch.setattr(svc, "_cleanup_retained_scratches", held)
    try:
        began = time.monotonic()
        first = svc.request_run_scratch_sweep(PROJECT)
        assert first is not None and first.name == ADMISSION_THREAD and first.daemon
        assert entered.wait(10)
        assert svc.request_run_scratch_sweep(PROJECT) is None   # already sweeping: one more pass asked
        assert svc.request_run_scratch_sweep(PROJECT) is None
        assert time.monotonic() - began < 5
    finally:
        gate.set()
    first.join(10)
    assert not first.is_alive()
    assert calls == [(PROJECT, ADMISSION_THREAD)] * 2          # the held pass, then exactly one more
    assert PROJECT not in runtime._run_sweeping and PROJECT not in runtime._run_sweep_again

    warnings = []

    class Refused:
        def __init__(self, *_a, **_k):
            pass

        def start(self):
            raise RuntimeError("can't start new thread")

    monkeypatch.setattr(runtime, "threading", SimpleNamespace(Thread=Refused))
    monkeypatch.setattr(runtime.logger, "warning", lambda message, *a, **k: warnings.append(message % a))
    assert svc.request_run_scratch_sweep(PROJECT) is None
    assert len(calls) == 2 and PROJECT not in runtime._run_sweeping
    assert any("could not start" in message for message in warnings)


def test_explicit_cleanup_is_busy_while_first_ttl_delete_holds_it(snapshot_env, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from modules.flow_gate.api.v1 import snapshot_routes
    from modules.flow_gate.auth.middleware import get_current_user
    from modules.flow_gate.services import snapshot_request_service

    final = _publish(snapshot_env)                       # created, TTL already expired
    monkeypatch.setattr(snapshots, "_operations", {})
    monkeypatch.setattr(snapshots, "_sweep_started", False)
    monkeypatch.setattr(snapshots, "SNAPSHOT_SWEEP_INTERVAL_SECONDS", 3600)
    monkeypatch.setattr(snapshot_request_service, "retire_unmaterialized", lambda _actor: None)
    real = storage_fs.remove_managed_tree
    gate, entered, deletes = threading.Event(), threading.Event(), []

    def held(target, root, **kwargs):
        deletes.append((Path(target).resolve(), threading.current_thread().name))
        entered.set()
        assert gate.wait(30)
        return real(target, root, **kwargs)

    monkeypatch.setattr(storage_fs, "remove_managed_tree", held)
    app = FastAPI()
    app.include_router(snapshot_routes.router)
    app.dependency_overrides[get_current_user] = lambda: {"user_id": "human"}
    before = set(threading.enumerate())
    sweeper = None
    try:
        snapshots.startup()
        sweeper = next(t for t in threading.enumerate() if t not in before and t.name == "snapshot-cleanup-sweep")
        assert entered.wait(10)                          # the first TTL sweep is deleting it, held
        began = time.monotonic()
        busy = snapshots.cleanup("snap_test", "human", trigger="explicit")
        response = TestClient(app).post("/api/v1/snapshots/snap_test/cleanup")
        assert time.monotonic() - began < 5 and not gate.is_set()
        assert busy["status"] == "created" and busy["cleanup_busy"] is True and busy["cleanup_warning"]
        assert response.status_code == 200 and response.json()["request"]["cleanup_busy"] is True
        assert snapshot_env.row["status"] == "created" and not snapshot_env.row.get("cleanup_failed")
        assert int(snapshot_env.row.get("cleanup_attempts") or 0) == 0   # busy is not a failed attempt
        gate.set()
        deadline = time.monotonic() + 10
        while snapshot_env.row["status"] != "deleted" and time.monotonic() < deadline:
            time.sleep(0.01)
    finally:
        gate.set()
        snapshots.shutdown()
    sweeper.join(10)
    assert not sweeper.is_alive()
    assert deletes == [(final.resolve(), "snapshot-cleanup-sweep")]   # deleted exactly once
    assert snapshot_env.row["status"] == "deleted" and not final.exists() and not _record(snapshot_env).exists()
    after = snapshots.cleanup("snap_test", "human", trigger="explicit")
    assert after["status"] == "deleted" and "cleanup_busy" not in after
