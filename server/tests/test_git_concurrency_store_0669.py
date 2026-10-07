"""Unit 1a of the group-lock migration (flowgate.default.0669 chat seq 18; 0666 0010-DB / 0009-L).

Pins the storage layer the lock manager (unit 1b) will build on:

1. migrations 130~133 exist in all three dialects and build the tables on SQLite
2. server_instance heartbeat/stop/dead CAS (DB §4(m)(n), L 2.8 monotonic status)
3. (removed in unit 9c: legacy_admission_gate CAS and its callers are gone)
4. resource_lock insert/release/reclaim/protect (DB §4(a)~(f)) and its CHECK/FK guards
5. (removed in unit 9c: git_project_lock is no longer read or written)
6. instance_registry: register, heartbeat, declared-dead re-registration, instance_state,
   same-node predecessor retirement, graceful stop, startup wiring
"""
from __future__ import annotations

import re
import sqlite3
import sys
from pathlib import Path

import pytest

_SERVER_DIR = Path(__file__).resolve().parents[1]
if str(_SERVER_DIR) not in sys.path:
    sys.path.insert(0, str(_SERVER_DIR))

from modules.flow_gate.db import connection  # noqa: E402
from modules.flow_gate.db import git_concurrency as gc  # noqa: E402
from modules.flow_gate.services.git import instance_registry as reg  # noqa: E402

_MIGRATIONS = _SERVER_DIR / "sql" / "migrations"
_NEW_FILES = (
    "130_git_concurrency_instance_gate.sql",
    "131_git_concurrency_operation_job.sql",
    "132_git_concurrency_resource_lock.sql",
    "133_git_project_lock_owner_instance.sql",
)
NOW = "2026-10-04T12:00:00+09:00"
LATER = "2026-10-04T12:00:30+09:00"


@pytest.fixture(scope="module")
def db_path(migrated_sqlite_db):
    return migrated_sqlite_db("test_git_concurrency_0669.db")


@pytest.fixture
def store(db_path, monkeypatch):
    from sqloader.sqlite3 import SQLiteWrapper

    s = object.__new__(connection.FlowGateStore)
    s._db = SQLiteWrapper(db_path)
    s._sq = None
    for module in (connection, gc):
        monkeypatch.setattr(module, "get_store", lambda s=s: s)
    with s.transaction():
        for table in ("resource_lock", "legacy_admission_gate", "git_project_lock",
                      "server_instance"):
            s._execute(f"DELETE FROM {table}")
        s._execute(
            "INSERT INTO projects (project_id, project_name, is_active, created_at, updated_at) "
            "VALUES ('p_0669', 'P 0669', 1, ?, ?) ON CONFLICT(project_id) DO NOTHING",
            [NOW, NOW],
        )
    monkeypatch.setattr(gc, "_current_instance_id", None)
    yield s
    gc.set_current_instance_id(None)


def _instance(instance_id="inst_" + "a" * 32, node="node-a", pid=111, marker="m1", now=NOW):
    gc.insert_instance(instance_id, node, pid, marker, now)
    return instance_id


def _lock(**over):
    row = dict(lock_key="G:" + "1" * 64, domain="G", project_id="p_0669", group_id="g1",
               target_key=None, holder_ctx_id="req:1", holder_kind="source_mutation",
               job_id=None, instance_id="inst_" + "a" * 32, lock_epoch="lk_" + "0" * 24,
               hold_class="short", acquired_at=NOW, heartbeat_until=None)
    row.update(over)
    gc.insert_lock(**row)
    return row


# ── 1. migrations ────────────────────────────────────────────────────────────

@pytest.mark.parametrize("dialect", ("sqlite", "mysql", "postgres"))
def test_new_migrations_exist_in_every_dialect(dialect):
    present = {p.name for p in (_MIGRATIONS / dialect).glob("13[0-3]_*.sql")}
    assert set(_NEW_FILES) <= present


def test_mysql_migrations_declare_foreign_keys_as_constraints():
    # MySQL parses and silently ignores an inline column REFERENCES clause.
    for name in _NEW_FILES:
        body = (_MIGRATIONS / "mysql" / name).read_text(encoding="utf-8")
        for line in body.splitlines():
            if "REFERENCES" in line and not line.lstrip().startswith("--"):
                assert "FOREIGN KEY" in line, f"{name}: inline REFERENCES on MySQL: {line.strip()}"


def test_sqlite_schema_has_new_tables_and_column(store):
    for table in ("server_instance", "legacy_admission_gate", "operation_job",
                  "resource_lock", "resource_reservation"):
        assert store.table_exists(table), table
    cols = {r["name"] for r in store._fetch_all("PRAGMA table_info(git_project_lock)")}
    assert "owner_instance_id" in cols


# ── 2. server_instance ───────────────────────────────────────────────────────

