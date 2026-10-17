"""0061 T#4 regression fixtures. TR compiles; behavior runs belong to the test WP/TSR.

The ledger uses real SQLite migration 151/152 and conditional SQL. Process/dispatcher,
token reclamation, clock, and policy callbacks are controlled without launching AI.
"""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
import sqlite3
import threading

import pytest

from flowgate.graph_run import execution, execution_store as ledger, settle
from modules.flow_gate.graph_workflow.store import run_store
from modules.flow_gate.graph_workflow.services import run_apply
from modules.flow_gate.services import ai_invoke_service, graph_execution_hooks
from modules.flow_gate.services.ai_invoke import provider_cli, review

MIGRATIONS = Path(__file__).resolve().parents[1] / "sql" / "migrations"
STAMP = "2026-10-13T00:00:00.000Z"
FUTURE = "2026-10-13T04:05:00.000Z"
PAST = "2026-10-12T00:00:00.000Z"


class Store:
    def __init__(self):
        self.db = sqlite3.connect(":memory:", isolation_level=None, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.lock = threading.RLock()
        self.db.execute("PRAGMA foreign_keys=ON")

    def table_exists(self, name):
        return bool(self._fetch_one("SELECT name FROM sqlite_master WHERE type='table' AND name=?", [name]))

    def _fetch_one(self, sql, params=()):
        with self.lock:
            row = self.db.execute(sql, params).fetchone()
            return dict(row) if row else None

    def _fetch_all(self, sql, params=()):
        with self.lock:
            return [dict(r) for r in self.db.execute(sql, params).fetchall()]

    def _execute(self, sql, params=()):
        with self.lock:
            return self.db.execute(sql, params)

    def _execute_affected(self, sql, params=()):
        with self.lock:
            return self.db.execute(sql, params).rowcount

    @contextmanager
    def transaction(self):
        with self.lock:
            self.db.execute("BEGIN IMMEDIATE")
            try:
                yield
            except BaseException:
                self.db.execute("ROLLBACK")
                raise
            else:
                self.db.execute("COMMIT")


@pytest.fixture
def db(monkeypatch):
    store = Store()
    # The parent tables' non-ledger behavior is supplied by mocks below.
    store.db.executescript("""
        CREATE TABLE graph_runs(run_id TEXT PRIMARY KEY, group_id TEXT, status TEXT);
        CREATE TABLE graph_run_token_map(token_id TEXT PRIMARY KEY, run_id TEXT, node_key TEXT,
                                         stage TEXT, mapped_at TEXT);
        CREATE TABLE graph_node_runs(run_id TEXT, node_key TEXT, status TEXT, result_doc_id TEXT,
            opened_at TEXT, finished_at TEXT, dispatch_token_issued INTEGER, dispatch_issued_to TEXT,
            dispatch_mention_id TEXT, dispatch_error_code TEXT, stage TEXT, instruction_doc_id TEXT,
            result_event_floor INTEGER, updated_at TEXT);
    """)
    # 0064 T-B4d: 154 adds the token map kind run_store.map_token writes (DB0007 §2.2.1).
    for version in ("151_graph_run_state_attempts.sql", "152_graph_attempt_runs.sql", "154_graph_run_token_map_kind.sql"):
        store.db.executescript((MIGRATIONS / "sqlite" / version).read_text(encoding="utf-8"))
    store._execute("INSERT INTO graph_runs(run_id,group_id,status,run_mode,step_node_keys) "
                   "VALUES('run','flow','running','step','[\"node\"]')")
    store._execute("INSERT INTO graph_node_runs(run_id,node_key,status,result_doc_id) "
                   "VALUES('run','node','review','doc')")
    monkeypatch.setattr(run_store, "get_store", lambda: store)
    monkeypatch.setattr(execution, "get_store", lambda: store)
    monkeypatch.setattr(ledger, "now", lambda: STAMP)
    monkeypatch.setattr(ledger, "deadline", lambda: FUTURE)
    monkeypatch.setattr(run_store, "run_by_id", lambda rid: run_store._normalize(
        store._fetch_one("SELECT * FROM graph_runs WHERE run_id=?", [rid])))
    monkeypatch.setattr(run_store, "node_runs", lambda rid:
                        store._fetch_all("SELECT * FROM graph_node_runs WHERE run_id=?", [rid]))
    def guard(_flow, body):
        with store.transaction():
            return body()
    monkeypatch.setattr(execution, "with_flow_guard", guard)
    monkeypatch.setattr(graph_execution_hooks, "_handler", None)
    execution.register()
    store.attempt = run_store.open_attempt("run", "node", 1)
    yield store
    store.db.close()


def request(db, kind="write", payload=None):
    return ledger.request(db.attempt, "run", kind, payload or {"type": "write", "job": {}})


def ai_run(exec_id, ai_id="ai"):
    return {"graph_exec_id": exec_id, "run_id": ai_id, "group_id": "flow", "cancel_event": threading.Event()}


def test_attempt_survives_write_review_rework_until_node_done(db):
    for kind in ("write", "review", "rework", "review"):
        eid = request(db, kind)
        assert ledger.working("run")
        owner = ledger.acquire(eid, "ai-" + kind)
        ledger.end(eid, owner)
        assert not ledger.working("run")
        assert run_store.active_attempt_id("run", "node") == db.attempt
    assert not settle.working(SimpleNamespace(run_id="run"), [{"status": "review"}])
    assert run_store.end_attempt("run", "node") == 1
    assert run_store.active_attempt_id("run", "node") is None


def test_only_one_active_execution_and_cas_acquirer(db):
    eid = request(db)
    with pytest.raises(ledger.LeaseLost):
        request(db, "review")
    def acquire(number):
        try:
            return ledger.acquire(eid, str(number))
        except ledger.LeaseLost:
            return None
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(acquire, (1, 2)))
    assert len([r for r in results if r]) == 1
    assert ledger.get(eid)["lease_token"] in results