def test_heartbeat_counts_a_same_second_repeat_as_matched(store):
    iid = _instance()
    assert gc.heartbeat_instance(iid, NOW) == 1
    assert gc.heartbeat_instance(iid, NOW) == 1      # same value; touch_seq still moves
    assert gc.get_instance(iid)["touch_seq"] == 2


def test_instance_status_is_monotonic(store):
    iid = _instance()
    assert gc.mark_instance_dead(iid) == 1
    assert gc.heartbeat_instance(iid, LATER) == 0     # declared dead -> heartbeat misses
    assert gc.mark_instance_stopped(iid, LATER) == 0  # dead never becomes stopped
    assert gc.get_instance(iid)["status"] == "dead"


def test_dead_batch_only_touches_other_nodes_past_threshold(store):
    mine = _instance("inst_" + "1" * 32, node="node-a", now=NOW)
    old_other = _instance("inst_" + "2" * 32, node="node-b", now=NOW)
    fresh_other = _instance("inst_" + "3" * 32, node="node-b", now=LATER)
    assert gc.mark_dead_other_nodes("node-a", "2026-10-04T12:00:10+09:00") == 1
    assert gc.get_instance(old_other)["status"] == "dead"
    assert gc.get_instance(mine)["status"] == "alive"
    assert gc.get_instance(fresh_other)["status"] == "alive"


# ── 4. resource_lock ─────────────────────────────────────────────────────────

def test_lock_key_is_exclusive_and_release_needs_holder_and_epoch(store):
    _instance()
    row = _lock()
    with pytest.raises(Exception):
        _lock(holder_ctx_id="req:2", lock_epoch="lk_" + "9" * 24)
    assert gc.get_resource_lock(row["lock_key"])["holder_ctx_id"] == "req:1"
    assert gc.delete_lock(row["lock_key"], "req:2", row["lock_epoch"]) == 0
    assert gc.delete_lock(row["lock_key"], "req:1", "lk_" + "9" * 24) == 0
    assert gc.delete_lock(row["lock_key"], "req:1", row["lock_epoch"]) == 1
    assert gc.get_resource_lock(row["lock_key"]) is None


def test_different_groups_hold_their_own_g_lock(store):
    _instance()
    _lock(lock_key="G:" + "1" * 64, group_id="g1")
    _lock(lock_key="G:" + "2" * 64, group_id="g2", holder_ctx_id="req:2")
    assert len(gc.list_locks_in_scope("p_0669")) == 2
    assert [r["group_id"] for r in gc.list_locks_in_scope("p_0669", "g2")] == ["g2"]


def test_protected_lock_survives_release_and_reclaim(store):
    _instance()
    row = _lock(hold_class="long", heartbeat_until=NOW)
    assert gc.protect_lock(row["lock_key"], row["lock_epoch"], "selfcheck_recovery", NOW) == 1
    assert gc.delete_lock(row["lock_key"], "req:1", row["lock_epoch"]) == 0
    assert gc.reclaim_lock(row["lock_key"], row["lock_epoch"], 0) == 0
    got = gc.get_resource_lock(row["lock_key"])
    assert got["protected"] == 1 and got["heartbeat_until"] is None


def test_reclaim_requires_the_observed_exec_seq(store):
    _instance()
    row = _lock()
    store._execute("UPDATE resource_lock SET exec_seq = 1 WHERE lock_key = ?", [row["lock_key"]])
    assert gc.reclaim_lock(row["lock_key"], row["lock_epoch"], 0) == 0
    assert gc.reclaim_lock(row["lock_key"], row["lock_epoch"], 1) == 1


def test_stale_long_scan_and_heartbeat(store):
    _instance()
    stale = _lock(lock_key="R:" + "1" * 64, domain="R", group_id=None, hold_class="long",
                  heartbeat_until=NOW)
    _lock(lock_key="R:" + "2" * 64, domain="R", group_id=None, hold_class="long",
          heartbeat_until=LATER, holder_ctx_id="req:2")
    found = gc.list_stale_long_locks("2026-10-04T12:00:10+09:00", 50)
    assert [r["lock_key"] for r in found] == [stale["lock_key"]]
    assert gc.heartbeat_long_lock(stale["lock_key"], stale["lock_epoch"], "req:1", LATER) == 1
    assert gc.heartbeat_long_lock(stale["lock_key"], stale["lock_epoch"], "req:x", LATER) == 0
    assert gc.list_stale_long_locks("2026-10-04T12:00:10+09:00", 50) == []