@pytest.mark.parametrize("action", [ledger.renew, ledger.end, ledger.cancel, ledger.mark_lost, ledger.request_cancel])
def test_wrong_token_cannot_change_running_execution(db, action):
    eid = request(db)
    owner = ledger.acquire(eid, "ai")
    before = ledger.get(eid)
    with pytest.raises(ledger.LeaseLost):
        action(eid, "wrong")
    assert ledger.get(eid) == before
    ledger.renew(eid, owner)
    ledger.renew(eid, owner)  # same timestamp is still a real affected-row CAS on MySQL
    assert ledger.get(eid)["lease_touch_seq"] == 2


@pytest.mark.parametrize("status", ["idle", "stopping"])
def test_ineligible_request_never_launches(db, status):
    eid = request(db)
    db._execute("UPDATE graph_runs SET status=?", [status])
    run = ai_run(eid)
    assert execution.launch(run) is False
    assert ledger.get(eid)["status"] == "cancelled"
    assert run["graph_lease_lost"] and run["cancel_event"].is_set()


def test_cancel_before_acquire_does_not_strand_request(db):
    eid = request(db)
    run = ai_run(eid)
    run["cancel_event"].set()
    assert not execution.launch(run)
    assert ledger.get(eid)["status"] == "cancelled"
    assert not ledger.working("run")


def test_paused_request_can_acquire_and_finished_request_cannot(db):
    eid = request(db)
    db._execute("UPDATE graph_runs SET status='paused'")
    owner = ledger.acquire(eid, "ai")
    ledger.cancel(eid, owner)
    with pytest.raises(ledger.LeaseLost):
        ledger.acquire(eid, "another")


def test_node_attempt_and_start_request_rollback_together(db):
    with pytest.raises(RuntimeError, match="rollback"):
        with db.transaction():
            attempt = run_store.open_attempt("run", "other", 1)
            ledger.request(attempt, "run", "write", {"type": "write"})
            db._execute("UPDATE graph_node_runs SET status='open' WHERE node_key='node'")
            raise RuntimeError("rollback")
    assert run_store.active_attempt_id("run", "other") is None
    assert not ledger.working("run")
    assert run_store.node_runs("run")[0]["status"] == "review"


def test_provider_retry_retains_same_lease_and_loser_cannot_end_it(db):
    eid = request(db)
    winner, loser = ai_run(eid), ai_run(eid, "loser")
    assert execution.launch(winner)
    token = winner["graph_lease_token"]
    assert execution.launch(winner)
    assert winner["graph_lease_token"] == token
    assert not execution.launch(loser)
    execution.finish(loser)
    assert ledger.get(eid)["status"] == "running"
    execution.finish(winner)
    assert ledger.get(eid)["status"] == "ended"


def test_cli_lease_denial_precedes_process_construction(db, monkeypatch):
    eid = request(db)
    ledger.acquire(eid, "winner")
    run = {**ai_run(eid, "loser"), "scratch_dir": "scratch", "raw_token": "test-only",
           "token_scratch_dir": "scratch"}
    monkeypatch.setattr(provider_cli, "_prepare_cli_command", lambda *_: "cli")
    monkeypatch.setattr(provider_cli, "_canonicalize_cli_prompt", lambda *_: ("prompt", "local"))
    monkeypatch.setattr(provider_cli, "_cli_scratch_env", lambda *_: {})
    monkeypatch.setattr(provider_cli, "_resolve_cli_launch", lambda *_: ({"effective_command": "cli"}, None))
    process = Mock()
    monkeypatch.setattr(provider_cli, "_CliProcess", process)
    assert provider_cli._cli_execute({"cli_command": "cli", "kind": "codex"}, "prompt", run)[0] == "spawn_failed"
    process.assert_not_called()


def test_heartbeat_loss_uses_existing_process_cancel(db, monkeypatch):
    run = ai_run(request(db))
    assert execution.launch(run)
    monkeypatch.setattr(ai_invoke_service, "get_run_record", lambda _: run)
    cancel = Mock(side_effect=lambda _: run["cancel_event"].set())
    monkeypatch.setattr(ai_invoke_service, "cancel_run", cancel)
    db._execute("UPDATE graph_attempt_runs SET lease_token='replacement'")
    with pytest.raises(ledger.LeaseLost):
        execution.heartbeat("ai")
    cancel.assert_called_once_with("ai")
    assert run["graph_lease_lost"]


def test_review_transition_is_atomic_and_retains_replay_checkpoint(db, monkeypatch):
    eid = request(db, "review", {"type": "loop", "loop": {}, "doc_id": "doc"})
    run = ai_run(eid)
    assert execution.launch(run)
    loop = {"current_stage": "rework", "last_hop_outcome": "succeeded", "round_no": 2, "deadline_at": FUTURE}
    original = ledger.request
    with monkeypatch.context() as scoped:
        scoped.setattr(ledger, "request", Mock(side_effect=RuntimeError("insert failed")))
        with pytest.raises(RuntimeError, match="insert failed"):
            execution.transition(run, loop)
    assert ledger.get(eid)["status"] == "running"
    assert run["graph_exec_id"] == eid and not run.get("graph_execution_finished")
    assert ledger.request is original
    execution.transition(run, loop)
    assert ledger.get(eid)["status"] == "ended"
    state = execution.loop_state(run["graph_exec_id"])
    assert state["loop"] == loop and state["review_run_ids"] == ["ai"]
    assert ledger.get(run["graph_exec_id"])["attempt_id"] == db.attempt
    assert run_store.active_attempt_id("run", "node") == db.attempt


def test_replayed_loop_counts_only_its_recorded_predecessors():
    rows = [{"id": n, "revision_no": n, "review_run_id": owner, "attempt_no": 1}
            for n, owner in enumerate(("other", "old", "current"), 1)]
    history, _ = review._document_loop_review_view({"review_run_id": "current", "graph_review_run_ids": ["old"]}, rows)
    assert [r["review_run_id"] for r in history] == ["current", "old"]
    history, _ = review._document_loop_review_view({"review_run_id": "current"}, rows)
    assert [r["review_run_id"] for r in history] == ["current"]


def test_restart_replays_requested_once_through_acquire(db, monkeypatch):
    eid = request(db)
    launches = []
    def replay(row):
        if execution.launch(ai_run(row["exec_id"])):
            launches.append(row["exec_id"])
    monkeypatch.setattr(execution, "replay", replay)
    execution.recover()
    execution.recover()
    assert launches == [eid]


@pytest.mark.parametrize("ended", [False, True])
def test_expired_recovery_requires_termination_then_invalidates_and_reschedules(db, monkeypatch, ended):
    eid = request(db)
    ledger.acquire(eid, "ai")
    db._execute("UPDATE graph_attempt_runs SET lease_expires_at=?", [PAST])
    run_store.map_token("token", "run", "node", "result", map_kind=run_store.MAP_ATTEMPT)
    monkeypatch.setattr(ai_invoke_service, "get_run_record", lambda _: None)
    monkeypatch.setattr(execution.ai_invoke_runs, "get", lambda _: {"status": "finished"} if ended else None)
    reclaim = Mock()
    monkeypatch.setattr(ai_invoke_service, "_reclaim_orphan_lease_token", reclaim)
    monkeypatch.setattr(execution.db_groups, "get_by_id", lambda _: {"project_id": "project"})
    contexts = []
    monkeypatch.setattr(run_apply, "load_context", lambda run, *args: SimpleNamespace(run=run))
    monkeypatch.setattr(run_apply, "emit_node_state", Mock())
    monkeypatch.setattr(run_apply, "settle_run", lambda ctx: contexts.append(ctx))
    execution.recover()
    if ended:
        assert ledger.get(eid)["status"] == "lost"
        attempt = db._fetch_one("SELECT * FROM graph_node_attempts WHERE attempt_id=?", [db.attempt])
        assert attempt["status"] == "invalidated" and attempt["invalidated_reason"] == "lease_expired"
        assert run_store.node_runs("run")[0]["status"] == "pending"
        reclaim.assert_called_once_with({"token_id": "token"}, "lease_expired")
        assert contexts[0].run["step_node_keys"] == ["node"]
        execution.recover()
        assert len(contexts) == 1
    else:
        assert ledger.get(eid)["status"] == "running"
        assert run_store.active_attempt_id("run", "node") == db.attempt
        reclaim.assert_not_called()
        assert contexts == []