def test_lock_row_constraints(store):
    iid = _instance()
    with pytest.raises(Exception):      # G needs a group
        _lock(group_id=None)
    with pytest.raises(Exception):      # W needs a target
        _lock(lock_key="W:" + "1" * 64, domain="W", group_id=None, target_key=None)
    # FKs are enforced on the transaction connection (connection.py PRAGMA), which is
    # where unit 1b inserts locks (gate admission + insert share one transaction).
    with pytest.raises(Exception):      # owner must be a registered instance
        with store.transaction():
            _lock(instance_id="inst_" + "f" * 32)
    with pytest.raises(Exception):      # holder_kind is an enum
        _lock(holder_kind="whatever")
    assert gc.list_locks_of_instance(iid) == []


# ── 6. instance_registry ─────────────────────────────────────────────────────

@pytest.fixture
def registry(store, monkeypatch):
    markers = {}
    monkeypatch.setattr(reg, "_start_identity", lambda pid: markers.get(pid))
    monkeypatch.setattr(reg, "node_key", lambda: "node-a")
    monkeypatch.setattr(reg, "_lost_listeners", [])
    markers[reg.os.getpid()] = "self-marker"
    yield markers
    reg.shutdown()


def test_register_and_heartbeat(registry):
    iid = reg.register()
    row = gc.get_instance(iid)
    assert re.fullmatch(r"inst_[0-9a-f]{32}", iid)
    assert (row["status"], row["node_key"], row["process_started_at"]) == ("alive", "node-a", "self-marker")
    assert reg.current_instance_id() == iid
    assert reg.heartbeat_once() is True
    assert reg.instance_valid_for_self()


def test_declared_dead_instance_notifies_and_reregisters(registry):
    old = reg.register()
    lost = []
    reg.on_instance_lost(lost.append)
    gc.mark_instance_dead(old)
    assert reg.heartbeat_once() is False
    assert lost == [old]
    new = reg.current_instance_id()
    assert new and new != old and gc.get_instance(new)["status"] == "alive"


def test_instance_state(registry, monkeypatch):
    me = reg.register()
    assert reg.instance_state(me) == reg.ALIVE
    assert reg.instance_state(None) == reg.DEAD
    assert reg.instance_state("inst_" + "0" * 32) == reg.DEAD          # unknown

    other_node = _instance("inst_" + "b" * 32, node="node-b", now=NOW)
    assert reg.instance_state(other_node, "2026-10-04T12:01:00+09:00") == reg.ALIVE
    assert reg.instance_state(other_node, "2026-10-04T12:01:11+09:00") == reg.DEAD  # 60s + 10s margin

    registry[222] = "m-222"
    same_node = _instance("inst_" + "c" * 32, node="node-a", pid=222, marker="m-222")
    assert reg.instance_state(same_node) == reg.ALIVE
    registry[222] = "reused-pid"                                        # pid recycled
    assert reg.instance_state(same_node) == reg.DEAD

    gc.mark_instance_stopped(other_node, NOW)
    assert reg.instance_state(other_node, NOW) == reg.DEAD


def test_startup_retires_dead_same_node_predecessors_and_shutdown_stops(registry):
    registry[333] = "m-333"
    alive_peer = _instance("inst_" + "d" * 32, node="node-a", pid=333, marker="m-333")
    gone = _instance("inst_" + "e" * 32, node="node-a", pid=444, marker="m-444")
    other_node = _instance("inst_" + "f" * 32, node="node-b", pid=444, marker="m-444")

    me = reg.startup()
    assert reg.startup() == me                                          # idempotent
    assert gc.get_instance(gone)["status"] == "dead"
    assert gc.get_instance(alive_peer)["status"] == "alive"
    assert gc.get_instance(other_node)["status"] == "alive"             # other node: not judged by pid

    reg.shutdown()
    assert gc.get_instance(me)["status"] == "stopped"
    assert reg.current_instance_id() is None


def test_clamped_parameters(monkeypatch):
    monkeypatch.setenv("FLOWGATE_INSTANCE_HEARTBEAT_INTERVAL_SEC", "1")
    monkeypatch.setenv("FLOWGATE_INSTANCE_DEAD_AFTER_SEC", "5")
    assert reg.heartbeat_interval_sec() == 5
    assert reg.dead_after_sec() == 30
    monkeypatch.setenv("FLOWGATE_CLOCK_SKEW_MARGIN_SEC", "abc")
    assert reg.clock_skew_margin_sec() == 10


def test_startup_registers_before_git_recovery(monkeypatch):
    import startup

    calls = []
    for name in ("configure_console_encoding", "record_deployment", "preload_singletons",
                 "recover_ai_invoke_leases", "encrypt_ai_provider_keys",
                 "start_snapshot_cleanup", "remove_retired_source_bundle_storage"):
        monkeypatch.setattr(startup, name, lambda: None)
    monkeypatch.setattr(startup, "register_server_instance", lambda: calls.append("register"))
    monkeypatch.setattr(startup, "recover_git_sessions", lambda: calls.append("recover"))
    startup.run_all()
    assert calls == ["register", "recover"]
